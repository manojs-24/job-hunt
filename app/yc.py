"""Y Combinator's startup directory, as a free source of company names.

Uses a community-maintained copy of YC's public company list (yc-oss.github.io), so no key is needed.
It lists real, mostly small startups with their location, team size and a website, which makes it a way
to find companies you would never think to type in. It is not run by YC, so it can change or go away.

The list only covers YC-backed companies, and it gives a website, not a careers page: the finder still
has to find each company's careers page itself. `isHiring` is YC's own flag and can be out of date.
"""
import json
import re
import time
from pathlib import Path

import httpx

from .config import ROOT
from .providers.geo import location_filter

URL = "https://yc-oss.github.io/api/companies/all.json"
CACHE = ROOT / ".yc_cache.json"
CACHE_DAYS = 7
HEADERS = {"User-Agent": "Mozilla/5.0 (personal job search tool)"}
SMALL = 50       # people: at or below this we call a company small


class YcError(Exception):
    """The list could not be loaded. The message is safe to show."""


def _clean(raw: dict) -> dict:
    """Keep only the fields we use, with safe types."""
    def num(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    return {
        "name": str(raw.get("name") or "").strip(),
        "website": (raw.get("website") or "").strip(),
        "locations": str(raw.get("all_locations") or ""),
        "team_size": num(raw.get("team_size")),
        "batch": raw.get("batch") or "",
        "status": raw.get("status") or "",
        "hiring": bool(raw.get("isHiring")),
        "one_liner": str(raw.get("one_liner") or "")[:160],
        "url": raw.get("url") or "",
        "industry": raw.get("industry") or "",
    }


def _read_cache(path: Path, max_age_days: float) -> list[dict] | None:
    try:
        if time.time() - path.stat().st_mtime > max_age_days * 86400:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) else None


def load(*, client: httpx.Client | None = None, cache: Path | None = CACHE, max_age_days: float = CACHE_DAYS) -> list[dict]:
    """The whole directory. Uses a copy saved on disk for a week, so it is downloaded (11 MB) rarely."""
    if cache is not None:
        cached = _read_cache(cache, max_age_days)
        if cached is not None:
            return cached
    try:
        res = (client or httpx).get(URL, headers=HEADERS, timeout=90, follow_redirects=True)
        res.raise_for_status()
        raw = res.json()
        if not isinstance(raw, list):
            raise ValueError("not a list")
    except (httpx.HTTPError, ValueError) as err:
        stale = _read_cache(cache, 10_000) if cache is not None else None     # an old copy beats nothing
        if stale is not None:
            return stale
        raise YcError(f"Could not load the Y Combinator list ({err.__class__.__name__}).") from None
    companies = [c for c in map(_clean, raw) if c["name"]]
    if cache is not None:
        try:
            cache.write_text(json.dumps(companies), encoding="utf-8")
        except OSError:
            pass                                              # a read-only folder just means no cache
    return companies


def in_location(companies: list[dict], location: str, *, include_remote: bool = False,
                only_active: bool = True) -> list[dict]:
    """Companies with an office in `location` (same rules as the Location box elsewhere).

    A company can list several offices separated by ';', so it matches when any one of them does.
    """
    ok = location_filter(location, include_remote)
    out = []
    for c in companies:
        if only_active and c["status"] not in ("Active", "Public", ""):
            continue                                          # drop shut-down and acquired companies
        if any(ok(part.strip()) for part in c["locations"].split(";")):
            out.append(c)
    return out


def size_label(team_size: int | None) -> str:
    if team_size is None:
        return "size unknown"
    return f"{team_size} people"


def kind_of(c: dict) -> str:
    """Startup / Mid-size / Large from team size alone: a rough guide, not a fact."""
    size = c.get("team_size")
    if size is None or size <= 200:
        return "Startup"
    return "Mid-size" if size <= 1000 else "Large"


def website_host(url: str) -> str:
    """'https://www.inito.com/about?x=1' -> 'inito.com'."""
    return re.split(r"[/?#]", re.sub(r"^https?://(www\.)?", "", url or ""))[0].lower()
