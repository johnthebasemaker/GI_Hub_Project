"""
ai/image_utils.py — Round 14
============================
Image prep pipeline for the Ollama vision OCR path.

Smartphone uploads (iPhone in particular) routinely arrive at 4032×3024 ≈
3–6 MB. The OCR call path was sending those raw bytes straight into a JSON
payload to `qwen2.5vl:7b`, which:
  • blew the urllib timeout (cold start + huge upload),
  • occasionally crashed Ollama's vision preprocessor (HTTP 500),
  • landed sideways when the photo's EXIF orientation said "rotate 90°"
    (model sees a rotated page → OCR accuracy collapses).

`prep_image_for_vision` is the single boundary that fixes all three:
  1. HEIC support via pillow-heif (iPhone shares are HEIC even when renamed
     to .JPG — the file's magic bytes are 'ftypheic', not 'JFIF').
  2. EXIF auto-orient — `ImageOps.exif_transpose` rotates the image to the
     orientation the user actually intended.
  3. RGB convert — strips alpha + palette so JPEG re-encode never fails.
  4. Long-edge cap — `thumbnail(max_dim, max_dim)` shrinks while preserving
     aspect. 1600 px is the sweet spot: qwen2.5vl's internal tile
     preprocessor caps around there anyway, so larger uploads are wasted
     bandwidth without any OCR-accuracy gain.
  5. JPEG re-encode at quality=85 — small enough to fly through localhost
     in milliseconds, sharp enough that handwritten digits remain legible.

Failures are mapped to one typed exception (`ImagePrepError`) so callers
in `ai/ocr.py` can surface a clean user-facing message instead of letting
PIL exceptions leak into the UI.
"""

from __future__ import annotations

from io import BytesIO

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except ImportError as _e:  # pragma: no cover — PIL is a hard requirement
    raise

# pillow-heif is required for iPhone uploads. Wrapped so an air-gapped site
# without the wheel still loads this module — HEIC inputs will then raise
# ImagePrepError with a clear "install pillow-heif" hint instead of crashing
# at import time.
_HEIF_AVAILABLE: bool
try:
    import pillow_heif  # type: ignore[import-not-found]
    _HEIF_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised on minimal installs
    _HEIF_AVAILABLE = False


class ImagePrepError(Exception):
    """Typed wrapper for any failure inside prep_image_for_vision so the
    OCR callers don't need to know about PIL internals."""


_HEIF_REGISTERED = False


def _register_heif_opener_once() -> None:
    """Idempotent guard around pillow_heif.register_heif_opener(). Cheap to
    call on every prep invocation — registration is a no-op after the first
    call. Module-level state because PIL's plugin registry IS module-level
    state."""
    global _HEIF_REGISTERED
    if _HEIF_REGISTERED or not _HEIF_AVAILABLE:
        return
    try:
        pillow_heif.register_heif_opener()
    except Exception:
        # If registration somehow fails, surface as a HEIC-decode error at
        # use time rather than blocking the JPEG/PNG path here.
        return
    _HEIF_REGISTERED = True


def prep_image_for_vision(
    raw_bytes: bytes,
    *,
    max_dim: int = 1600,
    quality: int = 85,
) -> bytes:
    """Resize + re-encode an uploaded photo into a JPEG suitable for the
    Ollama vision endpoint.

    Args:
        raw_bytes: the file_uploader's `.getvalue()` output. Any format PIL
            can decode — JPEG, PNG, WebP, HEIC/HEIF (when pillow-heif is
            installed), GIF, BMP.
        max_dim: long-edge cap in pixels. Aspect ratio preserved. 1600 px is
            the Round 14 default — close to qwen2.5vl's internal max usable
            resolution.
        quality: JPEG quality (0–95). 85 is the sweet spot for OCR: text
            stays sharp, file shrinks ~5–10×.

    Returns:
        Re-encoded JPEG bytes ready for base64 + Ollama.

    Raises:
        ImagePrepError: unreadable bytes, unsupported format, or HEIC input
            without pillow-heif. Wraps the underlying PIL error.
    """
    if not isinstance(raw_bytes, (bytes, bytearray)) or not raw_bytes:
        raise ImagePrepError("Empty image bytes — nothing to process.")

    _register_heif_opener_once()

    try:
        img = Image.open(BytesIO(raw_bytes))
        # Force-load now so any decode error surfaces here, not later.
        img.load()
    except UnidentifiedImageError as e:
        # Common case: HEIC bytes on a site without pillow-heif installed.
        if not _HEIF_AVAILABLE and _looks_like_heif(raw_bytes):
            raise ImagePrepError(
                "This looks like an iPhone HEIC photo. The pillow-heif "
                "package is not installed on this server, so HEIC uploads "
                "cannot be processed. Share the photo as JPEG (iPhone → "
                "Settings → Camera → Formats → Most Compatible) or ask "
                "your admin to install pillow-heif."
            ) from e
        raise ImagePrepError(
            "Couldn't read this photo — corrupt or unsupported format."
        ) from e
    except (OSError, ValueError) as e:
        raise ImagePrepError(
            "Couldn't read this photo — corrupt or unsupported format."
        ) from e

    try:
        # EXIF orientation: a portrait iPhone shot is physically landscape
        # in the file, with an EXIF tag saying 'rotate 90° CW'. Without
        # this transpose the model would see a sideways page.
        img = ImageOps.exif_transpose(img)

        # Strip alpha / palette → RGB. JPEG can't store alpha; PNGs with
        # alpha would lose transparency anyway.
        if img.mode != "RGB":
            img = img.convert("RGB")

        # In-place long-edge cap. Skips resize if image is already smaller.
        img.thumbnail((int(max_dim), int(max_dim)), Image.LANCZOS)

        buf = BytesIO()
        img.save(
            buf, format="JPEG",
            quality=int(quality), optimize=True, progressive=False,
        )
        return buf.getvalue()
    except Exception as e:
        raise ImagePrepError(
            f"Image transformation failed: {e}"
        ) from e


def _looks_like_heif(raw_bytes: bytes) -> bool:
    """Magic-bytes check for HEIC/HEIF containers. The header is
    `....ftyp{heic,heix,heif,mif1,msf1,hevc}...` so we look at bytes 4–12.
    Used to give a targeted error message when pillow-heif is missing."""
    if len(raw_bytes) < 12:
        return False
    sig = raw_bytes[4:12]
    return sig.startswith(b"ftyp") and any(
        brand in raw_bytes[8:32]
        for brand in (b"heic", b"heix", b"heif", b"mif1", b"msf1", b"hevc")
    )
