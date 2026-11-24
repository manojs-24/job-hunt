"""Find companies that hire in a place, and where to apply.

Candidates come from four places: your watchlist, the employers in your saved jobs, Y Combinator's startup
list (many small startups you would never think to type in) and, optionally, names Claude suggests. Every
candidate is checked against its real careers page, so a name only becomes a verified result when jobs are
open there. Companies whose careers page cannot be read are still listed, marked unverified, with a search
link. Uses no job-API credits.
"""
import asyncio
import re
from datetime import datetime, timedelta, timezone

import httpx

from . import db, finder, yc
from .dedupe import norm_company
from .providers import careers
from .providers.geo import location_filter
from .relevance import Matcher

MAX_CLAUDE_NAMES = 25
RECENT_DAYS = 30
MAX_LOOKUPS = 100     # new careers-page guesses per run; answers are remembered, so the next run continues
SORTS = ("activity", "smallest")


def company_card(name, via, board, slug, kind, raw_jobs, in_place, role_score, sample_url, extra=None) -> dict:
    """Everything the page shows about one company whose careers page was read."""
    here, fits, titles, newest, recent = 0, 0, [], None, 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)
    for raw in raw_jobs:
        job = careers.normalise_job(board, name, raw)
        if not job["title"] or not in_place(job["location"]):
            continue
        here += 1
        posted = finder.parse_date(job["posted_at"])
        if posted:
            newest = max(newest, posted) if newest else posted
            recent += posted >= cutoff
        if role_score is not None and role_score(job["title"]) > 0:
            fits += 1
            if len(titles) < 3:
                titles.append(job["title"])
    card = {
        "name": name, "kind": kind, "found_via": via, "board": board, "slug": slug,
        "total_open": len(raw_jobs), "open_here": here, "posted_recently": recent,
        "newest": newest.date().isoformat() if newest else None,
        "role_matches": fits if role_score is not None else None, "example_titles": titles,
        "careers_url": finder.careers_url(board, slug, sample_url),
        "team_size": None, "batch": None, "one_liner": None, "website": None,
    }
    card.update(extra or {})
    return card


def _yc_facts(c: dict | None) -> dict:
    """The Y Combinator details for a company, or the same keys left empty when it is not on that list."""
    if not c:
        return {"team_size": None, "batch": None, "one_liner": None, "website": None}
    return {"team_size": c["team_size"], "batch": c["batch"] or None, "one_liner": c["one_liner"] or None,
            "website": c["website"] or None}


def _site_slug(website: str) -> str | None:
    """'https://www.inito.com/' -> 'inito', a good extra guess at the company's careers-page name."""
    label = yc.website_host(website).split(".")[0]
    label = re.sub(r"[^a-z0-9-]", "", label)
    return label if len(label) >= 3 else None


async def _look_up(cands: dict, cached: dict, max_lookups: int) -> dict:
    """For each candidate: {"found": {board, slug, jobs, how} | None, "transient": bool, "error": str | None}."""
    lookups = 0
    sem = asyncio.Semaphore(6)

    async with httpx.AsyncClient() as client:
        async def one(key: str, e: dict):
            nonlocal lookups
            async with sem:
                if e["board"]:                                   # the watchlist already knows the page
                    try:
                        jobs = await careers.fetch_company(client, e["board"], e["slug"])
                        return key, {"board": e["board"], "slug": e["slug"], "jobs": jobs}, False, None
                    except careers.CompanyError as err:
                        return key, None, True, str(err)
                note = cached.get(key)
                if note and note["board"] is None and not e["hint"]:
                    return key, None, False, None                # we looked before and found no careers page
                if note and note["board"]:
                    try:
                        jobs = await careers.fetch_company(client, note["board"], note["slug"])
                        return key, {"board": note["board"], "slug": note["slug"], "jobs": jobs}, False, None
                    except careers.BoardNotFound:
                        pass                                     # the company moved: look again
                    except careers.CompanyError as err:
                        return key, None, True, str(err)
                if lookups >= max_lookups and not e["hint"]:
                    return key, None, True, "left for the next run (lookup limit)"
                lookups += 1
                found, transient = await finder.find_board(client, e["name"], e["hint"], e["slugs"])
                return key, found, transient, None

        done = await asyncio.gather(*(one(k, e) for k, e in cands.items()))
    return {key: {"found": found, "transient": transient, "error": err} for key, found, transient, err in done}


