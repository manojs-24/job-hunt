from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from app import charts, dashboard, db
from app.ui import jobs_table

RANGES = {"Last 7 days": 7, "Last 30 days": 30, "All time": None}

st.title("Dashboard")

conn = db.connect()
try:
    stats = db.counts(conn)
    roles = db.distinct_roles(conn)
    searches = db.recent_searches(conn)
finally:
    conn.close()

if stats["total"] == 0:
    st.info("No jobs yet. Run a search and your dashboard will fill in.")
    st.page_link("views/search.py", label="Search for jobs", icon=":material/search:")
    st.stop()

# One filter row above everything it scopes: every number and chart below follows it.
f1, f2, _ = st.columns([3, 2, 3], vertical_alignment="bottom")
chosen = f1.segmented_control("Jobs first found", list(RANGES), default="All time", key="d_range")
role = f2.selectbox("Role searched", ["All roles", *roles], key="d_role")
days = RANGES[chosen or "All time"]
since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds") if days else None

conn = db.connect()
try:
    rows = db.query_jobs(conn, since=since, role=None if role == "All roles" else role, sort="newest")
finally:
    conn.close()

if not rows:
    st.info("No jobs match this filter. Try a longer time range.")
    st.stop()

k = dashboard.kpis(rows)
tiles = st.columns(5)
tiles[0].metric("Jobs found", k["total"], border=True)
tiles[1].metric("Strong matches", k["strong"], border=True,
                help=f"Jobs with a fit score of {dashboard.STRONG_FIT} or more")
tiles[2].metric("Saved", k["saved"], border=True)
tiles[3].metric("Applied", k["applied"], border=True)
tiles[4].metric("Average fit", "–" if k["avg_fit"] is None else k["avg_fit"], border=True,
                help="Mean fit score (0-100) of the jobs that have been scored")
if k["unscored"]:
    st.caption(f"{k['unscored']} of {k['total']} jobs have no fit score yet. "
               "Claude scoring was off or failed when they were found.")


def chart_card(title: str, df: pd.DataFrame, make_chart, note: str | None = None) -> None:
    """A titled card with the chart and a table twin, so no value is only readable from a tooltip."""
    with st.container(border=True):
        st.markdown(f"**{title}**")
        if note:
            st.caption(note)
        if df.empty or not df.iloc[:, 1].any():
            st.caption("Nothing to show yet.")
            return
        chart_tab, table_tab = st.tabs(["Chart", "Table"])
        with chart_tab:
            st.altair_chart(make_chart(df), width="stretch", theme=None)
        with table_tab:
            st.dataframe(df, hide_index=True, width="stretch")


chart_card("Jobs found per day", dashboard.per_day(rows), charts.column_chart,
           "Counted on the day each job was first found.")

c1, c2 = st.columns(2)
with c1:
    chart_card("How well the jobs fit you", dashboard.fit_distribution(rows), charts.column_chart,
               "Claude's 0-100 fit score for your role and experience.")
with c2:
    chart_card("Experience the jobs ask for", dashboard.experience_distribution(rows), charts.column_chart,
               "Read from each job description by Claude.")

c3, c4 = st.columns(2)
with c3:
    chart_card("Which API found the jobs", dashboard.by_api(rows), charts.ranked_bars,
               "A job found by two APIs counts for both.")
with c4:
    boards, board_total = dashboard.by_board(rows)
    chart_card("Where the jobs are listed", boards, charts.ranked_bars,
               f"Top {len(boards)} of {board_total} job boards." if board_total > len(boards)
               else f"{board_total} job boards.")

st.subheader("Best matches you haven't applied to yet")
todo = dashboard.best_to_apply(rows)
if todo:
    st.caption("Highest fit first. Tick **Applied** here once you have applied; it is saved straight away.")
    jobs_table(todo)
else:
    st.success("You have applied to every scored job in this range.")

with st.expander("Recent searches"):
    st.caption("Fetched is everything the APIs returned; New is what was not already saved.")
    st.dataframe(
        pd.DataFrame({
            "When (UTC)": [str(s["ran_at"])[:16].replace("T", " ") for s in searches],
            "Role": [s["role"] for s in searches],
            "Experience": [f"{s['experience']:g} yrs" if s["experience"] is not None else "" for s in searches],
            "Location": [s["location"] for s in searches],
            "Fetched": [s["fetched"] for s in searches],
            "New": [s["new_count"] for s in searches],
        }),
        hide_index=True, width="stretch",
    )
