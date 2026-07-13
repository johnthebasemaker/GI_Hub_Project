"""
ai/locate_anything/model_loader.py — heavy-deps wrapper (Phase 8A).

⚠️  IMPORT WARNING
   This module imports torch + transformers at module top-level.
   NEVER import it from Streamlit-side code — keep the import inside the
   sidecar uvicorn process only. The package `__init__.py` deliberately
   does NOT re-export anything from this file.

Loader contract:
  - Single in-process model instance, lazy-loaded on first inference call.
  - device = "mps" on Apple Silicon (preferred), "cpu" elsewhere.
  - dtype = int8 via bitsandbytes when available, else fp16 fallback.
  - Weights read from ~/Library/Caches/gi_locate/  (bundle target).
  - Loader NEVER reaches the network — sites without the bundled weights
    raise ModelNotReadyError so the sidecar can return a clean 503.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger("gi.locate_anything.model_loader")

# ── Bundle layout (matches scripts/download_model.sh) ────────────────────
WEIGHTS_DIR = Path(
    os.environ.get(
        "GI_LOCATE_WEIGHTS_DIR",
        os.path.expanduser("~/Library/Caches/gi_locate"),
    )
)
LOCATE_REPO_DIRNAME = "LocateAnything-3B"   # subdir under WEIGHTS_DIR


class ModelNotReadyError(RuntimeError):
    """Raised when the weights aren't on disk yet (bundle not deployed)."""


# ── Device + dtype selection ─────────────────────────────────────────────
def _select_device() -> str:
    """MPS on Apple Silicon, CPU elsewhere.

    NVIDIA CUDA is not supported in this scaffold — the spec is Apple
    Silicon MPS only. If a site later adds an NVIDIA GPU, add a `"cuda"`
    branch here and a torch.cuda.is_available() probe.
    """
    try:
        import torch
        if torch.backends.mps.is_available() and torch.backends.mps.is_built():
            return "mps"
    except Exception as e:
        logger.warning("torch.mps probe failed: %s — falling back to CPU.", e)
    return "cpu"


def _select_quant_kwargs() -> dict[str, Any]:
    """Return the `from_pretrained` kwargs that activate int8 quantisation
    when bitsandbytes is installed. On MPS, bitsandbytes int8 is not yet
    supported reliably, so we fall back to fp16 there with a logged note.

    Result merges into transformers.from_pretrained(...).
    """
    try:
        import bitsandbytes  # noqa: F401  (probe only)
    except Exception:
        logger.warning(
            "bitsandbytes not installed — falling back to fp16. "
            "Pip-install bitsandbytes for int8 on supported devices."
        )
        return {"torch_dtype": "auto"}

    device = _select_device()
    if device == "mps":
        # bitsandbytes int8 is x86/CUDA-only as of 0.43; MPS gets fp16.
        logger.info(
            "bitsandbytes int8 not yet supported on MPS — using fp16 "
            "with mixed-precision inference."
        )
        return {"torch_dtype": "auto"}

    # CUDA path (future-proofing, not in 8A's hardware target).
    return {"load_in_8bit": True}


# ── Lazy single-load ─────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def get_model_and_processor():
    """Load LocateAnything-3B once per process. Raises ModelNotReadyError
    if the bundle isn't on disk — the sidecar maps that to HTTP 503."""
    weights_path = WEIGHTS_DIR / LOCATE_REPO_DIRNAME
    if not weights_path.exists():
        raise ModelNotReadyError(
            f"Weights not found at {weights_path}. "
            f"Run scripts/download_model.sh (HQ) then "
            f"scripts/install_locate_anything_weights.sh (site)."
        )

    # Heavy imports DEFERRED so a missing bundle doesn't penalise startup.
    import torch
    from transformers import AutoModelForVision2Seq, AutoProcessor

    device = _select_device()
    quant_kwargs = _select_quant_kwargs()
    logger.info(
        "Loading LocateAnything-3B from %s on device=%s …",
        weights_path, device,
    )
    processor = AutoProcessor.from_pretrained(str(weights_path))
    model = AutoModelForVision2Seq.from_pretrained(
        str(weights_path),
        **quant_kwargs,
    )
    if "load_in_8bit" not in quant_kwargs:
        # bitsandbytes manages device placement itself; we move manually
        # only when we're NOT going through it.
        model = model.to(device)
    model.eval()
    logger.info("LocateAnything-3B ready on %s.", device)
    return model, processor, device


def is_ready() -> bool:
    """Cheap disk probe — does NOT load the model. Used by /health."""
    return (WEIGHTS_DIR / LOCATE_REPO_DIRNAME).exists()


def reset_for_tests() -> None:
    """Clear the lru_cache so unit-test mocks can re-trigger the loader."""
    get_model_and_processor.cache_clear()
