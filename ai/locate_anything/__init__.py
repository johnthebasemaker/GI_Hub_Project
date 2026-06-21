"""
ai/locate_anything — Smart Scan Tier 3 fallback (Phase 8A scaffold).

ARCHITECTURAL CONTRACT
======================
Two-process design — Streamlit + a separate sidecar:

  Streamlit (this venv)
      ↓ HTTP POST  /detect
  Sidecar (separate uvicorn process, optional, opt-in via Admin gate)
      ↓ in-process
  PyTorch + transformers + LocateAnything-3B  (MPS, int8)

PACKAGE LAYOUT
--------------
  __init__.py       — this file. Re-exports the LIGHTWEIGHT client only.
                       Importing this package MUST NOT pull in torch /
                       transformers / fastapi — Streamlit must stay nimble.
  client.py         — pure-stdlib HTTP wrapper used by the Streamlit side.
                       Reads the Admin toggle from app_settings on every call.
                       Circuit breaker + 30s timeout. Never raises to caller.
  server.py         — FastAPI app exposing POST /detect, GET /health.
                       Only imported by the sidecar uvicorn process.
                       Lazy-loads the model on first /detect call.
  model_loader.py   — torch + transformers wrapper. MPS device, int8 quant.
                       Imports torch — NEVER touch from Streamlit-side code.
  requirements.txt  — sidecar-only deps (torch, transformers, fastapi, uvicorn).
                       NOT in the project root requirements.txt — sites
                       without the sidecar never install them.
"""
from .client import (
    is_enabled,
    sidecar_url,
    detect,
    health,
)

__all__ = ["is_enabled", "sidecar_url", "detect", "health"]
