"""Free, instant checks on a job's title. They decide which fetched jobs are worth storing and sending
to Claude, so a run over a few hundred careers-page jobs scores only the promising ones."""
import re

# Words that carry little meaning in a role title.
IGNORE = {"and", "the", "for", "with", "of", "in", "to", "a", "an", "senior", "junior", "jr", "sr", "associate",
          "lead", "staff", "principal", "intern", "trainee", "i", "ii", "iii", "iv", "remote", "all", "levels"}
# True of many titles ("Sales Engineer"), so on their own they are weak evidence of a match.
WEAK = {"engineer", "developer", "programmer", "engineering", "development", "software"}
SENIOR = re.compile(r"\b(staff|principal|director|vp|vice president|head of|chief|distinguished|lead|manager|"
                    r"architect|senior|sr)\b", re.I)
JUNIOR = re.compile(r"\b(junior|jr|associate|intern|trainee|graduate|fresher|entry[- ]level)\b", re.I)
_WORD = re.compile(r"[a-z0-9+#.]+")


def words(text: str | None) -> set[str]:
    """Lower-case words, keeping c++, c#, node.js and the like whole."""
    return {w.strip(".") for w in _WORD.findall((text or "").lower()) if w.strip(".")}


def _norm(text: str | None) -> str:
    return " ".join(w.strip(".") for w in _WORD.findall((text or "").lower()))


class Matcher:
    """Built once per search from the chosen roles and the candidate's skills."""

    def __init__(self, roles: list[str], skills: list[str] | None = None):
        role_words = set().union(*(words(r) for r in roles)) if roles else set()
        role_words -= IGNORE
        self.strong = role_words - WEAK
        self.weak = role_words & WEAK
        self.skill_words = {s.strip().lower() for s in (skills or []) if " " not in s.strip() and len(s.strip()) > 1}
        self.roles_norm = [_norm(r) for r in roles if _norm(r)]
        self.phrases = [r for r in self.roles_norm if " " in r]

    def score(self, title: str | None) -> int:
        """0 means the title does not match any role or skill. Higher is a closer match."""
        tw = words(title)
        t = _norm(title)
        strong_hits = len(tw & self.strong) + len(tw & self.skill_words - self.strong)
        phrase = any(p in t for p in self.phrases)
        weak_hits = len(tw & self.weak)
        if strong_hits == 0 and not phrase and not (not self.strong and weak_hits):
            return 0
        return 3 * strong_hits + weak_hits + (5 if phrase else 0) + (3 if t in self.roles_norm else 0)


def rank(title: str | None, match: int, experience: float) -> int:
    """Order matches best-first. Early-career candidates are steered toward junior and mid titles."""
    t = title or ""
    if experience < 5 and SENIOR.search(t):
        return match - 4
    if experience < 3 and JUNIOR.search(t):
        return match + 1
    return match
