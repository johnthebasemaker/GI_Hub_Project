"""
backend/api/config.py — API configuration.

The API is Postgres-first (async). It reads DATABASE_URL and normalises it to the
asyncpg driver, so the same env var used by the migration/dual-CI tooling (which
uses the sync psycopg2 driver) also works here without editing.
"""
from __future__ import annotations

import os

# Local default: the throwaway Postgres 16 cluster on port 5433 (trust auth, no
# password), database `gihub` — the one the migration/dual-CI already populate.
DEFAULT_DATABASE_URL = "postgresql+asyncpg://postgres@127.0.0.1:5433/gihub"


def async_database_url() -> str:
    """Return an asyncpg SQLAlchemy URL, normalising common Postgres URL forms.

    Accepts the sync forms that the rest of the tooling uses (psycopg2 / bare
    postgres://) and rewrites them onto the async driver. A URL that already
    names an async driver is passed through untouched.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return DEFAULT_DATABASE_URL
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql+asyncpg://" + url[len("postgresql+psycopg2://"):]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://"):]
    # Anything else (e.g. an explicit async URL for another dialect) is honoured
    # as-is; the API is designed and verified against Postgres.
    return url


# CORS origins for the future React dev server(s). Adjust when the frontend lands.
CORS_ORIGINS = [
    "http://localhost:5173", "http://127.0.0.1:5173",   # Vite default
    "http://localhost:3000", "http://127.0.0.1:3000",   # CRA / Next default
]
