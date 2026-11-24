import asyncio
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest

from app import db, service
from app.providers import careers, geo, remote_boards
from app.providers.base import html_to_text
from app.relevance import Matcher, rank


@pytest.fixture(autouse=True)
def no_live_calls(monkeypatch, tmp_path):
    import app.config
    monkeypatch.setattr(app.config, "load_dotenv", lambda *a, **k: None)
    for key in ("JSEARCH_RAPIDAPI_KEY", "SERPAPI_KEY", "OPENWEB_NINJA_API_KEY",
                "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
        monkeypatch.setenv(key, "")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "s.db")


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ geography
@pytest.mark.parametrize("loc,expected", [
    ("Bengaluru, Karnataka", True), ("Remote", True), ("Worldwide", True), ("Remote-India (PEO)", True),
    ("Remote - Asia Pacific", True), ("Remote (LATAM, Europe, USA, Canada, APAC)", True), ("", True),
    (None, True), ("Pune; Remote, US", True), ("New York; Remote", True),
    ("Remote, EST United States", False), ("EMEA", False), ("USA", False), ("London, UK", False),
    ("New York; London", False), ("Remote, United States", False),
])
def test_open_to_india(loc, expected):
    assert geo.open_to_india(loc) is expected


# ------------------------------------------------------------------ title matching
def test_matcher_keeps_relevant_titles_and_drops_the_rest():
    m = Matcher(["Web Developer", "Full Stack Developer"], ["React", "Node.js"])
    assert m.score("Senior Full Stack Engineer") > 0
    assert m.score("Frontend Developer (React)") > 0               # skill word matches
    assert m.score("Web Developer") > m.score("Software Developer - Web")
    assert m.score("Sales Engineer") == 0                          # "engineer" alone is not enough
    assert m.score("Customer Support Specialist") == 0
    assert m.score(None) == 0


def test_matcher_handles_odd_skill_names():
    m = Matcher(["Backend Developer"], ["C++", "C#", "Node.js", "Machine Learning", "R"])
    assert m.score("C++ Developer") > 0 and m.score("Node.js Backend Engineer") > 0
    assert m.score("Marketing Manager") == 0                       # multi-word and 1-letter skills are ignored


def test_matcher_with_only_generic_role_words_still_matches_something():
    assert Matcher(["Software Engineer"]).score("Software Engineer, Platform") > 0


def test_rank_demotes_senior_titles_for_early_career():
    assert rank("Staff Engineer", 5, 2) < rank("Engineer", 5, 2)
    assert rank("Staff Engineer", 5, 9) == rank("Engineer", 5, 9)


# ------------------------------------------------------------------ html
def test_html_to_text_decodes_double_encoded_greenhouse_html():
    out = html_to_text("&lt;p&gt;We use &amp;amp; love React&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Node&lt;/li&gt;&lt;/ul&gt;")
    assert out == "We use & love React\n- Node"
    assert html_to_text(None) == ""


# ------------------------------------------------------------------ careers pages (mock transport)
GH = {"jobs": [
    {"id": 1, "title": "Web Developer", "company_name": "Acme", "absolute_url": "https://gh/1",
     "location": {"name": "Bengaluru, India"}, "first_published": "2026-11-01T10:00:00-04:00",
     "content": "&lt;p&gt;React and Node&lt;/p&gt;"},
    {"id": 2, "title": "Web Developer", "company_name": "Acme", "absolute_url": "https://gh/2",
     "location": {"name": "Remote, United States"}, "content": ""},
    {"id": 3, "title": "", "absolute_url": "https://gh/3", "location": {"name": "Pune"}},
]}
LEVER = [{"id": "a", "text": "Backend Engineer", "hostedUrl": "https://lever/a", "createdAt": 1786469891368,
          "categories": {"location": "Mumbai", "allLocations": ["Mumbai", "Remote"]},
          "descriptionPlain": "Python", "workplaceType": "remote"},
         {"id": "b", "text": "Backend Engineer", "hostedUrl": "https://lever/b", "createdAt": None,
          "categories": {"location": "Singapore"}, "descriptionPlain": ""}]
ASHBY = {"jobs": [{"id": "x", "title": "Platform Engineer", "jobUrl": "https://ashby/x", "location": "Remote",
                   "isRemote": True, "secondaryLocations": [{"location": "Pune"}], "publishedAt": "2026-10-01",
                   "descriptionPlain": "Go", "descriptionHtml": "<p>Go</p>"}]}


