"""
backend/api/sme.py — SME Material Estimator (READ-ONLY).

The SME integration is FROZEN in the Streamlit app (see the SME Canon in
handoff.md). This portal only READS the sme_* tables — it never writes them.
`SQL_SME_MATERIALS` is a Postgres-native port of the SQLite `sme_materials_view`
(derived Available_Qty = seed + received − consumed, joined via
SAP_Code → inventory.Material_Code); parity vs the SQLite view is asserted by
backend/api/parity_check.py.

Ordering always uses an explicit key (never rowid) per SME Canon Rule 1.
Restricted to hod/admin (level ≥ 2), matching the old PAGE_ACCESS.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_level
from .db import get_session
from .services.ledger import _MD

router = APIRouter(prefix="/sme", tags=["SME estimator"],
                   dependencies=[Depends(require_level(2))])

sme_equipment_t = _MD.tables["sme_equipment"]
sme_recipe_t = _MD.tables["sme_recipe"]
sme_sqm_t = _MD.tables["sme_sqm_progress"]

# Postgres-native port of the SQLite sme_materials_view (same columns/order).
SQL_SME_MATERIALS = '''
SELECT s."Material_Code" AS material_code, s."Material_Name" AS material_name,
       s."Item" AS item, s."Vendor" AS vendor,
       s."Purchasing_Document" AS purchasing_document, s."Document_Date" AS document_date,
       s."Nature" AS nature, s."UOM" AS uom,
       s."Initial_Available_Qty" AS initial_available_qty,
       s."Initial_Ordered_Qty" AS initial_ordered_qty,
       COALESCE((SELECT SUM(r."Quantity") FROM receipts r JOIN inventory i ON r."SAP_Code"=i."SAP_Code"
                 WHERE TRIM(COALESCE(i."Material_Code",''))=TRIM(s."Material_Code")), 0) AS received_qty,
       COALESCE((SELECT SUM(c."Quantity") FROM consumption c JOIN inventory i ON c."SAP_Code"=i."SAP_Code"
                 WHERE TRIM(COALESCE(i."Material_Code",''))=TRIM(s."Material_Code")), 0) AS consumed_qty,
       (s."Initial_Available_Qty"
        + COALESCE((SELECT SUM(r."Quantity") FROM receipts r JOIN inventory i ON r."SAP_Code"=i."SAP_Code"
                    WHERE TRIM(COALESCE(i."Material_Code",''))=TRIM(s."Material_Code")), 0)
        - COALESCE((SELECT SUM(c."Quantity") FROM consumption c JOIN inventory i ON c."SAP_Code"=i."SAP_Code"
                    WHERE TRIM(COALESCE(i."Material_Code",''))=TRIM(s."Material_Code")), 0)
       ) AS available_qty,
       s."Initial_Ordered_Qty" AS ordered_qty
FROM sme_inventory_seed s
'''

# Parity registry (consumed by parity_check.py alongside stock.DERIVED).
DERIVED_SME = {"sme_materials": {"sql": SQL_SME_MATERIALS, "view": "sme_materials_view"}}


def _rows(res):
    return [dict(m) for m in res.mappings().all()]


@router.get("/summary", summary="SME KPIs (equipment / SQM / recipes / materials)")
async def summary(site_id: Optional[str] = None, session: AsyncSession = Depends(get_session)):
    eq = sme_equipment_t
    eqf = eq.c["Site_ID"] == site_id if site_id else None

    def _w(stmt):
        return stmt.where(eqf) if eqf is not None else stmt

    equipment = (await session.execute(_w(select(func.count()).select_from(eq)))).scalar_one()
    total_sqm = (await session.execute(_w(select(func.coalesce(func.sum(eq.c["Equipment_Total_SQM"]), 0))))).scalar_one()
    recipes = (await session.execute(select(func.count()).select_from(sme_recipe_t))).scalar_one()
    materials = (await session.execute(select(func.count()).select_from(_MD.tables["sme_inventory_seed"]))).scalar_one()

    sqm = sme_sqm_t
    sqmf = sqm.c["Site_ID"] == site_id if site_id else None
    orig = select(func.coalesce(func.sum(sqm.c["Original_SQM"]), 0))
    done = select(func.coalesce(func.sum(sqm.c["Done_SQM"]), 0))
    if sqmf is not None:
        orig, done = orig.where(sqmf), done.where(sqmf)
    original_sqm = (await session.execute(orig)).scalar_one()
    done_sqm = (await session.execute(done)).scalar_one()

    by_ls = (await session.execute(_w(
        select(eq.c["Lining_System_Code"], func.count())
        .group_by(eq.c["Lining_System_Code"]).order_by(eq.c["Lining_System_Code"])))).all()

    return {
        "equipment": equipment, "recipes": recipes, "materials": materials,
        "total_sqm": float(total_sqm or 0),
        "original_sqm": float(original_sqm or 0), "done_sqm": float(done_sqm or 0),
        "by_lining_system": [{"Lining_System_Code": r[0], "count": r[1]} for r in by_ls],
    }


@router.get("/equipment", summary="SME equipment master")
async def equipment(site_id: Optional[str] = None, session: AsyncSession = Depends(get_session)):
    e = sme_equipment_t
    stmt = select(
        e.c["id"], e.c["Site_ID"], e.c["Equipment_Tag_No"], e.c["Name"], e.c["Location"],
        e.c["Type"], e.c["Substrate"], e.c["Lining_System_Code"], e.c["Lining_System"],
        e.c["Surface_Area_SQM"], e.c["Equipment_Total_SQM"],
    )
    if site_id:
        stmt = stmt.where(e.c["Site_ID"] == site_id)
    return {"items": _rows(await session.execute(stmt.order_by(e.c["id"])))}


@router.get("/recipes", summary="SME recipes / BOM (per lining system)")
async def recipes(lining_system_code: Optional[str] = None, session: AsyncSession = Depends(get_session)):
    r = sme_recipe_t
    stmt = select(
        r.c["id"], r.c["Lining_System_Code"], r.c["Lining_System_Name"], r.c["Substrate"],
        r.c["Material_Code"], r.c["Material_Name"], r.c["UOM"], r.c["Nature"], r.c["For_1_SQM"],
    )
    if lining_system_code:
        stmt = stmt.where(r.c["Lining_System_Code"] == lining_system_code)
    return {"items": _rows(await session.execute(stmt.order_by(r.c["id"])))}


@router.get("/sqm-progress", summary="SQM progress per equipment/system")
async def sqm_progress(site_id: Optional[str] = None, session: AsyncSession = Depends(get_session)):
    s = sme_sqm_t
    stmt = select(
        s.c["Site_ID"], s.c["Equipment_Tag_No"], s.c["Lining_System_Code"],
        s.c["Original_SQM"], s.c["Done_SQM"],
    )
    if site_id:
        stmt = stmt.where(s.c["Site_ID"] == site_id)
    # Explicit composite key order (no rowid) — SME Canon Rule 1.
    stmt = stmt.order_by(s.c["Site_ID"], s.c["Equipment_Tag_No"], s.c["Lining_System_Code"])
    return {"items": _rows(await session.execute(stmt))}


@router.get("/materials", summary="SME materials with derived Available_Qty")
async def materials(session: AsyncSession = Depends(get_session)):
    rows = _rows(await session.execute(text(SQL_SME_MATERIALS + ' ORDER BY s."Material_Code"')))
    return {"items": rows}
