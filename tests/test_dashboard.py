import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest

from app import charts, dashboard, db


def row(**kw):
    base = {"id": 1, "title": "T", "company": "C", "score": None, "exp_required": None, "saved": 0,
            "applied": 0, "found_by": "serpapi", "source": "LinkedIn", "first_seen": "2026-11-20T10:00:00+00:00"}
    return {**base, **kw}


def test_kpis():
    rows = [row(score=90, saved=1), row(score=70, applied=1), row(score=40), row()]
    k = dashboard.kpis(rows)
    assert k == {"total": 4, "strong": 2, "saved": 1, "applied": 1, "avg_fit": 67, "unscored": 1}


def test_kpis_with_nothing_scored():
    assert dashboard.kpis([row()])["avg_fit"] is None


def test_per_day_fills_quiet_days_with_zero():
    rows = [row(first_seen="2026-11-20T01:00:00"), row(first_seen="2026-11-20T09:00:00"),
            row(first_seen="2026-11-23T09:00:00")]
    df = dashboard.per_day(rows)
    assert df["Jobs"].tolist() == [2, 0, 0, 1]
    assert df["Day"].tolist()[0] == "Nov 20"


def test_per_day_empty():
    assert dashboard.per_day([]).empty


def test_fit_bands_are_inclusive_and_count_unscored():
    rows = [row(score=s) for s in (0, 19, 20, 79, 80, 100)] + [row()]
    df = dashboard.fit_distribution(rows).set_index("Fit score")["Jobs"]
    assert df["0\u201319"] == 2 and df["20\u201339"] == 1 and df["60\u201379"] == 1 and df["80\u2013100"] == 2
    assert df["Not scored"] == 1
    assert df.sum() == 7                       # every job lands in exactly one band


def test_experience_bands_cover_boundaries():
    rows = [row(exp_required=v) for v in (None, 0, 1, 1.5, 3, 5, 5.5, 12)]
    df = dashboard.experience_distribution(rows).set_index("Experience asked")["Jobs"]
    assert df.to_dict() == {"Not stated": 1, "Up to 1 yr": 2, "1\u20133 yrs": 2, "3\u20135 yrs": 1, "5+ yrs": 2}


def test_by_api_counts_a_shared_job_for_each_api():
    rows = [row(found_by="jsearch,serpapi"), row(found_by="serpapi"), row(found_by=None)]
    df = dashboard.by_api(rows).set_index("Found by")["Jobs"].to_dict()
    assert df == {"SerpApi": 2, "JSearch": 1, "Unknown": 1}


def test_by_board_limits_and_reports_total():
    rows = [row(source=f"Board{i}") for i in range(12)] + [row(source="Board0")]
    df, total = dashboard.by_board(rows, top=5)
    assert len(df) == 5 and total == 12 and df.iloc[0]["Job board"] == "Board0"


def test_best_to_apply_skips_applied_and_unscored():
    rows = [row(id=1, score=50), row(id=2, score=95, applied=1), row(id=3, score=80), row(id=4)]
    assert [r["id"] for r in dashboard.best_to_apply(rows)] == [3, 1]


def test_charts_build_for_both_themes(monkeypatch):
    df = dashboard.fit_distribution([row(score=90), row(score=10)])
    for mode in ("light", "dark"):
        monkeypatch.setattr(charts, "palette", lambda m=mode: charts.PALETTES[m])
        spec = charts.column_chart(df).to_dict()
        assert spec["layer"][0]["mark"]["color"] if "layer" in spec else spec["mark"]["color"]
        assert charts.ranked_bars(dashboard.by_api([row()])).to_dict()


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    import app.config
    monkeypatch.setattr(app.config, "load_dotenv", lambda *a, **k: None)
    path = tmp_path / "d.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    conn = db.connect(path)
    jobs = [{"title": f"Dev {i}", "company": f"Co{i}", "location": "Pune", "url": f"https://x/{i}",
             "source": "LinkedIn", "provider": "serpapi", "description": ""} for i in range(4)]
    db.insert_new(conn, jobs, "dev", "Pune")
    db.save_scores(conn, {jobs[0]["id"]: {"score": 90, "exp_required": 2}, jobs[1]["id"]: {"score": 60}})
    conn.close()
    return path


def test_dashboard_page_renders_with_data(seeded):
    at = AppTest.from_file(str(ROOT / "views/dashboard.py"), default_timeout=60).run()
    assert not at.exception
    assert [m.label for m in at.metric] == ["Jobs found", "Strong matches", "Saved", "Applied", "Average fit"]
    assert [m.value for m in at.metric][:2] == ["4", "1"]


def test_dashboard_empty_database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "none.db")
    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=60).run()
    assert not at.exception
    assert any("No jobs yet" in i.value for i in at.info)


def test_dashboard_filters(seeded):
    at = AppTest.from_file(str(ROOT / "views/dashboard.py"), default_timeout=60).run()
    at.selectbox(key="d_role").select("dev").run()
    assert not at.exception and at.metric[0].value == "4"
    at.selectbox(key="d_role").select("All roles").run()
    at.segmented_control(key="d_range").set_value("Last 7 days").run()
    assert not at.exception and at.metric[0].value == "4"      # seeded just now, so inside 7 days


def test_since_filter_excludes_old_jobs(seeded):
    conn = db.connect(seeded)
    conn.execute("UPDATE jobs SET first_seen = '2020-01-01T00:00:00+00:00' WHERE title = 'Dev 0'")
    conn.commit()
    assert len(db.query_jobs(conn)) == 4
    assert len(db.query_jobs(conn, since="2026-01-01T00:00:00+00:00")) == 3
    assert db.distinct_roles(conn) == ["dev"]
    conn.close()
