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
import logging
import os
from typing import AsyncIterator, Optional

import httpx

logger = logging.getLogger("gi.ai.client")

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Model registry — same canonical ids as legacy ai/client.py, env-overridable.
MODEL_CHAT = os.environ.get("GI_AI_CHAT_MODEL", "llama3.1:8b")
MODEL_CODER = os.environ.get("GI_AI_CODER_MODEL", "qwen2.5-coder:7b")
MODEL_VISION = os.environ.get("GI_AI_VISION_MODEL", "qwen2.5vl:7b")
# Phase 17 — the System One router (ai/system_one.py). Ruling Q17-1 amends "one
# warm model" to one warm GENERATION model plus ONE pinned router of <= 1.5 GB
# (raised from 1 GB by the operator after the spike, deviation D1). The spike
# (PROPOSED_PHASE17_PLAN.md §9) found 0.5b fails routing (0.72 macro) and 1.5b
# passes (0.95) at 1.35 GB resident. Switch with this variable or the
# `ai_router_enabled` setting; nothing else depends on which model it is.
MODEL_ROUTER = os.environ.get("GI_AI_ROUTER_MODEL", "qwen2.5:1.5b")
# Phase 19d — the semantic safety signal (ai/semantic.py). An EMBEDDING model,
# not a generation model, so it never takes the router's prompt cache — but it
# is 578 MB resident beside the router's 1.35 GB, past Q17-1's 1.5 GB router
# budget. That is why `ai_semantic_guard` ships OFF (ruling pending, Q19-4).
MODEL_EMBED = os.environ.get("GI_AI_EMBED_MODEL", "nomic-embed-text")
EMBED_TIMEOUT_S = float(os.environ.get("GI_AI_EMBED_TIMEOUT_S", "2.0"))

HEALTH_TIMEOUT_S = 2.0
GEN_TIMEOUT_S = float(os.environ.get("GI_AI_TIMEOUT_S", "300"))  # 7B cold start

# ⚠️ VISION IS NOT CHAT, AND ONE TIMEOUT FOR BOTH WAS THE BUG.
#
# A chat answer is a few hundred tokens over a text prompt. Reading a full page
# of tabular handwriting is a few THOUSAND tokens over an image prompt, and on
# the standing one-warm-7B-model box that is a different order of magnitude of
# wall clock. Measured here on the real files, 2026-09-01:
#
#   consumption log JPEG, 1024 output tokens ......... 189 s   (~5.4 tok/s)
#   the same page needs ~2,500-3,500 tokens to finish  460-650 s
#
# So the old single 240 s ceiling could not physically be met by the one job
# that most needed it: every full-page read died on `httpx.ReadTimeout` at 240 s
# with the model still generating, and the operator saw "the vision model is not
# reachable" about a model that was reachable and working.
#
# Vision therefore gets its own, much longer budget. This is NOT a licence to
# hang a request: nothing user-facing awaits it. Every vision call in this
# codebase runs inside a JOB WORKER (`ai/jobs.py`, `ai/form_jobs.py`) that the
# browser polls, so the only thing a long timeout costs is a row sitting in
# `ai_jobs` at status='running' for longer. A timeout that fires BEFORE the
# model finishes costs the whole read.
VISION_TIMEOUT_S = float(os.environ.get("GI_AI_VISION_TIMEOUT_S", "900"))

KEEP_ALIVE = "30m"  # hold the KV cache warm between calls (legacy behavior)

# At most N generations in flight; the rest wait (the /ai endpoints emit a
# "queued" SSE event while waiting so the UI never looks frozen).
GEN_SEMAPHORE = asyncio.Semaphore(int(os.environ.get("GI_AI_CONCURRENCY", "2")))


