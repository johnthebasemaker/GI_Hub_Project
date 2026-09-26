#!/usr/bin/env python3
"""
tools/stock_excel_check.py — which SAPs' GI Hub stock differs from the Excel
workbook's Current Stock, and WHY. Changes no stock.

    .venv/bin/python tools/stock_excel_check.py                     # report
    .venv/bin/python tools/stock_excel_check.py --store             # + save for the Stock page
    .venv/bin/python tools/stock_excel_check.py --marked            # + write a marked COPY

The marked copy is written NEXT TO the workbook as "<name> - GI Hub check.xlsx":
red rows on the Inventory sheet, a note on each Current Stock cell, amber rows
on the log sheets it names, and a "GI Hub check" sheet listing every cause with
how to fix it. ⚠️ The original workbook is never written (openpyxl would drop
its data-validation extensions).

`tools/pg_excel_sync.py --commit` runs the same check and stores it after every
committed sync, so the Stock page is always current.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workbook", default=str(ROOT / "CNCEC_Inventory.xlsx"))
    ap.add_argument("--site", default="CNCEC")
    ap.add_argument("--store", action="store_true", help="save the result for the Stock page")
    ap.add_argument("--marked", action="store_true", help="write a marked COPY of the workbook")
    ap.add_argument("--user", default="tools/stock_excel_check.py")
    a = ap.parse_args()
    wb = Path(a.workbook)
    data = wb.read_bytes()

    async def run():
        from backend.api.db import SessionLocal, engine
        from backend.api.services import stock_excel as SE
        async with SessionLocal() as s:
            async with s.begin():
                r = await SE.diagnose(s, data, site_id=a.site)
                if a.store:
                    await SE.store(s, r, site_id=a.site, workbook=wb.name, username=a.user)
        await engine.dispose()
        return r

    r = asyncio.run(run())
    print(f"== {r['matched']}/{r['total']} materials match the workbook's Current Stock ==")
    for it in r["items"]:
        app = "—" if it["app"] is None else f"{it['app']:g}"
        print(f"\n✗ {it['sap']}  {it['description'][:40]}  workbook {it['workbook']:g} · GI Hub {app}")
        for c in it["causes"]:
            print(f"    • {c['what']}\n      → {c['fix']}")
    if a.marked:
        from backend.api.services import stock_excel as SE
        out = wb.with_name(f"{wb.stem} - GI Hub check.xlsx")
        out.write_bytes(SE.mark_workbook(data, r, checked_at=datetime.now(timezone.utc).isoformat()))
        print(f"\nmarked copy: {out}")
    if a.store:
        print("stored — the Stock page now shows this check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