def handler(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if "greenhouse" in host:
        return httpx.Response(200, json=GH) if "acme" in path else httpx.Response(404)
    if "lever" in host:
        return httpx.Response(200, json=LEVER) if "acme" in path else httpx.Response(404)
    if "ashby" in host:
        if "slow" in path:
            raise httpx.ReadTimeout("t")
        if "limited" in path:
            return httpx.Response(429)
        if "garbage" in path:
            return httpx.Response(200, content=b"<html>not json</html>")
        return httpx.Response(200, json=ASHBY) if "acme" in path else httpx.Response(404)
    return httpx.Response(500)


def client():
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_each_board_is_normalised_and_filtered_by_location():
    comps = [{"id": 1, "name": "Acme", "board": "greenhouse", "slug": "acme", "kind": "Startup"},
             {"id": 2, "name": "Acme", "board": "lever", "slug": "acme", "kind": "Startup"},
             {"id": 3, "name": "Acme", "board": "ashby", "slug": "acme", "kind": "Mid-size"}]

    async def go():
        async with client() as c:
            return await careers.search_companies(c, comps)
    out = run(go())
    jobs = {j["url"]: j for j in out["jobs"]}
    assert set(jobs) == {"https://gh/1", "https://lever/a", "https://ashby/x"}      # US-only, Singapore, untitled dropped
    gh = jobs["https://gh/1"]
    assert gh["location"] == "Bengaluru, India" and gh["description"] == "React and Node" and gh["provider"] == "greenhouse"
    assert gh["source"] == "Acme careers" and gh["company_kind"] == "Startup" and gh["remote"] is False
    assert jobs["https://lever/a"]["remote"] is True and jobs["https://lever/a"]["posted_at"].startswith("2026-")
    assert jobs["https://ashby/x"]["remote"] is True and jobs["https://ashby/x"]["company_kind"] == "Mid-size"
    assert out["per_company"][1] == {"name": "Acme", "status": "ok", "detail": "", "total": 3, "kept": 1}


@pytest.mark.parametrize("slug,message", [("nobody", "no ashby board named"), ("slow", "timed out"),
                                         ("limited", "rate limited"), ("garbage", "unreadable")])
def test_company_failures_are_reported_and_do_not_stop_the_others(slug, message):
    comps = [{"id": 1, "name": "Bad", "board": "ashby", "slug": slug, "kind": "Startup"},
             {"id": 2, "name": "Acme", "board": "greenhouse", "slug": "acme", "kind": "Startup"}]

    async def go():
        async with client() as c:
            return await careers.search_companies(c, comps)
    out = run(go())
    assert out["per_company"][1]["status"] == "error" and message in out["per_company"][1]["detail"]
    assert out["per_company"][2]["status"] == "ok" and len(out["jobs"]) == 1


def test_unknown_board_name_is_rejected():
    async def go():
        async with client() as c:
            await careers.fetch_company(c, "workday", "x")
    with pytest.raises(careers.CompanyError, match="unknown board"):
        run(go())


# ------------------------------------------------------------------ remote boards (mock transport)
REMOTIVE = {"jobs": [
    {"title": "Full-Stack Developer", "company_name": "Fluence", "url": "https://remotive/1", "tags": ["react"],
     "category": "Software Development", "candidate_required_location": "Worldwide", "salary": "$40k",
     "publication_date": "2026-11-19T14:58:42", "description": "<p>Build things</p>"},
    {"title": "Full-Stack Developer", "company_name": "UsOnly", "url": "https://remotive/2", "tags": [],
     "category": "Software Development", "candidate_required_location": "USA"},
    {"title": "Chef", "company_name": "Kitchen", "url": "https://remotive/3", "tags": [], "category": "Other",
     "candidate_required_location": "Worldwide"}]}
REMOTEOK = [{"legal": "Link back to us"},
            {"position": "React Developer", "company": "Shop", "url": "https://rok/1", "tags": ["react"],
             "location": "Worldwide", "date": "2026-11-23T00:20:04+00:00", "salary_min": "40000",
             "salary_max": "60000", "description": "<b>x</b>"},
            {"position": "React Developer", "company": "Shop2", "url": "https://rok/2", "tags": [],
             "location": "US Only"}]


def remote_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=REMOTIVE if "remotive" in request.url.host else REMOTEOK)


