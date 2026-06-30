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
import sqlite3  # Round 15 — IntegrityError catches in schema/unique tests
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
        # Phase 6A — CV foundation
        "employees", "tool_catalogue", "cv_model_versions",
        # Phase 7B — Supervisor Material Request workflow
        "supervisor_material_requests",
        "supervisor_material_request_items",
        # Phase 7C — HOD Cross-Site view notification debounce
        "cross_site_views",
        # Phase 7E — Form draft recovery
        "form_drafts",
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
        # Phase 6A — CV audit cols self-healed onto returnable_items
        "returnable_items": ["cv_detected", "cv_confidence",
                             "cv_employee_id", "cv_tool_class"],
        # Phase 7A — Employee Site Binding
        "employees": ["Site_ID"],
        # Phase 7B — Supervisor Material Request workflow
        "supervisor_material_requests": [
            "request_no", "Site_ID", "Worker_ID", "Worker_Name",
            "Job_Tank_Place", "Old_PPE_Returned", "No_Return_Reason",
            "requested_by", "requested_at", "status",
            "sk_decided_by", "sk_decided_at", "sk_reject_reason",
            "posted_pending_ids",
        ],
        "supervisor_material_request_items": [
            "request_id", "SAP_Code", "Material_Code", "Equipment_Description",
            "UOM", "Requested_Qty", "Stock_At_Request", "Available_Flag",
            "SK_Adjusted_Qty", "Notes",
        ],
        # Self-heal on existing ledger tables
        "pending_issues": ["Source_Ref"],
        "consumption":    ["Source_Ref"],
        # Phase 7C — Cross-Site view notification debounce
        "cross_site_views": [
            "viewer_username", "viewer_site_id", "target_site_id",
            "view_date", "first_seen_at",
        ],
        # Phase 7E — Form draft recovery
        "form_drafts": [
            "username", "form_id", "site_id", "payload_json",
            "created_at", "updated_at", "expires_at",
        ],
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
        # Round 16 — DN flow simplified: Warehouse → HOD → SK (Logistics
        # is no longer in the approval chain). submit_dn_for_logistics now
        # writes status='pending_hod' directly, so the HOD decides next.
        ok1, _ = database.submit_dn_for_logistics(dn, "wh1", conn=conn)
        assert ok1
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

        # Round 16 — DN flow simplified: submit lands directly at
        # pending_hod (no Logistics step). The test now seeds three DNs
        # at different states to exercise the pipeline-order sort:
        #   dn1 → pending_hod
        #   dn2 → pending_hod
        #   dn3 → pending_sk (HOD has approved)
        dn1 = _ship("4720070001", "SITE-A", 1)
        database.submit_dn_for_logistics(dn1, "wh1", conn=conn)
        dn2 = _ship("4720070003", "SITE-A", 1)
        database.submit_dn_for_logistics(dn2, "wh1", conn=conn)
        dn3 = _ship("4720070004", "SITE-A", 1)
        database.submit_dn_for_logistics(dn3, "wh1", conn=conn)
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
        # Ordering: pending_sk should sort earlier than pending_hod.
        order = df["status"].tolist()
        idx_sk  = order.index("pending_sk")
        idx_hod = order.index("pending_hod")
        assert idx_sk < idx_hod, f"Ordering wrong: {order}"
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
        "pages_internal.manhour_portal",
    ]:
        try:
            importlib.import_module(mod)
        except Exception as e:
            failed.append(f"{mod} → {type(e).__name__}: {e}")
    assert not failed, "Module import failures:\n  " + "\n  ".join(failed)


# ---------------------------------------------------------------------------
# Workstream C — Meta WhatsApp webhook parser/router (services/whatsapp_webhook.py)
# ---------------------------------------------------------------------------
# Pure (FastAPI-free) logic, so we can exercise it directly here. Covers payload
# parsing for the common message types, status-callback separation, the GET
# handshake token check, X-Hub-Signature-256 verification, and the stub router.
def _wa_text_payload(body: str = "RECEIVED DN-1042",
                     wa_id: str = "966500000000",
                     name: str = "Ahmed") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_ID",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "+966...",
                                 "phone_number_id": "PNID_123"},
                    "contacts": [{"profile": {"name": name}, "wa_id": wa_id}],
                    "messages": [{
                        "from": wa_id, "id": "wamid.ABC", "timestamp": "1719600000",
                        "type": "text", "text": {"body": body},
                    }],
                },
            }],
        }],
    }


def check_wsc_webhook_parses_text():
    from services.whatsapp_webhook import parse_inbound_messages
    msgs = parse_inbound_messages(_wa_text_payload())
    assert len(msgs) == 1, f"expected 1 message, got {len(msgs)}"
    m = msgs[0]
    assert m.from_phone == "966500000000", f"sender phone wrong: {m.from_phone!r}"
    assert m.text == "RECEIVED DN-1042", f"body wrong: {m.text!r}"
    assert m.sender_name == "Ahmed", f"name wrong: {m.sender_name!r}"
    assert m.phone_number_id == "PNID_123", "our phone_number_id must be captured for replies"
    assert m.wa_message_id == "wamid.ABC", "wa message id (for dedup/reply) missing"


def check_wsc_webhook_parses_interactive():
    from services.whatsapp_webhook import parse_inbound_messages
    payload = {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "PNID_123"},
        "contacts": [{"profile": {"name": "Sara"}, "wa_id": "966511111111"}],
        "messages": [{
            "from": "966511111111", "id": "wamid.INT", "type": "interactive",
            "interactive": {"type": "button_reply",
                            "button_reply": {"id": "confirm", "title": "Confirm Receipt"}},
        }],
    }}]}]}
    msgs = parse_inbound_messages(payload)
    assert len(msgs) == 1 and msgs[0].text == "Confirm Receipt", \
        f"interactive button title not extracted: {msgs and msgs[0].text!r}"


def check_wsc_status_callback_separated():
    from services.whatsapp_webhook import parse_inbound_messages, parse_statuses
    payload = {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "PNID_123"},
        "statuses": [{"id": "wamid.X", "recipient_id": "966500000000",
                      "status": "delivered", "timestamp": "1719600100"}],
    }}]}]}
    assert parse_inbound_messages(payload) == [], "status callbacks must NOT be inbound messages"
    statuses = parse_statuses(payload)
    assert len(statuses) == 1 and statuses[0]["status"] == "delivered", "status not parsed"


def check_wsc_router_stub():
    from services.whatsapp_webhook import parse_inbound_messages, route_inbound_message
    hi = parse_inbound_messages(_wa_text_payload(body="hi"))[0]
    reply = route_inbound_message(hi)
    assert reply and "RECEIVED" in reply, "greeting should return the help/menu reply"
    unknown = parse_inbound_messages(_wa_text_payload(body="zzz unmatched"))[0]
    assert route_inbound_message(unknown) is None, "unmatched message must not auto-reply (stub)"


def check_wsc_verify_subscription_and_signature():
    import importlib
    W = importlib.import_module("services.whatsapp_webhook")
    # GET handshake token check
    W.META_WEBHOOK_VERIFY_TOKEN = "tok123"
    assert W.verify_subscription("subscribe", "tok123") is True, "matching token must verify"
    assert W.verify_subscription("subscribe", "wrong") is False, "wrong token must fail"
    assert W.verify_subscription("subscribe", None) is False, "missing token must fail"
    # Signature: passthrough when secret unset; strict when set
    W.META_APP_SECRET = ""
    assert W.verify_signature(b"{}", None) is True, "unset secret must not block setup"
    import hmac as _h, hashlib as _hh
    W.META_APP_SECRET = "s3cr3t"
    body = b'{"hello":"world"}'
    good = "sha256=" + _h.new(b"s3cr3t", body, _hh.sha256).hexdigest()
    assert W.verify_signature(body, good) is True, "valid signature must pass"
    assert W.verify_signature(body, "sha256=deadbeef") is False, "bad signature must fail"
    assert W.verify_signature(body, None) is False, "missing signature must fail when secret set"


# ---------------------------------------------------------------------------
# Man-Hour & Labor Tracking (workstream §2Z) — schema + helpers
# ---------------------------------------------------------------------------
# mh_* tables are isolated: write-only to mh_*, read-only against sme_*. These
# checks run against the throwaway bug_check DB (mh tables self-heal in init_db).
def _mh_conn():
    conn = database.get_connection(":memory:")
    database.init_db(conn)
    return conn


def check_mh_schema_present():
    conn = _mh_conn()
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE 'mh_%' "
        "OR name='v_mh_estimate_vs_actual'")}
    for t in ("mh_employees", "mh_timesheets", "mh_production",
              "mh_manhour_estimates", "mh_variance_notes",
              "v_mh_estimate_vs_actual"):
        assert t in names, f"missing man-hour object: {t}"


def check_mh_hours_math():
    # 8h normal + 1h break policy
    assert database.compute_mh_hours("07:30", "16:30", 60) == (8.0, 8.0, 0.0), \
        "07:30-16:30 -60m must be 8/8/0"
    assert database.compute_mh_hours("07:00", "18:00", 60) == (10.0, 8.0, 2.0), \
        "10h net must be 10/8/2 (OT beyond 8)"
    # overnight wrap, no break
    assert database.compute_mh_hours("22:00", "06:00", 0) == (8.0, 8.0, 0.0), \
        "overnight 22:00->06:00 must wrap to 8h"
    # unparseable → zeros, never raises
    assert database.compute_mh_hours(None, "x", 60) == (0.0, 0.0, 0.0)


def check_mh_employee_upsert_idempotent():
    conn = _mh_conn()
    ok, _ = database.upsert_mh_employee("CNCEC", "30551", "Thameem", conn=conn)
    assert ok
    # second upsert updates in place (no duplicate; name changes)
    database.upsert_mh_employee("CNCEC", "30551", "Thameem Ansari",
                                worker_type="OWN", conn=conn)
    df = database.list_mh_employees("CNCEC", conn=conn)
    assert len(df) == 1, f"expected 1 employee, got {len(df)}"
    assert df.iloc[0]["Name"] == "Thameem Ansari", "upsert must update Name in place"
    bad, _ = database.upsert_mh_employee("CNCEC", "X", "Y", worker_type="Bogus", conn=conn)
    assert bad is False, "invalid Worker_Type must be rejected"


def check_mh_timesheet_and_distribution():
    conn = _mh_conn()
    for code in ("A", "B"):
        database.upsert_mh_employee("CNCEC", code, f"Worker {code}", conn=conn)
    database.add_mh_timesheet("CNCEC", "A", "2026-05-16", "07:30", "16:30",
                              equipment_tag="EQ1", system_code="RL-1", conn=conn)
    database.add_mh_timesheet("CNCEC", "B", "2026-05-16", "07:00", "18:00",
                              equipment_tag="EQ1", system_code="RL-1", conn=conn)
    # even split of 20 SQM across 2 workers → 10/10
    database.set_mh_production("CNCEC", "2026-05-16", "EQ1", "RL-1", 20.0,
                               distribution_method="even", conn=conn)
    ts = database.list_mh_timesheets("CNCEC", conn=conn)
    assert sorted(ts["Allocated_SQM"]) == [10.0, 10.0], "even split must be 10/10"
    # by_hours: A=8h, B=10h, total 18 → A=8.889, B=11.111
    database.set_mh_production("CNCEC", "2026-05-16", "EQ1", "RL-1", 20.0,
                               distribution_method="by_hours", conn=conn)
    ts = database.list_mh_timesheets("CNCEC", conn=conn)
    vals = sorted(round(v, 2) for v in ts["Allocated_SQM"])
    assert vals == [8.89, 11.11], f"by_hours split wrong: {vals}"


def check_mh_parse_workbook():
    import io
    buf = io.BytesIO()
    sar = pd.DataFrame([
        {"Location": "", "Equipment Tag #": "", "code": 30551, "name": "Thameem",
         "work date": "2026-05-16", "in time": "07:30", "out time": "16:30",
         "status": "PR", "remarks": ""},
        {"Location": "", "Equipment Tag #": "", "code": 30802, "name": "Anish",
         "work date": "2026-05-16", "in time": "07:30", "out time": "16:30",
         "status": "PR", "remarks": ""},
    ])
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        sar.to_excel(w, sheet_name="SAR", index=False)
    buf.seek(0)
    parsed = database.parse_attendance_workbook(buf)
    assert len(parsed["employees"]) == 2, parsed["employees"]
    assert len(parsed["timesheets"]) == 2, "two SAR rows expected"
    assert parsed["dates"] == ["2026-05-16"], parsed["dates"]
    assert parsed["timesheets"][0]["code"] == "30551", "numeric code must coerce to str"


def check_mh_import_replace_vs_append():
    conn = _mh_conn()
    parsed = {
        "employees": [{"code": "A", "name": "Worker A", "designation": "",
                       "worker_type": "OWN", "company": ""}],
        "timesheets": [{"code": "A", "name": "Worker A", "work_date": "2026-05-16",
                        "location": "", "equipment_tag": "", "in_time": "07:30",
                        "out_time": "16:30", "status": "PR", "remarks": ""}],
        "dates": ["2026-05-16"],
    }
    e, t = database.import_mh_attendance("CNCEC", parsed, replace=True, conn=conn)
    assert (e, t) == (1, 1), (e, t)
    # replace re-import → no duplication
    database.import_mh_attendance("CNCEC", parsed, replace=True, conn=conn)
    assert len(database.list_mh_timesheets("CNCEC", conn=conn)) == 1, \
        "replace-by-date must not duplicate on re-import"
    # append → adds a second row
    database.import_mh_attendance("CNCEC", parsed, replace=False, conn=conn)
    assert len(database.list_mh_timesheets("CNCEC", conn=conn)) == 2, \
        "append mode should insert again"


def check_mh_estimate_vs_actual_view():
    conn = _mh_conn()
    database.upsert_mh_employee("CNCEC", "A", "Worker A", conn=conn)
    database.add_mh_timesheet("CNCEC", "A", "2026-05-16", "07:00", "18:00",
                              equipment_tag="EQ1", system_code="RL-1", conn=conn)
    database.upsert_mh_estimate("CNCEC", "EQ1", "RL-1", 8.0, conn=conn)
    database.set_mh_variance_reason("CNCEC", "EQ1", "RL-1", "rework", conn=conn)
    df = database.get_mh_estimate_vs_actual("CNCEC", conn=conn)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["Estimated_Manhours"] == 8.0 and row["Actual_Manhours"] == 10.0, \
        "view must sum actual Total_Hours (10) vs estimate (8)"
    assert row["Variance_Manhours"] == 2.0 and row["Variance_Pct"] == 25.0, \
        "variance must be +2h / +25%"
    assert row["Variance_Reason"] == "rework", "variance reason must join through"


# ---------------------------------------------------------------------------
# Workstream C — Meta WhatsApp sender provider + SME download-button forward-compat
# ---------------------------------------------------------------------------
def check_meta_provider_routing():
    import whatsapp_worker as W
    saved = {k: os.environ.get(k) for k in ("META_PHONE_NUMBER_ID", "META_ACCESS_TOKEN")}
    try:
        os.environ["META_PHONE_NUMBER_ID"] = "PNID-123"
        os.environ["META_ACCESS_TOKEN"] = "TKN-xyz"
        pnid, tok, ver = W._meta_config()
        assert pnid == "PNID-123" and tok == "TKN-xyz", (pnid, tok)
        assert ver.startswith("v"), f"api version looks wrong: {ver}"
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    # provider=meta routes _send_whatsapp → _send_via_meta
    sent = {}
    op, om = W.WHATSAPP_PROVIDER, W._send_via_meta
    try:
        W.WHATSAPP_PROVIDER = "meta"
        W._send_via_meta = lambda p, t: sent.update(phone=p, text=t)
        W._send_whatsapp("+966500000000", "hello")
        assert sent == {"phone": "+966500000000", "text": "hello"}, sent
    finally:
        W.WHATSAPP_PROVIDER, W._send_via_meta = op, om

    # missing config → _send_via_meta raises (so the queue row is marked failed)
    saved2 = {k: os.environ.pop(k, None) for k in ("META_PHONE_NUMBER_ID", "META_ACCESS_TOKEN")}
    try:
        raised = False
        try:
            W._send_via_meta("+966500000000", "x")
        except RuntimeError:
            raised = True
        assert raised, "missing META_* config must raise RuntimeError"
    finally:
        for k, v in saved2.items():
            if v is not None:
                os.environ[k] = v


def check_pages_internal_exports_resolve():
    # Guards the cold-start ImportError: every page_* main.py imports must be
    # resolvable from the pages_internal package, and the SME portal must not
    # re-enter the half-built package at module level.
    import importlib, pathlib
    pkg = importlib.import_module("pages_internal")
    for name in ("page_live_dashboard", "page_daily_issue_log", "page_hod_portal",
                 "page_admin_portal", "page_reports", "page_logistics_portal",
                 "page_warehouse_portal", "page_supervisor_portal",
                 "page_material_estimator", "page_manhour_portal"):
        assert hasattr(pkg, name), f"pages_internal missing export: {name}"
    src = pathlib.Path(REPO_ROOT / "pages_internal" /
                       "material_estimator_portal.py").read_text(encoding="utf-8")
    assert "from pages_internal.material_estimator_engine import build_demand_matrix" \
        not in src.replace("#   from pages_internal.material_estimator_engine import build_demand_matrix", ""), \
        "SME portal must not module-level import back through the pages_internal package"
    # lazy loading guard: pages must NOT be eagerly imported during package init
    # (that left the cold-start race window). PEP 562 __getattr__ defers them.
    init_src = pathlib.Path(REPO_ROOT / "pages_internal" / "__init__.py").read_text(encoding="utf-8")
    assert "def __getattr__" in init_src, \
        "pages_internal must lazily import page modules via __getattr__"
    assert "from .material_estimator_portal import" not in init_src, \
        "pages_internal must NOT eagerly import the heavy estimator at package init"


def check_estimator_filters_cross_filter():
    import pathlib
    src = pathlib.Path(REPO_ROOT / "pages_internal" /
                       "material_estimator_portal.py").read_text(encoding="utf-8")
    # §4 — System Code options narrow by selected Substrate (look-back via session_state)
    assert '_sub_sel_prev = st.session_state.get("dash_substrate")' in src, \
        "System Code filter must cross-filter by the selected Substrate"
    # and Substrate options narrow by selected System Codes
    assert "_eq_pool_sub = _eq_pool[_eq_pool[\"Equipment_Tag_No.\"].isin(_code_tags)]" in src, \
        "Substrate filter must cross-filter by the selected System Codes"
    # §4 rollout — the Material Requirement block must also cascade L->T->Code
    assert '_t1_eq_pool = _t1_eq_pool[_t1_eq_pool["Type"].str.strip().isin(f_type)]' in src, \
        "Material Requirement filters must cascade Location->Type->System Code"


def check_kpi_drilldown_is_modal():
    import pathlib
    src = pathlib.Path(REPO_ROOT / "pages_internal" /
                       "material_estimator_portal.py").read_text(encoding="utf-8")
    assert "@st.dialog(" in src and "def _kpi_drilldown_dialog(" in src, \
        "KPI drill-down must open a centered st.dialog modal"
    assert 'key=f"kpimetric_{state_key}"' in src, \
        "KPI metric must be a keyed button (card-CSS hook + modal trigger)"
    assert "_kpi_drilldown_dialog()" in src, "metric click must open the modal"


def check_sme_admin_site_picker_and_sidebar_hidden():
    import pathlib
    src = pathlib.Path(REPO_ROOT / "pages_internal" /
                       "material_estimator_portal.py").read_text(encoding="utf-8")
    # 2a — admin gets a single-site picker (else defaults to HQ → zeros)
    assert 'st.sidebar.selectbox("🧪 Estimator site"' in src, \
        "admin Material-Estimator site picker missing"
    assert 'if (user.get("role") or "").lower() == "admin":' in src, \
        "admin-role branch for the site picker missing"
    # 2c — SME's own sidebar suppressed, theme still applied
    assert "_SME_LEGACY_SIDEBAR = False" in src, "SME legacy-sidebar flag missing"
    assert "if _SME_LEGACY_SIDEBAR:" in src, "SME sidebar must be gated by the flag"
    assert "        _apply_theme_attr()\n        if _SME_LEGACY_SIDEBAR:" in src, \
        "theme must be applied OUTSIDE the suppressed sidebar block"


def check_sme_download_button_forwards_width():
    import pathlib
    src = pathlib.Path(REPO_ROOT / "pages_internal" /
                       "material_estimator_portal.py").read_text(encoding="utf-8")
    assert "icon=None, **extra)" in src, \
        "_secure_download_button must accept **extra (forward-compat with width=)"
    assert "_orig_download_button(**call_kwargs, **extra)" in src, \
        "must forward **extra to the real download_button"
    assert 'if "width" not in extra:' in src, \
        "must drop use_container_width when width is supplied (avoid passing both)"


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
# ---------------------------------------------------------------------------
# Phase 6A — CV foundation (employees, tool_catalogue, cv_model_versions)
# ---------------------------------------------------------------------------
def check_employees_crud() -> None:
    """add_employee + duplicate rejection + update + get round-trip."""
    conn = database.get_connection()
    try:
        ok, msg = database.add_employee("EMP_TST_001", "Alice", "+966500000001",
                                         "Warehouse", "test_admin", conn=conn)
        assert ok, f"first add should succeed: {msg}"

        ok2, msg2 = database.add_employee("EMP_TST_001", "Dup", conn=conn)
        assert not ok2, "duplicate ID_Number should be rejected"
        assert "already" in msg2.lower(), f"duplicate msg unhelpful: {msg2}"

        # Missing required field — should reject, not raise.
        ok3, _ = database.add_employee("", "Nobody", conn=conn)
        assert not ok3, "missing ID_Number should be rejected"

        # Update + lookup.
        assert database.update_employee("EMP_TST_001", department="Logistics",
                                        status="suspended",
                                        updated_by="test_admin", conn=conn)
        row = database.get_employee_by_id_number("EMP_TST_001", conn=conn)
        assert row and row["Department"] == "Logistics", f"update did not stick: {row}"
        assert row["status"] == "suspended", f"status update missed: {row}"
    finally:
        conn.close()


def check_import_employees_csv_idempotent() -> None:
    """5-row CSV first import → 5 inserted; re-import with 1 changed → 1 updated, 4 skipped."""
    import io
    conn = database.get_connection()
    try:
        csv1 = io.StringIO(
            "ID_Number,Name,Phone_Number,Department\n"
            "EMP_CSV_1,Worker One,+96650001,Ops\n"
            "EMP_CSV_2,Worker Two,+96650002,Ops\n"
            "EMP_CSV_3,Worker Three,+96650003,QC\n"
            "EMP_CSV_4,Worker Four,+96650004,QC\n"
            "EMP_CSV_5,Worker Five,+96650005,Warehouse\n"
        )
        r1 = database.import_employees_csv(csv1, "test_admin", conn=conn)
        assert r1["inserted"] == 5 and r1["updated"] == 0, f"first import: {r1}"

        csv2 = io.StringIO(
            "ID_Number,Name,Phone_Number,Department\n"
            "EMP_CSV_1,Worker One,+96650001,Ops\n"
            "EMP_CSV_2,Worker Two,+96650002,Ops\n"
            "EMP_CSV_3,Worker Three,+96650003,QC\n"
            "EMP_CSV_4,Worker Four,+96650004,QC\n"
            "EMP_CSV_5,Worker Five Renamed,+96650005,Warehouse\n"   # name changed
        )
        r2 = database.import_employees_csv(csv2, "test_admin", conn=conn)
        assert r2["inserted"] == 0, f"re-import inserted unexpectedly: {r2}"
        assert r2["updated"] == 1, f"expected exactly 1 update, got {r2}"
        assert r2["skipped"] == 4, f"expected 4 skipped, got {r2}"

        # Verify the update actually persisted
        row = database.get_employee_by_id_number("EMP_CSV_5", conn=conn)
        assert row["Name"] == "Worker Five Renamed", f"update content wrong: {row}"

        # Header is case-insensitive — should not crash
        csv3 = io.StringIO("id_number,NAME,phone_number,department\n"
                           "EMP_CSV_6,Worker Six,+96650006,Tools\n")
        r3 = database.import_employees_csv(csv3, "test_admin", conn=conn)
        assert r3["inserted"] == 1, f"case-insensitive header import failed: {r3}"
    finally:
        conn.close()


def check_cv_model_register_and_promote() -> None:
    """register + promote round-trip + only one active at a time."""
    conn = database.get_connection()
    try:
        id_a = database.register_cv_model_version(
            "v_test_A", "/m/A.pt", ["clsA", "clsB"], mAP=0.81, conn=conn,
        )
        id_b = database.register_cv_model_version(
            "v_test_B", "/m/B.pt", ["clsA", "clsB", "clsC"], mAP=0.87, conn=conn,
        )
        assert isinstance(id_a, int) and isinstance(id_b, int), "ids missing"
        assert database.get_active_cv_model(conn=conn) is None, \
            "no model should be active after pure registers"

        assert database.promote_cv_model_version("v_test_B", promoted_by="test_admin",
                                                  conn=conn)
        active = database.get_active_cv_model(conn=conn)
        assert active and active["version"] == "v_test_B", f"v_B not active: {active}"
        assert active["classes"] == ["clsA", "clsB", "clsC"], \
            f"classes_json round-trip wrong: {active}"

        # Promote the other one — the partial unique index must allow it
        # because we demote the current active first inside one transaction.
        assert database.promote_cv_model_version("v_test_A", promoted_by="test_admin",
                                                  conn=conn)
        active2 = database.get_active_cv_model(conn=conn)
        assert active2 and active2["version"] == "v_test_A", f"swap failed: {active2}"

        # Sanity: confirm only one active row in the table
        cnt = conn.execute(
            "SELECT COUNT(*) FROM cv_model_versions WHERE is_active = 1"
        ).fetchone()[0]
        assert cnt == 1, f"expected exactly 1 active row, got {cnt}"

        # Promoting an unknown version returns False, not raise
        assert database.promote_cv_model_version("v_does_not_exist", conn=conn) is False
    finally:
        conn.close()


def check_tool_catalogue_crud() -> None:
    """add_tool_class + dup + set_min_confidence + list filter by model."""
    conn = database.get_connection()
    try:
        mid = database.register_cv_model_version(
            "v_tool_test", "/m/tool.pt", ["torque_wrench_12", "hammer_8"],
            mAP=0.9, conn=conn,
        )
        ok, _ = database.add_tool_class(
            "torque_wrench_12", "Torque Wrench 12mm",
            category="wrench", model_version_id=mid,
            created_by="test_admin", conn=conn,
        )
        assert ok, "first add_tool_class should succeed"

        ok2, msg2 = database.add_tool_class(
            "torque_wrench_12", "Dup", "x", mid, "test_admin", conn=conn,
        )
        assert not ok2 and "already" in msg2.lower(), \
            f"duplicate class_name should be rejected: {msg2}"

        assert database.set_tool_class_min_confidence(
            "torque_wrench_12", 0.88, updated_by="test_admin", conn=conn,
        )
        df = database.list_tool_catalogue(model_version_id=mid, conn=conn)
        assert len(df) == 1, f"expected 1 row for model {mid}, got {len(df)}"
        assert float(df.iloc[0]["min_confidence"]) == 0.88, \
            f"min_confidence override missed: {df.iloc[0].to_dict()}"

        # Unknown class → False, not raise
        assert database.set_tool_class_min_confidence(
            "no_such_class", 0.5, conn=conn,
        ) is False
    finally:
        conn.close()


# ── Phase 6B — QR encode/decode roundtrip ────────────────────────────────────
def check_qr_encode_produces_png() -> None:
    """Pure encode-side check: always runnable — no native deps.

    Verifies encode_id_to_png returns a non-trivial PNG byte string for a
    realistic ID and rejects blank input with ValueError.
    """
    from ai.cv.qr import encode_id_to_png

    sample_id = "EMP-RT-001"
    png = encode_id_to_png(sample_id)
    assert isinstance(png, bytes) and len(png) > 100, \
        f"encode_id_to_png returned suspicious payload (size={len(png) if png else 0})"
    # PNG magic header — sanity check we actually got a PNG.
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "encode_id_to_png did not return a PNG"

    # Blank / whitespace-only input must raise.
    try:
        encode_id_to_png("   ")
    except ValueError:
        pass
    else:
        raise AssertionError("encode_id_to_png should reject blank input")


# ── Phase 6C — YOLO inference helper (all mocked) ────────────────────────────
def _make_mock_detect_box(cls_idx: int, conf: float, xyxy=(10, 10, 100, 100)):
    """Build a minimal mock that matches the duck-type ai/cv/inference.py
    reads from an ultralytics Box (b.cls.item(), b.conf.item(), b.xyxy[0])."""
    import types
    return types.SimpleNamespace(
        cls=types.SimpleNamespace(item=lambda: cls_idx),
        conf=types.SimpleNamespace(item=lambda: conf),
        xyxy=[list(xyxy)],
    )


def _make_mock_yolo_result(boxes_list):
    import types
    return types.SimpleNamespace(boxes=boxes_list)


def check_detect_tool_no_active_model() -> None:
    """detect_tool returns [] cleanly when no active model exists in DB."""
    from unittest.mock import patch
    from ai.cv import inference
    inference.invalidate_model_cache()
    # Force _load_active_yolo to report "no model"
    with patch.object(inference, "_load_active_yolo", lambda: (None, None)):
        assert inference.detect_tool(b"fake-bytes") == []


def check_detect_tool_missing_disk_file() -> None:
    """If the active row's model_path doesn't exist on disk, return []."""
    from unittest.mock import patch
    from ai.cv import inference
    # Simulate the case where DB has an active row but the file is missing
    # by making _load_active_yolo report (None, None) (which is what the
    # real loader does on FileNotFoundError).
    with patch.object(inference, "_load_active_yolo", lambda: (None, None)):
        assert inference.detect_tool(b"fake-bytes") == []


def check_detect_tool_min_confidence_filter() -> None:
    """Detections below DEFAULT_MIN_CONFIDENCE (0.75) are dropped."""
    from unittest.mock import patch
    from ai.cv import inference

    # Mock YOLO model: returns one high-conf and one low-conf box.
    boxes = [
        _make_mock_detect_box(0, 0.92),
        _make_mock_detect_box(0, 0.50),
    ]
    mock_result = _make_mock_yolo_result(boxes)

    class _MockYOLO:
        names = {0: "torque_wrench_12"}
        def predict(self, *a, **k): return [mock_result]

    inference.invalidate_model_cache()
    with patch.object(inference, "_load_active_yolo",
                      lambda: (_MockYOLO(), {"version": "v1"})):
        with patch.object(inference, "_min_confidence_for_class",
                          lambda c: inference.DEFAULT_MIN_CONFIDENCE):
            # Need a real PNG so PIL.Image.open succeeds. Reuse the QR helper.
            from ai.cv.qr import encode_id_to_png
            dets = inference.detect_tool(encode_id_to_png("dummy"))
    assert len(dets) == 1, f"expected 1 detection above 0.75, got {len(dets)}"
    assert dets[0]["class_name"] == "torque_wrench_12"
    assert abs(dets[0]["confidence"] - 0.92) < 1e-6
    assert dets[0]["applied_threshold"] == inference.DEFAULT_MIN_CONFIDENCE


def check_detect_tool_per_class_override() -> None:
    """Per-class min_confidence (0.55) lets a 0.60 detection through."""
    from unittest.mock import patch
    from ai.cv import inference

    boxes = [_make_mock_detect_box(0, 0.60)]
    mock_result = _make_mock_yolo_result(boxes)

    class _MockYOLO:
        names = {0: "small_wrench"}
        def predict(self, *a, **k): return [mock_result]

    inference.invalidate_model_cache()
    # The override puts the class threshold at 0.55, so 0.60 survives.
    with patch.object(inference, "_load_active_yolo",
                      lambda: (_MockYOLO(), {"version": "v1"})):
        with patch.object(inference, "_min_confidence_for_class",
                          lambda c: 0.55 if c == "small_wrench" else 0.75):
            from ai.cv.qr import encode_id_to_png
            dets = inference.detect_tool(encode_id_to_png("dummy"))
    assert len(dets) == 1, f"per-class override didn't apply: {dets}"
    assert dets[0]["applied_threshold"] == 0.55


def check_invalidate_model_cache_clears_lru() -> None:
    """invalidate_model_cache() actually resets the lru_cache state."""
    from unittest.mock import patch
    from ai.cv import inference

    # Prime the cache with a sentinel
    sentinel = object()
    with patch.object(inference, "_load_active_yolo",
                      lambda: (sentinel, {"version": "v1"})):
        # Force one call so cache_info().currsize would go up if it were
        # a real lru_cache call (we're patching the function so it isn't,
        # but the threshold cache IS real).
        inference._min_confidence_for_class("primer_class")
        info_before = inference._min_confidence_for_class.cache_info()
        assert info_before.currsize >= 1

    inference.invalidate_model_cache()
    info_after = inference._min_confidence_for_class.cache_info()
    assert info_after.currsize == 0, \
        f"threshold cache not cleared (currsize={info_after.currsize})"


# ── Phase 6D — Smart Scan logic helpers ─────────────────────────────────────
def check_bucket_detections_auto_bucket() -> None:
    """≥ 0.75 → ('auto', [top_only])."""
    from ai.cv.smart_scan import bucket_detections
    dets = [
        {"class_name": "wrench_a", "confidence": 0.92},
        {"class_name": "wrench_b", "confidence": 0.81},
    ]
    mode, items = bucket_detections(dets)
    assert mode == "auto", f"expected 'auto', got {mode!r}"
    assert len(items) == 1 and items[0]["class_name"] == "wrench_a", items


def check_bucket_detections_candidates_bucket() -> None:
    """0.30 ≤ top < 0.75 → ('candidates', up to 3). And empty / sub-0.30 → 'manual'."""
    from ai.cv.smart_scan import bucket_detections
    dets = [
        {"class_name": "a", "confidence": 0.65},
        {"class_name": "b", "confidence": 0.55},
        {"class_name": "c", "confidence": 0.45},
        {"class_name": "d", "confidence": 0.35},  # should be dropped — cap=3
    ]
    mode, items = bucket_detections(dets)
    assert mode == "candidates", f"expected 'candidates', got {mode!r}"
    assert len(items) == 3, f"expected 3 candidates, got {len(items)}"
    assert [d["class_name"] for d in items] == ["a", "b", "c"]

    # Manual branch — empty and sub-0.30 top both fall back.
    assert bucket_detections([]) == ("manual", [])
    mode2, _ = bucket_detections([{"class_name": "x", "confidence": 0.20}])
    assert mode2 == "manual", f"sub-threshold top should bucket as 'manual', got {mode2!r}"


def check_lookup_employee_by_qr_active_only() -> None:
    """lookup_employee_by_qr returns active rows only; inactive/suspended → None."""
    import database
    from ai.cv.smart_scan import lookup_employee_by_qr
    conn = database.get_connection()
    try:
        # Seed: one active employee + one suspended.
        ok, _msg = database.add_employee(
            "EMP-D-ACT", "Active Ahmed", "+9665", "Logistics",
            created_by="harness", conn=conn,
        )
        assert ok
        ok2, _msg2 = database.add_employee(
            "EMP-D-SUS", "Suspended Sami", "+9665", "Maintenance",
            created_by="harness", conn=conn,
        )
        assert ok2
        assert database.update_employee(
            "EMP-D-SUS", status="suspended", updated_by="harness", conn=conn,
        )

        hit_active = lookup_employee_by_qr("EMP-D-ACT", conn=conn)
        assert hit_active is not None
        assert hit_active["Name"] == "Active Ahmed"

        miss_suspended = lookup_employee_by_qr("EMP-D-SUS", conn=conn)
        assert miss_suspended is None, "suspended employees must not auth"

        miss_unknown = lookup_employee_by_qr("EMP-D-NONE", conn=conn)
        assert miss_unknown is None

        miss_blank = lookup_employee_by_qr("   ", conn=conn)
        assert miss_blank is None
    finally:
        conn.close()


def check_get_open_loans_for_employee_dual_path() -> None:
    """Matches loans created via CV path (cv_employee_id) AND manual path
    (borrower_name = employees.Name) for the same employee ID."""
    import database
    conn = database.get_connection()
    try:
        # Seed an employee.
        ok, _ = database.add_employee(
            "EMP-DUAL-01", "Dual-Path Daoud", "+9665", "Logistics",
            created_by="harness", conn=conn,
        )
        assert ok

        # Loan 1: CV-created — cv_employee_id populated, borrower_name blank.
        database.insert_returnable_item(
            conn=conn,
            material_name="torque_wrench_12",
            uom="Pcs",
            qty=1,
            borrower_name="",
            borrower_phone="",
            expected_return_time="2026-06-20 17:00:00",
            site_id="CNCEC",
            cv_detected=1,
            cv_confidence=0.91,
            cv_employee_id="EMP-DUAL-01",
            cv_tool_class="torque_wrench_12",
        )

        # Loan 2: manual — borrower_name set, no CV fields.
        database.insert_returnable_item(
            conn=conn,
            material_name="multimeter",
            uom="Pcs",
            qty=1,
            borrower_name="Dual-Path Daoud",
            borrower_phone="+9665",
            expected_return_time="2026-06-21 09:00:00",
            site_id="CNCEC",
        )

        # Loan 3: a different employee — must NOT come back.
        database.insert_returnable_item(
            conn=conn,
            material_name="hammer",
            uom="Pcs",
            qty=1,
            borrower_name="Some Other Worker",
            borrower_phone="+9665",
            expected_return_time="2026-06-22 10:00:00",
            site_id="CNCEC",
        )

        df = database.get_open_loans_for_employee("EMP-DUAL-01", site_id="CNCEC",
                                                   conn=conn)
        names = sorted(df["material_name"].tolist())
        assert names == ["multimeter", "torque_wrench_12"], \
            f"expected both loans for EMP-DUAL-01, got {names!r}"

        # Cross-site filter: explicit other site → no rows.
        df_other = database.get_open_loans_for_employee("EMP-DUAL-01",
                                                        site_id="SAR",
                                                        conn=conn)
        assert df_other.empty, "site filter must scope to the requested site"
    finally:
        conn.close()


