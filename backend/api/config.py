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
            _warn_if_world_readable(p)
    return loaded


def _warn_if_world_readable(path: "Path") -> None:
    """Audit A04-F3: deploy/.env ships live Meta credentials and was mode 0644 —
    readable by every account on the host, including the production box where it
    sits alongside other services. `chmod 600` is an operator ritual nobody is
    reminded of, so say it out loud at load time. Never fatal: a wrong mode must
    not stop the app from serving.
    """
    try:
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            print(f"[config] WARNING: {path} is mode {mode:04o} — readable beyond "
                  f"its owner. It holds live credentials; run: chmod 600 {path}")
    except OSError:
        pass


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
def _is_production_env() -> bool:
    """Module-level twin of is_production() — CORS_ORIGINS is computed at import
    time, before the function below is defined."""
    return os.environ.get("GI_ENV", "dev").strip().lower() in ("prod", "production")


_env_cors = os.environ.get("CORS_ORIGINS", "").strip()
# Audit A03-F5: docker-compose passes CORS_ORIGINS as an EMPTY string when the
# operator hasn't set it, which .strip() makes falsy — so the dev list below
# applied in production, making http://localhost{,:3000,:5173} credentialed
# origins against the live API. Behind the single-origin nginx proxy no CORS is
# needed at all, so production defaults to nothing and must opt in explicitly
# (the native shells' fixed origins go in CORS_ORIGINS on the deploy box).
CORS_ORIGINS = (
    [o.strip() for o in _env_cors.split(",") if o.strip()] if _env_cors
    else [] if _is_production_env() else [
        "http://localhost:5173", "http://127.0.0.1:5173",   # Vite default
        "http://localhost:3000", "http://127.0.0.1:3000",   # CRA / Next default
        # Native app shells (built with VITE_API_URL → cross-origin calls).
        # These are fixed webview origins, not attacker-choosable ones:
        "tauri://localhost", "http://tauri.localhost",       # Tauri macOS/Linux · Windows
        "https://tauri.localhost",
        "capacitor://localhost", "https://localhost",        # Capacitor iOS · Android
        "http://localhost",                                  # Capacitor androidScheme=http builds
    ]
)


# --- environment + secrets ---------------------------------------------------
# The dev JWT signing key. Deliberately long (≥32 bytes) so PyJWT doesn't warn
# about HMAC key length in local dev — but it is refused in production.
_DEV_JWT_SECRET = "dev-insecure-change-me-not-for-production-use-0123456789"
_DEV_JWT_SECRET_PRACTICE = "dev-insecure-practice-sandbox-key-not-for-production-01"

# Audit A04-F4: the production guard rejected a missing, short, or dev-default
# secret — but the CI/test key is 43 chars and none of those, so it PASSED. That
# string appears in five docs and two workflows and is the one every developer
# copy-pastes, which makes it the value most likely to be pasted into a .env
# "just to get the server up". Any published constant is refused in production
# regardless of length; add new ones here rather than trusting the length check.
_PUBLISHED_SECRETS = frozenset({
    _DEV_JWT_SECRET,
    _DEV_JWT_SECRET_PRACTICE,
    "ci-only-service-test-secret-key-32bytes-min",   # docs §8 + CI workflows
    "CHANGE_ME",                                     # deploy/.env placeholder
    "CHANGE_ME_run_openssl_rand_hex_32",             # .env.example placeholder
    "jwt_secret",
    "changeme", "change-me", "secret", "password",
})


def is_production() -> bool:
    """True when GI_ENV names a production environment."""
    return os.environ.get("GI_ENV", "dev").strip().lower() in ("prod", "production")


def jwt_secret() -> str:
    """Resolve the JWT signing key.

    In production (GI_ENV=production) a strong secret is MANDATORY: a missing,
    too-short (<32 chars), publicly-published, or dev-default key raises at
    startup — the app refuses to boot with an insecure signing key. In dev it
    falls back to a long-but-obvious placeholder so local runs work without any
    setup.
    """
    s = os.environ.get("JWT_SECRET", "").strip()
    if is_production():
        if not s or len(s) < 32:
            raise RuntimeError(
                "JWT_SECRET must be set to a strong secret (≥32 chars) when "
                "GI_ENV=production — refusing to start with an insecure signing key.")
        if s in _PUBLISHED_SECRETS:
            raise RuntimeError(
                "JWT_SECRET is a publicly published placeholder/test value — "
                "refusing to start. Generate a real one: openssl rand -hex 32")
        return s
    # Dev: the two environments get DIFFERENT fallback keys, so a Practice token
    # cannot verify on Live even on a laptop with no JWT_SECRET set at all. The
    # `env` claim (auth._decode) is the second wall behind this one.
    if not s and normalize_instance(os.environ.get("GI_INSTANCE")) == "training":
        return _DEV_JWT_SECRET_PRACTICE
    return s or _DEV_JWT_SECRET


# --- instance identity: Live vs Practice (rule 17) ----------------------------
# ⚠️ PRACTICE IS A SECOND PROCESS, NOT A SECOND SESSION. Nothing in the API
# selects a database per request. A process knows which environment it is from
# its OWN environment variables, holds exactly one DATABASE_URL, and refuses to
# boot when its name and its database disagree — the mechanism rule 15 already
# uses for the test database, applied to a long-running process. See
# PROPOSED_SANDBOX_PLAN.md §3 for why a header- or token-selected database was
# rejected (30 session sites have no request to read either from).
#
# Internal name `training` (GI_INSTANCE, database suffix); user-facing label
# "Practice", because "Training" is already the video Training Hub.
INSTANCE_LIVE = "production"
INSTANCE_PRACTICE = "training"
PRACTICE_DB_SUFFIX = "_training"
_INSTANCE_ALIASES = {
    "": INSTANCE_LIVE, "production": INSTANCE_LIVE, "live": INSTANCE_LIVE,
    "prod": INSTANCE_LIVE,
    "training": INSTANCE_PRACTICE, "practice": INSTANCE_PRACTICE,
}
INSTANCE_LABELS = {INSTANCE_LIVE: "Live", INSTANCE_PRACTICE: "Practice"}

