"""
backend/api/warehouse.py — Warehouse portal: assignment → receive → DN → outbound.

Restricted to warehouse_user (+ logistics oversight + admin). Prices are never
returned to this role.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_roles
from .db import get_session
from .services import warehouse as wh

router = APIRouter(prefix="/warehouse", tags=["warehouse"],
                   dependencies=[Depends(require_roles("warehouse_user", "logistics"))])

_ROLE = require_roles("warehouse_user", "logistics")


class ReceiveIn(BaseModel):
    received: dict[str, float] = Field(..., description="{po_item_id: qty_received}")


class DNLineIn(BaseModel):
    po_item_id: int
    Qty: float = Field(..., gt=0)
    Lot_Number: Optional[str] = None
    Expiry_Date: Optional[str] = None
    Remarks: Optional[str] = None


class CreateDNIn(BaseModel):
    po_number: str
    warehouse_id: str
    site_id: str
    line_items: list[DNLineIn]
    Vehicle_No: Optional[str] = None
    Driver_Name: Optional[str] = None
    Driver_Phone: Optional[str] = None
    Remarks: Optional[str] = None


def _guard(res: dict) -> dict:
    if res.get("error"):
        raise HTTPException(409, res["error"])
    return res


@router.get("/assignments", summary="POs routed to a warehouse")
async def assignments(warehouse_id: str, status: Optional[str] = None,
                      session: AsyncSession = Depends(get_session)):
    statuses = [s.strip() for s in status.split(",")] if status else \
        ["assigned", "acknowledged", "partial"]
    return {"items": await wh.assignments_for(session, warehouse_id, statuses)}


@router.get("/assignments/{assignment_id}/items", summary="PO items for an assignment")
async def assignment_items(assignment_id: int, session: AsyncSession = Depends(get_session)):
    return {"items": await wh.assignment_items(session, assignment_id)}


@router.post("/assignments/{assignment_id}/acknowledge", summary="Acknowledge an assignment")
async def acknowledge(assignment_id: int, user: dict = Depends(_ROLE),
                      session: AsyncSession = Depends(get_session)):
    async with session.begin():
        res = await wh.acknowledge(session, username=user["username"], assignment_id=assignment_id)
    return _guard(res)


@router.post("/assignments/{assignment_id}/receive", summary="Record goods received")
async def receive(assignment_id: int, body: ReceiveIn = Body(...),
                  user: dict = Depends(_ROLE),
                  session: AsyncSession = Depends(get_session)):
    try:
        async with session.begin():
            res = await wh.receive(session, username=user["username"],
                                   assignment_id=assignment_id, received_map=body.received)
        return _guard(res)
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")


@router.post("/dns", status_code=201, summary="Prepare a Delivery Note")
async def create_dn(body: CreateDNIn = Body(...), user: dict = Depends(_ROLE),
                    session: AsyncSession = Depends(get_session)):
    header = {"Vehicle_No": body.Vehicle_No, "Driver_Name": body.Driver_Name,
              "Driver_Phone": body.Driver_Phone, "Remarks": body.Remarks}
    lines = [li.model_dump() for li in body.line_items]
    try:
        async with session.begin():
            res = await wh.create_dn(session, username=user["username"],
                                     po_number=body.po_number, warehouse_id=body.warehouse_id,
                                     site_id=body.site_id, line_items=lines, header=header)
        return _guard(res)
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")


@router.get("/dns", summary="Delivery notes")
async def dns(warehouse_id: Optional[str] = None, status: Optional[str] = None,
              session: AsyncSession = Depends(get_session)):
    return {"items": await wh.dns_for(session, warehouse_id, status)}


@router.get("/dns/{dn_number}/items", summary="DN line items")
async def dn_items(dn_number: str, session: AsyncSession = Depends(get_session)):
    return {"items": await wh.dn_lines(session, dn_number)}


@router.post("/dns/{dn_number}/ship", summary="Mark a DN outbound (in_transit)")
async def ship(dn_number: str, user: dict = Depends(_ROLE),
               session: AsyncSession = Depends(get_session)):
    async with session.begin():
        res = await wh.ship_dn(session, username=user["username"], dn_number=dn_number)
    return _guard(res)
