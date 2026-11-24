"""Duplicate detection, ported from multi-agent-ai-job-hunter/scripts/search-jobs.mjs."""
import hashlib
import re
import time
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_COMPANY_NOISE = re.compile(
    r"\b(pvt|private|ltd|limited|inc|llc|llp|technologies|technology|india)\b"
)
FIELDS = ["location", "salary", "posted_at", "url", "source"]


def norm_company(s: str | None) -> str:
    s = _COMPANY_NOISE.sub("", (s or "").lower())
    return re.sub(r"[^a-z0-9]", "", s)


def title_tokens(s: str | None) -> set[str]:
    return {t for t in re.sub(r"[^a-z0-9+#]+", " ", (s or "").lower()).split(" ") if t}


def similar_title(a: str | None, b: str | None) -> bool:
    A, B = title_tokens(a), title_tokens(b)
    if not A or not B:
        return False
    inter = len(A & B)
    return inter / (len(A) + len(B) - inter) >= 0.7


def norm_city(location: str | None) -> str:
    first = (location or "").split(",")[0]
    return re.sub(r"[^a-z0-9]", "", first.lower())


def strip_utm(link: str | None) -> str | None:
    if not link:
        return None
    try:
        parts = urlsplit(link)
        query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
        return urlunsplit(parts._replace(query=urlencode(query)))
    except ValueError:
        return link


def fingerprint(job: dict) -> str:
    title = " ".join(sorted(title_tokens(job.get("title"))))
    raw = f"{norm_company(job.get('company'))}|{title}|{norm_city(job.get('location'))}"
    return hashlib.sha1(raw.encode()).hexdigest()


def _detail_score(j: dict) -> float:
    filled = sum(1 for k in FIELDS if j.get(k)) + (1 if j.get("remote") else 0)
    return filled + min(len(j.get("description") or "") / 1000, 3)


def dedupe_batch(jobs: list[dict]) -> tuple[list[dict], int]:
    """Collapse same company + similar title within one fetch; keep the richer record."""
    kept: list[dict] = []
    dupes = 0
    for job in jobs:
        job["providers"] = set(job.get("providers") or ()) | ({job["provider"]} if job.get("provider") else set())
        c = norm_company(job.get("company"))
        idx = next(
            (i for i, k in enumerate(kept)
             if c and norm_company(k.get("company")) == c and similar_title(k.get("title"), job.get("title"))),
            -1,
        )
        if idx == -1:
            kept.append(job)
            continue
        dupes += 1
        existing = kept[idx]
        winner, loser = (job, existing) if _detail_score(job) > _detail_score(existing) else (existing, job)
        for k in FIELDS:
            if not winner.get(k):
                winner[k] = loser.get(k)
        winner["remote"] = bool(winner.get("remote") or loser.get("remote"))
        winner["providers"] = winner["providers"] | loser["providers"]  # every API that found it
        if not winner.get("description"):
            winner["description"] = loser.get("description")
        kept[idx] = winner
    return kept, dupes


def too_old(job: dict, days: int | None) -> bool:
    posted = job.get("posted_at")
    if days is None or not posted:
        return False
    try:
        dt = datetime.fromisoformat(str(posted).replace("Z", "+00:00"))
        return time.time() - dt.timestamp() > days * 86400
    except ValueError:
        pass
    m = re.search(r"(\d+)\s*(hour|day|week|month)", str(posted), re.I)
    if not m:
        return False
    per = {"hour": 1 / 24, "day": 1, "week": 7, "month": 30}[m.group(2).lower()]
    return int(m.group(1)) * per > days