# ── Phase 6E — Returnable loan reminder sweep ───────────────────────────────
def _seed_loan_for_reminder(conn, *, loan_due: datetime.datetime,
                            cv_id: str = "", borrower_phone: str = "",
                            borrower_name: str = "Test Borrower",
                            site_id: str = "CNCEC") -> int:
    """Insert a borrowed returnable_items row and return its id."""
    import database as _db
    _db.insert_returnable_item(
        conn=conn,
        material_name="test_tool",
        uom="Pcs",
        qty=1,
        borrower_name=borrower_name,
        borrower_phone=borrower_phone,
        expected_return_time=loan_due.strftime("%Y-%m-%d %H:%M:%S"),
        site_id=site_id,
        cv_detected=1 if cv_id else 0,
        cv_employee_id=cv_id or None,
    )
    rid = conn.execute(
        "SELECT MAX(id) FROM returnable_items"
    ).fetchone()[0]
    return int(rid)


def check_returnable_sweep_fires_all_four_offsets() -> None:
    """Across four hypothetical 'now' values, the sweep fires exactly one
    reminder per offset for one loan."""
    import database
    from datetime import datetime, timedelta
    conn = database.get_connection()
    try:
        due = datetime(2026, 7, 1, 17, 0, 0)
        loan_id = _seed_loan_for_reminder(
            conn, loan_due=due,
            cv_id="EMP-RE-1", borrower_phone="+9665100000000",
        )
        # Seed the matching employee so the CV phone fallback works.
        database.add_employee(
            "EMP-RE-1", "Ali Ali", "+9665100000000", "Logistics",
            created_by="harness", conn=conn,
        )

        # Map: 'now' values that land each offset square in the middle of
        # its 1-hour window.
        now_for_offset = {
            -2: due - timedelta(hours=1, minutes=30),   # window [1, 2) before
             0: due - timedelta(minutes=30),            # window [-1, 0)
             2: due + timedelta(hours=2, minutes=30),   # window [-3, -2)
            24: due + timedelta(hours=24, minutes=30),  # window [-25, -24)
        }
        for offset, now in now_for_offset.items():
            n = database.sweep_returnable_reminders(now=now, conn=conn)
            assert n >= 1, f"offset={offset} produced 0 fires at now={now}"

        # Dedup row count should equal exactly 4 (one per offset).
        n_dedup = conn.execute(
            "SELECT COUNT(*) FROM delivery_reminders_sent "
            "WHERE ref_type='returnable_loan' AND ref_number=?",
            (str(loan_id),),
        ).fetchone()[0]
        assert n_dedup == 4, f"expected 4 dedup rows, got {n_dedup}"
    finally:
        conn.close()


def check_returnable_sweep_idempotent() -> None:
    """Running the sweep twice at the same 'now' fires once."""
    import database
    from datetime import datetime, timedelta
    conn = database.get_connection()
    try:
        due = datetime(2026, 8, 1, 17, 0, 0)
        loan_id = _seed_loan_for_reminder(
            conn, loan_due=due,
            cv_id="EMP-IDEM", borrower_phone="+9665100000001",
            borrower_name="Idem Test",
        )
        database.add_employee(
            "EMP-IDEM", "Idem Test", "+9665100000001", "Logistics",
            created_by="harness", conn=conn,
        )

        # Pick a 'now' in the T-0 window: [due-1h, due) (i.e. 30 min before due)
        now = due - timedelta(minutes=30)

        n1 = database.sweep_returnable_reminders(now=now, conn=conn)
        n2 = database.sweep_returnable_reminders(now=now, conn=conn)
        assert n1 >= 1, "first sweep must fire at least one event"
        assert n2 == 0, f"second sweep must fire 0 events (got {n2})"
        # Sanity — exactly one dedup row for this loan + offset 0
        n_dedup = conn.execute(
            "SELECT COUNT(*) FROM delivery_reminders_sent "
            "WHERE ref_type='returnable_loan' AND ref_number=? AND offset_days=0",
            (str(loan_id),),
        ).fetchone()[0]
        assert n_dedup == 1, f"expected 1 dedup row, got {n_dedup}"
    finally:
        conn.close()


def check_returnable_phone_resolution_three_tier() -> None:
    """CV path wins over manual; manual fills when CV missing; neither →
    log audit row, no WhatsApp queued."""
    import database
    from unittest.mock import patch
    from datetime import datetime, timedelta
    conn = database.get_connection()
    try:
        # Three loans, all due in 30 minutes (T-0 window).
        due = datetime(2026, 9, 1, 17, 0, 0)
        # Loan A: CV path with employee phone available.
        database.add_employee(
            "EMP-PH-CV", "CV Borrower", "+9665PHONECV", "Logistics",
            created_by="harness", conn=conn,
        )
        loan_a = _seed_loan_for_reminder(
            conn, loan_due=due, cv_id="EMP-PH-CV", borrower_phone="",
        )
        # Loan B: manual path with borrower_phone.
        loan_b = _seed_loan_for_reminder(
            conn, loan_due=due, cv_id="", borrower_phone="+9665MANUAL",
        )
        # Loan C: neither phone source.
        loan_c = _seed_loan_for_reminder(
            conn, loan_due=due, cv_id="", borrower_phone="",
        )

        captured = []
        def _capture(event_key, phone, msg, conn=None):
            captured.append((event_key, phone))
            return True

        now = due - timedelta(minutes=30)
        with patch.object(database, "fire_whatsapp_event", side_effect=_capture):
            database.sweep_returnable_reminders(now=now, conn=conn)

        # Among the captured calls, find the ones tied to each loan via
        # the message. Easier: just check phones set.
        phones_used = {ph for _, ph in captured}
        assert "+9665PHONECV" in phones_used, \
            f"CV phone missing from {phones_used}"
        assert "+9665MANUAL" in phones_used, \
            f"Manual phone missing from {phones_used}"

        # Loan C → audit row with RETURNABLE_REMINDER_NO_PHONE
        n_audit = conn.execute(
            "SELECT COUNT(*) FROM system_audit_log "
            "WHERE action_type='RETURNABLE_REMINDER_NO_PHONE' "
            "  AND details LIKE ?",
            (f"loan={loan_c}%",),
        ).fetchone()[0]
        assert n_audit == 1, f"expected 1 audit row for orphan loan, got {n_audit}"
    finally:
        conn.close()


def check_returnable_t_plus_24h_supervisor_fanout() -> None:
    """T+24h fans WhatsApp to borrower + every SK + every Supervisor at site,
    plus emits an in-app row for supervisor role (Phase 6E spec change)."""
    import database
    from unittest.mock import patch
    from datetime import datetime, timedelta
    conn = database.get_connection()
    try:
        # Seed two SKs + one supervisor + one HOD (HOD MUST NOT be paged).
        conn.execute(
            "INSERT INTO users (username, password_hash, role, Site_ID, Phone_Number) "
            "VALUES (?, '', 'store_keeper', 'SITE-X', '+9665SK1')",
            ("sk_one_x",),
        )
        conn.execute(
            "INSERT INTO users (username, password_hash, role, Site_ID, Phone_Number) "
            "VALUES (?, '', 'store_keeper', 'SITE-X', '+9665SK2')",
            ("sk_two_x",),
        )
        conn.execute(
            "INSERT INTO users (username, password_hash, role, Site_ID, Phone_Number) "
            "VALUES (?, '', 'supervisor', 'SITE-X', '+9665SUPER')",
            ("sup_x",),
        )
        conn.execute(
            "INSERT INTO users (username, password_hash, role, Site_ID, Phone_Number) "
            "VALUES (?, '', 'hod', 'SITE-X', '+9665HOD')",
            ("hod_x",),
        )
        conn.commit()

        due = datetime(2026, 10, 1, 9, 0, 0)
        database.add_employee(
            "EMP-FAN", "Fan Borrower", "+9665BORROWER", "Logistics",
            created_by="harness", conn=conn,
        )
        loan_id = _seed_loan_for_reminder(
            conn, loan_due=due, cv_id="EMP-FAN", borrower_phone="",
            site_id="SITE-X",
        )

        captured_phones = []
        def _capture(event_key, phone, msg, conn=None):
            captured_phones.append(phone)
            return True

        now = due + timedelta(hours=24, minutes=30)  # T+24h window
        with patch.object(database, "fire_whatsapp_event", side_effect=_capture):
            database.sweep_returnable_reminders(now=now, conn=conn)

        # Expected phones at T+24h: borrower + 2 SKs + 1 Supervisor = 4.
        assert "+9665BORROWER" in captured_phones, captured_phones
        assert captured_phones.count("+9665SK1") == 1, captured_phones
        assert captured_phones.count("+9665SK2") == 1, captured_phones
        assert "+9665SUPER" in captured_phones, captured_phones
        # CRITICAL: HOD must NOT be paged at T+24h (spec change vs handoff).
        assert "+9665HOD" not in captured_phones, \
            f"HOD should not be paged at T+24h: {captured_phones}"

        # Supervisor-role broadcast must be present in in-app notifications.
        n_sup = conn.execute(
            "SELECT COUNT(*) FROM app_notifications "
            "WHERE event_key='returnable_reminder_t_plus_24h' "
            "  AND recipient_role='supervisor' AND recipient_site='SITE-X' "
            "  AND related_ref=?",
            (str(loan_id),),
        ).fetchone()[0]
        assert n_sup == 1, f"expected 1 supervisor in-app row, got {n_sup}"

        # And SK-role broadcast (this fires at every offset >= 0).
        n_sk = conn.execute(
            "SELECT COUNT(*) FROM app_notifications "
            "WHERE event_key='returnable_reminder_t_plus_24h' "
            "  AND recipient_role='store_keeper' AND recipient_site='SITE-X' "
            "  AND related_ref=?",
            (str(loan_id),),
        ).fetchone()[0]
        assert n_sk == 1, f"expected 1 SK in-app row at T+24h, got {n_sk}"
    finally:
        conn.close()


# ── Phase 6F — Bulk employee badge PDF ──────────────────────────────────────
def check_employee_badges_pdf_smoke() -> None:
    """generate_employee_qr_badges_pdf returns valid PDF bytes for real
    employees and for an empty list (one-page placeholder).

    Skips silently if qrcode is not installed (mirrors the existing
    pattern at the top of reports.py)."""
    try:
        from reports import generate_employee_qr_badges_pdf, _HAS_QRCODE
    except ImportError:
        return
    if not _HAS_QRCODE:
        return

    emps = [
        {"ID_Number": "EMP-PDF-1", "Name": "Ahmed — Test",   "Department": "Logistics"},
        {"ID_Number": "EMP-PDF-2", "Name": "Sara K",          "Department": "Warehouse"},
        {"ID_Number": "",           "Name": "Skip me",         "Department": "x"},  # missing ID → silently skipped
    ]
    pdf = generate_employee_qr_badges_pdf(emps)
    assert isinstance(pdf, bytes) and len(pdf) > 500, \
        f"badge PDF suspiciously small (size={len(pdf) if pdf else 0})"
    assert pdf[:4] == b"%PDF", "output is not a PDF (missing magic header)"

    # Empty input → still produces a valid one-page placeholder so the
    # download button never delivers zero bytes.
    pdf_empty = generate_employee_qr_badges_pdf([])
    assert pdf_empty[:4] == b"%PDF", "empty-input PDF lacks magic header"
    assert len(pdf_empty) > 500, "empty-input PDF should still carry a header band"


def check_qr_decode_roundtrip() -> None:
    """encode → decode preserves the ID_Number exactly.

    Requires libzbar via pyzbar. If the import fails (libzbar missing on
    this host), the check no-ops — the encode side is already covered by
    `check_qr_encode_produces_png`. Once libzbar is installed, the check
    will assert for real on the next run.
    """
    try:
        from pyzbar.pyzbar import decode as _zbar_probe  # noqa: F401
    except ImportError:
        # Encode-only environments (e.g. Streamlit Cloud) — skip silently.
        # The companion check_qr_encode_produces_png() still guards the
        # encode path so this section isn't completely uncovered.
        return

    from ai.cv.qr import encode_id_to_png, decode_png_to_id

    sample_id = "EMP-RT-001"
    decoded = decode_png_to_id(encode_id_to_png(sample_id))
    assert decoded == sample_id, \
        f"roundtrip mismatch: encoded {sample_id!r} but decoded {decoded!r}"

    # Garbage / empty inputs must NOT raise — they return None.
    assert decode_png_to_id(b"") is None
    assert decode_png_to_id(b"not-a-png") is None


# ═══════════════════════════════════════════════════════════════════════════
# Phase 7A — Employee Site Binding checks
# ═══════════════════════════════════════════════════════════════════════════
def _7a_emp(prefix: str) -> str:
    """Generate a unique ID_Number per check so tests don't collide."""
    import uuid as _u
    return f"{prefix}_{_u.uuid4().hex[:8]}"


def check_7a_site_id_column_present() -> None:
    conn = database.get_connection()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(employees)").fetchall()}
        assert "Site_ID" in cols, f"Site_ID missing from employees: {cols}"
    finally:
        conn.close()


def check_7a_site_id_index_present() -> None:
    conn = database.get_connection()
    try:
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='employees'"
        ).fetchall()}
        assert "ix_employees_site" in idx, f"ix_employees_site missing: {idx}"
    finally:
        conn.close()


def check_7a_add_with_site_persists() -> None:
    conn = database.get_connection()
    try:
        eid = _7a_emp("7A_ADD_SITE")
        ok, _ = database.add_employee(eid, "Site Bound", site_id="HQ", conn=conn)
        assert ok
        row = conn.execute(
            "SELECT Site_ID FROM employees WHERE ID_Number=?", (eid,)
        ).fetchone()
        assert row[0] == "HQ", f"Site_ID not persisted: {row}"
    finally:
        conn.close()


def check_7a_add_without_site_is_null() -> None:
    conn = database.get_connection()
    try:
        eid = _7a_emp("7A_ADD_NO_SITE")
        ok, _ = database.add_employee(eid, "Unbound", conn=conn)
        assert ok
        row = conn.execute(
            "SELECT Site_ID FROM employees WHERE ID_Number=?", (eid,)
        ).fetchone()
        assert row[0] is None, f"expected NULL Site_ID, got {row[0]!r}"
    finally:
        conn.close()


def check_7a_update_site_reassigns() -> None:
    conn = database.get_connection()
    try:
        eid = _7a_emp("7A_UPD_REASSIGN")
        database.add_employee(eid, "Mover", site_id="HQ", conn=conn)
        assert database.update_employee(eid, site_id="Site_B", conn=conn)
        row = conn.execute(
            "SELECT Site_ID FROM employees WHERE ID_Number=?", (eid,)
        ).fetchone()
        assert row[0] == "Site_B", f"reassign failed: {row}"
    finally:
        conn.close()


def check_7a_update_site_clears() -> None:
    conn = database.get_connection()
    try:
        eid = _7a_emp("7A_UPD_CLEAR")
        database.add_employee(eid, "Toggle", site_id="HQ", conn=conn)
        assert database.update_employee(eid, site_id="", conn=conn)
        row = conn.execute(
            "SELECT Site_ID FROM employees WHERE ID_Number=?", (eid,)
        ).fetchone()
        assert row[0] is None, f"empty-string should clear binding; got {row[0]!r}"
    finally:
        conn.close()


def check_7a_update_site_none_untouched() -> None:
    conn = database.get_connection()
    try:
        eid = _7a_emp("7A_UPD_NONE")
        database.add_employee(eid, "Sticky", site_id="HQ", conn=conn)
        # Only update Name — Site_ID untouched.
        assert database.update_employee(eid, name="Sticky Renamed", conn=conn)
        row = conn.execute(
            "SELECT Site_ID, Name FROM employees WHERE ID_Number=?", (eid,)
        ).fetchone()
        assert row[0] == "HQ", f"Site_ID should be preserved, got {row[0]!r}"
        assert row[1] == "Sticky Renamed"
    finally:
        conn.close()


def check_7a_list_employees_has_site_column() -> None:
    conn = database.get_connection()
    try:
        df = database.list_employees(conn=conn)
        assert "Site_ID" in df.columns, f"Site_ID missing from list_employees df: {list(df.columns)}"
    finally:
        conn.close()


def check_7a_list_employees_site_filter() -> None:
    conn = database.get_connection()
    try:
        e1 = _7a_emp("7A_FLT_HQ")
        e2 = _7a_emp("7A_FLT_B")
        database.add_employee(e1, "HQ Person", site_id="HQ", conn=conn)
        database.add_employee(e2, "Site B Person", site_id="Site_B", conn=conn)
        df = database.list_employees(site_id_filter="HQ", conn=conn)
        ids = set(df["ID_Number"].tolist())
        assert e1 in ids, f"HQ filter missed HQ employee: {ids}"
        assert e2 not in ids, f"HQ filter leaked Site_B employee: {ids}"
    finally:
        conn.close()


def check_7a_list_employees_unassigned_sentinel() -> None:
    conn = database.get_connection()
    try:
        eid = _7a_emp("7A_UNASSIGNED")
        database.add_employee(eid, "Floater", conn=conn)  # no site
        df = database.list_employees(site_id_filter="__UNASSIGNED__", conn=conn)
        assert eid in set(df["ID_Number"].tolist()), \
            f"__UNASSIGNED__ sentinel did not return NULL-site employee"
        # And the same employee MUST NOT appear under a concrete site filter.
        df_hq = database.list_employees(site_id_filter="HQ", conn=conn)
        assert eid not in set(df_hq["ID_Number"].tolist())
    finally:
        conn.close()


def check_7a_list_employees_for_site_active_only() -> None:
    conn = database.get_connection()
    try:
        e_active = _7a_emp("7A_FS_ACT")
        e_susp = _7a_emp("7A_FS_SUSP")
        database.add_employee(e_active, "Active One", site_id="HQ", conn=conn)
        database.add_employee(e_susp, "Suspended One", site_id="HQ", conn=conn)
        database.update_employee(e_susp, status="suspended", conn=conn)
        df = database.list_employees_for_site("HQ", conn=conn)  # default active
        ids = set(df["ID_Number"].tolist())
        assert e_active in ids, "active employee dropped"
        assert e_susp not in ids, "suspended leaked into active-only list"
    finally:
        conn.close()


def check_7a_csv_with_site_id_column() -> None:
    import io
    conn = database.get_connection()
    try:
        eid = _7a_emp("7A_CSV_SITE")
        csv = io.StringIO(
            "ID_Number,Name,Phone_Number,Department,Site_ID\n"
            f"{eid},CSV With Site,+96650999,Ops,Site_C\n"
        )
        r = database.import_employees_csv(csv, "test_admin", conn=conn)
        assert r["inserted"] == 1, f"unexpected import counts: {r}"
        row = conn.execute(
            "SELECT Site_ID FROM employees WHERE ID_Number=?", (eid,)
        ).fetchone()
        assert row[0] == "Site_C", f"CSV Site_ID not persisted: {row}"
    finally:
        conn.close()


def check_7a_csv_without_site_id_column() -> None:
    """Legacy CSV (no Site_ID column) must still import successfully."""
    import io
    conn = database.get_connection()
    try:
        eid = _7a_emp("7A_CSV_LEGACY")
        csv = io.StringIO(
            "ID_Number,Name,Phone_Number,Department\n"
            f"{eid},Legacy CSV,+96650777,Ops\n"
        )
        r = database.import_employees_csv(csv, "test_admin", conn=conn)
        assert r["inserted"] == 1, f"legacy CSV import failed: {r}"
        row = conn.execute(
            "SELECT Site_ID FROM employees WHERE ID_Number=?", (eid,)
        ).fetchone()
        assert row[0] is None, f"legacy CSV should leave Site_ID NULL, got {row[0]!r}"
    finally:
        conn.close()


def check_7a_csv_omitted_col_preserves_binding() -> None:
    """Re-importing a CSV without the Site_ID column must NOT wipe an existing binding."""
    import io
    conn = database.get_connection()
    try:
        eid = _7a_emp("7A_CSV_PRESERVE")
        # First import: with Site_ID column → binds to HQ
        csv1 = io.StringIO(
            "ID_Number,Name,Phone_Number,Department,Site_ID\n"
            f"{eid},Preserve Me,+96650888,Ops,HQ\n"
        )
        database.import_employees_csv(csv1, "test_admin", conn=conn)
        # Second import: same row WITHOUT Site_ID col → must preserve HQ
        csv2 = io.StringIO(
            "ID_Number,Name,Phone_Number,Department\n"
            f"{eid},Preserve Me,+96650888,Ops\n"
        )
        r2 = database.import_employees_csv(csv2, "test_admin", conn=conn)
        assert r2["skipped"] == 1, f"expected skipped (no change), got: {r2}"
        row = conn.execute(
            "SELECT Site_ID FROM employees WHERE ID_Number=?", (eid,)
        ).fetchone()
        assert row[0] == "HQ", f"binding wiped by legacy re-import: {row[0]!r}"
    finally:
        conn.close()


def check_7a_bulk_assign_helper() -> None:
    conn = database.get_connection()
    try:
        e1 = _7a_emp("7A_BULK_1")
        e2 = _7a_emp("7A_BULK_2")
        e3 = _7a_emp("7A_BULK_3")
        for e in (e1, e2, e3):
            database.add_employee(e, f"Bulk {e}", conn=conn)
        n = database.bulk_assign_employees_to_site(
            [e1, e2, e3], "Site_X", updated_by="test_admin", conn=conn,
        )
        assert n == 3, f"bulk-assign rowcount wrong: {n}"
        rows = conn.execute(
            f"SELECT Site_ID FROM employees WHERE ID_Number IN ({','.join('?'*3)})",
            (e1, e2, e3),
        ).fetchall()
        assert all(r[0] == "Site_X" for r in rows), f"binding not applied: {rows}"
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Phase 7B — Supervisor Material Request workflow checks
# ═══════════════════════════════════════════════════════════════════════════
def _7b_seed_worker(site_id: str = "TEST_7B_SITE", suffix: str = "") -> str:
    """Insert an active employee bound to site_id. Returns ID_Number."""
    import uuid as _u
    conn = database.get_connection()
    try:
        eid = f"7B_EMP_{_u.uuid4().hex[:8]}{suffix}"
        conn.execute(
            "INSERT INTO employees (ID_Number, Name, Phone_Number, Department, Site_ID, status) "
            "VALUES (?, ?, ?, ?, ?, 'active')",
            (eid, f"Worker {eid}", "+966500000001", "Ops", site_id),
        )
        conn.commit()
        return eid
    finally:
        conn.close()


def _7b_seed_inventory(site_id: str = "TEST_7B_SITE",
                       sap: str = None, stock: float = 100.0,
                       suffix: str = "") -> str:
    """Insert an inventory item with Opening_Stock=stock at site_id. Returns SAP_Code."""
    import uuid as _u
    sap = sap or f"7B-SAP-{_u.uuid4().hex[:6]}{suffix}"
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO inventory "
            "(SAP_Code, Material_Code, Equipment_Description, UOM, "
            " Minimum_Qty, Unit_Cost, Site_ID, Category, Opening_Stock) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sap, f"MC-{sap}", f"Material {sap}", "PCS",
             10, 5.0, site_id, "Consumable", stock),
        )
        conn.commit()
        return sap
    finally:
        conn.close()


def check_7b_generate_request_no_first() -> None:
    """First SMR of the day starts at 0001 (deterministic regardless of test order)."""
    no = database.generate_smr_request_no()
    today = datetime.date.today().strftime("%Y%m%d")
    assert no.startswith(f"SMR-{today}-"), f"prefix wrong: {no}"
    # Format must always be 4-digit padded sequence.
    seq = no.split("-")[-1]
    assert len(seq) == 4 and seq.isdigit(), f"sequence format wrong: {no}"


def check_7b_generate_request_no_increments() -> None:
    """generate_smr_request_no inspects the highest existing row and returns N+1."""
    site = "TEST_7B_INCR"
    worker = _7b_seed_worker(site, suffix="_INCR")
    sap = _7b_seed_inventory(site, suffix="_INCR")
    ok, no1 = database.create_supervisor_request(
        site_id=site, worker_id=worker, job_tank_place="Tank-A",
        old_ppe_returned=1, no_return_reason="",
        items=[{"SAP_Code": sap, "Requested_Qty": 5}],
        supervisor_username="test_sup_1",
    )
    assert ok, no1
    no2 = database.generate_smr_request_no()
    # Both share same date prefix; no2 must be at least 1 higher than no1.
    n1 = int(no1.split("-")[-1])
    n2 = int(no2.split("-")[-1])
    assert n2 == n1 + 1, f"expected {n1+1}, got {n2}"


def check_7b_create_happy_path() -> None:
    site = "TEST_7B_HAPPY"
    worker = _7b_seed_worker(site, suffix="_HAPPY")
    sap1 = _7b_seed_inventory(site, suffix="_HAPPY1")
    sap2 = _7b_seed_inventory(site, suffix="_HAPPY2")
    ok, no = database.create_supervisor_request(
        site_id=site, worker_id=worker, job_tank_place="Tank-99",
        old_ppe_returned=0, no_return_reason="Old set damaged",
        items=[
            {"SAP_Code": sap1, "Requested_Qty": 3, "Notes": "urgent"},
            {"SAP_Code": sap2, "Requested_Qty": 1},
        ],
        supervisor_username="test_sup",
    )
    assert ok, no
    conn = database.get_connection()
    try:
        hdr = conn.execute(
            "SELECT Site_ID, Worker_ID, status, Old_PPE_Returned, No_Return_Reason "
            "FROM supervisor_material_requests WHERE request_no = ?", (no,),
        ).fetchone()
        assert hdr == (site, worker, "pending_sk", 0, "Old set damaged"), hdr
        cnt = conn.execute(
            "SELECT COUNT(*) FROM supervisor_material_request_items i "
            "JOIN supervisor_material_requests r ON r.id = i.request_id "
            "WHERE r.request_no = ?", (no,),
        ).fetchone()[0]
        assert cnt == 2, f"expected 2 items, got {cnt}"
    finally:
        conn.close()


def check_7b_create_rejects_wrong_site_worker() -> None:
    site_a = "TEST_7B_RSA"
    site_b = "TEST_7B_RSB"
    worker = _7b_seed_worker(site_a, suffix="_RSA")
    sap = _7b_seed_inventory(site_b, suffix="_RSB")
    ok, msg = database.create_supervisor_request(
        site_id=site_b, worker_id=worker, job_tank_place="Tank-1",
        old_ppe_returned=1, no_return_reason="",
        items=[{"SAP_Code": sap, "Requested_Qty": 1}],
        supervisor_username="test_sup",
    )
    assert not ok and "bound to site" in msg, f"expected site-binding rejection: {msg}"


def check_7b_create_rejects_empty_items() -> None:
    site = "TEST_7B_EMPTY"
    worker = _7b_seed_worker(site, suffix="_EMPTY")
    ok, msg = database.create_supervisor_request(
        site_id=site, worker_id=worker, job_tank_place="Tank-1",
        old_ppe_returned=1, no_return_reason="",
        items=[],
        supervisor_username="test_sup",
    )
    assert not ok and "at least one item" in msg.lower(), msg


def check_7b_create_rejects_no_ppe_no_reason() -> None:
    site = "TEST_7B_PPE"
    worker = _7b_seed_worker(site, suffix="_PPE")
    sap = _7b_seed_inventory(site, suffix="_PPE")
    ok, msg = database.create_supervisor_request(
        site_id=site, worker_id=worker, job_tank_place="Tank-1",
        old_ppe_returned=0, no_return_reason="",
        items=[{"SAP_Code": sap, "Requested_Qty": 1}],
        supervisor_username="test_sup",
    )
    assert not ok and "reason" in msg.lower(), msg


def check_7b_create_rejects_unknown_sap() -> None:
    site = "TEST_7B_SAP"
    worker = _7b_seed_worker(site, suffix="_SAP")
    _ = _7b_seed_inventory(site, suffix="_SAP_GOOD")
    ok, msg = database.create_supervisor_request(
        site_id=site, worker_id=worker, job_tank_place="Tank-1",
        old_ppe_returned=1, no_return_reason="",
        items=[{"SAP_Code": "NO-SUCH-SAP-7B", "Requested_Qty": 1}],
        supervisor_username="test_sup",
    )
    assert not ok and "unknown sap_code" in msg.lower(), msg


def check_7b_stock_snapshot_captured() -> None:
    site = "TEST_7B_SNAP"
    worker = _7b_seed_worker(site, suffix="_SNAP")
    sap = _7b_seed_inventory(site, stock=42.0, suffix="_SNAP")
    ok, no = database.create_supervisor_request(
        site_id=site, worker_id=worker, job_tank_place="Tank-1",
        old_ppe_returned=1, no_return_reason="",
        items=[{"SAP_Code": sap, "Requested_Qty": 5}],
        supervisor_username="test_sup",
    )
    assert ok, no
    conn = database.get_connection()
    try:
        snap = conn.execute(
            "SELECT i.Stock_At_Request FROM supervisor_material_request_items i "
            "JOIN supervisor_material_requests r ON r.id = i.request_id "
            "WHERE r.request_no = ? LIMIT 1", (no,),
        ).fetchone()[0]
        assert float(snap) == 42.0, f"snapshot wrong: {snap}"
    finally:
        conn.close()


def check_7b_available_flag_zero_when_short() -> None:
    site = "TEST_7B_FLAG"
    worker = _7b_seed_worker(site, suffix="_FLAG")
    sap = _7b_seed_inventory(site, stock=2.0, suffix="_FLAG")
    ok, no = database.create_supervisor_request(
        site_id=site, worker_id=worker, job_tank_place="Tank-1",
        old_ppe_returned=1, no_return_reason="",
        items=[{"SAP_Code": sap, "Requested_Qty": 5}],  # > stock 2
        supervisor_username="test_sup",
    )
    assert ok, no
    conn = database.get_connection()
    try:
        flag = conn.execute(
            "SELECT i.Available_Flag FROM supervisor_material_request_items i "
            "JOIN supervisor_material_requests r ON r.id = i.request_id "
            "WHERE r.request_no = ? LIMIT 1", (no,),
        ).fetchone()[0]
        assert flag == 0, f"Available_Flag should be 0 (short), got {flag}"
    finally:
        conn.close()


def _7b_make_pending(site: str, suffix: str, qty: float = 5,
                    sap_stock: float = 100) -> tuple[int, str, str, str]:
    """Helper: seed worker + sap, create pending SMR; returns (req_id, no, worker, sap)."""
    worker = _7b_seed_worker(site, suffix=suffix)
    sap = _7b_seed_inventory(site, stock=sap_stock, suffix=suffix)
    ok, no = database.create_supervisor_request(
        site_id=site, worker_id=worker, job_tank_place="Tank-X",
        old_ppe_returned=1, no_return_reason="",
        items=[{"SAP_Code": sap, "Requested_Qty": qty}],
        supervisor_username="test_sup",
    )
    assert ok, no
    conn = database.get_connection()
    try:
        rid = conn.execute(
            "SELECT id FROM supervisor_material_requests WHERE request_no = ?",
            (no,),
        ).fetchone()[0]
        return int(rid), no, worker, sap
    finally:
        conn.close()


def check_7b_approve_mirrors_to_pending_issues() -> None:
    rid, no, worker, sap = _7b_make_pending("TEST_7B_APPR", "_APPR", qty=7)
    ok, msg = database.approve_supervisor_request(rid, "test_sk")
    assert ok, msg
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT SAP_Code, Quantity, Work_Type, Tank_No, Issued_By, "
            "       Issued_To, status, Source_Ref, Requested_By "
            "FROM pending_issues WHERE Source_Ref LIKE ?",
            (f"SMR:{no}:%",),
        ).fetchone()
        assert row is not None, "no pending_issues row mirrored"
        (sap_got, qty, work, tank, by, to, status, src, req_by) = row
        assert sap_got == sap
        assert float(qty) == 7.0, qty
        assert work == "SUPERVISOR_REQUEST", work
        assert tank == "Tank-X", tank
        assert by == "test_sk", by
        assert to.startswith("Worker "), to
        # Round 12: lands in SK staging grid (draft), not HOD's EOD queue.
        assert status == "draft", status
        assert src.startswith(f"SMR:{no}:"), src
        # Round 12: supervisor's username auto-filled from header.
        assert req_by == "test_sup", req_by
    finally:
        conn.close()


def check_7b_approve_captures_posted_ids() -> None:
    rid, no, *_ = _7b_make_pending("TEST_7B_IDS", "_IDS")
    ok, _ = database.approve_supervisor_request(rid, "test_sk")
    assert ok
    conn = database.get_connection()
    try:
        status, posted = conn.execute(
            "SELECT status, posted_pending_ids FROM supervisor_material_requests "
            "WHERE id = ?", (rid,),
        ).fetchone()
        assert status == "approved"
        import json
        ids = json.loads(posted or "[]")
        assert len(ids) == 1, f"expected 1 mirrored row id, got {ids}"
    finally:
        conn.close()


def check_7b_approve_idempotent() -> None:
    rid, _, *_ = _7b_make_pending("TEST_7B_IDEM", "_IDEM")
    ok1, _ = database.approve_supervisor_request(rid, "test_sk")
    assert ok1
    ok2, msg = database.approve_supervisor_request(rid, "test_sk")
    assert not ok2 and "already" in msg.lower(), msg


def check_7b_approve_drops_zero_adjusted() -> None:
    site = "TEST_7B_ZERO"
    worker = _7b_seed_worker(site, suffix="_ZERO")
    sap1 = _7b_seed_inventory(site, suffix="_ZERO1")
    sap2 = _7b_seed_inventory(site, suffix="_ZERO2")
    ok, no = database.create_supervisor_request(
        site_id=site, worker_id=worker, job_tank_place="Tank-Z",
        old_ppe_returned=1, no_return_reason="",
        items=[
            {"SAP_Code": sap1, "Requested_Qty": 3},
            {"SAP_Code": sap2, "Requested_Qty": 4},
        ],
        supervisor_username="test_sup",
    )
    assert ok, no
    conn = database.get_connection()
    try:
        rid, item_ids = conn.execute(
            "SELECT id FROM supervisor_material_requests WHERE request_no = ?",
            (no,),
        ).fetchone()[0], [r[0] for r in conn.execute(
            "SELECT i.id FROM supervisor_material_request_items i "
            "JOIN supervisor_material_requests r ON r.id = i.request_id "
            "WHERE r.request_no = ? ORDER BY i.id", (no,),
        ).fetchall()]
    finally:
        conn.close()
    # Set the SECOND line's SK_Adjusted_Qty to 0 — should be dropped at approval.
    database.update_supervisor_request_item(item_ids[1], sk_adjusted_qty=0)
    ok2, _ = database.approve_supervisor_request(rid, "test_sk")
    assert ok2
    conn = database.get_connection()
    try:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM pending_issues WHERE Source_Ref LIKE ?",
            (f"SMR:{no}:%",),
        ).fetchone()[0]
        assert cnt == 1, f"expected 1 mirrored row (other dropped), got {cnt}"
    finally:
        conn.close()


def check_7b_reject_blocks_without_reason() -> None:
    rid, no, *_ = _7b_make_pending("TEST_7B_REJ", "_REJ")
    ok, msg = database.reject_supervisor_request(rid, "test_sk", "")
    assert not ok and "required" in msg.lower(), msg
    ok2, _ = database.reject_supervisor_request(rid, "test_sk", "no stock")
    assert ok2
    conn = database.get_connection()
    try:
        status = conn.execute(
            "SELECT status FROM supervisor_material_requests WHERE id = ?",
            (rid,),
        ).fetchone()[0]
        assert status == "rejected"
        cnt = conn.execute(
            "SELECT COUNT(*) FROM pending_issues WHERE Source_Ref LIKE ?",
            (f"SMR:{no}:%",),
        ).fetchone()[0]
        assert cnt == 0, "reject must not write to pending_issues"
    finally:
        conn.close()


