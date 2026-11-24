import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=True)  # .env wins over keys already set in the shell

DB_PATH = ROOT / "jobs.db"
STATIC_DIR = ROOT / "static"


def env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def settings() -> dict:
    return {
        "jsearch_key": env("JSEARCH_RAPIDAPI_KEY"),
        "serpapi_key": env("SERPAPI_KEY"),
        "ninja_key": env("OPENWEB_NINJA_API_KEY"),
        "ninja_url": env("OPENWEB_NINJA_URL", "https://api.openwebninja.com/realtime-jobs-data/google-jobs/search"),
        "ninja_header": env("OPENWEB_NINJA_AUTH_HEADER", "X-API-Key"),
        "ninja_pages": max(1, int(env("OPENWEB_NINJA_PAGES", "1") or 1)),
        "anthropic_key": env("ANTHROPIC_API_KEY"),
        "anthropic_base_url": env("ANTHROPIC_BASE_URL"),
        "model": env("CLAUDE_MODEL", "claude-opus-5-5"),
        "country": env("COUNTRY", "in").lower(),
    }


def key_status() -> dict:
    s = settings()
    return {
        "jsearch": bool(s["jsearch_key"]),
        "serpapi": bool(s["serpapi_key"]),
        "openwebninja": bool(s["ninja_key"]),
        "claude": bool(s["anthropic_key"]),
    }
