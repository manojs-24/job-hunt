import asyncio
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest

from app import company_finder, db, finder, llm, yc
from app.providers import careers


def yc_company(name, *, city="Bengaluru, KA, India", size=20, hiring=True, status="Active", site=None, batch="Winter 2023"):
    """A Y Combinator list entry in the shape `yc.load` returns."""
    return {"name": name, "website": site or f"https://www.{name.lower().replace(' ', '')}.com/", "locations": city,
            "team_size": size, "batch": batch, "status": status, "hiring": hiring, "one_liner": f"{name} does things",
            "url": "", "industry": ""}


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    import app.config
    monkeypatch.setattr(app.config, "load_dotenv", lambda *a, **k: None)
    for key in ("JSEARCH_RAPIDAPI_KEY", "SERPAPI_KEY", "OPENWEB_NINJA_API_KEY", "ANTHROPIC_API_KEY",
                "ANTHROPIC_BASE_URL"):
        monkeypatch.setenv(key, "")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "f.db")
    # A test must never download the real Y Combinator list (11 MB): default to an empty one.
    monkeypatch.setattr(yc, "load", lambda *a, **k: [])


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ reading a company's board from a link
@pytest.mark.parametrize("url,expected", [
    ("https://jobs.lever.co/meesho/7d9af9b5-c1c7", ("lever", "meesho")),
    ("https://job-boards.eu.greenhouse.io/groww/jobs/4970739101", ("greenhouse", "groww")),
    ("https://boards.greenhouse.io/gitlab/jobs/1", ("greenhouse", "gitlab")),
    ("https://jobs.ashbyhq.com/atlan/c6bb032c", ("ashby", "atlan")),
    ("https://in.linkedin.com/jobs/view/123", None),
    ("https://boards.greenhouse.io/embed/job_app?token=1", None),         # "embed" is not a company
    ("", None), (None, None),
])
def test_board_from_url(url, expected):
    assert finder.board_from_url(url) == expected


# ------------------------------------------------------------------ guessing a board name
def test_slug_guesses_use_the_whole_name_only():
    assert finder.slug_guesses("Applied Materials India Pvt Ltd")[:2] == ["appliedmaterials", "applied-materials"]
    assert finder.slug_guesses("Blue Yonder") == ["blueyonder", "blue-yonder"]       # never just "blue"
    assert "blue" not in finder.slug_guesses("Blue Yonder") and "applied" not in finder.slug_guesses("Applied Materials")
    assert finder.slug_guesses("BlueStone.com")[0] == "bluestone"
    assert finder.slug_guesses("IM Solutions") == []                                  # too short to guess safely
    assert finder.slug_guesses("") == []


def test_looks_like_rejects_a_different_company_with_a_similar_name():
    gh_other = [{"company_name": "Blue Origin", "title": "Engineer", "content": "rockets"}]
    assert not finder.looks_like("Blue Yonder", "greenhouse", gh_other)
    gh_same = [{"company_name": "Bluestone", "title": "Engineer", "content": ""}]
    assert finder.looks_like("BlueStone.com", "greenhouse", gh_same)
    # lever/ashby do not state a company name, so the name must appear in the job text
    lever_wrong = [{"text": "Backend Engineer", "descriptionPlain": "We build rockets"}] * 10
    assert not finder.looks_like("Blue Yonder", "lever", lever_wrong)
    lever_right = [{"text": "Backend Engineer", "descriptionPlain": "Blue Yonder builds supply chain software"}] * 10
    assert finder.looks_like("Blue Yonder", "lever", lever_right)
    assert not finder.looks_like("Co", "lever", lever_right) and not finder.looks_like("Blue Yonder", "lever", [])


# ------------------------------------------------------------------ careers links
def test_careers_url():
    assert finder.careers_url("lever", "meesho") == "https://jobs.lever.co/meesho"
    assert finder.careers_url("ashby", "atlan") == "https://jobs.ashbyhq.com/atlan"
    assert finder.careers_url("greenhouse", "groww", "https://job-boards.eu.greenhouse.io/groww/jobs/1") == \
        "https://job-boards.eu.greenhouse.io/groww"
    assert finder.careers_url("greenhouse", "x") == "https://job-boards.greenhouse.io/x"
    assert "Acme+careers+Pune" in finder.search_link("Acme", "Pune")


