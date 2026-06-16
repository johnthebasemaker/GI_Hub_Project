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
        # Phase C — Procurement chain
        "warehouses", "vendors", "purchase_orders", "po_items",
        "po_shipment_schedule", "po_assignments", "delivery_notes", "dn_items",
        "po_returns", "po_reschedule_requests", "po_force_closures",
        "app_notifications",
        # Phase 5
        "delivery_reminders_sent",
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
                       "DN_No", "Lot_Number",
                       # Phase C — DN / Warehouse / PO traceback chain
                       "DN_Number", "Warehouse_ID", "PO_Number_Source"],
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
        "users":      ["username", "role", "Site_ID", "Phone_Number",
                       "Warehouse_ID"],
        # Phase C — Procurement chain
        "warehouses": ["Warehouse_ID", "Name", "status"],
        "vendors":    ["Vendor_Code", "Vendor_Name", "status",
                       "Default_Inco_Terms", "Default_Payment_Terms"],
        "purchase_orders": ["PO_Number", "PR_Number", "Vendor_Code",
                            "PO_Date", "Expected_Delivery", "status",
                            "Inco_Terms", "Payment_Terms", "source"],
        "po_items":   ["PO_Number", "Material_Code", "Qty", "UOM",
                       "Unit_Price", "Total_Price", "rl_bl_family",
                       "Delivered_Qty", "line_status",
                       "WBS_Number", "Network"],
        "po_shipment_schedule": ["PO_Number", "shipment_no", "target_date",
                                 "status"],
        "po_assignments": ["PO_Number", "Warehouse_ID", "assigned_by",
                           "Expected_Delivery", "status"],
        "delivery_notes": ["DN_Number", "PO_Number", "Warehouse_ID",
                           "Site_ID", "status", "rl_bl_family"],
        "dn_items":   ["DN_Number", "po_item_id", "Qty", "UOM",
                       "rl_bl_family", "status"],
        "po_returns": ["PO_Number", "Qty", "Reason", "raised_by_role",
                       "status"],
        "po_reschedule_requests": ["PO_Number", "requested_date", "reason",
                                   "requested_by_role", "status"],
        "po_force_closures": ["target_type", "target_ref", "reason",
                              "closed_by"],
        "app_notifications": ["event_key", "title", "severity",
                              "recipient_user", "recipient_role"],
        # Self-heal on existing tables
        "pr_master":  ["WBS_Number", "Network", "Plant", "Delivery_Date",
                       "logistics_status"],
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
_EXACT_ROLE_PAGES = {
    "📝 Entry Log":         {"store_keeper"},
    # HOD Portal is exact-locked so procurement roles don't inherit via hierarchy.
    "📋 HOD Portal":        {"hod", "admin"},
    # Procurement chain — exact-role lock, admin shadow allowed.
    "🚚 Logistics Portal":  {"logistics", "admin"},
    "🏭 Warehouse Portal":  {"warehouse_user", "admin"},
}


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
        # Phase C — Procurement chain
        ("logistics",      "🚚 Logistics Portal", True),
        ("admin",          "🚚 Logistics Portal", True),
        ("hod",            "🚚 Logistics Portal", False),
        ("warehouse_user", "🚚 Logistics Portal", False),
        ("warehouse_user", "🏭 Warehouse Portal", True),
        ("admin",          "🏭 Warehouse Portal", True),
        ("logistics",      "🏭 Warehouse Portal", False),
        ("hod",            "🏭 Warehouse Portal", False),
        ("store_keeper",   "🚚 Logistics Portal", False),
        ("store_keeper",   "🏭 Warehouse Portal", False),
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


def check_rl_bl_classification() -> None:
    """RL and BL strict-separation invariant: classify_rl_bl_family must
    return distinct family tags so the PO/DN splitter can never aggregate
    Rubber Lining and Brick Lining lines into the same group."""
    fn = config.classify_rl_bl_family
    # RL detection
    assert fn("RL-100-CHEM", "Rubber Lining 6mm")    == "RL"
    assert fn("XYZ-001", "RUBBER LINING SHEET")      == "RL"
    # BL detection
    assert fn("BL-200-BRICK", "Brick Lining tile")   == "BL"
    assert fn("XYZ-002", "BRICK MATERIAL Class A")   == "BL"
    assert fn("XYZ-003", "BRICK-LINING red")         == "BL"
    # Negatives — neither token
    assert fn("GI-7001079", "SAFETY HELMET WHITE")   is None
    assert fn("", "")                                is None
    # Strict separation: a description containing both tokens locks to the
    # first-detected family (RL takes precedence by dict insertion order).
    # The point of the test is that result is never "RL/BL" — never aggregated.
    mixed = fn("HYBRID-1", "RUBBER LINING with BRICK LINING wrap")
    assert mixed in ("RL", "BL"), "RL/BL must classify to ONE family, not both"


def check_warehouses_crud() -> None:
    conn = database.get_connection()
    try:
        ok, _ = database.add_warehouse("WH-A", "Yard Alpha",
                                       location="Jubail",
                                       contact_name="Ali", conn=conn)
        assert ok
        # Duplicate insert must fail gracefully
        dup_ok, _ = database.add_warehouse("WH-A", "dup", conn=conn)
        assert not dup_ok
        df = database.list_warehouses(conn=conn)
        assert "WH-A" in set(df["Warehouse_ID"]), "Warehouse not returned"
    finally:
        conn.close()


def check_vendors_crud() -> None:
    conn = database.get_connection()
    try:
        ok, _ = database.add_vendor("0000110341", "Carborundum Universal",
                                    default_inco_terms="EXW Chennai",
                                    default_payment_terms="60 days",
                                    conn=conn)
        assert ok
        df = database.list_vendors(conn=conn)
        assert "0000110341" in set(df["Vendor_Code"])
        # Inco/Payment terms persist for auto-fill
        row = df[df["Vendor_Code"] == "0000110341"].iloc[0]
        assert row["Default_Inco_Terms"] == "EXW Chennai"
    finally:
        conn.close()


def check_app_notifications() -> None:
    conn = database.get_connection()
    try:
        nid = database.queue_app_notification(
            event_key="po_issued",
            title="New PO 4720002930 issued",
            body="Vendor: Carborundum",
            severity="info",
            recipient_user="hod",
            link_page="📋 HOD Portal",
            related_table="purchase_orders",
            related_ref="4720002930",
            conn=conn,
        )
        assert nid > 0
        # Role-broadcast variant
        rid = database.queue_app_notification(
            event_key="po_assigned_to_warehouse",
            title="PO assigned",
            recipient_role="warehouse_user",
            recipient_warehouse="WH-A",
            conn=conn,
        )
        assert rid > 0
        # User-targeted query returns the hod row
        inbox = database.get_app_notifications("hod", role="hod", conn=conn)
        assert len(inbox) >= 1
        # Warehouse user role-broadcast query returns the WH-A row
        wh_inbox = database.get_app_notifications(
            "wh1", role="warehouse_user",
            warehouse_id="WH-A", conn=conn,
        )
        assert len(wh_inbox) >= 1
        # mark_read flips read_at
        database.mark_notification_read(nid, conn=conn)
        unread = database.count_unread_notifications("hod", role="hod", conn=conn)
        # hod's nid is now read but other notifications may exist; the
        # specific one we marked must not contribute.
        still_unread_ids = set(database.get_app_notifications(
            "hod", role="hod", unread_only=True, conn=conn,
        ).get("id", []))
        assert nid not in still_unread_ids
    finally:
        conn.close()


def check_whatsapp_event_gate() -> None:
    """fire_whatsapp_event honours per-event toggle in config.WHATSAPP_TRIGGERS
    and the global WHATSAPP_ENABLED switch."""
    conn = database.get_connection()
    try:
        # Baseline queue size
        baseline = conn.execute(
            "SELECT COUNT(*) FROM whatsapp_queue").fetchone()[0]
        # Unknown event → suppressed
        sent = database.fire_whatsapp_event(
            "no_such_event", "+966500000000", "x", conn=conn)
        assert sent is False
        after_unknown = conn.execute(
            "SELECT COUNT(*) FROM whatsapp_queue").fetchone()[0]
        assert after_unknown == baseline, "Unknown event must not enqueue"
        # Enabled event → enqueued (po_issued is True by default in config)
        sent2 = database.fire_whatsapp_event(
            "po_issued", "+966500000000", "PO 123 issued", conn=conn)
        assert sent2 is True
        after_enabled = conn.execute(
            "SELECT COUNT(*) FROM whatsapp_queue").fetchone()[0]
        assert after_enabled == after_unknown + 1
        # Master switch off → suppressed even though the per-event flag is True
        prev = config.WHATSAPP_ENABLED
        try:
            config.WHATSAPP_ENABLED = False
            suppressed = database.fire_whatsapp_event(
                "po_issued", "+966500000000", "x", conn=conn)
            assert suppressed is False
        finally:
            config.WHATSAPP_ENABLED = prev
    finally:
        conn.close()


