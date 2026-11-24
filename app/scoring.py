"""Relevance scoring with Claude. Only newly inserted jobs are ever sent."""
import json

from .config import settings
from .llm import ask_json, make_client

BATCH_SIZE = 10
DESC_CHARS = 1500
MAX_SKILLS_SENT = 60

SYSTEM = (
    "You screen job postings for a candidate. For each job, judge how well it fits the "
    "candidate's target role and years of experience. Score 0-100 (100 = ideal fit; "
    "penalise postings that need much more experience than the candidate has, or are a "
    "different role). Extract the minimum years of experience the posting asks for "
    "(null if not stated). Give a reason of at most 15 words."
)
SYSTEM_SKILLS = (
    SYSTEM
    + " The candidate's skills are listed too; let how well they cover the posting's required "
    "skills strongly influence the score. Also return skills_matched (candidate skills the "
    "posting asks for or clearly uses, in the candidate's wording, at most 8) and skills_missing "
    "(important skills the posting requires that the candidate lacks, at most 5)."
)

_RESULT = {
    "id": {"type": "integer"},
    "score": {"type": "integer"},
    "exp_required": {"type": ["number", "null"]},
    "reason": {"type": "string"},
}
_SKILLS_FIELDS = {
    "skills_matched": {"type": "array", "items": {"type": "string"}},
    "skills_missing": {"type": "array", "items": {"type": "string"}},
}


def _schema(with_skills: bool) -> dict:
    props = {**_RESULT, **(_SKILLS_FIELDS if with_skills else {})}
    return {
        "type": "object",
        "properties": {"results": {"type": "array", "items": {
            "type": "object", "properties": props, "required": list(props),
            "additionalProperties": False,
        }}},
        "required": ["results"],
        "additionalProperties": False,
    }


def _prompt(role: str, experience: float, batch: list[dict], skills: list[str] | None) -> str:
    jobs = [
        {
            "id": j["id"],
            "title": j["title"],
            "company": j["company"],
            "location": j.get("location"),
            "description": (j.get("description") or "")[:DESC_CHARS],
        }
        for j in batch
    ]
    head = f"Candidate target role(s): {role}\nCandidate experience: {experience:g} years\n"
    if skills:
        head += f"Candidate skills: {', '.join(skills[:MAX_SKILLS_SENT])}\n"
    return f"{head}\nJobs:\n{json.dumps(jobs, ensure_ascii=False)}"


def _score_batch(client, role: str, experience: float, batch: list[dict], skills: list[str] | None) -> list[dict]:
    data = ask_json(
        client,
        system=SYSTEM_SKILLS if skills else SYSTEM,
        user=_prompt(role, experience, batch, skills),
        schema=_schema(bool(skills)),
    )
    return data["results"]


def _skill_text(value) -> str | None:
    """A list of skill names as one comma-separated string (commas inside a name are dropped)."""
    if not isinstance(value, list):
        return None
    names = [" ".join(str(v).replace(",", " ").split())[:40] for v in value]
    return ", ".join(n for n in names if n) or None


def score_jobs(role: str, experience: float, jobs: list[dict],
               skills: list[str] | None = None) -> tuple[dict[int, dict], str | None]:
    """Return ({job_id: {score, exp_required, reason, skills_matched, skills_missing}}, notice).

    `role` may name several roles. With `skills`, each job also gets the skills it matches and
    the ones the candidate is missing. Never raises: unscored jobs are still saved.
    """
    client = make_client()
    if client is None:
        return {}, "Scoring skipped: ANTHROPIC_API_KEY is not set."
    if not jobs:
        return {}, None
    scores: dict[int, dict] = {}
    failed = 0
    last_error = ""
    for i in range(0, len(jobs), BATCH_SIZE):
        batch = jobs[i:i + BATCH_SIZE]
        valid_ids = {j["id"] for j in batch}
        try:
            for r in _score_batch(client, role, experience, batch, skills):
                if r.get("id") in valid_ids:
                    scores[r["id"]] = {
                        "score": max(0, min(100, int(r["score"]))),
                        "exp_required": r.get("exp_required"),
                        "reason": (r.get("reason") or "")[:200],
                        "skills_matched": _skill_text(r.get("skills_matched")),
                        "skills_missing": _skill_text(r.get("skills_missing")),
                    }
        except Exception as err:  # keep going: unscored jobs are still saved
            failed += len(batch)
            last_error = f"{err.__class__.__name__}: {err}"[:200]
    notice = f"Scoring failed for {failed} job(s) ({last_error})." if failed else None
    return scores, notice
