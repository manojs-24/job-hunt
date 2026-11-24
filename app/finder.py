"""Find which companies hire in a place, and where each one's careers page is.

Free and public only: the Greenhouse, Lever, Ashby, SmartRecruiters and Workable job boards. A company's board is found three ways,
from most to least certain:
  1. a job link you already have (a link on jobs.lever.co/<name>/... names the board outright),
  2. the watchlist, where you or the starter list gave the name,
  3. a guess from the company name, accepted only if the board really belongs to that company.
"""
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus

import httpx

from . import llm
from .providers import careers

_URL_BOARDS = (
    ("greenhouse", re.compile(r"^https?://(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"^https?://jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"^https?://jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)", re.I)),
    ("smartrecruiters", re.compile(r"^https?://(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_.-]+)", re.I)),
    # a Workable job link is apply.workable.com/j/<id>, which does not name the company (see _NOT_A_COMPANY)
    ("workable", re.compile(r"^https?://apply\.workable\.com/([A-Za-z0-9_-]+)", re.I)),
)
_NOT_A_COMPANY = {"embed", "jobs", "job", "careers", "v1", "j", "api", "oneclick-ui"}
# Words that say what kind of legal entity it is rather than which company it is.
_FILLER = re.compile(r"\b(pvt|private|ltd|limited|inc|llc|llp|plc|corp|corporation|co|company|technologies|"
                     r"technology|tech|solutions|software|systems|services|labs|india|group|global)\b", re.I)
_DOMAIN = re.compile(r"\.(com|io|ai|co|in|ag|app|dev|net|org)\b", re.I)


def board_from_url(url: str | None) -> tuple[str, str] | None:
    """('lever', 'meesho') for a link like https://jobs.lever.co/meesho/abc, else None."""
    for board, pattern in _URL_BOARDS:
        m = pattern.match(url or "")
        if m and m.group(1).lower() not in _NOT_A_COMPANY:
            return board, m.group(1)
    return None


def clean_words(name: str) -> list[str]:
    """The words that identify a company: 'Applied Materials India Pvt Ltd' -> ['applied', 'materials']."""
    s = _FILLER.sub(" ", _DOMAIN.sub("", (name or "").lower()))
    return re.sub(r"[^a-z0-9]+", " ", s).split()


def slug_guesses(name: str) -> list[str]:
    """Likely board names. Only the whole name is guessed, never its first word: 'Blue Yonder' must not
    become 'blue', which is somebody else."""
    words = clean_words(name)
    if len("".join(words)) < 3:        # too short to tell apart from other companies, and `looks_like` would refuse it
        return []
    every = re.sub(r"[^a-z0-9]+", " ", _DOMAIN.sub("", (name or "").lower())).split()
    guesses = ["".join(words), "-".join(words), "".join(every)]
    return [g for g in dict.fromkeys(guesses) if len(g) >= 3][:3]


def _job_text(job: dict) -> str:
    parts = [job.get("title") or job.get("text") or ""]
    for field in ("content", "descriptionPlain", "description", "descriptionHtml", "name"):
        if job.get(field):
            parts.append(str(job[field])[:4000])
    return " ".join(parts)


def looks_like(name: str, board: str, jobs: list[dict]) -> bool:
    """Is this board really `name`'s? Greenhouse states the company name; for the others the name must
    appear in the job texts. A false 'no' just means the company is listed without a careers link."""
    words = clean_words(name)
    joined = "".join(words)
    if len(joined) < 3 or not jobs:
        return False
    if board in ("greenhouse", "smartrecruiters"):             # these two state the company's name on each job
        def stated_name(j: dict) -> str:
            raw = j.get("company_name") if board == "greenhouse" else (j.get("company") or {}).get("name")
            return re.sub(r"[^a-z0-9]", "", (raw or "").lower())
        stated = {stated_name(j) for j in jobs[:5]} - {""}
        if stated:
            return any(joined == s or (len(s) >= 4 and (joined in s or s in joined)) for s in stated)
    phrase = re.compile(r"\b" + r"\W*".join(re.escape(w) for w in words) + r"\b", re.I)
    sample = jobs[:40]
    need = min(len(sample), max(2, len(sample) // 5))
    return sum(1 for j in sample if phrase.search(_job_text(j))) >= need


async def find_board(client: httpx.AsyncClient, name: str, hint: tuple[str, str] | None = None,
                     extra_slugs: list[str] | None = None):
    """Find a company's board. Returns (result, transient_failure).

    `extra_slugs` are further names worth trying, such as the first part of the company's website address.
    A board is still accepted only if it really belongs to `name`.

    `result` is {"board", "slug", "jobs", "how"} or None. `transient_failure` is True when a timeout or
    rate limit stopped the search, so a 'not found' must not be remembered as a definite answer.
    """
    transient = False
    if hint:
        try:
            jobs = await careers.fetch_company(client, *hint)
            return {"board": hint[0], "slug": hint[1], "jobs": jobs, "how": "link in your saved jobs"}, False
        except careers.BoardNotFound:
            pass
        except careers.CompanyError:
            transient = True
    tries = list(dict.fromkeys([*slug_guesses(name), *(s.lower() for s in (extra_slugs or []))]))
    for slug in tries[:4]:
        for board in careers.GUESS_BOARDS:
            try:
                jobs = await careers.fetch_company(client, board, slug)
            except careers.BoardNotFound:
                continue
            except careers.CompanyError:
                transient = True
                continue
            if jobs and looks_like(name, board, jobs):
                return {"board": board, "slug": slug, "jobs": jobs, "how": "matched by name"}, False
    return None, transient


def careers_url(board: str, slug: str, sample_job_url: str | None = None) -> str:
    """The company's own page on its hiring tool, where every open job is listed."""
    if board == "greenhouse":
        m = re.match(r"^(https?://(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io)/", sample_job_url or "", re.I)
        return f"{m.group(1) if m else 'https://job-boards.greenhouse.io'}/{slug}"
    if board == "lever":
        return f"https://jobs.lever.co/{slug}"
    if board == "smartrecruiters":
        return f"https://jobs.smartrecruiters.com/{slug}"
    if board == "workable":
        return f"https://apply.workable.com/{slug}/"
    return f"https://jobs.ashbyhq.com/{slug}"


def search_link(name: str, location: str) -> str:
    """A web search for the company's careers page, for companies whose page we could not find."""
    return "https://www.google.com/search?q=" + quote_plus(f"{name} careers {location}".strip())


def parse_date(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ names from Claude
SUGGEST_SYSTEM = (
    "You help a job seeker list companies that hire in a given place. Return real companies you are "
    "confident exist and have an office or hiring presence there, using each company's everyday name. "
    "Mix startups, growth-stage companies and established employers. Prefer companies that publish their own "
    "careers page. Never invent a name: fewer, correct names are better than more. This list is only a "
    "starting point and every name will be checked against the company's real careers page."
)
SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {"companies": {"type": "array", "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "kind": {"type": "string", "enum": ["Startup", "Mid-size", "Large"]}},
        "required": ["name", "kind"], "additionalProperties": False}}},
    "required": ["companies"], "additionalProperties": False,
}


def suggest_companies(location: str, roles: list[str], limit: int = 25) -> tuple[list[dict], str | None]:
    """Company names Claude suggests for a place. Returns (names, notice). Never raises."""
    client = llm.make_client()
    if client is None:
        return [], "Claude suggestions skipped: ANTHROPIC_API_KEY is not set."
    where = location.strip() or "India"
    wanted = f" Focus on companies that hire for: {', '.join(roles)}." if roles else ""
    try:
        data = llm.ask_json(client, system=SUGGEST_SYSTEM, schema=SUGGEST_SCHEMA, max_tokens=2000,
                            user=f"Place: {where}.{wanted} List up to {limit} companies.")
    except Exception as err:   # a failed suggestion must not stop the rest of the search
        return [], f"Claude suggestions failed ({err.__class__.__name__}: {str(err)[:100]})"
    out, seen = [], set()
    for c in data.get("companies") or []:
        name = str((c or {}).get("name", "")).strip()[:60]
        if name and name.lower() not in seen:
            seen.add(name.lower())
            kind = c.get("kind") if c.get("kind") in ("Startup", "Mid-size", "Large") else "Unknown"
            out.append({"name": name, "kind": kind})
    return out[:limit], None
