"""
backend/models.py — SQLAlchemy 2.0 Declarative schema for GI Hub ERP.

AUTO-GENERATED (for inspection) by introspecting the authoritative live
SQLite schema from database.init_db() — includes self-heal ALTER columns,
not just CREATE TABLE text. This is the *target PostgreSQL* structure for the
future FastAPI backend. It is NOT yet wired to anything; SQLite + database.py
remain the runtime until Phase 5 cutover.

ARCHITECTURAL RULES honoured here (see handoff.md SME Canon):
  1. SME sub-module is feature-frozen; its business logic lives in SQL VIEWs
     (equipment, recipe, sqm_progress, locations, types, consumption_log,
     sme_materials_view) that ALIAS the sme_* tables. Views are NOT modeled as
     tables here — see SME_AND_DERIVED_VIEWS at the bottom; they must be
     re-created as PostgreSQL views at migration time.
  2. No rowid in PostgreSQL. system_settings has already been migrated to an
     explicit `id` PK in SQLite. The remaining PK-less ledger tables
     (consumption, receipts, returns) get a SERIAL `id` here (marked ⚠); the
     Phase-5 copy-script populates id := sqlite rowid to preserve references.
  3. sme_inventory_seed stays strictly separate from ERP `inventory`; live SME
     Available_Qty is DERIVED via sme_materials_view (never stored).
  4. Site_ID columns preserved verbatim for multi-site scoping.
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, Integer,
    LargeBinary, Numeric, Text, UniqueConstraint, text,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all GI Hub ERP models."""


# ==========================================================================
# 1. Core ERP ledger + masters
# ==========================================================================

class AppSettings(Base):
    __tablename__ = "app_settings"
    key = Column(Text, primary_key=True)
    value = Column(Text, nullable=False)

class Consumption(Base):
    __tablename__ = "consumption"
    # ⚠ Postgres SERIAL PK — SQLite used implicit rowid (rowid audit).
    id = Column(Integer, primary_key=True, autoincrement=True)
    Date = Column(Text)
    SAP_Code = Column(Text)
    Quantity = Column(Float)
    Work_Type = Column(Text)
    Remarks = Column(Text)
    Lot_Number = Column(Text)
    FEFO_Override = Column(Text)
    Issued_By = Column(Text)
    Issued_To = Column(Text)
    Tank_No = Column(Text)
    Serial_No = Column(Text)
    PR_Number = Column(Text)
    Site_ID = Column(Text, server_default=text("'HQ'"))
    wbs = Column(Text)
    Source_Ref = Column(Text)
    Requested_By = Column(Text)
    Approved_By = Column("Approved By", Text)

class CrossSiteViews(Base):
    __tablename__ = "cross_site_views"
    id = Column(Integer, primary_key=True, autoincrement=True)
    viewer_username = Column(Text, nullable=False)
    viewer_site_id = Column(Text)
    target_site_id = Column(Text, nullable=False)
    view_date = Column(Text, nullable=False)
    first_seen_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("viewer_username", "target_site_id", "view_date"),
    )

class CvModelVersions(Base):
    __tablename__ = "cv_model_versions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(Text, nullable=False, unique=True)
    model_path = Column(Text, nullable=False)
    classes_json = Column(Text, nullable=False)
    mAP = Column(Float)
    trained_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    is_active = Column(Integer, unique=True, server_default=text('0'))

class DnItems(Base):
    __tablename__ = "dn_items"
    id = Column(Integer, primary_key=True, autoincrement=True)
    DN_Number = Column(Text, nullable=False)
    po_item_id = Column(Integer, nullable=False)
    Material_Code = Column(Text)
    Description = Column(Text)
    Qty = Column(Float, nullable=False)
    UOM = Column(Text)
    Lot_Number = Column(Text)
    Expiry_Date = Column(Text)
    Remarks = Column(Text)
    rl_bl_family = Column(Text)
    sk_received_qty = Column(Float)
    status = Column(Text, server_default=text("'pending'"))
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        CheckConstraint("status IN ('pending','received','partial','returned','cancelled')"),
    )

class Employees(Base):
    __tablename__ = "employees"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ID_Number = Column(Text, nullable=False, unique=True)
    Name = Column(Text, nullable=False)
    Phone_Number = Column(Text)
    Department = Column(Text)
    status = Column(Text, server_default=text("'active'"))
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    Site_ID = Column(Text)
    __table_args__ = (
        CheckConstraint("status IN ('active','inactive','suspended')"),
    )

class EntryAttachments(Base):
    __tablename__ = "entry_attachments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Site_ID = Column(Text, nullable=False)
    doc_type = Column(Text, nullable=False)
    doc_number = Column(Text, nullable=False)
    entry_table = Column(Text)
    entry_id = Column(Integer)
    entry_date = Column(Text)
    file_name = Column(Text, nullable=False)
    mime_type = Column(Text)
    file_size = Column(Integer)
    file_blob = Column(LargeBinary)
    disk_path = Column(Text)
    uploaded_by = Column(Text, nullable=False)
    uploaded_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        CheckConstraint("doc_type IN ('consumption','receipt','return')"),
    )

class FormDrafts(Base):
    __tablename__ = "form_drafts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(Text, nullable=False)
    form_id = Column(Text, nullable=False)
    site_id = Column(Text)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    expires_at = Column(DateTime)
    __table_args__ = (
        UniqueConstraint("username", "form_id"),
    )

class Inventory(Base):
    __tablename__ = "inventory"
    SAP_Code = Column(Text, primary_key=True)
    Equipment_Description = Column(Text)
    Material_Code = Column(Text, unique=True)
    UOM = Column(Text)
    Minimum_Qty = Column(Float, server_default=text('0'))
    Unit_Cost = Column(Float, server_default=text('0'))
    Site_ID = Column(Text, server_default=text("'HQ'"))
    Expiry_Date = Column(Text)
    Category = Column(Text, server_default=text("'Others'"))
    Opening_Stock = Column(Float, server_default=text('0'))

class InventorySiteCosts(Base):
    __tablename__ = "inventory_site_costs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    SAP_Code = Column(Text, nullable=False)
    Site_ID = Column(Text, nullable=False)
    Unit_Cost = Column(Float, nullable=False)
    updated_by = Column(Text)
    updated_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("SAP_Code", "Site_ID"),
    )

class InventorySiteOverrides(Base):
    __tablename__ = "inventory_site_overrides"
    id = Column(Integer, primary_key=True, autoincrement=True)
    SAP_Code = Column(Text, nullable=False)
    Site_ID = Column(Text, nullable=False)
    Minimum_Qty = Column(Float, nullable=False)
    updated_by = Column(Text)
    updated_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("SAP_Code", "Site_ID"),
    )

class LocateAnythingCalls(Base):
    __tablename__ = "locate_anything_calls"
    id = Column(Integer, primary_key=True, autoincrement=True)
    called_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    site_id = Column(Text)
    sk_username = Column(Text)
    yolo_top_conf = Column(Float)
    detection_count = Column(Integer)
    accepted = Column(Integer)
    latency_ms = Column(Integer)
    error = Column(Text)

