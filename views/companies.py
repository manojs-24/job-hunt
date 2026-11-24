import pandas as pd
import streamlit as st

from app import company_finder, db
from app.providers.base import PROVIDER_LABELS

S = st.session_state
S.setdefault("company_result", None)

st.title("Find companies hiring")
st.caption("Type a place and see which companies are hiring there, with a link to each one's careers page "
           "so you can apply directly. Free: no job-API credits are used.")

with st.container(border=True):
    c1, c2 = st.columns([2, 3])
    c1.text_input("Location", key="c_loc", value=S.get("c_loc", "Bengaluru"), placeholder="e.g. Bengaluru, Pune",
                  help="A city, state or country. Use commas for several places. Blank or 'India' means anywhere in India. "
                       "'NCR' covers Delhi, Noida and Gurugram.")
    c2.text_input("Roles you want (optional)", key="c_roles", value=S.get("c_roles", ""),
                  placeholder="e.g. Web Developer, Full Stack Developer",
                  help="If you add roles, companies with matching job titles are listed first.")
    o1, o2, o3, o4 = st.columns(4)
    use_yc = o1.checkbox("Include startups from Y Combinator", value=True,
                         help="Y Combinator's public list of startups, many of them small and little known. "
                              "Free. Only YC-backed companies are covered.")
    use_claude = o2.checkbox("Ask Claude for more names", value=True,
                             help="Claude suggests well-known companies for the place. Each name is checked against the "
                                  "company's real careers page, so nothing is listed as hiring on Claude's word alone.")
    saved_jobs = o3.checkbox("Include my saved jobs' companies", value=True,
                             help="Employers in the jobs you already fetched that are located in this place.")
    remote = o4.checkbox("Also count remote jobs", value=False,
                         help="Counts jobs anyone can do from home, next to the ones in your place.")
    sort_label = st.radio("Order the list by", ["Most active hiring", "Smallest teams first"], horizontal=True,
                          key="c_sort", help="'Smallest teams first' is a rough guide to lesser-known companies: "
                                             "it only works where a team size is known, which is mostly YC startups.")
    go = st.button("Find companies", type="primary", icon=":material/search:")

if go:
    try:
        with st.spinner("Checking careers pages. The first run for a place can take a few minutes..."):
            S["company_result"] = company_finder.run(
                S["c_loc"], [r.strip() for r in S.get("c_roles", "").split(",") if r.strip()],
                include_remote=remote, use_claude=use_claude, check_saved_jobs=saved_jobs, use_yc=use_yc,
                sort="smallest" if sort_label.startswith("Smallest") else "activity")
    except Exception as err:     # show the problem instead of a blank page
        S["company_result"] = None
        st.error(f"Search failed: {err}")

res = S["company_result"]
if res is None:
    st.subheader("How it works")
    for col, (n, title, text) in zip(st.columns(3), [
        ("1", "Gather names", "From your watchlist, your saved jobs, Y Combinator's startup list and companies Claude suggests."),
        ("2", "Check the real careers page", "Companies on Greenhouse, Lever, Ashby, SmartRecruiters or Workable are checked for jobs open in your place."),
        ("3", "Go and apply", "Open a company's careers page from this list. The others get a search link."),
    ]):
        with col.container(border=True):
            st.markdown(f"**{n}. {title}**")
            st.write(text)
    st.stop()

verified, unverified = res["verified"], res["unverified"]
m1, m2, m3 = st.columns(3)
m1.metric("Names considered", res["candidates"])
m2.metric("Hiring here (verified)", len(verified),
          help="Their careers page was read and has jobs open in your place right now.")
m3.metric("Possible, not verified", len(unverified),
          help="They may well hire here, but their careers page is not one we can read.")
if res["notice"]:
    st.warning(res["notice"])
if res["not_checked_yet"]:
    st.info(f"{res['not_checked_yet']} companies were left unchecked to keep this run quick. Press **Find companies** "
            "again and it continues from where it stopped, because it remembers what it has already found.",
            icon=":material/schedule:")

# ---------------------------------------------------------------- verified
st.subheader(f"Hiring in {res['location']} ({len(verified)})")
if not verified:
    st.info("No company's careers page showed open jobs here yet. Try a larger place, tick 'Also count remote jobs', "
            "or press Find companies again to check more names.")
