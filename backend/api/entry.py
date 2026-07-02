"""
backend/api/entry.py — data-entry endpoints (ledger writes) for the new UI.

Thin HTTP layer over backend/api/services/ledger.py. Owns the transaction
boundary and input validation; the business rules live in the service.

  POST /entry/receipts   — post a goods receipt (ports process_receipt_delivery)

Actor: until auth lands, the acting username comes from an `X-Actor` header
(defaults to "api"). When JWT is added this becomes the authenticated user.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import LargeBinary
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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


@router.post("/receipts", status_code=201, summary="Post a goods receipt")
async def create_receipt(
    body: ReceiptIn = Body(...),
    actor: str = Header("api", alias="X-Actor"),
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
            result = await ledger.post_receipt(session, username=actor, data=data)
        return result
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")