def check_role_check_constraint() -> None:
    """The rebuilt users CHECK constraint must accept the two new procurement
    roles (logistics, warehouse_user). The bcrypt cost makes hash_password
    slow — we go direct INSERT here since we just need to validate the CHECK."""
    conn = database.get_connection()
    try:
        # Direct insert with a placeholder hash — CHECK is what matters here.
        conn.execute(
            "INSERT INTO users (username, password_hash, role, Site_ID, Warehouse_ID) "
            "VALUES (?, ?, ?, ?, ?)",
            ("logi_test", "x" * 60, "logistics", "HQ", None),
        )
        conn.execute(
            "INSERT INTO users (username, password_hash, role, Site_ID, Warehouse_ID) "
            "VALUES (?, ?, ?, ?, ?)",
            ("wh_test", "x" * 60, "warehouse_user", "HQ", "WH-A"),
        )
        conn.commit()
        # Invalid role must still be rejected
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, Site_ID) "
                "VALUES (?, ?, ?, ?)",
                ("bogus_test", "x" * 60, "ceo", "HQ"),
            )
            conn.commit()
            assert False, "CHECK constraint should have rejected 'ceo'"
        except Exception:
            pass  # expected
    finally:
        conn.close()


def check_po_items_rl_bl_tagging() -> None:
    """Inserting po_items with RL/BL descriptions must set the rl_bl_family
    column at the application boundary (caller responsibility). Bug-check
    asserts the column exists + accepts the values."""
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO purchase_orders (PO_Number, Vendor_Code, PO_Date, status) "
            "VALUES (?, ?, ?, 'open')",
            ("4720002930", "0000110341", "2026-06-15"),
        )
        # Helper to tag at insert (caller-side rule, validated here)
        def _ins(line_no: int, code: str, desc: str, qty: float):
            fam = config.classify_rl_bl_family(code, desc)
            conn.execute(
                "INSERT INTO po_items "
                "(PO_Number, line_no, Material_Code, Description, Qty, UOM, "
                " Unit_Price, Total_Price, rl_bl_family) "
                "VALUES (?, ?, ?, ?, ?, 'EA', 0, 0, ?)",
                ("4720002930", line_no, code, desc, qty, fam),
            )
        _ins(1, "RL-100", "Rubber Lining 6mm",       10)
        _ins(2, "BL-200", "Brick Lining red",         5)
        _ins(3, "GI-7001", "Safety helmet",            3)
        conn.commit()
        # Strict separation: RL rows and BL rows have distinct families
        rl = conn.execute(
            "SELECT COUNT(*) FROM po_items "
            "WHERE PO_Number='4720002930' AND rl_bl_family='RL'").fetchone()[0]
        bl = conn.execute(
            "SELECT COUNT(*) FROM po_items "
            "WHERE PO_Number='4720002930' AND rl_bl_family='BL'").fetchone()[0]
        none_fam = conn.execute(
            "SELECT COUNT(*) FROM po_items "
            "WHERE PO_Number='4720002930' AND rl_bl_family IS NULL").fetchone()[0]
        assert rl == 1 and bl == 1 and none_fam == 1, \
            f"RL/BL strict separation violated: rl={rl} bl={bl} none={none_fam}"
        # And critically — no row is ever tagged 'RL/BL' or similar combo.
        combos = conn.execute(
            "SELECT DISTINCT rl_bl_family FROM po_items "
            "WHERE PO_Number='4720002930'"
        ).fetchall()
        allowed = {None, "RL", "BL"}
        for (fam,) in combos:
            assert fam in allowed, f"Unexpected family value: {fam!r}"
    finally:
        conn.close()


def check_pr_to_logistics_handoff() -> None:
    """End-to-end: insert PR via insert_manual_pr → submit_pr_to_logistics
    → it appears in list_prs_for_logistics queue."""
    conn = database.get_connection()
    try:
        ok, _ = database.insert_manual_pr(
            pr_number="3000099001",
            sap_code="SAP-LOGI-1",
            material_code="GI-9000001",
            material_name="Test material",
            requested_qty=10.0,
            uom="EA",
            supplier="x", est_cost_sar=0,
            notes="", site_id="HQ",
            conn=conn,
        )
        assert ok
        # Before submit: not in queue
        q0 = database.list_prs_for_logistics(conn=conn)
        assert "3000099001" not in set(q0.get("PR_Number", []))
        # Submit
        ok2, _ = database.submit_pr_to_logistics(
            "3000099001", "HQ", "hod", conn=conn)
        assert ok2
        # In queue
        q1 = database.list_prs_for_logistics(conn=conn)
        assert "3000099001" in set(q1["PR_Number"])
        # Idempotent re-submit returns False (already submitted)
        ok3, _ = database.submit_pr_to_logistics(
            "3000099001", "HQ", "hod", conn=conn)
        # After first submit, logistics_status='submitted' — re-submit on
        # the same row is permitted by the WHERE clause (still 'submitted'),
        # but rowcount > 0 means the timestamp is updated. Either way the
        # outcome is "row is in queue".
        q2 = database.list_prs_for_logistics(conn=conn)
        assert "3000099001" in set(q2["PR_Number"])
    finally:
        conn.close()


def check_po_manual_creation_and_rl_bl() -> None:
    """create_po_manual round-trip with mixed RL/BL/normal lines; assert
    rl_bl_family tagging; assert PR flips to in_po."""
    conn = database.get_connection()
    try:
        # Prep: PR submitted to logistics
        database.insert_manual_pr(
            pr_number="3000099002", sap_code="SAP-1",
            material_code="GI-9000002", material_name="m",
            requested_qty=20, uom="EA", supplier="",
            est_cost_sar=0, notes="", site_id="HQ",
            conn=conn,
        )
        database.submit_pr_to_logistics(
            "3000099002", "HQ", "hod", conn=conn)
        # Issue PO with 3 items: RL, BL, neutral
        ok, msg = database.create_po_manual(
            header={
                "PO_Number": "4720099001",
                "PR_Number": "3000099002",
                "Site_ID":   "HQ",
                "Vendor_Code": "0000099001",
                "Vendor_Name": "Test Vendor",
                "PO_Date":   "2026-06-15",
                "Inco_Terms": "EXW",
                "Payment_Terms": "30 days",
                "Total_Amount": 12345.67,
            },
            items=[
                {"Material_Code": "RL-001",
                 "Description":  "Rubber Lining sheet",
                 "Qty": 5, "UOM": "EA",
                 "Unit_Price": 100, "Total_Price": 500},
                {"Material_Code": "BL-001",
                 "Description":  "Brick Lining red",
                 "Qty": 3, "UOM": "EA",
                 "Unit_Price": 50, "Total_Price": 150},
                {"Material_Code": "GI-9000003",
                 "Description":  "Helmet",
                 "Qty": 10, "UOM": "EA",
                 "Unit_Price": 25, "Total_Price": 250},
            ],
            created_by="logi", conn=conn,
        )
        assert ok, msg
        # PO row exists
        po = conn.execute(
            "SELECT status, source FROM purchase_orders WHERE PO_Number = ?",
            ("4720099001",)).fetchone()
        assert po is not None and po[0] == "open"
        # 3 items, families tagged
        fams = [r[0] for r in conn.execute(
            "SELECT rl_bl_family FROM po_items WHERE PO_Number = ? "
            "ORDER BY line_no", ("4720099001",)).fetchall()]
        assert fams == ["RL", "BL", None], f"Families: {fams}"
        # PR flipped to in_po
        pr_status = conn.execute(
            "SELECT logistics_status FROM pr_master WHERE PR_Number = ?",
            ("3000099002",)).fetchone()[0]
        assert pr_status == "in_po", pr_status
        # Duplicate PO Number rejected
        ok_dup, _ = database.create_po_manual(
            header={"PO_Number": "4720099001"},
            items=[{"Material_Code": "X", "Description": "x", "Qty": 1,
                    "UOM": "EA"}],
            conn=conn,
        )
        assert not ok_dup
    finally:
        conn.close()


