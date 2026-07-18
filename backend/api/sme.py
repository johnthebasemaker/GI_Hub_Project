"""
backend/api/sme.py — SME Material Estimator (reads + pure compute).

This module only READS the sme_* tables — it never writes them. Master Data
writes live in backend/api/sme_master.py (Phase S6, unlocked at cutover);
everything here stays a pure read so the analytics/engine surface is
side-effect-free.
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
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from . import sme_engine
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


# --- Phase S1: model snapshot + cascade oracle (still pure reads) --------------
# The snapshot feeds the client-side TypeScript engine (frontend/src/sme/
# engine.ts); the cascade endpoint runs the SAME algorithm server-side via
# backend/api/sme_engine.py — it is the parity oracle for the TS port and the
# compute backend for session-scoped exports (Phase S3). POST here is compute,
# not mutation: nothing is written (S6 writes live in sme_master.py only).

class CascadeBody(BaseModel):
    priority_order: list[str] = Field(default_factory=list, max_length=2000)
    site_id: Optional[str] = None
    include_suggestions: bool = False


async def _snapshot_rows(session: AsyncSession, site_id: str | None) -> dict:
    """The four raw inputs of the legacy load_all(), snapshot-shaped."""
    e = sme_equipment_t
    stmt = select(e.c["Equipment_Tag_No"], e.c["Name"], e.c["Location"],
                  e.c["Sub_Location"], e.c["Type"], e.c["Substrate"],
                  e.c["Lining_System_Code"], e.c["Surface_Area_SQM"])
    if site_id is not None:
        stmt = stmt.where(e.c["Site_ID"] == site_id)
    equipment = _rows(await session.execute(stmt.order_by(e.c["Equipment_Tag_No"], e.c["id"])))

    r = sme_recipe_t
    recipes = _rows(await session.execute(
        select(r.c["Lining_System_Code"], r.c["Lining_System_Name"],
               r.c["Material_Code"], r.c["Material_Name"], r.c["UOM"],
               r.c["For_1_SQM"]).order_by(r.c["id"])))

    materials = [{k: m[k] for k in ("material_code", "material_name", "nature",
                                    "uom", "available_qty", "ordered_qty")}
                 for m in _rows(await session.execute(
                     text(SQL_SME_MATERIALS + ' ORDER BY s."Material_Code"')))]

    s = sme_sqm_t
    # Legacy load_all() folds Done_SQM_staged into done; keep both raw here and
    # let the engine fold (guard: the mirror schema may predate the R18 column).
    staged = s.c["Done_SQM_staged"] if "Done_SQM_staged" in s.c else None
    cols = [s.c["Equipment_Tag_No"], s.c["Lining_System_Code"],
            s.c["Original_SQM"], s.c["Done_SQM"]]
    if staged is not None:
        cols.append(staged)
    pstmt = select(*cols)
    if site_id is not None:
        pstmt = pstmt.where(s.c["Site_ID"] == site_id)
    progress = _rows(await session.execute(
        pstmt.order_by(s.c["Site_ID"], s.c["Equipment_Tag_No"], s.c["Lining_System_Code"])))
    if staged is None:
        progress = [{**p, "Done_SQM_staged": 0} for p in progress]

    return {"equipment": equipment, "recipes": recipes,
            "materials": materials, "progress": progress}


@router.get("/model-snapshot",
            summary="Unified SME model payload for the client-side engine")
async def model_snapshot(site_id: Optional[str] = None,
                         user: dict = Depends(require_level(2)),
                         session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    snap = await _snapshot_rows(session, site_id)
    tags = sorted({str(e["Equipment_Tag_No"]).strip()
                   for e in snap["equipment"] if e["Equipment_Tag_No"]})
    return {"site_id": site_id, **snap, "default_order": tags}


@router.post("/plan/cascade",
             summary="Server-side cascade allocation for a given priority order "
                     "(read-only compute; parity oracle for the TS engine)")
async def plan_cascade(body: CascadeBody,
                       user: dict = Depends(require_level(2)),
                       session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, body.site_id)
    snap = await _snapshot_rows(session, site_id)
    model = sme_engine.build_model(snap["equipment"], snap["recipes"],
                                   snap["materials"], snap["progress"])
    result = sme_engine.run_plan(model, body.priority_order)
    if body.include_suggestions:
        result.update(sme_engine.run_suggestion_engine(model, body.priority_order))
    return {"site_id": site_id, **result}


# --- Phase S3: session exports rendered by the server oracle -------------------
# The React session builder holds the priority order client-side; official
# documents are rendered here so the Python engine (not the browser) is the
# authority on exported numbers. Still a pure read + compute.
_PLAN_EXPORT_KEYS = {
    "session-full": ("SME Session Report (priority order)", "lines"),
    "order-list": ("SME Session Order List (to procure)", "procurement"),
    "feasibility": ("SME Session Feasibility", "feasibility"),
    "overview": ("SME Total Overview (per equipment × system code)", "_overview"),
    # Legacy Tab 6 "Execution Order List": shortfall lines for ONE equipment
    # across all its codes (body.equipment_tag selects the tag).
    "execution-plan": ("SME Execution Plan (order list)", "_execution"),
    # Legacy Tab 3 "Location Report": alloc lines (optionally one location) in
    # the legacy workbook layout — main table + 3 summary blocks (T3).
    "location-report": ("SME Location Report", "_location"),
}


def _overview_rows(model: dict, plan: dict) -> list[dict]:
    """Per-(tag, code) rollup of oracle cascade lines for the Total Overview
    grid — presentation aggregation, deliberately outside the parity-locked
    engine (mirrors frontend/src/sme/session.ts codeStats + progress meta)."""
    acc: dict[tuple[str, str], dict] = {}
    for ln in plan["lines"]:
        k = (ln["Equipment_Tag_No"], ln["Lining_System_Code"])
        a = acc.setdefault(k, {"demand": 0.0, "alloc": 0.0, "short": 0.0,
                               "min_rate": 1.0})
        a["demand"] += ln["Demand_Qty"]
        a["alloc"] += ln["Allocated_Qty"]
        a["short"] += ln["Shortfall_Qty"]
        # 2026-07-07 STRICT BOTTLENECK ruling: the unit's coverage is its
        # least-available material, never the alloc/demand average.
        rate = (min(1.0, ln["Allocated_Qty"] / ln["Demand_Qty"])
                if ln["Demand_Qty"] > 0 else 1.0)
        if rate < a["min_rate"]:
            a["min_rate"] = rate
    out, sno = [], 0
    for tag in plan["order_used"]:
        meta = model["tag_meta"].get(tag, {})
        for code in model["codes_by_tag"].get(tag, []):
            u = model["units"][(tag, code)]
            a = acc.get((tag, code), {"demand": 0.0, "alloc": 0.0, "short": 0.0,
                                      "min_rate": 1.0})
            pct = a["min_rate"] * 100.0 if a["demand"] > 0 else 100.0
            sno += 1
            out.append({
                "S_No": sno, "Equipment_Tag_No": tag, "Name": meta.get("Name", ""),
                "Substrate": meta.get("Substrate", ""), "Type": meta.get("Type", ""),
                "Location": meta.get("Location", ""), "Lining_System_Code": code,
                "System_Name": u["short_name"],
                "Total_SQM": round(u["total_original"], 2),
                "Done_SQM": round(u["done"], 2),
                "Remaining_SQM": round(u["remaining"], 2),
                "Total_Demand": round(a["demand"], 3),
                "Allocated": round(a["alloc"], 3),
                "Shortfall_Qty": round(a["short"], 3),
                "Fulfillment_Pct": round(pct, 1),
            })
    return out


class PlanExportBody(CascadeBody):
    key: str = "session-full"
    format: str = "xlsx"
    # Optional document title override (e.g. "SME Location Report — TRAIN J")
    # so per-location/per-scope exports are self-describing.
    title: Optional[str] = Field(default=None, max_length=80)
    # execution-plan key: which equipment's shortfall lines to export.
    equipment_tag: Optional[str] = Field(default=None, max_length=120)
    # location-report key: restrict to one location ("" / None → all equipment).
    location: Optional[str] = Field(default=None, max_length=120)


def _fname_part(s: str) -> str:
    """Filename-safe fragment (legacy replaced '/' in tags)."""
    return "".join(c if c.isalnum() or c in "._-" else "-" for c in str(s)).strip("-") or "x"


@router.post("/plan/export",
             summary="Export the client's session plan (xlsx | csv | pdf), "
                     "computed server-side by the parity oracle")
async def plan_export(body: PlanExportBody,
                      user: dict = Depends(require_level(2)),
                      session: AsyncSession = Depends(get_session)):
    import io

    from fastapi.responses import StreamingResponse

    from .reports import _FORMATS
    fmt = body.format.lower()
    if fmt not in _FORMATS:
        raise HTTPException(400, f"format must be one of {sorted(_FORMATS)}")
    if body.key not in _PLAN_EXPORT_KEYS:
        raise HTTPException(404, f"unknown plan export {body.key!r}")
    site_id = resolve_site_param(user, body.site_id)
    snap = await _snapshot_rows(session, site_id)
    model = sme_engine.build_model(snap["equipment"], snap["recipes"],
                                   snap["materials"], snap["progress"])
    plan = sme_engine.run_plan(model, body.priority_order)
    from .sme_export_layouts import legacy_filename, loc_scheme, location_report_xlsx
    uname = user["username"]
    title, part = _PLAN_EXPORT_KEYS[body.key]
    # Legacy filename stems per report (convention: {stem}_{user}_{date}.{ext}).
    _STEMS = {"session-full": "session_full_report", "order-list": "order_list",
              "feasibility": "session_feasibility", "overview": "total_overview"}
    fname = legacy_filename(_STEMS.get(body.key, body.key), uname, fmt)
    if part == "_overview":
        items = _overview_rows(model, plan)
    elif part == "_execution":
        tag = (body.equipment_tag or "").strip()
        if not tag:
            raise HTTPException(400, "execution-plan export needs equipment_tag")
        items = [ln for ln in plan["lines"]
                 if ln["Equipment_Tag_No"] == tag and ln["Shortfall_Qty"] > 0]
        title = f"SME Execution Plan — {tag} (order list)"
        fname = legacy_filename(f"execution_plan_{_fname_part(tag)}", uname, fmt)
    elif part == "_location":
        loc = (body.location or "").strip()
        if loc:
            loc_tags = {t for t, m in model["tag_meta"].items()
                        if (m.get("Location") or "") == loc}
            items = [ln for ln in plan["lines"] if ln["Equipment_Tag_No"] in loc_tags]
        else:
            items = plan["lines"]
        scope = loc or "All Equipment"
        title = f"SME Location Report — {scope}"
        fname = legacy_filename(
            f"location_report_{_fname_part(scope.lower().replace(' ', '_'))}", uname, fmt)
        if fmt == "xlsx":  # legacy workbook: main alloc table + 3 summary blocks
            cols = ["Equipment_Tag_No", "Lining_System_Code",
                    "Lining_System_Short_Name", "Total_SQM", "Material_Code",
                    "Material_Name", "UOM", "Demand_Qty", "Allocated_Qty",
                    "Shortfall_Qty", "Fulfillment_Pct"]
            data = location_report_xlsx(
                [{"name": scope, "title": body.title or title,
                  "color_scheme": loc_scheme(loc or None),
                  "columns": cols, "rows": items}], username=uname)
            return StreamingResponse(io.BytesIO(data),
                                     media_type=_FORMATS["xlsx"][1],
                                     headers={"Content-Disposition":
                                              f'attachment; filename="{fname}"'})
    else:
        items = plan[part]
    if body.title and body.title.strip():
        title = body.title.strip()
    columns = list(items[0].keys()) if items else []
    rows = [[r.get(c) for c in columns] for r in items]
    render, media = _FORMATS[fmt]
    data = render(title, columns, rows, user["username"])
    return StreamingResponse(io.BytesIO(data), media_type=media,
                             headers={"Content-Disposition":
                                      f'attachment; filename="{fname}"'})


# --- Phase S5: production log + progress list (Execution Plan reads) -----------
# Pure reads over sme_consumption_log / sme_sqm_progress per the SME Canon.
# The legacy `consumption_log` VIEW exposes committed rows only — same here.
sme_log_t = _MD.tables["sme_consumption_log"]


async def _production_log_rows(session: AsyncSession, site_id: str | None,
                               equipment_tag: str | None = None,
                               lining_system_code: str | None = None) -> list[dict]:
    l = sme_log_t
    stmt = select(l.c["entry_date"], l.c["Equipment_Tag_No"], l.c["Lining_System_Code"],
                  l.c["Material_Code"], l.c["SQM_Completed"], l.c["Expected_Qty"],
                  l.c["Actual_Qty"]).where(l.c["status"] == "committed")
    if site_id is not None:
        stmt = stmt.where(l.c["Site_ID"] == site_id)
    if equipment_tag:
        stmt = stmt.where(l.c["Equipment_Tag_No"] == equipment_tag)
    if lining_system_code:
        stmt = stmt.where(l.c["Lining_System_Code"] == lining_system_code)
    stmt = stmt.order_by(l.c["entry_date"], l.c["Equipment_Tag_No"],
                         l.c["Lining_System_Code"], l.c["Material_Code"], l.c["id"])
    return _rows(await session.execute(stmt))


@router.get("/production-log",
            summary="Committed SME consumption entries (date-wise production detail)")
async def production_log(site_id: Optional[str] = None,
                         equipment_tag: Optional[str] = None,
                         lining_system_code: Optional[str] = None,
                         user: dict = Depends(require_level(2)),
                         session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    return {"items": await _production_log_rows(session, site_id,
                                                equipment_tag, lining_system_code)}


async def _progress_list_rows(session: AsyncSession, site_id: str | None) -> list[dict]:
    """Legacy Progress List: PROGRESS-driven (one row per sqm-progress entry,
    equipment meta LEFT-joined) — the legacy portal reads `FROM sqm_progress
    LEFT JOIN equipment`, so scopes entered directly in the SQM editor (e.g.
    work areas with no equipment-master row) must still appear here. Done
    includes the staged column (matches load_all's in-flight view)."""
    snap = await _snapshot_rows(session, site_id)
    model = sme_engine.build_model(snap["equipment"], snap["recipes"],
                                   snap["materials"], snap["progress"])
    short_names: dict[str, str] = {}
    for r in snap["recipes"]:
        code = str(r.get("Lining_System_Code") or "").strip()
        if code and code not in short_names:
            short_names[code] = str(r.get("Lining_System_Name") or "").strip()
    out = []
    for p in snap["progress"]:
        tag = str(p.get("Equipment_Tag_No") or "").strip()
        code = str(p.get("Lining_System_Code") or "").strip()
        meta = model["tag_meta"].get(tag, {})
        total = float(p.get("Original_SQM") or 0)
        done = (float(p.get("Done_SQM") or 0)
                + float(p.get("Done_SQM_staged") or 0))
        pct = round(100 * done / total, 1) if total > 0 else 0.0
        status = ("✅ Complete" if pct >= 100 else
                  "🔄 In Progress" if done > 0 else "⏳ Not Started")
        out.append({"Location": meta.get("Location", ""),
                    "Equipment_Tag_No": tag,
                    "Name": meta.get("Name", ""),
                    "Lining_System_Code": code,
                    "System_Name": short_names.get(code, ""),
                    "Total_SQM": round(total, 2),
                    "Completed_SQM": round(done, 2),
                    "Remaining_SQM": round(max(total - done, 0), 2),
                    "Completion_Pct": pct,
                    "Status": status})
    out.sort(key=lambda r: (r["Location"], r["Equipment_Tag_No"],
                            _syskey(r["Lining_System_Code"])))
    return out


# --- Phase S4: system-code matrix rollup (inverse of the equipment report) -----
async def _system_code_report_rows(session: AsyncSession, site_id: str | None) -> list[dict]:
    """One row per lining-system code: short name, equipment count, total
    (original) SQM — the legacy System Code Report summary."""
    e = sme_equipment_t
    stmt = select(e.c["Equipment_Tag_No"], e.c["Lining_System_Code"], e.c["Surface_Area_SQM"])
    if site_id is not None:
        stmt = stmt.where(e.c["Site_ID"] == site_id)
    eq_rows = _rows(await session.execute(stmt.order_by(e.c["Equipment_Tag_No"], e.c["id"])))

    r = sme_recipe_t
    names: dict[str, str] = {}
    for rr in _rows(await session.execute(
            select(r.c["Lining_System_Code"], r.c["Lining_System_Name"]).order_by(r.c["id"]))):
        names.setdefault(str(rr["Lining_System_Code"]).strip(),
                         str(rr["Lining_System_Name"] or "").strip())

    acc: dict[str, dict] = {}
    for row in eq_rows:
        code = str(row["Lining_System_Code"] or "").strip()
        a = acc.setdefault(code, {"System_Code": code,
                                  "Short_Name": names.get(code, ""),
                                  "_tags": set(), "Total_SQM": 0.0})
        a["_tags"].add(str(row["Equipment_Tag_No"]).strip())
        a["Total_SQM"] += float(row["Surface_Area_SQM"] or 0)
    out = []
    for code in sorted(acc, key=_syskey):
        a = acc[code]
        out.append({"System_Code": code, "Short_Name": a["Short_Name"],
                    "Equipment_Count": len(a["_tags"]),
                    "Total_SQM": round(a["Total_SQM"], 2)})
    return out


# --- scoped export row builders (legacy per-tag / per-code files) --------------
async def _equipment_detail_rows(session: AsyncSession, site_id: str | None,
                                 tag: str) -> list[dict]:
    """Per-code breakdown for ONE equipment — the legacy `equipment_{tag}` file."""
    e = sme_equipment_t
    stmt = select(e.c["Site_ID"], e.c["Lining_System_Code"],
                  e.c["Lining_System_Short_Name"], e.c["Lining_Area_Location"],
                  e.c["Surface_Area_SQM"]).where(e.c["Equipment_Tag_No"] == tag)
    if site_id is not None:
        stmt = stmt.where(e.c["Site_ID"] == site_id)
    eq_rows = _rows(await session.execute(stmt.order_by(e.c["Lining_System_Code"], e.c["id"])))

    s = sme_sqm_t
    pstmt = select(s.c["Site_ID"], s.c["Lining_System_Code"], s.c["Original_SQM"],
                   s.c["Done_SQM"]).where(s.c["Equipment_Tag_No"] == tag)
    if site_id is not None:
        pstmt = pstmt.where(s.c["Site_ID"] == site_id)
    prog = {(p["Site_ID"], p["Lining_System_Code"]): p
            for p in _rows(await session.execute(pstmt))}

    out = []
    for r in sorted(eq_rows, key=lambda x: _syskey(x["Lining_System_Code"])):
        code = r["Lining_System_Code"]
        p = prog.get((r["Site_ID"], code))
        planned = float((p and p["Original_SQM"]) or r["Surface_Area_SQM"] or 0)
        done = float((p and p["Done_SQM"]) or 0)
        out.append({"Lining_System_Code": code,
                    "System": r["Lining_System_Short_Name"] or "",
                    "Areas": r["Lining_Area_Location"] or "",
                    "Total_SQM": round(planned, 2), "Done_SQM": round(done, 2),
                    "Remaining_SQM": round(max(planned - done, 0), 2),
                    "Pct_Complete": round(100 * done / planned, 1) if planned else None})
    return out


async def _code_equipment_rows(session: AsyncSession, site_id: str | None,
                               code: str) -> list[dict]:
    """Equipment carrying ONE lining-system code — legacy `system_code_{code}`."""
    e = sme_equipment_t
    stmt = select(e.c["Location"], e.c["Type"], e.c["Equipment_Tag_No"],
                  e.c["Name"], e.c["Substrate"], e.c["Surface_Area_SQM"]) \
        .where(e.c["Lining_System_Code"] == code)
    if site_id is not None:
        stmt = stmt.where(e.c["Site_ID"] == site_id)
    rows = _rows(await session.execute(stmt.order_by(e.c["Equipment_Tag_No"], e.c["id"])))
    return [{"Location": r["Location"] or "", "Type": r["Type"] or "",
             "Equipment_Tag_No": r["Equipment_Tag_No"], "Name": r["Name"] or "",
             "Substrate": r["Substrate"] or "",
             "Total_SQM": round(float(r["Surface_Area_SQM"] or 0), 2)} for r in rows]


def _tabular(items: list[dict]) -> tuple[list, list]:
    columns = list(items[0].keys()) if items else []
    return columns, [[r.get(c) for c in columns] for r in items]


async def _equipment_matrix_rows(session: AsyncSession, site_id: str | None,
                                 tag: str | None = None,
                                 location: str | None = None) -> list[dict]:
    """One row per (tag × code) with the LEGACY display column names — the
    detail rows the legacy Equipment Report workbook is built from."""
    e = sme_equipment_t
    stmt = select(e.c["Site_ID"], e.c["Location"], e.c["Type"],
                  e.c["Equipment_Tag_No"], e.c["Name"], e.c["Lining_System_Code"],
                  e.c["Lining_System_Short_Name"], e.c["Surface_Area_SQM"])
    if site_id is not None:
        stmt = stmt.where(e.c["Site_ID"] == site_id)
    if tag:
        stmt = stmt.where(e.c["Equipment_Tag_No"] == tag)
    if location:
        stmt = stmt.where(e.c["Location"] == location)
    eq_rows = _rows(await session.execute(stmt.order_by(e.c["Equipment_Tag_No"], e.c["id"])))

    s = sme_sqm_t
    pstmt = select(s.c["Site_ID"], s.c["Equipment_Tag_No"], s.c["Lining_System_Code"],
                   s.c["Original_SQM"])
    if site_id is not None:
        pstmt = pstmt.where(s.c["Site_ID"] == site_id)
    prog = {(p["Site_ID"], p["Equipment_Tag_No"], p["Lining_System_Code"]): p
            for p in _rows(await session.execute(pstmt))}

    out = []
    for r in sorted(eq_rows, key=lambda x: (str(x["Equipment_Tag_No"]),
                                            _syskey(x["Lining_System_Code"]))):
        p = prog.get((r["Site_ID"], r["Equipment_Tag_No"], r["Lining_System_Code"]))
        planned = float((p and p["Original_SQM"]) or r["Surface_Area_SQM"] or 0)
        out.append({"Location": r["Location"] or "", "Type": r["Type"] or "",
                    "Equipment Tag No.": r["Equipment_Tag_No"],
                    "Equipment Name": r["Name"] or "",
                    "System Code": r["Lining_System_Code"],
                    "System Name": r["Lining_System_Short_Name"] or "",
                    "Total SQM": round(planned, 2)})
    return out


# --- exports (reuse the reports renderers; still pure reads) -------------------
@router.get("/export/{key}", summary="Export an SME view (xlsx | csv | pdf); "
            "optional tag / location / code narrow the scope (legacy per-"
            "section downloads)")
async def sme_export(key: str, format: str = "xlsx", site_id: Optional[str] = None,
                     tag: Optional[str] = None, location: Optional[str] = None,
                     code: Optional[str] = None,
                     user: dict = Depends(require_level(2)),
                     session: AsyncSession = Depends(get_session)):
    import io
    from datetime import date as _date

    from fastapi.responses import StreamingResponse

    from .reports import _FORMATS, to_pdf_sheets, to_xlsx_sheets
    from .sme_export_layouts import (equipment_report_xlsx, legacy_filename,
                                     loc_scheme, single_table_xlsx)
    fmt = format.lower()
    if fmt not in _FORMATS:
        raise HTTPException(400, f"format must be one of {sorted(_FORMATS)}")
    site_id = resolve_site_param(user, site_id)
    uname = user["username"]

    def _resp(data: bytes, media: str, filename: str):
        return StreamingResponse(io.BytesIO(data), media_type=media,
                                 headers={"Content-Disposition":
                                          f'attachment; filename="{filename}"'})

    # sheets: multi-sheet xlsx / sectioned pdf when set (legacy file layout).
    sheets: list[tuple] | None = None
    fname: str | None = None

    if key == "equipment-report":
        # xlsx → the LEGACY workbook layout (T3 blueprint: 5-row header +
        # Summary by Equipment · Summary by System Code · Detailed Table).
        if tag:  # one equipment (legacy per-tag button)
            title = f"Equipment Report — {tag}"
            matrix = await _equipment_matrix_rows(session, site_id, tag=tag)
            fname = legacy_filename(f"equipment_{_fname_part(tag)}", uname, fmt)
            if fmt == "xlsx":
                scheme = loc_scheme(matrix[0]["Location"] if matrix else None)
                data = equipment_report_xlsx([{"name": str(tag), "title": title,
                                               "color_scheme": scheme, "rows": matrix}],
                                             username=uname)
                return _resp(data, _FORMATS["xlsx"][1], fname)
            items = await _equipment_detail_rows(session, site_id, tag)
        elif location:  # one location (legacy per-location button)
            title = f"Equipment Report — {location}"
            fname = legacy_filename(f"equipment_report_{_fname_part(location)}", uname, fmt)
            if fmt == "xlsx":
                matrix = await _equipment_matrix_rows(session, site_id, location=location)
                data = equipment_report_xlsx([{"name": location, "title": title,
                                               "color_scheme": loc_scheme(location),
                                               "rows": matrix}], username=uname)
                return _resp(data, _FORMATS["xlsx"][1], fname)
            items = [r for r in await _equipment_report_rows(session, site_id)
                     if (r["Location"] or "") == location]
        else:  # all — legacy multi-sheet: per-location + All Equipment + codes
            title = "Equipment Report — All Equipment"
            fname = legacy_filename("equipment_report_all", uname, fmt)
            if fmt == "xlsx":
                matrix = await _equipment_matrix_rows(session, site_id)
                locs: dict[str, list[dict]] = {}
                for r in matrix:
                    locs.setdefault(r["Location"] or "—", []).append(r)
                loc_sheets = [{"name": loc, "title": f"Equipment Report — {loc}",
                               "color_scheme": loc_scheme(loc), "rows": rs}
                              for loc, rs in sorted(locs.items())]
                data = equipment_report_xlsx(
                    loc_sheets,
                    all_eq_sheet={"name": "All Equipment",
                                  "title": "Equipment Report — All Equipment",
                                  "color_scheme": "dashboard", "rows": matrix},
                    include_all_codes_sheet=True, username=uname)
                return _resp(data, _FORMATS["xlsx"][1], fname)
            items = await _equipment_report_rows(session, site_id)
            if fmt == "pdf":
                locs2: dict[str, list[dict]] = {}
                for r in items:
                    locs2.setdefault(r["Location"] or "—", []).append(r)
                sheets = [(loc, *_tabular(rs)) for loc, rs in sorted(locs2.items())]
                sheets.append(("All Equipment", *_tabular(items)))
                sheets.append(("All System Codes",
                               *_tabular(await _system_code_report_rows(session, site_id))))
    elif key == "system-code-report":
        if code:  # one code (legacy per-code button)
            title = f"System Code Report — Code {code}"
            items = await _code_equipment_rows(session, site_id, code)
            fname = legacy_filename(f"system_code_{_fname_part(code)}", uname, fmt)
            if fmt == "xlsx":
                cols, _ = _tabular(items)
                data = single_table_xlsx([{"name": f"Code {code}", "title": title,
                                           "color_scheme": "overview",
                                           "columns": cols, "rows": items}],
                                         username=uname)
                return _resp(data, _FORMATS["xlsx"][1], fname)
        else:  # all — legacy multi-sheet: summary + one sheet per code
            title = "System Code Report"
            items = await _system_code_report_rows(session, site_id)
            fname = legacy_filename("system_code_report", uname, fmt)
            if fmt == "xlsx":
                specs = [{"name": "Summary", "title": "System Code Report — Summary",
                          "color_scheme": "overview",
                          "columns": _tabular(items)[0], "rows": items}]
                for srow in items:
                    c = srow["System_Code"]
                    crows = await _code_equipment_rows(session, site_id, c)
                    specs.append({"name": f"Code {c}",
                                  "title": f"System Code Report — Code {c} "
                                           f"({srow['Short_Name'] or '—'})",
                                  "color_scheme": "overview",
                                  "columns": _tabular(crows)[0], "rows": crows})
                data = single_table_xlsx(specs, username=uname)
                return _resp(data, _FORMATS["xlsx"][1], fname)
            if fmt == "pdf":
                sheets = [("Summary", *_tabular(items))]
                for srow in items:
                    c = srow["System_Code"]
                    sheets.append((f"Code {c}",
                                   *_tabular(await _code_equipment_rows(session, site_id, c))))
    elif key == "progress-list":
        # Legacy multi-sheet: Progress List + per-(tag, code) production detail.
        title = "SME Progress List"
        items = await _progress_list_rows(session, site_id)
        fname = legacy_filename("progress_list", uname, fmt)
        if fmt in ("xlsx", "pdf"):
            sheets = [("Progress List", *_tabular(items))]
            log = await _production_log_rows(session, site_id)
            by_unit: dict[tuple[str, str], list[dict]] = {}
            for ln in log:
                by_unit.setdefault((ln["Equipment_Tag_No"], ln["Lining_System_Code"]),
                                   []).append(ln)
            for i, ((t, c), lns) in enumerate(sorted(by_unit.items()), 1):
                sheets.append((f"{i}. {str(t)[:8]}-{c}", *_tabular(lns)))
    elif key == "consumption-comparison":
        title = "SME Consumption Comparison"
        items = await _comparison_rows(session, site_id)
        fname = legacy_filename("consumption_comparison", uname, fmt)
    elif key == "production-log":
        title = "SME Production Log (committed)"
        items = await _production_log_rows(session, site_id)
        fname = legacy_filename("consumption_log_full", uname, fmt)
    elif key == "demand-matrix":
        title, items = "SME Demand Matrix", (await _demand_matrix(session, site_id))["lines"]
    elif key == "demand-totals":
        title, items = "SME Demand Totals (Net Order List)", (await _demand_matrix(session, site_id))["totals"]
    elif key == "materials":
        title = "SME Materials (Available Qty)"
        items = _rows(await session.execute(text(SQL_SME_MATERIALS + ' ORDER BY s."Material_Code"')))
    else:
        raise HTTPException(404, f"unknown SME export {key!r}")

    render, media = _FORMATS[fmt]
    if sheets is not None and fmt == "xlsx":
        data = to_xlsx_sheets(sheets, user["username"])
    elif sheets is not None and fmt == "pdf":
        data = to_pdf_sheets(title, sheets, user["username"])
    else:
        columns, rows = _tabular(items)
        data = render(title, columns, rows, user["username"])
    return StreamingResponse(io.BytesIO(data), media_type=media,
                             headers={"Content-Disposition":
                                      f'attachment; filename="{fname or f"sme-{key}.{fmt}"}"'})


# --- generic client-view renderer (Dashboard / filtered-view parity) ------------
class RowsExportBody(BaseModel):
    """Render rows the CLIENT computed (dashboard filters, variance views) into
    a document — the legacy portal exported the displayed frame verbatim.
    Pure formatting: no DB access, no writes; capped to keep it a document
    renderer rather than a bulk channel."""
    title: str = Field(max_length=120)
    columns: list[str] = Field(max_length=60)
    rows: list[list[Optional[str | int | float | bool]]] = Field(max_length=20000)
    format: str = "xlsx"
    filename: Optional[str] = Field(default=None, max_length=120)


@router.post("/export/rows", summary="Render client-computed rows as a document "
             "(xlsx | csv | pdf) — legacy filtered-view export parity")
async def sme_export_rows(body: RowsExportBody,
                          user: dict = Depends(require_level(2))):
    import io

    from fastapi.responses import StreamingResponse

    from .reports import _FORMATS
    fmt = body.format.lower()
    if fmt not in _FORMATS:
        raise HTTPException(400, f"format must be one of {sorted(_FORMATS)}")
    for r in body.rows:
        if len(r) != len(body.columns):
            raise HTTPException(400, "every row must match the columns length")
    render, media = _FORMATS[fmt]
    data = render(body.title, body.columns, body.rows, user["username"])
    stem = _fname_part(body.filename or body.title.lower().replace(" ", "_"))
    return StreamingResponse(io.BytesIO(data), media_type=media,
                             headers={"Content-Disposition":
                                      f'attachment; filename="{stem}.{fmt}"'})


# ─── Smart Calculator (2026-07-18) ───────────────────────────────────────────
# "System Code + target SQM → segregated material list with explanations."
# Pure recipe math (demand = For_1_SQM × SQM — the CNCEC prediction project's
# model) enriched with live ERP stock through the SAP join, so the answer is
# both the requirement AND whether the store can cover it.

@router.get("/calculator", summary="Smart Calculator — materials for a target SQM")
async def smart_calculator(code: str, sqm: float,
                           user: dict = Depends(require_level(2)),
                           session: AsyncSession = Depends(get_session)):
    if sqm <= 0 or sqm != sqm or sqm > 1_000_000:
        raise HTTPException(422, "sqm must be a positive number")
    code = code.strip()
    r = sme_recipe_t.c
    recipes = (await session.execute(
        select(r["SAP_Code"], r["Material_Code"], r["Material_Name"],
               r["Material_Description"], r["UOM"], r["For_1_SQM"],
               r["Package_Size"], r["Lining_System_Name"], r["Lining_System"],
               r["Substrate"], r["Lining_Thickness"])
        .where(func.trim(r["Lining_System_Code"]) == code)
        .order_by(r["id"]))).mappings().all()
    if not recipes:
        raise HTTPException(404, f"no recipe lines for system code {code!r}")

    saps = sorted({str(x["SAP_Code"]).strip() for x in recipes if x["SAP_Code"]})
    stock: dict[str, float] = {}
    if saps:
        stock = {row.sap: float(row.stock or 0) for row in (await session.execute(
            text('''SELECT TRIM(i."SAP_Code") AS sap,
                 COALESCE((SELECT SUM(x."Quantity") FROM receipts x
                           WHERE TRIM(x."SAP_Code")=TRIM(i."SAP_Code")),0)
               - COALESCE((SELECT SUM(x."Quantity") FROM consumption x
                           WHERE TRIM(x."SAP_Code")=TRIM(i."SAP_Code")),0)
               - COALESCE((SELECT SUM(x."Quantity") FROM returns x
                           WHERE TRIM(x."SAP_Code")=TRIM(i."SAP_Code")),0) AS stock
                 FROM inventory i WHERE TRIM(i."SAP_Code") = ANY(:saps)'''),
            {"saps": saps})).all()}

    lines, shortfall_lines = [], 0
    for x in recipes:
        per = float(x["For_1_SQM"] or 0)
        required = sme_engine.round_n(per * sqm, 4)
        sap = str(x["SAP_Code"] or "").strip() or None
        uom = (x["UOM"] or "").strip()
        try:
            pkg = float(str(x["Package_Size"]).strip())
        except (TypeError, ValueError):
            pkg = None
        packages = (int(-(-required // pkg)) if pkg and pkg > 0 and required > 0
                    else None)
        available = stock.get(sap) if sap else None
        short = (sme_engine.round_n(max(required - available, 0.0), 4)
                 if available is not None else None)
        if short:
            shortfall_lines += 1
        expl = f"{per:g} {uom or 'unit'}/SQM × {sqm:g} SQM = {required:g} {uom}".strip()
        if packages:
            expl += f" → {packages} × {pkg:g} {uom} pack(s)"
        if available is not None:
            expl += (f" · in stock: {available:g}"
                     + (f" (short {short:g})" if short else " ✓"))
        lines.append({
            "sap_code": sap, "material_code": x["Material_Code"],
            "component": (x["Material_Description"] or "").strip(),
            "material_name": (x["Material_Name"] or "").strip(),
            "uom": uom, "for_1_sqm": per, "required_qty": required,
            "package_size": pkg, "packages_needed": packages,
            "available_stock": available, "shortfall_qty": short,
            "explanation": expl})
    first = recipes[0]
    return {"code": code,
            "short_name": (first["Lining_System_Name"] or "").strip(),
            "lining_system": (first["Lining_System"] or "").strip(),
            "substrate": (first["Substrate"] or "").strip(),
            "thickness": (first["Lining_Thickness"] or "").strip(),
            "target_sqm": sqm,
            "lines": lines,
            "totals": {"line_count": len(lines),
                       "shortfall_lines": shortfall_lines}}
