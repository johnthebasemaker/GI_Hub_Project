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


# ── Phase 23f (ruling Q23-13) — who can sign in to Practice ──────────────────
# The shared accounts and their password. ⚠️ Mirrors tools/practice_overlay
# (ACCOUNTS, DEFAULT_PASSWORD, PRACTICE_PASSWORD) — suite 23F asserts they
# agree. `practice.admin` is NOT here: its password is its own, shown only to a
# signed-in Live admin (`live_router` below).
SHARED_ACCOUNTS = (
    ("practice.storekeeper", "store_keeper", "Store Keeper", "issue, receive, return; OCR papers"),
    ("practice.supervisor", "supervisor", "Supervisor", "execution jobs, material requests, forms"),
    ("practice.hod", "hod", "Head of Department", "approvals, PRs, Requests & Pending, pictures"),
    ("practice.qc", "qc", "Quality Control", "inspections, certificates (MTC)"),
    ("practice.qchod", "qc_hod", "Head of Qualities", "QC across sites"),
    ("practice.logistics", "logistics", "Logistics", "POs, the PR queue, pictures"),
    ("practice.warehouse", "warehouse_user", "Warehouse", "the central warehouse"),
    ("practice.auditor", "auditor", "Auditor (view-only)", "reads everything, changes nothing"),
)
DEFAULT_SHARED_PASSWORD = "Practice@2026"


def shared_password() -> str:
    return os.environ.get("PRACTICE_PASSWORD", "").strip() or DEFAULT_SHARED_PASSWORD


accounts_router = APIRouter(prefix="/practice", tags=["practice"])


@accounts_router.get("/accounts", summary="The shared Practice accounts (Practice only)")
async def practice_accounts():
    """OPEN, on purpose, and PRACTICE ONLY (not mounted on Live — a 404 there).
    The login page shows these so a trainee needs nobody to hand them over;
    the data behind them is invented, and the password is the one the trainer
    hands out anyway. The admin account is never listed (Q23-13)."""
    if not is_practice():
        raise HTTPException(404, "Not Found")
    return {"password": shared_password(),
            "accounts": [{"username": u, "role": r, "label": lbl, "what": w}
                         for u, r, lbl, w in SHARED_ACCOUNTS]}


live_router = APIRouter(prefix="/admin/practice", tags=["admin"])


@live_router.get("/credentials", summary="Practice sign-in details, for a Live admin (Q23-13)")
async def practice_credentials(user: dict = Depends(require_roles("admin"))):
    """LIVE ONLY, ADMIN ONLY. The Practice admin password lives in
    `deploy/.env` (PRACTICE_ADMIN_PASSWORD) and is shown here — behind a Live
    admin's sign-in — and nowhere on the open Practice login page. Resetting
    every Practice password is the overlay, which re-sets them all; this
    returns the command rather than reaching into the Practice database (the
    wall, rule 17)."""
    admin_pw = os.environ.get("PRACTICE_ADMIN_PASSWORD", "").strip()
    return {"shared_password": shared_password(),
            "admin_username": "practice.admin",
            "admin_password": admin_pw or None,
            "admin_password_note": None if admin_pw else
            "not set in deploy/.env — the overlay generated one and printed it once; "
            "set PRACTICE_ADMIN_PASSWORD and re-run the overlay to choose it",
            "accounts": [{"username": u, "label": lbl} for u, _r, lbl, _w in SHARED_ACCOUNTS],
            "reset_command": ".venv/bin/python tools/practice_db.py overlay"}


def mounted() -> bool:
    """Whether main.py includes the router. A function so the decision is in
    one place and suite TR can assert it."""
    return is_practice() and os.environ.get("GI_PRACTICE_RESET", "on") != "off"


async def _run_passwords() -> tuple[int, str]:
    """`tools/practice_db.py passwords` — the nine Practice accounts back to
    their passwords, in both Practice databases. A SUBPROCESS, on purpose: the
    tool runs each database as a Practice process (GI_INSTANCE=training), so
    this Live process never opens a Practice database itself (rule 17). The
    output is logged as counts only — it can carry a generated password."""
    import asyncio
    import pathlib
    import sys

    root = pathlib.Path(__file__).resolve().parents[2]
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(root / "tools" / "practice_db.py"), "passwords", cwd=str(root),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "timed out after 3 minutes"
    lines = [ln for ln in (out or b"").decode(errors="replace").splitlines()
             if ln.startswith("▶ passwords") or ln.startswith("❌")]
    return proc.returncode or 0, "\n".join(lines)


@live_router.post("/reset-passwords", summary="Every Practice password back to its value (Live admin)")
async def reset_practice_passwords(user: dict = Depends(require_roles("admin"))):
    """LIVE ONLY, ADMIN ONLY (plan §6.1). Somebody changed a Practice password
    inside the sandbox and the class is locked out: this puts all nine back —
    the shared one, and practice.admin's from deploy/.env — and touches no
    other Practice data. Refused while PRACTICE_ADMIN_PASSWORD is unset: the
    tool would then make a NEW admin password up and show it only once, in a
    log nobody reads."""
    if not os.environ.get("PRACTICE_ADMIN_PASSWORD", "").strip():
        raise HTTPException(409, "set PRACTICE_ADMIN_PASSWORD in deploy/.env first — otherwise the "
                                 "Practice admin password would be replaced by a new random one")
    code, summary = await _run_passwords()
    from .db import SessionLocal
    from .services.ledger import write_audit
    async with SessionLocal() as s:
        await write_audit(s, user["username"], "PRACTICE_PASSWORDS_RESET", "users",
                          f"tools/practice_db.py passwords → exit {code}")
        await s.commit()
    if code != 0:
        raise HTTPException(502, f"the reset did not finish (exit {code}): {summary or 'see the API log'}")
    return {"ok": True, "databases": summary.count("▶ passwords"), "detail": summary}