class MtcDocuments(Base):
    __tablename__ = "mtc_documents"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Site_ID = Column(Text, nullable=False)
    SAP_Code = Column(Text, nullable=False)
    Material_Code = Column(Text)
    Lot_Number = Column(Text)
    Quantity = Column(Float)
    mtc_number = Column(Text)
    file_name = Column(Text)
    mime_type = Column(Text)
    file_blob = Column(LargeBinary)
    disk_path = Column(Text)
    status = Column(Text, server_default=text("'attached'"))
    pending_receipt_id = Column(Integer)
    submitted_by = Column(Text, nullable=False)
    submitted_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    logistics_emailed_at = Column(DateTime)
    __table_args__ = (
        CheckConstraint("status IN ('attached','missing','sent_to_logistics')"),
    )

class PendingIssues(Base):
    __tablename__ = "pending_issues"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Date = Column(Text)
    SAP_Code = Column(Text)
    Quantity = Column(Float)
    Work_Type = Column(Text)
    Remarks = Column(Text)
    Timestamp = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    Lot_Number = Column(Text)
    FEFO_Override = Column(Text)
    Issued_By = Column(Text)
    Issued_To = Column(Text)
    Tank_No = Column(Text)
    Serial_No = Column(Text)
    PR_Number = Column(Text)
    Site_ID = Column(Text, server_default=text("'HQ'"))
    status = Column(Text, server_default=text("'draft'"))
    wbs = Column(Text)
    Source_Ref = Column(Text)
    Requested_By = Column(Text)

class PendingReceipts(Base):
    __tablename__ = "pending_receipts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Date = Column(Text)
    SAP_Code = Column(Text)
    Serial_No = Column(Text)
    PR = Column(Text)
    Quantity = Column(Float)
    Location = Column(Text)
    Vehicle_No = Column(Text)
    Driver_Name = Column(Text)
    DN_No = Column(Text)
    Pallet_No = Column(Text)
    Mob_From = Column(Text)
    Prepared_by = Column(Text)
    Mob_To = Column(Text)
    Received_by = Column(Text)
    DN_Copy = Column(Text)
    Remarks = Column(Text)
    Supplier = Column(Text)
    PR_Number = Column(Text)
    Expiry_Date = Column(Text)
    Timestamp = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    status = Column(Text, server_default=text("'draft'"))
    Site_ID = Column(Text, server_default=text("'HQ'"))
    rejection_reason = Column(Text)
    Lot_Number = Column(Text)
    Bin_Location = Column(Text)
    wbs = Column(Text)
    DN_Number = Column(Text)
    Warehouse_ID = Column(Text)
    PO_Number_Source = Column(Text)

class PendingReturns(Base):
    __tablename__ = "pending_returns"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Site_ID = Column(Text, nullable=False)
    SAP_Code = Column(Text, nullable=False)
    Material_Code = Column(Text)
    Equipment_Description = Column(Text)
    Quantity = Column(Float, nullable=False)
    Return_Reason = Column(Text, nullable=False)
    Return_DN_No = Column(Text, nullable=False)
    received_date = Column(Text)
    received_dn_no = Column(Text)
    received_qty = Column(Float)
    PR_Number = Column(Text)
    Lot_Number = Column(Text)
    override_required = Column(Integer, server_default=text('0'))
    override_reason = Column(Text)
    status = Column(Text, server_default=text("'pending_hod'"))
    submitted_by = Column(Text, nullable=False)
    submitted_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    approved_by = Column(Text)
    approved_at = Column(DateTime)
    rejection_reason = Column(Text)
    __table_args__ = (
        CheckConstraint("status IN ('pending_hod','approved','rejected')"),
    )

class PendingUsers(Base):
    __tablename__ = "pending_users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(Text, nullable=False, unique=True)
    password_hash = Column(Text, nullable=False)
    role = Column(Text, nullable=False)
    Site_ID = Column(Text, nullable=False)
    status = Column(Text, server_default=text("'pending'"))
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    Phone_Number = Column(Text)
    Warehouse_ID = Column(Text)

class PoRescheduleRequests(Base):
    __tablename__ = "po_reschedule_requests"
    id = Column(Integer, primary_key=True, autoincrement=True)
    PO_Number = Column(Text, nullable=False)
    DN_Number = Column(Text)
    current_date = Column(Text)
    requested_date = Column(Text, nullable=False)
    reason = Column(Text, nullable=False)
    requested_by_role = Column(Text, nullable=False)
    requested_by = Column(Text, nullable=False)
    requested_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    status = Column(Text, server_default=text("'pending'"))
    decided_by = Column(Text)
    decided_at = Column(DateTime)
    decision_notes = Column(Text)
    __table_args__ = (
        CheckConstraint("requested_by_role IN ('warehouse_user','hod','admin')"),
        CheckConstraint("status IN ('pending','approved','rejected')"),
    )

class PoReturns(Base):
    __tablename__ = "po_returns"
    id = Column(Integer, primary_key=True, autoincrement=True)
    PO_Number = Column(Text, nullable=False)
    po_item_id = Column(Integer)
    DN_Number = Column(Text)
    Material_Code = Column(Text)
    Qty = Column(Float, nullable=False)
    Reason = Column(Text, nullable=False)
    raised_by_role = Column(Text, nullable=False)
    raised_by = Column(Text, nullable=False)
    raised_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    Expected_Resupply = Column(Text)
    status = Column(Text, server_default=text("'open'"))
    closed_at = Column(DateTime)
    closed_by = Column(Text)
    notes = Column(Text)
    __table_args__ = (
        CheckConstraint("raised_by_role IN ('logistics','warehouse_user','hod','store_keeper','admin')"),
        CheckConstraint("status IN ('open','vendor_acknowledged','resupplied','cancelled')"),
    )

class PoShipmentSchedule(Base):
    __tablename__ = "po_shipment_schedule"
    id = Column(Integer, primary_key=True, autoincrement=True)
    PO_Number = Column(Text, nullable=False)
    shipment_no = Column(Text)
    material_group = Column(Text)
    target_date = Column(Text)
    actual_date = Column(Text)
    status = Column(Text, server_default=text("'pending'"))
    notes = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        CheckConstraint("status IN ('pending','shipped','delivered','delayed','cancelled')"),
    )

class PwaTokens(Base):
    __tablename__ = "pwa_tokens"
    token = Column(Text, primary_key=True)
    username = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    last_used_at = Column(DateTime)

class QrApprovalRequests(Base):
    __tablename__ = "qr_approval_requests"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Site_ID = Column(Text, nullable=False)
    SAP_Code = Column(Text, nullable=False)
    Material_Code = Column(Text)
    Equipment_Description = Column(Text)
    Quantity = Column(Integer, server_default=text('1'))
    requested_by = Column(Text, nullable=False)
    requested_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    status = Column(Text, server_default=text("'pending'"))
    approved_by = Column(Text)
    approved_at = Column(DateTime)
    rejection_reason = Column(Text)
    __table_args__ = (
        CheckConstraint("status IN ('pending','approved','rejected')"),
    )