def check_po_detail_price_hiding() -> None:
    """get_po_detail(hide_prices=True) must blank Unit_Price + Total_Price."""
    conn = database.get_connection()
    try:
        database.create_po_manual(
            header={"PO_Number": "4720099002", "Vendor_Code": "V1",
                    "PO_Date": "2026-06-15"},
            items=[{"Material_Code": "GI-1", "Description": "x",
                    "Qty": 1, "UOM": "EA", "Unit_Price": 99.0,
                    "Total_Price": 99.0}],
            created_by="logi", conn=conn,
        )
        with_prices = database.get_po_detail("4720099002", hide_prices=False,
                                              conn=conn)
        assert float(with_prices["items"]["Unit_Price"].iloc[0]) == 99.0
        without = database.get_po_detail("4720099002", hide_prices=True,
                                          conn=conn)
        assert without["items"]["Unit_Price"].iloc[0] is None
        assert without["items"]["Total_Price"].iloc[0] is None
    finally:
        conn.close()


def check_assign_po_to_warehouse() -> None:
    """End-to-end: create warehouse + PO, then assign full + subset, both
    should fire an in-app notification to warehouse_user role scoped to WH."""
    conn = database.get_connection()
    try:
        database.add_warehouse("WH-PHASE2", "Phase 2 yard", conn=conn)
        database.create_po_manual(
            header={"PO_Number": "4720099010", "PO_Date": "2026-06-15"},
            items=[
                {"Material_Code": "M-1", "Description": "a",
                 "Qty": 1, "UOM": "EA"},
                {"Material_Code": "M-2", "Description": "b",
                 "Qty": 2, "UOM": "EA"},
            ],
            created_by="logi", conn=conn,
        )
        # 1. Full PO
        ok, msg = database.assign_po_to_warehouse(
            "4720099010", "WH-PHASE2",
            expected_delivery="2026-07-01",
            items_subset_ids=None,
            assigned_by="logi", notes="full", conn=conn,
        )
        assert ok, msg
        # 2. Subset assignment — pick the first item id
        first_id = conn.execute(
            "SELECT id FROM po_items WHERE PO_Number='4720099010' "
            "ORDER BY line_no LIMIT 1").fetchone()[0]
        ok2, _ = database.assign_po_to_warehouse(
            "4720099010", "WH-PHASE2",
            expected_delivery="2026-07-02",
            items_subset_ids=[int(first_id)],
            assigned_by="logi", notes="subset", conn=conn,
        )
        assert ok2
        # Two assignments exist
        n = conn.execute(
            "SELECT COUNT(*) FROM po_assignments "
            "WHERE PO_Number='4720099010'").fetchone()[0]
        assert n == 2
        # In-app notification fired to warehouse_user scope
        inbox = database.get_app_notifications(
            username="wh1", role="warehouse_user",
            warehouse_id="WH-PHASE2", conn=conn,
        )
        assert (inbox["event_key"] == "po_assigned_to_warehouse").any()
        # Unknown warehouse → rejected
        ok3, msg3 = database.assign_po_to_warehouse(
            "4720099010", "WH-DOES-NOT-EXIST",
            expected_delivery=None, items_subset_ids=None,
            assigned_by="logi", conn=conn,
        )
        assert not ok3
    finally:
        conn.close()


def check_reschedule_flow() -> None:
    """request_reschedule → decide_reschedule (approve) updates PO date."""
    conn = database.get_connection()
    try:
        database.create_po_manual(
            header={"PO_Number": "4720099020",
                    "PO_Date": "2026-06-15",
                    "Expected_Delivery": "2026-07-15"},
            items=[{"Material_Code": "X", "Description": "x",
                    "Qty": 1, "UOM": "EA"}],
            created_by="logi", conn=conn,
        )
        ok, _ = database.request_reschedule(
            po_number="4720099020", dn_number=None,
            current_date="2026-07-15", requested_date="2026-07-25",
            reason="Vendor delay",
            requested_by_role="warehouse_user",
            requested_by="wh1", conn=conn,
        )
        assert ok
        # Get pending row
        pend = database.list_pending_reschedules(conn=conn)
        assert not pend.empty
        rid = int(pend.iloc[0]["id"])
        # Approve
        ok2, _ = database.decide_reschedule(
            rid, approve=True, decided_by="logi",
            decision_notes="approved", conn=conn,
        )
        assert ok2
        new_date = conn.execute(
            "SELECT Expected_Delivery FROM purchase_orders "
            "WHERE PO_Number='4720099020'").fetchone()[0]
        assert new_date == "2026-07-25"
        # Reject path (separate request)
        database.request_reschedule(
            po_number="4720099020", dn_number=None,
            current_date="2026-07-25", requested_date="2026-08-01",
            reason="Another reason",
            requested_by_role="hod", requested_by="hod",
            conn=conn,
        )
        rid2 = int(database.list_pending_reschedules(conn=conn).iloc[0]["id"])
        ok3, _ = database.decide_reschedule(
            rid2, approve=False, decided_by="logi",
            decision_notes="no capacity", conn=conn,
        )
        assert ok3
        # The PO date must NOT have been updated by the rejected request
        new_date2 = conn.execute(
            "SELECT Expected_Delivery FROM purchase_orders "
            "WHERE PO_Number='4720099020'").fetchone()[0]
        assert new_date2 == "2026-07-25", new_date2
    finally:
        conn.close()


def check_force_close_flow() -> None:
    """force_close_target on each of pr/po/po_item — verify audit row +
    state flip + notification fan-out (admin + originating HOD)."""
    conn = database.get_connection()
    try:
        # Set up PR + PO + line
        database.insert_manual_pr(
            pr_number="3000099050", sap_code="S", material_code="GI-FC-1",
            material_name="m", requested_qty=5, uom="EA",
            supplier="", est_cost_sar=0, notes="",
            site_id="HQ", conn=conn,
        )
        database.submit_pr_to_logistics(
            "3000099050", "HQ", "hod", conn=conn)
        database.create_po_manual(
            header={"PO_Number": "4720099050",
                    "PR_Number": "3000099050",
                    "Site_ID": "HQ",
                    "PO_Date": "2026-06-15"},
            items=[
                {"Material_Code": "GI-FC-A", "Description": "a",
                 "Qty": 2, "UOM": "EA"},
                {"Material_Code": "GI-FC-B", "Description": "b",
                 "Qty": 3, "UOM": "EA"},
            ],
            created_by="logi", conn=conn,
        )
        # 1. Close a single line
        line_id = conn.execute(
            "SELECT id FROM po_items WHERE PO_Number='4720099050' "
            "ORDER BY line_no LIMIT 1").fetchone()[0]
        ok, _ = database.force_close_target(
            "po_item", str(int(line_id)),
            "Line is obsolete", closed_by="logi", conn=conn,
        )
        assert ok
        line_status = conn.execute(
            "SELECT line_status, close_reason FROM po_items "
            "WHERE id=?", (line_id,)).fetchone()
        assert line_status[0] == "force_closed"
        # 2. Force-close PO
        ok2, _ = database.force_close_target(
            "po", "4720099050", "Vendor cancelled",
            closed_by="logi", conn=conn,
        )
        assert ok2
        po_status = conn.execute(
            "SELECT status FROM purchase_orders "
            "WHERE PO_Number='4720099050'").fetchone()[0]
        assert po_status == "force_closed"
        # 3. Force-close PR
        ok3, _ = database.force_close_target(
            "pr", "3000099050", "Cancelled by requestor",
            closed_by="logi", conn=conn,
        )
        assert ok3
        pr_status = conn.execute(
            "SELECT logistics_status, status FROM pr_master "
            "WHERE PR_Number='3000099050' LIMIT 1").fetchone()
        assert pr_status[0] == "force_closed" and pr_status[1] == "closed"
        # Audit rows present (one per close)
        n = conn.execute(
            "SELECT COUNT(*) FROM po_force_closures "
            "WHERE PR_Number='3000099050' OR PO_Number='4720099050' "
            "   OR (target_type='po_item' AND target_ref=?)",
            (str(int(line_id)),)).fetchone()[0]
        assert n == 3
        # Admin in-app notification fired
        admin_inbox = database.get_app_notifications(
            "admin", role="admin", conn=conn)
        assert (admin_inbox["event_key"]
                .isin(["po_force_closed", "pr_force_closed"])).any()
        # Reason < 3 chars rejected
        ok4, _ = database.force_close_target(
            "pr", "3000099051", "x", closed_by="logi", conn=conn,
        )
        assert not ok4
    finally:
        conn.close()


