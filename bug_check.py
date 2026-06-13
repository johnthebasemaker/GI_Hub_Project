#!/usr/bin/env python3
"""
bug_check.py — automated smoke harness for General Industries Hub.

Usage:
    python bug_check.py
    python bug_check.py --verbose

Runs a battery of data-layer checks against a throwaway SQLite file,
captures pass / fail per check, and writes BUG_REPORT.md next to the
repo root. The live database (`gi_database.db`) is never touched.

Exit codes:
    0  all checks passed
    1  one or more checks failed
    2  the harness itself crashed before any check ran
"""

from __future__ import annotations

import os
import sys
import json
import shutil
import datetime
import tempfile
import traceback
import importlib
import subprocess
import platform
from pathlib import Path

# ---------------------------------------------------------------------------
# Safety net — redirect DB + uploads to a throwaway directory BEFORE we touch
# any project module. This keeps gi_database.db pristine.
# ---------------------------------------------------------------------------
REPO_ROOT   = Path(__file__).resolve().parent
TMP_ROOT    = Path(tempfile.mkdtemp(prefix="gi_bugcheck_"))
TMP_DB      = TMP_ROOT / "bug_check.db"
TMP_UPLOADS = TMP_ROOT / "uploads"
TMP_UPLOADS.mkdir(parents=True, exist_ok=True)

# Stop mailer.py from actually launching Mail.app / xdg-open / Outlook
_orig_popen = subprocess.Popen
subprocess.Popen = lambda *a, **kw: None  # type: ignore[assignment]
platform.system = lambda: "Linux"          # avoid Windows COM path

os.environ.setdefault("LOGISTICS_EMAIL", "qa-dummy@example.invalid")
sys.path.insert(0, str(REPO_ROOT))

import config
config.DB_FILE = str(TMP_DB)

import database
database.DB_FILE     = str(TMP_DB)
database.UPLOADS_ROOT = str(TMP_UPLOADS)

import pandas as pd


# ---------------------------------------------------------------------------
# Result registry
# ---------------------------------------------------------------------------
RESULTS: list[dict] = []
VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv


def run_check(area: str, name: str, fn, hint: str = "") -> None:
    """Execute one check function, capturing the result + any exception."""
    started = datetime.datetime.now()
    try:
        fn()
        status, error, tb = "PASS", "", ""
    except AssertionError as e:
        status = "FAIL"
        error  = str(e) or "AssertionError"
        tb     = _format_tb(e)
    except Exception as e:
        status = "FAIL"
        error  = f"{type(e).__name__}: {e}"
        tb     = _format_tb(e)
    elapsed = (datetime.datetime.now() - started).total_seconds()
    RESULTS.append({
        "area": area, "name": name, "status": status,
        "error": error, "trace": tb, "hint": hint,
        "elapsed_ms": int(elapsed * 1000),
    })
    if VERBOSE:
        glyph = "✅" if status == "PASS" else "❌"
        print(f"  {glyph} {area} · {name}" + (f"  →  {error}" if error else ""))


def _format_tb(exc: BaseException) -> str:
    return "".join(traceback.format_exception(
        type(exc), exc, exc.__traceback__, limit=3,
    ))


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------
SITES   = ("HQ", "SITE_B")
TEST_SK = "test_sk"
TEST_HOD = "test_hod"


