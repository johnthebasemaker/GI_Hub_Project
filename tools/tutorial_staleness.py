#!/usr/bin/env python3
"""
tools/tutorial_staleness.py — Phase 14d: which recorded tutorials show pages
that have changed since they were recorded.

    .venv/bin/python tools/tutorial_staleness.py            # report
    .venv/bin/python tools/tutorial_staleness.py --json     # machine-readable
    .venv/bin/python tools/tutorial_staleness.py --notify   # + store it and ring the ADMIN bell

Run it on the deploy host after a deploy (it needs git; the API image has none).
The logic is `backend/api/services/tutorial_staleness.py`.

⚠️ NEVER A GATE. Exit status is 0 whatever it finds — Phase 12's ruling: a
tutorial that renders badly, or has gone stale, is one to re-record, not a red
build. Re-recording is `tools/generate_tutorial.py --script <yaml> --force`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def _announced() -> dict[str, str]:
    from sqlalchemy import text
    from backend.api.db import SessionLocal
    async with SessionLocal() as s:
        rows = (await s.execute(text(
            "SELECT tutorial_module, key FROM feature_announcements "
            "WHERE rerender AND tutorial_module IS NOT NULL AND status <> 'retracted'"))).all()
    return {r[0]: r[1] for r in rows}


def main() -> int:
    from backend.api.services import tutorial_staleness as T
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true",
                    help="store the result for the Training Hub and ring the admin bell")
    a = ap.parse_args()

    async def run():
        from backend.api.db import SessionLocal, engine
        announced = await _announced() if a.notify else None
        r = T.scan(announced=announced)
        rang = False
        if a.notify:
            async with SessionLocal() as s:
                async with s.begin():
                    rang = await T.record(s, r)
        await engine.dispose()
        return r, rang

    if a.notify:
        r, rang = asyncio.run(run())
    else:
        r, rang = T.scan(), False
    if a.json:
        print(json.dumps(r, indent=1))
        return 0
    if not r["available"]:
        print(f"⚠️  {r['reason']}")
        return 0
    icon = {"current": "✅", "possibly_stale": "⚠️ ", "unknown": "❔"}
    for i in r["items"]:
        print(f"{icon.get(i['status'], '?')} {i['tutorial']:<34} {i['reason']}")
        for f in (i.get("changed") or [])[:6]:
            print(f"      · {f}")
        if i["status"] == "possibly_stale":
            print(f"      re-record: {i['rerecord']}")
    print(T.summary_line(r))
    if a.notify:
        print("admin bell: " + ("rang" if rang else "not rung (nothing new since the last run)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