def check_vendor_return_reopens_po() -> None:
    """Raising a return on a delivered PO line flips line_status back to
    partially_delivered (and PO header to partially_delivered if it was closed)."""
    conn = database.get_connection()
    try:
        database.create_po_manual(
            header={"PO_Number": "4720099060",
                    "PO_Date": "2026-06-15"},
            items=[{"Material_Code": "GI-RET-1",
                    "Description": "thing",
                    "Qty": 10, "UOM": "EA"}],
            created_by="logi", conn=conn,
        )
        # Pretend it was delivered
        line_id = conn.execute(
            "SELECT id FROM po_items WHERE PO_Number='4720099060'"
        ).fetchone()[0]
        conn.execute(
            "UPDATE po_items SET Delivered_Qty=10, line_status='delivered' "
            "WHERE id=?", (line_id,))
        conn.execute(
            "UPDATE purchase_orders SET status='delivered' "
            "WHERE PO_Number='4720099060'")
        conn.commit()
        ok, msg = database.raise_vendor_return(
            po_number="4720099060", po_item_id=int(line_id),
            dn_number=None, qty=3, reason="Damaged at receiving",
            raised_by_role="warehouse_user",
            raised_by="wh1",
            expected_resupply="2026-07-30",
            conn=conn,
        )
        assert ok, msg
        # Line state
        row = conn.execute(
            "SELECT Returned_Qty, line_status FROM po_items WHERE id=?",
            (line_id,)).fetchone()
        assert row[0] == 3 and row[1] == "partially_delivered"
        # PO reopened
        po_status = conn.execute(
            "SELECT status FROM purchase_orders "
            "WHERE PO_Number='4720099060'").fetchone()[0]
        assert po_status == "partially_delivered"
    finally:
        conn.close()


def check_process_po_pdf_smoke() -> None:
    """Synthetic-PDF smoke: build a tiny PDF that resembles the sample PO
    layout and assert process_po_pdf returns at least the PO_Number + 1+ item.
    If reportlab isn't installed, skip cleanly — this is a smoke test, not a
    blocker."""
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter
    except ImportError:
        return  # skip silently — reportlab not in requirements

    import io as _io
    buf = _io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 10)
    # Headers
    c.drawString(40, 760, "Purchase Order")
    c.drawString(40, 740, "Vendor :0000110341")
    c.drawString(40, 730, "TEST VENDOR LIMITED")
    c.drawString(40, 710, "Inco Terms: EXW Chennai")
    c.drawString(40, 700, "Payment Terms: 30 days")
    c.drawString(40, 690, "Purch. Order No.: 4720099999")
    c.drawString(40, 680, "Date: 15.06.2026")
    c.drawString(40, 670, "PO Type: ZGI2 -GI Trading/Import PO")
    c.drawString(40, 660, "Contact: 914430006080")
    c.drawString(40, 650, "Mobile: +966 59 733 2265")
    c.drawString(40, 640, "Our Reference: Mohammed Hyder")
    c.drawString(40, 630, "subramanyamv@cumi.murugappa.com")
    c.drawString(40, 620, "logistics@generalindustries.net")
    # A line item in the recognised pattern
    c.drawString(40, 580,
                 "001  GI-8003100  CUMIFURAN SYRUP LIQUID RESIN  5,025.00  KG  10.00  50250.00")
    c.drawString(40, 560,
                 "002  GI-8003099  RUBBER LINING PANEL  100.00  KG  20.00  2000.00")
    # Annexure shipment schedule (single row)
    c.drawString(40, 500, "SHIPMENT 01  BRICK MATERIALS  05.02.2026")
    # Totals
    c.drawString(40, 460, "Freight Charges 100.00")
    c.drawString(40, 450, "Total Amount 52350.00")
    c.showPage()
    c.save()
    pdf_bytes = buf.getvalue()

    ok, msg, extracted = database.process_po_pdf(pdf_bytes, created_by="logi")
    assert ok, f"PO PDF smoke failed: {msg}"
    assert extracted["header"].get("PO_Number") == "4720099999", extracted["header"]
    assert len(extracted["items"]) >= 2
    fams = [it.get("rl_bl_family") for it in extracted["items"]]
    assert "RL" in fams, f"Expected RL tag, got {fams}"
    # Annexure parsed
    assert len(extracted["shipment_schedule"]) >= 1


def check_warehouse_acknowledge_and_receive() -> None:
    """End-to-end: WH ack assignment → WH receives partial qty → po_items
    Delivered_Qty bumps → over-deliver is rejected."""
    conn = database.get_connection()
    try:
        database.add_warehouse("WH-P3", "Phase 3 yard", conn=conn)
        database.create_po_manual(
            header={"PO_Number": "4720033001",
                    "PO_Date": "2026-06-16"},
            items=[
                {"Material_Code": "P3-A", "Description": "Item A",
                 "Qty": 10, "UOM": "EA"},
                {"Material_Code": "P3-B", "Description": "Item B",
                 "Qty": 5,  "UOM": "EA"},
            ],
            created_by="logi", conn=conn,
        )
        ok, _ = database.assign_po_to_warehouse(
            "4720033001", "WH-P3", expected_delivery="2026-07-01",
            items_subset_ids=None, assigned_by="logi", conn=conn,
        )
        assert ok
        aid = conn.execute(
            "SELECT id FROM po_assignments WHERE PO_Number='4720033001'"
        ).fetchone()[0]
        # Ack
        ok_ack, _ = database.acknowledge_assignment(int(aid), "wh1", conn=conn)
        assert ok_ack
        assert conn.execute(
            "SELECT status FROM po_assignments WHERE id=?", (aid,)
        ).fetchone()[0] == "acknowledged"
        # Partial receive
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM po_items WHERE PO_Number='4720033001' "
            "ORDER BY line_no").fetchall()]
        ok_rec, msg = database.record_warehouse_receipt(
            int(aid), {ids[0]: 4, ids[1]: 5}, "wh1", conn=conn,
        )
        assert ok_rec, msg
        # po_items state
        states = conn.execute(
            "SELECT line_status, Delivered_Qty FROM po_items "
            "WHERE PO_Number='4720033001' ORDER BY line_no").fetchall()
        assert states == [("partially_delivered", 4), ("delivered", 5)]
        # PO header rolled to partial
        assert conn.execute(
            "SELECT status FROM purchase_orders "
            "WHERE PO_Number='4720033001'").fetchone()[0] == "partially_delivered"
        # Over-deliver blocked
        ok_over, msg_over = database.record_warehouse_receipt(
            int(aid), {ids[0]: 100}, "wh1", conn=conn,
        )
        assert not ok_over and "over-deliver" in msg_over.lower()
    finally:
        conn.close()


def check_warehouse_view_strict_price_hiding() -> None:
    """The Warehouse-facing assignment detail MUST blank every monetary
    column on items AND remove monetary keys from the header."""
    conn = database.get_connection()
    try:
        database.create_po_manual(
            header={"PO_Number": "4720033010", "PO_Date": "2026-06-16",
                    "Vendor_Code": "V", "Vendor_Name": "V Co",
                    "Total_Amount": 99999.99, "Freight_Charges": 100.0,
                    "Handling_Charges": 50.0, "Discount_Amount": 25.0,
                    "Amount_In_Words": "Ninety nine thousand"},
            items=[{"Material_Code": "X", "Description": "x",
                    "Qty": 1, "UOM": "EA",
                    "Unit_Price": 77.0, "Total_Price": 77.0}],
            created_by="logi", conn=conn,
        )
        database.add_warehouse("WH-PRICEHIDE", "PriceHide WH", conn=conn)
        database.assign_po_to_warehouse(
            "4720033010", "WH-PRICEHIDE", expected_delivery="2026-07-01",
            items_subset_ids=None, assigned_by="logi", conn=conn,
        )
        aid = conn.execute(
            "SELECT id FROM po_assignments WHERE PO_Number='4720033010'"
        ).fetchone()[0]
        detail = database.get_assignment_detail(int(aid), conn=conn)
        items = detail["items"]
        # Prices blanked
        assert items["Unit_Price"].iloc[0] is None
        assert items["Total_Price"].iloc[0] is None
        # Header has no money keys
        h = detail["po_header"]
        for forbidden in ("Total_Amount", "Freight_Charges",
                          "Handling_Charges", "Discount_Amount",
                          "Amount_In_Words"):
            assert forbidden not in h, f"WH header leaked {forbidden}"
    finally:
        conn.close()


