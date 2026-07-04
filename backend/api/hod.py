"""
backend/api/hod.py — HOD portal: staging approvals + burn-rate.

The Store Keeper's data-entry submissions land in pending_* / stock_adjustments
(status=pending_hod). Here the HOD reviews them and either APPROVES (which commits
to the permanent ledger via the ledger services — FEFO, audit, PR-close, etc.) or
REJECTS (marks the row, no ledger write). Plus a burn-rate report.

All routes require role level ≥ 2 (hod / admin).
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_level
from .db import get_session
from .services import ledger, procurement

router = APIRouter(prefix="/hod", tags=["HOD approvals"],
                   dependencies=[Depends(require_level(2))])

_TABLES = {
    "receipts": ledger.pending_receipts_t,
    "issues": ledger.pending_issues_t,
    "returns": ledger.pending_returns_t,
    "adjustments": ledger.adjustments_t,
}
_COMMIT = {
    "receipts": ledger.commit_receipt,
    "issues": ledger.commit_consumption,
    "returns": ledger.commit_return,
    "adjustments": ledger.commit_adjustment,
}
# reject_pending uses singular kind names
_REJECT_KIND = {"receipts": "receipt", "issues": "issue",
                "returns": "return", "adjustments": "adjustment"}


class RejectIn(BaseModel):
    reason: Optional[str] = None


class PRLineIn(BaseModel):
    SAP_Code: str
    Requested_Qty: float
    Material_Code: Optional[str] = None
    Material_Name: Optional[str] = None
    UOM: Optional[str] = None
    Est_Cost_SAR: Optional[float] = None
    Notes: Optional[str] = None


class CreatePRIn(BaseModel):
    site_id: str
    lines: list[PRLineIn]
    supplier: Optional[str] = None
    notes: Optional[str] = None
    delivery_date: Optional[str] = None


def _rows(res):
    return [dict(m) for m in res.mappings().all()]


@router.get("/pending", summary="Pending-approval counts per type")
async def pending_counts(site_id: Optional[str] = None,
                         session: AsyncSession = Depends(get_session)):
    out = {}
    for k, t in _TABLES.items():
        stmt = select(func.count()).select_from(t).where(t.c["status"] == ledger.PENDING)
        if site_id:
            stmt = stmt.where(t.c["Site_ID"] == site_id)
        out[k] = (await session.execute(stmt)).scalar_one()
    return out


@router.get("/pending/{kind}", summary="List pending items of a type")
async def pending_list(kind: str, site_id: Optional[str] = None,
                       session: AsyncSession = Depends(get_session)):
    if kind not in _TABLES:
        raise HTTPException(404, f"unknown kind {kind!r}")
    t = _TABLES[kind]
    stmt = select(t).where(t.c["status"] == ledger.PENDING)
    if site_id:
        stmt = stmt.where(t.c["Site_ID"] == site_id)
    stmt = stmt.order_by(t.c["id"])
    return {"items": _rows(await session.execute(stmt))}


@router.post("/pending/{kind}/{pid}/approve", summary="Approve → commit to the ledger")
async def approve(kind: str, pid: int, user: dict = Depends(require_level(2)),
                  session: AsyncSession = Depends(get_session)):
    if kind not in _COMMIT:
        raise HTTPException(404, f"unknown kind {kind!r}")
    try:
        async with session.begin():
            res = await _COMMIT[kind](session, approver=user["username"], pending_id=pid)
        if res.get("error"):
            raise HTTPException(409, res["error"])
        return res
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")


@router.post("/pending/{kind}/{pid}/reject", summary="Reject a pending item")
async def reject(kind: str, pid: int, body: RejectIn = Body(default=RejectIn()),
                 user: dict = Depends(require_level(2)),
                 session: AsyncSession = Depends(get_session)):
    if kind not in _REJECT_KIND:
        raise HTTPException(404, f"unknown kind {kind!r}")
    async with session.begin():
        res = await ledger.reject_pending(session, approver=user["username"],
                                          kind=_REJECT_KIND[kind], pending_id=pid,
                                          reason=body.reason or "")
    if res.get("error"):
        raise HTTPException(409, res["error"])
    return res


@router.get("/burn-rate", summary="Consumption by material over the last N days")
async def burn_rate(site_id: Optional[str] = None, days: int = 30,
                    session: AsyncSession = Depends(get_session)):
    days = max(1, min(days, 365))
    cutoff = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
    where = '"Date" >= :cutoff'
    params = {"cutoff": cutoff, "days": days}
    if site_id:
        where += ' AND COALESCE("Site_ID", \'HQ\') = :site'
        params["site"] = site_id
    sql = text(f'''
        SELECT TRIM("SAP_Code") AS "SAP_Code",
               ROUND(CAST(SUM("Quantity") AS NUMERIC), 3) AS "Consumed",
               ROUND(CAST(SUM("Quantity") / :days AS NUMERIC), 3) AS "Daily_Avg"
        FROM consumption
        WHERE {where}
        GROUP BY TRIM("SAP_Code")
        ORDER BY "Consumed" DESC
        LIMIT 200
    ''')
    return {"days": days, "since": cutoff,
            "items": _rows(await session.execute(sql, params))}


@router.get("/prs", summary="Site purchase requests (grouped) — to submit to Logistics")
async def hod_pr_list(site_id: Optional[str] = None,
                      session: AsyncSession = Depends(get_session)):
    return {"items": await procurement.hod_prs(session, site_id)}


@router.post("/prs", status_code=201, summary="Create a site PR (draft) from lines")
async def hod_pr_create(body: CreatePRIn = Body(...),
                        user: dict = Depends(require_level(2)),
                        session: AsyncSession = Depends(get_session)):
    if not body.lines:
        raise HTTPException(422, "add at least one line")
    try:
        async with session.begin():
            res = await procurement.create_pr(
                session, username=user["username"], site_id=body.site_id,
                lines=[ln.model_dump() for ln in body.lines],
                supplier=body.supplier, notes=body.notes,
                delivery_date=body.delivery_date)
        if res.get("error"):
            raise HTTPException(409, res["error"])
        return res
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")


@router.post("/prs/{pr_number}/submit", summary="Submit a PR to Logistics")
async def hod_pr_submit(pr_number: str, site_id: str,
                        user: dict = Depends(require_level(2)),
                        session: AsyncSession = Depends(get_session)):
    async with session.begin():
        res = await procurement.submit_pr(session, username=user["username"],
                                          pr_number=pr_number, site_id=site_id)
    if res.get("error"):
        raise HTTPException(409, res["error"])
    return res
