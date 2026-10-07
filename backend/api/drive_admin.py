"""
backend/api/drive_admin.py — the Drive sync (Phase 21c, extended in Phase 22a).

    GET  /admin/drive-sync               is it set up; the schedule; the last run; history
    PUT  /admin/drive-sync/schedule      the pull times, on/off, auto-commit (Q22-1/2)
    POST /admin/drive-sync/run           fetch from Drive now (then SME commit + ERP dry run)
    POST /admin/drive-sync/commit-erp    the operator's Commit of the ERP ledger (Q21-8)

    GET  /drive/freshness                "Last updated from Drive", for every signed-in user
    POST /drive/pull                     the top-bar Pull button — admin and HOD (Q22-3)
    GET  /drive/files/{id}               a cached read-only copy of a DN / MTC / request file

and `schedule_loop()` — the pulls at the times set in the UI (07:30 and 19:30
by default, ruling Q22-2), started from the FastAPI lifespan, each time slot
claimed once across every worker.

⚠️ FOUR WORKERS (RULES.md). The run's state lives in Postgres
(`app_settings`: `drive_sync_last`, `drive_sync_running`, `drive_sync_last_ok`,
`drive_sync_schedule`; table `drive_sync_runs`), never in memory, and a run
holds a session-level advisory lock for its whole length, so a second click —
on any worker — is told one is already running instead of starting a second
download over the first.

⚠️ LIVE ONLY. In the Practice process the endpoints answer "Live only", the
banner says "Practice data", and the loop is not started (rule 17).

⚠️ AUTO-COMMIT ONLY ADDS (ruling Q22-1). After a fetch the ERP side is
dry-run; when that dry run would only INSERT rows (`DS.additions_only`) and the
setting is on, the same run commits it. A dry run that would edit or remove an
existing row, or reject one, waits for the operator's Commit — amber banner and
a bell notice. The rule is the same for the schedule, the Pull button, the
Admin card and the CLI: "only adds" is what makes it safe, not who pressed.

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
import mimetypes
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user, require_roles
from .config import is_practice
from .db import SessionLocal, get_session
from .services import drive_sync as DS

log = logging.getLogger("gi.drive_sync")
router = APIRouter(prefix="/admin/drive-sync", tags=["admin"])
drive_router = APIRouter(prefix="/drive", tags=["drive"])

_ADMIN = require_roles("admin")
_PULLERS = require_roles("admin", "hod")          # ruling Q22-3
_LOCK_KEY = 2_102_103            # pg advisory lock: one Drive run at a time
LAST_KEY, RUNNING_KEY = "drive_sync_last", "drive_sync_running"
LAST_OK_KEY, SCHEDULE_KEY = "drive_sync_last_ok", "drive_sync_schedule"


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


async def _schedule(session: AsyncSession) -> dict:
    raw = (await session.execute(text("SELECT value FROM app_settings WHERE key = :k"),
                                 {"k": SCHEDULE_KEY})).scalar()
    return DS.parse_schedule(raw, os.environ.get("GI_DRIVE_SYNC_AT"))


async def _sync(argv: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, *argv, cwd=str(DS.ROOT), env=dict(os.environ),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "replace")


async def _dry_run_with_report(argv: list[str]) -> tuple[int, str, Optional[dict]]:
    with tempfile.TemporaryDirectory(prefix="gi-drive-") as td:
        path = Path(td) / "report.json"
        rc, out = await _sync([*argv, "--report-json", str(path)])
        try:
            report = json.loads(path.read_text())
        except (OSError, ValueError):
            report = None
    return rc, out, report


# ── the subfolder index (drive_files) ────────────────────────────────────────
async def _known_files(session: AsyncSession) -> dict[str, dict]:
    rows = (await session.execute(text(
        "SELECT drive_id, md5, modified_time FROM drive_files"))).mappings().all()
    return {r["drive_id"]: dict(r) for r in rows}


async def _upsert_files(session: AsyncSession, rows: list[dict], seen_ids: set[str]) -> dict:
    """Write the crawl into drive_files; files no longer in Drive are marked
    `removed_at` (kept — a receipt may still point at the DN)."""
    counts: dict[str, dict[str, int]] = {}
    for r in rows:
        c = counts.setdefault(r["kind"], {"total": 0, "new": 0, "changed": 0, "same": 0,
                                          "failed": 0, "later": 0})
        c["total"] += 1
        c[r["state"]] = c.get(r["state"], 0) + 1
        size = r.get("size")
        await session.execute(text("""
            INSERT INTO drive_files (drive_id, kind, folder, name, mime, size, md5,
                                     modified_time, cache_path, first_seen, last_seen)
            VALUES (:id, :kind, :folder, :name, :mime, :size, :md5, :mt, :cp,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (drive_id) DO UPDATE SET
                kind = :kind, folder = :folder, name = :name, mime = :mime,
                size = COALESCE(:size, drive_files.size),
                md5 = CASE WHEN :ok THEN :md5 ELSE drive_files.md5 END,
                modified_time = CASE WHEN :ok THEN :mt ELSE drive_files.modified_time END,
                cache_path = COALESCE(:cp, drive_files.cache_path),
                last_seen = CURRENT_TIMESTAMP, removed_at = NULL"""),
            {"id": r["id"], "kind": r["kind"], "folder": r.get("folder"), "name": r["name"],
             "mime": r.get("mimeType"), "size": int(size) if size not in (None, "") else None,
             "md5": r.get("md5Checksum"), "mt": r.get("modifiedTime"),
             "cp": r.get("cache_path"), "ok": r["state"] in ("new", "changed", "same")})
    removed = (await session.execute(text(
        "UPDATE drive_files SET removed_at = CURRENT_TIMESTAMP "
        "WHERE removed_at IS NULL AND NOT (drive_id = ANY(:ids)) RETURNING drive_id"),
        {"ids": list(seen_ids)})).all()
    await session.commit()
    return {"kinds": counts, "removed": len(removed)}


async def _record_run(session: AsyncSession, rep: dict) -> None:
    def ts(v):
        return _dt.datetime.fromisoformat(v) if v else None
    changed = len((rep.get("fetch") or {}).get("changed") or [])
    await session.execute(text("""
        INSERT INTO drive_sync_runs (started_at, finished_at, trigger, by_user, kind, ok,
                                     files_changed, erp_auto_committed, report)
        VALUES (:s, :f, :t, :b, :k, :ok, :n, :auto, :rep)"""),
        {"s": ts(rep.get("started")) or _dt.datetime.now(), "f": ts(rep.get("finished")),
         "t": rep.get("trigger") or "manual", "b": rep.get("by"), "k": rep.get("kind") or "fetch",
         "ok": rep.get("ok"), "n": changed,
         "auto": bool((rep.get("auto_commit") or {}).get("done")),
         "rep": json.dumps(rep, default=str)})
    await session.commit()


# ── one run ──────────────────────────────────────────────────────────────────
async def run_once(*, trigger: str, user: str, commit_erp: bool = False,
                   accept_shrink: bool = False, client_factory=None) -> dict:
    """One fetch-and-sync (or the ERP commit), start to finish. Returns the
    report it stored. Raises RuntimeError("busy") when another is running."""
    from .services.notifications import dispatch
    make_client = client_factory or DS.DriveClient
    async with SessionLocal() as lock_s:
        got = (await lock_s.execute(text("SELECT pg_try_advisory_lock(:k)"),
                                    {"k": _LOCK_KEY})).scalar()
        if not got:
            raise RuntimeError("busy")
        try:
            async with SessionLocal() as s:
                await _put(s, RUNNING_KEY, {"since": _dt.datetime.now().isoformat(timespec="seconds"),
                                            "by": user, "what": "commit_erp" if commit_erp else "fetch"})
                schedule = await _schedule(s)
            rep: dict = {"trigger": trigger, "by": user,
                         "started": _dt.datetime.now().isoformat(timespec="seconds")}
            stamps: dict[str, str] = {}
            try:
                if commit_erp:
                    rc, out = await _sync(DS.ERP_COMMIT)
                    rep.update(kind="commit_erp", ok=rc == 0,
                               runs={"erp_commit": {"rc": rc, "summary": DS.summarise_sync_output(out)}})
                    if rc == 0:
                        stamps["erp_commit_at"] = rep["started"]
                else:
                    client = make_client()
                    root = await asyncio.to_thread(client.list_folder, DS.FOLDER_ID)
                    fetched = await asyncio.to_thread(DS.fetch, client, files=root,
                                                      accept_shrink=accept_shrink)
                    rep.update(kind="fetch", fetch=fetched, runs={})
                    dry_report = None
                    for label, argv in DS.sync_commands(fetched).items():
                        if label == "erp_dry_run":
                            rc, out, dry_report = await _dry_run_with_report(argv)
                        else:
                            rc, out = await _sync(argv)
                        rep["runs"][label] = {"rc": rc, "summary": DS.summarise_sync_output(out)}
                        if label == "sme_commit" and rc == 0:
                            stamps["sme_commit_at"] = rep["started"]
                    rep["erp_pending"] = False
                    if "erp_dry_run" in rep["runs"]:
                        rep["erp_pending"] = True
                        rep["erp_dry_run_report"] = dry_report
                        ok_add, why = DS.additions_only(dry_report)
                        if rep["runs"]["erp_dry_run"]["rc"] != 0:
                            why = ["the dry run failed"] + why
                            ok_add = False
                        if ok_add and not DS.inserts_total(dry_report):
                            rep["erp_pending"] = False            # nothing to write
                            rep["auto_commit"] = {"done": False, "reasons": ["no new rows"]}
                        elif ok_add and schedule.get("auto_commit_additions"):
                            rc, out = await _sync(DS.ERP_COMMIT)
                            rep["runs"]["erp_commit"] = {"rc": rc,
                                                         "summary": DS.summarise_sync_output(out)}
                            rep["auto_commit"] = {"done": rc == 0,
                                                  "rows": DS.inserts_total(dry_report)}
                            if rc == 0:
                                rep["erp_pending"] = False
                                stamps["erp_commit_at"] = rep["started"]
                        else:
                            rep["auto_commit"] = {"done": False, "reasons": why or
                                                  ["auto-commit is switched off"]}
                    # the subfolders (22a plumbing; parsed by 22b–22d)
                    try:
                        crawl = await asyncio.to_thread(DS.crawl, client, root)
                        async with SessionLocal() as s:
                            known = await _known_files(s)
                        cached = await asyncio.to_thread(DS.cache_files, client,
                                                         crawl["files"], known)
                        async with SessionLocal() as s:
                            rep["folders"] = await _upsert_files(
                                s, cached, {f["id"] for f in crawl["files"]})
                        rep["folders"]["ignored"] = crawl["ignored"]
                        rep["folders"]["unknown"] = crawl["unknown"]
                        await _after_crawl()
                    except DS.DriveError as e:
                        rep["folders"] = {"error": str(e)}
                    rep["ok"] = not fetched["refused"] and all(
                        r["rc"] == 0 for r in rep["runs"].values())
            except DS.TokenError as e:
                rep.update(ok=False, error=str(e), token_error=True)
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
                if rep.get("ok"):
                    ok_prev = await _get(s, LAST_OK_KEY) or {}
                    if not commit_erp:
                        ok_prev["fetch_at"] = rep["finished"]
                        ok_prev["files"] = _file_stamps(rep, ok_prev.get("files") or {})
                    ok_prev.update(stamps)
                    await _put(s, LAST_OK_KEY, ok_prev)
                await _record_run(s, rep)
                await _put(s, RUNNING_KEY, None)
                title, body = _notice(rep)
                await dispatch(s, event_key="drive_sync", title=title, body=body,
                               severity="info" if rep.get("ok") and not rep.get("erp_pending")
                               else "warning",
                               recipient_role="admin", link_page="/admin/console",
                               related_table="app_settings", related_ref=LAST_KEY,
                               wa=False)
                await s.commit()
            return rep
        finally:
            await lock_s.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY})
            await lock_s.commit()


async def _after_crawl() -> None:
    """Hook for slices 22b–22d: parse the names and link them (DN → receipts,
    MTC → lots, requests → lines). Each parser is idempotent over drive_files."""
    from .services import drive_links
    async with SessionLocal() as s:
        await drive_links.relink_all(s)
        await s.commit()


def _file_stamps(rep: dict, prev: dict) -> dict:
    """Per workbook: the Drive modified time of the copy GI Hub now holds."""
    out = dict(prev)
    for c in (rep.get("fetch") or {}).get("changed") or []:
        out[c["dest"]] = {"source": c["source"], "modifiedTime": c.get("modifiedTime"),
                          "fetched_at": rep.get("finished")}
    return out


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
    auto = rep.get("auto_commit") or {}
    if auto.get("done"):
        parts.append(f"ERP ledger committed by itself — {auto.get('rows', 0)} new row(s), "
                     "nothing existing changed")
    elif rep.get("erp_pending"):
        why = "; ".join((auto.get("reasons") or [])[:3])
        parts.append("ERP ledger dry-run ready — review and press Commit in the Admin Console"
                     + (f" ({why})" if why else ""))
    if f.get("refused"):
        parts.append(f"{len(f['refused'])} refused (old file kept)")
    return "Drive sync: " + parts[0], " · ".join(parts[1:]) or parts[0]


# ── endpoints: the Admin card ────────────────────────────────────────────────
def _token_info() -> Optional[dict]:
    try:
        st = DS.TOKEN_PATH.stat()
    except OSError:
        return None
    saved = _dt.datetime.fromtimestamp(st.st_mtime)
    return {"saved_at": saved.isoformat(timespec="minutes"),
            # a Google app left in "Testing" ends the sign-in 7 days later
            "testing_expiry": (saved + _dt.timedelta(days=7)).isoformat(timespec="minutes")}


@router.get("", summary="Drive sync status (Phase 21c / 22a)")
async def status(user: dict = Depends(_ADMIN), session: AsyncSession = Depends(get_session)):
    if is_practice():
        return {"live_only": True}
    sch = await _schedule(session)
    hist = (await session.execute(text(
        "SELECT id, started_at, finished_at, trigger, by_user, kind, ok, files_changed, "
        "erp_auto_committed FROM drive_sync_runs ORDER BY started_at DESC, id DESC LIMIT 10"))
    ).mappings().all()
    files = (await session.execute(text(
        "SELECT kind, COALESCE(link_status, 'unlinked') AS link, COUNT(*) AS n FROM drive_files "
        "WHERE removed_at IS NULL GROUP BY 1, 2 ORDER BY 1, 2"))).mappings().all()
    return {"live_only": False,
            "configured": {"client": DS.CLIENT_PATH.exists(), "token": DS.TOKEN_PATH.exists()},
            "token": _token_info(),
            "folder_id": DS.FOLDER_ID, "daily_at": ", ".join(sch["times"]),
            "schedule": sch,
            "next_at": (DS.next_slot(_dt.datetime.now(), sch["times"]).isoformat(timespec="minutes")
                        if sch.get("enabled") else None),
            "last": await _get(session, LAST_KEY), "last_ok": await _get(session, LAST_OK_KEY),
            "running": await _get(session, RUNNING_KEY),
            "history": [{k: (v.isoformat(timespec="seconds") if isinstance(v, _dt.datetime) else v)
                         for k, v in dict(r).items()} for r in hist],
            "files": [dict(r) for r in files]}


class ScheduleIn(BaseModel):
    enabled: bool = True
    times: list[str]
    auto_commit_additions: bool = True


@router.put("/schedule", summary="Set the pull times (ruling Q22-2) and auto-commit (Q22-1)")
async def set_schedule(body: ScheduleIn, user: dict = Depends(_ADMIN),
                       session: AsyncSession = Depends(get_session)):
    if is_practice():
        raise HTTPException(409, "Drive sync is Live only — Practice has no Drive (rule 17)")
    try:
        times = DS.clean_times(body.times)
    except ValueError as e:
        raise HTTPException(422, str(e))
    value = {"enabled": body.enabled, "times": times,
             "auto_commit_additions": body.auto_commit_additions}
    from .services.ledger import write_audit
    await write_audit(session, user["username"], "drive_schedule", "app_settings",
                      json.dumps(value))
    await _put(session, SCHEDULE_KEY, value)
    return value


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
async def run_now(accept_shrink: bool = False, user: dict = Depends(_ADMIN),
                  session: AsyncSession = Depends(get_session)):
    await _check_can_start(session)
    _start(trigger="manual", user=user["username"], accept_shrink=accept_shrink)
    return {"started": True}


@router.post("/commit-erp", status_code=202, summary="Commit the ERP ledger from the fetched workbook")
async def commit_erp(user: dict = Depends(_ADMIN), session: AsyncSession = Depends(get_session)):
    await _check_can_start(session)
    last = await _get(session, LAST_KEY) or {}
    if not last.get("erp_pending"):
        raise HTTPException(409, "no ERP dry run is waiting — fetch from Drive first")
    _start(trigger="manual", user=user["username"], commit_erp=True)
    return {"started": True}


# ── endpoints: every signed-in user ──────────────────────────────────────────
@drive_router.get("/freshness", summary="Last updated from Drive (top bar, Phase 22a)")
async def freshness(user: dict = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
    if is_practice():
        return {"status": "practice"}
    from .services import lots as LOTS
    last = await _get(session, LAST_KEY) or None
    last_ok = await _get(session, LAST_OK_KEY) or {}
    running = await _get(session, RUNNING_KEY)
    sch = await _schedule(session)
    now = _dt.datetime.now()
    connected = DS.CLIENT_PATH.exists() and DS.TOKEN_PATH.exists()
    st = DS.freshness_status(practice=False, connected=connected, running=bool(running),
                             last=last, last_ok_at=last_ok.get("fetch_at"), now=now)
    counts = await LOTS.problem_counts(session)
    site = user.get("site_id")
    return {
        "status": st,
        "last_ok_at": last_ok.get("fetch_at"),
        "erp_commit_at": last_ok.get("erp_commit_at"),
        "sme_commit_at": last_ok.get("sme_commit_at"),
        "files": last_ok.get("files") or {},
        "last_run_at": (last or {}).get("finished"),
        "last_trigger": (last or {}).get("trigger"),
        "erp_pending": bool((last or {}).get("erp_pending")),
        "pending_reasons": ((last or {}).get("auto_commit") or {}).get("reasons") or [],
        "error": (last or {}).get("error") if st in ("failed", "token") else None,
        "running": bool(running),
        "next_at": (DS.next_slot(now, sch["times"]).isoformat(timespec="minutes")
                    if sch.get("enabled") else None),
        "lot_problems": counts.get(site, 0) if site else sum(counts.values()),
        "can_pull": user.get("role") in ("admin", "hod") and connected,
    }


@drive_router.post("/pull", status_code=202, summary="Pull from Drive now (admin, HOD — Q22-3)")
async def pull(user: dict = Depends(_PULLERS), session: AsyncSession = Depends(get_session)):
    await _check_can_start(session)
    _start(trigger="pull", user=user["username"])
    return {"started": True}


@drive_router.get("/files/{file_id}", summary="A cached read-only copy of a Drive file")
async def drive_file(file_id: int, user: dict = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)):
    if is_practice():
        raise HTTPException(404, "not found")
    row = (await session.execute(text(
        "SELECT name, mime, cache_path FROM drive_files WHERE id = :i"), {"i": file_id})
    ).mappings().first()
    if not row or not row["cache_path"]:
        raise HTTPException(404, "not found")
    path = Path(row["cache_path"]).resolve()
    if DS.CACHE_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "not found")
    media = row["mime"] or mimetypes.guess_type(row["name"])[0] or "application/octet-stream"
    return FileResponse(path, media_type=media, filename=row["name"],
                        content_disposition_type="inline")


# ── the schedule (Q22-2) ─────────────────────────────────────────────────────
def _daily_at() -> str:
    """Phase 21c's single time — now only the default's first slot."""
    return os.environ.get("GI_DRIVE_SYNC_AT", "07:30")


TICK_S = 60


async def schedule_loop() -> None:
    """Every minute: is a pull time due? Each (time, day) slot is claimed once
    across every worker (`daily_job_runs`), a slot missed while the server was
    down still runs up to 3 hours late, Live only, and only when Drive is
    connected — an unconnected install is silent."""
    from .services import dailyjob
    log.info("drive-sync scheduler started")
    while True:
        await asyncio.sleep(TICK_S)
        if is_practice() or not (DS.CLIENT_PATH.exists() and DS.TOKEN_PATH.exists()):
            continue
        try:
            async with SessionLocal() as s:
                sch = await _schedule(s)
                if not sch.get("enabled"):
                    continue
                due = [(t, slot) for t, slot in DS.due_slots(_dt.datetime.now(), sch["times"])
                       if await dailyjob.claim(s, f"drive_sync@{t}", slot)]
            if due:
                await run_once(trigger="schedule", user="system")
        except RuntimeError:
            log.info("drive sync: a run is in progress — the scheduled slot skipped")
        except Exception:  # noqa: BLE001 — never kill the loop
            log.exception("scheduled drive sync failed")


daily_loop = schedule_loop          # the lifespan's Phase 21c name
