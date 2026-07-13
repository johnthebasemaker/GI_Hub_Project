"""
ai/cv/qr.py — QR encode + decode helpers for employee badges
=============================================================
Encoding uses the pure-Python `qrcode` library (no native deps).
Decoding uses `pyzbar`, which wraps the native `libzbar` library — that
import is performed LAZILY inside `decode_png_to_id` so machines without
libzbar (Streamlit Cloud, CI) can still import this module and use the
encode side. The roundtrip test in `bug_check.py` skips gracefully when
pyzbar is missing.

Payload policy
--------------
The QR encodes ONLY the raw `ID_Number` string. No JSON wrapping, no
signing, no PII. Phase 6D's scanner reads the decoded string and looks
it up against `employees.ID_Number` for the actual employee record.
Keeping the payload minimal means stolen / photographed badges leak
nothing beyond the ID itself.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

# Homebrew dylib search paths for macOS. Apple Silicon uses /opt/homebrew/lib,
# Intel Macs use /usr/local/lib. pyzbar's loader calls ctypes.util.find_library
# which does NOT include /opt/homebrew/lib by default — hence the
# "Unable to find zbar shared library" error after a successful
# `brew install zbar` on M-series Macs. We patch find_library to fall back to
# these paths before pyzbar imports.
_MAC_HOMEBREW_LIB_PATHS = (
    "/opt/homebrew/lib",   # Apple Silicon
    "/usr/local/lib",      # Intel
)


def _patch_find_library_for_macos_homebrew() -> None:
    """Make ctypes.util.find_library look in Homebrew dylib paths on macOS.

    Idempotent — safe to call repeatedly. No-op on non-macOS platforms.
    """
    if sys.platform != "darwin":
        return
    import ctypes.util as _cu
    if getattr(_cu.find_library, "_gi_homebrew_patched", False):
        return
    _orig = _cu.find_library

    def _patched(name):
        # Try the stdlib search first — if Homebrew is on the DYLD path or
        # the library lives in /usr/local/lib (Intel), this picks it up.
        found = _orig(name)
        if found:
            return found
        # Fall back to known Homebrew prefixes.
        for base in _MAC_HOMEBREW_LIB_PATHS:
            for fname in (f"lib{name}.dylib", f"lib{name}.0.dylib"):
                p = Path(base) / fname
                if p.exists():
                    return str(p)
        return None

    _patched._gi_homebrew_patched = True  # type: ignore[attr-defined]
    _cu.find_library = _patched


def encode_id_to_png(
    id_number: str,
    *,
    box_size: int = 10,
    border: int = 2,
) -> bytes:
    """QR-encode `id_number` and return PNG bytes.

    Parameters
    ----------
    id_number : str
        Employee ID. Stripped and validated non-empty before encoding.
    box_size : int
        Pixel size of each QR module. Default 10 → ~330x330 px image at
        ECC level M with typical ID lengths. Good for phone-camera scans
        from 30-60 cm away.
    border : int
        Quiet-zone width in modules. QR spec minimum is 4; we go tighter
        (2) because the badge layout already adds whitespace around the
        QR. Increase if scanners struggle.

    Returns
    -------
    bytes
        PNG image bytes ready for `st.image(...)` or `st.download_button`.

    Raises
    ------
    ValueError
        If `id_number` is None, empty, or whitespace-only.
    """
    payload = (id_number or "").strip()
    if not payload:
        raise ValueError("id_number must be a non-empty string.")

    # Lazy import — keeps test collection cheap and gives a cleaner error
    # if the package is missing.
    import qrcode

    qr = qrcode.QRCode(
        version=None,                     # auto-size for shortest payload
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(payload)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def decode_png_to_id(png_bytes: bytes) -> str | None:
    """Decode the first QR code found in a PNG and return its payload.

    Returns
    -------
    str
        The decoded payload string (UTF-8).
    None
        If no QR was detected, the image is unreadable, or pyzbar isn't
        installed (the ImportError is intentionally surfaced as None so
        callers can degrade gracefully — bug_check.py distinguishes
        "missing dep" from "decode failure" by attempting the import
        directly).
    """
    if not png_bytes:
        return None

    # Apply the macOS Homebrew search-path patch BEFORE pyzbar imports.
    # On Apple Silicon, pyzbar's loader calls ctypes.util.find_library
    # which doesn't see /opt/homebrew/lib — without this patch a clean
    # `brew install zbar` still results in "Unable to find zbar shared
    # library". Idempotent + no-op on non-macOS.
    _patch_find_library_for_macos_homebrew()

    try:
        from pyzbar.pyzbar import decode as zbar_decode
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        # Genuine missing dep — pyzbar uninstalled, or libzbar not on disk.
        # Caller can distinguish this from "decode failure" by importing
        # pyzbar directly (bug_check.py does this).
        return None

    # Open the image — narrow except so unrelated failures propagate
    # instead of being silently swallowed (that's how we lost a day on
    # the macOS dylib path issue).
    try:
        img = Image.open(io.BytesIO(png_bytes))
    except (UnidentifiedImageError, OSError):
        return None

    results = zbar_decode(img)
    if not results:
        return None

    raw = results[0].data
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")
