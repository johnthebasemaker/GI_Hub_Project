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
    Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, Index,
    Integer, LargeBinary, Numeric, Text, UniqueConstraint, Uuid, text,
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
    # DB column is "WBS" (matches legacy SQLite exactly — a lowercase 'wbs'
    # here silently dropped the legacy data at migration time).
    wbs = Column("WBS", Text)
    status = Column(Text)          # legacy workflow flag — preserved, not used by v2
    Technician = Column(Text)      # legacy field — preserved, not used by v2
    Source_Ref = Column(Text)
    Requested_By = Column(Text)
    Approved_By = Column("Approved By", Text)
    # The workbook's `type` column (alembic b8d41f6a92c3) — which programme a
    # consumption belongs to: "Surface Shield", "R/L Consumables", "Safety"…
    # PERSISTED rather than re-derived from inventory.Category at read time,
    # so recategorising a material later cannot retroactively rewrite what
    # past consumption "was".
    Item_Type = Column(Text)
    # Hot-path indexes (alembic e7c3b95a41d2). Stock maths filters this
    # ledger by (SAP_Code, Site_ID) and every report windows it by Date;
    # with primary keys alone both were sequential scans. NON-UNIQUE by
    # rule — the same (date, SAP, quantity) line may legitimately repeat.
    __table_args__ = (
        Index("ix_consumption_sap_site", "SAP_Code", "Site_ID"),
        Index("ix_consumption_date", "Date"),
    )
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

class Employees(Base):
    """THE PERSON (QSEP ruling R1, 2026-08-09).

    `ID_Number` is globally unique, so one row is one human being no matter
    how many sites they have worked at. `mh_employees` is the per-site
    EMPLOYMENT RECORD — it is keyed on (Site_ID, Employee_Code), so a
    transfer necessarily creates a second row there, and anything hung off
    it forks on transfer. PPE, movements and badges therefore all key on
    `ID_Number` and nothing keys on `mh_employees.id`.
    """
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
    # alembic d2f84b19e57c — what the attendance workbook already supplies, so
    # the two registries stop disagreeing about the same person.
    Designation = Column(Text)
    Worker_Type = Column(Text)
    Company = Column(Text)

class EntryAttachments(Base):
    __tablename__ = "entry_attachments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    # NULLABLE since alembic e6a91c37b208. Every uploader used to be a site
    # Store Keeper; a PO scan is uploaded by LOGISTICS, who are unscoped by
    # design and raise POs across every site, so NOT NULL made storing one
    # impossible. NULL already reads correctly under the Document Library's
    # scoping — a scoped caller filters `Site_ID == <site>`, which a NULL
    # never matches (invisible, fail-closed), while the unscoped uploader
    # gets no filter and sees it.
    Site_ID = Column(Text)
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
    Sl_No = Column(Text)   # legacy serial label (293 live values — keep at cutover)

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
    # NULLABLE since alembic b4d17c8e93a2. A WAREHOUSE certificate has no site
    # — the goods have not been allocated to one yet — and the old NOT NULL
    # made the mandatory gate at warehouse goods-in unsatisfiable. Exactly one
    # of Site_ID / Warehouse_ID is set; entry.upload_mtc enforces that.
    Site_ID = Column(Text)
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
    # QSEP (alembic b4d17c8e93a2). An MTC used to be a site-side artefact
    # bolted to one staged receipt. It now also has to answer "does THIS
    # warehouse hold a certificate for THIS PO line", because the mandatory
    # gate moved to warehouse goods-in and DN creation.
    Warehouse_ID = Column(Text)
    Material_Code_Ref = Column(Text)   # dn_items carry Material_Code, not SAP
    po_item_id = Column(Integer)
    DN_Number = Column(Text)
    qc_inspection_id = Column(Integer)

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
    wbs = Column(Text)             # lowercase in legacy pending_issues (verified)
    Technician = Column(Text)      # legacy field — preserved, not used by v2
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
    Location = Column(Text)  # free-text for unscoped roles (T4)

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
    # normal | urgent (alembic e6a91c37b208). "Urgent delivery" is a
    # reschedule to an EARLIER date; the flag is what makes the dispatch
    # severity="critical", which bypasses the 16:00 evening digest. A request
    # to bring a delivery forward that lands after the working day it meant
    # to change is worthless.
    urgency = Column(Text, nullable=False, server_default=text("'normal'"))

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
    wbs = Column("WBS", Text)   # DB column is "WBS" in legacy SQLite (case fix)
    DN_Number = Column(Text)
    Warehouse_ID = Column(Text)
    PO_Number_Source = Column(Text)
    # alembic c7a93e5d2b18 — when the row entered the LEDGER, as opposed to
    # `Date`, which is the delivery date copied off the vendor's paperwork.
    # The return form's "last 30 days" rule means the former and had been
    # measuring the latter, so goods received this morning against a document
    # dated six weeks ago were invisible on the return form.
    #
    # ⚠️ NULL on every row that predates the migration, deliberately — see the
    # revision docstring. A backfilled CURRENT_TIMESTAMP would have claimed all
    # 632 historical receipts were posted on migration day.
    posted_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    # Hot-path indexes (alembic e7c3b95a41d2). Stock maths filters this
    # ledger by (SAP_Code, Site_ID) and every report windows it by Date;
    # with primary keys alone both were sequential scans. NON-UNIQUE by
    # rule — the same (date, SAP, quantity) line may legitimately repeat.
    __table_args__ = (
        Index("ix_receipts_sap_site", "SAP_Code", "Site_ID"),
        Index("ix_receipts_date", "Date"),
    )
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
    Technician = Column(Text)      # legacy field — preserved, not used by v2

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
    # Hot-path indexes (alembic e7c3b95a41d2). Stock maths filters this
    # ledger by (SAP_Code, Site_ID) and every report windows it by Date;
    # with primary keys alone both were sequential scans. NON-UNIQUE by
    # rule — the same (date, SAP, quantity) line may legitimately repeat.
    __table_args__ = (
        Index("ix_returns_sap_site", "SAP_Code", "Site_ID"),
        Index("ix_returns_date", "Date"),
    )
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
    # Per-recipient address (alembic a71e93b4c2f8), for the weekly Executive
    # Summary. Nullable and deliberately NOT unique — a shift account or a
    # departmental mailbox is legitimately shared, and a UNIQUE would reject
    # the second user for no benefit. Until it is filled in, weekly_report
    # falls back to the configured inbox.
    email = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    totp_secret = Column(Text)
    totp_enabled = Column(Integer, server_default=text('0'))
    Location = Column(Text)  # free-text for unscoped roles (T4)

class AuthSessions(Base):
    """LEGACY refresh-token sessions (superseded by RefreshSessions/RTR —
    kept only so pre-RTR rows stay auditable until they expire; no code
    writes here any more). NEW-STACK ONLY — no SQLite counterpart."""
    __tablename__ = "auth_sessions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(Text, nullable=False, index=True)
    refresh_hash = Column(Text, nullable=False, unique=True)  # sha256 hex, never the raw token
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime)
    revoke_reason = Column(Text)  # rotated | logout | reuse-detected | admin-reset | user-deleted
    replaced_by = Column(Integer)  # successor session id (rotation chain)

class RefreshSessions(Base):
    """Refresh Token Rotation (RTR) sessions (NEW-STACK ONLY — no SQLite
    counterpart; dual_ci leaves it empty on reset → local re-login).
    One row per refresh token; a login opens a new *family* and every
    rotation appends a row to it (same family_id, new jti). Replaying a
    revoked token is theft evidence → the WHOLE family is revoked, but other
    families (the user's other devices) survive. client_type drives the TTL:
    'web' 7 days, 'native' (Tauri/Capacitor) 90 days.
    ⚠️ SECURITY: listed in the gi_ai_ro REVOKE set
    (backend/scripts/create_ai_readonly_role.sql) — never expose to the AI
    read-only role."""
    __tablename__ = "refresh_sessions"
    id = Column(Uuid, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    username = Column(Text, nullable=False, index=True)  # denormalized: cheap revoke-by-name
    family_id = Column(Uuid, nullable=False, index=True)  # one login = one family
    refresh_token_jti = Column(Text, nullable=False, unique=True)  # jti claim of the JWT
    client_type = Column(Text, nullable=False, server_default=text("'web'"))  # web | native
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    expires_at = Column(DateTime, nullable=False)
    is_revoked = Column(Boolean, nullable=False, server_default=text('FALSE'))
    revoked_at = Column(DateTime)
    revoke_reason = Column(Text)  # rotated | logout | reuse-detected | admin-revoked | user-deleted | expired
    replaced_by = Column(Uuid)   # successor row id (rotation chain)

class SlaDismissals(Base):
    """Admin 'Clear' actions on the Overdue Actions view (NEW-STACK ONLY — no
    SQLite counterpart; dual_ci leaves it empty on reset, same contract as
    auth_sessions/ai_jobs). One row hides one (kind, ref_id) pending item from
    the >24h SLA tracker; UNIQUE so a double-clear is a clean 409."""
    __tablename__ = "sla_dismissals"
    __table_args__ = (UniqueConstraint("kind", "ref_id", name="uq_sla_dismissals_kind_ref"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(Text, nullable=False)      # queue key (hod-issue, sk-request, …)
    ref_id = Column(Text, nullable=False)    # queue row id / PR_Number
    cleared_by = Column(Text, nullable=False)
    cleared_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))


class AiJob(Base):
    """Async AI job queue (NEW-STACK ONLY — no SQLite counterpart; dual_ci
    leaves it empty on reset, same contract as auth_sessions). Backs the
    long-running vision-OCR flow: POST /ai/jobs inserts a row + spawns an
    in-process worker; React polls GET /ai/jobs/{id}. The queued→running
    transition is an atomic claim UPDATE (multi-worker safe, same discipline
    as the report scheduler); orphaned 'running' rows are failed on startup."""
    __tablename__ = "ai_jobs"
    # ⚠️ DECLARED HERE AS WELL AS IN ALEMBIC. `tools/migration/cutover_migrate.py`
    # builds production from `metadata.create_all`, so an index that lives only
    # in a revision does not exist on a freshly cut database (ARCHITECTURE §3).
    #
    # Two scans this closes, both on a table whose rows carry base64 images:
    #   · the startup orphan sweep, `WHERE status IN ('queued','running')`;
    #   · the submission-summary cache, `WHERE kind = … AND status = 'done'
    #     AND created_at >= …`, which ran on every review-screen open.
    __table_args__ = (
        Index("ix_ai_jobs_kind_status_created", "kind", "status", "created_at"),
        Index("ix_ai_jobs_status", "status"),
        # The orphan sweep's own predicate. It reads
        # `COALESCE(heartbeat_at, started_at, created_at) < cutoff` over
        # unfinished rows, which is the only query that touches heartbeat_at.
        Index("ix_ai_jobs_heartbeat", "status", "heartbeat_at"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(Text, nullable=False)          # ocr_consumption | ocr_delivery_note
    status = Column(Text, nullable=False, server_default=text("'queued'"))
    actor = Column(Text, nullable=False, index=True)  # username (owner-only polling)
    Site_ID = Column(Text)
    payload_json = Column(Text)                  # prepped-image b64 + input metadata
    result_json = Column(Text)                   # parsed + fuzzy-resolved rows
    error = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    # ⚠️ WHO OWNS THIS JOB, AND IS IT STILL ALIVE (2026-09-02).
    #
    # The startup sweep used to fail EVERY queued/running row on the theory
    # that a job in flight belonged to the process that just died. That is true
    # of a single-process server and false of `uvicorn --workers 4`: when one
    # worker crashed and was respawned, its lifespan swept away the in-flight
    # OCR jobs of the three workers that were still running them — a supervisor
    # five minutes into a six-minute form read was told "server restarted while
    # this job was in flight" by a server that had not restarted.
    #
    # `worker_id` records which process claimed the row; `heartbeat_at` is
    # touched every 30 s for as long as that process is still working on it. A
    # sweep can then distinguish a job whose owner is gone (no beat for minutes)
    # from one that is simply slow, which is the distinction the old predicate
    # did not have available to it.
    worker_id = Column(Text)
    heartbeat_at = Column(DateTime)


class WhatsappOutbox(Base):
    """WhatsApp Cloud API outbound queue (NEW-STACK ONLY — no SQLite counterpart;
    dual_ci leaves it empty on reset, same contract as auth_sessions/ai_jobs).
    Every outbound message is logged here with its rendered Meta payload and a
    delivery status (pending → sent | failed); the admin WhatsApp Console lists
    it and retries failures. Phase 7 native v2 replacement for the legacy
    SQLite whatsapp_worker."""
    __tablename__ = "whatsapp_outbox"
    id = Column(Integer, primary_key=True, autoincrement=True)
    to_number = Column(Text)
    message_type = Column(Text, server_default=text("'text'"))  # text | document
    body = Column(Text)                     # human-readable preview / caption
    payload_json = Column(Text)             # the exact Meta Graph payload sent
    status = Column(Text, nullable=False, server_default=text("'pending'"), index=True)
    meta_message_id = Column(Text)          # wamid returned by the Cloud API
    error = Column(Text)
    event_key = Column(Text)                # xsite_escalation | fefo_override | report_delivery
    related_table = Column(Text)
    related_ref = Column(Text)
    attempts = Column(Integer, server_default=text('0'))
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    sent_at = Column(DateTime)
    updated_at = Column(DateTime)


class EmailOutbox(Base):
    """SMTP outbound queue (NEW-STACK ONLY — no SQLite counterpart; dual_ci
    leaves it empty on reset, same contract as whatsapp_outbox/ai_jobs).
    Every outbound email is logged with a delivery status (pending → sent |
    failed); the admin Email Console lists it and retries failures. Phase 7b —
    native v2 replacement for the legacy SQLite mailer, not a port of it."""
    __tablename__ = "email_outbox"
    id = Column(Integer, primary_key=True, autoincrement=True)
    to_email = Column(Text)
    cc = Column(Text)
    subject = Column(Text)
    body = Column(Text)
    status = Column(Text, nullable=False, server_default=text("'pending'"), index=True)
    error = Column(Text)
    event_key = Column(Text)                # mtc_missing | vendor_return | …
    related_table = Column(Text)
    related_ref = Column(Text)
    attempts = Column(Integer, server_default=text('0'))
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    sent_at = Column(DateTime)
    updated_at = Column(DateTime)


class PhoneOtp(Base):
    """One-time codes for self-service phone-number changes (NEW-STACK ONLY;
    dual_ci leaves it empty, same contract as whatsapp_outbox/email_outbox).
    The code is hashed at rest (bcrypt), expires in ~10 min and is single-use.
    Dual-OTP flow: stage='old' authorizes the change from the currently
    registered device; stage='new' then proves the NEW number can receive
    WhatsApp (typo lock-out guard) — users.Phone_Number only changes after the
    stage='new' code verifies. First-time setup (no number on file) skips the
    'old' stage. Admins bypass this entirely via PATCH /admin/users/{username}."""
    __tablename__ = "phone_otp"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(Text, nullable=False, index=True)
    new_number = Column(Text, nullable=False)
    code_hash = Column(Text, nullable=False)
    stage = Column(Text, nullable=False, server_default=text("'new'"))
    expires_at = Column(DateTime, nullable=False)
    consumed_at = Column(DateTime)
    attempts = Column(Integer, server_default=text('0'))
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))


class PendingSummaryNotifications(Base):
    """Staged WhatsApp events awaiting the 16:00 evening digest (NEW-STACK
    ONLY; dual_ci leaves it empty, same contract as phone_otp). One row per
    (recipient, event); the batch aggregator groups by recipient_user, compiles
    a single bulleted digest per person via the gi_evening_summary template,
    and stamps processed_at + digest_outbox_id only after a successful send
    (failed sends stay pending and retry on the next run)."""
    __tablename__ = "pending_summary_notifications"
    id = Column(Integer, primary_key=True, autoincrement=True)
    recipient_user = Column(Text, nullable=False, index=True)
    event_key = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    body = Column(Text)
    related_table = Column(Text)
    related_ref = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    processed_at = Column(DateTime)
    digest_outbox_id = Column(Integer)


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
    )