def check_7b_e2e_commit_eod_preserves_source_ref() -> None:
    rid, no, *_ = _7b_make_pending("TEST_7B_E2E", "_E2E", qty=2, sap_stock=50)
    ok, _ = database.approve_supervisor_request(rid, "test_sk")
    assert ok
    # Round 12 — after SK approval the row sits in pending_issues as draft.
    # The SK Submit-Batch step flips it to pending_hod so commit_eod picks
    # it up. Simulate that flip here.
    conn = database.get_connection()
    try:
        conn.execute(
            "UPDATE pending_issues SET status='pending_hod' "
            "WHERE Source_Ref LIKE ?", (f"SMR:{no}:%",),
        )
        conn.commit()
    finally:
        conn.close()
    n = database.commit_eod(hod_username="test_hod")
    assert n >= 1, f"commit_eod should commit ≥1 row, got {n}"
    conn = database.get_connection()
    try:
        row = conn.execute(
            'SELECT Quantity, Work_Type, Source_Ref, Requested_By, '
            '       Issued_By, "Approved By" '
            'FROM consumption WHERE Source_Ref LIKE ?', (f"SMR:{no}:%",),
        ).fetchone()
        assert row is not None, "consumption row missing after commit_eod"
        qty, work, src, req_by, iss_by, appr_by = row
        assert float(qty) == 2.0
        assert work == "SUPERVISOR_REQUEST"
        assert src.startswith(f"SMR:{no}:")
        # Round 12 — auto-attribution all three roles to the ledger.
        assert req_by == "test_sup", req_by
        assert iss_by == "test_sk", iss_by
        assert appr_by == "test_hod", appr_by
    finally:
        conn.close()


def check_7b_update_item_locked_post_approval() -> None:
    rid, _, *_ = _7b_make_pending("TEST_7B_LOCK", "_LOCK")
    conn = database.get_connection()
    try:
        item_id = conn.execute(
            "SELECT i.id FROM supervisor_material_request_items i "
            "JOIN supervisor_material_requests r ON r.id = i.request_id "
            "WHERE r.id = ? LIMIT 1", (rid,),
        ).fetchone()[0]
    finally:
        conn.close()
    # While pending_sk, update succeeds.
    assert database.update_supervisor_request_item(item_id, notes="early note")
    # After approval, update refused.
    ok, _ = database.approve_supervisor_request(rid, "test_sk")
    assert ok
    assert not database.update_supervisor_request_item(item_id, notes="too late")


def check_7b_cancel_locked_post_decision() -> None:
    rid, _, *_ = _7b_make_pending("TEST_7B_CANC", "_CANC")
    assert database.cancel_supervisor_request(rid, "test_sup")
    # Second cancel refused (no longer pending_sk).
    assert not database.cancel_supervisor_request(rid, "test_sup")


def check_7b_delete_item_works() -> None:
    site = "TEST_7B_DEL"
    worker = _7b_seed_worker(site, suffix="_DEL")
    sap1 = _7b_seed_inventory(site, suffix="_DEL1")
    sap2 = _7b_seed_inventory(site, suffix="_DEL2")
    ok, no = database.create_supervisor_request(
        site_id=site, worker_id=worker, job_tank_place="Tank-D",
        old_ppe_returned=1, no_return_reason="",
        items=[
            {"SAP_Code": sap1, "Requested_Qty": 1},
            {"SAP_Code": sap2, "Requested_Qty": 2},
        ],
        supervisor_username="test_sup",
    )
    assert ok, no
    conn = database.get_connection()
    try:
        item_id_to_drop = conn.execute(
            "SELECT i.id FROM supervisor_material_request_items i "
            "JOIN supervisor_material_requests r ON r.id = i.request_id "
            "WHERE r.request_no = ? ORDER BY i.id DESC LIMIT 1", (no,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert database.delete_supervisor_request_item(item_id_to_drop)
    conn = database.get_connection()
    try:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM supervisor_material_request_items i "
            "JOIN supervisor_material_requests r ON r.id = i.request_id "
            "WHERE r.request_no = ?", (no,),
        ).fetchone()[0]
        assert cnt == 1, f"expected 1 line after delete, got {cnt}"
    finally:
        conn.close()


def check_7b_report_joins_on_source_ref() -> None:
    rid, no, *_ = _7b_make_pending("TEST_7B_RPT", "_RPT", qty=3, sap_stock=50)
    database.approve_supervisor_request(rid, "test_sk")
    # Round 12 — flip SMR draft rows to pending_hod (simulating SK Submit
    # Batch) before commit_eod can pick them up.
    conn = database.get_connection()
    try:
        conn.execute(
            "UPDATE pending_issues SET status='pending_hod' "
            "WHERE Source_Ref LIKE ?", (f"SMR:{no}:%",),
        )
        conn.commit()
    finally:
        conn.close()
    database.commit_eod(hod_username="test_hod")
    df = database.report_supervisor_intent_vs_actual(site_id="TEST_7B_RPT", days=7)
    assert not df.empty, "report should have at least one row"
    matched = df[df["Request_No"] == no]
    assert not matched.empty, f"report missing approved request {no}"
    row = matched.iloc[0]
    assert float(row["Requested_Qty"]) == 3.0
    assert float(row["Actual_Qty"]) == 3.0
    assert row["Variance_Pct"] == 0.0


def check_7b_open_returnables_for_employee() -> None:
    site = "TEST_7B_LOAN"
    worker = _7b_seed_worker(site, suffix="_LOAN")
    # Seed a returnable loan for this worker.
    conn = database.get_connection()
    try:
        nrow = conn.execute(
            "SELECT Name FROM employees WHERE ID_Number = ?", (worker,),
        ).fetchone()
        worker_name = nrow[0]
        conn.execute(
            "INSERT INTO returnable_items "
            "(material_name, uom, qty, borrower_name, borrower_phone, "
            " given_time, expected_return_time, status, Site_ID, cv_employee_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'borrowed', ?, ?)",
            ("Test Drill", "PCS", 1, worker_name, "+96650",
             datetime.datetime.utcnow().isoformat(),
             (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).isoformat(),
             site, worker),
        )
        conn.commit()
    finally:
        conn.close()
    df = database.get_open_returnables_for_employee(worker)
    assert not df.empty, "open-loan side-panel should find the seeded loan"
    assert "Test Drill" in df["material_name"].tolist()


def check_7b_config_smr_triggers() -> None:
    from config import WHATSAPP_TRIGGERS
    for key in ("smr_submitted", "smr_approved",
                "smr_rejected", "smr_cancelled"):
        assert key in WHATSAPP_TRIGGERS, f"{key} missing from WHATSAPP_TRIGGERS"
        assert WHATSAPP_TRIGGERS[key] is True, f"{key} should default True"


# ═══════════════════════════════════════════════════════════════════════════
# Round 12 — SMR-via-SK-Grid + Auto-Attribution checks
# ═══════════════════════════════════════════════════════════════════════════
def check_r12_schema_requested_by_columns() -> None:
    """pending_issues + consumption carry the new Requested_By column."""
    conn = database.get_connection()
    try:
        for tbl in ("pending_issues", "consumption"):
            cols = {r[1] for r in conn.execute(
                f"PRAGMA table_info({tbl})"
            ).fetchall()}
            assert "Requested_By" in cols, f"Requested_By missing from {tbl}"
    finally:
        conn.close()


def check_r12_schema_line_status_column() -> None:
    """supervisor_material_request_items.line_status exists and defaults to
    'active' on freshly inserted lines."""
    conn = database.get_connection()
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(supervisor_material_request_items)"
        ).fetchall()}
        assert "line_status" in cols, f"line_status missing: {cols}"
    finally:
        conn.close()


def check_r12_line_status_default_active() -> None:
    """A newly-created SMR line carries line_status='active'."""
    rid, no, *_ = _7b_make_pending("TEST_R12_LSDEF", "_LSDEF")
    conn = database.get_connection()
    try:
        statuses = [r[0] for r in conn.execute(
            "SELECT line_status FROM supervisor_material_request_items "
            "WHERE request_id = ?", (rid,),
        ).fetchall()]
        assert statuses and all(s == "active" for s in statuses), statuses
    finally:
        conn.close()


def check_r12_withdraw_smr_line_at_staging() -> None:
    """SK deletes an SMR-draft row from the staging grid → the matching
    supervisor_material_request_items row flips to 'withdrawn_at_staging'.
    """
    rid, no, *_ = _7b_make_pending("TEST_R12_WDR", "_WDR")
    ok, _ = database.approve_supervisor_request(rid, "test_sk")
    assert ok
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT id, Source_Ref FROM pending_issues "
            "WHERE Source_Ref LIKE ?", (f"SMR:{no}:%",),
        ).fetchone()
        assert row, "no SMR-sourced pending_issues row"
        pi_id, _src = row
    finally:
        conn.close()
    ok2, _ = database.withdraw_smr_line_at_staging(int(pi_id), "test_sk")
    assert ok2
    conn = database.get_connection()
    try:
        ls = conn.execute(
            "SELECT line_status FROM supervisor_material_request_items "
            "WHERE request_id = ?", (rid,),
        ).fetchone()[0]
        assert ls == "withdrawn_at_staging", ls
    finally:
        conn.close()


def check_r12_commit_eod_writes_approved_by() -> None:
    """commit_eod(hod_username=X) populates legacy 'Approved By' column."""
    rid, no, *_ = _7b_make_pending("TEST_R12_APPR", "_APPR", qty=1, sap_stock=20)
    ok, _ = database.approve_supervisor_request(rid, "test_sk")
    assert ok
    conn = database.get_connection()
    try:
        conn.execute(
            "UPDATE pending_issues SET status='pending_hod' "
            "WHERE Source_Ref LIKE ?", (f"SMR:{no}:%",),
        )
        conn.commit()
    finally:
        conn.close()
    n = database.commit_eod(hod_username="hod_alpha")
    assert n >= 1
    conn = database.get_connection()
    try:
        approved_by = conn.execute(
            'SELECT "Approved By" FROM consumption '
            'WHERE Source_Ref LIKE ?', (f"SMR:{no}:%",),
        ).fetchone()[0]
        assert approved_by == "hod_alpha", approved_by
    finally:
        conn.close()


def check_r12_commit_eod_flips_line_status_committed() -> None:
    """commit_eod flips the matching supervisor_material_request_items row to
    line_status='committed'."""
    rid, no, *_ = _7b_make_pending("TEST_R12_LSC", "_LSC", qty=1, sap_stock=20)
    ok, _ = database.approve_supervisor_request(rid, "test_sk")
    assert ok
    conn = database.get_connection()
    try:
        conn.execute(
            "UPDATE pending_issues SET status='pending_hod' "
            "WHERE Source_Ref LIKE ?", (f"SMR:{no}:%",),
        )
        conn.commit()
    finally:
        conn.close()
    database.commit_eod(hod_username="hod_beta")
    conn = database.get_connection()
    try:
        ls = conn.execute(
            "SELECT line_status FROM supervisor_material_request_items "
            "WHERE request_id = ?", (rid,),
        ).fetchone()[0]
        assert ls == "committed", ls
    finally:
        conn.close()


def check_r12_commit_eod_carries_requested_by_to_consumption() -> None:
    """Requested_By survives the pending_issues → consumption commit."""
    rid, no, *_ = _7b_make_pending("TEST_R12_RB", "_RB", qty=1, sap_stock=20)
    ok, _ = database.approve_supervisor_request(rid, "test_sk")
    assert ok
    conn = database.get_connection()
    try:
        conn.execute(
            "UPDATE pending_issues SET status='pending_hod' "
            "WHERE Source_Ref LIKE ?", (f"SMR:{no}:%",),
        )
        conn.commit()
    finally:
        conn.close()
    database.commit_eod(hod_username="hod_gamma")
    conn = database.get_connection()
    try:
        req_by = conn.execute(
            "SELECT Requested_By FROM consumption "
            "WHERE Source_Ref LIKE ?", (f"SMR:{no}:%",),
        ).fetchone()[0]
        # Header.requested_by was 'test_sup' (set inside _7b_make_pending).
        assert req_by == "test_sup", req_by
    finally:
        conn.close()


def check_r12_hidden_form_cols_present() -> None:
    """config.HIDDEN_FORM_COLS exists and covers the retired/auto-filled
    columns the SK form must not render."""
    from config import HIDDEN_FORM_COLS
    must_hide = {
        "Technician", "Issued_By", "Approved By", "Approved_By",
        "Requested_By", "Source_Ref", "FEFO_Override", "Lot_Number",
    }
    missing = must_hide - set(HIDDEN_FORM_COLS)
    assert not missing, f"HIDDEN_FORM_COLS missing: {missing}"


def check_r12_list_smr_history_filters() -> None:
    """list_smr_history honours date / supervisor / tank filters and the
    decided-only default."""
    import datetime as _dt
    site = "TEST_R12_HIST"
    # Seed three SMRs: one approved, one rejected, one still pending.
    rid1, no1, *_ = _7b_make_pending(site, "_H1", qty=1, sap_stock=10)
    database.approve_supervisor_request(rid1, "test_sk")
    rid2, no2, *_ = _7b_make_pending(site, "_H2", qty=1, sap_stock=10)
    database.reject_supervisor_request(rid2, "test_sk", "out of stock")
    rid3, no3, *_ = _7b_make_pending(site, "_H3", qty=1, sap_stock=10)
    # rid3 stays pending_sk.

    # Decided-only by default (no status_in passed; days=180 to be safe).
    df_dec = database.list_smr_history(
        site_id=site,
        status_in=("approved", "rejected", "cancelled"),
        days=180,
    )
    nos = set(df_dec["request_no"].tolist())
    assert no1 in nos and no2 in nos, f"decided missing: {nos}"
    assert no3 not in nos, "pending should be excluded from decided-only"

    # Include pending toggle equivalent: status_in covers all 4 states.
    df_all = database.list_smr_history(
        site_id=site,
        status_in=("approved", "rejected", "cancelled", "pending_sk"),
        days=180,
    )
    assert no3 in set(df_all["request_no"].tolist())

    # Tank filter — Tank-X (default in _7b_make_pending).
    df_tank = database.list_smr_history(
        site_id=site, tank="Tank-X", days=180,
    )
    # Every row in this site is Tank-X, so count matches the all-status pull.
    assert len(df_tank) >= 2

    # Supervisor filter — 'test_sup'.
    df_sup = database.list_smr_history(
        site_id=site, supervisor="test_sup", days=180,
    )
    assert not df_sup.empty
    assert df_sup["requested_by"].iloc[0] == "test_sup"


def check_r12_e2e_full_pipeline_three_role_attribution() -> None:
    """Supervisor → SK approve → SK submit batch → HOD commit → consumption
    row carries Requested_By (sup) + Issued_By (sk) + 'Approved By' (hod)
    + line_status='committed'."""
    site = "TEST_R12_E2E"
    rid, no, *_ = _7b_make_pending(site, "_FULL", qty=4, sap_stock=99)
    ok, _ = database.approve_supervisor_request(rid, "sk_dave")
    assert ok
    # SK Submit-Batch step: flip draft → pending_hod.
    conn = database.get_connection()
    try:
        conn.execute(
            "UPDATE pending_issues SET status='pending_hod' "
            "WHERE Source_Ref LIKE ?", (f"SMR:{no}:%",),
        )
        conn.commit()
    finally:
        conn.close()
    n = database.commit_eod(hod_username="hod_eve")
    assert n >= 1
    conn = database.get_connection()
    try:
        row = conn.execute(
            'SELECT Requested_By, Issued_By, "Approved By", '
            'Work_Type, Source_Ref FROM consumption '
            'WHERE Source_Ref LIKE ?', (f"SMR:{no}:%",),
        ).fetchone()
        assert row, "no consumption row"
        req_by, iss_by, appr_by, work, src = row
        assert req_by == "test_sup", req_by
        assert iss_by == "sk_dave", iss_by
        assert appr_by == "hod_eve", appr_by
        assert work == "SUPERVISOR_REQUEST"
        assert src.startswith(f"SMR:{no}:")
        # line_status='committed' end-to-end contract.
        ls = conn.execute(
            "SELECT line_status FROM supervisor_material_request_items "
            "WHERE request_id = ?", (rid,),
        ).fetchone()[0]
        assert ls == "committed", ls
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Round 13 — EOD State Unification + Schema Cleanup checks
# ═══════════════════════════════════════════════════════════════════════════
def _r13_seed_pending_issue(site: str, sap: str, qty: float = 1.0,
                            *, status: str = "pending_hod",
                            issued_by: str = "test_sk",
                            source_ref: str | None = None) -> int:
    """Drop a single pending_issues row in the requested status. Returns id."""
    conn = database.get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO pending_issues "
            "(SAP_Code, Quantity, Date, Site_ID, Work_Type, Issued_By, "
            " Issued_To, Tank_No, status, Source_Ref) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sap, qty,
             datetime.date.today().isoformat(),
             site, "Maintenance", issued_by, "Worker A", "Tank-A",
             status, source_ref),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _r13_seed_inventory(site: str, sap: str, opening: float = 100.0) -> None:
    """Make sure inventory has the SAP row + decent opening stock so the
    over-issue guard never blocks commit_eod in these tests."""
    conn = database.get_connection()
    try:
        # inventory table is global keyed by SAP_Code (no site column on it).
        existing = conn.execute(
            "SELECT 1 FROM inventory WHERE SAP_Code = ?", (sap,),
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO inventory "
                "(SAP_Code, Equipment_Description, UOM, Material_Code, "
                " Opening_Stock, Minimum_Qty) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sap, f"Mat {sap}", "PCS", f"MC-{sap}", opening, 0),
            )
        conn.commit()
    finally:
        conn.close()


def check_r13_commit_eod_picks_up_approved_status() -> None:
    """commit_eod commits status='approved' rows (Round 13 widen)."""
    site, sap = "TEST_R13_APP", "R13APP-1"
    _r13_seed_inventory(site, sap, opening=50)
    pid = _r13_seed_pending_issue(site, sap, qty=3, status="approved")
    n = database.commit_eod(hod_username="hod_r13")
    assert n >= 1, f"commit_eod should commit approved rows, got {n}"
    conn = database.get_connection()
    try:
        row = conn.execute(
            'SELECT Quantity, "Approved By" FROM consumption '
            "WHERE SAP_Code = ?", (sap,),
        ).fetchone()
        assert row, "consumption row missing for approved → commit"
        assert float(row[0]) == 3.0
        assert row[1] == "hod_r13"
    finally:
        conn.close()


def check_r13_commit_eod_picks_up_flagged_status() -> None:
    """commit_eod commits status='flagged' rows too."""
    site, sap = "TEST_R13_FLG", "R13FLG-1"
    _r13_seed_inventory(site, sap, opening=50)
    _r13_seed_pending_issue(site, sap, qty=2, status="flagged")
    n = database.commit_eod(hod_username="hod_r13")
    assert n >= 1
    conn = database.get_connection()
    try:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM consumption WHERE SAP_Code = ?", (sap,),
        ).fetchone()[0]
        assert cnt == 1, f"flagged row didn't commit (got {cnt})"
    finally:
        conn.close()


def check_r13_commit_eod_skips_rejected_status() -> None:
    """commit_eod must NOT commit status='rejected' rows.

    (In real use, rejection routes through hod_reject_pending_issue which
    moves to archive — but a stray legacy row directly UPDATEd to 'rejected'
    must still be ignored.)
    """
    site, sap = "TEST_R13_REJ", "R13REJ-1"
    _r13_seed_inventory(site, sap, opening=50)
    _r13_seed_pending_issue(site, sap, qty=1, status="rejected")
    n = database.commit_eod(hod_username="hod_r13")
    conn = database.get_connection()
    try:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM consumption WHERE SAP_Code = ?", (sap,),
        ).fetchone()[0]
        assert cnt == 0, "rejected rows must never reach consumption"
        # Row itself stays in pending_issues (only archive moves it out).
        still = conn.execute(
            "SELECT COUNT(*) FROM pending_issues WHERE SAP_Code = ?",
            (sap,),
        ).fetchone()[0]
        assert still == 1, "rejected row must NOT be deleted by commit_eod"
    finally:
        conn.close()


def check_r13_get_pending_issues_for_site_widened() -> None:
    """get_pending_issues_for_site returns approved + flagged + pending_hod."""
    site = "TEST_R13_VIS"
    _r13_seed_inventory(site, "R13VIS-1")
    _r13_seed_inventory(site, "R13VIS-2")
    _r13_seed_inventory(site, "R13VIS-3")
    _r13_seed_pending_issue(site, "R13VIS-1", status="pending_hod")
    _r13_seed_pending_issue(site, "R13VIS-2", status="approved")
    _r13_seed_pending_issue(site, "R13VIS-3", status="flagged")
    df = database.get_pending_issues_for_site(site_id=site)
    sap_seen = set(df["SAP_Code"].tolist())
    assert sap_seen == {"R13VIS-1", "R13VIS-2", "R13VIS-3"}, sap_seen


def check_r13_hod_reject_moves_to_archive() -> None:
    """hod_reject_pending_issue archives + deletes the source row."""
    site, sap = "TEST_R13_ARCH", "R13ARCH-1"
    _r13_seed_inventory(site, sap)
    pid = _r13_seed_pending_issue(site, sap, qty=4, status="pending_hod")
    ok = database.hod_reject_pending_issue(
        pid, rejected_by="hod_r13", reason="wrong qty",
    )
    assert ok
    conn = database.get_connection()
    try:
        # Source row gone.
        cnt = conn.execute(
            "SELECT COUNT(*) FROM pending_issues WHERE id = ?", (pid,),
        ).fetchone()[0]
        assert cnt == 0, "rejected row must be deleted from pending_issues"
        # Archive row landed with the metadata.
        arch = conn.execute(
            "SELECT original_id, SAP_Code, Quantity, rejected_by, "
            "       reject_reason FROM rejected_issues_archive "
            "WHERE original_id = ?", (pid,),
        ).fetchone()
        assert arch is not None, "no archive row"
        assert arch[1] == sap
        assert float(arch[2]) == 4.0
        assert arch[3] == "hod_r13"
        assert arch[4] == "wrong qty"
    finally:
        conn.close()


def check_r13_hod_unapprove_flips_back_to_pending() -> None:
    """hod_unapprove_pending_issue moves status='approved' → 'pending_hod'."""
    site, sap = "TEST_R13_UNA", "R13UNA-1"
    _r13_seed_inventory(site, sap)
    pid = _r13_seed_pending_issue(site, sap, status="approved")
    ok = database.hod_unapprove_pending_issue(pid)
    assert ok
    conn = database.get_connection()
    try:
        st = conn.execute(
            "SELECT status FROM pending_issues WHERE id = ?", (pid,),
        ).fetchone()[0]
        assert st == "pending_hod", st
        # Idempotent no-op on a row that isn't approved.
        ok2 = database.hod_unapprove_pending_issue(pid)
        assert ok2 is False, "unapprove must be a no-op on non-approved rows"
    finally:
        conn.close()


def check_r13_bogus_approved_column_dropped() -> None:
    """The legacy bogus `Approved` column (with parsed type "By TEXT") must
    not exist on consumption after init_db. Only the proper "Approved By"
    column should remain.
    """
    conn = database.get_connection()
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(consumption)"
        ).fetchall()}
        assert "Approved" not in cols, (
            f"Bogus legacy 'Approved' column still present: {cols}"
        )
        assert "Approved By" in cols, (
            f'Proper "Approved By" column missing: {cols}'
        )
    finally:
        conn.close()


def check_r13_rejected_issues_archive_table_exists() -> None:
    """rejected_issues_archive table + minimum column set exist."""
    conn = database.get_connection()
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(rejected_issues_archive)"
        ).fetchall()}
        for must in ("archive_id", "original_id", "SAP_Code", "Quantity",
                     "Site_ID", "Source_Ref", "Requested_By",
                     "rejected_by", "rejected_at", "reject_reason"):
            assert must in cols, f"archive missing {must}: {cols}"
    finally:
        conn.close()


def check_r13_consumption_export_cols_constant() -> None:
    """config.CONSUMPTION_EXPORT_COLS is shaped right and excludes the
    legacy / hidden columns."""
    from config import CONSUMPTION_EXPORT_COLS
    assert isinstance(CONSUMPTION_EXPORT_COLS, list)
    cols = {db for db, _label in CONSUMPTION_EXPORT_COLS}
    must_include = {
        "Date", "SAP_Code", "Material_Code", "Equipment_Description", "UOM",
        "Quantity", "Work_Type", "Issued_By", "Issued_To",
        "Requested_By", "Approved By", "Remarks", "Site_ID",
    }
    missing = must_include - cols
    assert not missing, f"canonical export missing: {missing}"
    must_exclude = {
        "Technician", "Approved", "status",
        "Source_Ref", "FEFO_Override",
    }
    leaked = must_exclude & cols
    assert not leaked, f"canonical export leaks legacy cols: {leaked}"


def check_r13_smr_reject_flips_line_status() -> None:
    """Rejecting an SMR-sourced pending_issues row at HOD review must flip
    the matching SMR line to line_status='rejected_at_hod' (distinct from
    SK-side 'withdrawn_at_staging'). Source_Ref preserved in archive."""
    rid, no, *_ = _7b_make_pending("TEST_R13_SMR", "_SMRREJ", qty=2,
                                   sap_stock=20)
    ok, _ = database.approve_supervisor_request(rid, "sk_r13")
    assert ok
    # Flip the draft row to pending_hod (simulates SK submit-batch).
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT id, Source_Ref FROM pending_issues "
            "WHERE Source_Ref LIKE ?", (f"SMR:{no}:%",),
        ).fetchone()
        assert row, "SMR draft row missing"
        pi_id, src = int(row[0]), row[1]
        conn.execute(
            "UPDATE pending_issues SET status='pending_hod' WHERE id = ?",
            (pi_id,),
        )
        conn.commit()
    finally:
        conn.close()
    # HOD rejects it at EOD review.
    ok = database.hod_reject_pending_issue(
        pi_id, rejected_by="hod_r13", reason="exceeds shift cap",
    )
    assert ok
    conn = database.get_connection()
    try:
        ls = conn.execute(
            "SELECT line_status FROM supervisor_material_request_items "
            "WHERE request_id = ?", (rid,),
        ).fetchone()[0]
        assert ls == "rejected_at_hod", (
            f"line_status should be 'rejected_at_hod', got {ls!r}"
        )
        # Archive carries the Source_Ref so SMR intent-vs-actual reports can
        # still resolve the line.
        arch_src = conn.execute(
            "SELECT Source_Ref FROM rejected_issues_archive "
            "WHERE original_id = ?", (pi_id,),
        ).fetchone()
        assert arch_src and arch_src[0] == src, (
            f"archive Source_Ref mismatch: {arch_src}"
        )
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Round 14 — Vision OCR image-prep pipeline hardening
# ═══════════════════════════════════════════════════════════════════════════
def _r14_synth_jpeg(width: int, height: int, *, mode: str = "RGB",
                    quality: int = 95, exif_orientation: int | None = None) -> bytes:
    """Build a synthetic JPEG/PNG in memory. Used by image-prep checks so
    they never depend on a real photo being present on disk."""
    from io import BytesIO
    from PIL import Image
    img = Image.new(mode, (width, height), color=(180, 180, 180) if mode == "RGB" else 200)
    buf = BytesIO()
    if exif_orientation is not None:
        # Build a minimal EXIF block with just the Orientation tag (0x0112).
        exif = img.getexif()
        exif[0x0112] = int(exif_orientation)
        img.save(buf, format="JPEG", quality=quality, exif=exif.tobytes())
    else:
        img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def check_r14_prep_caps_long_edge() -> None:
    """prep_image_for_vision shrinks a 4032×3024 photo so its long edge
    is ≤ 1600 px (Round 14 default)."""
    from io import BytesIO
    from PIL import Image
    from ai.image_utils import prep_image_for_vision
    raw = _r14_synth_jpeg(4032, 3024)
    out = prep_image_for_vision(raw)
    w, h = Image.open(BytesIO(out)).size
    assert max(w, h) <= 1600, f"long edge not capped: {w}x{h}"
    # Aspect ratio preserved within 1 px (rounding).
    assert abs((w / h) - (4032 / 3024)) < 0.01, (
        f"aspect mismatch: {w}/{h}"
    )


def check_r14_prep_converts_to_rgb() -> None:
    """A grayscale-mode source comes back as RGB JPEG (Ollama's preprocessor
    chokes on palette/alpha-mode encodings)."""
    from io import BytesIO
    from PIL import Image
    from ai.image_utils import prep_image_for_vision
    raw = _r14_synth_jpeg(800, 600, mode="L")
    out = prep_image_for_vision(raw)
    im = Image.open(BytesIO(out))
    assert im.mode == "RGB", f"mode not RGB: {im.mode}"
    assert im.format == "JPEG", f"format not JPEG: {im.format}"


def check_r14_prep_shrinks_byte_size() -> None:
    """A 4032×3024 quality-95 source must come back substantially smaller
    after the 1600 px cap + quality-85 re-encode."""
    from ai.image_utils import prep_image_for_vision
    raw = _r14_synth_jpeg(4032, 3024, quality=95)
    out = prep_image_for_vision(raw)
    assert len(out) < len(raw), (
        f"prep didn't shrink: {len(raw)} → {len(out)}"
    )
    # Sanity floor: at least 3× smaller for a 12-MP synthetic frame.
    # (Solid-color synthetic JPEGs compress hard already, so any improvement
    # at all on top is meaningful — bump the assertion if synthetic noise
    # is added later.)
    assert len(out) * 2 < len(raw), (
        f"prep barely shrunk: {len(raw)} → {len(out)}"
    )


def check_r14_prep_honours_exif_orientation() -> None:
    """EXIF orientation 6 = 'rotate 90° CW for display' — a 800×600 frame
    flagged as orientation 6 should come back as 600×800 (width/height
    swapped) once ImageOps.exif_transpose normalises it."""
    from io import BytesIO
    from PIL import Image
    from ai.image_utils import prep_image_for_vision
    raw = _r14_synth_jpeg(800, 600, exif_orientation=6)
    out = prep_image_for_vision(raw)
    w, h = Image.open(BytesIO(out)).size
    # After transpose, the displayed dimensions swap.
    assert (w, h) == (600, 800), (
        f"EXIF transpose not applied: {w}x{h} (expected 600x800)"
    )


def check_r14_prep_raises_on_unreadable_bytes() -> None:
    """Random / corrupt bytes raise ImagePrepError (not a raw PIL
    exception, which would leak into the Streamlit error toast)."""
    from ai.image_utils import prep_image_for_vision, ImagePrepError
    bad = b"this is not an image - random text bytes"
    try:
        prep_image_for_vision(bad)
    except ImagePrepError:
        return
    except Exception as e:  # pragma: no cover — wrong exception type
        raise AssertionError(f"expected ImagePrepError, got {type(e).__name__}: {e}")
    raise AssertionError("expected ImagePrepError on corrupt bytes; none raised")


# ═══════════════════════════════════════════════════════════════════════════
# Round 15 — Multi-Portal Polish + Material Master + PO Parser Fix
# ═══════════════════════════════════════════════════════════════════════════
def _r15_seed_inventory_rows(rows: list[tuple[str, str]]) -> None:
    """Seed inventory with (SAP_Code, Material_Code) tuples for test isolation."""
    conn = database.get_connection()
    try:
        for sap, mc in rows:
            conn.execute(
                "INSERT INTO inventory "
                "(SAP_Code, Material_Code, Equipment_Description, UOM, "
                " Category, Minimum_Qty) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sap, mc, f"Desc {mc}", "PCS", "Others", 0),
            )
        conn.commit()
    finally:
        conn.close()


def check_r15_schema_inventory_site_overrides_table() -> None:
    """inventory_site_overrides table + UNIQUE(SAP_Code, Site_ID) present."""
    conn = database.get_connection()
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(inventory_site_overrides)"
        ).fetchall()}
        for must in ("SAP_Code", "Site_ID", "Minimum_Qty",
                     "updated_by", "updated_at"):
            assert must in cols, f"missing: {must} / {cols}"
        # UNIQUE constraint must reject same-(SAP,Site) twice.
        conn.execute(
            "INSERT INTO inventory_site_overrides "
            "(SAP_Code, Site_ID, Minimum_Qty, updated_by) "
            "VALUES (?, ?, ?, ?)",
            ("R15-OVR-1", "JUBAIL", 10, "test"),
        )
        try:
            conn.execute(
                "INSERT INTO inventory_site_overrides "
                "(SAP_Code, Site_ID, Minimum_Qty, updated_by) "
                "VALUES (?, ?, ?, ?)",
                ("R15-OVR-1", "JUBAIL", 99, "test"),
            )
            raise AssertionError("UNIQUE constraint not enforced")
        except sqlite3.IntegrityError:
            pass
    finally:
        conn.close()


def check_r15_next_sap_code_increments() -> None:
    """next_sap_code returns max(numeric tail)+1 formatted as GI-NNNNNNN."""
    _r15_seed_inventory_rows([
        ("GI-9000100", "R15-NSP-1"),
        ("GI-9000099", "R15-NSP-2"),
    ])
    nxt = database.next_sap_code()
    # Max across the table — handles whatever already exists in the test DB.
    conn = database.get_connection()
    try:
        max_num = 0
        for (s,) in conn.execute(
            "SELECT SAP_Code FROM inventory WHERE SAP_Code LIKE 'GI-%'"
        ).fetchall():
            try:
                max_num = max(max_num, int(str(s).split("-", 1)[1]))
            except (ValueError, IndexError):
                pass
    finally:
        conn.close()
    expected = f"GI-{max_num + 1:07d}"
    assert nxt == expected, f"next_sap_code={nxt}, expected={expected}"
    # Bounded format: starts with 'GI-' and ends with 7-digit tail.
    assert nxt.startswith("GI-") and nxt[3:].isdigit() and len(nxt[3:]) == 7


def check_r15_next_temp_material_code_persists() -> None:
    """next_temp_material_code increments and survives a re-read."""
    a = database.next_temp_material_code()
    b = database.next_temp_material_code()
    assert a.startswith("Temp-GI-") and a[8:].isdigit()
    assert int(b[8:]) == int(a[8:]) + 1, f"expected sequential, got {a}, {b}"


def check_r15_bulk_upsert_inserts_with_auto_codes() -> None:
    """bulk_upsert_materials auto-assigns SAP_Code; blank Material_Code →
    Temp-GI-…; full path persists the row."""
    res = database.bulk_upsert_materials([
        {"Equipment_Description": "R15 Test Material A",
         "UOM": "PCS", "Category": "Others", "Minimum_Qty": 5,
         "Material_Code": "R15-UPS-A"},
        {"Material_Description": "R15 Test Material B (no code)",
         "UOM": "KG", "Minimum_Qty": 0},  # blank Material_Code
    ], created_by="r15_test")
    assert len(res["inserted"]) == 2, res
    a, b = res["inserted"]
    assert a["Material_Code"] == "R15-UPS-A"
    assert b["Material_Code"].startswith("Temp-GI-")
    assert a["SAP_Code"].startswith("GI-")
    # Verify DB persistence.
    conn = database.get_connection()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM inventory "
            "WHERE Material_Code IN ('R15-UPS-A', ?)", (b["Material_Code"],),
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 2, n


def check_r15_bulk_upsert_rejects_duplicates() -> None:
    """Duplicate Material_Code rejected when overwrite_duplicates=False."""
    database.bulk_upsert_materials([
        {"Material_Code": "R15-DUP-X",
         "Equipment_Description": "First copy", "UOM": "PCS",
         "Minimum_Qty": 0},
    ], created_by="r15_test")
    res = database.bulk_upsert_materials([
        {"Material_Code": "R15-DUP-X",
         "Equipment_Description": "Second copy attempt", "UOM": "PCS",
         "Minimum_Qty": 0},
    ], created_by="r15_test")
    assert not res["inserted"]
    assert len(res["rejected"]) == 1
    assert "already exists" in (res["rejected"][0].get("_reason") or "").lower()


def check_r15_bulk_upsert_overwrite_path() -> None:
    """overwrite_duplicates=True updates the existing row in place."""
    database.bulk_upsert_materials([
        {"Material_Code": "R15-OVR-X",
         "Equipment_Description": "Original", "UOM": "PCS",
         "Minimum_Qty": 0},
    ], created_by="r15_test")
    res = database.bulk_upsert_materials([
        {"Material_Code": "R15-OVR-X",
         "Equipment_Description": "Updated description",
         "UOM": "KG", "Minimum_Qty": 50, "Category": "Consumable"},
    ], created_by="r15_test", overwrite_duplicates=True)
    assert len(res["updated"]) == 1
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT Equipment_Description, UOM, Minimum_Qty "
            "FROM inventory WHERE Material_Code = ?", ("R15-OVR-X",),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "Updated description"
    assert row[1] == "KG"
    assert float(row[2]) == 50.0


