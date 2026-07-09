"""
backend/api/services/emailer.py — native v2 SMTP outbox (Phase 7b).

A PostgreSQL-backed outbound email queue mirroring the WhatsApp outbox pattern:
every email is logged in `email_outbox` with a delivery status (pending →
sent | failed); failures surface in the admin Email Console and can be retried.

From-scratch async implementation — it does NOT port the legacy SQLite mailer.
stdlib smtplib runs in a worker thread (no extra dependency); credentials from:
  SMTP_HOST · SMTP_PORT (default 587) · SMTP_USER · SMTP_PASS
  SMTP_FROM (default SMTP_USER) · SMTP_STARTTLS (default 1)
  EMAIL_LOGISTICS_TO — the logistics alert inbox (users have no email column;
  same fixed-recipient model as the legacy mailer).

`_smtp_send` is the ONLY function that touches the network — service_tests
monkeypatch it so CI never opens an SMTP connection.

(Named `emailer` rather than `email` to keep well clear of the stdlib package.)
"""
from __future__ import annotations

import asyncio
import os
import smtplib
from email.message import EmailMessage

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .ledger import _MD

email_outbox_t = _MD.tables["email_outbox"]

_TIMEOUT_S = 20


def _host() -> str:
    return os.environ.get("SMTP_HOST", os.environ.get("SMTP_SERVER", "")).strip()


def _port() -> int:
    try:
        return int(os.environ.get("SMTP_PORT", "587"))
    except ValueError:
        return 587


def _user() -> str:
    return os.environ.get("SMTP_USER", "").strip()


def _password() -> str:
    return os.environ.get("SMTP_PASS", "")


def _from_addr() -> str:
    return os.environ.get("SMTP_FROM", "").strip() or _user()


def _starttls() -> bool:
    return os.environ.get("SMTP_STARTTLS", "1").strip() != "0"


def enabled() -> bool:
    return bool(_host())


def logistics_to() -> str:
    """The logistics alert inbox (fixed recipient, like the legacy mailer)."""
    return os.environ.get("EMAIL_LOGISTICS_TO", "").strip() or _user()


# ── the live-SMTP boundary (monkeypatched in tests) ──────────────────────────
def _smtp_send_sync(to: str, subject: str, body: str, cc: str | None) -> dict:
    msg = EmailMessage()
    msg["From"] = _from_addr()
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject or "(no subject)"
    msg.set_content(body or "")
    with smtplib.SMTP(_host(), _port(), timeout=_TIMEOUT_S) as s:
        if _starttls():
            s.starttls()
        if _user():
            s.login(_user(), _password())
        s.send_message(msg)
    return {"ok": True}


async def _smtp_send(to: str, subject: str, body: str, cc: str | None = None) -> dict:
    """Send one email. Returns {"ok": True} or {"ok": False, "error": …}."""
    if not enabled():
        return {"ok": False, "error": "SMTP not configured (SMTP_HOST / SMTP_USER / SMTP_PASS)"}
    try:
        return await asyncio.to_thread(_smtp_send_sync, to, subject, body, cc)
    except Exception as e:  # network/auth — recorded, retryable
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ── outbox record + send ─────────────────────────────────────────────────────
async def _apply_result(session: AsyncSession, oid: int, res: dict) -> None:
    if res.get("ok"):
        await session.execute(update(email_outbox_t).where(email_outbox_t.c["id"] == oid).values(
            status="sent", error=None, attempts=email_outbox_t.c["attempts"] + 1,
            sent_at=func.now(), updated_at=func.now()))
    else:
        await session.execute(update(email_outbox_t).where(email_outbox_t.c["id"] == oid).values(
            status="failed", error=(res.get("error") or "unknown")[:1000],
            attempts=email_outbox_t.c["attempts"] + 1, updated_at=func.now()))


async def send_email(session: AsyncSession, *, to: str, subject: str, body: str,
                     event_key: str, cc: str | None = None,
                     related_table: str | None = None, related_ref=None,
                     created_by: str = "system") -> dict:
    to = (to or "").strip()
    oid = (await session.execute(insert(email_outbox_t).values(
        to_email=to or None, cc=(cc or None), subject=(subject or "")[:500],
        body=body, status="pending", event_key=event_key, related_table=related_table,
        related_ref=(str(related_ref) if related_ref is not None else None),
        attempts=0, created_by=created_by).returning(email_outbox_t.c["id"]))).scalar_one()
    if not to:
        await session.execute(update(email_outbox_t).where(email_outbox_t.c["id"] == oid).values(
            status="failed", error="no recipient address", attempts=1, updated_at=func.now()))
        return {"id": oid, "status": "failed", "error": "no recipient address"}
    res = await _smtp_send(to, subject, body, cc)
    await _apply_result(session, oid, res)
    return {"id": oid, "status": "sent" if res["ok"] else "failed",
            **({} if res["ok"] else {"error": res.get("error")})}


async def retry(session: AsyncSession, *, outbox_id: int) -> dict:
    row = (await session.execute(select(
        email_outbox_t.c["status"], email_outbox_t.c["to_email"], email_outbox_t.c["cc"],
        email_outbox_t.c["subject"], email_outbox_t.c["body"],
    ).where(email_outbox_t.c["id"] == outbox_id))).first()
    if row is None:
        return {"error": f"outbox email {outbox_id} not found"}
    if row[0] == "sent":
        return {"error": "email already sent"}
    if not (row[1] or "").strip():
        return {"error": "no recipient address on this email — cannot retry"}
    res = await _smtp_send(row[1], row[3] or "", row[4] or "", row[2])
    await _apply_result(session, outbox_id, res)
    return {"retried": True, "id": outbox_id, "status": "sent" if res["ok"] else "failed",
            **({} if res["ok"] else {"error": res.get("error")})}
