"""
tools/tutorial_fixture.py — the tutorial-retrieval eval's committed corpus.

    .venv/bin/python tools/tutorial_fixture.py           # rebuild the fixture
    .venv/bin/python tools/tutorial_fixture.py --check   # has it drifted?

WHY IT EXISTS (Phase 17d). `backend/api/ai/tutorials.py` matches a question to a
moment in a recorded tutorial, and the System One router sends TUTORIAL_SEARCH
questions straight to it. Its corpus — `docs/tutorials/out/*.manifest.json` — is
gitignored (the renders and their manifests are build output), so CI had no
corpus and the retrieval evals had nothing to retrieve from.

The fixture is those manifests STRIPPED TO THE FIELDS THE MATCHER READS:
`tutorial_id, training_module_key, language, title, subtitle, audience,
hub_role, video.duration_s` and `beats[id, note, start_s, text]`. The heygen
block, git metadata, dataset hashes, routes and file paths are dropped — this
repository is public, and an eval corpus has no business carrying pipeline
metadata. The narration itself was recorded against SYNTHETIC data (P12-0).

⚠️ `--check` compares the fixture with the real manifests WHEN THEY EXIST (the
operator's machine). On a machine without renders there is nothing to compare,
and it says so rather than claiming the fixture is current (rule 16).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "tutorials" / "out"
DST = ROOT / "tests" / "ai_eval" / "fixtures" / "tutorials"
TOP = ("tutorial_id", "training_module_key", "language", "title", "subtitle",
       "audience", "hub_role")
BEAT = ("id", "note", "start_s", "text")


def strip(m: dict) -> dict:
    out = {k: m[k] for k in TOP if k in m}
    dur = (m.get("video") or {}).get("duration_s")
    if dur is not None:
        out["video"] = {"duration_s": dur}
    out["beats"] = [{k: b[k] for k in BEAT if k in b} for b in m.get("beats") or []]
    return out


def render(m: dict) -> str:
    return json.dumps(strip(m), indent=1, ensure_ascii=False, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    srcs = sorted(SRC.glob("*.manifest.json")) if SRC.is_dir() else []
    if not srcs:
        print(f"⏭  SKIPPED — no tutorial manifests at {SRC.relative_to(ROOT)} on this "
              f"machine, so the fixture cannot be compared (a skip, not a pass).")
        return 0 if not a.check else 0
    want = {p.name: render(json.loads(p.read_text(encoding="utf-8"))) for p in srcs}
    if a.check:
        have = {p.name: p.read_text(encoding="utf-8")
                for p in sorted(DST.glob("*.manifest.json"))} if DST.is_dir() else {}
        if have != want:
            stale = sorted(set(want) ^ set(have) | {k for k in want if have.get(k) != want[k]})
            print("❌ the tutorial fixture is STALE — " + ", ".join(stale)
                  + ". Run `.venv/bin/python tools/tutorial_fixture.py` and commit it.")
            return 1
        print(f"✅ tutorial fixture is current ({len(want)} manifests, "
              f"{sum(len(json.loads(v)['beats']) for v in want.values())} beats)")
        return 0
    DST.mkdir(parents=True, exist_ok=True)
    for old in DST.glob("*.manifest.json"):
        if old.name not in want:
            old.unlink()
    for name, text in want.items():
        (DST / name).write_text(text, encoding="utf-8")
    print(f"wrote {len(want)} manifests to {DST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
