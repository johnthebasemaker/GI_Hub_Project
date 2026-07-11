"""
backend/api/webhook.py — inbound WhatsApp webhook (Phase 6).

Meta Cloud API pushes user-initiated messages here. Two routes (mounted at
BOTH /whatsapp/webhook and /api/v1/whatsapp/webhook):

  GET  /webhook — Meta's one-time subscription handshake: echoes hub.challenge
                  only when hub.verify_token matches WHATSAPP_WEBHOOK_VERIFY_TOKEN.
  POST /webhook — message delivery. When WHATSAPP_APP_SECRET is set, the raw
                  body MUST carry a valid X-Hub-Signature-256 HMAC (Meta signs
                  every delivery with the App Secret) — invalid → 403.

Sender resolution: the wa_id digits are canonicalised to +E.164 and matched
against users.Phone_Number (the project-wide storage format). Numbers with no
matching user are logged and silently dropped — no reply is sent to strangers
(no cost, no information leak). Verified users get their role/site loaded and
may run interactive commands:

  STOCK <SAP_CODE>  → current balance, site-scoped by the user's role
                      (level ≥3 = global; scoped roles = their Site_ID,
                      fail-closed when the account has no site)
  RESET PASSWORD    → a single-use temporary password to the registered number;
                      every refresh session is revoked so only the new
                      credential works
  HELP              → command list

Replies are free-form session texts (send_session_text) — always inside Meta's
24-hour customer-service window because they answer an inbound message.
Handlers ACK 200 even for messages they drop; a non-200 makes Meta redeliver
the same event and would duplicate side effects.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets

import bcrypt
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import delete, select, text as sqltext
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import ROLE_META, SITE_SCOPE_MIN_LEVEL, _audit
from .db import get_session
from .services import whatsapp as wa
from .services.ledger import _MD
from .stock import SQL_LIVE_STOCK, SQL_SITE_STOCK

log = logging.getLogger("gi.webhook")

router = APIRouter(tags=["whatsapp webhook"])

users_t = _MD.tables["users"]
sessions_t = _MD.tables["auth_sessions"]

_TEMP_PW_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"


def _verify_token() -> str:
    return os.environ.get("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "").strip()


def _app_secret() -> str:
    return os.environ.get("WHATSAPP_APP_SECRET", "").strip()


@router.get("/webhook", summary="Meta webhook verification handshake")
async def verify_webhook(
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
):
    expected = _verify_token()
    if mode == "subscribe" and expected and hmac.compare_digest(token, expected):
        return PlainTextResponse(challenge)
    log.warning("webhook handshake rejected (mode=%r, token %s)", mode,
                "matched" if token == expected else "mismatch/unset")
    return PlainTextResponse("verification failed", status_code=403)


def _signature_ok(raw: bytes, header: str) -> bool:
    secret = _app_secret()
    if not secret:
        return True  # not configured — local/testing mode
    if not header.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len("sha256="):], digest)


def _iter_text_messages(payload: dict):
    """Yield (sender_digits, text_body) for every inbound text message."""
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for msg in value.get("messages") or []:
                if msg.get("type") == "text":
                    body = ((msg.get("text") or {}).get("body") or "").strip()
                    sender = (msg.get("from") or "").strip()
                    if sender and body:
                        yield sender, body


async def _resolve_sender(session: AsyncSession, sender_digits: str) -> dict | None:
    """wa_id digits → the matching active user (canonical +E.164 lookup)."""
    canonical = "+" + "".join(ch for ch in sender_digits if ch.isdigit())
    row = (await session.execute(select(
        users_t.c["username"], users_t.c["role"], users_t.c["Site_ID"],
        users_t.c["Phone_Number"]
    ).where(users_t.c["Phone_Number"] == canonical))).first()
    if row is None:
        return None
    level = ROLE_META.get(row[1] or "", {}).get("level", 0)
    return {"username": row[0], "role": row[1], "site_id": (row[2] or "").strip(),
            "phone": row[3], "level": level}


async def _cmd_stock(session: AsyncSession, user: dict, sap: str) -> str:
    sap = sap.strip().upper()
    if user["level"] >= SITE_SCOPE_MIN_LEVEL:
        row = (await session.execute(sqltext(
            f'SELECT * FROM ({SQL_LIVE_STOCK}) sub WHERE sub."SAP_Code" = :sap'
        ), {"sap": sap})).mappings().first()
        scope_label = "all sites"
    else:
        if not user["site_id"]:
            return ("Your account has no site assigned, so site-scoped stock "
                    "cannot be shown. Please contact an admin.")
        row = (await session.execute(sqltext(
            f'SELECT * FROM ({SQL_SITE_STOCK}) sub '
            'WHERE sub."SAP_Code" = :sap AND sub."Site_ID" = :site'
        ), {"sap": sap, "site": user["site_id"]})).mappings().first()
        scope_label = f"site {user['site_id']}"
    if row is None:
        return (f"SAP code {sap} was not found for {scope_label}. "
                "Check the code and try again (e.g. STOCK 4000123).")
    desc = (row.get("Equipment_Description") or "").strip()
    uom = (row.get("UOM") or "").strip()
    qty = row.get("Current_Stock") or 0
    return (f"{sap}{' — ' + desc if desc else ''}\n"
            f"Current stock ({scope_label}): {qty:g}{' ' + uom if uom else ''}\n"
            f"Received {row.get('Total_Received') or 0:g} · "
            f"Consumed {row.get('Total_Consumed') or 0:g} · "
            f"Returned {row.get('Total_Returned') or 0:g}")


async def _cmd_reset_password(session: AsyncSession, user: dict) -> str:
    temp = "".join(secrets.choice(_TEMP_PW_ALPHABET) for _ in range(10))
    pw_hash = bcrypt.hashpw(temp.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    await session.execute(users_t.update()
                          .where(users_t.c["username"] == user["username"])
                          .values(password_hash=pw_hash))
    # Containment: the old credential and every refresh session die together —
    # only the holder of the phone (this temp password) can sign in now.
    await session.execute(delete(sessions_t)
                          .where(sessions_t.c["username"] == user["username"]))
    await _audit(session, user["username"], "PASSWORD_RESET_WHATSAPP",
                 "temporary password issued via WhatsApp self-service; all sessions revoked")
    return (f"Your GI Hub temporary password: {temp}\n"
            "Sign in with it now and change your password immediately "
            "(Profile → Change password). All previous sessions were signed out. "
            "If you did not request this, contact an admin at once.")


_HELP = ("GI Hub WhatsApp commands:\n"
         "• STOCK <SAP_CODE> — current stock balance for your site\n"
         "• RESET PASSWORD — receive a temporary password\n"
         "• HELP — this list")


async def _handle_command(session: AsyncSession, user: dict, body: str) -> str:
    words = body.split()
    verb = words[0].upper() if words else ""
    if verb == "STOCK" and len(words) >= 2:
        return await _cmd_stock(session, user, words[1])
    if verb == "RESET" and len(words) >= 2 and words[1].upper() == "PASSWORD":
        return await _cmd_reset_password(session, user)
    return _HELP


@router.post("/webhook", summary="Inbound WhatsApp messages (Meta Cloud API)")
async def receive_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    raw = await request.body()
    if not _signature_ok(raw, request.headers.get("X-Hub-Signature-256", "")):
        log.warning("webhook POST rejected: X-Hub-Signature-256 mismatch")
        return PlainTextResponse("invalid signature", status_code=403)
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        log.warning("webhook POST rejected: body is not JSON")
        return {"received": True}

    handled = 0
    for sender_digits, body in _iter_text_messages(payload):
        try:
            user = await _resolve_sender(session, sender_digits)
            if user is None:
                # Unknown/unregistered number: log + silently drop (no reply —
                # no cost and no hint that this hotline exists).
                log.warning("inbound WhatsApp from unregistered number "
                            "+%s dropped (%r)", sender_digits, body[:60])
                continue
            reply = await _handle_command(session, user, body)
            await wa.send_session_text(
                session, to=user["phone"], body=reply, event_key="webhook_reply",
                related_table="users", related_ref=user["username"],
                created_by=user["username"])
            await session.commit()
            handled += 1
        except Exception:  # noqa: BLE001 — one bad message must not 403 the batch
            await session.rollback()
            log.exception("webhook message from +%s failed", sender_digits)
    return {"received": True, "handled": handled}
