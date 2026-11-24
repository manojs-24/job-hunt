import streamlit as st

from app.config import key_status

st.set_page_config(page_title="Job Search", page_icon=":material/work:", layout="wide")

pages = [
    st.Page("views/dashboard.py", title="Dashboard", icon=":material/dashboard:", default=True),
    st.Page("views/search.py", title="Search jobs", icon=":material/search:"),
    st.Page("views/resume_match.py", title="Match my resume", icon=":material/description:"),
    st.Page("views/startups.py", title="Startups & remote", icon=":material/rocket_launch:"),
    st.Page("views/companies.py", title="Find companies", icon=":material/business:"),
    st.Page("views/all_jobs.py", title="All jobs", icon=":material/table:"),
]
page = st.navigation(pages)

LABELS = {"jsearch": "JSearch", "serpapi": "SerpApi", "openwebninja": "OpenWeb Ninja", "claude": "Claude scoring"}
with st.sidebar:
    st.caption("API keys found in .env")
    for name, ok in key_status().items():
        st.markdown(f"{':green[:material/check_circle:]' if ok else ':gray[:material/do_not_disturb_on:]'} {LABELS[name]}")

page.run()