class Receipts(Base):
    __tablename__ = "receipts"
    # ⚠ Postgres SERIAL PK — SQLite used implicit rowid (rowid audit).
    id = Column(Integer, primary_key=True, autoincrement=True)
    Date = Column(Text)
    SAP_Code = Column(Text)
    Quantity = Column(Float)
    Supplier = Column(Text)
    Remarks = Column(Text)
    Unit_Cost = Column(Float)
    Lot_Number = Column(Text)
    Site_ID = Column(Text, server_default=text("'HQ'"))
    Expiry_Date = Column(Text)
    PR_Number = Column(Text)
    Serial_No = Column(Text)
    PR = Column(Text)
    Location = Column(Text)
    Vehicle_No = Column(Text)
    Driver_Name = Column(Text)
    DN_No = Column(Text)
    Pallet_No = Column(Text)
    Mob_From = Column(Text)
    Prepared_by = Column(Text)
    Mob_To = Column(Text)
    Received_by = Column(Text)
    DN_Copy = Column(Text)
    Bin_Location = Column(Text)
    wbs = Column(Text)
    DN_Number = Column(Text)
    Warehouse_ID = Column(Text)
    PO_Number_Source = Column(Text)

class RejectedIssuesArchive(Base):
    __tablename__ = "rejected_issues_archive"
    archive_id = Column(Integer, primary_key=True, autoincrement=True)
    original_id = Column(Integer)
    SAP_Code = Column(Text)
    Quantity = Column(Float)
    Date = Column(Text)
    Site_ID = Column(Text)
    Work_Type = Column(Text)
    Issued_By = Column(Text)
    Issued_To = Column(Text)
    Tank_No = Column(Text)
    Serial_No = Column(Text)
    PR_Number = Column(Text)
    Remarks = Column(Text)
    Lot_Number = Column(Text)
    FEFO_Override = Column(Text)
    Source_Ref = Column(Text)
    Requested_By = Column(Text)
    rejected_by = Column(Text, nullable=False)
    rejected_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    reject_reason = Column(Text)
    wbs = Column(Text)

class Requests(Base):
    __tablename__ = "requests"
    id = Column(Integer, primary_key=True, autoincrement=True)
    requesting_site = Column(Text, nullable=False)
    target_site = Column(Text, nullable=False)
    SAP_Code = Column(Text, nullable=False)
    requested_qty = Column(Float, nullable=False)
    available_qty = Column(Float, server_default=text('0'))
    suggested_qty = Column(Float, server_default=text('0'))
    status = Column(Text, server_default=text("'pending'"))
    notes = Column(Text)
    requested_by = Column(Text)
    reviewed_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        CheckConstraint("status IN ('pending','approved','rejected','fulfilled')"),
    )

class Returns(Base):
    __tablename__ = "returns"
    # ⚠ Postgres SERIAL PK — SQLite used implicit rowid (rowid audit).
    id = Column(Integer, primary_key=True, autoincrement=True)
    Date = Column(Text)
    SAP_Code = Column(Text)
    Quantity = Column(Float)
    Reason = Column(Text)
    Remarks = Column(Text)
    Site_ID = Column(Text, server_default=text("'HQ'"))

class ReturnsHistory(Base):
    __tablename__ = "returns_history"
    archive_id = Column(Integer, primary_key=True, autoincrement=True)
    original_id = Column(Integer)
    Site_ID = Column(Text)
    SAP_Code = Column(Text)
    Material_Code = Column(Text)
    Equipment_Description = Column(Text)
    Quantity = Column(Float)
    Return_Reason = Column(Text)
    Return_DN_No = Column(Text)
    received_date = Column(Text)
    received_dn_no = Column(Text)
    received_qty = Column(Float)
    PR_Number = Column(Text)
    Lot_Number = Column(Text)
    override_required = Column(Integer)
    override_reason = Column(Text)
    status = Column(Text)
    submitted_by = Column(Text)
    submitted_at = Column(DateTime)
    approved_by = Column(Text)
    approved_at = Column(DateTime)
    rejection_reason = Column(Text)
    archived_by = Column(Text)
    archived_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))

class StockAdjustments(Base):
    __tablename__ = "stock_adjustments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Site_ID = Column(Text, nullable=False)
    SAP_Code = Column(Text, nullable=False)
    system_qty = Column(Float, nullable=False)
    counted_qty = Column(Float, nullable=False)
    variance = Column(Float, nullable=False)
    reason_code = Column(Text, nullable=False)
    notes = Column(Text)
    status = Column(Text, server_default=text("'pending_hod'"))
    submitted_by = Column(Text, nullable=False)
    submitted_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    approved_by = Column(Text)
    approved_at = Column(DateTime)
    rejection_reason = Column(Text)
    posted_txn_ref = Column(Text)
    Lot_Number = Column(Text)
    __table_args__ = (
        CheckConstraint("status IN ('pending_hod','approved','rejected')"),
    )

class StockReservations(Base):
    __tablename__ = "stock_reservations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    SAP_Code = Column(Text, nullable=False)
    Site_ID = Column(Text, nullable=False)
    Qty = Column(Float, nullable=False)
    request_id = Column(Integer)
    status = Column(Text, server_default=text("'active'"))
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    released_at = Column(DateTime)
    __table_args__ = (
        CheckConstraint("status IN ('active','released')"),
    )

class SystemSettings(Base):
    __tablename__ = "system_settings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(Text)
    value = Column(Text)
    Site_ID = Column(Text)

class ToolCatalogue(Base):
    __tablename__ = "tool_catalogue"
    id = Column(Integer, primary_key=True, autoincrement=True)
    class_name = Column(Text, nullable=False, unique=True)
    display_name = Column(Text, nullable=False)
    category = Column(Text)
    model_version_id = Column(Integer)
    min_confidence = Column(Float, server_default=text('0.75'))
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))

class UomConversions(Base):
    __tablename__ = "uom_conversions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    SAP_Code = Column(Text, nullable=False)
    Pack_UOM = Column(Text, nullable=False)
    Factor = Column(Float, nullable=False)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("SAP_Code", "Pack_UOM"),
    )

class Users(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(Text, nullable=False, unique=True)
    password_hash = Column(Text, nullable=False)
    role = Column(Text, nullable=False)
    Site_ID = Column(Text, server_default=text("'HQ'"))
    Warehouse_ID = Column(Text)
    Phone_Number = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    totp_secret = Column(Text)
    totp_enabled = Column(Integer, server_default=text('0'))
    __table_args__ = (
        CheckConstraint("role IN ('admin','logistics','hod','warehouse_user','supervisor','store_keeper')"),
    )

class WbsMaster(Base):
    __tablename__ = "wbs_master"
    id = Column(Integer, primary_key=True, autoincrement=True)
    WBS_Number = Column(Text, nullable=False)
    Description = Column(Text)
    Site_ID = Column(Text, nullable=False, server_default=text("'HQ'"))
    status = Column(Text, server_default=text("'active'"))
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("WBS_Number", "Site_ID"),
        CheckConstraint("status IN ('active','closed')"),
    )


