#!/usr/bin/env python3
"""
tools/excel_sync.py — one-shot Excel data sync through the Bulk Import service.

Runs the SAME plan/apply code as POST /import/{kind} (backend/api/bulk_import),
so a sync on the laptop mirror today and on the production box after the
final cutover load are byte-identical operations. Order matters: the
inventory master lands first so the ledger backfill's soft-FK check passes.

    DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub \
    .venv/bin/python tools/excel_sync.py \
        --site CNCEC [--dir /path/to/workbooks] [--commit]

Workbooks are read from --dir (default: the repo root, where the 4 tracking
files live). All columns are resolved by HEADER NAME, so reordering or adding
columns in the workbooks is safe; unknown columns are reported as warnings.

Ends with a per-SAP stock verification: Opening_Stock + Σreceipts −
Σconsumption − Σreturns in the DB must equal the workbook's "Current Stock"
column — the workbook is the tracking truth, so any mismatch is listed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

os.environ.setdefault("GI_DOTENV", "0")
os.environ.setdefault("GI_SCHEDULER", "0")
os.environ.setdefault("JWT_SECRET", "excel-sync-offline-run-key-32bytes-min!")

FILES = {
    "inventory": "CNCEC_Inventory.xlsx",
    "ledger": "CNCEC_Inventory.xlsx",
    "sme-equipment": "Equipment.xlsx",
    "sme-recipes": "For_1_SQM.xlsx",
    "sme-materials": "Materials_DetailsAvailable_Qty.xlsx",
}


def _fmt(summary) -> str:
    if isinstance(summary, dict) and "receipts" in summary:
        return " · ".join(f"{k}: +{v['inserts']} ~{v['corrections']} "
                          f"={v['matched']} 0skip={v['zero_skipped']} "
                          f"dbonly={v['db_only']}" for k, v in summary.items())
    return (f"+{summary['inserts']} ~{summary['updates']} "
            f"={summary['unchanged']} rejected={summary['rejects']}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=_ROOT,
                    help="folder holding the 4 workbooks (default: repo root)")
    ap.add_argument("--site", default="CNCEC")
    ap.add_argument("--commit", action="store_true",
                    help="apply the plans (default: dry-run report only)")
    ap.add_argument("--user", default="excel-sync",
                    help="username stamped on the audit rows")
    args = ap.parse_args()

    from backend.api import bulk_import as bi
    from backend.api.db import SessionLocal, engine
    from sqlalchemy import text

    datas = {}
    for kind, fname in FILES.items():
        path = os.path.join(os.path.expanduser(args.dir), fname)
        if not os.path.exists(path):
            print(f"❌ missing {path}")
            return 2
        with open(path, "rb") as fh:
            datas[kind] = fh.read()

    pending_saps: set[str] = set()  # dry-run: inventory inserts feed the ledger plan

    planners = {
        "inventory": lambda s, d: bi.plan_inventory(s, d, args.site),
        "ledger": lambda s, d: bi.plan_ledger(s, d, args.site,
                                              extra_saps=pending_saps),
        "sme-equipment": lambda s, d: bi.plan_sme_equipment(s, d, args.site),
        "sme-recipes": lambda s, d: bi.plan_sme_recipes(s, d),
        "sme-materials": lambda s, d: bi.plan_sme_materials(s, d),
    }
    appliers = {
        "inventory": bi.apply_inventory, "ledger": bi.apply_ledger,
        "sme-equipment": bi.apply_sme_equipment,
        "sme-recipes": bi.apply_sme_recipes,
        "sme-materials": bi.apply_sme_materials,
    }

    mode = "COMMIT" if args.commit else "DRY-RUN"
    print(f"== Excel sync ({mode}) → site {args.site} ==")
    failures = 0
    for kind in FILES:  # dict order == the safe sequence
        async with SessionLocal() as session:
            plan = await planners[kind](session, datas[kind])
            if kind == "inventory" and not args.commit:
                pending_saps |= {row["SAP_Code"] for row in plan["inserts"]}
            print(f"\n▶ {kind}: {_fmt(bi._summary(plan))}")
            for w in plan.get("warnings", []):
                print(f"    ⚠ {w}")
            for rej in plan.get("rejects", [])[:10]:
                print(f"    ✗ {rej}")
            if len(plan.get("rejects", [])) > 10:
                print(f"    … {len(plan['rejects']) - 10} more rejects")
            if args.commit:
                await appliers[kind](session, plan, args.user)
                await session.commit()
                print("    ✅ committed")

    # ── verification: DB stock == workbook Current Stock ────────────────────
    # Header-driven like the importers (the operator reorders/adds columns in
    # the tracking workbook — positional reads would silently mis-verify).
    headers, vrows = bi._sheet_rows(datas["inventory"], "Inventory",
                                    ("sap code", "current stock"))
    sap_i = bi._col(headers, "SAP CODE", "SAP_Code")
    cur_i = bi._col(headers, "Current Stock", "Current_Stock")
    expected = {}
    for r in vrows:
        sap = bi._s(r[sap_i]) if sap_i < len(r) else None
        cur = bi._f(r[cur_i]) if cur_i < len(r) else None
        if sap and cur is not None:
            expected[sap] = cur
    async with SessionLocal() as session:
        db = {r[0]: float(r[1]) for r in (await session.execute(text('''
            SELECT i."SAP_Code",
                   COALESCE(i."Opening_Stock",0)
                 + COALESCE((SELECT SUM(r."Quantity") FROM receipts r
                             WHERE r."SAP_Code"=i."SAP_Code"),0)
                 - COALESCE((SELECT SUM(c."Quantity") FROM consumption c
                             WHERE c."SAP_Code"=i."SAP_Code"),0)
                 - COALESCE((SELECT SUM(t."Quantity") FROM returns t
                             WHERE t."SAP_Code"=i."SAP_Code"),0)
            FROM inventory i'''))).all()}
    mismatches = [(sap, expected[sap], db.get(sap))
                  for sap in expected
                  if db.get(sap) is None or abs(db[sap] - expected[sap]) > 1e-6]
    print(f"\n== STOCK VERIFICATION: {len(expected) - len(mismatches)}/"
          f"{len(expected)} SAPs match the workbook's Current Stock ==")
    for sap, want, got in mismatches[:15]:
        print(f"    ✗ {sap}: workbook={want} db={got}")
    if len(mismatches) > 15:
        print(f"    … {len(mismatches) - 15} more")
    failures += len(mismatches) if args.commit else 0
    await engine.dispose()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
