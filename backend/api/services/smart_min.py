"""
backend/api/services/smart_min.py — intelligent minimum stock (Phase 18 Track 4).

`inventory.Minimum_Qty` is 0 on almost every item, so every "below minimum"
signal in the system (the HOD's Low Stock page, the dashboard's Stock vs
Minimum chart, the AI's low-stock template) has been silent. This module
RECOMMENDS a minimum for every (SAP, site) — computed on read, written nowhere —
and says how the stock on the shelf compares to it: red / amber / green.

TWO KINDS OF ITEM, TWO KINDS OF EVIDENCE

  General items       what the site USES. Daily use is the higher of the
                      30-day and the window-day average (a recent surge is not
                      averaged away by a quiet quarter), and the minimum is
                      that rate × the cover days (`min_stock_cover_days`).
  Surface Shields     what the PLAN NEEDS (operator brief: never past use — a
                      lining job draws in bursts that say nothing about the
                      next one). Demand per component = remaining SQM ×
                      For_1_SQM, exactly the estimator's formula
                      (`sme.py::_demand_matrix`), plus Garnet for the surface
                      prep of that same remaining area at the equipment's Old /
                      New rate (`services/prep`). The minimum is the share of
                      that demand the next `cover days` of work will draw:
                      remaining × min(1, pace × cover days / remaining SQM),
                      where pace is, first found: the SITE's planned rate
                      (`ss_planned_sqm_per_day@<site>`, set on the Reorder
                      signals tab — ruling Q6 option B), the global planned
                      rate (`ss_planned_sqm_per_day`), or the approved SQM per
                      day over the last 30 days — paper-form entries AND
                      approved jobs (Phase 21b, D0). With no pace at all
                      the minimum is the WHOLE remaining plan, and the row
                      says so (`basis = plan_all`) — shown, never ORDERED
                      (Phase 21b, D2: ruling Q21-20), and with no days of
                      cover (a total is not a rate, D1).

⚠️ A MANUAL MINIMUM WINS. An item with `Minimum_Qty > 0` keeps it as the
effective minimum (`source = manual`); the recommendation is shown beside it.
Nothing here writes to `inventory` — Live data stays exactly as the operator
set it, and suite 18M pins that.

⚠️ RULES THIS READS UNDER, AND DOES NOT CHANGE
  · rule 1: recipe components are keyed (Material_Code, SAP_Code) — the ledger
    is per SAP, so components are summed onto their SAP only at the end;
  · rule 1c / the parity engines: NEITHER engine is touched. This reads the
    same tables with the same arithmetic as the read-only `_demand_matrix`
    port and compares the result with LIVE ledger stock — the pattern
    `lining_analytics.py` uses — but converts packs ⇄ base units through
    `services/units` (lining_analytics compares packs with KG; noted in
    MORNING_REPORT as a bug to fix separately);
  · Q14-7: minimums are in PACKS, like `Minimum_Qty` and the ledger.

The RAG rule, one place:  min ≤ 0 → none (grey, no demand signal) ·
stock < min → red · stock < min × AMBER_FACTOR → amber · else green.
Suggested order = max(target − stock − on order, 0), rounded up, for red and
amber rows. "On order" is the open PO quantity raised from THIS site's PRs
(Phase 19b); a global PO is shown beside it and subtracted from no site.
For red and amber rows, target = 2 × min (one full cover period above the minimum) —
⚠️ but for a Surface Shield never more than the REMAINING PLAN needs. Without
that cap, a site with no SQM pace (minimum = the whole remaining plan) was
told to order TWICE the project (found in the Phase 18 E2E screenshot: 305,370
AR bricks against a plan of 152,685).
"""
from __future__ import annotations

import datetime as _dt
import math
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import prep as PREP
from . import quality
from . import units as U