# ==========================================================================
# 2. SME sub-module (feature-frozen — strict isolation)
# ==========================================================================

class SmeConsumptionLog(Base):
    __tablename__ = "sme_consumption_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(Text, nullable=False)
    Site_ID = Column(Text, nullable=False)
    entry_date = Column(Text, nullable=False)
    entered_by = Column(Text)
    Equipment_Tag_No = Column(Text, nullable=False)
    Lining_System_Code = Column(Text, nullable=False)
    Material_Code = Column(Text, nullable=False)
    SQM_Completed = Column(Float, nullable=False, server_default=text('0'))
    Expected_Qty = Column(Float, nullable=False, server_default=text('0'))
    Actual_Qty = Column(Float, nullable=False, server_default=text('0'))
    Variance_Pct = Column(Float)
    notes = Column(Text)
    status = Column(Text, nullable=False, server_default=text("'staged'"))
    staged_pi_id = Column(Integer)
    committed_at = Column(DateTime)
    rejected_at = Column(DateTime)
    rejected_reason = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        CheckConstraint("status IN ('staged','committed','rejected')"),
    )

class SmeEquipment(Base):
    __tablename__ = "sme_equipment"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Site_ID = Column(Text, nullable=False)
    Equipment_Tag_No = Column(Text, nullable=False)
    Name = Column(Text)
    Location = Column(Text)
    Type = Column(Text)
    Substrate = Column(Text)
    Lining_System_Code = Column(Text, nullable=False)
    Surface_Area_SQM = Column(Float, nullable=False, server_default=text('0'))
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    Sl_No = Column(Text)
    Project = Column(Text)
    WBS_No = Column(Text)
    IO_No = Column(Text)
    Sub_Location = Column(Text)
    Drawing_No = Column(Text)
    Design = Column(Text)
    Dia_L = Column(Text)
    Ht_W = Column(Text)
    Equipment_Total_SQM = Column(Float)
    Remaraks = Column(Text)
    Lining_System_Short_Name = Column(Text)
    Lining_Type = Column(Text)
    Lining_System = Column(Text)
    Material_Spec = Column(Text)
    Lining_Area_Location = Column(Text)
    __table_args__ = (
        UniqueConstraint("Site_ID", "Equipment_Tag_No", "Lining_System_Code"),
    )

class SmeInventorySeed(Base):
    __tablename__ = "sme_inventory_seed"
    Material_Code = Column(Text, primary_key=True)
    Material_Name = Column(Text)
    Item = Column(Text)
    Vendor = Column(Text)
    Purchasing_Document = Column(Text)
    Document_Date = Column(Text)
    Nature = Column(Text)
    UOM = Column(Text)
    Initial_Available_Qty = Column(Float, server_default=text('0'))
    Initial_Ordered_Qty = Column(Float, server_default=text('0'))
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))

class SmeRecipe(Base):
    __tablename__ = "sme_recipe"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Lining_System_Code = Column(Text, nullable=False)
    Lining_System_Name = Column(Text)
    Material_Code = Column(Text, nullable=False)
    Material_Name = Column(Text)
    UOM = Column(Text)
    Nature = Column(Text)
    For_1_SQM = Column(Float, nullable=False, server_default=text('0'))
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    Sl_No = Column(Text)
    Substrate = Column(Text)
    System_Keys = Column(Text)
    Lining_Thickness = Column(Text)
    Lining_System = Column(Text)
    Lining_Type = Column(Text)
    Material_Description = Column(Text)
    Package_Size = Column(Text)
    __table_args__ = (
        UniqueConstraint("Lining_System_Code", "Material_Code"),
    )

class SmeSqmProgress(Base):
    __tablename__ = "sme_sqm_progress"
    Site_ID = Column(Text, primary_key=True)
    Equipment_Tag_No = Column(Text, primary_key=True)
    Lining_System_Code = Column(Text, primary_key=True)
    Original_SQM = Column(Float, nullable=False, server_default=text('0'))
    Done_SQM = Column(Float, nullable=False, server_default=text('0'))
    updated_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    Done_SQM_staged = Column(Float, server_default=text('0'))


# ==========================================================================
# 3. Man-Hour & Labor tracking
# ==========================================================================

class MhEmployees(Base):
    __tablename__ = "mh_employees"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Site_ID = Column(Text, nullable=False)
    Employee_Code = Column(Text, nullable=False)
    Name = Column(Text, nullable=False)
    Designation = Column(Text)
    Worker_Type = Column(Text, nullable=False, server_default=text("'OWN'"))
    Company = Column(Text)
    linked_id_number = Column(Text)
    status = Column(Text, nullable=False, server_default=text("'active'"))
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("Site_ID", "Employee_Code"),
        CheckConstraint("Worker_Type IN ('OWN','Supply')"),
        CheckConstraint("status IN ('active','inactive')"),
    )

class MhManhourEstimates(Base):
    __tablename__ = "mh_manhour_estimates"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Site_ID = Column(Text, nullable=False)
    Location = Column(Text)
    Equipment_Tag = Column(Text, nullable=False)
    System_Code = Column(Text, nullable=False)
    Estimated_Manhours = Column(Float, nullable=False, server_default=text('0'))
    Estimated_SQM = Column(Float)
    Basis = Column(Text)
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("Site_ID", "Equipment_Tag", "System_Code"),
    )

class MhProduction(Base):
    __tablename__ = "mh_production"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Site_ID = Column(Text, nullable=False)
    Work_Date = Column(Text, nullable=False)
    Equipment_Tag = Column(Text, nullable=False)
    System_Code = Column(Text, nullable=False)
    SQM_Done = Column(Float, nullable=False, server_default=text('0'))
    Distribution_Method = Column(Text, nullable=False, server_default=text("'even'"))
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("Site_ID", "Work_Date", "Equipment_Tag", "System_Code"),
        CheckConstraint("Distribution_Method IN ('even','by_hours','manual')"),
    )

class MhTimesheets(Base):
    __tablename__ = "mh_timesheets"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Site_ID = Column(Text, nullable=False)
    Employee_Code = Column(Text, nullable=False)
    Work_Date = Column(Text, nullable=False)
    Location = Column(Text)
    Equipment_Tag = Column(Text)
    System_Code = Column(Text)
    In_Time = Column(Text)
    Out_Time = Column(Text)
    Break_Mins = Column(Integer, nullable=False, server_default=text('60'))
    Total_Hours = Column(Float, nullable=False, server_default=text('0'))
    Normal_Hours = Column(Float, nullable=False, server_default=text('0'))
    OT_Hours = Column(Float, nullable=False, server_default=text('0'))
    Allocated_SQM = Column(Float, nullable=False, server_default=text('0'))
    Status = Column(Text, nullable=False, server_default=text("'PR'"))
    Remarks = Column(Text)
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("Site_ID", "Employee_Code", "Work_Date", "Equipment_Tag", "System_Code"),
    )

