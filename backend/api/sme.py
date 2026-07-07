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
# not mutation: nothing is written (SME Canon holds — no write endpoints exist).

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
}


def _overview_rows(model: dict, plan: dict) -> list[dict]:
    """Per-(tag, code) rollup of oracle cascade lines for the Total Overview
    grid — presentation aggregation, deliberately outside the parity-locked
    engine (mirrors frontend/src/sme/session.ts codeStats + progress meta)."""
    acc: dict[tuple[str, str], dict] = {}
    for ln in plan["lines"]:
        k = (ln["Equipment_Tag_No"], ln["Lining_System_Code"])
        a = acc.setdefault(k, {"demand": 0.0, "alloc": 0.0, "short": 0.0})
        a["demand"] += ln["Demand_Qty"]
        a["alloc"] += ln["Allocated_Qty"]
        a["short"] += ln["Shortfall_Qty"]
    out, sno = [], 0
    for tag in plan["order_used"]:
        meta = model["tag_meta"].get(tag, {})
        for code in model["codes_by_tag"].get(tag, []):
            u = model["units"][(tag, code)]
            a = acc.get((tag, code), {"demand": 0.0, "alloc": 0.0, "short": 0.0})
            pct = min(100.0, a["alloc"] / a["demand"] * 100) if a["demand"] > 0 else 100.0
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
    title, part = _PLAN_EXPORT_KEYS[body.key]
    if body.title and body.title.strip():
        title = body.title.strip()
    items = _overview_rows(model, plan) if part == "_overview" else plan[part]
    columns = list(items[0].keys()) if items else []
    rows = [[r.get(c) for c in columns] for r in items]
    render, media = _FORMATS[fmt]
    data = render(title, columns, rows, user["username"])
    return StreamingResponse(io.BytesIO(data), media_type=media,
                             headers={"Content-Disposition":
                                      f'attachment; filename="sme-{body.key}.{fmt}"'})


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
    elif key == "system-code-report":
        title, items = "SME System Code Report", await _system_code_report_rows(session, site_id)
    elif key == "progress-list":
        title, items = "SME Progress List", await _progress_list_rows(session, site_id)
    elif key == "production-log":
        title, items = "SME Production Log (committed)", await _production_log_rows(session, site_id)
    else:
        raise HTTPException(404, f"unknown SME export {key!r}")

    columns = list(items[0].keys()) if items else []
    rows = [[r.get(c) for c in columns] for r in items]
    render, media = _FORMATS[fmt]
    data = render(title, columns, rows, user["username"])
    return StreamingResponse(io.BytesIO(data), media_type=media,
                             headers={"Content-Disposition":
                                      f'attachment; filename="sme-{key}.{fmt}"'})
