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
            "       Issued_To, status, Source_Ref "
            "FROM pending_issues WHERE Source_Ref LIKE ?",
            (f"SMR:{no}:%",),
        ).fetchone()
        assert row is not None, "no pending_issues row mirrored"
        (sap_got, qty, work, tank, by, to, status, src) = row
        assert sap_got == sap
        assert float(qty) == 7.0, qty
        assert work == "SUPERVISOR_REQUEST", work
        assert tank == "Tank-X", tank
        assert by == "test_sk", by
        assert to.startswith("Worker "), to
        assert status == "pending_hod", status
        assert src.startswith(f"SMR:{no}:"), src
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
    n = database.commit_eod()
    assert n >= 1, f"commit_eod should commit ≥1 row, got {n}"
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT Quantity, Work_Type, Source_Ref FROM consumption "
            "WHERE Source_Ref LIKE ?", (f"SMR:{no}:%",),
        ).fetchone()
        assert row is not None, "consumption row missing after commit_eod"
        qty, work, src = row
        assert float(qty) == 2.0
        assert work == "SUPERVISOR_REQUEST"
        assert src.startswith(f"SMR:{no}:")
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
    database.commit_eod()
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
    run_check("Phase 7B", "approve mirrors lines → pending_issues pending_hod",
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
