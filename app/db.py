import sqlite3
from datetime import datetime, timezone

from .config import DB_PATH
from .dedupe import fingerprint, norm_company, similar_title

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    company_key TEXT NOT NULL,
    location TEXT,
    remote INTEGER NOT NULL DEFAULT 0,
    salary TEXT,
    posted_at TEXT,
    url TEXT,
    source TEXT,
    provider TEXT,
    found_by TEXT,
    description TEXT,
    exp_required REAL,
    score INTEGER,
    reason TEXT,
    skills_matched TEXT,
    skills_missing TEXT,
    company_kind TEXT,
    saved INTEGER NOT NULL DEFAULT 0,
    saved_at TEXT,
    applied INTEGER NOT NULL DEFAULT 0,
    applied_at TEXT,
    query_role TEXT,
    query_location TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_company_key ON jobs(company_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_url ON jobs(url) WHERE url IS NOT NULL;
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    summary TEXT,
    experience REAL NOT NULL,
    skills TEXT NOT NULL,
    roles TEXT NOT NULL,
    location TEXT,
    created_at TEXT NOT NULL,
    last_run_at TEXT
);
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    board TEXT NOT NULL,
    slug TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'Startup',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_checked TEXT,
    last_total INTEGER,
    last_error TEXT,
    UNIQUE (board, slug)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS board_lookups (
    company_key TEXT PRIMARY KEY,
    board TEXT,
    slug TEXT,
    how TEXT,
    checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT, experience REAL, location TEXT,
    ran_at TEXT NOT NULL, fetched INTEGER, new_count INTEGER
);
"""

# Columns added after the first release; older jobs.db files get them on connect.
ADDED_COLUMNS = {
    "saved": "INTEGER NOT NULL DEFAULT 0",
    "saved_at": "TEXT",
    "applied": "INTEGER NOT NULL DEFAULT 0",
    "applied_at": "TEXT",
    "found_by": "TEXT",
    "skills_matched": "TEXT",
    "skills_missing": "TEXT",
    "company_kind": "TEXT",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    for name, ddl in ADDED_COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {ddl}")
    # jobs saved before found_by existed were each found by the one API recorded in `provider`
    conn.execute("UPDATE jobs SET found_by = provider WHERE found_by IS NULL AND provider IS NOT NULL")
    conn.commit()


def connect(path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    _seed_companies(conn)
    return conn


def _providers_of(job: dict) -> set[str]:
    found = set(job.get("providers") or ())
    if job.get("provider"):
        found.add(job["provider"])
    return found


def _merge_found_by(existing: str | None, new: set[str]) -> str:
    return ",".join(sorted({p for p in (existing or "").split(",") if p} | new))


def insert_new(conn: sqlite3.Connection, jobs: list[dict], role: str, location: str,
               limit_new: int | None = None) -> list[dict]:
    """Insert jobs not seen before; refresh last_seen on ones we already have.

    A job seen again by a different API gets that API added to `found_by`.
    Returns only the jobs that were newly inserted (each gains an `id`). With `limit_new`, once that
    many jobs have been inserted the remaining unseen jobs are left alone (no `id`), so a later run
    can pick them up instead of them being stored without a score.
    """
    ts = now()
    new: list[dict] = []
    for job in jobs:
        fp = fingerprint(job)
        company_key = norm_company(job["company"])
        providers = _providers_of(job)
        existing = conn.execute(
            "SELECT id, found_by FROM jobs WHERE fingerprint = ? OR (url IS NOT NULL AND url = ?)",
            (fp, job.get("url")),
        ).fetchone()
        if existing is None:
            # catch near-identical titles at the same company that fingerprint differently
            for row in conn.execute("SELECT id, title, found_by FROM jobs WHERE company_key = ?", (company_key,)):
                if similar_title(row["title"], job["title"]):
                    existing = row
                    break
        if existing is None and limit_new is not None and len(new) >= limit_new:
            continue
        if existing is not None:
            conn.execute(
                "UPDATE jobs SET last_seen = ?, found_by = ? WHERE id = ?",
                (ts, _merge_found_by(existing["found_by"], providers), existing["id"]),
            )
            job["id"] = existing["id"]
            continue
        cur = conn.execute(
            """INSERT INTO jobs (fingerprint, title, company, company_key, location, remote, salary,
                posted_at, url, source, provider, found_by, description, query_role, query_location,
                company_kind, first_seen, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fp, job["title"], job["company"], company_key, job.get("location"),
             int(bool(job.get("remote"))), job.get("salary"), job.get("posted_at"), job.get("url"),
             job.get("source"), job.get("provider"), _merge_found_by(None, providers),
             job.get("description"), role, location, job.get("company_kind"), ts, ts),
        )
        job["id"] = cur.lastrowid
        new.append(job)
    conn.commit()
    return new