else:
    if res.get("sort") == "smallest":
        st.caption("Ordered by team size, smallest first. A team size is only known for Y Combinator startups, so "
                   "other companies come last. Size is a rough guide to lesser-known companies, not a rating.")
    else:
        st.caption("Ordered by: jobs that match your roles, then how many were posted in the last "
                   f"{company_finder.RECENT_DAYS} days, then how many openings there are here.")
    has_roles = bool(res["roles"])
    df = pd.DataFrame({
        "Company": [c["name"] for c in verified],
        "What they do": [c["one_liner"] or "" for c in verified],
        "Type": [c["kind"] or "" for c in verified],
        "Team size": [c["team_size"] for c in verified],
        "YC batch": [c["batch"] or "" for c in verified],
        "Open here": [c["open_here"] for c in verified],
        "Posted in 30 days": [c["posted_recently"] for c in verified],
        "Newest posting": [c["newest"] or "" for c in verified],
        **({"Match your roles": [c["role_matches"] for c in verified]} if has_roles else {}),
        "Example jobs": ["; ".join(c["example_titles"]) for c in verified] if has_roles else [""] * len(verified),
        "Careers page": [c["careers_url"] for c in verified],
        "Runs on": [PROVIDER_LABELS.get(c["board"], c["board"]) for c in verified],
        "Found via": [", ".join(c["found_via"]) for c in verified],
    })
    if not has_roles:
        df = df.drop(columns=["Example jobs"])
    if not any(c["team_size"] is not None or c["batch"] for c in verified):     # no YC company here: hide the empty columns
        df = df.drop(columns=["What they do", "Team size", "YC batch"])
    st.dataframe(
        df, hide_index=True, width="stretch",
        column_config={
            "Careers page": st.column_config.LinkColumn("Careers page", display_text="Open careers page", width="medium"),
            "Open here": st.column_config.NumberColumn(help="Jobs open right now in your place"),
            "Team size": st.column_config.NumberColumn(help="People at the company, from Y Combinator's list", format="%d"),
        })

    with st.expander("Add these to my watchlist"):
        st.caption("Companies on your watchlist are re-checked on the **Startups & remote** page, where their jobs are "
                   "scored and saved. Pick the ones you want to follow.")
        conn = db.connect()
        try:
            known = {(c["board"], c["slug"]) for c in db.list_companies(conn)}
        finally:
            conn.close()
        fresh = [c for c in verified if (c["board"], c["slug"]) not in known]
        if not fresh:
            st.write("Every company above is already on your watchlist.")
        else:
            pick = st.multiselect("Companies", [c["name"] for c in fresh], key="c_add")
            if st.button("Add selected", disabled=not pick, icon=":material/add:"):
                conn = db.connect()
                try:
                    added = sum(bool(db.add_company(conn, c["name"], c["board"], c["slug"], c["kind"] or "Startup"))
                                for c in fresh if c["name"] in pick)
                finally:
                    conn.close()
                st.success(f"Added {added} to your watchlist.")

# ---------------------------------------------------------------- unverified
if unverified:
    with_evidence = [c for c in unverified if c["evidence_url"]]
    st.subheader(f"Possibly hiring, not verified ({len(unverified)})")
    st.caption("We could not read these companies' careers pages automatically, so there is no live job count. "
               "The strongest evidence is first: a real job we saved in your place, then startups Y Combinator marks "
               "as hiring (its own flag, which can be out of date), then other Y Combinator startups and Claude's "
               "suggestions, which are only ideas to check. Use the search link to find each careers page.")
    show = st.radio("Show", ["Strongest evidence first", "Only with a saved job", "Only Y Combinator marked hiring",
                             "Only Claude's suggestions"], horizontal=True, key="c_show")
    rows = (with_evidence if "saved job" in show else
            [c for c in unverified if c["yc_hiring"]] if "marked hiring" in show else
            [c for c in unverified if "Claude's suggestion" in c["found_via"]] if "Claude" in show else unverified)

    def evidence(c: dict) -> str:
        if c["evidence_title"]:
            return f"Saved job: {c['evidence_title']}"
        if c["yc_hiring"]:
            return "Y Combinator marks it as hiring"
        return "Y Combinator startup" if "Y Combinator list" in c["found_via"] else "Claude suggestion only"
    st.dataframe(
        pd.DataFrame({
            "Company": [c["name"] for c in rows],
            "What they do": [c["one_liner"] or "" for c in rows],
            "Type": [c["kind"] or "" for c in rows],
            "Team size": [c["team_size"] for c in rows],
            "Evidence": [evidence(c) for c in rows],
            "Website": [c["website"] for c in rows],
            "A job we saw": [c["evidence_url"] for c in rows],
            "Find careers page": [c["search_url"] for c in rows],
            "Why not verified": ["not checked yet" if c["not_checked_yet"] else c["reason"] for c in rows],
        }),
        hide_index=True, width="stretch", height=min(40 + 36 * len(rows), 520),
        column_config={
            "Website": st.column_config.LinkColumn("Website", display_text="Open site", width="small"),
            "A job we saw": st.column_config.LinkColumn("A job we saw", display_text="Open job", width="small"),
            "Find careers page": st.column_config.LinkColumn("Find careers page", display_text="Search Google", width="small"),
            "Team size": st.column_config.NumberColumn(format="%d"),
        })