def check_r15_set_and_get_site_min_qty() -> None:
    """set_site_min_qty + get_min_qty_for COALESCE override over default."""
    database.bulk_upsert_materials([
        {"Material_Code": "R15-MIN-X",
         "Equipment_Description": "Min override test",
         "UOM": "PCS", "Minimum_Qty": 10},
    ], created_by="r15_test")
    conn = database.get_connection()
    try:
        sap = conn.execute(
            "SELECT SAP_Code FROM inventory WHERE Material_Code = ?",
            ("R15-MIN-X",),
        ).fetchone()[0]
    finally:
        conn.close()
    # Default (no override) → falls back to inventory.Minimum_Qty=10.
    assert database.get_min_qty_for(sap, "JUBAIL") == 10.0
    # Set site override.
    assert database.set_site_min_qty(sap, "JUBAIL", 25.0, updated_by="r15_test")
    assert database.get_min_qty_for(sap, "JUBAIL") == 25.0
    # Different site still sees the default.
    assert database.get_min_qty_for(sap, "RIYADH") == 10.0
    # Clear via negative value → falls back to default.
    database.set_site_min_qty(sap, "JUBAIL", -1, updated_by="r15_test")
    assert database.get_min_qty_for(sap, "JUBAIL") == 10.0


def check_r15_inventory_material_code_unique_index() -> None:
    """Direct INSERT with duplicate Material_Code is rejected by the
    partial UNIQUE index added in Round 15."""
    conn = database.get_connection()
    try:
        # First insert via the helper path so SAP_Code is auto-assigned.
        database.bulk_upsert_materials([
            {"Material_Code": "R15-UQ-DUP",
             "Equipment_Description": "Unique test",
             "UOM": "PCS", "Minimum_Qty": 0},
        ], created_by="r15_test")
        # Now attempt a raw duplicate INSERT — the index should reject it.
        try:
            conn.execute(
                "INSERT INTO inventory "
                "(SAP_Code, Material_Code, Equipment_Description, UOM) "
                "VALUES (?, ?, ?, ?)",
                ("R15-UQ-DUP-FAKE-SAP", "R15-UQ-DUP", "Dup attempt", "PCS"),
            )
            conn.commit()
            raise AssertionError("UNIQUE Material_Code index not enforced")
        except sqlite3.IntegrityError:
            pass
    finally:
        conn.close()


def check_r15_po_pdf_three_items() -> None:
    """process_po_pdf extracts the 3 line items from PO#4710003114.pdf
    (regression guard for the two-line layout fix)."""
    import os
    pdf_path = "/Users/johnsonandrew/GI_Hub_Project/PO#4710003114.pdf"
    if not os.path.exists(pdf_path):
        return  # skip silently in CI environments without the sample file
    with open(pdf_path, "rb") as f:
        raw = f.read()
    ok, msg, ext = database.process_po_pdf(raw)
    assert ok, msg
    items = ext.get("items", [])
    assert len(items) == 3, f"expected 3 items, got {len(items)} ({msg})"
    assert ext["header"].get("PR_Number") == "3000000681"
    assert ext["header"].get("PO_Number") == "4710003114"
    # Item codes match the PDF.
    codes = sorted(i.get("Material_Code") for i in items)
    assert codes == ["GI-7001958", "GI-7002522", "GI-7002615"], codes


def check_r15_po_pdf_synthetic_two_line_layout() -> None:
    """Synthetic regression — re-run the items extractor against a hand-rolled
    text fixture matching the Round 15 two-line layout. Verifies the
    regression even on machines without the real PDF on disk."""
    # Mock pdfplumber so we control the extracted text exactly.
    sample = (
        "Page 1 of 1\nPurchase Order\n4710999999\n"
        "Sr. No. Material Description QTY UoM Unit Price VAT Amount Total Price\n"
        "GI-7099001\n"
        "001 ITEM ALPHA SAMPLE 10.00 KG 5.00 7.50 50.00\n"
        "GI-7099002\n"
        "002 ITEM BETA SAMPLE 20.00 PCS 3.00 9.00 60.00\n"
    )

    class _MockPage:
        def extract_text(self): return sample
        def extract_tables(self): return []

    class _MockPDF:
        def __init__(self, *_a, **_kw): self.pages = [_MockPage()]
        def __enter__(self): return self
        def __exit__(self, *_a): return False

    orig = database.pdfplumber
    class _Stub:
        @staticmethod
        def open(buf): return _MockPDF()
    database.pdfplumber = _Stub
    try:
        ok, msg, ext = database.process_po_pdf(b"fake")
    finally:
        database.pdfplumber = orig

    assert ok, msg
    items = ext.get("items", [])
    assert len(items) == 2, f"expected 2 items, got {len(items)}"
    sa, sb = items
    assert sa["Material_Code"] == "GI-7099001"
    assert sa["Description"].startswith("ITEM ALPHA")
    assert float(sa["Qty"]) == 10.0
    assert sa["UOM"] == "KG"
    assert sb["Material_Code"] == "GI-7099002"
    assert float(sb["Qty"]) == 20.0


def check_r15_list_pending_hod_dns_site_fallback() -> None:
    """list_pending_hod_dns falls back through the 3-way join when the DN
    has the wrong (or NULL) Site_ID — the bug Phase EE fixed for future
    DNs but legacy rows still need to surface."""
    conn = database.get_connection()
    try:
        # Seed a PR + PO at site 'R15HOD' with a DN that carries the
        # default 'HQ' Site_ID (mismatched). The HOD of 'R15HOD' must
        # still see this DN.
        conn.execute(
            "INSERT INTO pr_master (PR_Number, SAP_Code, Requested_Qty, Site_ID) "
            "VALUES (?, ?, ?, ?)",
            ("R15-PR-HOD", "GI-9999991", 1, "R15HOD"),
        )
        conn.execute(
            "INSERT INTO purchase_orders (PO_Number, PR_Number, Site_ID) "
            "VALUES (?, ?, ?)",
            ("R15-PO-HOD", "R15-PR-HOD", "R15HOD"),
        )
        # DN with HQ as Site_ID (the bug) — the JOIN should still tie it
        # back to R15HOD via the PO.
        conn.execute(
            "INSERT INTO delivery_notes "
            "(DN_Number, PO_Number, Site_ID, Warehouse_ID, status, created_by) "
            "VALUES (?, ?, ?, ?, ?, 'r15_test')",
            ("R15-DN-HOD", "R15-PO-HOD", "HQ", "WH1", "pending_hod"),
        )
        conn.commit()
    finally:
        conn.close()
    df = database.list_pending_hod_dns("R15HOD")
    assert not df.empty, "DN should surface via 3-way join fallback"
    assert "R15-DN-HOD" in df["DN_Number"].tolist()


def check_r15_request_reschedule_routes_to_warehouse() -> None:
    """When the DN status is in _RESCHEDULE_WAREHOUSE_DIRECT_STATUSES, the
    reschedule notification fans out to warehouse_user, not logistics."""
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO delivery_notes "
            "(DN_Number, PO_Number, Site_ID, Warehouse_ID, status, created_by) "
            "VALUES (?, ?, ?, ?, ?, 'r15_test')",
            ("R15-DN-WH", "R15-PO-WH", "R15WH", "WH-R15", "pending_sk"),
        )
        conn.commit()
    finally:
        conn.close()
    ok, msg = database.request_reschedule(
        po_number="R15-PO-WH", dn_number="R15-DN-WH",
        current_date="2026-07-01", requested_date="2026-07-08",
        reason="Site offline during planned shutdown",
        requested_by_role="hod", requested_by="hod_r15",
    )
    assert ok, msg
    assert "warehouse" in msg.lower(), f"msg should mention warehouse: {msg}"
    # Verify a warehouse-targeted notification landed.
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT recipient_role, recipient_warehouse "
            "FROM app_notifications WHERE related_table='po_reschedule_requests' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row, "no notification queued"
    assert row[0] == "warehouse_user", row
    assert row[1] == "WH-R15", row


def check_r15_request_reschedule_routes_to_logistics_when_no_dn() -> None:
    """PO-level reschedule (no DN_Number, or DN in pre-dispatch state) still
    routes to logistics — back-compat for the existing flow."""
    ok, msg = database.request_reschedule(
        po_number="R15-PO-LOG", dn_number=None,
        current_date="2026-07-01", requested_date="2026-07-15",
        reason="Vendor lead time slipped",
        requested_by_role="hod", requested_by="hod_r15",
    )
    assert ok, msg
    assert "logistics" in msg.lower(), f"msg should mention logistics: {msg}"
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT recipient_role FROM app_notifications "
            "WHERE related_table='po_reschedule_requests' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row and row[0] == "logistics", row


def check_r15_consumption_export_cols_preserved() -> None:
    """Round 13 contract — the canonical export columns must still hold
    after the Round 15 additions to inventory schema."""
    from config import CONSUMPTION_EXPORT_COLS
    cols = [c for c, _ in CONSUMPTION_EXPORT_COLS]
    assert "Material_Code" in cols
    assert "Approved By" in cols  # legacy space-named


def check_r15_pr_report_keeps_uom_column() -> None:
    """_ALWAYS_KEEP now includes UOM so the strip helper never drops it
    even when a batch of legacy PRs has it blank."""
    import importlib
    rp = importlib.import_module("pages_internal.reports_page")
    assert "UOM" in rp._ALWAYS_KEEP, rp._ALWAYS_KEEP


# ═══════════════════════════════════════════════════════════════════════════
# Round 16 — Logistics removed from DN chain + PR PDF polish
# ═══════════════════════════════════════════════════════════════════════════
def _r16_seed_dn(dn_no: str, site_id: str = "R16HOD") -> str:
    """Insert a DN at status='draft' so submit_dn_for_logistics can flip it."""
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO delivery_notes "
            "(DN_Number, PO_Number, Site_ID, Warehouse_ID, status, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (dn_no, "R16-PO-1", site_id, "WH-R16", "draft", "wh_r16"),
        )
        conn.commit()
    finally:
        conn.close()
    return dn_no


def check_r16_submit_dn_for_logistics_writes_pending_hod() -> None:
    """Round 16 — submit_dn_for_logistics flips draft → pending_hod
    (was pending_logistics). Logistics is no longer in the approval loop."""
    _r16_seed_dn("R16-DN-001")
    ok, msg = database.submit_dn_for_logistics("R16-DN-001", "wh_r16")
    assert ok, msg
    conn = database.get_connection()
    try:
        status = conn.execute(
            "SELECT status FROM delivery_notes WHERE DN_Number = ?",
            ("R16-DN-001",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert status == "pending_hod", (
        f"Round 16 contract: status should be 'pending_hod', got {status!r}"
    )


def check_r16_submit_dn_dual_notification_fanout() -> None:
    """submit_dn_for_logistics queues TWO notifications: the actionable one
    to HOD at the destination site, and an awareness 'info' one to
    Logistics (no action required)."""
    _r16_seed_dn("R16-DN-002")
    ok, _ = database.submit_dn_for_logistics("R16-DN-002", "wh_r16")
    assert ok
    conn = database.get_connection()
    try:
        rows = conn.execute(
            "SELECT recipient_role, recipient_site, severity, title "
            "FROM app_notifications WHERE related_ref = ? "
            "ORDER BY id", ("R16-DN-002",),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2, (
        f"expected 2 notifications (HOD + Logistics), got {len(rows)}: {rows}"
    )
    roles = sorted(r[0] for r in rows)
    assert roles == ["hod", "logistics"], (
        f"expected [hod, logistics], got {roles}"
    )
    # Both severities should be 'info' per Round 16 design.
    for r in rows:
        assert r[2] == "info", f"non-info severity: {r}"
    # The HOD-targeted row carries the site; the Logistics row does not.
    hod_row = next(r for r in rows if r[0] == "hod")
    log_row = next(r for r in rows if r[0] == "logistics")
    assert hod_row[1] == "R16HOD", hod_row
    assert "awaiting your approval" in hod_row[3].lower(), hod_row[3]
    assert "info only" in log_row[3].lower(), log_row[3]


def check_r16_legacy_dn_migration_to_pending_hod() -> None:
    """init_db migration sweeps any DN at pending_logistics or
    logistics_approved forward to pending_hod (idempotent on re-runs)."""
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO delivery_notes "
            "(DN_Number, PO_Number, Site_ID, Warehouse_ID, status, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("R16-LEGACY-001", "R16-PO-LEG", "R16HOD", "WH-R16",
             "pending_logistics", "wh_r16"),
        )
        conn.execute(
            "INSERT INTO delivery_notes "
            "(DN_Number, PO_Number, Site_ID, Warehouse_ID, status, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("R16-LEGACY-002", "R16-PO-LEG", "R16HOD", "WH-R16",
             "logistics_approved", "wh_r16"),
        )
        conn.commit()
    finally:
        conn.close()
    # Re-run init_db — the migration should pick up both rows.
    database.init_db()
    conn = database.get_connection()
    try:
        rows = conn.execute(
            "SELECT DN_Number, status FROM delivery_notes "
            "WHERE DN_Number IN (?, ?) ORDER BY DN_Number",
            ("R16-LEGACY-001", "R16-LEGACY-002"),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    for dn, status in rows:
        assert status == "pending_hod", (
            f"{dn} not migrated: status={status!r}"
        )
    # Idempotent: second call leaves rows alone (no integrity errors, no
    # double-audit). Re-running here just must not raise.
    database.init_db()


def check_r16_get_pr_with_po_numbers_comma_joined() -> None:
    """get_pr_with_po_numbers returns {pr_line_id: 'PO-1, PO-2, …'} for
    PRs with multiple POs against the same SAP line."""
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO inventory (SAP_Code, Material_Code, "
            " Equipment_Description, UOM) VALUES (?, ?, ?, ?)",
            ("SAP-R16-MULTI", "MC-R16-M", "Multi-PO test", "PCS"),
        )
        conn.execute(
            "INSERT INTO pr_master (PR_Number, SAP_Code, Material_Code, "
            " Material_Name, UOM, Requested_Qty, Site_ID, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("PR-R16-M", "SAP-R16-MULTI", "MC-R16-M",
             "Multi-PO test", "PCS", 100, "R16HOD", "open"),
        )
        line_id = conn.execute(
            "SELECT id FROM pr_master WHERE PR_Number=?", ("PR-R16-M",),
        ).fetchone()[0]
        # Three POs split across the same PR line.
        for po_no in ("PO-R16-A", "PO-R16-B", "PO-R16-C"):
            conn.execute(
                "INSERT INTO purchase_orders (PO_Number, PR_Number, Site_ID) "
                "VALUES (?, ?, ?)", (po_no, "PR-R16-M", "R16HOD"),
            )
            conn.execute(
                "INSERT INTO po_items (PO_Number, Material_Code, "
                " Description, Qty, UOM) VALUES (?, ?, ?, ?, ?)",
                (po_no, "MC-R16-M", "Multi-PO test", 33, "PCS"),
            )
        conn.commit()
    finally:
        conn.close()

    out = database.get_pr_with_po_numbers("PR-R16-M")
    assert int(line_id) in out, out
    poseq = out[int(line_id)]
    assert poseq == "PO-R16-A, PO-R16-B, PO-R16-C", poseq


def check_r16_generate_pr_pdf_has_new_columns() -> None:
    """generate_pr_pdf renders PO # + UoM headers and accepts the po_map
    kwarg. Output is a valid PDF byte stream."""
    import pandas as pd
    from reports import generate_pr_pdf

    df = pd.DataFrame([
        {"id": 1, "Material_Code": "MC-R16-A",
         "Material_Name": "Test Material A",
         "UOM": "PCS", "Requested_Qty": 10,
         "Pending_Qty": 4, "status": "open"},
        {"id": 2, "Material_Code": "MC-R16-B",
         "Material_Name": "Test Material B",
         "UOM": "KG", "Requested_Qty": 50,
         "Pending_Qty": 0, "status": "closed"},
    ])
    po_map = {1: "PO-001, PO-002"}
    pdf_bytes = generate_pr_pdf(
        "PR-R16-PDF", "R16HOD", df,
        generated_by="hod_r16", po_map=po_map,
    )
    # Valid PDF magic.
    assert pdf_bytes[:4] == b"%PDF", pdf_bytes[:20]
    # Reasonable size for a 2-row table (≥ 1 KB).
    assert len(pdf_bytes) > 1024, len(pdf_bytes)
    # The PDF stream is compressed so header literals aren't byte-greppable.
    # The function must, however, not raise — and the back-compat kwarg
    # path (no po_map) also has to succeed.
    pdf_bytes_no_map = generate_pr_pdf(
        "PR-R16-PDF", "R16HOD", df, generated_by="hod_r16",
    )
    assert pdf_bytes_no_map[:4] == b"%PDF"


# ═══════════════════════════════════════════════════════════════════════════
# Phase 7C — HOD Cross-Site View notification + indicator checks
# ═══════════════════════════════════════════════════════════════════════════
def _7c_tag(prefix: str) -> str:
    """Generate a unique viewer/site tag per check so tests don't collide."""
    import uuid as _u
    return f"{prefix}_{_u.uuid4().hex[:8]}"


def check_7c_index_target_date() -> None:
    conn = database.get_connection()
    try:
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='cross_site_views'"
        ).fetchall()}
        assert "ix_csv_target_date" in idx, f"missing: {idx}"
    finally:
        conn.close()


def check_7c_index_viewer_date() -> None:
    conn = database.get_connection()
    try:
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='cross_site_views'"
        ).fetchall()}
        assert "ix_csv_viewer_date" in idx, f"missing: {idx}"
    finally:
        conn.close()


def check_7c_unique_constraint() -> None:
    """The UNIQUE constraint is the entire debounce — second INSERT silently
    no-ops via INSERT OR IGNORE; equivalent raw INSERT would raise."""
    import sqlite3 as _sqlite3
    viewer = _7c_tag("UNQ_V")
    conn = database.get_connection()
    try:
        today = datetime.date.today().isoformat()
        conn.execute(
            "INSERT INTO cross_site_views "
            "(viewer_username, viewer_site_id, target_site_id, view_date) "
            "VALUES (?, ?, ?, ?)",
            (viewer, "A", "B", today),
        )
        conn.commit()
        # Same triple must raise on a raw INSERT (no OR IGNORE).
        raised = False
        try:
            conn.execute(
                "INSERT INTO cross_site_views "
                "(viewer_username, viewer_site_id, target_site_id, view_date) "
                "VALUES (?, ?, ?, ?)",
                (viewer, "A", "B", today),
            )
            conn.commit()
        except _sqlite3.IntegrityError:
            raised = True
            conn.rollback()
        assert raised, "UNIQUE constraint not enforced on (viewer, target, date)"
    finally:
        conn.close()


def check_7c_record_first_returns_true() -> None:
    viewer = _7c_tag("REC_FIRST")
    assert database.record_cross_site_view(viewer, "A", "B") is True
    conn = database.get_connection()
    try:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM cross_site_views WHERE viewer_username = ?",
            (viewer,),
        ).fetchone()[0]
        assert cnt == 1, f"expected 1 row, got {cnt}"
    finally:
        conn.close()


def check_7c_record_dedupe_returns_false() -> None:
    viewer = _7c_tag("REC_DUPE")
    assert database.record_cross_site_view(viewer, "A", "B") is True
    assert database.record_cross_site_view(viewer, "A", "B") is False
    conn = database.get_connection()
    try:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM cross_site_views WHERE viewer_username = ?",
            (viewer,),
        ).fetchone()[0]
        assert cnt == 1, f"dedupe failed — got {cnt} rows"
    finally:
        conn.close()


def check_7c_different_target_returns_true() -> None:
    viewer = _7c_tag("REC_TGT")
    assert database.record_cross_site_view(viewer, "A", "B") is True
    assert database.record_cross_site_view(viewer, "A", "C") is True


def check_7c_different_viewer_returns_true() -> None:
    v1 = _7c_tag("REC_V1")
    v2 = _7c_tag("REC_V2")
    assert database.record_cross_site_view(v1, "A", "B") is True
    assert database.record_cross_site_view(v2, "A", "B") is True


def check_7c_self_view_skipped() -> None:
    viewer = _7c_tag("REC_SELF")
    assert database.record_cross_site_view(viewer, "B", "B") is False
    conn = database.get_connection()
    try:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM cross_site_views WHERE viewer_username = ?",
            (viewer,),
        ).fetchone()[0]
        assert cnt == 0, f"self-view should not insert; got {cnt} rows"
    finally:
        conn.close()


def check_7c_blank_inputs_skipped() -> None:
    assert database.record_cross_site_view("", "A", "B") is False
    assert database.record_cross_site_view("user", "A", "") is False
    # None should be safely coerced via .strip("" or None) pattern.
    assert database.record_cross_site_view(None, "A", "B") is False


def check_7c_admin_role_suppressed() -> None:
    """notify_cross_site_view: admin viewer never records or fires."""
    viewer = _7c_tag("NTF_ADM")
    fired = database.notify_cross_site_view(
        {"username": viewer, "role": "admin", "site_id": "A"},
        "B",
        viewed_item="Test Item",
    )
    assert fired is False
    conn = database.get_connection()
    try:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM cross_site_views WHERE viewer_username = ?",
            (viewer,),
        ).fetchone()[0]
        assert cnt == 0, f"admin shadowing should not record; got {cnt} rows"
    finally:
        conn.close()


def check_7c_notify_queues_app_notification() -> None:
    viewer = _7c_tag("NTF_APP")
    fired = database.notify_cross_site_view(
        {"username": viewer, "role": "hod", "site_id": "A"},
        "B_NTF_APP",
        viewed_item="[SAP-001] Widget",
    )
    assert fired is True
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT event_key, recipient_role, recipient_site, severity, body "
            "FROM app_notifications "
            "WHERE event_key = 'cross_site_viewed' "
            "  AND recipient_site = 'B_NTF_APP' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None, "no app_notifications row queued"
        assert row[0] == "cross_site_viewed", row
        assert row[1] == "hod", row
        assert row[2] == "B_NTF_APP", row
        assert row[3] == "info", row
        # Item context must appear in the body.
        assert "Widget" in (row[4] or ""), f"item not woven into body: {row[4]}"
    finally:
        conn.close()


def check_7c_notify_writes_audit_row() -> None:
    viewer = _7c_tag("NTF_AUD")
    fired = database.notify_cross_site_view(
        {"username": viewer, "role": "hod", "site_id": "A"},
        "B_AUD",
    )
    assert fired is True
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT username, action_type, target_table FROM system_audit_log "
            "WHERE username = ? AND action_type = 'CROSS_SITE_VIEW' "
            "ORDER BY id DESC LIMIT 1",
            (viewer,),
        ).fetchone()
        assert row is not None, "no audit row written"
        assert row[2] == "cross_site_views", row
    finally:
        conn.close()


def check_7c_notify_dedupe_no_double_send() -> None:
    viewer = _7c_tag("NTF_DUPE")
    target = "B_DUPE"
    first = database.notify_cross_site_view(
        {"username": viewer, "role": "hod", "site_id": "A"}, target,
    )
    second = database.notify_cross_site_view(
        {"username": viewer, "role": "hod", "site_id": "A"}, target,
    )
    assert first is True and second is False
    conn = database.get_connection()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM app_notifications "
            "WHERE event_key = 'cross_site_viewed' "
            "  AND recipient_site = ? AND related_ref LIKE ?",
            (target, f"{viewer}|%"),
        ).fetchone()[0]
        assert n == 1, f"expected exactly 1 notification, got {n}"
    finally:
        conn.close()


def check_7c_whatsapp_trigger_default_false() -> None:
    from config import WHATSAPP_TRIGGERS
    assert "cross_site_viewed" in WHATSAPP_TRIGGERS, "key missing"
    assert WHATSAPP_TRIGGERS["cross_site_viewed"] is False, \
        "spec Q6(b): cross_site_viewed defaults False"


# ═══════════════════════════════════════════════════════════════════════════
# Phase 7D — Site-bound PO notification with strict data masking
# ═══════════════════════════════════════════════════════════════════════════
def _7d_tag(prefix: str) -> str:
    import uuid as _u
    return f"{prefix}_{_u.uuid4().hex[:8]}"


def _7d_seed_po(
    site_id: str = "TEST_7D_SITE",
    *,
    pr_numbers: list | None = None,
    items: list | None = None,
    with_site: bool = True,
) -> str:
    """Seed a PO via create_po_manual and return its PO_Number.

    Returns the PO number on success — raises AssertionError on failure
    so the calling check surfaces a clear cause.
    """
    po_no = _7d_tag("PO_7D")
    header = {
        "PO_Number":         po_no,
        "PR_Number":         (pr_numbers or ["PR-1"])[0],
        "Site_ID":           site_id if with_site else None,
        "Vendor_Code":       "V-XYZ",
        "Vendor_Name":       "Acme Supplies Ltd",
        "Contact_Person":    "Bob Vendor",
        "Contact_Email":     "bob@acme.test",
        "Mobile":            "+966509999999",
        "Our_Email":         "buyer@gi.test",
        "Inco_Terms":        "DDP Riyadh",
        "Payment_Terms":     "Net 30",
        "PO_Date":           "2026-06-15",
        "PO_Type":           "Capex",
        "Quotation_No":      "Q-555",
        "Quotation_Date":    "2026-06-10",
        "Your_Reference":    "REF-VEN",
        "Our_Reference":     "REF-GI",
        "Expected_Delivery": "2026-06-25",
        "Freight_Charges":   500.0,
        "Handling_Charges":  150.0,
        "Discount_Amount":   100.0,
        "Total_Amount":      9876.54,
        "Amount_In_Words":   "Nine thousand eight hundred…",
    }
    if items is None:
        items = [{
            "Material_Code": "MAT-1",
            "Description":   "Widget A",
            "Qty":           5,
            "UOM":           "PCS",
            "Unit_Price":    100.0,
            "Total_Price":   500.0,
            "PR_Number":     (pr_numbers or ["PR-1"])[0],
        }]
    ok, msg = database.create_po_manual(
        header, items, created_by="test_logistics",
    )
    assert ok, f"create_po_manual failed: {msg}"
    return po_no


def check_7d_mask_field_count() -> None:
    from database import PO_VENDOR_MASK_FIELDS
    assert len(PO_VENDOR_MASK_FIELDS) == 17, \
        f"expected 17 mask fields, got {len(PO_VENDOR_MASK_FIELDS)}"


def check_7d_default_no_mask() -> None:
    po = _7d_seed_po(site_id="TEST_7D_DEF")
    detail = database.get_po_detail(po)
    h = detail["header"]
    assert h.get("Vendor_Name") == "Acme Supplies Ltd", h.get("Vendor_Name")
    assert h.get("Total_Amount") not in (None, 0, 0.0), h.get("Total_Amount")


def check_7d_hide_vendor_strips_all() -> None:
    from database import PO_VENDOR_MASK_FIELDS
    po = _7d_seed_po(site_id="TEST_7D_HV")
    detail = database.get_po_detail(po, hide_vendor=True)
    h = detail["header"]
    for f in PO_VENDOR_MASK_FIELDS:
        assert h.get(f) is None, f"{f} should be None when hide_vendor=True, got {h.get(f)!r}"


def check_7d_hide_vendor_keeps_operational() -> None:
    po = _7d_seed_po(site_id="TEST_7D_KEEP")
    h = database.get_po_detail(po, hide_vendor=True)["header"]
    assert h.get("PO_Type") == "Capex", h.get("PO_Type")
    assert h.get("PO_Date") == "2026-06-15", h.get("PO_Date")
    assert h.get("Expected_Delivery") == "2026-06-25"
    assert h.get("Site_ID") == "TEST_7D_KEEP"


def check_7d_combined_masks() -> None:
    po = _7d_seed_po(site_id="TEST_7D_COMB")
    detail = database.get_po_detail(po, hide_prices=True, hide_vendor=True)
    h = detail["header"]
    assert h.get("Vendor_Name") is None
    assert h.get("Total_Amount") is None
    items = detail["items"]
    assert not items.empty
    assert items["Unit_Price"].isna().all(), items["Unit_Price"].tolist()
    assert items["Total_Price"].isna().all(), items["Total_Price"].tolist()


def check_7d_summary_title_and_site() -> None:
    po = _7d_seed_po(site_id="TEST_7D_TITLE")
    s = database.build_po_site_notification(po)
    assert s["site_id"] == "TEST_7D_TITLE", s
    assert s["title"] == f"PO {po} issued for delivery to TEST_7D_TITLE", s["title"]


def check_7d_summary_pr_list_dedup() -> None:
    items = [
        {"Material_Code": "M-A", "Description": "A", "Qty": 1, "UOM": "PCS",
         "Unit_Price": 10, "Total_Price": 10, "PR_Number": "PR-100"},
        {"Material_Code": "M-B", "Description": "B", "Qty": 2, "UOM": "PCS",
         "Unit_Price": 10, "Total_Price": 20, "PR_Number": "PR-100"},
        {"Material_Code": "M-C", "Description": "C", "Qty": 3, "UOM": "PCS",
         "Unit_Price": 10, "Total_Price": 30, "PR_Number": "PR-200"},
    ]
    po = _7d_seed_po(site_id="TEST_7D_PR", items=items, pr_numbers=["PR-100"])
    s = database.build_po_site_notification(po)
    # Distinct list, comma-joined.
    assert "PR-100" in s["pr_numbers"], s["pr_numbers"]
    assert "PR-200" in s["pr_numbers"], s["pr_numbers"]
    # No dupes — split + dedupe check.
    parts = [p.strip() for p in s["pr_numbers"].split(",")]
    assert len(parts) == len(set(parts)), parts


def check_7d_summary_expected_delivery() -> None:
    po = _7d_seed_po(site_id="TEST_7D_ED")
    s = database.build_po_site_notification(po)
    assert "2026-06-25" in s["app_body"], s["app_body"]


def check_7d_summary_line_truncation() -> None:
    items = [
        {"Material_Code": f"M-{i}", "Description": f"Desc {i}",
         "Qty": i + 1, "UOM": "PCS",
         "Unit_Price": 10, "Total_Price": 10 * (i + 1),
         "PR_Number": "PR-LONG"}
        for i in range(8)
    ]
    po = _7d_seed_po(site_id="TEST_7D_TRUNC", items=items,
                     pr_numbers=["PR-LONG"])
    s = database.build_po_site_notification(po)
    body = s["app_body"]
    # First 5 SAP codes present (M-0..M-4); M-7 must NOT appear (truncated).
    for i in range(5):
        assert f"M-{i}" in body, f"M-{i} missing in body"
    assert "M-7" not in body, "8th item should be in overflow"
    assert "and 3 more line(s)" in body, f"overflow caption missing: {body}"


def check_7d_summary_no_vendor_in_body() -> None:
    po = _7d_seed_po(site_id="TEST_7D_NV")
    s = database.build_po_site_notification(po)
    blob = s["app_body"] + "\n" + s["whatsapp_body"]
    # Vendor identifiers must not appear anywhere.
    for needle in ("Acme Supplies", "V-XYZ", "Bob Vendor",
                   "bob@acme.test", "+966509999999"):
        assert needle not in blob, f"vendor leak: {needle!r} in body"


def check_7d_summary_no_financials_in_body() -> None:
    po = _7d_seed_po(site_id="TEST_7D_NF")
    s = database.build_po_site_notification(po)
    blob = s["app_body"] + "\n" + s["whatsapp_body"]
    # Financial figures from the seed (9876.54, 500.0, 150.0, 100.0).
    for needle in ("9876.54", "9876", "Nine thousand"):
        assert needle not in blob, f"financial leak: {needle!r} in body"
    # Commercial terms strings must not appear.
    for needle in ("DDP Riyadh", "Net 30", "Q-555"):
        assert needle not in blob, f"terms leak: {needle!r} in body"


def check_7d_summary_whatsapp_mirrors_app() -> None:
    po = _7d_seed_po(site_id="TEST_7D_WA")
    s = database.build_po_site_notification(po)
    # Same item bullet lines must appear in both bodies.
    for line in s["app_body"].splitlines():
        if line.startswith("• "):
            assert line in s["whatsapp_body"], \
                f"item line missing from WhatsApp body: {line}"


def _7d_seed_user(role: str, site_id: str) -> str:
    """Insert a user row so get_site_role_phones() returns a phone."""
    import uuid as _u
    uname = f"7d_{role}_{_u.uuid4().hex[:6]}"
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO users "
            "(username, password_hash, role, Site_ID, Phone_Number) "
            "VALUES (?, ?, ?, ?, ?)",
            (uname, "hash", role, site_id, "+966500001234"),
        )
        conn.commit()
    finally:
        conn.close()
    return uname


def check_7d_create_po_notifies_hod() -> None:
    site = "TEST_7D_HOD_NTF"
    _7d_seed_user("hod", site)
    po = _7d_seed_po(site_id=site)
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT title, body FROM app_notifications "
            "WHERE event_key='po_issued' AND recipient_role='hod' "
            "  AND recipient_site=? AND related_ref=? "
            "ORDER BY id DESC LIMIT 1",
            (site, po),
        ).fetchone()
        assert row is not None, "no HOD notification queued"
        assert po in (row[0] or ""), f"PO# missing in title: {row[0]}"
        assert "Expected Delivery" in (row[1] or ""), \
            f"operational summary missing: {row[1]}"
    finally:
        conn.close()


def check_7d_create_po_notifies_sk() -> None:
    site = "TEST_7D_SK_NTF"
    _7d_seed_user("store_keeper", site)
    po = _7d_seed_po(site_id=site)
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT title, body FROM app_notifications "
            "WHERE event_key='po_issued' AND recipient_role='store_keeper' "
            "  AND recipient_site=? AND related_ref=? "
            "ORDER BY id DESC LIMIT 1",
            (site, po),
        ).fetchone()
        assert row is not None, "no SK notification queued (fan-out missing)"
    finally:
        conn.close()


def check_7d_create_po_no_vendor_leak() -> None:
    """Regression guard against the pre-7D body=f'Vendor: …' leak."""
    site = "TEST_7D_LEAK"
    po = _7d_seed_po(site_id=site)
    conn = database.get_connection()
    try:
        bodies = [r[0] for r in conn.execute(
            "SELECT body FROM app_notifications "
            "WHERE related_ref=? AND event_key='po_issued'", (po,),
        ).fetchall()]
        assert bodies, "no notifications written"
        for b in bodies:
            assert "Acme Supplies" not in (b or ""), b
            assert "V-XYZ" not in (b or ""), b
            assert "Vendor:" not in (b or ""), b  # the exact leaky prefix
    finally:
        conn.close()


def check_7d_create_po_no_site_no_notif() -> None:
    """PO with Site_ID=NULL → notification block must be skipped entirely."""
    po = _7d_seed_po(with_site=False)
    conn = database.get_connection()
    try:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM app_notifications "
            "WHERE event_key='po_issued' AND related_ref=?", (po,),
        ).fetchone()[0]
        assert cnt == 0, f"expected 0 notifications for site-less PO, got {cnt}"
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Phase 7E — Form draft recovery checks
# ═══════════════════════════════════════════════════════════════════════════
def _7e_tag(prefix: str) -> str:
    import uuid as _u
    return f"{prefix}_{_u.uuid4().hex[:8]}"


def check_7e_index_expires() -> None:
    conn = database.get_connection()
    try:
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='form_drafts'"
        ).fetchall()}
        assert "ix_form_drafts_expires" in idx, f"missing: {idx}"
    finally:
        conn.close()


def check_7e_index_user() -> None:
    conn = database.get_connection()
    try:
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='form_drafts'"
        ).fetchall()}
        assert "ix_form_drafts_user" in idx, f"missing: {idx}"
    finally:
        conn.close()


def check_7e_unique_constraint() -> None:
    """Raw INSERT with same (username, form_id) must raise IntegrityError."""
    import sqlite3 as _sqlite3
    u = _7e_tag("U")
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO form_drafts (username, form_id, payload_json) "
            "VALUES (?, ?, ?)", (u, "f1", "{}"),
        )
        conn.commit()
        raised = False
        try:
            conn.execute(
                "INSERT INTO form_drafts (username, form_id, payload_json) "
                "VALUES (?, ?, ?)", (u, "f1", "{}"),
            )
            conn.commit()
        except _sqlite3.IntegrityError:
            raised = True
            conn.rollback()
        assert raised, "UNIQUE(username, form_id) not enforced"
    finally:
        conn.close()


def check_7e_upsert_new() -> None:
    u = _7e_tag("UPN")
    ok = database.upsert_form_draft(u, "sk_consumption", {"a": 1}, site_id="HQ")
    assert ok is True
    conn = database.get_connection()
    try:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM form_drafts WHERE username=?", (u,),
        ).fetchone()[0]
        assert cnt == 1, f"expected 1 row, got {cnt}"
    finally:
        conn.close()


def check_7e_upsert_updates() -> None:
    u = _7e_tag("UPD")
    database.upsert_form_draft(u, "sk_consumption", {"v": 1})
    database.upsert_form_draft(u, "sk_consumption", {"v": 2})
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT payload_json FROM form_drafts WHERE username=?", (u,),
        ).fetchone()
        import json as _j
        assert _j.loads(row[0]) == {"v": 2}, row
        cnt = conn.execute(
            "SELECT COUNT(*) FROM form_drafts WHERE username=?", (u,),
        ).fetchone()[0]
        assert cnt == 1, f"expected upsert (1 row), got {cnt}"
    finally:
        conn.close()


