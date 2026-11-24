"""Can someone living in India apply for this job? Used by the careers pages and the remote boards."""
import re

INDIA = re.compile(
    r"\b(india|bengaluru|bangalore|hyderabad|mumbai|pune|delhi|new delhi|gurugram|gurgaon|chennai|noida|"
    r"kolkata|ahmedabad|kochi|jaipur|indore|chandigarh|thiruvananthapuram|coimbatore|mysuru|mysore)\b", re.I)
# Words that mean "any country is fine" (APAC and Asia include India).
OPEN = re.compile(r"\b(worldwide|anywhere|global|globally|apac|asia|international|any location|all locations)\b", re.I)
REMOTE = re.compile(r"\b(remote|work from home|wfh|distributed)\b", re.I)
# A location that is only the word "remote", with no place named.
JUST_REMOTE = re.compile(r"^\W*(fully\s+|100%\s*)?(remote|work from home|wfh|distributed)(\s+(first|only|friendly))?\W*$", re.I)


def open_to_india(location: str | None) -> bool:
    """True for an Indian location, a worldwide one, a plain "Remote", or no location at all.

    "Remote, United States" or "EMEA" is remote but tied to another region, so it is False: those
    jobs cannot be done from India and would only crowd out useful results.
    """
    loc = (location or "").strip()
    if not loc:
        return True
    # A job can list several places separated by ";". It is open if any one of them is.
    return any(INDIA.search(part) or OPEN.search(part) or JUST_REMOTE.match(part)
               for part in loc.split(";"))


# Different spellings of the same place. Typing any one of them matches all of them.
CITY_GROUPS = [
    {"bengaluru", "bangalore", "bengalooru"},
    {"gurugram", "gurgaon"},
    {"mumbai", "bombay", "navi mumbai"},
    {"chennai", "madras"},
    {"kolkata", "calcutta"},
    {"kochi", "cochin", "ernakulam"},
    {"mysuru", "mysore"},
    {"thiruvananthapuram", "trivandrum"},
    {"hyderabad", "secunderabad"},
    {"delhi", "new delhi"},
]
# Typing one of these means the whole National Capital Region.
NCR_KEYS = {"ncr", "delhi ncr", "delhi-ncr"}
NCR_PLACES = {"delhi", "new delhi", "noida", "greater noida", "gurugram", "gurgaon", "ghaziabad", "faridabad"}
INDIA_WORDS = {"india", "anywhere in india", "all india", "pan india", "any"}
REMOTE_WORDS = {"remote", "remote only", "work from home", "wfh"}


def _wanted_parts(wanted: str | None) -> list[str]:
    """'Bengaluru, Pune or Remote' -> ['bengaluru', 'pune', 'remote']."""
    parts = re.split(r"[;,|]|\bor\b", (wanted or "").lower())
    return [" ".join(p.split()) for p in parts if p.strip()]


def location_filter(wanted: str | None, include_remote: bool = True):
    """Build `ok(job_location) -> bool` for what the user typed in the Location box.

    - blank or "India": a job anywhere in India
    - a city, state or country ("Bengaluru", "Karnataka"): jobs listed there. Spelling variants count
    - several places separated by commas: any one of them
    - "Remote": only jobs that can be done from anywhere
    `include_remote` also keeps fully remote jobs (and remote jobs in India) next to the place you chose.
    """
    parts = _wanted_parts(wanted)
    remote_only = bool(parts) and all(p in REMOTE_WORDS for p in parts)
    anywhere_in_india = not parts or any(p in INDIA_WORDS for p in parts)

    patterns = []
    if not (anywhere_in_india or remote_only):
        for p in parts:
            if p in REMOTE_WORDS:
                continue
            names = NCR_PLACES if p in NCR_KEYS else next((g for g in CITY_GROUPS if p in g), {p})
            patterns.append(re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
                                       + r")\b", re.I))
    wanted_is_indian = anywhere_in_india or bool(INDIA.search(" ".join(parts)))

    def in_place(loc: str) -> bool:
        if remote_only:
            return False
        return bool(INDIA.search(loc)) if anywhere_in_india else any(p.search(loc) for p in patterns)

    def remote_anywhere(loc: str) -> bool:
        if not loc:
            return True                                    # no location listed: could be anywhere
        return any(OPEN.search(part) or JUST_REMOTE.match(part) for part in loc.split(";"))

    def ok(location: str | None) -> bool:
        loc = (location or "").strip()
        if in_place(loc):
            return True
        if not (include_remote or remote_only):
            return False
        if remote_anywhere(loc):
            return True
        # "Remote - India" suits anyone who is looking in India
        return wanted_is_indian and bool(REMOTE.search(loc)) and bool(INDIA.search(loc))

    return ok


def is_remote(location: str | None, flag: bool | None = None) -> bool:
    loc = location or ""
    return bool(flag) or bool(REMOTE.search(loc)) or bool(OPEN.search(loc))