def seed() -> None:
    """Fresh DB with 4 users + 5 inventory items (1 rubber, 1 already zero-stock)."""
    database.init_db()
    conn = database.get_connection()
    try:
        c = conn.cursor()
        for u, role, site in [
            ("test_admin", "admin",        "HQ"),
            ("test_hod",   "hod",          "HQ"),
            ("test_super", "supervisor",   "HQ"),
            ("test_sk",    "store_keeper", "HQ"),
            ("test_sk_b",  "store_keeper", "SITE_B"),
        ]:
            c.execute(
                "INSERT OR IGNORE INTO users "
                "(username, password_hash, role, Site_ID, Phone_Number) "
                "VALUES (?,?,?,?,?)",
                (u, "$2b$12$placeholder", role, site, "+966500000000"),
            )
        items = [
            ("SAP-001", "Widget A",      "MC-001", "PCS", "Consumable",       100, 10.0),
            ("SAP-002", "Bolt M8",       "MC-002", "PCS", "Consumable",        50,  2.0),
            ("SAP-003", "O-Ring Rubber", "MC-003", "PCS", "Rubber materials",  20,  5.0),
            ("SAP-004", "Drill Bit",     "MC-004", "PCS", "Tools",              0, 25.0),
            ("SAP-005", "Test Kit",      "MC-005", "SET", "QC items",           5, 100.0),
        ]
        for sap, desc, mc, uom, cat, opening, cost in items:
            c.execute(
                "INSERT OR IGNORE INTO inventory "
                "(SAP_Code, Equipment_Description, Material_Code, UOM, "
                " Minimum_Qty, Unit_Cost, Site_ID, Category, Opening_Stock) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (sap, desc, mc, uom, 5.0, cost, "HQ", cat, opening),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema checks
# ---------------------------------------------------------------------------
def assert_table(name: str) -> None:
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        assert row is not None, f"Table missing: {name}"
    finally:
        conn.close()


def assert_column(table: str, col: str) -> None:
    conn = database.get_connection()
    try:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        assert col in cols, f"{table}.{col} missing"
    finally:
        conn.close()


def register_schema_checks() -> None:
    tables = [
        "inventory", "consumption", "receipts", "returns",
        "pending_issues", "pending_receipts", "pending_returns",
        "returnable_items", "users", "pending_users",
        "pr_master", "lots", "stock_adjustments",
        "system_settings", "system_audit_log", "app_settings",
        "whatsapp_queue", "bug_reports",
        "report_schedules", "report_archive",
        "qr_approval_requests", "entry_attachments", "mtc_documents",
    ]
    for t in tables:
        run_check("Schema", f"table · {t}",
                  lambda t=t: assert_table(t),
                  "init_db() should self-heal — verify CREATE TABLE block.")

    cols = {
        "inventory":  ["SAP_Code", "Material_Code", "Equipment_Description",
                       "UOM", "Minimum_Qty", "Unit_Cost",
                       "Category", "Opening_Stock", "Site_ID"],
        "consumption":["Date", "SAP_Code", "Quantity", "Work_Type",
                       "Remarks", "Site_ID", "Tank_No"],
        "receipts":   ["Date", "SAP_Code", "Quantity", "Supplier",
                       "Expiry_Date", "PR_Number", "Site_ID", "Unit_Cost",
                       "DN_No", "Lot_Number"],
        "returns":    ["Date", "SAP_Code", "Quantity", "Reason",
                       "Remarks", "Site_ID"],
        "pending_returns": ["SAP_Code", "Quantity", "Return_Reason",
                            "Return_DN_No", "override_required", "status",
                            "Material_Code", "Equipment_Description"],
        "qr_approval_requests": ["SAP_Code", "Quantity", "requested_by",
                                 "status", "approved_by"],
        "entry_attachments":    ["doc_type", "doc_number", "file_blob",
                                 "uploaded_by", "Site_ID"],
        "mtc_documents":        ["SAP_Code", "mtc_number", "status",
                                 "pending_receipt_id", "Site_ID"],
        "users":      ["username", "role", "Site_ID", "Phone_Number"],
    }
    for tbl, cs in cols.items():
        for c in cs:
            run_check("Schema", f"column · {tbl}.{c}",
                      lambda tbl=tbl, c=c: assert_column(tbl, c),
                      "Check the corresponding ALTER TABLE block in init_db().")

    def idempotent():
        database.init_db()
        database.init_db()
    run_check("Schema", "init_db() is idempotent", idempotent,
              "Re-running init_db should never error or duplicate data.")


# ---------------------------------------------------------------------------
# RBAC matrix — replicates main.py:_can_access logic so we don't have to
# import main.py (which calls st.set_page_config at import time).
# ---------------------------------------------------------------------------
_EXACT_ROLE_PAGES = {"📝 Entry Log": {"store_keeper"}}


def _can_access(role: str, page: str) -> bool:
    exact = _EXACT_ROLE_PAGES.get(page)
    if exact is not None:
        return role in exact
    required = config.PAGE_ACCESS.get(page, "admin")
    return config.ROLE_HIERARCHY.get(role, -1) >= config.ROLE_HIERARCHY.get(required, 99)


def register_rbac_checks() -> None:
    cases = [
        ("store_keeper", "📝 Entry Log",       True),
        ("hod",          "📝 Entry Log",       False),
        ("admin",        "📝 Entry Log",       False),
        ("supervisor",   "📝 Entry Log",       False),
        ("store_keeper", "📦 Live Dashboard",  False),
        ("supervisor",   "📦 Live Dashboard",  True),
        ("hod",          "📦 Live Dashboard",  True),
        ("admin",        "📦 Live Dashboard",  True),
        ("admin",        "🛡️ Admin Portal",    True),
        ("hod",          "🛡️ Admin Portal",    False),
        ("hod",          "📋 HOD Portal",      True),
        ("supervisor",   "📋 HOD Portal",      False),
        ("supervisor",   "📊 Reports",         True),
        ("store_keeper", "📊 Reports",         False),
    ]
    for role, page, expected in cases:
        def fn(role=role, page=page, expected=expected):
            got = _can_access(role, page)
            assert got == expected, (
                f"_can_access({role!r}, {page!r}) → {got}, expected {expected}"
            )
        verdict = "allow" if expected else "block"
        run_check("RBAC", f"{role} {verdict} {page}", fn,
                  "Tune PAGE_ACCESS or _EXACT_ROLE_PAGES in main.py.")


# ---------------------------------------------------------------------------
# Identity math: Closing = Opening + Received − Consumed − Returned
# ---------------------------------------------------------------------------
def check_identity_math() -> None:
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO receipts (Date, SAP_Code, Quantity, Site_ID) "
            "VALUES (DATE('now'), 'SAP-001', 30, 'HQ')",
        )
        conn.execute(
            "INSERT INTO consumption (Date, SAP_Code, Quantity, Site_ID) "
            "VALUES (DATE('now'), 'SAP-001', 12, 'HQ')",
        )
        conn.execute(
            "INSERT INTO returns (Date, SAP_Code, Quantity, Site_ID) "
            "VALUES (DATE('now'), 'SAP-001', 5, 'HQ')",
        )
        conn.commit()
    finally:
        conn.close()

    live = database.load_live_inventory(site_id="HQ")
    row = live[live["SAP_Code"] == "SAP-001"]
    assert not row.empty, "SAP-001 missing from live inventory"
    # Opening 100, +30 received, −12 consumed, −5 returned = 113
    closing = float(row.iloc[0]["Current_Stock"])
    assert abs(closing - 113.0) < 0.001, (
        f"Identity math wrong: expected 113, got {closing}"
    )


