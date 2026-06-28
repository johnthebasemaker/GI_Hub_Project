"""
Man-Hour & Labor Tracking — Bootstrap Loader (CLI)
==================================================
One-shot importer for an attendance workbook (the `to john_Attendance.xlsx`
format) into the ERP's gi_database.db. Thin CLI wrapper around the shared
parse/import helpers in database.py — the exact same code path the HOD/Admin
Man-Hours portal uses for in-app Excel upload, so behaviour can't drift.

Loads two ISOLATED mh_* tables (writes ONLY mh_*, never sme_*/inventory/EOD):
  - "ADD EMPLOYEE" sheet (+ every distinct SAR worker) → mh_employees
  - "SAR" sheet (daily attendance)                       → mh_timesheets

Hours are computed from In/Out − break (8h normal + 1h break); the file's dirty
Total/Normal/OT columns are ignored. Location/Equipment/System import as-is
(usually blank → assigned later in the Daily Timesheet UI).

Run:
    python3 scripts/manhour_bootstrap.py --file "/path/to/to john_Attendance.xlsx" --site-id CNCEC
    python3 scripts/manhour_bootstrap.py --file "..." --site-id CNCEC --dry-run
    python3 scripts/manhour_bootstrap.py --file "..." --site-id CNCEC --no-replace
"""
from __future__ import annotations

import argparse
import os
import sys

# Allow running as a script from any CWD by anchoring imports to repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

import database as D  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Man-Hour attendance bootstrap")
    ap.add_argument("--file", required=True, help="path to the attendance .xlsx")
    ap.add_argument("--site-id", default="CNCEC", help="target Site_ID (default CNCEC)")
    ap.add_argument("--dry-run", action="store_true", help="parse + report, no writes")
    ap.add_argument("--no-replace", action="store_true",
                    help="append instead of replacing this site's rows for the file's dates")
    args = ap.parse_args()

    if not os.path.exists(args.file):
        print(f"❌ File not found: {args.file}")
        return 1

    site = args.site_id.strip()
    parsed = D.parse_attendance_workbook(args.file)
    emps, ts, dates = parsed["employees"], parsed["timesheets"], parsed["dates"]

    print(f"▶ File: {os.path.basename(args.file)}")
    print(f"▶ Parsed: {len(emps)} distinct employees, {len(ts)} timesheet rows")
    print(f"▶ Date range: {dates[0]} → {dates[-1]}  ({len(dates)} days)" if dates
          else "▶ no dated rows found")
    print(f"▶ Target Site_ID: {site}")

    if args.dry_run:
        print("\n(dry-run) sample employees:")
        for e in emps[:5]:
            print("   ", e)
        print("(dry-run) sample timesheets (computed hours):")
        for r in ts[:3]:
            t, n, o = D.compute_mh_hours(r["in_time"], r["out_time"])
            print(f"    {r['code']} {r['work_date']} {r['in_time']}–{r['out_time']} "
                  f"→ Total={t} Normal={n} OT={o}")
        print("\n(dry-run) no rows written.")
        return 0

    conn = D.get_connection()
    D.init_db(conn)  # self-heal: ensures the mh_* tables exist
    emp_n, ts_n = D.import_mh_attendance(
        site, parsed, replace=not args.no_replace,
        created_by="manhour_bootstrap", conn=conn)

    emp_v = len(D.list_mh_employees(site, conn=conn))
    ts_v = len(D.list_mh_timesheets(site, conn=conn))
    print(f"\n✅ Imported {emp_n} employees, {ts_n} timesheet rows "
          f"({'replace' if not args.no_replace else 'append'} mode).")
    print(f"✅ Verified in DB → mh_employees(site={site}): {emp_v} · "
          f"mh_timesheets(site={site}): {ts_v}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
