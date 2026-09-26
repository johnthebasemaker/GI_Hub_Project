"""
backend/api/practice.py — what a Practice (sandbox) process does differently,
and the reset that only a Practice process can perform (rule 17).

Everything here is keyed on `config.is_practice()`, which a process learns from
its OWN environment at start-up. Nothing is keyed on a request.

WHAT IS DELIBERATELY DIFFERENT IN PRACTICE — each one a line of the plan:

  OCR           Refused (503) at every entry point that would queue a vision
                job. One Ollama box, one warm model, 90–400 s a page: a class of
                trainees photographing forms would queue ahead of Live's real
                ones (operator ruling Q5). The assistant and NL→SQL stay on; the
                paste lane and the deterministic handwritten post-processor
                stay on, because they never call the model.
  Compliance    `training_compliance` is not written (ruling Q2; training.py). A certificate
                earned in the sandbox would be evidence nobody can produce in
                Live — and writing it ACROSS into Live is precisely the
                cross-database path rule 17 forbids.
  2FA           Not mandated and not enrollable (ruling Q4; auth.py). The practice
                accounts are SHARED; one trainee binding an authenticator to
                `practice.hod` would lock the whole class out.
  Outbound      Impossible — config refuses to boot with credentials and
                `whatsapp/emailer.enabled()` say no (vector V7).

THE RESET (vector V11)
  `POST /practice/reset` exists ONLY in a Practice process — the router is not
  included in Live's app at all, so on Live it is a 404, not a hidden button.
  The target is this process's OWN database (from DATABASE_URL, never from the
  request) and must end `_training`; the seed is derived from it. The role that
  runs it owns only the Practice databases, so even a bug that computed Live's
  name would be refused by Postgres ("must be owner of database").
"""
from __future__ import annotations

import datetime as _dt
import os

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from .auth import require_roles
from .config import (PRACTICE_DB_SUFFIX, async_database_url, database_name,
                     is_practice)

# ── Practice-only behaviours ────────────────────────────────────────────────
OCR_OFF_MESSAGE = (
    "Photo reading (OCR) is switched off in Practice so trainees never queue "
    "ahead of Live's real forms on the shared AI engine. Use the paste lane or "
    "type the rows in — everything after the read works exactly as in Live.")


def assert_ocr_available() -> None:
    """503 in Practice. Called by every route that would queue a vision job."""
    if is_practice():
        raise HTTPException(503, OCR_OFF_MESSAGE)


# ── the reset ───────────────────────────────────────────────────────────────
SEED_INFIX = "_seed"
CONFIRM_PHRASE = "RESET PRACTICE DATA"


def seed_name(sandbox: str) -> str:
    """`gihub_training` → `gihub_seed_training`. Ends `_training` on purpose:
    the overlay runs AS a Practice process against the seed, and rule 17's boot
    check must accept it."""
    return sandbox[: -len(PRACTICE_DB_SUFFIX)] + SEED_INFIX + PRACTICE_DB_SUFFIX


def reset_targets(sandbox: str) -> tuple[str, str]:
    """(sandbox, seed), or ValueError. Pure — suite TR drives every branch."""
    if not sandbox or not sandbox.endswith(PRACTICE_DB_SUFFIX):
        raise ValueError(f"refusing to reset {sandbox!r}: only a database ending "
                         f"{PRACTICE_DB_SUFFIX!r} is a Practice sandbox")
    if sandbox.endswith(SEED_INFIX + PRACTICE_DB_SUFFIX):
        raise ValueError(f"refusing to reset {sandbox!r}: that is the SEED, the "
                         f"thing a reset copies FROM")
    if not sandbox.replace("_", "").isalnum():
        raise ValueError(f"refusing to reset {sandbox!r}: not a plain identifier")
    return sandbox, seed_name(sandbox)