# ---------------------------------------------------------------------------
# Workflow: consumption stage → commit
# ---------------------------------------------------------------------------
def check_consumption_flow() -> None:
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO pending_issues "
            "(Date, SAP_Code, Quantity, Work_Type, Remarks, Site_ID, status) "
            "VALUES (DATE('now'), 'SAP-002', 7, 'Maintenance', 'auto', 'HQ', 'pending_hod')",
        )
        conn.commit()
    finally:
        conn.close()

    n = database.commit_eod()
    assert n >= 1, f"commit_eod returned {n}, expected ≥ 1"

    conn = database.get_connection()
    try:
        consumed = conn.execute(
            "SELECT COALESCE(SUM(Quantity),0) FROM consumption "
            "WHERE SAP_Code='SAP-002' AND Site_ID='HQ'",
        ).fetchone()[0]
        assert consumed >= 7, f"consumption row missing — sum={consumed}"
        # pending_issues should be drained
        left = conn.execute(
            "SELECT COUNT(*) FROM pending_issues "
            "WHERE status='pending_hod' AND Site_ID='HQ'",
        ).fetchone()[0]
        assert left == 0, f"pending_issues still has {left} pending_hod rows"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Workflow: receipt staging → HOD approval → ledger commit
# ---------------------------------------------------------------------------
def check_receipt_flow() -> None:
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO pending_receipts "
            "(Date, SAP_Code, Quantity, Supplier, Site_ID, status) "
            "VALUES (DATE('now'), 'SAP-001', 25, 'TestSup', 'HQ', 'pending_hod')",
        )
        conn.commit()

        n = database.commit_pending_receipts(conn, site_id="HQ", username=TEST_HOD)
        assert n >= 1, f"commit_pending_receipts returned {n}"
        rcv = conn.execute(
            "SELECT COALESCE(SUM(Quantity),0) FROM receipts "
            "WHERE SAP_Code='SAP-001' AND Supplier='TestSup' AND Site_ID='HQ'",
        ).fetchone()[0]
        assert rcv >= 25, f"receipts ledger missing the committed row (sum={rcv})"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Workflow: SK return submit → HOD approve → returns ledger row
