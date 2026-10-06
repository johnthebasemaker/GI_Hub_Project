"""
backend/api/drive_admin.py — the Drive sync in the Admin Console (Phase 21c).

    GET  /admin/drive-sync             is it set up; the last run; is one running
    POST /admin/drive-sync/run         fetch from Drive now (then SME commit + ERP dry run)
    POST /admin/drive-sync/commit-erp  the operator's Commit of the ERP ledger (Q21-8)

and `daily_loop()` — the 07:30 run (Q21-9), started from the FastAPI lifespan
beside the other daily loops, behind the same one-worker claim.

⚠️ FOUR WORKERS (RULES.md). The run's state lives in Postgres
(`app_settings`: `drive_sync_last`, `drive_sync_running`), never in memory, and
a run holds a session-level advisory lock for its whole length, so a second
click — on any worker — is told one is already running instead of starting a
second download over the first.

⚠️ LIVE ONLY. In the Practice process the endpoints answer "Live only" and the
loop is not started (rule 17): Practice has no Drive and no workbooks.

The sync itself is `tools/pg_excel_sync.py`, run as a SUBPROCESS — the same
command the operator runs by hand, with the same output — rather than a second
in-process copy of its orchestration (inventory → ledger → lots order, the
pending-SAP hand-off, the one-transaction rule).
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import os
import sys
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_roles
from .config import is_practice
from .db import SessionLocal, get_session
from .services import drive_sync as DS

log = logging.getLogger("gi.drive_sync")
router = APIRouter(prefix="/admin/drive-sync", tags=["admin"])
_ADMIN = require_roles("admin")
_LOCK_KEY = 2_102_103            # pg advisory lock: one Drive run at a time
LAST_KEY, RUNNING_KEY = "drive_sync_last", "drive_sync_running"


async def _get(session: AsyncSession, key: str) -> Optional[dict]:
    v = (await session.execute(text("SELECT value FROM app_settings WHERE key = :k"),
                               {"k": key})).scalar()
    try:
        return json.loads(v) if v else None
    except ValueError:
        return None


async def _put(session: AsyncSession, key: str, value: Optional[dict]) -> None:
    if value is None:
        await session.execute(text("DELETE FROM app_settings WHERE key = :k"), {"k": key})
    else:
        await session.execute(text(
            "INSERT INTO app_settings (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = :v"), {"k": key, "v": json.dumps(value)})
    await session.commit()


async def _sync(argv: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, *argv, cwd=str(DS.ROOT), env=dict(os.environ),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "replace")


async def run_once(*, trigger: str, user: str, commit_erp: bool = False) -> dict:
    """One fetch-and-sync (or the ERP commit), start to finish. Returns the
    report it stored. Raises RuntimeError("busy") when another is running."""
    from .services.notifications import dispatch
    async with SessionLocal() as lock_s:
        got = (await lock_s.execute(text("SELECT pg_try_advisory_lock(:k)"),
                                    {"k": _LOCK_KEY})).scalar()
        if not got:
            raise RuntimeError("busy")
        try:
            async with SessionLocal() as s:
                await _put(s, RUNNING_KEY, {"since": _dt.datetime.now().isoformat(timespec="seconds"),
                                            "by": user, "what": "commit_erp" if commit_erp else "fetch"})
            rep: dict = {"trigger": trigger, "by": user,
                         "started": _dt.datetime.now().isoformat(timespec="seconds")}
            try:
                if commit_erp:
                    rc, out = await _sync(DS.ERP_COMMIT)
                    rep.update(kind="commit_erp", ok=rc == 0,
                               runs={"erp_commit": {"rc": rc, "summary": DS.summarise_sync_output(out)}})
                else:
                    fetched = await asyncio.to_thread(DS.fetch, DS.DriveClient())
                    rep.update(kind="fetch", fetch=fetched, runs={})
                    for label, argv in DS.sync_commands(fetched).items():
                        rc, out = await _sync(argv)
                        rep["runs"][label] = {"rc": rc, "summary": DS.summarise_sync_output(out)}
                    rep["ok"] = not fetched["refused"] and all(
                        r["rc"] == 0 for r in rep["runs"].values())
                    rep["erp_pending"] = "erp_dry_run" in rep["runs"]
            except DS.DriveError as e:
                rep.update(ok=False, error=str(e))
            rep["finished"] = _dt.datetime.now().isoformat(timespec="seconds")
            async with SessionLocal() as s:
                prev = await _get(s, LAST_KEY) or {}
                if commit_erp:
                    # keep the fetch report; record the commit beside it
                    prev["erp_commit"] = rep
                    prev["erp_pending"] = not rep.get("ok")
                    stored = prev
                else:
                    stored = rep
                await _put(s, LAST_KEY, stored)
                await _put(s, RUNNING_KEY, None)
                title, body = _notice(rep)
                await dispatch(s, event_key="drive_sync", title=title, body=body,
                               severity="info" if rep.get("ok") else "warning",
                               recipient_role="admin", link_page="/admin/console",
                               related_table="app_settings", related_ref=LAST_KEY,
                               wa=False)
                await s.commit()
            return rep
        finally:
            await lock_s.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY})
            await lock_s.commit()


def _notice(rep: dict) -> tuple[str, str]:
    if rep.get("error"):
        return "Drive sync could not run", rep["error"]
    if rep.get("kind") == "commit_erp":
        return ("ERP ledger committed from the Drive workbook" if rep.get("ok") else
                "ERP ledger commit FAILED — nothing was written"), \
            " · ".join((rep["runs"]["erp_commit"]["summary"] or [])[-4:])
    f = rep.get("fetch", {})
    if not f.get("changed"):
        return "Drive sync: nothing new", "No workbook changed in Drive since the last run."
    parts = [f"{len(f['changed'])} workbook(s) fetched"]
    if "sme_commit" in rep.get("runs", {}):
        parts.append("SME files synced" if rep["runs"]["sme_commit"]["rc"] == 0 else "SME sync FAILED")
    if rep.get("erp_pending"):
        parts.append("ERP ledger dry-run ready — review and press Commit in the Admin Console")
    if f.get("refused"):
        parts.append(f"{len(f['refused'])} refused (old file kept)")
    return "Drive sync: " + parts[0], " · ".join(parts[1:]) or parts[0]


# ── endpoints ────────────────────────────────────────────────────────────────
@router.get("", summary="Drive sync status (Phase 21c)")
async def status(user: dict = Depends(_ADMIN), session: AsyncSession = Depends(get_session)):
    if is_practice():
        return {"live_only": True}
    return {"live_only": False,
            "configured": {"client": DS.CLIENT_PATH.exists(), "token": DS.TOKEN_PATH.exists()},
            "folder_id": DS.FOLDER_ID, "daily_at": _daily_at(),
            "last": await _get(session, LAST_KEY), "running": await _get(session, RUNNING_KEY)}


def _start(**kw) -> None:
    async def go():
        try:
            await run_once(**kw)
        except RuntimeError:
            pass
        except Exception:  # noqa: BLE001 — logged; the status row says it failed
            log.exception("drive sync run failed")
            async with SessionLocal() as s:
                await _put(s, RUNNING_KEY, None)
    asyncio.get_running_loop().create_task(go())


async def _check_can_start(session: AsyncSession) -> None:
    if is_practice():
        raise HTTPException(409, "Drive sync is Live only — Practice has no Drive (rule 17)")
    if not (DS.CLIENT_PATH.exists() and DS.TOKEN_PATH.exists()):
        raise HTTPException(409, "Drive is not connected yet — see docs/GDRIVE_SETUP.md")
    free = (await session.execute(text(
        "SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEY})).scalar()
    if not free:
        raise HTTPException(409, "a Drive sync is already running")
    await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY})


@router.post("/run", status_code=202, summary="Fetch from Drive now (SME commit + ERP dry run)")
async def run_now(user: dict = Depends(_ADMIN), session: AsyncSession = Depends(get_session)):
    await _check_can_start(session)
    _start(trigger="manual", user=user["username"])
    return {"started": True}


@router.post("/commit-erp", status_code=202, summary="Commit the ERP ledger from the fetched workbook")
async def commit_erp(user: dict = Depends(_ADMIN), session: AsyncSession = Depends(get_session)):
    await _check_can_start(session)
    last = await _get(session, LAST_KEY) or {}
    if not last.get("erp_pending"):
        raise HTTPException(409, "no ERP dry run is waiting — fetch from Drive first")
    _start(trigger="manual", user=user["username"], commit_erp=True)
    return {"started": True}


# ── the daily run (Q21-9) ────────────────────────────────────────────────────
def _daily_at() -> str:
    return os.environ.get("GI_DRIVE_SYNC_AT", "07:30")


async def daily_loop() -> None:
    """07:30 local, one worker (the `daily_job_runs` claim), Live only, and
    only when Drive is connected — an unconnected install is silent."""
    from .services import dailyjob
    hh, mm = (int(x) for x in _daily_at().split(":"))
    log.info("drive-sync scheduler started (daily %02d:%02d local)", hh, mm)
    while True:
        now = _dt.datetime.now()
        nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if nxt <= now:
            nxt += _dt.timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        if is_practice() or not (DS.CLIENT_PATH.exists() and DS.TOKEN_PATH.exists()):
            continue
        try:
            async with SessionLocal() as s:
                if not await dailyjob.claim(s, "drive_sync", nxt):
                    continue
            await run_once(trigger="daily", user="system")
        except RuntimeError:
            log.info("drive sync: a manual run is in progress — the daily run skipped")
        except Exception:  # noqa: BLE001 — never kill the loop
            log.exception("daily drive sync failed")