class MhVarianceNotes(Base):
    __tablename__ = "mh_variance_notes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Site_ID = Column(Text, nullable=False)
    Equipment_Tag = Column(Text, nullable=False)
    System_Code = Column(Text, nullable=False)
    Reason = Column(Text, nullable=False)
    entered_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("Site_ID", "Equipment_Tag", "System_Code"),
    )


# ==========================================================================
# 4. Procurement chain (PR / PO / DN / Vendor)
# ==========================================================================

class DeliveryNotes(Base):
    __tablename__ = "delivery_notes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    DN_Number = Column(Text, nullable=False, unique=True)
    PO_Number = Column(Text, nullable=False)
    Warehouse_ID = Column(Text, nullable=False)
    Site_ID = Column(Text, nullable=False)
    rl_bl_family = Column(Text)
    DN_Date = Column(Text)
    Vehicle_No = Column(Text)
    Driver_Name = Column(Text)
    Driver_Phone = Column(Text)
    Prepared_By = Column(Text)
    Remarks = Column(Text)
    status = Column(Text, server_default=text("'draft'"))
    logistics_decided_at = Column(DateTime)
    logistics_decided_by = Column(Text)
    logistics_decision = Column(Text)
    hod_decided_at = Column(DateTime)
    hod_decided_by = Column(Text)
    sk_received_at = Column(DateTime)
    sk_received_by = Column(Text)
    rejection_reason = Column(Text)
    created_by = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        CheckConstraint("status IN ('draft','pending_logistics','logistics_approved','pending_hod','hod_approved','pending_sk','received','rejected','cancelled')"),
    )

class PoAssignments(Base):
    __tablename__ = "po_assignments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    PO_Number = Column(Text, nullable=False)
    Warehouse_ID = Column(Text, nullable=False)
    items_subset_json = Column(Text)
    Expected_Delivery = Column(Text)
    assigned_by = Column(Text, nullable=False)
    assigned_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    acknowledged_at = Column(DateTime)
    acknowledged_by = Column(Text)
    status = Column(Text, server_default=text("'assigned'"))
    notes = Column(Text)
    __table_args__ = (
        CheckConstraint("status IN ('assigned','acknowledged','received','partial','closed','cancelled')"),
    )

class PoForceClosures(Base):
    __tablename__ = "po_force_closures"
    id = Column(Integer, primary_key=True, autoincrement=True)
    target_type = Column(Text, nullable=False)
    target_ref = Column(Text, nullable=False)
    Site_ID = Column(Text)
    PR_Number = Column(Text)
    PO_Number = Column(Text)
    reason = Column(Text, nullable=False)
    closed_by = Column(Text, nullable=False)
    closed_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    notes = Column(Text)
    prior_state = Column(Text)
    reverted_at = Column(DateTime)
    reverted_by = Column(Text)
    __table_args__ = (
        CheckConstraint("target_type IN ('pr','po','po_item')"),
    )

class PoItems(Base):
    __tablename__ = "po_items"
    id = Column(Integer, primary_key=True, autoincrement=True)
    PO_Number = Column(Text, nullable=False)
    line_no = Column(Integer)
    Material_Code = Column(Text)
    Description = Column(Text)
    Qty = Column(Float, nullable=False)
    UOM = Column(Text)
    Unit_Price = Column(Float, server_default=text('0'))
    Total_Price = Column(Float, server_default=text('0'))
    PR_Number = Column(Text)
    WBS_Number = Column(Text)
    Network = Column(Text)
    Plant = Column(Text)
    rl_bl_family = Column(Text)
    Delivered_Qty = Column(Float, server_default=text('0'))
    Returned_Qty = Column(Float, server_default=text('0'))
    line_status = Column(Text, server_default=text("'open'"))
    close_reason = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        CheckConstraint("line_status IN ('open','partially_delivered','delivered','returned','closed','force_closed')"),
    )

class PrMaster(Base):
    __tablename__ = "pr_master"
    id = Column(Integer, primary_key=True, autoincrement=True)
    PR_Number = Column(Text, nullable=False)
    SAP_Code = Column(Text, nullable=False)
    Requested_Qty = Column(Float, nullable=False)
    Site_ID = Column(Text, server_default=text("'HQ'"))
    status = Column(Text, server_default=text("'open'"))
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    Material_Code = Column(Text)
    Material_Name = Column(Text)
    workflow_state = Column(Text, server_default=text("'submitted'"))
    UOM = Column(Text)
    Supplier = Column(Text)
    Est_Cost_SAR = Column(Float)
    Notes = Column(Text)
    WBS_Number = Column(Text)
    Network = Column(Text)
    Plant = Column(Text)
    Delivery_Date = Column(Text)
    submitted_to_logistics_at = Column(DateTime)
    submitted_to_logistics_by = Column(Text)
    logistics_status = Column(Text, server_default=text("'site_draft'"))
    __table_args__ = (
        CheckConstraint("status IN ('open','closed')"),
    )

class PurchaseOrders(Base):
    __tablename__ = "purchase_orders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    PO_Number = Column(Text, nullable=False, unique=True)
    PR_Number = Column(Text)
    Site_ID = Column(Text)
    Vendor_Code = Column(Text)
    Vendor_Name = Column(Text)
    Inco_Terms = Column(Text)
    Payment_Terms = Column(Text)
    PO_Date = Column(Text)
    PO_Type = Column(Text)
    Quotation_No = Column(Text)
    Quotation_Date = Column(Text)
    Your_Reference = Column(Text)
    Our_Reference = Column(Text)
    Contact_Person = Column(Text)
    Contact_Email = Column(Text)
    Mobile = Column(Text)
    Our_Email = Column(Text)
    Expected_Delivery = Column(Text)
    Freight_Charges = Column(Float, server_default=text('0'))
    Handling_Charges = Column(Float, server_default=text('0'))
    Discount_Amount = Column(Float, server_default=text('0'))
    Total_Amount = Column(Float, server_default=text('0'))
    Amount_In_Words = Column(Text)
    source = Column(Text, server_default=text("'manual'"))
    attachment_blob = Column(LargeBinary)
    attachment_name = Column(Text)
    attachment_mime = Column(Text)
    status = Column(Text, server_default=text("'open'"))
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    closed_at = Column(DateTime)
    closed_by = Column(Text)
    close_reason = Column(Text)
    __table_args__ = (
        CheckConstraint("source IN ('manual','pdf_upload')"),
        CheckConstraint("status IN ('open','partially_delivered','delivered','closed','force_closed','cancelled')"),
    )

class SupervisorMaterialRequestItems(Base):
    __tablename__ = "supervisor_material_request_items"
    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(Integer, nullable=False)
    SAP_Code = Column(Text, nullable=False)
    Material_Code = Column(Text)
    Equipment_Description = Column(Text)
    UOM = Column(Text)
    Requested_Qty = Column(Float, nullable=False)
    Stock_At_Request = Column(Float)
    Available_Flag = Column(Integer)
    SK_Adjusted_Qty = Column(Float)
    Notes = Column(Text)
    line_status = Column(Text, server_default=text("'active'"))

