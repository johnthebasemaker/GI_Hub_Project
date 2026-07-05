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

from sqlalchemy import func, insert, select, text, update

from .auth import require_roles, resolve_warehouse_param, warehouse_scope
from .db import get_session
from .services import warehouse as wh
from .services.ledger import _MD, write_audit
from .services.notifications import notify

router = APIRouter(prefix="/warehouse", tags=["warehouse"],
                   dependencies=[Depends(require_roles("warehouse_user", "logistics"))])

_ROLE = require_roles("warehouse_user", "logistics")

_po_assignments_t = _MD.tables["po_assignments"]
_delivery_notes_t = _MD.tables["delivery_notes"]
_po_returns_t = _MD.tables["po_returns"]

# Disposition lifecycle for a return-from-site (legacy warehouse-portal tab):
# open → hold | return_to_vendor | scrap | rework → closed.
DISPOSITIONS = ("hold", "return_to_vendor", "scrap", "rework", "closed")


async def _guard_row_warehouse(session: AsyncSession, table, key_col: str,
                               key, user: dict) -> None:
    """403 when a warehouse-bound user acts on another warehouse's row.
    A missing row passes through — the service raises its own not-found."""
    scope = warehouse_scope(user)
    if scope is None:
        return
    row_wh = (await session.execute(
        select(table.c["Warehouse_ID"]).where(table.c[key_col] == key))).scalar_one_or_none()
    if row_wh is None:
        return
    if scope == "" or (row_wh or "").strip() != scope:
        raise HTTPException(403, "this item belongs to another warehouse")


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
                      user: dict = Depends(_ROLE),
                      session: AsyncSession = Depends(get_session)):
    # Warehouse users are pinned server-side to their bound Warehouse_ID.
    warehouse_id = resolve_warehouse_param(user, warehouse_id)
    if warehouse_id == "":
        return {"items": []}
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
        await _guard_row_warehouse(session, _po_assignments_t, "id", assignment_id, user)
        res = await wh.acknowledge(session, username=user["username"], assignment_id=assignment_id)
    return _guard(res)


@router.post("/assignments/{assignment_id}/receive", summary="Record goods received")
async def receive(assignment_id: int, body: ReceiveIn = Body(...),
                  user: dict = Depends(_ROLE),
                  session: AsyncSession = Depends(get_session)):
    try:
        async with session.begin():
            await _guard_row_warehouse(session, _po_assignments_t, "id", assignment_id, user)
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
    # A warehouse-bound user may only cut DNs from their own warehouse.
    scope = warehouse_scope(user)
    if scope is not None and body.warehouse_id != scope:
        raise HTTPException(403, "you may only prepare DNs for your own warehouse")
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
              user: dict = Depends(_ROLE),
              session: AsyncSession = Depends(get_session)):
    warehouse_id = resolve_warehouse_param(user, warehouse_id)
    if warehouse_id == "":
        return {"items": []}
    return {"items": await wh.dns_for(session, warehouse_id, status)}


@router.get("/dns/{dn_number}/items", summary="DN line items")
async def dn_items(dn_number: str, session: AsyncSession = Depends(get_session)):
    return {"items": await wh.dn_lines(session, dn_number)}


# --- Returns from site (disposition workflow over po_returns) -----------------
class ReturnFromSiteIn(BaseModel):
    PO_Number: str
    Qty: float = Field(..., gt=0)
    Reason: str
    DN_Number: Optional[str] = None
    po_item_id: Optional[int] = None
    Material_Code: Optional[str] = None
    Expected_Resupply: Optional[str] = None
    notes: Optional[str] = None


class DispositionIn(BaseModel):
    status: str
    notes: Optional[str] = None


async def _guard_po_warehouse(session: AsyncSession, po_number: str, user: dict) -> None:
    """A warehouse-bound user may only touch returns for POs assigned to their
    own warehouse."""
    scope = warehouse_scope(user)
    if scope is None:
        return
    if scope == "":
        raise HTTPException(403, "no warehouse is bound to your account")
    n = (await session.execute(
        select(func.count()).select_from(_po_assignments_t)
        .where(_po_assignments_t.c["PO_Number"] == po_number,
               _po_assignments_t.c["Warehouse_ID"] == scope))).scalar_one()
    if n == 0:
        raise HTTPException(403, "this PO is not assigned to your warehouse")


@router.get("/returns", summary="Returns-from-site queue (disposition workflow)")
async def list_returns(status: Optional[str] = None,
                       user: dict = Depends(_ROLE),
                       session: AsyncSession = Depends(get_session)):
    t = _po_returns_t
    stmt = select(t)
    if status:
        stmt = stmt.where(t.c["status"] == status)
    scope = warehouse_scope(user)
    if scope == "":
        return {"items": []}
    if scope is not None:
        stmt = stmt.where(t.c["PO_Number"].in_(
            select(_po_assignments_t.c["PO_Number"])
            .where(_po_assignments_t.c["Warehouse_ID"] == scope)))
    stmt = stmt.order_by(t.c["id"].desc()).limit(500)
    return {"items": [dict(m) for m in (await session.execute(stmt)).mappings().all()]}


