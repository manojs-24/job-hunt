import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest

from app import company_finder, db, finder, yc
from app.providers import careers


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    import app.config
    monkeypatch.setattr(app.config, "load_dotenv", lambda *a, **k: None)
    for key in ("JSEARCH_RAPIDAPI_KEY", "SERPAPI_KEY", "OPENWEB_NINJA_API_KEY", "ANTHROPIC_API_KEY",
                "ANTHROPIC_BASE_URL"):
        monkeypatch.setenv(key, "")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "y.db")
    monkeypatch.setattr(yc, "load", lambda *a, **k: [])            # never download the real 11 MB list in a test


def run(coro):
    return asyncio.run(coro)


def entry(name, **kw):
    base = {"name": name, "website": f"https://www.{name.lower().replace(' ', '')}.com/", "locations": "Bengaluru, KA, India",
            "team_size": 20, "batch": "Winter 2023", "status": "Active", "hiring": True,
            "one_liner": f"{name} does things", "url": "", "industry": ""}
    return {**base, **kw}


# ------------------------------------------------------------------ reading the Y Combinator list
RAW = [
    {"name": "Inito", "website": "https://www.inito.com/", "all_locations": "Bengaluru, KA, India", "team_size": 100,
     "batch": "Winter 2019", "status": "Active", "isHiring": True, "one_liner": "Hormone tracking", "url": "u",
     "industry": "Healthcare"},
    {"name": "NoSize", "all_locations": "Pune, India", "team_size": None, "isHiring": False, "status": "Active"},
    {"name": "WeirdSize", "all_locations": "Pune, India", "team_size": "lots", "isHiring": 1, "status": "Active"},
    {"name": "", "all_locations": "Pune"},
    {"name": "Gone", "all_locations": "Bengaluru, India", "status": "Inactive"},
    {"name": "Bought", "all_locations": "Bengaluru, India", "status": "Acquired"},
    {"name": "Multi", "all_locations": "San Francisco, CA, USA; Bengaluru, KA, India", "status": "Active"},
    {"name": "Abroad", "all_locations": "London, UK", "status": "Active"},
]


def _client(payload, status=200):
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=payload)))


def test_load_cleans_the_list_and_caches_it(tmp_path, monkeypatch):
    monkeypatch.undo()                                                  # use the real `yc.load` for this test
    cache = tmp_path / "yc.json"
    got = yc.load(client=_client(RAW), cache=cache)
    assert [c["name"] for c in got][:2] == ["Inito", "NoSize"] and "" not in [c["name"] for c in got]
    inito = got[0]
    assert (inito["team_size"], inito["hiring"], inito["locations"]) == (100, True, "Bengaluru, KA, India")
    assert next(c for c in got if c["name"] == "WeirdSize")["team_size"] is None        # junk size becomes unknown
    assert next(c for c in got if c["name"] == "NoSize")["website"] == ""
    assert json.loads(cache.read_text(encoding="utf-8"))[0]["name"] == "Inito"
    # a second call must use the saved copy and never touch the network
    def explode(request):
        raise AssertionError("downloaded again")
    again = yc.load(client=httpx.Client(transport=httpx.MockTransport(explode)), cache=cache)
    assert [c["name"] for c in again] == [c["name"] for c in got]


def test_a_failed_download_falls_back_to_an_old_copy_or_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.undo()
    cache = tmp_path / "yc.json"
    with pytest.raises(yc.YcError, match="Could not load the Y Combinator list"):
        yc.load(client=_client({}, status=503), cache=cache)
    with pytest.raises(yc.YcError):
        yc.load(client=_client({"not": "a list"}), cache=cache)
    cache.write_text(json.dumps([{"name": "Old", "locations": "Pune", "status": "Active"}]), encoding="utf-8")
    import os, time
    os.utime(cache, (time.time() - 30 * 86400,) * 2)                     # a month old: stale, but better than nothing
    assert yc.load(client=_client({}, status=503), cache=cache, max_age_days=7)[0]["name"] == "Old"


def test_in_location_matches_any_office_and_skips_dead_companies():
    data = [yc._clean(r) for r in RAW if r["name"]]
    names = lambda loc, **kw: sorted(c["name"] for c in yc.in_location(data, loc, **kw))
    assert names("Bengaluru") == ["Inito", "Multi"]                      # Inactive and Acquired are dropped
    assert names("Bangalore") == ["Inito", "Multi"]                      # spelling variants
    assert names("Bengaluru", only_active=False) == ["Bought", "Gone", "Inito", "Multi"]
    assert names("Pune") == ["NoSize", "WeirdSize"]
    assert names("London") == ["Abroad"]


