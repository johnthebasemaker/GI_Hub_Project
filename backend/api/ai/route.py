"""
backend/api/ai/route.py — the model gateway: one policy table, per lane.

⚠️ THIS IS A POLICY LAYER, NOT A NEW TRANSPORT. `client.py` still owns the HTTP
and, critically, still owns `vision_num_ctx()`. Nothing here computes a context
window, and nothing here may start.

────────────────────────────────────────────────────────────────────────────
WHY NOT PORTKEY, AND WHY NOT LITELLM EITHER

**Portkey** — the open-source gateway is a Node service, and its value lives in
a control plane that is either their cloud (our prompts carry manual chapters,
live SQL results and OCR'd delivery notes) or a second application to run,
patch and back up. It also sits IN THE REQUEST PATH, so when it is down the
assistant is down. That is ruling P10-1's objection to Redis — "a new daemon and
a new 3 a.m. failure mode" — in a strictly larger form.

**LiteLLM** was closer, and the reason for saying no is specific rather than
ideological:

1. ⚠️ **It would abstract away the vision envelope, which is THREE NUMBERS THAT
   ARE ONE DECISION** (ARCHITECTURE §7a). `client.vision_num_ctx()` computes
   `num_ctx` from a measured image-token estimate, because getting it wrong does
   not truncate politely — it ABORTS the Ollama runner (`ggml_abort`, SIGABRT,
   empty body, no error field) and takes every other queued job down with it.
   LiteLLM's Ollama adapter passes its own options dict. Handing that decision
   to a library's defaults re-opens a bug that cost a phase to find, and
   re-opens it silently.
2. **Retry semantics here are not generic.** This codebase has already learned
   that A TIMEOUT IS NOT AN OUTAGE: a read timeout on a vision call means the
   model is still generating (a five-row form measured at 399 s), and retrying
   doubles the load on a box that holds one warm model. A generic
   `num_retries=3` would do exactly the wrong thing on the slowest, most
   important lane.
3. The seam it sells already exists — `client.vision_provider()` and the
   cloud-fallback switches added in slice 11b.

So the gateway is ~200 lines in-process: no daemon, no proxy, no new port.

────────────────────────────────────────────────────────────────────────────
THE PIPELINE

    call(lane, …) ─▶ 1. lane policy   (model, budgets, retry classes, fallback)
                     2. cache lookup  (assistant only — see answer_cache.py)
                     3. primary       (local Ollama, warm)
                     4. classify the error, and act on the CLASS:
                          connect / 5xx / 429  → retry, jittered backoff
                          read timeout         → NO RETRY, no fallback
                          model not pulled     → straight to fallback
                     5. fallback chain, if this lane is allowed one
                     6. record provider · model · ms · retries · fallback

⚠️ **LANES, NOT MODELS.** The policy table is keyed by the lane names that
already exist, because `NUM_PREDICT` and the timeout were already per-lane and
were already a bug when they were not (`ai/jobs.py`: one budget for four lanes
clipped the 30-row consumption sheet at row 13 for a whole phase).

⚠️ **FALLBACK IS A POLICY, NOT A DEFAULT.** Routing to a cloud provider means
proprietary data leaves the network. Operator ruling Q5 (2026-09-02): VISION
ONLY, opt-in, off by default. Every text lane — and especially `/ai/query`,
`/ai/nl-search`, `/ai/insights` and `/ai/eod-summary`, which carry live stock
rows and generated SQL over the ERP — has `cloud_fallback=False` and there is no
switch that turns it on.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from . import client as aic

logger = logging.getLogger("gi.ai.route")


@dataclass(frozen=True)
class LanePolicy:
    """Everything the gateway needs to know about one lane.

    ⚠️ `num_ctx` IS ABSENT FROM THIS TABLE ON PURPOSE. It is not a policy, it is
    a computation over the image that this particular request carries, and it
    lives in `client.vision_num_ctx()` where the measurements that justify it
    are written down. A `num_ctx: 8192` column here would be a default somebody
    would eventually tune without reading them.
    """
    model: str
    num_predict: int
    timeout_s: float
    vision: bool = False
    # ⚠️ VISION ONLY, AND ONLY WITH BOTH SWITCHES SET (ruling Q5).
    cloud_fallback: bool = False
    # Transport failures worth trying again. Deliberately NOT read timeouts.
    max_retries: int = 2
    cacheable: bool = False


def _p(**kw) -> LanePolicy:
    return LanePolicy(**kw)


# ⚠️ EVERY LANE IS NAMED. An unlisted lane gets `DEFAULT_POLICY` and a warning,
# rather than silently inheriting the chat model's budget — which is how the
# consumption lane came to share the delivery note's 1,024 tokens and lose
# seventeen of its thirty rows.
POLICIES: dict[str, LanePolicy] = {
    # ── text ────────────────────────────────────────────────────────────────
    "assistant":  _p(model=aic.MODEL_CHAT, num_predict=512,
                     timeout_s=aic.GEN_TIMEOUT_S, cacheable=True),
    "insights":   _p(model=aic.MODEL_CHAT, num_predict=700,
                     timeout_s=aic.GEN_TIMEOUT_S),
    "eod":        _p(model=aic.MODEL_CHAT, num_predict=700,
                     timeout_s=aic.GEN_TIMEOUT_S),
    "nl_search":  _p(model=aic.MODEL_CODER, num_predict=400,
                     timeout_s=aic.GEN_TIMEOUT_S),
    # Phase 17 — System One (ai/system_one.py). One forward pass over a short
    # question into a 2-key JSON object: 48 tokens is ~3x the longest valid
    # reply. ⚠️ NO RETRY AND A 3-SECOND BUDGET, because the fallback is not an
    # error — it is today's fully-fenced path, so a slow router costs the user
    # nothing but the wait it already spent. No cloud, and no switch for one.
    "router":     _p(model=aic.MODEL_ROUTER, num_predict=48,
                     timeout_s=float(os.environ.get("GI_AI_ROUTER_TIMEOUT_S", "3")),
                     max_retries=0),
    # ── vision. The only lanes with a cloud path at all. ────────────────────
    "ocr_consumption":      _p(model=aic.MODEL_VISION, num_predict=3072,
                               timeout_s=aic.VISION_TIMEOUT_S, vision=True,
                               cloud_fallback=True, max_retries=0),
    "ocr_delivery_note":    _p(model=aic.MODEL_VISION, num_predict=1536,
                               timeout_s=aic.VISION_TIMEOUT_S, vision=True,
                               cloud_fallback=True, max_retries=0),
    "ocr_purchase_doc":     _p(model=aic.MODEL_VISION, num_predict=2560,
                               timeout_s=aic.VISION_TIMEOUT_S, vision=True,
                               cloud_fallback=True, max_retries=0),
    "ocr_consumption_form": _p(model=aic.MODEL_VISION, num_predict=2600,
                               timeout_s=aic.VISION_TIMEOUT_S, vision=True,
                               cloud_fallback=True, max_retries=0),
    # Phase 23b (ruling Q23-3) — the background second read of the rows the
    # first read skipped. A COMPACT answer (one `S.No|Name|…` line per row,
    # ~30 tokens, not ~100 of JSON) under a hard cap: the measured cost of the
    # JSON answer was 158–578 s, the model running on to the full 3,072. No
    # cloud path: the papers carry workers' names, and nobody is waiting.
    "ocr_consumption_second": _p(model=aic.MODEL_VISION, num_predict=1024,
                                 timeout_s=min(aic.VISION_TIMEOUT_S, 300.0), vision=True,
                                 cloud_fallback=False, max_retries=0),
    "tool_identify":        _p(model=aic.MODEL_VISION, num_predict=384,
                               timeout_s=aic.VISION_TIMEOUT_S, vision=True,
                               cloud_fallback=True, max_retries=0),
}

DEFAULT_POLICY = LanePolicy(model=aic.MODEL_CHAT, num_predict=512,
                            timeout_s=aic.GEN_TIMEOUT_S)


def policy(lane: str) -> LanePolicy:
    p = POLICIES.get(lane)
    if p is None:
        logger.warning("no routing policy for lane %r — using the default; "
                       "add it to route.POLICIES", lane)
        return DEFAULT_POLICY
    return p


# ── error classification ────────────────────────────────────────────────────

RETRYABLE = "retryable"          # transport hiccup: try again, same engine
TIMEOUT = "timeout"              # the model was WORKING. Never retry, never fallback.
UNAVAILABLE = "unavailable"      # the engine is gone: fall back if allowed
FATAL = "fatal"                  # anything else


def classify(exc: BaseException) -> str:
    """Which of four things went wrong. The distinction drives the behaviour.

    ⚠️ THE TIMEOUT CASE IS THE ONE THAT MATTERS AND THE ONE EVERY GENERIC
    GATEWAY GETS WRONG. `httpx.ReadTimeout` from Ollama does not mean the server
    is unwell — it means our budget ran out while the model was still producing
    tokens. Retrying starts a SECOND full generation on a box that holds one
    warm model, so the retry makes the very condition it is responding to worse;
    and falling back would ship the page to a third party because our own
    stopwatch expired, which is a data-egress decision made out of impatience.
    Both are refused here, in one place, so no lane can opt into either.
    """
    if isinstance(exc, aic.VisionTimeout):
        return TIMEOUT
    if isinstance(exc, aic.VisionUnavailable):
        return UNAVAILABLE
    if isinstance(exc, httpx.TimeoutException):
        return TIMEOUT
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout,
                        httpx.RemoteProtocolError, httpx.ReadError)):
        return RETRYABLE
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429 or 500 <= code < 600:
            return RETRYABLE
        return FATAL
    msg = str(exc).lower()
    if "429" in msg or "connect" in msg or "unreachable" in msg:
        return RETRYABLE
    return FATAL


BACKOFF_BASE_S = float(0.4)
BACKOFF_MAX_S = float(4.0)


def _backoff(attempt: int) -> float:
    """Exponential with FULL jitter.

    Jittered because four uvicorn workers whose Ollama restarts together would
    otherwise retry in lockstep and hit it with a synchronised thundering herd
    at exactly the moment it is least able to take one.
    """
    ceiling = min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2 ** attempt))
    return random.uniform(0, ceiling)


@dataclass
class Outcome:
    """What actually happened, for the `ai.generate` span."""
    text: str = ""
    model: str = ""
    provider: str = "ollama"
    ms: int = 0
    retries: int = 0
    fell_back: bool = False
    error_class: str = ""

    def as_attrs(self) -> dict:
        return {"model": self.model, "provider": self.provider,
                "ms": self.ms, "retries": self.retries,
                "fell_back": self.fell_back,
                "error_class": self.error_class or None}


async def call_vision(lane: str, prompt: str, *, system: str, image_b64: str,
                      image_tokens: Optional[int] = None,
                      temperature: float = 0.0) -> Outcome:
    """One vision completion, under this lane's policy.

    ⚠️ `client.vision_json` STAYS THE CALLEE, and it is the function that calls
    `vision_num_ctx()`. That indirection is the point of this whole module's
    existence: the gateway decides WHICH engine and HOW MANY TRIES, and the
    client decides how big the context window has to be for the image in hand.
    Collapsing the two — which is exactly what adopting a gateway library would
    have done — is how `ggml_abort` comes back.
    """
    pol = policy(lane)
    t0 = time.perf_counter()
    out = Outcome(model=pol.model, provider=aic.vision_provider())
    attempt = 0
    while True:
        try:
            text, model_id = await aic.vision_json(
                prompt, system=system, image_b64=image_b64,
                num_predict=pol.num_predict, timeout_s=pol.timeout_s,
                image_tokens=image_tokens, temperature=temperature)
            out.text, out.model = text, model_id
            out.provider = aic.provider_of(model_id)
            # `vision_json` owns the cloud hop (slice 11b) precisely so the
            # "both switches set AND the local engine is dead" rule lives in
            # ONE place. If the model that answered is not the local one, a
            # fallback happened inside it.
            out.fell_back = out.provider != "ollama" and \
                aic.vision_provider() == "ollama"
            out.ms = int((time.perf_counter() - t0) * 1000)
            return out
        except BaseException as e:                      # noqa: BLE001
            kind = classify(e)
            out.error_class = kind
            if kind == RETRYABLE and attempt < pol.max_retries:
                attempt += 1
                out.retries = attempt
                await asyncio.sleep(_backoff(attempt))
                continue
            out.ms = int((time.perf_counter() - t0) * 1000)
            raise


async def call_structured(lane: str, prompt: str, *, system: str, schema: dict,
                          options: Optional[dict] = None,
                          keep_alive: Optional[object] = None) -> Outcome:
    """One schema-constrained completion, under this lane's policy.

    ⚠️ IT DOES NOT TAKE `GEN_SEMAPHORE`, and that is the reason it exists apart
    from `call_text`. The semaphore guards the GENERATION model; the router is a
    different, pinned model (ruling Q17-1), and a classification that queued
    behind a 300-second chat answer would add the whole of someone else's wait
    to every question. Temperature is fixed at 0 — this lane classifies, and a
    classifier that samples answers Monday and Tuesday differently (§7f).
    """
    pol = policy(lane)
    t0 = time.perf_counter()
    out = Outcome(model=pol.model, provider="ollama")
    attempt = 0
    while True:
        try:
            out.text = await aic.generate(
                pol.model, prompt, system=system, temperature=0.0,
                num_predict=pol.num_predict, timeout_s=pol.timeout_s,
                fmt=schema, options=options, keep_alive=keep_alive)
            out.ms = int((time.perf_counter() - t0) * 1000)
            return out
        except BaseException as e:                      # noqa: BLE001
            kind = classify(e)
            out.error_class = kind
            if kind == RETRYABLE and attempt < pol.max_retries:
                attempt += 1
                out.retries = attempt
                await asyncio.sleep(_backoff(attempt))
                continue
            out.ms = int((time.perf_counter() - t0) * 1000)
            raise


async def call_text(lane: str, prompt: str, *, system: Optional[str] = None,
                    temperature: float = 0.2) -> Outcome:
    """One blocking text completion, under this lane's policy.

    ⚠️ NO CLOUD PATH EXISTS FOR ANY TEXT LANE, and none can be switched on.
    `/ai/insights` and `/ai/eod-summary` summarise the results of live SQL
    probes over the ERP; `/ai/nl-search` sends generated SQL. Ruling Q5 permits
    cloud fallback for vision only, and the way to keep a permission narrow is
    for the wider path not to be written.
    """
    pol = policy(lane)
    t0 = time.perf_counter()
    out = Outcome(model=pol.model, provider="ollama")
    attempt = 0
    while True:
        try:
            async with aic.GEN_SEMAPHORE:
                out.text = await aic.generate(
                    pol.model, prompt, system=system, temperature=temperature,
                    num_predict=pol.num_predict, timeout_s=pol.timeout_s)
            out.ms = int((time.perf_counter() - t0) * 1000)
            return out
        except BaseException as e:                      # noqa: BLE001
            kind = classify(e)
            out.error_class = kind
            if kind == RETRYABLE and attempt < pol.max_retries:
                attempt += 1
                out.retries = attempt
                await asyncio.sleep(_backoff(attempt))
                continue
            out.ms = int((time.perf_counter() - t0) * 1000)
            raise