class WbsWorkTypeMap(Base):
    """A work type, and the WBS number it charges to at this site.

    ⚠️ `Work_Type_Norm` is the identity — see the migration for why. Four of
    the 35 spellings in the live ledger differ from another only in case, and
    keyed on raw text those would take different WBS numbers silently.
    `Work_Type` is only how the identity is spelled back to a human, and it is
    what lands on the ledger row.
    """
    __tablename__ = "wbs_work_type_map"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Site_ID = Column(Text, nullable=False)
    Work_Type = Column(Text, nullable=False)
    Work_Type_Norm = Column(Text, nullable=False)
    WBS_Number = Column(Text)          # NULL = a work type with no WBS yet
    Description = Column(Text)
    status = Column(Text, nullable=False, server_default=text("'active'"))
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("Site_ID", "Work_Type_Norm",
                         name="uq_wbs_work_type_site_norm"),
        Index("ix_wbs_work_type_active", "Site_ID", "status"),
    )


# ==========================================================================
# 2. SME sub-module (feature-frozen — strict isolation)
# ==========================================================================

class SmeTankAlias(Base):
    """Workbook `Tank No.` → `sme_equipment.Equipment_Tag_No` (alembic
    b8d41f6a92c3).

    The Consumption Log's Tank No. cannot be matched automatically: `TNK-091`
    (39 Surface-Shield rows, the largest bucket) suffix-matches BOTH
    `522-8J10-TNK-091` (TRAIN J) and `522-8k10-TNK-091` (TRAIN K). The sync
    auto-maps only aliases whose normalised form matches EXACTLY ONE tag and
    parks the rest as `unresolved` for an operator — see the tank-alias screen.
    """
    __tablename__ = "sme_tank_alias"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Site_ID = Column(Text, nullable=False)
    alias_raw = Column(Text, nullable=False)    # verbatim, as the workbook typed it
    alias_norm = Column(Text, nullable=False)   # upper, spaces/hyphens/underscores gone
    Equipment_Tag_No = Column(Text)
    status = Column(Text, nullable=False, server_default=text("'unresolved'"))
    match_count = Column(Integer, nullable=False, server_default=text('0'))
    row_count = Column(Integer, nullable=False, server_default=text('0'))
    resolved_by = Column(Text)
    resolved_at = Column(DateTime)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("Site_ID", "alias_norm", name="uq_sme_tank_alias_site_norm"),
    )


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
    # THE APP WINS (alembic c1a72e5b83d9). An operator SQM correction made in
    # the UI survives both the ordinary workbook upsert and `--sme-reseed`;
    # every sync re-applies it and REPORTS the divergence instead of silently
    # reverting it. NULL = the workbook owns this row.
    SQM_Override = Column(Float)
    SQM_Override_By = Column(Text)
    SQM_Override_At = Column(DateTime)
    Lining_Area_Location = Column(Text)
    __table_args__ = (
        UniqueConstraint("Site_ID", "Equipment_Tag_No", "Lining_System_Code"),
    )

class SmeInventorySeed(Base):
    __tablename__ = "sme_inventory_seed"
    # 2026-07-30 COMPONENT IDENTITY: (Material_Code, SAP_Code) — one row per
    # PHYSICAL component. A multi-part system lists one Material_Code as several
    # distinct drums (GI-8005765 → Comp-A/B/C/D at SAPs 1041 / 1041-1 / -2 / -3)
    # and each holds its own stock. Keying on Material_Code alone summed four
    # unlike things into one bucket and wrecked the bottleneck ratio. SAP_Code
    # is whitespace-normalized on write (the ERP writes "1043 - 2").
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
    # The ONE variant SAP this component is. Part of the primary key as of the
    # 2026-07-30 ruling — it used to hold a comma-joined list of every SAP the
    # Material_Code spanned ("1041, 1041-1, 1041-2, 1041-3"), which is exactly
    # what collapsed four physical components into one stock row.
    # Whitespace-normalized on write (the ERP writes "1043 - 2" for "1043-2").
    SAP_Code = Column(Text, primary_key=True, nullable=False,
                      server_default=text("''"))
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
    # Exact ERP inventory item for this line. PU systems list one material
    # (e.g. GI-8005765) as four Comp-A/B/C/D lines that only the variant SAP
    # (1041 / 1041-1 / -2 / -3) distinguishes — hence part of the identity.
    SAP_Code = Column(Text)
    # Which execution sub-activity this line's quantity belongs to (ESC21 =
    # primer coat, ESC22 = screed coat …), from the 2026-08 For_1_SQM workbook.
    #
    # ⚠️ IT IS PART OF THE IDENTITY, and adding it SPLIT a number that used to
    # be merged. LSC2/GI-6002243/1049 appears under BOTH ESC21 (primer, 0.2700)
    # and ESC22 (screed, 1.4674); the old three-part key could not tell them
    # apart, so `plan_sme_recipes` summed them into one 1.7374 "coat merge".
    # That was right while a system was consumed as a whole and is wrong the
    # moment a supervisor reports against ONE sub-activity — a correct primer
    # draw compared against 1.7374 reads as 15.5 % of benchmark.
    #
    # '' means "not yet classified by a workbook sync" — a real sentinel, not
    # NULL, because Postgres treats NULLs as distinct and the unique constraint
    # below would stop constraining anything.
    Execution_Sub_Activity_Code = Column(Text, nullable=False,
                                         server_default=text("''"))
    __table_args__ = (
        UniqueConstraint("Lining_System_Code", "Execution_Sub_Activity_Code",
                         "Material_Code", "SAP_Code"),
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
    # 2026-08-18 Phase 7: the vocabulary is GI | NON_GI. It was OWN | Supply —
    # the same distinction under the attendance workbook's names — and the
    # rename is what makes the OT rule below readable, because the two words
    # now say WHY the thresholds differ rather than where the person is paid
    # from. Migrated by alembic (OWN→GI, Supply→NON_GI).
    Worker_Type = Column(Text, nullable=False, server_default=text("'GI'"))
    # Day | Night. The shift is 12 physical hours either way (11 worked + 1
    # lunch); this records WHICH one, not how long it is.
    Shift = Column(Text, nullable=False, server_default=text("'Day'"))
    Company = Column(Text)
    linked_id_number = Column(Text)
    status = Column(Text, nullable=False, server_default=text("'active'"))
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("Site_ID", "Employee_Code"),
    )


class MhRoles(Base):
    """The role / designation master — the dropdown behind every crew figure.

    Seeded from the nine role COLUMNS of Manpower_Hour_Details.xlsx (Blaster,
    Potman, Rubber Liner, …) and extendable by an HOD, which is the whole
    reason it is a table and not an enum: the workbook's nine are what the
    benchmarks are expressed in, but a site hires roles the workbook never
    anticipated and must not need a migration to record one.
    """
    __tablename__ = "mh_roles"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Role_Code = Column(Text, nullable=False)      # canonical, e.g. 'MASON'
    Name = Column(Text, nullable=False)           # as printed, e.g. 'Mason'
    # 'workbook' rows are recreated by the importer and must not be renamed by
    # hand; 'custom' rows are the HOD's and the importer never touches them.
    Source = Column(Text, nullable=False, server_default=text("'custom'"))
    Sort_Order = Column(Integer, nullable=False, server_default=text('0'))
    status = Column(Text, nullable=False, server_default=text("'active'"))
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("Role_Code"),
    )


