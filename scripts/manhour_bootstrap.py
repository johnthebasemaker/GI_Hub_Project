"""
Man-Hour & Labor Tracking — Bootstrap Loader
============================================
One-shot importer for an attendance workbook (the `to john_Attendance.xlsx`
format) into the ERP's gi_database.db. Loads two ISOLATED mh_* tables:

  - "ADD EMPLOYEE" sheet (+ every distinct worker in SAR) → mh_employees
  - "SAR" sheet (daily attendance)                        → mh_timesheets

Notes on the source format (verified against the CNCEC example file):
  * The SAR sheet's Location / Equipment Tag # / System Code are typically
    BLANK — they import as NULL and get assigned later in the Daily Timesheet
    UI. There is no SQM column in the file (entered via mh_production later).
  * The file's Total/Normal/OT hour columns are unreliable, so hours are
    COMPUTED from In/Out − break (8h normal + 1h unpaid break) by
    database.add_mh_timesheet → compute_mh_hours.
  * The "ADD EMPLOYEE" sheet is often just a header/legend; real worker
    identities come from SAR's distinct (code, name). Both are merged.

This script writes ONLY to mh_* tables. It never touches sme_*, inventory,
or the EOD path.

Run:
    python3 scripts/manhour_bootstrap.py --file "/path/to/to john_Attendance.xlsx" --site-id CNCEC
    python3 scripts/manhour_bootstrap.py --file "..." --site-id CNCEC --dry-run
    python3 scripts/manhour_bootstrap.py --file "..." --site-id CNCEC --force   # wipe site rows first
"""
from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

# Allow running as a script from any CWD by anchoring imports to repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

import database as D  # noqa: E402


def _norm(s) -> str:
    return str(s or "").strip().lower()


