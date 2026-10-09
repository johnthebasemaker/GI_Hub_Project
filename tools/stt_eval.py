#!/usr/bin/env python3
"""
tools/stt_eval.py — how well Whistle hears site English (Phase 23e).

    .venv/bin/python tools/stt_eval.py            # 20 phrases spoken by macOS `say`

Synthetic voices only — no real person's voice is recorded or committed. Each
phrase is spoken by `say` at 16 kHz mono, transcribed by the pinned Whistle
(services/stt.py, telemetry off, offline), and scored by word error rate with
numbers folded ("two" = "2", "thirteen thousand six hundred" stays words —
counted as errors, which is why the strict figure is the one reported).

⚠️ NOT A CI GATE (P10-7): it needs macOS `say` and the model files, neither of
which CI has. Measured 2026-10-09 on this Mac: WER 0.14 with the site's words
as keywords, 17 ms median per phrase, 0.5 s to load, 88 MB resident.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PHRASES = [
    "How many leather gloves are in stock at CNCEC", "Issue two boxes of dust masks to tank J027",
    "Show me the Tyvek coverall stock", "What is pending for Remafix",
    "Raise a PR for twenty sanding discs", "Which lots expire this week",
    "How much garnet did we use yesterday", "Show the receipts from delivery note thirteen thousand six hundred",
    "Is the air compressor sticker still valid", "Ten pairs of safety gloves for the night shift",
    "What did Kalied prepare on the fifth of October", "List the request lines without a SAP code",
    "Add a picture for the jubilee clamp", "How many rolls of warning tape are left",
    "Move five buckets to the CNCEC store", "What is below minimum stock today",
    "Show me tank five two two eight nine D zero", "Open the material card for blasting nozzle",
    "How many hours did the rubber lining crew work", "Print a consumption form for system LSC ten",
]
KEYWORDS = ["CNCEC", "Tyvek", "Remafix", "J027", "Kalied", "garnet", "LSC", "jubilee clamp",
            "sanding discs", "coverall"]
_NUM = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
        "seven": "7", "eight": "8", "nine": "9", "ten": "10", "twenty": "20"}


def words(s: str) -> list[str]:
    return [_NUM.get(w, w) for w in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()]


def wer(ref: str, hyp: str) -> tuple[int, int]:
    r, h = words(ref), words(hyp)
    d = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
    for i in range(len(r) + 1):
        d[i][0] = i
    for j in range(len(h) + 1):
        d[0][j] = j
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (r[i - 1] != h[j - 1]))
    return d[-1][-1], len(r)


def main() -> int:
    if not shutil.which("say"):
        print("❌ needs macOS `say` to speak the phrases")
        return 2
    from backend.api.services import stt as STT
    st = STT.status()
    if st["provider"] != "whistle":
        print(f"❌ {st['reason']}")
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="gi-stt-eval-"))
    for kw in (False, True):
        errs = total = 0
        times = []
        for i, p in enumerate(PHRASES):
            f = tmp / f"p{i}.wav"
            if not f.exists():
                subprocess.run(["say", "-o", str(f), "--data-format=LEI16@16000", p], check=True)
            t0 = time.perf_counter()
            out = STT._run(STT.decode_wav(f.read_bytes()), KEYWORDS if kw else [])
            times.append(time.perf_counter() - t0)
            e, n = wer(p, out["text"])
            errs, total = errs + e, total + n
            if kw and e:
                print(f"  {i + 1:2d} ✗ {out['text']!r}")
        times.sort()
        print(f"keywords={kw}: WER {errs / total:.3f} ({errs}/{total}) · median "
              f"{times[len(times) // 2] * 1000:.0f} ms · max {times[-1] * 1000:.0f} ms")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("GI_DOTENV", "0")
    sys.exit(main())