class SmeManpowerNorm(Base):
    """One productivity benchmark from Manpower_Hour_Details.xlsx (Block A).

    ⚠️ THE KEY IS FIVE PARTS, and each part earns its place against the real
    workbook:

      · Type (CV/ME) separates LSC4/ESC41 and LSC5/ESC51, which appear once for
        civil and once for mechanical;
      · Activity separates LSC10/ESC101, the PU seal coat, which is one code
        serving BOTH the 4 mm and 6 mm systems at 70 and 90 m²/shift;
      · Variant_Key separates what nothing else can — CV blasting is filed
        under ESC1 twice, at 300 m²/shift with a crew of 4 and at 40 m²/shift
        with a crew of 2, and no other column in the row differs.

    Without all five the importer would silently keep whichever row it read
    last, and a blasting crew would be planned against a benchmark 7.5× wrong.

    ⚠️ `Lining_System_Code` is NOT always an LSC code. Blasting rows carry
    'ESC1'/'ESC2' in that column because blasting prepares a surface and
    belongs to no lining system. Those are the manpower-ONLY activities that a
    supervisor opens without a store keeper (Phase 5) — they consume no Surface
    Shield, so a material benchmark for them does not exist and its absence is
    not a data error.
    """
    __tablename__ = "sme_manpower_norm"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Activity_Code = Column(Text)                  # the workbook's 'Activity Code#'
    Type = Column(Text, nullable=False)           # CV | ME
    System = Column(Text)                         # 'Cold Bonding' | 'None'
    Lining_System_Code = Column(Text, nullable=False)
    Execution_Sub_Activity_Code = Column(Text, nullable=False)
    Activity = Column(Text, nullable=False)
    Sub_Activity = Column(Text)
    Variant_Key = Column(Text, nullable=False, server_default=text("''"))
    Crew_Size = Column(Float, nullable=False, server_default=text('0'))
    # Read from the sheet, never assumed: the workbook ships 11 for every row
    # except Buffing, which is 12. Hard-coding either turns the operator's
    # correction into a code change.
    Hours_Per_Shift = Column(Float, nullable=False, server_default=text('0'))
    Manhours_Per_Shift = Column(Float, nullable=False, server_default=text('0'))
    Standard_Productivity_Per_Shift = Column(Float, nullable=False,
                                             server_default=text('0'))
    SQM_Per_Hour_Per_Person = Column(Float, nullable=False, server_default=text('0'))
    Remarks = Column(Text)
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("Type", "Lining_System_Code", "Execution_Sub_Activity_Code",
                         "Activity", "Variant_Key"),
    )


class SmeConsumptionForm(Base):
    """A printed consumption form, registered at the moment it was generated.

    ⚠️ `Recipe_Fingerprint` records what was ON THE PAPER, not what the recipe
    says now. Paper outlives the recipe it was printed from: a sheet in
    somebody's pocket still lists row 3 as whatever it listed last week, and
    resolving the recipe again at upload would show today's materials against
    yesterday's handwriting and call it agreement. Slice 9d compares the two
    and refuses a stale sheet.
    """
    __tablename__ = "sme_consumption_form"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Form_UUID = Column(Text, nullable=False)
    Site_ID = Column(Text, nullable=False)
    Lining_System_Code = Column(Text, nullable=False)
    # '' = every sub-activity of the system, matching sme_execution_entry.
    Execution_Sub_Activity_Code = Column(Text, nullable=False,
                                         server_default=text("''"))
    Recipe_Fingerprint = Column(Text, nullable=False)
    Row_Count = Column(Integer, nullable=False, server_default=text("0"))
    status = Column(Text, nullable=False, server_default=text("'open'"))
    consumed_entry_id = Column(Integer)
    consumed_at = Column(DateTime)
    created_by = Column(Text)
    created_by_role = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))

    # ── Phase 13a: one print run of many forms ──────────────────────────────
    # ⚠️ A BATCH IS NOT A FORM. Fifty forms printed in one go are fifty rows
    # here, fifty `Form_UUID`s and fifty QR codes — because that is the whole
    # point of the feature: a photocopied form duplicates its QR, and slice 9d
    # maps handwriting POSITIONALLY off a sheet identity it has to be able to
    # tell apart. What `Batch_UUID` adds is the ability to answer "where did
    # the fifty sheets I printed on Tuesday go", which fifty unrelated rows
    # cannot.
    #
    # Every row WRITTEN FROM PHASE 13 ON gets all three, including a single
    # download — a lone form is a batch of one. The alternative is a nullable
    # special case that every reader has to COALESCE around, and the first
    # reader to forget reports a single print as an orphan.
    #
    # ⚠️ `Batch_UUID` IS STILL NULL ON EVERY PRE-PHASE-13 ROW, and it is left
    # that way on purpose. Those forms were printed one at a time and there is
    # no batch they belonged to; back-filling a synthetic one would let a
    # report claim a print run that never happened. `Batch_Seq`/`Batch_Size`
    # DO back-fill to 1, because "a batch of one" is exactly what they were.
    Batch_UUID = Column(Text)
    Batch_Seq = Column(Integer, nullable=False, server_default=text("1"))
    Batch_Size = Column(Integer, nullable=False, server_default=text("1"))

    # ── Phase 13b: which PAGES of this form have been photographed ──────────
    # ⚠️ DO NOT CONFUSE THIS WITH `Batch_Seq`. `Batch_Seq` is which FORM of a
    # print run this is (sheet 7 of 50 forms); `Sheets_Seen` is which A4 PAGES
    # of THIS form have come back in (a 36-material recipe prints on two, and
    # they are photographed separately).
    #
    # A comma-separated list of printed page numbers rather than a count,
    # because "sheets 1 and 3 are in, 2 is missing" is what a supervisor
    # standing in a plant needs told. A count of 2 out of 3 does not say which
    # page to go and photograph.
    Sheets_Seen = Column(Text)
    __table_args__ = (
        UniqueConstraint("Form_UUID", name="uq_consumption_form_uuid"),
        Index("ix_consumption_form_site_status", "Site_ID", "status"),
        # The registry's own question: show me that print run, in sheet order.
        Index("ix_consumption_form_batch", "Batch_UUID", "Batch_Seq"),
    )


class SmeExecutionEntry(Base):
    """One execution report: what was consumed, what area was done, by whom.

    THE STATE MACHINE (Phase 9d — paper first; the Phase 5 one is drained):

        DRAFT_SUPERVISOR ─→ PENDING_SK ─→ PENDING_HOD ─→ APPROVED
                                                      └─→ REJECTED (terminal)

    ⚠️ THE DIRECTION REVERSED, AND SO DID WHO MAY EDIT A MATERIAL LINE. Phase 5
    forbade the supervisor from touching one because the store keeper had
    counted it. The paper originates in the field now, so the supervisor
    AUTHORS the numbers and the store keeper VERIFIES them — see the four-layer
    trail on SmeExecutionEntryMaterial for what replaces the old control.

    The store keeper records the physical material draw and the equipment. The
    supervisor names the sub-activity and reports the actual area and crew. The
    HOD approves — and approval is what deducts stock, so nothing before it
    moves a quantity.

    ⚠️ THE BYPASS IS NOT AN EXCEPTION, IT IS A SECOND FRONT DOOR. Blasting and
    buffing consume no Surface Shield, so there is no material for a store
    keeper to record and no draft for them to raise. A supervisor opens those
    entries directly at PENDING_SUPERVISOR. Modelling that as "an SK draft with
    zero materials" would put a signature on a step nobody performed.

    ⚠️ `Lining_System_Code` CAN BE '' — surface prep belongs to no lining
    system. It is the empty string and NOT NULL, matching the ruling already
    taken for `sme_recipe.Execution_Sub_Activity_Code`: Postgres treats NULLs
    as distinct, so a nullable column in a key stops the key constraining, and
    every GROUP BY grows an untyped bucket. One sentinel convention across two
    adjacent columns beats two conventions.

    ⚠️ THE BENCHMARK IS SNAPSHOTTED, NOT JOINED. Every Bench_* column is copied
    from `sme_manpower_norm` at the moment the supervisor submits. Master data
    is editable by an HOD, and a variance report that re-derives its benchmark
    would silently rewrite history the first time somebody corrects a
    productivity figure — last quarter's 12% overrun becoming 4% with no edit
    to the entry and nothing to point at. `Norm_ID` records WHICH benchmark was
    used; the Bench_* columns record what it SAID.
    """
    __tablename__ = "sme_execution_entry"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Site_ID = Column(Text, nullable=False)
    Entry_No = Column(Text, nullable=False)
    Work_Date = Column(Text, nullable=False)
    Equipment_Tag_No = Column(Text, nullable=False)
    # '' = system-agnostic (surface prep). See the class docstring.
    Lining_System_Code = Column(Text, nullable=False, server_default=text("''"))
    Execution_Sub_Activity_Code = Column(Text, nullable=False)
    Variant_Key = Column(Text, nullable=False, server_default=text("''"))
    status = Column(Text, nullable=False, server_default=text("'DRAFT_SK'"))

    # — store keeper —
    sk_username = Column(Text)
    sk_submitted_at = Column(DateTime)

    # — supervisor —
    Actual_SQM = Column(Float)
    supervisor_username = Column(Text)
    supervisor_submitted_at = Column(DateTime)
    # ALWAYS required at submission, whatever the variance — the operator's
    # ruling. A reason demanded only past a threshold trains people to aim just
    # under it, and a zero-variance entry with a stated reason is evidence the
    # supervisor looked.
    Material_Variance_Reason = Column(Text)
    Manpower_Variance_Reason = Column(Text)

    # — benchmark snapshot (taken at supervisor submission) —
    Norm_ID = Column(Integer, ForeignKey("sme_manpower_norm.id",
                                         ondelete="SET NULL"))
    Bench_Crew_Size = Column(Float)
    Bench_Hours_Per_Shift = Column(Float)
    Bench_Manhours_Per_Shift = Column(Float)
    Bench_Productivity_Per_Shift = Column(Float)
    Bench_SQM_Per_Hour_Per_Person = Column(Float)
    Bench_Snapshot_At = Column(DateTime)

    # — HOD —
    hod_username = Column(Text)
    hod_decided_at = Column(DateTime)
    # Mandatory the moment an HOD changes any supervisor or store-keeper
    # number. An approval that silently rewrote the figures would leave the
    # supervisor answering for numbers they never entered.
    HOD_Edit_Justification = Column(Text)
    hod_edited = Column(Boolean, nullable=False, server_default=text("false"))
    Reject_Reason = Column(Text)

    # ── Phase 9d: where this entry came from ────────────────────────────────
    # Which printed sheet, which photo, and what the model actually said. The
    # raw JSON is kept because "what did the machine read" is only answerable
    # six weeks later if somebody wrote it down at the time.
    Form_UUID = Column(Text)
    OCR_Job_ID = Column(Integer)
    OCR_Image = Column(LargeBinary)
    OCR_Image_Mime = Column(Text)
    OCR_Raw_JSON = Column(Text)
    OCR_Confidence = Column(Float)
    OCR_Model = Column(Text)
    # ⚠️ 'ocr' | 'manual' | 'legacy'. A hand-typed entry must never be mistaken
    # for a scanned one: the grey OCR layer is empty for both, and only this
    # says whether that means "the model read nothing" or "there was no model".
    Entry_Origin = Column(Text, nullable=False, server_default=text("'manual'"))

    # ── the SK's verification step (the new middle of the chain) ────────────
    sk_verified_at = Column(DateTime)
    sk_edited = Column(Boolean, nullable=False, server_default=text('false'))
    SK_Edit_Reason = Column(Text)

    # ── the QSEP override (ruling Q2-D) ─────────────────────────────────────
    # On a paper-first flow the drum was emptied days ago, so a hard block can
    # only strand the record. It blocks by default and an HOD may override, at
    # the price of a written reason and a notification to the Head of Qualities.
    QSEP_Override = Column(Boolean, nullable=False, server_default=text('false'))
    QSEP_Override_Reason = Column(Text)
    QSEP_Override_By = Column(Text)
    QSEP_Override_At = Column(DateTime)

    # ⚠️ APPROVAL NOW MOVES STOCK (ruling Q1-b) — see post_stock().
    Stock_Posted_At = Column(DateTime)
    WBS_Number = Column(Text)
    # ⚠️ 'Day' | 'Night' | NULL, and NULL is not a defect (slice 10b).
    #
    # Track 2's chase alert asks about material staged for DAY-SHIFT work, and
    # nothing recorded which shift an entry belonged to. Inferring it from the
    # clock was rejected: an entry is filed when somebody reaches a desk, not
    # when the work happened, so a night crew filing at 06:40 would be counted
    # as day shift on a timestamp nobody looked at.
    #
    # Every pre-existing row is NULL and there is no honest backfill — 'Day'
    # would be a guess printed into the record. The probe reads NULL as "not
    # known to be day shift" and skips it, so the feature starts empty and
    # fills as people use it, exactly as `WBS_Number` above did in Phase 9a.
    Shift = Column(Text)

    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("Site_ID", "Entry_No"),
        Index("ix_sme_exec_entry_status", "Site_ID", "status"),
        Index("ix_execution_entry_form", "Form_UUID"),
        # The day-shift MTC probe's predicate. Without it, that probe is a
        # sequential scan of every execution entry ever filed — every morning,
        # in every worker.
        Index("ix_exec_entry_shift", "Site_ID", "Work_Date", "Shift"),
    )