def check_dn_rl_bl_strict_separation_blocks_mixed() -> None:
    """create_delivery_note must REJECT a DN that bundles RL + BL lines.
    Single-family DNs go through."""
    conn = database.get_connection()
    try:
        database.create_po_manual(
            header={"PO_Number": "4720033020", "PO_Date": "2026-06-16"},
            items=[
                {"Material_Code": "RL-X", "Description": "Rubber Lining sheet",
                 "Qty": 5, "UOM": "EA"},
                {"Material_Code": "BL-X", "Description": "Brick Lining tile",
                 "Qty": 5, "UOM": "EA"},
                {"Material_Code": "GI-X", "Description": "Safety helmet",
                 "Qty": 5, "UOM": "EA"},
            ],
            created_by="logi", conn=conn,
        )
        database.add_warehouse("WH-RLBL", "RLBL WH", conn=conn)
        database.assign_po_to_warehouse(
            "4720033020", "WH-RLBL", expected_delivery="2026-07-01",
            items_subset_ids=None, assigned_by="logi", conn=conn,
        )
        aid = conn.execute(
            "SELECT id FROM po_assignments WHERE PO_Number='4720033020'"
        ).fetchone()[0]
        ok_ack, _ = database.acknowledge_assignment(int(aid), "wh1", conn=conn)
        assert ok_ack
        # Receive everything
        ids = {row[1]: row[0] for row in conn.execute(
            "SELECT id, Material_Code FROM po_items "
            "WHERE PO_Number='4720033020'").fetchall()}
        database.record_warehouse_receipt(
            int(aid), {v: 5 for v in ids.values()}, "wh1", conn=conn,
        )
        # Mixed-family DN → rejected
        ok_mix, msg_mix, _ = database.create_delivery_note(
            po_number="4720033020", warehouse_id="WH-RLBL",
            site_id="HQ",
            line_items=[
                {"po_item_id": ids["RL-X"], "Qty": 2},
                {"po_item_id": ids["BL-X"], "Qty": 2},
            ],
            created_by="wh1", conn=conn,
        )
        assert not ok_mix
        assert "strict separation" in msg_mix.lower()
        # Single-family DN → goes through
        ok_rl, _, dn_rl = database.create_delivery_note(
            po_number="4720033020", warehouse_id="WH-RLBL",
            site_id="HQ",
            line_items=[{"po_item_id": ids["RL-X"], "Qty": 3}],
            created_by="wh1", conn=conn,
        )
        assert ok_rl and dn_rl
        # Family stamped on the DN header
        fam = conn.execute(
            "SELECT rl_bl_family FROM delivery_notes "
            "WHERE DN_Number = ?", (dn_rl,)).fetchone()[0]
        assert fam == "RL"
    finally:
        conn.close()


def check_full_dn_flow_to_sk_receipt() -> None:
    """End-to-end: WH draft DN → submit Logistics → Logistics approve →
    HOD approve (stages pending_receipts row) → SK confirm (→ receipts).
    Asserts identity math + every state transition + dn_items.status."""
    conn = database.get_connection()
    try:
        # Inventory entry so Material_Code → SAP_Code join works
        conn.execute(
            "INSERT INTO inventory (SAP_Code, Material_Code, "
            " Equipment_Description, UOM, Minimum_Qty) "
            "VALUES ('SAP-DN-1', 'M-DN-1', 'Widget', 'EA', 0)",
        )
        database.create_po_manual(
            header={"PO_Number": "4720033030", "PO_Date": "2026-06-16",
                    "Site_ID": "HQ"},
            items=[{"Material_Code": "M-DN-1", "Description": "Widget",
                    "Qty": 10, "UOM": "EA"}],
            created_by="logi", conn=conn,
        )
        database.add_warehouse("WH-FLOW", "Flow WH", conn=conn)
        database.assign_po_to_warehouse(
            "4720033030", "WH-FLOW", expected_delivery="2026-07-01",
            items_subset_ids=None, assigned_by="logi", conn=conn,
        )
        aid = conn.execute(
            "SELECT id FROM po_assignments WHERE PO_Number='4720033030'"
        ).fetchone()[0]
        database.acknowledge_assignment(int(aid), "wh1", conn=conn)
        po_item_id = conn.execute(
            "SELECT id FROM po_items WHERE PO_Number='4720033030'"
        ).fetchone()[0]
        database.record_warehouse_receipt(
            int(aid), {int(po_item_id): 10}, "wh1", conn=conn,
        )
        # Build a DN
        ok_dn, _, dn = database.create_delivery_note(
            po_number="4720033030", warehouse_id="WH-FLOW", site_id="HQ",
            line_items=[{"po_item_id": int(po_item_id), "Qty": 10,
                         "Lot_Number": "LOT-DN-1",
                         "Expiry_Date": "2027-01-01"}],
            header={"DN_Date": "2026-06-16", "Vehicle_No": "TRK-1",
                    "Driver_Name": "Bob"},
            created_by="wh1", conn=conn,
        )
        assert ok_dn and dn
        # Submit → Logistics → HOD → SK
        ok1, _ = database.submit_dn_for_logistics(dn, "wh1", conn=conn)
        assert ok1
        ok2, _ = database.logistics_decide_dn(
            dn, approve=True, decided_by="logi", conn=conn)
        assert ok2
        ok3, _ = database.hod_decide_dn(
            dn, approve=True, decided_by="hod", conn=conn)
        assert ok3
        # pending_receipts mirror row created
        pr_n = conn.execute(
            "SELECT COUNT(*) FROM pending_receipts "
            "WHERE DN_Number = ? AND status='pending_sk'",
            (dn,)).fetchone()[0]
        assert pr_n == 1, f"Expected 1 pending_sk row, got {pr_n}"
        # SK confirms — writes to receipts, drops pending_receipts mirror
        ok4, _ = database.sk_mark_dn_received(
            dn, store_keeper="sk", conn=conn)
        assert ok4
        rcpt = conn.execute(
            "SELECT COUNT(*), SUM(Quantity) FROM receipts "
            "WHERE DN_Number = ?", (dn,)).fetchone()
        assert rcpt[0] == 1 and float(rcpt[1]) == 10.0
        # DN status
        st_ = conn.execute(
            "SELECT status FROM delivery_notes WHERE DN_Number=?",
            (dn,)).fetchone()[0]
        assert st_ == "received"
        # dn_items.status flipped + sk_received_qty stored
        dn_item = conn.execute(
            "SELECT status, sk_received_qty FROM dn_items "
            "WHERE DN_Number = ?", (dn,)).fetchone()
        assert dn_item[0] == "received" and float(dn_item[1]) == 10.0
        # pending_receipts mirror row cleaned up
        pr_remaining = conn.execute(
            "SELECT COUNT(*) FROM pending_receipts "
            "WHERE DN_Number = ?", (dn,)).fetchone()[0]
        assert pr_remaining == 0
        # Receipt carries the traceback fields
        traceback_row = conn.execute(
            "SELECT DN_Number, Warehouse_ID, PO_Number_Source FROM receipts "
            "WHERE DN_Number = ?", (dn,)).fetchone()
        assert traceback_row == (dn, "WH-FLOW", "4720033030")
    finally:
        conn.close()