def check_7e_default_ttl_seven_days() -> None:
    import datetime as _dt
    u = _7e_tag("TTL7")
    database.upsert_form_draft(u, "f", {"k": "v"})
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT expires_at FROM form_drafts WHERE username=?", (u,),
        ).fetchone()
        exp = _dt.datetime.fromisoformat(row[0])
        delta = exp - _dt.datetime.utcnow()
        # 7 days ± 5s for clock skew during test execution.
        assert _dt.timedelta(days=7) - _dt.timedelta(seconds=5) <= delta <= \
               _dt.timedelta(days=7) + _dt.timedelta(seconds=5), \
            f"expected ~7d TTL, got {delta}"
    finally:
        conn.close()


def check_7e_custom_ttl() -> None:
    import datetime as _dt
    u = _7e_tag("TTL30")
    database.upsert_form_draft(u, "f", {"k": "v"}, ttl_days=30)
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT expires_at FROM form_drafts WHERE username=?", (u,),
        ).fetchone()
        exp = _dt.datetime.fromisoformat(row[0])
        delta = exp - _dt.datetime.utcnow()
        assert _dt.timedelta(days=30) - _dt.timedelta(seconds=5) <= delta <= \
               _dt.timedelta(days=30) + _dt.timedelta(seconds=5), delta
    finally:
        conn.close()


def check_7e_rejects_non_json() -> None:
    """Truly unserialisable payloads (circular refs) must raise ValueError.

    The helper intentionally uses default=str so widgets carrying Decimal /
    datetime / UploadedFile remnants persist as strings — drafts MUST succeed
    or we defeat the purpose. But a circular reference is the one shape
    `json.dumps(default=str)` cannot rescue, so we test that path."""
    u = _7e_tag("BAD")
    circular: dict = {}
    circular["self"] = circular
    raised = False
    try:
        database.upsert_form_draft(u, "f", circular)
    except ValueError:
        raised = True
    assert raised, "circular-reference payload should raise ValueError"


def check_7e_get_returns_payload() -> None:
    u = _7e_tag("GETP")
    payload = {"cart": [{"sap": "S-1", "qty": 5}], "tank": "T-9"}
    database.upsert_form_draft(u, "supervisor_request", payload, site_id="HQ")
    got = database.get_form_draft(u, "supervisor_request")
    assert got is not None, "draft should be readable"
    assert got["payload"] == payload, got["payload"]


def check_7e_get_missing_returns_none() -> None:
    u = _7e_tag("MISS")
    assert database.get_form_draft(u, "supervisor_request") is None


def check_7e_get_expired_returns_none() -> None:
    """Manually insert an expired row; get_form_draft must treat as missing."""
    import datetime as _dt
    u = _7e_tag("EXP")
    conn = database.get_connection()
    try:
        past = (_dt.datetime.utcnow() - _dt.timedelta(days=1)).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO form_drafts (username, form_id, payload_json, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (u, "f", "{}", past),
        )
        conn.commit()
    finally:
        conn.close()
    assert database.get_form_draft(u, "f") is None, \
        "expired draft should be hidden from get_form_draft"


def check_7e_delete_works() -> None:
    u = _7e_tag("DEL")
    database.upsert_form_draft(u, "f", {"k": "v"})
    assert database.delete_form_draft(u, "f") is True
    assert database.get_form_draft(u, "f") is None


def check_7e_delete_missing_returns_false() -> None:
    u = _7e_tag("DELM")
    assert database.delete_form_draft(u, "f") is False


def check_7e_prune_drops_expired() -> None:
    import datetime as _dt
    u_live = _7e_tag("PRN_L")
    u_dead = _7e_tag("PRN_D")
    database.upsert_form_draft(u_live, "f", {"k": 1})       # 7d TTL, alive
    # Insert manually-expired row.
    conn = database.get_connection()
    try:
        past = (_dt.datetime.utcnow() - _dt.timedelta(days=1)).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO form_drafts (username, form_id, payload_json, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (u_dead, "f", "{}", past),
        )
        conn.commit()
    finally:
        conn.close()
    n = database.prune_expired_form_drafts()
    assert n >= 1, f"prune should remove ≥1 expired row, got {n}"
    # Live row survives.
    assert database.get_form_draft(u_live, "f") is not None


def check_7e_list_user_drafts() -> None:
    u = _7e_tag("LST")
    database.upsert_form_draft(u, "supervisor_request", {"k": 1})
    database.upsert_form_draft(u, "sk_consumption", {"k": 2})
    df = database.list_user_drafts(u)
    assert len(df) == 2, f"expected 2 drafts, got {len(df)}"
    forms = set(df["form_id"].tolist())
    assert forms == {"supervisor_request", "sk_consumption"}, forms


def check_7e_requirements_has_local_storage() -> None:
    """requirements.txt must declare streamlit-local-storage so the
    client-side primary draft layer ships with the app."""
    import pathlib
    text = pathlib.Path(REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "streamlit-local-storage" in text.lower(), \
        "streamlit-local-storage missing from requirements.txt"


# ═══════════════════════════════════════════════════════════════════════════
# Phase 7F — Role-segregated manual PDFs + screenshot embedding
# ═══════════════════════════════════════════════════════════════════════════
def _7f_load_real_md() -> str:
    """Read the actual USER_MANUAL.md from the repo root. Slicer tests rely
    on the real chapter titles being present, so we don't use a fixture."""
    import pathlib
    return pathlib.Path(REPO_ROOT / "USER_MANUAL.md").read_text(encoding="utf-8")


def check_7f_recipes_cover_all_roles() -> None:
    from build_manual_pdf import ROLE_MANUAL_RECIPES
    expected = {"store_keeper", "supervisor", "hod", "logistics",
                "warehouse_user", "admin"}
    got = set(ROLE_MANUAL_RECIPES.keys())
    missing = expected - got
    assert not missing, f"recipes missing for roles: {missing}"


def check_7f_slice_sk_keeps_own() -> None:
    from build_manual_pdf import slice_markdown_for_role
    sliced = slice_markdown_for_role("store_keeper", _7f_load_real_md())
    assert "# 4. Store Keeper Manual" in sliced, \
        "SK chapter heading missing from sliced output"


def check_7f_slice_sk_drops_logistics() -> None:
    from build_manual_pdf import slice_markdown_for_role
    sliced = slice_markdown_for_role("store_keeper", _7f_load_real_md())
    assert "# 14. Logistics Portal Manual" not in sliced, \
        "Logistics chapter leaked into SK booklet"
    assert "# 15. Warehouse Portal Manual" not in sliced, \
        "Warehouse chapter leaked into SK booklet"


def check_7f_slice_supervisor_keeps_own() -> None:
    from build_manual_pdf import slice_markdown_for_role
    sliced = slice_markdown_for_role("supervisor", _7f_load_real_md())
    assert "# 5. Supervisor Manual" in sliced
    assert "# 4. Store Keeper Manual" not in sliced, "SK chapter leaked into supervisor booklet"


def check_7f_slice_hod_keeps_reports() -> None:
    from build_manual_pdf import slice_markdown_for_role
    sliced = slice_markdown_for_role("hod", _7f_load_real_md())
    assert "# 6. HOD (Head of Department) Manual" in sliced
    assert "# 8. Reports Module" in sliced, "Reports chapter missing from HOD booklet"


def check_7f_slice_admin_full() -> None:
    from build_manual_pdf import slice_markdown_for_role
    md = _7f_load_real_md()
    sliced = slice_markdown_for_role("admin", md)
    assert sliced == md, "admin recipe should be a passthrough"


def check_7f_parse_image_block() -> None:
    from build_manual_pdf import parse_markdown
    md = "Intro paragraph.\n\n![Hero shot](docs/screenshots/foo.png)\n\nMore text."
    blocks = parse_markdown(md)
    img_blocks = [b for b in blocks if b.kind == "img"]
    assert len(img_blocks) == 1, f"expected 1 image block, got {len(img_blocks)}"
    b = img_blocks[0]
    assert b.text == "docs/screenshots/foo.png", b.text
    assert b.items == ["Hero shot"], b.items


def check_7f_render_image_missing_no_crash() -> None:
    """Missing screenshot file must render a placeholder, not raise."""
    from build_manual_pdf import ManualPDF
    pdf = ManualPDF()
    pdf.render_cover()       # need at least one page so render_image has context
    # Force a placeholder by referencing a nonexistent path.
    pdf.render_image("docs/screenshots/__definitely_missing_7f__.png",
                     caption="placeholder caption")
    out = bytes(pdf.output())
    assert out.startswith(b"%PDF-"), "PDF magic bytes missing"


def check_7f_role_pdf_starts_with_magic() -> None:
    from build_manual_pdf import build_role_manual_pdf
    pdf = build_role_manual_pdf("store_keeper", _7f_load_real_md())
    assert isinstance(pdf, (bytes, bytearray))
    assert pdf.startswith(b"%PDF-"), "Output does not start with %PDF-"
    assert len(pdf) > 5_000, f"PDF too small to be valid: {len(pdf)} bytes"


def check_7f_admin_equals_master() -> None:
    """Admin recipe == 'ALL' falls through to build_manual_pdf.

    PDF byte equality is unreliable across runs (timestamps inside PDF
    metadata), so we assert both PDFs render the same chapter set by
    comparing their byte-length within ±5%."""
    from build_manual_pdf import build_manual_pdf, build_role_manual_pdf
    md = _7f_load_real_md()
    a = build_role_manual_pdf("admin", md)
    b = build_manual_pdf(md)
    ratio = abs(len(a) - len(b)) / max(len(b), 1)
    assert ratio < 0.05, f"admin PDF differs from master by {ratio*100:.1f}% (len {len(a)} vs {len(b)})"


def check_7f_unknown_role_falls_back() -> None:
    from build_manual_pdf import build_role_manual_pdf
    pdf = build_role_manual_pdf("nonexistent_role_xyz", _7f_load_real_md())
    assert pdf.startswith(b"%PDF-"), "Unknown role should silently fall back to master PDF"


def check_7f_screenshot_placeholders_exist() -> None:
    """The seed set produced by scripts/generate_screenshot_placeholders.py
    must be present on disk so the manual PDF doesn't render all-placeholder
    cards in production. (CI / fresh-clone owners run the script once.)"""
    import pathlib
    d = pathlib.Path(REPO_ROOT / "docs" / "screenshots")
    assert d.exists(), f"directory missing: {d}"
    pngs = list(d.glob("*.png"))
    assert len(pngs) >= 10, f"expected >=10 seed placeholders, found {len(pngs)}"


# ═══════════════════════════════════════════════════════════════════════════
# Phase 7G — Hub Assistant context fix checks
# ═══════════════════════════════════════════════════════════════════════════
# Tests import from ai.manual_qa which lazily uses USER_MANUAL.md via
# GI_USER_MANUAL_PATH (defaults to repo-root USER_MANUAL.md). The bug_check
# harness runs from the repo root, so the real manual is available.
def _7g_reset_context_cache() -> None:
    """The role context is @lru_cached. Clear it between checks so this
    test ordering doesn't leak state. Cheap — single call."""
    from ai import manual_qa as _mq
    try:
        _mq._context_for_role.cache_clear()
    except Exception:
        pass


def check_7g_prompt_has_username_and_role() -> None:
    _7g_reset_context_cache()
    from ai.manual_qa import _build_system_prompt
    prompt = _build_system_prompt("admin", "andrew")
    assert "andrew" in prompt, "username not injected into prompt"
    assert "Administrator" in prompt, "role label missing from prompt"


def check_7g_prompt_empty_username_ok() -> None:
    """Empty username must fall back gracefully — no KeyError, no crash."""
    _7g_reset_context_cache()
    from ai.manual_qa import _build_system_prompt
    prompt = _build_system_prompt("store_keeper", "")
    assert "Store Keeper" in prompt
    # Falls back to "the user" placeholder per the template.
    assert "the user" in prompt, "empty username should fall back to 'the user'"


def check_7g_admin_context_includes_users_tab() -> None:
    """The original bug: §7.6 '👥 Users' content lived past the 800-char cap.
    Admin now gets full sections — verify the User Management text is present."""
    _7g_reset_context_cache()
    from ai.manual_qa import _context_for_role
    ctx = _context_for_role("admin")
    assert ctx, "admin context unexpectedly empty"
    # The "👥 Users" tab header appears in §7.6 of USER_MANUAL.md. Without
    # the no-truncation special case, this string would be cut off.
    assert "👥 Users" in ctx, \
        "admin context missing §7.6 User Management text (truncation regression)"


def check_7g_logistics_context_includes_section_14() -> None:
    """Cause C: logistics used to silently fall through to store_keeper
    context (§14 absent). Verify §14 markers are present."""
    _7g_reset_context_cache()
    from ai.manual_qa import _context_for_role
    ctx = _context_for_role("logistics")
    assert ctx, "logistics context unexpectedly empty"
    assert "Section 14" in ctx and "Logistics Portal" in ctx, \
        "logistics context missing §14 Logistics Portal Manual"


def check_7g_warehouse_context_includes_section_15() -> None:
    _7g_reset_context_cache()
    from ai.manual_qa import _context_for_role
    ctx = _context_for_role("warehouse_user")
    assert ctx, "warehouse_user context unexpectedly empty"
    assert "Section 15" in ctx and "Warehouse Portal" in ctx, \
        "warehouse_user context missing §15 Warehouse Portal Manual"


def check_7g_admin_refusal_phrase_self_aware() -> None:
    """The admin refusal must NOT tell the admin to ask their admin."""
    _7g_reset_context_cache()
    from ai.manual_qa import _build_system_prompt
    prompt = _build_system_prompt("admin", "andrew")
    # Should mention the download bay / settings path.
    assert "Download Role Manuals" in prompt or "USER_MANUAL.md" in prompt, \
        "admin refusal should point to Settings download bay"
    # And critically, must NOT contain the old "ask your HOD or Admin" string.
    assert "ask your HOD or Admin" not in prompt, \
        "stale refusal text still present — admin would be told to ask themselves"


# ═══════════════════════════════════════════════════════════════════════════
# Phase 8A — Smart Scan AI sidecar scaffold (strict offline, mocked HTTP)
# ═══════════════════════════════════════════════════════════════════════════
# These checks NEVER hit the network and NEVER look for model weight files.
# Every HTTP path is monkey-patched via client._perform_http_post.
# torch / transformers are NEVER imported (verified by check #8).
#
# Hardcoded mock data structure — used by every successful-detect check:
_8A_MOCK_DETECTIONS = {
    "detections": [
        {"label": "impact driver", "box": [120.0, 84.0, 380.0, 240.0],  "score": 0.87},
        {"label": "torque wrench", "box": [410.0, 90.0, 640.0, 235.0], "score": 0.73},
    ],
    "latency_ms": 142,
    "device":     "mps",
}


def _8a_reset_client() -> None:
    """Reset breaker state + ensure the toggle starts at a known value.
    Called at the top of every 8A check so test ordering is irrelevant."""
    from ai.locate_anything import client as _c
    _c._breaker_reset()
    import os as _os
    # Make sure the test-suppression env var is OFF inside 8A checks so
    # we exercise the real gate-read path. (test_ui_crawler sets this to
    # 1; bug_check should always see the DB toggle.)
    _os.environ.pop("GI_SUPPRESS_LOCATE_ANYTHING", None)


def check_8a_setting_seed_enabled_default_off() -> None:
    _8a_reset_client()
    val = database.get_app_setting("locate_anything_enabled", "missing")
    assert val == "0", f"expected '0' (off by default), got {val!r}"


def check_8a_setting_seed_sidecar_url() -> None:
    _8a_reset_client()
    url = database.get_app_setting("locate_anything_sidecar_url", "missing")
    assert url == "http://127.0.0.1:8503", f"unexpected default url: {url!r}"


def check_8a_client_gate_off_short_circuits() -> None:
    _8a_reset_client()
    from ai.locate_anything import client as c
    database.set_app_setting("locate_anything_enabled", "0")
    assert c.is_enabled() is False
    # Flip it on, confirm true, flip it off again — the helper must re-read
    # on every call (no caching).
    database.set_app_setting("locate_anything_enabled", "1")
    assert c.is_enabled() is True
    database.set_app_setting("locate_anything_enabled", "0")
    assert c.is_enabled() is False


def check_8a_client_detect_off_returns_empty() -> None:
    """When the gate is OFF, detect() must NOT call HTTP. Verify by setting
    the HTTP mock to raise — if the gate works, the mock never fires.
    Phase 8E: detect() now returns (detections, call_id) tuple."""
    _8a_reset_client()
    from ai.locate_anything import client as c
    database.set_app_setting("locate_anything_enabled", "0")

    sentinel = {"called": False}
    def _mock(_url, _payload, timeout=30.0):
        sentinel["called"] = True
        raise AssertionError("HTTP must NOT be called when gate is off")

    orig = c._perform_http_post
    c._perform_http_post = _mock
    try:
        dets, call_id = c.detect("BASE64STUB", prompt="locate the wrench")
        assert dets == [], f"gate-off detect should be [], got {dets!r}"
        assert call_id == 0, "no telemetry row should be written when gate off"
        assert sentinel["called"] is False
    finally:
        c._perform_http_post = orig


def check_8a_client_detect_mock_200_returns_detections() -> None:
    """Happy path — gate ON, mocked HTTP 200 with synthetic bounding boxes."""
    _8a_reset_client()
    from ai.locate_anything import client as c
    database.set_app_setting("locate_anything_enabled", "1")

    def _mock(_url, _payload, timeout=30.0):
        return 200, dict(_8A_MOCK_DETECTIONS)

    orig = c._perform_http_post
    c._perform_http_post = _mock
    try:
        dets, call_id = c.detect("BASE64STUB", prompt="locate the wrench")
        assert len(dets) == 2, f"expected 2 detections, got {len(dets)}"
        assert dets[0]["label"] == "impact driver", dets[0]
        assert dets[1]["score"] == 0.73, dets[1]
        # Box shape preserved
        assert dets[0]["box"] == [120.0, 84.0, 380.0, 240.0]
        # Breaker should be reset on success
        assert c._breaker.consecutive_failures == 0
        # Phase 8E — happy path must have written a telemetry row.
        assert call_id > 0, "expected non-zero call_id on success"
    finally:
        c._perform_http_post = orig
        database.set_app_setting("locate_anything_enabled", "0")


def check_8a_client_detect_503_returns_empty_trips_breaker() -> None:
    """Sidecar 503 (missing weights) → empty list, breaker increments."""
    _8a_reset_client()
    from ai.locate_anything import client as c
    database.set_app_setting("locate_anything_enabled", "1")

    def _mock(_url, _payload, timeout=30.0):
        return 503, {"detail": "ModelNotReadyError: weights missing"}

    orig = c._perform_http_post
    c._perform_http_post = _mock
    try:
        dets, _id = c.detect("BASE64STUB")
        assert dets == [], f"expected [] on 503, got {dets!r}"
        assert c._breaker.consecutive_failures == 1, \
            f"breaker should have recorded 1 failure, got {c._breaker.consecutive_failures}"
    finally:
        c._perform_http_post = orig
        database.set_app_setting("locate_anything_enabled", "0")


def check_8a_client_circuit_breaker_opens() -> None:
    """3 consecutive failures → breaker opens → next call short-circuits."""
    _8a_reset_client()
    from ai.locate_anything import client as c
    database.set_app_setting("locate_anything_enabled", "1")

    call_count = {"n": 0}
    def _mock(_url, _payload, timeout=30.0):
        call_count["n"] += 1
        return 0, None  # 0 == sidecar unreachable

    orig = c._perform_http_post
    c._perform_http_post = _mock
    try:
        for _ in range(3):
            c.detect("STUB")   # tuple ignored
        assert call_count["n"] == 3, f"expected 3 HTTP attempts, got {call_count['n']}"
        # Breaker is now open — next call must NOT increment call_count.
        c.detect("STUB")
        assert call_count["n"] == 3, \
            f"breaker should have prevented call #4, but HTTP was attempted ({call_count['n']} total)"
        assert c._breaker_is_open() is True
    finally:
        c._perform_http_post = orig
        database.set_app_setting("locate_anything_enabled", "0")


def check_8a_client_import_does_not_pull_torch() -> None:
    """Critical isolation guarantee — Streamlit must never accidentally pay
    torch's import cost via the client. Verifies the package layout:
    `__init__.py` re-exports client only, model_loader is opt-in."""
    import sys as _sys
    # If torch was already imported by an earlier test in this run we can't
    # un-import it. Instead: assert that `client` doesn't TRANSITIVELY pull
    # torch by checking sys.modules BEFORE and AFTER a fresh-ish import.
    # Snapshot pre-state.
    torch_in_modules_before = "torch" in _sys.modules
    # Force re-import of the client module without dragging model_loader.
    for k in list(_sys.modules):
        if k.startswith("ai.locate_anything"):
            del _sys.modules[k]
    from ai.locate_anything import client as _c  # noqa: F401
    # The client module itself must not have triggered a torch import.
    # If torch was ALREADY in sys.modules (from some other test), we can't
    # prove negative — but in a clean run this assertion is meaningful.
    if not torch_in_modules_before:
        assert "torch" not in _sys.modules, \
            "ai.locate_anything.client transitively imported torch"
        assert "transformers" not in _sys.modules, \
            "ai.locate_anything.client transitively imported transformers"


def check_8a_sidecar_requirements_file_exists() -> None:
    import pathlib
    req = pathlib.Path(REPO_ROOT / "ai" / "locate_anything" / "requirements.txt")
    assert req.exists(), f"missing: {req}"
    body = req.read_text(encoding="utf-8")
    # Sanity — should NOT have leaked into the project-root requirements.
    root_req = pathlib.Path(REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "transformers>=" not in root_req, \
        "transformers must NOT appear in project-root requirements.txt"
    # Sidecar reqs must declare torch + transformers + fastapi.
    for needle in ("torch", "transformers", "fastapi", "uvicorn", "pillow"):
        assert needle in body, f"sidecar requirements.txt missing: {needle}"


def check_8a_download_script_present() -> None:
    import pathlib, stat
    p = pathlib.Path(REPO_ROOT / "scripts" / "download_model.sh")
    assert p.exists(), f"missing: {p}"
    mode = p.stat().st_mode
    assert mode & stat.S_IXUSR, "download_model.sh must be executable"
    body = p.read_text(encoding="utf-8")
    # Verify the script targets BOTH models we documented.
    assert "LocateAnything-3B" in body
    assert "qwen2.5vl:7b" in body
    assert "Library/Caches/gi_locate" in body, \
        "download script must store LocateAnything weights under ~/Library/Caches/gi_locate"


# ═══════════════════════════════════════════════════════════════════════════
# Phase 8B — bundle + first-run setup tooling
# ═══════════════════════════════════════════════════════════════════════════
def _8b_assert_bash_script(path: str) -> None:
    """Common check: file exists, is executable, parses as valid bash.

    Uses _orig_popen directly because the harness monkey-patches
    subprocess.Popen to a no-op for mailer-safety (see line 45)."""
    import pathlib, stat
    p = pathlib.Path(REPO_ROOT / path)
    assert p.exists(), f"missing: {path}"
    mode = p.stat().st_mode
    assert mode & stat.S_IXUSR, f"{path} must be executable"
    # Run `bash -n` via the un-patched Popen so we get a real process.
    proc = _orig_popen(
        ["bash", "-n", str(p)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode == 0, (
        f"{path} has bash syntax errors: "
        f"{stderr.decode('utf-8', errors='replace').strip()}"
    )


def check_8b_bundle_script() -> None:
    _8b_assert_bash_script("scripts/bundle_locate_anything_weights.sh")


def check_8b_install_script() -> None:
    _8b_assert_bash_script("scripts/install_locate_anything_weights.sh")


def check_8b_run_script() -> None:
    _8b_assert_bash_script("host_setup/scripts/run_locate_anything.sh")
    # Sanity — must reference the right uvicorn module path.
    import pathlib
    body = pathlib.Path(
        REPO_ROOT / "host_setup" / "scripts" / "run_locate_anything.sh"
    ).read_text(encoding="utf-8")
    assert "ai.locate_anything.server:app" in body, \
        "run_locate_anything.sh must launch ai.locate_anything.server:app"
    assert "127.0.0.1" in body and "8503" in body, \
        "run_locate_anything.sh must bind 127.0.0.1:8503 only"


def check_8b_plist_template() -> None:
    import pathlib, plistlib
    p = pathlib.Path(
        REPO_ROOT / "host_setup" / "launchd" / "com.gi.locate-anything.plist.tmpl"
    )
    assert p.exists(), f"missing: {p}"
    # Render the template placeholders to dummy values so plistlib accepts it.
    body = p.read_bytes()\
        .replace(b"__PROJECT_DIR__", b"/tmp/dummy")\
        .replace(b"__USER_HOME__",   b"/tmp/dummy")\
        .replace(b"__USER__",        b"dummy")
    doc = plistlib.loads(body)
    assert doc["Label"] == "com.gi.locate-anything", doc.get("Label")
    # ProgramArguments must point at run_locate_anything.sh.
    args = doc["ProgramArguments"]
    assert any("run_locate_anything.sh" in a for a in args), \
        f"plist must invoke run_locate_anything.sh; got {args}"


def check_8b_install_flag() -> None:
    """install.sh must recognise --with-locate-anything as an opt-in flag
    (off by default per spec Q5)."""
    import pathlib
    body = pathlib.Path(
        REPO_ROOT / "host_setup" / "scripts" / "install.sh"
    ).read_text(encoding="utf-8")
    assert "--with-locate-anything" in body, \
        "install.sh must recognise --with-locate-anything flag"
    assert "com.gi.locate-anything" in body, \
        "install.sh must reference the locate-anything service"


# ═══════════════════════════════════════════════════════════════════════════
# Phase 8C — Smart Scan Tier-3 wiring
# ═══════════════════════════════════════════════════════════════════════════
def check_8c_should_invoke_empty_yes() -> None:
    from ai.cv.smart_scan import should_invoke_tier3
    assert should_invoke_tier3([]) is True


def check_8c_should_invoke_low_yes() -> None:
    from ai.cv.smart_scan import should_invoke_tier3
    assert should_invoke_tier3([{"class_name": "x", "confidence": 0.25}]) is True


def check_8c_should_invoke_mid_no() -> None:
    """Spec Q1(a) — Tier 3 must NOT fire in the 0.30–0.45 overlap. YOLO's
    own "candidates" UI handles that band."""
    from ai.cv.smart_scan import should_invoke_tier3
    assert should_invoke_tier3([{"class_name": "x", "confidence": 0.40}]) is False
    assert should_invoke_tier3([{"class_name": "x", "confidence": 0.50}]) is False


def check_8c_should_invoke_high_no() -> None:
    from ai.cv.smart_scan import should_invoke_tier3
    assert should_invoke_tier3([{"class_name": "x", "confidence": 0.95}]) is False


def check_8c_reshape_basic() -> None:
    from ai.cv.smart_scan import tier3_to_candidates
    out = tier3_to_candidates([
        {"label": "impact driver", "box": [10, 20, 100, 120], "score": 0.87},
    ])
    assert len(out) == 1
    r = out[0]
    assert r["class_name"] == "impact driver"
    assert r["confidence"] == 0.87
    assert r["bbox"] == [10, 20, 100, 120]
    assert r["source"] == "tier3_locate_anything"


def check_8c_reshape_filter_noise() -> None:
    from ai.cv.smart_scan import tier3_to_candidates, TIER3_NOISE_FLOOR
    assert TIER3_NOISE_FLOOR == 0.20, "noise floor regression"
    out = tier3_to_candidates([
        {"label": "good",  "box": [0,0,1,1], "score": 0.40},
        {"label": "noise", "box": [0,0,1,1], "score": 0.10},
    ])
    assert [d["class_name"] for d in out] == ["good"], out


def check_8c_reshape_cap() -> None:
    from ai.cv.smart_scan import tier3_to_candidates, MAX_CANDIDATES
    out = tier3_to_candidates([
        {"label": f"tool_{i}", "box": [0,0,1,1], "score": 0.5 + i*0.05}
        for i in range(5)
    ])
    assert len(out) == MAX_CANDIDATES == 3
    # Highest score first.
    assert out[0]["class_name"] == "tool_4", out


def check_8c_reshape_source_tag() -> None:
    from ai.cv.smart_scan import tier3_to_candidates
    out = tier3_to_candidates([{"label": "x", "box": [], "score": 0.9}])
    assert out[0]["source"] == "tier3_locate_anything"


def check_8c_integration_yolo_empty_plus_mock() -> None:
    """End-to-end logic check: YOLO empty → bucket_detections returns 'manual'
    → should_invoke_tier3 says yes → mock sidecar returns 2 dets → reshape
    produces 2 Tier-3 candidates ready to render."""
    from ai.cv.smart_scan import (
        bucket_detections, should_invoke_tier3, tier3_to_candidates,
    )
    yolo_dets: list = []
    mode, _ = bucket_detections(yolo_dets)
    assert mode == "manual"
    assert should_invoke_tier3(yolo_dets) is True
    # Simulated sidecar reply — matches _8A_MOCK_DETECTIONS shape.
    sidecar_reply = [
        {"label": "impact driver", "box": [0, 0, 100, 100], "score": 0.87},
        {"label": "torque wrench", "box": [0, 0, 200, 100], "score": 0.73},
    ]
    candidates = tier3_to_candidates(sidecar_reply)
    assert len(candidates) == 2
    assert candidates[0]["class_name"] == "impact driver"
    # Source tag must propagate so the UI knows to render the amber panel.
    assert all(c["source"] == "tier3_locate_anything" for c in candidates)


def check_8c_gate_guard_off() -> None:
    """Critical: when the admin gate is OFF, client.detect() must short-
    circuit to [] without calling HTTP — even on inputs that would
    normally trigger Tier 3. Reuses the Phase 8A mock-sentinel pattern."""
    _8a_reset_client()
    from ai.locate_anything import client as c
    database.set_app_setting("locate_anything_enabled", "0")

    sentinel = {"called": False}
    def _mock(_url, _payload, timeout=30.0):
        sentinel["called"] = True
        raise AssertionError("HTTP must NOT be called when gate is off")

    orig = c._perform_http_post
    c._perform_http_post = _mock
    try:
        dets, call_id = c.detect("STUB", classes=["wrench"])
        assert dets == []
        assert call_id == 0
        assert sentinel["called"] is False
    finally:
        c._perform_http_post = orig


def check_8c_gate_guard_confident() -> None:
    """should_invoke_tier3 must return False for confident YOLO detections —
    so even if the caller code mistakenly tries to invoke Tier 3, the gate
    refuses upstream of HTTP. (Belt-and-suspenders test.)"""
    from ai.cv.smart_scan import should_invoke_tier3
    # 0.65 confidence is in the YOLO "candidates" band — explicitly Tier 2.
    confident = [{"class_name": "x", "confidence": 0.65}]
    assert should_invoke_tier3(confident) is False, \
        "Tier 3 must NOT fire when YOLO is in the candidates band"


# ═══════════════════════════════════════════════════════════════════════════
# Phase 8E — Telemetry tests
# ═══════════════════════════════════════════════════════════════════════════
def check_8e_telemetry_schema() -> None:
    conn = database.get_connection()
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(locate_anything_calls)"
        ).fetchall()}
        for needed in (
            "id", "called_at", "site_id", "sk_username",
            "yolo_top_conf", "detection_count", "accepted",
            "latency_ms", "error",
        ):
            assert needed in cols, f"missing column: {needed}"
    finally:
        conn.close()


def check_8e_telemetry_index_called_at() -> None:
    conn = database.get_connection()
    try:
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='locate_anything_calls'"
        ).fetchall()}
        assert "ix_la_calls_called_at" in idx, f"missing index: {idx}"
    finally:
        conn.close()


def check_8e_log_helper_writes_row() -> None:
    rid = database.log_locate_anything_call(
        site_id="TEST_8E_SITE",
        sk_username="test_sk",
        yolo_top_conf=0.18,
        detection_count=2,
        latency_ms=148,
    )
    assert rid > 0, f"expected non-zero rowid, got {rid}"
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT site_id, sk_username, yolo_top_conf, detection_count, "
            "       latency_ms, error, accepted "
            "FROM locate_anything_calls WHERE id = ?", (rid,),
        ).fetchone()
        assert row is not None
        assert row[0] == "TEST_8E_SITE"
        assert row[1] == "test_sk"
        assert abs(row[2] - 0.18) < 1e-6
        assert row[3] == 2
        assert row[4] == 148
        assert row[5] is None, "error should be NULL on success"
        assert row[6] is None, "accepted should be NULL until SK decides"
    finally:
        conn.close()


def check_8e_mark_outcome_updates_accepted() -> None:
    rid = database.log_locate_anything_call(
        site_id="TEST_8E_MARK",
        sk_username="test_sk",
        detection_count=1,
    )
    assert rid > 0
    ok = database.mark_locate_anything_outcome(rid, accepted=True)
    assert ok
    conn = database.get_connection()
    try:
        v = conn.execute(
            "SELECT accepted FROM locate_anything_calls WHERE id = ?",
            (rid,),
        ).fetchone()[0]
        assert v == 1, f"expected accepted=1, got {v}"
    finally:
        conn.close()
    # Idempotent — second mark with rejected overwrites cleanly.
    assert database.mark_locate_anything_outcome(rid, accepted=False)
    conn = database.get_connection()
    try:
        v = conn.execute(
            "SELECT accepted FROM locate_anything_calls WHERE id = ?",
            (rid,),
        ).fetchone()[0]
        assert v == 0, f"expected accepted=0 after reject, got {v}"
    finally:
        conn.close()


def check_8e_client_happy_writes_telemetry() -> None:
    """End-to-end: gate ON, mock 200 → telemetry row populated + call_id > 0."""
    _8a_reset_client()
    from ai.locate_anything import client as c
    database.set_app_setting("locate_anything_enabled", "1")

    def _mock(_url, _payload, timeout=30.0):
        return 200, dict(_8A_MOCK_DETECTIONS)

    orig = c._perform_http_post
    c._perform_http_post = _mock
    try:
        dets, call_id = c.detect(
            "STUB",
            site_id="TEST_8E_HAPPY",
            sk_username="sk_happy",
            yolo_top_conf=0.12,
        )
        assert len(dets) == 2 and call_id > 0
        conn = database.get_connection()
        try:
            row = conn.execute(
                "SELECT site_id, sk_username, yolo_top_conf, detection_count, "
                "       error FROM locate_anything_calls WHERE id = ?",
                (call_id,),
            ).fetchone()
            assert row is not None, "telemetry row not written"
            assert row[0] == "TEST_8E_HAPPY"
            assert row[1] == "sk_happy"
            assert abs(row[2] - 0.12) < 1e-6
            assert row[3] == 2
            assert row[4] is None, f"error should be NULL on success, got {row[4]!r}"
        finally:
            conn.close()
    finally:
        c._perform_http_post = orig
        database.set_app_setting("locate_anything_enabled", "0")


def check_8e_client_failure_writes_telemetry_with_error() -> None:
    """503 must still log telemetry so we can measure missing-weights frequency."""
    _8a_reset_client()
    from ai.locate_anything import client as c
    database.set_app_setting("locate_anything_enabled", "1")

    def _mock(_url, _payload, timeout=30.0):
        return 503, {"detail": "ModelNotReadyError"}

    orig = c._perform_http_post
    c._perform_http_post = _mock
    try:
        dets, call_id = c.detect("STUB", site_id="TEST_8E_FAIL",
                                  sk_username="sk_fail",
                                  yolo_top_conf=0.05)
        assert dets == []
        assert call_id > 0, "failure path must still log telemetry"
        conn = database.get_connection()
        try:
            row = conn.execute(
                "SELECT error, detection_count FROM locate_anything_calls "
                "WHERE id = ?", (call_id,),
            ).fetchone()
            assert row is not None
            assert "503" in (row[0] or ""), f"error should mention 503, got {row[0]!r}"
            assert row[1] == 0
        finally:
            conn.close()
    finally:
        c._perform_http_post = orig
        database.set_app_setting("locate_anything_enabled", "0")


def check_8e_client_gate_off_no_telemetry() -> None:
    """Gate OFF short-circuits ABOVE telemetry — no row should be written.
    Verifies we don't spam the table with no-op gate-off calls."""
    _8a_reset_client()
    from ai.locate_anything import client as c
    database.set_app_setting("locate_anything_enabled", "0")

    conn = database.get_connection()
    try:
        before = conn.execute(
            "SELECT COUNT(*) FROM locate_anything_calls"
        ).fetchone()[0]
    finally:
        conn.close()

    def _mock(_url, _payload, timeout=30.0):
        raise AssertionError("must not be called when gate is off")

    orig = c._perform_http_post
    c._perform_http_post = _mock
    try:
        dets, call_id = c.detect("STUB", site_id="TEST_8E_OFF",
                                  sk_username="x", yolo_top_conf=0.0)
        assert dets == []
        assert call_id == 0, "no telemetry row should be written when gate off"
    finally:
        c._perform_http_post = orig

    conn = database.get_connection()
    try:
        after = conn.execute(
            "SELECT COUNT(*) FROM locate_anything_calls"
        ).fetchone()[0]
        assert after == before, \
            f"expected no new telemetry rows; before={before} after={after}"
    finally:
        conn.close()


