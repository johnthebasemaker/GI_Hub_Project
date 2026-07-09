"""
backend/api/report_center.py — report ARCHIVE + SCHEDULER (Phase-5 parity).

Archive: generated files live on disk (GI_REPORTS_ARCHIVE_DIR, default
`reports_archive/` — deliberately the same directory the legacy app used, so
both stacks share one archive pre-cutover) and are indexed in the legacy
`report_archive` table (no new schema).

Scheduler: a dependency-free asyncio loop started from the app lifespan —
NOT APScheduler, for two reasons: zero new deps, and APScheduler wouldn't
solve the real problem anyway (multiple uvicorn workers each run a scheduler).
Duplicate runs are prevented by an ATOMIC per-schedule claim:
    UPDATE report_schedules SET last_run = now
    WHERE id = :id AND (last_run IS NULL OR last_run < :due)
— only one worker's UPDATE wins; the losers skip. Frequencies (legacy table's
text column): 'daily HH:MM' · 'weekly <mon..sun> HH:MM' · 'monthly <DD> HH:MM'.
Times are server-local. On each run: render → archive → notify recipients
(comma-separated usernames, else the creator).
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import os
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_level, site_scope
from .db import SessionLocal, get_session
from .reports import REPORTS, render_report
from .services.ledger import _MD, write_audit
from .services.notifications import dispatch, notify

log = logging.getLogger("gi.reports.scheduler")

archive_t = _MD.tables["report_archive"]
schedules_t = _MD.tables["report_schedules"]

_FREQ_RE = re.compile(
    r"^(daily (?P<dt>\d{2}:\d{2})"
    r"|weekly (?P<wd>mon|tue|wed|thu|fri|sat|sun) (?P<wt>\d{2}:\d{2})"
    r"|monthly (?P<md>\d{1,2}) (?P<mt>\d{2}:\d{2}))$")
_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _archive_dir() -> str:
    d = os.environ.get("GI_REPORTS_ARCHIVE_DIR", "reports_archive")
    os.makedirs(d, exist_ok=True)
    return d


def _due_at(frequency: str, now: _dt.datetime) -> Optional[_dt.datetime]:
    """The most recent moment this schedule became due, or None if it has no
    due moment in the current period (e.g. weekly on another day)."""
    m = _FREQ_RE.match(frequency.strip().lower())
    if not m:
        return None
    if m.group("dt"):
        hh, mm = map(int, m.group("dt").split(":"))
        due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return due if now >= due else due - _dt.timedelta(days=1)
    if m.group("wd"):
        hh, mm = map(int, m.group("wt").split(":"))
        target = _WEEKDAYS.index(m.group("wd"))
        due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        due -= _dt.timedelta(days=(now.weekday() - target) % 7)
        return due if now >= due else due - _dt.timedelta(days=7)
    day = min(int(m.group("md")), 28)  # keep every month valid
    hh, mm = map(int, m.group("mt").split(":"))
    due = now.replace(day=day, hour=hh, minute=mm, second=0, microsecond=0)
    if now >= due:
        return due
    prev_month_end = due.replace(day=1) - _dt.timedelta(days=1)
    return prev_month_end.replace(day=day)


async def _write_archive(session: AsyncSession, *, key: str, fmt: str, user: dict,
                         site_id: Optional[str], **params) -> dict:
    data, fname, _media = await render_report(
        session, key, format=fmt, user=user, site_id=site_id, **params)
    disk_name = f"{uuid.uuid4().hex[:8]}-{fname}"
    path = os.path.join(_archive_dir(), disk_name)
    with open(path, "wb") as fh:
        fh.write(data)
    rid = (await session.execute(insert(archive_t).values(
        name=fname, report_type=key, generated_by=user["username"],
        format=fmt.lower(), size_bytes=len(data), file_path=path,
        site_id=site_id or None).returning(archive_t.c["id"]))).scalar_one()
    return {"id": rid, "name": fname, "size_bytes": len(data)}


# Router registered BEFORE the /reports/{key} catch-all (see main.py) so the
# literal /reports/archive + /reports/schedules paths win.
router = APIRouter(prefix="/reports", tags=["reports"],
                   dependencies=[Depends(require_level(2))])


class ArchiveIn(BaseModel):
    key: str
    format: str = "xlsx"
    site_id: Optional[str] = None
    days: int = 30
    within_days: int = 30
    status: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    month: Optional[str] = None


@router.post("/archive", status_code=201, summary="Generate a report into the archive")
async def archive_report(body: ArchiveIn = Body(...),
                         user: dict = Depends(require_level(2)),
                         session: AsyncSession = Depends(get_session)):
    res = await _write_archive(
        session, key=body.key, fmt=body.format, user=user, site_id=body.site_id,
        days=body.days, within_days=body.within_days, status=body.status,
        date_from=body.date_from, date_to=body.date_to, month=body.month)
    await write_audit(session, user["username"], "REPORT_ARCHIVED", "report_archive",
                      f"id={res['id']} {body.key}.{body.format}")
    await session.commit()
    return res


@router.get("/archive", summary="Archived reports (scoped users see their site's)")
async def list_archive(report_type: Optional[str] = None,
                       user: dict = Depends(require_level(2)),
                       session: AsyncSession = Depends(get_session)):
    stmt = select(archive_t)
    if report_type:
        stmt = stmt.where(archive_t.c["report_type"] == report_type)
    scope = site_scope(user)
    if scope is not None:
        stmt = stmt.where(archive_t.c["site_id"] == (scope or None))
    rows = (await session.execute(stmt.order_by(archive_t.c["id"].desc()).limit(500))
            ).mappings().all()
    return {"items": [dict(r) for r in rows]}


async def _archive_row(session: AsyncSession, aid: int, user: dict):
    row = (await session.execute(select(archive_t).where(archive_t.c["id"] == aid))
           ).mappings().first()
    if row is None:
        raise HTTPException(404, f"archive entry {aid} not found")
    scope = site_scope(user)
    if scope is not None and (row["site_id"] or "") != scope:
        raise HTTPException(404, f"archive entry {aid} not found")  # no site leak
    return row


_MEDIA = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          "pdf": "application/pdf", "csv": "text/csv"}


@router.get("/archive/{aid}/download", summary="Re-download an archived report")
async def download_archived(aid: int, user: dict = Depends(require_level(2)),
                            session: AsyncSession = Depends(get_session)):
    row = await _archive_row(session, aid, user)
    if not os.path.exists(row["file_path"]):
        raise HTTPException(410, "the archived file is gone from disk")
    with open(row["file_path"], "rb") as fh:
        data = fh.read()
    import io
    return StreamingResponse(io.BytesIO(data),
                             media_type=_MEDIA.get(row["format"], "application/octet-stream"),
                             headers={"Content-Disposition":
                                      f'attachment; filename="{row["name"]}"'})


@router.delete("/archive/{aid}", summary="Delete an archived report (admin or generator)")
async def delete_archived(aid: int, user: dict = Depends(require_level(2)),
                          session: AsyncSession = Depends(get_session)):
    row = await _archive_row(session, aid, user)
    if user["level"] < 4 and row["generated_by"] != user["username"]:
        raise HTTPException(403, "only the generator or an admin may delete this")
    try:
        if os.path.exists(row["file_path"]):
            os.remove(row["file_path"])
    except OSError:
        pass
    await session.execute(delete(archive_t).where(archive_t.c["id"] == aid))
    await write_audit(session, user["username"], "REPORT_ARCHIVE_DELETE",
                      "report_archive", f"id={aid} {row['name']}")
    await session.commit()
    return {"deleted": aid}


# --- schedules ------------------------------------------------------------------
class ScheduleIn(BaseModel):
    label: str
    report_type: str
    frequency: str  # 'daily HH:MM' | 'weekly mon..sun HH:MM' | 'monthly DD HH:MM'
    format: str = "xlsx"
    site_id: Optional[str] = None
    recipients: str = ""  # comma-separated usernames; blank → notify the creator


@router.get("/schedules", summary="Report schedules")
async def list_schedules(user: dict = Depends(require_level(2)),
                         session: AsyncSession = Depends(get_session)):
    stmt = select(schedules_t)
    scope = site_scope(user)
    if scope is not None:
        stmt = stmt.where(schedules_t.c["site_id"] == (scope or None))
    rows = (await session.execute(stmt.order_by(schedules_t.c["id"]))).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.post("/schedules", status_code=201, summary="Create a report schedule")
async def create_schedule(body: ScheduleIn = Body(...),
                          user: dict = Depends(require_level(2)),
                          session: AsyncSession = Depends(get_session)):
    if body.report_type not in REPORTS:
        raise HTTPException(422, f"unknown report {body.report_type!r}")
    if not _FREQ_RE.match(body.frequency.strip().lower()):
        raise HTTPException(422, "frequency must be 'daily HH:MM', "
                                 "'weekly <mon..sun> HH:MM' or 'monthly <DD> HH:MM'")
    if body.format.lower() not in _MEDIA:
        raise HTTPException(422, f"format must be one of {sorted(_MEDIA)}")
    scope = site_scope(user)
    site = scope if scope is not None else body.site_id
    if scope is not None and body.site_id and body.site_id != scope:
        raise HTTPException(403, "you may only schedule reports for your own site")
    sid = (await session.execute(insert(schedules_t).values(
        label=body.label.strip(), report_type=body.report_type,
        frequency=body.frequency.strip().lower(), recipients=body.recipients.strip(),
        format=body.format.lower(), site_id=site or None, active=1,
        created_by=user["username"]).returning(schedules_t.c["id"]))).scalar_one()
    await write_audit(session, user["username"], "REPORT_SCHEDULE_CREATE",
                      "report_schedules", f"id={sid} {body.report_type} {body.frequency}")
    await session.commit()
    return {"created": True, "id": sid}


@router.post("/schedules/{sid}/toggle", summary="Enable/disable a schedule")
async def toggle_schedule(sid: int, user: dict = Depends(require_level(2)),
                          session: AsyncSession = Depends(get_session)):
    row = (await session.execute(select(schedules_t.c["active"], schedules_t.c["site_id"])
           .where(schedules_t.c["id"] == sid))).first()
    if row is None:
        raise HTTPException(404, f"schedule {sid} not found")
    scope = site_scope(user)
    if scope is not None and (row.site_id or "") != scope:
        raise HTTPException(404, f"schedule {sid} not found")
    new = 0 if row.active else 1
    await session.execute(update(schedules_t).where(schedules_t.c["id"] == sid)
                          .values(active=new))
    await session.commit()
    return {"id": sid, "active": bool(new)}


@router.delete("/schedules/{sid}", summary="Delete a schedule")
async def delete_schedule(sid: int, user: dict = Depends(require_level(2)),
                          session: AsyncSession = Depends(get_session)):
    row = (await session.execute(select(schedules_t.c["site_id"])
           .where(schedules_t.c["id"] == sid))).first()
    if row is None:
        raise HTTPException(404, f"schedule {sid} not found")
    scope = site_scope(user)
    if scope is not None and (row.site_id or "") != scope:
        raise HTTPException(404, f"schedule {sid} not found")
    await session.execute(delete(schedules_t).where(schedules_t.c["id"] == sid))
    await write_audit(session, user["username"], "REPORT_SCHEDULE_DELETE",
                      "report_schedules", f"id={sid}")
    await session.commit()
    return {"deleted": sid}


async def _execute_schedule(session: AsyncSession, sched: dict) -> dict:
    """Render + archive + notify for one schedule row. Caller owns commit."""
    runner = {"username": f"scheduler:{sched['created_by'] or 'system'}",
              "role": "admin", "level": 4, "site_id": "", "warehouse_id": "",
              "label": "Scheduler"}
    res = await _write_archive(session, key=sched["report_type"],
                               fmt=sched["format"] or "xlsx", user=runner,
                               site_id=sched["site_id"])
    recipients = [u.strip() for u in (sched["recipients"] or "").split(",") if u.strip()]
    if not recipients and sched["created_by"]:
        recipients = [sched["created_by"]]
    for username in recipients:
        await dispatch(session, event_key="report_ready", recipient_user=username,
                       severity="info", wa_template="status_update",
                       title=f"Scheduled report ready: {sched['label']}",
                       body=f"{sched['report_type']} ({sched['format']}) is in the report archive.",
                       link_page="/reports", related_table="report_archive",
                       related_ref=str(res["id"]), created_by=runner)
    return res


@router.post("/schedules/{sid}/run", summary="Run a schedule now")
async def run_schedule_now(sid: int, user: dict = Depends(require_level(2)),
                           session: AsyncSession = Depends(get_session)):
    row = (await session.execute(select(schedules_t).where(schedules_t.c["id"] == sid))
           ).mappings().first()
    if row is None:
        raise HTTPException(404, f"schedule {sid} not found")
    scope = site_scope(user)
    if scope is not None and (row["site_id"] or "") != scope:
        raise HTTPException(404, f"schedule {sid} not found")
    res = await _execute_schedule(session, dict(row))
    await session.execute(update(schedules_t).where(schedules_t.c["id"] == sid)
                          .values(last_run=_dt.datetime.now()))
    await session.commit()
    return {"ran": True, "archive": res}


# --- the daemon -------------------------------------------------------------------
async def run_due_schedules(now: Optional[_dt.datetime] = None) -> int:
    """One scheduler tick. Safe under multiple workers: the last_run UPDATE is
    the atomic claim — only the winner executes."""
    now = now or _dt.datetime.now()
    ran = 0
    async with SessionLocal() as session:
        rows = (await session.execute(select(schedules_t)
                .where(schedules_t.c["active"] == 1))).mappings().all()
        for sched in rows:
            due = _due_at(sched["frequency"] or "", now)
            if due is None:
                continue
            last = sched["last_run"]
            if last is not None and last >= due:
                continue
            claim = await session.execute(
                update(schedules_t)
                .where(schedules_t.c["id"] == sched["id"],
                       (schedules_t.c["last_run"].is_(None)) | (schedules_t.c["last_run"] < due))
                .values(last_run=now))
            if not claim.rowcount:
                continue  # another worker won
            await session.commit()
            try:
                await _execute_schedule(session, dict(sched))
                await session.commit()
                ran += 1
            except Exception:  # noqa: BLE001 — one bad schedule must not kill the loop
                await session.rollback()
                log.exception("schedule %s (%s) failed", sched["id"], sched["label"])
    return ran


async def scheduler_loop(interval: int = 60) -> None:
    log.info("report scheduler started (interval %ss)", interval)
    while True:
        try:
            await run_due_schedules()
        except Exception:  # noqa: BLE001
            log.exception("scheduler tick failed")
        await asyncio.sleep(interval)
