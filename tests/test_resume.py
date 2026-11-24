import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest

from app import db, llm, resume, scoring, service

RESUME = ("Jane Doe. Full stack developer with 2 years of experience. Skills: React, Node.js, Python, SQL, "
          "Docker, Git. Built REST APIs and dashboards. B.Tech Computer Science 2023. " * 2)

CLAUDE_PROFILE = {
    "name": "Jane Doe", "summary": "Early-career full stack developer.", "experience_years": 2.2,
    "skills": ["React", "Node.js", "react", " Python ", "SQL", "", "Docker"],
    "roles": [{"title": "Web Developer", "reason": "React + Node.js"},
              {"title": "Associate Systems Engineer", "reason": "Python, SQL"},
              {"title": "web developer", "reason": "duplicate"}, {"title": "", "reason": "blank"}],
}


@pytest.fixture(autouse=True)
def no_live_calls(monkeypatch, tmp_path):
    import app.config
    monkeypatch.setattr(app.config, "load_dotenv", lambda *a, **k: None)
    for key in ("JSEARCH_RAPIDAPI_KEY", "SERPAPI_KEY", "OPENWEB_NINJA_API_KEY",
                "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
        monkeypatch.setenv(key, "")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "r.db")


# ------------------------------------------------------------------ reading files
def test_txt_and_text_cleaning():
    text = resume.extract_text("cv.txt", b"Jane\r\n\r\n\r\n\r\nReact   Node\x00")
    assert "\x00" not in text and "\n\n\n" not in text and "React Node" in text


def test_docx_including_tables():
    import docx
    d = docx.Document()
    d.add_paragraph("Jane Doe, React developer")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text = "Skills"
    t.rows[0].cells[1].text = "Node.js, Python"
    buf = io.BytesIO()
    d.save(buf)
    text = resume.extract_text("cv.docx", buf.getvalue())
    assert "React developer" in text and "Node.js, Python" in text


