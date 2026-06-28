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

import logging

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from ai.manual_qa import answer_manual_question, health
from services.whatsapp_webhook import (
    handle_webhook_payload,
    verify_signature,
    verify_subscription,
)

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
# Meta WhatsApp Business Cloud API — webhook receiver
# ---------------------------------------------------------------------------
# Meta calls our public callback URL (nginx → /api/whatsapp/webhook):
#   GET  — one-time verification handshake when you register the callback URL.
#   POST — inbound messages + delivery/read status callbacks.
#
# The parsing/signature/routing logic lives in services/whatsapp_webhook.py
# (FastAPI-free, unit-tested). These endpoints stay thin: verify, hand off,
# and ACK FAST with 200 — Meta retries aggressively if you don't ack within a
# few seconds, so the router stub must never block. Sending replies is wired
# later alongside the Meta sender provider.
#
# Config (env, via server .env / Docker secrets — never hardcoded):
#   META_WEBHOOK_VERIFY_TOKEN — random string also pasted into Meta's "Verify
#                               token" field when registering the webhook.
#   META_APP_SECRET           — Meta App Secret; enables X-Hub-Signature-256
#                               validation once set.
# ===========================================================================

_log = logging.getLogger("gihub.whatsapp")


@app.get("/api/whatsapp/webhook")
def whatsapp_verify(request: Request) -> Response:
    """Meta verification handshake — echo ``hub.challenge`` when the token matches."""
    params = request.query_params
    if verify_subscription(params.get("hub.mode"), params.get("hub.verify_token")):
        return PlainTextResponse(params.get("hub.challenge", ""), status_code=200)
    return PlainTextResponse("verification failed", status_code=403)


@app.post("/api/whatsapp/webhook")
async def whatsapp_receive(request: Request) -> JSONResponse:
    """Receive inbound messages + status callbacks. Verify, route, ACK fast."""
    raw = await request.body()
    if not verify_signature(raw, request.headers.get("X-Hub-Signature-256")):
        return JSONResponse({"status": "bad signature"}, status_code=403)

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    # Parse + run the (stub) router. Pure/non-blocking, so we can ack inline.
    summary = handle_webhook_payload(payload)
    _log.info(
        "WhatsApp webhook: %d msg(s), %d status(es), %d reply(ies) planned",
        summary["messages"], summary["statuses"], summary["replies_planned"],
    )
    return JSONResponse({"status": "received", **summary}, status_code=200)