# A Practice process must not be ABLE to reach the outside world. These are
# refused at boot, not merely ignored: a value present in a Practice process
# means somebody copied the Live env file, and the next thing it would do is
# text a real phone number a trainee typed (P10-8, P11-9).
PRACTICE_FORBIDDEN_ENV = (
    "WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID",
    "SMTP_HOST", "SMTP_SERVER", "SMTP_USER", "SMTP_PASS",
    "GI_AI_VISION_API_KEY",
)


def normalize_instance(raw: str | None) -> str | None:
    """Canonical instance name, or None for a value nobody should guess at."""
    return _INSTANCE_ALIASES.get((raw or "").strip().lower())


def instance() -> str:
    """This process's environment: 'production' (Live) or 'training' (Practice).

    An unrecognised GI_INSTANCE is NOT read as Live — `assert_instance_safe`
    refuses it at boot, so by the time anything calls this the value is known.
    """
    return normalize_instance(os.environ.get("GI_INSTANCE")) or INSTANCE_LIVE


def is_practice() -> bool:
    return instance() == INSTANCE_PRACTICE


def database_name(url: str | None = None) -> str:
    """The DATABASE a URL names (never the cluster) — '' when it names none."""
    from urllib.parse import urlsplit
    u = url if url is not None else async_database_url()
    try:
        return (urlsplit(u).path or "").lstrip("/").split("?")[0]
    except ValueError:
        return ""


def instance_problems(env=None) -> list[str]:
    """Every reason this process must not start, as a pure function of its env.

    Pure so suite TR can exercise each branch without spawning a server; the
    wiring into `db.py` is proved separately with one real subprocess.
    """
    env = os.environ if env is None else env
    raw = env.get("GI_INSTANCE", "")
    inst = normalize_instance(raw)
    if inst is None:
        return [f"GI_INSTANCE={raw!r} is not a known environment "
                f"(use 'production' or 'training')"]
    url = (env.get("DATABASE_URL") or "").strip() or DEFAULT_DATABASE_URL
    db = database_name(url)
    problems: list[str] = []
    if inst == INSTANCE_PRACTICE:
        if not db.endswith(PRACTICE_DB_SUFFIX):
            problems.append(
                f"GI_INSTANCE=training but DATABASE_URL names {db!r} — a Practice "
                f"process may only open a database ending {PRACTICE_DB_SUFFIX!r}")
        present = [k for k in PRACTICE_FORBIDDEN_ENV if (env.get(k) or "").strip()]
        if present:
            problems.append(
                "GI_INSTANCE=training but outbound credentials are set "
                f"({', '.join(present)}) — Practice must not be able to send")
    elif PRACTICE_DB_SUFFIX in db:
        # `in`, not `endswith`: Live must not open the Practice TEMPLATE
        # (`gihub_training_tpl`) either, or TR's throwaway copies.
        problems.append(
            f"DATABASE_URL names the Practice database {db!r} but GI_INSTANCE is "
            f"Live — set GI_INSTANCE=training, or point Live at its own database")
    return problems


def assert_instance_safe() -> None:
    """Refuse to start. Called by db.py BEFORE the engine exists, so a
    misconfigured process never holds a connection to anything."""
    problems = instance_problems()
    if problems:
        raise RuntimeError("refusing to start — environment/database mismatch:\n  "
                           + "\n  ".join(problems))


def outbound_enabled() -> bool:
    """False in Practice, or when GI_OUTBOUND=off. Checked by
    whatsapp.enabled() and emailer.enabled() — the second wall behind the boot
    refusal, for somebody editing a running box's environment."""
    if is_practice():
        return False
    return os.environ.get("GI_OUTBOUND", "on").strip().lower() not in (
        "off", "0", "false", "no")


def refresh_cookie_name(inst: str | None = None) -> str:
    """Two backends on one origin must not overwrite each other's refresh
    cookie (path '/'). Not a leak — the secrets differ — but it signs people
    out of the other environment every time they switch."""
    return "gi_refresh_training" if (inst or instance()) == INSTANCE_PRACTICE \
        else "gi_refresh"


def public_base_url() -> str:
    """Base URL for outbound links (weekly-report capability URLs, etc).

    Audit A04-F7: this silently fell back to http://localhost:8000, so an unset
    variable in production produced WhatsApp links that resolve to the
    RECIPIENT'S own device — the link fails quietly and a 256-bit capability
    token has been broadcast for nothing. Fail fast in production instead,
    mirroring the JWT_SECRET pattern.
    """
    v = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if is_production():
        if not v:
            raise RuntimeError(
                "PUBLIC_BASE_URL must be set when GI_ENV=production — outbound "
                "report links would otherwise point at localhost.")
        if "localhost" in v or "127.0.0.1" in v:
            raise RuntimeError(
                f"PUBLIC_BASE_URL={v!r} points at localhost — outbound links "
                "must use the public hostname in production.")
    return v or "http://localhost:8000"
