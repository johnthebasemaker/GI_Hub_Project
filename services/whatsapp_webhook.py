"""
services/whatsapp_webhook.py — Meta WhatsApp Cloud API webhook logic (pure)
===========================================================================
Pure, dependency-light helpers for the inbound webhook: signature verification,
the GET handshake check, payload parsing, and the (stubbed) response router.

Deliberately FastAPI-free so it can be unit-tested with plain Python (and from
bug_check.py) without importing the web framework. `services/rag_api.py` wires
these into the HTTP endpoints.

Meta payload shape (text message)::

    {
      "object": "whatsapp_business_account",
      "entry": [{
        "id": "<WABA_ID>",
        "changes": [{
          "field": "messages",
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "...", "phone_number_id": "<OUR_ID>"},
            "contacts": [{"profile": {"name": "Ahmed"}, "wa_id": "966500000000"}],
            "messages": [{
              "from": "966500000000",
              "id": "wamid.ABC...",
              "timestamp": "1719600000",
              "type": "text",
              "text": {"body": "RECEIVED DN-1042"}
            }]
          }
        }]
      }]
    }

Status callbacks (sent/delivered/read/failed) arrive under ``value.statuses``
instead of ``value.messages`` — parsed separately and NOT treated as inbound
user messages.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from dataclasses import dataclass, field

_log = logging.getLogger("gihub.whatsapp")

# Config — provided via the server's .env / Docker secrets. Read at import time;
# empty defaults keep the endpoints functional during early setup.
META_APP_SECRET = os.environ.get("META_APP_SECRET", "")
META_WEBHOOK_VERIFY_TOKEN = os.environ.get("META_WEBHOOK_VERIFY_TOKEN", "")


# ---------------------------------------------------------------------------
# Security / handshake
# ---------------------------------------------------------------------------
def verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Validate Meta's ``X-Hub-Signature-256`` (HMAC-SHA256 over the raw body).

    Returns True when the signature matches, OR when ``META_APP_SECRET`` is not
    yet configured (so the endpoint works during early setup). Once the secret
    is set, an absent/invalid signature is rejected.
    """
    if not META_APP_SECRET:
        return True  # not configured yet — don't block setup
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(META_APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    received = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, received)