class SupervisorMaterialRequests(Base):
    __tablename__ = "supervisor_material_requests"
    id = Column(Integer, primary_key=True, autoincrement=True)
    request_no = Column(Text, unique=True)
    Site_ID = Column(Text, nullable=False)
    Worker_ID = Column(Text, nullable=False)
    Worker_Name = Column(Text, nullable=False)
    Job_Tank_Place = Column(Text, nullable=False)
    Old_PPE_Returned = Column(Integer, nullable=False)
    No_Return_Reason = Column(Text)
    requested_by = Column(Text, nullable=False)
    requested_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    status = Column(Text, nullable=False, server_default=text("'pending_sk'"))
    sk_decided_by = Column(Text)
    sk_decided_at = Column(DateTime)
    sk_reject_reason = Column(Text)
    posted_pending_ids = Column(Text)
    __table_args__ = (
        CheckConstraint("status IN ('pending_sk','approved','rejected','cancelled')"),
    )

class Vendors(Base):
    __tablename__ = "vendors"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Vendor_Code = Column(Text, nullable=False, unique=True)
    Vendor_Name = Column(Text, nullable=False)
    Address = Column(Text)
    Contact_Name = Column(Text)
    Contact_Phone = Column(Text)
    Contact_Email = Column(Text)
    Default_Inco_Terms = Column(Text)
    Default_Payment_Terms = Column(Text)
    status = Column(Text, server_default=text("'active'"))
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        CheckConstraint("status IN ('active','inactive')"),
    )

class Warehouses(Base):
    __tablename__ = "warehouses"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Warehouse_ID = Column(Text, nullable=False, unique=True)
    Name = Column(Text, nullable=False)
    Location = Column(Text)
    Contact_Name = Column(Text)
    Contact_Phone = Column(Text)
    Contact_Email = Column(Text)
    status = Column(Text, server_default=text("'active'"))
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        CheckConstraint("status IN ('active','inactive')"),
    )


# ==========================================================================
# 5. Notifications / WhatsApp / reports
# ==========================================================================

class AppNotifications(Base):
    __tablename__ = "app_notifications"
    id = Column(Integer, primary_key=True, autoincrement=True)
    recipient_user = Column(Text)
    recipient_role = Column(Text)
    recipient_site = Column(Text)
    recipient_warehouse = Column(Text)
    event_key = Column(Text, nullable=False)
    severity = Column(Text, server_default=text("'info'"))
    title = Column(Text, nullable=False)
    body = Column(Text)
    link_page = Column(Text)
    link_anchor = Column(Text)
    related_table = Column(Text)
    related_ref = Column(Text)
    read_at = Column(DateTime)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        CheckConstraint("severity IN ('info','warning','critical','success')"),
    )

class DeliveryRemindersSent(Base):
    __tablename__ = "delivery_reminders_sent"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ref_type = Column(Text, nullable=False)
    ref_number = Column(Text, nullable=False)
    target_date = Column(Text, nullable=False)
    offset_days = Column(Integer, nullable=False)
    fired_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("ref_type", "ref_number", "target_date", "offset_days"),
    )

class ReportArchive(Base):
    __tablename__ = "report_archive"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)
    report_type = Column(Text, nullable=False)
    generated_by = Column(Text, nullable=False)
    generated_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    format = Column(Text, nullable=False)
    size_bytes = Column(Integer)
    file_path = Column(Text, nullable=False)
    site_id = Column(Text)
    date_from = Column(Text)
    date_to = Column(Text)

class ReportSchedules(Base):
    __tablename__ = "report_schedules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    label = Column(Text, nullable=False)
    report_type = Column(Text, nullable=False)
    frequency = Column(Text, nullable=False)
    recipients = Column(Text, nullable=False)
    format = Column(Text, server_default=text("'PDF'"))
    site_id = Column(Text)
    active = Column(Integer, server_default=text('1'))
    last_run = Column(DateTime)
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))

class ReturnableItems(Base):
    __tablename__ = "returnable_items"
    id = Column(Integer, primary_key=True, autoincrement=True)
    material_name = Column(Text, nullable=False)
    uom = Column(Text)
    qty = Column(Float)
    borrower_name = Column(Text)
    borrower_phone = Column(Text)
    given_time = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    expected_return_time = Column(DateTime)
    status = Column(Text, server_default=text("'borrowed'"))
    Site_ID = Column(Text, server_default=text("'HQ'"))
    whatsapp_alert_sent = Column(Integer, server_default=text('0'))
    cv_detected = Column(Integer, server_default=text('0'))
    cv_confidence = Column(Float)
    cv_employee_id = Column(Text)
    cv_tool_class = Column(Text)

class WhatsappQueue(Base):
    __tablename__ = "whatsapp_queue"
    id = Column(Integer, primary_key=True, autoincrement=True)
    phone_number = Column(Text, nullable=False)
    message = Column(Text, nullable=False)
    status = Column(Text, server_default=text("'pending'"))
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    sent_at = Column(DateTime)
    error_message = Column(Text)
    attempts = Column(Integer, server_default=text('0'))


# ==========================================================================
# 6. Lot tracking
# ==========================================================================

class LotTransfers(Base):
    __tablename__ = "lot_transfers"
    id = Column(Integer, primary_key=True, autoincrement=True)
    From_Lot = Column(Text, nullable=False)
    To_Lot = Column(Text, nullable=False)
    SAP_Code = Column(Text, nullable=False)
    Site_ID = Column(Text, server_default=text("'HQ'"))
    Qty = Column(Float, nullable=False)
    kind = Column(Text, server_default=text("'split'"))
    by_user = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        CheckConstraint("kind IN ('split','merge')"),
    )

class Lots(Base):
    __tablename__ = "lots"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Lot_Number = Column(Text, nullable=False)
    SAP_Code = Column(Text, nullable=False)
    Site_ID = Column(Text, server_default=text("'HQ'"))
    Received_Date = Column(Text, nullable=False)
    Expiry_Date = Column(Text)
    Supplier = Column(Text)
    PR_Number = Column(Text)
    Status = Column(Text, server_default=text("'open'"))
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("Lot_Number", "SAP_Code", "Site_ID"),
        CheckConstraint("Status IN ('open','exhausted','expired','disposed','quarantine')"),
    )


# ==========================================================================
# 7. Audit / meta
# ==========================================================================

class BugReports(Base):
    __tablename__ = "bug_reports"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(Text, nullable=False)
    type = Column(Text, nullable=False)
    page = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    status = Column(Text, server_default=text("'open'"))
    admin_response = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime)
    __table_args__ = (
        CheckConstraint("type IN ('bug','feature')"),
        CheckConstraint("status IN ('open','in_review','closed')"),
    )

class SystemAuditLog(Base):
    __tablename__ = "system_audit_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    username = Column(Text, nullable=False)
    action_type = Column(Text, nullable=False)
    target_table = Column(Text)
    details = Column(Text, nullable=False)


