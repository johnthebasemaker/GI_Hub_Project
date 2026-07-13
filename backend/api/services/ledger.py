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
ORDER BY CASE WHEN "Expiry_Date" IS NULL OR "Expiry_Date" = '' THEN 1 ELSE 0 END,
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
    lot = (data.get("Lot_Number") or "").strip()
    extra = data.get("extra") or {}

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
                Expiry_Date=expiry, Supplier=supplier, PR_Number=pr, Status="open"))

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
    lot = (data.get("Lot_Number") or "").strip()
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
    new_id = (await session.execute(insert(returns_t).values(
        Date=data["Date"], SAP_Code=sap, Quantity=qty,
        Reason=data.get("Reason") or None, Remarks=data.get("Remarks") or None,
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
    sap = data["SAP_Code"].strip()
    values = {
        "Date": data["Date"], "SAP_Code": sap, "Quantity": float(data["Quantity"]),
        "Work_Type": data.get("Work_Type") or None, "Issued_To": data.get("Issued_To") or None,
        "Issued_By": data.get("Issued_By") or username, "PR_Number": data.get("PR_Number") or None,
        "Tank_No": data.get("Tank_No") or None, "Serial_No": data.get("Serial_No") or None,
        "Remarks": data.get("Remarks") or None, "Requested_By": data.get("Requested_By") or None,
        "Lot_Number": (data.get("Lot_Number") or "").strip() or None,
        "FEFO_Override": data.get("FEFO_Override") or None,
        "wbs": (data.get("wbs") or "").strip() or None,          # parity A4
        "Site_ID": data["Site_ID"], "status": PENDING,
    }
    pid = (await session.execute(insert(pending_issues_t).values(**values)
           .returning(pending_issues_t.c["id"]))).scalar_one()
    await write_audit(session, username, "STAGE_ISSUE", "pending_issues",
                      f"id={pid} sap={sap} site={data['Site_ID']} qty={float(data['Quantity']):g}")
    return {"pending_id": pid, "status": PENDING, "message": "Issue submitted for HOD approval"}


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
    row = await _load_pending(session, pending_issues_t, pending_id)
    if row is None:
        return {"error": "not found or already handled"}
    res = await post_consumption(session, username=approver, data=row)
    if row.get("wbs"):                      # pending 'wbs' → ledger 'WBS' (parity A4)
        await session.execute(update(consumption_t)
                              .where(consumption_t.c["id"] == res.get("consumption_id", res.get("id", -1)))
                              .values(WBS=row["wbs"]))
    await session.execute(delete(pending_issues_t).where(pending_issues_t.c["id"] == pending_id))
    return {"committed": True, **res}


async def commit_return(session: AsyncSession, *, approver: str, pending_id: int) -> dict:
    row = await _load_pending(session, pending_returns_t, pending_id)
    if row is None:
        return {"error": "not found or already handled"}
    data = {"Date": _dt.date.today().isoformat(), "SAP_Code": row["SAP_Code"],
            "Quantity": row["Quantity"], "Site_ID": row["Site_ID"],
            "Reason": row.get("Return_Reason"),
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
    await write_audit(session, approver, f"REJECT_{kind.upper()}", table.name,
                      f"id={pending_id} reason={reason or '-'}")
    return {"rejected": True, "id": pending_id}
