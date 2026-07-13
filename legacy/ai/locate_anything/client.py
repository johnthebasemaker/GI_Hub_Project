"""
ai/locate_anything/client.py — Streamlit-side HTTP wrapper (Phase 8A).

This module is the ONLY entry point Streamlit code touches. It must:
  - NEVER import torch, transformers, fastapi, or PIL.
  - NEVER raise to the caller — every failure path returns an empty list
    or a structured error dict so the Smart Scan UI degrades gracefully.
  - Read the Admin gate (`app_settings.locate_anything_enabled`) on EVERY
    call. The Admin can flip it off at any moment; the next /detect call
    short-circuits without touching the network.
  - Honour an in-process circuit breaker: after N consecutive failures the
    client refuses to call the sidecar for COOLDOWN_SECONDS, so a wedged
    sidecar can't slow down every Smart Scan attempt.

The HTTP call itself is isolated in `_perform_http_post` so the bug_check
harness can monkey-patch a single symbol and run 100% offline.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

# stdlib-only HTTP — avoids dragging httpx/requests into the import graph
# and keeps the test mock surface to a single function.
import urllib.error
import urllib.request


logger = logging.getLogger("gi.locate_anything.client")

# ── Tuning knobs ────────────────────────────────────────────────────────
DEFAULT_TIMEOUT_SECONDS = 30.0
CIRCUIT_BREAKER_THRESHOLD = 3         # consecutive failures before tripping
CIRCUIT_BREAKER_COOLDOWN  = 60.0      # seconds to stay tripped
DEFAULT_SIDECAR_URL = "http://127.0.0.1:8503"

# Test hook — set GI_SUPPRESS_LOCATE_ANYTHING=1 to make `is_enabled()` and
# `detect()` no-op regardless of the DB setting. Mirrors the pattern we use
# for streamlit-local-storage under AppTest (see test_ui_crawler.py).
_SUPPRESS_ENV = "GI_SUPPRESS_LOCATE_ANYTHING"


# ── Module-level mutable state for the circuit breaker ──────────────────
class _BreakerState:
    """Tiny mutable holder so monkey-patched tests can reset it cleanly."""
    consecutive_failures: int = 0
    tripped_until: float = 0.0


_breaker = _BreakerState()


def _breaker_reset() -> None:
    """Public test helper — bug_check uses this between checks."""
    _breaker.consecutive_failures = 0
    _breaker.tripped_until = 0.0


def _breaker_record_failure() -> None:
    _breaker.consecutive_failures += 1
    if _breaker.consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
        _breaker.tripped_until = time.monotonic() + CIRCUIT_BREAKER_COOLDOWN
        logger.warning(
            "Sidecar circuit breaker tripped after %d consecutive failures — "
            "cooldown %ds.",
            _breaker.consecutive_failures, int(CIRCUIT_BREAKER_COOLDOWN),
        )


def _breaker_record_success() -> None:
    _breaker.consecutive_failures = 0
    _breaker.tripped_until = 0.0


def _breaker_is_open() -> bool:
    return time.monotonic() < _breaker.tripped_until


# ── Admin gate + URL lookup ─────────────────────────────────────────────
def is_enabled() -> bool:
    """Read `app_settings.locate_anything_enabled`. Cheap — single SELECT.
    Always re-reads — the Admin can flip the toggle mid-session.

    Returns False when the test-suppression env var is set so AppTest /
    bug_check never accidentally exercise the real sidecar.
    """
    if os.environ.get(_SUPPRESS_ENV) == "1":
        return False
    try:
        from database import get_app_setting
        return str(get_app_setting("locate_anything_enabled", "0")).strip() == "1"
    except Exception:
        return False


def sidecar_url() -> str:
    """Read the sidecar URL from app_settings, with DEFAULT_SIDECAR_URL fallback."""
    try:
        from database import get_app_setting
        url = (get_app_setting("locate_anything_sidecar_url",
                               DEFAULT_SIDECAR_URL) or "").strip()
        return url or DEFAULT_SIDECAR_URL
    except Exception:
        return DEFAULT_SIDECAR_URL


# ── The HTTP layer — single seam for offline mocking ────────────────────
def _perform_http_post(
    url: str,
    payload: dict,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, dict | None]:
    """Send a JSON POST. Returns (status_code, parsed_json_or_None).

    Bug_check monkey-patches THIS function so the test suite never hits
    the network and never depends on a running sidecar. Production code
    paths call this exactly once per Smart Scan Tier-3 invocation.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.getcode() or 0
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, None  # 0 == "couldn't reach the sidecar"
    try:
        return status, json.loads(body) if body else None
    except json.JSONDecodeError:
        return status, None