class SmeSurfacePrepProgress(Base):
    """Surface-prep area done, tracked SEPARATELY from lining progress.

    ⚠️ THIS TABLE EXISTS SO THAT BLASTING CANNOT INFLATE COMPLETION. Blasting
    100 m² of a tank is not 100 m² of lining done — the surface is merely ready
    to be lined. Folding it into `sme_sqm_progress.Done_SQM` would report a
    vessel as part-lined the moment it was cleaned, and every downstream
    figure (Completion_Pct, SQM_Achievable_Now, the buy list) reads that column.

    Keyed on the SUB-ACTIVITY, not a lining system, because that is exactly
    what surface prep has none of. One tag can carry several prep activities
    (blasting, then buffing) and each is tracked on its own line.

    There is deliberately no `Original_SQM` twin here. Prep has no planned area
    of its own — the area that needs preparing is the equipment's, which
    `sme_equipment.Surface_Area_SQM` already states. Duplicating it would give
    two numbers that drift.
    """
    __tablename__ = "sme_surface_prep_progress"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Site_ID = Column(Text, nullable=False)
    Equipment_Tag_No = Column(Text, nullable=False)
    Execution_Sub_Activity_Code = Column(Text, nullable=False)
    Variant_Key = Column(Text, nullable=False, server_default=text("''"))
    Activity = Column(Text)
    Done_SQM = Column(Float, nullable=False, server_default=text('0'))
    Entry_Count = Column(Integer, nullable=False, server_default=text('0'))
    Last_Entry_No = Column(Text)
    updated_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("Site_ID", "Equipment_Tag_No",
                         "Execution_Sub_Activity_Code", "Variant_Key"),
    )


class SmeExecutionEntryMaterial(Base):
    """A material line on an execution entry — the store keeper's physical draw.

    `Bench_For_1_SQM` is snapshotted from `sme_recipe` for the same reason the
    manpower benchmark is: the recipe is editable master data.
    """
    __tablename__ = "sme_execution_entry_material"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Entry_ID = Column(Integer, ForeignKey("sme_execution_entry.id",
                                          ondelete="CASCADE"), nullable=False)
    Material_Code = Column(Text, nullable=False)
    SAP_Code = Column(Text, nullable=False, server_default=text("''"))
    Actual_Qty = Column(Float, nullable=False, server_default=text('0'))
    UOM = Column(Text)
    Lot_No = Column(Text)
    Bench_For_1_SQM = Column(Float)
    # What the store keeper originally wrote, kept when an HOD corrects the
    # row. Without it the audit trail says a number changed but not from what.
    Original_Qty = Column(Float)

    # ── Phase 9d: the four layers, each with an owner ──────────────────────
    # ⚠️ Row_Index IS THE MAPPING. The QR carries no material list, so a
    # handwritten quantity is matched to a material by its POSITION on the
    # printed page.
    Row_Index = Column(Integer)
    OCR_Qty = Column(Float)              # grey  — what the camera saw
    OCR_Qty_Text = Column(Text)          #         "2+3" and "~4" kept verbatim
    OCR_Lot_Text = Column(Text)
    Supervisor_Qty = Column(Float)       # amber — what the supervisor filed
    SK_Qty = Column(Float)               # red   — what the store keeper set
    sk_edited = Column(Boolean, nullable=False, server_default=text('false'))
    # `Actual_Qty` above is the settled figure (purple when the HOD moved it).

    # The `consumption` row this line became — the double-deduction guard.
    Consumption_ID = Column(Integer)
    # Set when the benchmark says this quantity is implausible for the area
    # reported. A 10x reading is far likelier a misread digit than real use.
    Plausibility_Flag = Column(Text)
    __table_args__ = (
        UniqueConstraint("Entry_ID", "Material_Code", "SAP_Code"),
    )


class SmeExecutionEntryManpower(Base):
    """A crew line on an execution entry — the supervisor's actual headcount.

    Hours are per PERSON, so man-hours = Headcount x Hours. Stored split rather
    than pre-multiplied: a corrected headcount must not silently carry the old
    hours with it.
    """
    __tablename__ = "sme_execution_entry_manpower"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Entry_ID = Column(Integer, ForeignKey("sme_execution_entry.id",
                                          ondelete="CASCADE"), nullable=False)
    Role_Code = Column(Text, nullable=False)
    Headcount = Column(Float, nullable=False, server_default=text('0'))
    Hours = Column(Float, nullable=False, server_default=text('0'))
    Bench_Headcount = Column(Float)
    Original_Headcount = Column(Float)
    Original_Hours = Column(Float)
    __table_args__ = (
        UniqueConstraint("Entry_ID", "Role_Code"),
    )


class SmeManpowerNormRole(Base):
    """The crew composition of one norm — how many of each role.

    Stored as rows rather than nine columns so an HOD-added role needs no
    migration, and so `Crew_Size` can be checked against the parts that make
    it up instead of being a number nobody can decompose.
    """
    __tablename__ = "sme_manpower_norm_role"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Norm_ID = Column(Integer, ForeignKey("sme_manpower_norm.id", ondelete="CASCADE"),
                     nullable=False)
    Role_Code = Column(Text, nullable=False)
    Headcount = Column(Float, nullable=False, server_default=text('0'))
    __table_args__ = (
        UniqueConstraint("Norm_ID", "Role_Code"),
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
    # alembic e6a91c37b208 — drafted by auto_draft_dns after warehouse
    # goods-in. Still a DRAFT needing a human to submit it; the flag is so a
    # clerk can tell which rows in their queue they did not create.
    auto_generated = Column(Integer, nullable=False, server_default=text('0'))
    source_assignment_id = Column(Integer)
    # alembic b4f21c8ea9d7 — the paperwork the truck actually leaves with.
    # `dn_document_no` is the number printed on the PHYSICAL document, which
    # is somebody else's numbering scheme and therefore free text; DN_Number
    # above is ours and the two are unrelated strings. `dn_attachment_id`
    # points at entry_attachments (doc_type 'delivery_note'). Both nullable:
    # every DN shipped before 2026-08-13 went out without them, and a
    # backfill would have to invent what the driver was carrying. The gate
    # lives in ship_dn, where a NULL can still be refused going forward.
    dn_document_no = Column(Text)
    dn_attachment_id = Column(Integer)
    shipped_at = Column(DateTime)
    shipped_by = Column(Text)


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

class PrRegistry(Base):
    """One row per PR NUMBER — the place the number can be unique.

    `pr_master` cannot carry that constraint: a PR is many lines, so the number
    repeats by design. Without a table where it appears once, `_next_pr_number`
    was a read-then-write with nothing behind it, and two HODs creating a PR in
    the same second both read the same last number and both wrote the next one.
    From then on two different purchase requests are ONE PR to every query in
    the system, and nothing ever raised.

    The number is the primary key, so the database decides who got it. The
    generator inserts and retries on conflict rather than trusting what it read.

    ⚠️ KEYED ON THE NUMBER ALONE, not (number, site). That is deliberate: a PR
    number that means one thing at CNCEC and another at a second site is
    exactly the collision this exists to make impossible, and `Site_ID` here
    records where the number was issued rather than being part of its identity.
    """
    __tablename__ = "pr_registry"
    PR_Number = Column(Text, primary_key=True)
    Site_ID = Column(Text, nullable=False)
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))


class ProcurementIdempotency(Base):
    """A retry is not a second order.

    Four actions in the procurement chain are dangerous to repeat: creating a
    PR, submitting it, raising a PO and assigning one. A double-click, a flaky
    network retry or a stale tab sends the same request twice, and the second
    one is a second purchase request or a second warehouse told to expect the
    same goods.

    The key is CLAIMED before the work (`result_json = ''`) and filled in after,
    so two concurrent requests carrying one key serialise on the primary key
    instead of racing. A second request that arrives while the first is still
    in flight is told to wait — never handed an answer that does not exist yet.

    `body_hash` is what separates a retry from a bug: the same key with a
    DIFFERENT body is a client error, and replaying the first answer would hide
    it behind a success.
    """
    __tablename__ = "procurement_idempotency"
    idem_key = Column(Text, primary_key=True)
    action = Column(Text, nullable=False)
    body_hash = Column(Text, nullable=False)
    result_json = Column(Text, nullable=False, server_default=text("''"))
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        Index("ix_procurement_idem_action", "action", "created_at"),
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
    # entry_attachments.id of the scan these figures were read from (alembic
    # e6a91c37b208). /ai/extract/pr used to read the upload, parse it and
    # throw the bytes away — a PR created from a scan had no scan.
    source_attachment_id = Column(Integer)

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
    # ⚠️ Legacy import columns. They keep the rows they already hold and
    # NOTHING new is written to them — `entry_attachments` is the one
    # document store from alembic e6a91c37b208 onward. Two blob columns for
    # the same document is how the two disagree about which is current.
    attachment_blob = Column(LargeBinary)
    attachment_name = Column(Text)
    attachment_mime = Column(Text)
    source_attachment_id = Column(Integer)   # entry_attachments.id of the scan
    status = Column(Text, server_default=text("'open'"))
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    closed_at = Column(DateTime)
    closed_by = Column(Text)
    close_reason = Column(Text)

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

class LoginAttempts(Base):
    """The per-account failure budget, SHARED across workers (alembic
    f3c81d5a97e2).

    The in-process budget (ratelimit.LOGIN_FAIL_MAX) multiplies by the worker
    count; this row is the cross-worker authority. Postgres rather than Redis:
    the counter ticks a few times a minute and Postgres is already deployed,
    backed up and holding the users table this protects.

    ⚠️ Still THROTTLES, never LOCKS (rule 10). `window_start` rolls forward on
    its own and a correct password deletes the row — recovery is the passage of
    time, never an administrator, because a per-account limit someone else can
    trip on your behalf must not need a support ticket to undo.
    """
    __tablename__ = "login_attempts"
    username_lc = Column(Text, primary_key=True)
    window_start = Column(DateTime, nullable=False,
                          server_default=text('CURRENT_TIMESTAMP'))
    failures = Column(Integer, nullable=False, server_default=text('0'))