# ── ⚠️ THE CLOUD SEAM (Phase 9d, ruling Q7) ─────────────────────────────────
# Local `qwen2.5vl:7b` reads the consumption form today. Its weakest task is a
# handwritten DIGIT — 4/9, 1/7, 3/8 and a lost decimal point are the failure
# modes — and slice 9c's form is built to make that as easy as possible
# (pre-printed names, a QR instead of header text, one boxed figure per row).
# If UAT still shows it is not good enough, the escalation is NOT a bigger local
# model: one warm 6 GB VLM is the standing on-box ruling, and a second would
# either cold-start on every switch or sit resident beside it.
#
# The escalation is this one function. Set GI_AI_VISION_PROVIDER=anthropic and
# GI_AI_VISION_API_KEY, and vision calls route to a cloud VLM instead. Nothing
# else in the pipeline changes: `ocr_form.py` calls `vision_json()` and neither
# knows nor cares which answered. Deliberately a runtime switch and not a code
# branch to be written later — a seam that has never been exercised is a plan,
# not a seam.
VISION_PROVIDER = os.environ.get("GI_AI_VISION_PROVIDER", "ollama").lower()
VISION_API_KEY = os.environ.get("GI_AI_VISION_API_KEY", "")
VISION_CLOUD_MODEL = os.environ.get("GI_AI_VISION_CLOUD_MODEL",
                                    "claude-sonnet-4-5")
VISION_CLOUD_URL = os.environ.get("GI_AI_VISION_CLOUD_URL",
                                  "https://api.anthropic.com/v1/messages")

# ── ⚠️ FALLBACK IS A SECOND, SEPARATE DECISION FROM PROVIDER (Phase 11, Q5) ──
#
# `GI_AI_VISION_PROVIDER=anthropic` says "read every page in the cloud".
# `GI_AI_VISION_CLOUD_FALLBACK=1` says "read locally, and only if the local
# engine is DOWN send the page out". They are deliberately two switches and not
# one, because they are two different agreements about company data and an
# operator must be able to grant one without the other — and be able to see, in
# an env file, which one they granted.
#
# **DEFAULT OFF.** Vision is the only lane allowed to do this at all: the pages
# are consumption sheets and delivery notes. `/ai/query`, `/ai/nl-search`,
# `/ai/insights` and `/ai/eod-summary` carry live stock rows and generated SQL
# over the ERP and have NO cloud path, by ruling (Q5, 2026-09-02).
VISION_CLOUD_FALLBACK = os.environ.get("GI_AI_VISION_CLOUD_FALLBACK",
                                       "0").strip().lower() in ("1", "true", "yes")


class VisionTimeout(RuntimeError):
    """The model was reachable and still generating when the budget ran out."""


class VisionUnavailable(RuntimeError):
    """The engine could not be reached, or died mid-generation."""


def cloud_vision_ready() -> bool:
    """True when a cloud vision call could actually be made right now.

    ⚠️ THE KEY IS THE GATE, AND ITS ABSENCE IS SILENT ON PURPOSE. An operator
    who has not obtained a key must get the local behaviour they had before,
    not a stack trace and not a page sitting in a queue — so every path here
    degrades to Ollama rather than failing. `/ai/health` is where a
    misconfiguration is meant to become visible; somebody's photograph is not.
    """
    return bool(VISION_API_KEY) and bool(VISION_CLOUD_URL)


def cloud_fallback_ready() -> bool:
    """True when a LOCAL failure is permitted to leave the network."""
    return VISION_CLOUD_FALLBACK and cloud_vision_ready()


def vision_provider() -> str:
    """Which engine vision calls will reach FIRST.

    Reports `ollama` when a cloud provider is named but unconfigured, rather
    than failing at the first upload: a missing key is an operator mistake that
    should surface in /ai/health, not in somebody's photograph.
    """
    if VISION_PROVIDER == "anthropic" and cloud_vision_ready():
        return "anthropic"
    return "ollama"


