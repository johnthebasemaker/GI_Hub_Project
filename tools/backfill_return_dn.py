#!/usr/bin/env python3
"""
tools/backfill_return_dn.py — one-off after alembic a4c7e1d9b3f2 (Phase 22b).

The Return Log's `DN. No.` is imported from now on (ruling Q22-9). Rows the
sync wrote BEFORE the column existed are filled here from the same workbook,
by the sheet row each one was read from at the last sync (`Source_Row`), so
the next pull does not see every return as an "edit" — which would hold the
automatic ERP commit (Q22-1) for a column that only arrived.

    .venv/bin/python tools/backfill_return_dn.py            # dry run: what it would set
    .venv/bin/python tools/backfill_return_dn.py --commit

Only rows whose DN_No is still empty are touched; the value is normalised
exactly as the importer does (`bulk_import._s`). Run it right after a pull
whose ERP side committed, so the workbook rows are the rows the sync saw.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import warnings
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workbook", default=str(_ROOT / "CNCEC_Inventory.xlsx"))
    ap.add_argument("--site", default="CNCEC")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    import openpyxl
    from sqlalchemy import text

    from backend.api import bulk_import as bi
    from backend.api.db import SessionLocal, engine
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(a.workbook, read_only=True, data_only=True)
    rows = list(wb["Return Log"].iter_rows(min_row=1, values_only=True))
    hi = next(i for i, r in enumerate(rows[:8]) if r and any(
        isinstance(c, str) and c.strip().lower() == "dn. no." for c in r))
    col = [str(c or "").strip().lower() for c in rows[hi]].index("dn. no.")
    dn_at = {i + 1: bi._s(r[col]) if r and len(r) > col else None for i, r in enumerate(rows)}
    async with SessionLocal() as s:
        db = (await s.execute(text(
            'SELECT id, "Source_Row" FROM returns WHERE "Site_ID" = :s AND "Source_Sheet" = '
            "'Return Log' AND COALESCE(\"DN_No\", '') = ''"), {"s": a.site})).all()
        sets = [(rid, dn_at.get(row)) for rid, row in db if dn_at.get(row)]
        print(f"returns without a DN: {len(db)} · the workbook names one for {len(sets)}")
        for rid, dn in sets[:30]:
            print(f"   id {rid} → DN {dn}")
        if a.commit and sets:
            for rid, dn in sets:
                await s.execute(text('UPDATE returns SET "DN_No" = :d WHERE id = :i'),
                                {"d": dn, "i": rid})
            await s.commit()
            print("✅ committed")
        elif sets:
            print("… dry run. Re-run with --commit.")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    os.environ.setdefault("GI_DOTENV", "1")
    sys.exit(asyncio.run(main()))