def test_remote_boards_filter_by_role_and_region_and_credit_the_source():
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(remote_handler)) as c:
            return await remote_boards.search_remote_boards(c, ["Full Stack Developer", "React"])
    jobs, report = run(go())
    assert report == {"remotive": {"status": "ok", "count": 1}, "remoteok": {"status": "ok", "count": 1}}
    by_url = {j["url"]: j for j in jobs}
    assert set(by_url) == {"https://remotive/1", "https://rok/1"}          # US-only and the chef are dropped
    assert by_url["https://remotive/1"]["source"] == "Remotive" and by_url["https://rok/1"]["source"] == "Remote OK"
    assert by_url["https://rok/1"]["salary"] == "USD 40000-60000 per year" and all(j["remote"] for j in jobs)


def test_one_remote_board_failing_keeps_the_other():
    def h(request):
        if "remotive" in request.url.host:
            return httpx.Response(503)
        return httpx.Response(200, json=REMOTEOK)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as c:
            return await remote_boards.search_remote_boards(c, ["React"])
    jobs, report = run(go())
    assert report["remotive"]["status"] == "error" and report["remoteok"]["status"] == "ok" and len(jobs) == 1


# ------------------------------------------------------------------ watchlist in the database
def test_watchlist_is_seeded_once_and_removals_stick(tmp_path):
    path = tmp_path / "w.db"
    conn = db.connect(path)
    n = len(db.list_companies(conn))
    assert n >= 30
    victim = db.list_companies(conn)[0]
    assert db.delete_companies(conn, [victim["id"]]) == 1
    conn.close()
    conn = db.connect(path)                                      # reconnecting must not re-add it
    assert len(db.list_companies(conn)) == n - 1


