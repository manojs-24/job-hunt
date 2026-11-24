import streamlit as st

from app import db, export, service
from app.ui import jobs_table

st.title("Find the right job, faster")
st.caption("Search several job sites at once. New jobs are scored for how well they fit you, "
           "and jobs you've already seen are skipped.")

with st.form("search"):
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    role = c1.text_input("Job role", placeholder="e.g. Python developer", value="Web Developer")
    experience = c2.number_input("Experience (years)", min_value=0.0, max_value=60.0, value=1.0, step=0.5)
    location = c3.text_input("Location", placeholder="e.g. Bengaluru", value="Bengaluru")
    days = c4.number_input("Posted within (days)", min_value=0, max_value=60, value=7,
                           help="0 means any time")
    submitted = st.form_submit_button("Search jobs", type="primary", icon=":material/search:", width="stretch")

if submitted:
    if not role.strip() or not location.strip():
        st.error("Please enter a job role and a location.")
    else:
        try:
            with st.spinner("Fetching jobs and scoring new ones. This can take up to a minute..."):
                st.session_state["result"] = service.run_search(role, experience, location, days or None)
        except Exception as err:  # surface anything unexpected instead of a blank page
            st.session_state.pop("result", None)
            st.error(f"Search failed: {err}")

result = st.session_state.get("result")

if result is None:
    st.subheader("How it works")
    for col, (n, title, text) in zip(st.columns(3), [
        ("1", "Tell us what you want", "Enter a role, your years of experience and a city, then press Search jobs."),
        ("2", "See how well each job fits", "Every new job gets a 0-100 fit score and a short reason."),
        ("3", "Save and download", "Tick the jobs you like, then use All jobs to filter and download them."),
    ]):
        with col.container(border=True):
            st.markdown(f"**{n}. {title}**")
            st.write(text)
else:
    badges = st.columns(len(result["providers"]))
    for col, (name, r) in zip(badges, result["providers"].items()):
        with col:
            if r["status"] == "ok":
                st.badge(f"{name}: {r['count']} jobs", color="green", icon=":material/check:")
            elif r["status"] == "error":
                st.badge(f"{name}: {r['detail']}", color="red", icon=":material/error:")
            else:
                st.badge(f"{name}: skipped", color="gray", icon=":material/remove:")

    if result["notice"]:
        st.warning(result["notice"])

    m1, m2, m3 = st.columns(3)
    m1.metric("Fetched", result["fetched"])
    m2.metric("New", result["new_count"])
    m3.metric("Already saved", result["already_seen"])

    conn = db.connect()
    try:
        rows = db.query_jobs(conn, only_ids=result["ids"])
    finally:
        conn.close()

    st.subheader(f"Results ({len(rows)})")
    if rows:
        jobs_table(rows, new_ids=frozenset(result["new_ids"]))
        d1, d2, _ = st.columns([1, 1, 4])
        d1.download_button("Download CSV", export.to_csv(rows), "jobs.csv", "text/csv",
                           icon=":material/download:", width="stretch")
        d2.download_button(
            "Download Excel", export.to_xlsx(rows), "jobs.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            icon=":material/download:", width="stretch")
    else:
        st.info("No jobs matched. Try a broader role or location.")
    st.page_link("views/all_jobs.py", label="Open all jobs", icon=":material/table:")
