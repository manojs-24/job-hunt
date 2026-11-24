import streamlit as st

from app import db, export
from app.providers.base import PROVIDER_LABELS
from app.ui import jobs_table

SORTS = {"Best fit": "score", "Newest first": "newest"}
SHOW = {"All jobs": None, "Saved only": True, "Not saved": False}
APPLIED = {"Any": None, "Applied": True, "Not applied yet": False}
API = {"Any API": None, **{label: key for key, label in PROVIDER_LABELS.items()}}
DEFAULTS = {"f_q": "", "f_show": "All jobs", "f_applied": "Any", "f_api": "Any API",
            "f_source": "Any source", "f_min": 0, "f_sort": "Best fit"}

for key, value in DEFAULTS.items():
    st.session_state.setdefault(key, value)

conn = db.connect()
try:
    stats = db.counts(conn)
    sources = db.distinct_sources(conn)
finally:
    conn.close()

st.title("All jobs")

if stats["total"] == 0:
    st.info("No jobs saved yet. Run a search and the jobs will appear here.")
    st.page_link("views/search.py", label="Search for jobs", icon=":material/search:")
    st.stop()

source_options = ["Any source", *sources]
if st.session_state["f_source"] not in source_options:
    st.session_state["f_source"] = "Any source"


def reset_filters():
    st.session_state.update(DEFAULTS)


with st.container(border=True):
    r1 = st.columns([3, 2, 2, 2])
    r1[0].text_input("Search", key="f_q", placeholder="title, company or location")
    r1[1].selectbox("Show", list(SHOW), key="f_show")
    r1[2].selectbox("Application", list(APPLIED), key="f_applied")
    r1[3].selectbox("Found by", list(API), key="f_api", help="Which API returned the job")
    r2 = st.columns([3, 2, 2, 2])
    r2[0].selectbox("Job board", source_options, key="f_source", help="Where the job is listed, e.g. LinkedIn")
    r2[1].selectbox("Sort by", list(SORTS), key="f_sort")
    r2[2].number_input("Min score", min_value=0, max_value=100, step=5, key="f_min")
    r2[3].button("Reset filters", on_click=reset_filters, icon=":material/restart_alt:")

conn = db.connect()
try:
    rows = db.query_jobs(
        conn,
        q=st.session_state["f_q"].strip() or None,
        saved=SHOW[st.session_state["f_show"]],
        applied=APPLIED[st.session_state["f_applied"]],
        found_by=API[st.session_state["f_api"]],
        source=None if st.session_state["f_source"] == "Any source" else st.session_state["f_source"],
        min_score=st.session_state["f_min"] or None,
        sort=SORTS[st.session_state["f_sort"]],
    )
finally:
    conn.close()

m1, m2, m3, m4 = st.columns(4)
m1.metric("In database", stats["total"])
m2.metric("Saved", stats["saved"])
m3.metric("Applied", stats["applied"])
m4.metric("Showing", len(rows))

if rows:
    jobs_table(rows)
    d1, d2, _ = st.columns([1, 1, 4])
    d1.download_button("Download CSV", export.to_csv(rows), "jobs.csv", "text/csv",
                       icon=":material/download:", width="stretch")
    d2.download_button(
        "Download Excel", export.to_xlsx(rows), "jobs.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        icon=":material/download:", width="stretch")
else:
    st.warning("No jobs match these filters. Try clearing a filter.")