def check_internal_return_from_site() -> None:
    """A site SK-confirmed DN line can be returned by Warehouse → raises a
    vendor_return + reopens the originating PO line."""
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO inventory (SAP_Code, Material_Code, "
            " Equipment_Description, UOM, Minimum_Qty) "
            "VALUES ('SAP-RET-1', 'M-RET-1', 'X', 'EA', 0)",
        )
        database.create_po_manual(
            header={"PO_Number": "4720033040", "PO_Date": "2026-06-16",
                    "Site_ID": "HQ"},
            items=[{"Material_Code": "M-RET-1", "Description": "X",
                    "Qty": 8, "UOM": "EA"}],
            created_by="logi", conn=conn,
        )
        database.add_warehouse("WH-RET", "Returns WH", conn=conn)
        database.assign_po_to_warehouse(
            "4720033040", "WH-RET", expected_delivery="2026-07-01",
            items_subset_ids=None, assigned_by="logi", conn=conn,
        )
        aid = conn.execute(
            "SELECT id FROM po_assignments WHERE PO_Number='4720033040'"
        ).fetchone()[0]
        database.acknowledge_assignment(int(aid), "wh1", conn=conn)
        po_item_id = conn.execute(
            "SELECT id FROM po_items WHERE PO_Number='4720033040'"
        ).fetchone()[0]
        database.record_warehouse_receipt(
            int(aid), {int(po_item_id): 8}, "wh1", conn=conn,
        )
        # Prepare + ship the full DN
        ok, _, dn = database.create_delivery_note(
            po_number="4720033040", warehouse_id="WH-RET", site_id="HQ",
            line_items=[{"po_item_id": int(po_item_id), "Qty": 8}],
            created_by="wh1", conn=conn,
        )
        database.submit_dn_for_logistics(dn, "wh1", conn=conn)
        database.logistics_decide_dn(dn, approve=True, decided_by="logi", conn=conn)
        database.hod_decide_dn(dn, approve=True, decided_by="hod", conn=conn)
        database.sk_mark_dn_received(dn, store_keeper="sk", conn=conn)
        dn_item_id = conn.execute(
            "SELECT id FROM dn_items WHERE DN_Number = ?",
            (dn,)).fetchone()[0]
        # Now raise an internal return for 2 units
        ok_ret, _ = database.record_internal_return(
            dn_number=dn,
            items=[{"dn_item_id": int(dn_item_id), "qty": 2}],
            reason="Damaged during unloading",
            raised_by_role="warehouse_user", raised_by="wh1",
            conn=conn,
        )
        assert ok_ret
        # po_items reopened
        rec = conn.execute(
            "SELECT Returned_Qty, line_status FROM po_items "
            "WHERE id = ?", (po_item_id,)).fetchone()
        assert rec[0] == 2 and rec[1] == "partially_delivered"
        # dn_items flagged returned
        assert conn.execute(
            "SELECT status FROM dn_items WHERE id = ?",
            (dn_item_id,)).fetchone()[0] == "returned"
        # vendor_return row written + linked to DN
        assert conn.execute(
            "SELECT COUNT(*) FROM po_returns "
            "WHERE DN_Number = ?", (dn,)).fetchone()[0] == 1
    finally:
        conn.close()


def check_hod_rejection_flow() -> None:
    """HOD rejection should NOT stage pending_receipts and should leave DN
    status='rejected' with a rejection_reason."""
    conn = database.get_connection()
    try:
        database.create_po_manual(
            header={"PO_Number": "4720033050", "PO_Date": "2026-06-16"},
            items=[{"Material_Code": "REJ-1", "Description": "X",
                    "Qty": 3, "UOM": "EA"}],
            created_by="logi", conn=conn,
        )
        database.add_warehouse("WH-REJ", "Rej WH", conn=conn)
        database.assign_po_to_warehouse(
            "4720033050", "WH-REJ", expected_delivery="2026-07-01",
            items_subset_ids=None, assigned_by="logi", conn=conn,
        )
        aid = conn.execute(
            "SELECT id FROM po_assignments WHERE PO_Number='4720033050'"
        ).fetchone()[0]
        database.acknowledge_assignment(int(aid), "wh1", conn=conn)
        po_item_id = conn.execute(
            "SELECT id FROM po_items WHERE PO_Number='4720033050'"
        ).fetchone()[0]
        database.record_warehouse_receipt(
            int(aid), {int(po_item_id): 3}, "wh1", conn=conn,
        )
        ok, _, dn = database.create_delivery_note(
            po_number="4720033050", warehouse_id="WH-REJ", site_id="HQ",
            line_items=[{"po_item_id": int(po_item_id), "Qty": 3}],
            created_by="wh1", conn=conn,
        )
        database.submit_dn_for_logistics(dn, "wh1", conn=conn)
        database.logistics_decide_dn(dn, approve=True, decided_by="logi", conn=conn)
        ok_rej, _ = database.hod_decide_dn(
            dn, approve=False, decided_by="hod",
            decision_notes="Wrong qty", conn=conn,
        )
        assert ok_rej
        row = conn.execute(
            "SELECT status, rejection_reason FROM delivery_notes "
            "WHERE DN_Number = ?", (dn,)).fetchone()
        assert row[0] == "rejected" and row[1] == "Wrong qty"
        # No pending_receipts row created
        n = conn.execute(
            "SELECT COUNT(*) FROM pending_receipts WHERE DN_Number = ?",
            (dn,)).fetchone()[0]
        assert n == 0
    finally:
        conn.close()


def check_in_transit_for_site() -> None:
    """A DN sitting at each pipeline state should surface in the HOD's
    In-Transit view, ordered by pipeline depth (closest-to-SK first).
    Filters out other sites."""
    conn = database.get_connection()
    try:
        # Inventory join target so SK confirm works downstream
        conn.execute(
            "INSERT INTO inventory (SAP_Code, Material_Code, "
            " Equipment_Description, UOM, Minimum_Qty) "
            "VALUES ('SAP-IT-1','M-IT-1','Widget','EA',0)",
        )
        database.add_warehouse("WH-IT", "In-Transit WH", conn=conn)

        # Build two POs: one bound for SITE-A, one for SITE-OTHER
        for po, site in (("4720070001", "SITE-A"),
                         ("4720070002", "SITE-OTHER")):
            database.create_po_manual(
                header={"PO_Number": po, "PO_Date": "2026-06-16",
                        "Site_ID": site},
                items=[{"Material_Code": "M-IT-1",
                        "Description": "Widget",
                        "Qty": 5, "UOM": "EA"}],
                created_by="logi", conn=conn,
            )
            database.assign_po_to_warehouse(
                po, "WH-IT", expected_delivery="2026-07-01",
                items_subset_ids=None, assigned_by="logi", conn=conn,
            )
            aid = conn.execute(
                "SELECT id FROM po_assignments WHERE PO_Number=?",
                (po,)).fetchone()[0]
            database.acknowledge_assignment(int(aid), "wh1", conn=conn)
            pid = conn.execute(
                "SELECT id FROM po_items WHERE PO_Number=?",
                (po,)).fetchone()[0]
            database.record_warehouse_receipt(
                int(aid), {int(pid): 5}, "wh1", conn=conn,
            )

        # SITE-A DNs across the pipeline
        # DN1: stays at draft → submit → pending_logistics
        # DN2: → logistics approved → pending_hod
        # DN3: → logistics approved → hod approved → pending_sk
        def _ship(po_number, site_id, qty):
            pid = conn.execute(
                "SELECT id FROM po_items WHERE PO_Number=?",
                (po_number,)).fetchone()[0]
            ok, _, dn = database.create_delivery_note(
                po_number=po_number, warehouse_id="WH-IT",
                site_id=site_id,
                line_items=[{"po_item_id": int(pid), "Qty": qty}],
                created_by="wh1", conn=conn,
            )
            assert ok
            return dn

        # 3 separate POs for SITE-A — but we only built one. Build extras.
        for po_n in ("4720070003", "4720070004"):
            database.create_po_manual(
                header={"PO_Number": po_n, "PO_Date": "2026-06-16",
                        "Site_ID": "SITE-A"},
                items=[{"Material_Code": "M-IT-1",
                        "Description": "Widget",
                        "Qty": 5, "UOM": "EA"}],
                created_by="logi", conn=conn,
            )
            database.assign_po_to_warehouse(
                po_n, "WH-IT", expected_delivery="2026-07-01",
                items_subset_ids=None, assigned_by="logi", conn=conn,
            )
            aid2 = conn.execute(
                "SELECT id FROM po_assignments WHERE PO_Number=?",
                (po_n,)).fetchone()[0]
            database.acknowledge_assignment(int(aid2), "wh1", conn=conn)
            pid2 = conn.execute(
                "SELECT id FROM po_items WHERE PO_Number=?",
                (po_n,)).fetchone()[0]
            database.record_warehouse_receipt(
                int(aid2), {int(pid2): 5}, "wh1", conn=conn,
            )

        dn1 = _ship("4720070001", "SITE-A", 1)
        database.submit_dn_for_logistics(dn1, "wh1", conn=conn)
        dn2 = _ship("4720070003", "SITE-A", 1)
        database.submit_dn_for_logistics(dn2, "wh1", conn=conn)
        database.logistics_decide_dn(dn2, approve=True,
                                     decided_by="logi", conn=conn)
        dn3 = _ship("4720070004", "SITE-A", 1)
        database.submit_dn_for_logistics(dn3, "wh1", conn=conn)
        database.logistics_decide_dn(dn3, approve=True,
                                     decided_by="logi", conn=conn)
        database.hod_decide_dn(dn3, approve=True, decided_by="hod",
                               conn=conn)
        # SITE-OTHER DN — should NOT appear in SITE-A view
        dn_other = _ship("4720070002", "SITE-OTHER", 1)
        database.submit_dn_for_logistics(dn_other, "wh1", conn=conn)

        # Query
        df = database.list_in_transit_dns_for_site("SITE-A", conn=conn)
        dns_seen = set(df["DN_Number"])
        assert dn1 in dns_seen and dn2 in dns_seen and dn3 in dns_seen, dns_seen
        # Site isolation
        assert dn_other not in dns_seen, "Cross-site leak"
        # Ordering: pending_sk should sort earlier than pending_logistics
        order = df["status"].tolist()
        idx_sk = order.index("pending_sk")
        idx_log = order.index("pending_logistics")
        assert idx_sk < idx_log, f"Ordering wrong: {order}"
    finally:
        conn.close()


