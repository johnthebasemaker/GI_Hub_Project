#!/usr/bin/env python3
"""
tools/ocr_site_setup.py — load a site's learned OCR names and its Day / Night
preparers in one audited step (Phase 22, rulings Q22-16/18).

    # dry run: what it would write
    .venv/bin/python tools/ocr_site_setup.py --site CNCEC \\
        --aliases .cache/ocr_eval/proposed_aliases_phase21.json \\
        --preparers 2026-09-26:Johnson:Kalied

    # write it (audited as `ocr-site-setup`)
    … --commit

`--aliases` takes the harness's proposal file (`tools/ocr_eval.py
--propose-aliases`): a list of {written_key, SAP_Code}. A name already learned
for the site is left alone (the store keeper's own Accept wins). Every SAP must
exist in the inventory master, or nothing is written.

`--preparers FROM:DAY:NIGHT` adds (or replaces) the history line starting on
FROM; earlier lines are kept, so older papers keep their names.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

ACTOR = "ocr-site-setup"


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--site", required=True)
    ap.add_argument("--aliases", help="JSON list of {written_key, SAP_Code}")
    ap.add_argument("--preparers", help="FROM:DAY:NIGHT, e.g. 2026-09-26:Johnson:Kalied")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    from sqlalchemy import text

    from backend.api.db import SessionLocal, engine
    from backend.api.services import preparers as PREP
    from backend.api.services.ledger import write_audit

    rc = 0
    async with SessionLocal() as s:
        if a.aliases:
            rows = json.loads(Path(a.aliases).read_text())
            have = {r[0] for r in (await s.execute(text(
                'SELECT written_key FROM ocr_aliases WHERE "Site_ID" = :s'), {"s": a.site})).all()}
            saps = {r[0] for r in (await s.execute(text(
                'SELECT TRIM("SAP_Code") FROM inventory'))).all()}
            bad = [r for r in rows if str(r["SAP_Code"]).strip() not in saps]
            if bad:
                print(f"❌ {len(bad)} SAP(s) not in the inventory: {[r['SAP_Code'] for r in bad]} — nothing written")
                return 1
            new = [r for r in rows if r["written_key"] not in have]
            print(f"names: {len(rows)} in the file · {len(rows) - len(new)} already learned · {len(new)} to add")
            for r in new:
                print(f"   {r['written_key']!r} → {r['SAP_Code']} {r.get('description', '')}")
                await s.execute(text('''
                    INSERT INTO ocr_aliases ("Site_ID", written_key, written_example, "SAP_Code",
                                             confirmations, created_by, updated_by)
                    VALUES (:s, :k, :k, :p, 1, :u, :u)
                    ON CONFLICT ("Site_ID", written_key) DO NOTHING'''),
                    {"s": a.site, "k": r["written_key"], "p": str(r["SAP_Code"]).strip(), "u": ACTOR})
            if new:
                await write_audit(s, ACTOR, "OCR_ALIAS_LEARN", "ocr_aliases",
                                  f"site={a.site} loaded {len(new)} operator-approved name(s) (Q22-18): "
                                  + ", ".join(f"{r['written_key']!r}→{r['SAP_Code']}" for r in new))
        if a.preparers:
            try:
                frm, day, night = a.preparers.split(":", 2)
            except ValueError:
                print("❌ --preparers is FROM:DAY:NIGHT")
                return 2
            hist = [h for h in await PREP.history(s, a.site) if h.get("from") != frm]
            hist.append({"from": frm, "day": day, "night": night})
            saved = await PREP.save(s, a.site, hist)
            print(f"preparers for {a.site}: {saved}")
            await write_audit(s, ACTOR, "PREPARERS_SET", "app_settings",
                              f"site={a.site} {json.dumps(saved)}")
        if a.commit:
            await s.commit()
            print("✅ committed")
        else:
            await s.rollback()
            print("… dry run. Re-run with --commit.")
    await engine.dispose()
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