# ==========================================================================
# SQL VIEWS — recreate as PostgreSQL views at migration (NOT ORM tables).
# SME compat views alias sme_* tables (Canon rule 1); derived views compute
# live stock/lot balances. Order SME view reads by explicit PK, never rowid.
# ==========================================================================
SME_AND_DERIVED_VIEWS = {
    'consumption_log': (
        "CREATE VIEW consumption_log AS SELECT id, entry_date, Equipment_Tag_No AS equipment_tag, Lining_System_Code AS lining_system_code, SQM_Completed AS sqm_completed, Material_Code AS material_code, Expected_Qty AS expected_qty, Actual_Qty AS consumed_qty, Variance_Pct AS variance_pct, '' AS variance_status, '' AS material_name, '' AS uom, '' AS lining_system_name, committed_at AS submitted_at, Site_ID FROM sme_consumption_log WHERE status = 'committed'"
    ),
    'equipment': (
        'CREATE VIEW equipment AS SELECT id, Site_ID AS site_id, Equipment_Tag_No AS equipment_tag, Name AS name, Location AS location, Type AS type, Substrate AS substrate, Lining_System_Code AS lining_system_code, Lining_System_Short_Name AS lining_system_short_name, Lining_Type AS lining_type, Material_Spec AS "Material Spec.", Design AS design, Lining_System AS "Lining_System", Lining_Area_Location AS "Lining_Area/location", Sl_No AS "Sl. #", Project AS project, WBS_No AS "WBS #", IO_No AS "IO#", Sub_Location AS "Sub_Location", Drawing_No AS "Drawing #", Dia_L AS "Dia / L", Ht_W AS "Ht. /W", Equipment_Total_SQM AS "Equipment Total SQM", Remaraks AS remaraks, Lining_System AS lining_systems, Surface_Area_SQM AS surface_area_sqm FROM sme_equipment'
    ),
    'locations': (
        "CREATE VIEW locations AS SELECT value AS name, '#64748B' AS badge_color, MIN(id) AS sort_order, '' AS added_at FROM system_settings WHERE category = 'sme_location' GROUP BY value"
    ),
    'recipe': (
        'CREATE VIEW recipe AS SELECT id, Lining_System_Code AS lining_system_code, Lining_System_Name AS lining_system_short_name, Lining_Type AS lining_type, Lining_System AS lining_system, Substrate AS substrate, System_Keys AS system_keys, Lining_Thickness AS lining_thickness, Material_Code AS material_code, COALESCE(Material_Description, Material_Name) AS material_description, Material_Name AS material_name, For_1_SQM AS for_1_sqm, UOM AS uom, Nature AS nature, Package_Size AS package_size, Sl_No AS "Sl. #" FROM sme_recipe'
    ),
    'sme_materials_view': (
        "CREATE VIEW sme_materials_view AS SELECT s.Material_Code AS material_code, s.Material_Name AS material_name, s.Item AS item, s.Vendor AS vendor, s.Purchasing_Document AS purchasing_document, s.Document_Date AS document_date, s.Nature AS nature, s.UOM AS uom, s.Initial_Available_Qty AS initial_available_qty, s.Initial_Ordered_Qty AS initial_ordered_qty, COALESCE(( SELECT SUM(r.Quantity) FROM receipts r JOIN inventory i ON r.SAP_Code = i.SAP_Code WHERE TRIM(COALESCE(i.Material_Code,'')) = TRIM(s.Material_Code) ), 0) AS received_qty, COALESCE(( SELECT SUM(c.Quantity) FROM consumption c JOIN inventory i ON c.SAP_Code = i.SAP_Code WHERE TRIM(COALESCE(i.Material_Code,'')) = TRIM(s.Material_Code) ), 0) AS consumed_qty, (s.Initial_Available_Qty + COALESCE(( SELECT SUM(r.Quantity) FROM receipts r JOIN inventory i ON r.SAP_Code = i.SAP_Code WHERE TRIM(COALESCE(i.Material_Code,'')) = TRIM(s.Material_Code) ), 0) - COALESCE(( SELECT SUM(c.Quantity) FROM consumption c JOIN inventory i ON c.SAP_Code = i.SAP_Code WHERE TRIM(COALESCE(i.Material_Code,'')) = TRIM(s.Material_Code) ), 0) ) AS available_qty, s.Initial_Ordered_Qty AS ordered_qty FROM sme_inventory_seed s"
    ),
    'sqm_progress': (
        'CREATE VIEW sqm_progress AS SELECT Site_ID AS site_id, Equipment_Tag_No AS equipment_tag, Lining_System_Code AS lining_system_code, Original_SQM AS original_sqm, (COALESCE(Done_SQM,0) + COALESCE(Done_SQM_staged,0)) AS done_sqm FROM sme_sqm_progress'
    ),
    'types': (
        "CREATE VIEW types AS SELECT value AS name, MIN(id) AS sort_order, '' AS added_at FROM system_settings WHERE category = 'sme_equipment_type' GROUP BY value"
    ),
    'v_expiring_stock': (
        "CREATE VIEW v_expiring_stock AS SELECT TRIM(r.SAP_Code) AS SAP_Code, i.Equipment_Description AS Equipment_Description, i.UOM AS UOM, COALESCE(r.Site_ID, 'HQ') AS Site_ID, r.Quantity AS Quantity, r.Supplier AS Supplier, r.PR_Number AS PR_Number, r.Expiry_Date AS Expiry_Date, CAST(julianday(date(r.Expiry_Date)) - julianday(date('now')) AS INTEGER) AS Days_Until_Expiry, CASE WHEN julianday(date(r.Expiry_Date)) < julianday(date('now')) THEN 'Expired' WHEN julianday(date(r.Expiry_Date)) <= julianday(date('now','+30 days')) THEN 'Short-Dated' ELSE 'Good' END AS Expiry_Status FROM receipts r LEFT JOIN inventory i ON TRIM(i.SAP_Code) = TRIM(r.SAP_Code) WHERE r.Expiry_Date IS NOT NULL AND r.Expiry_Date != '' AND date(r.Expiry_Date) IS NOT NULL"
    ),
    'v_inventory_with_sme': (
        "CREATE VIEW v_inventory_with_sme AS SELECT i.*, CASE WHEN EXISTS ( SELECT 1 FROM sme_recipe r WHERE TRIM(r.Material_Code) = TRIM(COALESCE(i.Material_Code,'')) AND TRIM(COALESCE(i.Material_Code,'')) <> '' ) THEN 1 ELSE 0 END AS is_sme FROM inventory i"
    ),
    'v_live_stock': (
        'CREATE VIEW v_live_stock AS SELECT TRIM(i.SAP_Code) AS SAP_Code, i.Equipment_Description AS Equipment_Description, i.Material_Code AS Material_Code, i.UOM AS UOM, COALESCE(i.Minimum_Qty, 0) AS Minimum_Qty, COALESCE(r.Total_Received, 0) AS Total_Received, COALESCE(c.Total_Consumed, 0) AS Total_Consumed, COALESCE(rt.Total_Returned, 0) AS Total_Returned, COALESCE(r.Total_Received, 0) - COALESCE(c.Total_Consumed, 0) - COALESCE(rt.Total_Returned, 0) AS Current_Stock FROM inventory i LEFT JOIN ( SELECT TRIM(SAP_Code) AS SAP_Code, SUM(Quantity) AS Total_Received FROM receipts GROUP BY TRIM(SAP_Code) ) r ON r.SAP_Code = TRIM(i.SAP_Code) LEFT JOIN ( SELECT TRIM(SAP_Code) AS SAP_Code, SUM(Quantity) AS Total_Consumed FROM consumption GROUP BY TRIM(SAP_Code) ) c ON c.SAP_Code = TRIM(i.SAP_Code) LEFT JOIN ( SELECT TRIM(SAP_Code) AS SAP_Code, SUM(Quantity) AS Total_Returned FROM returns GROUP BY TRIM(SAP_Code) ) rt ON rt.SAP_Code = TRIM(i.SAP_Code)'
    ),
    'v_lot_balance': (
        "CREATE VIEW v_lot_balance AS SELECT l.Lot_Number, l.SAP_Code, l.Site_ID, l.Received_Date, l.Expiry_Date, l.Supplier, l.PR_Number, l.Status, COALESCE(( SELECT SUM(r.Quantity) FROM receipts r WHERE r.Lot_Number = l.Lot_Number AND r.SAP_Code = l.SAP_Code AND COALESCE(r.Site_ID,'HQ') = l.Site_ID ), 0) AS Received_Qty, COALESCE(( SELECT SUM(c.Quantity) FROM consumption c WHERE c.Lot_Number = l.Lot_Number AND c.SAP_Code = l.SAP_Code AND COALESCE(c.Site_ID,'HQ') = l.Site_ID ), 0) AS Consumed_Qty, COALESCE(( SELECT SUM(r.Quantity) FROM receipts r WHERE r.Lot_Number = l.Lot_Number AND r.SAP_Code = l.SAP_Code AND COALESCE(r.Site_ID,'HQ') = l.Site_ID ), 0) - COALESCE(( SELECT SUM(c.Quantity) FROM consumption c WHERE c.Lot_Number = l.Lot_Number AND c.SAP_Code = l.SAP_Code AND COALESCE(c.Site_ID,'HQ') = l.Site_ID ), 0) -- split/merge reclassification (within-SAP; nets to zero) - COALESCE(( SELECT SUM(t.Qty) FROM lot_transfers t WHERE t.From_Lot = l.Lot_Number AND t.SAP_Code = l.SAP_Code AND COALESCE(t.Site_ID,'HQ') = l.Site_ID ), 0) + COALESCE(( SELECT SUM(t.Qty) FROM lot_transfers t WHERE t.To_Lot = l.Lot_Number AND t.SAP_Code = l.SAP_Code AND COALESCE(t.Site_ID,'HQ') = l.Site_ID ), 0) AS Remaining_Qty FROM lots l"
    ),
    'v_mh_estimate_vs_actual': (
        'CREATE VIEW v_mh_estimate_vs_actual AS SELECT e.Site_ID AS Site_ID, e.Equipment_Tag AS Equipment_Tag, e.System_Code AS System_Code, e.Location AS Location, e.Estimated_Manhours AS Estimated_Manhours, COALESCE(a.Actual_Manhours, 0) AS Actual_Manhours, COALESCE(a.Actual_Manhours, 0) - e.Estimated_Manhours AS Variance_Manhours, CASE WHEN e.Estimated_Manhours > 0 THEN ROUND((COALESCE(a.Actual_Manhours, 0) - e.Estimated_Manhours) * 100.0 / e.Estimated_Manhours, 1) ELSE NULL END AS Variance_Pct, COALESCE(p.SQM_Done, 0) AS SQM_Done, n.Reason AS Variance_Reason FROM mh_manhour_estimates e LEFT JOIN ( SELECT Site_ID, Equipment_Tag, System_Code, SUM(Total_Hours) AS Actual_Manhours FROM mh_timesheets GROUP BY Site_ID, Equipment_Tag, System_Code ) a ON a.Site_ID = e.Site_ID AND a.Equipment_Tag = e.Equipment_Tag AND a.System_Code = e.System_Code LEFT JOIN ( SELECT Site_ID, Equipment_Tag, System_Code, SUM(SQM_Done) AS SQM_Done FROM mh_production GROUP BY Site_ID, Equipment_Tag, System_Code ) p ON p.Site_ID = e.Site_ID AND p.Equipment_Tag = e.Equipment_Tag AND p.System_Code = e.System_Code LEFT JOIN mh_variance_notes n ON n.Site_ID = e.Site_ID AND n.Equipment_Tag = e.Equipment_Tag AND n.System_Code = e.System_Code'
    ),
    'v_site_stock': (
        "CREATE VIEW v_site_stock AS WITH activity AS ( SELECT TRIM(SAP_Code) AS SAP_Code, COALESCE(Site_ID,'HQ') AS Site_ID, SUM(Quantity) AS rec, 0 AS con, 0 AS ret FROM receipts GROUP BY TRIM(SAP_Code), COALESCE(Site_ID,'HQ') UNION ALL SELECT TRIM(SAP_Code), COALESCE(Site_ID,'HQ'), 0, SUM(Quantity), 0 FROM consumption GROUP BY TRIM(SAP_Code), COALESCE(Site_ID,'HQ') UNION ALL SELECT TRIM(SAP_Code), COALESCE(Site_ID,'HQ'), 0, 0, SUM(Quantity) FROM returns GROUP BY TRIM(SAP_Code), COALESCE(Site_ID,'HQ') ) SELECT a.SAP_Code AS SAP_Code, a.Site_ID AS Site_ID, i.Equipment_Description AS Equipment_Description, i.Material_Code AS Material_Code, i.UOM AS UOM, COALESCE(i.Minimum_Qty, 0) AS Minimum_Qty, SUM(a.rec) AS Total_Received, SUM(a.con) AS Total_Consumed, SUM(a.ret) AS Total_Returned, SUM(a.rec) - SUM(a.con) - SUM(a.ret) AS Current_Stock FROM activity a LEFT JOIN inventory i ON TRIM(i.SAP_Code) = a.SAP_Code GROUP BY a.SAP_Code, a.Site_ID"
    ),
    'v_supplier_activity': (
        "CREATE VIEW v_supplier_activity AS SELECT TRIM(r.Supplier) AS Supplier, COALESCE(r.Site_ID, 'HQ') AS Site_ID, COUNT(*) AS Receipt_Count, COUNT(DISTINCT TRIM(r.SAP_Code)) AS Distinct_Items, SUM(r.Quantity) AS Total_Received, MIN(r.Date) AS First_Receipt_Date, MAX(r.Date) AS Last_Receipt_Date FROM receipts r WHERE r.Supplier IS NOT NULL AND TRIM(r.Supplier) != '' GROUP BY TRIM(r.Supplier), COALESCE(r.Site_ID, 'HQ')"
    ),
}