def save_scores(conn: sqlite3.Connection, scores: dict[int, dict]) -> None:
    for job_id, s in scores.items():
        conn.execute(
            "UPDATE jobs SET score = ?, exp_required = ?, reason = ?, skills_matched = ?, skills_missing = ? "
            "WHERE id = ?",
            (s.get("score"), s.get("exp_required"), s.get("reason"),
             s.get("skills_matched"), s.get("skills_missing"), job_id),
        )
    conn.commit()


def record_search(conn, role, experience, location, fetched, new_count) -> None:
    conn.execute(
        "INSERT INTO searches (role, experience, location, ran_at, fetched, new_count) VALUES (?,?,?,?,?,?)",
        (role, experience, location, now(), fetched, new_count),
    )
    conn.commit()


SORTS = {
    "score": "score IS NULL, score DESC, first_seen DESC",
    "newest": "first_seen DESC, id DESC",
}


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def query_jobs(conn, *, role=None, location=None, min_score=None, only_ids=None,
               saved=None, applied=None, found_by=None, q=None, source=None, since=None,
               kind=None, sort="score") -> list[dict]:
    sql = ("SELECT id, title, company, location, remote, salary, posted_at, url, source, provider, found_by, "
           "exp_required, score, reason, skills_matched, skills_missing, company_kind, saved, applied, first_seen "
           "FROM jobs WHERE 1=1")
    args: list = []
    if role:
        sql += " AND query_role = ? COLLATE NOCASE"
        args.append(role)
    if location:
        sql += " AND query_location = ? COLLATE NOCASE"
        args.append(location)
    if min_score:
        sql += " AND score >= ?"
        args.append(min_score)
    if saved is not None:
        sql += " AND saved = ?"
        args.append(int(saved))
    if applied is not None:
        sql += " AND applied = ?"
        args.append(int(applied))
    if found_by:
        sql += " AND (',' || COALESCE(found_by, '') || ',') LIKE ?"
        args.append(f"%,{found_by},%")
    if q:
        sql += (" AND (title LIKE ? ESCAPE '\\' OR company LIKE ? ESCAPE '\\'"
                " OR location LIKE ? ESCAPE '\\')")
        args.extend([_like(q)] * 3)
    if source:
        sql += " AND source = ? COLLATE NOCASE"
        args.append(source)
    if kind:
        sql += " AND company_kind = ? COLLATE NOCASE"
        args.append(kind)
    if since:
        sql += " AND first_seen >= ?"      # first_seen is an ISO timestamp, so text comparison orders correctly
        args.append(since)
    if only_ids is not None:
        sql += f" AND id IN ({','.join('?' * len(only_ids)) or 'NULL'})"
        args.extend(only_ids)
    sql += " ORDER BY " + SORTS.get(sort, SORTS["score"])
    return [dict(r) for r in conn.execute(sql, args)]


def _set_flag(conn: sqlite3.Connection, job_id: int, flag: str, value: bool) -> bool:
    cur = conn.execute(
        f"UPDATE jobs SET {flag} = ?, {flag}_at = ? WHERE id = ?",   # flag is one of two fixed names below
        (int(value), now() if value else None, job_id),
    )
    conn.commit()
    return cur.rowcount > 0


def set_saved(conn: sqlite3.Connection, job_id: int, saved: bool) -> bool:
    """Bookmark or un-bookmark a job. Returns False if no such job."""
    return _set_flag(conn, job_id, "saved", saved)


def set_applied(conn: sqlite3.Connection, job_id: int, applied: bool) -> bool:
    """Mark a job as applied-to (or not). Returns False if no such job."""
    return _set_flag(conn, job_id, "applied", applied)


def distinct_sources(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT source FROM jobs WHERE source IS NOT NULL AND source != '' ORDER BY source COLLATE NOCASE"
    )
    return [r["source"] for r in rows]


def distinct_roles(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT query_role FROM jobs WHERE query_role IS NOT NULL AND query_role != '' "
        "ORDER BY query_role COLLATE NOCASE"
    )
    return [r["query_role"] for r in rows]


def recent_searches(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        "SELECT ran_at, role, experience, location, fetched, new_count FROM searches ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        "SELECT COUNT(*) AS total, COALESCE(SUM(saved), 0) AS saved, COALESCE(SUM(applied), 0) AS applied FROM jobs"
    ).fetchone()
    return {"total": row["total"], "saved": row["saved"], "applied": row["applied"]}


# --- saved resume profiles -------------------------------------------------------------------
# skills and roles are stored as JSON text so a profile is one row.
import json  # noqa: E402


def save_profile(conn: sqlite3.Connection, *, name: str, summary: str, experience: float,
                 skills: list[str], roles: list[str], location: str) -> int:
    cur = conn.execute(
        "INSERT INTO profiles (name, summary, experience, skills, roles, location, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (name, summary, experience, json.dumps(skills), json.dumps(roles), location, now()),
    )
    conn.commit()
    return cur.lastrowid


