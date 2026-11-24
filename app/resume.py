"""Read a resume and turn it into a profile: skills, experience and roles worth searching for."""
import io
import re

from .llm import ClaudeError, ask_json, make_client

MAX_BYTES = 5 * 1024 * 1024
MAX_CHARS = 20_000          # a resume is ~2-3k words; this is generous and keeps the request small
MIN_CHARS = 80
MAX_ROLES = 8


class ResumeError(ValueError):
    """The file could not be turned into usable text. The message is safe to show the user."""


def extract_text(filename: str, data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise ResumeError("That file is larger than 5 MB. Upload a smaller one or paste the text.")
    name = filename.lower()
    try:
        if name.endswith(".pdf"):
            text = _pdf_text(data)
        elif name.endswith(".docx"):
            text = _docx_text(data)
        elif name.endswith((".txt", ".md")):
            text = data.decode("utf-8", errors="replace")
        else:
            raise ResumeError("Unsupported file type. Use a PDF, a Word .docx or a .txt file.")
    except ResumeError:
        raise
    except Exception as err:
        raise ResumeError(f"Could not read that file ({err.__class__.__name__}). "
                          "Try another copy or paste the text instead.") from err
    return clean_text(text)


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise ResumeError("That PDF is password protected. Remove the password or paste the text.")
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _docx_text(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:                      # many resumes put skills in a table
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def check_text(text: str) -> str:
    """Validate resume text before spending a Claude call on it."""
    text = clean_text(text)
    if len(text) < MIN_CHARS:
        raise ResumeError("Not enough text found. If this is a scanned PDF (an image), "
                          "paste the text instead.")
    return text[:MAX_CHARS]


SYSTEM = (
    "You analyse a resume to help the candidate search for jobs. Read only what is in the resume; "
    "never invent skills or experience. Return:\n"
    "- name: the candidate's name, or empty\n"
    "- skills: technical and professional skills the resume actually shows (languages, frameworks, "
    "tools, platforms, methods). One short name each, no duplicates, most important first, at most 30.\n"
    "- experience_years: total professional experience in years as a number (count internships at half "
    "weight; 0 for a fresher). Estimate from dates when not stated.\n"
    "- summary: one sentence describing the candidate.\n"
    "- roles: job titles the candidate is realistically eligible for NOW given their skills and level, "
    "best fit first, at most 8. Use titles employers actually post (for example 'Web Developer', "
    "'Associate Systems Engineer', 'Full Stack Developer'). Include a mix of the obvious fit and "
    "adjacent roles they may not have considered. Match the seniority to their experience: do not "
    "suggest senior or lead roles to a fresher. For each role give a short reason naming the skills "
    "that make them eligible."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "summary": {"type": "string"},
        "experience_years": {"type": "number"},
        "skills": {"type": "array", "items": {"type": "string"}},
        "roles": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["title", "reason"],
            "additionalProperties": False,
        }},
    },
    "required": ["name", "summary", "experience_years", "skills", "roles"],
    "additionalProperties": False,
}


def normalise(raw: dict) -> dict:
    """Tidy Claude's answer: trim, drop blanks and duplicates (case-insensitive), clamp numbers."""
    seen: set[str] = set()
    skills = []
    for s in raw.get("skills") or []:
        s = str(s).strip()[:40]
        if s and s.lower() not in seen:
            seen.add(s.lower())
            skills.append(s)

    seen_roles: set[str] = set()
    roles = []
    for r in raw.get("roles") or []:
        title = str((r or {}).get("title", "")).strip()[:60]
        if title and title.lower() not in seen_roles:
            seen_roles.add(title.lower())
            roles.append({"title": title, "reason": str(r.get("reason", "")).strip()[:200]})

    try:
        years = float(raw.get("experience_years") or 0)
    except (TypeError, ValueError):
        years = 0.0
    return {
        "name": str(raw.get("name") or "").strip()[:80],
        "summary": str(raw.get("summary") or "").strip()[:300],
        "experience_years": max(0.0, min(60.0, round(years * 2) / 2)),
        "skills": skills[:30],
        "roles": roles[:MAX_ROLES],
    }


def analyse(text: str) -> dict:
    """Ask Claude for the profile. Raises ResumeError with a message safe to show."""
    client = make_client()
    if client is None:
        raise ResumeError("Resume analysis needs Claude. Set ANTHROPIC_API_KEY in .env and restart.")
    try:
        raw = ask_json(client, system=SYSTEM, user=f"Resume:\n\n{check_text(text)}",
                       schema=SCHEMA, max_tokens=3000, effort="medium")
    except ClaudeError as err:
        raise ResumeError(f"Claude could not analyse this resume: {err}") from err
    except ResumeError:
        raise
    except Exception as err:
        raise ResumeError(f"Could not reach Claude ({err.__class__.__name__}: {str(err)[:120]})") from err
    profile = normalise(raw)
    if not profile["skills"]:
        raise ResumeError("No skills were found in that text. Check that it is a resume.")
    return profile
