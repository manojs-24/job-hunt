"""OpenWeb Ninja Real-Time Jobs Data: Google Jobs search.

GET https://api.openwebninja.com/realtime-jobs-data/google-jobs/search
Header X-API-Key. Params: query, page, num_pages, country, language.
Each result uses JSearch's field names, so the JSearch parser is reused.

The free plan is a hard 100 requests/month at 1 request/second, so by default
only one page (10 jobs) is fetched per search. Raise OPENWEB_NINJA_PAGES to get more.
"""
import asyncio

import httpx

from ..config import settings
from .base import ProviderSkipped, get_json
from .jsearch import parse_jsearch_jobs


async def search(client: httpx.AsyncClient, role: str, location: str, days: int | None) -> list[dict]:
    s = settings()
    if not s["ninja_key"]:
        raise ProviderSkipped("no OPENWEB_NINJA_API_KEY set")
    headers = {s["ninja_header"]: s["ninja_key"]}
    items: list[dict] = []
    for page in range(1, s["ninja_pages"] + 1):
        params = {
            "query": f"{role} jobs in {location}",
            "page": page,
            "num_pages": 1,
            "country": s["country"],
            "language": "en",
        }
        if page > 1:
            await asyncio.sleep(1.1)  # plan limit: 1 request per second
        try:
            data = await get_json(client, s["ninja_url"], params=params, headers=headers)
        except httpx.HTTPError:
            if page == 1:
                raise
            break
        batch = data.get("data") or []
        if not isinstance(batch, list) or not batch:
            break
        items.extend(batch)
    return parse_jsearch_jobs(items, "openwebninja")