def test_kind_and_size_labels_and_website_host():
    assert [yc.kind_of({"team_size": s}) for s in (None, 5, 200, 201, 1000, 1001)] == \
        ["Startup", "Startup", "Startup", "Mid-size", "Mid-size", "Large"]
    assert yc.size_label(None) == "size unknown" and yc.size_label(12) == "12 people"
    assert yc.website_host("https://www.100x.bot?utm_source=x&y=1") == "100x.bot"
    assert yc.website_host("http://inito.com/about?x=1") == "inito.com" and yc.website_host("") == ""


# ------------------------------------------------------------------ SmartRecruiters and Workable
SR_JOB = {"id": "7440", "name": "Backend Engineer", "releasedDate": "2026-11-10T00:00:00.000Z",
          "company": {"identifier": "Freshworks", "name": "Freshworks"},
          "location": {"city": "Bengaluru", "country": "in", "remote": False, "fullLocation": "Bengaluru, , India"}}
WK_JOB = {"title": "ML Engineer", "url": "https://apply.workable.com/j/ABC123", "published_on": "2026-11-12",
          "telecommuting": True, "description": "<p>Build &amp; ship</p>",
          "locations": [{"city": "Paris", "region": "Ile-de-France", "country": "France"}]}


def sr_handler(request: httpx.Request) -> httpx.Response:
    path, offset = request.url.path, int(request.url.params.get("offset", 0))
    if "/companies/bigco/" in path:                                      # 250 postings across three pages
        n = max(0, min(100, 250 - offset))
        return httpx.Response(200, json={"content": [{**SR_JOB, "id": str(offset + i)} for i in range(n)]})
    if "/companies/freshworks/" in path.lower():
        return httpx.Response(200, json={"content": [SR_JOB]})
    return httpx.Response(200, json={"offset": 0, "totalFound": 0, "content": []})      # SmartRecruiters' "unknown" answer


def wk_handler(request: httpx.Request) -> httpx.Response:
    if "/accounts/hf" in request.url.path:
        return httpx.Response(200, json={"name": "HF", "jobs": [WK_JOB]})
    if "/accounts/busy" in request.url.path:
        return httpx.Response(429)
    return httpx.Response(404)


def test_smartrecruiters_pages_through_and_treats_empty_as_not_found():
    async def go(slug):
        async with httpx.AsyncClient(transport=httpx.MockTransport(sr_handler)) as c:
            return await careers.fetch_company(c, "smartrecruiters", slug)
    assert len(run(go("bigco"))) == 250
    assert len(run(go("Freshworks"))) == 1
    with pytest.raises(careers.BoardNotFound):
        run(go("nobody"))


def test_smartrecruiters_and_workable_jobs_are_normalised():
    sr = careers.normalise_job("smartrecruiters", "Freshworks", SR_JOB)
    assert (sr["title"], sr["location"], sr["posted_at"]) == ("Backend Engineer", "Bengaluru, , India", "2026-11-10T00:00:00.000Z")
    assert sr["url"] == "https://jobs.smartrecruiters.com/Freshworks/7440" and sr["remote_flag"] is None
    wk = careers.normalise_job("workable", "HF", WK_JOB)
    assert wk["location"] == "Paris, Ile-de-France, France" and wk["remote_flag"] is True
    assert wk["description"] == "Build & ship" and wk["url"] == "https://apply.workable.com/j/ABC123"


def test_workable_reports_rate_limits_without_calling_them_a_missing_company():
    async def go(slug):
        async with httpx.AsyncClient(transport=httpx.MockTransport(wk_handler)) as c:
            return await careers.fetch_company(c, "workable", slug)
    assert len(run(go("hf"))) == 1
    with pytest.raises(careers.BoardNotFound):
        run(go("nobody"))
    with pytest.raises(careers.CompanyError, match="rate limited") as err:
        run(go("busy"))
    assert not isinstance(err.value, careers.BoardNotFound)


def test_guessing_skips_workable_but_a_known_name_still_uses_it():
    assert "workable" in careers.BOARDS and "workable" not in careers.GUESS_BOARDS
    seen = []

    async def fake(client, board, slug):
        seen.append(board)
        raise careers.BoardNotFound("no")

    async def go():
        async with httpx.AsyncClient() as c:
            return await finder.find_board(c, "Some Company")
    original = careers.fetch_company
    careers.fetch_company = fake
    try:
        run(go())
    finally:
        careers.fetch_company = original
    assert "workable" not in seen and set(seen) == set(careers.GUESS_BOARDS)


