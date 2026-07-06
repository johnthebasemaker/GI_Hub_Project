"""
backend/api/ai/router.py — /ai endpoints (Phase AI-0 health + AI-1 assistant).

SSE contract for /ai/assistant (media type text/event-stream):
    data: {"status": "queued"}      only when waiting on the gen semaphore
    data: {"token": "..."}          one per model chunk
    data: {"done": true}            always the final event
    data: {"error": "...", "done": true}   disabled/offline (HTTP still 200 —
                                            SSE consumers read events, not codes)

Feature flags live in app_settings (admin console → Settings): `ai_enabled`
is the master switch, `ai_assistant_enabled` gates this endpoint. Missing
keys default ON — the runtime Ollama health check is the real gate, same
philosophy as legacy AI_ENABLED=True.
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Body, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..db import SessionLocal, get_session
from ..services.ledger import _MD
from . import client as aic
from . import manual_qa

settings_t = _MD.tables["app_settings"]

router = APIRouter(prefix="/ai", tags=["ai"])

_FLAG_DEFAULTS = {"ai_enabled": "1", "ai_assistant_enabled": "1"}


async def _flags(session: AsyncSession) -> dict[str, bool]:
    rows = (await session.execute(
        select(settings_t.c["key"], settings_t.c["value"])
        .where(settings_t.c["key"].in_(list(_FLAG_DEFAULTS))))).all()
    got = {r.key: r.value for r in rows}
    return {k: (got.get(k, d) == "1") for k, d in _FLAG_DEFAULTS.items()}


@router.get("/health", summary="AI layer status (flags + Ollama + model + manual)")
async def ai_health(user: dict = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
    flags = await _flags(session)
    if not (flags["ai_enabled"] and flags["ai_assistant_enabled"]):
        return {"ok": False, "enabled": False,
                "message": "AI features are switched off in Settings."}
    ok, msg = await manual_qa.health()
    return {"ok": ok, "enabled": True, "message": msg,
            "model": aic.MODEL_CHAT}


class AskIn(BaseModel):
    question: str


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.post("/assistant", summary="Hub Assistant — SSE token stream")
async def assistant(body: AskIn = Body(...),
                    user: dict = Depends(get_current_user)):
    role, username = user["role"], user["username"]

    async def gen():
        # Flags need their own session: the request-scoped one would be
        # closed by the time this generator streams.
        async with SessionLocal() as s:
            flags = await _flags(s)
        if not (flags["ai_enabled"] and flags["ai_assistant_enabled"]):
            yield _sse({"error": "AI features are switched off in Settings.",
                        "done": True})
            return

        # Greetings skip health + semaphore entirely (in-process fast path).
        canned = manual_qa.greeting_reply(body.question)
        if canned is not None:
            yield _sse({"token": canned})
            yield _sse({"done": True})
            return

        # Generation semaphore: emit "queued" only when actually waiting so
        # the UI can say "waiting for a free AI slot…" instead of freezing.
        try:
            await asyncio.wait_for(aic.GEN_SEMAPHORE.acquire(), timeout=0.05)
        except (asyncio.TimeoutError, TimeoutError):
            yield _sse({"status": "queued"})
            await aic.GEN_SEMAPHORE.acquire()
        try:
            async for chunk in manual_qa.answer_manual_question(
                    body.question, role, username):
                yield _sse({"token": chunk})
            yield _sse({"done": True})
        finally:
            aic.GEN_SEMAPHORE.release()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
