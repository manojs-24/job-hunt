"""The numbers behind the dashboard. Plain pandas, so it can be tested without Streamlit.

Every function takes the list of job dicts from `db.query_jobs` and returns a small table whose
first column is the category and second is the job count (the names double as the table view).
"""
from collections import Counter

import pandas as pd

from .providers.base import PROVIDER_LABELS

STRONG_FIT = 70   # a score at or above this counts as a strong match
FIT_BANDS = [("0–19", 0, 19), ("20–39", 20, 39), ("40–59", 40, 59),
             ("60–79", 60, 79), ("80–100", 80, 100)]
EXPERIENCE_BANDS = ["Not stated", "Up to 1 yr", "1–3 yrs", "3–5 yrs", "5+ yrs"]


def kpis(rows: list[dict]) -> dict:
    scores = [r["score"] for r in rows if r.get("score") is not None]
    return {
        "total": len(rows),
        "strong": sum(s >= STRONG_FIT for s in scores),
        "saved": sum(1 for r in rows if r.get("saved")),
        "applied": sum(1 for r in rows if r.get("applied")),
        "avg_fit": round(sum(scores) / len(scores)) if scores else None,
        "unscored": len(rows) - len(scores),
    }


def per_day(rows: list[dict]) -> pd.DataFrame:
    """Jobs first found on each day, with quiet days shown as zero so gaps are visible."""
    counts = Counter(str(r["first_seen"])[:10] for r in rows if r.get("first_seen"))
    if not counts:
        return pd.DataFrame({"Day": [], "Jobs": []})
    days = pd.date_range(min(counts), max(counts), freq="D")
    return pd.DataFrame({
        "Day": [f"{d:%b} {d.day}" for d in days],
        "Jobs": [counts.get(f"{d:%Y-%m-%d}", 0) for d in days],
    })


def fit_distribution(rows: list[dict]) -> pd.DataFrame:
    scores = [r["score"] for r in rows if r.get("score") is not None]
    out = [(label, sum(lo <= s <= hi for s in scores)) for label, lo, hi in FIT_BANDS]
    unscored = len(rows) - len(scores)
    if unscored:
        out.append(("Not scored", unscored))
    return pd.DataFrame(out, columns=["Fit score", "Jobs"])


def _experience_band(years) -> str:
    if years is None:
        return "Not stated"
    if years <= 1:
        return "Up to 1 yr"
    if years <= 3:
        return "1–3 yrs"
    if years <= 5:
        return "3–5 yrs"
    return "5+ yrs"


def experience_distribution(rows: list[dict]) -> pd.DataFrame:
    counts = Counter(_experience_band(r.get("exp_required")) for r in rows)
    return pd.DataFrame([(band, counts.get(band, 0)) for band in EXPERIENCE_BANDS],
                        columns=["Experience asked", "Jobs"])


def by_api(rows: list[dict]) -> pd.DataFrame:
    """Jobs per API. A job found by two APIs counts once for each."""
    counts: Counter = Counter()
    for r in rows:
        found = [p for p in (r.get("found_by") or "").split(",") if p] or ["unknown"]
        for p in found:
            counts[PROVIDER_LABELS.get(p, "Unknown")] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return pd.DataFrame(ranked, columns=["Found by", "Jobs"])


def by_board(rows: list[dict], top: int = 8) -> tuple[pd.DataFrame, int]:
    """Top job boards (LinkedIn, Indeed...) and how many distinct boards there are in total."""
    counts = Counter(r["source"] for r in rows if r.get("source"))
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return pd.DataFrame(ranked[:top], columns=["Job board", "Jobs"]), len(counts)


def best_to_apply(rows: list[dict], n: int = 10) -> list[dict]:
    """Highest-scoring jobs not yet marked as applied."""
    todo = [r for r in rows if not r.get("applied") and r.get("score") is not None]
    return sorted(todo, key=lambda r: (-r["score"], str(r.get("first_seen"))))[:n]