class DailyJobRun(Base):
    """One row per daily scheduled job — the claim the daily loops never had.

    ⚠️ THE DAILY LOOPS HAVE BEEN FIRING FOUR TIMES A DAY IN PRODUCTION.
    `report_center.scheduler_loop` claims its work with an atomic `last_run`
    UPDATE and says why in its docstring. The three DAILY loops — the 07:00
    morning briefing, the 16:00 evening digest and the Friday 17:00 executive
    report — do not: each sleeps until its hour and dispatches, in every worker.
    `deploy/Dockerfile.api` runs `uvicorn --workers 4`, so every one of those
    messages reached every recipient four times.

    Invisible in development (one worker) and invisible in the tests (the loops
    are off under GI_SCHEDULER=0), which is how it survived three phases.

    `last_worker` and `last_result` are here because "did it run, where, and
    what did it find" is the first question asked when a morning briefing does
    not arrive, and it is unanswerable afterwards if nobody wrote it down.
    """
    __tablename__ = "daily_job_runs"
    job_key = Column(Text, primary_key=True)
    last_run = Column(DateTime)
    last_worker = Column(Text)
    last_result = Column(Text)


class TrainingModule(Base):
    """A tutorial people must watch, and optionally a feature it gates.

    ⚠️ `version` IS LOAD-BEARING. Bumping it invalidates every acknowledgement
    of the previous version by construction — see `TrainingCompliance`.
    """
    __tablename__ = "training_modules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    module_key = Column(Text, nullable=False, unique=True)
    title = Column(Text, nullable=False)
    description = Column(Text)
    version = Column(Integer, nullable=False, server_default=text('1'))
    required_roles = Column(Text)          # CSV, e.g. 'supervisor,store_keeper'
    # The feature this module gates ('ocr_upload'), or NULL when it is purely
    # informational. A missing module means an UNGATED feature, which is the
    # right failure direction for a soft gate.
    gates_feature = Column(Text)
    active = Column(Integer, nullable=False, server_default=text('1'))
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))


class TrainingAsset(Base):
    """One video, in one language.

    ⚠️ A URI, NOT A BLOB (operator ruling Q5.3). A three-language tutorial set
    is plausibly 200-600 MB. In a `LargeBinary` column that lands in every
    nightly `pg_dump` forever, cannot serve HTTP Range requests — so the viewer
    cannot seek, and a training video you cannot scrub is one nobody re-watches
    — and competes for the same shared buffers as the OLTP workload.

    `language` is BCP-47: `ta`, `ar`, `en`, and `ta-Latn` for Tanglish (Tamil
    written in Latin script, which is what people speak on site and what the
    avatar videos are recorded in). A future language is a value, not a
    migration.
    """
    __tablename__ = "training_assets"
    __table_args__ = (
        UniqueConstraint("module_id", "language", name="uq_training_asset_lang"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    module_id = Column(Integer, nullable=False)
    language = Column(Text, nullable=False)
    storage_uri = Column(Text, nullable=False)
    captions_uri = Column(Text)
    duration_s = Column(Integer)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))


class TrainingCompliance(Base):
    """Who has watched what, and at which version of it.

    ⚠️ `module_version` IS PART OF THE UNIQUE KEY, and that is the whole point
    of the table. Keyed on `(username, module_id)` alone, re-recording a
    tutorial because the workflow changed would leave everybody certified
    against a video they have never seen — worse than having no record at all,
    because it looks like evidence. Bumping `TrainingModule.version`
    invalidates every prior acknowledgement by construction, rather than by a
    script somebody has to remember to run.

    ⚠️ A DEFERRAL IS RECORDED, NOT PUNISHED. The operator ruled a SOFT gate
    (Q5.1): a supervisor holding a filled form at 06:00 must never be unable to
    file consumption because of a video. "Watch Later" writes `deferred_at` and
    increments `deferrals`, which is what the HOD dashboard counts — the
    control is visibility, not refusal.
    """
    __tablename__ = "training_compliance"
    __table_args__ = (
        UniqueConstraint("username", "module_id", "module_version",
                         name="uq_training_compliance_user_module_version"),
        Index("ix_training_compliance_user", "username"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(Text, nullable=False)
    module_id = Column(Integer, nullable=False)
    module_version = Column(Integer, nullable=False)
    language = Column(Text)
    watched_seconds = Column(Integer, nullable=False, server_default=text('0'))
    completed_at = Column(DateTime)
    acknowledged_at = Column(DateTime)
    deferred_at = Column(DateTime)
    deferrals = Column(Integer, nullable=False, server_default=text('0'))
    updated_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))


class RateBuckets(Base):
    """The cross-worker half of every OTHER limiter (alembic d5b8c3f92a41).

    `LoginAttempts` above made the per-ACCOUNT login throttle true across
    workers. This generalises the same mechanism to the four that were still
    per-process, and therefore had an effective ceiling of 4x their configured
    limit under `uvicorn --workers 4`:

      · the per-IP `rate_limit(n, w)` dependency on the public auth endpoints
      · identity-keyed `check_bucket` budgets (one OTP allowance per PHONE
        NUMBER, regardless of how many source IPs an attacker rotates through)
      · `PenaltyBox` IP bans on the WhatsApp webhook
      · the per-account TOTP attempt budget — the most serious of the four,
        because it is the ceiling on brute-forcing the SECOND FACTOR and
        `_verify_totp` accepts three codes at any instant (valid_window=1)

    ⚠️ ONE TABLE, MANY LIMITERS. `bucket_key` carries its own namespace
    ("ip:…:/auth/login", "totp:jsmith", "otp:+966…", "ban:…"). They share the
    window algorithm exactly and differ only in what they count, so separate
    tables would be four copies of one `ON CONFLICT DO UPDATE`.

    ⚠️ POSTGRES, NOT REDIS — operator ruling re-confirmed 2026-09-02. And it
    FAILS OPEN: every read and write swallows storage errors and allows the
    request, because a throttle that takes sign-in down when its own storage
    hiccups is worse than the attack it prevents. That is the opposite of the
    access matrix, which fails CLOSED; the two are different decisions and are
    meant to differ.
    """
    __tablename__ = "rate_buckets"
    __table_args__ = (
        Index("ix_rate_buckets_window", "window_start"),
    )
    bucket_key = Column(Text, primary_key=True)
    window_start = Column(DateTime, nullable=False,
                          server_default=text('CURRENT_TIMESTAMP'))
    hits = Column(Integer, nullable=False, server_default=text('1'))


class AssetUnits(Base):
    """One row per PHYSICAL THING (alembic e9f2a4c68b71).

    Two hammers share one SAP code, so a scan cannot say which one you are
    holding. Identity is **`(SAP_Code, serial_no)`, GLOBALLY** — deliberately
    mirroring rule 1's lesson that what distinguishes two physical objects
    belongs IN THE KEY.

    ⚠️ It used to include `Site_ID` (alembic e9f2a4c68b71), which let one
    physical hammer exist as two rows at two sites with two custody chains
    and two GPS fixes. A serial number is stamped on the object, not issued
    per yard. `Site_ID` stays on the row because it is WHERE THE THING IS —
    data, not identity — and it changes only through an approved transfer
    (`asset_transfers`), never by a silent update. Widened key retired by
    alembic a3c17e9b25d4.

    ASSETS ONLY: a row exists only where an operator creates one, so
    consumables simply have none. The workbook cannot seed this — its
    `Serial No.` column is a BATCH number (3441 appears on both components of
    one primer) and its Location columns are blank.

    `current_*` caches the newest `AssetMovements` row, written in the same
    transaction; the movement log is the history and is never deleted.
    """
    __tablename__ = "asset_units"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Site_ID = Column(Text, nullable=False)
    SAP_Code = Column(Text, nullable=False)
    serial_no = Column(Text, nullable=False)
    asset_tag = Column(Text)
    status = Column(Text, nullable=False, server_default=text("'in_stock'"))
    current_location_id = Column(Integer)
    current_lat = Column(Float)
    current_lng = Column(Float)
    gps_accuracy_m = Column(Float)
    location_note = Column(Text)
    holder = Column(Text)
    last_seen_at = Column(DateTime)
    last_seen_by = Column(Text)
    notes = Column(Text)
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("SAP_Code", "serial_no", name="uq_asset_units_sap_serial"),
        Index("ix_asset_units_sap_site", "SAP_Code", "Site_ID"),
        Index("ix_asset_units_serial", "serial_no"),
        # Was the unique constraint; kept as an index because it is also the
        # shape of every by-site asset lookup.
        Index("ix_asset_units_site_sap_serial", "Site_ID", "SAP_Code", "serial_no"),
    )


class AssetMovements(Base):
    """Append-only "where has this been" (alembic e9f2a4c68b71).

    Same discipline as `system_audit_log`: rows are never deleted, so the
    history is a query rather than a guess.

    ⚠️ `lat`/`lng` is where an EMPLOYEE was standing when they scanned. It is
    best-effort — a denied browser permission still records the move with the
    coordinates NULL, because location capture must never block a location
    update — and it is the first genuinely personal data this system stores.
    """
    __tablename__ = "asset_movements"
    id = Column(Integer, primary_key=True, autoincrement=True)
    asset_unit_id = Column(Integer, nullable=False)
    moved_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    moved_by = Column(Text)
    from_location_id = Column(Integer)
    to_location_id = Column(Integer)
    from_note = Column(Text)
    to_note = Column(Text)
    lat = Column(Float)
    lng = Column(Float)
    accuracy_m = Column(Float)
    source = Column(Text)
    status = Column(Text)
    note = Column(Text)
    __table_args__ = (Index("ix_asset_movements_unit", "asset_unit_id", "moved_at"),)


class StorageLocations(Base):
    """A physical place in the warehouse (alembic d5b83c17e604).

    `code` is the QR payload printed on the shelf label — scanning a RACK
    answers "what is supposed to be here", which is what makes a stock count
    fast. Zone / rack / row / bin are kept as separate fields so the locator
    can group and sort by them; `code` is what a human reads out.
    """
    __tablename__ = "storage_locations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Site_ID = Column(Text, nullable=False)
    code = Column(Text, nullable=False)
    zone = Column(Text)
    rack_no = Column(Text)
    row_no = Column(Text)
    bin_no = Column(Text)
    description = Column(Text)
    status = Column(Text, nullable=False, server_default=text("'active'"))
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("Site_ID", "code", name="uq_storage_locations_site_code"),
        Index("ix_storage_locations_site", "Site_ID", "status"),
    )


class MaterialLocations(Base):
    """Which SAP lives in which rack (alembic d5b83c17e604).

    MANY-TO-MANY on purpose: a material legitimately sits in more than one
    place, and `is_primary` marks the one to walk to first. Deliberately not a
    column on `inventory`, which is one row per SAP and already carries a
    UNIQUE on Material_Code — the wrong grain for a material in three racks.
    """
    __tablename__ = "material_locations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Site_ID = Column(Text, nullable=False)
    SAP_Code = Column(Text, nullable=False)
    location_id = Column(Integer, nullable=False)
    is_primary = Column(Boolean, nullable=False, server_default=text('true'))
    note = Column(Text)
    updated_by = Column(Text)
    updated_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("Site_ID", "SAP_Code", "location_id",
                         name="uq_material_locations_site_sap_loc"),
        # The store keeper's lookup — the whole point of the feature.
        Index("ix_material_locations_sap", "SAP_Code", "Site_ID"),
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
    # 2026-07-18 Bug Tracking Engine (alembic c7d4e8f19a25) — safe
    # change-management fields, new-stack only (frozen SQLite never learns
    # them; allowlisted in bug_check models-parity).
    title = Column(Text)
    severity = Column(Text)             # low | medium | high | critical
    rollback_notes = Column(Text)       # how to back the change out
    safety_constraints = Column(Text)   # what must NOT break / gates to run
    triage_notes = Column(Text)         # admin analysis before implementation

