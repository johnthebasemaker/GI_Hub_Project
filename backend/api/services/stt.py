"""
backend/api/services/stt.py — speech to text on this machine (Phase 23e,
rulings Q23-10..12).

THE MODEL. Whistle by Cactus Compute (Apache-2.0): one 16.9 MB file, run on the
CPU by the `cactus-needle` engine — outside Ollama, an explicit exception to
the one-warm-model rule (Q23-12). English only (Q23-10). Measured on this Mac,
2026-10-09, 20 site phrases spoken by macOS `say`: 17 ms median per phrase,
0.5 s to load, 88 MB resident, word error 0.14 with the site's own words as
keywords (`tools/stt_eval.py`; the misses: "Rays of PR", "roles of warning
take", "Billow minimum", a tank tag as one number).

NOTHING IS DOWNLOADED HERE. `tools/stt_setup.py` puts the engine and the model
in `models/stt/` once and pins their SHA-256 in `manifest.json`; this module
loads them only when every checksum matches. A missing, swapped or truncated
file means `provider == "none"` and the microphone hides itself — never an
attempt to fetch, never a fallback that sends audio anywhere else.

NOTHING LEAVES THE MACHINE. The package's telemetry is switched off
(NEEDLE_TELEMETRY=0, DO_NOT_TRACK=1) and Hugging Face is put offline before it
is imported. Audio is decoded in memory and dropped; only its length and the
timing are logged.

ONE MODEL, ONE CALLER AT A TIME. The engine is not thread-safe, so calls are
serialised behind a lock and run in a worker thread. It stays loaded while the
API runs (the engine has no unload call); 88 MB.
"""
from __future__ import annotations

import array
import asyncio
import hashlib
import io
import json
import logging
import os
import threading
import time
import wave
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]
STT_DIR = Path(os.environ.get("GI_STT_DIR") or (_ROOT / "models" / "stt"))
SAMPLE_RATE = 16000
MAX_SECONDS = 30.5
MAX_BYTES = 2 * 1024 * 1024
LANGUAGE = "en"                      # ruling Q23-10

_lock = threading.Lock()
_model = None
_checked: Optional[dict] = None


class SttError(ValueError):
    """Something the person can act on (bad audio, too long)."""


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def status(refresh: bool = False) -> dict:
    """{"provider": "whistle" | "none", "reason": …} — checked once, cached."""
    global _checked
    if _checked is not None and not refresh:
        return _checked
    if os.environ.get("GI_STT", "1").strip() in ("0", "off", "false", "no"):
        _checked = {"provider": "none", "reason": "voice input is switched off (GI_STT=0)"}
        return _checked
    man = STT_DIR / "manifest.json"
    try:
        m = json.loads(man.read_text())
        files = m["files"]
        for role in ("engine", "weights"):
            p = STT_DIR / files[role]["name"]
            if not p.is_file() or _sha(p) != files[role]["sha256"]:
                _checked = {"provider": "none",
                            "reason": f"the {role} file does not match its pin — run tools/stt_setup.py"}
                return _checked
        import needle  # noqa: F401 — the package must be importable too
    except FileNotFoundError:
        _checked = {"provider": "none", "reason": "voice input is not set up on this server "
                                                  "(tools/stt_setup.py)"}
        return _checked
    except ImportError:
        _checked = {"provider": "none", "reason": "the cactus-needle package is not installed"}
        return _checked
    except (OSError, ValueError, KeyError) as e:
        _checked = {"provider": "none", "reason": f"the voice model's manifest is unreadable ({type(e).__name__})"}
        return _checked
    _checked = {"provider": "whistle", "reason": None, "version": m.get("whistle_version"),
                "language": LANGUAGE}
    return _checked


def _env() -> None:
    files = json.loads((STT_DIR / "manifest.json").read_text())["files"]
    os.environ["NEEDLE_TELEMETRY"] = "0"
    os.environ["DO_NOT_TRACK"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["NEEDLE3_LIB_PATH"] = str(STT_DIR / files["engine"]["name"])
    os.environ["NEEDLE_WHISTLE_WEIGHTS"] = str(STT_DIR / files["weights"]["name"])


def _load():
    global _model
    if _model is None:
        _env()
        from needle.agent.whistle import Whistle
        t0 = time.perf_counter()
        _model = Whistle()
        logger.info("stt: Whistle loaded in %.0f ms", (time.perf_counter() - t0) * 1000)
    return _model


def decode_wav(data: bytes) -> array.array:
    """A 16 kHz mono 16-bit WAV (what the browser makes) → float samples."""
    if not data or len(data) > MAX_BYTES:
        raise SttError("the recording is empty or too large")
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
    except (wave.Error, EOFError) as e:
        raise SttError("the recording is not a WAV file") from e
    if ch != 1 or width != 2 or rate != SAMPLE_RATE:
        raise SttError(f"expected 16 kHz mono 16-bit audio, got {rate} Hz × {ch} channel(s) × {8 * width} bit")
    if n / SAMPLE_RATE > MAX_SECONDS:
        raise SttError("a recording can be at most 30 seconds")
    pcm = array.array("h")
    pcm.frombytes(raw)
    return array.array("f", (s / 32768.0 for s in pcm))


def _run(samples: array.array, keywords: list[str]) -> dict:
    with _lock:
        model = _load()
        t0 = time.perf_counter()
        out = model.transcribe(samples, language=LANGUAGE, keywords=keywords or None)
        return {"text": (out.get("text") or "").strip(), "ms": round((time.perf_counter() - t0) * 1000),
                "language": out.get("language") or LANGUAGE}


async def transcribe(data: bytes, keywords: Optional[list[str]] = None) -> dict:
    st = status()
    if st["provider"] != "whistle":
        raise RuntimeError(st["reason"])
    samples = decode_wav(data)
    out = await asyncio.to_thread(_run, samples, (keywords or [])[:80])
    out["seconds"] = round(len(samples) / SAMPLE_RATE, 1)
    return out
