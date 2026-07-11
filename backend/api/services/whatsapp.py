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
  WHATSAPP_TEMPLATE_NAME (default alert_notification) · WHATSAPP_TEMPLATE_LANG (default en)

DELIVERABILITY: business-initiated messages outside Meta's 24-hour customer-
service window MUST be pre-approved template messages, so alert sends use
`type: "template"` with the alert text as the single {{1}} body parameter
(the template must be approved with one body variable — e.g.
`alert_notification`: "{{1}}"). Set WHATSAPP_TEMPLATE_NAME=hello_world only for
smoke-testing (it takes no variables; we then send it without components).

The two live-HTTP boundaries (`_post_message`, `_upload_media`) are the ONLY
functions that touch the network — service_tests monkeypatch them so CI never
calls Meta.
"""
from __future__ import annotations

import datetime as _dt  # noqa: F401  (imported for parity / future use)
import json
import logging
import os

import httpx
from sqlalchemy import and_, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .ledger import _MD

outbox_t = _MD.tables["whatsapp_outbox"]
users_t = _MD.tables["users"]

_API_VERSION = os.environ.get("WHATSAPP_API_VERSION", "v20.0")
_TIMEOUT_S = 15.0

_log = logging.getLogger("gi.whatsapp")

# Meta error 131030: "Recipient phone number not in allowed list" — the app is
# in Development/Sandbox mode and the destination isn't whitelisted under
# WhatsApp → API Setup. Mapped to a user-facing message so the frontend never
# has to surface the raw Graph JSON blob.
SANDBOX_NOT_WHITELISTED = 131030

_FRIENDLY_ERRORS: dict[int, str] = {
    SANDBOX_NOT_WHITELISTED: (
        "WhatsApp delivery failed: Destination number is not whitelisted in the "
        "Meta Developer Sandbox console. Add the recipient under WhatsApp → "
        "API Setup → allowed recipient list (or switch the app to Live mode)."),
}


def _graph_error(status: int, body_text: str) -> dict:
    """Normalise a non-2xx Graph response into {"ok": False, "error", "code"}.

    Known codes get a user-facing message first; the raw Meta detail is kept
    (bracketed, truncated) so the whatsapp_outbox row stays fully diagnostic."""
    code = None
    detail = ""
    try:
        err = (json.loads(body_text or "{}").get("error") or {})
        code = err.get("code")
        detail = (err.get("error_data") or {}).get("details") or err.get("message") or ""
    except (ValueError, TypeError):
        pass
    raw = f"{status}{f' (#{code})' if code else ''}: {detail or (body_text or '')[:300]}"
    friendly = _FRIENDLY_ERRORS.get(code)
    if code == SANDBOX_NOT_WHITELISTED:
        _log.warning("Meta sandbox restriction (#131030): recipient not in the "
                     "allowed list — send recorded as failed, app flow continues")
    else:
        _log.warning("WhatsApp send failed: %s", raw[:300])
    return {"ok": False, "code": code, "error": f"{friendly} [{raw}]" if friendly else raw}


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
        return _graph_error(r.status_code, r.text)
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
        return _graph_error(r.status_code, r.text)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ── payload builders ─────────────────────────────────────────────────────────
def _meta_to(raw: str) -> str:
    """Meta's `to` field: digits only (strips the canonical '+', spaces, dashes).
    Numbers are STORED as strict E.164 with a leading '+' (project-wide rule);
    this is the one boundary where the '+' is dropped for the Graph API."""
    return "".join(ch for ch in (raw or "") if ch.isdigit())


def _template_name() -> str:
    return os.environ.get("WHATSAPP_TEMPLATE_NAME", "alert_notification").strip()


def _template_lang() -> str:
    return os.environ.get("WHATSAPP_TEMPLATE_LANG", "en").strip()


def _text_payload(to: str, body: str) -> dict:
    """Alert sends use an approved TEMPLATE (deliverable outside the 24h window).

    The template is expected to carry ONE body variable ({{1}}) that receives the
    alert text. `hello_world` (Meta's built-in smoke-test template) takes no
    variables, so it is sent without components. Template body params may not
    contain newlines/tabs (Meta #132000) — they are flattened to spaces.
    """
    name = _template_name()
    tpl: dict = {"name": name, "language": {"code": _template_lang()}}
    if name != "hello_world":
        flat = " ".join((body or "").split())[:1024]
        tpl["components"] = [{"type": "body",
                              "parameters": [{"type": "text", "text": flat}]}]
    return {"messaging_product": "whatsapp", "to": _meta_to(to), "type": "template",
            "template": tpl}


def _doc_payload(to: str, media_id: str, filename: str, caption: str | None) -> dict:
    doc = {"id": media_id, "filename": filename}
    if caption:
        doc["caption"] = caption[:1024]
    return {"messaging_product": "whatsapp", "to": _meta_to(to), "type": "document", "document": doc}


# ── reusable templates (Phase 7c) ────────────────────────────────────────────
# A small set of purpose-built, variable-driven templates covers every alert in
# the app. Each logical key maps to (env override, default Meta template name).
# The Meta templates must be created + approved in WhatsApp Manager; see the
# `deploy/.env.example` block and docs/PROJECT_STATUS.md for the exact bodies.
#   action_required / status_update / critical_alert → TWO body vars ({{1}} {{2}})
#   otp_code                                          → ONE body var  ({{1}})
_TEMPLATES: dict[str, tuple[str, str]] = {
    "action_required": ("WHATSAPP_TPL_ACTION", "gi_action_required"),
    "status_update": ("WHATSAPP_TPL_STATUS", "gi_status_update"),
    "critical_alert": ("WHATSAPP_TPL_CRITICAL", "gi_critical_alert"),
    "otp_code": ("WHATSAPP_TPL_OTP", "gi_otp_code"),
    # Evening digest (Phase 6): {{1}} = header (date + count), {{2}} = the
    # compiled bullet list. Must be approved in the WABA's template language.
    "evening_summary": ("WHATSAPP_TPL_SUMMARY", "gi_evening_summary"),
}


def _resolve_template(key: str) -> str:
    """Logical key → approved Meta template name (env-overridable)."""
    env, default = _TEMPLATES.get(key, ("", key))
    return (os.environ.get(env, "") or default).strip()


def _tpl_param(value) -> dict:
    # Template body params may not contain newlines/tabs (Meta #132000) and may
    # not be empty/whitespace — flatten to spaces, cap at 1024, fall back to '-'.
    flat = " ".join(str(value if value is not None else "").split())[:1024]
    return {"type": "text", "text": flat or "-"}


def _template_payload(to: str, name: str, variables: list) -> dict:
    tpl: dict = {"name": name, "language": {"code": _template_lang()}}
    params = [_tpl_param(v) for v in (variables or [])]
    if name != "hello_world" and params:
        tpl["components"] = [{"type": "body", "parameters": params}]
    return {"messaging_product": "whatsapp", "to": _meta_to(to), "type": "template", "template": tpl}


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
    if not _meta_to(to):  # no digits at all → nothing Meta could deliver to
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


async def send_template(session: AsyncSession, *, to: str, template_key: str,
                        variables: list, event_key: str,
                        related_table: str | None = None, related_ref=None,
                        created_by: str = "system") -> dict:
    """Send one of the reusable templates (action_required / status_update /
    critical_alert / otp_code) with positional body variables."""
    name = _resolve_template(template_key)
    payload = _template_payload(to, name, list(variables or []))
    preview = " · ".join(str(v) for v in (variables or []) if v is not None)
    return await _record_and_send(session, to=to, payload=payload, preview=preview,
                                  event_key=event_key, related_table=related_table,
                                  related_ref=related_ref, created_by=created_by)


async def send_session_text(session: AsyncSession, *, to: str, body: str, event_key: str,
                            related_table: str | None = None, related_ref=None,
                            created_by: str = "system") -> dict:
    """Send a FREE-FORM text message (`type: "text"`, not a template).

    Only deliverable inside Meta's 24-hour customer-service window — i.e. as a
    REPLY to a user-initiated inbound message (the webhook flows). Business-
    initiated alerts must keep using the approved templates via send_template.
    """
    payload = {"messaging_product": "whatsapp", "to": _meta_to(to), "type": "text",
               "text": {"preview_url": False, "body": (body or "")[:4096]}}
    return await _record_and_send(session, to=to, payload=payload, preview=body,
                                  event_key=event_key, related_table=related_table,
                                  related_ref=related_ref, created_by=created_by)


async def send_otp(session: AsyncSession, *, to: str, code: str,
                   created_by: str = "system") -> dict:
    """Send a phone-verification code via the otp_code template. The outbox
    `body` preview is redacted (the code still lives in payload_json, which is
    the message actually sent — short-lived, admin-only)."""
    name = _resolve_template("otp_code")
    payload = _template_payload(to, name, [code])
    return await _record_and_send(session, to=to, payload=payload,
                                  preview="verification code (redacted)",
                                  event_key="otp_verification", related_table="phone_otp",
                                  created_by=created_by)


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


async def resolve_numbers(session: AsyncSession, *, recipient_user: str | None = None,
                          recipient_role: str | None = None, recipient_site: str | None = None,
                          recipient_warehouse: str | None = None, limit: int = 25) -> list[str]:
    """Resolve the same recipient descriptor the in-app notifier uses (a specific
    user, or a role optionally narrowed by site/warehouse) to WhatsApp numbers.
    De-duplicated, capped, and never raises for a missing number."""
    nums: list[str] = []
    if recipient_user:
        n = (await session.execute(select(users_t.c["Phone_Number"])
             .where(users_t.c["username"] == recipient_user))).scalar_one_or_none()
        if n and n.strip():
            nums.append(n.strip())
    elif recipient_role or recipient_warehouse:
        conds = [users_t.c["Phone_Number"].isnot(None)]
        if recipient_role:
            conds.append(users_t.c["role"] == recipient_role)
        if recipient_site:
            conds.append(func.coalesce(users_t.c["Site_ID"], "") == recipient_site)
        if recipient_warehouse:
            conds.append(func.coalesce(users_t.c["Warehouse_ID"], "") == recipient_warehouse)
        rows = (await session.execute(select(users_t.c["Phone_Number"])
                .where(and_(*conds)).limit(limit * 2))).scalars().all()
        nums = [r.strip() for r in rows if r and r.strip()]
        # Opt-in catch-all: if a ROLE/warehouse broadcast matches nobody with a
        # phone on file, fall back to WHATSAPP_ESCALATION_TO (when configured) so
        # the alert still reaches someone — same behaviour as hod_numbers(). No
        # fallback for a user-targeted message (wrong-person risk).
        if not nums and _escalation_to():
            nums = [_escalation_to()]
    seen: set[str] = set()
    out: list[str] = []
    for n in nums:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out[:limit]
