"""Company careers pages on Greenhouse, Lever, Ashby, SmartRecruiters and Workable.

These services publish every company's open jobs as free public JSON, so they use none of your
API credits. A company's jobs are listed per company, so we need a watchlist of (board, slug)
pairs. Endpoints and fields were checked against the live services.
"""
import asyncio
from datetime import datetime, timezone

import httpx

from .base import html_to_text
from .geo import is_remote, location_filter

BOARDS = ("greenhouse", "lever", "ashby", "smartrecruiters", "workable")
# Tools tried when we are only guessing a company's board name. Workable is left out: it refuses after a
# handful of requests (HTTP 429, even at one per second), so guessing with it would spoil every other lookup.
# It still works whenever we already know the name: the watchlist, or a link to one of its jobs.
GUESS_BOARDS = ("greenhouse", "lever", "ashby", "smartrecruiters")
HEADERS = {"User-Agent": "Mozilla/5.0 (personal job search tool)", "accept": "application/json"}
URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    # 100 is the most SmartRecruiters returns per page; `_fetch_smartrecruiters` pages through the rest
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true",
}
SMARTRECRUITERS_MAX_PAGES = 5        # 500 postings is more than any one company needs here

class CompanyError(Exception):
    """A company's board could not be read. The message is safe to show."""


class BoardNotFound(CompanyError):
    """The hiring tool has no board under that name (a definite no, unlike a timeout)."""


def _iso_from_ms(ms) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _location_text(parts: list[str | None]) -> str | None:
    seen, out = set(), []
    for p in parts:
        p = (p or "").strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return "; ".join(out) or None


def _normalise(board: str, company: str, j: dict) -> dict:
    """One job, in the shape the rest of the app uses."""
    if board == "greenhouse":
        loc = _location_text([(j.get("location") or {}).get("name")])
        return {"title": j.get("title"), "company": company, "location": loc,
                "url": j.get("absolute_url"), "posted_at": j.get("first_published") or j.get("updated_at"),
                "description": html_to_text(j.get("content")), "remote_flag": None}
    if board == "lever":
        cat = j.get("categories") or {}
        loc = _location_text([cat.get("location"), *(cat.get("allLocations") or [])])
        return {"title": j.get("text"), "company": company, "location": loc,
                "url": j.get("hostedUrl"), "posted_at": _iso_from_ms(j.get("createdAt")),
                "description": j.get("descriptionPlain") or html_to_text(j.get("description")),
                "remote_flag": (j.get("workplaceType") or "").lower() == "remote" or None}
    if board == "smartrecruiters":
        place = j.get("location") or {}
        full = place.get("fullLocation") or ", ".join(
            p for p in (place.get("city"), place.get("region"), (place.get("country") or "").upper()) if p)
        loc = _location_text([full, "Remote" if place.get("remote") else None])
        ident = (j.get("company") or {}).get("identifier")
        return {"title": j.get("name"), "company": company, "location": loc,
                "url": f"https://jobs.smartrecruiters.com/{ident}/{j.get('id')}" if ident and j.get("id") else None,
                "posted_at": j.get("releasedDate"), "description": "",
                "remote_flag": bool(place.get("remote")) or None}
    if board == "workable":
        places = [", ".join(p for p in (x.get("city"), x.get("region"), x.get("country")) if p)
                  for x in (j.get("locations") or []) if isinstance(x, dict)]
        loc = _location_text(places or [", ".join(p for p in (j.get("city"), j.get("state"), j.get("country")) if p)])
        return {"title": j.get("title"), "company": company, "location": loc,
                "url": j.get("url") or j.get("shortlink"), "posted_at": j.get("published_on") or j.get("created_at"),
                "description": html_to_text(j.get("description")),
                "remote_flag": bool(j.get("telecommuting")) or None}
    loc = _location_text([j.get("location"), *[s if isinstance(s, str) else (s or {}).get("location")
                                               for s in (j.get("secondaryLocations") or [])]])
    return {"title": j.get("title"), "company": company, "location": loc,
            "url": j.get("jobUrl") or j.get("applyUrl"), "posted_at": j.get("publishedAt"),
            "description": j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
            "remote_flag": bool(j.get("isRemote")) or None}


