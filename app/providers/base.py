import html
import re

import httpx

PROVIDER_LABELS = {
    "jsearch": "JSearch", "serpapi": "SerpApi", "openwebninja": "OpenWeb Ninja",
    "greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby",
    "smartrecruiters": "SmartRecruiters", "workable": "Workable",
    "remoteok": "Remote OK", "remotive": "Remotive",
}


def html_to_text(raw: str | None) -> str:
    """Job-board HTML to readable text. Greenhouse double-encodes its HTML, so decode before stripping."""
    if not raw:
        return ""
    s = html.unescape(raw)
    s = re.sub(r"<(br|/p|/li|/h\d|/div)\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<li[^>]*>", "- ", s, flags=re.I)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


def provider_names(found_by: str | None) -> str:
    """'jsearch,serpapi' -> 'JSearch, SerpApi'."""
    return ", ".join(PROVIDER_LABELS.get(p, p) for p in (found_by or "").split(",") if p)


class ProviderSkipped(Exception):
    """Raised when a provider has no key configured."""


def clean(v):
    return None if v in (None, "") else v


def format_salary(lo, hi, cur, period) -> str | None:
    if not lo and not hi:
        return None
    rng = f"{lo}-{hi}" if lo and hi and lo != hi else f"{lo or hi}"
    return " ".join(p for p in [cur, rng, f"per {str(period).lower()}" if period else None] if p)


def _api_message(res: httpx.Response) -> str:
    """Pull the provider's own error text out of a failed response."""
    try:
        body = res.json()
    except ValueError:
        return res.text[:120].strip()
    err = body.get("error") if isinstance(body, dict) else None
    msg = (err.get("message") if isinstance(err, dict) else err) or (body.get("message") if isinstance(body, dict) else None)
    return str(msg or "")[:120]


def fail_reason(err: Exception) -> str:
    if isinstance(err, httpx.HTTPStatusError):
        code = err.response.status_code
        detail = _api_message(err.response)
        suffix = f': "{detail}"' if detail else ""
        if code == 429:
            return f"quota exhausted or rate limited (HTTP 429{suffix})"
        if code in (401, 403):
            return f"HTTP {code}{suffix}"
        return f"HTTP {code}{suffix}"
    if isinstance(err, httpx.TimeoutException):
        return "timed out"
    return str(err) or err.__class__.__name__


async def get_json(client: httpx.AsyncClient, url: str, *, params=None, headers=None) -> dict:
    res = await client.get(url, params=params, headers=headers, timeout=30)
    res.raise_for_status()
    try:
        return res.json()
    except ValueError:
        return {}


def date_posted_param(days: int | None) -> str:
    if days is None or days > 7:
        return "month"
    if days <= 1:
        return "today"
    if days <= 3:
        return "3days"
    return "week"
