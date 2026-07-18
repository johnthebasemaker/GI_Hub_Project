"""
Phase 8-1 — Predictive lining analytics: GET /analytics/lining-coverage.

The legacy Streamlit material-estimator predicted how much lining work the
site can still execute from the materials on hand. This module bridges that
engineering logic to the LIVE ledger: it runs the same read-only SME planning
engine (backend/api/sme_engine.py — dual TS/Python, golden-parity, NEVER
modified here), but swaps the engine's material availability input from the
frozen `sme_inventory_seed` snapshot to the live PostgreSQL stock
(Σ receipts − Σ consumption − Σ returns per material, joined on the GI
Material_Code), then rolls the result up into rubber-lining (RL) and
brick-lining (BL) coverage:

  · per FAMILY   remaining SQM vs achievable SQM + coverage % + bottlenecks
  · per SYSTEM   the same, sorted worst-coverage-first
  · per MATERIAL demand vs live stock vs shortfall, plus a burn-rate
                 prediction (90-day daily consumption → days of cover →
                 depletion date) — the "predictive" half.

Family classification is heuristic but matches the procurement RL/BL
separation: system NAMES are tokenized (…RL… = rubber, CBL/ARTL/brick/stone =
brick — composites like "RL+CBL 30THK" count in BOTH), and material names are
checked first (BRICK/STONE → BL, CHEMOLINE/RUBBER → RL) before inheriting
from the systems that consume them.
"""
from __future__ import annotations

import datetime as _dt
import re
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import sme_engine
from .auth import require_roles, site_scope
from .db import get_session
from .exec_summary import _capacity_from_lines
from .sme import _snapshot_rows

router = APIRouter(prefix="/analytics", tags=["analytics"])

FAMILY_LABELS = {"RL": "Rubber lining", "BL": "Brick lining", "OTHER": "Other linings"}

_RL_SYS = re.compile(r"(?:^|[+ ])RL|RUBBER", re.I)
_BL_SYS = re.compile(r"CBL|ARTL|BRICK|STONE|TILE", re.I)
_RL_MAT = re.compile(r"CHEMOLINE|RUBBER", re.I)
_BL_MAT = re.compile(r"BRICK|STONE|TILE", re.I)


def system_families(system_name: str | None, system_code: str | None) -> set[str]:
    """A composite system ("RL+CBL 30THK") belongs to BOTH families."""
    blob = f"{system_name or ''} {system_code or ''}"
    fams = set()
    if _RL_SYS.search(blob):
        fams.add("RL")
    if _BL_SYS.search(blob):
        fams.add("BL")
    return fams or {"OTHER"}


def material_family(material_name: str | None, from_systems: set[str]) -> str:
    name = material_name or ""
    if _BL_MAT.search(name):
        return "BL"
    if _RL_MAT.search(name):
        return "RL"
    for fam in ("RL", "BL"):  # single-family consumers inherit it
        if from_systems == {fam}:
            return fam
    return sorted(from_systems)[0] if from_systems else "OTHER"


