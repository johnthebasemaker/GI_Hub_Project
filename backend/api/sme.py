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

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_level, resolve_site_param
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
async def summary(site_id: Optional[str] = None,
                  user: dict = Depends(require_level(2)),
                  session: AsyncSession = Depends(get_session)):
    # None → no filter (admin/logistics). '' (scoped user without a site) is
    # kept as a real filter value — it matches no rows, so it fails closed.
    site_id = resolve_site_param(user, site_id)
    eq = sme_equipment_t
    eqf = eq.c["Site_ID"] == site_id if site_id is not None else None

    def _w(stmt):
        return stmt.where(eqf) if eqf is not None else stmt

    equipment = (await session.execute(_w(select(func.count()).select_from(eq)))).scalar_one()
    total_sqm = (await session.execute(_w(select(func.coalesce(func.sum(eq.c["Equipment_Total_SQM"]), 0))))).scalar_one()
    recipes = (await session.execute(select(func.count()).select_from(sme_recipe_t))).scalar_one()
    materials = (await session.execute(select(func.count()).select_from(_MD.tables["sme_inventory_seed"]))).scalar_one()

    sqm = sme_sqm_t
    sqmf = sqm.c["Site_ID"] == site_id if site_id is not None else None
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
async def equipment(site_id: Optional[str] = None,
                    user: dict = Depends(require_level(2)),
                    session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    e = sme_equipment_t
    stmt = select(
        e.c["id"], e.c["Site_ID"], e.c["Equipment_Tag_No"], e.c["Name"], e.c["Location"],
        e.c["Type"], e.c["Substrate"], e.c["Lining_System_Code"], e.c["Lining_System"],
        e.c["Surface_Area_SQM"], e.c["Equipment_Total_SQM"],
    )
    if site_id is not None:
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
async def sqm_progress(site_id: Optional[str] = None,
                       user: dict = Depends(require_level(2)),
                       session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    s = sme_sqm_t
    stmt = select(
        s.c["Site_ID"], s.c["Equipment_Tag_No"], s.c["Lining_System_Code"],
        s.c["Original_SQM"], s.c["Done_SQM"],
    )
    if site_id is not None:
        stmt = stmt.where(s.c["Site_ID"] == site_id)
    # Explicit composite key order (no rowid) — SME Canon Rule 1.
    stmt = stmt.order_by(s.c["Site_ID"], s.c["Equipment_Tag_No"], s.c["Lining_System_Code"])
    return {"items": _rows(await session.execute(stmt))}


@router.get("/materials", summary="SME materials with derived Available_Qty")
async def materials(session: AsyncSession = Depends(get_session)):
    rows = _rows(await session.execute(text(SQL_SME_MATERIALS + ' ORDER BY s."Material_Code"')))
    return {"items": rows}


# --- Phase-8 read-parity (SME Canon: pure SELECT + in-memory compute) ----------
async def _equipment_report_rows(session: AsyncSession, site_id: str | None) -> list[dict]:
    """One row per equipment tag: systems, planned/done/remaining SQM, % done.
    Port of the legacy eq_master rollup; explicit tag ordering (Canon Rule 1)."""
    e = sme_equipment_t
    stmt = select(e.c["Equipment_Tag_No"], e.c["Name"], e.c["Location"],
                  e.c["Sub_Location"], e.c["Type"], e.c["Substrate"],
                  e.c["Lining_System_Code"], e.c["Lining_Type"],
                  e.c["Surface_Area_SQM"], e.c["Site_ID"])
    if site_id is not None:
        stmt = stmt.where(e.c["Site_ID"] == site_id)
    eq_rows = _rows(await session.execute(stmt.order_by(e.c["Equipment_Tag_No"], e.c["id"])))

    s = sme_sqm_t
    pstmt = select(s.c["Site_ID"], s.c["Equipment_Tag_No"], s.c["Lining_System_Code"],
                   s.c["Original_SQM"], s.c["Done_SQM"])
    if site_id is not None:
        pstmt = pstmt.where(s.c["Site_ID"] == site_id)
    prog = {(p["Site_ID"], p["Equipment_Tag_No"], p["Lining_System_Code"]): p
            for p in _rows(await session.execute(pstmt))}

    by_tag: dict[str, dict] = {}
    for r in eq_rows:
        tag = r["Equipment_Tag_No"]
        row = by_tag.setdefault(tag, {
            "Equipment_Tag_No": tag, "Name": r["Name"], "Location": r["Location"],
            "Sub_Location": r["Sub_Location"], "Type": r["Type"],
            "Substrate": r["Substrate"], "Lining_Type": r["Lining_Type"],
            "Systems": [], "Planned_SQM": 0.0, "Done_SQM": 0.0,
        })
        code = r["Lining_System_Code"]
        if code and code not in row["Systems"]:
            row["Systems"].append(code)
        p = prog.get((r["Site_ID"], tag, code))
        planned = float((p and p["Original_SQM"]) or r["Surface_Area_SQM"] or 0)
        done = float((p and p["Done_SQM"]) or 0)
        row["Planned_SQM"] += planned
        row["Done_SQM"] += done
    out = []
    for tag in sorted(by_tag):  # explicit deterministic order
        row = by_tag[tag]
        planned, done = row["Planned_SQM"], row["Done_SQM"]
        out.append({**row, "Systems": ", ".join(row["Systems"]),
                    "Planned_SQM": round(planned, 2), "Done_SQM": round(done, 2),
                    "Remaining_SQM": round(max(planned - done, 0), 2),
                    "Pct_Complete": round(100 * done / planned, 1) if planned else None})
    return out


@router.get("/equipment-report", summary="Per-tag rollup: systems + SQM progress + % done")
async def equipment_report(site_id: Optional[str] = None,
                           user: dict = Depends(require_level(2)),
                           session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    return {"items": await _equipment_report_rows(session, site_id)}


SQL_SME_COMPARISON = '''
    SELECT l."Material_Code",
           MIN(r.mname) AS "Material_Name", MIN(r.uom) AS "UOM",
           ROUND(CAST(SUM(l."SQM_Completed") AS NUMERIC), 3) AS "SQM_Completed",
           ROUND(CAST(SUM(l."Expected_Qty") AS NUMERIC), 3) AS "Expected_Qty",
           ROUND(CAST(SUM(l."Actual_Qty") AS NUMERIC), 3) AS "Actual_Qty",
           ROUND(CAST(SUM(l."Actual_Qty") - SUM(l."Expected_Qty") AS NUMERIC), 3) AS "Variance",
           CASE WHEN SUM(l."Expected_Qty") > 0
                THEN ROUND(CAST(100.0 * (SUM(l."Actual_Qty") - SUM(l."Expected_Qty"))
                           / SUM(l."Expected_Qty") AS NUMERIC), 1) END AS "Variance_Pct",
           COUNT(*) FILTER (WHERE l.status = 'committed') AS "Committed_Rows",
           COUNT(*) FILTER (WHERE l.status = 'pending') AS "Pending_Rows",
           COUNT(*) FILTER (WHERE l.status = 'rejected') AS "Rejected_Rows"
    FROM sme_consumption_log l
    LEFT JOIN (SELECT "Material_Code", MIN("Material_Name") AS mname, MIN("UOM") AS uom
               FROM sme_recipe GROUP BY "Material_Code") r
      ON r."Material_Code" = l."Material_Code"
    {where}
    GROUP BY l."Material_Code"
    ORDER BY l."Material_Code"'''


async def _comparison_rows(session: AsyncSession, site_id: str | None) -> list[dict]:
    where, params = "", {}
    if site_id is not None:
        where = 'WHERE l."Site_ID" = :site'
        params["site"] = site_id
    return _rows(await session.execute(
        text(SQL_SME_COMPARISON.format(where=where)), params))


@router.get("/consumption-comparison",
            summary="Planned (recipe-expected) vs actual per material")
async def consumption_comparison(site_id: Optional[str] = None,
                                 user: dict = Depends(require_level(2)),
                                 session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    return {"items": await _comparison_rows(session, site_id)}


def _syskey(code) -> tuple:
    """Numeric-first sort for lining-system codes (legacy sorted by int(code))."""
    s = str(code or "")
    return (0, int(s)) if s.isdigit() else (1, s)


async def _demand_matrix(session: AsyncSession, site_id: str | None) -> dict:
    """Read-only port of the legacy allocation engine. Demand per (equipment ×
    recipe material) = remaining SQM × For_1_SQM, then a cascade allocation
    against the derived available pool. The legacy drag-to-prioritize order is
    interactive UI state — the port allocates in FIXED order (tag asc, system
    numeric asc), which is documented in the response."""
    e = sme_equipment_t
    stmt = select(e.c["Site_ID"], e.c["Equipment_Tag_No"], e.c["Lining_System_Code"],
                  e.c["Lining_System_Short_Name"], e.c["Surface_Area_SQM"])
    if site_id is not None:
        stmt = stmt.where(e.c["Site_ID"] == site_id)
    eq_rows = _rows(await session.execute(stmt.order_by(e.c["Equipment_Tag_No"], e.c["id"])))

    s = sme_sqm_t
    pstmt = select(s.c["Site_ID"], s.c["Equipment_Tag_No"], s.c["Lining_System_Code"],
                   s.c["Original_SQM"], s.c["Done_SQM"])
    if site_id is not None:
        pstmt = pstmt.where(s.c["Site_ID"] == site_id)
    prog = {(p["Site_ID"], p["Equipment_Tag_No"], p["Lining_System_Code"]): p
            for p in _rows(await session.execute(pstmt))}

    r = sme_recipe_t
    rec_rows = _rows(await session.execute(
        select(r.c["Lining_System_Code"], r.c["Material_Code"], r.c["Material_Name"],
               r.c["UOM"], r.c["For_1_SQM"]).order_by(r.c["id"])))
    recipes: dict[str, list[dict]] = {}
    for rr in rec_rows:
        recipes.setdefault(str(rr["Lining_System_Code"]), []).append(rr)

    pool = {m["material_code"]: float(m["available_qty"] or 0)
            for m in _rows(await session.execute(text(SQL_SME_MATERIALS)))}

    # Deterministic cascade order: tag asc, then system code numeric asc.
    eq_rows.sort(key=lambda x: (str(x["Equipment_Tag_No"]), _syskey(x["Lining_System_Code"])))
    lines, totals = [], {}
    for eq in eq_rows:
        p = prog.get((eq["Site_ID"], eq["Equipment_Tag_No"], eq["Lining_System_Code"]))
        if p is not None:
            remaining = max(float(p["Original_SQM"] or 0) - float(p["Done_SQM"] or 0), 0.0)
        else:
            remaining = float(eq["Surface_Area_SQM"] or 0)
        if remaining <= 0:
            continue
        for rr in recipes.get(str(eq["Lining_System_Code"]), []):
            mat = rr["Material_Code"]
            demand = remaining * float(rr["For_1_SQM"] or 0)
            if demand <= 0:
                continue
            before = pool.get(mat, 0.0)
            alloc = min(demand, before)
            pool[mat] = max(0.0, before - alloc)
            lines.append({
                "Equipment_Tag_No": eq["Equipment_Tag_No"],
                "Lining_System_Code": eq["Lining_System_Code"],
                "Lining_System_Short_Name": eq["Lining_System_Short_Name"],
                "Remaining_SQM": round(remaining, 2),
                "Material_Code": mat, "Material_Name": rr["Material_Name"],
                "UOM": rr["UOM"],
                "Demand_Qty": round(demand, 4),
                "Allocated_Qty": round(alloc, 4),
                "Shortfall_Qty": round(demand - alloc, 4),
                "Pool_Before": round(before, 4),
                "Pool_After": round(pool[mat], 4),
            })
            t = totals.setdefault(mat, {"Material_Code": mat,
                                        "Material_Name": rr["Material_Name"],
                                        "UOM": rr["UOM"], "Demand_Qty": 0.0,
                                        "Allocated_Qty": 0.0, "Shortfall_Qty": 0.0})
            t["Demand_Qty"] += demand
            t["Allocated_Qty"] += alloc
            t["Shortfall_Qty"] += demand - alloc
    totals_rows = [{**t, "Demand_Qty": round(t["Demand_Qty"], 3),
                    "Allocated_Qty": round(t["Allocated_Qty"], 3),
                    "Shortfall_Qty": round(t["Shortfall_Qty"], 3)}
                   for _, t in sorted(totals.items())]
    return {"lines": lines, "totals": totals_rows,
            "allocation_order": "Equipment_Tag_No asc, system code numeric asc "
                                "(fixed read-only port of the drag-priority engine)"}


@router.get("/demand-matrix", summary="SQM demand cascaded to per-material quantities")
async def demand_matrix(site_id: Optional[str] = None,
                        user: dict = Depends(require_level(2)),
                        session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    return await _demand_matrix(session, site_id)


# --- exports (reuse the reports renderers; still pure reads) -------------------
@router.get("/export/{key}", summary="Export an SME view (xlsx | csv | pdf)")
async def sme_export(key: str, format: str = "xlsx", site_id: Optional[str] = None,
                     user: dict = Depends(require_level(2)),
                     session: AsyncSession = Depends(get_session)):
    import io

    from fastapi.responses import StreamingResponse

    from .reports import _FORMATS
    fmt = format.lower()
    if fmt not in _FORMATS:
        raise HTTPException(400, f"format must be one of {sorted(_FORMATS)}")
    site_id = resolve_site_param(user, site_id)

    if key == "equipment-report":
        title, items = "SME Equipment Report", await _equipment_report_rows(session, site_id)
    elif key == "consumption-comparison":
        title, items = "SME Consumption Comparison", await _comparison_rows(session, site_id)
    elif key == "demand-matrix":
        title, items = "SME Demand Matrix", (await _demand_matrix(session, site_id))["lines"]
    elif key == "demand-totals":
        title, items = "SME Demand Totals (Net Order List)", (await _demand_matrix(session, site_id))["totals"]
    elif key == "materials":
        title = "SME Materials (Available Qty)"
        items = _rows(await session.execute(text(SQL_SME_MATERIALS + ' ORDER BY s."Material_Code"')))
    else:
        raise HTTPException(404, f"unknown SME export {key!r}")

    columns = list(items[0].keys()) if items else []
    rows = [[r.get(c) for c in columns] for r in items]
    render, media = _FORMATS[fmt]
    data = render(title, columns, rows, user["username"])
    return StreamingResponse(io.BytesIO(data), media_type=media,
                             headers={"Content-Disposition":
                                      f'attachment; filename="sme-{key}.{fmt}"'})
