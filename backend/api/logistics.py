"""
backend/api/logistics.py — Logistics portal: PR queue → create PO → assign.

All routes require role level ≥ 3 (logistics / admin). The PR queue is fed by the
HOD submitting PRs (POST /hod/prs/{pr}/submit).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
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


class ManualPOLineIn(BaseModel):
    Material_Code: Optional[str] = None
    Description: Optional[str] = None
    Qty: float
    UOM: Optional[str] = None
    Unit_Price: Optional[float] = 0
    PR_Number: Optional[str] = None
    WBS_Number: Optional[str] = None
    Network: Optional[str] = None
    Plant: Optional[str] = None


class ManualPOIn(BaseModel):
    po_number: str
    site_id: Optional[str] = None
    pr_number: Optional[str] = None          # free-text; may be an "unlisted" PR
    vendor_code: Optional[str] = None
    vendor_name: Optional[str] = None
    inco_terms: Optional[str] = None
    payment_terms: Optional[str] = None
    po_date: Optional[str] = None
    expected_delivery: Optional[str] = None
    lines: list[ManualPOLineIn] = Field(..., min_length=1)


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


@router.post("/pos/manual", status_code=201, summary="Create a PO manually (free-text lines/prices, unlisted PR allowed)")
async def create_po_manual(body: ManualPOIn = Body(...),
                           user: dict = Depends(require_level(3)),
                           session: AsyncSession = Depends(get_session)):
    try:
        async with session.begin():
            res = await procurement.create_po_manual(
                session, username=user["username"],
                header=body.model_dump(exclude={"lines"}),
                lines=[ln.model_dump() for ln in body.lines])
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


# --- reschedule workflow (H7): Logistics reviews + decides -------------------
class RescheduleDecideIn(BaseModel):
    action: str  # approve | reject
    decision_notes: Optional[str] = None


@router.get("/reschedules", summary="Reschedule requests (WH/HOD → Logistics)")
async def reschedules(status: Optional[str] = None,
                      session: AsyncSession = Depends(get_session)):
    return {"items": await procurement.list_reschedules(session, status)}


@router.post("/reschedules/{req_id}/decide", summary="Approve/reject a reschedule (approve pushes the new date)")
async def decide_reschedule(req_id: int, body: RescheduleDecideIn = Body(...),
                            user: dict = Depends(require_level(3)),
                            session: AsyncSession = Depends(get_session)):
    try:
        async with session.begin():
            res = await procurement.decide_reschedule(
                session, username=user["username"], req_id=req_id,
                action=body.action, decision_notes=body.decision_notes or "")
        if res.get("error"):
            raise HTTPException(409, res["error"])
        return res
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")


# --- force-close (H8): PR / PO / line with reason + 24h undo -----------------
class ForceCloseIn(BaseModel):
    target_type: str            # pr | po | line
    target_ref: str             # PR_Number | PO_Number | po_items.id
    reason: str
    notes: Optional[str] = None


@router.get("/force-closures", summary="Force-closure log (with undo window)")
async def force_closures(session: AsyncSession = Depends(get_session)):
    return {"items": await procurement.list_force_closures(session)}


@router.post("/force-close", status_code=201, summary="Force-close a PR/PO/line (reason required)")
async def force_close(body: ForceCloseIn = Body(...),
                      user: dict = Depends(require_level(3)),
                      session: AsyncSession = Depends(get_session)):
    try:
        async with session.begin():
            res = await procurement.force_close(
                session, username=user["username"], target_type=body.target_type,
                target_ref=body.target_ref, reason=body.reason, notes=body.notes or "")
        if res.get("error"):
            raise HTTPException(409, res["error"])
        return res
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")


@router.post("/force-close/{closure_id}/undo", summary="Undo a force-closure (within 24h)")
async def undo_force_close(closure_id: int,
                           user: dict = Depends(require_level(3)),
                           session: AsyncSession = Depends(get_session)):
    try:
        async with session.begin():
            res = await procurement.undo_force_close(
                session, username=user["username"], closure_id=closure_id)
        if res.get("error"):
            raise HTTPException(409, res["error"])
        return res
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")
