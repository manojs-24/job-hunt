"""The search pipeline: fetch -> clean -> de-duplicate against the DB -> score new jobs."""
import asyncio

import httpx

from . import db
from .dedupe import dedupe_batch, too_old
from .providers import jsearch, openwebninja, serpapi
from .providers.base import ProviderSkipped, fail_reason
from .scoring import score_jobs

PROVIDERS = {"jsearch": jsearch, "serpapi": serpapi, "openwebninja": openwebninja}


async def fetch_all(role: str, location: str, days: int | None) -> tuple[list[dict], dict]:
    async with httpx.AsyncClient() as client:
        names = list(PROVIDERS)
        results = await asyncio.gather(
            *(PROVIDERS[n].search(client, role, location, days) for n in names),
            return_exceptions=True,
        )
    jobs: list[dict] = []
    report: dict = {}
    for name, res in zip(names, results):
        if isinstance(res, ProviderSkipped):
            report[name] = {"status": "skipped", "detail": str(res)}
        elif isinstance(res, Exception):
            report[name] = {"status": "error", "detail": fail_reason(res)}
        else:
            report[name] = {"status": "ok", "count": len(res)}
            jobs.extend(res)
    return jobs, report


def run_search(role: str, experience: float, location: str, days: int | None = None) -> dict:
    """Run one search end to end. Only jobs not already in the database are scored."""
    role, location = role.strip(), location.strip()
    fetched, providers = asyncio.run(fetch_all(role, location, days))

    usable = [j for j in fetched if j.get("title") and j.get("company") and not too_old(j, days)]
    unique, batch_dupes = dedupe_batch(usable)

    conn = db.connect()
    try:
        new_jobs = db.insert_new(conn, unique, role, location)
        scores, notice = score_jobs(role, experience, new_jobs)
        db.save_scores(conn, scores)
        db.record_search(conn, role, experience, location, len(fetched), len(new_jobs))
    finally:
        conn.close()

    return {
        "providers": providers,
        "fetched": len(fetched),
        "duplicates_in_batch": batch_dupes,
        "new_count": len(new_jobs),
        "already_seen": len(unique) - len(new_jobs),
        "notice": notice,
        "ids": [j["id"] for j in unique if "id" in j],
        "new_ids": [j["id"] for j in new_jobs],
    }


# --- resume search: several approved roles, scored against the candidate's skills --------------
MAX_ROLES_PER_RUN = 5
REQUESTS_PER_ROLE = 5   # JSearch 2 pages + SerpApi 2 pages + OpenWeb Ninja 1 page (at most)


def estimate_requests(n_roles: int) -> int:
    return n_roles * REQUESTS_PER_ROLE


def run_resume_search(roles: list[str], skills: list[str], experience: float, location: str,
                      days: int | None = None) -> dict:
    """Search every approved role, merge the results, and score all new jobs against the skills.

    The searches run one role after another so each stays inside the free plans' rate limits.
    A role whose APIs all fail is reported but does not stop the others.
    """
    roles = [r.strip() for r in roles if r.strip()][:MAX_ROLES_PER_RUN]
    location = location.strip()

    all_jobs: list[dict] = []
    per_role: dict[str, dict] = {}
    fetched_total = 0
    for role in roles:
        jobs, providers = asyncio.run(fetch_all(role, location, days))
        fetched_total += len(jobs)
        for j in jobs:
            j["matched_role"] = role
        all_jobs.extend(jobs)
        per_role[role] = {"providers": providers, "fetched": len(jobs)}

    usable = [j for j in all_jobs if j.get("title") and j.get("company") and not too_old(j, days)]
    unique, batch_dupes = dedupe_batch(usable)

    label = " / ".join(roles)[:200]
    conn = db.connect()
    try:
        new_jobs = db.insert_new(conn, unique, label, location)
        scores, notice = score_jobs(label, experience, new_jobs, skills=skills)
        db.save_scores(conn, scores)
        db.record_search(conn, label, experience, location, fetched_total, len(new_jobs))
    finally:
        conn.close()

    return {
        "per_role": per_role,
        "fetched": fetched_total,
        "duplicates_in_batch": batch_dupes,
        "new_count": len(new_jobs),
        "already_seen": len(unique) - len(new_jobs),
        "notice": notice,
        "ids": [j["id"] for j in unique if "id" in j],
        "new_ids": [j["id"] for j in new_jobs],
    }