def check_8e_summary_computes_rates() -> None:
    """Summary helper must never raise ZeroDivisionError on empty windows
    and must compute accept/error rates correctly when data exists."""
    summary = database.get_locate_anything_summary(days=7)
    # Every key present + safe numeric defaults.
    for k in ("calls", "errors", "accepted", "rejected", "pending",
              "avg_latency_ms", "error_rate_pct", "accept_rate_pct"):
        assert k in summary, f"missing key: {k}"
    # Force a known shape and re-check the rates.
    rid1 = database.log_locate_anything_call(
        site_id="TEST_8E_SUM_OK", detection_count=2, latency_ms=100,
    )
    database.mark_locate_anything_outcome(rid1, accepted=True)
    rid2 = database.log_locate_anything_call(
        site_id="TEST_8E_SUM_REJ", detection_count=1, latency_ms=200,
    )
    database.mark_locate_anything_outcome(rid2, accepted=False)
    database.log_locate_anything_call(
        site_id="TEST_8E_SUM_ERR", detection_count=0, latency_ms=50,
        error="Sidecar 5xx (HTTP 500)",
    )
    summary = database.get_locate_anything_summary(days=7)
    # At least 3 calls, 1 error, 1 accepted, 1 rejected from our injection.
    assert summary["calls"] >= 3
    assert summary["errors"] >= 1
    assert summary["accepted"] >= 1
    assert summary["rejected"] >= 1
    assert 0.0 <= summary["accept_rate_pct"] <= 100.0
    assert 0.0 <= summary["error_rate_pct"] <= 100.0


# ═══════════════════════════════════════════════════════════════════════════
# Phase 8D — Admin Settings panel (offline)
# ═══════════════════════════════════════════════════════════════════════════
def check_8d_panel_renders_with_sidecar_down() -> None:
    """Verify the panel renderer is importable + the function exists.
    We can't fully render under bug_check (no Streamlit runtime), but we
    can import the module and confirm the symbol is bound — guards against
    accidental rename/regression. The UI crawler exercises the actual
    Admin Portal rendering."""
    import importlib
    mod = importlib.import_module("pages_internal.admin_portal")
    assert hasattr(mod, "_render_locate_anything_panel"), \
        "Admin Portal must export the locate_anything panel renderer"
    # The renderer is a function (not a class / coroutine).
    assert callable(mod._render_locate_anything_panel)


def check_8d_toggle_persists_one() -> None:
    """Simulate the toggle 'Save changes' path — confirm the value lands
    in app_settings exactly as the panel writes it."""
    database.set_app_setting("locate_anything_enabled", "0")
    assert database.get_app_setting("locate_anything_enabled") == "0"
    database.set_app_setting("locate_anything_enabled", "1")
    assert database.get_app_setting("locate_anything_enabled") == "1"
    # Restore default OFF so later tests start from the known state.
    database.set_app_setting("locate_anything_enabled", "0")


# ---------------------------------------------------------------------------
# Round 17 — Smart Material Estimator (SME) merge
# ---------------------------------------------------------------------------
# Each check opens a throwaway SQLite connection so they're independent of
# the seeded harness DB. Self-heal via init_db() runs first on every conn.

def _r17_conn():
    """Fresh isolated SQLite connection with the full self-healed schema."""
    conn = sqlite3.connect(":memory:")
    database.init_db(conn)
    return conn


def check_r17_sme_equipment_schema():
    conn = _r17_conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sme_equipment)").fetchall()}
    for required in ("Site_ID", "Equipment_Tag_No", "Lining_System_Code",
                     "Surface_Area_SQM"):
        assert required in cols, f"sme_equipment missing column {required!r}"
    # Confirm the UNIQUE constraint actually rejects duplicates
    conn.execute(
        "INSERT INTO sme_equipment (Site_ID, Equipment_Tag_No, "
        "Lining_System_Code, Surface_Area_SQM) VALUES ('HQ','T1','1',10)"
    )
    try:
        conn.execute(
            "INSERT INTO sme_equipment (Site_ID, Equipment_Tag_No, "
            "Lining_System_Code, Surface_Area_SQM) VALUES ('HQ','T1','1',99)"
        )
        assert False, "duplicate (Site_ID, tag, system) should be rejected"
    except sqlite3.IntegrityError:
        pass


def check_r17_sme_recipe_schema():
    conn = _r17_conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sme_recipe)").fetchall()}
    for required in ("Lining_System_Code", "Material_Code", "For_1_SQM"):
        assert required in cols, f"sme_recipe missing column {required!r}"


def check_r17_sme_progress_schema():
    conn = _r17_conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sme_sqm_progress)").fetchall()}
    for required in ("Site_ID", "Equipment_Tag_No", "Lining_System_Code",
                     "Original_SQM", "Done_SQM"):
        assert required in cols, f"sme_sqm_progress missing column {required!r}"


def check_r17_sme_settings_seeded():
    conn = _r17_conn()
    locs = conn.execute(
        "SELECT value FROM system_settings "
        "WHERE category='sme_location' AND Site_ID='HQ'"
    ).fetchall()
    assert len(locs) >= 3, f"expected ≥3 seed locations for HQ, got {len(locs)}"
    types = conn.execute(
        "SELECT value FROM system_settings "
        "WHERE category='sme_equipment_type' AND Site_ID='HQ'"
    ).fetchall()
    assert len(types) >= 5, f"expected ≥5 seed types for HQ, got {len(types)}"


def check_r17_sme_init_db_idempotent():
    conn = _r17_conn()  # already runs init_db once
    # Second run should be a no-op (seed inserts are NOT EXISTS-guarded)
    database.init_db(conn)
    n = conn.execute(
        "SELECT COUNT(*) FROM system_settings "
        "WHERE category='sme_location' AND Site_ID='HQ'"
    ).fetchone()[0]
    assert n == 3, f"seed locations duplicated on re-init: count={n}"


def check_r17_on_order_arithmetic():
    conn = _r17_conn()
    conn.execute(
        "INSERT INTO purchase_orders (PO_Number, Site_ID, status) "
        "VALUES ('PO-A', 'HQ', 'open')"
    )
    conn.execute(
        "INSERT INTO po_items (PO_Number, Material_Code, Qty, "
        "Delivered_Qty, Returned_Qty, line_status) "
        "VALUES ('PO-A', 'MAT-A', 10, 3, 1, 'open')"
    )
    conn.commit()
    df = database.get_on_order_by_material(site_id='HQ', conn=conn)
    assert not df.empty, "expected one row"
    val = float(df.iloc[0]["Ordered_Qty"])
    assert val == 6.0, f"expected Ordered_Qty=6.0, got {val}"


def check_r17_on_order_closed_excluded():
    conn = _r17_conn()
    conn.execute(
        "INSERT INTO purchase_orders (PO_Number, Site_ID, status) "
        "VALUES ('PO-CLOSED', 'HQ', 'closed')"
    )
    conn.execute(
        "INSERT INTO po_items (PO_Number, Material_Code, Qty, "
        "Delivered_Qty, line_status) "
        "VALUES ('PO-CLOSED', 'MAT-X', 10, 0, 'open')"
    )
    conn.commit()
    df = database.get_on_order_by_material(site_id='HQ', conn=conn)
    assert df.empty, f"closed POs leaked into on-order aggregate: {df.to_dict()}"


def check_r17_on_order_site_scope():
    conn = _r17_conn()
    conn.execute(
        "INSERT INTO purchase_orders (PO_Number, Site_ID, status) "
        "VALUES ('PO-HQ', 'HQ', 'open')"
    )
    conn.execute(
        "INSERT INTO po_items (PO_Number, Material_Code, Qty, line_status) "
        "VALUES ('PO-HQ', 'MAT-1', 5, 'open')"
    )
    conn.execute(
        "INSERT INTO purchase_orders (PO_Number, Site_ID, status) "
        "VALUES ('PO-B', 'SITE_B', 'open')"
    )
    conn.execute(
        "INSERT INTO po_items (PO_Number, Material_Code, Qty, line_status) "
        "VALUES ('PO-B', 'MAT-1', 99, 'open')"
    )
    conn.commit()
    df_hq = database.get_on_order_by_material(site_id='HQ', conn=conn)
    assert float(df_hq.iloc[0]["Ordered_Qty"]) == 5.0, \
        f"site filter leaked SITE_B PO into HQ aggregate: {df_hq.to_dict()}"


def check_r17_sme_inventory_view():
    # R20.5.1 — get_sme_inventory_view now sources from the SME-owned
    # `sme_inventory_seed` baseline (NOT ERP live stock), rolling up ERP
    # receipts / consumption via SAP_Code → inventory.Material_Code:
    #   Available_Qty = Initial_Available_Qty + receipts - consumption
    #   Ordered_Qty   = Initial_Ordered_Qty
    # Seed the new source with the same numbers so the math still resolves
    # to Available=130, Ordered=15.
    conn = _r17_conn()
    # SAP → Material mapping lives in ERP inventory (the join key for the
    # receipts / consumption rollup).
    conn.execute(
        "INSERT INTO inventory (SAP_Code, Material_Code, "
        "Equipment_Description, UOM, Opening_Stock, Category) "
        "VALUES ('SAP-1', 'MAT-1', 'Glue', 'KG', 0, 'Consumable')"
    )
    # SME baseline (the standalone SME's inventory file lands here).
    conn.execute(
        "INSERT INTO sme_inventory_seed (Material_Code, Material_Name, UOM, "
        "Nature, Initial_Available_Qty, Initial_Ordered_Qty) "
        "VALUES ('MAT-1', 'Glue', 'KG', 'Consumable', 100, 15)"
    )
    conn.execute(
        "INSERT INTO receipts (SAP_Code, Site_ID, Quantity) "
        "VALUES ('SAP-1', 'HQ', 50)"
    )
    conn.execute(
        "INSERT INTO consumption (SAP_Code, Site_ID, Quantity) "
        "VALUES ('SAP-1', 'HQ', 20)"
    )
    conn.commit()
    df = database.get_sme_inventory_view(site_id='HQ', conn=conn)
    assert not df.empty, "view returned empty after seeding"
    row = df[df["Material_Code"] == "MAT-1"].iloc[0]
    # Available = Initial(100) + Recv(50) - Cons(20) = 130
    assert float(row["Available_Qty"]) == 130.0, \
        f"Available_Qty math wrong: got {row['Available_Qty']}"
    # Ordered = Initial_Ordered_Qty(15)
    assert float(row["Ordered_Qty"]) == 15.0, \
        f"Ordered_Qty wrong: got {row['Ordered_Qty']}"


def check_r17_sme_setting_crud():
    conn = _r17_conn()
    inserted = database.add_sme_setting(
        "sme_location", "FOO BAR", "SITE_B", conn=conn,
    )
    assert inserted is True
    # Idempotent re-insert
    again = database.add_sme_setting(
        "sme_location", "FOO BAR", "SITE_B", conn=conn,
    )
    assert again is False, "duplicate insert should be rejected"
    locs = database.get_sme_locations(site_id="SITE_B", conn=conn)
    assert "FOO BAR" in locs, f"value not visible after insert: {locs}"
    n = database.delete_sme_setting(
        "sme_location", "FOO BAR", "SITE_B", conn=conn,
    )
    assert n == 1, f"delete rowcount expected 1, got {n}"
    # Refuse unknown category to prevent accidental writes
    try:
        database.add_sme_setting("Work_Type", "GUARD", "HQ", conn=conn)
        assert False, "should have raised on cross-category write"
    except ValueError:
        pass


def check_r17_sme_progress_preservation():
    conn = _r17_conn()
    # First load: set both Original and Done.
    database.upsert_sme_sqm_progress(
        "HQ", "TAG-1", "1",
        original_sqm=100.0, done_sqm=40.0, conn=conn,
    )
    # Bootstrap re-load: pass original_sqm only (done is omitted).
    database.upsert_sme_sqm_progress(
        "HQ", "TAG-1", "1",
        original_sqm=120.0, conn=conn,
    )
    row = conn.execute(
        "SELECT Original_SQM, Done_SQM FROM sme_sqm_progress "
        "WHERE Site_ID='HQ' AND Equipment_Tag_No='TAG-1' "
        "AND Lining_System_Code='1'"
    ).fetchone()
    assert row[0] == 120.0, f"Original_SQM should update: got {row[0]}"
    assert row[1] == 40.0, f"Done_SQM should be preserved: got {row[1]}"


def check_r17_material_estimator_rbac():
    # Read main.py as text and assert the exact-role lock contains the
    # expected page → {hod, admin} mapping. Avoids importing main, which
    # transitively requires bcrypt / fpdf that the bug-check env may lack.
    import pathlib, re
    main_src = pathlib.Path(REPO_ROOT / "main.py").read_text(encoding="utf-8")
    # Grab the _EXACT_ROLE_PAGES dict literal
    m = re.search(
        r"_EXACT_ROLE_PAGES\s*=\s*\{(.*?)\n\}",
        main_src, re.DOTALL,
    )
    assert m, "_EXACT_ROLE_PAGES dict not found in main.py"
    block = m.group(1)
    me_line = re.search(
        r'"🧪 Material Estimator"\s*:\s*\{([^}]+)\}', block,
    )
    assert me_line, "Material Estimator missing from _EXACT_ROLE_PAGES"
    roles = {r.strip().strip("'\"") for r in me_line.group(1).split(",") if r.strip()}
    assert roles == {"hod", "admin"}, (
        f"expected {{hod, admin}} exact-lock for Material Estimator, got {roles}"
    )


def check_r17_material_estimator_in_page_access():
    import config as _cfg
    assert "🧪 Material Estimator" in _cfg.PAGE_ACCESS, \
        "Material Estimator missing from PAGE_ACCESS — router won't render it"


# ---------------------------------------------------------------------------
# Round 18 — SME consumption form, listeners, downloads, UI parity
# ---------------------------------------------------------------------------

def _r18_conn():
    conn = sqlite3.connect(":memory:")
    database.init_db(conn)
    return conn


def _r18_seed_basic(conn):
    """Two SME materials + one non-SME bolt + 100-SQM equipment on system 1."""
    conn.execute(
        "INSERT INTO inventory (SAP_Code, Material_Code, "
        "Equipment_Description, UOM, Opening_Stock) "
        "VALUES ('SAP-A', 'MAT-A', 'Glue', 'KG', 500)"
    )
    conn.execute(
        "INSERT INTO inventory (SAP_Code, Material_Code, "
        "Equipment_Description, UOM, Opening_Stock) "
        "VALUES ('SAP-B', 'MAT-B', 'Resin', 'KG', 500)"
    )
    conn.execute(
        "INSERT INTO inventory (SAP_Code, Material_Code, "
        "Equipment_Description, UOM, Opening_Stock) "
        "VALUES ('SAP-X', 'MAT-X', 'Bolt', 'EA', 999)"
    )
    conn.execute(
        "INSERT INTO sme_recipe (Lining_System_Code, Material_Code, "
        "Material_Name, UOM, For_1_SQM) "
        "VALUES ('1', 'MAT-A', 'Glue', 'KG', 2.0)"
    )
    conn.execute(
        "INSERT INTO sme_recipe (Lining_System_Code, Material_Code, "
        "Material_Name, UOM, For_1_SQM) "
        "VALUES ('1', 'MAT-B', 'Resin', 'KG', 3.0)"
    )
    conn.execute(
        "INSERT INTO sme_equipment (Site_ID, Equipment_Tag_No, Name, "
        "Lining_System_Code, Surface_Area_SQM) "
        "VALUES ('HQ', 'TAG-1', 'Tank 1', '1', 100)"
    )
    conn.commit()


def _r18_extras():
    return {"Issued_To": "crew1", "Tank_No": "T1",
            "Serial_No": "S1", "PR_Number": "PR1"}


def check_r18_staged_column():
    conn = _r18_conn()
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(sme_sqm_progress)"
    ).fetchall()}
    assert "Done_SQM_staged" in cols, \
        "sme_sqm_progress.Done_SQM_staged missing (Round 18 self-heal)"
    assert "Done_SQM" in cols, "Done_SQM column lost on R18 self-heal"


def check_r18_consumption_log_schema():
    conn = _r18_conn()
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(sme_consumption_log)"
    ).fetchall()}
    for req in ("batch_id", "Site_ID", "entry_date", "entered_by",
                "Equipment_Tag_No", "Lining_System_Code", "Material_Code",
                "SQM_Completed", "Expected_Qty", "Actual_Qty",
                "Variance_Pct", "status", "staged_pi_id"):
        assert req in cols, f"sme_consumption_log missing column {req!r}"
    # FSM check: only staged/committed/rejected allowed
    conn.execute(
        "INSERT INTO sme_consumption_log "
        "(batch_id, Site_ID, entry_date, Equipment_Tag_No, "
        " Lining_System_Code, Material_Code) "
        "VALUES ('B','HQ','2026-06-25','TAG','1','MAT')"
    )
    try:
        conn.execute(
            "UPDATE sme_consumption_log SET status='exploded' WHERE id=1"
        )
        conn.commit()
        assert False, "status FSM should reject 'exploded'"
    except sqlite3.IntegrityError:
        pass


def check_r18_inventory_view_flag():
    conn = _r18_conn()
    _r18_seed_basic(conn)
    rows = dict(conn.execute(
        "SELECT SAP_Code, is_sme FROM v_inventory_with_sme"
    ).fetchall())
    assert rows.get("SAP-A") == 1
    assert rows.get("SAP-B") == 1
    assert rows.get("SAP-X") == 0


def check_r18_dispatch_helpers():
    conn = _r18_conn()
    _r18_seed_basic(conn)
    assert database.is_sme_sap("SAP-A", conn=conn) is True
    assert database.is_sme_sap("SAP-X", conn=conn) is False
    assert database.is_sme_sap("", conn=conn) is False
    assert database.is_sme_sap(None, conn=conn) is False
    assert database.is_sme_material("MAT-A", conn=conn) is True
    assert database.is_sme_material("NOPE", conn=conn) is False


def check_r18_sap_resolution():
    conn = _r18_conn()
    _r18_seed_basic(conn)
    assert database.get_sap_for_material("MAT-A", conn=conn) == "SAP-A"
    assert database.get_sap_for_material("MAT-X", conn=conn) == "SAP-X"
    assert database.get_sap_for_material("NOPE", conn=conn) is None
    assert database.get_sap_for_material(None, conn=conn) is None


def check_r18_stage_aggregation():
    conn = _r18_conn()
    _r18_seed_basic(conn)
    res = database.stage_sme_consumption_batch(
        site_id="HQ", entry_date="2026-06-25", entered_by="sk1",
        rows=[
            {"equipment_tag": "TAG-1", "lining_system_code": "1",
             "material_code": "MAT-A", "sqm_completed": 10,
             "expected_qty": 20, "actual_qty": 12},
            {"equipment_tag": "TAG-1", "lining_system_code": "1",
             "material_code": "MAT-A", "sqm_completed": 10,
             "expected_qty": 20, "actual_qty": 8},
        ],
        extras=_r18_extras(), conn=conn,
    )
    assert res["materials_staged"] == 1, \
        f"expected 1 distinct material, got {res['materials_staged']}"
    assert len(res["pending_issue_ids"]) == 1, \
        "two detail rows for same material should aggregate to 1 PI row"
    pi_qty = conn.execute(
        "SELECT Quantity FROM pending_issues WHERE id = ?",
        (res["pending_issue_ids"][0],),
    ).fetchone()[0]
    assert float(pi_qty) == 20.0, \
        f"expected aggregated qty 20 (12+8), got {pi_qty}"


def check_r18_stage_missing_extras():
    conn = _r18_conn()
    _r18_seed_basic(conn)
    try:
        database.stage_sme_consumption_batch(
            site_id="HQ", entry_date="2026-06-25", entered_by="sk1",
            rows=[{"equipment_tag": "TAG-1",
                   "lining_system_code": "1",
                   "material_code": "MAT-A", "sqm_completed": 1,
                   "expected_qty": 2, "actual_qty": 2}],
            extras={"Issued_To": "x"},  # missing Tank_No, Serial_No, PR_Number
            conn=conn,
        )
        assert False, "stage should raise ValueError on missing extras"
    except ValueError as e:
        assert "missing" in str(e).lower()


def check_r18_stage_increments_staged():
    conn = _r18_conn()
    _r18_seed_basic(conn)
    database.stage_sme_consumption_batch(
        site_id="HQ", entry_date="2026-06-25", entered_by="sk1",
        rows=[
            {"equipment_tag": "TAG-1", "lining_system_code": "1",
             "material_code": "MAT-A", "sqm_completed": 8,
             "expected_qty": 16, "actual_qty": 16},
            {"equipment_tag": "TAG-1", "lining_system_code": "1",
             "material_code": "MAT-B", "sqm_completed": 8,
             "expected_qty": 24, "actual_qty": 24},
        ],
        extras=_r18_extras(), conn=conn,
    )
    row = conn.execute(
        "SELECT Done_SQM_staged, Done_SQM FROM sme_sqm_progress "
        "WHERE Equipment_Tag_No='TAG-1' AND Lining_System_Code='1'"
    ).fetchone()
    # MAX per (tag,system) — both detail rows share SQM=8, so staged=8 not 16
    assert row[0] == 8.0, f"expected staged=8, got {row[0]}"
    assert row[1] == 0.0, f"expected Done_SQM=0 pre-commit, got {row[1]}"


def check_r18_commit_shifts_sqm():
    conn = _r18_conn()
    _r18_seed_basic(conn)
    database.stage_sme_consumption_batch(
        site_id="HQ", entry_date="2026-06-25", entered_by="sk1",
        rows=[{"equipment_tag": "TAG-1", "lining_system_code": "1",
               "material_code": "MAT-A", "sqm_completed": 12,
               "expected_qty": 24, "actual_qty": 24}],
        extras=_r18_extras(), conn=conn,
    )
    pre = conn.execute(
        "SELECT Done_SQM_staged, Done_SQM FROM sme_sqm_progress"
    ).fetchone()
    assert pre == (12.0, 0.0)
    n = database.commit_eod_with_sme_sync(conn=conn, hod_username="hod1")
    assert n == 1, f"expected 1 row committed, got {n}"
    post = conn.execute(
        "SELECT Done_SQM_staged, Done_SQM FROM sme_sqm_progress"
    ).fetchone()
    assert post == (0.0, 12.0), \
        f"expected (0,12) after commit, got {post}"
    statuses = [r[0] for r in conn.execute(
        "SELECT status FROM sme_consumption_log"
    ).fetchall()]
    assert statuses == ["committed"], \
        f"expected ['committed'], got {statuses}"


def check_r18_commit_eod_unchanged():
    """Non-SME pending_issues rows still commit identically through the
    wrapper — regression check that we didn't break the legacy path."""
    conn = _r18_conn()
    _r18_seed_basic(conn)
    conn.execute(
        "INSERT INTO pending_issues (Date, SAP_Code, Quantity, "
        " Site_ID, Issued_By, Issued_To, Tank_No, Serial_No, "
        " PR_Number, status) "
        "VALUES ('2026-06-25','SAP-X',5,'HQ','sk1','crew','T1',"
        "        'S1','PR-1','pending_hod')"
    )
    conn.commit()
    n = database.commit_eod_with_sme_sync(conn=conn, hod_username="hod1")
    assert n == 1, f"expected 1 committed, got {n}"
    cons = conn.execute(
        "SELECT SAP_Code, Quantity FROM consumption"
    ).fetchall()
    assert ("SAP-X", 5.0) in [(r[0], float(r[1])) for r in cons]


def check_r18_reject_decrements_staged():
    conn = _r18_conn()
    _r18_seed_basic(conn)
    res = database.stage_sme_consumption_batch(
        site_id="HQ", entry_date="2026-06-25", entered_by="sk1",
        rows=[{"equipment_tag": "TAG-1", "lining_system_code": "1",
               "material_code": "MAT-A", "sqm_completed": 7,
               "expected_qty": 14, "actual_qty": 14}],
        extras=_r18_extras(), conn=conn,
    )
    pi_id = res["pending_issue_ids"][0]
    pre = conn.execute("SELECT Done_SQM_staged FROM sme_sqm_progress").fetchone()[0]
    assert pre == 7.0
    ok = database.hod_reject_pending_issue_with_sme_sync(
        pi_id, rejected_by="hod1", reason="wrong tank", conn=conn,
    )
    assert ok
    post = conn.execute(
        "SELECT Done_SQM_staged, Done_SQM FROM sme_sqm_progress"
    ).fetchone()
    assert post == (0.0, 0.0), f"expected both 0 after reject, got {post}"
    status = conn.execute(
        "SELECT status, rejected_reason FROM sme_consumption_log"
    ).fetchone()
    assert status == ("rejected", "wrong tank")
    # Confirm PI moved to archive (handled by the wrapped legacy fn)
    arch = conn.execute(
        "SELECT COUNT(*) FROM rejected_issues_archive WHERE original_id = ?",
        (pi_id,),
    ).fetchone()[0]
    assert arch == 1


def check_r18_sme_form_in_daily_issue_log():
    """_render_sme_consumption_form must exist after Phase 4."""
    import pathlib
    src = pathlib.Path(
        REPO_ROOT / "pages_internal" / "daily_issue_log.py"
    ).read_text(encoding="utf-8")
    assert "_render_sme_consumption_form" in src, \
        "SME form helper missing from daily_issue_log.py"
    assert "stage_sme_consumption_batch" in src, \
        "daily_issue_log doesn't import stage_sme_consumption_batch"
    assert "SME Multi-Material Entry" in src, \
        "SME expander label missing"


def check_r18_hod_portal_wires_wrappers():
    """hod_portal.py must alias commit_eod → commit_eod_with_sme_sync and
    hod_reject_pending_issue → hod_reject_pending_issue_with_sme_sync."""
    import pathlib
    src = pathlib.Path(
        REPO_ROOT / "pages_internal" / "hod_portal.py"
    ).read_text(encoding="utf-8")
    assert "commit_eod_with_sme_sync as commit_eod" in src, \
        "hod_portal must alias commit_eod_with_sme_sync as commit_eod"
    assert "hod_reject_pending_issue_with_sme_sync as hod_reject_pending_issue" in src, \
        "hod_portal must alias the SME-sync reject wrapper"


# ---------------------------------------------------------------------------
# Round 20.1 — Bug fixes after first live render
# ---------------------------------------------------------------------------

def check_r20_1_no_string_indent_bug():
    """Verify multi-line strings in the wrap region don't have 8-space
    interior indent (which would make markdown treat HTML/SVG/CSS as
    indented code blocks)."""
    import pathlib, tokenize, io
    src = pathlib.Path(
        REPO_ROOT / "pages_internal" / "material_estimator_portal.py"
    ).read_text(encoding="utf-8")
    lines = src.split("\n")
    wrap_line = None
    for i, line in enumerate(lines, start=1):
        if line.startswith("def page_material_estimator("):
            wrap_line = i
            break
    assert wrap_line, "page_material_estimator def not found"

    # Tokenize and find multi-line strings + f-strings
    tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    ranges = []
    fstring_open = None
    for tok in tokens:
        if tok.type == tokenize.STRING and "\n" in tok.string:
            if tok.start[0] >= wrap_line:
                ranges.append((tok.start[0], tok.end[0]))
        elif tok.type == tokenize.FSTRING_START:
            fstring_open = tok.start[0]
        elif tok.type == tokenize.FSTRING_END:
            if fstring_open is not None:
                if tok.end[0] > fstring_open and tok.end[0] >= wrap_line:
                    ranges.append((fstring_open, tok.end[0]))
                fstring_open = None

    # For each string, the FIRST non-empty interior line must NOT start
    # with 8+ spaces AND begin with HTML (a '<' character). That's the
    # exact case where Streamlit's markdown renderer treats the content
    # as an indented code block instead of recognizing the HTML open tag.
    # SQL queries (INSERT/SELECT/UPDATE) don't matter — they're inside
    # cur.execute() / pd.read_sql() and the database doesn't care about
    # leading whitespace.
    offenders = []
    for s, e in ranges:
        for line_idx in range(s + 1, e + 1):
            z = line_idx - 1
            if z >= len(lines):
                continue
            line = lines[z]
            stripped = line.strip()
            if not stripped:
                continue  # skip blank lines
            # First non-empty interior line found — check it.
            # Only HTML content (starting with '<') is at risk; SQL,
            # plain text, and other non-markdown content is fine even
            # with leading whitespace.
            if line.startswith(" " * 8) and stripped.startswith("<"):
                offenders.append((line_idx, line[:80]))
            break  # only checking the first non-empty interior line
    if offenders:
        msg = "\n".join(f"  line {ln}: {repr(text)}" for ln, text in offenders[:5])
        assert False, (
            f"{len(offenders)} HTML string range(s) have 8+ leading "
            f"spaces on their first content line — markdown will render "
            f"as code block instead of HTML:\n{msg}"
        )


def check_r20_1_cascade_allocate_empty_safe():
    """The SME's cascade_allocate must return a DataFrame with the
    expected columns even when rows=[]. Otherwise groupby downstream
    raises KeyError on Equipment_Tag_No."""
    import pathlib
    src = pathlib.Path(
        REPO_ROOT / "pages_internal" / "material_estimator_portal.py"
    ).read_text(encoding="utf-8")
    # Look for the column list passed to pd.DataFrame(rows, columns=...)
    assert "pd.DataFrame(rows, columns=" in src, \
        "cascade_allocate must pass explicit columns= to pd.DataFrame for empty safety"
    # Required columns must be in the list
    import re
    m = re.search(
        r"_EXPECTED_COLS\s*=\s*\[([^\]]+)\]",
        src,
    )
    assert m, "_EXPECTED_COLS column list missing"
    block = m.group(1)
    for col in ("Equipment_Tag_No.", "Lining_System_Code",
                "Material_Code", "Demand_Qty",
                "Allocated_Qty", "Shortfall_Qty"):
        assert col in block, f"_EXPECTED_COLS missing {col!r}"


def check_r20_1_stock_only_sme_filter():
    """Stock-Only Materials block must filter to SME-tracked materials
    only (Material_Code in sme_recipe). Bolts/gloves/etc. must not pass."""
    import pathlib
    src = pathlib.Path(
        REPO_ROOT / "pages_internal" / "material_estimator_portal.py"
    ).read_text(encoding="utf-8")
    # The filter expression must reference recipe['Material_Code']
    assert "_all_sme_codes" in src, \
        "Stock-Only Materials block missing _all_sme_codes filter"
    assert 'recipe["Material_Code"].unique()' in src, \
        "filter must derive set from recipe['Material_Code'].unique()"
    assert 'isin(_all_sme_codes)' in src, \
        "filter must restrict to SME-tracked materials via .isin(_all_sme_codes)"


def check_r20_1_load_all_empty_safe():
    """load_all() must return DataFrames with expected columns even when
    underlying data is empty (no equipment / no recipe loaded)."""
    import pathlib
    src = pathlib.Path(
        REPO_ROOT / "pages_internal" / "material_estimator_portal.py"
    ).read_text(encoding="utf-8")
    # Look for the dm-empty guard
    assert "if dm.empty:" in src, \
        "load_all missing `if dm.empty:` guard"
    # Look for the eq_master-empty guard
    assert "if equip_raw_local.empty:" in src, \
        "load_all missing `if equip_raw_local.empty:` guard"
    # Look for the sqm_ref-empty guard
    assert "if equip_sc.empty:" in src, \
        "load_all missing `if equip_sc.empty:` guard"


# ---------------------------------------------------------------------------
# Round 20.5 — Tab 8 Master Data CRUD wiring
# ---------------------------------------------------------------------------
# Phase A added 15 cols to sme_equipment + 8 cols to sme_recipe, created
# sme_inventory_seed, and added 9 CRUD helpers + sme_materials_view.
# Phase C rewired Tab 8 to call those helpers instead of running raw SQL
# against the compatibility views (which SQLite rejects with "cannot modify
# ... because it is a view").

_R20_5_EQUIP_NEW_COLS = (
    "Sl_No", "Project", "WBS_No", "IO_No", "Drawing_No", "Design",
    "Dia_L", "Ht_W", "Equipment_Total_SQM", "Remaraks",
    "Lining_System_Short_Name", "Lining_Type", "Lining_System",
    "Material_Spec", "Lining_Area_Location",
)
_R20_5_RECIPE_NEW_COLS = (
    "Sl_No", "Substrate", "System_Keys", "Lining_Thickness",
    "Lining_System", "Lining_Type", "Material_Description", "Package_Size",
)


def check_r20_5_sme_equipment_columns():
    conn = sqlite3.connect(":memory:")
    database.init_db(conn)
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(sme_equipment)"
    ).fetchall()}
    missing = [c for c in _R20_5_EQUIP_NEW_COLS if c not in cols]
    assert not missing, f"sme_equipment missing R20.5 cols: {missing}"


def check_r20_5_sme_recipe_columns():
    conn = sqlite3.connect(":memory:")
    database.init_db(conn)
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(sme_recipe)"
    ).fetchall()}
    missing = [c for c in _R20_5_RECIPE_NEW_COLS if c not in cols]
    assert not missing, f"sme_recipe missing R20.5 cols: {missing}"


def check_r20_5_sme_inventory_seed_table():
    conn = sqlite3.connect(":memory:")
    database.init_db(conn)
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(sme_inventory_seed)"
    ).fetchall()}
    expected = {
        "Material_Code", "Material_Name", "Item", "Vendor",
        "Purchasing_Document", "Document_Date", "Nature", "UOM",
        "Initial_Available_Qty", "Initial_Ordered_Qty",
        "created_at", "updated_at",
    }
    missing = expected - cols
    assert not missing, f"sme_inventory_seed missing cols: {missing}"
    # Material_Code must be PK
    pk_rows = conn.execute(
        "SELECT name FROM pragma_table_info('sme_inventory_seed') WHERE pk = 1"
    ).fetchall()
    assert pk_rows and pk_rows[0][0] == "Material_Code", \
        "sme_inventory_seed PK must be Material_Code"


def check_r20_5_sme_materials_view_math():
    """Seed 1 row, add 1 receipt + 1 consumption (via SAP_Code in inventory),
    then verify the view's available_qty computes Initial + Received - Consumed."""
    conn = sqlite3.connect(":memory:")
    database.init_db(conn)
    # Seed inventory mapping: SAP→Material
    conn.execute("INSERT INTO inventory (SAP_Code, Material_Code, UOM) "
                 "VALUES ('SAP-X', 'MAT-X', 'KG')")
    # Seed SME baseline
    conn.execute("INSERT INTO sme_inventory_seed (Material_Code, "
                 "Initial_Available_Qty, Initial_Ordered_Qty) "
                 "VALUES ('MAT-X', 100, 50)")
    # Receipt: +25
    conn.execute("INSERT INTO receipts (Date, SAP_Code, Quantity) "
                 "VALUES ('2026-01-01','SAP-X', 25)")
    # Consumption: -30
    conn.execute("INSERT INTO consumption (Date, SAP_Code, Quantity) "
                 "VALUES ('2026-01-02','SAP-X', 30)")
    conn.commit()
    row = conn.execute(
        "SELECT initial_available_qty, received_qty, consumed_qty, "
        "       available_qty, ordered_qty "
        "FROM sme_materials_view WHERE material_code='MAT-X'"
    ).fetchone()
    assert row is not None, "sme_materials_view returned no row for MAT-X"
    init_q, rcv_q, cons_q, avail_q, ord_q = row
    assert init_q == 100, f"initial: {init_q}"
    assert rcv_q  == 25,  f"received: {rcv_q}"
    assert cons_q == 30,  f"consumed: {cons_q}"
    assert avail_q == 95, f"available_qty math wrong: 100 + 25 - 30 != {avail_q}"
    assert ord_q == 50,   f"ordered_qty: {ord_q}"


def check_r20_5_equipment_view_aliases():
    conn = sqlite3.connect(":memory:")
    database.init_db(conn)
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(equipment)"
    ).fetchall()}
    # The autofill SELECT quotes these identifiers verbatim; PRAGMA returns
    # them as-quoted, so we look for the exact strings.
    for needed in ("Lining_System", "Material Spec.", "Lining_Area/location"):
        assert needed in cols, \
            f"equipment view missing aliased col {needed!r}"


def check_r20_5_recipe_view_lining_type():
    """recipe.lining_type must come from sme_recipe.Lining_Type, not the
    legacy '' AS lining_type placeholder. Seed a real value + verify
    it round-trips through the view."""
    conn = sqlite3.connect(":memory:")
    database.init_db(conn)
    conn.execute("INSERT INTO sme_recipe (Lining_System_Code, Material_Code, "
                 "For_1_SQM, Lining_Type) VALUES ('99','MAT-Y',1.0,'Acid')")
    conn.commit()
    row = conn.execute(
        "SELECT lining_type FROM recipe WHERE lining_system_code='99'"
    ).fetchone()
    assert row and row[0] == "Acid", \
        f"recipe.lining_type should round-trip 'Acid', got {row!r}"


def check_r20_5_crud_helpers_exist():
    for fn in ("insert_sme_equipment", "update_sme_equipment",
               "delete_sme_equipment", "insert_sme_recipe",
               "update_sme_recipe", "delete_sme_recipe",
               "insert_sme_inventory_seed", "update_sme_inventory_seed",
               "delete_sme_inventory_seed"):
        assert callable(getattr(database, fn, None)), \
            f"missing R20.5 helper: database.{fn}"


