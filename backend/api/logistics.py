"""
backend/api/logistics.py — Logistics portal: PR queue → create PO → assign.

All routes require role level ≥ 3 (logistics / admin). The PR queue is fed by the
HOD submitting PRs (POST /hod/prs/{pr}/submit).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_level
from .db import get_session
from .services import procurement

router = APIRouter(prefix="/logistics", tags=["logistics"],
                   dependencies=[Depends(require_level(3))])


class CreatePOIn(BaseModel):
    pr_number: str
    site_id: str
    po_number: str
    vendor_code: Optional[str] = None
    vendor_name: Optional[str] = None
    expected_delivery: Optional[str] = None


class AssignIn(BaseModel):
    warehouse_id: str
    expected_delivery: Optional[str] = None
    notes: Optional[str] = None


@router.get("/prs", summary="Incoming PR queue (submitted)")
async def pr_queue(site_id: Optional[str] = None,
                   session: AsyncSession = Depends(get_session)):
    return {"items": await procurement.pr_queue(session, site_id)}


@router.get("/prs/{pr_number}/lines", summary="PR lines")
async def pr_lines(pr_number: str, site_id: Optional[str] = None,
                   session: AsyncSession = Depends(get_session)):
    return {"items": await procurement.pr_lines(session, pr_number, site_id)}


@router.post("/pos", status_code=201, summary="Create a PO from a submitted PR")
async def create_po(body: CreatePOIn = Body(...),
                    user: dict = Depends(require_level(3)),
                    session: AsyncSession = Depends(get_session)):
    try:
        async with session.begin():
            res = await procurement.create_po_from_pr(
                session, username=user["username"], pr_number=body.pr_number,
                site_id=body.site_id, po_number=body.po_number,
                vendor_code=body.vendor_code, vendor_name=body.vendor_name,
                expected_delivery=body.expected_delivery)
        if res.get("error"):
            raise HTTPException(409, res["error"])
        return res
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")


@router.get("/pos", summary="Purchase orders")
async def pos(status: Optional[str] = None,
              session: AsyncSession = Depends(get_session)):
    return {"items": await procurement.po_list(session, status)}


@router.get("/pos/{po_number}/items", summary="PO line items")
async def po_items(po_number: str, session: AsyncSession = Depends(get_session)):
    return {"items": await procurement.po_items(session, po_number)}


@router.post("/pos/{po_number}/assign", summary="Assign a PO to a warehouse")
async def assign(po_number: str, body: AssignIn = Body(...),
                 user: dict = Depends(require_level(3)),
                 session: AsyncSession = Depends(get_session)):
    try:
        async with session.begin():
            res = await procurement.assign_po(
                session, username=user["username"], po_number=po_number,
                warehouse_id=body.warehouse_id, expected_delivery=body.expected_delivery,
                notes=body.notes or "")
        if res.get("error"):
            raise HTTPException(409, res["error"])
        return res
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")
