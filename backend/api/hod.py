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
import io
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_level, resolve_site_param, site_scope
from .db import get_session
from .services import ledger, procurement
from .services import warehouse as wh
from .services.notifications import notify
from .stock import SQL_SITE_STOCK

router = APIRouter(prefix="/hod", tags=["HOD approvals"],
                   dependencies=[Depends(require_level(2))])

_TABLES = {
    "receipts": ledger.pending_receipts_t,
    "issues": ledger.pending_issues_t,
    "returns": ledger.pending_returns_t,
    "adjustments": ledger.adjustments_t,
}
_MD_PR = ledger._MD.tables["pr_master"]
_COMMIT = {
    "receipts": ledger.commit_receipt,
    "issues": ledger.commit_consumption,
    "returns": ledger.commit_return,
    "adjustments": ledger.commit_adjustment,
}
# reject_pending uses singular kind names
_REJECT_KIND = {"receipts": "receipt", "issues": "issue",
                "returns": "return", "adjustments": "adjustment"}

# Column holding the submitter's username per pending kind, so an approve/reject
# can notify them. Receipts don't store the submitter on the row → None → skip.
_SUBMITTER_COL = {"receipts": None, "issues": "Issued_By",
                  "returns": "submitted_by", "adjustments": "submitted_by"}


async def _submitter(session: AsyncSession, kind: str, pid: int):
    col = _SUBMITTER_COL.get(kind)
    if not col:
        return None
    t = _TABLES[kind]
    return (await session.execute(
        select(t.c[col]).where(t.c["id"] == pid))).scalar_one_or_none()


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


async def _guard_pending_site(session: AsyncSession, kind: str, pid: int, user: dict) -> None:
    """403 when a site-scoped HOD acts on another site's staged row. A missing
    row passes through — the commit/reject service raises its own not-found."""
    scope = site_scope(user)
    if scope is None:
        return
    t = _TABLES[kind]
    row_site = (await session.execute(
        select(t.c["Site_ID"]).where(t.c["id"] == pid))).scalar_one_or_none()
    if row_site is None:
        return
    if scope == "" or (row_site or "").strip() != scope:
        raise HTTPException(403, "this item belongs to another site")


