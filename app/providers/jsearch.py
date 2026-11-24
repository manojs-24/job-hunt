import httpx

from ..config import settings
from .base import ProviderSkipped, clean, date_posted_param, format_salary, get_json

HOST = "jsearch.p.rapidapi.com"


def parse_jsearch_jobs(items: list[dict], provider: str) -> list[dict]:
    """Shared by JSearch (RapidAPI) and OpenWeb Ninja, which serve the same response shape."""
    return [
        {
            "title": clean(j.get("job_title")),
            "company": clean(j.get("employer_name")),
            "location": clean(j.get("job_location"))
            or ", ".join(p for p in [j.get("job_city"), j.get("job_state")] if p)
            or None,
            "remote": bool(j.get("job_is_remote")),
            "salary": format_salary(
                j.get("job_min_salary"), j.get("job_max_salary"),
                j.get("job_salary_currency"), j.get("job_salary_period"),
            ),
            "posted_at": clean(j.get("job_posted_at_datetime_utc")),
            "url": clean(j.get("job_apply_link")),
            "source": clean(j.get("job_publisher")),
            "provider": provider,
            "description": j.get("job_description") or "",
        }
        for j in items
    ]


async def search(client: httpx.AsyncClient, role: str, location: str, days: int | None) -> list[dict]:
    s = settings()
    if not s["jsearch_key"]:
        raise ProviderSkipped("no JSEARCH_RAPIDAPI_KEY set")
    headers = {"X-RapidAPI-Key": s["jsearch_key"], "X-RapidAPI-Host": HOST}
    items: list[dict] = []
    cursor = None
    for page in range(2):
        params = {
            "query": f"{role} in {location}",
            "country": s["country"],
            "date_posted": date_posted_param(days),
        }
        if cursor:
            params["cursor"] = cursor
        try:
            data = await get_json(client, f"https://{HOST}/search-v2", params=params, headers=headers)
        except httpx.HTTPError:
            if page == 0:
                raise
            break
        items.extend((data.get("data") or {}).get("jobs") or [])
        cursor = (data.get("data") or {}).get("cursor")
        if not cursor:
            break
    return parse_jsearch_jobs(items, "jsearch")