class GeneratedReport(Base):
    """Phase 8-3 — auto-generated report artifacts (weekly executive PDF).

    Each row is one rendered PDF plus a SECURE EXPIRING DOWNLOAD token: the
    WhatsApp message carries `{PUBLIC_BASE_URL}/reports/weekly-exec/{token}`;
    the raw token is never stored (only its sha256), and downloads stop at
    `expires_at`. NEW-STACK ONLY — no SQLite counterpart; dual_ci reloads
    leave it empty, which just means old links die (they expire in 72 h
    anyway)."""
    __tablename__ = "generated_reports"
    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(Text, nullable=False)                 # 'weekly_exec'
    Site_ID = Column(Text)                              # NULL = all sites
    date_from = Column(Text, nullable=False)
    date_to = Column(Text, nullable=False)
    filename = Column(Text, nullable=False)
    content = Column(LargeBinary, nullable=False)
    token_hash = Column(Text, nullable=False, unique=True)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    expires_at = Column(DateTime, nullable=False)


# ==========================================================================
# 6b. Quality Control — the QSEP programme (alembic b4d17c8e93a2)
#
# "QSEP" = Quality · Safety · Employees · Procurement, the 2026-08 programme.
# It is deliberately NOT called "Phase 6": that name is already used in
# entry.py, notifications.py and warehouse.py for the 2026-07-10 UAT work,
# and two meanings for one label is how a comment stops being readable.
# ==========================================================================

class PpeRules(Base):
    """How long an item of PPE is deemed to last (alembic c9e35a71d4b6).

    Separate from `inventory."Category" = 'PPE'` on purpose, and the two do
    different jobs:

      Category  → which items OFFER the PPE flow (what the UI filters on)
      a rule    → which items have a usable time (what the maths needs)

    A PPE-category item with no rule is still distributed and recorded; it
    simply has no expiry and the forecast cannot see it. That is a
    data-entry gap the rules page surfaces, not an error.

    `Site_ID` NULL = the global default; a site row overrides it. The unique
    index is on COALESCE(Site_ID,'') — see the migration for why a plain
    UNIQUE would silently permit duplicate global rules.
    """
    __tablename__ = "ppe_rules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    SAP_Code = Column(Text, nullable=False)
    Site_ID = Column(Text)
    usable_days = Column(Integer, nullable=False)
    requires_safety_doc = Column(Integer, nullable=False, server_default=text('1'))
    notes = Column(Text)
    created_by = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        Index("ux_ppe_rules_sap_site", "SAP_Code",
              text('COALESCE("Site_ID", \'\')'), unique=True),
    )


class PpeDistributions(Base):
    """One handover of PPE to one person (alembic c9e35a71d4b6).

    Keyed on `employee_id_number` — the PERSON — never on a per-site
    employment record, which is what makes the history follow a transfer
    (ruling R1). Each row keeps its own issuing `Site_ID` for site reporting.

    `usable_days_applied` and `expires_on` are both STORED rather than
    derived, so shortening a rule later cannot retroactively rewrite when
    the boots already on someone's feet were deemed to expire.

    Lifecycle mirrors the ledger's stage→approve shape: the row is written
    when the issue is STAGED (the SK has physically handed the gear over,
    and that is what stops a second pair being issued while the HOD
    approval is pending), gains `consumption_id` on approval, and flips to
    `void` if the HOD rejects.
    """
    __tablename__ = "ppe_distributions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Site_ID = Column(Text, nullable=False)
    employee_id_number = Column(Text, nullable=False)
    employee_name = Column(Text)
    SAP_Code = Column(Text, nullable=False)
    Material_Code = Column(Text)
    Description = Column(Text)
    Lot_Number = Column(Text)
    Qty = Column(Float, nullable=False)
    issued_on = Column(Text, nullable=False)
    usable_days_applied = Column(Integer)
    expires_on = Column(Text)
    safety_doc_id = Column(Integer)
    replaces_distribution_id = Column(Integer)
    early_replacement = Column(Integer, nullable=False, server_default=text('0'))
    early_reason = Column(Text)
    pending_issue_id = Column(Integer)
    consumption_id = Column(Integer)
    # active | replaced | expired | returned | void
    status = Column(Text, nullable=False, server_default=text("'active'"))
    issued_by = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))


class EmployeeMovements(Base):
    """Every site change a person has made (alembic d2f84b19e57c).

    HOD-initiated and immediate (ruling R4) — only the QC *user* transfer
    needs an admin's second signature, because that one rewrites an
    authentication row and has to revoke sessions.
    """
    __tablename__ = "employee_movements"
    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id_number = Column(Text, nullable=False)
    from_site = Column(Text)
    to_site = Column(Text, nullable=False)
    effective_date = Column(Text, nullable=False)
    reason = Column(Text)
    moved_by = Column(Text, nullable=False)
    status = Column(Text, nullable=False, server_default=text("'applied'"))
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))


class QcInspections(Base):
    """One quality decision about one lot of one material at one place.

    Keyed to the LOT, because that is the granularity `lots` already uses
    (UNIQUE Lot_Number/SAP_Code/Site_ID). A partial approval is
    approved_qty < submitted_qty; the remainder is rejected_qty and a
    decision_reason is mandatory whenever anything is rejected.

    The UNIQUE on (source_type, source_ref, SAP_Code, Lot_Number) is what
    makes the trigger idempotent — re-running a warehouse receipt must not
    open a second inspection for the same physical goods.

    Exactly one of Site_ID / Warehouse_ID is set: an inspection happens
    either at a site or at a warehouse, never both.
    """
    __tablename__ = "qc_inspections"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Site_ID = Column(Text)
    Warehouse_ID = Column(Text)
    SAP_Code = Column(Text, nullable=False)
    Material_Code = Column(Text)
    Lot_Number = Column(Text)
    # warehouse_receipt | dn_receipt | site_receipt
    source_type = Column(Text, nullable=False)
    source_ref = Column(Text, nullable=False)
    mtc_document_id = Column(Integer)
    submitted_qty = Column(Float, nullable=False)
    approved_qty = Column(Float, nullable=False, server_default=text('0'))
    rejected_qty = Column(Float, nullable=False, server_default=text('0'))
    # pending | approved | partially_approved | rejected
    status = Column(Text, nullable=False, server_default=text("'pending'"))
    decision_reason = Column(Text)
    inspected_by = Column(Text)
    inspected_at = Column(DateTime)
    created_by = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    # alembic c7a93e5d2b18 — the handle a rejection hands to the store keeper.
    # The QC quotes it, the SK types it into the return form, and the form
    # fills itself from this row. UNIQUE (partially, since approvals leave it
    # NULL) because two returns against one Return No would take the rejected
    # quantity out of stock twice.
    return_no = Column(Text)
    return_posted_id = Column(Integer)
    __table_args__ = (
        UniqueConstraint("source_type", "source_ref", "SAP_Code", "Lot_Number",
                         name="uq_qc_inspection_source"),
        Index("ux_qc_inspection_return_no", "return_no", unique=True,
              postgresql_where=text("return_no IS NOT NULL")),
        # The issuance guard reads (Site_ID, SAP_Code, status) on every
        # surface-shield issue. NOT indexed yet — rule 11 says an index is
        # benchmarked before it is added, and this table has zero rows.
    )


class AssetTransfers(Base):
    """Moving a physical asset between sites, approved by the site LOSING it.

    Silently updating `asset_units.Site_ID` is how a tool leaves a yard
    without anybody agreeing to it. The SOURCE site's HOD decides, because
    that is the site with something at stake and the only one that can
    confirm the thing physically left.

    A partial unique index (`ux_asset_transfer_open`) allows exactly one
    request per asset in `pending_source_hod`, so two sites cannot both hold
    a claim on the same hammer with the second approval silently winning.

    ⚠️ That index lived ONLY in alembic a3c17e9b25d4 until 2026-08-13, so it
    was present on every database Alembic had walked and absent from every
    database built by `metadata.create_all` — which is how
    `tools/migration/cutover_migrate.py` builds the schema on cutover day. A
    production box would have been loaded WITHOUT the guard this docstring
    promises, and nothing would have said so: the race is silent by nature,
    and the suite that covers it (BR) had only ever run against a migrated
    database. Declaring it here makes the two paths agree.
    """
    __tablename__ = "asset_transfers"
    id = Column(Integer, primary_key=True, autoincrement=True)
    asset_unit_id = Column(Integer, nullable=False)
    SAP_Code = Column(Text, nullable=False)
    serial_no = Column(Text, nullable=False)
    from_site = Column(Text, nullable=False)
    to_site = Column(Text, nullable=False)
    reason = Column(Text, nullable=False)
    requested_by = Column(Text, nullable=False)
    requested_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    # pending_source_hod | approved | rejected | cancelled
    status = Column(Text, nullable=False,
                    server_default=text("'pending_source_hod'"))
    decided_by = Column(Text)
    decided_at = Column(DateTime)
    decision_notes = Column(Text)
    movement_id = Column(Integer)
    __table_args__ = (
        # Mirrors alembic a3c17e9b25d4 exactly — same name, same predicate, so
        # create_all() and `alembic upgrade head` converge on one database.
        Index("ux_asset_transfer_open", "asset_unit_id", unique=True,
              postgresql_where=text("status = 'pending_source_hod'")),
    )


class QcTransferRequests(Base):
    """An HOD asks to move a QC account to another site; an admin decides.

    Requirement 1: QC users "can be transferred between sites by HODs with
    Admin approval". The two-step exists because moving a users row changes
    authority that rides inside a 15-minute access token — approval is also
    where revoke_all_sessions() fires.
    """
    __tablename__ = "qc_transfer_requests"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(Text, nullable=False)
    from_site = Column(Text)
    to_site = Column(Text, nullable=False)
    reason = Column(Text, nullable=False)
    requested_by = Column(Text, nullable=False)
    requested_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    # pending_admin | approved | rejected | cancelled
    status = Column(Text, nullable=False, server_default=text("'pending_admin'"))
    decided_by = Column(Text)
    decided_at = Column(DateTime)
    decision_notes = Column(Text)


class QcEscalations(Base):
    """A Head of Qualities asking somebody who CAN act, to act.

    The role reads across every site and writes almost nothing — this table is
    the almost. It is the only thing a `qc_hod` may create, and every row is a
    message, never a change to stock, an inspection decision or a document.

    ⚠️ IT IS A LOG, NOT A FIRE-AND-FORGET NOTIFICATION. "Send the site QC a
    reminder about the missing MTC" is trivially a `dispatch()` call; what that
    cannot answer is *how long has this been chased, and by whom*. Uncertified
    Surface Shield is a standing condition, so the second and third chase are
    the ones that matter, and they only exist if the first was written down.

    ⚠️ THE TARGET IS A SPECIFIC PLACE (operator ruling Q12). Exactly one of
    `target_site` / `target_warehouse` is set. A broadcast to every site QC
    about one site's material is the kind of message people learn to ignore,
    and an escalation nobody reads is worse than none — it looks like the
    problem was raised.
    """
    __tablename__ = "qc_escalations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    raised_by = Column(Text, nullable=False)
    target_role = Column(Text, nullable=False)      # qc | warehouse_user | logistics
    target_site = Column(Text)                      # exactly one of these two
    target_warehouse = Column(Text)
    # mtc_demand | inspection_request | transfer_suggestion
    kind = Column(Text, nullable=False)
    SAP_Code = Column(Text)
    Material_Code = Column(Text)
    Lot_Number = Column(Text)
    PO_Number = Column(Text)
    message = Column(Text, nullable=False)
    status = Column(Text, nullable=False, server_default=text("'open'"))
    resolved_by = Column(Text)
    resolved_at = Column(DateTime)
    resolution_note = Column(Text)
    # The app_notifications row this actually sent. Without it "I raised it" and
    # "they were told" are two different claims with nothing joining them.
    notification_id = Column(Integer)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        Index("ix_qc_escalations_open", "status", "kind", "created_at"),
    )