@router.get("/pending", summary="Pending-approval counts per type")
async def pending_counts(site_id: Optional[str] = None,
                         user: dict = Depends(require_level(2)),
                         session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    out = {}
    for k, t in _TABLES.items():
        if site_id == "":
            out[k] = 0
            continue
        stmt = select(func.count()).select_from(t).where(t.c["status"] == ledger.PENDING)
        if site_id:
            stmt = stmt.where(t.c["Site_ID"] == site_id)
        out[k] = (await session.execute(stmt)).scalar_one()
    return out


@router.get("/pending/{kind}", summary="List pending items of a type")
async def pending_list(kind: str, site_id: Optional[str] = None,
                       user: dict = Depends(require_level(2)),
                       session: AsyncSession = Depends(get_session)):
    if kind not in _TABLES:
        raise HTTPException(404, f"unknown kind {kind!r}")
    site_id = resolve_site_param(user, site_id)
    if site_id == "":
        return {"items": []}
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
            await _guard_pending_site(session, kind, pid, user)
            submitter = await _submitter(session, kind, pid)  # read before commit removes the row
            res = await _COMMIT[kind](session, approver=user["username"], pending_id=pid)
            if res.get("error"):
                raise HTTPException(409, res["error"])
            if submitter and submitter != user["username"]:
                await notify(session, recipient_user=submitter, event_key="entry_approved",
                             severity="success", title=f"Your {_REJECT_KIND[kind]} was approved",
                             body=f"Approved by {user['username']} and committed to the ledger.",
                             related_table="pending", related_ref=str(pid))
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
        await _guard_pending_site(session, kind, pid, user)
        submitter = await _submitter(session, kind, pid)
        res = await ledger.reject_pending(session, approver=user["username"],
                                          kind=_REJECT_KIND[kind], pending_id=pid,
                                          reason=body.reason or "")
        if res.get("error"):
            raise HTTPException(409, res["error"])
        if submitter and submitter != user["username"]:
            await notify(session, recipient_user=submitter, event_key="entry_rejected",
                         severity="warning", title=f"Your {_REJECT_KIND[kind]} was rejected",
                         body=(f"Rejected by {user['username']}: {body.reason}" if body.reason
                               else f"Rejected by {user['username']}."),
                         related_table="pending", related_ref=str(pid))
    return res


# Fields an HOD may correct on a staged row before committing (legacy EOD
# in-grid edits). Intersected with each pending table's real columns
# (e.g. returns carry Return_Reason, not Remarks).
_EDITABLE = {"Quantity", "Lot_Number", "Remarks", "Expiry_Date", "Supplier",
             "Work_Type", "Issued_To", "Return_Reason", "Date",
             "counted_qty", "reason_code", "notes"}


class PendingEditIn(BaseModel):
    fields: dict[str, Any]


@router.patch("/pending/{kind}/{pid}", summary="Edit a staged entry before approval")
async def edit_pending(kind: str, pid: int, body: PendingEditIn = Body(...),
                       user: dict = Depends(require_level(2)),
                       session: AsyncSession = Depends(get_session)):
    if kind not in _TABLES:
        raise HTTPException(404, f"unknown kind {kind!r}")
    t = _TABLES[kind]
    updates: dict[str, Any] = {}
    for k, v in (body.fields or {}).items():
        if k not in _EDITABLE or k not in t.c:
            raise HTTPException(422, f"field not editable: {k!r}")
        updates[k] = v
    if not updates:
        raise HTTPException(422, "no editable fields provided")
    for numf in ("Quantity", "counted_qty"):
        if numf in updates:
            try:
                updates[numf] = float(updates[numf])
            except (TypeError, ValueError):
                raise HTTPException(422, f"{numf} must be a number")
            if numf == "Quantity" and updates[numf] <= 0:
                raise HTTPException(422, "Quantity must be > 0")
    async with session.begin():
        await _guard_pending_site(session, kind, pid, user)
        # Adjustments store a derived variance — keep it consistent with an
        # edited counted_qty.
        if kind == "adjustments" and "counted_qty" in updates:
            sysq = (await session.execute(
                select(t.c["system_qty"]).where(t.c["id"] == pid))).scalar_one_or_none()
            if sysq is not None:
                updates["variance"] = updates["counted_qty"] - float(sysq)
        res = await session.execute(
            update(t).where(t.c["id"] == pid, t.c["status"] == ledger.PENDING)
            .values(**updates))
        if res.rowcount == 0:
            raise HTTPException(404, f"no pending {kind} row with id {pid}")
        await ledger.write_audit(session, user["username"], "EDIT_PENDING", t.name,
                                 f"id={pid} fields={sorted(updates)}")
    return {"updated": True, "id": pid, "fields": updates}


# NB: lives at /preflight (not /pending/preflight) — the /pending/{kind} route
# is registered first and would swallow the literal segment.
@router.get("/preflight", summary="Negative-stock pre-flight for pending issues")
async def pending_preflight(site_id: Optional[str] = None,
                            user: dict = Depends(require_level(2)),
                            session: AsyncSession = Depends(get_session)):
    """Which SAP codes would go negative if every pending issue were approved
    right now — the legacy EOD-commit deficit table."""
    site_id = resolve_site_param(user, site_id)
    if site_id == "":
        return {"items": []}
    where = 'WHERE p.pending_qty > COALESCE(s."Current_Stock", 0)'
    params: dict = {}
    if site_id:
        where += ' AND p.site = :site'
        params["site"] = site_id
    sql = text(f'''
        WITH p AS (
            SELECT TRIM("SAP_Code") AS sap, COALESCE("Site_ID",'HQ') AS site,
                   SUM("Quantity") AS pending_qty, COUNT(*) AS pending_rows
            FROM pending_issues WHERE status = :st
            GROUP BY TRIM("SAP_Code"), COALESCE("Site_ID",'HQ')
        )
        SELECT p.sap AS "SAP_Code", p.site AS "Site_ID",
               s."Equipment_Description", s."UOM",
               p.pending_rows AS "Pending_Rows", p.pending_qty AS "Pending_Qty",
               COALESCE(s."Current_Stock", 0) AS "Current_Stock",
               p.pending_qty - COALESCE(s."Current_Stock", 0) AS "Deficit"
        FROM p LEFT JOIN ({SQL_SITE_STOCK}) s
          ON s."SAP_Code" = p.sap AND s."Site_ID" = p.site
        {where}
        ORDER BY p.pending_qty - COALESCE(s."Current_Stock", 0) DESC''')
    params["st"] = ledger.PENDING
    return {"items": _rows(await session.execute(sql, params))}


class BulkApproveIn(BaseModel):
    ids: list[int]


@router.post("/pending/{kind}/bulk-approve", summary="Approve many staged entries at once")
async def bulk_approve(kind: str, body: BulkApproveIn = Body(...),
                       user: dict = Depends(require_level(2)),
                       session: AsyncSession = Depends(get_session)):
    """Each id commits in its OWN transaction — one bad row (FEFO conflict,
    already handled, cross-site) fails alone and the rest still land."""
    if kind not in _COMMIT:
        raise HTTPException(404, f"unknown kind {kind!r}")
    if not body.ids:
        raise HTTPException(422, "provide at least one id")
    if len(body.ids) > 200:
        raise HTTPException(422, "at most 200 ids per bulk approve")
    results = []
    for pid in body.ids:
        try:
            async with session.begin():
                await _guard_pending_site(session, kind, pid, user)
                submitter = await _submitter(session, kind, pid)
                res = await _COMMIT[kind](session, approver=user["username"], pending_id=pid)
                if res.get("error"):
                    raise ValueError(res["error"])  # rolls back just this id
                if submitter and submitter != user["username"]:
                    await notify(session, recipient_user=submitter, event_key="entry_approved",
                                 severity="success", title=f"Your {_REJECT_KIND[kind]} was approved",
                                 body=f"Approved by {user['username']} (bulk commit).",
                                 related_table="pending", related_ref=str(pid))
            results.append({"id": pid, "ok": True})
        except HTTPException as e:
            results.append({"id": pid, "ok": False, "error": str(e.detail)})
        except (ValueError, IntegrityError, DataError) as e:
            results.append({"id": pid, "ok": False, "error": str(e)})
    committed = sum(1 for r in results if r["ok"])
    return {"committed": committed, "failed": len(results) - committed, "results": results}


@router.get("/low-stock", summary="Items below minimum at a site, with reorder suggestion")
async def low_stock(site_id: Optional[str] = None,
                    user: dict = Depends(require_level(2)),
                    session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    if site_id == "":
        return {"items": []}
    where = 's."Minimum_Qty" > 0 AND s."Current_Stock" < s."Minimum_Qty"'
    params: dict = {"cutoff": (_dt.date.today() - _dt.timedelta(days=30)).isoformat()}
    if site_id:
        where += ' AND s."Site_ID" = :site'
        params["site"] = site_id
    sql = text(f'''
        SELECT s."SAP_Code", s."Site_ID", s."Equipment_Description", s."UOM",
               s."Minimum_Qty", s."Current_Stock",
               s."Minimum_Qty" - s."Current_Stock" AS "Shortage",
               ROUND(CAST(COALESCE(b.daily_avg, 0) AS NUMERIC), 3) AS "Daily_Burn",
               CASE WHEN COALESCE(b.daily_avg, 0) > 0
                    THEN ROUND(CAST(s."Current_Stock" / b.daily_avg AS NUMERIC), 1) END
                 AS "Days_Of_Supply",
               ROUND(CAST((s."Minimum_Qty" - s."Current_Stock")
                          + COALESCE(b.daily_avg, 0) * 30 AS NUMERIC), 3)
                 AS "Suggested_Reorder"
        FROM ({SQL_SITE_STOCK}) s
        LEFT JOIN (
            SELECT TRIM("SAP_Code") AS sap, COALESCE("Site_ID",'HQ') AS site,
                   SUM("Quantity") / 30.0 AS daily_avg
            FROM consumption WHERE "Date" >= :cutoff
            GROUP BY TRIM("SAP_Code"), COALESCE("Site_ID",'HQ')
        ) b ON b.sap = s."SAP_Code" AND b.site = s."Site_ID"
        WHERE {where}
        ORDER BY s."Minimum_Qty" - s."Current_Stock" DESC
        LIMIT 500''')
    return {"items": _rows(await session.execute(sql, params))}


class AutoDraftIn(BaseModel):
    site_id: str


@router.post("/prs/auto-draft", status_code=201,
             summary="Draft a PR from every below-minimum item at a site")
async def auto_draft_pr(body: AutoDraftIn = Body(...),
                        user: dict = Depends(require_level(2)),
                        session: AsyncSession = Depends(get_session)):
    scope = site_scope(user)
    if scope is not None and body.site_id != scope:
        raise HTTPException(403, "you may only draft PRs for your own site")
    low = (await low_stock(site_id=body.site_id, user=user, session=session))["items"]
    if not low:
        return {"created": False, "reason": "no items below minimum at this site"}
    lines = [{
        "SAP_Code": r["SAP_Code"],
        "Requested_Qty": float(r["Suggested_Reorder"] or r["Shortage"]),
        "Material_Name": r.get("Equipment_Description"),
        "UOM": r.get("UOM"),
        "Notes": f"auto: stock {r['Current_Stock']} < min {r['Minimum_Qty']}",
    } for r in low]
    try:
        async with session.begin():
            res = await procurement.create_pr(
                session, username=user["username"], site_id=body.site_id,
                lines=lines, supplier=None,
                notes="Auto-drafted from low-stock report", delivery_date=None)
        if res.get("error"):
            raise HTTPException(409, res["error"])
        return {**res, "lines": len(lines)}
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")


@router.get("/prs/{pr_number}/pdf", summary="PR status snapshot as a PDF download")
async def pr_pdf(pr_number: str, site_id: Optional[str] = None,
                 user: dict = Depends(require_level(2)),
                 session: AsyncSession = Depends(get_session)):
    from .reports import to_pdf  # shared renderer (deferred: reports imports stock too)
    site_id = resolve_site_param(user, site_id)
    if site_id == "":
        raise HTTPException(403, "no site is assigned to your account")
    t = _MD_PR
    stmt = select(t.c["SAP_Code"], t.c["Material_Name"], t.c["UOM"],
                  t.c["Requested_Qty"], t.c["Supplier"], t.c["Est_Cost_SAR"],
                  t.c["status"], t.c["workflow_state"], t.c["Site_ID"],
                  ).where(t.c["PR_Number"] == pr_number)
    if site_id:
        stmt = stmt.where(t.c["Site_ID"] == site_id)
    rows = (await session.execute(stmt.order_by(t.c["id"]))).all()
    if not rows:
        raise HTTPException(404, f"PR {pr_number!r} not found")
    columns = ["SAP_Code", "Material_Name", "UOM", "Requested_Qty", "Supplier",
               "Est_Cost_SAR", "status", "workflow_state", "Site_ID"]
    data = to_pdf(f"Purchase Request {pr_number}", columns,
                  [list(r) for r in rows], user["username"])
    return StreamingResponse(io.BytesIO(data), media_type="application/pdf",
                             headers={"Content-Disposition":
                                      f'attachment; filename="{pr_number}.pdf"'})


@router.get("/burn-rate", summary="Consumption by material over the last N days")
async def burn_rate(site_id: Optional[str] = None, days: int = 30,
                    user: dict = Depends(require_level(2)),
                    session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    days = max(1, min(days, 365))
    if site_id == "":
        return {"days": days, "since": (_dt.date.today() - _dt.timedelta(days=days)).isoformat(),
                "items": []}
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
                      user: dict = Depends(require_level(2)),
                      session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    if site_id == "":
        return {"items": []}
    return {"items": await procurement.hod_prs(session, site_id)}


@router.post("/prs", status_code=201, summary="Create a site PR (draft) from lines")
async def hod_pr_create(body: CreatePRIn = Body(...),
                        user: dict = Depends(require_level(2)),
                        session: AsyncSession = Depends(get_session)):
    if not body.lines:
        raise HTTPException(422, "add at least one line")
    # A site-scoped HOD may only raise PRs for their own site.
    scope = site_scope(user)
    if scope is not None and body.site_id != scope:
        raise HTTPException(403, "you may only create PRs for your own site")
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


class PrLineEditIn(BaseModel):
    fields: dict[str, Any]


class PrRenameIn(BaseModel):
    site_id: str
    new_pr: str


@router.get("/prs/{pr_number}/lines", summary="PR lines (for draft editing)")
async def hod_pr_lines(pr_number: str, site_id: Optional[str] = None,
                       user: dict = Depends(require_level(2)),
                       session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, site_id)
    return {"items": await procurement.pr_lines(session, pr_number, site or None)}


@router.patch("/prs/lines/{line_id}", summary="Edit a draft PR line")
async def hod_pr_line_edit(line_id: int, body: PrLineEditIn = Body(...),
                           user: dict = Depends(require_level(2)),
                           session: AsyncSession = Depends(get_session)):
    async with session.begin():
        res = await procurement.update_pr_line(
            session, username=user["username"], line_id=line_id,
            fields=body.fields, caller_site=site_scope(user))
    if res.get("error"):
        raise HTTPException(409, res["error"])
    return res


@router.post("/prs/{pr_number}/rename", summary="Rename a draft PR number")
async def hod_pr_rename(pr_number: str, body: PrRenameIn = Body(...),
                        user: dict = Depends(require_level(2)),
                        session: AsyncSession = Depends(get_session)):
    scope = site_scope(user)
    if scope is not None and body.site_id != scope:
        raise HTTPException(403, "you may only rename PRs for your own site")
    async with session.begin():
        res = await procurement.rename_pr(session, username=user["username"],
                                          old_pr=pr_number, site_id=body.site_id, new_pr=body.new_pr)
    if res.get("error"):
        raise HTTPException(409, res["error"])
    return res


@router.post("/prs/{pr_number}/submit", summary="Submit a PR to Logistics")
async def hod_pr_submit(pr_number: str, site_id: str,
                        user: dict = Depends(require_level(2)),
                        session: AsyncSession = Depends(get_session)):
    scope = site_scope(user)
    if scope is not None and site_id != scope:
        raise HTTPException(403, "you may only submit PRs for your own site")
    async with session.begin():
        res = await procurement.submit_pr(session, username=user["username"],
                                          pr_number=pr_number, site_id=site_id)
    if res.get("error"):
        raise HTTPException(409, res["error"])
    return res


# --- DN multi-stage approval (Phase 6): HOD content stage -------------------
class DnDecideIn(BaseModel):
    action: str  # approve | reject
    reason: Optional[str] = None


@router.get("/dns", summary="Delivery notes awaiting HOD approval")
async def hod_dns(status: str = "pending_hod",
                  session: AsyncSession = Depends(get_session)):
    return {"items": await wh.dns_for(session, None, status)}


@router.get("/dns/{dn_number}/items", summary="DN line items")
async def hod_dn_items(dn_number: str, session: AsyncSession = Depends(get_session)):
    return {"items": await wh.dn_lines(session, dn_number)}


@router.post("/dns/{dn_number}/decide", summary="HOD approve/reject a DN (content stage)")
async def hod_decide_dn(dn_number: str, body: DnDecideIn = Body(...),
                        user: dict = Depends(require_level(2)),
                        session: AsyncSession = Depends(get_session)):
    async with session.begin():
        res = await wh.decide_dn_hod(session, username=user["username"],
                                     dn_number=dn_number, action=body.action,
                                     reason=body.reason or "")
    if res.get("error"):
        raise HTTPException(409, res["error"])
    return res


# --- reschedule workflow (H7): HOD raises a delivery-date change --------------
class RescheduleRaiseIn(BaseModel):
    po_number: str
    requested_date: str
    reason: str
    dn_number: Optional[str] = None


@router.post("/reschedule", summary="Request a delivery-date reschedule (→ Logistics)")
async def hod_raise_reschedule(body: RescheduleRaiseIn = Body(...),
                               user: dict = Depends(require_level(2)),
                               session: AsyncSession = Depends(get_session)):
    async with session.begin():
        res = await procurement.raise_reschedule(
            session, username=user["username"], role=user["role"],
            po_number=body.po_number, requested_date=body.requested_date,
            reason=body.reason, dn_number=body.dn_number)
    if res.get("error"):
        raise HTTPException(409, res["error"])
    return res
