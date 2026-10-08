"""
backend/api/services/ledger.py — ledger write services (async, Postgres).

Faithful ports of the Streamlit app's write logic in database.py:
  * post_receipt   — mirrors process_receipt_delivery() (database.py:5062):
        auto lot-number when an expiry is given, mirror into the `lots` master,
        PR-fulfilment auto-close, all in one transaction.
  * write_audit    — mirrors log_audit_action() (database.py:5375) → system_audit_log.
  * auto_generate_lot_number — identical formula to database.py:7818.

The caller (router) owns the transaction boundary (`async with session.begin()`),
so these compose and roll back together on any error.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys

from sqlalchemy import LargeBinary, delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

# Ensure `backend.models` importable regardless of launch context.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from backend import models  # noqa: E402

from ..stock import SQL_LOT_BALANCE  # noqa: E402  (parity-tested lot-balance SQL)

_MD = models.Base.metadata
receipts_t = _MD.tables["receipts"]
consumption_t = _MD.tables["consumption"]
returns_t = _MD.tables["returns"]
lots_t = _MD.tables["lots"]
inventory_t = _MD.tables["inventory"]
pr_master_t = _MD.tables["pr_master"]
adjustments_t = _MD.tables["stock_adjustments"]
audit_t = _MD.tables["system_audit_log"]
# Staging tables (SK submits → HOD approves → committed to the ledger).
pending_receipts_t = _MD.tables["pending_receipts"]
pending_issues_t = _MD.tables["pending_issues"]
pending_returns_t = _MD.tables["pending_returns"]

PENDING = "pending_hod"

# Trace/logistics columns to carry from a staged pending_receipt onto the
# committed receipt (e.g. DN_Number / PO_Number_Source / Warehouse_ID from a
# warehouse delivery). = receipts columns ∩ pending_receipts columns, minus the
# ones post_receipt already handles, minus blobs.
_POST_RECEIPT_BASE = {"id", "Date", "SAP_Code", "Quantity", "Supplier",
                      "Remarks", "Site_ID", "Expiry_Date", "PR_Number", "Lot_Number"}
_RECEIPT_TRACE_COLS = {
    c.name for c in receipts_t.columns
    if c.name not in _POST_RECEIPT_BASE and not isinstance(c.type, LargeBinary)
} & {c.name for c in pending_receipts_t.columns}

# Stock-adjustment reason codes — identical to database.py:61 (ADJUSTMENT_REASONS).
ADJUSTMENT_REASONS = {
    "cycle_count": "Cycle count correction",
    "damaged": "Damaged / unusable",
    "expired_disposal": "Expired — disposed",
    "miscount_in": "Miscount — found extra",
    "miscount_out": "Miscount — short",
    "lost": "Lost / unaccounted",
    "theft": "Suspected theft",
    "return_to_supplier": "Returned to supplier",
    "other": "Other (see notes)",
}

# FEFO lot picker: earliest-expiry OPEN lot with remaining qty (ports
# suggest_fefo_lot_for_consumption + get_lots_for_item ordering).
_FEFO_PICK = f"""
SELECT "Lot_Number" FROM ({SQL_LOT_BALANCE}) lb
WHERE "SAP_Code" = :sap AND "Site_ID" = :site
  AND "Status" = 'open' AND "Remaining_Qty" > 0
-- Phase 16c: an EXPIRED lot goes LAST, not first. Earliest-expiry-first would
-- otherwise suggest exactly the lot nobody should use. Still pickable by hand
-- (FEFO is allow-and-log, never a block) — just never the default.
ORDER BY CASE WHEN "Expiry_Date" IS NULL OR "Expiry_Date" = '' THEN 1
              WHEN SUBSTRING("Expiry_Date" FROM 1 FOR 10) < TO_CHAR(CURRENT_DATE, 'YYYY-MM-DD') THEN 2
              ELSE 0 END,
         "Expiry_Date" ASC, "Received_Date" ASC
LIMIT 1
"""

# Current site stock for one SAP (identity: received − consumed − returned).
_SITE_STOCK_ONE = """
SELECT
  COALESCE((SELECT SUM("Quantity") FROM receipts    WHERE TRIM("SAP_Code")=:sap AND COALESCE("Site_ID",'HQ')=:site),0)