def provider_of(model_id: str) -> str:
    """Which engine actually answered, from the model id it returned.

    ⚠️ ASKED OF THE ANSWER, NOT OF THE CONFIGURATION. `vision_provider()`
    describes intent; after a fallback the intent and the fact differ, and the
    one worth recording beside a quantity is the fact. "Which engine read this"
    is the first question asked when a number is disputed.
    """
    return "anthropic" if model_id == VISION_CLOUD_MODEL else "ollama"


async def vision_json(prompt: str, *, system: str, image_b64: str,
                      num_predict: int = 1400,
                      timeout_s: float = VISION_TIMEOUT_S,
                      image_tokens: Optional[int] = None,
                      temperature: float = 0.0) -> tuple[str, str]:
    """One vision completion. Returns `(raw_text, model_id)`.

    The model id comes back with the text because it is stored on the entry:
    "which engine read this" is the first question asked when a quantity is
    disputed, and it is unanswerable later if nobody recorded it.
    """
    # Rule 17, the LAST wall: every route that queues a vision job already
    # refuses in Practice (practice.assert_ocr_available). This catches the
    # route somebody adds next year without remembering to.
    from ..config import is_practice
    if is_practice():
        raise RuntimeError("vision is switched off in Practice (ruling Q5)")
    if vision_provider() == "anthropic":
        return await _cloud_vision(prompt, system=system, image_b64=image_b64,
                                   num_predict=num_predict, timeout_s=timeout_s)

    try:
        async with GEN_SEMAPHORE:
            text = await generate(
                MODEL_VISION, prompt, system=system, temperature=temperature,
                num_predict=num_predict, images=[image_b64],
                timeout_s=timeout_s,
                num_ctx=vision_num_ctx(num_predict, image_tokens))
        return text, MODEL_VISION
    except VisionTimeout:
        # ⚠️ A TIMEOUT NEVER FALLS BACK, AND THIS IS THE IMPORTANT HALF OF THE
        # RULE. A read timeout means the local model was reachable, healthy and
        # STILL GENERATING — the page is slow, not the server broken (a five-row
        # form measured at 399 s here). Sending that page to a third party
        # because our own stopwatch ran out would make a data-egress decision on
        # the basis of impatience, and it would do it most often for exactly the
        # dense, information-rich pages the operator would least want to send.
        # The named "raise the budget" error goes back unchanged.
        raise
    except VisionUnavailable as e:
        if not cloud_fallback_ready():
            raise
        logger.warning(
            "local vision unavailable (%s) — falling back to %s. The page is "
            "leaving the network because GI_AI_VISION_CLOUD_FALLBACK is on.",
            e, VISION_CLOUD_MODEL)
        return await _cloud_vision(prompt, system=system, image_b64=image_b64,
                                   num_predict=num_predict, timeout_s=timeout_s,
                                   after=str(e))


