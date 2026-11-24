import re

import httpx

from ..config import settings
from ..dedupe import strip_utm
from .base import ProviderSkipped, clean, get_json

STATES = {
    "bengaluru": "Karnataka", "bangalore": "Karnataka", "mysuru": "Karnataka", "mysore": "Karnataka",
    "mumbai": "Maharashtra", "pune": "Maharashtra", "nagpur": "Maharashtra", "thane": "Maharashtra",
    "delhi": "Delhi", "new delhi": "Delhi", "gurugram": "Haryana", "gurgaon": "Haryana",
    "faridabad": "Haryana", "noida": "Uttar Pradesh", "ghaziabad": "Uttar Pradesh",
    "lucknow": "Uttar Pradesh", "hyderabad": "Telangana", "chennai": "Tamil Nadu",
    "coimbatore": "Tamil Nadu", "kolkata": "West Bengal", "ahmedabad": "Gujarat",
    "surat": "Gujarat", "jaipur": "Rajasthan", "kochi": "Kerala",
    "thiruvananthapuram": "Kerala", "indore": "Madhya Pradesh", "bhopal": "Madhya Pradesh",
    "chandigarh": "Chandigarh", "bhubaneswar": "Odisha", "visakhapatnam": "Andhra Pradesh",
}
COUNTRY_NAMES = {"in": "India", "us": "United States", "gb": "United Kingdom", "ca": "Canada", "au": "Australia"}


async def search(client: httpx.AsyncClient, role: str, location: str, days: int | None) -> list[dict]:
    s = settings()
    if not s["serpapi_key"]:
        raise ProviderSkipped("no SERPAPI_KEY set")
    city = location.strip()
    state = STATES.get(city.lower()) if s["country"] == "in" else None
    country_name = COUNTRY_NAMES.get(s["country"], s["country"].upper())
    items: list[dict] = []
    token = None
    for page in range(2):
        params = {
            "engine": "google_jobs",
            "q": role,
            "location": ", ".join(p for p in [city, state, country_name] if p),
            "gl": s["country"],
            "hl": "en",
            "api_key": s["serpapi_key"],
        }
        if token:
            params["next_page_token"] = token
        try:
            data = await get_json(client, "https://serpapi.com/search.json", params=params)
        except httpx.HTTPError:
            if page == 0:
                raise
            break
        if data.get("error") and not data.get("jobs_results"):
            if page > 0 or re.search(r"hasn't returned any results", data["error"], re.I):
                break
            raise RuntimeError(data["error"])
        items.extend(data.get("jobs_results") or [])
        token = (data.get("serpapi_pagination") or {}).get("next_page_token")
        if not token:
            break
    return [
        {
            "title": clean(j.get("title")),
            "company": clean(j.get("company_name")),
            "location": clean(j.get("location")),
            "remote": bool((j.get("detected_extensions") or {}).get("work_from_home")),
            "salary": clean((j.get("detected_extensions") or {}).get("salary")),
            "posted_at": clean((j.get("detected_extensions") or {}).get("posted_at")),
            "url": strip_utm(((j.get("apply_options") or [{}])[0]).get("link") or j.get("share_link")),
            "source": re.sub(r"^via\s+", "", j["via"], flags=re.I) if j.get("via") else None,
            "provider": "serpapi",
            "description": j.get("description") or "",
        }
        for j in items
    ]