# --- startups and remote: company careers pages + free remote job boards --------------------------
from .providers import careers, remote_boards   # noqa: E402
from .relevance import Matcher, rank              # noqa: E402

MAX_NEW_PER_RUN = 60     # the most new jobs saved (and sent to Claude for scoring) in one run


async def _fetch_startup_sources(companies: list[dict], roles: list[str], include_remote: bool,
                                 location: str = ""):
    async with httpx.AsyncClient() as client:
        tasks = ([careers.search_companies(client, companies, location=location, include_remote=include_remote)]
                 if companies else [])
        if include_remote:
            tasks.append(remote_boards.search_remote_boards(client, roles, location))
        results = await asyncio.gather(*tasks)
    company_out = results[0] if companies else {"jobs": [], "per_company": {}}
    remote_jobs, remote_report = results[-1] if include_remote else ([], {})
    return company_out, remote_jobs, remote_report


def run_startup_search(roles: list[str], experience: float, skills: list[str] | None = None, *,
                       location: str = "", company_ids: list[int] | None = None, include_remote: bool = True,
                       days: int | None = None, max_new: int = MAX_NEW_PER_RUN) -> dict:
    """Read the watchlist's careers pages (and the remote boards), keep jobs in `location` whose
    title fits the roles, save the best new ones and have Claude score them.

    `location` is a city, state or country, several separated by commas ("Bengaluru, Pune"), or
    blank for anywhere in India. `include_remote` also keeps jobs anyone can do from home.

    Free for the APIs: no JSearch / SerpApi / OpenWeb Ninja credit is used. Claude is called only
    for the (at most `max_new`) new jobs that survive the title match.
    """
    roles = [r.strip() for r in roles if r.strip()]
    conn = db.connect()
    try:
        companies = [c for c in db.list_companies(conn, enabled=True)
                     if company_ids is None or c["id"] in company_ids]
    finally:
        conn.close()

    location = location.strip()
    company_out, remote_jobs, remote_report = asyncio.run(
        _fetch_startup_sources(companies, roles, include_remote, location))
    fetched = company_out["jobs"] + remote_jobs

    matcher = Matcher(roles, skills)
    kept = []
    for job in fetched:
        if too_old(job, days) or not job.get("title") or not job.get("company"):
            continue
        m = matcher.score(job["title"])
        if m > 0:
            job["_rank"] = rank(job["title"], m, experience)
            kept.append(job)
    kept.sort(key=lambda j: -j["_rank"])
    unique, batch_dupes = dedupe_batch(kept)
    unique.sort(key=lambda j: -j["_rank"])          # best title match first, so the cap keeps the best

    label = " / ".join(roles)[:200] or "Startups"
    conn = db.connect()
    try:
        for cid, info in company_out["per_company"].items():
            db.record_company_check(conn, cid, info["total"], info["detail"] if info["status"] == "error" else None)
        where = location or "India"
        new_jobs = db.insert_new(conn, unique, label, where, limit_new=max_new)
        scores, notice = score_jobs(label, experience, new_jobs, skills=skills or None)
        db.save_scores(conn, scores)
        db.record_search(conn, label, experience, where, len(fetched), len(new_jobs))
    finally:
        conn.close()

    saved_ids = [j["id"] for j in unique if "id" in j]
    return {
        "per_company": company_out["per_company"],
        "remote": remote_report,
        "companies_checked": len(companies),
        "fetched": len(fetched),
        "matched_title": len(kept),
        "duplicates_in_batch": batch_dupes,
        "new_count": len(new_jobs),
        "already_seen": len(saved_ids) - len(new_jobs),
        "left_for_later": len(unique) - len(saved_ids),
        "notice": notice,
        "ids": saved_ids,
        "new_ids": [j["id"] for j in new_jobs],
    }
