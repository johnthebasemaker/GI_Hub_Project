"""
backend/api/services/whatsapp.py — native v2 WhatsApp Cloud API outbox (Phase 7).

A PostgreSQL-backed outbound queue: every message is logged in `whatsapp_outbox`
with its exact Meta Graph payload and a delivery status (pending → sent | failed).
Sends are best-effort and synchronous (low volume); failures are visible in the
admin WhatsApp Console and can be retried manually.

This is a from-scratch async implementation — it does NOT reuse the legacy
SQLite `whatsapp_worker.py`. Credentials come from the environment:
  WHATSAPP_PHONE_NUMBER_ID · WHATSAPP_TOKEN · WHATSAPP_API_VERSION (default v20.0)
  WHATSAPP_ESCALATION_TO (optional fallback number when a recipient has none)

The two live-HTTP boundaries (`_post_message`, `_upload_media`) are the ONLY
functions that touch the network — service_tests monkeypatch them so CI never
calls Meta.
"""
from __future__ import annotations

import datetime as _dt  # noqa: F401  (imported for parity / future use)
import json
import os

import httpx
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .ledger import _MD

outbox_t = _MD.tables["whatsapp_outbox"]
users_t = _MD.tables["users"]

_API_VERSION = os.environ.get("WHATSAPP_API_VERSION", "v20.0")
_TIMEOUT_S = 15.0


def _phone_id() -> str:
    return os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()


def _token() -> str:
    return os.environ.get("WHATSAPP_TOKEN", "").strip()


def _escalation_to() -> str:
    return os.environ.get("WHATSAPP_ESCALATION_TO", "").strip()


def enabled() -> bool:
    return bool(_phone_id() and _token())


def _graph_url(path: str) -> str:
    return f"https://graph.facebook.com/{_API_VERSION}/{path}"


