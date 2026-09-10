"""
backend/api/sme.py — SME Material Estimator (reads + pure compute).

This module only READS the sme_* tables — it never writes them. Master Data
writes live in backend/api/sme_master.py (Phase S6, unlocked at cutover);
everything here stays a pure read so the analytics/engine surface is
side-effect-free.

STRICT DECOUPLING (locked 2026-08-02)
-------------------------------------
The SME estimator is a SEPARATE POOL from the ERP warehouse. Its quantities come
from `sme_inventory_seed` (the Excel workbook) and nothing else. The ERP ledger —
`receipts`, `consumption`, `returns` — and the ERP `inventory` master are NEVER
read from this module. Both places they used to leak in are gone:

  * `SQL_SME_MATERIALS` derived `available_qty = seed + Σreceipts − Σconsumption`
    via a SAP join into `inventory`. It is now `available_qty = seed` verbatim.
  * `_CALC_POOL_SQL` (Smart Calculator) built its whole stock pool out of the ERP
    ledger. It now pools `sme_inventory_seed.Initial_Available_Qty`.

Consequence, and it is deliberate: the ordered pool is no longer netted against
receipts (the 2026-07-28 ruling Q2a). That netting existed ONLY because arriving
goods inflated `available_qty` through the ledger, so counting the order too
double-counted them. With the ledger disconnected nothing inflates availability,
so netting would now UNDER-count the order by every unit ever received. See
`sme_engine.build_model`.

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
# `require_level` is still imported: the per-endpoint `require_level(2)` gates
# below are now subordinate to the router gate (both must pass) and redundant
# rather than wrong — HOD is 2 and Auditor is 3. Left in place because
# rewriting fourteen call sites to say the same thing twice is churn, and
# because a per-endpoint gate is the right place to NARROW one of them later.
from .auth import require_level, require_roles, resolve_site_param
from .db import get_session
from .services.ledger import _MD

# ⚠️ ROLES, NOT A LEVEL (2026-08-12). This was `require_level(2)`, which
# admits HOD (2), Logistics (3), Auditor (3) and admin — so a Logistics
# account could call every Estimator endpoint while the sidebar showed it no
# SME page at all. That gap is the likeliest source of the "Logistics is
# seeing SME" report, and closing it in the manifest alone would have left the
# capability in place and merely hidden the door.
#
# The Estimator belongs to the people who PLAN work (HOD) and to the role that
# reads everything and writes nothing (Auditor). Its two write surfaces —
# /sme/actuals and /sme/master — are separately `require_roles("hod")` and stay
# that way, so the auditor reaches the analysis and none of the editing.
router = APIRouter(prefix="/sme", tags=["SME estimator"],
                   dependencies=[Depends(require_roles("hod", "auditor"))])

sme_equipment_t = _MD.tables["sme_equipment"]
sme_recipe_t = _MD.tables["sme_recipe"]
sme_sqm_t = _MD.tables["sme_sqm_progress"]

# The SME stock master, at PER-COMPONENT grain — seed table ONLY.
#
# 2026-08-02 STRICT DECOUPLING: `available_qty` is the workbook's
# `Initial_Available_Qty`, full stop. There is no ledger sub-select here any
# more, and there must never be one again: an ERP receipt, issue or return is a
# warehouse event and has no bearing on what the estimator believes it holds.
# The two `received_qty` / `consumed_qty` columns went with it — they were pure
# ERP readings, and leaving them in the payload would invite exactly the
# recoupling this rule forbids.
#
# 2026-07-30 COMPONENT IDENTITY ruling still holds: one row per
# (Material_Code, SAP_Code), because one Material_Code can be four physical
# drums separated only by the variant SAP.
SQL_SME_MATERIALS = '''
SELECT s."Material_Code" AS material_code, s."SAP_Code" AS sap_code,
       s."Material_Name" AS material_name,
       s."Item" AS item, s."Vendor" AS vendor,
       s."Purchasing_Document" AS purchasing_document, s."Document_Date" AS document_date,
       s."Nature" AS nature, s."UOM" AS uom,
       s."Initial_Available_Qty" AS initial_available_qty,
       s."Initial_Ordered_Qty" AS initial_ordered_qty,
       s."Initial_Available_Qty" AS available_qty,
       s."Initial_Ordered_Qty" AS ordered_qty
FROM sme_inventory_seed s
'''

# ── Legacy parity, restated as a CONSERVATION invariant ──────────────────────
# The PG port and the frozen SQLite view no longer share a grain, so row-for-row
# parity is not merely broken — it is the thing we set out to break. What must
# still hold is that splitting a material into components LOSES NOTHING: rolled
# back up to Material_Code, every quantity has to match the legacy view exactly.
# That catches any double-count or dropped SAP the split could introduce, which
# is the failure mode worth gating on. Both sides are rolled up explicitly.
#
# 2026-08-02: the two ledger-derived columns are gone from the comparison. The
# legacy SQLite view still ADDS the ledger into its `available_qty`, so this gate
# now also asserts that the frozen dataset carries no ERP movement against an SME
# material — true on a fresh cutover, which is the only state parity_check is
# meaningful in anyway (PROJECT_HANDOVER caveat 3). Suite BA pins the decoupling
# directly, on live-shaped data, and does not depend on that.
_ROLLUP_COLS = '''SELECT material_code,
       SUM(initial_available_qty) AS initial_available_qty,
       SUM(initial_ordered_qty)   AS initial_ordered_qty,
       SUM(available_qty)         AS available_qty,
       SUM(ordered_qty)           AS ordered_qty'''

SQL_SME_MATERIALS_ROLLUP = f'''
{_ROLLUP_COLS}
FROM ({SQL_SME_MATERIALS}) c
GROUP BY material_code
'''
SQL_SME_MATERIALS_ROLLUP_SQLITE = f'''
{_ROLLUP_COLS}
FROM sme_materials_view
GROUP BY material_code
'''

# Parity registry (consumed by parity_check.py alongside stock.DERIVED).
DERIVED_SME = {"sme_materials": {
    "sql": SQL_SME_MATERIALS,
    "view": "sme_materials_view",
    "parity_sql": SQL_SME_MATERIALS_ROLLUP,
    "parity_source_sql": SQL_SME_MATERIALS_ROLLUP_SQLITE,
}}


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
    rows = _rows(await session.execute(text(SQL_SME_MATERIALS + ' ORDER BY s."Material_Code", s."SAP_Code"')))
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
    """Sort key for lining-system codes — delegates to the engine's ONE
    implementation. This used to be a second copy, and it had drifted: it read
    `(1, s)` for anything non-numeric, so every `LSC*` code fell into one
    bucket and sorted lexically (LSC1, LSC10, LSC11, LSC2). Do not re-inline it.
    """
    return sme_engine.syscode_sort_key(code)


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
        select(r.c["Lining_System_Code"], r.c["Material_Code"], r.c["SAP_Code"],
               r.c["Material_Name"], r.c["UOM"],
               r.c["For_1_SQM"]).order_by(r.c["id"])))
    recipes: dict[str, list[dict]] = {}
    for rr in rec_rows:
        recipes.setdefault(str(rr["Lining_System_Code"]), []).append(rr)

    # 2026-07-30 COMPONENT IDENTITY: keyed per (Material_Code, SAP_Code) like
    # the parity engine. Keying on material_code alone did NOT merely pool the
    # components here — the dict comprehension let the LAST component's stock
    # silently overwrite the other three.
    mat_rows = _rows(await session.execute(text(SQL_SME_MATERIALS)))
    pool = {sme_engine.mat_key(m["material_code"], m["sap_code"]):
            float(m["available_qty"] or 0) for m in mat_rows}
    names = {sme_engine.mat_key(m["material_code"], m["sap_code"]):
             (m["material_name"] or "") for m in mat_rows}

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
            key = sme_engine.mat_key(rr["Material_Code"], rr["SAP_Code"])
            sap = sme_engine.sap_norm(rr["SAP_Code"])
            # The stock master names the component; the recipe repeats one
            # generic name across all four rows of a multi-part system.
            name = names.get(key) or rr["Material_Name"]
            demand = remaining * float(rr["For_1_SQM"] or 0)
            if demand <= 0:
                continue
            before = pool.get(key, 0.0)
            alloc = min(demand, before)
            pool[key] = max(0.0, before - alloc)
            # Round ONCE, then derive: rounding demand, allocated and shortfall
            # independently lets `allocated + shortfall == demand` drift by up
            # to 1e-4 per line, which at CNCEC's ~1,400 lines no longer hides
            # inside the reconciliation tolerance. Deriving the shortfall from
            # the two rounded figures makes the identity exact by construction.
            d4 = round(demand, 4)
            a4 = round(alloc, 4)
            s4 = round(d4 - a4, 4)
            lines.append({
                "Equipment_Tag_No": eq["Equipment_Tag_No"],
                "Lining_System_Code": eq["Lining_System_Code"],
                "Lining_System_Short_Name": eq["Lining_System_Short_Name"],
                "Remaining_SQM": round(remaining, 2),
                "Material_Code": rr["Material_Code"], "SAP_Code": sap,
                "Material_Name": name,
                "UOM": rr["UOM"],
                "Demand_Qty": d4,
                "Allocated_Qty": a4,
                "Shortfall_Qty": s4,
                "Pool_Before": round(before, 4),
                "Pool_After": round(pool[key], 4),
            })
            t = totals.setdefault(key, {"Material_Code": rr["Material_Code"],
                                        "SAP_Code": sap,
                                        "Material_Name": name,
                                        "UOM": rr["UOM"], "Demand_Qty": 0.0,
                                        "Allocated_Qty": 0.0, "Shortfall_Qty": 0.0})
            # Accumulate the SAME rounded figures the lines report, so the
            # totals reconcile with them exactly instead of to a tolerance.
            t["Demand_Qty"] += d4
            t["Allocated_Qty"] += a4
            t["Shortfall_Qty"] += s4
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
    # SAP_Code rides along as the component discriminator (2026-07-30 ruling):
    # without it the engine cannot tell the Comp-A/B/C/D lines of a multi-part
    # system apart — they share a Material_Code AND a Material_Name.
    recipes = _rows(await session.execute(
        select(r.c["Lining_System_Code"], r.c["Lining_System_Name"],
               r.c["Material_Code"], r.c["SAP_Code"], r.c["Material_Name"],
               r.c["UOM"], r.c["For_1_SQM"]).order_by(r.c["id"])))

    # 2026-08-02 STRICT DECOUPLING: the snapshot carries NO ERP-derived field.
    # `received_qty` used to ride along for the engine's effective-ordered
    # netting (ruling Q2a); with the ledger disconnected there is nothing to net
    # against, and shipping the field would re-open the coupling from the client
    # side. Both engines now take `ordered_qty` at face value.
    materials = [{k: m[k] for k in ("material_code", "sap_code", "material_name",
                                    "nature", "uom", "available_qty",
                                    "ordered_qty")}
                 for m in _rows(await session.execute(
                     text(SQL_SME_MATERIALS
                          + ' ORDER BY s."Material_Code", s."SAP_Code"')))]

    # ── Phase 13g: `consumed_qty`, an OBSERVATION beside the plan ───────────
    # ⚠️ A SECOND QUERY, DELIBERATELY, AND NOT A JOIN INTO `SQL_SME_MATERIALS`.
    #
    # That query is a QUANTITY query and rule 1a's mechanism is that it names
    # only `sme_inventory_seed`. Suite BA greps it — for ERP table names AND
    # for `sme_consumption_log` — precisely so a future join is caught at
    # review rather than in a stock report. Merging the observation into it
    # would trip that guard, and rightly: the guard is not the rule, but it is
    # how the rule is kept.
    #
    # ⚠️ SO `available_qty` IS STILL `Initial_Available_Qty`, FULL STOP. Nothing
    # here nets, subtracts or compares. The consumed figure is attached beside
    # it under its own key and the engines carry it as metadata (ruling Q13-5,
    # Option B).
    #
    # ⚠️ AND IT COUNTS ONLY HOD-APPROVED ROWS. `sme_consumption_log` is an
    # SME-owned table and this reads only `status = 'committed'`, so the
    # estimator never sees raw warehouse movement — only a draw somebody
    # attributed and a second person signed for. That is what keeps suite BA's
    # byte-identical probe green: posting an ERP consumption moves nothing here.
    from .services.sme_link import consumed_by_component

    consumed = {c["Material_Key"]: c["consumed_qty"]
                for c in await consumed_by_component(session, site_id)}
    if consumed:
        for m in materials:
            key = sme_engine.mat_key(m["material_code"], m["sap_code"])
            if key in consumed:
                m["consumed_qty"] = consumed[key]

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
    # 2026-07-28: SQM-achievable vs deficit per system code + the exact
    # material shortfalls blocking them. Multi-section (see _segregated_sections).
    "segregated": ("SME Material-Wise Segregated Report", "_segregated"),
}


def _overview_rows(model: dict, plan: dict) -> list[dict]:
    """Per-(tag, code) rollup of oracle cascade lines for the Total Overview
    grid — presentation aggregation, deliberately outside the parity-locked
    engine (mirrors frontend/src/sme/session.ts codeStats + progress meta)."""
    acc: dict[tuple[str, str], dict] = {}
    for ln in plan["lines"]:
        k = (ln["Equipment_Tag_No"], ln["Lining_System_Code"])
        a = acc.setdefault(k, {"demand": 0.0, "alloc": 0.0, "short": 0.0,
                               "avail": 0.0, "pending": 0.0, "net": 0.0,
                               "min_rate": 1.0})
        a["demand"] += ln["Demand_Qty"]
        a["alloc"] += ln["Allocated_Qty"]
        a["avail"] += ln["Alloc_Available"]
        a["pending"] += ln["Alloc_Pending"]
        a["short"] += ln["Shortfall_Available_Qty"]
        a["net"] += ln["Shortfall_Qty"]
        # 2026-07-07 STRICT BOTTLENECK ruling: the unit's coverage is its
        # least-available material, never the alloc/demand average. 2026-07-28:
        # measured on PHYSICAL stock, matching compute_feasibility — on-order
        # stock cannot be applied to a tank today.
        rate = (min(1.0, ln["Alloc_Available"] / ln["Demand_Qty"])
                if ln["Demand_Qty"] > 0 else 1.0)
        if rate < a["min_rate"]:
            a["min_rate"] = rate
    out, sno = [], 0
    for tag in plan["order_used"]:
        meta = model["tag_meta"].get(tag, {})
        for code in model["codes_by_tag"].get(tag, []):
            u = model["units"][(tag, code)]
            a = acc.get((tag, code), {"demand": 0.0, "alloc": 0.0, "short": 0.0,
                                      "avail": 0.0, "pending": 0.0, "net": 0.0,
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
                "Available_Qty": round(a["avail"], 3),
                "Pending_Delivery_Qty": round(a["pending"], 3),
                "Total_Procured_Qty": round(a["avail"] + a["pending"], 3),
                "Shortfall_Qty": round(a["short"], 3),
                "Net_Shortfall_Qty": round(a["net"], 3),
                "Fulfillment_Pct": round(pct, 1),
            })
    return out


# Columns of the "Total Material Demand" lead summary. Named explicitly rather
# than derived from dict order so a future field addition can't silently
# reshuffle an operator-facing procurement document.
#
# NOTE on naming: the brief glossed Allocated_Qty as "Ordered". It is kept as
# Allocated_Qty here — it is the quantity the cascade could allocate from
# EXISTING stock, not a quantity on order. Labelling it "Ordered" in a
# procurement document invites double-ordering. Net_Demand (= Shortfall_Qty) is
# the figure to actually raise a PR against.
# SAP_Code sits right after Material_Code: for a multi-part system the code
# repeats on four rows and the SAP is what says WHICH component this is.
_MATERIAL_DEMAND_COLS = ["S_No", "Material_Code", "SAP_Code", "Material_Name",
                         "UOM", "Total_Needed", "Available_Qty",
                         "Pending_Delivery_Qty", "Total_Procured_Qty",
                         "Allocated_Qty", "Net_Demand", "Fulfillment_Pct"]

# The session report's per-equipment breakdown. Named EXPLICITLY, for two
# reasons that were both live defects when this list was derived from dict key
# order instead (2026-08-04):
#
#  1. THE RECIPE LEAKED. `For_1_SQM` — the per-square-metre consumption rate —
#     rode into every operator-facing session document. It is an INPUT to the
#     calculation, not a result, and the ruling is that it stays backend-side:
#     reports state what to draw, not the formula that produced it. (It remains
#     in `/sme/snapshot`, because the TypeScript engine computes demand in the
#     browser and cannot work without it.)
#  2. ENGINE INTERNALS LEAKED. `Material_Key`, `Pool_Before`, `Pool_After`,
#     `Pending_Pool_Before`, `Pending_Pool_After` are cascade bookkeeping — the
#     running pool as the allocator walks the priority order. In a spreadsheet
#     they read like quantities somebody might sum, and summing them is
#     meaningless.
#
# TIER DISCIPLINE (rule 1b) is preserved exactly: `Alloc_Available` (tier 1)
# and `Alloc_Pending` (tier 2) stay separate columns, `Allocated_Qty` is their
# conserved sum and is NOT a coverage numerator, and the two Fulfillment
# figures keep their own names.
_SESSION_LINE_COLS = ["Equipment_Tag_No", "Lining_System_Code",
                      "Lining_System_Short_Name", "Total_SQM",
                      "Material_Code", "SAP_Code", "Material_Name", "UOM",
                      "Demand_Qty", "Alloc_Available", "Alloc_Pending",
                      "Allocated_Qty",
                      # 2026-08-05 — the PROJECT-level order, beside the
                      # per-line allocation. See `_with_material_totals`.
                      "Pending_Delivery_Qty", "Total_Procured_Qty",
                      "Shortfall_Available_Qty",
                      "Shortfall_Qty", "Fulfillment_Pct",
                      "Fulfillment_With_Ordered_Pct"]

# Columns whose value is a property of the MATERIAL, not of the line they sit
# on, and which therefore REPEAT down the sheet. Named here so the test suite
# can assert exactly that, and so nobody later "fixes" the repetition by
# apportioning them per line.
_MATERIAL_LEVEL_LINE_COLS = ("Pending_Delivery_Qty", "Total_Procured_Qty")


def _with_material_totals(model: dict, lines: list[dict]) -> list[dict]:
    """Attach the PROJECT-level order position to each cascade line.

    WHY. `Alloc_Available` / `Alloc_Pending` / `Allocated_Qty` say what THIS
    equipment×material line was given by the cascade. They do not say what is
    on the way for the project as a whole, and that is the question procurement
    actually asks of the session report. On a material short enough to be
    rationed, a line can show `Alloc_Pending = 230` while 409 are genuinely in
    transit — the rest having been allocated to other equipment.

    ⚠️ THE SUBSET RULE (1c) — HOW THE CEILING IS BUILT.
    `pool_pending_init` is ALREADY `max(ordered − available, 0)`: the UNRECEIVED
    subset, not the whole order. So

        Total_Procured_Qty = pool_init + pool_pending_init
                           = available + (ordered − available)
                           = max(available, ordered)          ← the ceiling

    That is emphatically NOT "ordered + available", which is the additive
    reading rule 1c overturned and which understated the buy list by 22,951
    units. Both figures are taken from the engine's own pools; nothing here
    re-derives a quantity from the raw seed.

    ⚠️ THESE TWO COLUMNS DO NOT SUM DOWN THE SHEET. A material appearing on
    twelve equipment lines carries the same 409 on all twelve, because it is
    one purchase order and not twelve. The per-line quantities beside them
    (`Alloc_Pending`, `Allocated_Qty`) are the additive ones. Suite AX asserts
    the repetition explicitly so that a future refactor cannot quietly turn
    these into an apportioned share that looks summable and is wrong either way.
    """
    pool = model.get("pool_init") or {}
    pending = model.get("pool_pending_init") or {}
    out = []
    for ln in lines:
        key = ln.get("Material_Key")
        avail = float(pool.get(key, 0.0) or 0.0)
        pend = float(pending.get(key, 0.0) or 0.0)
        out.append({**ln,
                    "Pending_Delivery_Qty": sme_engine.round_n(pend, 3),
                    "Total_Procured_Qty": sme_engine.round_n(avail + pend, 3)})
    return out

# Material-Wise Segregated Report (2026-07-28), grouped by system code.
_SEGREGATED_CODE_COLS = ["Lining_System_Code", "System_Name", "Equipment_Count",
                         "Total_SQM", "Done_SQM", "Remaining_SQM",
                         "SQM_Achievable_Now", "SQM_Achievable_With_Ordered",
                         "SQM_Deficit", "Coverage_Now_Pct", "Equipment_Tags"]
_SEGREGATED_BLOCK_COLS = ["Lining_System_Code", "System_Name", "Material_Code",
                          "SAP_Code", "Material_Name", "UOM", "Demand_Qty",
                          "Available_Qty", "Pending_Delivery_Qty",
                          "Shortfall_Qty", "Net_Shortfall_Qty"]
_SEGREGATED_UNIT_COLS = ["Equipment_Tag_No", "Name", "Lining_System_Code",
                         "System_Name", "Remaining_SQM", "SQM_Achievable_Now",
                         "SQM_Achievable_With_Ordered", "SQM_Deficit",
                         "Coverage_Now_Pct", "Has_Recipe"]


def _segregated_sections(plan: dict) -> list[tuple[str, list[str], list[list]]]:
    """The three tables of the Material-Wise Segregated Report, in the order
    they appear in both the workbook and the PDF: the system-code SQM rollup
    first (what can we actually build), then the exact material shortfalls
    blocking those codes, then the per-equipment detail behind the rollup."""
    codes = plan.get("sqm_by_code") or []
    blocking: list[dict] = []
    for c in codes:
        for m in c.get("Blocking_Materials") or []:
            blocking.append({
                "Lining_System_Code": c["Lining_System_Code"],
                "System_Name": c["System_Name"],
                "Material_Code": m["Material_Code"],
                "Material_Name": m["Material_Name"], "UOM": m["UOM"],
                "Demand_Qty": m["Demand_Qty"],
                "Available_Qty": m["Alloc_Available"],
                "Pending_Delivery_Qty": m["Alloc_Pending"],
                "Shortfall_Qty": m["Shortfall_Available_Qty"],
                "Net_Shortfall_Qty": m["Shortfall_Qty"]})
    return [
        ("SQM by System Code", _SEGREGATED_CODE_COLS,
         [[c.get(k) for k in _SEGREGATED_CODE_COLS] for c in codes]),
        ("Blocking Materials", _SEGREGATED_BLOCK_COLS,
         [[b.get(k) for k in _SEGREGATED_BLOCK_COLS] for b in blocking]),
        ("Equipment Detail", _SEGREGATED_UNIT_COLS,
         [[u.get(k) for k in _SEGREGATED_UNIT_COLS]
          for u in (plan.get("sqm_units") or [])]),
    ]


def _material_demand_rows(plan: dict) -> list[dict]:
    """Session-wide material rollup: the per-(equipment × material) cascade
    lines collapsed to ONE row per PHYSICAL COMPONENT.

    Presentation aggregation, deliberately outside the parity-locked engine —
    same contract as _overview_rows. Mirrors the on-screen combined-procurement
    table (frontend/src/sme/session.ts weightedProcurement), including its
    worst-coverage-first ordering, so the exported document and the UI agree.

    2026-07-30 COMPONENT IDENTITY: the group key is the engine's Material_Key,
    i.e. (Material_Code, SAP_Code). It used to include Material_Name and UOM,
    which looks like it would separate components but does NOT — the recipe
    repeats one generic name and one UOM across all four rows of a multi-part
    system, so name+UOM collapsed them right back into a single line.
    """
    acc: dict[str, dict] = {}
    for ln in plan["lines"]:
        key = str(ln["Material_Key"])
        a = acc.setdefault(key, {"demand": 0.0, "alloc": 0.0, "short": 0.0,
                                 "avail": 0.0, "pending": 0.0,
                                 "code": str(ln["Material_Code"]),
                                 "sap": str(ln.get("SAP_Code") or ""),
                                 "name": str(ln.get("Material_Name") or ""),
                                 "uom": str(ln.get("UOM") or "")})
        a["demand"] += ln["Demand_Qty"]
        a["alloc"] += ln["Allocated_Qty"]
        a["avail"] += ln["Alloc_Available"]
        a["pending"] += ln["Alloc_Pending"]
        a["short"] += ln["Shortfall_Qty"]
    out = []
    for a in acc.values():
        # Coverage stays on PHYSICAL stock, matching feasibility and the
        # segregated report — on-order units cannot be applied today.
        pct = (min(100.0, a["avail"] / a["demand"] * 100.0)
               if a["demand"] > 0 else 100.0)
        out.append({"S_No": 0, "Material_Code": a["code"], "SAP_Code": a["sap"],
                    "Material_Name": a["name"],
                    "UOM": a["uom"], "Total_Needed": round(a["demand"], 3),
                    "Available_Qty": round(a["avail"], 3),
                    "Pending_Delivery_Qty": round(a["pending"], 3),
                    "Total_Procured_Qty": round(a["avail"] + a["pending"], 3),
                    "Allocated_Qty": round(a["alloc"], 3),
                    "Net_Demand": round(a["short"], 3),
                    "Fulfillment_Pct": round(pct, 1)})
    out.sort(key=lambda r: (r["Fulfillment_Pct"], r["Material_Code"], r["SAP_Code"]))
    for i, r in enumerate(out, 1):
        r["S_No"] = i
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
              "feasibility": "session_feasibility", "overview": "total_overview",
              "segregated": "material_wise_segregated"}
    fname = legacy_filename(_STEMS.get(body.key, body.key), uname, fmt)
    if part == "_segregated":
        # Three tables, always in the same order, for both xlsx and pdf. CSV
        # cannot carry three schemas, so it falls back to the code rollup —
        # the section a spreadsheet-driven procurement flow actually needs.
        if fmt == "csv":
            items = plan.get("sqm_by_code") or []
            items = [{k: r.get(k) for k in _SEGREGATED_CODE_COLS} for r in items]
        else:
            from .reports import to_pdf_sheets, to_xlsx_sheets
            sections = _segregated_sections(plan)
            data = (to_xlsx_sheets(sections, uname) if fmt == "xlsx" else
                    to_pdf_sheets(body.title or title, sections, uname,
                                  page_break_between=True))
            return StreamingResponse(io.BytesIO(data),
                                     media_type=_FORMATS[fmt][1],
                                     headers={"Content-Disposition":
                                              f'attachment; filename="{fname}"'})
    elif part == "_overview":
        items = _overview_rows(model, plan)
    elif part == "_execution":
        tag = (body.equipment_tag or "").strip()
        if not tag:
            raise HTTPException(400, "execution-plan export needs equipment_tag")
        items = _with_material_totals(model, [
            ln for ln in plan["lines"]
            if ln["Equipment_Tag_No"] == tag and ln["Shortfall_Qty"] > 0])
        title = f"SME Execution Plan — {tag} (order list)"
        fname = legacy_filename(f"execution_plan_{_fname_part(tag)}", uname, fmt)
    elif part == "_location":
        loc = (body.location or "").strip()
        if loc:
            loc_tags = {t for t, m in model["tag_meta"].items()
                        if (m.get("Location") or "") == loc}
            items = _with_material_totals(model, [
                ln for ln in plan["lines"] if ln["Equipment_Tag_No"] in loc_tags])
        else:
            items = _with_material_totals(model, plan["lines"])
        scope = loc or "All Equipment"
        title = f"SME Location Report — {scope}"
        fname = legacy_filename(
            f"location_report_{_fname_part(scope.lower().replace(' ', '_'))}", uname, fmt)
        if fmt == "xlsx":  # legacy workbook: main alloc table + 3 summary blocks
            # 2026-08-03 STRICT TIER SEGREGATION: the single `Allocated_Qty`
            # column summed physical stock and stock on order, and sat beside a
            # physical-only Fulfillment_Pct. Split into the two tiers plus both
            # gaps, so a reader can never mistake an open PO for a full shelf.
            cols = ["Equipment_Tag_No", "Lining_System_Code",
                    "Lining_System_Short_Name", "Total_SQM", "Material_Code",
                    "SAP_Code", "Material_Name", "UOM", "Demand_Qty",
                    "Alloc_Available", "Alloc_Pending",
                    "Shortfall_Available_Qty", "Shortfall_Qty",
                    "Fulfillment_Pct", "Fulfillment_With_Ordered_Pct"]
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
        # Cascade lines carry the per-line allocation; the session report also
        # states the PROJECT-level order beside it (2026-08-05).
        if part == "lines":
            items = _with_material_totals(model, items)
    if body.title and body.title.strip():
        title = body.title.strip()

    # The session report LEADS with the aggregated material demand: procurement
    # reads "what do we buy, in total" first, and only then drills into which
    # equipment drove it. The per-equipment breakdown is unchanged and follows
    # as the second section. CSV is untouched — it is a single flat table, and
    # welding two different column schemas into one file would break every
    # downstream parser.
    if body.key == "session-full" and fmt in ("xlsx", "pdf"):
        from .reports import to_pdf_sheets, to_xlsx_sheets
        summary = _material_demand_rows(plan)
        lead = "Total Material Demand" if fmt == "xlsx" else "Material-Wise Summary"
        sections = [
            (lead, _MATERIAL_DEMAND_COLS,
             [[r.get(c) for c in _MATERIAL_DEMAND_COLS] for r in summary]),
            ("Equipment Breakdown", _SESSION_LINE_COLS,
             [[r.get(c) for c in _SESSION_LINE_COLS] for r in items]),
        ]
        data = (to_xlsx_sheets(sections, uname) if fmt == "xlsx" else
                to_pdf_sheets(title, sections, uname, page_break_between=True))
        return StreamingResponse(io.BytesIO(data), media_type=_FORMATS[fmt][1],
                                 headers={"Content-Disposition":
                                          f'attachment; filename="{fname}"'})

    # Cascade-line documents (session-full CSV, location report, execution
    # plan) go through the same explicit column list, so the recipe rate and
    # the allocator's running pools cannot leak into them either — see
    # `_SESSION_LINE_COLS`.
    if part in ("lines", "_execution", "_location") and items:
        columns = [c for c in _SESSION_LINE_COLS if c in items[0]]
    else:
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
        items = _rows(await session.execute(text(SQL_SME_MATERIALS + ' ORDER BY s."Material_Code", s."SAP_Code"')))
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


# ─── Smart Calculator (2026-07-18 · multi-system, per-COMPONENT stock) ───────
# "System codes + target SQM(s) → segregated material list with explanations."
# Pure recipe math (demand = For_1_SQM × SQM — the CNCEC prediction project's
# model) enriched with SME stock.
#
# 2026-08-04 COMPONENT IDENTITY (overturns the 2026-07-18 Material_Code pooling
# rule for this endpoint, on explicit operator authorization). Stock is keyed on
# **(Material_Code, SAP_Code)**, exactly like the estimator engine's
# `mat_key()`. It used to be summed over EVERY variant SAP sharing a
# Material_Code, so the four Comp-A/B/C/D drums of a multi-part system each read
# the SAME pooled figure — the identical clumping that locked rule 1 removed
# from the rest of the module: a calculator line could report a component as
# covered out of a sibling's stock that is a different drum on a different
# shelf, and conversely mark a well-stocked component short.
#
# SAP strings are whitespace-normalized on BOTH sides of the join (`sap_norm()`
# in Python, `REPLACE(TRIM(...), ' ', '')` here) because the workbook and the
# ERP both write the same variant as "1043-2" and "1043 - 2".
#
# 2026-08-02 STRICT DECOUPLING: this pool was built from the ERP ledger
# (Σreceipts − Σconsumption − Σreturns, gated on a SAP existing in the ERP
# `inventory` master). It is now `sme_inventory_seed` only.
#
# `stock` stays NULL — meaning "unknown", which the UI renders as "—" rather
# than a confident zero — when that exact component has no seed row.

_CALC_POOL_SQL = text('''
    WITH mats AS (
        SELECT DISTINCT TRIM("Material_Code") AS mat,
               REPLACE(TRIM("SAP_Code"), ' ', '') AS sap
        FROM sme_recipe
        WHERE TRIM("Material_Code") = ANY(:mats) AND "SAP_Code" IS NOT NULL
    ),
    seed AS (
        -- One row per PHYSICAL component. The SUM collapses only genuine
        -- duplicates of the SAME (code, SAP) pair, never two components.
        SELECT TRIM("Material_Code") AS mat,
               REPLACE(TRIM("SAP_Code"), ' ', '') AS sap,
               SUM(COALESCE("Initial_Available_Qty", 0)) AS stock,
               -- 2026-08-05 SUBSET RULE: Initial_Ordered_Qty is the TOTAL
               -- procured and `stock` is the ARRIVED part of it, so the
               -- pending delivery is derived as max(procured - stock, 0).
               SUM(COALESCE("Initial_Ordered_Qty", 0))   AS procured
        FROM sme_inventory_seed
        WHERE TRIM(COALESCE("SAP_Code", '')) <> ''
        GROUP BY TRIM("Material_Code"), REPLACE(TRIM("SAP_Code"), ' ', '')
    )
    SELECT m.mat, m.sap,
           s.stock    AS stock,
           s.procured AS procured
    FROM mats m
    LEFT JOIN seed s ON s.sap = m.sap AND s.mat = m.mat''')


@router.get("/calculator", summary="Smart Calculator — materials for target "
            "SQM across one or more lining systems")
async def smart_calculator(code: Optional[str] = None,
                           sqm: Optional[float] = None,
                           codes: Optional[str] = None,
                           sqms: Optional[str] = None,
                           user: dict = Depends(require_level(2)),
                           session: AsyncSession = Depends(get_session)):
    # -- parse system codes: multi via `codes` (comma-sep), legacy single via
    #    `code`. SQM targets: one global `sqm` for all, or per-code `sqms`.
    raw_codes = [c.strip() for c in (codes.split(",") if codes else [code or ""])]
    raw_codes = [c for c in raw_codes if c]
    if not raw_codes:
        raise HTTPException(422, "provide a system code (code=) or codes=")
    if len(raw_codes) > 25:
        raise HTTPException(422, "at most 25 system codes per calculation")
    if sqms is not None and sqms.strip():
        try:
            raw_targets = [float(x) for x in sqms.split(",")]
        except ValueError:
            raise HTTPException(422, "sqms must be comma-separated numbers")
        if len(raw_targets) != len(raw_codes):
            raise HTTPException(422,
                                "sqms must carry exactly one value per system code")
        mode = "per_system"
    else:
        if sqm is None:
            raise HTTPException(422, "sqm must be a positive number")
        raw_targets = [sqm] * len(raw_codes)
        mode = "global"
    pairs: list[tuple[str, float]] = []
    for c, t in zip(raw_codes, raw_targets):          # dedupe, first wins
        if t <= 0 or t != t or t > 1_000_000:
            raise HTTPException(422, "sqm must be a positive number")
        if all(c != pc for pc, _ in pairs):
            pairs.append((c, t))

    r = sme_recipe_t.c
    rows = (await session.execute(
        select(func.trim(r["Lining_System_Code"]).label("sys_code"),
               r["SAP_Code"], r["Material_Code"], r["Material_Name"],
               r["Material_Description"], r["UOM"], r["For_1_SQM"],
               r["Package_Size"], r["Lining_System_Name"], r["Lining_System"],
               r["Substrate"], r["Lining_Thickness"])
        .where(func.trim(r["Lining_System_Code"]).in_([c for c, _ in pairs]))
        .order_by(r["id"]))).mappings().all()
    by_code: dict[str, list] = {}
    for x in rows:
        by_code.setdefault(x["sys_code"], []).append(x)
    unknown = [c for c, _ in pairs if c not in by_code]
    if unknown:
        raise HTTPException(404, "no recipe lines for system code(s) "
                            + ", ".join(repr(c) for c in unknown))

    # -- per-COMPONENT SME stock, keyed (Material_Code, SAP_Code) --
    # 2026-08-04: this used to key on Material_Code alone and sum every variant
    # SAP under it, so four unlike drums shared one figure. The key is now the
    # engine's own component identity; `sap_norm` matches the SQL's
    # REPLACE(TRIM(...)) on both sides, so a recipe "1043-2" finds a seed row
    # written "1043 - 2".
    mats = sorted({str(x["Material_Code"]).strip() for x in rows
                   if x["Material_Code"] and str(x["Material_Code"]).strip()})
    pool: dict[str, dict] = {}
    if mats:
        pool = {sme_engine.mat_key(p.mat, p.sap): {
                    "stock": (float(p.stock) if p.stock is not None else None),
                    "procured": (float(p.procured) if p.procured is not None else None)}
                for p in (await session.execute(_CALC_POOL_SQL,
                                                {"mats": mats})).all()}

    def _mk_line(x, target: float) -> dict:
        per = float(x["For_1_SQM"] or 0)
        required = sme_engine.round_n(per * target, 4)
        # The SAP is normalized the same way everywhere in the module, so the
        # payload, the aggregation key and the stock lookup all agree.
        sap = sme_engine.sap_norm(x["SAP_Code"]) or None
        mat = str(x["Material_Code"] or "").strip() or None
        uom = (x["UOM"] or "").strip()
        try:
            pkg = float(str(x["Package_Size"]).strip())
        except (TypeError, ValueError):
            pkg = None
        packages = (int(-(-required // pkg)) if pkg and pkg > 0 and required > 0
                    else None)
        p = pool.get(sme_engine.mat_key(mat, sap)) if mat else None
        available = p["stock"] if p else None
        # 2026-08-05 SUBSET RULE: the workbook's Ordered_Qty is the TOTAL
        # procured and Available_Qty is the ARRIVED part of it, so the pipeline
        # is the difference and the CEILING is the order itself. Subtracting
        # `available + procured` (the old `available + on_order`) counted every
        # delivered unit twice and understated the buy list by exactly the
        # arrived quantity.
        procured = (max(p["procured"] or 0.0, available)
                    if p and available is not None else None)
        pending = (max(procured - available, 0.0)
                   if procured is not None else None)
        # 2026-08-03 STRICT TIER SEGREGATION: `shortfall_qty` stays the PHYSICAL
        # gap (what blocks the job today); `net_shortfall_qty` is what is left
        # to BUY once the WHOLE order has landed. Two fields, never one.
        short = (sme_engine.round_n(max(required - available, 0.0), 4)
                 if available is not None else None)
        net_short = (sme_engine.round_n(max(required - procured, 0.0), 4)
                     if procured is not None else None)
        # 2026-08-04: the explanation used to LEAD with the recipe:
        #   "0.35 KG/SQM × 1000 SQM = 350 KG"
        # which is the per-square-metre rate written out in full, in a string
        # that lands in every calculator export. The rate is an input to the
        # calculation, not a result — reports state what to draw, not the
        # formula behind it. It now states the outcome and the area it covers.
        # (`per` is still what computes `required`; it is simply not narrated.)
        expl = f"{required:g} {uom} for {target:g} SQM".strip()
        if packages:
            expl += f" → {packages} × {pkg:g} {uom} pack(s)"
        if available is not None:
            expl += f" · in stock: {available:g}"
            if short:
                expl += f" (short {short:g}"
                expl += (f", {net_short:g} to buy after {pending:g} pending "
                         f"delivery of {procured:g} procured)"
                         if pending else ")")
            else:
                expl += " ✓"
        return {"sap_code": sap, "material_code": mat,
                "component": (x["Material_Description"] or "").strip(),
                "material_name": (x["Material_Name"] or "").strip(),
                "uom": uom, "for_1_sqm": per, "required_qty": required,
                "package_size": pkg, "packages_needed": packages,
                "available_stock": available,
                "pending_delivery": pending, "total_procured": procured,
                # `pooled_saps` is retained at 1 for payload compatibility: as
                # of 2026-08-04 a line's stock is its OWN component's, never a
                # pool, so there is nothing left to count. Kept rather than
                # dropped so an older cached bundle does not render `undefined`.
                "pooled_saps": 1,
                "shortfall_qty": short, "net_shortfall_qty": net_short,
                "explanation": expl}

    systems: list[dict] = []
    agg: dict[tuple, dict] = {}
    for c, target in pairs:
        recipes = by_code[c]
        lines = [_mk_line(x, target) for x in recipes]
        shortfall_lines = sum(1 for ln in lines if ln["shortfall_qty"])
        first = recipes[0]
        systems.append({
            "code": c,
            "short_name": (first["Lining_System_Name"] or "").strip(),
            "lining_system": (first["Lining_System"] or "").strip(),
            "substrate": (first["Substrate"] or "").strip(),
            "thickness": (first["Lining_Thickness"] or "").strip(),
            "target_sqm": target,
            "lines": lines,
            "totals": {"line_count": len(lines),
                       "shortfall_lines": shortfall_lines}})
        for ln in lines:                    # cross-system material aggregation
            # 2026-08-04 COMPONENT IDENTITY: the key is (Material_Code,
            # SAP_Code) — the physical drum — matching the engine's mat_key()
            # and `_material_demand_rows`. `component` (the recipe's
            # Material_Description) used to ride in the key, which could split
            # ONE drum into two aggregate rows when two systems described it
            # differently; the first description still labels the row.
            key = sme_engine.mat_key(ln["material_code"], ln["sap_code"])
            a = agg.setdefault(key, {
                "material_code": ln["material_code"], "sap_code": ln["sap_code"],
                "component": ln["component"],
                "material_name": ln["material_name"], "uom": ln["uom"],
                "required_qty": 0.0, "package_size": ln["package_size"],
                "packages_needed": None,
                "available_stock": ln["available_stock"],
                "pending_delivery": ln["pending_delivery"],
                "total_procured": ln["total_procured"],
                "pooled_saps": 1,
                "shortfall_qty": None, "net_shortfall_qty": None,
                "systems": [], "explanation": ""})
            a["required_qty"] = sme_engine.round_n(
                a["required_qty"] + ln["required_qty"], 4)
            if a["package_size"] is None:
                a["package_size"] = ln["package_size"]
            if c not in a["systems"]:
                a["systems"].append(c)

    agg_lines, agg_short = [], 0
    for a in agg.values():
        req, pkg, avail = a["required_qty"], a["package_size"], a["available_stock"]
        procured = a["total_procured"]
        a["packages_needed"] = (int(-(-req // pkg))
                                if pkg and pkg > 0 and req > 0 else None)
        # PHYSICAL gap vs NET gap, kept apart (2026-08-03 tier segregation).
        a["shortfall_qty"] = (sme_engine.round_n(max(req - avail, 0.0), 4)
                              if avail is not None else None)
        a["net_shortfall_qty"] = (sme_engine.round_n(
            max(req - procured, 0.0), 4) if procured is not None else None)
        if a["shortfall_qty"]:
            agg_short += 1
        uom = a["uom"]
        expl = (f"Σ over {len(a['systems'])} system(s) "
                f"[{', '.join(a['systems'])}]: {req:g} {uom}".strip())
        if a["packages_needed"]:
            expl += f" → {a['packages_needed']} × {pkg:g} {uom} pack(s)"
        if avail is not None:
            expl += f" · in stock: {avail:g}"
            if a["shortfall_qty"]:
                expl += f" (short {a['shortfall_qty']:g}"
                expl += (f", {a['net_shortfall_qty']:g} to buy after "
                         f"{a['pending_delivery']:g} pending delivery of "
                         f"{procured:g} procured)"
                         if a["pending_delivery"] else ")")
            else:
                expl += " ✓"
        a["explanation"] = expl
        agg_lines.append(a)

    return {"codes": [c for c, _ in pairs], "mode": mode,
            "target_total_sqm": sme_engine.round_n(sum(t for _, t in pairs), 4),
            "systems": systems,
            "aggregate": {"lines": agg_lines,
                          "totals": {"line_count": len(agg_lines),
                                     "shortfall_lines": agg_short}}}
