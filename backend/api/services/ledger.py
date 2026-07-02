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

import os
import sys

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

# Ensure `backend.models` importable regardless of launch context.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from backend import models  # noqa: E402

_MD = models.Base.metadata
receipts_t = _MD.tables["receipts"]
lots_t = _MD.tables["lots"]
inventory_t = _MD.tables["inventory"]
pr_master_t = _MD.tables["pr_master"]
audit_t = _MD.tables["system_audit_log"]


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
