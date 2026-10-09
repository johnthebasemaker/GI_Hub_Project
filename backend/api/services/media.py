"""
backend/api/services/media.py — pictures of materials and equipment on disk
(Phase 23d, rulings Q23-7/8).

WHERE. `media/catalog/<sha[:2]>/<sha>.jpg` (the picture, re-encoded),
`…_512.webp` (what pages show) and `…_128.webp` (thumbnails). The folder is
git-ignored and archived beside every nightly database dump by
`bin/backup_db.sh` (Q23-7). The Drive token stays read-only: Drive is an INPUT
(the `Material Images` folder), never where GI Hub writes.

CONTENT-ADDRESSED. A picture is stored once however many codes use it (a
family of 20 rubber-sheet sizes shares one photo), so the files are named by
the sha256 of the re-encoded picture, and `item_images` rows point at them.

PRIVACY. Every picture is DECODED and RE-ENCODED, which drops EXIF — no GPS,
no phone model, no timestamp leaves the upload. HEIC (iPhone) is read the way
OCR reads it (pillow-heif when installed).

SIGNED LINKS. An `<img>` cannot send the bearer token, so a list hands out
short-lived signed links (`?e=<expiry>&t=<hmac>`) — the same idea as the
training-video tickets: the picture endpoint checks the signature, not a
session, and a link is useless an hour later.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[3]
MEDIA_DIR = Path(os.environ.get("GI_MEDIA_DIR") or (_ROOT / "media" / "catalog"))

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_SIDE = 2400              # the stored picture's long edge
SIZES = {"display": 512, "thumb": 128}
ACCEPTED = ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif")
LINK_TTL_S = 3600


class MediaError(ValueError):
    """A picture that cannot be taken — the message is for the person."""


@dataclass
class Stored:
    sha256: str
    width: int
    height: int
    bytes: int
    mime: str = "image/jpeg"


def _open(data: bytes):
    from PIL import Image, ImageOps
    try:
        import pillow_heif  # noqa: F401
        pillow_heif.register_heif_opener()
    except Exception:  # noqa: BLE001 — HEIC simply stays unsupported
        pass
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as e:  # noqa: BLE001
        raise MediaError("this file is not a picture GI Hub can read (JPG, PNG, WebP or HEIC)") from e
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, "white")
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
    return im.convert("RGB")


def path_for(sha: str, size: str = "original") -> Path:
    d = MEDIA_DIR / sha[:2]
    return d / (f"{sha}.jpg" if size == "original" else f"{sha}_{SIZES[size]}.webp")


def store(data: bytes) -> Stored:
    """Decode, orient, strip EXIF, cap the size, write the three files. Raises
    MediaError with a sentence. Idempotent: the same picture is one set of files."""
    if not data:
        raise MediaError("the file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise MediaError(f"a picture can be at most {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    from PIL import Image
    im = _open(data)
    if max(im.size) > MAX_SIDE:
        im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=88, optimize=True)      # no exif= → none written
    jpeg = buf.getvalue()
    sha = hashlib.sha256(jpeg).hexdigest()
    orig = path_for(sha)
    orig.parent.mkdir(parents=True, exist_ok=True)
    if not orig.exists():
        tmp = orig.with_name(f".{orig.name}.tmp")
        tmp.write_bytes(jpeg)
        os.replace(tmp, orig)
    for size, side in SIZES.items():
        p = path_for(sha, size)
        if p.exists():
            continue
        small = im.copy()
        small.thumbnail((side, side), Image.LANCZOS)
        tmp = p.with_name(f".{p.name}.tmp")
        small.save(tmp, format="WEBP", quality=82, method=4)
        os.replace(tmp, p)
    return Stored(sha256=sha, width=im.size[0], height=im.size[1], bytes=len(jpeg))


def media_type(size: str) -> str:
    return "image/jpeg" if size == "original" else "image/webp"


# ── signed links ─────────────────────────────────────────────────────────────
def _secret() -> bytes:
    from ..auth import JWT_SECRET
    return ("gi-media|" + JWT_SECRET).encode()


def sign(image_id: int, size: str, now: Optional[float] = None, ttl: int = LINK_TTL_S) -> str:
    exp = int((now or time.time()) + ttl)
    # rounded to the hour so a page's links are identical across re-renders and
    # the browser cache keeps working
    exp = exp - exp % 3600 + 3600
    mac = hmac.new(_secret(), f"{image_id}|{size}|{exp}".encode(), hashlib.sha256).digest()
    return f"e={exp}&t={base64.urlsafe_b64encode(mac[:18]).decode().rstrip('=')}"


def verify(image_id: int, size: str, e: str, t: str, now: Optional[float] = None) -> bool:
    try:
        exp = int(e)
    except (TypeError, ValueError):
        return False
    if exp < (now or time.time()):
        return False
    mac = hmac.new(_secret(), f"{image_id}|{size}|{exp}".encode(), hashlib.sha256).digest()
    want = base64.urlsafe_b64encode(mac[:18]).decode().rstrip("=")
    return hmac.compare_digest(want, t or "")


def url(image_id: int, size: str = "thumb") -> str:
    return f"/catalogue/img/{image_id}/{size}?{sign(image_id, size)}"
