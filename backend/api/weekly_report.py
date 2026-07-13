"""
Phase 8-3 — automated weekly Executive Summary.

Every FRIDAY 17:00 local time (the box runs Asia/Riyadh, UTC+3 — same
convention as the 16:00 evening digest) the scheduler renders the last
7 days' Executive Summary PDF (backend/api/exec_pdf.py) and dispatches it to
every ACTIVE admin and HOD:

  · one ALL-SITES PDF for the global roles (admin), and
  · one site-scoped PDF per distinct HOD site,

each stored in `generated_reports` with a SECURE EXPIRING DOWNLOAD LINK —
Meta document attachments only deliver inside a 24-hour customer-service
window, which a Friday broadcast usually is NOT in, so the reliable path is
an approved template message carrying
`{PUBLIC_BASE_URL}/reports/weekly-exec/{token}`. The raw token (32 bytes,
urlsafe) is never stored — only its sha256 — and the link dies after
`LINK_TTL_HOURS` (72 h). Delivery goes through services.notifications
.dispatch(), so every recipient ALSO gets the in-app bell row, and WhatsApp
failures can never break the run.

Manual trigger for ops/testing: POST /admin/reports/weekly-exec/run (admin).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import io
import logging
import os
import secrets

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_roles
from .db import get_session
from .exec_summary import _build_summary
from .services.ledger import _MD
from .services.notifications import dispatch

log = logging.getLogger("gi.weekly_report")
router = APIRouter(tags=["reports"])

reports_t = _MD.tables["generated_reports"]

LINK_TTL_HOURS = 72
KIND = "weekly_exec"


def _public_base() -> str:
    return os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")


def next_friday_1700(now: _dt.datetime) -> _dt.datetime:
    """Next Friday 17:00 strictly after `now` (server-local wall clock)."""
    target = now.replace(hour=17, minute=0, second=0, microsecond=0)
    days_ahead = (4 - now.weekday()) % 7  # Monday=0 … Friday=4
    target += _dt.timedelta(days=days_ahead)
    if target <= now:
        target += _dt.timedelta(days=7)
    return target


async def _render_site(session: AsyncSession, site: str | None,
                       dfrom: str, dto: str) -> tuple[bytes, str]:
    from .exec_pdf import render_exec_pdf
    d = await _build_summary(session, site=site, dfrom=dfrom, dto=dto, detail_limit=500)
    blob = render_exec_pdf(d, site=site, username="scheduler")
    fname = f"weekly_executive_summary_{(site or 'ALL')}_{dfrom}_{dto}.pdf"
    return blob, fname


async def _store(session: AsyncSession, *, site: str | None, dfrom: str, dto: str,
                 blob: bytes, fname: str) -> str:
    """Insert the artifact; returns the RAW download token (stored hashed)."""
    token = secrets.token_urlsafe(32)
    expires = _dt.datetime.now() + _dt.timedelta(hours=LINK_TTL_HOURS)
    await session.execute(insert(reports_t).values(
        kind=KIND, Site_ID=site, date_from=dfrom, date_to=dto, filename=fname,
        content=blob, token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=expires))
    return token


async def generate_and_dispatch(session: AsyncSession) -> dict:
    """Render + store + notify. One PDF per distinct recipient scope."""
    today = _dt.date.today()
    dfrom, dto = (today - _dt.timedelta(days=6)).isoformat(), today.isoformat()

    # recipients: every admin (all-sites scope) + every hod (their site)
    users_t = _MD.tables["users"]
    rows = (await session.execute(select(
        users_t.c["username"], users_t.c["role"], users_t.c["Site_ID"]
    ).where(users_t.c["role"].in_(("admin", "hod"))))).all()

    scopes: dict[str | None, list[str]] = {}
    for r in rows:
        scope = None if r.role == "admin" else ((r.Site_ID or "").strip() or None)
        scopes.setdefault(scope, []).append(r.username)

    # housekeeping: purge expired artifacts of this kind
    await session.execute(delete(reports_t).where(
        (reports_t.c["kind"] == KIND)
        & (reports_t.c["expires_at"] < text("CURRENT_TIMESTAMP"))))

    links, notified = {}, 0
    for scope, usernames in scopes.items():
        blob, fname = await _render_site(session, scope, dfrom, dto)
        token = await _store(session, site=scope, dfrom=dfrom, dto=dto,
                             blob=blob, fname=fname)
        url = f"{_public_base()}/reports/weekly-exec/{token}"
        links[scope or "ALL"] = url
        for username in usernames:
            await dispatch(
                session, recipient_user=username, event_key="weekly_exec_report",
                severity="info", wa_template="status_update",
                title=f"Weekly Executive Summary — {scope or 'all sites'}",
                body=(f"Period {dfrom} → {dto}. Download the PDF (link valid "
                      f"{LINK_TTL_HOURS} h): {url}"),
                related_table="generated_reports", related_ref=f"{KIND}:{scope or 'ALL'}",
                created_by="scheduler", delivery="urgent")
            notified += 1
    await session.commit()
    log.info("weekly exec report: %d PDF(s), %d recipient(s), period %s→%s",
             len(links), notified, dfrom, dto)
    return {"reports": len(links), "recipients": notified,
            "date_from": dfrom, "date_to": dto, "links": links}


async def weekly_report_loop() -> None:
    """Daemon: Friday 17:00 local, forever. Started from the FastAPI lifespan
    next to the digest loop; disabled by GI_SCHEDULER=0."""
    import asyncio

    from .db import SessionLocal

    log.info("weekly exec-report scheduler started (Friday 17:00 local)")
    while True:
        now = _dt.datetime.now()
        nxt = next_friday_1700(now)
        await asyncio.sleep((nxt - now).total_seconds())
        try:
            async with SessionLocal() as s:
                res = await generate_and_dispatch(s)
            log.info("weekly exec report run: %s", res)
        except Exception:  # noqa: BLE001 — one bad run must not kill the loop
            log.exception("weekly exec report run failed")


# ── endpoints ────────────────────────────────────────────────────────────────
@router.get("/reports/weekly-exec/{token}",
            summary="Secure expiring download of an auto-generated weekly PDF")
async def download_weekly_exec(token: str,
                               session: AsyncSession = Depends(get_session)):
    """UNAUTHENTICATED by design — recipients open it from WhatsApp on their
    phones with no app session. Security = the unguessable 256-bit token
    (stored only as sha256) + hard expiry."""
    h = hashlib.sha256(token.encode()).hexdigest()
    row = (await session.execute(select(
        reports_t.c["filename"], reports_t.c["content"], reports_t.c["expires_at"]
    ).where(reports_t.c["token_hash"] == h))).first()
    if row is None:
        raise HTTPException(404, "unknown or revoked link")
    if row.expires_at < _dt.datetime.now():
        raise HTTPException(410, "this download link has expired")
    return StreamingResponse(
        io.BytesIO(row.content), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{row.filename}"'})


@router.post("/admin/reports/weekly-exec/run",
             summary="Run the weekly executive report now (admin)")
async def run_weekly_exec_now(user: dict = Depends(require_roles()),
                              session: AsyncSession = Depends(get_session)):
    return await generate_and_dispatch(session)
