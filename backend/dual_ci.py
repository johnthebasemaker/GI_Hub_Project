"""
backend/dual_ci.py — Phase 4 *data-layer* dual-backend CI.

Runs the SQLite→target migration, then verifies cross-backend parity:
  1. per-table row counts (from the migration report),
  2. per-VIEW row counts (source SQLite vs target),
  3. a few semantic aggregates (identity-math totals) that must match.
Exits non-zero on any mismatch.

SCOPE (important): this validates the DATA LAYER on the target — schema, types,
data copy, and view dialect. It does NOT yet run the full bug_check / UI crawler
against Postgres, because the app's `database.get_connection()` is still
SQLite-only. That full *behavioural* dual-CI comes after the engine seam is
wired into get_connection() (a later phase). Until then, this harness is the
Postgres safety net for the migration + schema + views.

Usage
-----
    # Against real Postgres (CI or a local server):
    DATABASE_URL=postgresql+psycopg2://gihub:pw@localhost:5432/gihub \
        .venv/bin/python backend/dual_ci.py --source gi_database.db
    # Local structural validation with no Postgres (SQLite → throwaway SQLite):
    .venv/bin/python backend/dual_ci.py --source gi_database.db --dry-run
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                      # backend/  (models, migrate)
sys.path.insert(0, os.path.dirname(_HERE))     # repo root (database)
import models  # noqa: E402
import migrate_sqlite_to_postgres as mig  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402


# Table-based scalar checks — MUST match on both backends (data-layer parity).
# Mixed-case identifiers are DOUBLE-QUOTED so the same SQL runs on both SQLite
# and Postgres (PG folds unquoted identifiers to lowercase).
SEMANTIC_CHECKS: dict[str, str] = {
    "inventory rows":              "SELECT COUNT(*) FROM inventory",
    "receipts SUM(Quantity)":      'SELECT COALESCE(ROUND(CAST(SUM("Quantity") AS NUMERIC), 3), 0) FROM receipts',
    "consumption SUM(Quantity)":   'SELECT COALESCE(ROUND(CAST(SUM("Quantity") AS NUMERIC), 3), 0) FROM consumption',
    "returns SUM(Quantity)":       'SELECT COALESCE(ROUND(CAST(SUM("Quantity") AS NUMERIC), 3), 0) FROM returns',
    "system_audit_log rows":       "SELECT COUNT(*) FROM system_audit_log",
}

# View-based checks — only meaningful where the SQL views exist (SQLite). The
# views are NOT migrated to Postgres (SQLite/Streamlit legacy; the FastAPI layer
# computes these via the ORM), so these are skipped on a Postgres target.
VIEW_SEMANTIC_CHECKS: dict[str, str] = {
    "v_site_stock SUM(Current_Stock)": 'SELECT COALESCE(ROUND(SUM("Current_Stock"), 3), 0) FROM v_site_stock',
    "v_lot_balance SUM(Remaining_Qty)": 'SELECT COALESCE(ROUND(SUM("Remaining_Qty"), 3), 0) FROM v_lot_balance',
    "v_expiring_stock rows":       "SELECT COUNT(*) FROM v_expiring_stock",
}


def _num_eq(a, b, tol: float = 1e-6) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return str(a) == str(b)


def _facade_smoke() -> dict:
    """Exercise database.get_connection() (the sqlite3-compat facade) end-to-end
    on the real target — `?` placeholders, a `?`/`%`/`'` value passed as a
    PARAMETER (must NOT be translated), lastrowid, and rowcount. Only meaningful
    when DATABASE_URL is Postgres (that's when get_connection returns the facade)."""
    import database
    import pandas as pd
    # init_db on PG must take the guard path (create_all + views), not the SQLite
    # self-heal DDL — this asserts the app can *start* on Postgres.
    database.init_db()
    conn = database.get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS _facade_smoke")
        conn.execute("CREATE TABLE _facade_smoke "
                     "(id SERIAL PRIMARY KEY, a INTEGER, b TEXT)")
        conn.commit()
        cur = conn.execute(
            "INSERT INTO _facade_smoke (a, b) VALUES (?, ?)", (7, "x'?%y"))
        lid = cur.lastrowid
        got = conn.execute(
            "SELECT a, b FROM _facade_smoke WHERE a = ?", (7,)).fetchone()
        up = conn.execute("UPDATE _facade_smoke SET a = ? WHERE id = ?", (9, lid))
        rc = up.rowcount
        # read_sql THROUGH the facade on real Postgres (the 265-site path).
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = pd.read_sql("SELECT a, b FROM _facade_smoke WHERE id = ?", conn,
                             params=(lid,))
        rs_ok = (len(df) == 1 and int(df.iloc[0]["a"]) == 9 and df.iloc[0]["b"] == "x'?%y")
        conn.execute("DROP TABLE _facade_smoke")
        conn.commit()
        ok = (lid == 1 and got is not None and got[0] == 7
              and got[1] == "x'?%y" and rc == 1 and rs_ok)
        return {"ok": ok, "lastrowid": lid, "select": list(got) if got else None,
                "rowcount": rc, "read_sql_ok": rs_ok}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        conn.close()


def run(source_path: str, target_url: str) -> dict:
    """Migrate + verify. Returns {'migration', 'views', 'semantic', 'ok'}."""
    result: dict = {"ok": True}
    # 1. Migrate (schema + data + views + per-table parity).
    mrep = mig.run_migration(source_path, target_url, wipe=True)
    result["migration"] = mrep
    if not mrep["ok"]:
        result["ok"] = False

    src = sqlite3.connect(source_path)
    engine = create_engine(target_url)
    is_pg = engine.dialect.name == "postgresql"
    # Views are migrated only on SQLite (parked on PG — see run_migration).
    checks = dict(SEMANTIC_CHECKS)
    if not is_pg:
        checks.update(VIEW_SEMANTIC_CHECKS)
    try:
        with engine.connect() as tconn:
            # 2. Per-view row-count parity — SQLite target only (no views on PG).
            vparity = {}
            if not is_pg:
                for vname in models.SME_AND_DERIVED_VIEWS:
                    try:
                        s = src.execute(f'SELECT COUNT(*) FROM "{vname}"').fetchone()[0]
                    except Exception as e:  # noqa: BLE001
                        s = f"ERR:{type(e).__name__}"
                    try:
                        t = tconn.execute(text(f'SELECT COUNT(*) FROM "{vname}"')).scalar()
                    except Exception as e:  # noqa: BLE001
                        t = f"ERR:{type(e).__name__}"
                    ok = _num_eq(s, t)
                    vparity[vname] = {"source": s, "target": t, "ok": ok}
                    if not ok:
                        result["ok"] = False
            result["views"] = vparity
            result["views_skipped"] = is_pg

            # 3. Semantic aggregate parity (table-based always; view-based SQLite-only).
            sem = {}
            for label, sql in checks.items():
                try:
                    s = src.execute(sql).fetchone()[0]
                except Exception as e:  # noqa: BLE001
                    s = f"ERR:{type(e).__name__}"
                try:
                    t = tconn.execute(text(sql)).scalar()
                except Exception as e:  # noqa: BLE001
                    t = f"ERR:{type(e).__name__}"
                ok = _num_eq(s, t)
                sem[label] = {"source": s, "target": t, "ok": ok}
                if not ok:
                    result["ok"] = False
            result["semantic"] = sem
    finally:
        src.close()
        engine.dispose()

    # Facade smoke — only when the target is Postgres (get_connection returns the
    # compat facade). On a SQLite dry-run get_connection is raw sqlite3, so skip.
    import database
    if database.db_dialect() == "postgresql":
        fs = _facade_smoke()
        result["facade"] = fs
        if not fs["ok"]:
            result["ok"] = False
    return result


def _print(result: dict) -> None:
    m = result["migration"]
    tbad = [t for t, i in m["tables"].items() if not i["ok"]]
    print(f"\n[1] Table parity: {len(m['tables']) - len(tbad)}/{len(m['tables'])} ok"
          + (f"  ❌ {tbad}" if tbad else "  ✅"))
    vbad = [v for v, s in m["views"].items() if s != "ok"]
    if m["views"]:
        print(f"[1] View creation: {len(m['views']) - len(vbad)}/{len(m['views'])} ok"
              + (f"  ❌ {vbad}" if vbad else "  ✅"))
    else:
        print("[1] View creation: skipped (views not migrated to Postgres)")

    if result.get("views_skipped"):
        print("\n[2] View row-count parity: skipped on Postgres "
              "(views are SQLite/Streamlit legacy; FastAPI computes via ORM)")
    else:
        print("\n[2] View row-count parity (SQLite → target):")
        for v, i in sorted(result["views"].items()):
            print(f"  {'✅' if i['ok'] else '❌'} {v:24} {i['source']} → {i['target']}")

    print("\n[3] Semantic aggregate parity:")
    for label, i in result["semantic"].items():
        print(f"  {'✅' if i['ok'] else '❌'} {label:34} {i['source']} → {i['target']}")

    if "facade" in result:
        f = result["facade"]
        print("\n[4] get_connection() facade smoke (Postgres):")
        print(f"  {'✅' if f['ok'] else '❌'} {f}")

    print(f"\n== DUAL-CI: {'✅ PASS' if result['ok'] else '❌ FAIL'} ==")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Phase 4 data-layer dual-backend CI")
    ap.add_argument("--source", default=os.environ.get("GI_DB_FILE", "gi_database.db"))
    ap.add_argument("--target", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--dry-run", action="store_true",
                    help="Target a throwaway SQLite (structural validation, no PG)")
    args = ap.parse_args(argv)

    if not os.path.exists(args.source):
        print(f"[error] source not found: {args.source}", file=sys.stderr)
        return 2
    if args.dry_run:
        target = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "dualci.db")
        print(f"[dry-run] target = {target}")
    elif args.target:
        target = args.target
    else:
        print("[error] set DATABASE_URL / --target, or use --dry-run", file=sys.stderr)
        return 2

    result = run(args.source, target)
    _print(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
