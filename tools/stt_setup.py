#!/usr/bin/env python3
"""
tools/stt_setup.py — fetch the Whistle speech-to-text engine ONCE, and pin it
(Phase 23e, rulings Q23-10..12).

    .venv/bin/python tools/stt_setup.py            # download + pin (needs the internet once)
    .venv/bin/python tools/stt_setup.py --verify   # check the pinned files (offline)

Whistle (Cactus Compute, Apache-2.0) is a 16.9 MB model run by the `cactus-needle`
package's native engine. Neither is downloaded while GI Hub runs: this tool puts
both in `models/stt/` (git-ignored) and writes `models/stt/manifest.json` with
their SHA-256. `backend/api/services/stt.py` loads ONLY files whose checksum
matches the manifest, so a swapped or truncated file turns the microphone off
instead of running unknown code.

⚠️ TELEMETRY OFF. `cactus-needle` sends anonymous usage counts by default. This
tool and the API set NEEDLE_TELEMETRY=0 and DO_NOT_TRACK=1 before the package is
imported; audio and transcripts never leave this machine either way.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
from pathlib import Path

os.environ["NEEDLE_TELEMETRY"] = "0"
os.environ["DO_NOT_TRACK"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("GI_STT_DIR") or (ROOT / "models" / "stt"))
MANIFEST = OUT / "manifest.json"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify() -> int:
    if not MANIFEST.is_file():
        print(f"❌ no {MANIFEST.relative_to(ROOT)} — run this tool without --verify first")
        return 1
    m = json.loads(MANIFEST.read_text())
    bad = 0
    for role, f in m["files"].items():
        p = OUT / f["name"]
        ok = p.is_file() and sha256(p) == f["sha256"]
        bad += not ok
        print(f"  {'✅' if ok else '❌'} {role:<7} {f['name']}  {f['bytes']:,} B  sha256 {f['sha256'][:16]}…")
    print("✅ pinned files match" if not bad else "❌ a file does not match its pin — the microphone stays off")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    if a.verify:
        return verify()
    try:
        import needle
        from needle.agent import fetch
    except ImportError:
        print("❌ cactus-needle is not installed: .venv/bin/pip install 'cactus-needle==3.1.3'")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"▶ engine {fetch.engine_version(3)} for {platform.system()} {platform.machine()} …")
    lib = Path(fetch.fetch_library(dest_dir=str(OUT), generation=3))
    print(f"▶ Whistle weights {fetch.engine_version(fetch.WHISTLE)} …")
    weights = Path(fetch.fetch_weights(generation=fetch.WHISTLE, dest_dir=str(OUT)))
    files = {}
    for role, p in (("engine", lib), ("weights", weights)):
        files[role] = {"name": p.name, "bytes": p.stat().st_size, "sha256": sha256(p)}
    manifest = {"package": f"cactus-needle {getattr(needle, '__version__', '?')}",
                "engine_version": fetch.engine_version(3),
                "whistle_version": fetch.engine_version(fetch.WHISTLE),
                "platform": f"{platform.system()}-{platform.machine()}",
                "files": files}
    MANIFEST.write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"✅ pinned in {MANIFEST.relative_to(ROOT)}")
    return verify()


if __name__ == "__main__":
    sys.exit(main())