def run(location: str, roles: list[str] | None = None, *, include_remote: bool = False, use_claude: bool = True,
        check_saved_jobs: bool = True, use_yc: bool = True, sort: str = "activity",
        max_lookups: int = MAX_LOOKUPS) -> dict:
    roles = [r.strip() for r in (roles or []) if r.strip()]
    location = location.strip()
    in_place = location_filter(location, include_remote)
    role_score = Matcher(roles).score if roles else None

    conn = db.connect()
    try:
        watch = db.list_companies(conn)
        saved = db.employers_in_location(conn, in_place) if check_saved_jobs else {}
    finally:
        conn.close()

    # 1. gather candidates, keyed by company so the same company is checked once
    cands: dict[str, dict] = {}

    def cand(name: str) -> dict:
        return cands.setdefault(norm_company(name), {"name": name, "via": [], "hint": None, "board": None,
                                                     "slug": None, "kind": None, "slugs": [], "yc": None})
    for c in watch:
        e = cand(c["name"])
        e.update(board=c["board"], slug=c["slug"], kind=c["kind"])
        e["via"].append("your watchlist")
    for key, info in saved.items():
        e = cand(info["name"])
        n = len(info["jobs"])
        e["via"].append(f"{n} saved job{'s' if n != 1 else ''} here")
        e["evidence"] = next(({"title": j["title"], "url": j["url"]} for j in info["jobs"] if j["url"]), None)
        for j in info["jobs"]:
            hint = finder.board_from_url(j["url"])
            if hint:
                e["hint"] = hint
                break
    notices: list[str] = []
    if use_yc:
        try:
            for c in yc.in_location(yc.load(), location):
                e = cand(c["name"])
                e["via"].append("Y Combinator list")
                e["yc"] = c
                e["kind"] = e["kind"] or yc.kind_of(c)
                guess = _site_slug(c["website"])
                if guess:
                    e["slugs"].append(guess)
        except yc.YcError as err:
            notices.append(str(err))
    if use_claude:
        names, notice = finder.suggest_companies(location, roles, MAX_CLAUDE_NAMES)
        if notice:
            notices.append(notice)
        for n in names:
            e = cand(n["name"])
            e["via"].append("Claude's suggestion")
            e["kind"] = e["kind"] or n["kind"]

    # 2. check each one's real careers page (remembered answers are reused)
    conn = db.connect()
    try:
        cached = {k: db.get_board_lookup(conn, k) for k in cands}
    finally:
        conn.close()
    looked = asyncio.run(_look_up(cands, cached, max_lookups))

    # 3. build the cards and remember what we learned about each company's careers page
    verified, unverified = [], []
    conn = db.connect()
    try:
        for key, e in cands.items():
            r = looked[key]
            found = r["found"]
            if found:
                sample = next((j.get("absolute_url") for j in found["jobs"] if isinstance(j, dict)), None)
                card = company_card(e["name"], e["via"], found["board"], found["slug"], e["kind"], found["jobs"],
                                    in_place, role_score, sample, _yc_facts(e["yc"]))
                if not e["board"]:
                    db.set_board_lookup(conn, key, found["board"], found["slug"], found.get("how"))
                if card["open_here"] > 0:
                    verified.append(card)
                continue
            if not r["transient"] and not e["board"]:
                db.set_board_lookup(conn, key, None, None, "no public careers page found")
            # keep companies we have a reason to think hire here, and say plainly that they are unverified
            has_job = bool(e.get("evidence"))
            if has_job or e["yc"] or "Claude's suggestion" in e["via"]:
                evidence = e.get("evidence") or {}
                facts = _yc_facts(e["yc"])
                unverified.append({
                    "name": e["name"], "kind": e["kind"], "found_via": e["via"],
                    "not_checked_yet": bool(r["error"] and "lookup limit" in r["error"]),
                    "reason": r["error"] or "no public careers page found",
                    "search_url": finder.search_link(e["name"], location),
                    "evidence_title": evidence.get("title"), "evidence_url": evidence.get("url"),
                    "yc_hiring": bool(e["yc"] and e["yc"]["hiring"]),
                    "evidence_rank": 0 if has_job else 1 if e["yc"] and e["yc"]["hiring"] else 2 if e["yc"] else 3,
                    **facts})
    finally:
        conn.close()

    if sort == "smallest":     # a rough guide to "underrated": the smallest teams first, unknown sizes last
        verified.sort(key=lambda c: (c["team_size"] is None, c["team_size"] or 0, -c["open_here"], c["name"].lower()))
        unverified.sort(key=lambda c: (c["team_size"] is None, c["team_size"] or 0, c["evidence_rank"],
                                       c["name"].lower()))
    else:                      # strongest evidence of active hiring first: fits your roles, recent, most openings
        verified.sort(key=lambda c: (-(c["role_matches"] or 0), -c["posted_recently"], -c["open_here"],
                                     c["name"].lower()))
        unverified.sort(key=lambda c: (c["evidence_rank"], c["not_checked_yet"], c["name"].lower()))
    return {"location": location or "India", "candidates": len(cands), "verified": verified,
            "unverified": unverified, "notice": " ".join(notices) or None, "roles": roles, "sort": sort,
            "yc_companies": sum(1 for e in cands.values() if e["yc"]),
            "not_checked_yet": sum(1 for c in unverified if c["not_checked_yet"])}