def test_links_to_the_new_tools_name_the_company():
    assert finder.board_from_url("https://jobs.smartrecruiters.com/Freshworks/7440") == ("smartrecruiters", "Freshworks")
    assert finder.board_from_url("https://apply.workable.com/hf/j/ABC") == ("workable", "hf")
    assert finder.board_from_url("https://apply.workable.com/j/ABC123") is None           # a /j/ link names no company
    assert finder.careers_url("smartrecruiters", "Freshworks") == "https://jobs.smartrecruiters.com/Freshworks"
    assert finder.careers_url("workable", "hf") == "https://apply.workable.com/hf/"
    assert finder.slug_guesses("Freshworks")[0] == "freshworks"


def test_smartrecruiters_names_must_match_the_company():
    jobs = [{**SR_JOB, "company": {"identifier": "Blue", "name": "Blue Origin"}}] * 3
    assert not finder.looks_like("Blue Yonder", "smartrecruiters", jobs)
    assert finder.looks_like("Freshworks", "smartrecruiters", [SR_JOB] * 3)


# ------------------------------------------------------------------ the finder with the YC source
GH = {"jobs": [{"id": 1, "title": "Engineer", "company_name": "Inito", "absolute_url": "https://gh/inito/1",
                "location": {"name": "Bengaluru"}, "first_published": "2026-11-20T00:00:00Z", "content": ""}]}


def _boards(monkeypatch, known: dict):
    seen = []

    async def fake(client, board, slug):
        seen.append((board, slug))
        if (board, slug) in known:
            return known[(board, slug)]
        raise careers.BoardNotFound("no")
    monkeypatch.setattr(careers, "fetch_company", fake)
    return seen


def _no_watchlist():
    conn = db.connect()
    for c in db.list_companies(conn):
        db.delete_companies(conn, [c["id"]])
    conn.close()


def test_yc_startups_become_candidates_and_the_website_name_is_tried(monkeypatch):
    _no_watchlist()
    monkeypatch.setattr(yc, "load", lambda *a, **k: [
        entry("Inito", website="https://www.inito.com/", team_size=100),            # board name matches the website
        entry("Odd Name Labs", website="https://getoddname.com", team_size=8),      # website name differs from the company
        entry("Pune Only", locations="Pune, India"),                                 # wrong city: not a candidate
        entry("Dead Co", status="Inactive")])                                        # shut down: skipped
    seen = _boards(monkeypatch, {("greenhouse", "inito"): GH["jobs"], ("lever", "getoddname"): [
        {"text": "Dev", "hostedUrl": "https://jobs.lever.co/getoddname/1", "categories": {"location": "Bengaluru"},
         "descriptionPlain": "Odd Name Labs builds things"} for _ in range(3)]})
    out = company_finder.run("Bengaluru", use_claude=False, check_saved_jobs=False)
    assert out["yc_companies"] == 2 and out["candidates"] == 2
    by = {c["name"]: c for c in out["verified"]}
    assert set(by) == {"Inito", "Odd Name Labs"}
    inito = by["Inito"]
    assert (inito["team_size"], inito["batch"], inito["one_liner"]) == (100, "Winter 2023", "Inito does things")
    assert by["Odd Name Labs"]["slug"] == "getoddname"                               # found through the website's name
    assert ("lever", "getoddname") in seen and not any(s == "punelyonly" for _, s in seen)


def test_yc_company_without_a_readable_careers_page_is_listed_as_unverified(monkeypatch):
    _no_watchlist()
    monkeypatch.setattr(yc, "load", lambda *a, **k: [entry("Hiring Co", hiring=True), entry("Quiet Co", hiring=False)])
    _boards(monkeypatch, {})
    out = company_finder.run("Bengaluru", use_claude=False, check_saved_jobs=False)
    assert out["verified"] == []
    by = {c["name"]: c for c in out["unverified"]}
    assert by["Hiring Co"]["yc_hiring"] is True and by["Hiring Co"]["evidence_rank"] == 1
    assert by["Quiet Co"]["yc_hiring"] is False and by["Quiet Co"]["evidence_rank"] == 2
    assert [c["name"] for c in out["unverified"]] == ["Hiring Co", "Quiet Co"]       # marked-hiring first


def test_a_saved_job_outranks_a_yc_flag_and_everything_is_merged(monkeypatch):
    _no_watchlist()
    conn = db.connect()
    db.insert_new(conn, [{"title": "Dev", "company": "Alpha Pvt Ltd", "location": "Bengaluru", "url": "https://x/1",
                          "provider": "serpapi", "source": "s", "description": ""}], "dev", "India")
    conn.close()
    monkeypatch.setattr(yc, "load", lambda *a, **k: [entry("Alpha", hiring=True), entry("Beta", hiring=True)])
    _boards(monkeypatch, {})
    out = company_finder.run("Bengaluru", use_claude=False)
    assert [c["name"] for c in out["unverified"]] == ["Alpha Pvt Ltd", "Beta"]       # one Alpha, not two
    assert out["unverified"][0]["evidence_rank"] == 0 and set(out["unverified"][0]["found_via"]) == \
        {"1 saved job here", "Y Combinator list"}