AMBER_FACTOR = 1.5
# Ruling Q19-1 (Phase 19a): an ACCEPTED minimum is flagged "changed" when the
# fresh recommendation has moved more than this fraction away from it.
CHANGED_BAND = 0.20
DEFAULTS = {
    "min_stock_cover_days": 30,     # days of use a minimum must cover (lead time + safety)
    "min_stock_window_days": 90,    # the long consumption window
    "ss_pace_window_days": 30,      # approved SQM per day, over this many days
}
SURGE_WINDOW_DAYS = 30


def _num(v, default=0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


async def _setting(session: AsyncSession, key: str, default: float) -> float:
    v = (await session.execute(text(
        "SELECT value FROM app_settings WHERE key = :k"), {"k": key})).scalar()
    x = _num(v, default)
    return x if x > 0 else default


def _norm(s) -> str:
    return "".join(str(s or "").split())


def changed(accepted: Optional[float], recommended: float) -> Optional[float]:
    """The recommendation's move away from an accepted minimum, as a fraction
    of it, when that move is beyond CHANGED_BAND. Otherwise None.

    An accepted 0 ("no minimum here") is changed by any recommendation above
    0, and the move is reported as 1.0."""
    if accepted is None:
        return None
    if accepted <= 0:
        return 1.0 if recommended > 0 else None
    move = abs(recommended - accepted) / accepted
    return round(move, 3) if move > CHANGED_BAND else None


async def accepted_minimums(session: AsyncSession, site_id: Optional[str]) -> dict:
    """{(SAP, site): {qty, by, at}} from inventory_site_overrides (Phase 19a)."""
    sql = ('SELECT TRIM("SAP_Code") AS sap, "Site_ID" AS site, "Minimum_Qty" AS qty, '
           'updated_by AS by, updated_at AS at FROM inventory_site_overrides')
    params = {}
    if site_id:
        sql += ' WHERE "Site_ID" = :s'
        params["s"] = site_id
    return {(_norm(r["sap"]), r["site"]): {"qty": _num(r["qty"]), "by": r["by"],
                                          "at": r["at"].isoformat() if r["at"] else None}
            for r in (await session.execute(text(sql), params)).mappings().all()}


# Phase 19b, ruling Q19-2. An open PO line belongs to the site of the PR it
# was raised from: the line's own PR_Number, else the PO header's, resolved
# through pr_registry (one row per PR) and then pr_master. A line with no PR,
# or a PR the Hub does not know (one raised outside it), is GLOBAL: it is shown
# on every site's row and subtracted from none, so one PO is never counted
# against several sites.
_OPEN_PO_SQL = """
WITH lines AS (
    SELECT UPPER(REPLACE(COALESCE(pi."Material_Code", ''), ' ', '')) AS mat,
           GREATEST(pi."Qty" - COALESCE(pi."Delivered_Qty", 0), 0) AS open_qty,
           COALESCE(NULLIF(TRIM(pi."PR_Number"), ''), NULLIF(TRIM(po."PR_Number"), '')) AS pr
    FROM po_items pi
    LEFT JOIN purchase_orders po ON po."PO_Number" = pi."PO_Number"
    WHERE COALESCE(pi.line_status, 'open') = 'open'
)
SELECT l.mat,
       COALESCE(r."Site_ID", (SELECT MIN(m."Site_ID") FROM pr_master m
                              WHERE m."PR_Number" = l.pr)) AS site,
       SUM(l.open_qty) AS qty
FROM lines l
LEFT JOIN pr_registry r ON r."PR_Number" = l.pr
GROUP BY 1, 2
"""


async def open_po_qty(session: AsyncSession) -> tuple[dict, dict]:
    """({(material, site): open qty}, {material: GLOBAL open qty})."""
    per_site: dict[tuple, float] = {}
    glob: dict[str, float] = {}
    for mat, site, qty in (await session.execute(text(_OPEN_PO_SQL))).all():
        if not mat:
            continue
        if site:
            per_site[(mat, site)] = per_site.get((mat, site), 0.0) + _num(qty)
        else:
            glob[mat] = glob.get(mat, 0.0) + _num(qty)
    return per_site, glob


def rag(stock: float, minimum: float) -> str:
    if minimum <= 0:
        return "none"
    if stock < minimum:
        return "red"
    if stock < minimum * AMBER_FACTOR:
        return "amber"
    return "green"


# ── Surface Shield demand from the plan ──────────────────────────────────────

async def plan_demand(session: AsyncSession, site: str) -> dict:
    """Remaining-plan demand for one site, per SAP, in BASE units.

    {"by_sap": {sap: {"base": float, "keys": [mat_key…], "uom": str,
                      "garnet": bool, "notes": [str]}},
     "remaining_sqm": float, "garnet_flags": [str]}
    """
    from .. import sme_engine as E

    prep_codes = await PREP.prep_codes(session)
    eq = (await session.execute(text(
        'SELECT "Equipment_Tag_No", "Lining_System_Code", "Surface_Area_SQM" '
        'FROM sme_equipment WHERE "Site_ID" = :s'), {"s": site})).mappings().all()
    prog = {(r["Equipment_Tag_No"], r["Lining_System_Code"]): r for r in (await session.execute(text(
        'SELECT "Equipment_Tag_No", "Lining_System_Code", "Original_SQM", "Done_SQM", '
        '"Done_SQM_staged" FROM sme_sqm_progress WHERE "Site_ID" = :s'),
        {"s": site})).mappings().all()}
    recipes: dict[str, list] = {}
    for r in (await session.execute(text(
            'SELECT "Lining_System_Code", "Material_Code", "SAP_Code", "UOM", "For_1_SQM" '
            'FROM sme_recipe ORDER BY id'))).mappings().all():
        recipes.setdefault(str(r["Lining_System_Code"] or "").strip(), []).append(r)

    # A recipe line with no SAP (older workbooks) names its material only:
    # resolve it through inventory.Material_Code, which is UNIQUE — the same
    # direction services/sme_link reads it. A line that resolves nowhere is
    # reported, never guessed.
    mat_to_sap = {str(m).replace(" ", "").upper(): E.sap_norm(sp) for m, sp in (await session.execute(text(
        'SELECT "Material_Code", "SAP_Code" FROM inventory '
        'WHERE COALESCE(TRIM("Material_Code"), \'\') <> \'\''))).all()}
    unresolved: set[str] = set()

    def sap_of(r) -> str:
        sap = E.sap_norm(r["SAP_Code"])
        if sap:
            return sap
        sap = mat_to_sap.get(str(r["Material_Code"] or "").replace(" ", "").upper(), "")
        if not sap:
            unresolved.add(str(r["Material_Code"] or "?"))
        return sap

    by_sap: dict[str, dict] = {}
    remaining_by_tag: dict[str, float] = {}
    total = 0.0
    for e in eq:
        code = str(e["Lining_System_Code"] or "").strip()
        if not code or code in prep_codes:
            continue
        p = prog.get((e["Equipment_Tag_No"], e["Lining_System_Code"]))
        if p is not None:
            rem = max(_num(p["Original_SQM"]) - _num(p["Done_SQM"]) - _num(p["Done_SQM_staged"]), 0.0)
        else:
            rem = max(_num(e["Surface_Area_SQM"]), 0.0)
        if rem <= 0:
            continue
        total += rem
        tag = str(e["Equipment_Tag_No"])
        remaining_by_tag[tag] = remaining_by_tag.get(tag, 0.0) + rem
        seen_saps: set[str] = set()
        for r in recipes.get(code, []):
            d = rem * _num(r["For_1_SQM"])
            if d <= 0:
                continue
            sap = sap_of(r)
            if not sap:
                continue
            row = by_sap.setdefault(sap, {"base": 0.0, "keys": [], "uom": r["UOM"],
                                          "garnet": False, "notes": [], "sqm": 0.0})
            row["base"] += d
            # the m² of work that draws this SAP — once per equipment, even
            # where its system lists the SAP on two lines (primer + screed)
            if sap not in seen_saps:
                row["sqm"] = row.get("sqm", 0.0) + rem
                seen_saps.add(sap)
            k = E.mat_key(r["Material_Code"], r["SAP_Code"])
            if k not in row["keys"]:
                row["keys"].append(k)

    # Garnet: every m² still to be lined is blasted first, at the equipment's
    # Old / New rate. The primary Garnet SAP is the prep code's recipe line.
    flags: list[str] = []
    prep_sap: dict[str, str] = {}
    for c in prep_codes:
        lines = recipes.get(c, [])
        sap = next((x for x in (sap_of(ln) for ln in lines) if x), "")
        if sap:
            prep_sap[c] = sap
    unknown_state = old_fallback = no_code = 0
    for tag, area in remaining_by_tag.items():
        code = await PREP.code_for_tag(session, site_id=site, tag=tag)
        if code is None or code not in prep_sap:
            no_code += 1
            continue
        state = await PREP.last_state(session, site_id=site, tag=tag)
        rate = await PREP.rate(session, code, state) if state else None
        if state is None:
            # No Old/New answer yet: the higher of the two known rates.
            unknown_state += 1
            rate = max([x for x in [await PREP.rate(session, code, "OLD"),
                                    await PREP.rate(session, code, "NEW")] if x] or [0.0])
        elif rate is None:
            old_fallback += 1            # OLD has no benchmark yet → NEW's workbook rate
            rate = await PREP.rate(session, code, "NEW")
        if not rate:
            continue
        row = by_sap.setdefault(prep_sap[code], {"base": 0.0, "keys": [], "uom": "KG",
                                                 "garnet": True, "notes": [], "sqm": 0.0})
        row["garnet"] = True
        row["base"] += area * rate
        row["sqm"] = row.get("sqm", 0.0) + area
    if unknown_state:
        flags.append(f"{unknown_state} equipment tag(s) have no Old/New answer yet — "
                     "Garnet uses the higher rate for them")
    if old_fallback:
        flags.append(f"{old_fallback} tag(s) are OLD surfaces with no OLD benchmark — "
                     "Garnet uses the NEW (workbook) rate; set it under SME → Prep baseline")
    if unresolved:
        flags.append(f"{len(unresolved)} recipe material(s) match no inventory item "
                     f"(e.g. {sorted(unresolved)[0]}) — not counted")
    if no_code:
        flags.append(f"{no_code} tag(s) have no surface-prep code (substrate unknown) — "
                     "no Garnet counted for them")
    return {"by_sap": by_sap, "remaining_sqm": total, "garnet_flags": flags}


async def sqm_pace(session: AsyncSession, site: str, days: float) -> float:
    """Approved LINING SQM per day over the last `days` (prep codes excluded).

    ⚠️ PHASE 21b (D0) — BOTH WAYS AN AREA IS APPROVED. Since Phase 14c most
    Surface Shield m² is approved as a JOB (`sme_attribution_group`, credited
    ONCE per job on approval, `Done_SQM_Credited`), not as a paper-form
    execution entry. This read only the entries, so a site that approves
    through the job queue showed a pace near 0 — the reorder fell back to
    "the whole remaining plan" (`plan_all`): the order far too high and the
    days of cover far too low, which is what the operator reported. A job
    and an entry are separate paths, so adding them never counts an area
    twice; Garnet (prep) jobs credit 0 and are excluded by code as well."""
    cutoff = (_dt.date.today() - _dt.timedelta(days=int(days))).isoformat()
    prep_codes = await PREP.prep_codes(session)
    rows = (await session.execute(text(
        'SELECT "Lining_System_Code", SUM("Actual_SQM") FROM sme_execution_entry '
        'WHERE "Site_ID" = :s AND status = \'APPROVED\' AND "Work_Date" >= :c '
        'GROUP BY 1 '
        'UNION ALL '
        'SELECT "Lining_System_Code", SUM(COALESCE("Done_SQM_Credited", "SQM_Completed")) '
        'FROM sme_attribution_group '
        'WHERE "Site_ID" = :s AND status = \'committed\' AND "Work_Date" >= :c '
        'GROUP BY 1'), {"s": site, "c": cutoff})).all()
    done = sum(_num(v) for c, v in rows if str(c or "").strip() not in prep_codes)
    return done / days if days > 0 else 0.0


# One app_settings row per site — `ss_planned_sqm_per_day@<Site_ID>` — so the
# per-site rate needs no schema change; the bare key stays the global fallback.
SITE_PACE_PREFIX = "ss_planned_sqm_per_day@"


async def site_paces(session: AsyncSession) -> dict[str, float]:
    """{Site_ID: planned SQM per day} for every site that set its own rate."""
    rows = (await session.execute(text(
        "SELECT key, value FROM app_settings WHERE key LIKE :p"),
        {"p": SITE_PACE_PREFIX + "%"})).all()
    out = {str(k)[len(SITE_PACE_PREFIX):]: _num(v) for k, v in rows}
    return {k: v for k, v in out.items() if k and v > 0}


def _g(v: float) -> str:
    """A number as a person writes it: 1,234.5 · 0.85 · 12."""
    v = round(float(v), 2)
    return f"{v:,.2f}".rstrip("0").rstrip(".") if v != int(v) else f"{int(v):,}"


def _trail(p: dict, fac: float, cover: float, base_min: float, base_uom: str,
           pack_uom: str, packs: float) -> list[str]:
    """Phase 21b — HOW a Surface Shield minimum was worked out, one step per
    line, so a wrong number shows WHICH factor is wrong (the m², the recipe
    rate, the pace or the pack size)."""
    sqm = float(p.get("sqm") or 0)
    per = (p["base"] / sqm) if sqm else 0.0
    what = "Garnet for" if p.get("garnet") else "Plan:"
    out = [f"{what} {_g(sqm)} m² still to do × {_g(per)} {base_uom}/m² = "
           f"{_g(p['base'])} {base_uom} for the whole remaining plan"]
    if p["basis"] == "plan_pace":
        out.append(f"× the next {_g(cover)} days at {_g(p['pace'])} m²/day of the site's "
                   f"{_g(p['site_rem'])} m² left ({_g(p['share'] * 100)} %) = {_g(base_min)} {base_uom}")
    else:
        out.append("No SQM pace yet, so this is the WHOLE plan — set the site's pace to "
                   "plan by rate")
    out.append(f"÷ {_g(fac)} {base_uom} per {pack_uom} = {_g(base_min / fac)} → "
               f"{_g(packs)} {pack_uom}")
    return out


# ── the whole table ──────────────────────────────────────────────────────────

async def compute(session: AsyncSession, site_id: Optional[str]) -> dict:
    """Every (SAP, site) with a recommendation, its RAG status and the why.

    `site_id`: a site → that site only; None → every site (unscoped roles).
    """
    from ..stock import SQL_SITE_STOCK

    cover = await _setting(session, "min_stock_cover_days", DEFAULTS["min_stock_cover_days"])
    window = await _setting(session, "min_stock_window_days", DEFAULTS["min_stock_window_days"])
    pace_days = await _setting(session, "ss_pace_window_days", DEFAULTS["ss_pace_window_days"])
    planned = _num((await session.execute(text(
        "SELECT value FROM app_settings WHERE key = 'ss_planned_sqm_per_day'"))).scalar())
    site_plans = await site_paces(session)
    ss_cat = (await quality.controlled_category(session)).strip().lower()
    today = _dt.date.today()
    c_long = (today - _dt.timedelta(days=int(window))).isoformat()
    c_short = (today - _dt.timedelta(days=SURGE_WINDOW_DAYS)).isoformat()

    where, params = "", {"cl": c_long, "cs": c_short}
    if site_id:
        where = 'WHERE s."Site_ID" = :site'
        params["site"] = site_id
    stock_rows = (await session.execute(text(f'''
        SELECT s."SAP_Code", s."Site_ID", s."Current_Stock", s."Minimum_Qty"
        FROM ({SQL_SITE_STOCK}) s {where}'''), params)).mappings().all()
    use = {(r["sap"], r["site"]): (_num(r["q_long"]), _num(r["q_short"]))
           for r in (await session.execute(text(f'''
        SELECT TRIM("SAP_Code") AS sap, COALESCE("Site_ID", 'HQ') AS site,
               SUM(CASE WHEN "Date" >= :cl THEN "Quantity" ELSE 0 END) AS q_long,
               SUM(CASE WHEN "Date" >= :cs THEN "Quantity" ELSE 0 END) AS q_short
        FROM consumption WHERE "Date" >= :cl
        {('AND COALESCE("Site_ID", \'HQ\') = :site' if site_id else '')}
        GROUP BY 1, 2'''), params)).mappings().all()}
    inv = {_norm(r["SAP_Code"]): r for r in (await session.execute(text(
        'SELECT "SAP_Code", "Material_Code", "Equipment_Description", "Category", "UOM", '
        '"Unit_Size", "Base_UOM", COALESCE("Minimum_Qty", 0) AS "Minimum_Qty" '
        'FROM inventory'))).mappings().all()}
    on_order, global_on_order = await open_po_qty(session)
    # Phase 22d (ruling Q22-13): a request mailed WITHOUT a PR and not yet
    # received is on order too — labelled apart from the POs
    from . import requests_sync as _req
    requested_no_pr = await _req.pending_no_pr(session)

    accepted = await accepted_minimums(session, site_id)

    stock: dict[tuple, float] = {}
    for r in stock_rows:
        stock[(_norm(r["SAP_Code"]), r["Site_ID"])] = _num(r["Current_Stock"])

    # Sites in scope: the one asked for, else every site with stock or a plan.
    if site_id:
        sites = [site_id]
    else:
        sites = sorted({s for (_, s) in stock} | {r[0] for r in (await session.execute(text(
            'SELECT DISTINCT "Site_ID" FROM sme_equipment WHERE "Site_ID" IS NOT NULL'))).all()})

    rows: list[dict] = []
    site_info: dict[str, dict] = {}
    plan_saps: dict[tuple, dict] = {}
    for s in sites:
        plan = await plan_demand(session, s)
        # Precedence (ruling Q6, option B): the site's own planned rate, then
        # the global one, then what the site actually got approved lately.
        # The approved rate is computed EVERY time — it is the suggestion the
        # Set-pace dialog offers even when a plan overrides it.
        approved = await sqm_pace(session, s, pace_days)
        if site_plans.get(s, 0) > 0:
            pace, source = site_plans[s], "site_plan"
        elif planned > 0:
            pace, source = planned, "planned"
        else:
            pace, source = approved, "approved_entries"
        rem = plan["remaining_sqm"]
        if rem > 0 and pace > 0:
            share, basis = min(1.0, pace * cover / rem), "plan_pace"
        else:
            share, basis = 1.0, "plan_all"
        site_info[s] = {"remaining_sqm": round(rem, 2), "sqm_per_day": round(pace, 2),
                        "pace_source": source,
                        "suggested_sqm_per_day": round(approved, 2),
                        "site_planned_sqm_per_day": site_plans.get(s) or None,
                        "plan_share": round(share, 4), "basis": basis,
                        "flags": plan["garnet_flags"]}
        for sap, d in plan["by_sap"].items():
            plan_saps[(sap, s)] = {**d, "share": share, "basis": basis, "pace": pace,
                                   "site_rem": rem}

    garnet_saps = await PREP.garnet_saps(session)
    # an accepted minimum shows even where the item has no stock movement yet
    keys = set(stock) | set(plan_saps) | set(accepted)
    for (sap, s) in sorted(keys, key=lambda k: (k[1], k[0])):
        i = inv.get(sap)
        if i is None:
            continue
        is_ss = str(i["Category"] or "").strip().lower() == ss_cat
        st = stock.get((sap, s), 0.0)
        manual = _num(i["Minimum_Qty"])
        fac = U.factor(is_surface_shield=is_ss, unit_size=i["Unit_Size"], pack_uom=i["UOM"])
        plan_packs: Optional[float] = None
        ss_rate: Optional[float] = None
        row = {"SAP_Code": i["SAP_Code"], "Site_ID": s, "Material_Code": i["Material_Code"],
               "Description": i["Equipment_Description"], "Category": i["Category"],
               "UOM": i["UOM"], "Surface_Shield": is_ss, "Current_Stock": round(st, 3),
               "Manual_Min": manual or None, "Recommended_Min": 0.0, "Basis": "no_use",
               "Daily_Use": None, "Plan_Demand_Base": None, "Base_UOM": None,
               "Why": "", "Trail": None,
               "On_Order": round(on_order.get((_norm(i["Material_Code"]).upper(), s), 0.0), 3),
               "Global_On_Order": round(global_on_order.get(_norm(i["Material_Code"]).upper(), 0.0), 3),
               "Requested_No_PR": round(requested_no_pr.get((sap, s), 0.0), 3)}
        if is_ss:
            p = plan_saps.get((sap, s))
            if p is None:
                row["Basis"] = "garnet_pool" if sap in garnet_saps else "no_plan"
                row["Why"] = ("Garnet — counted in the site's Garnet requirement on the "
                              "primary Garnet SAP" if sap in garnet_saps else
                              "Surface Shield with no remaining planned SQM")
            else:
                base_min = p["base"] * p["share"]
                row["Plan_Demand_Base"] = round(p["base"], 3)
                row["Base_UOM"] = U.base_uom(pack_uom=i["UOM"], stored=i["Base_UOM"], fac=fac) or p["uom"]
                row["Basis"] = p["basis"]
                if fac:
                    row["Recommended_Min"] = math.ceil(round(base_min / fac, 6))
                    plan_packs = math.ceil(round(p["base"] / fac, 6))
                    # D3 — the daily rate in EXACT packs. It used to be the
                    # rounded-up minimum ÷ 30, so a slow mover needing 0.2 of a
                    # pack a month read as 1 pack a month: 5× the true rate and
                    # a fifth of the true days of cover.
                    if p["basis"] == "plan_pace":
                        ss_rate = p["base"] * p["pace"] / p["site_rem"] / fac if p["site_rem"] else 0.0
                    row["Trail"] = _trail(p, fac, cover, base_min, row["Base_UOM"] or p["uom"] or "",
                                          i["UOM"] or "packs", row["Recommended_Min"])
                    days = int(cover)
                    row["Why"] = ((f"{'Garnet for' if p['garnet'] else 'Plan:'} "
                                   f"{round(base_min, 1):g} {row['Base_UOM'] or ''} for the next "
                                   f"{days} days of planned work") if p["basis"] == "plan_pace" else
                                  (f"{'Garnet for' if p['garnet'] else 'Plan:'} the WHOLE remaining "
                                   f"plan, {round(base_min, 1):g} {row['Base_UOM'] or ''} — no SQM "
                                   "pace yet"))
                    if p["garnet"]:
                        row["Basis"] = p["basis"] + "_garnet"
                else:
                    row["Basis"] = "no_pack_size"
                    row["Why"] = (f"Plan needs {round(base_min, 1):g} {p['uom'] or ''}, but the item "
                                  "has no pack size (Unit_Size) to convert it to packs")
        else:
            q_long, q_short = use.get((sap, s), (0.0, 0.0))
            daily = max(q_long / window, q_short / SURGE_WINDOW_DAYS)
            row["Daily_Use"] = round(daily, 4)
            if daily > 0:
                row["Recommended_Min"] = math.ceil(round(daily * cover, 6))
                row["Basis"] = "consumption"
                lead = "30-day" if q_short / SURGE_WINDOW_DAYS >= q_long / window else f"{int(window)}-day"
                row["Why"] = (f"Uses {daily:.2f}/day ({lead} average) × {int(cover)} days of cover")
            else:
                row["Why"] = f"No use in the last {int(window)} days"
        # Precedence (Phase 19a): the site's ACCEPTED minimum, then the item's
        # global manual minimum, then the recommendation.
        acc = accepted.get((sap, s))
        row["Accepted_Min"] = acc["qty"] if acc else None
        row["Accepted_By"] = acc["by"] if acc else None
        row["Accepted_At"] = acc["at"] if acc else None
        row["Changed"] = changed(row["Accepted_Min"], _num(row["Recommended_Min"]))
        if acc is not None:
            eff, src = acc["qty"], "accepted"
        elif manual > 0:
            eff, src = manual, "manual"
        else:
            eff = _num(row["Recommended_Min"])
            src = "smart" if eff > 0 else None
        row["Effective_Min"] = eff
        row["Min_Source"] = src
        row["Status"] = rag(st, eff)
        row["Order_Hint"] = None
        if is_ss and row["Basis"].startswith("plan_all") and src == "smart":
            # D2 (ruling Q21-20) — with no SQM pace the system's minimum is the
            # WHOLE remaining plan (kept, with the yellow "whole plan" tag, so
            # the HOD can see it and accept a figure). It no longer orders: a
            # minimum that is a project total is not a reorder point, and
            # ordering against it told a site to buy the entire project now.
            # An accepted or manual minimum still orders as usual.
            row["Suggested_Order"] = 0
            row["Order_Hint"] = "Set the site's SQM pace to get an order quantity"
        elif row["Status"] in ("red", "amber"):
            target = 2 * eff
            if plan_packs is not None:
                target = min(target, max(plan_packs, eff))   # never past the plan
            row["Suggested_Order"] = math.ceil(max(
                target - st - row["On_Order"] - row["Requested_No_PR"], 0))
        else:
            row["Suggested_Order"] = 0
        # D1 — days of cover need a RATE. Without an SQM pace there is none: the
        # "whole plan" is a total, not a speed, and dividing it by 30 days
        # (as this did) made every lining item look nearly out of cover.
        rate = row["Daily_Use"] if not is_ss else ss_rate
        row["Days_Of_Cover"] = round(st / rate, 1) if rate and rate > 0 and st > 0 else (0.0 if rate else None)
        rows.append(row)

    order = {"red": 0, "amber": 1, "green": 2, "none": 3}
    rows.sort(key=lambda r: (order[r["Status"]], r["Site_ID"], -(r["Suggested_Order"] or 0), r["SAP_Code"]))
    counts = {k: sum(1 for r in rows if r["Status"] == k) for k in order}
    counts["changed"] = sum(1 for r in rows if r["Changed"] is not None)
    counts["accepted"] = sum(1 for r in rows if r["Min_Source"] == "accepted")
    return {"items": rows, "counts": counts, "sites": site_info,
            "params": {"cover_days": cover, "window_days": window,
                       "surge_window_days": SURGE_WINDOW_DAYS, "pace_window_days": pace_days,
                       "planned_sqm_per_day": planned or None, "amber_factor": AMBER_FACTOR,
                       "changed_band": CHANGED_BAND}}
