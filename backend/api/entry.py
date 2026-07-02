"""
backend/api/entry.py — data-entry endpoints (ledger writes) for the new UI.

Thin HTTP layer over backend/api/services/ledger.py. Owns the transaction
boundary and input validation; the business rules live in the service.

  POST /entry/*  — stage a receipt/issue/return/adjustment (status=pending_hod) for
                 HOD approval; the HOD portal commits them to the ledger.

Actor: the acting username is the authenticated user (JWT via get_current_user),
recorded on the ledger row and in the audit log. All entry routes require auth.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import LargeBinary
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user
from .db import get_session
from .services import ledger

router = APIRouter(prefix="/entry", tags=["data entry"])

# Columns a client may pass under `extra` on a receipt: real receipts columns
# minus the ones handled explicitly, minus id/blobs.
_RECEIPT_BASE = {
    "id", "Date", "SAP_Code", "Quantity", "Supplier", "Remarks",
    "Site_ID", "Expiry_Date", "PR_Number", "Lot_Number",
}
_RECEIPT_EXTRA_OK = {
    c.name for c in ledger.receipts_t.columns
    if c.name not in _RECEIPT_BASE and not isinstance(c.type, LargeBinary)
}


class ReceiptIn(BaseModel):
    Date: str = Field(..., description="Receipt date, YYYY-MM-DD")
    SAP_Code: str
    Quantity: float = Field(..., gt=0)
    Site_ID: str
    Supplier: Optional[str] = None
    Remarks: Optional[str] = None
    Expiry_Date: Optional[str] = Field(None, description="YYYY-MM-DD; auto-creates a lot")
    PR_Number: Optional[str] = None
    Lot_Number: Optional[str] = None
    extra: Optional[dict[str, Any]] = Field(
        None, description="Optional extra receipts columns (logistics fields)")


class ConsumptionIn(BaseModel):
    Date: str
    SAP_Code: str
    Quantity: float = Field(..., gt=0)
    Site_ID: str
    Work_Type: Optional[str] = None
    Issued_To: Optional[str] = None
    Issued_By: Optional[str] = None
    PR_Number: Optional[str] = None
    Tank_No: Optional[str] = None
    Serial_No: Optional[str] = None
    Remarks: Optional[str] = None
    Requested_By: Optional[str] = None
    Lot_Number: Optional[str] = Field(None, description="explicit lot; blank → FEFO auto-pick")
    FEFO_Override: Optional[str] = None


class ReturnIn(BaseModel):
    Date: str
    SAP_Code: str
    Quantity: float = Field(..., gt=0)
    Site_ID: str
    Reason: Optional[str] = None
    Remarks: Optional[str] = None


class AdjustmentIn(BaseModel):
    SAP_Code: str
    Site_ID: str
    system_qty: float = Field(..., description="on-system qty")
    counted_qty: float = Field(..., description="physically counted qty")
    reason_code: str
    notes: Optional[str] = None
    Lot_Number: Optional[str] = Field(None, description="set → dispose this lot")


@router.post("/receipts", status_code=201, summary="Submit a goods receipt for HOD approval")
async def create_receipt(
    body: ReceiptIn = Body(...),
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if body.extra:
        bad = [k for k in body.extra if k not in _RECEIPT_EXTRA_OK]
        if bad:
            raise HTTPException(422, f"unknown/for-bidden receipt columns: {bad}")

    data = body.model_dump()
    try:
        async with session.begin():
            if not await ledger.sap_exists(session, body.SAP_Code):
                raise HTTPException(404, f"SAP_Code {body.SAP_Code!r} not in inventory")
            result = await ledger.stage_receipt(session, username=user["username"], data=data)
        return result
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")


@router.post("/consumption", status_code=201, summary="Submit a material issue for HOD approval")
async def create_consumption(
    body: ConsumptionIn = Body(...),
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        async with session.begin():
            if not await ledger.sap_exists(session, body.SAP_Code):
                raise HTTPException(404, f"SAP_Code {body.SAP_Code!r} not in inventory")
            return await ledger.stage_consumption(session, username=user["username"], data=body.model_dump())
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")


@router.post("/returns", status_code=201, summary="Submit a return for HOD approval")
async def create_return(
    body: ReturnIn = Body(...),
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        async with session.begin():
            if not await ledger.sap_exists(session, body.SAP_Code):
                raise HTTPException(404, f"SAP_Code {body.SAP_Code!r} not in inventory")
            return await ledger.stage_return(session, username=user["username"], data=body.model_dump())
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")


@router.post("/adjustments", status_code=201, summary="Submit a stock-count adjustment for HOD approval")
async def create_adjustment(
    body: AdjustmentIn = Body(...),
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if body.reason_code not in ledger.ADJUSTMENT_REASONS:
        raise HTTPException(422, f"unknown reason_code {body.reason_code!r}")
    if abs(body.counted_qty - body.system_qty) < 1e-9:
        raise HTTPException(400, "counted qty matches system qty — no adjustment needed")
    try:
        async with session.begin():
            if not await ledger.sap_exists(session, body.SAP_Code):
                raise HTTPException(404, f"SAP_Code {body.SAP_Code!r} not in inventory")
            return await ledger.stage_adjustment(session, username=user["username"], data=body.model_dump())
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")


@router.get("/adjustment-reasons", tags=["data entry"], summary="Reason codes for adjustments")
async def adjustment_reasons(user: dict = Depends(get_current_user)):
    return ledger.ADJUSTMENT_REASONS