def test_pdf_text_is_extracted():
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    w = PdfWriter()
    page = w.add_blank_page(width=300, height=300)
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 200 Td (React Node Python developer) Tj ET")
    page[NameObject("/Contents")] = w._add_object(stream)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): DictionaryObject({
            NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica")})})})
    buf = io.BytesIO()
    w.write(buf)
    assert "React Node Python developer" in resume.extract_text("cv.pdf", buf.getvalue())


def test_bad_files_give_friendly_errors():
    with pytest.raises(resume.ResumeError, match="Unsupported"):
        resume.extract_text("cv.exe", b"x")
    with pytest.raises(resume.ResumeError, match="larger than 5 MB"):
        resume.extract_text("cv.txt", b"x" * (resume.MAX_BYTES + 1))
    with pytest.raises(resume.ResumeError, match="Could not read"):
        resume.extract_text("cv.pdf", b"this is not a pdf")
    with pytest.raises(resume.ResumeError, match="Could not read"):
        resume.extract_text("cv.docx", b"this is not a docx")


def test_too_little_text_is_rejected_before_calling_claude():
    with pytest.raises(resume.ResumeError, match="Not enough text"):
        resume.check_text("hi")
    assert len(resume.check_text("word " * 10_000)) == resume.MAX_CHARS


# ------------------------------------------------------------------ the profile
def test_normalise_cleans_claudes_answer():
    p = resume.normalise(CLAUDE_PROFILE)
    assert p["skills"] == ["React", "Node.js", "Python", "SQL", "Docker"]       # deduped, trimmed, blanks gone
    assert [r["title"] for r in p["roles"]] == ["Web Developer", "Associate Systems Engineer"]
    assert p["experience_years"] == 2.0                                          # rounded to half years


def test_normalise_survives_garbage():
    p = resume.normalise({"skills": None, "roles": [None, {"title": 5}], "experience_years": "lots"})
    assert p["skills"] == [] and p["experience_years"] == 0.0
    assert resume.normalise({"experience_years": 999})["experience_years"] == 60.0


def test_analyse_without_a_key_says_what_to_do():
    with pytest.raises(resume.ResumeError, match="ANTHROPIC_API_KEY"):
        resume.analyse(RESUME)


def test_analyse_with_fake_claude(monkeypatch):
    monkeypatch.setattr(resume, "make_client", lambda: object())
    seen = {}

    def fake(client, **kw):
        seen.update(kw)
        return CLAUDE_PROFILE
    monkeypatch.setattr(resume, "ask_json", fake)
    p = resume.analyse(RESUME)
    assert "Jane Doe" in seen["user"] and p["skills"][0] == "React" and len(p["roles"]) == 2


def test_analyse_turns_claude_failures_into_friendly_errors(monkeypatch):
    monkeypatch.setattr(resume, "make_client", lambda: object())

    def refuse(client, **kw):
        raise llm.ClaudeError("Claude declined this request")
    monkeypatch.setattr(resume, "ask_json", refuse)
    with pytest.raises(resume.ResumeError, match="could not analyse"):
        resume.analyse(RESUME)

    def boom(client, **kw):
        raise ConnectionError("proxy down")
    monkeypatch.setattr(resume, "ask_json", boom)
    with pytest.raises(resume.ResumeError, match="Could not reach Claude"):
        resume.analyse(RESUME)

    monkeypatch.setattr(resume, "ask_json", lambda c, **kw: {"skills": [], "roles": []})
    with pytest.raises(resume.ResumeError, match="No skills"):
        resume.analyse(RESUME)


# ------------------------------------------------------------------ scoring with skills
def test_scoring_with_skills_stores_matched_and_missing(monkeypatch):
    monkeypatch.setattr(scoring, "make_client", lambda: object())
    sent = {}

    def fake(client, **kw):
        sent.update(kw)
        return {"results": [{"id": 1, "score": 88, "exp_required": 2, "reason": "good",
                             "skills_matched": ["React", "Node.js"], "skills_missing": ["AWS, EC2"]}]}
    monkeypatch.setattr(scoring, "ask_json", fake)
    scores, notice = scoring.score_jobs("Web Developer", 2, [{"id": 1, "title": "T", "company": "C"}],
                                        skills=["React", "Node.js"])
    assert notice is None and scores[1]["skills_matched"] == "React, Node.js"
    assert scores[1]["skills_missing"] == "AWS EC2"                         # a comma inside a name is removed
    assert "Candidate skills: React, Node.js" in sent["user"]
    assert "skills_matched" in sent["schema"]["properties"]["results"]["items"]["properties"]


def test_scoring_without_skills_is_unchanged(monkeypatch):
    monkeypatch.setattr(scoring, "make_client", lambda: object())
    seen = {}

    def fake(client, **kw):
        seen.update(kw)
        return {"results": [{"id": 1, "score": 50, "exp_required": None, "reason": "ok"}]}
    monkeypatch.setattr(scoring, "ask_json", fake)
    scores, _ = scoring.score_jobs("Dev", 1, [{"id": 1, "title": "T", "company": "C"}])
    assert scores[1]["skills_matched"] is None
    assert "skills_matched" not in seen["schema"]["properties"]["results"]["items"]["properties"]


# ------------------------------------------------------------------ profiles + multi-role search
def test_profile_round_trip(tmp_path):
    conn = db.connect(tmp_path / "p.db")
    pid = db.save_profile(conn, name="Jane", summary="s", experience=2, skills=["React", "C++"],
                          roles=["Web Developer"], location="Pune")
    p = db.get_profile(conn, pid)
    assert p["skills"] == ["React", "C++"] and p["roles"] == ["Web Developer"] and p["last_run_at"] is None
    db.touch_profile(conn, pid)
    assert db.list_profiles(conn)[0]["last_run_at"]
    assert db.delete_profile(conn, pid) and not db.delete_profile(conn, pid)
    assert db.get_profile(conn, pid) is None


def test_resume_search_runs_each_role_once_merges_and_scores(monkeypatch):
    calls = []

    async def fake_fetch(role, location, days):
        calls.append(role)
        job = {"title": "Web Developer", "company": "Acme", "location": location, "url": "https://x/1",
               "source": "LinkedIn", "provider": "serpapi", "description": ""}
        extra = {"title": f"{role} Pro", "company": f"Co-{role}", "location": location,
                 "url": f"https://x/{role}", "source": "Indeed", "provider": "jsearch", "description": ""}
        return [dict(job), extra], {"serpapi": {"status": "ok", "count": 2}}

    scored = {}

    def fake_score(label, experience, jobs, skills=None):
        scored.update(label=label, n=len(jobs), skills=skills)
        return {j["id"]: {"score": 80, "exp_required": None, "reason": "r", "skills_matched": "React",
                          "skills_missing": None} for j in jobs}, None
    monkeypatch.setattr(service, "fetch_all", fake_fetch)
    monkeypatch.setattr(service, "score_jobs", fake_score)

    out = service.run_resume_search(["Web Developer", "Systems Engineer", "A", "B", "C", "D-ignored"],
                                    ["React"], 2, "Pune")
    assert calls == ["Web Developer", "Systems Engineer", "A", "B", "C"]      # capped at 5, in order
    assert out["fetched"] == 10 and out["duplicates_in_batch"] == 4           # "Acme" returned 5 times -> 1
    assert out["new_count"] == 6 == scored["n"] and scored["skills"] == ["React"]
    conn = db.connect()
    rows = db.query_jobs(conn, only_ids=out["ids"])
    assert len(rows) == 6 and all(r["skills_matched"] == "React" for r in rows)
    # a second identical run adds nothing and scores nothing new
    again = service.run_resume_search(["Web Developer"], ["React"], 2, "Pune")
    assert again["new_count"] == 0
    conn.close()


def test_estimate_and_cap():
    assert service.estimate_requests(3) == 15 and service.MAX_ROLES_PER_RUN == 5


# ------------------------------------------------------------------ the page
def _page():
    return AppTest.from_file(str(ROOT / "views/resume_match.py"), default_timeout=60)


def test_page_starts_at_step_one():
    at = _page().run()
    assert not at.exception
    assert [s.value for s in at.subheader][0] == "1. Add your resume"


def test_page_empty_submit_and_missing_key_show_errors():
    at = _page().run()
    at.button[0].click().run()
    assert any("Upload a file or paste" in e.value for e in at.error)
    at.text_area[0].set_value(RESUME).run()
    at.button[0].click().run()
    assert any("ANTHROPIC_API_KEY" in e.value for e in at.error) and not at.exception


def test_page_review_then_approve_flow(monkeypatch):
    monkeypatch.setattr(resume, "analyse", lambda text: resume.normalise(CLAUDE_PROFILE))
    ran = {}

    def fake_search(roles, skills, experience, location, days=None):
        ran.update(roles=roles, skills=skills, exp=experience, loc=location)
        return {"per_role": {r: {"providers": {}, "fetched": 0} for r in roles}, "fetched": 0, "new_count": 0,
                "already_seen": 0, "notice": None, "ids": [], "new_ids": [], "duplicates_in_batch": 0}
    monkeypatch.setattr(service, "run_resume_search", fake_search)

    at = _page().run()
    at.text_area[0].set_value(RESUME).run()
    at.button[0].click().run()
    assert not at.exception and at.subheader[0].value == "2. Check what Claude found"
    assert at.multiselect(key="w_skills").value == ["React", "Node.js", "Python", "SQL", "Docker"]
    assert at.multiselect(key="w_roles").value == ["Web Developer", "Associate Systems Engineer"]

    # the user edits the lists, then approves
    at.multiselect(key="w_skills").set_value(["React", "Node.js"]).run()
    at.multiselect(key="w_roles").set_value(["Web Developer"]).run()
    approve = next(b for b in at.button if b.label == "Approve and search")
    assert not approve.disabled
    approve.click().run()
    assert not at.exception
    assert ran == {"roles": ["Web Developer"], "skills": ["React", "Node.js"], "exp": 2.0, "loc": "Bengaluru"}
    # the profile was kept for later
    assert db.list_profiles(db.connect())[0]["skills"] == ["React", "Node.js"]


def test_approve_is_disabled_with_nothing_selected(monkeypatch):
    monkeypatch.setattr(resume, "analyse", lambda text: resume.normalise(CLAUDE_PROFILE))
    at = _page().run()
    at.text_area[0].set_value(RESUME).run()
    at.button[0].click().run()
    at.multiselect(key="w_roles").set_value([]).run()
    assert next(b for b in at.button if b.label == "Approve and search").disabled


def test_saved_profile_can_be_reused():
    conn = db.connect()
    db.save_profile(conn, name="Jane", summary="", experience=3, skills=["Go"], roles=["Backend Developer"],
                    location="Delhi")
    conn.close()
    at = _page().run()
    next(b for b in at.button if b.label == "Use").click().run()
    assert not at.exception
    assert at.multiselect(key="w_skills").value == ["Go"] and at.text_input(key="w_loc").value == "Delhi"
    assert at.number_input(key="w_exp").value == 3.0


def test_more_roles_than_the_cap_does_not_crash(monkeypatch):
    """Regression: 8 suggested roles used to raise StreamlitSelectionCountExceedsMaxError."""
    eight = {**CLAUDE_PROFILE, "roles": [{"title": f"Role {i}", "reason": "r"} for i in range(8)]}
    monkeypatch.setattr(resume, "analyse", lambda text: resume.normalise(eight))
    at = _page().run()
    at.text_area[0].set_value(RESUME).run()
    at.button[0].click().run()
    assert not at.exception
    assert at.multiselect(key="w_roles").value == [f"Role {i}" for i in range(5)]      # best 5 pre-selected
    assert len(at.multiselect(key="w_roles").options) == 8                             # all 8 still pickable
    assert any("8 roles" in c.value for c in at.caption)


def test_saved_profile_with_too_many_roles_does_not_crash():
    conn = db.connect()
    db.save_profile(conn, name="Old", summary="", experience=1, skills=["Go"],
                    roles=[f"R{i}" for i in range(8)], location="Pune")
    conn.close()
    at = _page().run()
    next(b for b in at.button if b.label == "Use").click().run()
    assert not at.exception and len(at.multiselect(key="w_roles").value) == 5