def verify_subscription(mode: str | None, token: str | None) -> bool:
    """GET handshake check: Meta sends hub.mode=subscribe + hub.verify_token."""
    return bool(
        mode == "subscribe"
        and META_WEBHOOK_VERIFY_TOKEN
        and token == META_WEBHOOK_VERIFY_TOKEN
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
@dataclass
class InboundMessage:
    """One normalised inbound WhatsApp message."""
    wa_message_id: str          # "wamid.…" — use to de-dup / mark read / reply
    from_phone: str             # sender wa_id, digits only (e.g. "966500000000")
    sender_name: str            # WhatsApp profile name, if Meta included it
    type: str                   # text | button | interactive | image | location | …
    text: str                   # best-effort human text (body / button title / caption)
    timestamp: str              # epoch seconds as sent by Meta
    phone_number_id: str        # OUR receiving number id — needed to send a reply
    raw: dict = field(default_factory=dict, repr=False)  # original message object


def _extract_text(message: dict, mtype: str) -> str:
    """Pull the most meaningful human-readable text out of a message object."""
    if mtype == "text":
        return ((message.get("text") or {}).get("body") or "").strip()
    if mtype == "button":  # template quick-reply button tap
        return ((message.get("button") or {}).get("text") or "").strip()
    if mtype == "interactive":
        inter = message.get("interactive") or {}
        itype = inter.get("type", "")
        if itype == "button_reply":
            return ((inter.get("button_reply") or {}).get("title") or "").strip()
        if itype == "list_reply":
            return ((inter.get("list_reply") or {}).get("title") or "").strip()
        return ""
    # media types (image/document/audio/video) — surface a caption if present
    media = message.get(mtype)
    if isinstance(media, dict) and media.get("caption"):
        return str(media["caption"]).strip()
    return ""


def parse_inbound_messages(payload: dict) -> list[InboundMessage]:
    """Extract every inbound USER message from a webhook payload.

    Ignores status callbacks (``value.statuses``). Tolerant of partial/legacy
    payloads — never raises on a missing key.
    """
    out: list[InboundMessage] = []
    for entry in (payload.get("entry") or []):
        for change in (entry.get("changes") or []):
            value = change.get("value") or {}
            metadata = value.get("metadata") or {}
            phone_number_id = metadata.get("phone_number_id", "")

            # wa_id -> profile name
            names: dict[str, str] = {}
            for contact in (value.get("contacts") or []):
                names[contact.get("wa_id", "")] = (
                    (contact.get("profile") or {}).get("name", "")
                )

            for message in (value.get("messages") or []):
                mtype = message.get("type", "")
                frm = message.get("from", "")
                out.append(InboundMessage(
                    wa_message_id=message.get("id", ""),
                    from_phone=frm,
                    sender_name=names.get(frm, ""),
                    type=mtype,
                    text=_extract_text(message, mtype),
                    timestamp=message.get("timestamp", ""),
                    phone_number_id=phone_number_id,
                    raw=message,
                ))
    return out


def parse_statuses(payload: dict) -> list[dict]:
    """Extract delivery/read/failed status callbacks (for the WhatsApp log)."""
    out: list[dict] = []
    for entry in (payload.get("entry") or []):
        for change in (entry.get("changes") or []):
            for status in ((change.get("value") or {}).get("statuses") or []):
                out.append({
                    "wa_message_id": status.get("id", ""),
                    "recipient": status.get("recipient_id", ""),
                    "status": status.get("status", ""),      # sent|delivered|read|failed
                    "timestamp": status.get("timestamp", ""),
                })
    return out


# ---------------------------------------------------------------------------
# Response router — STUB (sending is wired later via the worker)
# ---------------------------------------------------------------------------
def route_inbound_message(msg: InboundMessage) -> str | None:
    """Decide what to do with one inbound message.

    Returns an OPTIONAL reply string. NOTE: this stub does NOT send anything —
    actually dispatching a reply is the WhatsApp-sender step (worker + Meta
    Graph API). Returning the text now lets us unit-test the routing decisions
    and makes the send a one-line plug-in later.

    The keyword skeleton below is a placeholder for the real ERP hooks
    (delivery confirmations, PR/PO status lookups, OTP flows) — each TODO maps
    to a database.py helper we'll call in the WhatsApp step.
    """
    body = (msg.text or "").strip()
    lowered = body.lower()

    if not body:
        return None  # non-text (image/location/etc) — nothing to route yet

    if lowered in {"hi", "hello", "start", "help", "menu"}:
        return (
            "👋 GI Hub. Reply *RECEIVED <DN#>* to confirm a delivery, "
            "or *STATUS <PR#>* to check a request."
        )

    # TODO (WhatsApp step): route into the ERP via database.py, e.g.
    #   if lowered.startswith("received "): confirm DN <id> for this sender's site
    #   if lowered.startswith("status "):   look up PR/PO status and reply
    #   OTP / supervisor confirmations, etc.
    _log.info(
        "Inbound WhatsApp from %s (%s) [%s]: %r — no route matched",
        msg.from_phone, msg.sender_name or "?", msg.type, body,
    )
    return None


def handle_webhook_payload(payload: dict) -> dict:
    """Top-level entry: parse messages + statuses, run the router on each.

    Pure/side-effect-light (logging + the stub router only). Returns a small
    summary dict — handy for tests and for the HTTP layer's ack body.
    """
    messages = parse_inbound_messages(payload)
    statuses = parse_statuses(payload)
    replies = 0
    for m in messages:
        if route_inbound_message(m) is not None:
            replies += 1
    return {
        "messages": len(messages),
        "statuses": len(statuses),
        "replies_planned": replies,
    }