# ------------------------------------------------------------------ Claude's suggestions
def test_suggestions_without_a_key_are_skipped_politely():
    names, notice = finder.suggest_companies("Pune", [])
    assert names == [] and "ANTHROPIC_API_KEY" in notice


def test_suggestions_are_cleaned_and_failures_do_not_raise(monkeypatch):
    monkeypatch.setattr(llm, "make_client", lambda: object())
    monkeypatch.setattr(llm, "ask_json", lambda c, **kw: {"companies": [
        {"name": " Razorpay ", "kind": "Mid-size"}, {"name": "razorpay", "kind": "Large"},
        {"name": "", "kind": "Startup"}, {"name": "Acme", "kind": "weird"}]})
    names, notice = finder.suggest_companies("Bengaluru", ["Web Developer"])
    assert notice is None and names == [{"name": "Razorpay", "kind": "Mid-size"}, {"name": "Acme", "kind": "Unknown"}]

    def boom(c, **kw):
        raise ConnectionError("proxy down")
    monkeypatch.setattr(llm, "ask_json", boom)
    names, notice = finder.suggest_companies("Bengaluru", [])
    assert names == [] and "failed" in notice


# ------------------------------------------------------------------ finding a board (mock careers pages)
LEVER_ACME = [{"id": "a", "text": "Web Developer", "hostedUrl": "https://jobs.lever.co/acme/a",
               "createdAt": 1786469891368, "categories": {"location": "Bengaluru"},
               "descriptionPlain": "Acme Robotics builds robots."}]
GH_BETA = {"jobs": [{"id": 1, "title": "Engineer", "company_name": "Beta Labs", "absolute_url": "https://gh/b/1",
                     "location": {"name": "Pune"}, "content": ""}]}