class QcStagnationRules(Base):
    """When controlled material has sat too long, per category.

    A TABLE and not a constant, for the same reason the overtime thresholds are
    (`mh_ot_threshold_*`): 90 days is the operator's policy, not the system's,
    and changing a policy must not be a code change. Seeded at 90 days without
    movement and 60 days to expiry (operator ruling Q9) so the settings page
    shows a real number on a fresh box rather than an empty form.
    """
    __tablename__ = "qc_stagnation_rules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    Category = Column(Text, nullable=False)
    stagnant_days = Column(Integer, nullable=False, server_default=text('90'))
    expiry_warn_days = Column(Integer, nullable=False, server_default=text('60'))
    status = Column(Text, nullable=False, server_default=text("'active'"))
    updated_by = Column(Text)
    updated_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        UniqueConstraint("Category"),
    )


class SystemAuditLog(Base):
    __tablename__ = "system_audit_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    username = Column(Text, nullable=False)
    action_type = Column(Text, nullable=False)
    target_table = Column(Text)
    details = Column(Text, nullable=False)
    # Filtered by action_type on the audit page and by several suites. NOT
    # indexed on (id DESC) or (username, id DESC): both were benchmarked at
    # ~9.5 MB each for zero planner uses, because the primary key already
    # serves "newest N" by scanning backwards. See alembic e7c3b95a41d2.
    __table_args__ = (
        Index("ix_audit_action_type", "action_type"),
    )


class AiTrace(Base):
    """One SPAN of one AI request (alembic f8a3c05d1b27, Phase 11 slice 11c).

    ⚠️ WHAT THIS EXISTS FOR: WHEN THE ASSISTANT ANSWERS BADLY, NOBODY COULD
    TELL WHETHER RETRIEVAL OR GENERATION WAS AT FAULT. `manual_index.Index`
    already COMPUTED a BM25 score for every candidate chunk and then discarded
    all of them, so a wrong answer and a right answer left identical evidence:
    none. The 800-character head-truncation that hid the access matrix from
    every non-admin role — and made the assistant tell HODs they could not open
    the Manpower portal — was a retrieval failure that survived a whole phase
    for exactly this reason.

    ⚠️ POSTGRES, NOT A SAAS TRACER (ruling P11-1). A LangSmith span carries the
    PROMPT, and our prompts contain manual chapters, the results of five live
    SQL probes over the ERP, generated SQL, and OCR'd images of signed delivery
    notes with employee names on them. Sending that to a third party is a
    decision far above an observability upgrade. This table is already backed
    up, already in the runbook, and sits next to `system_audit_log` and
    `ai_jobs` — which is where an incident investigation starts anyway.

    ⚠️ AND WHAT IT DELIBERATELY DOES NOT STORE. The QUESTION is kept (operator
    ruling Q11, 2026-09-02 — the twenty most-asked questions are the most
    valuable eval cases in the system and nothing was retaining them). The
    ANSWER is not, and neither is retrieved chunk TEXT: `ai.retrieve` records
    chapter numbers, headings, scores and character counts. Storing the passages
    would duplicate the manual into a table with a laxer read path than the
    manual itself has, which is a way of undoing rule 9 by accident.

    One row per span rather than one per request, because the whole point is
    that the stages are separately measurable. `trace_id` groups them and
    `span` names the stage; a request that dies in the guard has one row and a
    complete one has five.
    """
    __tablename__ = "ai_traces"
    __table_args__ = (
        # The console reads "the last N", newest first, optionally by lane.
        Index("ix_ai_traces_created", "created_at"),
        Index("ix_ai_traces_lane_created", "lane", "created_at"),
        # Assembling one request's waterfall from its spans.
        Index("ix_ai_traces_trace", "trace_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    # A request's own id, shared by every span of it. Text, not UUID: the
    # column is grouped and equality-matched, never generated in SQL.
    trace_id = Column(Text, nullable=False)
    # ai.request | ai.guard.input | ai.retrieve | ai.cache | ai.generate |
    # ai.guard.output — see ai/trace.py, which owns the vocabulary.
    span = Column(Text, nullable=False)
    lane = Column(Text)                     # assistant | ocr_consumption | …
    role = Column(Text)
    username = Column(Text)
    Site_ID = Column(Text)
    started_at = Column(DateTime)
    duration_ms = Column(Integer)
    ok = Column(Integer, nullable=False, server_default=text('1'))
    outcome = Column(Text)                  # short verb: ok | refused | error
    # JSON object. Text rather than JSONB to match every other JSON column in
    # this schema (`OCR_Raw_JSON`, `payload_json`, `result_json`) — the parity
    # tooling and the SQLite bridge both assume Text, and one column with a
    # different storage type would be a trap for whoever writes the next
    # migration rather than a feature anybody asked for.
    attrs_json = Column(Text)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))


class AiAnswerCache(Base):
    """A previously-given assistant answer, keyed so it cannot be given to the
    wrong person (alembic `a1c9e64b3d70`, Phase 11 slice 11e).

    ⚠️ THE KEY IS THE ENTIRE SECURITY OF THIS TABLE, AND OMITTING `role` WOULD
    BE A FENCE BYPASS. Rule 9's whole guarantee is that a role's CONTEXT
    differs: two people can type a byte-identical question and be entitled to
    different answers, because they were shown different chapters. A cache
    keyed on the question alone would serve an Admin's answer to a Store
    Keeper — undoing, from the side, the boundary the retrieval fence enforces
    so carefully from the front. `key_hash` therefore covers:

        normalised question · role · manual content hash · prompt template
        hash · answer schema version

    ⚠️ AND THE MANUAL HASH IS NOT OPTIONAL EITHER. The manual gains a chapter
    almost every phase; an answer cached against the previous one is a
    confidently-worded description of a screen that has changed. Hashing the
    corpus means a manual edit silently retires every entry, which is the only
    invalidation rule nobody has to remember.

    ⚠️ ONLY THE MANUAL ASSISTANT IS CACHED. `/ai/query`, `/ai/nl-search`,
    `/ai/insights` and `/ai/eod-summary` answer from LIVE STOCK, and a cached
    "you have 40 drums" is a wrong number wearing a timestamp. The lane is
    recorded so that a future addition has to say so out loud.
    """
    __tablename__ = "ai_answer_cache"
    __table_args__ = (
        # The sweep and the console both read by age.
        Index("ix_ai_answer_cache_created", "created_at"),
        # "What is this role asking repeatedly" — the hit-rate question that
        # decides whether a semantic cache is ever worth building (rule 11:
        # measure before you add).
        Index("ix_ai_answer_cache_role", "role", "created_at"),
    )
    key_hash = Column(Text, primary_key=True)
    lane = Column(Text, nullable=False, server_default=text("'assistant'"))
    role = Column(Text, nullable=False)
    # Kept in the clear for the console and for eval mining (ruling Q11 already
    # retains questions in `ai_traces`; this is the same data, deduplicated).
    question_norm = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    model = Column(Text)
    manual_hash = Column(Text, nullable=False)
    prompt_hash = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))
    last_hit_at = Column(DateTime)
    hit_count = Column(Integer, nullable=False, server_default=text('0'))


