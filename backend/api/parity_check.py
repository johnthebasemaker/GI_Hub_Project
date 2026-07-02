"""
backend/api/parity_check.py — assert the API's derived-stock SQL matches the
SQLite reporting views on the real data.

For each entry in stock.DERIVED it:
  * runs `SELECT * FROM <v_view>` on the SQLite source (gi_database.db),
  * runs the ported Postgres SQL on the target,
  * compares them as an order-independent multiset of value-normalised rows
    (int/float unified + rounded; Decimal->float; dates/everything-else->str),
    so a match proves identical math regardless of row/column order or numeric
    representation.

Run:
    DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub \
        .venv/bin/python backend/api/parity_check.py --source gi_database.db

Exits non-zero on any mismatch. Date-derived columns (Days_Until_Expiry / status)
assume the SQLite and Postgres queries run on the same calendar day.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import Counter
from decimal import Decimal

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))  # repo root

from sqlalchemy import create_engine, text  # noqa: E402

from backend.api import sme, stock  # noqa: E402

# All derived-view ports to parity-check: stock views + the SME materials view.
_ALL_DERIVED = {**stock.DERIVED, **sme.DERIVED_SME}


def _norm(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return round(float(v), 6)
    if isinstance(v, Decimal):
        return round(float(v), 6)
    if isinstance(v, float):
        return round(v, 6)
    return str(v)


def _canon_rows(rows) -> Counter:
    """Order-independent multiset of rows; each row a frozenset of (col, norm)."""
    bag = Counter()
    for r in rows:
        m = dict(r)
        bag[frozenset((k, _norm(v)) for k, v in m.items())] += 1
    return bag


def _sqlite_view_rows(src: sqlite3.Connection, view: str):
    src.row_factory = sqlite3.Row
    return [dict(r) for r in src.execute(f'SELECT * FROM "{view}"').fetchall()]


def run(source_path: str, target_url: str) -> dict:
    # normalise to a sync driver for the checker (independent of the async API).
    if target_url.startswith("postgresql+asyncpg://"):
        target_url = "postgresql+psycopg2://" + target_url[len("postgresql+asyncpg://"):]
    src = sqlite3.connect(source_path)
    engine = create_engine(target_url)
    results, ok_all = {}, True
    try:
        with engine.connect() as tconn:
            for key, spec in _ALL_DERIVED.items():
                view = spec["view"]
                try:
                    s_rows = _sqlite_view_rows(src, view)
                except Exception as e:  # noqa: BLE001
                    results[key] = {"ok": False, "error": f"sqlite: {type(e).__name__}: {e}"}
                    ok_all = False
                    continue
                try:
                    t_rows = tconn.execute(text(spec["sql"])).mappings().all()
                except Exception as e:  # noqa: BLE001
                    results[key] = {"ok": False, "error": f"pg: {type(e).__name__}: {e}"}
                    ok_all = False
                    continue
                s_bag, t_bag = _canon_rows(s_rows), _canon_rows(t_rows)
                ok = s_bag == t_bag
                info = {"ok": ok, "view": view,
                        "sqlite_rows": len(s_rows), "pg_rows": len(t_rows)}
                if not ok:
                    only_s = list((s_bag - t_bag).elements())[:2]
                    only_t = list((t_bag - s_bag).elements())[:2]
                    info["only_in_sqlite"] = [dict(x) for x in only_s]
                    info["only_in_pg"] = [dict(x) for x in only_t]
                    ok_all = False
                results[key] = info
    finally:
        src.close()
        engine.dispose()
    return {"ok": ok_all, "checks": results}


def _print(res: dict) -> None:
    print("\nDerived-view parity (SQLite view  vs  ported PG SQL):")
    for key, i in res["checks"].items():
        if "error" in i:
            print(f"  ❌ {key:10} ({i.get('view','?')})  ERROR: {i['error']}")
            continue
        mark = "✅" if i["ok"] else "❌"
        print(f"  {mark} {key:10} ({i['view']:18}) sqlite={i['sqlite_rows']:>4}  pg={i['pg_rows']:>4}")
        if not i["ok"]:
            print(f"       only_in_sqlite (sample): {i['only_in_sqlite']}")
            print(f"       only_in_pg     (sample): {i['only_in_pg']}")
    print(f"\n== PARITY: {'✅ PASS' if res['ok'] else '❌ FAIL'} ==")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Derived-view SQLite<->PG parity")
    ap.add_argument("--source", default=os.environ.get("GI_DB_FILE", "gi_database.db"))
    ap.add_argument("--target", default=os.environ.get("DATABASE_URL"))
    args = ap.parse_args(argv)
    if not os.path.exists(args.source):
        print(f"[error] source not found: {args.source}", file=sys.stderr)
        return 2
    if not args.target:
        print("[error] set DATABASE_URL or --target (a Postgres URL)", file=sys.stderr)
        return 2
    res = run(args.source, args.target)
    _print(res)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
