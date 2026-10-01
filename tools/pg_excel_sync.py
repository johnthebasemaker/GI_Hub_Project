#!/usr/bin/env python3
"""
tools/pg_excel_sync.py — one-command, idempotent Excel → PostgreSQL sync.

Reads the tracking workbooks that live in the repo root and syncs them into
PostgreSQL in a single ATOMIC transaction. Dry-run by default:

    DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub \
    .venv/bin/python tools/pg_excel_sync.py --site CNCEC            # dry-run
    …                                       --site CNCEC --commit   # writes

SME BY DEFAULT — THE ERP LEDGER IS OPT-IN (2026-08-02 STRICT DECOUPLING)
------------------------------------------------------------------------
The SME estimator is a separate pool from the ERP warehouse; a routine
"re-sync my updated spreadsheets" must not be able to rewrite the live
warehouse master or append to its ledger by accident. So the command above
touches the SME tables ONLY:

    Equipment.xlsx                      → sme_equipment (+ sme_sqm_progress)
    For_1_SQM.xlsx                      → sme_recipe
    Materials_DetailsAvailable_Qty.xlsx → sme_inventory_seed

The ERP half is still here and still works, but you have to ask for it:

    …/pg_excel_sync.py --site CNCEC --erp             # SME + ERP
    …/pg_excel_sync.py --site CNCEC --kinds inventory,ledger --commit

    CNCEC_Inventory.xlsx → inventory master + receipts/consumption/returns

`--kinds` naming an ERP kind is itself the opt-in — `--erp` is only shorthand
for "everything". Workbooks are read lazily, so a missing CNCEC_Inventory.xlsx
no longer blocks an SME-only run.

WHY THIS EXISTS ALONGSIDE tools/excel_sync.py
---------------------------------------------
`excel_sync.py` is the 2026-07-13/18 injection script: it mirrors the HTTP
import endpoint, commits per-kind, and the documented cutover chain needs it
run THREE times in a specific order plus `excel_sync_reconcile.py`. Getting
that order wrong silently produces a half-synced database. This tool is the
single entry point for routine re-syncs, and hardens three things:

  1. ATOMIC — one transaction across all five kinds. A failure anywhere rolls
     the whole sync back, instead of leaving inventory loaded and the ledger
     not. It also removes `excel_sync.py`'s `pending_saps` shim: because the
     inventory rows are flushed inside the same transaction, the ledger's
     soft-FK check sees brand-new SAPs naturally.
  2. POSTGRES-NATIVE UPSERTS — every master-data write is
     `INSERT … ON CONFLICT (<natural key>) DO UPDATE`, so a concurrent writer
     or a stale plan snapshot can no longer raise a duplicate-key error
     mid-run. See "IDEMPOTENCY" below for where this does and does not hold.
  3. POSTGRES-ONLY — refuses to run against anything that is not a Postgres
     URL, and refuses outright if the URL mentions the legacy SQLite file.
     `gi_database.db` is never opened, read, or written by this tool.

MAPPING LOGIC IS DELIBERATELY NOT DUPLICATED
--------------------------------------------
Column mapping lives in `backend/api/bulk_import.py` and nowhere else. That
module is what `POST /import/{kind}` runs, and it encodes rules that took
several passes to get right — Material_Code re-mapping, the ledger's
three-tier (day, SAP, qty, ref) reconcile, category canonicalisation for the
MTC hard-block, Name-identity tag backfill, Done_SQM preservation. Re-deriving
any of that in pandas would create a second set of rules that drifts from the
API's, and the two would disagree about the same spreadsheet. So this script
imports the planners and only replaces the WRITE path. A workbook restructure
is still a one-file fix.

For the same reason the reader is openpyxl (via bulk_import), not pandas:
pandas is present only transitively via streamlit for the legacy app and is
not in `backend/requirements.txt`, so a production-path tool must not import
it. All columns resolve BY HEADER NAME, so reordering or adding workbook
columns is safe and unmapped columns are reported as warnings.

IDEMPOTENCY
-----------
Re-running with --commit is a no-op. Proven by suite AW in service_tests.

  * Master data (inventory, sme_equipment, sme_recipe, sme_inventory_seed) —
    every table has a real natural key, so ON CONFLICT DO UPDATE genuinely
    converges. The SET clause is COALESCE(excluded.col, table.col): a column
    the workbook leaves blank never erases data already in the DB.
  * Ledger (receipts / consumption / returns) — ⚠️ REWRITTEN 2026-09-16.
    There is still NO uniqueness on a MOVEMENT, and there must never be:
    (Date, SAP, Tank No.) is shared by 2,785 of the real Consumption Log's
    4,394 rows, and two identical drums are two real movements. What IS
    unique is a per-row PROVENANCE LABEL the sync writes into `Source_Ref`:

        XLSX:<site>:<kind>:<day>:<sap>:<hash of Tank No./DN No./Reason>:<n>

    `<n>` is allocated once and never renumbered, so genuine duplicates are
    `:1` and `:2`. A partial unique index on (Site_ID, Source_Ref) WHERE it
    starts `XLSX:` makes the write a true `INSERT … ON CONFLICT DO UPDATE`:
    a re-run converges to zero writes, and an edited quantity updates the
    SAME row id — which is what keeps every Phase 13 attribution linked.

    Each run MATCHES workbook lines to rows inside a (day, SAP, ref) group,
    strongest evidence first (identical → same quantity → quantity edited →
    ref edited), then writes. Rows the APP posted (`SME_EXEC:`, `SMR:`, stock
    adjustments) may absorb an identical workbook line but are never rewritten
    or relabelled; a disagreement is reported as a CONFLICT. A labelled row the
    workbook no longer names is reported as VANISHED and kept, unless
    `--prune-vanished` is given — which deletes only rows nothing references,
    and refuses a workbook that looks truncated unless `--force-prune`.
    Both the CLI and `POST /import/ledger` call `bulk_import.apply_ledger`;
    there is one ledger writer. Proven by suite DC.
  * One caveat, reported at runtime: `sme_recipe`'s natural key includes
    SAP_Code, and Postgres treats NULLs as distinct in a unique constraint.
    A recipe line with no SAP_Code cannot be caught by ON CONFLICT. The plan
    still classifies it correctly (it keys on `sap or ""`), so this only
    matters as a lost safety net, and the run prints how many such lines it saw.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

# Set BEFORE importing backend.api.config: never pick up a developer's local
# secrets, never start the background scheduler for a one-shot CLI run.
os.environ.setdefault("GI_DOTENV", "0")
os.environ.setdefault("GI_SCHEDULER", "0")
os.environ.setdefault("JWT_SECRET", "pg-excel-sync-offline-run-key-32bytes!!")

# kind → workbook filename. Order is the SAFE SEQUENCE and is load-bearing:
#   inventory before ledger  — the ledger rejects SAPs missing from the master.
#   recipes before equipment — plan_sme_equipment backfills a missing
#                              Lining_System_Code from Lining_System_Short_Name
#                              using sme_recipe as the lookup. Loading equipment
#                              first (as the older excel_sync.py does) leaves
#                              that map empty on a fresh database, so short-name
#                              rows are silently skipped.
WORKBOOKS: dict[str, str] = {
    "inventory": "CNCEC_Inventory.xlsx",
    "ledger": "CNCEC_Inventory.xlsx",
    "sme-recipes": "For_1_SQM.xlsx",
    "sme-manpower": "Manpower_Hour_Details.xlsx",
    "sme-equipment": "Equipment.xlsx",
    "sme-materials": "Materials_DetailsAvailable_Qty.xlsx",
}

# 2026-08-02 STRICT DECOUPLING. `inventory` and `ledger` write the live ERP
# warehouse — the master every stock figure, FEFO lot and PR is computed from.
# They are NOT in the default run: the everyday invocation is an SME re-sync,
# and a typo in a spreadsheet must not be able to append to the warehouse
# ledger as a side effect. Order within each group stays the SAFE SEQUENCE.
ERP_KINDS = ("inventory", "ledger")
SME_KINDS = tuple(k for k in WORKBOOKS if k not in ERP_KINDS)

# kind → (table key in bulk_import, natural-key columns for ON CONFLICT).
# These MUST match a real UNIQUE constraint or PK, or Postgres raises
# "no unique or exclusion constraint matching the ON CONFLICT specification".
# Verified against backend/models.py:
#   inventory          PK  (SAP_Code)                       + UQ (Material_Code)
#   sme_equipment      UQ  (Site_ID, Equipment_Tag_No, Lining_System_Code)
#   sme_recipe         UQ  (Lining_System_Code, Execution_Sub_Activity_Code,
#                            Material_Code, SAP_Code)   ← widened f1d3b7a24c60
#   sme_inventory_seed PK  (Material_Code, SAP_Code)
CONFLICT_KEYS: dict[str, tuple[str, ...]] = {
    "inventory": ("SAP_Code",),
    "sme-equipment": ("Site_ID", "Equipment_Tag_No", "Lining_System_Code"),
    # 2026-08 ESC: the sub-activity is part of the identity. LSC2's Resin A is
    # 0.2700 under ESC21 (primer) and 1.4674 under ESC22 (screed); without ESC
    # here the second row overwrites the first instead of joining it.
    "sme-recipes": ("Lining_System_Code", "Execution_Sub_Activity_Code",
                    "Material_Code", "SAP_Code"),
    # 2026-08 manpower norms. Five parts, because CV blasting is filed under
    # ESC1 twice with different crews and nothing narrower separates them.
    "sme-manpower": ("Type", "Lining_System_Code", "Execution_Sub_Activity_Code",
                     "Activity", "Variant_Key"),
    # 2026-07-30 COMPONENT IDENTITY: one seed row per PHYSICAL component. Was
    # ("Material_Code",), which made the four Comp-A/B/C/D drums of a PU system
    # collide onto one row and overwrite each other.
    "sme-materials": ("Material_Code", "SAP_Code"),
}

LEDGER_KINDS = ("ledger",)
AUDIT_ACTION = {
    "inventory": ("BULK_IMPORT_INVENTORY", "inventory"),
    "sme-equipment": ("BULK_IMPORT_SME_EQUIPMENT", "sme_equipment"),
    "sme-recipes": ("BULK_IMPORT_SME_RECIPES", "sme_recipe"),
    "sme-manpower": ("BULK_IMPORT_SME_MANPOWER", "sme_manpower_norm"),
    "sme-materials": ("BULK_IMPORT_SME_MATERIALS", "sme_inventory_seed"),
}


# ─── preflight: the right interpreter ────────────────────────────────────────
def require_project_env() -> None:
    """Fail with an actionable message when run outside the project venv.

    The planners live in `backend/api/bulk_import.py`, which is FastAPI code, so
    the system python dies with a bare `ModuleNotFoundError: No module named
    'fastapi'` and a traceback pointing at bulk_import line 43 — which names the
    symptom, not the cause (wrong interpreter). Say it plainly instead.

    Dropping the FastAPI dependency is NOT the fix: it would mean forking the
    column-mapping logic out of bulk_import, which is precisely the drift this
    tool is built to avoid (see the module docstring). FastAPI is a declared
    dependency in backend/requirements.txt; the venv already has it.
    """
    import importlib.util
    missing = [m for m in ("fastapi", "sqlalchemy", "openpyxl", "asyncpg",
                           "xlsxwriter")
               if importlib.util.find_spec(m) is None]
    if not missing:
        return
    venv = os.path.join(_ROOT, ".venv", "bin", "python")
    hint = (f"{venv} tools/pg_excel_sync.py" if os.path.exists(venv)
            else "python3 -m venv .venv && .venv/bin/pip install -r requirements.txt")
    raise SystemExit(
        f"❌ wrong interpreter: {sys.executable}\n"
        f"   missing module(s): {', '.join(missing)}\n\n"
        f"   This script reuses the API's import planners, so it needs the "
        f"project virtualenv.\n   Run it with:\n\n       {hint}\n")


# ─── safety: PostgreSQL only, never the legacy SQLite file ───────────────────
def _redact(url: str) -> str:
    """Hide any password before a URL reaches stdout or a log."""
    if "@" not in url:
        return url
    head, tail = url.rsplit("@", 1)
    if ":" in head.rsplit("/", 1)[-1]:
        head = head[:head.rindex(":")] + ":***"
    return f"{head}@{tail}"


def assert_postgres_target(url: str) -> None:
    """PRIME DIRECTIVE: this tool targets PostgreSQL exclusively.

    `gi_database.db` is the live legacy database and is untouchable. A stray
    `DATABASE_URL=sqlite:///gi_database.db` in the environment must abort the
    run loudly rather than quietly writing to it.
    """
    low = url.strip().lower()
    # Test the SCHEME first. A substring hunt for ".db" would reject a perfectly
    # good URL whose HOST happens to contain it (db.example.com, x.dbhost.net) —
    # a false refusal in production is as bad as a missing guard.
    if low.startswith("postgresql"):
        if "gi_database" in low:
            raise SystemExit(
                f"❌ refusing to run: DATABASE_URL={_redact(url)} names "
                f"gi_database — that is the untouchable legacy database.")
        return
    if low.startswith("sqlite") or low.endswith(".db") or "gi_database" in low:
        raise SystemExit(
            f"❌ refusing to run: DATABASE_URL={_redact(url)} looks like a SQLite "
            f"file. This tool writes to PostgreSQL only — the legacy "
            f"gi_database.db is never touched.")
    raise SystemExit(
        f"❌ refusing to run: DATABASE_URL={_redact(url)} is not a PostgreSQL "
        f"URL. Expected postgresql+asyncpg:// (or a psycopg2/postgres:// "
        f"form, which is normalised automatically).")


# ─── PostgreSQL-native upsert ────────────────────────────────────────────────
async def native_upsert(session, table, rows: list[dict],
                        conflict_cols: tuple[str, ...]) -> int:
    """INSERT … ON CONFLICT (conflict_cols) DO UPDATE, batched.

    Planned rows carry only the columns the workbook actually filled, so the
    batch is grouped by column signature — a single multi-row VALUES clause
    requires every row to share the same keys.

    The SET clause is COALESCE(excluded.col, table.col) rather than a bare
    excluded.col: if this row turns out to already exist (concurrent writer, or
    a plan computed against a snapshot that has since moved), a column absent
    from the workbook must NOT blank the value already stored. That mirrors how
    every plan_* function diffs only non-None fields.
    """
    if not rows:
        return 0
    from sqlalchemy import func
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault(tuple(sorted(r)), []).append(r)

    written = 0
    for _sig, batch in groups.items():
        stmt = pg_insert(table).values(batch)
        updatable = [c for c in batch[0] if c not in conflict_cols]
        set_ = {c: func.coalesce(stmt.excluded[c], table.c[c]) for c in updatable}
        if "updated_at" in table.c:
            set_["updated_at"] = func.now()
        stmt = (stmt.on_conflict_do_update(index_elements=list(conflict_cols),
                                           set_=set_)
                if set_ else
                stmt.on_conflict_do_nothing(index_elements=list(conflict_cols)))
        await session.execute(stmt)
        written += len(batch)
    return written


# ─── per-kind apply (mapping comes from bulk_import; only writes live here) ──
async def apply_master(session, kind: str, plan: dict, username: str,
                       site: str | None = None) -> dict:
    """Apply one master-data plan with native upserts. Returns write counts."""
    from sqlalchemy import update as sa_update
    from sqlalchemy import and_ as sa_and
    from sqlalchemy import delete as sa_delete
    from sqlalchemy import func

    import backend.api.bulk_import as bi
    from backend.api.services.ledger import write_audit

    table = {"inventory": bi.inventory_t, "sme-equipment": bi.equipment_t,
             "sme-recipes": bi.recipe_t, "sme-materials": bi.seed_t}[kind]

    # Inventory only: free Material_Codes that the workbook re-maps to a new
    # SAP, BEFORE the upserts, or the UQ on Material_Code trips mid-plan.
    for rel in plan.get("releases", []):
        await session.execute(
            sa_update(table)
            .where(table.c["SAP_Code"] == rel["sap"],
                   table.c["Material_Code"] == rel["mat"])
            .values(Material_Code=None))

    inserted = await native_upsert(session, table, plan["inserts"],
                                   CONFLICT_KEYS[kind])

    # Targeted UPDATEs for rows the plan matched: `diff` holds only the columns
    # that actually changed, so re-running writes the same values and converges.
    where_for = {
        "inventory": lambda u: table.c["SAP_Code"] == u["SAP_Code"],
        "sme-equipment": lambda u: table.c["id"] == u["id"],
        "sme-recipes": lambda u: table.c["id"] == u["id"],
        "sme-materials": lambda u: sa_and(
            table.c["Material_Code"] == u["Material_Code"],
            table.c["SAP_Code"] == u["SAP_Code"]),
    }[kind]
    for u in plan["updates"]:
        vals = dict(u["diff"])
        if "updated_at" in table.c:
            vals["updated_at"] = func.now()
        await session.execute(sa_update(table).where(where_for(u)).values(**vals))

    # sme-materials only: retire SAP-less placeholder seed rows the workbook has
    # superseded with real per-component rows. The frozen legacy SQLite seed has
    # no SAP_Code column, so a cutover lands every material as one blank-SAP row;
    # left behind it would double-count the stock its components now carry.
    for s in plan.get("stale", []):
        await session.execute(sa_delete(table).where(
            table.c["Material_Code"] == s["Material_Code"],
            table.c["SAP_Code"] == s["SAP_Code"]))

    # Equipment carries a second table: re-seed the SQM baseline. done_sqm=None
    # PRESERVES recorded progress (legacy sme_bootstrap contract).
    if kind == "sme-equipment":
        from backend.api.sme_master import _upsert_progress
        for row in plan["inserts"]:
            await _upsert_progress(session, site, row["Equipment_Tag_No"],
                                   row["Lining_System_Code"],
                                   original_sqm=row["Surface_Area_SQM"],
                                   done_sqm=None)
        for u in plan["updates"]:
            await _upsert_progress(session, site, u["tag"], u["code"],
                                   original_sqm=u["sqm"], done_sqm=None)

    action, target = AUDIT_ACTION[kind]
    await write_audit(session, username, action, target,
                      f"pg_excel_sync: +{len(plan['inserts'])} "
                      f"~{len(plan['updates'])} ={plan['unchanged']} "
                      f"rejected={len(plan['rejects'])}")
    out = {"upserted": inserted, "updated": len(plan["updates"])}
    if plan.get("stale"):
        out["retired"] = len(plan["stale"])
    return out


async def apply_ledger(session, plan: dict, username: str) -> dict:
    """Delegates to `bulk_import.apply_ledger` — THE one ledger writer.

    This file used to keep its own append-only copy beside the HTTP import's,
    and the two differed (this one counted, that one did not). A ledger
    written two ways is a ledger that can drift two ways, so both now call the
    same function: stamp labels by id, then one `INSERT … ON CONFLICT
    (Site_ID, Source_Ref) DO UPDATE` per batch. See its docstring.
    """
    import backend.api.bulk_import as bi
    return await bi.apply_ledger(session, plan, username)


# ─── reporting ───────────────────────────────────────────────────────────────
def format_summary(summary) -> str:
    if isinstance(summary, dict) and "receipts" in summary:
        return "\n      ".join(
            f"{k:12s} +{v['inserts']} new  ~{v.get('updates', 0)} edited "
            f"(qty {v['corrections']})  ={v['matched']} unchanged  "
            f"adopted={v.get('adopted', 0)}  relabelled={v.get('relabelled', 0)}  "
            f"conflicts={v.get('conflicts', 0)}  vanished={v.get('vanished', 0)}  "
            f"0skip={v['zero_skipped']}  dbonly={v['db_only']}"
            for k, v in summary.items())
    return (f"+{summary['inserts']} new  ~{summary['updates']} changed  "
            f"={summary['unchanged']} unchanged  "
            f"rejected={summary['rejects']}")


async def verify_stock(session, inventory_bytes: bytes
                       ) -> tuple[list[tuple], dict[str, float]]:
    """The workbook's Current Stock column is the tracking truth. Recompute
    Opening_Stock + Σreceipts − Σconsumption − Σreturns per SAP and list any
    SAP where the DB disagrees. Header-driven, like the importers."""
    from sqlalchemy import text
    import backend.api.bulk_import as bi

    headers, rows = bi._sheet_rows(inventory_bytes, "Inventory",
                                   ("sap code", "current stock"))
    sap_i = bi._col(headers, "SAP CODE", "SAP_Code")
    cur_i = bi._col(headers, "Current Stock", "Current_Stock")
    expected: dict[str, float] = {}
    for r in rows:
        sap = bi._s(r[sap_i]) if sap_i is not None and sap_i < len(r) else None
        cur = bi._f(r[cur_i]) if cur_i is not None and cur_i < len(r) else None
        if sap and cur is not None:
            expected[sap] = cur
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
    return [(sap, expected[sap], db.get(sap)) for sap in expected
            if db.get(sap) is None or abs(db[sap] - expected[sap]) > 1e-6], expected


# ─── SME reseed (workbook RENUMBERED the Lining_System_Codes) ────────────────
# An upsert is the WRONG operation when the workbook renumbers system codes:
# the old-code rows have no counterpart in the file, so they linger and
# double-count SQM. The trio is replaced wholesale instead. Off by default.
RESEED_SQL = {
    "sme-equipment": [("sme_sqm_progress",
                       'DELETE FROM sme_sqm_progress WHERE "Site_ID" = :site'),
                      ("sme_equipment",
                       'DELETE FROM sme_equipment WHERE "Site_ID" = :site')],
    "sme-recipes": [("sme_recipe", "DELETE FROM sme_recipe")],
    # norm_role cascades off norm, but naming it keeps the dry-run count honest
    "sme-manpower": [("sme_manpower_norm_role",
                      "DELETE FROM sme_manpower_norm_role"),
                     ("sme_manpower_norm", "DELETE FROM sme_manpower_norm")],
    "sme-materials": [("sme_inventory_seed", "DELETE FROM sme_inventory_seed")],
}


async def run_reseed(session, kind: str, site: str, commit: bool) -> None:
    from sqlalchemy import text
    for table, sql in RESEED_SQL[kind]:
        params = {"site": site} if ":site" in sql else {}
        if commit:
            res = await session.execute(text(sql), params)
            print(f"      reseed: dropped {res.rowcount} {table} row(s)")
        else:
            probe = ("SELECT COUNT(*) FROM ("
                     + sql.replace("DELETE FROM", "SELECT 1 FROM", 1) + ") q")
            n = (await session.execute(text(probe), params)).scalar()
            print(f"      reseed (dry-run): would drop {n} {table} row(s)")
            if n:
                # The plan below is computed against rows this reseed WOULD
                # have deleted but, being a dry-run, has not. So any warning
                # about pre-existing rows ("not in the file", "remain
                # unclassified") describes state that --commit removes first.
                print(f"      note: the plan below still sees those {n} row(s) "
                      f"— with --commit they are deleted BEFORE planning, so "
                      f"warnings about them do not apply to a real run")


# ─── main ────────────────────────────────────────────────────────────────────
async def main() -> int:
    ap = argparse.ArgumentParser(
        description="Idempotent Excel → PostgreSQL sync (dry-run by default).")
    ap.add_argument("--dir", default=_ROOT,
                    help="folder holding the four workbooks (default: repo root)")
    ap.add_argument("--site", default="CNCEC", help="Site_ID to stamp on rows")
    ap.add_argument("--commit", action="store_true",
                    help="APPLY the sync. Without this the run is a dry-run "
                         "that only reports what would change.")
    ap.add_argument("--dry-run", action="store_true",
                    help="explicit no-op default; refuses to combine with --commit")
    ap.add_argument("--kinds", default=None,
                    help=f"comma list to run EXACTLY (choices: "
                         f"{','.join(WORKBOOKS)}). Naming an ERP kind "
                         f"({'/'.join(ERP_KINDS)}) is itself the opt-in.")
    ap.add_argument("--erp", action="store_true",
                    help=f"also sync the ERP warehouse kinds "
                         f"({', '.join(ERP_KINDS)}) from CNCEC_Inventory.xlsx. "
                         f"Off by default: the SME estimator is a separate "
                         f"pool and a routine re-sync must not write the live "
                         f"warehouse master or ledger.")
    ap.add_argument("--sme-reseed", action="store_true",
                    help="replace the SME trio wholesale instead of upserting "
                         "— required when the workbook RENUMBERS system codes")
    ap.add_argument("--force-drop-progress", action="store_true",
                    help="let --sme-reseed drop progress rows that hold "
                         "recorded Done_SQM (otherwise the run aborts)")
    ap.add_argument("--prune-vanished", action="store_true",
                    help="DELETE ledger rows this sync wrote earlier that the "
                         "workbook no longer names (a Date or SAP edit looks "
                         "like this) — only rows nothing references; an "
                         "attributed row is kept and reported. Off by default.")
    ap.add_argument("--force-prune", action="store_true",
                    help="let --prune-vanished run even when so many rows are "
                         "missing that the workbook looks truncated")
    ap.add_argument("--user", default="pg-excel-sync",
                    help="username stamped on the audit rows")
    args = ap.parse_args()

    if args.commit and args.dry_run:
        print("❌ --commit and --dry-run are mutually exclusive.")
        return 2

    require_project_env()   # before any backend import, so the error is useful
    from backend.api.config import async_database_url
    url = async_database_url()
    assert_postgres_target(url)

    import backend.api.bulk_import as bi
    from backend.api.db import SessionLocal, engine

    # DEFAULT = SME only (strict decoupling). --erp widens it; an explicit
    # --kinds is taken literally, so it can select either side.
    kinds = list(WORKBOOKS) if args.erp else list(SME_KINDS)
    if args.kinds:
        want = {k.strip() for k in args.kinds.split(",") if k.strip()}
        unknown = want - set(WORKBOOKS)
        if unknown:
            print(f"❌ unknown --kinds {sorted(unknown)} "
                  f"(choose from {list(WORKBOOKS)})")
            return 2
        kinds = [k for k in WORKBOOKS if k in want]  # keep the safe sequence

    # Read every workbook up front: a missing file must abort before any write.
    data: dict[str, bytes] = {}
    for kind in kinds:
        path = os.path.join(os.path.expanduser(args.dir), WORKBOOKS[kind])
        if not os.path.exists(path):
            print(f"❌ missing workbook: {path}")
            return 2
        with open(path, "rb") as fh:
            data[kind] = fh.read()

    mode = "COMMIT" if args.commit else "DRY-RUN"
    erp_kinds = [k for k in kinds if k in ERP_KINDS]
    print(f"== pg_excel_sync ({mode}) ==")
    print(f"   target : {_redact(url)}")
    print(f"   site   : {args.site}")
    print(f"   kinds  : {', '.join(kinds)}")
    # Say plainly which side of the wall this run writes. Silence here is how
    # an SME re-sync would quietly append to the warehouse ledger.
    scope = (f"⚠️  ERP + SME — this run WRITES the live warehouse "
             f"({', '.join(erp_kinds)})" if erp_kinds else
             "SME tables only — the ERP warehouse is untouched")
    print(f"   scope  : {scope}")
    # State the sheet scope out loud. CNCEC_Inventory.xlsx went from 5 to 14
    # worksheets on 2026-08-04 and the sync reads exactly four of them; a run
    # that silently ignores ten tabs should say so rather than leave the
    # operator to infer it.
    if "inventory" in data or "ledger" in data:
        _wb = data.get("inventory") or data.get("ledger")
        _ignored = bi.ignored_sheets(_wb)
        print(f"   sheets : reading {', '.join(bi._CONSUMED_SHEETS)}")
        if _ignored:
            print(f"            ignoring {len(_ignored)}: {', '.join(_ignored)}")

    # Dry-run only: the ledger's soft-FK check cannot see inventory rows that
    # were never written, so hand it the SAPs the inventory plan would insert.
    pending_saps: set[str] = set()
    totals: dict[str, dict] = {}
    exit_code = 0

    # ONE session, ONE transaction: either the whole sync lands or none of it.
    async with SessionLocal() as session:
        if args.sme_reseed:
            done = (await session.execute(_progress_probe(), {"site": args.site})).scalar()
            if done and not args.force_drop_progress:
                print(f"\n❌ --sme-reseed would drop {done} progress row(s) that "
                      f"hold recorded Done_SQM.\n   Re-run with "
                      f"--force-drop-progress once you accept losing it.")
                return 3

        for kind in kinds:
            print(f"\n▶ {kind}  ({WORKBOOKS[kind]})")
            # THE APP WINS: capture operator SQM overrides BEFORE a reseed
            # deletes the rows carrying them, so they can be put back after the
            # workbook has been re-applied.
            sqm_snapshot = {}
            if kind == "sme-equipment":
                sqm_snapshot = await bi.snapshot_sqm_overrides(session, args.site)
            if args.sme_reseed and kind in RESEED_SQL:
                await run_reseed(session, kind, args.site, args.commit)

            if kind == "inventory":
                plan = await bi.plan_inventory(session, data[kind], args.site)
            elif kind == "ledger":
                plan = await bi.plan_ledger(session, data[kind], args.site,
                                            extra_saps=pending_saps)
            elif kind == "sme-equipment":
                plan = await bi.plan_sme_equipment(session, data[kind], args.site)
            elif kind == "sme-recipes":
                plan = await bi.plan_sme_recipes(session, data[kind])
            elif kind == "sme-manpower":
                plan = await bi.plan_sme_manpower_norms(session, data[kind])
            else:
                plan = await bi.plan_sme_materials(session, data[kind])

            if kind == "inventory" and not args.commit:
                pending_saps |= {r["SAP_Code"] for r in plan["inserts"]}

            print(f"      {format_summary(bi._summary(plan))}")
            if kind == "ledger" and plan.get("lots"):
                # Phase 16 — `Serial No.` read by the item (services/lots.py)
                _lr = plan["lots"]
                print(f"      lots            {_lr['rows']} row(s) carry a lot "
                      f"(Surface Shield batch / roll batch) · "
                      f"{len(_lr['multi'])} cell(s) with several lots · "
                      f"{len(_lr['roll_fixed'])} roll no. corrected")

            # ── where things live ──
            # Racks ride on the inventory sheet, assets on the Consumption Log.
            # Both READ THE SHEET rather than the plan, because a place is
            # STATE: it must seed from a workbook whose rows are already loaded,
            # and a re-run must converge. Both are create-if-absent, so the app
            # keeps every status, rack and GPS fix a human has set.
            racks = assets = None
            if kind == "inventory":
                racks = await bi.plan_rack_locations(
                    session, data[kind], args.site,
                    extra_saps={r["SAP_Code"] for r in plan["inserts"]})
                if racks["racks"] or racks["links"]:
                    print(f"      racks           "
                          f"+{len(racks['racks'])} location(s)  "
                          f"+{len(racks['links'])} material link(s)  "
                          f"({racks['kept']} kept by the app)")
                elif racks["column"]:
                    print(f"      racks           column "
                          f"{racks['column']!r} present, {racks['blank']} row(s) "
                          f"blank — nothing to seed")
                for w in racks["warnings"]:
                    print(f"      ⚠ {w}")

            # Surface-Shield routing rides on the ledger plan: the SME log is
            # derived from the consumption rows that are ABOUT to be inserted,
            # so `plan_ledger`'s reconcile makes it idempotent for free.
            routing = None
            if kind == "ledger":
                routing = await bi.plan_sme_routing(session, args.site, plan)
                assets = await bi.plan_asset_units(session, data[kind], args.site,
                                                   extra_saps=pending_saps)
                if assets["inserts"] or assets["existing"]:
                    print(f"      assets          "
                          f"+{len(assets['inserts'])} unit(s)  "
                          f"({assets['existing']} already tracked)  ·  "
                          f"{assets['consumable_rows']} consumable row(s)")
                for w in assets["warnings"]:
                    print(f"      ⚠ {w}")
                for rej in assets["rejects"][:5]:
                    print(f"      ✗ asset row {rej['row']} SAP {rej['sap']}: "
                          f"{rej['reason']}")
                if routing["log_inserts"] or routing["aliases"]:
                    print(f"      surface-shield  "
                          f"+{len(routing['log_inserts'])} SME log row(s)  "
                          f"({routing['unassigned']} unassigned)  ·  "
                          f"{len(routing['aliases'])} tank alias/es")
                for w in routing["warnings"]:
                    print(f"      ⚠ {w}")

            for w in plan.get("warnings", []):
                print(f"      ⚠ {w}")
            if kind == "ledger":
                for _k, _sec in plan["sections"].items():
                    for c in _sec.get("conflicts", [])[:10]:
                        print(f"        ✗ {_k} id {c['id']} {c['date']} SAP {c['sap']}"
                              f" {c['ref'] or ''}: workbook {c['workbook_qty']:g}"
                              f" vs app {c['app_qty']:g} ({c['source_ref'] or 'app row'})"
                              f" — not changed")
                    for v in _sec.get("vanished", [])[:10]:
                        print(f"        · {_k} id {v['id']} {v['date']} SAP {v['sap']}"
                              f" {v['ref'] or ''} qty {v['qty']:g} — no longer in"
                              f" the workbook, NOT deleted")
            for rej in plan.get("rejects", [])[:10]:
                print(f"      ✗ {rej}")
            if len(plan.get("rejects", [])) > 10:
                print(f"      … {len(plan['rejects']) - 10} more rejects")

            # Rows the DATABASE holds that this workbook does not name. The
            # counts line above can only describe rows that ARRIVED, so before
            # this a rename looked identical to a clean run: the new row was an
            # insert, every other row matched, nothing was rejected, and the
            # superseded row was never mentioned by anything. Printed one per
            # line with its numbers, because "there is an orphan" is not
            # actionable and "CV/ESC1/ESC1 'Blasting Civil PU Area' crew 3 @ 40
            # /shift" is.
            orphans = plan.get("orphans", [])
            if orphans:
                print(f"      ⚠ {len(orphans)} row(s) in the database that this "
                      f"workbook does not name — NOT deleted:")
                for o in orphans[:10]:
                    print(f"        · id {o['id']}  {o['Type']}/"
                          f"{o['Lining_System_Code']}/"
                          f"{o['Execution_Sub_Activity_Code']}  "
                          f"{o['Activity']!r}"
                          + (f" [{o['Variant_Key']}]" if o.get("Variant_Key") else "")
                          + f"  crew {o.get('Crew_Size')} @ "
                            f"{o.get('Standard_Productivity_Per_Shift')} /shift")
                if len(orphans) > 10:
                    print(f"        … {len(orphans) - 10} more")

            # The ON CONFLICT safety net has a hole where the natural key is
            # NULLable — say so rather than implying full protection.
            if kind == "sme-recipes":
                blind = sum(1 for r in plan["inserts"] if not r.get("SAP_Code"))
                if blind:
                    print(f"      ⚠ {blind} recipe line(s) have no SAP_Code; "
                          f"Postgres treats NULL as distinct, so ON CONFLICT "
                          f"cannot dedupe them (the plan still can)")

            if args.commit:
                if kind == "ledger":
                    if args.prune_vanished:
                        _why = bi.prune_guard(plan)
                        if _why and not args.force_prune:
                            print("\n❌ --prune-vanished refused — nothing was "
                                  "written:")
                            for w in _why:
                                print(f"   {w}")
                            print("   Check the workbook is complete, or re-run "
                                  "with --force-prune once you accept it.")
                            await session.rollback()
                            return 4
                    totals[kind] = await apply_ledger(session, plan, args.user)
                    # Phase 16 — lots the ledger names that no receipt brought in
                    from backend.api.services import lots as _lots
                    _unk = await _lots.unknown_lots(session, args.site)
                    if _unk:
                        print(f"      ⚠ {len(_unk)} lot(s) used but never received for "
                              f"that SAP — kept as typed, correct them in the workbook:")
                        for _u in _unk[:15]:
                            print(f"          {_u['kind']:11s} SAP {_u['sap']:8s} lot "
                                  f"{_u['lot']!r} ×{_u['n']} qty {_u['qty']:g}"
                                  + (f"  (received under SAP {_u['received_under']})"
                                     if _u.get("received_under") else ""))
                        if len(_unk) > 15:
                            print(f"          … {len(_unk) - 15} more")
                    if args.prune_vanished:
                        _pr = await bi.prune_vanished(session, plan, args.user)
                        for _k, _r in _pr.items():
                            print(f"      ✂ {_k}: pruned {len(_r['deleted'])} vanished "
                                  f"row(s); kept {len(_r['kept'])} still referenced")
                            for _kept in _r["kept"][:10]:
                                print(f"          kept id {_kept['id']} — {_kept['reason']}")
                        totals[kind]["pruned"] = sum(len(r["deleted"])
                                                     for r in _pr.values())
                    # ⚠️ 2026-09-16: an in-place edit keeps the row's id, so a
                    # Phase 13 attribution stays linked — and is now measured
                    # against figures the ledger no longer holds. Say which, and
                    # tell the HODs once per site rather than once per row.
                    from backend.api.services import sme_link as _sl
                    _stale = await _sl.stale_attributions(
                        session, consumption_ids=totals[kind]["updated_ids"]
                        .get("consumption", []))
                    if _stale:
                        print(f"      ⚠ {len(_stale)} Surface Shield attribution(s) "
                              f"now out of date with an edited ledger row — back "
                              f"in the queue marked Edited:")
                        for _s in _stale[:10]:
                            print(f"          log {_s['id']} ({_s['status']})  "
                                  f"consumption {_s['Consumption_ID']}  "
                                  f"{_s['Equipment_Tag_No']}/{_s['Lining_System_Code']}  "
                                  f"qty {_s['Actual_Qty']:g} → {_s['now_qty']:g}")
                        await _sl.notify_sync_edits(session, stale=_stale,
                                                    username=args.user)
                    totals[kind]["attributions_edited"] = len(_stale)
                    totals[kind].pop("updated_ids", None)
                    if routing is not None:
                        await bi.apply_sme_routing(session, routing, args.user)
                        totals[kind]["sme_logged"] = len(routing["log_inserts"])
                    if assets is not None:
                        totals[kind] |= await bi.apply_asset_units(
                            session, assets, args.user)
                        refreshed = await bi.refresh_asset_location_notes(
                            session, assets, args.site)
                        if refreshed:
                            print(f"      ↻ {refreshed} untouched asset(s) took "
                                  f"the workbook's new Location text")
                elif kind == "sme-manpower":
                    # Its own path, not apply_master's: a norm writes TWO
                    # tables (the benchmark and its per-role crew), and the
                    # generic native-upsert helper is single-table by design.
                    await bi.apply_sme_manpower_norms(session, plan, args.user)
                    totals[kind] = {"upserted": len(plan["inserts"]),
                                    "updated": len(plan["updates"])}
                else:
                    totals[kind] = await apply_master(session, kind, plan,
                                                      args.user, site=args.site)
                    if racks is not None:
                        # After the master upserts, inside the same transaction.
                        await session.flush()
                        totals[kind] |= await bi.apply_rack_locations(
                            session, racks, args.user)
                # Flush (do NOT commit): later kinds — notably the ledger's
                # soft-FK check against `inventory` — must see these rows, but
                # the whole sync stays a single rollback-able transaction.
                await session.flush()
                if kind == "sme-equipment":
                    diverged = await bi.restore_sqm_overrides(
                        session, args.site, sqm_snapshot)
                    if diverged:
                        print(f"      ⚠ {len(diverged)} equipment row(s) keep an "
                              f"operator SQM override (workbook differs):")
                        for d in diverged[:10]:
                            print(f"          {d['tag']} / {d['code']}  "
                                  f"workbook {d['workbook']:g} → "
                                  f"override {d['override']:g}"
                                  + (f"  ({d['by']})" if d["by"] else ""))
                        if len(diverged) > 10:
                            print(f"          … {len(diverged) - 10} more")
                    await session.flush()
                print(f"      ✅ staged {totals[kind]}")

        if args.commit:
            await session.commit()
            print("\n✅ COMMITTED — one atomic transaction")
        else:
            print("\n… dry-run only. Re-run with --commit to apply.")

        # Verification always runs, against whatever is now in the DB.
        if "inventory" in data:
            mismatches, expected = await verify_stock(session, data["inventory"])
            ok = len(expected) - len(mismatches)
            print(f"\n== STOCK VERIFICATION: {ok}/{len(expected)} SAPs match "
                  f"the workbook's Current Stock ==")
            for sap, want, got in mismatches[:15]:
                print(f"    ✗ {sap}: workbook={want} db={got}")
            if len(mismatches) > 15:
                print(f"    … {len(mismatches) - 15} more")
            # Only a COMMITTED run is expected to reconcile; a dry-run against
            # an unsynced database legitimately mismatches.
            if args.commit and mismatches:
                exit_code = 1
            # 2026-09-26: after a committed sync, store WHY each SAP differs, so
            # the Stock page shows it (services/stock_excel.py; read-only).
            if args.commit:
                from backend.api.services import stock_excel as SE
                # (the session is already in a transaction — verify_stock
                # read through it — so this commits rather than begin()s)
                diag = await SE.diagnose(session, data["inventory"], site_id=args.site)
                await SE.store(session, diag, site_id=args.site,
                               workbook=WORKBOOKS["inventory"], username=args.user)
                await session.commit()
                print(f"   stored for the Stock page — reasons: "
                      f"tools/stock_excel_check.py --marked writes a marked copy")

    await engine.dispose()
    return exit_code


def _progress_probe():
    from sqlalchemy import text
    return text('SELECT COUNT(*) FROM sme_sqm_progress WHERE "Site_ID"=:site '
                'AND COALESCE("Done_SQM",0)+COALESCE("Done_SQM_staged",0) > 0')


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
