"""
backend/pg_smoke.py — behavioural Postgres smoke (the behavioural half of the
dual-CI). Exercises real `database.py` code paths against the configured DB and
reports per-path pass/fail, so runtime SQL dialect-isms (date('now'),
INSERT OR IGNORE, casts, …) surface in ACTUAL app code — not just the schema.

Every path runs isolated (try/except), so one run yields the COMPLETE list of
what still needs porting, rather than stopping at the first failure.

Usage
-----
    # CI — real Postgres (DATABASE_URL already exported; source migrated in):
    DATABASE_URL=postgresql+psycopg2://gihub:pw@localhost:5432/gihub \
        python backend/pg_smoke.py --source gi_database.db
    # Local structural check on SQLite (no Postgres, no migration):
    python backend/pg_smoke.py --source gi_database.db --dry-run
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))  # repo root, so `import database` works


def _build_paths(db):
    """(name, thunk) for real app code paths that lean on runtime SQL. Args are
    generous ranges / a real site so they actually execute query logic."""
    D0, D1 = "2000-01-01", "2100-01-01"
    site = "CNCEC"
    today = datetime.date(2026, 7, 2)
    return [
        ("load_live_inventory",       lambda: db.load_live_inventory(site_id=site)),
        ("get_pending_returns",       lambda: db.get_pending_returns(site)),
        ("get_receipt_history",       lambda: db.get_receipt_history(site, limit=20)),
        ("get_item_bin_locations",    lambda: db.get_item_bin_locations("GI-0000001", site)),
        ("get_whatsapp_log",          lambda: db.get_whatsapp_log(limit=20)),
        ("report_wbs_consumption",    lambda: db.report_wbs_consumption(D0, D1, site)),
        ("report_daily_receipts",     lambda: db.report_daily_receipts(D0, D1, site)),
        ("report_monthly_summary",    lambda: db.report_monthly_summary(D0, D1, site)),
        ("get_all_lots",              lambda: db.get_all_lots()),
        ("get_lots_for_item",         lambda: db.get_lots_for_item("GI-0000001", site)),
        ("list_vendors",              lambda: db.list_vendors()),
        ("get_site_unit_costs",       lambda: db.get_site_unit_costs()),
        ("get_reminder_offsets",      lambda: db.get_reminder_offsets()),
        ("get_procurement_adoption",  lambda: db.get_procurement_adoption()),
        ("get_procurement_email_dep", lambda: db.procurement_email_deprecated()),
        # Write + date-heavy path (queries POs/DNs with date offsets, writes
        # notifications) — a strong dialect test.
        ("sweep_delivery_reminders",  lambda: db.sweep_delivery_reminders(today)),
    ]


def run(source_path: str, target_url: str | None, dry_run: bool) -> dict:
    """Point the app's connection factory at the target (real PG in CI, or the
    source SQLite in --dry-run), then run every path. Returns {name: 'ok'|err}."""
    if dry_run or not target_url:
        # Structural check: run paths against the source SQLite as-is.
        os.environ.pop("DATABASE_URL", None)
        os.environ["GI_DB_FILE"] = source_path
    else:
        # CI: migrate the SQLite source into the Postgres target, then run the
        # app paths against it (DATABASE_URL already points there).
        import migrate_sqlite_to_postgres as mig
        mig.run_migration(source_path, target_url, wipe=True)
        os.environ["DATABASE_URL"] = target_url

    import database as db          # imported AFTER env is set
    db.init_db()                   # PG-guard on PG; SQLite self-heal otherwise

    results: dict = {}
    for name, thunk in _build_paths(db):
        try:
            thunk()
            results[name] = "ok"
        except Exception as e:      # noqa: BLE001 — collect, don't stop
            results[name] = f"{type(e).__name__}: {str(e)[:180]}"
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Behavioural Postgres smoke")
    ap.add_argument("--source", default="gi_database.db")
    ap.add_argument("--target", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.exists(args.source):
        print(f"[error] source not found: {args.source}", file=sys.stderr)
        return 2

    where = "SQLite (dry-run)" if (args.dry_run or not args.target) else args.target
    print(f"[pg_smoke] exercising app paths against: {where}")
    results = run(args.source, args.target, args.dry_run)

    ok = [n for n, r in results.items() if r == "ok"]
    bad = {n: r for n, r in results.items() if r != "ok"}
    print(f"\n== Behavioural paths: {len(ok)}/{len(results)} ok ==")
    for n, r in results.items():
        print(f"  {'✅' if r == 'ok' else '❌'} {n:28} {'' if r == 'ok' else r}")
    print(f"\n== PG SMOKE: {'✅ PASS' if not bad else f'❌ {len(bad)} FAIL'} ==")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