# ---------------------------------------------------------------------------
def check_returns_flow() -> None:
    # Seed a receipt the return can attach to.
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO receipts (Date, SAP_Code, Quantity, DN_No, PR_Number, Site_ID) "
            "VALUES (DATE('now'), 'SAP-003', 18, 'DN-AUTO-1', 'PR-AUTO-1', 'HQ')",
        )
        conn.commit()
    finally:
        conn.close()

    recv_df = database.get_returnable_receipts("HQ")
    assert not recv_df.empty, "get_returnable_receipts returned no rows"
    rubber = recv_df[recv_df["SAP_Code"] == "SAP-003"].iloc[0]

    rid = database.submit_return_request(
        site_id="HQ", sap_code="SAP-003",
        quantity=4.0,
        return_reason="Defective",
        return_dn_no="RDN-AUTO-1",
        received_receipt_row={
            "Material_Code": rubber.get("Material_Code", ""),
            "Equipment_Description": rubber.get("Equipment_Description", ""),
            "Date": str(rubber.get("Date", "")),
            "DN_No": str(rubber.get("DN_No", "")),
            "PR_Number": str(rubber.get("PR_Number", "")),
            "Lot_Number": str(rubber.get("Lot_Number", "")),
            "received_qty": float(rubber.get("received_qty", 0) or 0),
        },
        submitted_by=TEST_SK,
    )
    assert isinstance(rid, int) and rid > 0, "submit_return_request returned bad id"

    pending = database.get_pending_returns("HQ")
    assert (pending["id"] == rid).any(), "pending_returns missing the submitted row"

    ok, msg = database.approve_return_request(rid, approver=TEST_HOD)
    assert ok, f"approve_return_request failed: {msg}"

    conn = database.get_connection()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM returns WHERE SAP_Code='SAP-003' AND Site_ID='HQ'",
        ).fetchone()[0]
        assert n >= 1, "approved return did not land in `returns` ledger"
        # Idempotent: re-approving should not duplicate
        ok2, msg2 = database.approve_return_request(rid, approver=TEST_HOD)
        assert not ok2, "Re-approving an already-approved return should refuse"
    finally:
        conn.close()


def check_returns_reject() -> None:
    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO pending_returns "
            "(Site_ID, SAP_Code, Quantity, Return_Reason, Return_DN_No, "
            " submitted_by) VALUES (?,?,?,?,?,?)",
            ("HQ", "SAP-002", 1, "Wrong item", "RDN-REJ", TEST_SK),
        )
        rid = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    assert database.reject_return_request(rid, approver=TEST_HOD, reason="auto-reject")
    df = database.get_pending_returns("HQ")
    assert not (df["id"] == rid).any(), "Rejected return should leave pending list"


# ---------------------------------------------------------------------------
# Returnable items (tool loans)
# ---------------------------------------------------------------------------
def check_returnable_items() -> None:
    database.insert_returnable_item(
        material_name="Torque Wrench",
        uom="PCS", qty=1.0,
        borrower_name="bob",
        borrower_phone="+966500000001",
        expected_return_time=str(datetime.datetime.now() + datetime.timedelta(hours=2)),
        site_id="HQ",
    )
    conn = database.get_connection()
    try:
        df = database.get_returnable_items(conn, site_id="HQ")
        assert not df.empty, "returnable item list empty after insert"
        rid = int(df.iloc[0]["id"])
    finally:
        conn.close()
    database.mark_item_returned(item_id=rid)
    conn = database.get_connection()
    try:
        status = conn.execute(
            "SELECT status FROM returnable_items WHERE id=?", (rid,),
        ).fetchone()[0]
        assert status == "returned", f"expected status='returned', got {status!r}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# QR approval flow
