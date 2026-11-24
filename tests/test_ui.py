"""Run the real Streamlit pages headlessly against a throwaway database."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest

from app import db, service
from app.ui import changed_flags, to_frame


def _job(title, company, **kw):
    return {"title": title, "company": company, "location": "Bengaluru", "url": f"https://x.com/{title}",
            "source": "LinkedIn", "description": "", **kw}


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    path = tmp_path / "ui.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    conn = db.connect(path)
    jobs = [_job("Python Dev", "Acme"), _job("Data Engineer", "Beta Corp", source="Indeed"),
            _job("Java Architect", "Gamma")]
    db.insert_new(conn, jobs, "dev", "India")
    db.save_scores(conn, {jobs[0]["id"]: {"score": 90, "exp_required": 3, "reason": "good"},
                          jobs[1]["id"]: {"score": 50}})
    conn.close()
    return path


@pytest.fixture(autouse=True)
def no_live_api_calls(monkeypatch):
    """Tests must never spend real API credits: blank every key and ignore .env."""
    import app.config
    monkeypatch.setattr(app.config, "load_dotenv", lambda *a, **k: None)
    for key in ("JSEARCH_RAPIDAPI_KEY", "SERPAPI_KEY", "OPENWEB_NINJA_API_KEY",
                "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
        monkeypatch.setenv(key, "")


def _app(page: str) -> AppTest:
    return AppTest.from_file(str(ROOT / page), default_timeout=30)


def test_home_page_renders_with_no_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "empty.db")
    at = _app("views/search.py").run()
    assert not at.exception
    assert at.text_input[0].label == "Job role"
    assert "How it works" in [s.value for s in at.subheader]


def test_blank_search_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "empty.db")
    at = _app("views/search.py").run()
    at.text_input[0].set_value("")        # the form is pre-filled, so blank it explicitly
    at.text_input[1].set_value("")
    at.button[0].click().run()
    assert not at.exception
    assert any("job role and a location" in e.value for e in at.error)


def test_search_with_no_keys_reports_skipped_and_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "s.db")
    result = service.run_search("python", 2, "Pune")
    assert result["fetched"] == 0 and result["new_count"] == 0
    assert {r["status"] for r in result["providers"].values()} == {"skipped"}


def test_all_jobs_page_lists_and_filters(seeded):
    at = _app("views/all_jobs.py").run()
    assert not at.exception
    assert [m.value for m in at.metric] == ["3", "0", "0", "3"]
    at.text_input(key="f_q").set_value("beta").run()
    assert [m.value for m in at.metric][3] == "1"
    at.selectbox(key="f_show").select("Saved only").run()
    assert [m.value for m in at.metric][3] == "0"
    assert any("No jobs match" in w.value for w in at.warning)


def test_all_jobs_reset_restores_filters(seeded):
    at = _app("views/all_jobs.py").run()
    at.text_input(key="f_q").set_value("zzz").run()
    assert [m.value for m in at.metric][3] == "0"
    next(b for b in at.button if b.label == "Reset filters").click().run()
    assert [m.value for m in at.metric][3] == "3"


def test_all_jobs_empty_database_message_via_real_entrypoint(tmp_path, monkeypatch):
    # page_link needs the pages registered by st.navigation, so go through streamlit_app.py
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "none.db")
    at = _app("streamlit_app.py").run()
    assert not at.exception
    at.switch_page("views/all_jobs.py").run()
    assert not at.exception
    assert any("No jobs saved yet" in i.value for i in at.info)


def test_entrypoint_navigation_between_pages(seeded):
    at = _app("streamlit_app.py").run()
    assert not at.exception
    assert at.metric[0].label == "Jobs found"            # lands on the Dashboard
    at.switch_page("views/search.py").run()
    assert not at.exception
    assert at.text_input[0].label == "Job role"
    at.switch_page("views/all_jobs.py").run()
    assert not at.exception
    assert [m.value for m in at.metric] == ["3", "0", "0", "3"]


def test_saving_a_row_is_detected_and_stored(seeded):
    conn = db.connect(seeded)
    rows = db.query_jobs(conn)
    original = to_frame(rows)
    edited = original.copy()
    target = rows[1]["id"]
    edited.loc[target, "Saved"] = True
    assert changed_flags(original, edited) == [(target, "Saved", True)]
    db.set_saved(conn, target, True)
    assert [r["id"] for r in db.query_jobs(conn, saved=True)] == [target]
    conn.close()


def test_frame_handles_missing_values(seeded):
    conn = db.connect(seeded)
    df = to_frame(db.query_jobs(conn))
    assert df["Experience"].tolist().count("3 yrs") == 1
    assert df["Fit"].isna().sum() == 1          # the unscored job
    conn.close()


def test_applied_toggle_and_filter(seeded):
    conn = db.connect(seeded)
    rows = db.query_jobs(conn)
    target = rows[0]["id"]
    assert db.set_applied(conn, target, True)
    assert [r["id"] for r in db.query_jobs(conn, applied=True)] == [target]
    assert len(db.query_jobs(conn, applied=False)) == 2
    assert db.counts(conn) == {"total": 3, "saved": 0, "applied": 1}
    db.set_applied(conn, target, False)
    assert db.query_jobs(conn, applied=True) == []
    assert not db.set_applied(conn, 9999, True)
    conn.close()


def test_applied_and_saved_are_independent(seeded):
    conn = db.connect(seeded)
    a, b = [r["id"] for r in db.query_jobs(conn)][:2]
    db.set_saved(conn, a, True)
    db.set_applied(conn, b, True)
    assert [r["id"] for r in db.query_jobs(conn, saved=True)] == [a]
    assert [r["id"] for r in db.query_jobs(conn, applied=True)] == [b]
    conn.close()


def test_changed_flags_reports_both_columns(seeded):
    conn = db.connect(seeded)
    rows = db.query_jobs(conn)
    original = to_frame(rows)
    edited = original.copy()
    a, b = rows[0]["id"], rows[1]["id"]
    edited.loc[a, "Applied"] = True
    edited.loc[b, "Saved"] = True
    assert sorted(changed_flags(original, edited)) == sorted([(a, "Applied", True), (b, "Saved", True)])
    conn.close()


def test_found_by_records_every_api_that_returned_the_job(tmp_path):
    from app.dedupe import dedupe_batch
    conn = db.connect(tmp_path / "f.db")
    one = {**_job("Python Dev", "Acme"), "provider": "jsearch"}
    two = {**_job("Python Dev", "Acme"), "provider": "serpapi", "url": "https://other/1"}
    merged, dupes = dedupe_batch([one, two])           # same job from two APIs in one search
    assert dupes == 1 and merged[0]["providers"] == {"jsearch", "serpapi"}
    db.insert_new(conn, merged, "dev", "Pune")
    row = db.query_jobs(conn)[0]
    assert row["found_by"] == "jsearch,serpapi"
    # a later search where only OpenWeb Ninja returns it adds that API, and inserts nothing new
    three = {**_job("Python Dev", "Acme"), "provider": "openwebninja"}
    assert db.insert_new(conn, [three], "dev", "Pune") == []
    assert db.query_jobs(conn)[0]["found_by"] == "jsearch,openwebninja,serpapi"
    assert [r["title"] for r in db.query_jobs(conn, found_by="openwebninja")] == ["Python Dev"]
    assert db.query_jobs(conn, found_by="jsear") == []        # exact match, not a substring


def test_old_jobs_get_found_by_from_provider(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT UNIQUE, title TEXT, "
                "company TEXT, company_key TEXT, location TEXT, remote INTEGER, salary TEXT, posted_at TEXT, url TEXT, "
                "source TEXT, provider TEXT, description TEXT, exp_required REAL, score INTEGER, reason TEXT, "
                "query_role TEXT, query_location TEXT, first_seen TEXT, last_seen TEXT)")
    raw.execute("INSERT INTO jobs (fingerprint, title, company, company_key, provider, first_seen, last_seen) "
                "VALUES ('f','Old','Co','co','serpapi','t','t')")
    raw.commit(); raw.close()
    row = db.query_jobs(db.connect(path))[0]
    assert row["found_by"] == "serpapi" and row["applied"] == 0


def test_export_includes_found_by_and_applied(seeded):
    import io, csv as csvmod
    from app import export
    conn = db.connect(seeded)
    rows = db.query_jobs(conn)
    db.set_applied(conn, rows[0]["id"], True)
    out = csvmod.reader(io.StringIO(export.to_csv(db.query_jobs(conn)).decode("utf-8-sig")))
    header = next(out)
    first = next(out)
    assert header[header.index("Applied")] == "Applied"
    assert first[header.index("Applied")] == "Yes"
    assert first[header.index("Found by")] in ("", )  # seeded rows have no provider
    conn.close()


def test_all_jobs_page_has_new_filters_and_applied_metric(seeded):
    at = _app("views/all_jobs.py").run()
    assert not at.exception
    assert [m.label for m in at.metric] == ["In database", "Saved", "Applied", "Showing"]
    at.selectbox(key="f_applied").select("Applied").run()
    assert [m.value for m in at.metric][3] == "0"
    at.selectbox(key="f_api").select("JSearch").run()
    assert not at.exception
