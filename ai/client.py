"""
ai/client.py — Thin Ollama HTTP wrapper (stdlib only)
======================================================
Talks to a local Ollama server (default http://localhost:11434) using
`urllib.request`. No new pip dependencies.

Public API
----------
- ollama_health()           — quick reachability check (cached briefly)
- ollama_generate(model, prompt, ...) → str  — blocking, returns full response
- ollama_stream(model, prompt, ...) → Iterator[str]  — yields token chunks
- MODEL_CODER / MODEL_CHAT / MODEL_EMBED — canonical model ids

Streamlit
---------
This module imports `streamlit` only to use `@st.cache_resource` for the
health-check cache. If Streamlit is missing (e.g. unit tests importing
ai.safety directly), the import is wrapped so the rest of the package still
works.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Iterator, Optional

try:
    import streamlit as st
    _HAS_ST = True
except ImportError:  # pragma: no cover — tests may import safety without streamlit
    _HAS_ST = False


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
def _secret(key: str, default: str) -> str:
    """
    Resolution order: Streamlit Secrets [ollama] block → env var → default.
    Lets one Streamlit-Cloud secrets.toml point at a tunneled local Ollama:

        [ollama]
        host         = "https://your-tailnet.ts.net:11434"
        vision_model = "qwen2.5vl:7b"
    """
    try:
        if _HAS_ST:
            cfg = st.secrets.get("ollama", {})  # type: ignore[attr-defined]
            short = key.replace("OLLAMA_", "").lower()
            val = cfg.get(short)
            if val:
                return str(val)
    except Exception:
        pass
    return os.environ.get(key, default)


OLLAMA_HOST = _secret("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_TIMEOUT_S = 60   # generous; coder + chat on M-series Air can take ~10–30s
OLLAMA_HEALTH_TIMEOUT_S = 2

# Canonical model ids — the three the user has pulled locally.
MODEL_CODER = "qwen2.5-coder:7b"        # used for NL → SQL
MODEL_CHAT  = "llama3.1:8b"             # used for summaries / chat
MODEL_EMBED = "nomic-embed-text:latest" # reserved for future RAG features
# Vision model — used for OCR of handwritten / printed delivery notes.
# Pull with: ollama pull qwen2.5vl:7b   (NOT the same as qwen2.5-coder).
# Override via Streamlit Secrets [ollama] vision_model OR OLLAMA_VISION_MODEL env.
MODEL_VISION = _secret("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")


# ---------------------------------------------------------------------------
# HEALTH CHECK — cached so we don't probe every page render
# ---------------------------------------------------------------------------
def _probe_ollama() -> bool:
    """One blocking GET /api/tags. True if reachable, False otherwise."""
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=OLLAMA_HEALTH_TIMEOUT_S) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return False


if _HAS_ST:
    @st.cache_resource(show_spinner=False)
    def ollama_health() -> bool:
        """Cached health probe. Cleared on Streamlit restart."""
        return _probe_ollama()
else:  # pragma: no cover
    def ollama_health() -> bool:
        return _probe_ollama()


# Module-load probe so importers can branch on availability without paying
# the round-trip on every call. Refreshed by callers via ollama_health().
OLLAMA_AVAILABLE: bool = _probe_ollama()


def list_ollama_models() -> list[str]:
    """
    Return the model ids the Ollama instance reports under /api/tags.
    Empty list on transport failure. No exceptions raised — callers can
    branch on `model in list_ollama_models()`.
    """
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=OLLAMA_HEALTH_TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return [m.get("name", "") for m in body.get("models", []) if m.get("name")]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# BLOCKING GENERATE
# ---------------------------------------------------------------------------
def ollama_generate(
    model: str,
    prompt: str,
    *,
    system: Optional[str] = None,
    temperature: float = 0.2,
    num_predict: int = 512,
    timeout_s: int = OLLAMA_TIMEOUT_S,
) -> str:
    """
    Send a non-streaming generate request. Returns the full assistant text.
    Raises RuntimeError on transport failure — callers should catch and
    surface a friendly message.
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
        },
    }
    if system:
        payload["system"] = system

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise RuntimeError(f"Ollama request failed: {e}") from e

    try:
        return json.loads(body).get("response", "")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Ollama returned malformed JSON: {e}") from e


# ---------------------------------------------------------------------------
# VISION GENERATE — accepts one or more base64-encoded images
# ---------------------------------------------------------------------------
def ollama_vision_generate(
    model: str,
    prompt: str,
    image_b64_list: list[str],
    *,
    system: Optional[str] = None,
    temperature: float = 0.1,
    num_predict: int = 1024,
    timeout_s: int = 120,
) -> str:
    """
    Non-streaming vision request. `image_b64_list` is a list of base64
    strings (NO `data:` prefix, raw base64 payload only). Returns the full
    assistant text. Raises RuntimeError on transport failure.

    Lower default temperature than text-only generate: OCR work wants
    determinism, not creativity. Longer default timeout because vision
    models take noticeably longer per token than the chat/coder models.
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "images": list(image_b64_list or []),
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
        },
    }
    if system:
        payload["system"] = system

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise RuntimeError(f"Ollama vision request failed: {e}") from e

    try:
        return json.loads(body).get("response", "")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Ollama returned malformed JSON: {e}") from e


# ---------------------------------------------------------------------------
# STREAMING GENERATE — yields incremental text chunks
# ---------------------------------------------------------------------------
def ollama_stream(
    model: str,
    prompt: str,
    *,
    system: Optional[str] = None,
    temperature: float = 0.3,
    num_predict: int = 768,
    timeout_s: int = OLLAMA_TIMEOUT_S,
    keep_alive: str = "30m",
) -> Iterator[str]:
    """
    Yields text chunks as they arrive from Ollama. Compatible with
    `st.write_stream(...)`. Raises RuntimeError on transport failure
    BEFORE the first yield; per-chunk errors are swallowed silently to
    avoid breaking a stream mid-display.

    `keep_alive` tells Ollama how long to keep the model + its KV cache
    resident after the request finishes. Default 30 minutes — keeps the
    system-prompt KV state warm so follow-up questions get near-instant
    prompt-eval. Set to "0" to evict immediately (saves RAM, slower).
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "keep_alive": keep_alive,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
        },
    }
    if system:
        payload["system"] = system

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout_s)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise RuntimeError(f"Ollama stream failed: {e}") from e

    try:
        for raw_line in resp:
            if not raw_line:
                continue
            try:
                chunk = json.loads(raw_line.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            piece = chunk.get("response", "")
            if piece:
                yield piece
            if chunk.get("done"):
                break
    finally:
        try:
            resp.close()
        except Exception:
            pass