def handler(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if "lever" in host and "acme" in path:
        return httpx.Response(200, json=LEVER_ACME)
    if "greenhouse" in host and "betalabs" in path:
        return httpx.Response(200, json=GH_BETA)
    if "greenhouse" in host and "flaky" in path:
        return httpx.Response(429)
    return httpx.Response(404)


def client():
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_find_board_by_link_hint_and_by_name():
    async def go():
        async with client() as c:
            return (await finder.find_board(c, "Anything", ("lever", "acme")),
                    await finder.find_board(c, "Beta Labs"),
                    await finder.find_board(c, "Acme Robotics"),
                    await finder.find_board(c, "Nobody Corp"))
    by_link, by_name, by_lever_text, none = run(go())
    assert by_link[0]["how"] == "link in your saved jobs" and by_link[1] is False
    assert (by_name[0]["board"], by_name[0]["slug"]) == ("greenhouse", "betalabs")
    assert by_lever_text[0] is None or by_lever_text[0]["slug"] == "acmerobotics"   # no board named acmerobotics
    assert none == (None, False)                                                    # a definite "no"


def test_a_temporary_failure_is_not_treated_as_a_definite_no():
    async def go():
        async with client() as c:
            return await finder.find_board(c, "Flaky")
    result, transient = run(go())
    assert result is None and transient is True


# ------------------------------------------------------------------ remembered answers
def test_board_lookups_are_remembered(tmp_path):
    conn = db.connect(tmp_path / "l.db")
    assert db.get_board_lookup(conn, "acme") is None
    db.set_board_lookup(conn, "acme", None, None, "no public careers page found")
    assert db.get_board_lookup(conn, "acme")["board"] is None
    db.set_board_lookup(conn, "acme", "lever", "acme", "matched by name")          # a later success overwrites
    assert db.get_board_lookup(conn, "acme")["slug"] == "acme"


def test_employers_in_location_groups_and_filters(tmp_path):
    from app.providers.geo import location_filter
    conn = db.connect(tmp_path / "e.db")
    jobs = [{"title": f"Dev {i}", "company": c, "location": loc, "url": f"https://x/{i}", "provider": "ashby",
             "source": "s", "description": ""} for i, (c, loc) in enumerate(
        [("Acme Pvt Ltd", "Bengaluru, Karnataka"), ("Acme", "Bangalore"), ("Beta", "Pune"), ("Gamma", "Remote")])]
    db.insert_new(conn, jobs, "dev", "India")
    got = db.employers_in_location(conn, location_filter("Bengaluru", False))
    assert [v["name"] for v in got.values()] == ["Acme Pvt Ltd"] and len(next(iter(got.values()))["jobs"]) == 2
    assert len(db.employers_in_location(conn, location_filter("India", False))) == 2      # Acme and Beta, not remote


# ------------------------------------------------------------------ the whole finder
def _patch_boards(monkeypatch, boards: dict, failing=()):
    """boards: {(board, slug): [raw jobs]}. Anything else is 'no such board'."""
    async def fake_fetch(client_, board, slug):
        if (board, slug) in failing:
            raise careers.CompanyError("timed out")
        if (board, slug) in boards:
            return boards[(board, slug)]
        raise careers.BoardNotFound(f"no {board} board named '{slug}'")
    monkeypatch.setattr(careers, "fetch_company", fake_fetch)


def _seed_saved(jobs):
    conn = db.connect()
    db.insert_new(conn, [{"provider": "serpapi", "source": "x", "description": "", **j} for j in jobs],
                  "dev", "India")
    conn.close()


def test_finder_verifies_flags_and_ranks(monkeypatch):
    conn = db.connect()
    for c in db.list_companies(conn):                 # start from an empty watchlist for a clean test
        db.delete_companies(conn, [c["id"]])
    db.add_company(conn, "Meesho", "lever", "meesho", "Mid-size")
    db.add_company(conn, "Quiet Co", "lever", "quiet", "Startup")
    conn.close()
    _seed_saved([
        {"title": "Web Developer", "company": "Acme Robotics", "location": "Bengaluru",
         "url": "https://jobs.lever.co/acmerobotics/1"},                   # board named in the link
        {"title": "Sales Manager", "company": "Mystery Ltd", "location": "Bengaluru", "url": "https://x.com/1"},
    ])
    recent = 1786469891368                                                # a past timestamp in ms
    _patch_boards(monkeypatch, {
        ("lever", "meesho"): [{"text": "Web Developer", "hostedUrl": "https://jobs.lever.co/meesho/1",
                               "createdAt": recent, "categories": {"location": "Bengaluru, India"}}] * 3 +
                             [{"text": "Chef", "hostedUrl": "https://jobs.lever.co/meesho/2", "createdAt": recent,
                               "categories": {"location": "Mumbai"}}],
        ("lever", "quiet"): [{"text": "Designer", "hostedUrl": "https://jobs.lever.co/quiet/1", "createdAt": recent,
                              "categories": {"location": "Pune"}}],      # a board with no job in Bengaluru
        ("lever", "acmerobotics"): [{"text": "Backend Developer", "hostedUrl": "https://jobs.lever.co/acmerobotics/9",
                                     "createdAt": recent, "categories": {"location": "Bangalore"},
                                     "descriptionPlain": "Acme Robotics builds robots"}],
    })
    out = company_finder.run("Bengaluru", ["Web Developer"], use_claude=False)
    names = [c["name"] for c in out["verified"]]
    assert names == ["Meesho", "Acme Robotics"]                  # Meesho: 3 title matches beats 1; Quiet Co has none here
    meesho = out["verified"][0]
    assert (meesho["open_here"], meesho["total_open"], meesho["role_matches"]) == (3, 4, 3)
    assert meesho["careers_url"] == "https://jobs.lever.co/meesho" and meesho["example_titles"] == ["Web Developer"] * 3
    assert [c["name"] for c in out["unverified"]] == ["Mystery Ltd"]
    mystery = out["unverified"][0]
    assert mystery["evidence_title"] == "Sales Manager" and "Mystery" in mystery["search_url"]
    assert db.get_board_lookup(db.connect(), "acmerobotics")["slug"] == "acmerobotics"     # remembered


def test_finder_counts_remote_only_when_asked(monkeypatch):
    conn = db.connect()
    for c in db.list_companies(conn):
        db.delete_companies(conn, [c["id"]])
    db.add_company(conn, "Remo", "ashby", "remo", "Startup")
    conn.close()
    _patch_boards(monkeypatch, {("ashby", "remo"): [{"title": "Dev", "jobUrl": "https://a/1", "location": "Remote",
                                                     "isRemote": True, "publishedAt": "2026-11-20T00:00:00Z"}]})
    assert company_finder.run("Bengaluru", use_claude=False)["verified"] == []
    out = company_finder.run("Bengaluru", use_claude=False, include_remote=True)
    assert [c["name"] for c in out["verified"]] == ["Remo"] and out["verified"][0]["open_here"] == 1


def test_a_failing_careers_page_is_not_remembered_as_missing(monkeypatch):
    _seed_saved([{"title": "Dev", "company": "Flaky Inc", "location": "Pune", "url": "https://x.com/f"}])
    _patch_boards(monkeypatch, {}, failing={("greenhouse", "flakyinc"), ("lever", "flakyinc"), ("ashby", "flakyinc")})
    out = company_finder.run("Pune", use_claude=False)
    assert [c["name"] for c in out["unverified"]] == ["Flaky Inc"]
    assert db.get_board_lookup(db.connect(), "flaky") is None          # temporary trouble: look again next time


def test_the_lookup_cap_is_applied_then_continued_on_the_next_run(monkeypatch):
    conn = db.connect()
    for c in db.list_companies(conn):
        db.delete_companies(conn, [c["id"]])
    conn.close()
    _seed_saved([{"title": "Dev", "company": f"Company{i:02d}", "location": "Pune", "url": f"https://x.com/{i}"}
                 for i in range(6)])
    _patch_boards(monkeypatch, {})
    first = company_finder.run("Pune", use_claude=False, max_lookups=2)
    assert first["not_checked_yet"] > 0
    pending_names = {c["name"] for c in first["unverified"] if c["not_checked_yet"]}
    second = company_finder.run("Pune", use_claude=False, max_lookups=2)
    assert second["not_checked_yet"] < first["not_checked_yet"]
    assert pending_names                                                # the unchecked ones were carried forward


def test_finder_merges_the_same_company_from_every_source(monkeypatch):
    monkeypatch.setattr(finder, "suggest_companies", lambda loc, roles, n: ([{"name": "meesho", "kind": "Large"}], None))
    conn = db.connect()
    for c in db.list_companies(conn):
        db.delete_companies(conn, [c["id"]])
    db.add_company(conn, "Meesho", "lever", "meesho", "Mid-size")
    conn.close()
    _seed_saved([{"title": "Dev", "company": "Meesho Pvt Ltd", "location": "Bengaluru", "url": "https://x/1"}])
    _patch_boards(monkeypatch, {("lever", "meesho"): [{"text": "Dev", "hostedUrl": "https://jobs.lever.co/meesho/1",
                                                      "categories": {"location": "Bengaluru"}}]})
    out = company_finder.run("Bengaluru", use_claude=True)
    assert len(out["verified"]) == 1 and out["candidates"] == 1
    assert set(out["verified"][0]["found_via"]) == {"your watchlist", "1 saved job here", "Claude's suggestion"}


# ------------------------------------------------------------------ the page
def _page():
    return AppTest.from_file(str(ROOT / "views/companies.py"), default_timeout=60)


def _fake_result(**over):
    base = {"location": "Bengaluru", "candidates": 3, "notice": None, "roles": [], "not_checked_yet": 0,
            "verified": [{"name": "Meesho", "kind": "Mid-size", "found_via": ["your watchlist"], "board": "lever",
                          "slug": "meesho", "total_open": 48, "open_here": 46, "posted_recently": 9,
                          "newest": "2026-11-20", "role_matches": None, "example_titles": [],
                          "careers_url": "https://jobs.lever.co/meesho", "team_size": None, "batch": None,
                          "one_liner": None, "website": None}],
            "unverified": [{"name": "Razorpay", "kind": "Mid-size", "found_via": ["Claude's suggestion"],
                            "not_checked_yet": False, "reason": "no public careers page found",
                            "search_url": "https://www.google.com/search?q=Razorpay", "evidence_title": None,
                            "evidence_url": None, "yc_hiring": False, "evidence_rank": 3, "team_size": None,
                            "batch": None, "one_liner": None, "website": None},
                           {"name": "Mystery", "kind": None, "found_via": ["1 saved job here"],
                            "not_checked_yet": False, "reason": "no public careers page found",
                            "search_url": "https://www.google.com/search?q=Mystery", "evidence_title": "Sales Manager",
                            "evidence_url": "https://x.com/1", "yc_hiring": False, "evidence_rank": 0,
                            "team_size": None, "batch": None, "one_liner": None, "website": None}],
            "sort": "activity", "yc_companies": 0}
    base.update(over)
    return base


def test_page_starts_with_the_how_it_works_cards():
    at = _page().run()
    assert not at.exception and at.text_input(key="c_loc").value == "Bengaluru"
    assert [s.value for s in at.subheader] == ["How it works"]


def test_page_passes_the_inputs_and_shows_the_results(monkeypatch):
    seen = {}

    def fake(location, roles, **kw):
        seen.update(location=location, roles=roles, **kw)
        return _fake_result()
    monkeypatch.setattr(company_finder, "run", fake)
    at = _page().run()
    at.text_input(key="c_loc").set_value("Pune").run()
    at.text_input(key="c_roles").set_value("Web Developer, QA").run()
    next(b for b in at.button if b.label == "Find companies").click().run()
    assert not at.exception
    assert seen == {"location": "Pune", "roles": ["Web Developer", "QA"], "include_remote": False,
                    "use_claude": True, "check_saved_jobs": True, "use_yc": True, "sort": "activity"}
    assert [m.value for m in at.metric] == ["3", "1", "2"]
    assert any("Hiring in Bengaluru (1)" in s.value for s in at.subheader)
    assert any("not verified (2)" in s.value for s in at.subheader)


def test_page_empty_and_pending_messages(monkeypatch):
    monkeypatch.setattr(company_finder, "run", lambda *a, **k: _fake_result(verified=[], not_checked_yet=7))
    at = _page().run()
    next(b for b in at.button if b.label == "Find companies").click().run()
    assert not at.exception
    assert any("No company's careers page showed open jobs" in i.value for i in at.info)
    assert any("7 companies were left unchecked" in i.value for i in at.info)


def test_page_survives_a_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(company_finder, "run", boom)
    at = _page().run()
    next(b for b in at.button if b.label == "Find companies").click().run()
    assert not at.exception and any("network down" in e.value for e in at.error)


def test_page_adds_verified_companies_to_the_watchlist(monkeypatch):
    monkeypatch.setattr(company_finder, "run", lambda *a, **k: _fake_result())
    conn = db.connect()
    db.delete_companies(conn, [c["id"] for c in db.list_companies(conn) if c["slug"] == "meesho"])
    conn.close()
    at = _page().run()
    next(b for b in at.button if b.label == "Find companies").click().run()
    at.multiselect(key="c_add").set_value(["Meesho"]).run()
    next(b for b in at.button if b.label == "Add selected").click().run()
    assert not at.exception
    assert any(c["slug"] == "meesho" for c in db.list_companies(db.connect()))


def test_page_filters_the_unverified_list(monkeypatch):
    monkeypatch.setattr(company_finder, "run", lambda *a, **k: _fake_result())
    at = _page().run()
    next(b for b in at.button if b.label == "Find companies").click().run()
    at.radio(key="c_show").set_value("Only with a saved job").run()
    assert not at.exception