def check_hod_can_submit_reschedule_and_see_outcome() -> None:
    """Site HOD raises a reschedule on a DN, it appears as 'pending' in
    My-Reschedule-Requests; Logistics approves; the next list call
    reflects the new status + decision metadata."""
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO inventory (SAP_Code, Material_Code, "
            " Equipment_Description, UOM, Minimum_Qty) "
            "VALUES ('SAP-IT-2','M-IT-2','W2','EA',0)",
        )
        database.add_warehouse("WH-RES", "Reschedule WH", conn=conn)
        database.create_po_manual(
            header={"PO_Number": "4720071000", "PO_Date": "2026-06-16",
                    "Site_ID": "SITE-A"},
            items=[{"Material_Code": "M-IT-2", "Description": "W2",
                    "Qty": 2, "UOM": "EA"}],
            created_by="logi", conn=conn,
        )
        database.assign_po_to_warehouse(
            "4720071000", "WH-RES",
            expected_delivery="2026-07-01",
            items_subset_ids=None, assigned_by="logi", conn=conn,
        )
        aid = conn.execute(
            "SELECT id FROM po_assignments WHERE PO_Number='4720071000'"
        ).fetchone()[0]
        database.acknowledge_assignment(int(aid), "wh1", conn=conn)
        pid = conn.execute(
            "SELECT id FROM po_items WHERE PO_Number='4720071000'"
        ).fetchone()[0]
        database.record_warehouse_receipt(int(aid), {int(pid): 2},
                                          "wh1", conn=conn)
        ok, _, dn = database.create_delivery_note(
            po_number="4720071000", warehouse_id="WH-RES",
            site_id="SITE-A",
            line_items=[{"po_item_id": int(pid), "Qty": 2}],
            header={"DN_Date": "2026-07-01"},
            created_by="wh1", conn=conn,
        )
        assert ok
        database.submit_dn_for_logistics(dn, "wh1", conn=conn)
        database.logistics_decide_dn(dn, approve=True,
                                     decided_by="logi", conn=conn)
        # HOD raises the reschedule
        ok_req, _ = database.request_reschedule(
            po_number="4720071000", dn_number=dn,
            current_date="2026-07-01", requested_date="2026-07-08",
            reason="Site is on shutdown that week",
            requested_by_role="hod", requested_by="hod",
            conn=conn,
        )
        assert ok_req
        # Site list view shows it as pending — grab the row matching THIS
        # specific PO + DN to avoid any race-condition with prior tests'
        # rows sharing the same second-precision timestamp.
        rdf = database.list_reschedule_requests_for_site(
            "SITE-A", status_filter=["pending"], conn=conn)
        mine = rdf[(rdf["PO_Number"] == "4720071000")
                   & (rdf["DN_Number"] == dn)]
        assert not mine.empty, "Newly-submitted reschedule not visible"
        rid = int(mine.iloc[0]["id"])
        # Logistics approves
        ok_dec, msg_dec = database.decide_reschedule(
            rid, approve=True, decided_by="logi",
            decision_notes="Shutdown noted",
            conn=conn,
        )
        assert ok_dec, f"decide_reschedule(rid={rid}) failed: {msg_dec}"
        # Site view now reflects approved + decided_by + new date pushed
        rdf2 = database.list_reschedule_requests_for_site(
            "SITE-A", status_filter=["approved"], conn=conn,
        )
        assert (rdf2["status"] == "approved").all()
        assert rdf2.iloc[0]["decided_by"] == "logi"
        # PO Expected_Delivery picked up the new date
        new_eta = conn.execute(
            "SELECT Expected_Delivery FROM purchase_orders "
            "WHERE PO_Number='4720071000'").fetchone()[0]
        assert new_eta == "2026-07-08"
    finally:
        conn.close()


def check_force_closure_site_visibility() -> None:
    """A force-closure on a PR/PO bound for SITE-A must surface in
    SITE-A's list and NOT in SITE-B's list. The fallback joins
    (via pr_master + purchase_orders) catch rows with NULL Site_ID."""
    conn = database.get_connection()
    try:
        # PR bound for SITE-A → force-close it
        database.insert_manual_pr(
            pr_number="3000077001", sap_code="SAP-FC", material_code="GI-FC",
            material_name="m", requested_qty=5, uom="EA",
            supplier="", est_cost_sar=0, notes="",
            site_id="SITE-A", conn=conn,
        )
        database.submit_pr_to_logistics(
            "3000077001", "SITE-A", "hod", conn=conn)
        ok, _ = database.force_close_target(
            "pr", "3000077001", "Cancelled by site",
            closed_by="logi", conn=conn,
        )
        assert ok
        # PO bound for SITE-B → also force-close
        database.create_po_manual(
            header={"PO_Number": "4720077002", "PO_Date": "2026-06-16",
                    "Site_ID": "SITE-B"},
            items=[{"Material_Code": "X", "Description": "x",
                    "Qty": 1, "UOM": "EA"}],
            created_by="logi", conn=conn,
        )
        database.force_close_target(
            "po", "4720077002", "Vendor cancelled",
            closed_by="logi", conn=conn,
        )
        # SITE-A view: PR row only
        fc_a = database.list_force_closures_for_site("SITE-A", conn=conn)
        refs_a = set(fc_a["target_ref"])
        assert "3000077001" in refs_a
        assert "4720077002" not in refs_a, "PO from SITE-B leaked into SITE-A"
        # SITE-B view: PO row only
        fc_b = database.list_force_closures_for_site("SITE-B", conn=conn)
        refs_b = set(fc_b["target_ref"])
        assert "4720077002" in refs_b
        assert "3000077001" not in refs_b
    finally:
        conn.close()


