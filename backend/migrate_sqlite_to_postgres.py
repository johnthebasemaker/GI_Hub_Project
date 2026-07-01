"""
Phase 5 — copy the SQLite `gi_database.db` into a target database using the
`backend/models.py` schema (SQLAlchemy). Idempotent, chunked, with per-table
row-count parity checks.

Design notes
------------
* SQLite stays authoritative until cutover. This script only *reads* the source
  SQLite DB (never writes to it) and *loads* a fresh target.
* The 3 remaining PK-less ledger tables (`consumption`, `receipts`, `returns`)
  have no explicit PK in SQLite — they relied on the implicit `rowid`.
  `models.py` gives them a `SERIAL id`; we copy **id := sqlite rowid** so the
  existing `posted_txn_ref` values ('C:{rowid}' / 'R:{rowid}') stay valid.
  (`system_settings` was already migrated to a real `id` PK in SQLite.)
* Only columns present in BOTH source and target are copied. Source-only columns
  are reported loudly (data that would NOT migrate — add them to models.py and
  regenerate, or accept the drop).
* Views are recreated from `models.SME_AND_DERIVED_VIEWS` best-effort, each in
  its own try/except; a dialect-sensitive view (e.g. `v_expiring_stock` uses
  SQLite `date('now')`/`julianday`) may need a hand-written Postgres definition
  — failures are reported, they do NOT abort the table/data migration.

Usage
-----
    # Real migration (Postgres target from --target or $DATABASE_URL):
    .venv/bin/python backend/migrate_sqlite_to_postgres.py \
        --source gi_database.db \
        --target postgresql+psycopg2://gihub:<pw>@localhost:5432/gihub --wipe

    # Dry-run validation (SQLite -> throwaway SQLite, no Postgres needed):
    .venv/bin/python backend/migrate_sqlite_to_postgres.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile

# Import the Declarative schema (this module lives in backend/, next to it).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import models  # noqa: E402  (backend/models.py)

from sqlalchemy import create_engine, func, insert, select, text  # noqa: E402
from sqlalchemy import Boolean, Date, DateTime, Float, Integer, Numeric  # noqa: E402

import datetime as _dt  # noqa: E402

_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M", "%Y-%m-%d",
)


def _coerce_dt(value):
    """SQLite stores DATETIME as TEXT; SQLAlchemy's DateTime type (and strict PG)
    want a Python datetime/date. Parse the common formats; '' / None → None;
    unparseable → None (counted by the caller)."""
    if value in (None, ""):
        return None
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value
    s = str(value)
    for fmt in _DT_FORMATS:
        try:
            return _dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _make_coercer(col):
    """Return a value-coercer for a column, bridging SQLite's loose typing to
    the target's strict types. Empty strings and unparseable values in
    numeric/date/bool columns become NULL (the caller counts these)."""
    t = col.type
    if isinstance(t, (DateTime, Date)):
        return _coerce_dt
    if isinstance(t, (Float, Numeric)):
        def _num(v):
            if v in (None, ""):
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        return _num
    if isinstance(t, Integer):
        def _int(v):
            if v in (None, ""):
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                try:
                    return int(float(v))
                except (TypeError, ValueError):
                    return None
        return _int
    if isinstance(t, Boolean):
        def _bool(v):
            if v in (None, ""):
                return None
            if isinstance(v, str):
                return v.strip().lower() in ("1", "true", "t", "yes", "y")
            return bool(v)
        return _bool
    return lambda v: v  # Text / LargeBinary — passthrough


# PostgreSQL-native view definitions for views whose SQLite text uses dialect-
# specific functions. Used only when the target is Postgres; otherwise the
# SQLite view text (models.SME_AND_DERIVED_VIEWS) is used verbatim.
#   v_expiring_stock: SQLite julianday()/date('now') → PG date arithmetic. The
#   `~ '^\d{4}-\d{2}-\d{2}'` guard ensures ::date never errors on a malformed
#   string (SQLite's date() returned NULL; PG's cast would raise).
PG_VIEW_OVERRIDES: dict[str, str] = {
    "v_expiring_stock": (
        "CREATE VIEW v_expiring_stock AS "
        "SELECT TRIM(r.SAP_Code) AS SAP_Code, "
        "       i.Equipment_Description AS Equipment_Description, i.UOM AS UOM, "
        "       COALESCE(r.Site_ID,'HQ') AS Site_ID, r.Quantity AS Quantity, "
        "       r.Supplier AS Supplier, r.PR_Number AS PR_Number, "
        "       r.Expiry_Date AS Expiry_Date, "
        "       (r.Expiry_Date::date - CURRENT_DATE) AS Days_Until_Expiry, "
        "       CASE WHEN r.Expiry_Date::date < CURRENT_DATE THEN 'Expired' "
        "            WHEN r.Expiry_Date::date <= CURRENT_DATE + 30 THEN 'Short-Dated' "
        "            ELSE 'Good' END AS Expiry_Status "
        "FROM receipts r LEFT JOIN inventory i "
        "  ON TRIM(i.SAP_Code) = TRIM(r.SAP_Code) "
        "WHERE r.Expiry_Date IS NOT NULL AND r.Expiry_Date <> '' "
        "  AND r.Expiry_Date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'"
    ),
}


# Tables that lack an explicit `id` in SQLite → populate id := rowid on copy.
def _needs_rowid_id(src_cols: list[str], tgt_cols: list[str]) -> bool:
    return ("id" in tgt_cols) and ("id" not in src_cols)


def _sqlite_tables(src: sqlite3.Connection) -> set[str]:
    return {r[0] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'")}


def _sqlite_columns(src: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in src.execute(f'PRAGMA table_info("{table}")')]


def run_migration(
    source_path: str,
    target_url: str,
    chunk: int = 1000,
    wipe: bool = False,
    echo: bool = False,
) -> dict:
    """Copy `source_path` (SQLite file) into `target_url`. Returns a report:
    {'tables': {name: {source, target, ok, dropped_columns}}, 'views': {name:
    'ok'|error}, 'ok': bool}. Never mutates the source DB."""
    src = sqlite3.connect(source_path)  # read-only usage
    src_tables = _sqlite_tables(src)
    engine = create_engine(target_url, echo=echo)
    is_pg = engine.dialect.name == "postgresql"

    if wipe:
        # Drop views first (they depend on tables), then all tables.
        with engine.begin() as conn:
            for vname in models.SME_AND_DERIVED_VIEWS:
                conn.execute(text(f'DROP VIEW IF EXISTS "{vname}" CASCADE'
                                  if is_pg else f'DROP VIEW IF EXISTS "{vname}"'))
        models.Base.metadata.drop_all(engine)
    models.Base.metadata.create_all(engine)

    report: dict = {"tables": {}, "views": {}, "ok": True}

    with engine.begin() as conn:
        for table in models.Base.metadata.sorted_tables:
            tname = table.name
            tgt_cols = [c.name for c in table.columns]
            if tname not in src_tables:
                # New table not present in the source DB — leave empty.
                report["tables"][tname] = {
                    "source": 0, "target": 0, "ok": True, "dropped_columns": []}
                continue
            src_cols = _sqlite_columns(src, tname)
            add_id = _needs_rowid_id(src_cols, tgt_cols)
            shared = [c for c in tgt_cols if c in src_cols]
            dropped = [c for c in src_cols if c not in tgt_cols]

            select_cols = (["rowid AS id"] if add_id else []) + \
                          [f'"{c}"' for c in shared]
            rows = src.execute(
                f'SELECT {", ".join(select_cols)} FROM "{tname}"').fetchall()
            insert_cols = (["id"] if add_id else []) + shared

            # Per-column coercers bridge SQLite's loose typing to strict target
            # types (empty strings / junk in numeric+date columns → NULL).
            coercers = [_make_coercer(table.columns[c]) for c in insert_cols]
            coerced_to_null = 0
            data = []
            for row in rows:
                out = {}
                for i, c in enumerate(insert_cols):
                    v = row[i]
                    nv = coercers[i](v)
                    if nv is None and v not in (None, ""):
                        coerced_to_null += 1  # real value couldn't be typed
                    out[c] = nv
                data.append(out)

            conn.execute(table.delete())  # idempotent reload
            for i in range(0, len(data), chunk):
                if data[i:i + chunk]:
                    conn.execute(insert(table), data[i:i + chunk])

            report["tables"][tname] = {
                "source": len(rows), "target": None, "ok": None,
                "dropped_columns": dropped, "coerced_to_null": coerced_to_null}

        # Fix Postgres sequences so new inserts continue past the copied max(id).
        if is_pg:
            for table in models.Base.metadata.sorted_tables:
                pk = list(table.primary_key.columns)
                if len(pk) == 1 and pk[0].name == "id":
                    conn.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('{table.name}', "
                        f"'id'), COALESCE((SELECT MAX(id) FROM {table.name}), 1), "
                        f"(SELECT COUNT(*) FROM {table.name}) > 0)"))

        # Recreate views. Use a PG-native override where the SQLite text uses
        # dialect-specific functions (e.g. v_expiring_stock); otherwise the
        # portable SQLite text works on both.
        for vname, vsql in models.SME_AND_DERIVED_VIEWS.items():
            if is_pg and vname in PG_VIEW_OVERRIDES:
                vsql = PG_VIEW_OVERRIDES[vname]
            try:
                conn.execute(text(f'DROP VIEW IF EXISTS "{vname}"'
                                  + (" CASCADE" if is_pg else "")))
                conn.execute(text(vsql))
                report["views"][vname] = "ok"
            except Exception as e:  # noqa: BLE001 — report, don't abort
                report["views"][vname] = f"FAILED: {type(e).__name__}: {e}"
                report["ok"] = False

    # Parity: compare source vs target row counts.
    with engine.connect() as conn:
        for tname, info in report["tables"].items():
            tgt = conn.execute(
                select(func.count()).select_from(
                    models.Base.metadata.tables[tname])).scalar()
            info["target"] = int(tgt)
            info["ok"] = (info["source"] == info["target"])
            if not info["ok"]:
                report["ok"] = False

    src.close()
    engine.dispose()
    return report


def _print_report(report: dict) -> None:
    print("\n== Table parity (source SQLite → target) ==")
    for t, info in sorted(report["tables"].items()):
        flag = "✅" if info["ok"] else "❌"
        notes = []
        if info.get("dropped_columns"):
            notes.append(f"⚠ dropped source cols: {info['dropped_columns']}")
        if info.get("coerced_to_null"):
            notes.append(f"⚠ {info['coerced_to_null']} value(s) coerced to NULL "
                         f"(loose-typed data)")
        note = ("  " + "; ".join(notes)) if notes else ""
        print(f"  {flag} {t:36} {info['source']:>7} → {info['target']:<7}{note}")
    print("\n== Views ==")
    for v, status in sorted(report["views"].items()):
        flag = "✅" if status == "ok" else "❌"
        print(f"  {flag} {v:24} {status}")
    print(f"\n== OVERALL: {'✅ PARITY OK' if report['ok'] else '❌ MISMATCH'} ==")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Copy SQLite → target DB via models.py")
    ap.add_argument("--source", default=os.environ.get("GI_DB_FILE", "gi_database.db"),
                    help="Source SQLite file (default: $GI_DB_FILE or gi_database.db)")
    ap.add_argument("--target", default=os.environ.get("DATABASE_URL"),
                    help="Target SQLAlchemy URL (default: $DATABASE_URL)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Target a throwaway SQLite DB (validates copy logic, no PG)")
    ap.add_argument("--wipe", action="store_true",
                    help="Drop all target tables/views first (clean reload)")
    ap.add_argument("--chunk", type=int, default=1000)
    args = ap.parse_args(argv)

    if not os.path.exists(args.source):
        print(f"[error] source not found: {args.source}", file=sys.stderr)
        return 2
    if args.dry_run:
        target = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "pg_dryrun.db")
        print(f"[dry-run] target = {target}")
    elif args.target:
        target = args.target
    else:
        print("[error] provide --target <url> or set DATABASE_URL "
              "(or use --dry-run)", file=sys.stderr)
        return 2

    report = run_migration(args.source, target, chunk=args.chunk, wipe=args.wipe)
    _print_report(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