def normalise_job(board: str, company: str, job: dict) -> dict:
    """One raw job from `fetch_company`, in the shape the rest of the app uses."""
    return _normalise(board, company, job)


async def _get_json(client: httpx.AsyncClient, url: str, board: str, slug: str):
    """GET a board and return its parsed answer, or raise CompanyError with a message safe to show."""
    try:
        res = await client.get(url, headers=HEADERS, timeout=30)
    except httpx.TimeoutException:
        raise CompanyError("timed out") from None
    except httpx.HTTPError as err:
        raise CompanyError(f"could not connect ({err.__class__.__name__})") from None
    if res.status_code == 404:
        raise BoardNotFound(f"no {board} board named '{slug}'")
    if res.status_code == 429:
        raise CompanyError("rate limited, try again later")
    if res.status_code != 200:
        raise CompanyError(f"HTTP {res.status_code}")
    try:
        return res.json()
    except ValueError:
        raise CompanyError("sent an unreadable answer") from None


async def _fetch_smartrecruiters(client: httpx.AsyncClient, slug: str) -> list[dict]:
    """SmartRecruiters answers 200 with an empty list for a company it does not know, so an empty
    first page is treated as 'no such board'."""
    out: list[dict] = []
    for page in range(SMARTRECRUITERS_MAX_PAGES):
        data = await _get_json(client, f"{URLS['smartrecruiters'].format(slug=slug)}&offset={page * 100}",
                               "smartrecruiters", slug)
        batch = data.get("content") if isinstance(data, dict) else None
        if not isinstance(batch, list):
            raise CompanyError("unexpected answer format")
        out.extend(batch)
        if len(batch) < 100:
            break
    if not out:
        raise BoardNotFound(f"no smartrecruiters board named '{slug}'")
    return out


async def fetch_company(client: httpx.AsyncClient, board: str, slug: str) -> list[dict]:
    """Every open job of one company, unfiltered. Raises CompanyError with a friendly message."""
    if board not in URLS:
        raise CompanyError(f"unknown board '{board}'")
    if board == "smartrecruiters":
        return await _fetch_smartrecruiters(client, slug)
    data = await _get_json(client, URLS[board].format(slug=slug), board, slug)
    jobs = data if isinstance(data, list) else (data.get("jobs") if isinstance(data, dict) else None)
    if not isinstance(jobs, list):
        raise CompanyError("unexpected answer format")
    return jobs


async def search_companies(client: httpx.AsyncClient, companies: list[dict], *, location: str = "",
                           include_remote: bool = True, concurrency: int = 6) -> dict:
    """Read every company's board and keep the jobs in `location` (blank = anywhere in India).

    Returns {"jobs": [...], "per_company": {id: {...}}}. `total` is how many open jobs the company
    has; `kept` is how many of them are in the place you asked for.
    """
    sem = asyncio.Semaphore(concurrency)
    in_wanted_place = location_filter(location, include_remote)

    async def one(c: dict):
        async with sem:
            try:
                raw = await fetch_company(client, c["board"], c["slug"])
            except CompanyError as err:
                return c, None, str(err)
            return c, raw, None

    out_jobs: list[dict] = []
    report: dict[int, dict] = {}
    for c, raw, err in await asyncio.gather(*(one(c) for c in companies)):
        if err:
            report[c["id"]] = {"name": c["name"], "status": "error", "detail": err, "total": None, "kept": 0}
            continue
        kept = 0
        for j in raw:
            job = _normalise(c["board"], c["name"], j)
            if not job["title"] or not job["url"]:
                continue
            if not in_wanted_place(job["location"]):
                continue
            job.update(remote=is_remote(job["location"], job.pop("remote_flag")), provider=c["board"], source=f"{c['name']} careers",
                       company_kind=c.get("kind"), salary=None)
            out_jobs.append(job)
            kept += 1
        report[c["id"]] = {"name": c["name"], "status": "ok", "detail": "", "total": len(raw), "kept": kept}
    return {"jobs": out_jobs, "per_company": report}