# ==========================================================================
# SQL VIEWS — recreate as PostgreSQL views at migration (NOT ORM tables).
# SME compat views alias sme_* tables (Canon rule 1); derived views compute
# live stock/lot balances. Order SME view reads by explicit PK, never rowid.
# ==========================================================================
SME_AND_DERIVED_VIEWS = {
    'consumption_log': "CREATE VIEW consumption_log AS\n            SELECT id,\n                   entry_date,\n                   Equipment_Tag_No    AS equipment_tag,\n                   Lining_System_Code  AS lining_system_code,\n                   SQM_Completed       AS sqm_completed,\n                   Material_Code       AS material_code,\n                   Expected_Qty        AS expected_qty,\n                   Actual_Qty          AS consumed_qty,\n                   Variance_Pct        AS variance_pct,\n                   '' AS variance_status,\n                   '' AS material_name,\n                   '' AS uom,\n                   '' AS lining_system_name,\n                   committed_at        AS submitted_at,\n                   Site_ID\n            FROM sme_consumption_log\n            WHERE status = 'committed'",
    'equipment': 'CREATE VIEW equipment AS\n            SELECT id,\n                   Site_ID                  AS site_id,\n                   Equipment_Tag_No         AS equipment_tag,\n                   Name                     AS name,\n                   Location                 AS location,\n                   Type                     AS type,\n                   Substrate                AS substrate,\n                   Lining_System_Code       AS lining_system_code,\n                   Lining_System_Short_Name AS lining_system_short_name,\n                   Lining_Type              AS lining_type,\n                   Material_Spec            AS "Material Spec.",\n                   Design                   AS design,\n                   Lining_System            AS "Lining_System",\n                   Lining_Area_Location     AS "Lining_Area/location",\n                   Sl_No                    AS "Sl. #",\n                   Project                  AS project,\n                   WBS_No                   AS "WBS #",\n                   IO_No                    AS "IO#",\n                   Sub_Location             AS "Sub_Location",\n                   Drawing_No               AS "Drawing #",\n                   Dia_L                    AS "Dia / L",\n                   Ht_W                     AS "Ht. /W",\n                   Equipment_Total_SQM      AS "Equipment Total SQM",\n                   Remaraks                 AS remaraks,\n                   Lining_System            AS lining_systems,\n                   Surface_Area_SQM         AS surface_area_sqm\n            FROM sme_equipment',
    'locations': "CREATE VIEW locations AS\n            SELECT value AS name,\n                   '#64748B' AS badge_color,\n                   MIN(id) AS sort_order,\n                   '' AS added_at\n            FROM system_settings\n            WHERE category = 'sme_location'\n            GROUP BY value",
    'recipe': 'CREATE VIEW recipe AS\n            SELECT id,\n                   Lining_System_Code       AS lining_system_code,\n                   Lining_System_Name       AS lining_system_short_name,\n                   Lining_Type              AS lining_type,\n                   Lining_System            AS lining_system,\n                   Substrate                AS substrate,\n                   System_Keys              AS system_keys,\n                   Lining_Thickness         AS lining_thickness,\n                   Material_Code            AS material_code,\n                   COALESCE(Material_Description, Material_Name) AS material_description,\n                   Material_Name            AS material_name,\n                   For_1_SQM                AS for_1_sqm,\n                   UOM                      AS uom,\n                   Nature                   AS nature,\n                   Package_Size             AS package_size,\n                   Sl_No                    AS "Sl. #"\n            FROM sme_recipe',
    'sme_materials_view': "CREATE VIEW sme_materials_view AS\n            SELECT s.Material_Code         AS material_code,\n                   s.Material_Name         AS material_name,\n                   s.Item                  AS item,\n                   s.Vendor                AS vendor,\n                   s.Purchasing_Document   AS purchasing_document,\n                   s.Document_Date         AS document_date,\n                   s.Nature                AS nature,\n                   s.UOM                   AS uom,\n                   s.Initial_Available_Qty AS initial_available_qty,\n                   s.Initial_Ordered_Qty   AS initial_ordered_qty,\n                   COALESCE((\n                       SELECT SUM(r.Quantity)\n                       FROM receipts r\n                       JOIN inventory i ON r.SAP_Code = i.SAP_Code\n                       WHERE TRIM(COALESCE(i.Material_Code,'')) = TRIM(s.Material_Code)\n                   ), 0) AS received_qty,\n                   COALESCE((\n                       SELECT SUM(c.Quantity)\n                       FROM consumption c\n                       JOIN inventory i ON c.SAP_Code = i.SAP_Code\n                       WHERE TRIM(COALESCE(i.Material_Code,'')) = TRIM(s.Material_Code)\n                   ), 0) AS consumed_qty,\n                   (s.Initial_Available_Qty\n                       + COALESCE((\n                           SELECT SUM(r.Quantity)\n                           FROM receipts r\n                           JOIN inventory i ON r.SAP_Code = i.SAP_Code\n                           WHERE TRIM(COALESCE(i.Material_Code,'')) = TRIM(s.Material_Code)\n                         ), 0)\n                       - COALESCE((\n                           SELECT SUM(c.Quantity)\n                           FROM consumption c\n                           JOIN inventory i ON c.SAP_Code = i.SAP_Code\n                           WHERE TRIM(COALESCE(i.Material_Code,'')) = TRIM(s.Material_Code)\n                         ), 0)\n                   ) AS available_qty,\n                   s.Initial_Ordered_Qty   AS ordered_qty\n            FROM sme_inventory_seed s",
    'sqm_progress': 'CREATE VIEW sqm_progress AS\n            SELECT Site_ID            AS site_id,\n                   Equipment_Tag_No   AS equipment_tag,\n                   Lining_System_Code AS lining_system_code,\n                   Original_SQM       AS original_sqm,\n                   (COALESCE(Done_SQM,0) + COALESCE(Done_SQM_staged,0)) AS done_sqm\n            FROM sme_sqm_progress',
    'types': "CREATE VIEW types AS\n            SELECT value AS name,\n                   MIN(id) AS sort_order,\n                   '' AS added_at\n            FROM system_settings\n            WHERE category = 'sme_equipment_type'\n            GROUP BY value",
    'v_expiring_stock': "CREATE VIEW v_expiring_stock AS\n            SELECT\n                TRIM(r.SAP_Code)                   AS SAP_Code,\n                i.Equipment_Description            AS Equipment_Description,\n                i.UOM                              AS UOM,\n                COALESCE(r.Site_ID, 'HQ')          AS Site_ID,\n                r.Quantity                         AS Quantity,\n                r.Supplier                         AS Supplier,\n                r.PR_Number                        AS PR_Number,\n                r.Expiry_Date                      AS Expiry_Date,\n                CAST(julianday(date(r.Expiry_Date)) - julianday(date('now')) AS INTEGER)\n                                                   AS Days_Until_Expiry,\n                CASE\n                    WHEN julianday(date(r.Expiry_Date)) < julianday(date('now'))\n                        THEN 'Expired'\n                    WHEN julianday(date(r.Expiry_Date))\n                         <= julianday(date('now','+30 days'))\n                        THEN 'Short-Dated'\n                    ELSE 'Good'\n                END                                AS Expiry_Status\n            FROM receipts r\n            LEFT JOIN inventory i ON TRIM(i.SAP_Code) = TRIM(r.SAP_Code)\n            WHERE r.Expiry_Date IS NOT NULL\n              AND r.Expiry_Date != ''\n              AND date(r.Expiry_Date) IS NOT NULL",
    'v_inventory_with_sme': "CREATE VIEW v_inventory_with_sme AS\n            SELECT i.*,\n                   CASE WHEN EXISTS (\n                       SELECT 1 FROM sme_recipe r\n                       WHERE TRIM(r.Material_Code) = TRIM(COALESCE(i.Material_Code,''))\n                         AND TRIM(COALESCE(i.Material_Code,'')) <> ''\n                   ) THEN 1 ELSE 0 END AS is_sme\n            FROM inventory i",
    'v_live_stock': 'CREATE VIEW v_live_stock AS\n            SELECT\n                TRIM(i.SAP_Code)               AS SAP_Code,\n                i.Equipment_Description        AS Equipment_Description,\n                i.Material_Code                AS Material_Code,\n                i.UOM                          AS UOM,\n                COALESCE(i.Minimum_Qty, 0)     AS Minimum_Qty,\n                COALESCE(r.Total_Received, 0)  AS Total_Received,\n                COALESCE(c.Total_Consumed, 0)  AS Total_Consumed,\n                COALESCE(rt.Total_Returned, 0) AS Total_Returned,\n                COALESCE(r.Total_Received, 0)\n                  - COALESCE(c.Total_Consumed, 0)\n                  - COALESCE(rt.Total_Returned, 0) AS Current_Stock\n            FROM inventory i\n            LEFT JOIN (\n                SELECT TRIM(SAP_Code) AS SAP_Code, SUM(Quantity) AS Total_Received\n                FROM receipts GROUP BY TRIM(SAP_Code)\n            ) r  ON r.SAP_Code  = TRIM(i.SAP_Code)\n            LEFT JOIN (\n                SELECT TRIM(SAP_Code) AS SAP_Code, SUM(Quantity) AS Total_Consumed\n                FROM consumption GROUP BY TRIM(SAP_Code)\n            ) c  ON c.SAP_Code  = TRIM(i.SAP_Code)\n            LEFT JOIN (\n                SELECT TRIM(SAP_Code) AS SAP_Code, SUM(Quantity) AS Total_Returned\n                FROM returns GROUP BY TRIM(SAP_Code)\n            ) rt ON rt.SAP_Code = TRIM(i.SAP_Code)',
    'v_lot_balance': "CREATE VIEW v_lot_balance AS\n            SELECT\n                l.Lot_Number,\n                l.SAP_Code,\n                l.Site_ID,\n                l.Received_Date,\n                l.Expiry_Date,\n                l.Supplier,\n                l.PR_Number,\n                l.Status,\n                COALESCE((\n                    SELECT SUM(r.Quantity) FROM receipts r\n                    WHERE r.Lot_Number = l.Lot_Number\n                      AND r.SAP_Code   = l.SAP_Code\n                      AND COALESCE(r.Site_ID,'HQ') = l.Site_ID\n                ), 0) AS Received_Qty,\n                COALESCE((\n                    SELECT SUM(c.Quantity) FROM consumption c\n                    WHERE c.Lot_Number = l.Lot_Number\n                      AND c.SAP_Code   = l.SAP_Code\n                      AND COALESCE(c.Site_ID,'HQ') = l.Site_ID\n                ), 0) AS Consumed_Qty,\n                COALESCE((\n                    SELECT SUM(r.Quantity) FROM receipts r\n                    WHERE r.Lot_Number = l.Lot_Number\n                      AND r.SAP_Code   = l.SAP_Code\n                      AND COALESCE(r.Site_ID,'HQ') = l.Site_ID\n                ), 0) - COALESCE((\n                    SELECT SUM(c.Quantity) FROM consumption c\n                    WHERE c.Lot_Number = l.Lot_Number\n                      AND c.SAP_Code   = l.SAP_Code\n                      AND COALESCE(c.Site_ID,'HQ') = l.Site_ID\n                ), 0)\n                -- split/merge reclassification (within-SAP; nets to zero)\n                - COALESCE((\n                    SELECT SUM(t.Qty) FROM lot_transfers t\n                    WHERE t.From_Lot = l.Lot_Number\n                      AND t.SAP_Code = l.SAP_Code\n                      AND COALESCE(t.Site_ID,'HQ') = l.Site_ID\n                ), 0)\n                + COALESCE((\n                    SELECT SUM(t.Qty) FROM lot_transfers t\n                    WHERE t.To_Lot = l.Lot_Number\n                      AND t.SAP_Code = l.SAP_Code\n                      AND COALESCE(t.Site_ID,'HQ') = l.Site_ID\n                ), 0) AS Remaining_Qty\n            FROM lots l",
    'v_mh_estimate_vs_actual': 'CREATE VIEW v_mh_estimate_vs_actual AS\n            SELECT\n                e.Site_ID                                   AS Site_ID,\n                e.Equipment_Tag                             AS Equipment_Tag,\n                e.System_Code                               AS System_Code,\n                e.Location                                  AS Location,\n                e.Estimated_Manhours                        AS Estimated_Manhours,\n                COALESCE(a.Actual_Manhours, 0)              AS Actual_Manhours,\n                COALESCE(a.Actual_Manhours, 0)\n                    - e.Estimated_Manhours                  AS Variance_Manhours,\n                CASE WHEN e.Estimated_Manhours > 0\n                     THEN ROUND((COALESCE(a.Actual_Manhours, 0)\n                          - e.Estimated_Manhours) * 100.0\n                          / e.Estimated_Manhours, 1)\n                     ELSE NULL END                          AS Variance_Pct,\n                COALESCE(p.SQM_Done, 0)                     AS SQM_Done,\n                n.Reason                                    AS Variance_Reason\n            FROM mh_manhour_estimates e\n            LEFT JOIN (\n                SELECT Site_ID, Equipment_Tag, System_Code,\n                       SUM(Total_Hours) AS Actual_Manhours\n                FROM mh_timesheets\n                GROUP BY Site_ID, Equipment_Tag, System_Code\n            ) a ON a.Site_ID = e.Site_ID\n               AND a.Equipment_Tag = e.Equipment_Tag\n               AND a.System_Code = e.System_Code\n            LEFT JOIN (\n                SELECT Site_ID, Equipment_Tag, System_Code,\n                       SUM(SQM_Done) AS SQM_Done\n                FROM mh_production\n                GROUP BY Site_ID, Equipment_Tag, System_Code\n            ) p ON p.Site_ID = e.Site_ID\n               AND p.Equipment_Tag = e.Equipment_Tag\n               AND p.System_Code = e.System_Code\n            LEFT JOIN mh_variance_notes n\n                   ON n.Site_ID = e.Site_ID\n                  AND n.Equipment_Tag = e.Equipment_Tag\n                  AND n.System_Code = e.System_Code',
    'v_site_stock': "CREATE VIEW v_site_stock AS\n            WITH activity AS (\n                SELECT TRIM(SAP_Code) AS SAP_Code, COALESCE(Site_ID,'HQ') AS Site_ID,\n                       SUM(Quantity) AS rec, 0 AS con, 0 AS ret\n                FROM receipts    GROUP BY TRIM(SAP_Code), COALESCE(Site_ID,'HQ')\n                UNION ALL\n                SELECT TRIM(SAP_Code), COALESCE(Site_ID,'HQ'),\n                       0, SUM(Quantity), 0\n                FROM consumption GROUP BY TRIM(SAP_Code), COALESCE(Site_ID,'HQ')\n                UNION ALL\n                SELECT TRIM(SAP_Code), COALESCE(Site_ID,'HQ'),\n                       0, 0, SUM(Quantity)\n                FROM returns     GROUP BY TRIM(SAP_Code), COALESCE(Site_ID,'HQ')\n            )\n            SELECT\n                a.SAP_Code                         AS SAP_Code,\n                a.Site_ID                          AS Site_ID,\n                i.Equipment_Description            AS Equipment_Description,\n                i.Material_Code                    AS Material_Code,\n                i.UOM                              AS UOM,\n                COALESCE(i.Minimum_Qty, 0)         AS Minimum_Qty,\n                SUM(a.rec)                         AS Total_Received,\n                SUM(a.con)                         AS Total_Consumed,\n                SUM(a.ret)                         AS Total_Returned,\n                SUM(a.rec) - SUM(a.con) - SUM(a.ret) AS Current_Stock\n            FROM activity a\n            LEFT JOIN inventory i ON TRIM(i.SAP_Code) = a.SAP_Code\n            GROUP BY a.SAP_Code, a.Site_ID",
    'v_supplier_activity': "CREATE VIEW v_supplier_activity AS\n            SELECT\n                TRIM(r.Supplier)                   AS Supplier,\n                COALESCE(r.Site_ID, 'HQ')          AS Site_ID,\n                COUNT(*)                           AS Receipt_Count,\n                COUNT(DISTINCT TRIM(r.SAP_Code))   AS Distinct_Items,\n                SUM(r.Quantity)                    AS Total_Received,\n                MIN(r.Date)                        AS First_Receipt_Date,\n                MAX(r.Date)                        AS Last_Receipt_Date\n            FROM receipts r\n            WHERE r.Supplier IS NOT NULL AND TRIM(r.Supplier) != ''\n            GROUP BY TRIM(r.Supplier), COALESCE(r.Site_ID, 'HQ')",
}