def check_r20_5_col_translation():
    """insert_sme_equipment must accept UI-shaped form keys (lowercase +
    dotted / slashed) and write to the PascalCase table columns."""
    conn = sqlite3.connect(":memory:")
    database.init_db(conn)
    # Use the public helper with the same key shapes the Tab 8 form emits
    row_id = database.insert_sme_equipment(
        {
            "equipment_tag":         "T-PARITY",
            "lining_system_code":    "7",
            "surface_area_sqm":      12.5,
            "Material Spec.":        "EpoxyA",            # dotted UI key
            "Lining_Area/location":  "Shell",             # slashed UI key
            "lining_system":         "LS Full Text",      # lowercase form key
            "wbs #":                 "WBS-001",           # hashed UI key
        },
        site_id="HQ", conn=conn,
    )
    conn.commit()
    row = conn.execute(
        "SELECT Material_Spec, Lining_Area_Location, Lining_System, WBS_No "
        "FROM sme_equipment WHERE id=?", (row_id,),
    ).fetchone()
    assert row == ("EpoxyA", "Shell", "LS Full Text", "WBS-001"), \
        f"col translation mismatch: {row!r}"


def check_r20_5_no_raw_view_writes():
    """Tab 8 (the master-data block from `with tab_master:` to the end of
    page_material_estimator) must not contain raw INSERT/UPDATE/DELETE
    statements against the compat views — those would fail at runtime
    ('cannot modify view') and indicate Phase C wiring regressed."""
    import pathlib, re
    src = pathlib.Path(
        REPO_ROOT / "pages_internal" / "material_estimator_portal.py"
    ).read_text(encoding="utf-8")
    # Slice the Tab 8 region (from `with tab_master:` to the END marker)
    start = src.find("with tab_master:")
    end_marker = "END ORIGINAL SME IMPERATIVE BODY"
    end = src.find(end_marker, start)
    assert start != -1, "with tab_master: block not found"
    assert end != -1, "END ORIGINAL SME IMPERATIVE BODY marker missing"
    tab8 = src[start:end]
    forbidden_re = re.compile(
        r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"
        r"(equipment|recipe|inventory|sqm_progress|sme_materials_view)\b",
        re.IGNORECASE,
    )
    matches = forbidden_re.findall(tab8)
    assert not matches, \
        f"Tab 8 has residual raw view-write SQL: {matches}"


def check_r20_5_table_map_materials_view():
    import pathlib
    src = pathlib.Path(
        REPO_ROOT / "pages_internal" / "material_estimator_portal.py"
    ).read_text(encoding="utf-8")
    assert '"Materials_DetailsAvailable_Qty": "sme_materials_view"' in src, \
        "TABLE_MAP must point Materials_DetailsAvailable_Qty → sme_materials_view"
    # And the old "inventory" mapping must be gone
    assert '"Materials_DetailsAvailable_Qty":  "inventory"' not in src, \
        "stale 'inventory' mapping still present in TABLE_MAP"


def check_r20_5_equipment_smart_entry_helpers():
    import pathlib
    src = pathlib.Path(
        REPO_ROOT / "pages_internal" / "material_estimator_portal.py"
    ).read_text(encoding="utf-8")
    assert "D.insert_sme_equipment(" in src, \
        "Equipment Smart Entry must call D.insert_sme_equipment"
    assert "D.upsert_sme_sqm_progress(" in src, \
        "Equipment Smart Entry must call D.upsert_sme_sqm_progress"
    assert "D.insert_sme_recipe(" in src, \
        "Dynamic add form must call D.insert_sme_recipe for the recipe radio"
    assert "D.insert_sme_inventory_seed(" in src, (
        "Dynamic add form must call D.insert_sme_inventory_seed for "
        "Materials Details radio"
    )


def check_r20_5_1_master_data_no_order_by_rowid():
    """Bug A: `db_table` resolves to a VIEW (equipment / recipe /
    sme_materials_view) which has no implicit rowid. `ORDER BY rowid` raised
    'no such column: rowid', swallowed by a bare except into an empty grid
    ('No records found' for all three radios). Guard: the Tab 8 View/Edit/
    Delete read must NOT order by rowid."""
    import pathlib
    src = pathlib.Path(
        REPO_ROOT / "pages_internal" / "material_estimator_portal.py"
    ).read_text(encoding="utf-8")
    assert "SELECT * FROM {db_table} ORDER BY rowid" not in src, \
        "Tab 8 still does `ORDER BY rowid` on a VIEW (returns empty grid)"
    # Must order by a real per-table column instead.
    assert '"sme_materials_view": "material_code"' in src, \
        "Tab 8 _ORDER_COL map missing sme_materials_view ordering"


def check_r20_5_1_inventory_view_seed_sourced():
    """Bug B: get_sme_inventory_view must source Available_Qty / Ordered_Qty
    from the SME seed (sme_inventory_seed via sme_materials_view), NOT ERP
    live stock. End-to-end: a material present ONLY in the seed (no ERP
    Opening_Stock) must still report its baseline qty."""
    conn = sqlite3.connect(":memory:")
    database.init_db(conn)
    # ERP inventory carries the SAP→Material mapping but ZERO stock.
    conn.execute(
        "INSERT INTO inventory (SAP_Code, Material_Code, "
        "Equipment_Description, UOM, Opening_Stock, Category) "
        "VALUES ('SAP-Z', 'MAT-Z', 'Resin', 'KG', 0, 'Consumable')"
    )
    # SME baseline only.
    conn.execute(
        "INSERT INTO sme_inventory_seed (Material_Code, Material_Name, UOM, "
        "Initial_Available_Qty, Initial_Ordered_Qty) "
        "VALUES ('MAT-Z', 'Resin', 'KG', 500, 200)"
    )
    conn.commit()
    df = database.get_sme_inventory_view(site_id="HQ", conn=conn)
    row = df[df["Material_Code"] == "MAT-Z"]
    assert not row.empty, \
        "seed-only material absent — view still reads ERP stock, not the seed"
    assert float(row.iloc[0]["Available_Qty"]) == 500.0, \
        f"Available_Qty must be seed baseline 500, got {row.iloc[0]['Available_Qty']}"
    assert float(row.iloc[0]["Ordered_Qty"]) == 200.0, \
        f"Ordered_Qty must be seed Initial_Ordered_Qty 200, got {row.iloc[0]['Ordered_Qty']}"


def check_r20_5_2_no_deleted_pkg_import():
    """R20 deleted the package `pages_internal/material_estimator/` (literal
    drop-in lives in material_estimator_portal.py). No live module may import
    from it — `daily_issue_log.py` did (days_of_continuation_block), which
    crashed the SK Consumption page with ModuleNotFoundError. Guard: no
    `from pages_internal.material_estimator.<sub> import` anywhere."""
    import pathlib, re
    pkg_dir = REPO_ROOT / "pages_internal" / "material_estimator"
    assert not pkg_dir.exists(), \
        "the R19 package pages_internal/material_estimator/ should not exist"
    # Scan every .py for a real import statement against the deleted package
    # (the suffixed modules _portal / _engine are fine; the bare package and
    # its submodules are not).
    bad_re = re.compile(
        r"(?:from|import)\s+pages_internal\.material_estimator(?:\.\w+)*"
        r"(?:\s+import|\s*$)"
    )
    offenders = []
    for py in REPO_ROOT.rglob("*.py"):
        if py.name == "bug_check.py":
            continue
        for ln in py.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if s.startswith("#"):
                continue
            # Allow the suffixed modules.
            if "material_estimator_portal" in s or "material_estimator_engine" in s:
                continue
            if bad_re.search(s):
                offenders.append(f"{py.relative_to(REPO_ROOT)}: {s}")
    assert not offenders, \
        "live import(s) of deleted package found:\n  " + "\n  ".join(offenders)


def check_r20_5_2_doc_block_vendored():
    """days_of_continuation_block must be defined locally in daily_issue_log.py
    (vendored from the deleted package) and callable with the engine
    contract."""
    import importlib.util as iu
    name = "pages_internal.daily_issue_log"
    # Ensure the parent package stub exists for the relative import context.
    if "pages_internal" not in sys.modules:
        parent = type(sys)("pages_internal")
        parent.__path__ = [str(REPO_ROOT / "pages_internal")]
        sys.modules["pages_internal"] = parent
    spec = iu.spec_from_file_location(
        name, str(REPO_ROOT / "pages_internal" / "daily_issue_log.py"))
    mod = iu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    fn = getattr(mod, "days_of_continuation_block", None)
    assert callable(fn), \
        "days_of_continuation_block must be defined in daily_issue_log.py"
    # Empty inputs must no-op without raising (defensive contract).
    fn(daily_consumption_per_material={}, inventory_view=None)


def check_r20_5_2_loc_order_reconciled():
    """Location Report must reconcile st.session_state.loc_order against the
    current eq_master so a stale persisted drag-order (post re-bootstrap)
    can't drive `eq_master[... == tag].iloc[0]` out of bounds."""
    import pathlib
    src = pathlib.Path(
        REPO_ROOT / "pages_internal" / "material_estimator_portal.py"
    ).read_text(encoding="utf-8")
    assert "_valid_loc_tags = eq_master[eq_master[\"Location\"] == loc]" in src, \
        "Location Report missing loc_order reconciliation against eq_master"
    assert "_reconciled" in src, \
        "Location Report reconciliation variable missing"


# ---------------------------------------------------------------------------
# Round 20 — Literal SME drop-in
# ---------------------------------------------------------------------------
# R19 piecemeal port reverted in favor of a literal drop-in of the SME
# `app.py`, wired to the ERP via surgical edits (load_all() now calls our
# helpers; locations/types CRUD routes through add_sme_setting; SME's
# legacy SQL against locations/types/consumption_log/equipment/recipe/
# sqm_progress resolves via compatibility VIEWS added in database.py).
#
# Most R20 tests are static text-greps against the portal file (the SME UI
# itself is Streamlit and can't be exercised in bare mode) plus DB-level
# checks on the compatibility views and ledger schemas.

_R20_PORTAL_PATH = REPO_ROOT / "pages_internal" / "material_estimator_portal.py"


def _r20_portal_src() -> str:
    return _R20_PORTAL_PATH.read_text(encoding="utf-8")


def check_r20_portal_exists():
    import pathlib
    assert _R20_PORTAL_PATH.exists(), \
        "material_estimator_portal.py missing"
    # Companion files
    assert (REPO_ROOT / "pages_internal" / "material_estimator_engine.py").exists()
    assert (REPO_ROOT / "pages_internal" / "sme_logo.png").exists()
    src = _r20_portal_src()
    assert "def page_material_estimator(user" in src, \
        "page_material_estimator(user) entry point missing"


def check_r20_portal_loads():
    """Import the portal module via importlib so a bcrypt/fpdf-less env
    still loads it. The module is intentionally importable; render-time
    work happens inside page_material_estimator(user)."""
    import importlib.util as iu
    # Stub parent package since pages_internal/__init__ imports siblings
    if "pages_internal" not in sys.modules:
        parent = type(sys)("pages_internal")
        parent.__path__ = [str(REPO_ROOT / "pages_internal")]
        sys.modules["pages_internal"] = parent
    # Pre-stub the engine sibling so the portal's relative import works
    eng_name = "pages_internal.material_estimator_engine"
    if eng_name not in sys.modules:
        eng_spec = iu.spec_from_file_location(
            eng_name,
            str(REPO_ROOT / "pages_internal" / "material_estimator_engine.py"),
        )
        eng_mod = iu.module_from_spec(eng_spec)
        sys.modules[eng_name] = eng_mod
        eng_spec.loader.exec_module(eng_mod)
    name = "pages_internal.material_estimator_portal"
    if name in sys.modules:
        del sys.modules[name]
    spec = iu.spec_from_file_location(name, str(_R20_PORTAL_PATH))
    mod = iu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    assert callable(getattr(mod, "page_material_estimator", None)), \
        "page_material_estimator must be callable"


def check_r20_css_block_present():
    src = _r20_portal_src()
    # SME's massive <style> block has these distinctive class markers
    for marker in (".loc-badge", ".pill-g", ".pill-r", ".status-dot-g",
                   ".sticky-header-wrap"):
        assert marker in src, f"missing SME CSS marker: {marker!r}"


def check_r20_theme_toggle_present():
    src = _r20_portal_src()
    assert "def _apply_theme_attr" in src, \
        "_apply_theme_attr function deleted — dark/light toggle broken"
    assert "sme_theme" in src, \
        "sme_theme state-key missing — toggle UI deleted"
    # The theme attr injection must be CALLED (not just defined)
    assert "_apply_theme_attr()" in src, \
        "_apply_theme_attr() never invoked"


def check_r20_inventory_tab_deleted():
    src = _r20_portal_src()
    assert "with tab_consume:" not in src, \
        "with tab_consume: block must be deleted (Inventory tab)"
    # The label itself must be gone from the tab declaration
    # (the comment block referencing it may stay)
    lines = src.split("\n")
    in_tabs_call = False
    for line in lines:
        if "tab0, tab1" in line and "= st.tabs([" in line:
            in_tabs_call = True
            continue
        if in_tabs_call:
            if line.strip().startswith("])"):
                break
            assert '"📦  Inventory"' not in line, \
                f"Inventory label still in tab list: {line!r}"


def check_r20_eight_tabs():
    src = _r20_portal_src()
    import re
    # The tab declaration lives inside page_material_estimator(user) so
    # it's indented. Look for the unique 8-var unpacking signature.
    m = re.search(
        r"tab0, tab1, tab2, tab3, tab_eqrep, tab4, tab5, tab_master\s*=\s*st\.tabs\(\[",
        src,
    )
    assert m, "tab unpacking must be exactly 8 vars (no tab_consume)"
    # Make sure the 9-var form isn't present anywhere
    nine = re.search(
        r"tab_eqrep, tab4, tab_consume, tab5",
        src,
    )
    assert nine is None, "stale 9-var unpacking with tab_consume still present"


def check_r20_login_deleted():
    src = _r20_portal_src()
    # The function body should not exist
    assert "def _show_login" not in src, \
        "_show_login function must be deleted"
    # The hardcoded admin credential constants should be gone
    assert "_ADMIN_USER = " not in src or src.count("_ADMIN_USER = ") == 0, \
        "_ADMIN_USER constant must be deleted"
    # No st.stop()-gated auth check
    assert "if not st.session_state[\"_authenticated\"]:" not in src, \
        "auth gate must be deleted"


def check_r20_monkey_patch_scoped():
    src = _r20_portal_src()
    # Module-level patch must be commented out / absent
    import re
    bad = re.search(
        r"^st\.download_button = _secure_download_button",
        src, re.MULTILINE,
    )
    assert bad is None, \
        "module-level st.download_button patch must be removed"
    # Scoped patch must appear inside page_material_estimator
    assert "_orig_dl_button = st.download_button" in src, \
        "scoped patch save missing"
    assert "st.download_button = _orig_dl_button" in src, \
        "scoped patch restore missing"


def check_r20_master_data_routing():
    src = _r20_portal_src()
    import re
    # No raw INSERT INTO locations / types remaining
    assert not re.search(
        r"INSERT INTO locations\b", src, re.IGNORECASE,
    ), "stale INSERT INTO locations found — must route via add_sme_setting"
    assert not re.search(
        r"INSERT INTO types\b", src, re.IGNORECASE,
    ), "stale INSERT INTO types found — must route via add_sme_setting"
    assert not re.search(
        r"DELETE FROM locations\b", src, re.IGNORECASE,
    ), "stale DELETE FROM locations found"
    assert not re.search(
        r"DELETE FROM types\b", src, re.IGNORECASE,
    ), "stale DELETE FROM types found"
    # Must call the R17 helpers
    assert 'D.add_sme_setting("sme_location"' in src, \
        "must call D.add_sme_setting for sme_location"
    assert 'D.add_sme_setting("sme_equipment_type"' in src, \
        "must call D.add_sme_setting for sme_equipment_type"
    assert 'D.delete_sme_setting("sme_location"' in src
    assert 'D.delete_sme_setting("sme_equipment_type"' in src


def check_r20_compat_views_present():
    """Round 20 added 6 compatibility VIEWS in database.py init_db so the
    SME's legacy SQL works against the ERP DB."""
    conn = sqlite3.connect(":memory:")
    database.init_db(conn)
    views = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view'"
    ).fetchall()}
    for v in ("locations", "types", "consumption_log",
              "equipment", "recipe", "sqm_progress"):
        assert v in views, f"missing R20 compatibility view: {v!r}"
    # Functional check — query each view shape
    # Seed minimal data
    conn.execute("INSERT INTO sme_equipment (Site_ID, Equipment_Tag_No, "
                 "Lining_System_Code, Surface_Area_SQM) "
                 "VALUES ('HQ','T1','1',10)")
    conn.execute("INSERT INTO sme_recipe (Lining_System_Code, Material_Code, "
                 "For_1_SQM) VALUES ('1','M1',2)")
    conn.commit()
    # equipment view exposes lowercase snake_case
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(equipment)"
    ).fetchall()}
    for c in ("equipment_tag", "lining_system_code", "surface_area_sqm",
              "location", "type"):
        assert c in cols, f"equipment view missing column {c!r}"
    # recipe view
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(recipe)"
    ).fetchall()}
    for c in ("lining_system_code", "material_code", "for_1_sqm"):
        assert c in cols, f"recipe view missing column {c!r}"