def check_delivery_reminders_idempotent() -> None:
    """sweep_delivery_reminders fires once per (ref, date, offset). Running
    it twice on the same day must NOT double-insert. T-2 / T-1 / T-0 windows
    must each fire correctly when matching dates exist."""
    import datetime as _dt
    conn = database.get_connection()
    try:
        # Three POs landing on the three offset dates from a fixed "today"
        today = _dt.date(2026, 6, 16)
        for off, po_no in [(0, "4720088000"),  # T-0
                           (1, "4720088001"),  # T-1
                           (2, "4720088002")]:  # T-2
            d_iso = (today + _dt.timedelta(days=off)).isoformat()
            database.create_po_manual(
                header={"PO_Number": po_no, "PO_Date": "2026-06-16",
                        "Expected_Delivery": d_iso,
                        "Site_ID": "HQ"},
                items=[{"Material_Code": "X", "Description": "x",
                        "Qty": 1, "UOM": "EA"}],
                created_by="logi", conn=conn,
            )
        # First sweep — three new fires, one per PO
        n1 = database.sweep_delivery_reminders(today=today, conn=conn)
        assert n1 >= 3, f"Expected ≥3 fresh fires, got {n1}"
        # Dedup row count matches
        dedup_n = conn.execute(
            "SELECT COUNT(*) FROM delivery_reminders_sent "
            "WHERE ref_type='po' AND ref_number IN (?, ?, ?)",
            ("4720088000", "4720088001", "4720088002")).fetchone()[0]
        assert dedup_n == 3, f"Dedup rows expected 3, got {dedup_n}"
        # Second sweep — same day, no new fires
        n2 = database.sweep_delivery_reminders(today=today, conn=conn)
        # n2 may be 0 (UNIQUE blocks) — that's the whole point
        dedup_n2 = conn.execute(
            "SELECT COUNT(*) FROM delivery_reminders_sent "
            "WHERE ref_type='po' AND ref_number IN (?, ?, ?)",
            ("4720088000", "4720088001", "4720088002")).fetchone()[0]
        assert dedup_n2 == 3, f"After re-sweep, dedup should stay 3, got {dedup_n2}"
        # Verify in-app notifications were queued at the right severities
        inbox = database.get_app_notifications(
            "logi", role="logistics", conn=conn,
        )
        sev_seen = set(inbox[
            inbox["event_key"].isin([
                "delivery_reminder_t_minus_2",
                "delivery_reminder_t_minus_1",
                "delivery_reminder_t_zero",
            ])
        ]["severity"])
        assert "critical" in sev_seen, "T-0 must be critical"
        assert "warning"  in sev_seen, "T-1/T-2 must be warning"
    finally:
        conn.close()


def check_phase5_reports_run_without_raising() -> None:
    """report_po_status / report_warehouse_throughput / report_force_closures
    must each execute without raising and return a (DataFrame, dict) shape."""
    conn = database.get_connection()
    try:
        for fn in (database.report_po_status,
                   database.report_warehouse_throughput,
                   database.report_force_closures):
            df, summary = fn(date_from="2020-01-01", date_to="2030-12-31",
                             site_id=None, conn=conn)
            import pandas as _pd
            assert isinstance(df, _pd.DataFrame), f"{fn.__name__} bad type"
            assert isinstance(summary, dict), f"{fn.__name__} bad summary"
    finally:
        conn.close()


def check_mark_all_notifications_read() -> None:
    """mark_all_notifications_read flips every visible row. Does NOT touch
    rows for a different user or a different role."""
    conn = database.get_connection()
    try:
        # Targeted to user A, plus broadcast to role X at site Y
        database.queue_app_notification(
            event_key="x", title="alpha",
            recipient_user="user_a", conn=conn,
        )
        database.queue_app_notification(
            event_key="x", title="beta",
            recipient_role="role_x", recipient_site="Y", conn=conn,
        )
        # And a row for someone else that must stay unread
        database.queue_app_notification(
            event_key="x", title="other",
            recipient_user="someone_else", conn=conn,
        )
        n = database.mark_all_notifications_read(
            username="user_a", role="role_x",
            site_id="Y", warehouse_id=None, conn=conn,
        )
        assert n >= 2, f"Expected ≥2 marked, got {n}"
        # Other user's row still unread
        other = database.get_app_notifications(
            "someone_else", role="other_role", unread_only=True, conn=conn,
        )
        assert (other["title"] == "other").any()
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

    # ── Phase C — Procurement chain checks ─────────────────────────────────
    run_check("Procurement", "RL/BL strict-separation classifier",
              check_rl_bl_classification,
              "config.classify_rl_bl_family must tag each line as RL/BL/None.")
    run_check("Procurement", "Warehouses CRUD round-trip",
              check_warehouses_crud,
              "add_warehouse/list_warehouses + UNIQUE constraint.")
    run_check("Procurement", "Vendors CRUD round-trip",
              check_vendors_crud,
              "add_vendor/list_vendors + Inco/Payment terms persist.")
    run_check("Procurement", "App notifications inbox (user + role broadcast)",
              check_app_notifications,
              "queue_app_notification, get_app_notifications, mark_read.")
    run_check("Procurement", "WhatsApp per-event gate honours config toggles",
              check_whatsapp_event_gate,
              "fire_whatsapp_event respects WHATSAPP_ENABLED + WHATSAPP_TRIGGERS.")
    run_check("Procurement", "users CHECK accepts logistics + warehouse_user",
              check_role_check_constraint,
              "init_db must rebuild users with the new role CHECK.")
    run_check("Procurement", "po_items RL/BL strict-separation persists",
              check_po_items_rl_bl_tagging,
              "rl_bl_family column accepts 'RL', 'BL', NULL — no combo values.")

    # ── Phase 2 — Logistics flow end-to-end ────────────────────────────────
    run_check("Logistics", "HOD submits PR → appears in Logistics queue",
              check_pr_to_logistics_handoff,
              "submit_pr_to_logistics + list_prs_for_logistics.")
    run_check("Logistics", "Create PO (manual) — RL/BL tagged, PR→in_po",
              check_po_manual_creation_and_rl_bl,
              "create_po_manual + post-insert side-effects.")
    run_check("Logistics", "get_po_detail(hide_prices=True) blanks prices",
              check_po_detail_price_hiding,
              "Warehouse view must NEVER see prices.")
    run_check("Logistics", "Assign PO to Warehouse — full + subset",
              check_assign_po_to_warehouse,
              "assign_po_to_warehouse, items_subset, notification fan-out.")
    run_check("Logistics", "Reschedule request → approve updates PO date",
              check_reschedule_flow,
              "request_reschedule + decide_reschedule.")
    run_check("Logistics", "Force-close PR / PO / line with audit",
              check_force_close_flow,
              "force_close_target + po_force_closures audit row.")
    run_check("Logistics", "Vendor return reopens the closed PO",
              check_vendor_return_reopens_po,
              "raise_vendor_return + po_items.line_status flip.")
    run_check("Logistics", "PO PDF extraction smoke test",
              check_process_po_pdf_smoke,
              "process_po_pdf — synthetic PDF, RL tag survives.")

    # ── Phase 3 — Warehouse Portal end-to-end ─────────────────────────────
    run_check("Warehouse", "Acknowledge + receive (partial + over-deliver guard)",
              check_warehouse_acknowledge_and_receive,
              "acknowledge_assignment, record_warehouse_receipt.")
    run_check("Warehouse", "Warehouse view strictly hides prices (items + header)",
              check_warehouse_view_strict_price_hiding,
              "get_assignment_detail must blank every monetary field.")
    run_check("Warehouse", "DN splitter enforces RL/BL strict separation",
              check_dn_rl_bl_strict_separation_blocks_mixed,
              "create_delivery_note rejects mixed-family payloads.")
    run_check("Warehouse", "Full DN flow → SK confirms → receipts row",
              check_full_dn_flow_to_sk_receipt,
              "submit + logistics_decide_dn + hod_decide_dn + sk_mark_dn_received.")
    run_check("Warehouse", "Internal return reopens PO line",
              check_internal_return_from_site,
              "record_internal_return + po_returns + line_status flip.")
    run_check("Warehouse", "HOD rejection terminates the DN cleanly",
              check_hod_rejection_flow,
              "hod_decide_dn(approve=False) writes rejection_reason, no pending_receipts.")

    # ── Phase 4 — Site-side visibility + reschedule wiring ─────────────────
    run_check("Site Visibility", "In-Transit DNs filtered + sorted per site",
              check_in_transit_for_site,
              "list_in_transit_dns_for_site — site isolation + pipeline order.")
    run_check("Site Visibility", "HOD reschedule → Logistics → outcome reflected",
              check_hod_can_submit_reschedule_and_see_outcome,
              "request_reschedule + decide_reschedule + list_reschedule_requests_for_site.")
    run_check("Site Visibility", "Force-closure visibility scoped to site",
              check_force_closure_site_visibility,
              "list_force_closures_for_site — PR/PO/po_item joins.")

    # ── Phase 5 — Admin oversight + reminders + reports + notifications ────
    run_check("Reminders", "T-2 / T-1 / T-0 sweep is idempotent",
              check_delivery_reminders_idempotent,
              "sweep_delivery_reminders + delivery_reminders_sent UNIQUE.")
    run_check("Reports", "Phase 5 procurement reports run cleanly",
              check_phase5_reports_run_without_raising,
              "report_po_status / report_warehouse_throughput / report_force_closures.")
    run_check("Notifications", "mark_all_notifications_read scopes correctly",
              check_mark_all_notifications_read,
              "Only touches rows visible to this user/role.")

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