# ---------------------------------------------------------------------------
def check_qr_flow() -> None:
    rid = database.submit_qr_request(
        site_id="HQ", sap_code="SAP-001",
        requested_by=TEST_SK, quantity=3,
    )
    assert rid > 0
    df = database.list_qr_requests(site_id="HQ", status="pending")
    assert (df["id"] == rid).any(), "pending QR not listed"
    database.approve_qr_request(rid, approver=TEST_HOD)
    df2 = database.list_qr_requests(site_id="HQ", status="approved")
    assert (df2["id"] == rid).any(), "approved QR not in approved list"

    rid2 = database.submit_qr_request(
        site_id="HQ", sap_code="SAP-002",
        requested_by=TEST_SK, quantity=1,
    )
    database.reject_qr_request(rid2, approver=TEST_HOD, reason="test reject")
    df3 = database.list_qr_requests(site_id="HQ", status="rejected")
    assert (df3["id"] == rid2).any(), "rejected QR not in rejected list"


# ---------------------------------------------------------------------------
# MTC documents (rubber receipts)
# ---------------------------------------------------------------------------
class _StubUpload:
    """Mimics streamlit.UploadedFile enough for save_mtc_document / save_entry_attachment."""
    def __init__(self, name: str, data: bytes, mime: str = "application/pdf"):
        self.name = name
        self.type = mime
        self._data = data
        self._pos  = 0
    def read(self) -> bytes:
        return self._data
    def seek(self, p: int) -> None:
        self._pos = p


def check_mtc_attached() -> None:
    rid = database.save_mtc_document(
        site_id="HQ", sap_code="SAP-003",
        material_code="MC-003", lot_number="LOT-AUTO-1",
        quantity=4.0, mtc_number="MTC-AUTO-1",
        uploaded_file=_StubUpload("mtc.pdf", b"%PDF-1.4 mock"),
        pending_receipt_id=999, submitted_by=TEST_SK,
    )
    assert rid > 0
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT status, length(file_blob), mtc_number FROM mtc_documents WHERE id=?",
            (rid,),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "attached", f"expected status='attached', got {row[0]}"
    assert row[1] > 0,           "BLOB not stored"
    assert row[2] == "MTC-AUTO-1"


def check_mtc_missing_then_email() -> None:
    rid = database.save_mtc_document(
        site_id="HQ", sap_code="SAP-003",
        material_code="MC-003", lot_number="LOT-AUTO-2",
        quantity=2.0, mtc_number="",
        uploaded_file=None,
        pending_receipt_id=1000, submitted_by=TEST_SK,
    )
    miss = database.get_missing_mtc_for_site("HQ")
    assert (miss["id"] == rid).any(), "Missing-MTC row not surfaced"
    n = database.mark_mtc_emailed([rid])
    assert n == 1


# ---------------------------------------------------------------------------
# Attachments — BLOB round-trip
# ---------------------------------------------------------------------------
def check_entry_attachment() -> None:
    fake = _StubUpload("dn-receipt.pdf", b"%PDF-1.4 attachment")
    aid = database.save_entry_attachment(
        site_id="HQ", doc_type="receipt", doc_number="DN-AUTO-99",
        file_obj=fake, uploaded_by=TEST_SK,
        entry_table="pending_receipts", entry_id=9999,
    )
    assert aid > 0
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT file_name, file_size, file_blob, disk_path "
            "FROM entry_attachments WHERE id=?",
            (aid,),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "dn-receipt.pdf"
    assert row[1] == len(b"%PDF-1.4 attachment")
    assert bytes(row[2]) == b"%PDF-1.4 attachment", "BLOB round-trip mismatch"
    if row[3]:
        assert os.path.exists(row[3]), f"disk mirror missing: {row[3]}"


# ---------------------------------------------------------------------------
# Reports — every report_* function must run without raising and return a DF
# ---------------------------------------------------------------------------
def _df_ok(out) -> None:
    # Most reports return (DataFrame, summary); some return DataFrame only.
    df = out[0] if isinstance(out, tuple) else out
    assert isinstance(df, pd.DataFrame), f"report returned {type(df).__name__}, expected DataFrame"


def check_reports() -> None:
    today = datetime.date.today().isoformat()
    a_month_ago = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    _df_ok(database.report_daily_consumption(a_month_ago, today, site_id="HQ"))
    _df_ok(database.report_daily_receipts  (a_month_ago, today, site_id="HQ"))
    _df_ok(database.report_monthly_summary (a_month_ago, today, site_id="HQ"))
    _df_ok(database.report_pr_status(site_id="HQ"))
    _df_ok(database.report_fefo_compliance (a_month_ago, today, site_id="HQ"))
    _df_ok(database.report_audit_export    (a_month_ago, today))


