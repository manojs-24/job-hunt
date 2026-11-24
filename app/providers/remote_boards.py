"""Free remote-job feeds: Remotive and Remote OK.

Both ask that you link back to the job's page on their site and name them as the source, so each
job keeps its original URL and the `source` column says where it came from. Remotive asks for at
most a few requests a day, and its free feed returns only the latest ~15 jobs, so this app asks
it once per search and no more.
"""
import re

import httpx

from .base import html_to_text
from .careers import HEADERS
from .geo import location_filter

REMOTIVE_URL = "https://remotive.com/api/remote-jobs"
REMOTEOK_URL = "https://remoteok.com/api"
MAX_DESC = 6000     # these descriptions are long; the scorer only reads the first 1,500 characters anyway


def _matches(text: str, terms: list[str]) -> bool:
    text = text.lower()
    return not terms or any(t in text for t in terms)


async def search_remotive(client: httpx.AsyncClient, terms: list[str], location: str = "") -> list[dict]:
    res = await client.get(REMOTIVE_URL, headers=HEADERS, timeout=40)
    res.raise_for_status()
    suits = location_filter(location, include_remote=True)     # these boards are all remote jobs
    jobs = []
    for j in res.json().get("jobs", []):
        blob = f"{j.get('title', '')} {' '.join(j.get('tags') or [])} {j.get('category', '')}"
        if not _matches(blob, terms) or not suits(j.get("candidate_required_location")):
            continue
        jobs.append({
            "title": j.get("title"), "company": j.get("company_name"),
            "location": f"Remote ({j['candidate_required_location']})" if j.get("candidate_required_location") else "Remote",
            "remote": True, "salary": j.get("salary") or None, "posted_at": j.get("publication_date"),
            "url": j.get("url"), "source": "Remotive", "provider": "remotive",
            "description": html_to_text(j.get("description"))[:MAX_DESC], "company_kind": None,
        })
    return jobs


async def search_remoteok(client: httpx.AsyncClient, terms: list[str], location: str = "") -> list[dict]:
    res = await client.get(REMOTEOK_URL, headers=HEADERS, timeout=40)
    res.raise_for_status()
    suits = location_filter(location, include_remote=True)
    jobs = []
    for j in res.json():
        if not isinstance(j, dict) or not j.get("position"):      # the first element is a legal notice
            continue
        blob = f"{j.get('position', '')} {' '.join(j.get('tags') or [])}"
        if not _matches(blob, terms) or not suits(j.get("location")):
            continue
        lo, hi = j.get("salary_min"), j.get("salary_max")
        salary = f"USD {lo}-{hi} per year" if lo and hi and str(lo) != "0" else None
        jobs.append({
            "title": j["position"], "company": j.get("company"),
            "location": f"Remote ({j['location']})" if j.get("location") else "Remote",
            "remote": True, "salary": salary, "posted_at": j.get("date"),
            "url": j.get("url") or j.get("apply_url"), "source": "Remote OK", "provider": "remoteok",
            "description": html_to_text(j.get("description"))[:MAX_DESC], "company_kind": None,
        })
    return jobs


async def search_remote_boards(client: httpx.AsyncClient, roles: list[str],
                               location: str = "") -> tuple[list[dict], dict]:
    """Search both feeds for any of the role words. One failing feed does not stop the other.

    These boards list only remote jobs, so `location` decides who may apply: jobs open worldwide
    or to the region you named stay, jobs limited to other regions go.
    """
    terms = sorted({w for r in roles for w in re.findall(r"[a-z0-9+#.]{3,}", r.lower())
                    if w not in {"and", "the", "for", "senior", "junior", "associate", "engineer", "developer"}}
                   | {r.lower() for r in roles})
    jobs: list[dict] = []
    report: dict[str, dict] = {}
    for name, fn in (("remotive", search_remotive), ("remoteok", search_remoteok)):
        try:
            found = await fn(client, terms, location)
        except httpx.HTTPError as err:
            report[name] = {"status": "error", "detail": f"{err.__class__.__name__}"}
        except (ValueError, KeyError, TypeError):
            report[name] = {"status": "error", "detail": "unexpected answer format"}
        else:
            report[name] = {"status": "ok", "count": len(found)}
            jobs.extend(found)
    return jobs, report
