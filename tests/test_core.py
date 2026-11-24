import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.dedupe import dedupe_batch, fingerprint, norm_company, similar_title, strip_utm, too_old


def job(**kw):
    base = {"title": "Python Developer", "company": "Acme Pvt Ltd", "location": "Bengaluru, Karnataka",
            "url": "https://x.com/1", "remote": False, "description": ""}
    return {**base, **kw}


def test_company_suffixes_stripped():
    assert norm_company("Acme Technologies Pvt. Ltd.") == norm_company("ACME")


def test_similar_title():
    assert similar_title("Senior Python Developer", "Python Developer Senior")
    assert not similar_title("Python Developer", "Java Architect")


def test_strip_utm():
    assert strip_utm("https://a.com/j?id=3&utm_source=x") == "https://a.com/j?id=3"


def test_fingerprint_ignores_company_noise_and_word_order():
    assert fingerprint(job()) == fingerprint(job(company="ACME", title="Developer Python"))


def test_dedupe_batch_merges_and_fills_gaps():
    a = job(salary=None, source="Indeed")
    b = job(salary="INR 10", url="https://y.com/2", company="Acme")
    kept, dupes = dedupe_batch([a, b])
    assert len(kept) == 1 and dupes == 1
    assert kept[0]["salary"] == "INR 10"


def test_too_old():
    assert too_old({"posted_at": "3 weeks ago"}, 7)
    assert not too_old({"posted_at": "2 days ago"}, 7)
    assert not too_old({"posted_at": None}, 7)


def test_second_insert_is_a_noop(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    first = db.insert_new(conn, [job()], "python", "Bengaluru")
    assert len(first) == 1
    again = db.insert_new(conn, [job()], "python", "Bengaluru")
    assert again == []
    # same posting under another apply URL / wording is still recognised
    renamed = db.insert_new(conn, [job(title="Python Developer", url="https://z.com/9")], "python", "Bengaluru")
    assert renamed == []
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_new_distinct_job_is_inserted(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.insert_new(conn, [job()], "python", "Bengaluru")
    added = db.insert_new(conn, [job(title="Data Engineer", url="https://x.com/2")], "python", "Bengaluru")
    assert len(added) == 1


def _seed(conn):
    jobs = [
        job(title="Python Developer", company="Acme", url="https://x.com/a", source="LinkedIn"),
        job(title="Data Engineer", company="Beta Corp", url="https://x.com/b", source="Indeed", location="Pune"),
        job(title="Java Architect", company="Gamma", url="https://x.com/c", source="LinkedIn"),
    ]
    db.insert_new(conn, jobs, "dev", "India")
    db.save_scores(conn, {jobs[0]["id"]: {"score": 90}, jobs[1]["id"]: {"score": 50}})
    return jobs


def test_save_toggle_and_filter(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    a, b, c = _seed(conn)
    assert db.set_saved(conn, a["id"], True)
    assert [r["title"] for r in db.query_jobs(conn, saved=True)] == ["Python Developer"]
    assert len(db.query_jobs(conn, saved=False)) == 2
    db.set_saved(conn, a["id"], False)
    assert db.query_jobs(conn, saved=True) == []
    assert not db.set_saved(conn, 9999, True)


def test_filters_search_source_score_sort(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    _seed(conn)
    assert [r["company"] for r in db.query_jobs(conn, q="pune")] == ["Beta Corp"]
    assert len(db.query_jobs(conn, source="linkedin")) == 2
    assert [r["company"] for r in db.query_jobs(conn, min_score=60)] == ["Acme"]
    assert db.query_jobs(conn)[0]["company"] == "Acme"          # best score first
    assert db.query_jobs(conn)[-1]["score"] is None             # unscored last
    assert db.query_jobs(conn, q="100%") == []                  # % is literal, not a wildcard
    assert db.distinct_sources(conn) == ["Indeed", "LinkedIn"]


def test_old_database_gets_saved_column(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT UNIQUE, title TEXT, "
                "company TEXT, company_key TEXT, location TEXT, remote INTEGER, salary TEXT, posted_at TEXT, url TEXT, "
                "source TEXT, provider TEXT, description TEXT, exp_required REAL, score INTEGER, reason TEXT, "
                "query_role TEXT, query_location TEXT, first_seen TEXT, last_seen TEXT)")
    raw.execute("INSERT INTO jobs (fingerprint, title, company, company_key, first_seen, last_seen) "
                "VALUES ('f','Old job','Co','co','t','t')")
    raw.commit(); raw.close()
    conn = db.connect(path)
    rows = db.query_jobs(conn)
    assert rows[0]["title"] == "Old job" and rows[0]["saved"] == 0
