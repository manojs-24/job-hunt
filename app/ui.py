"""Streamlit pieces shared by both pages: the jobs table with its Save and Applied checkboxes."""
import re

import pandas as pd
import streamlit as st

from . import db
from .providers.base import provider_names

FLAGS = {"Saved": db.set_saved, "Applied": db.set_applied}   # editable checkbox columns


def _posted(value) -> str:
    if not value:
        return ""
    s = str(value)
    return s[:10] if re.match(r"\d{4}-\d{2}-\d{2}", s) else s  # keep "20 hours ago" as written


def _experience(value) -> str:
    return "" if value is None else f"{value:g} yrs"


def to_frame(rows: list[dict], new_ids=frozenset()) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": [r["id"] for r in rows],
            "Saved": [bool(r.get("saved")) for r in rows],
            "Applied": [bool(r.get("applied")) for r in rows],
            "New": ["NEW" if r["id"] in new_ids else "" for r in rows],
            "Title": [r["title"] for r in rows],
            "Company": [r["company"] for r in rows],
            "Found by": [provider_names(r.get("found_by")) for r in rows],
            "Location": [r.get("location") or "" for r in rows],
            "Experience": [_experience(r.get("exp_required")) for r in rows],
            "Salary": [r.get("salary") or "" for r in rows],
            "Posted": [_posted(r.get("posted_at")) for r in rows],
            "Source": [r.get("source") or "" for r in rows],
            "Fit": [r.get("score") for r in rows],
            "Why": [r.get("reason") or "" for r in rows],
            "You match": [r.get("skills_matched") or "" for r in rows],
            "You lack": [r.get("skills_missing") or "" for r in rows],
            "Apply": [r.get("url") for r in rows],
        }
    ).set_index("id")


def changed_flags(original: pd.DataFrame, edited: pd.DataFrame) -> list[tuple[int, str, bool]]:
    """(job id, column, new value) for every Saved/Applied checkbox that differs from what is stored."""
    changes = []
    for col in FLAGS:
        diff = edited[col] != original[col]
        changes.extend((int(i), col, bool(edited.loc[i, col])) for i in edited.index[diff])
    return changes


def persist_flags(changes: list[tuple[int, str, bool]]) -> None:
    conn = db.connect()
    try:
        for job_id, col, value in changes:
            FLAGS[col](conn, job_id, value)
    finally:
        conn.close()


def jobs_table(rows: list[dict], new_ids=frozenset()) -> None:
    """Show jobs; ticking Save or Applied stores the change straight away."""
    df = to_frame(rows, new_ids)
    has_skills = bool(df["You match"].any() or df["You lack"].any())   # only after a resume search
    # Most useful columns first: the table scrolls sideways, so the rest can sit off-screen.
    order = ["Saved", "Applied", *(["New"] if new_ids else []), "Title", "Company", "Found by", "Fit",
             "Apply", *(["You match", "You lack"] if has_skills else []), "Location", "Experience", "Posted",
             "Source", "Salary", "Why"]
    config = {
        "Saved": st.column_config.CheckboxColumn("Save", help="Tick to save this job", width=55),
        "Applied": st.column_config.CheckboxColumn("Applied", help="Tick once you have applied", width=70),
        "New": st.column_config.TextColumn("New", width=55),
        "Title": st.column_config.TextColumn("Title", width=270),
        "Company": st.column_config.TextColumn("Company", width=170),
        "Found by": st.column_config.TextColumn("Found by", width=150,
                                                help="Which API returned this job"),
        "Fit": st.column_config.ProgressColumn("Fit", min_value=0, max_value=100, format="%d", width=90,
                                               help="How well the job fits your role and experience"),
        "Apply": st.column_config.LinkColumn("Apply", display_text="Apply", width=70),
        "Location": st.column_config.TextColumn("Location", width=170),
        "Experience": st.column_config.TextColumn("Exp. needed", width=100),
        "Posted": st.column_config.TextColumn("Posted", width=110),
        "Source": st.column_config.TextColumn("Source", width=110, help="The job board that lists it"),
        "Salary": st.column_config.TextColumn("Salary", width=150),
        "Why": st.column_config.TextColumn("Why this score", width=420),
        "You match": st.column_config.TextColumn("Skills you match", width=240,
                                                 help="Your skills this job asks for"),
        "You lack": st.column_config.TextColumn("Skills you lack", width=240,
                                                help="Skills the job asks for that are not on your resume"),
    }
    edited = st.data_editor(
        df,
        hide_index=True,
        width="stretch",
        height=min(40 + 36 * len(df), 640),
        column_order=order,
        column_config=config,
        disabled=[c for c in df.columns if c not in FLAGS],
    )
    changes = changed_flags(df, edited)
    if changes:
        persist_flags(changes)
        st.rerun()