def check_report_columns() -> None:
    today = datetime.date.today().isoformat()
    a_month_ago = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    df, _ = database.report_daily_receipts(a_month_ago, today, site_id="HQ")
    must_have = {"SAP_Code", "Material_Code", "Material", "Quantity"}
    missing = must_have - set(df.columns)
    assert not missing, f"Daily Receipts report missing columns: {missing}"


# ---------------------------------------------------------------------------
# Mailer drafts — patched so nothing actually opens
# ---------------------------------------------------------------------------
def check_mailer_drafts() -> None:
    import mailer
    # Rubber MTC email
    df = pd.DataFrame([{
        "SAP_Code": "SAP-003", "Equipment_Description": "O-Ring Rubber",
        "Lot_Number": "LOT-1", "Quantity": 4,
    }])
    ok, _ = mailer.draft_rubber_mtc_email("HQ", df)
    assert ok, "draft_rubber_mtc_email returned False"
    # Return-approved email
    ok2, _ = mailer.draft_return_logistics_email("HQ", {
        "SAP_Code": "SAP-003", "Material_Code": "MC-003",
        "Equipment_Description": "O-Ring Rubber",
        "Quantity": 4, "Return_Reason": "Defective",
        "Return_DN_No": "RDN-AUTO-1",
        "received_date": "2026-06-01", "received_dn_no": "DN-AUTO-1",
        "received_qty": 18, "PR_Number": "PR-AUTO-1", "Lot_Number": "LOT-1",
    })
    assert ok2, "draft_return_logistics_email returned False"


# ---------------------------------------------------------------------------
# Audit log + WhatsApp queue + Sites
# ---------------------------------------------------------------------------
def check_audit_log() -> None:
    database.log_audit_action(TEST_SK, "AUTO_TEST", "bug_check", "harness ping")
    conn = database.get_connection()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM system_audit_log WHERE action_type='AUTO_TEST'",
        ).fetchone()[0]
        assert n >= 1
    finally:
        conn.close()


def check_whatsapp_queue() -> None:
    database.queue_whatsapp_alert("+966500000099", "smoke test")
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT status, message FROM whatsapp_queue "
            "WHERE phone_number='+966500000099' ORDER BY id DESC LIMIT 1",
        ).fetchone()
        assert row is not None and row[0] == "pending"
    finally:
        conn.close()


def check_sites() -> None:
    sites = database.get_sites()
    assert "HQ" in sites, f"HQ not in get_sites() → {sites}"