def test_add_update_and_duplicate_company(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    cid = db.add_company(conn, "Razorpay", "lever", "Razorpay", "Mid-size")
    assert cid and db.add_company(conn, "Razorpay Again", "lever", "razorpay") is None   # slug is case-insensitive
    db.update_company(conn, cid, enabled=False, kind="Large")
    row = next(c for c in db.list_companies(conn) if c["id"] == cid)
    assert (row["enabled"], row["kind"], row["slug"]) == (0, "Large", "razorpay")
    assert cid not in [c["id"] for c in db.list_companies(conn, enabled=True)]
    db.record_company_check(conn, cid, 12, None)
    assert next(c for c in db.list_companies(conn) if c["id"] == cid)["last_total"] == 12


def test_insert_cap_leaves_the_rest_for_later(tmp_path):
    conn = db.connect(tmp_path / "i.db")
    jobs = [{"title": f"Dev {i}", "company": f"Co{i}", "location": "Pune", "url": f"https://x/{i}",
             "provider": "ashby", "source": "x careers", "description": "", "company_kind": "Startup"} for i in range(5)]
    new = db.insert_new(conn, jobs, "dev", "Startups & remote", limit_new=2)
    assert len(new) == 2 and [("id" in j) for j in jobs] == [True, True, False, False, False]
    assert len(db.insert_new(conn, jobs, "dev", "Startups & remote", limit_new=2)) == 2     # next run: the next two
    assert db.query_jobs(conn, kind="startup")[0]["company_kind"] == "Startup"


def test_old_database_gains_company_kind(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT UNIQUE, title TEXT, "
                "company TEXT, company_key TEXT, location TEXT, remote INTEGER, salary TEXT, posted_at TEXT, url TEXT, "
                "source TEXT, provider TEXT, description TEXT, exp_required REAL, score INTEGER, reason TEXT, "
                "query_role TEXT, query_location TEXT, first_seen TEXT, last_seen TEXT)")
    raw.execute("INSERT INTO jobs (fingerprint, title, company, company_key, first_seen, last_seen) "
                "VALUES ('f','Old','Co','co','t','t')")
    raw.commit()
    raw.close()
    conn = db.connect(path)
    assert db.query_jobs(conn)[0]["company_kind"] is None and len(db.list_companies(conn)) >= 30


# ------------------------------------------------------------------ the whole pipeline
def _fake_sources(monkeypatch, per_company=None):
    async def fake(companies, roles, include_remote, location=""):
        jobs = [
            {"title": "Senior Web Developer", "company": "Acme", "location": "Pune", "url": "https://a/1",
             "provider": "greenhouse", "source": "Acme careers", "description": "React", "company_kind": "Startup",
             "remote": False, "posted_at": "2026-11-20T00:00:00+00:00", "salary": None},
            {"title": "Web Developer", "company": "Beta", "location": "Remote", "url": "https://b/1",
             "provider": "ashby", "source": "Beta careers", "description": "Node", "company_kind": "Mid-size",
             "remote": True, "posted_at": "2026-11-21T00:00:00+00:00", "salary": None},
            {"title": "Account Executive", "company": "Gamma", "location": "Pune", "url": "https://g/1",
             "provider": "lever", "source": "Gamma careers", "description": "", "company_kind": "Startup",
             "remote": False, "posted_at": None, "salary": None},
        ]
        rep = per_company or {c["id"]: {"name": c["name"], "status": "ok", "detail": "", "total": 3, "kept": 1}
                              for c in companies}
        return {"jobs": jobs, "per_company": rep}, [], {}
    monkeypatch.setattr(service, "_fetch_startup_sources", fake)


def test_startup_search_filters_scores_skills_and_never_repeats(monkeypatch):
    _fake_sources(monkeypatch)
    scored = {}

    def fake_score(label, experience, jobs, skills=None):
        scored.update(n=len(jobs), skills=skills, titles=[j["title"] for j in jobs])
        return {j["id"]: {"score": 75, "exp_required": None, "reason": "r", "skills_matched": "React",
                          "skills_missing": None} for j in jobs}, None
    monkeypatch.setattr(service, "score_jobs", fake_score)

    out = service.run_startup_search(["Web Developer"], 2, ["React"])
    assert (out["fetched"], out["matched_title"], out["new_count"]) == (3, 2, 2)      # the Account Executive is dropped
    assert scored["n"] == 2 and scored["skills"] == ["React"]
    assert scored["titles"][0] == "Web Developer"                                     # exact title ranks above the "Senior" one
    conn = db.connect()
    rows = db.query_jobs(conn, only_ids=out["ids"])
    assert {r["company_kind"] for r in rows} == {"Startup", "Mid-size"} and all(r["score"] == 75 for r in rows)
    assert db.query_jobs(conn, kind="Mid-size")[0]["company"] == "Beta"
    assert all(c["last_checked"] for c in db.list_companies(conn, enabled=True))      # check times recorded
    conn.close()

    scored.clear()
    again = service.run_startup_search(["Web Developer"], 2, ["React"])
    assert again["new_count"] == 0 and again["already_seen"] == 2
    assert scored["n"] == 0 and scored["titles"] == []                                 # Claude was handed no jobs


def test_startup_search_cap_keeps_the_best_and_reports_the_rest(monkeypatch):
    _fake_sources(monkeypatch)
    monkeypatch.setattr(service, "score_jobs", lambda *a, **k: ({}, None))
    out = service.run_startup_search(["Web Developer"], 2, max_new=1)
    assert out["new_count"] == 1 and out["left_for_later"] == 1
    conn = db.connect()
    assert db.query_jobs(conn, only_ids=out["ids"])[0]["title"] == "Web Developer"     # the closer title won the slot
    conn.close()
    nxt = service.run_startup_search(["Web Developer"], 2, max_new=1)                  # next run picks up the other one
    assert nxt["new_count"] == 1 and nxt["left_for_later"] == 0


def test_a_broken_company_is_recorded_without_failing_the_run(monkeypatch):
    conn = db.connect()
    cid = db.list_companies(conn)[0]["id"]
    conn.close()
    _fake_sources(monkeypatch, per_company={cid: {"name": "X", "status": "error", "detail": "timed out",
                                                   "total": None, "kept": 0}})
    monkeypatch.setattr(service, "score_jobs", lambda *a, **k: ({}, None))
    out = service.run_startup_search(["Web Developer"], 2, company_ids=[cid])
    assert out["new_count"] == 2
    row = next(c for c in db.list_companies(db.connect()) if c["id"] == cid)
    assert row["last_error"] == "timed out" and row["last_total"] is None


# ------------------------------------------------------------------ the page
def _page():
    return AppTest.from_file(str(ROOT / "views/startups.py"), default_timeout=60)


def test_page_renders_with_the_seeded_list():
    at = _page().run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Find jobs", "Company list"]
    assert any("Will check" in i.value for i in at.info)
    assert any("Your companies (32)" in s.value for s in at.subheader)


def test_page_find_jobs_flow(monkeypatch):
    seen = {}

    def fake(roles, experience, skills=None, **kw):
        seen.update(roles=roles, exp=experience, skills=skills, **kw)
        return {"per_company": {}, "remote": {}, "companies_checked": 0, "fetched": 5, "matched_title": 3,
                "new_count": 2, "already_seen": 1, "left_for_later": 4, "notice": None, "duplicates_in_batch": 0,
                "ids": [], "new_ids": []}
    monkeypatch.setattr(service, "run_startup_search", fake)
    at = _page().run()
    next(b for b in at.button if b.label == "Find jobs").click().run()
    assert not at.exception
    assert seen["roles"] == ["Web Developer", "Full Stack Developer"] and seen["include_remote"] is True
    assert seen["max_new"] == 60 and len(seen["company_ids"]) > 0
    assert [m.value for m in at.metric] == ["5", "3", "2", "1"]
    assert any("4 more new matches" in i.value for i in at.info)


def test_page_survives_a_failing_search(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(service, "run_startup_search", boom)
    at = _page().run()
    next(b for b in at.button if b.label == "Find jobs").click().run()
    assert not at.exception and any("network down" in e.value for e in at.error)


def test_page_find_button_needs_a_role():
    at = _page().run()
    at.text_input(key="s_roles").set_value("  ").run()
    assert next(b for b in at.button if b.label == "Find jobs").disabled


def test_page_uses_a_saved_resume_profile():
    conn = db.connect()
    db.save_profile(conn, name="Jane", summary="", experience=3, skills=["React", "Go"],
                    roles=["Frontend Developer", "Backend Developer"], location="Pune")
    conn.close()
    at = _page().run()
    at.selectbox(key="s_profile").select_index(1).run()
    assert not at.exception
    assert at.text_input(key="s_roles").value == "Frontend Developer, Backend Developer"
    assert at.number_input(key="s_exp").value == 3.0
    assert any("2 skills from your resume" in c.value for c in at.caption)


def test_page_add_company_validates_and_adds(monkeypatch):
    async def fake_fetch(client, board, slug):
        if slug == "ghost":
            raise careers.CompanyError(f"no {board} board named '{slug}'")
        return [{}, {}, {}]
    monkeypatch.setattr(careers, "fetch_company", fake_fetch)
    at = _page().run()
    add = next(b for b in at.button if b.label == "Check and add")
    add.click().run()
    assert any("Enter the company name" in e.value for e in at.error)
    at.text_input(key="n_name").set_value("Ghostly").run()
    at.text_input(key="n_slug").set_value("ghost").run()
    next(b for b in at.button if b.label == "Check and add").click().run()
    assert any("Could not add it" in e.value for e in at.error)
    at.text_input(key="n_slug").set_value("https://jobs.lever.co/Realco/").run()          # a pasted address works
    next(b for b in at.button if b.label == "Check and add").click().run()
    assert not at.exception
    added = [c for c in db.list_companies(db.connect()) if c["name"] == "Ghostly"]
    assert added and added[0]["slug"] == "realco"


# ------------------------------------------------------------------ the Location box
@pytest.mark.parametrize("wanted,remote,loc,expected", [
    # one city, spelling variants
    ("Bengaluru", False, "Bengaluru, Karnataka", True),
    ("Bengaluru", False, "Bangalore, India", True),
    ("bangalore", False, "Bengaluru", True),
    ("Bengaluru", False, "Pune, India", False),
    ("Bengaluru", False, "Remote", False),                        # remote excluded unless asked for
    ("Bengaluru", False, "", False),
    # a city plus remote
    ("Bengaluru", True, "Remote", True),
    ("Bengaluru", True, "Worldwide", True),
    ("Bengaluru", True, "Remote - Asia Pacific", True),
    ("Bengaluru", True, "Remote, United States", False),          # remote but not for India
    ("Bengaluru", True, "Remote - India", True),
    ("Bengaluru", True, "Pune, India", False),                    # other Indian city still excluded
    ("Bengaluru", True, "", True),                                # no location listed: could be anywhere
    # several places
    ("Bengaluru, Pune", False, "Pune", True),
    ("Bengaluru or Hyderabad", False, "Hyderabad, Telangana", True),
    ("Bengaluru; Mumbai", False, "Chennai", False),
    # state and country level
    ("Karnataka", False, "Bengaluru, Karnataka", True),
    ("Karnataka", False, "Mysuru, Karnataka, India", True),
    # anywhere in India
    ("", False, "Pune, India", True),
    ("India", False, "Hyderabad", True),
    ("", False, "London, UK", False),
    ("India", False, "Remote", False),
    ("India", True, "Remote", True),
    # a region name
    ("NCR", False, "Noida, Uttar Pradesh", True),
    ("NCR", False, "Gurgaon", True),
    ("NCR", False, "Pune", False),
    ("Delhi", False, "New Delhi", True),
    # remote only
    ("Remote", False, "Remote", True),
    ("Remote", False, "Worldwide", True),
    ("Remote", False, "Bengaluru", False),
    ("Remote", False, "Remote, United States", False),
    # a place outside India is searched literally
    ("London", False, "London, UK", True),
    ("London", False, "Bengaluru", False),
    # names that merely contain another word
    ("Pune", False, "Punetown", False),
    ("Delhi", False, "Delhi", True),
])
def test_location_filter(wanted, remote, loc, expected):
    assert geo.location_filter(wanted, remote)(loc) is expected


def test_location_filter_handles_none_and_regex_characters():
    ok = geo.location_filter("C++ (City)", False)            # must not crash on regex characters
    assert ok("Somewhere") is False and geo.location_filter("Pune", False)(None) is False


def test_search_companies_uses_the_location():
    comps = [{"id": 1, "name": "Acme", "board": "greenhouse", "slug": "acme", "kind": "Startup"},
             {"id": 2, "name": "Acme", "board": "lever", "slug": "acme", "kind": "Startup"}]

    async def go(loc, remote):
        async with client() as c:
            return await careers.search_companies(c, comps, location=loc, include_remote=remote)
    pune = run(go("Pune", False))
    assert pune["jobs"] == []                                  # nothing in the canned data is in Pune
    mumbai = run(go("Mumbai", False))
    assert [j["url"] for j in mumbai["jobs"]] == ["https://lever/a"]
    bangalore = run(go("Bangalore", False))
    assert [j["url"] for j in bangalore["jobs"]] == ["https://gh/1"]       # "Bengaluru, India" matches "Bangalore"
    assert run(go("Mumbai", True))["per_company"][2]["kept"] == 1


def test_remote_boards_use_the_location():
    async def go(loc):
        async with httpx.AsyncClient(transport=httpx.MockTransport(remote_handler)) as c:
            return await remote_boards.search_remote_boards(c, ["Full Stack Developer", "React"], loc)
    jobs, _ = run(go("Bengaluru"))
    assert {j["url"] for j in jobs} == {"https://remotive/1", "https://rok/1"}       # worldwide ones stay
    jobs, _ = run(go("London"))
    assert {j["url"] for j in jobs} == {"https://remotive/1", "https://rok/1"}       # still open to anyone


def test_startup_search_passes_the_location_through_and_records_it(monkeypatch):
    seen = {}

    async def fake(companies, roles, include_remote, location=""):
        seen.update(location=location, remote=include_remote)
        return {"jobs": [], "per_company": {}}, [], {}
    monkeypatch.setattr(service, "_fetch_startup_sources", fake)
    monkeypatch.setattr(service, "score_jobs", lambda *a, **k: ({}, None))
    service.run_startup_search(["Web Developer"], 2, location="  Bengaluru, Pune ", include_remote=False)
    assert seen == {"location": "Bengaluru, Pune", "remote": False}
    service.run_startup_search(["Web Developer"], 2)
    assert seen["location"] == ""
    searches = db.connect().execute("SELECT location FROM searches ORDER BY id").fetchall()
    assert [s["location"] for s in searches] == ["Bengaluru, Pune", "India"]


def test_page_has_a_location_box_and_passes_it_on(monkeypatch):
    seen = {}

    def fake(roles, experience, skills=None, **kw):
        seen.update(kw)
        return {"per_company": {}, "remote": {}, "companies_checked": 0, "fetched": 0, "matched_title": 0,
                "new_count": 0, "already_seen": 0, "left_for_later": 0, "notice": None, "duplicates_in_batch": 0,
                "ids": [], "new_ids": []}
    monkeypatch.setattr(service, "run_startup_search", fake)
    at = _page().run()
    assert at.text_input(key="s_loc").label == "Location" and at.text_input(key="s_loc").value == "Bengaluru"
    assert any("jobs in **Bengaluru** and remote" in i.value for i in at.info)
    at.text_input(key="s_loc").set_value("Pune, Hyderabad").run()
    next(b for b in at.button if b.label == "Find jobs").click().run()
    assert not at.exception and seen["location"] == "Pune, Hyderabad"
    at.text_input(key="s_loc").set_value("").run()
    assert any("jobs in **India**" in i.value for i in at.info)
