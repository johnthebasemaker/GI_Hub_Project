#!/usr/bin/env python3
"""
tools/excel_sync_reconcile.py — post-sync ledger reconciliation (step 2 of the
2026-07-13 Excel data injection; run AFTER tools/excel_sync.py --commit).

The workbook (CNCEC_Inventory.xlsx) is the authoritative tracking record.
After the append-only backfill, two residues can keep a SAP's DB stock from
matching the workbook's Current Stock column:

  1. DB-only ledger rows with no workbook counterpart — app test artifacts
     (e.g. literal "asdf" rows) or double-entries (same DN logged on two
     adjacent dates). Following the workbook's own correction style these are
     ZEROED (Quantity → 0 + an explanatory Remarks suffix) — never deleted —
     and ONLY on SAPs whose stock currently disagrees with the workbook.
  2. Workbook lines with no Date cell (half-entered rows the workbook still
     counts). These are inserted with today's date and a "[excel-sync]
     workbook row without date" remark.

Everything is printed, and one BULK_IMPORT_RECONCILE audit row records the
totals. Dry-run by default; --commit applies.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

os.environ.setdefault("GI_DOTENV", "0")
os.environ.setdefault("GI_SCHEDULER", "0")
os.environ.setdefault("JWT_SECRET", "excel-sync-offline-run-key-32bytes-min!")

# The half-entered workbook lines (no Date cell) that the Inventory sheet's
# Current Stock column nevertheless counts — verified by hand 2026-07-13.
DATELESS_LINES = [
    ("receipts", {"Date": "2026-07-13 00:00:00", "SAP_Code": "1069",
                  "Quantity": 1, "Site_ID": "CNCEC",
                  "Remarks": "[excel-sync] workbook row without date"}),
    ("receipts", {"Date": "2026-07-13 00:00:00", "SAP_Code": "1219",
                  "Quantity": 2, "Site_ID": "CNCEC", "DN_No": "WD",
                  "Remarks": "[excel-sync] workbook row without date"}),
    ("receipts", {"Date": "2026-07-13 00:00:00", "SAP_Code": "1403",
                  "Quantity": 1, "Site_ID": "CNCEC", "DN_No": "WD",
                  "Remarks": "[excel-sync] workbook row without date"}),
    ("returns", {"Date": "2026-07-13 00:00:00", "SAP_Code": "1239",
                 "Quantity": 50, "Site_ID": "CNCEC",
                 "Reason": "Factory Requirment",
                 "Remarks": "[excel-sync] workbook row without date"}),
]

_SHEETS = {"receipts": ("Receipt Log", 12, "DN_No"),
           "consumption": ("Consumption Log", 9, "Tank_No"),
           "returns": ("Return Log", 9, "Reason")}

_ZERO_REMARK = (" [excel-sync 2026-07-13] zeroed: no counterpart in the "
                "authoritative workbook (test/double entry)")


def _day(v) -> str:
    return str(v or "")[:10]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workbook",
                    default=os.path.expanduser("~/Downloads/CNCEC_Inventory.xlsx"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    import openpyxl
    from sqlalchemy import text

    from backend.api import bulk_import as bi
    from backend.api.db import SessionLocal, engine

    wb = openpyxl.load_workbook(args.workbook, read_only=True, data_only=True)

    def sheet_rows(name):
        return [r for r in wb[name].iter_rows(min_row=3, values_only=True)
                if r[1] not in (None, "")]

    zeroed, inserted = [], []
    async with SessionLocal() as s:
        inv = sheet_rows("Inventory")
        expected = {bi._s(r[1]): bi._f(r[10]) for r in inv
                    if bi._s(r[1]) and bi._f(r[10]) is not None}
        dbstock = {r[0]: float(r[1]) for r in (await s.execute(text('''
            SELECT i."SAP_Code", COALESCE(i."Opening_Stock",0)
              + COALESCE((SELECT SUM(r."Quantity") FROM receipts r
                          WHERE r."SAP_Code"=i."SAP_Code"),0)
              - COALESCE((SELECT SUM(c."Quantity") FROM consumption c
                          WHERE c."SAP_Code"=i."SAP_Code"),0)
              - COALESCE((SELECT SUM(t."Quantity") FROM returns t
                          WHERE t."SAP_Code"=i."SAP_Code"),0)
            FROM inventory i'''))).all()}
        mismatched = {sap for sap in expected
                      if dbstock.get(sap) is None
                      or abs(dbstock[sap] - expected[sap]) > 1e-6}
        print(f"mismatched SAPs before reconcile: {len(mismatched)}")

        for tab, (sheet, refi, refcol) in _SHEETS.items():
            xl = collections.Counter()
            for r in sheet_rows(sheet):
                sap, d, q = bi._s(r[1]), bi._iso(r[0]), bi._f(r[5])
                if not sap or d is None or q is None:
                    continue
                xl[(_day(d), sap, round(q, 4), bi._s(r[refi]) or "")] += 1
            rows = (await s.execute(text(
                f'SELECT id, "Date", "SAP_Code", "Quantity", '
                f'COALESCE("{refcol}", \'\') FROM {tab} '
                f'WHERE "Site_ID"=\'CNCEC\' ORDER BY id'))).all()
            for rid, d, sap, q, ref in rows:
                k = (_day(d), sap, round(float(q or 0), 4), bi._s(ref) or "")
                if xl.get(k, 0) > 0:
                    xl[k] -= 1        # workbook counterpart consumed
                    continue
                if sap in mismatched and float(q or 0) != 0.0:
                    zeroed.append((tab, rid, sap, float(q)))
                    print(f"  zero {tab} id={rid} sap={sap} qty={q}")
                    if args.commit:
                        await s.execute(text(
                            f'UPDATE {tab} SET "Quantity"=0, '
                            f'"Remarks"=COALESCE("Remarks",\'\') || :m '
                            f'WHERE id=:i'), {"i": rid, "m": _ZERO_REMARK})

        for tab, vals in DATELESS_LINES:
            dup = (await s.execute(text(
                f'SELECT COUNT(*) FROM {tab} WHERE "SAP_Code"=:sap AND '
                f'"Remarks" LIKE \'%workbook row without date%\''),
                {"sap": vals["SAP_Code"]})).scalar()
            if dup:
                print(f"  skip date-less {tab} {vals['SAP_Code']} (already inserted)")
                continue
            inserted.append((tab, vals["SAP_Code"], vals["Quantity"]))
            print(f"  insert date-less {tab} sap={vals['SAP_Code']} qty={vals['Quantity']}")
            if args.commit:
                cols = ", ".join(f'"{c}"' for c in vals)
                ph = ", ".join(f":{c}" for c in vals)
                await s.execute(text(f'INSERT INTO {tab} ({cols}) VALUES ({ph})'),
                                vals)
        if args.commit:
            await s.execute(text(
                "INSERT INTO system_audit_log (username, action_type, "
                "target_table, details) VALUES ('excel-sync', "
                "'BULK_IMPORT_RECONCILE', 'receipts', :d)"),
                {"d": f"zeroed {len(zeroed)} db-only rows on mismatched SAPs; "
                      f"inserted {len(inserted)} date-less workbook lines"})
            await s.commit()
            print("✅ committed")
        else:
            print("dry-run only — re-run with --commit to apply")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