# ---------------------------------------------------------------------------
# Module import smoke — every page module must load without raising
# ---------------------------------------------------------------------------
def check_module_imports() -> None:
    failed = []
    for mod in [
        "config", "database", "mailer", "whatsapp_worker",
        "auth", "cache_layer", "ui_components", "reports",
        "pages_internal.daily_issue_log",
        "pages_internal.hod_portal",
        "pages_internal.admin_portal",
        "pages_internal.live_dashboard",
        "pages_internal.reports_page",
    ]:
        try:
            importlib.import_module(mod)
        except Exception as e:
            failed.append(f"{mod} → {type(e).__name__}: {e}")
    assert not failed, "Module import failures:\n  " + "\n  ".join(failed)


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------
def write_report() -> Path:
    out = REPO_ROOT / "BUG_REPORT.md"
    total   = len(RESULTS)
    passed  = sum(1 for r in RESULTS if r["status"] == "PASS")
    failed  = sum(1 for r in RESULTS if r["status"] == "FAIL")
    by_area: dict[str, list[dict]] = {}
    for r in RESULTS:
        by_area.setdefault(r["area"], []).append(r)

    lines = []
    lines.append("# Bug Check Report")
    lines.append("")
    lines.append(f"**Run at:** `{datetime.datetime.now().isoformat(timespec='seconds')}`  ")
    lines.append(f"**Throwaway DB:** `{TMP_DB}`  ")
    lines.append(f"**Total checks:** {total}  ")
    lines.append(f"**Passing:** {passed}  ")
    lines.append(f"**Failing:** {failed}  ")
    lines.append("")
    lines.append(
        "_The harness writes a fresh SQLite file under your system temp dir, "
        "seeds it, exercises every flow, then deletes the temp dir. "
        "`gi_database.db` is never touched._"
    )
    lines.append("")

    # Failures first
    fails = [r for r in RESULTS if r["status"] == "FAIL"]
    lines.append(f"## ❌ Failures ({len(fails)})")
    lines.append("")
    if not fails:
        lines.append("_None — every check passed._")
        lines.append("")
    else:
        for r in fails:
            lines.append(f"### {r['area']} · {r['name']}")
            lines.append(f"- **Error:** `{r['error']}`")
            if r["hint"]:
                lines.append(f"- **Hint:** {r['hint']}")
            if r["trace"]:
                lines.append("- **Trace:**")
                lines.append("```")
                lines.append(r["trace"].rstrip())
                lines.append("```")
            lines.append("")

    lines.append("## ✅ Passing by area")
    lines.append("")
    for area in sorted(by_area):
        rows = by_area[area]
        p = sum(1 for r in rows if r["status"] == "PASS")
        f = sum(1 for r in rows if r["status"] == "FAIL")
        lines.append(f"### {area} — {p}/{p+f}")
        for r in rows:
            glyph = "✅" if r["status"] == "PASS" else "❌"
            lines.append(f"- {glyph} {r['name']}"
                         + (f" ({r['elapsed_ms']} ms)" if VERBOSE else ""))
        lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    print(f"▶ Bug-check harness · DB → {TMP_DB}")
    try:
        seed()
    except Exception as e:
        print(f"✖ Seeding crashed: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 2

    # Order matters slightly — schema first so later checks can rely on it.
    register_schema_checks()
    register_rbac_checks()

    run_check("Module load", "import every page module",
              check_module_imports,
              "Look for missing imports or top-level NameErrors.")

    run_check("Math",        "Identity: Closing = Opening + R − C − Rt",
              check_identity_math,
              "Compare load_live_inventory() output vs hand-summed ledger totals.")

    run_check("Consumption", "Stage → commit_eod",
              check_consumption_flow,
              "Check commit_eod() and pending_issues schema.")
    run_check("Receipts",    "Stage → commit_pending_receipts",
              check_receipt_flow,
              "Verify commit_pending_receipts() copies status='pending_hod' rows.")
    run_check("Returns",     "Submit → approve → ledger row",
              check_returns_flow)
    run_check("Returns",     "Reject removes from pending list",
              check_returns_reject)
    run_check("Returnable",  "Tool loan → mark returned",
              check_returnable_items)
    run_check("QR",          "Submit → approve / reject",
              check_qr_flow)
    run_check("MTC",         "Attached rubber MTC stored as BLOB",
              check_mtc_attached)
    run_check("MTC",         "Missing MTC → mark_emailed flow",
              check_mtc_missing_then_email)
    run_check("Attachments", "BLOB round-trip + disk mirror",
              check_entry_attachment)
    run_check("Reports",     "Every report_* runs without raising",
              check_reports,
              "Inspect the failing report's SQL.")
    run_check("Reports",     "Daily Receipts has Material_Code column",
              check_report_columns)
    run_check("Mailer",      "Draft helpers (Outlook / mailto patched)",
              check_mailer_drafts)
    run_check("Audit",       "log_audit_action writes row",
              check_audit_log)
    run_check("WhatsApp",    "queue_whatsapp_alert writes pending row",
              check_whatsapp_queue)
    run_check("Sites",       "HQ visible to get_sites()",
              check_sites)

    out = write_report()
    print()
    fail_n = sum(1 for r in RESULTS if r["status"] == "FAIL")
    pass_n = len(RESULTS) - fail_n
    print(f"▶ {pass_n} passed, {fail_n} failed")
    print(f"▶ Report: {out.relative_to(REPO_ROOT)}")
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        # Restore the real Popen so the rest of the process can use it
        subprocess.Popen = _orig_popen  # type: ignore[assignment]
        # Clean up the throwaway directory
        try:
            shutil.rmtree(TMP_ROOT, ignore_errors=True)
        except Exception:
            pass
    sys.exit(rc)
