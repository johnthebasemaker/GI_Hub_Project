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

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
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