@router.post("/returns", status_code=201, summary="Record a return received from a site")
async def create_return_from_site(body: ReturnFromSiteIn = Body(...),
                                  user: dict = Depends(_ROLE),
                                  session: AsyncSession = Depends(get_session)):
    async with session.begin():
        await _guard_po_warehouse(session, body.PO_Number, user)
        rid = (await session.execute(insert(_po_returns_t).values(
            PO_Number=body.PO_Number, po_item_id=body.po_item_id,
            DN_Number=body.DN_Number, Material_Code=body.Material_Code,
            Qty=body.Qty, Reason=body.Reason,
            raised_by_role=user["role"], raised_by=user["username"],
            Expected_Resupply=body.Expected_Resupply, status="open",
            notes=body.notes).returning(_po_returns_t.c["id"]))).scalar_one()
        await write_audit(session, user["username"], "RETURN_FROM_SITE", "po_returns",
                          f"id={rid} po={body.PO_Number} qty={body.Qty:g} reason={body.Reason}")
        await notify(session, event_key="vendor_return_raised", recipient_role="logistics",
                     severity="warning", title="Return from site recorded",
                     body=f"{body.PO_Number}: qty {body.Qty:g} — {body.Reason}",
                     related_table="po_returns", related_ref=str(rid))
    return {"created": True, "id": rid}


@router.post("/returns/{rid}/disposition", summary="Set a return's disposition")
async def set_return_disposition(rid: int, body: DispositionIn = Body(...),
                                 user: dict = Depends(_ROLE),
                                 session: AsyncSession = Depends(get_session)):
    if body.status not in DISPOSITIONS:
        raise HTTPException(422, f"status must be one of {sorted(DISPOSITIONS)}")
    async with session.begin():
        row = (await session.execute(
            select(_po_returns_t.c["PO_Number"], _po_returns_t.c["status"])
            .where(_po_returns_t.c["id"] == rid))).first()
        if row is None:
            raise HTTPException(404, f"return {rid} not found")
        if row.status == "closed":
            raise HTTPException(409, "this return is already closed")
        await _guard_po_warehouse(session, row.PO_Number, user)
        values: dict = {"status": body.status}
        if body.notes:
            values["notes"] = body.notes
        if body.status == "closed":
            values["closed_at"] = func.now()
            values["closed_by"] = user["username"]
        await session.execute(update(_po_returns_t)
                              .where(_po_returns_t.c["id"] == rid).values(**values))
        await write_audit(session, user["username"], "RETURN_DISPOSITION", "po_returns",
                          f"id={rid} → {body.status}")
        if body.status == "return_to_vendor":
            await notify(session, event_key="vendor_return_raised", recipient_role="logistics",
                         severity="warning", title="Return routed back to vendor",
                         body=f"{row.PO_Number}: return #{rid} dispositioned return_to_vendor",
                         related_table="po_returns", related_ref=str(rid))
    return {"updated": True, "id": rid, "status": body.status}


# --- History & throughput ------------------------------------------------------
@router.get("/history", summary="Completed DNs + fulfilled assignments + throughput")
async def history(warehouse_id: Optional[str] = None,
                  user: dict = Depends(_ROLE),
                  session: AsyncSession = Depends(get_session)):
    warehouse_id = resolve_warehouse_param(user, warehouse_id)
    if warehouse_id == "":
        return {"dns": [], "assignments": [], "throughput": {"dn_by_status": [], "dn_by_family": []}}
    dn_where, params = "1=1", {}
    if warehouse_id:
        dn_where += ' AND "Warehouse_ID" = :wh'
        params["wh"] = warehouse_id
    dns = (await session.execute(text(f'''
        SELECT "DN_Number", "PO_Number", "Warehouse_ID", "Site_ID", rl_bl_family,
               "DN_Date", status, created_by
        FROM delivery_notes WHERE {dn_where} AND status NOT IN ('prepared', 'in_transit')
        ORDER BY "DN_Number" DESC LIMIT 200'''), params)).mappings().all()

    a = _po_assignments_t
    astmt = select(a).where(a.c["status"] == "received")
    if warehouse_id:
        astmt = astmt.where(a.c["Warehouse_ID"] == warehouse_id)
    assignments = (await session.execute(
        astmt.order_by(a.c["id"].desc()).limit(200))).mappings().all()

    by_status = (await session.execute(text(f'''
        SELECT status, COUNT(*) AS n FROM delivery_notes WHERE {dn_where}
        GROUP BY status ORDER BY status'''), params)).mappings().all()
    by_family = (await session.execute(text(f'''
        SELECT COALESCE(rl_bl_family, '—') AS family, COUNT(*) AS n
        FROM delivery_notes WHERE {dn_where}
        GROUP BY COALESCE(rl_bl_family, '—') ORDER BY family'''), params)).mappings().all()

    return {"dns": [dict(r) for r in dns],
            "assignments": [dict(r) for r in assignments],
            "throughput": {"dn_by_status": [dict(r) for r in by_status],
                           "dn_by_family": [dict(r) for r in by_family]}}


@router.post("/dns/{dn_number}/ship", summary="Mark a DN outbound (in_transit)")
async def ship(dn_number: str, user: dict = Depends(_ROLE),
               session: AsyncSession = Depends(get_session)):
    async with session.begin():
        await _guard_row_warehouse(session, _delivery_notes_t, "DN_Number", dn_number, user)
        res = await wh.ship_dn(session, username=user["username"], dn_number=dn_number)
    return _guard(res)
