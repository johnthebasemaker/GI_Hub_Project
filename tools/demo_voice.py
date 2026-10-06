#!/usr/bin/env python3
"""
tools/demo_voice.py — record the self-driving demo's fixed sentences with the
Mac's own voice (Phase 21f, ruling Q21-12: on-device speech + pre-recorded Mac
clips, no paid cloud voice).

    .venv/bin/python tools/demo_voice.py            # (re)record what changed
    .venv/bin/python tools/demo_voice.py --check    # list sentences without a clip
    .venv/bin/python tools/demo_voice.py --voice Daniel

Why clips at all: the browser's on-device voice differs per laptop (Samantha
on a Mac, something plainer on Windows). A clip recorded once here sounds the
same on the projector laptop as on the office one. Only sentences WITHOUT a
`{placeholder}` are recorded — a sentence that changes per run is spoken by
the browser instead (frontend/src/demo/voice.ts says the order).

Output: frontend/public/demo-audio/<hash>.m4a + manifest.json. <hash> is djb2
over the sentence's UTF-16 code units — the SAME function as voice.ts
`sentenceHash`, so the browser finds the clip by the sentence alone. A clip
whose sentence is gone is deleted. macOS only (`say`, `afconvert` ship with it).
NOT a gate: a missing clip is spoken by the browser, never an error.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The FLOWS only (the Finance presentation). Page tours (tours.ts, ~180
# sentences) use the browser's on-device voice — Samantha on a Mac — so the
# repository does not carry several more megabytes of audio for them.
SOURCES = [ROOT / "frontend" / "src" / "demo" / "scripts.ts"]
OUT = ROOT / "frontend" / "public" / "demo-audio"
_SAY = re.compile(r"""\bsay:\s*(['"])((?:\\.|(?!\1).)*)\1""", re.S)


def sentence_hash(text: str) -> str:
    """djb2 over UTF-16 code units; identical to voice.ts sentenceHash."""
    h = 5381
    units = text.encode("utf-16-le")
    for i in range(0, len(units), 2):
        c = units[i] | (units[i + 1] << 8)
        h = ((h * 33) & 0xFFFFFFFF) ^ c
    return f"{h & 0xFFFFFFFF:08x}"


def sentences() -> list[str]:
    out: list[str] = []
    for src in SOURCES:
        if not src.exists():
            continue
        for _q, body in _SAY.findall(src.read_text(encoding="utf-8")):
            text = body.replace("\\'", "'").replace('\\"', '"')
            if "{" in text or text in out:
                continue
            out.append(text)
    return out


def record(text: str, dest: Path, voice: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        aiff = Path(tmp) / "s.aiff"
        subprocess.run(["say", "-v", voice, "-r", "178", "-o", str(aiff), text], check=True)
        subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "32000", "-c", "1", str(aiff), str(dest)],
                       check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--voice", default="Samantha")
    ap.add_argument("--check", action="store_true", help="list sentences with no clip; record nothing")
    a = ap.parse_args()
    todo = sentences()
    want = {sentence_hash(t): t for t in todo}
    have = {p.stem for p in OUT.glob("*.m4a")} if OUT.exists() else set()
    missing = [h for h in want if h not in have]
    if a.check:
        for h in missing:
            print(f"  no clip: {want[h]}")
        print(f"{len(want) - len(missing)}/{len(want)} sentence(s) recorded")
        return 0
    if not shutil.which("say") or not shutil.which("afconvert"):
        print("❌ needs macOS `say` and `afconvert`", file=sys.stderr)
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    for n, h in enumerate(missing, 1):
        print(f"  [{n}/{len(missing)}] {want[h][:70]}")
        record(want[h], OUT / f"{h}.m4a", a.voice)
    stale = [p for p in OUT.glob("*.m4a") if p.stem not in want]
    for p in stale:
        p.unlink()
    (OUT / "manifest.json").write_text(json.dumps(
        {"_note": "tools/demo_voice.py — Mac-voice clips of the demo's fixed sentences",
         "voice": a.voice, "clips": sorted(want)}, indent=1) + "\n")
    size = sum(p.stat().st_size for p in OUT.glob("*.m4a"))
    print(f"✅ {len(want)} clip(s), {len(missing)} new, {len(stale)} removed · {size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