- COALESCE((SELECT SUM("Quantity") FROM consumption WHERE TRIM("SAP_Code")=:sap AND COALESCE("Site_ID",'HQ')=:site),0)
- COALESCE((SELECT SUM("Quantity") FROM returns     WHERE TRIM("SAP_Code")=:sap AND COALESCE("Site_ID",'HQ')=:site),0)
"""


def auto_generate_lot_number(received_date: str, sap_code: str) -> str:
    """Identical to database.py:7818 — LOT-<YYYYMMDD>-<SAP_Code>."""
    safe_date = (received_date or "").replace("-", "")
    return f"LOT-{safe_date}-{(sap_code or '').strip()}"


async def write_audit(session: AsyncSession, username: str, action_type: str,
                      target_table: str, details: str) -> None:
    """Append one immutable row to system_audit_log (ports log_audit_action)."""
    await session.execute(insert(audit_t).values(
        username=username, action_type=action_type,
        target_table=target_table, details=details))


async def attach_material_names(session: AsyncSession, items: list[dict], *,
                                sap_key: str = "SAP_Code",
                                code_key: str | None = None) -> list[dict]:
    """Fill `Equipment_Description` (and the missing code) in-place from the
    inventory master, for lists that carry only an identifier.

    2026-08-05. A screen showing a bare `GI-6000012` or `1169` asks the reader
    to know 452 codes by heart, so every list that names a material names it in
    words too. Written as ONE helper rather than a join per endpoint because the
    lists differ in which identifier they hold — lots and burn-rate carry the
    SAP, PO returns carry the `Material_Code` — and both need the same answer.

    Matched on TRIM, like every other SAP comparison in this file: the workbook
    ships padded codes. Rows with no master entry keep a NULL description rather
    than being dropped; the caller is showing history and an unmatched row is
    the one most worth seeing.
    """
    if not items:
        return items
    lookup_by_code = code_key is not None and not any(i.get(sap_key) for i in items)
    key = code_key if lookup_by_code else sap_key
    wanted = {str(i[key]).strip() for i in items if i.get(key)}
    if not wanted:
        return items
    col = inventory_t.c["Material_Code" if lookup_by_code else "SAP_Code"]
    rows = (await session.execute(
        select(func.trim(col).label("k"), inventory_t.c["SAP_Code"],
               inventory_t.c["Material_Code"],
               inventory_t.c["Equipment_Description"], inventory_t.c["UOM"])
        .where(func.trim(col).in_(wanted)))).mappings().all()
    master = {r["k"]: r for r in rows}
    for i in items:
        m = master.get(str(i.get(key) or "").strip())
        i["Equipment_Description"] = m["Equipment_Description"] if m else None
        i.setdefault("UOM", m["UOM"] if m else None)
        for other in ("SAP_Code", "Material_Code"):
            if not i.get(other):
                i[other] = m[other] if m else None
    return items


async def sap_exists(session: AsyncSession, sap_code: str) -> bool:
    stmt = select(func.count()).select_from(inventory_t).where(
        func.trim(inventory_t.c["SAP_Code"]) == (sap_code or "").strip())
    return (await session.execute(stmt)).scalar_one() > 0


async def post_receipt(session: AsyncSession, *, username: str, data: dict) -> dict:
    """Post a goods receipt to the permanent ledger. Ports process_receipt_delivery.

    `data` is a validated dict with base fields + optional `extra` (logistics
    columns already validated against the receipts schema by the router).
    Returns {receipt_id, lot_number, pr_status, message}.
    """
    sap = data["SAP_Code"].strip()
    date = data["Date"]
    qty = float(data["Quantity"])
    site = data["Site_ID"]
    supplier = data.get("Supplier") or None
    remarks = data.get("Remarks") or None
    expiry = data.get("Expiry_Date") or None
    pr = data.get("PR_Number") or None
    from . import lots as LOTS
    lot = LOTS.norm_lot(data.get("Lot_Number")) or ""
    mfd = (data.get("MFD_Date") or "")[:10] or None
    extra = data.get("extra") or {}
    # Phase 16c — where this lot's expiry comes from: typed on the form ('app'),
    # or MFD + the item's shelf life ('derived', ruling Q16-5).
    exp_source = "app" if expiry else None
    if not expiry and mfd:
        life = (await session.execute(select(inventory_t.c["Shelf_Life_Months"]).where(
            func.trim(inventory_t.c["SAP_Code"]) == sap))).scalar()
        if life:
            from .lot_file import add_months
            expiry, exp_source = add_months(mfd, int(life)), "derived"

    # Auto-generate a lot only for expiry-tracked items (same rule as the app).
    if not lot and expiry:
        lot = auto_generate_lot_number(date, sap)

    values = {
        "Date": date, "SAP_Code": sap, "Quantity": qty, "Supplier": supplier,
        "Remarks": remarks, "Site_ID": site, "Expiry_Date": expiry,
        "PR_Number": pr, "Lot_Number": lot or None,
    }
    values.update(extra)

    new_id = (await session.execute(
        insert(receipts_t).values(**values).returning(receipts_t.c["id"]))).scalar_one()

    # Mirror into the lots master so FEFO can see it (idempotent existence check).
    if lot:
        exists = (await session.execute(
            select(func.count()).select_from(lots_t).where(
                (lots_t.c["Lot_Number"] == lot)
                & (lots_t.c["SAP_Code"] == sap)
                & (lots_t.c["Site_ID"] == site)))).scalar_one()
        if not exists:
            await session.execute(insert(lots_t).values(
                Lot_Number=lot, SAP_Code=sap, Site_ID=site, Received_Date=date,
                Expiry_Date=expiry, Supplier=supplier, PR_Number=pr, Status="open",
                MFD_Date=mfd, Expiry_Source=exp_source, Source="app"))
        else:
            # a known lot arriving again: fill what it was missing, never overwrite
            await session.execute(update(lots_t).where(
                (lots_t.c["Lot_Number"] == lot) & (lots_t.c["SAP_Code"] == sap)
                & (lots_t.c["Site_ID"] == site)).values(
                MFD_Date=func.coalesce(lots_t.c["MFD_Date"], mfd),
                Expiry_Source=func.coalesce(lots_t.c["Expiry_Source"],
                                            exp_source if expiry else None),
                Expiry_Date=func.coalesce(lots_t.c["Expiry_Date"], expiry)))

    # PR fulfilment: close the PR line when cumulative received >= requested.
    pr_status = None
    if pr:
        req = (await session.execute(
            select(func.sum(pr_master_t.c["Requested_Qty"])).where(
                (pr_master_t.c["PR_Number"] == pr)
                & (pr_master_t.c["SAP_Code"] == sap)
                & (pr_master_t.c["Site_ID"] == site)))).scalar_one()
        if req is not None:
            rec = (await session.execute(
                select(func.sum(receipts_t.c["Quantity"])).where(
                    (receipts_t.c["PR_Number"] == pr)
                    & (receipts_t.c["SAP_Code"] == sap)
                    & (receipts_t.c["Site_ID"] == site)))).scalar_one() or 0
            if float(rec) >= float(req):
                await session.execute(update(pr_master_t).where(
                    (pr_master_t.c["PR_Number"] == pr)
                    & (pr_master_t.c["SAP_Code"] == sap)
                    & (pr_master_t.c["Site_ID"] == site)).values(status="closed"))
                pr_status = f"PR {pr} fulfilled and closed"
            else:
                pr_status = f"PR {pr} balance: {float(req) - float(rec):g} remaining"

    await write_audit(session, username, "POST_RECEIPT", "receipts",
                      f"id={new_id} sap={sap} site={site} qty={qty:g} lot={lot or '-'}")

    return {"receipt_id": new_id, "lot_number": lot or None,
            "pr_status": pr_status, "message": "Receipt posted"}


async def fefo_lot(session: AsyncSession, sap: str, site: str) -> str | None:
    """Earliest-expiry open lot to consume from first (None → un-lotted item)."""
    row = (await session.execute(text(_FEFO_PICK), {"sap": sap.strip(), "site": site})).first()
    return row[0] if row else None


async def _site_available(session: AsyncSession, sap: str, site: str) -> float:
    val = (await session.execute(text(_SITE_STOCK_ONE), {"sap": sap.strip(), "site": site})).scalar_one()
    return float(val or 0)


async def post_consumption(session: AsyncSession, *, username: str, data: dict) -> dict:
    """Post a material issue (consumption). Ports the staging→consumption write.

    FEFO: when no explicit Lot_Number is given, tag the earliest-expiry open lot
    (suggest_fefo_lot_for_consumption). ALLOW-AND-LOG: consumption exceeding
    available stock is permitted and recorded (locked FEFO decision), returned as
    a `warning` rather than blocked.
    """
    sap = data["SAP_Code"].strip()
    site = data["Site_ID"]
    qty = float(data["Quantity"])
    from . import lots as LOTS
    lot = LOTS.norm_lot(data.get("Lot_Number")) or ""
    # Phase 16c — a ROLL names its batch: the roll register knows it
    if not lot and data.get("Serial_No"):
        if (await LOTS.tracking(session)).get(sap) == "roll":
            roll = LOTS.norm_roll(data.get("Serial_No"))
            data["Serial_No"] = roll
            lot = (await LOTS.roll_register(session)).get((sap, roll)) \
                or LOTS.roll_batch(roll) or ""
    if not lot:
        lot = await fefo_lot(session, sap, site)

    avail = await _site_available(session, sap, site)
    warning = (f"issue {qty:g} exceeds available {avail:g} at {site} "
               f"(allowed & logged)") if qty > avail else None

    values = {
        "Date": data["Date"], "SAP_Code": sap, "Quantity": qty, "Site_ID": site,
        "Work_Type": data.get("Work_Type") or None,
        "Issued_To": data.get("Issued_To") or None,
        "Issued_By": data.get("Issued_By") or username,
        "Prepared_By": data.get("Prepared_By") or None,          # Phase 22e
        "PR_Number": data.get("PR_Number") or None,
        "Tank_No": data.get("Tank_No") or None,
        "Serial_No": data.get("Serial_No") or None,
        "Remarks": data.get("Remarks") or None,
        "Requested_By": data.get("Requested_By") or None,
        "FEFO_Override": data.get("FEFO_Override") or None,
        "Lot_Number": lot or None,
    }
    new_id = (await session.execute(
        insert(consumption_t).values(**values).returning(consumption_t.c["id"]))).scalar_one()

    await write_audit(session, username, "POST_CONSUMPTION", "consumption",
                      f"id={new_id} sap={sap} site={site} qty={qty:g} "
                      f"lot={lot or '-'}" + (" OVERDRAW" if warning else ""))
    return {"consumption_id": new_id, "lot_number": lot or None,
            "warning": warning, "message": "Consumption posted"}


async def post_return(session: AsyncSession, *, username: str, data: dict) -> dict:
    """Post a return to the `returns` ledger (reduces stock). Ports approve_return_request."""
    sap = data["SAP_Code"].strip()
    site = data["Site_ID"]
    qty = float(data["Quantity"])
    # Phase 16: the lot it gives back to (returns had no lot column before).
    from . import lots as LOTS
    new_id = (await session.execute(insert(returns_t).values(
        Date=data["Date"], SAP_Code=sap, Quantity=qty,
        Reason=data.get("Reason") or None, Remarks=data.get("Remarks") or None,
        Lot_Number=LOTS.norm_lot(data.get("Lot_Number")),
        Site_ID=site).returning(returns_t.c["id"]))).scalar_one()
    await write_audit(session, username, "POST_RETURN", "returns",
                      f"id={new_id} sap={sap} site={site} qty={qty:g} "
                      f"reason={data.get('Reason') or '-'}")
    return {"return_id": new_id, "message": "Return posted"}


# ===========================================================================
# STAGING (Store Keeper submits) → pending_* / stock_adjustments (pending_hod).
# The post_* functions above are the COMMIT step, reused by the approvals below.
# ===========================================================================
async def stage_receipt(session: AsyncSession, *, username: str, data: dict) -> dict:
    sap = data["SAP_Code"].strip()
    values = {
        "Date": data["Date"], "SAP_Code": sap, "Quantity": float(data["Quantity"]),
        "Supplier": data.get("Supplier") or None, "Remarks": data.get("Remarks") or None,
        "Site_ID": data["Site_ID"], "Expiry_Date": data.get("Expiry_Date") or None,
        "PR_Number": data.get("PR_Number") or None,
        "Lot_Number": (data.get("Lot_Number") or "").strip() or None,
        "MFD_Date": (data.get("MFD_Date") or "")[:10] or None,   # Phase 16c
        "wbs": (data.get("wbs") or "").strip() or None,          # parity A4
        "Bin_Location": (data.get("Bin_Location") or "").strip() or None,  # parity B5
        "status": PENDING,
    }
    values.update(data.get("extra") or {})
    pid = (await session.execute(insert(pending_receipts_t).values(**values)
           .returning(pending_receipts_t.c["id"]))).scalar_one()
    await write_audit(session, username, "STAGE_RECEIPT", "pending_receipts",
                      f"id={pid} sap={sap} site={data['Site_ID']} qty={float(data['Quantity']):g}")
    return {"pending_id": pid, "status": PENDING, "message": "Receipt submitted for HOD approval"}


async def stage_consumption(session: AsyncSession, *, username: str, data: dict) -> dict:
    """Stage an issue for HOD approval.

    QSEP: this is one of the TWO mouths of the issue path, and the quality
    gate has to sit in both. The other is `supervisor.approve_smr`, which
    inserts into `pending_issues` directly and would walk straight past a
    guard placed only here.

    QSEP slice 4 also makes this the PPE handover: when the material is PPE,
    `ppe.validate_issue` demands the employee and the safety approval, and a
    `ppe_distributions` row is written beside the staged issue. Option A —
    there is ONE issue form, and PPE gets no parallel stock ledger. The
    quantity leaves the shelf through pending_issues → consumption exactly
    like everything else, so stock, FEFO, burn rate and the QC gate need no
    PPE-shaped exception.

    Phase 9a adds the third and fourth gate on the same reasoning: the work
    type must be on the site's canonical list (`wbs.assert_work_type`) and the
    WBS is resolved from it (`wbs.resolve_wbs`) when the form did not name one.
    Both are CONDITIONAL — a site with no list and no WBS numbers behaves
    exactly as it did before Phase 9 — and both belong here rather than in the
    router for the same reason the quality gates do.

    All imports here are deliberately lazy — `quality`, `ppe` and `wbs` import
    this module for its metadata and `write_audit`, so a top-level import would
    be a cycle. Same pattern as `notifications.digest_loop`'s lazy
    `db.SessionLocal`.
    """
    from . import ppe, quality
    from . import wbs as wbs_svc

    sap = data["SAP_Code"].strip()
    # BOTH halves of the issue gate, in the order that gives the most useful
    # refusal. Paperwork first: a missing certificate is fixed by a different
    # person (Logistics) than a missing inspection (the QC), and it is the one
    # the store keeper can chase immediately.
    await quality.assert_mtc_for_issue(
        session, sap_code=sap, site_id=data["Site_ID"], actor=username)
    await quality.assert_qc_cleared(
        session, sap_code=sap, site_id=data["Site_ID"],
        qty=float(data["Quantity"]), lot=(data.get("Lot_Number") or None),
        actor=username)
    # Validated BEFORE anything is written, so a bad PPE line raises while
    # there is still nothing to unwind. Returns None for every non-PPE
    # material, which is what leaves the ordinary issue path untouched.
    ppe_plan = await ppe.validate_issue(session, data=data)

    # ── Phase 9a: the work type, then the WBS it charges to ─────────────────
    # Placed HERE for the reason the QSEP gates are: this is one of the two
    # mouths of the issue path, and a rule written only in the router would be
    # walked past by `supervisor.approve_smr`. Both are conditional — a site
    # with no canonical list and no WBS numbers is completely unaffected.
    site_id = data["Site_ID"]
    await wbs_svc.assert_work_type(session, site_id=site_id,
                                   work_type=data.get("Work_Type"))
    # Store the HOD's casing, not the caller's. This is what stops the ledger
    # re-growing the `civil`/`Civil` split one entry at a time.
    work_type = await wbs_svc.display_spelling(session, site_id=site_id,
                                               work_type=data.get("Work_Type"))
    charge = await wbs_svc.resolve_wbs(
        session, site_id=site_id, work_type=work_type,
        equipment_tag=data.get("Tank_No"), explicit=data.get("wbs"))
    # ⚠️ ASSERTED ON THE RESOLVED VALUE, AND ONLY AFTER RESOLVING. Checking the
    # form's raw `wbs` first — which is what the router used to do — would
    # reject every issue that left the box blank for want of the very number
    # the work-type map was about to supply, making the map unreachable.
    await wbs_svc.assert_wbs(session, site_id=site_id, wbs=charge["wbs"])

    values = {
        "Date": data["Date"], "SAP_Code": sap, "Quantity": float(data["Quantity"]),
        "Work_Type": work_type or None, "Issued_To": data.get("Issued_To") or None,
        "Issued_By": data.get("Issued_By") or username, "PR_Number": data.get("PR_Number") or None,
        "Prepared_By": (data.get("Prepared_By") or "").strip() or None,   # Phase 22e
        "Tank_No": data.get("Tank_No") or None, "Serial_No": data.get("Serial_No") or None,
        "Remarks": data.get("Remarks") or None, "Requested_By": data.get("Requested_By") or None,
        "Lot_Number": (data.get("Lot_Number") or "").strip() or None,
        "FEFO_Override": data.get("FEFO_Override") or None,
        "wbs": charge["wbs"],                    # parity A4 + Phase 9a resolution
        "Site_ID": data["Site_ID"], "status": PENDING,
    }
    pid = (await session.execute(insert(pending_issues_t).values(**values)
           .returning(pending_issues_t.c["id"]))).scalar_one()
    # The WBS SOURCE goes in the audit line, not in a column. A wrong cost
    # centre is a question about who chose it, and "the work-type map did"
    # reads very differently from "the store keeper did" — but only one of the
    # two is worth a schema change to keep for every row forever.
    await write_audit(session, username, "STAGE_ISSUE", "pending_issues",
                      f"id={pid} sap={sap} site={data['Site_ID']} qty={float(data['Quantity']):g}"
                      + (f" wbs={charge['wbs']}({charge['source']})"
                         if charge["wbs"] else ""))
    out = {"pending_id": pid, "status": PENDING,
           "message": "Issue submitted for HOD approval"}
    if ppe_plan is not None:
        # Written at STAGE, not at approval. The boots are on the worker's
        # feet the moment the SK hands them over, and if the record only
        # appeared on the HOD's approval a second pair could be staged in
        # the gap and both would pass the duplicate check. A rejection voids
        # the row (ppe.void_rejected), so nothing is stranded.
        did = await ppe.record_distribution(session, plan=ppe_plan, data=data,
                                            pending_id=pid, username=username)
        out["ppe_distribution_id"] = did
        out["ppe_expires_on"] = ppe_plan["expires_on"]
        out["message"] = (
            f"Issue submitted for HOD approval · PPE recorded against "
            f"{ppe_plan['employee_name']}"
            + (f", replace by {ppe_plan['expires_on']}" if ppe_plan["expires_on"]
               else " (no usable time configured for this item)"))
    return out


async def stage_return(session: AsyncSession, *, username: str, data: dict) -> dict:
    sap = data["SAP_Code"].strip()
    values = {
        "Site_ID": data["Site_ID"], "SAP_Code": sap, "Quantity": float(data["Quantity"]),
        "Return_Reason": data.get("Reason") or "return",
        "Return_DN_No": data.get("Return_DN_No") or "",
        "PR_Number": data.get("PR_Number") or None,
        "Lot_Number": data.get("Lot_Number") or None,
        # parity A2: source-receipt provenance + the 30-day-window override.
        # (override_reason used to piggyback Remarks — the real justification
        # now wins; Remarks only lands here when no override is in play.)
        "received_date": data.get("received_date") or None,
        "received_dn_no": data.get("received_dn_no") or None,
        "received_qty": data.get("received_qty"),
        "override_required": 1 if data.get("override_reason") else 0,
        "override_reason": data.get("override_reason") or data.get("Remarks") or "",
        "status": PENDING, "submitted_by": username,
    }
    pid = (await session.execute(insert(pending_returns_t).values(**values)
           .returning(pending_returns_t.c["id"]))).scalar_one()
    await write_audit(session, username, "STAGE_RETURN", "pending_returns",
                      f"id={pid} sap={sap} site={data['Site_ID']} qty={float(data['Quantity']):g}")
    return {"pending_id": pid, "status": PENDING, "message": "Return submitted for HOD approval"}


async def stage_adjustment(session: AsyncSession, *, username: str, data: dict) -> dict:
    sap = data["SAP_Code"].strip()
    site = data["Site_ID"]
    variance = float(data["counted_qty"]) - float(data["system_qty"])
    adj_id = (await session.execute(insert(adjustments_t).values(
        Site_ID=site, SAP_Code=sap, system_qty=float(data["system_qty"]),
        counted_qty=float(data["counted_qty"]), variance=variance,
        reason_code=data["reason_code"], notes=(data.get("notes") or "").strip(),
        status=PENDING, submitted_by=username,
        Lot_Number=(data.get("Lot_Number") or "").strip() or None,
    ).returning(adjustments_t.c["id"]))).scalar_one()
    await write_audit(session, username, "SUBMIT_ADJUSTMENT", "stock_adjustments",
                      f"id={adj_id} sap={sap} site={site} var={variance:+g} reason={data['reason_code']}")
    return {"pending_id": adj_id, "status": PENDING, "message": "Adjustment submitted for HOD approval"}


# ===========================================================================
# APPROVE (HOD commits) — write the ledger via post_* then retire the pending row.
# REJECT — mark the pending row rejected (no ledger write).
# ===========================================================================
async def _load_pending(session: AsyncSession, table, pid: int) -> dict | None:
    row = (await session.execute(select(table).where(
        (table.c["id"] == pid) & (table.c["status"] == PENDING)))).mappings().first()
    return dict(row) if row else None


async def commit_receipt(session: AsyncSession, *, approver: str, pending_id: int) -> dict:
    row = await _load_pending(session, pending_receipts_t, pending_id)
    if row is None:
        return {"error": "not found or already handled"}
    # Carry DN/PO/warehouse trace fields onto the committed receipt.
    data = dict(row)
    extra = {k: row[k] for k in _RECEIPT_TRACE_COLS if row.get(k) is not None}
    if row.get("wbs"):
        extra["WBS"] = row["wbs"]          # pending 'wbs' → ledger 'WBS' (parity A4)
    if row.get("Bin_Location"):
        extra["Bin_Location"] = row["Bin_Location"]
    if extra:
        data["extra"] = {**(data.get("extra") or {}), **extra}
    res = await post_receipt(session, username=approver, data=data)
    await session.execute(delete(pending_receipts_t).where(pending_receipts_t.c["id"] == pending_id))
    return {"committed": True, **res}


async def commit_consumption(session: AsyncSession, *, approver: str, pending_id: int) -> dict:
    from . import ppe                       # lazy: ppe imports this module

    row = await _load_pending(session, pending_issues_t, pending_id)
    if row is None:
        return {"error": "not found or already handled"}
    res = await post_consumption(session, username=approver, data=row)
    if row.get("wbs"):                      # pending 'wbs' → ledger 'WBS' (parity A4)
        await session.execute(update(consumption_t)
                              .where(consumption_t.c["id"] == res.get("consumption_id", res.get("id", -1)))
                              .values(WBS=row["wbs"]))
    # QSEP — bind a PPE distribution staged alongside this issue to the ledger
    # row it became. Done BEFORE the delete below, because the pending id is
    # the only handle the two share. No-op for non-PPE issues.
    await ppe.link_committed(session, pending_id=pending_id,
                             consumption_id=res.get("consumption_id"))
    await session.execute(delete(pending_issues_t).where(pending_issues_t.c["id"] == pending_id))
    return {"committed": True, **res}


async def commit_return(session: AsyncSession, *, approver: str, pending_id: int) -> dict:
    row = await _load_pending(session, pending_returns_t, pending_id)
    if row is None:
        return {"error": "not found or already handled"}
    data = {"Date": _dt.date.today().isoformat(), "SAP_Code": row["SAP_Code"],
            "Quantity": row["Quantity"], "Site_ID": row["Site_ID"],
            "Reason": row.get("Return_Reason"), "Lot_Number": row.get("Lot_Number"),
            "Remarks": f"Return DN: {row.get('Return_DN_No') or ''} · approved by {approver}".strip()}
    res = await post_return(session, username=approver, data=data)
    await session.execute(update(pending_returns_t).where(pending_returns_t.c["id"] == pending_id)
                          .values(status="approved", approved_by=approver, approved_at=func.now()))
    return {"committed": True, **res}


async def commit_adjustment(session: AsyncSession, *, approver: str, pending_id: int) -> dict:
    row = await _load_pending(session, adjustments_t, pending_id)
    if row is None:
        return {"error": "not found or already handled"}
    sap, site = row["SAP_Code"], row["Site_ID"]
    variance = float(row["variance"])
    reason, notes, lot = row["reason_code"], row.get("notes") or "", row.get("Lot_Number")
    today = _dt.date.today().isoformat()
    remark = f"adj#{pending_id} reason={reason} · {notes}".strip(" ·")
    if variance < 0:
        cvals = {"Date": today, "SAP_Code": sap, "Quantity": abs(variance), "Site_ID": site,
                 "Work_Type": "STOCK_ADJUSTMENT", "Remarks": remark,
                 "Issued_By": approver, "Issued_To": "ADJUSTMENT"}
        if lot:
            cvals["Lot_Number"] = lot
        cid = (await session.execute(insert(consumption_t).values(**cvals)
               .returning(consumption_t.c["id"]))).scalar_one()
        posted = f"C:{cid}"
    else:
        rvals = {"Date": today, "SAP_Code": sap, "Quantity": variance, "Site_ID": site,
                 "Supplier": "STOCK_ADJUSTMENT", "Remarks": remark}
        rid = (await session.execute(insert(receipts_t).values(**rvals)
               .returning(receipts_t.c["id"]))).scalar_one()
        posted = f"R:{rid}"
    await session.execute(update(adjustments_t).where(adjustments_t.c["id"] == pending_id)
                          .values(status="approved", approved_by=approver,
                                  approved_at=func.now(), posted_txn_ref=posted))
    if lot:
        await session.execute(update(lots_t).where(
            (lots_t.c["Lot_Number"] == lot) & (lots_t.c["SAP_Code"] == sap)
            & (lots_t.c["Site_ID"] == site)).values(Status="disposed"))
    await write_audit(session, approver, "APPROVE_ADJUSTMENT", "stock_adjustments",
                      f"id={pending_id} sap={sap} site={site} var={variance:+g} posted={posted}"
                      + (f" lot={lot}→disposed" if lot else ""))
    return {"committed": True, "adjustment_id": pending_id, "variance": variance, "posted": posted}


_REJECT = {
    "receipt": pending_receipts_t,
    "issue": pending_issues_t,
    "return": pending_returns_t,
    "adjustment": adjustments_t,
}


async def reject_pending(session: AsyncSession, *, approver: str, kind: str,
                         pending_id: int, reason: str = "") -> dict:
    table = _REJECT[kind]
    vals: dict = {"status": "rejected"}
    if "rejection_reason" in table.c:
        vals["rejection_reason"] = reason or ""
    if "approved_by" in table.c:
        vals["approved_by"] = approver
    if "approved_at" in table.c:
        vals["approved_at"] = func.now()
    res = await session.execute(update(table).where(
        (table.c["id"] == pending_id) & (table.c["status"] == PENDING)).values(**vals))
    if res.rowcount == 0:
        return {"error": "not found or already handled"}
    voided = 0
    if kind == "issue":
        # QSEP — void any PPE distribution staged alongside this issue, and
        # restore the pair it replaced. Without the restore the worker would
        # hold nothing on record while still wearing the old boots.
        from . import ppe                   # lazy: ppe imports this module
        voided = await ppe.void_rejected(session, pending_id=pending_id)
    await write_audit(session, approver, f"REJECT_{kind.upper()}", table.name,
                      f"id={pending_id} reason={reason or '-'}"
                      + (" ppe_voided=1" if voided else ""))
    return {"rejected": True, "id": pending_id, "ppe_voided": voided}
