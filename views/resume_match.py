import streamlit as st

from app import db, export, resume, service
from app.ui import jobs_table

st.title("Match jobs to my resume")
st.caption("Upload your resume. Claude lists your skills and the roles you could apply for. "
           "You review and approve them, and only then are jobs searched.")

S = st.session_state
S.setdefault("profile", None)       # the editable profile: {name, summary, skills, roles, experience, location}
S.setdefault("resume_result", None)


def load_profile(profile: dict, location: str = "") -> None:
    """Put a profile into the editable widgets (their values live under the w_* keys)."""
    S["profile"] = profile
    S["w_skills"] = list(profile["skills"])
    titles = [r["title"] if isinstance(r, dict) else r for r in profile["roles"]]
    S["w_roles"] = titles[:service.MAX_ROLES_PER_RUN]      # best fits first; the rest stay pickable
    S["w_exp"] = float(profile["experience_years"] if "experience_years" in profile else profile["experience"])
    S["w_loc"] = location or profile.get("location") or S.get("w_loc") or "Bengaluru"
    S["resume_result"] = None


def start_over() -> None:
    for key in ("profile", "resume_result", "w_skills", "w_roles", "w_exp", "w_loc"):
        S.pop(key, None)


# ---------------------------------------------------------------- 1. get the resume
if S["profile"] is None:
    conn = db.connect()
    try:
        saved = db.list_profiles(conn)
    finally:
        conn.close()

    with st.container(border=True):
        st.subheader("1. Add your resume")
        tab_file, tab_text = st.tabs(["Upload a file", "Paste the text"])
        with tab_file:
            upload = st.file_uploader("PDF, Word (.docx) or text file", type=["pdf", "docx", "txt"],
                                      help="Up to 5 MB. Scanned PDFs (images) cannot be read; paste the text instead.")
        with tab_text:
            pasted = st.text_area("Resume text", height=220, placeholder="Paste your resume here...")

        if st.button("Analyse my resume", type="primary", icon=":material/psychology:"):
            try:
                if upload is not None:
                    text = resume.extract_text(upload.name, upload.getvalue())
                elif pasted.strip():
                    text = pasted
                else:
                    raise resume.ResumeError("Upload a file or paste your resume text first.")
                with st.spinner("Claude is reading your resume..."):
                    load_profile(resume.analyse(text))
                st.rerun()
            except resume.ResumeError as err:
                st.error(str(err))

    if saved:
        with st.container(border=True):
            st.subheader("Or reuse a saved profile")
            for p in saved:
                c1, c2, c3 = st.columns([5, 1, 1], vertical_alignment="center")
                c1.markdown(f"**{p['name'] or 'Profile'}** · {len(p['skills'])} skills · "
                            f"{p['experience']:g} yrs · {', '.join(p['roles'][:3])}")
                c1.caption(f"Saved {p['created_at'][:10]}"
                           + (f" · last searched {p['last_run_at'][:10]}" if p["last_run_at"] else ""))
                if c2.button("Use", key=f"use_{p['id']}"):
                    load_profile({**p, "id": p["id"]}, p["location"] or "")
                    st.rerun()
                if c3.button("", key=f"del_{p['id']}", icon=":material/delete:", help="Delete this profile"):
                    conn = db.connect()
                    try:
                        db.delete_profile(conn, p["id"])
                    finally:
                        conn.close()
                    st.rerun()
    st.stop()

