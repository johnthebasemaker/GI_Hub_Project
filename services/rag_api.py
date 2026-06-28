"""
services/rag_api.py — FastAPI sidecar for the User-Manual / RAG assistant
==========================================================================
v1 is a THIN WRAPPER over `ai.manual_qa` (which streams answers from Ollama
using only the caller's role-slice of USER_MANUAL.md). No vector store yet —
that's a later Workstream C decision. Running this in its own process means the
Streamlit app never blocks on a 30-second LLM prompt-eval; the browser calls
`/api/manual/ask` through nginx and streams tokens straight from here.

This module imports ONLY pure-Python helpers (`ai.manual_qa` → `ai.client`),
never Streamlit — same discipline as `pwa/api.py`.

Run locally:
    uvicorn services.rag_api:app --host 0.0.0.0 --port 8000
Reached in prod via nginx:
    POST https://<domain>/api/manual/ask
    GET  https://<domain>/api/healthz
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from ai.manual_qa import answer_manual_question, health

app = FastAPI(title="GI Hub — Manual / RAG Assistant", version="1.0.0")


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    role: str = "store_keeper"
    username: str = ""


@app.get("/healthz")
def healthz() -> JSONResponse:
    """Liveness + Ollama/model readiness. 200 when ready, 503 otherwise."""
    ok, detail = health()
    return JSONResponse({"ok": ok, "detail": detail}, status_code=200 if ok else 503)


@app.post("/api/manual/ask")
def ask(req: AskRequest) -> StreamingResponse:
    """Stream the assistant's answer token-by-token (text/plain)."""
    def _gen():
        for chunk in answer_manual_question(req.question, req.role, req.username):
            yield chunk

    return StreamingResponse(_gen(), media_type="text/plain; charset=utf-8")


# ===========================================================================
# Meta WhatsApp Business Cloud API — webhook receiver (STUB for the WhatsApp step)
# ---------------------------------------------------------------------------
# Meta calls our public callback URL (nginx → /api/whatsapp/webhook):
#   GET  — one-time verification handshake when you register the callback URL.
#   POST — inbound messages + delivery/read status callbacks.
#
# This stub: completes the handshake, optionally verifies Meta's payload
# signature, logs, and ACKS FAST with 200 (Meta retries aggressively if you
# don't acknowledge within a few seconds). Parsing + routing into the ERP and
# any auto-replies are wired later alongside the Meta sender provider.
#
# Config (env, provided via server .env / Docker secrets — never hardcoded):
#   META_WEBHOOK_VERIFY_TOKEN — a random string you also paste into Meta's
#                               "Verify token" field when registering the webhook.
#   META_APP_SECRET           — the Meta App Secret; enables X-Hub-Signature-256
#                               validation once set.
# ===========================================================================

_log = logging.getLogger("gihub.whatsapp")

META_WEBHOOK_VERIFY_TOKEN = os.environ.get("META_WEBHOOK_VERIFY_TOKEN", "")
META_APP_SECRET = os.environ.get("META_APP_SECRET", "")


def _verify_meta_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Validate Meta's ``X-Hub-Signature-256`` (HMAC-SHA256 over the raw body).

    Returns True when the signature matches, OR when ``META_APP_SECRET`` is not
    yet configured (so the endpoint still works during early setup). Once the
    secret is set, an absent/invalid signature is rejected.
    """
    if not META_APP_SECRET:
        return True  # not configured yet — don't block setup
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(META_APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    received = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, received)


@app.get("/api/whatsapp/webhook")
def whatsapp_verify(request: Request) -> Response:
    """Meta verification handshake — echo ``hub.challenge`` when the token matches."""
    params = request.query_params
    if (
        params.get("hub.mode") == "subscribe"
        and META_WEBHOOK_VERIFY_TOKEN
        and params.get("hub.verify_token") == META_WEBHOOK_VERIFY_TOKEN
    ):
        return PlainTextResponse(params.get("hub.challenge", ""), status_code=200)
    return PlainTextResponse("verification failed", status_code=403)


@app.post("/api/whatsapp/webhook")
async def whatsapp_receive(request: Request) -> JSONResponse:
    """Receive inbound messages + status callbacks. Verify, log, ACK fast."""
    raw = await request.body()
    if not _verify_meta_signature(raw, request.headers.get("X-Hub-Signature-256")):
        return JSONResponse({"status": "bad signature"}, status_code=403)

    # TODO (WhatsApp step): parse entry[].changes[].value.messages and route
    # into the ERP (delivery confirmations, OTP replies, etc.). For now: log + ack.
    _log.info("WhatsApp webhook received: %d bytes", len(raw))
    return JSONResponse({"status": "received"}, status_code=200)