def _maintenance_dsn(url: str) -> str:
    """Same host/port/credentials, database `postgres` — you cannot drop the
    database you are connected to."""
    from urllib.parse import urlsplit, urlunsplit
    raw = url.replace("postgresql+asyncpg://", "postgresql://", 1) \
             .replace("postgresql+psycopg2://", "postgresql://", 1)
    parts = urlsplit(raw)
    return urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))


async def clone_from_seed(dsn_admin: str, sandbox: str) -> None:
    """DROP the sandbox (terminating its sessions) and re-create it from the
    seed template. ~1 s. The caller must have released its OWN pooled
    connections first — `WITH (FORCE)` can only terminate sessions this role
    is allowed to signal."""
    import asyncpg
    sandbox, seed = reset_targets(sandbox)
    conn = await asyncpg.connect(dsn_admin)
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", seed)
        if not exists:
            raise RuntimeError(f"the seed database {seed!r} does not exist — run "
                               f"`tools/practice_db.py build` first")
        await conn.execute(f'DROP DATABASE IF EXISTS "{sandbox}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{sandbox}" TEMPLATE "{seed}"')
    finally:
        await conn.close()


async def stamp_reset(session, actor: str) -> None:
    """Record the reset INSIDE the fresh database, so the first audit row a
    trainee sees says who reset it and when."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from backend import models
    from .services.ledger import write_audit
    t = models.Base.metadata.tables["app_settings"]
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    for k, v in (("practice_reset_at", now), ("practice_reset_by", actor)):
        await session.execute(pg_insert(t).values(key=k, value=v)
                              .on_conflict_do_update(index_elements=["key"],
                                                     set_={"value": v}))
    await write_audit(session, actor, "PRACTICE_RESET", "app_settings",
                      "Practice sandbox restored from its seed")
    await session.commit()


router = APIRouter(prefix="/practice", tags=["practice"])


class ResetIn(BaseModel):
    confirm: str


@router.post("/reset", summary="Wipe the Practice sandbox back to its seed (Practice only)")
async def reset(body: ResetIn = Body(...),
                user: dict = Depends(require_roles("admin"))):
    """⚠️ NO `get_session` DEPENDENCY, and that is load-bearing: a request that
    held a pooled connection to the sandbox would be one of the sessions the
    DROP has to terminate."""
    if not is_practice():   # belt: the router is not even mounted on Live
        raise HTTPException(404, "Not Found")
    if (body.confirm or "").strip() != CONFIRM_PHRASE:
        raise HTTPException(422, f"type {CONFIRM_PHRASE!r} to confirm — this "
                                 f"wipes every trainee's work in progress")
    url = async_database_url()
    try:
        sandbox, _seed = reset_targets(database_name(url))
    except ValueError as e:
        raise HTTPException(409, str(e))

    from .ai import analytics as _an
    from .db import SessionLocal, engine
    # Release every connection this process holds to the sandbox — the app
    # pool AND the NL→SQL read-only pool, which connects as a different role
    # this one may not terminate.
    await engine.dispose()
    if _an._RO_ENGINE is not None:
        await _an._RO_ENGINE.dispose()
    try:
        await clone_from_seed(_maintenance_dsn(url), sandbox)
    except Exception as e:  # noqa: BLE001 — say exactly what Postgres said
        raise HTTPException(500, f"reset failed: {type(e).__name__}: {e}")

    async with SessionLocal() as s:
        await stamp_reset(s, user["username"])
    try:  # the username Bloom filter is a SNAPSHOT of the old register
        from .services import bloom as _bloom
        await _bloom.refresh_all()
    except Exception:  # noqa: BLE001 — an accelerator, never a correctness gate
        pass
    return {"reset": True, "database": sandbox,
            "reset_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "by": user["username"]}


def mounted() -> bool:
    """Whether main.py includes the router. A function so the decision is in
    one place and suite TR can assert it."""
    return is_practice() and os.environ.get("GI_PRACTICE_RESET", "on") != "off"
