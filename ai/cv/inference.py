"""
ai/cv/inference.py — YOLO detection helper for the returnable-tool flow
========================================================================
Lazy-loads the active model from `cv_model_versions WHERE is_active=1` and
exposes `detect_tool(image_bytes)`. ultralytics + torch are imported only
on first detection — Streamlit pages and bug_check never pay the ~6s
torch import cost up front.

Public API
----------
    detect_tool(image_bytes, *, top_k=5) -> list[dict]
        Returns [{class_name, confidence, bbox, applied_threshold}, ...]
        sorted by confidence desc. Each row passes the per-class
        `min_confidence` threshold (from `tool_catalogue`, fallback
        `DEFAULT_MIN_CONFIDENCE`). Returns [] cleanly when:
          - no active model exists in the DB
          - the active model's `model_path` doesn't exist on disk
          - the image bytes can't be decoded

    invalidate_model_cache() -> None
        Clears the in-process @lru_cache so the next detect_tool() call
        re-queries `cv_model_versions`. Called by the Admin Portal's
        Promote button so promotion takes effect without a server restart.

    get_loaded_model_info() -> dict | None
        Returns {"version", "model_path", "classes", "mAP", "trained_at"}
        for the currently-cached model, or None if no detection has run
        yet. Useful for the Admin UI status line.
"""

from __future__ import annotations

import functools
import io
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
DEFAULT_MIN_CONFIDENCE = 0.75   # matches Phase 6A default + roadmap §B9


# ---------------------------------------------------------------------------
# Active-model loader (cached)
# ---------------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def _load_active_yolo() -> tuple[object, dict] | tuple[None, None]:
    """Return (yolo_model, db_row_dict) or (None, None).

    Cached: the first call hits the DB and loads weights; subsequent calls
    reuse the in-memory model. `invalidate_model_cache()` clears it after
    admin promotes a new version.

    Returns (None, None) instead of raising when:
      - no active row in cv_model_versions
      - the model_path on the row doesn't exist on disk
      - ultralytics isn't installed
    """
    # Lazy import — works even on machines without torch (bug_check uses
    # this property to skip the live-model checks).
    try:
        from ultralytics import YOLO
    except ImportError:
        return None, None

    # Defer the DB import so this module can be imported in isolation
    # (e.g. unit tests that monkeypatch).
    try:
        # Make sure the repo root is on sys.path so `database` resolves
        # regardless of caller cwd.
        repo_root = Path(__file__).resolve().parent.parent.parent
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from database import get_active_cv_model
    except Exception:
        return None, None

    row = get_active_cv_model()
    if not row:
        return None, None

    path = row.get("model_path") or ""
    if not path or not os.path.exists(path):
        return None, None

    try:
        model = YOLO(path)
    except Exception:
        return None, None

    return model, row


def invalidate_model_cache() -> None:
    """Clear the cached YOLO instance + DB row. No-op if nothing cached."""
    _load_active_yolo.cache_clear()


def get_loaded_model_info() -> dict | None:
    """Return a slim dict describing the cached model, or None.

    Does NOT trigger a load — only returns info if `_load_active_yolo` has
    already been called this process. The Admin tab uses this to display
    "v2 loaded · 4 classes" without forcing a model load just to render
    the status line.
    """
    info = _load_active_yolo.cache_info()
    if info.currsize == 0:
        return None
    _, row = _load_active_yolo()
    if not row:
        return None
    return {
        "version":     row.get("version"),
        "model_path":  row.get("model_path"),
        "classes":     row.get("classes") or [],
        "mAP":         row.get("mAP"),
        "trained_at":  row.get("trained_at"),
    }


# ---------------------------------------------------------------------------
# Per-class threshold lookup
# ---------------------------------------------------------------------------
@functools.lru_cache(maxsize=128)
def _min_confidence_for_class(class_name: str) -> float:
    """Look up tool_catalogue.min_confidence for a class; fallback to default.

    Cached per-class to avoid hitting SQLite for every detection. The cache
    is cleared alongside the model cache via invalidate_model_cache().
    """
    try:
        repo_root = Path(__file__).resolve().parent.parent.parent
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from database import get_connection
    except Exception:
        return DEFAULT_MIN_CONFIDENCE

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT min_confidence FROM tool_catalogue WHERE class_name = ?",
            (class_name,),
        ).fetchone()
    finally:
        conn.close()

    if row is None or row[0] is None:
        return DEFAULT_MIN_CONFIDENCE
    try:
        return float(row[0])
    except (ValueError, TypeError):
        return DEFAULT_MIN_CONFIDENCE


def _clear_threshold_cache() -> None:
    _min_confidence_for_class.cache_clear()


# Wrap invalidate_model_cache so callers don't have to know about the
# threshold cache too — promotion clears both.
_orig_invalidate = invalidate_model_cache


def invalidate_model_cache() -> None:  # noqa: F811 — intentional override
    """Clear cached YOLO model AND per-class threshold cache."""
    _orig_invalidate()
    _clear_threshold_cache()


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
def detect_tool(image_bytes: bytes, *, top_k: int = 5) -> list[dict]:
    """Run the active YOLO model and return top-K detections above threshold.

    Each returned dict:
        {
          "class_name":         str,
          "confidence":         float,
          "bbox":               (x1, y1, x2, y2),  # int pixel coords
          "applied_threshold":  float,             # the min_confidence used
        }

    Filtering rules:
      - Each detected class is looked up in `tool_catalogue.min_confidence`.
        If not present, `DEFAULT_MIN_CONFIDENCE` (0.75) is used.
      - Detections strictly BELOW the applied threshold are dropped.
      - Survivors are sorted by `confidence` desc and truncated to `top_k`.
    """
    if not image_bytes:
        return []

    model, row = _load_active_yolo()
    if model is None:
        return []

    # Decode bytes → PIL image. ultralytics happily accepts PIL.
    try:
        from PIL import Image, UnidentifiedImageError
        img = Image.open(io.BytesIO(image_bytes))
        # Force-load so a malformed payload raises now rather than
        # surfacing as a torch error later.
        img.load()
    except (UnidentifiedImageError, OSError):
        return []
    except ImportError:
        return []

    try:
        results = model.predict(img, verbose=False)
    except Exception:
        return []

    # ultralytics returns a list of Results — we only feed one image so
    # take results[0].
    if not results:
        return []
    r = results[0]
    boxes = getattr(r, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return []

    # Map class_idx → class_name via the model's names dict.
    names = getattr(model, "names", None) or {}

    detections: list[dict] = []
    for b in boxes:
        try:
            cls_idx = int(b.cls.item())
            conf    = float(b.conf.item())
            # b.xyxy[0] is a torch tensor in real ultralytics; tolerate
            # plain list/tuple as well (used by mocks + future portability).
            xyxy_raw = b.xyxy[0]
            if hasattr(xyxy_raw, "tolist"):
                xyxy_raw = xyxy_raw.tolist()
            x1, y1, x2, y2 = (int(v) for v in xyxy_raw)
        except Exception:
            continue
        class_name = names.get(cls_idx, f"class_{cls_idx}")
        threshold  = _min_confidence_for_class(class_name)
        if conf < threshold:
            continue
        detections.append({
            "class_name":         class_name,
            "confidence":         conf,
            "bbox":               (x1, y1, x2, y2),
            "applied_threshold":  threshold,
        })

    detections.sort(key=lambda d: d["confidence"], reverse=True)
    return detections[:top_k]