def _str_code(v) -> str:
    """Employee codes arrive as int/float/str — normalise to a clean string."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, int):
        return str(v)
    return str(v).strip()


def _iso_date(v) -> str:
    try:
        return pd.to_datetime(v).date().isoformat()
    except Exception:
        return str(v or "").strip()[:10]


def _map_columns(df: pd.DataFrame, wanted: dict) -> dict:
    """Map normalised header names → actual df column labels. `wanted` is
    {canonical: [accepted normalised names]}."""
    by_norm = {_norm(c): c for c in df.columns}
    out = {}
    for canon, accepted in wanted.items():
        for a in accepted:
            if a in by_norm:
                out[canon] = by_norm[a]
                break
    return out


def load_employees_sheet(xls: pd.ExcelFile) -> list[dict]:
    """Real rows from the ADD EMPLOYEE sheet (CODE present). Often empty/legend."""
    if "ADD EMPLOYEE" not in xls.sheet_names:
        return []
    df = xls.parse("ADD EMPLOYEE")
    cm = _map_columns(df, {
        "code": ["code"], "name": ["name"], "designation": ["designation"],
        "type": ["type"], "company": ["company"],
    })
    rows = []
    if "code" not in cm:
        return rows
    for _, r in df.iterrows():
        code = _str_code(r.get(cm["code"]))
        name = str(r.get(cm.get("name"), "") or "").strip()
        if not code or not name:        # skip legend / blank rows
            continue
        wt = str(r.get(cm.get("type"), "") or "").strip() or "OWN"
        rows.append({
            "code": code, "name": name,
            "designation": str(r.get(cm.get("designation"), "") or "").strip(),
            "worker_type": "Supply" if wt.lower().startswith("supply") else "OWN",
            "company": str(r.get(cm.get("company"), "") or "").strip(),
        })
    return rows


def load_sar_sheet(xls: pd.ExcelFile) -> list[dict]:
    """Daily attendance rows from the SAR sheet."""
    df = xls.parse("SAR")
    cm = _map_columns(df, {
        "location": ["location"], "equipment_tag": ["equipment tag #", "equipment tag"],
        "code": ["code"], "name": ["name"], "work_date": ["work date"],
        "in_time": ["in time"], "out_time": ["out time"],
        "status": ["status"], "remarks": ["remarks"],
    })
    rows = []
    for _, r in df.iterrows():
        code = _str_code(r.get(cm.get("code")))
        wdate = _iso_date(r.get(cm.get("work_date")))
        if not code or not wdate:
            continue
        rows.append({
            "code": code,
            "name": str(r.get(cm.get("name"), "") or "").strip(),
            "work_date": wdate,
            "location": str(r.get(cm.get("location"), "") or "").strip(),
            "equipment_tag": str(r.get(cm.get("equipment_tag"), "") or "").strip(),
            "in_time": r.get(cm.get("in_time")),
            "out_time": r.get(cm.get("out_time")),
            "status": str(r.get(cm.get("status"), "") or "").strip() or "PR",
            "remarks": str(r.get(cm.get("remarks"), "") or "").strip(),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Man-Hour attendance bootstrap")
    ap.add_argument("--file", required=True, help="path to the attendance .xlsx")
    ap.add_argument("--site-id", default="CNCEC", help="target Site_ID (default CNCEC)")
    ap.add_argument("--dry-run", action="store_true", help="parse + report, no writes")
    ap.add_argument("--force", action="store_true",
                    help="wipe this site's mh_employees + mh_timesheets first")
    args = ap.parse_args()

    if not os.path.exists(args.file):
        print(f"❌ File not found: {args.file}")
        return 1

    site = args.site_id.strip()
    xls = pd.ExcelFile(args.file)
    print(f"▶ Workbook: {os.path.basename(args.file)}  sheets={xls.sheet_names}")

    emp_sheet = load_employees_sheet(xls)
    sar = load_sar_sheet(xls)

    # Merge: every distinct SAR worker becomes an employee; ADD EMPLOYEE rows
    # (if any) supply richer attributes.
    emp_by_code: dict[str, dict] = {}
    for e in emp_sheet:
        emp_by_code[e["code"]] = e
    for row in sar:
        if row["code"] not in emp_by_code:
            emp_by_code[row["code"]] = {
                "code": row["code"], "name": row["name"] or row["code"],
                "designation": "", "worker_type": "OWN", "company": "",
            }

    dates = sorted({r["work_date"] for r in sar})
    print(f"▶ Parsed: {len(emp_by_code)} distinct employees, {len(sar)} timesheet rows")
    print(f"▶ Date range: {dates[0]} → {dates[-1]}  ({len(dates)} days)" if dates else "▶ no dates")
    print(f"▶ Target Site_ID: {site}")

    if args.dry_run:
        print("\n(dry-run) sample employees:")
        for e in list(emp_by_code.values())[:5]:
            print("   ", e)
        print("(dry-run) sample timesheets:")
        for r in sar[:3]:
            t, n, o = D.compute_mh_hours(r["in_time"], r["out_time"])
            print(f"    {r['code']} {r['work_date']} {r['in_time']}–{r['out_time']} "
                  f"→ Total={t} Normal={n} OT={o}")
        print("\n(dry-run) no rows written.")
        return 0

    conn = D.get_connection()
    D.init_db(conn)   # self-heal: ensures the mh_* tables exist

    if args.force:
        cur = conn.cursor()
        cur.execute("DELETE FROM mh_timesheets WHERE Site_ID=?", (site,))
        cur.execute("DELETE FROM mh_employees WHERE Site_ID=?", (site,))
        conn.commit()
        print("▶ --force: cleared existing mh_employees + mh_timesheets for site")

    emp_ok = 0
    for e in emp_by_code.values():
        ok, _ = D.upsert_mh_employee(
            site, e["code"], e["name"], designation=e["designation"],
            worker_type=e["worker_type"], company=e["company"],
            created_by="manhour_bootstrap", conn=conn)
        emp_ok += int(ok)

    ts_ok = 0
    for r in sar:
        ok, _ = D.add_mh_timesheet(
            site, r["code"], r["work_date"], r["in_time"], r["out_time"],
            location=r["location"], equipment_tag=r["equipment_tag"],
            system_code="", status=r["status"], remarks=r["remarks"],
            created_by="manhour_bootstrap", conn=conn)
        ts_ok += int(ok)

    # Verify by reading back from the DB.
    emp_n = len(D.list_mh_employees(site, conn=conn))
    ts_n = len(D.list_mh_timesheets(site, conn=conn))
    print(f"\n✅ Loaded {emp_ok} employees, {ts_ok} timesheet rows.")
    print(f"✅ Verified in DB → mh_employees(site={site}): {emp_n} · "
          f"mh_timesheets(site={site}): {ts_n}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
