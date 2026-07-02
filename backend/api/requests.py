"""
backend/api/requests.py — supervisor material requests (SMR).

  POST /requests                 — supervisor creates a request (→ pending_sk)
  GET  /requests                 — list (own for supervisor; site-pending for SK; all for admin)
  GET  /requests/{id}/items      — request lines
  POST /requests/{id}/approve    — SK approves → mirrors to pending_issues (→ HOD Approvals)
  POST /requests/{id}/reject     — SK rejects

Supervisor create is restricted to supervisor/admin; approve/reject to store_keeper/admin.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user, require_roles
from .db import get_session
from .services import supervisor as smr

router = APIRouter(prefix="/requests", tags=["material requests"])

_SUPERVISOR = require_roles("supervisor")
_SK = require_roles("store_keeper")


class SMRItemIn(BaseModel):
    SAP_Code: str
    Requested_Qty: float = Field(..., gt=0)
    Notes: Optional[str] = None


class CreateSMRIn(BaseModel):
    site_id: Optional[str] = None
    worker_id: str
    job_tank_place: str
    old_ppe_returned: bool = True
    no_return_reason: Optional[str] = None
    items: list[SMRItemIn]


class RejectIn(BaseModel):
    reason: Optional[str] = None


def _guard(res: dict) -> dict:
    if res.get("error"):
        raise HTTPException(409, res["error"])
    return res


@router.post("", status_code=201, summary="Create a material request")
async def create(body: CreateSMRIn = Body(...), user: dict = Depends(_SUPERVISOR),
                 session: AsyncSession = Depends(get_session)):
    site = user["site_id"] or body.site_id
    if not site:
        raise HTTPException(422, "site_id is required")
    try:
        async with session.begin():
            res = await smr.create_smr(
                session, supervisor=user["username"], site_id=site, worker_id=body.worker_id,
                job_tank_place=body.job_tank_place, old_ppe_returned=int(body.old_ppe_returned),
                no_return_reason=body.no_return_reason,
                items=[i.model_dump() for i in body.items])
        return _guard(res)
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")


@router.get("", summary="List material requests")
async def listing(mine: bool = False, site_id: Optional[str] = None, status: Optional[str] = None,
                  user: dict = Depends(get_current_user),
                  session: AsyncSession = Depends(get_session)):
    # Sensible defaults per role: supervisor → own; store_keeper → site pending.
    if mine or user["role"] == "supervisor":
        return {"items": await smr.list_smr(session, requested_by=user["username"], status=status)}
    scope = site_id or (user["site_id"] or None)
    if user["role"] == "store_keeper" and status is None:
        status = "pending_sk"
    return {"items": await smr.list_smr(session, site_id=scope, status=status)}


@router.get("/{request_id}/items", summary="Request line items")
async def items(request_id: int, user: dict = Depends(get_current_user),
                session: AsyncSession = Depends(get_session)):
    return {"items": await smr.smr_items(session, request_id)}


@router.post("/{request_id}/approve", summary="SK approves → stages issues for HOD")
async def approve(request_id: int, user: dict = Depends(_SK),
                  session: AsyncSession = Depends(get_session)):
    try:
        async with session.begin():
            res = await smr.approve_smr(session, sk_username=user["username"], request_id=request_id)
        return _guard(res)
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")


@router.post("/{request_id}/reject", summary="SK rejects a request")
async def reject(request_id: int, body: RejectIn = Body(default=RejectIn()),
                 user: dict = Depends(_SK), session: AsyncSession = Depends(get_session)):
    async with session.begin():
        res = await smr.reject_smr(session, sk_username=user["username"],
                                   request_id=request_id, reason=body.reason or "")
    return _guard(res)