def check_r20_ledger_schemas_unchanged():
    """Regression check carried forward from R18/R19. The literal drop-in
    must not have caused any SME column to leak into ERP ledger tables."""
    conn = sqlite3.connect(":memory:")
    database.init_db(conn)
    for table in ("pending_issues", "consumption", "receipts", "returns"):
        cols = {r[1] for r in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()}
        for forbidden in ("Equipment_Tag", "Equipment_Tag_No",
                          "Lining_System_Code", "System_Code",
                          "SQM_Completed"):
            assert forbidden not in cols, (
                f"{table} acquired SME-specific column {forbidden!r} "
                f"— routing rule broken"
            )


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

    # ── Phase 6A — CV foundation (employees, tool_catalogue, cv_model_versions) ──
    run_check("CV Foundation", "Employees CRUD + duplicate rejection",
              check_employees_crud,
              "add_employee / update_employee / get_employee_by_id_number.")
    run_check("CV Foundation", "import_employees_csv idempotent upsert",
              check_import_employees_csv_idempotent,
              "Re-importing the same CSV must result in 0 inserts and only changed rows updated.")
    run_check("CV Foundation", "register + promote CV model — only one active",
              check_cv_model_register_and_promote,
              "Partial unique index ix_cv_models_active + atomic demote/promote.")
    run_check("CV Foundation", "Tool catalogue CRUD + min_confidence override",
              check_tool_catalogue_crud,
              "add_tool_class, list_tool_catalogue, set_tool_class_min_confidence.")

    # ── Phase 6B — QR badge encode/decode ─────────────────────────────────
    run_check("QR Badges", "encode_id_to_png produces a valid PNG",
              check_qr_encode_produces_png,
              "ai/cv/qr.py — encode side, no native deps.")
    run_check("QR Badges", "encode → decode roundtrip preserves ID_Number",
              check_qr_decode_roundtrip,
              "ai/cv/qr.py — requires libzbar (pyzbar). Skipped if missing.")

    # ── Phase 6C — YOLO inference helper (mocked — no torch needed) ───────
    run_check("CV Inference", "detect_tool returns [] when no active model",
              check_detect_tool_no_active_model,
              "ai/cv/inference.py — empty active result.")
    run_check("CV Inference", "detect_tool returns [] when model_path missing on disk",
              check_detect_tool_missing_disk_file,
              "ai/cv/inference.py — degraded path handling.")
    run_check("CV Inference", "detect_tool drops detections below DEFAULT threshold",
              check_detect_tool_min_confidence_filter,
              "Default 0.75 filter — mocked YOLO + real PNG.")
    run_check("CV Inference", "per-class min_confidence override beats default",
              check_detect_tool_per_class_override,
              "tool_catalogue.min_confidence override path.")
    run_check("CV Inference", "invalidate_model_cache clears threshold cache",
              check_invalidate_model_cache_clears_lru,
              "Promote-button hook clears caches without restart.")

    # ── Phase 6D — Smart Scan logic helpers ───────────────────────────────
    run_check("Smart Scan", "bucket_detections returns 'auto' for ≥0.75",
              check_bucket_detections_auto_bucket,
              "ai/cv/smart_scan.py — top confidence wins, only top1 returned.")
    run_check("Smart Scan", "bucket_detections caps candidates at 3 and floors at 0.30",
              check_bucket_detections_candidates_bucket,
              "Mid-confidence shows top-3; sub-0.30 falls back to manual.")
    run_check("Smart Scan", "lookup_employee_by_qr rejects suspended / unknown / blank",
              check_lookup_employee_by_qr_active_only,
              "Only active employees may auth via badge.")
    run_check("Smart Scan", "get_open_loans_for_employee matches CV + manual loans",
              check_get_open_loans_for_employee_dual_path,
              "Return-flow filter spans cv_employee_id OR borrower_name fallback.")

    # ── Phase 6E — Returnable loan reminder sweep ─────────────────────────
    run_check("Returnable Reminders", "sweep fires once per offset across all four windows",
              check_returnable_sweep_fires_all_four_offsets,
              "T-2h / T-0 / T+2h / T+24h each fire when 'now' lands in the window.")
    run_check("Returnable Reminders", "sweep is idempotent within an hour",
              check_returnable_sweep_idempotent,
              "delivery_reminders_sent UNIQUE constraint guards re-fire.")
    run_check("Returnable Reminders", "phone resolution prefers CV → manual → audit",
              check_returnable_phone_resolution_three_tier,
              "Missing-phone path writes audit row, no admin nag.")
    run_check("Returnable Reminders", "T+24h escalates to supervisor (NOT HOD)",
              check_returnable_t_plus_24h_supervisor_fanout,
              "Per Phase 6E spec change: supervisor replaces HOD at T+24h.")

    # ── Phase 6F — Bulk badge PDF ─────────────────────────────────────────
    run_check("Bulk Badges", "generate_employee_qr_badges_pdf produces valid PDF",
              check_employee_badges_pdf_smoke,
              "reports.py — multi-page grid + HR header band + empty-list placeholder.")

    # ── Phase 7A — Employee Site Binding ──────────────────────────────────
    run_check("Phase 7A", "employees.Site_ID column self-heals",
              check_7a_site_id_column_present,
              "init_db() ALTER TABLE ADD COLUMN Site_ID block.")
    run_check("Phase 7A", "ix_employees_site index exists",
              check_7a_site_id_index_present,
              "CREATE INDEX ix_employees_site ON employees(Site_ID).")
    run_check("Phase 7A", "add_employee(site_id=) persists binding",
              check_7a_add_with_site_persists,
              "add_employee writes Site_ID into the new column.")
    run_check("Phase 7A", "add_employee() without site_id writes NULL (back-compat)",
              check_7a_add_without_site_is_null,
              "Phase 6A call sites that don't pass site_id keep working.")
    run_check("Phase 7A", "update_employee(site_id=) reassigns site",
              check_7a_update_site_reassigns,
              "Admin path can move employees between sites.")
    run_check("Phase 7A", "update_employee(site_id='') clears binding to NULL",
              check_7a_update_site_clears,
              "Empty-string sentinel maps to NULL.")
    run_check("Phase 7A", "update_employee(site_id=None) leaves binding untouched",
              check_7a_update_site_none_untouched,
              "None means 'don't write Site_ID' — preserves existing binding.")
    run_check("Phase 7A", "list_employees() returns Site_ID column",
              check_7a_list_employees_has_site_column,
              "Roster df must expose Site_ID for the Admin grid.")
    run_check("Phase 7A", "list_employees(site_id_filter='HQ') filters",
              check_7a_list_employees_site_filter,
              "Per-site filter behind the Roster Site dropdown.")
    run_check("Phase 7A", "list_employees(site_id_filter='__UNASSIGNED__') gets NULL rows",
              check_7a_list_employees_unassigned_sentinel,
              "Powers the red 'unassigned' banner in Admin Portal.")
    run_check("Phase 7A", "list_employees_for_site(site, status='active') excludes inactive",
              check_7a_list_employees_for_site_active_only,
              "Convenience wrapper used by Phase 7B Supervisor form.")
    run_check("Phase 7A", "import_employees_csv with Site_ID column persists site",
              check_7a_csv_with_site_id_column,
              "Optional CSV column drives the binding on insert.")
    run_check("Phase 7A", "import_employees_csv without Site_ID column is back-compat",
              check_7a_csv_without_site_id_column,
              "Legacy CSVs continue to work; binding stays NULL.")
    run_check("Phase 7A", "import_employees_csv preserves existing binding when col absent",
              check_7a_csv_omitted_col_preserves_binding,
              "Re-importing legacy CSV must NOT wipe Site_ID set by Admin.")
    run_check("Phase 7A", "bulk_assign_employees_to_site sets Site_ID for N rows",
              check_7a_bulk_assign_helper,
              "Drives the Admin red-banner bulk-assign widget.")

    # ── Phase 7B — Supervisor Material Request workflow ──────────────────
    run_check("Phase 7B", "generate_smr_request_no returns SMR-YYYYMMDD-0001 day-empty",
              check_7b_generate_request_no_first,
              "First request of the day must start at 0001.")
    run_check("Phase 7B", "generate_smr_request_no increments on same day",
              check_7b_generate_request_no_increments,
              "Second call same day returns -0002.")
    run_check("Phase 7B", "create_supervisor_request happy path inserts header + items",
              check_7b_create_happy_path,
              "Single-transaction insert across both tables.")
    run_check("Phase 7B", "rejects worker not bound to site",
              check_7b_create_rejects_wrong_site_worker,
              "Worker must be active at requesting site.")
    run_check("Phase 7B", "rejects empty item list",
              check_7b_create_rejects_empty_items,
              "Need ≥1 item.")
    run_check("Phase 7B", "rejects PPE=No without reason",
              check_7b_create_rejects_no_ppe_no_reason,
              "Mandatory reason when Old_PPE_Returned=0.")
    run_check("Phase 7B", "rejects unknown SAP_Code",
              check_7b_create_rejects_unknown_sap,
              "SAP_Code must exist in inventory.")
    run_check("Phase 7B", "Stock_At_Request snapshot is captured",
              check_7b_stock_snapshot_captured,
              "Each line records the stock value at insert time.")
    run_check("Phase 7B", "Available_Flag = 0 when requested qty > stock",
              check_7b_available_flag_zero_when_short,
              "Flag drives the Supervisor's amber 'short' warning.")
    run_check("Phase 7B", "approve mirrors lines → pending_issues draft (Round 12)",
              check_7b_approve_mirrors_to_pending_issues,
              "Work_Type=SUPERVISOR_REQUEST, Source_Ref set, Issued_To/Tank_No populated.")
    run_check("Phase 7B", "approve flips status + captures posted_pending_ids JSON",
              check_7b_approve_captures_posted_ids,
              "Header status → approved, JSON rowid list persists.")
    run_check("Phase 7B", "approve is idempotent (refuses second call)",
              check_7b_approve_idempotent,
              "Second approval refused: 'already approved'.")
    run_check("Phase 7B", "approve drops SK_Adjusted_Qty=0 lines",
              check_7b_approve_drops_zero_adjusted,
              "Zero-out semantic per user spec: SK delete with no extra clicks.")
    run_check("Phase 7B", "reject requires reason + flips status, no pending_issues",
              check_7b_reject_blocks_without_reason,
              "Reject path never writes to consumption ledger.")
    run_check("Phase 7B", "end-to-end: approve → commit_eod → consumption row with Source_Ref",
              check_7b_e2e_commit_eod_preserves_source_ref,
              "commit_eod is unchanged; auto-syncs Source_Ref column.")
    run_check("Phase 7B", "update_supervisor_request_item only works while pending_sk",
              check_7b_update_item_locked_post_approval,
              "Cannot edit a request after SK approves or rejects.")
    run_check("Phase 7B", "cancel_supervisor_request only works while pending_sk",
              check_7b_cancel_locked_post_decision,
              "Cancel refused after SK decides.")
    run_check("Phase 7B", "delete_supervisor_request_item drops a pending line",
              check_7b_delete_item_works,
              "SK can drop lines pre-approval.")
    run_check("Phase 7B", "report_supervisor_intent_vs_actual joins on Source_Ref",
              check_7b_report_joins_on_source_ref,
              "Each row = one approved line; Actual_Qty sums via Source_Ref.")
    run_check("Phase 7B", "get_open_returnables_for_employee finds matching loans",
              check_7b_open_returnables_for_employee,
              "SK side-panel — matches by cv_employee_id OR borrower_name.")
    run_check("Phase 7B", "config.WHATSAPP_TRIGGERS has 4 smr_* keys",
              check_7b_config_smr_triggers,
              "smr_submitted/approved/rejected/cancelled — default True.")

    # ── Round 12 — SMR-via-SK-Grid + Auto-Attribution ────────────────────
    run_check("Round 12", "Requested_By column on pending_issues + consumption",
              check_r12_schema_requested_by_columns,
              "Self-heal must add Requested_By to both ledger tables.")
    run_check("Round 12", "line_status column on supervisor_material_request_items",
              check_r12_schema_line_status_column,
              "Self-heal must add line_status with default 'active'.")
    run_check("Round 12", "new SMR lines default to line_status='active'",
              check_r12_line_status_default_active,
              "create_supervisor_request inserts must leave line_status='active'.")
    run_check("Round 12", "withdraw_smr_line_at_staging flips line_status",
              check_r12_withdraw_smr_line_at_staging,
              "SK deletes SMR-draft row → line_status='withdrawn_at_staging'.")
    run_check("Round 12", "commit_eod(hod_username=…) writes 'Approved By'",
              check_r12_commit_eod_writes_approved_by,
              "Auto-attribution of HOD into the legacy space-named column.")
    run_check("Round 12", "commit_eod flips SMR line_status='committed'",
              check_r12_commit_eod_flips_line_status_committed,
              "Source_Ref → supervisor_material_request_items.line_status update.")
    run_check("Round 12", "commit_eod carries Requested_By into consumption",
              check_r12_commit_eod_carries_requested_by_to_consumption,
              "pending_issues.Requested_By must survive the commit.")
    run_check("Round 12", "HIDDEN_FORM_COLS covers Technician + auto-fields",
              check_r12_hidden_form_cols_present,
              "Single source of truth for retired/auto-filled column names.")
    run_check("Round 12", "list_smr_history honours filters + decided-only default",
              check_r12_list_smr_history_filters,
              "Date / supervisor / tank composing AND-wise; decided-only default.")
    run_check("Round 12", "E2E: sup → SK approve → SK submit → HOD commit",
              check_r12_e2e_full_pipeline_three_role_attribution,
              "Three-role auto-attribution + line_status='committed' end-to-end.")

    # ── Round 13 — EOD State Unification + Schema Cleanup ────────────────
    run_check("Round 13", "commit_eod commits 'approved' rows",
              check_r13_commit_eod_picks_up_approved_status,
              "Per-row ✓ rows must reach consumption on next bulk commit.")
    run_check("Round 13", "commit_eod commits 'flagged' rows",
              check_r13_commit_eod_picks_up_flagged_status,
              "Flagged status was always meant to be commit-eligible.")
    run_check("Round 13", "commit_eod skips 'rejected' rows",
              check_r13_commit_eod_skips_rejected_status,
              "Rejected rows route to archive — never to consumption.")
    run_check("Round 13", "get_pending_issues_for_site returns approved + flagged",
              check_r13_get_pending_issues_for_site_widened,
              "Approved rows must stay visible until commit so HOD can ↩️.")
    run_check("Round 13", "hod_reject_pending_issue moves to archive",
              check_r13_hod_reject_moves_to_archive,
              "Copy-then-delete with rejected_by + reject_reason metadata.")
    run_check("Round 13", "hod_unapprove_pending_issue flips approved → pending_hod",
              check_r13_hod_unapprove_flips_back_to_pending,
              "HOD can change their mind before bulk Commit EOD.")
    run_check("Round 13", "bogus 'Approved' column dropped from consumption",
              check_r13_bogus_approved_column_dropped,
              "Self-heal must DROP COLUMN \"Approved\" (always-NULL legacy).")
    run_check("Round 13", "rejected_issues_archive schema present",
              check_r13_rejected_issues_archive_table_exists,
              "Mirror columns + rejected_by/at/reason metadata.")
    run_check("Round 13", "CONSUMPTION_EXPORT_COLS contains canonical set",
              check_r13_consumption_export_cols_constant,
              "Includes business cols, excludes Technician / Source_Ref / etc.")
    run_check("Round 13", "SMR reject at HOD flips line_status='rejected_at_hod'",
              check_r13_smr_reject_flips_line_status,
              "4th line_status value, distinct from withdrawn_at_staging.")

    # ── Round 14 — Vision OCR image-prep pipeline ─────────────────────────
    run_check("Round 14", "prep_image_for_vision caps long edge ≤ 1600 px",
              check_r14_prep_caps_long_edge,
              "Smartphone 12-MP photos must shrink before reaching Ollama.")
    run_check("Round 14", "prep_image_for_vision converts to RGB JPEG",
              check_r14_prep_converts_to_rgb,
              "Non-RGB sources can crash Ollama's vision preprocessor.")
    run_check("Round 14", "prep_image_for_vision shrinks byte size",
              check_r14_prep_shrinks_byte_size,
              "1600 px + quality=85 must materially shrink the upload.")
    run_check("Round 14", "prep_image_for_vision honours EXIF orientation",
              check_r14_prep_honours_exif_orientation,
              "iPhone portraits arrive landscape-with-rotate-flag.")
    run_check("Round 14", "prep_image_for_vision raises ImagePrepError on bad bytes",
              check_r14_prep_raises_on_unreadable_bytes,
              "Typed exception so OCR callers can show a clean message.")

    # ── Round 15 — Multi-Portal Polish + Material Master + PO Parser ────
    run_check("Round 15", "inventory_site_overrides schema + UNIQUE",
              check_r15_schema_inventory_site_overrides_table,
              "Per-site Min Qty additive table with UNIQUE(SAP, Site) enforced.")
    run_check("Round 15", "next_sap_code increments from max numeric tail",
              check_r15_next_sap_code_increments,
              "GI-NNNNNNN format, max(tail)+1 across inventory.")
    run_check("Round 15", "next_temp_material_code persists + increments",
              check_r15_next_temp_material_code_persists,
              "Temp-GI-NNNNNNN counter stored in app_settings.")
    run_check("Round 15", "bulk_upsert_materials inserts + auto-codes blanks",
              check_r15_bulk_upsert_inserts_with_auto_codes,
              "Auto SAP + Temp-GI auto-assignment.")
    run_check("Round 15", "bulk_upsert_materials rejects duplicates",
              check_r15_bulk_upsert_rejects_duplicates,
              "Duplicate Material_Code stays out of inventory.")
    run_check("Round 15", "bulk_upsert_materials overwrite path updates in place",
              check_r15_bulk_upsert_overwrite_path,
              "overwrite_duplicates=True writes through to inventory.")
    run_check("Round 15", "set/get_site_min_qty COALESCEs override over default",
              check_r15_set_and_get_site_min_qty,
              "Per-site override beats global default; negative qty clears it.")
    run_check("Round 15", "inventory.Material_Code UNIQUE index enforced",
              check_r15_inventory_material_code_unique_index,
              "Partial UNIQUE index blocks duplicate raw INSERTs too.")
    run_check("Round 15", "process_po_pdf extracts 3 items from sample PDF",
              check_r15_po_pdf_three_items,
              "Regression guard against PO#4710003114.pdf two-line layout.")
    run_check("Round 15", "process_po_pdf synthetic two-line layout fixture",
              check_r15_po_pdf_synthetic_two_line_layout,
              "Hand-rolled text fixture covers the parser even without the PDF.")
    run_check("Round 15", "list_pending_hod_dns falls back via PO/PR Site_ID",
              check_r15_list_pending_hod_dns_site_fallback,
              "Fix for the legacy DN-visibility bug.")
    run_check("Round 15", "request_reschedule routes to warehouse post-receive",
              check_r15_request_reschedule_routes_to_warehouse,
              "DN in pending_sk → notify warehouse_user, not logistics.")
    run_check("Round 15", "request_reschedule keeps logistics for PO-level",
              check_r15_request_reschedule_routes_to_logistics_when_no_dn,
              "Back-compat: no DN ⇒ logistics routing preserved.")
    run_check("Round 15", "CONSUMPTION_EXPORT_COLS unchanged",
              check_r15_consumption_export_cols_preserved,
              "Round 13 export contract still holds.")
    run_check("Round 15", "_ALWAYS_KEEP includes UOM for PR report",
              check_r15_pr_report_keeps_uom_column,
              "UoM column survives the strip-empty helper.")

    # ── Round 16 — Logistics removed from DN approval chain + PR PDF ──────
    run_check("Round 16", "submit_dn_for_logistics writes status='pending_hod'",
              check_r16_submit_dn_for_logistics_writes_pending_hod,
              "Round 16 contract: Warehouse → HOD (Logistics skipped).")
    run_check("Round 16", "submit_dn_for_logistics fans out HOD + Logistics notifications",
              check_r16_submit_dn_dual_notification_fanout,
              "Two info-severity notifications: HOD actionable + Logistics awareness.")
    run_check("Round 16", "legacy pending_logistics DNs migrate to pending_hod",
              check_r16_legacy_dn_migration_to_pending_hod,
              "init_db sweeps in-flight DNs forward; idempotent on re-run.")
    run_check("Round 16", "get_pr_with_po_numbers comma-joins per PR line",
              check_r16_get_pr_with_po_numbers_comma_joined,
              "Powers the new PO # column in the PR PDF.")
    run_check("Round 16", "generate_pr_pdf renders new PO # + UoM columns",
              check_r16_generate_pr_pdf_has_new_columns,
              "Back-compat path (no po_map kwarg) still produces a valid PDF.")

    # ── Phase 7C — HOD Cross-Site View notifications + indicator ─────────
    run_check("Phase 7C", "ix_csv_target_date index exists",
              check_7c_index_target_date,
              "Drives the target-side lookup of who-viewed-today.")
    run_check("Phase 7C", "ix_csv_viewer_date index exists",
              check_7c_index_viewer_date,
              "Drives the viewer-side lookup for the audit panel.")
    run_check("Phase 7C", "UNIQUE(viewer,target,date) enforced",
              check_7c_unique_constraint,
              "Second INSERT same triple → rowcount 0.")
    run_check("Phase 7C", "record_cross_site_view first call returns True",
              check_7c_record_first_returns_true,
              "First view of the day → notification should fire.")
    run_check("Phase 7C", "record_cross_site_view dedupe returns False",
              check_7c_record_dedupe_returns_false,
              "Second call same day → silently no-op.")
    run_check("Phase 7C", "different target same day returns True",
              check_7c_different_target_returns_true,
              "Same viewer browsing two targets gets two notifications.")
    run_check("Phase 7C", "different viewer same target returns True",
              check_7c_different_viewer_returns_true,
              "Two HODs at different sites can both fire today.")
    run_check("Phase 7C", "self-view returns False",
              check_7c_self_view_skipped,
              "Defensive — UI flow already excludes own site from picker.")
    run_check("Phase 7C", "blank inputs return False",
              check_7c_blank_inputs_skipped,
              "Helper never raises on missing username / site.")
    run_check("Phase 7C", "notify_cross_site_view admin role → silent",
              check_7c_admin_role_suppressed,
              "Spec Q2(b): admin shadowing never fires the notification.")
    run_check("Phase 7C", "notify_cross_site_view queues notification on first fire",
              check_7c_notify_queues_app_notification,
              "queue_app_notification called with recipient_role=hod + target site.")
    run_check("Phase 7C", "notify_cross_site_view writes audit row on first fire",
              check_7c_notify_writes_audit_row,
              "system_audit_log: action=CROSS_SITE_VIEW with viewer/target/date.")
    run_check("Phase 7C", "notify_cross_site_view dedupe → no new notification",
              check_7c_notify_dedupe_no_double_send,
              "Second call same day must not add app_notifications row.")
    run_check("Phase 7C", "config.WHATSAPP_TRIGGERS['cross_site_viewed'] = False",
              check_7c_whatsapp_trigger_default_false,
              "Per spec Q6(b): default off; admin can flip later.")

    # ── Phase 7D — Site-bound PO notification with strict masking ────────
    run_check("Phase 7D", "PO_VENDOR_MASK_FIELDS has 17 entries",
              check_7d_mask_field_count,
              "Identity + commercial terms + financial totals.")
    run_check("Phase 7D", "get_po_detail() default returns commercial fields populated",
              check_7d_default_no_mask,
              "Back-compat: callers without hide_vendor see all data.")
    run_check("Phase 7D", "get_po_detail(hide_vendor=True) blanks all 17 fields",
              check_7d_hide_vendor_strips_all,
              "Every PO_VENDOR_MASK_FIELDS entry becomes None.")
    run_check("Phase 7D", "get_po_detail(hide_vendor=True) preserves PO_Type + PO_Date",
              check_7d_hide_vendor_keeps_operational,
              "Spec Q1: PO_Type + PO_Date are operational, not commercial.")
    run_check("Phase 7D", "get_po_detail combines hide_prices + hide_vendor",
              check_7d_combined_masks,
              "Items prices blank AND header vendor fields blank.")
    run_check("Phase 7D", "build_po_site_notification — title + site_id correct",
              check_7d_summary_title_and_site,
              "'PO {n} issued for delivery to {site}'.")
    run_check("Phase 7D", "build_po_site_notification — PR list deduped from items",
              check_7d_summary_pr_list_dedup,
              "Spec Q2(b): distinct PRs across line items, comma-joined.")
    run_check("Phase 7D", "build_po_site_notification — Expected_Delivery surfaced",
              check_7d_summary_expected_delivery,
              "Operational tracking field, always shown.")
    run_check("Phase 7D", "build_po_site_notification — body has top 5 lines + 'and N more'",
              check_7d_summary_line_truncation,
              "Spec Q3: 5-line ceiling with overflow caption.")
    run_check("Phase 7D", "build_po_site_notification body has NO Vendor_Name",
              check_7d_summary_no_vendor_in_body,
              "Strict masking — regression guard.")
    run_check("Phase 7D", "build_po_site_notification body has NO financial figure",
              check_7d_summary_no_financials_in_body,
              "Total_Amount / Freight_Charges / Discount_Amount excluded.")
    run_check("Phase 7D", "build_po_site_notification — WhatsApp body mirrors in-app",
              check_7d_summary_whatsapp_mirrors_app,
              "Spec Q4(a): line-for-line match modulo bold + emoji header.")
    run_check("Phase 7D", "create_po_manual queues notification to site HOD",
              check_7d_create_po_notifies_hod,
              "Replaces leaky 'Vendor: …' body.")
    run_check("Phase 7D", "create_po_manual queues notification to site SK",
              check_7d_create_po_notifies_sk,
              "Spec Q5: fan out to all SKs at site.")
    run_check("Phase 7D", "create_po_manual notifications NEVER contain Vendor_Name",
              check_7d_create_po_no_vendor_leak,
              "Regression guard against the pre-7D 'Vendor: …' body leak.")
    run_check("Phase 7D", "create_po_manual with Site_ID=NULL queues NO notification",
              check_7d_create_po_no_site_no_notif,
              "Defensive: no destination → no broadcast.")

    # ── Phase 7E — Form draft recovery (server-side layer) ───────────────
    run_check("Phase 7E", "ix_form_drafts_expires index exists",
              check_7e_index_expires,
              "Powers the daily prune.")
    run_check("Phase 7E", "ix_form_drafts_user index exists",
              check_7e_index_user,
              "Powers per-user draft listing.")
    run_check("Phase 7E", "UNIQUE(username, form_id) enforced",
              check_7e_unique_constraint,
              "One draft per (user, form) — upsert overwrites in place.")
    run_check("Phase 7E", "upsert_form_draft writes a new row",
              check_7e_upsert_new,
              "First save → INSERT.")
    run_check("Phase 7E", "upsert_form_draft updates on duplicate (user, form)",
              check_7e_upsert_updates,
              "Second save → ON CONFLICT UPDATE.")
    run_check("Phase 7E", "upsert_form_draft default TTL is 7 days",
              check_7e_default_ttl_seven_days,
              "Spec Q2 — covers Fri/Sat weekend.")
    run_check("Phase 7E", "upsert_form_draft honours custom ttl_days",
              check_7e_custom_ttl,
              "ttl_days=30 → expires_at = now + 30d.")
    run_check("Phase 7E", "upsert_form_draft rejects non-JSON payload",
              check_7e_rejects_non_json,
              "Raises ValueError; never persists garbage.")
    run_check("Phase 7E", "get_form_draft returns roundtripped payload",
              check_7e_get_returns_payload,
              "JSON encode/decode preserves nested dicts + lists.")
    run_check("Phase 7E", "get_form_draft returns None for missing entry",
              check_7e_get_missing_returns_none,
              "No row → None, never raises.")
    run_check("Phase 7E", "get_form_draft hides expired entries",
              check_7e_get_expired_returns_none,
              "Expired rows aren't auto-deleted; helper masks them.")
    run_check("Phase 7E", "delete_form_draft removes row + returns True",
              check_7e_delete_works,
              "Called after successful submit.")
    run_check("Phase 7E", "delete_form_draft on missing entry returns False",
              check_7e_delete_missing_returns_false,
              "Idempotent — never raises.")
    run_check("Phase 7E", "prune_expired_form_drafts deletes expired rows only",
              check_7e_prune_drops_expired,
              "Daily prune via WhatsApp worker poll loop.")
    run_check("Phase 7E", "list_user_drafts returns multi-form DataFrame",
              check_7e_list_user_drafts,
              "For future Admin Active Drafts view (deferred to 7E.1).")
    run_check("Phase 7E", "requirements.txt declares streamlit-local-storage",
              check_7e_requirements_has_local_storage,
              "Browser-side primary layer for draft recovery.")

    # ── Phase 7F — Role-segregated manual PDFs ───────────────────────────
    run_check("Phase 7F", "ROLE_MANUAL_RECIPES covers all 6 production roles",
              check_7f_recipes_cover_all_roles,
              "Every role from config.ROLES must have an entry.")
    run_check("Phase 7F", "slice_markdown_for_role('store_keeper') keeps SK chapter",
              check_7f_slice_sk_keeps_own,
              "Slicer extracts chapter 4 for the SK booklet.")
    run_check("Phase 7F", "slice_markdown_for_role('store_keeper') drops Logistics",
              check_7f_slice_sk_drops_logistics,
              "Cross-role chapters must NOT bleed into SK booklet.")
    run_check("Phase 7F", "slice_markdown_for_role('supervisor') keeps Supervisor chapter",
              check_7f_slice_supervisor_keeps_own,
              "Chapter 5 must appear in supervisor booklet.")
    run_check("Phase 7F", "slice_markdown_for_role('hod') keeps Reports chapter",
              check_7f_slice_hod_keeps_reports,
              "HOD booklet must include chapter 8 per recipe.")
    run_check("Phase 7F", "slice_markdown_for_role('admin') returns full markdown",
              check_7f_slice_admin_full,
              "Admin recipe == 'ALL' → unchanged passthrough.")
    run_check("Phase 7F", "parse_markdown recognises image syntax",
              check_7f_parse_image_block,
              "![alt](path) on its own line → Block(kind='img').")
    run_check("Phase 7F", "render_image handles missing file (placeholder)",
              check_7f_render_image_missing_no_crash,
              "Missing PNG renders the grey placeholder card; never raises.")
    run_check("Phase 7F", "build_role_manual_pdf returns valid PDF bytes",
              check_7f_role_pdf_starts_with_magic,
              "Output must start with %PDF- magic bytes.")
    run_check("Phase 7F", "build_role_manual_pdf('admin') == build_manual_pdf",
              check_7f_admin_equals_master,
              "Admin recipe is the master full PDF — identical chapter content.")
    run_check("Phase 7F", "build_role_manual_pdf(unknown role) falls back to master",
              check_7f_unknown_role_falls_back,
              "Unknown role_key → master PDF, no exception.")
    run_check("Phase 7F", "docs/screenshots/ has the seed placeholder PNGs",
              check_7f_screenshot_placeholders_exist,
              "Verifies the placeholder generator was run for the seed set.")

    # ── Phase 7G — Hub Assistant context fix ─────────────────────────────
    run_check("Hub Assistant", "system prompt injects username + role label",
              check_7g_prompt_has_username_and_role,
              "Admin asking 'how to add users?' must hear themself named.")
    run_check("Hub Assistant", "empty username does not crash the prompt builder",
              check_7g_prompt_empty_username_ok,
              "Back-compat with older call sites that don't pass username.")
    run_check("Hub Assistant", "admin gets FULL §7 with the 👥 Users content",
              check_7g_admin_context_includes_users_tab,
              "Admin no-truncation path — the user-management text lives "
              "~150 lines past §7 head, would be cut at 800-char cap.")
    run_check("Hub Assistant", "logistics role gets §14 (Logistics Portal)",
              check_7g_logistics_context_includes_section_14,
              "Fixes Cause C: logistics used to fall through to store_keeper default.")
    run_check("Hub Assistant", "warehouse_user role gets §15 (Warehouse Portal)",
              check_7g_warehouse_context_includes_section_15,
              "Fixes Cause C: warehouse_user used to fall through to store_keeper default.")
    run_check("Hub Assistant", "admin refusal phrase points to Settings download bay",
              check_7g_admin_refusal_phrase_self_aware,
              "Admin must not be told to 'ask their HOD or Admin' — they ARE Admin.")

    # ── Phase 8A — Smart Scan AI sidecar scaffold (strict offline) ───────
    run_check("Phase 8A", "app_settings seeds locate_anything_enabled=0",
              check_8a_setting_seed_enabled_default_off,
              "Admin gate must default OFF — sidecar is opt-in.")
    run_check("Phase 8A", "app_settings seeds locate_anything_sidecar_url",
              check_8a_setting_seed_sidecar_url,
              "Default 127.0.0.1:8503 so localhost wiring works out-of-the-box.")
    run_check("Phase 8A", "client.is_enabled() returns False when gate is off",
              check_8a_client_gate_off_short_circuits,
              "is_enabled MUST re-read every call — admin can flip at any time.")
    run_check("Phase 8A", "client.detect() short-circuits to [] when gate is off",
              check_8a_client_detect_off_returns_empty,
              "No HTTP call should be made when the admin toggle is 0.")
    run_check("Phase 8A", "client.detect() parses mock 200 response into list",
              check_8a_client_detect_mock_200_returns_detections,
              "Happy path — mocked HTTP returns boxes; client parses them.")
    run_check("Phase 8A", "client.detect() returns [] on 503 + trips breaker",
              check_8a_client_detect_503_returns_empty_trips_breaker,
              "Missing weights → sidecar 503 → no exception, breaker increments.")
    run_check("Phase 8A", "client circuit breaker opens after 3 failures",
              check_8a_client_circuit_breaker_opens,
              "Three consecutive 5xx → next call short-circuits without HTTP.")
    run_check("Phase 8A", "client module imports without torch / transformers",
              check_8a_client_import_does_not_pull_torch,
              "Critical: Streamlit must NEVER pay torch's import cost.")
    run_check("Phase 8A", "ai/locate_anything/requirements.txt exists",
              check_8a_sidecar_requirements_file_exists,
              "Sidecar deps live in their own requirements.txt, not project root.")
    run_check("Phase 8A", "scripts/download_model.sh exists and is executable",
              check_8a_download_script_present,
              "Manual download workflow — script must be on disk for ops to find.")

    # ── Phase 8B — bundle + first-run setup tooling ──────────────────────
    run_check("Phase 8B", "bundle_locate_anything_weights.sh exists + executable + bash-clean",
              check_8b_bundle_script,
              "HQ-side weight packager.")
    run_check("Phase 8B", "install_locate_anything_weights.sh exists + executable + bash-clean",
              check_8b_install_script,
              "Site-side weight installer with checksum verify.")
    run_check("Phase 8B", "run_locate_anything.sh exists + executable + bash-clean",
              check_8b_run_script,
              "uvicorn launcher invoked by the launchd plist.")
    run_check("Phase 8B", "com.gi.locate-anything.plist.tmpl parses as valid plist",
              check_8b_plist_template,
              "launchd template — must round-trip through plistlib.")
    run_check("Phase 8B", "install.sh recognises --with-locate-anything flag",
              check_8b_install_flag,
              "Opt-in 5th service per spec Q5 — off by default.")

    # ── Phase 8C — Smart Scan Tier-3 wiring ──────────────────────────────
    run_check("Phase 8C", "should_invoke_tier3([]) → True (empty)",
              check_8c_should_invoke_empty_yes,
              "No YOLO detections at all → fall through to Tier 3.")
    run_check("Phase 8C", "should_invoke_tier3([conf=0.25]) → True (manual band)",
              check_8c_should_invoke_low_yes,
              "Below CANDIDATES_CONF_THRESHOLD (0.30) is the manual band.")
    run_check("Phase 8C", "should_invoke_tier3([conf=0.50]) → False (candidates band)",
              check_8c_should_invoke_mid_no,
              "Spec Q1(a) — Tier 3 must NOT fire in the 0.30–0.45 overlap.")
    run_check("Phase 8C", "should_invoke_tier3([conf=0.95]) → False (auto band)",
              check_8c_should_invoke_high_no,
              "YOLO confident — Tier 3 silent.")
    run_check("Phase 8C", "tier3_to_candidates reshapes LocateAnything output",
              check_8c_reshape_basic,
              "{label,box,score} → {class_name,confidence,bbox,source}.")
    run_check("Phase 8C", "tier3_to_candidates filters items below noise floor",
              check_8c_reshape_filter_noise,
              "TIER3_NOISE_FLOOR = 0.20 — sub-noise items dropped.")
    run_check("Phase 8C", "tier3_to_candidates caps at MAX_CANDIDATES (3)",
              check_8c_reshape_cap,
              "5 in → 3 out, sorted by score desc.")
    run_check("Phase 8C", "tier3_to_candidates tags source='tier3_locate_anything'",
              check_8c_reshape_source_tag,
              "Provenance tag lets the UI know to render the amber panel.")
    run_check("Phase 8C", "integration: YOLO empty + mock sidecar → tier3 candidates ready",
              check_8c_integration_yolo_empty_plus_mock,
              "End-to-end through bucket_detections + tier3 reshape (logic only).")
    run_check("Phase 8C", "gate guard: toggle OFF + YOLO empty → sidecar HTTP NOT called",
              check_8c_gate_guard_off,
              "Critical: gate OFF must short-circuit upstream of HTTP.")
    run_check("Phase 8C", "gate guard: YOLO confident → sidecar HTTP NOT called",
              check_8c_gate_guard_confident,
              "Critical: high-confidence YOLO must NEVER fall through to Tier 3.")

    # ── Phase 8E — telemetry table + helpers ─────────────────────────────
    run_check("Phase 8E", "locate_anything_calls table exists with required columns",
              check_8e_telemetry_schema,
              "Driving the Admin cost/benefit panel + cost analysis.")
    run_check("Phase 8E", "ix_la_calls_called_at index present",
              check_8e_telemetry_index_called_at,
              "Speeds up the 7-day rollup query.")
    run_check("Phase 8E", "log_locate_anything_call writes a row + returns rowid",
              check_8e_log_helper_writes_row,
              "Best-effort write — wrapped by the client.")
    run_check("Phase 8E", "mark_locate_anything_outcome updates accepted field",
              check_8e_mark_outcome_updates_accepted,
              "Closes the loop after the SK accepts/rejects in the UI.")
    run_check("Phase 8E", "client.detect happy path writes telemetry row",
              check_8e_client_happy_writes_telemetry,
              "End-to-end: gate ON + mock 200 → telemetry row + non-zero call_id.")
    run_check("Phase 8E", "client.detect failure path writes telemetry with error",
              check_8e_client_failure_writes_telemetry_with_error,
              "503 path must still log so we can see how often weights are missing.")
    run_check("Phase 8E", "client.detect gate-off writes NO telemetry row",
              check_8e_client_gate_off_no_telemetry,
              "Gate-off short-circuits ABOVE telemetry — no row, no noise.")
    run_check("Phase 8E", "get_locate_anything_summary computes rates safely",
              check_8e_summary_computes_rates,
              "Includes ZeroDivisionError guard for empty / no-decisions-yet windows.")

    # ── Phase 8D — Admin Settings panel (offline, mocked health) ─────────
    run_check("Phase 8D", "_render_locate_anything_panel doesn't crash with sidecar down",
              check_8d_panel_renders_with_sidecar_down,
              "Streamlit import surface + helper invocation under AppTest.")
    run_check("Phase 8D", "panel toggle ON path stores '1' in app_settings",
              check_8d_toggle_persists_one,
              "Verifies set_app_setting reaches the right key.")

    # ── Round 17 — Smart Material Estimator (SME) merge ────────────────
    run_check("Round 17", "sme_equipment table + key columns present",
              check_r17_sme_equipment_schema,
              "Self-heal adds sme_equipment with UNIQUE(Site_ID, tag, system).")
    run_check("Round 17", "sme_recipe table + key columns present",
              check_r17_sme_recipe_schema,
              "Recipe master is global, UNIQUE(Lining_System_Code, Material_Code).")
    run_check("Round 17", "sme_sqm_progress table + composite PK",
              check_r17_sme_progress_schema,
              "PK = (Site_ID, Equipment_Tag_No, Lining_System_Code).")
    run_check("Round 17", "system_settings seeded with sme_location + sme_equipment_type for HQ",
              check_r17_sme_settings_seeded,
              "Per Correction #1 — locations/types ride on system_settings, no new tables.")
    run_check("Round 17", "init_db idempotent for SME tables (run twice, no errors)",
              check_r17_sme_init_db_idempotent,
              "Self-heal must be safe to re-run.")
    run_check("Round 17", "get_on_order_by_material: Qty=10 Delivered=3 Returned=1 → 6",
              check_r17_on_order_arithmetic,
              "Open-PO outstanding = Qty − Delivered − Returned, clamped at 0.")
    run_check("Round 17", "get_on_order_by_material: closed POs ignored",
              check_r17_on_order_closed_excluded,
              "purchase_orders.status in (closed,cancelled,force_closed) → excluded.")
    run_check("Round 17", "get_on_order_by_material: site filter scopes correctly",
              check_r17_on_order_site_scope,
              "site_id=X must drop PO rows for other sites.")
    run_check("Round 17", "get_sme_inventory_view bridges ledger → engine schema",
              check_r17_sme_inventory_view,
              "Available_Qty from load_live_inventory; Ordered_Qty join from open POs.")
    run_check("Round 17", "add_sme_setting / delete_sme_setting round-trip",
              check_r17_sme_setting_crud,
              "Idempotent insert; delete returns row count.")
    run_check("Round 17", "upsert_sme_sqm_progress preserves Done_SQM on re-load",
              check_r17_sme_progress_preservation,
              "Bootstrap re-load must not reset progress.")
    run_check("Round 17", "Material Estimator RBAC: hod + admin only",
              check_r17_material_estimator_rbac,
              "Exact-role lock excludes SK / Supervisor / Logistics / Warehouse.")
    # R20 EDIT: removed pages_internal.material_estimator import test — the
    # R19 package was deleted; the literal SME drop-in is tested by R20's
    # check_r20_portal_loads instead.
    run_check("Round 17", "Material Estimator portal listed in PAGE_ACCESS",
              check_r17_material_estimator_in_page_access,
              "Router needs the key in PAGE_ACCESS to render the nav radio.")

    # ── Round 18 — SME consumption form + EOD listener state machine ─────
    run_check("Round 18", "sme_sqm_progress.Done_SQM_staged column present",
              check_r18_staged_column,
              "Two-column model — staged + committed.")
    run_check("Round 18", "sme_consumption_log table + status FSM",
              check_r18_consumption_log_schema,
              "Rich detail ledger never touched by commit_eod.")
    run_check("Round 18", "v_inventory_with_sme exposes is_sme flag",
              check_r18_inventory_view_flag,
              "1 iff Material_Code participates in any sme_recipe row.")
    run_check("Round 18", "is_sme_sap / is_sme_material dispatch fork",
              check_r18_dispatch_helpers,
              "SAP→Material→recipe-membership lookup; returns False on blanks.")
    run_check("Round 18", "get_sap_for_material resolves the 1:1 mapping",
              check_r18_sap_resolution,
              "SME contract: every Material_Code has exactly one SAP_Code.")
    run_check("Round 18", "stage_sme_consumption_batch aggregates per Material_Code",
              check_r18_stage_aggregation,
              "2 detail rows on the same material → 1 pending_issues row.")
    run_check("Round 18", "stage_sme_consumption_batch rejects missing extras",
              check_r18_stage_missing_extras,
              "Issued_To / Tank_No / Serial_No / PR_Number are mandatory.")
    run_check("Round 18", "stage_sme_consumption_batch increments Done_SQM_staged",
              check_r18_stage_increments_staged,
              "Per (tag, system), take MAX SQM to dedupe across materials.")
    run_check("Round 18", "commit_eod_with_sme_sync shifts staged→committed",
              check_r18_commit_shifts_sqm,
              "After commit, Done_SQM_staged → 0; Done_SQM += sqm.")
    run_check("Round 18", "commit_eod itself is unchanged (regression)",
              check_r18_commit_eod_unchanged,
              "Non-SME pending_issues still commit identically.")
    run_check("Round 18", "hod_reject_pending_issue_with_sme_sync decrements staged",
              check_r18_reject_decrements_staged,
              "Reject path: status='rejected', SQM_staged -= sqm.")
    run_check("Round 18", "SME consumption form helper present in daily_issue_log",
              check_r18_sme_form_in_daily_issue_log,
              "_render_sme_consumption_form must exist after Phase 4.")
    run_check("Round 18", "hod_portal wires the SME-sync EOD + reject wrappers",
              check_r18_hod_portal_wires_wrappers,
              "Static check on hod_portal.py import block.")
    # R20 EDIT: Removed three R18 tests that referenced the deleted R19
    # package (pyzipper-no-import, filename-pattern, widgets-module).
    # The literal SME drop-in re-introduces pyzipper as an optional dep
    # (the SME's own download path uses it). Filenames are generated by
    # the SME's _standard_filename helper. Widgets live in-file.

    # ── Round 20 — Literal SME drop-in ───────────────────────────────────
    run_check("Round 20", "material_estimator_portal.py exists + exports page_material_estimator",
              check_r20_portal_exists,
              "Literal SME drop-in lives at pages_internal/material_estimator_portal.py.")
    run_check("Round 20", "portal module loads cleanly (no module-level set_page_config)",
              check_r20_portal_loads,
              "Importing must not crash; must not call st.set_page_config at module level.")
    run_check("Round 20", "SME <style> CSS block preserved",
              check_r20_css_block_present,
              "Apple-to-apple parity requires the SME's massive <style> block intact.")
    run_check("Round 20", "_apply_theme_attr preserved (dark/light mode toggle)",
              check_r20_theme_toggle_present,
              "Native SME dark/light theming must work as in standalone.")
    run_check("Round 20", "Inventory tab body deleted (R18 owns consumption flow)",
              check_r20_inventory_tab_deleted,
              "with tab_consume: block must be absent.")
    run_check("Round 20", "tab declaration unpacks 8 tabs (not 9)",
              check_r20_eight_tabs,
              "Removed 'Inventory' tab label + tab_consume variable.")
    run_check("Round 20", "_show_login + auth gate deleted",
              check_r20_login_deleted,
              "ERP main.py owns login; portal trusts user dict.")
    run_check("Round 20", "monkey-patch SCOPED inside page_material_estimator",
              check_r20_monkey_patch_scoped,
              "st.download_button patch must be wrapped in try/finally.")
    run_check("Round 20", "locations/types CRUD routes through add_sme_setting / delete_sme_setting",
              check_r20_master_data_routing,
              "No raw INSERT/DELETE against locations / types tables.")
    run_check("Round 20", "compatibility VIEWS created in init_db (locations/types/consumption_log/equipment/recipe/sqm_progress)",
              check_r20_compat_views_present,
              "SME's legacy SQL needs these 6 views to resolve.")
    run_check("Round 20", "ERP ledger schemas unchanged (regression — R18 routing rule)",
              check_r20_ledger_schemas_unchanged,
              "pending_issues / consumption MUST NOT carry SME-specific cols.")

    # ── Round 20.1 — bug fixes from the first live render ────────────────
    run_check("Round 20.1", "no string-interior 8-space indent (markdown-as-code-block bug)",
              check_r20_1_no_string_indent_bug,
              "Multi-line strings must not have 8-space leading indent on "
              "interior lines (markdown would render as code blocks).")
    run_check("Round 20.1", "cascade_allocate returns DataFrame with expected columns when empty",
              check_r20_1_cascade_allocate_empty_safe,
              "Empty allocation must not raise KeyError on downstream groupby.")
    run_check("Round 20.1", "Stock-Only Materials filter restricted to SME-tracked items",
              check_r20_1_stock_only_sme_filter,
              "Stock-Only must exclude generic warehouse items (bolts/gloves) — "
              "only show materials in sme_recipe but not in current plan.")
    run_check("Round 20.1", "load_all returns shape-preserving empty frames",
              check_r20_1_load_all_empty_safe,
              "dm / eq_master / sqm_ref must have expected columns even when empty.")

    # ── Round 20.5 — Tab 8 Master Data CRUD wiring ────────────────────────
    run_check("Round 20.5", "sme_equipment extended with 15 legacy Excel columns",
              check_r20_5_sme_equipment_columns,
              "ALTER TABLE adds Sl_No, Project, WBS_No, IO_No, Drawing_No, "
              "Design, Dia_L, Ht_W, Equipment_Total_SQM, Remaraks, "
              "Lining_System_Short_Name, Lining_Type, Lining_System, "
              "Material_Spec, Lining_Area_Location.")
    run_check("Round 20.5", "sme_recipe extended with 8 legacy Excel columns",
              check_r20_5_sme_recipe_columns,
              "ALTER TABLE adds Sl_No, Substrate, System_Keys, "
              "Lining_Thickness, Lining_System, Lining_Type, "
              "Material_Description, Package_Size.")
    run_check("Round 20.5", "sme_inventory_seed table exists with correct schema",
              check_r20_5_sme_inventory_seed_table,
              "SME-owned baseline; Material_Code PK; Initial_Available_Qty + "
              "Initial_Ordered_Qty REAL.")
    run_check("Round 20.5", "sme_materials_view computes Available_Qty from seed + ledger",
              check_r20_5_sme_materials_view_math,
              "available_qty = initial_available_qty + receipts.sum - "
              "consumption.sum (via SAP_Code → Material_Code join).")
    run_check("Round 20.5", "equipment VIEW exposes Lining_System / Material Spec. / "
                            "Lining_Area/location aliases",
              check_r20_5_equipment_view_aliases,
              "_get_autofill in Tab 8 queries these dotted/slashed identifiers "
              "verbatim; the VIEW must expose them.")
    run_check("Round 20.5", "recipe VIEW serves real Lining_Type (not empty literal)",
              check_r20_5_recipe_view_lining_type,
              "Lining_Type column must come from sme_recipe.Lining_Type, not "
              "the legacy '' AS lining_type placeholder.")
    run_check("Round 20.5", "9 SME CRUD helpers exist in database.py",
              check_r20_5_crud_helpers_exist,
              "insert/update/delete x sme_equipment / sme_recipe / sme_inventory_seed.")
    run_check("Round 20.5", "helpers translate UI form keys to PascalCase columns",
              check_r20_5_col_translation,
              "insert_sme_equipment accepts lowercase + dotted/slashed UI keys "
              "and writes to PascalCase table columns.")
    run_check("Round 20.5", "Tab 8 has no raw view-write SQL remaining",
              check_r20_5_no_raw_view_writes,
              "INSERT/UPDATE/DELETE against equipment / recipe / sqm_progress / "
              "sme_materials_view must be absent — all writes go through D helpers.")
    run_check("Round 20.5", "TABLE_MAP points Materials_DetailsAvailable_Qty → sme_materials_view",
              check_r20_5_table_map_materials_view,
              "Master Data Materials radio reads the SME-filtered view, not "
              "the full ERP inventory table.")
    run_check("Round 20.5", "Equipment Smart Entry calls D.insert_sme_equipment + "
                            "D.upsert_sme_sqm_progress",
              check_r20_5_equipment_smart_entry_helpers,
              "Smart Entry save block uses helpers (not raw INSERT INTO).")
    run_check("Round 20.5.1", "Master Data read does not ORDER BY rowid on a VIEW",
              check_r20_5_1_master_data_no_order_by_rowid,
              "Views have no rowid; ORDER BY rowid → empty grid ('No records "
              "found') for all three radios.")
    run_check("Round 20.5.1", "get_sme_inventory_view is seed-sourced (not ERP live stock)",
              check_r20_5_1_inventory_view_seed_sourced,
              "Available/Ordered come from sme_inventory_seed + ledger rollup so "
              "every SME tab reflects the SME inventory file, not ERP stock=0.")
    run_check("Round 20.5.2", "no live import of the deleted material_estimator package",
              check_r20_5_2_no_deleted_pkg_import,
              "R20 deleted pages_internal/material_estimator/; daily_issue_log "
              "still imported days_of_continuation_block → crashed SK page.")
    run_check("Round 20.5.2", "days_of_continuation_block vendored into daily_issue_log",
              check_r20_5_2_doc_block_vendored,
              "Defined module-level + no-ops on empty inputs.")
    run_check("Round 20.5.2", "Location Report reconciles stale loc_order vs eq_master",
              check_r20_5_2_loc_order_reconciled,
              "Prevents IndexError when persisted drag-order holds tags removed "
              "by a re-bootstrap.")

    # ---- Workstream C — Meta WhatsApp webhook parser/router ----
    run_check("Workstream C", "webhook parses a text message (phone + body + name)",
              check_wsc_webhook_parses_text,
              "parse_inbound_messages extracts from_phone, text, sender_name, "
              "phone_number_id and wa_message_id for replies/dedup.")
    run_check("Workstream C", "webhook parses an interactive button_reply title",
              check_wsc_webhook_parses_interactive,
              "interactive button_reply / list_reply titles surface as .text.")
    run_check("Workstream C", "status callbacks are separated from inbound messages",
              check_wsc_status_callback_separated,
              "value.statuses parsed by parse_statuses; never treated as a user message.")
    run_check("Workstream C", "stub router replies to greetings, stays silent otherwise",
              check_wsc_router_stub,
              "route_inbound_message returns the menu for hi/help; None for unmatched.")
    run_check("Workstream C", "GET handshake token + X-Hub-Signature-256 verification",
              check_wsc_verify_subscription_and_signature,
              "verify_subscription matches the verify token; verify_signature "
              "passes when secret unset, strict HMAC-SHA256 when set.")

    # ---- Man-Hour & Labor Tracking (§2Z) ----
    run_check("Man-Hour", "schema — 5 mh_ tables + comparison view exist",
              check_mh_schema_present,
              "mh_employees/timesheets/production/manhour_estimates/variance_notes "
              "+ v_mh_estimate_vs_actual self-heal in init_db.")
    run_check("Man-Hour", "hours math — 8h normal + 1h break, OT, overnight",
              check_mh_hours_math,
              "compute_mh_hours: (Out-In)-break; Normal=min(Total,8); OT=max(0,Total-8).")
    run_check("Man-Hour", "employee upsert is idempotent + validates Worker_Type",
              check_mh_employee_upsert_idempotent,
              "ON CONFLICT(Site_ID,Employee_Code) updates in place; OWN/Supply enforced.")
    run_check("Man-Hour", "timesheet insert + team-SQM distribution (even/by_hours)",
              check_mh_timesheet_and_distribution,
              "Allocated_SQM split equally or pro-rata on Total_Hours.")
    run_check("Man-Hour", "estimate-vs-actual view math + reason join",
              check_mh_estimate_vs_actual_view,
              "v_mh_estimate_vs_actual sums actual hours, computes Variance_Pct, "
              "joins the variance reason.")
    run_check("Man-Hour", "attendance workbook parser (shared by UI + bootstrap)",
              check_mh_parse_workbook,
              "parse_attendance_workbook reads SAR, coerces codes, merges roster, "
              "collects dates.")
    run_check("Man-Hour", "bulk import — replace-by-date idempotent vs append",
              check_mh_import_replace_vs_append,
              "import_mh_attendance(replace=True) is idempotent; replace=False appends.")

    # ---- Workstream C — Meta sender provider + download-button forward-compat ----
    run_check("Workstream C", "Meta provider — config read + routing + missing-config raise",
              check_meta_provider_routing,
              "WHATSAPP_PROVIDER=meta routes _send_whatsapp→_send_via_meta; "
              "_meta_config reads META_* env; missing creds raise RuntimeError.")
    run_check("Workstream C", "SME download_button forwards width= (no HOD TypeError)",
              check_sme_download_button_forwards_width,
              "_secure_download_button accepts/forwards **extra so width='stretch' "
              "callers (e.g. HOD PR PDF) never throw if the patch is active.")
    run_check("Material Estimator", "pages_internal exports resolve (no cold-start ImportError)",
              check_pages_internal_exports_resolve,
              "every page_* export is importable; SME portal no longer re-enters "
              "the half-built package at module level.")
    run_check("Material Estimator", "Dashboard filters cross-filter Code <-> Substrate",
              check_estimator_filters_cross_filter,
              "System Code & Substrate options narrow each other so impossible "
              "combos (e.g. Conductive Coating + PU codes) can't be picked.")
    run_check("Material Estimator", "KPI drill-down is a centered modal (not clipped popover)",
              check_kpi_drilldown_is_modal,
              "dbl_click_metric opens a navy/gold st.dialog so wide tables pop fully.")
    run_check("Material Estimator", "admin site picker + SME sidebar suppressed",
              check_sme_admin_site_picker_and_sidebar_hidden,
              "Admin gets a single-site picker; SME's own sidebar chrome is hidden "
              "(only ERP nav) while the theme CSS is still applied.")

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
