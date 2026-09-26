#!/usr/bin/env python3
"""
tools/announcements.py — Phase 14d: "What's new" announcements, authored as code.

    .venv/bin/python tools/announcements.py nav            # regenerate the nav snapshot
    .venv/bin/python tools/announcements.py nav --check    # fail if it is stale
    .venv/bin/python tools/announcements.py lint           # validate docs/announcements/*.yaml
    .venv/bin/python tools/announcements.py sync           # load them into the DB as drafts

Publishing is an ADMIN decision made in Admin Console → Announcements (or
`POST /announcements/admin/{key}/publish`), never a side effect of a deploy.

⚠️ `nav` is how the audience stays the navigation matrix (rule 14). The API
image has no Node, so the matrix is snapshotted into
`backend/api/data/nav_access.json` from `frontend/src/config/nav.tsx` by the
same dumper the tutorial linter uses, with role levels read from
`auth.ROLE_META`. Suite 14D runs `--check`: a manifest edit without a snapshot
refresh fails the service tests, not a user.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

SNAPSHOT = ROOT / "backend" / "api" / "data" / "nav_access.json"
DUMP = ROOT / "frontend" / "scripts" / "nav_access_dump.mjs"


def fresh_snapshot() -> dict:
    from generate_tutorial import role_levels
    out = subprocess.run(["node", str(DUMP), json.dumps(role_levels())],
                         cwd=ROOT / "frontend", check=True, capture_output=True,
                         text=True).stdout
    d = json.loads(out)
    return {"_generated_by": "tools/announcements.py nav — do not edit by hand",
            "sane": d["sane"], "roleLevels": d["roleLevels"], "publics": d["publics"],
            "routes": {k: d["routes"][k] for k in sorted(d["routes"])}}


def render(d: dict) -> str:
    return json.dumps(d, indent=1, ensure_ascii=False) + "\n"


def cmd_nav(check: bool) -> int:
    new = render(fresh_snapshot())
    old = SNAPSHOT.read_text(encoding="utf-8") if SNAPSHOT.exists() else ""
    if check:
        if new != old:
            print("❌ backend/api/data/nav_access.json is STALE — the nav manifest "
                  "changed. Run `.venv/bin/python tools/announcements.py nav` and commit it.")
            return 1
        print("✅ nav snapshot is current")
        return 0
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(new, encoding="utf-8")
    print(f"wrote {SNAPSHOT.relative_to(ROOT)} ({len(json.loads(new)['routes'])} routes)")
    return 0


def cmd_lint() -> int:
    from backend.api.services import announcements as A
    good, bad = A.load_dir()
    for a in good:
        print(f"✅ {a['key']:<32} → {', '.join(a['audience_roles'])}")
    for b in bad:
        print(f"❌ {b['file']}: {b['problem']}")
    return 1 if bad else 0


def cmd_sync() -> int:
    from backend.api.db import SessionLocal, engine
    from backend.api.services import announcements as A

    async def run():
        async with SessionLocal() as s:
            async with s.begin():
                r = await A.sync(s, username="tools/announcements.py")
        await engine.dispose()
        return r
    r = asyncio.run(run())
    print(f"added {r['added']} · updated {r['updated']} · {r['files']} file(s)")
    for b in r["problems"]:
        print(f"❌ {b['file']}: {b['problem']}")
    return 1 if r["problems"] else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("nav")
    n.add_argument("--check", action="store_true")
    sub.add_parser("lint")
    sub.add_parser("sync")
    a = ap.parse_args()
    if a.cmd == "nav":
        return cmd_nav(a.check)
    if a.cmd == "lint":
        return cmd_lint()
    return cmd_sync()


if __name__ == "__main__":
    raise SystemExit(main())
