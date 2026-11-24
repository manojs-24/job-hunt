import pandas as pd
import streamlit as st

from app import db, export, service
from app.providers import careers
from app.providers.base import PROVIDER_LABELS
from app.ui import jobs_table

KINDS = ["Startup", "Mid-size", "Large"]
BOARD_HELP = {
    "greenhouse": "boards.greenhouse.io/**name**  or  job-boards.greenhouse.io/**name**",
    "lever": "jobs.lever.co/**name**",
    "ashby": "jobs.ashbyhq.com/**name**",
}

S = st.session_state
S.setdefault("startup_result", None)

st.title("Startups & remote jobs")
st.caption("Reads company careers pages (Greenhouse, Lever, Ashby) and free remote job boards. "
           "No job-API credits are used. Only jobs that fit your roles are saved and scored.")

run_tab, list_tab = st.tabs(["Find jobs", "Company list"])

# ======================================================================== find jobs
with run_tab:
    conn = db.connect()
    try:
        companies = db.list_companies(conn, enabled=True)
        profiles = db.list_profiles(conn)
    finally:
        conn.close()

    with st.container(border=True):
        st.subheader("What are you looking for?")
        if profiles:
            names = {f"{p['name'] or 'Profile'} ({len(p['skills'])} skills, {p['experience']:g} yrs)": p
                     for p in profiles}
            pick = st.selectbox("Start from a saved resume profile (optional)", ["None", *names], key="s_profile")
            if pick != "None" and S.get("s_loaded") != pick:
                p = names[pick]
                S["s_roles"] = ", ".join(p["roles"][:5])
                S["s_exp"] = float(p["experience"])
                S["s_skills"] = list(p["skills"])
                S["s_loaded"] = pick
                st.rerun()
        c1, c2, c3 = st.columns([3, 2, 1])
        c1.text_input("Roles (separate with commas)", key="s_roles", value=S.get("s_roles", "Web Developer, Full Stack Developer"),
                      help="A job is kept if its title matches any of these.")
        c2.text_input("Location", key="s_loc", value=S.get("s_loc", "Bengaluru"), placeholder="e.g. Bengaluru, Pune",
                      help="A city, state or country. Use commas for several places, such as 'Bengaluru, Pune'. "
                           "Leave blank or type 'India' for anywhere in India. 'NCR' covers Delhi, Noida and Gurugram.")
        c3.number_input("Your experience (years)", min_value=0.0, max_value=60.0, step=0.5,
                        key="s_exp", value=S.get("s_exp", 2.0))
        skills_in_use = S.get("s_skills") or []
        if skills_in_use:
            st.caption(f"Using {len(skills_in_use)} skills from your resume profile: "
                       f"{', '.join(skills_in_use[:8])}{'...' if len(skills_in_use) > 8 else ''}. "
                       "Jobs will show the skills you match and lack.")

        d1, d2, d3 = st.columns([1, 1, 1])
        include_remote = d1.checkbox("Also include remote jobs", value=True,
                                     help="Keeps jobs anyone can do from home, next to the ones in your location, and "
                                          "reads the Remotive and Remote OK boards. Jobs limited to other regions are dropped.")
        days = d2.number_input("Posted within (days)", min_value=0, max_value=365, value=0,
                               help="0 means any time. Careers pages show how long ago a job was posted.")
        max_new = d3.number_input("Save at most (new jobs)", min_value=5, max_value=200,
                                  value=service.MAX_NEW_PER_RUN, step=5,
                                  help="Caps how many new jobs are saved and scored by Claude in one run. "
                                       "The rest are left for the next run.")

        by_kind = {k: [c for c in companies if c["kind"] == k] for k in KINDS}
        chosen_kinds = st.multiselect("Company types to check", KINDS,
                                      default=[k for k in KINDS if k != "Large" and by_kind[k]], key="s_kinds")
        selected = [c for c in companies if c["kind"] in chosen_kinds]
        where = S.get("s_loc", "").strip() or "India"
        st.info(f"Will check **{len(selected)}** companies"
                f"{' and the remote boards' if include_remote else ''} for jobs in **{where}**"
                f"{' and remote' if include_remote else ''}. "
                "Edit the list on the **Company list** tab.", icon=":material/info:")

        roles = [r.strip() for r in S.get("s_roles", "").split(",") if r.strip()]
        ready = bool(roles) and (bool(selected) or include_remote)
        go = st.button("Find jobs", type="primary", icon=":material/rocket_launch:", disabled=not ready,
                       help=None if ready else "Enter at least one role, and pick companies or the remote boards.")

    if go:
        try:
            with st.spinner("Reading careers pages and scoring the best new matches. This can take a minute..."):
                S["startup_result"] = service.run_startup_search(
                    roles, S["s_exp"], S.get("s_skills") or None, location=S.get("s_loc", ""),
                    company_ids=[c["id"] for c in selected],
                    include_remote=include_remote, days=days or None, max_new=int(max_new))
        except Exception as err:   # show the problem instead of a blank page
            S["startup_result"] = None
            st.error(f"Search failed: {err}")

    result = S["startup_result"]
    if result:
        st.subheader("Results")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Jobs read", result["fetched"], help="Jobs in your location (and remote ones, if ticked) out of everything "
                  "the careers pages and remote boards returned.")
        m2.metric("Matched your roles", result["matched_title"])
        m3.metric("New", result["new_count"])
        m4.metric("Already saved", result["already_seen"])
        if result["left_for_later"]:
            st.info(f"{result['left_for_later']} more new matches were left for your next run, because the limit of "
                    f"{int(max_new)} new jobs per run was reached. Run it again to see them.", icon=":material/schedule:")
        if result["notice"]:
            st.warning(result["notice"])

        with st.expander("What each source returned"):
            failed = [v for v in result["per_company"].values() if v["status"] == "error"]
            if failed:
                st.warning("Could not read: " + "; ".join(f"{v['name']} ({v['detail']})" for v in failed))
            if result["per_company"]:
                st.dataframe(pd.DataFrame([
                    {"Company": v["name"], "Open jobs": v["total"], "In your location": v["kept"],
                     "Status": "ok" if v["status"] == "ok" else v["detail"]}
                    for v in sorted(result["per_company"].values(), key=lambda v: -(v["kept"] or 0))]),
                    hide_index=True, width="stretch")
            for name, r in result["remote"].items():
                st.markdown(f"**{PROVIDER_LABELS.get(name, name)}**: "
                            + (f"{r['count']} matching jobs" if r["status"] == "ok" else f"error ({r['detail']})"))

        conn = db.connect()
        try:
            rows = db.query_jobs(conn, only_ids=result["ids"])
        finally:
            conn.close()
        if rows:
            jobs_table(rows, new_ids=frozenset(result["new_ids"]))
            e1, e2, _ = st.columns([1, 1, 4])
            e1.download_button("Download CSV", export.to_csv(rows), "startup_jobs.csv", "text/csv",
                               icon=":material/download:", width="stretch")
            e2.download_button("Download Excel", export.to_xlsx(rows), "startup_jobs.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               icon=":material/download:", width="stretch")
        else:
            st.info("No jobs matched those roles. Try broader role words, or add more companies.")

# ======================================================================== company list
with list_tab:
    conn = db.connect()
    try:
        all_companies = db.list_companies(conn)
    finally:
        conn.close()

    with st.container(border=True):
        st.subheader("Add a company")
        st.caption("Find a company's careers page, then copy the name from its web address.")
        a1, a2, a3, a4 = st.columns([2, 2, 2, 1], vertical_alignment="bottom")
        new_name = a1.text_input("Company name", key="n_name", placeholder="e.g. Razorpay")
        new_board = a2.selectbox("Careers page runs on", careers.BOARDS, key="n_board",
                                 format_func=lambda b: PROVIDER_LABELS[b])
        new_slug = a3.text_input("Name in the web address", key="n_slug", placeholder="e.g. razorpay")
        new_kind = a4.selectbox("Type", KINDS, key="n_kind")
        st.caption("Web address looks like: " + BOARD_HELP[new_board])
        if st.button("Check and add", icon=":material/add:"):
            slug = new_slug.strip().strip("/").split("/")[-1].lower()
            if not new_name.strip() or not slug:
                st.error("Enter the company name and the name from its web address.")
            else:
                import asyncio
                import httpx

                async def probe():
                    async with httpx.AsyncClient() as c:
                        return await careers.fetch_company(c, new_board, slug)
                try:
                    with st.spinner("Checking the careers page..."):
                        jobs = asyncio.run(probe())
                except careers.CompanyError as err:
                    st.error(f"Could not add it: {err}. Check the board and the spelling.")
                else:
                    conn = db.connect()
                    try:
                        added = db.add_company(conn, new_name, new_board, slug, new_kind)
                    finally:
                        conn.close()
                    if added is None:
                        st.warning("That company is already on your list.")
                    else:
                        st.success(f"Added {new_name.strip()}. It has {len(jobs)} open jobs right now.")
                        st.rerun()

    st.subheader(f"Your companies ({len(all_companies)})")
    if not all_companies:
        st.info("The list is empty. Add a company above.")
    else:
        df = pd.DataFrame({
            "id": [c["id"] for c in all_companies],
            "Check": [bool(c["enabled"]) for c in all_companies],
            "Company": [c["name"] for c in all_companies],
            "Careers page": [PROVIDER_LABELS.get(c["board"], c["board"]) for c in all_companies],
            "Name in address": [c["slug"] for c in all_companies],
            "Type": [c["kind"] for c in all_companies],
            "Open jobs (last run)": [c["last_total"] for c in all_companies],
            "Last problem": [c["last_error"] or "" for c in all_companies],
            "Remove": [False] * len(all_companies),
        }).set_index("id")
        edited = st.data_editor(
            df, hide_index=True, width="stretch", key="company_editor",
            column_config={
                "Check": st.column_config.CheckboxColumn("Check", help="Untick to skip this company", width=60),
                "Type": st.column_config.SelectboxColumn("Type", options=KINDS, width=90),
                "Remove": st.column_config.CheckboxColumn("Remove", help="Tick, then press Save changes", width=70),
            },
            disabled=["Company", "Careers page", "Name in address", "Open jobs (last run)", "Last problem"])
        if st.button("Save changes", icon=":material/save:"):
            conn = db.connect()
            try:
                gone = [int(i) for i in edited.index[edited["Remove"]]]
                for i in edited.index:
                    if int(i) in gone:
                        continue
                    row, old = edited.loc[i], df.loc[i]
                    if row["Check"] != old["Check"] or row["Type"] != old["Type"]:
                        db.update_company(conn, int(i), enabled=bool(row["Check"]), kind=row["Type"])
                removed = db.delete_companies(conn, gone)
            finally:
                conn.close()
            st.success("Saved." + (f" Removed {removed}." if removed else ""))
            st.rerun()