# ---------------------------------------------------------------- 2. review and approve
profile = S["profile"]
with st.container(border=True):
    head, reset = st.columns([5, 1], vertical_alignment="center")
    head.subheader("2. Check what Claude found")
    reset.button("Start over", on_click=start_over, icon=":material/restart_alt:")
    if profile.get("summary"):
        st.caption(profile["summary"])

    st.multiselect(
        "Your skills", options=sorted(set(profile["skills"]) | set(S["w_skills"]), key=str.lower),
        key="w_skills", accept_new_options=True,
        help="Remove any that are wrong and type to add ones that are missing.")

    role_options = [r["title"] if isinstance(r, dict) else r for r in profile["roles"]]
    if len(role_options) > service.MAX_ROLES_PER_RUN:
        st.caption(f"Claude found {len(role_options)} roles. The best {service.MAX_ROLES_PER_RUN} are selected; "
                   "swap in others from the list if you prefer.")
    st.multiselect(
        "Roles to search for", options=sorted(set(role_options) | set(S["w_roles"]), key=str.lower),
        key="w_roles", accept_new_options=True, max_selections=service.MAX_ROLES_PER_RUN,
        help=f"Pick up to {service.MAX_ROLES_PER_RUN}. Each role is searched separately.")
    reasons = {r["title"]: r["reason"] for r in profile["roles"] if isinstance(r, dict) and r.get("reason")}
    shown = [t for t in S["w_roles"] if t in reasons]
    if shown:
        with st.expander("Why these roles?"):
            for title in shown:
                st.markdown(f"**{title}**: {reasons[title]}")

    c1, c2, c3 = st.columns(3)
    c1.number_input("Years of experience", min_value=0.0, max_value=60.0, step=0.5, key="w_exp")
    c2.text_input("Location", key="w_loc", placeholder="e.g. Bengaluru")
    days = c3.number_input("Posted within (days)", min_value=0, max_value=60, value=14, help="0 means any time")

    n = len(S["w_roles"])
    ready = bool(S["w_skills"]) and n > 0 and bool(S["w_loc"].strip())
    credits = service.estimate_requests(n)
    st.info(f"This will search **{n}** role{'s' if n != 1 else ''}, using up to about **{credits}** API requests "
            "from your free monthly allowance (JSearch 200, OpenWeb Ninja 100, SerpApi has its own limit).",
            icon=":material/info:")
    keep = st.checkbox("Save this profile so I can search again later", value=True)
    approve = st.button("Approve and search", type="primary", disabled=not ready, icon=":material/rocket_launch:",
                        help=None if ready else "Choose at least one skill, one role and a location.")

if approve:
    if keep:
        conn = db.connect()
        try:
            S["profile"]["id"] = db.save_profile(
                conn, name=profile.get("name", ""), summary=profile.get("summary", ""),
                experience=S["w_exp"], skills=S["w_skills"], roles=S["w_roles"], location=S["w_loc"].strip())
        finally:
            conn.close()
    try:
        with st.spinner(f"Searching {n} role{'s' if n != 1 else ''} and matching each job to your skills. "
                        "This can take a few minutes..."):
            S["resume_result"] = service.run_resume_search(
                S["w_roles"], S["w_skills"], S["w_exp"], S["w_loc"], days or None)
        if S["profile"].get("id"):
            conn = db.connect()
            try:
                db.touch_profile(conn, S["profile"]["id"])
            finally:
                conn.close()
    except Exception as err:   # show the problem instead of a blank page
        S["resume_result"] = None
        st.error(f"Search failed: {err}")

# ---------------------------------------------------------------- 3. results
result = S["resume_result"]
if result:
    st.subheader("3. Your matches")
    m1, m2, m3 = st.columns(3)
    m1.metric("Fetched", result["fetched"])
    m2.metric("New", result["new_count"])
    m3.metric("Already saved", result["already_seen"])

    with st.expander("What each role returned"):
        for role, info in result["per_role"].items():
            parts = []
            for name, r in info["providers"].items():
                parts.append(f"{name}: {r['count']}" if r["status"] == "ok"
                             else f"{name}: {r['detail']}" if r["status"] == "error" else f"{name}: skipped")
            st.markdown(f"**{role}**: {info['fetched']} jobs · " + " · ".join(parts))
    if result["notice"]:
        st.warning(result["notice"])

    conn = db.connect()
    try:
        rows = db.query_jobs(conn, only_ids=result["ids"])
    finally:
        conn.close()
    if rows:
        jobs_table(rows, new_ids=frozenset(result["new_ids"]))
        d1, d2, _ = st.columns([1, 1, 4])
        d1.download_button("Download CSV", export.to_csv(rows), "resume_matches.csv", "text/csv",
                           icon=":material/download:", width="stretch")
        d2.download_button(
            "Download Excel", export.to_xlsx(rows), "resume_matches.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            icon=":material/download:", width="stretch")
    else:
        st.info("No jobs matched. Try other roles or a wider location.")