async def _cloud_vision(prompt: str, *, system: str, image_b64: str,
                        num_predict: int, timeout_s: float,
                        after: str = "") -> tuple[str, str]:
    """One cloud vision completion. Never called unless a key is configured."""
    body = {
        "model": VISION_CLOUD_MODEL,
        "max_tokens": num_predict,
        "system": system,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": "image/jpeg",
                                         "data": image_b64}},
            {"type": "text", "text": prompt}]}],
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            r = await c.post(VISION_CLOUD_URL, json=body, headers={
                "x-api-key": VISION_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"})
            r.raise_for_status()
            parts = r.json().get("content", [])
            text = "".join(p.get("text", "") for p in parts
                           if p.get("type") == "text")
            return text, VISION_CLOUD_MODEL
    except Exception as e:
        # When this was a fallback, BOTH failures are reported. Saying only
        # "cloud vision failed" would send an operator to check a cloud account
        # for a problem that started with their own Ollama being down.
        tail = f" (after the local engine failed: {after})" if after else ""
        raise RuntimeError(
            f"Cloud vision failed: {type(e).__name__}: {e}{tail}") from e


async def health() -> bool:
    """True when the Ollama server answers /api/tags within 2s."""
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_S) as c:
            r = await c.get(f"{OLLAMA_HOST}/api/tags")
            return r.status_code == 200
    except Exception:
        return False


async def embed(texts: list[str], *, model: Optional[str] = None,
                timeout_s: Optional[float] = None) -> list[list[float]]:
    """Embedding vectors for `texts`, one per text, via Ollama /api/embed.

    Raises on any failure. The one caller (ai/semantic.py) turns every failure
    into "no signal", so the request goes on exactly as it would without it."""
    async with httpx.AsyncClient(timeout=timeout_s or EMBED_TIMEOUT_S) as c:
        r = await c.post(f"{OLLAMA_HOST}/api/embed",
                         json={"model": model or MODEL_EMBED, "input": texts})
        r.raise_for_status()
        out = r.json().get("embeddings") or []
    if len(out) != len(texts):
        raise RuntimeError(f"embed: {len(out)} vectors for {len(texts)} texts")
    return out


async def list_models() -> list[str]:
    """Installed model names ([] when unreachable — callers treat as unknown)."""
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_S) as c:
            r = await c.get(f"{OLLAMA_HOST}/api/tags")
            r.raise_for_status()
            return [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        return []


# ⚠️ THE CONTEXT WINDOW IS 4,096 BY DEFAULT, AND THE MODEL CARD SAYS 128,000.
#
# `ollama show qwen2.5vl:7b` reports a 128k context length. That is the model's
# TRAINING context. What the runner actually allocates is `num_ctx`, and Ollama
# defaults it to 4,096 regardless — visible in its own log as
# `llama_context: n_ctx = 4096` next to `n_ctx_train = 131072`.
#
# An image is not free in that budget. Measured 2026-09-01 on the Phase 9 form:
#
#   page rendered at 1800 px long edge ... 3,120 prompt tokens  (76% of 4,096)
#   the same page at 1400 px ............. 2,247 prompt tokens
#   the same page at 1120 px ............. 1,617 prompt tokens
#
# So the image alone can consume three quarters of the window, and
# `prompt + num_predict` then exceeds it. That is not a soft failure: asking for
# 4,096 predicted tokens over a 1,400-token image ABORTED the runner outright
# (`ggml_abort`, SIGABRT, "llama runner terminated"), and the API answered with
# an empty body and no error field.
#
# ⚠️ WHICH MEANS RAISING `num_predict` WITHOUT RAISING THIS IS WORSE THAN
# LEAVING IT ALONE — it converts a truncated answer into a crashed model host,
# taking every other queued job with it. The two settings are one decision and
# must be changed together.
VISION_NUM_CTX = int(os.environ.get("GI_AI_VISION_NUM_CTX", "8192"))

# ⚠️ AND A CEILING, because the fix for "too small" must not become "so large
# it swaps". The KV cache grows linearly with `num_ctx` — ~450 MiB at 4,096 on
# the measurement box — so an unbounded computed context would trade a crash on
# a big image for a machine that pages itself to death on one. 16,384 is about
# 1.8 GiB of cache, which sits comfortably beside a 6 GiB model on the CPX42
# and on a developer laptop.
VISION_NUM_CTX_MAX = int(os.environ.get("GI_AI_VISION_NUM_CTX_MAX", "16384"))

# What the system prompt plus the JSON scaffolding costs, over and above the
# image and the reply. The consumption prompt is the longest at ~450 tokens.
VISION_PROMPT_HEADROOM = 640


def vision_num_ctx(num_predict: int, image_tokens: Optional[int] = None) -> int:
    """The context window one vision call actually needs.

    ⚠️ THE THREE NUMBERS ARE ONE DECISION (ARCHITECTURE §7a), and this function
    is where they meet. `num_ctx` must hold the image, the prompt AND the reply;
    when it does not, Ollama does not truncate politely — it aborts the runner
    (`ggml_abort`, SIGABRT) and answers with an empty body and no error field,
    killing every other job queued behind it.

    `image_tokens` comes from `ocr.estimate_image_tokens`, which is calibrated
    to err high. When a caller cannot supply it, the floor still covers every
    image `prep_image_for_vision` is capable of producing, because that function
    caps the long edge — an uncapped image is the only way to defeat this, and
    nothing in this codebase sends one.
    """
    need = int(image_tokens or 0) + VISION_PROMPT_HEADROOM + int(num_predict)
    return max(VISION_NUM_CTX, min(need, VISION_NUM_CTX_MAX))


def _payload(model: str, prompt: str, *, system: Optional[str], temperature: float,
             num_predict: int, images: Optional[list[str]] = None,
             num_ctx: Optional[int] = None, fmt: Optional[dict] = None,
             options: Optional[dict] = None,
             keep_alive: Optional[object] = None) -> dict:
    opts: dict = {"temperature": temperature, "num_predict": num_predict}
    if images:
        opts["num_ctx"] = int(num_ctx) if num_ctx else vision_num_ctx(num_predict)
    if options:
        # Text-only extras (seed, top_k, a small num_ctx for the router). A
        # vision call never passes these: its num_ctx is computed above, and
        # letting a caller override it is how ggml_abort comes back (§7a).
        opts.update({k: v for k, v in options.items()
                     if not (images and k == "num_ctx")})
    body: dict = {
        "model": model, "prompt": prompt,
        "keep_alive": KEEP_ALIVE if keep_alive is None else keep_alive,
        "options": opts,
    }
    if system:
        body["system"] = system
    if images:
        body["images"] = images  # base64, no data: prefix (Ollama contract)
    if fmt is not None:
        # Ollama structured outputs: a JSON Schema here makes decoding
        # grammar-constrained, so the reply is schema-valid by construction.
        # Callers still re-validate (system_one._validate).
        body["format"] = fmt
    return body


async def generate(model: str, prompt: str, *, system: Optional[str] = None,
                   temperature: float = 0.2, num_predict: int = 512,
                   images: Optional[list[str]] = None,
                   timeout_s: float = GEN_TIMEOUT_S,
                   num_ctx: Optional[int] = None, fmt: Optional[dict] = None,
                   options: Optional[dict] = None,
                   keep_alive: Optional[object] = None) -> str:
    """One blocking completion. Raises RuntimeError on transport failure."""
    body = _payload(model, prompt, system=system, temperature=temperature,
                    num_predict=num_predict, images=images, num_ctx=num_ctx,
                    fmt=fmt, options=options, keep_alive=keep_alive)
    body["stream"] = False
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            r = await c.post(f"{OLLAMA_HOST}/api/generate", json=body)
            r.raise_for_status()
            return r.json().get("response", "")
    except httpx.TimeoutException as e:
        # ⚠️ A TIMEOUT IS NOT AN OUTAGE, and saying so sent people the wrong way.
        # `httpx.ReadTimeout` stringifies to the empty string, so the old
        # catch-all produced "Ollama generate failed: ReadTimeout: " and the
        # caller rendered it as "the vision model is not reachable right now".
        # An operator then checks a service that is running fine. The model was
        # reachable, answering, and simply not finished — which is a budget
        # problem with a named fix, and this sentence is what names it.
        #
        # Since Phase 11 the distinction is TYPED as well as worded, because a
        # caller now acts on it: `vision_json` may send a page to a cloud
        # provider when the local engine is DEAD, and must never do so merely
        # because it was SLOW. A string nobody parses is documentation; a type
        # is a control.
        raise VisionTimeout(
            f"the model did not finish within {timeout_s:g}s (it was still "
            f"generating — this is a time budget, not an outage; raise "
            f"GI_AI_VISION_TIMEOUT_S if this page is genuinely this long)"
        ) from e
    except Exception as e:  # normalized like legacy — caller shows a friendly msg
        raise VisionUnavailable(
            f"Ollama generate failed: {type(e).__name__}: {e}") from e


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