async def _live_stock_by_material(session: AsyncSession, site: Optional[str]) -> dict[str, dict]:
    """Live ledger stock + 90-day burn per GI Material_Code (site-filtered).

    The material set is driven by the site's inventory rows that carry a
    Material_Code, but each material's SAP POOL is widened with the variant
    SAPs recorded in sme_recipe for that Material_Code (2026-07-18 rule: one
    material code ↔ several variant SAPs; only the base SAP carries the
    Material_Code in the ERP master, so an inventory-only join under-counts).
    SAP strings are whitespace-normalized on both sides ("1043 - 2")."""
    site_w = "AND COALESCE(i.\"Site_ID\",'HQ') = :site" if site else ""
    ledger_site = "WHERE COALESCE(\"Site_ID\",'HQ') = :site" if site else ""
    params: dict = {"site": site} if site else {}
    params["burn_from"] = (_dt.date.today() - _dt.timedelta(days=90)).isoformat()
    rows = (await session.execute(text(f'''
        WITH inv_map AS (
            SELECT TRIM(i."Material_Code") AS mat,
                   REPLACE(TRIM(i."SAP_Code"), ' ', '') AS sap
            FROM inventory i
            WHERE i."Material_Code" IS NOT NULL
              AND TRIM(i."Material_Code") <> '' {site_w}
        ),
        map AS (
            SELECT mat, sap FROM inv_map
            UNION
            SELECT TRIM(r."Material_Code"),
                   REPLACE(TRIM(r."SAP_Code"), ' ', '')
            FROM sme_recipe r
            WHERE r."SAP_Code" IS NOT NULL
              AND TRIM(r."Material_Code") IN (SELECT mat FROM inv_map)
        ),
        led AS (
            SELECT sap, SUM(q) AS stock, SUM(burn) AS burn_90d FROM (
                SELECT REPLACE(TRIM("SAP_Code"), ' ', '') AS sap,
                       COALESCE("Quantity", 0) AS q, 0::float AS burn
                FROM receipts {ledger_site}
                UNION ALL
                SELECT REPLACE(TRIM("SAP_Code"), ' ', ''),
                       -COALESCE("Quantity", 0),
                       CASE WHEN "Date" >= :burn_from
                            THEN COALESCE("Quantity", 0) ELSE 0 END
                FROM consumption {ledger_site}
                UNION ALL
                SELECT REPLACE(TRIM("SAP_Code"), ' ', ''),
                       -COALESCE("Quantity", 0), 0::float
                FROM returns {ledger_site}
            ) t GROUP BY sap
        )
        SELECT m.mat AS material_code,
               SUM(COALESCE(l.stock, 0))    AS live_stock,
               SUM(COALESCE(l.burn_90d, 0)) AS burn_90d
        FROM map m LEFT JOIN led l ON l.sap = m.sap
        GROUP BY m.mat'''), params)).mappings().all()
    return {r["material_code"]: {"live_stock": float(r["live_stock"] or 0),
                                 "burn_90d": float(r["burn_90d"] or 0)} for r in rows}


@router.get("/lining-coverage",
            summary="Predictive RL/BL lining coverage from LIVE stock (hod/logistics)")