def _profile(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["skills"] = json.loads(d["skills"])
    d["roles"] = json.loads(d["roles"])
    return d


def list_profiles(conn: sqlite3.Connection) -> list[dict]:
    return [_profile(r) for r in conn.execute("SELECT * FROM profiles ORDER BY id DESC")]


def get_profile(conn: sqlite3.Connection, profile_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
    return _profile(row) if row else None


def touch_profile(conn: sqlite3.Connection, profile_id: int) -> None:
    conn.execute("UPDATE profiles SET last_run_at = ? WHERE id = ?", (now(), profile_id))
    conn.commit()


def delete_profile(conn: sqlite3.Connection, profile_id: int) -> bool:
    cur = conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
    conn.commit()
    return cur.rowcount > 0


# --- company watchlist (careers pages) and small key/value settings ------------------------------
def meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))
    conn.commit()


def _seed_companies(conn: sqlite3.Connection) -> None:
    """Fill the watchlist once. Companies the user later removes stay removed."""
    if meta_get(conn, "companies_seeded"):
        return
    from .seed_companies import SEED
    conn.executemany("INSERT OR IGNORE INTO companies (name, board, slug, kind) VALUES (?,?,?,?)", SEED)
    conn.commit()
    meta_set(conn, "companies_seeded", now())


def list_companies(conn: sqlite3.Connection, enabled: bool | None = None) -> list[dict]:
    sql = "SELECT * FROM companies"
    args: list = []
    if enabled is not None:
        sql += " WHERE enabled = ?"
        args.append(int(enabled))
    return [dict(r) for r in conn.execute(sql + " ORDER BY name COLLATE NOCASE", args)]


def add_company(conn: sqlite3.Connection, name: str, board: str, slug: str, kind: str = "Startup") -> int | None:
    """Add a company; returns its id, or None when that board/slug is already on the list."""
    try:
        cur = conn.execute("INSERT INTO companies (name, board, slug, kind) VALUES (?,?,?,?)",
                           (name.strip(), board, slug.strip().lower(), kind))
    except sqlite3.IntegrityError:
        return None
    conn.commit()
    return cur.lastrowid


def update_company(conn: sqlite3.Connection, company_id: int, *, enabled: bool | None = None,
                   kind: str | None = None) -> None:
    if enabled is not None:
        conn.execute("UPDATE companies SET enabled = ? WHERE id = ?", (int(enabled), company_id))
    if kind is not None:
        conn.execute("UPDATE companies SET kind = ? WHERE id = ?", (kind, company_id))
    conn.commit()


def record_company_check(conn: sqlite3.Connection, company_id: int, total: int | None, error: str | None) -> None:
    conn.execute("UPDATE companies SET last_checked = ?, last_total = ?, last_error = ? WHERE id = ?",
                 (now(), total, error, company_id))
    conn.commit()


def delete_companies(conn: sqlite3.Connection, ids: list[int]) -> int:
    if not ids:
        return 0
    cur = conn.execute(f"DELETE FROM companies WHERE id IN ({','.join('?' * len(ids))})", ids)
    conn.commit()
    return cur.rowcount


# --- finding companies: employers seen in a place, and which careers page each one uses ------------
def employers_in_location(conn: sqlite3.Connection, is_here, limit: int = 200) -> dict[str, dict]:
    """Employers with at least one saved job whose location passes `is_here(location)`.

    Returns {company_key: {"name", "jobs": [{title, url, location, seen}]}}, busiest employers first.
    """
    out: dict[str, dict] = {}
    rows = conn.execute("SELECT company, company_key, title, location, url, first_seen FROM jobs "
                        "ORDER BY first_seen DESC")
    for r in rows:
        if not r["company_key"] or not is_here(r["location"]):
            continue
        entry = out.setdefault(r["company_key"], {"name": r["company"], "jobs": []})
        entry["jobs"].append({"title": r["title"], "url": r["url"], "location": r["location"],
                              "seen": r["first_seen"]})
    busiest = sorted(out.items(), key=lambda kv: -len(kv[1]["jobs"]))[:limit]
    return dict(busiest)


def get_board_lookup(conn: sqlite3.Connection, company_key: str) -> dict | None:
    row = conn.execute("SELECT * FROM board_lookups WHERE company_key = ?", (company_key,)).fetchone()
    return dict(row) if row else None


def set_board_lookup(conn: sqlite3.Connection, company_key: str, board: str | None, slug: str | None,
                     how: str | None) -> None:
    """Remember which careers page a company uses, or (board=None) that none could be found."""
    conn.execute(
        "INSERT INTO board_lookups (company_key, board, slug, how, checked_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(company_key) DO UPDATE SET board = excluded.board, slug = excluded.slug, "
        "how = excluded.how, checked_at = excluded.checked_at",
        (company_key, board, slug, how, now()))
    conn.commit()
