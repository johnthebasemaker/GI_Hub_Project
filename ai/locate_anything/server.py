"""
ai/locate_anything/server.py — sidecar FastAPI app (Phase 8A).

Run with:
    uvicorn ai.locate_anything.server:app --host 127.0.0.1 --port 8503

Endpoints:
    POST /detect   — image_b64 + optional prompt/classes → bounding boxes
    GET  /health   — sidecar status + device + readiness

Architectural contract:
  - torch / transformers are imported only INSIDE endpoint handlers, so
    `from ai.locate_anything.server import app` does NOT pay the torch
    import cost. Lets bug_check verify the module loads without GPU.
  - 127.0.0.1 only — NEVER bind 0.0.0.0. The Cloudflare Tunnel exposes
    Streamlit, not this sidecar.
  - Single-worker uvicorn — concurrent requests serialise on the single
    in-process model instance.
  - Missing bundle → /detect returns 503 with a friendly hint instead of
    crashing the worker.
"""
from __future__ import annotations

import base64
import io
import logging
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("gi.locate_anything.server")

app = FastAPI(
    title="GI Hub — LocateAnything Sidecar",
    version="0.1.0",
    description="Smart Scan Tier-3 grounding for the GI Hub ERP.",
)


# ── Request / response shapes ─────────────────────────────────────────
class DetectRequest(BaseModel):
    image_b64: str = Field(..., description="Base64-encoded image bytes.")
    prompt:    str = Field("", description="Optional grounding prompt.")
    classes:   list[str] = Field(
        default_factory=list,
        description="Optional list of class names to seed the prompt.",
    )


class Detection(BaseModel):
    label: str
    box:   list[float]   # [x1, y1, x2, y2] absolute pixels
    score: float


class DetectResponse(BaseModel):
    detections: list[Detection]
    latency_ms: int
    device:     str


# ── Endpoints ─────────────────────────────────────────────────────────
@app.get("/health")
def health() -> dict[str, Any]:
    """Cheap status probe — does NOT trigger model load."""
    # Deferred imports — keeps the module import light for bug_check.
    from .model_loader import is_ready, _select_device
    return {
        "ok":           True,
        "service":      "gi.locate_anything",
        "model_loaded": is_ready(),     # disk probe, not actual loaded-into-VRAM
        "device":       _select_device(),
    }


@app.post("/detect", response_model=DetectResponse)
def detect(req: DetectRequest) -> DetectResponse:
    """Run LocateAnything inference on the supplied image."""
    # Deferred imports — bug_check imports `server` module without torch.
    try:
        from PIL import Image
        from .model_loader import get_model_and_processor, ModelNotReadyError
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Sidecar deps not installed: {e}. "
                   f"Run `pip install -r ai/locate_anything/requirements.txt`.",
        ) from e

    # 1. Decode the image. Bad payloads → 400, never 500.
    try:
        raw = base64.b64decode(req.image_b64, validate=True)
        image = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Could not decode image_b64: {type(e).__name__}: {e}",
        ) from e

    # 2. Load the model lazily. Missing weights → 503 (gate breaker-friendly).
    try:
        model, processor, device = get_model_and_processor()
    except ModelNotReadyError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        # Genuine load failure — log and report 500. Circuit breaker on
        # the client side will trip after 3 consecutive of these.
        logger.exception("Model load failed.")
        raise HTTPException(
            status_code=500,
            detail=f"Model load failed: {type(e).__name__}: {e}",
        ) from e

    # 3. Inference. The exact call shape depends on the LocateAnything
    #    Hugging Face card — Phase 8C will wire the prompt template once
    #    the weights are on disk and we can read the model's tokenizer.
    #    Phase 8A scaffolds the wrapper but returns an empty detection
    #    list when no prompt/classes were supplied (defensive default).
    started_at = time.perf_counter()
    try:
        # Stub: scaffolded inference call. Phase 8C will replace with
        # the production prompt template + post-processing per the
        # LocateAnything-3B model card.
        prompt_text = req.prompt or (
            "Locate: " + ", ".join(req.classes) if req.classes else ""
        )
        if not prompt_text:
            detections: list[Detection] = []
        else:
            inputs = processor(images=image, text=prompt_text,
                               return_tensors="pt").to(device)
            with __import__("torch").no_grad():
                _ = model.generate(**inputs, max_new_tokens=128)
            # Phase 8A returns empty until 8C wires the parser. Production
            # callers are gated by the Admin toggle so this never fires
            # in real environments today.
            detections = []
    except Exception as e:
        logger.exception("Inference failed.")
        raise HTTPException(
            status_code=500,
            detail=f"Inference failed: {type(e).__name__}: {e}",
        ) from e

    latency_ms = int((time.perf_counter() - started_at) * 1000)
    return DetectResponse(
        detections=detections,
        latency_ms=latency_ms,
        device=device,
    )