async def lining_coverage(site_id: Optional[str] = Query(None),
                          user: dict = Depends(require_roles("hod", "logistics")),
                          session: AsyncSession = Depends(get_session)):
    scope = site_scope(user)
    site = scope if scope is not None else ((site_id or "").strip() or "CNCEC")
    if scope is not None and not scope:
        return {"site": None, "families": [], "per_system": [], "materials": [],
                "message": "your account has no site assigned"}

    # 1. engineering inputs (READ-ONLY sme_* tables) + live ledger stock
    snap = await _snapshot_rows(session, site)
    live = await _live_stock_by_material(session, site)

    # 2. swap the engine's availability pool to the LIVE ledger figures
    materials, live_n, seed_n = [], 0, 0
    for m in snap["materials"]:
        code = (m.get("material_code") or "").strip()
        if code in live:
            materials.append({**m, "available_qty": live[code]["live_stock"]})
            live_n += 1
        else:  # engineering material not in the live master yet — keep the seed
            materials.append(m)
            seed_n += 1

    if not snap["equipment"]:
        return {"site": site, "generated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                "source": {"live": live_n, "seed_only": seed_n},
                "families": [], "per_system": [], "materials": [],
                "message": f"no SME equipment registered for site {site}"}

    model = sme_engine.build_model(snap["equipment"], snap["recipes"],
                                   materials, snap["progress"])
    lines = sme_engine.cascade_allocate(model, model["default_order"])
    _, per_system = _capacity_from_lines(model, lines)

    # 3. family + system rollups
    sysmeta = {}
    for r in snap["recipes"]:
        sysmeta.setdefault(str(r["Lining_System_Code"]),
                           system_families(r.get("Lining_System_Name"),
                                           str(r["Lining_System_Code"])))
    fam_agg: dict[str, dict] = {}
    for s in per_system:
        fams = sysmeta.get(str(s["System_Code"]), {"OTHER"})
        s["families"] = sorted(fams)
        rem, ach = float(s["Remaining_SQM"]), float(s["Achievable_SQM"])
        s["Coverage_Pct"] = round(min(ach / rem, 1.0) * 100, 1) if rem > 0 else 100.0
        for fam in fams:
            f = fam_agg.setdefault(fam, {"family": fam, "label": FAMILY_LABELS[fam],
                                         "remaining_sqm": 0.0, "achievable_sqm": 0.0,
                                         "systems": [], "bottlenecks": []})
            f["remaining_sqm"] += rem
            f["achievable_sqm"] += ach
            f["systems"].append(s["System_Code"])
    per_system.sort(key=lambda s: (s["Coverage_Pct"], -float(s["Remaining_SQM"])))

    # 4. per-material demand vs live stock + burn prediction
    demand: dict[str, dict] = {}
    for ln in lines:
        code = (ln.get("Material_Code") or "").strip()
        d = demand.setdefault(code, {
            "material_code": code, "material_name": ln.get("Material_Name") or "",
            "uom": ln.get("UOM") or "", "demand_qty": 0.0, "allocated_qty": 0.0,
            "shortfall_qty": 0.0, "systems": set()})
        d["demand_qty"] += float(ln.get("Demand_Qty") or 0)
        d["allocated_qty"] += float(ln.get("Allocated_Qty") or 0)
        d["shortfall_qty"] += float(ln.get("Shortfall_Qty") or 0)
        d["systems"].add(str(ln.get("Lining_System_Code") or ""))

    out_materials, today = [], _dt.date.today()
    for code, d in demand.items():
        sys_fams: set[str] = set()
        for sc in d["systems"]:
            sys_fams |= sysmeta.get(sc, set())
        lv = live.get(code, {})
        stock = lv.get("live_stock")
        burn_daily = round(lv.get("burn_90d", 0.0) / 90.0, 4) if lv else 0.0
        days_cover = (round(stock / burn_daily, 1)
                      if stock is not None and burn_daily > 0 else None)
        out_materials.append({
            "material_code": code, "material_name": d["material_name"], "uom": d["uom"],
            "family": material_family(d["material_name"], sys_fams),
            "systems": sorted(d["systems"]),
            "demand_qty": round(d["demand_qty"], 2),
            "allocated_qty": round(d["allocated_qty"], 2),
            "shortfall_qty": round(d["shortfall_qty"], 2),
            "live_stock": round(stock, 2) if stock is not None else None,
            "stock_source": "live" if code in live else "seed",
            "burn_per_day_90d": burn_daily,
            "days_of_cover": days_cover,
            "depletion_date": ((today + _dt.timedelta(days=int(days_cover))).isoformat()
                               if days_cover is not None else None),
        })
        if d["shortfall_qty"] > 0:
            fam = material_family(d["material_name"], sys_fams)
            if fam in fam_agg and len(fam_agg[fam]["bottlenecks"]) < 5:
                fam_agg[fam]["bottlenecks"].append(
                    f'{code} ({d["material_name"]}) short {round(d["shortfall_qty"], 1)} {d["uom"]}')
    # worst first: biggest shortfall, then fewest days of cover
    out_materials.sort(key=lambda m: (-m["shortfall_qty"],
                                      m["days_of_cover"] if m["days_of_cover"] is not None else 1e9))

    families = []
    for fam in ("RL", "BL", "OTHER"):
        if fam not in fam_agg:
            continue
        f = fam_agg[fam]
        rem, ach = round(f["remaining_sqm"], 2), round(f["achievable_sqm"], 2)
        families.append({**f, "remaining_sqm": rem, "achievable_sqm": ach,
                         "coverage_pct": round(min(ach / rem, 1.0) * 100, 1) if rem > 0 else 100.0})

    return {
        "site": site,
        "generated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": {"live": live_n, "seed_only": seed_n},
        "families": families,
        "per_system": per_system,
        "materials": out_materials,
    }