# ── live-HTTP boundaries (monkeypatched in tests) ────────────────────────────
async def _post_message(payload: dict) -> dict:
    """POST a message to the Cloud API. Returns {"ok", "message_id"|"error"}."""
    if not enabled():
        return {"ok": False, "error": "WhatsApp not configured (WHATSAPP_PHONE_NUMBER_ID / WHATSAPP_TOKEN)"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as c:
            r = await c.post(_graph_url(f"{_phone_id()}/messages"),
                             headers={"Authorization": f"Bearer {_token()}"}, json=payload)
        if r.status_code // 100 == 2:
            mid = ((r.json().get("messages") or [{}])[0] or {}).get("id")
            return {"ok": True, "message_id": mid}
        return {"ok": False, "error": f"{r.status_code}: {r.text[:400]}"}
    except Exception as e:  # network/timeout — recorded, retryable
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def _upload_media(blob: bytes, filename: str, mime: str) -> dict:
    """Upload a document to the media endpoint. Returns {"ok", "media_id"|"error"}."""
    if not enabled():
        return {"ok": False, "error": "WhatsApp not configured"}
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(_graph_url(f"{_phone_id()}/media"),
                             headers={"Authorization": f"Bearer {_token()}"},
                             data={"messaging_product": "whatsapp", "type": mime},
                             files={"file": (filename, blob, mime)})
        if r.status_code // 100 == 2:
            return {"ok": True, "media_id": r.json().get("id")}
        return {"ok": False, "error": f"{r.status_code}: {r.text[:400]}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ── payload builders ─────────────────────────────────────────────────────────
def _text_payload(to: str, body: str) -> dict:
    return {"messaging_product": "whatsapp", "to": to, "type": "text",
            "text": {"body": (body or "")[:4096]}}


def _doc_payload(to: str, media_id: str, filename: str, caption: str | None) -> dict:
    doc = {"id": media_id, "filename": filename}
    if caption:
        doc["caption"] = caption[:1024]
    return {"messaging_product": "whatsapp", "to": to, "type": "document", "document": doc}


# ── outbox record + send ─────────────────────────────────────────────────────
async def _apply_result(session: AsyncSession, oid: int, res: dict) -> None:
    if res.get("ok"):
        await session.execute(update(outbox_t).where(outbox_t.c["id"] == oid).values(
            status="sent", meta_message_id=res.get("message_id"), error=None,
            attempts=outbox_t.c["attempts"] + 1, sent_at=func.now(), updated_at=func.now()))
    else:
        await session.execute(update(outbox_t).where(outbox_t.c["id"] == oid).values(
            status="failed", error=(res.get("error") or "unknown")[:1000],
            attempts=outbox_t.c["attempts"] + 1, updated_at=func.now()))


async def _record_and_send(session: AsyncSession, *, to: str, payload: dict,
                           preview: str, event_key: str, related_table: str | None = None,
                           related_ref=None, created_by: str = "system") -> dict:
    to = (to or "").strip()
    oid = (await session.execute(insert(outbox_t).values(
        to_number=to or None, message_type=payload.get("type", "text"),
        body=(preview or "")[:1000], payload_json=json.dumps(payload), status="pending",
        event_key=event_key, related_table=related_table,
        related_ref=(str(related_ref) if related_ref is not None else None),
        attempts=0, created_by=created_by).returning(outbox_t.c["id"]))).scalar_one()
    if not to:
        await session.execute(update(outbox_t).where(outbox_t.c["id"] == oid).values(
            status="failed", error="no recipient number", attempts=1, updated_at=func.now()))
        return {"id": oid, "status": "failed", "error": "no recipient number"}
    res = await _post_message(payload)
    await _apply_result(session, oid, res)
    return {"id": oid, "status": "sent" if res["ok"] else "failed",
            **({"message_id": res.get("message_id")} if res["ok"] else {"error": res.get("error")})}


async def send_text(session: AsyncSession, *, to: str, body: str, event_key: str,
                    related_table: str | None = None, related_ref=None,
                    created_by: str = "system") -> dict:
    return await _record_and_send(session, to=to, payload=_text_payload(to, body),
                                  preview=body, event_key=event_key, related_table=related_table,
                                  related_ref=related_ref, created_by=created_by)


async def send_document(session: AsyncSession, *, to: str, blob: bytes, filename: str,
                        mime: str, caption: str, event_key: str, created_by: str = "system") -> dict:
    up = await _upload_media(blob, filename, mime)
    if not up.get("ok"):
        oid = (await session.execute(insert(outbox_t).values(
            to_number=(to or None), message_type="document", body=(caption or filename)[:1000],
            payload_json=json.dumps({"type": "document", "filename": filename}), status="failed",
            error=("media upload: " + (up.get("error") or "?"))[:1000], event_key=event_key,
            attempts=1, created_by=created_by).returning(outbox_t.c["id"]))).scalar_one()
        return {"id": oid, "status": "failed", "error": up.get("error")}
    return await _record_and_send(session, to=to, payload=_doc_payload(to, up["media_id"], filename, caption),
                                  preview=(caption or filename), event_key=event_key, created_by=created_by)


async def retry(session: AsyncSession, *, outbox_id: int) -> dict:
    row = (await session.execute(select(
        outbox_t.c["status"], outbox_t.c["payload_json"], outbox_t.c["to_number"]
    ).where(outbox_t.c["id"] == outbox_id))).first()
    if row is None:
        return {"error": f"outbox message {outbox_id} not found"}
    if row[0] == "sent":
        return {"error": "message already sent"}
    if not (row[2] or "").strip():
        return {"error": "no recipient number on this message — cannot retry"}
    try:
        payload = json.loads(row[1] or "{}")
    except (ValueError, TypeError):
        return {"error": "stored payload is not valid JSON"}
    res = await _post_message(payload)
    await _apply_result(session, outbox_id, res)
    return {"retried": True, "id": outbox_id, "status": "sent" if res["ok"] else "failed",
            **({} if res["ok"] else {"error": res.get("error")})}


# ── recipient resolution ─────────────────────────────────────────────────────
async def hod_numbers(session: AsyncSession, site_id: str) -> list[str]:
    rows = (await session.execute(select(users_t.c["Phone_Number"]).where(
        (users_t.c["role"] == "hod")
        & (func.coalesce(users_t.c["Site_ID"], "") == (site_id or ""))
        & users_t.c["Phone_Number"].isnot(None)))).scalars().all()
    nums = [n.strip() for n in rows if n and n.strip()]
    if not nums and _escalation_to():
        nums = [_escalation_to()]
    return nums


async def user_number(session: AsyncSession, username: str) -> str:
    n = (await session.execute(select(users_t.c["Phone_Number"])
         .where(users_t.c["username"] == username))).scalar_one_or_none()
    return (n or "").strip() or _escalation_to()
