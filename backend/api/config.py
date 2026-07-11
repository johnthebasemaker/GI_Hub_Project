"""
backend/api/config.py — API configuration.

The API is Postgres-first (async). It reads DATABASE_URL and normalises it to the
asyncpg driver, so the same env var used by the migration/dual-CI tooling (which
uses the sync psycopg2 driver) also works here without editing.
"""
from __future__ import annotations

import os
from pathlib import Path


def _load_env_files() -> list[str]:
    """Bare-metal convenience: load repo-root `.env` then `deploy/.env` so a
    plain `uvicorn backend.api.main:app` sees the same WhatsApp/SMTP secrets
    docker-compose injects in production. Variables already present in the
    process environment ALWAYS win (override=False), so compose/systemd/CLI
    settings are never clobbered. Set GI_DOTENV=0 to skip entirely —
    service_tests do, so CI never depends on a developer's local secrets."""
    if os.environ.get("GI_DOTENV", "1").strip().lower() in ("0", "false", "no"):
        return []
    try:
        from dotenv import load_dotenv
    except ImportError:
        return []
    root = Path(__file__).resolve().parents[2]
    loaded: list[str] = []
    for p in (root / ".env", root / "deploy" / ".env"):
        if p.is_file():
            load_dotenv(p, override=False)
            loaded.append(str(p))
    return loaded


# Runs at import time, BEFORE any os.environ reads below (and before the other
# api modules read WHATSAPP_*/SMTP_*/JWT_SECRET lazily at request time).
LOADED_ENV_FILES = _load_env_files()

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


# CORS origins. In production behind a single-origin reverse proxy (nginx serves
# the SPA and proxies /api → the API), CORS isn't needed at all. If you ever split
# origins, set CORS_ORIGINS as a comma-separated env var; otherwise the dev
# defaults (the Vite/CRA dev servers) apply.
_env_cors = os.environ.get("CORS_ORIGINS", "").strip()
CORS_ORIGINS = (
    [o.strip() for o in _env_cors.split(",") if o.strip()] if _env_cors else [
        "http://localhost:5173", "http://127.0.0.1:5173",   # Vite default
        "http://localhost:3000", "http://127.0.0.1:3000",   # CRA / Next default
    ]
)


# --- environment + secrets ---------------------------------------------------
# The dev JWT signing key. Deliberately long (≥32 bytes) so PyJWT doesn't warn
# about HMAC key length in local dev — but it is refused in production.
_DEV_JWT_SECRET = "dev-insecure-change-me-not-for-production-use-0123456789"


def is_production() -> bool:
    """True when GI_ENV names a production environment."""
    return os.environ.get("GI_ENV", "dev").strip().lower() in ("prod", "production")


def jwt_secret() -> str:
    """Resolve the JWT signing key.

    In production (GI_ENV=production) a strong secret is MANDATORY: a missing,
    too-short (<32 chars), or the dev-default key raises at startup — the app
    refuses to boot with an insecure signing key. In dev it falls back to a
    long-but-obvious placeholder so local runs work without any setup.
    """
    s = os.environ.get("JWT_SECRET", "").strip()
    if is_production():
        if not s or s == _DEV_JWT_SECRET or len(s) < 32:
            raise RuntimeError(
                "JWT_SECRET must be set to a strong secret (≥32 chars) when "
                "GI_ENV=production — refusing to start with an insecure signing key.")
        return s
    return s or _DEV_JWT_SECRET