# ── Telemetry helper (best-effort, never raises to caller) ──────────────
def _log_telemetry(**kwargs) -> int:
    """Wrap database.log_locate_anything_call so a DB hiccup never bubbles
    up. Returns the new row id (0 on failure)."""
    try:
        from database import log_locate_anything_call
        return log_locate_anything_call(**kwargs)
    except Exception as e:
        logger.warning("Telemetry write failed: %s", e)
        return 0


# ── Public API ──────────────────────────────────────────────────────────
def detect(
    image_b64: str,
    prompt: str = "",
    *,
    classes: list[str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    site_id: str | None = None,
    sk_username: str | None = None,
    yolo_top_conf: float | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Send a base64-encoded image to the sidecar and return detections.

    Returns `(detections, call_id)` where:
      - detections is a list of `{label, box: [x1,y1,x2,y2], score}` dicts
        (the shape returned by LocateAnything-3B).
      - call_id is the rowid of the telemetry row this call wrote to
        `locate_anything_calls` (Phase 8E). 0 if no telemetry row was
        written (gate off / breaker open / telemetry DB failed).
        The caller passes this id to `mark_locate_anything_outcome()`
        once the SK accepts/rejects in the UI.

    Never raises — failure paths return `([], call_id_or_0)`.

    Circuit breaker trips on: sidecar unreachable (HTTP 0), 5xx, malformed
    JSON, timeout. 4xx does NOT trip — that's a client-side bug, not the
    sidecar's fault.

    Telemetry writes one row PER call attempt that actually hits HTTP —
    gate-off / breaker-open paths write NO row (they didn't generate work).
    """
    if not is_enabled():
        return [], 0
    if _breaker_is_open():
        logger.debug("Sidecar circuit breaker open; skipping /detect call.")
        return [], 0

    url = sidecar_url().rstrip("/") + "/detect"
    payload = {
        "image_b64": image_b64,
        "prompt":    prompt or "",
        "classes":   classes or [],
    }
    t0 = time.monotonic()
    status, body = _perform_http_post(url, payload, timeout=timeout)
    latency_ms = int((time.monotonic() - t0) * 1000)

    # ── Happy path ──────────────────────────────────────────────────────
    if status == 200 and isinstance(body, dict):
        dets = body.get("detections")
        if isinstance(dets, list):
            cleaned = [d for d in dets if isinstance(d, dict)]
            _breaker_record_success()
            call_id = _log_telemetry(
                site_id=site_id, sk_username=sk_username,
                yolo_top_conf=yolo_top_conf,
                detection_count=len(cleaned),
                latency_ms=latency_ms,
                error=None,
            )
            return cleaned, call_id
        # 200 with malformed body — count as a failure but don't crash.
        _breaker_record_failure()
        call_id = _log_telemetry(
            site_id=site_id, sk_username=sk_username,
            yolo_top_conf=yolo_top_conf, detection_count=0,
            latency_ms=latency_ms,
            error="200 with malformed body (no 'detections' list)",
        )
        return [], call_id

    # ── Failure paths ───────────────────────────────────────────────────
    if status == 0:
        err = "Sidecar unreachable (HTTP 0)"
    elif status >= 500:
        err = f"Sidecar 5xx (HTTP {status})"
    else:
        err = f"Sidecar {status}"

    if status == 0 or status >= 500:
        _breaker_record_failure()
    call_id = _log_telemetry(
        site_id=site_id, sk_username=sk_username,
        yolo_top_conf=yolo_top_conf, detection_count=0,
        latency_ms=latency_ms, error=err,
    )
    return [], call_id


def health() -> dict:
    """Probe the sidecar's /health endpoint. Used by the Admin status panel
    in Phase 8D. Always returns a dict — never raises.

    Returns shape: {"ok": bool, "status_code": int, "body": dict | None}.
    """
    if not is_enabled():
        return {"ok": False, "status_code": 0,
                "body": {"reason": "Admin toggle off."}}
    url = sidecar_url().rstrip("/") + "/health"
    # /health uses GET, not POST — small inline call rather than expanding
    # the seam (keeps the test mock surface minimal).
    try:
        with urllib.request.urlopen(url, timeout=5.0) as resp:
            status = resp.getcode() or 0
            try:
                body = json.loads(resp.read().decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                body = None
            return {"ok": status == 200, "status_code": status, "body": body}
    except Exception as e:
        return {"ok": False, "status_code": 0,
                "body": {"reason": f"{type(e).__name__}: {e}"}}
