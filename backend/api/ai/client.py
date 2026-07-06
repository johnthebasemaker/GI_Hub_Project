"""
backend/api/ai/client.py — async Ollama client (port of legacy ai/client.py).

Talks to a local Ollama server over HTTP with httpx (already a backend dep).
Awaiting Ollama costs the event loop nothing — the heavy compute lives in the
Ollama process, not here. A module-level semaphore caps CONCURRENT generations
so simultaneous users queue politely instead of thrashing the model host
(CPX42: one warm 7-8B model at a time — user ruling 2026-07-06).

Test seam: routes and manual_qa call these functions through the module
object (`from . import client as aic; aic.stream(...)`) so service_tests can
monkeypatch `health` / `list_models` / `stream` without a live Ollama.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import AsyncIterator, Optional

import httpx

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Model registry — same canonical ids as legacy ai/client.py, env-overridable.
MODEL_CHAT = os.environ.get("GI_AI_CHAT_MODEL", "llama3.1:8b")
MODEL_CODER = os.environ.get("GI_AI_CODER_MODEL", "qwen2.5-coder:7b")
MODEL_VISION = os.environ.get("GI_AI_VISION_MODEL", "qwen2.5vl:7b")

HEALTH_TIMEOUT_S = 2.0
GEN_TIMEOUT_S = float(os.environ.get("GI_AI_TIMEOUT_S", "240"))  # 7B cold start
KEEP_ALIVE = "30m"  # hold the KV cache warm between calls (legacy behavior)

# At most N generations in flight; the rest wait (the /ai endpoints emit a
# "queued" SSE event while waiting so the UI never looks frozen).
GEN_SEMAPHORE = asyncio.Semaphore(int(os.environ.get("GI_AI_CONCURRENCY", "2")))


async def health() -> bool:
    """True when the Ollama server answers /api/tags within 2s."""
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_S) as c:
            r = await c.get(f"{OLLAMA_HOST}/api/tags")
            return r.status_code == 200
    except Exception:
        return False


async def list_models() -> list[str]:
    """Installed model names ([] when unreachable — callers treat as unknown)."""
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_S) as c:
            r = await c.get(f"{OLLAMA_HOST}/api/tags")
            r.raise_for_status()
            return [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        return []


def _payload(model: str, prompt: str, *, system: Optional[str], temperature: float,
             num_predict: int, images: Optional[list[str]] = None) -> dict:
    body: dict = {
        "model": model, "prompt": prompt, "keep_alive": KEEP_ALIVE,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    if system:
        body["system"] = system
    if images:
        body["images"] = images  # base64, no data: prefix (Ollama contract)
    return body


async def generate(model: str, prompt: str, *, system: Optional[str] = None,
                   temperature: float = 0.2, num_predict: int = 512,
                   images: Optional[list[str]] = None,
                   timeout_s: float = GEN_TIMEOUT_S) -> str:
    """One blocking completion. Raises RuntimeError on transport failure."""
    body = _payload(model, prompt, system=system, temperature=temperature,
                    num_predict=num_predict, images=images)
    body["stream"] = False
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            r = await c.post(f"{OLLAMA_HOST}/api/generate", json=body)
            r.raise_for_status()
            return r.json().get("response", "")
    except Exception as e:  # normalized like legacy — caller shows a friendly msg
        raise RuntimeError(f"Ollama generate failed: {type(e).__name__}: {e}") from e


async def stream(model: str, prompt: str, *, system: Optional[str] = None,
                 temperature: float = 0.2, num_predict: int = 512,
                 timeout_s: float = GEN_TIMEOUT_S) -> AsyncIterator[str]:
    """Yield response chunks as they arrive. Raises RuntimeError before the
    first chunk on connection failure; mid-stream errors end the stream
    quietly (legacy contract — never break a half-rendered answer)."""
    body = _payload(model, prompt, system=system, temperature=temperature,
                    num_predict=num_predict)
    body["stream"] = True
    try:
        client = httpx.AsyncClient(timeout=timeout_s)
    except Exception as e:  # pragma: no cover
        raise RuntimeError(f"Ollama unreachable: {e}") from e
    try:
        async with client.stream("POST", f"{OLLAMA_HOST}/api/generate",
                                 json=body) as r:
            if r.status_code != 200:
                raise RuntimeError(f"Ollama returned HTTP {r.status_code}")
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                try:
                    piece = json.loads(line)
                except ValueError:
                    continue
                chunk = piece.get("response", "")
                if chunk:
                    yield chunk
                if piece.get("done"):
                    break
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        raise RuntimeError(f"Ollama unreachable at {OLLAMA_HOST}: {e}") from e
    finally:
        await client.aclose()