def test_smallest_first_puts_known_small_teams_first_and_unknown_last(monkeypatch):
    _no_watchlist()
    monkeypatch.setattr(yc, "load", lambda *a, **k: [
        entry("Big One", team_size=900), entry("Tiny One", team_size=4), entry("Mid One", team_size=60)])
    shared = {("greenhouse", n): [{"id": 1, "title": "Engineer", "company_name": c, "absolute_url": "https://gh/x",
                                    "location": {"name": "Bengaluru"}, "first_published": "2026-11-20T00:00:00Z"}]
              for n, c in (("bigone", "Big One"), ("tinyone", "Tiny One"), ("midone", "Mid One"))}
    _boards(monkeypatch, shared)
    by_size = company_finder.run("Bengaluru", use_claude=False, check_saved_jobs=False, sort="smallest")
    assert [c["name"] for c in by_size["verified"]] == ["Tiny One", "Mid One", "Big One"]
    assert by_size["sort"] == "smallest"


def test_a_yc_failure_is_reported_but_does_not_stop_the_run(monkeypatch):
    _no_watchlist()
    def boom(*a, **k):
        raise yc.YcError("Could not load the Y Combinator list (ConnectError).")
    monkeypatch.setattr(yc, "load", boom)
    _boards(monkeypatch, {})
    out = company_finder.run("Bengaluru", use_claude=False, check_saved_jobs=False)
    assert "Could not load the Y Combinator list" in out["notice"] and out["candidates"] == 0
    assert company_finder.run("Bengaluru", use_claude=False, check_saved_jobs=False, use_yc=False)["notice"] is None


# ------------------------------------------------------------------ the page
def _page():
    return AppTest.from_file(str(ROOT / "views/companies.py"), default_timeout=60)


def _result(sort="activity"):
    v = {"name": "Inito", "kind": "Startup", "found_via": ["Y Combinator list"], "board": "greenhouse", "slug": "inito",
         "total_open": 5, "open_here": 3, "posted_recently": 2, "newest": "2026-11-20", "role_matches": None,
         "example_titles": [], "careers_url": "https://job-boards.greenhouse.io/inito", "team_size": 100,
         "batch": "Winter 2019", "one_liner": "Hormone tracking", "website": "https://inito.com/"}
    u = {"name": "Wifi Dabba", "kind": "Startup", "found_via": ["Y Combinator list"], "not_checked_yet": False,
         "reason": "no public careers page found", "search_url": "https://g/?q=x", "evidence_title": None,
         "evidence_url": None, "yc_hiring": True, "evidence_rank": 1, "team_size": 21, "batch": "Winter 2020",
         "one_liner": "Broadband", "website": "https://dabba.com"}
    return {"location": "Bengaluru", "candidates": 2, "notice": None, "roles": [], "not_checked_yet": 0,
            "verified": [v], "unverified": [u], "sort": sort, "yc_companies": 2}


def test_page_sends_the_new_options(monkeypatch):
    seen = {}

    def fake(location, roles, **kw):
        seen.update(kw)
        return _result("smallest" if kw["sort"] == "smallest" else "activity")
    monkeypatch.setattr(company_finder, "run", fake)
    at = _page().run()
    assert not at.exception
    next(b for b in at.button if b.label == "Find companies").click().run()
    assert seen["use_yc"] is True and seen["sort"] == "activity"
    at.radio(key="c_sort").set_value("Smallest teams first").run()
    at.checkbox[0].uncheck().run()
    next(b for b in at.button if b.label == "Find companies").click().run()
    assert not at.exception and seen["use_yc"] is False and seen["sort"] == "smallest"
    assert any("Ordered by team size" in c.value for c in at.caption)


def test_page_shows_the_yc_evidence_filter(monkeypatch):
    monkeypatch.setattr(company_finder, "run", lambda *a, **k: _result())
    at = _page().run()
    next(b for b in at.button if b.label == "Find companies").click().run()
    assert not at.exception
    for choice in ("Only Y Combinator marked hiring", "Only with a saved job", "Only Claude's suggestions",
                   "Strongest evidence first"):
        at.radio(key="c_show").set_value(choice).run()
        assert not at.exception


def test_page_hides_yc_columns_when_no_company_has_yc_facts(monkeypatch):
    plain = _result()
    for c in plain["verified"]:
        c.update(team_size=None, batch=None, one_liner=None, website=None, found_via=["your watchlist"])
    monkeypatch.setattr(company_finder, "run", lambda *a, **k: plain)
    at = _page().run()
    next(b for b in at.button if b.label == "Find companies").click().run()
    assert not at.exception
