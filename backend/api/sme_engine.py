"""
backend/api/sme_engine.py — pure port of the legacy SME allocation engine
(Phase S1, READ-ONLY: no DB access, no framework imports).

This is the server-side "parity oracle" for the client-side TypeScript engine
(frontend/src/sme/engine.ts). The two implementations are line-for-line mirrors
and are proven equal against the shared golden fixture
(sme_parity_fixture.json / sme_parity_golden.json) — the Python side in
service_tests.py, the TS side in frontend/scripts/sme_parity.mjs.

Semantics ported from pages_internal/material_estimator_portal.py
`cascade_allocate` (the live drag-priority algorithm, per (tag, system code,
material) granularity) and pages_internal/material_estimator_engine.py
(feasibility, suggestion simulation, procurement list):

  * demand         = For_1_SQM × remaining SQM, where remaining =
                     max(Original_SQM − (Done_SQM + Done_SQM_staged), 0),
                     falling back to the summed Surface_Area_SQM when no
                     progress row exists (legacy load_all() steps 5–7).
  * allocation     = one GLOBAL pool per material; tags consume it strictly in
                     priority order; codes within a tag in numeric-first order;
                     materials within a code in recipe (id) order.
  * rounding       = quantities 4 dp, percentages 2 dp — matching the legacy
                     cascade. round_n() is half-up via floor(x·10ⁿ + 0.5) in
                     BOTH languages so ties can never diverge between runtimes
                     (Python's built-in round() is half-even; JS has no
                     built-in — this shared formula replaces both).
  * statuses       = the exact legacy label strings (✅ / 🟡 / 🔴).

Deliberate, documented deviations from legacy:
  * non-numeric system codes sort after numeric ones instead of crashing
    (legacy used int(code) and would raise ValueError);
  * bottleneck material = first line at the minimum fulfillment rate in
    cascade order (legacy relied on an unstable pandas sort for ties);
  * suggestion rows sort stably by (-count, -gain) keeping candidate order on
    ties (legacy sort_values quicksort is not stable).
"""
from __future__ import annotations

import math
from typing import Any

STATUS_FULL = "✅ 100% Fully Ready to Build"
STATUS_PARTIAL = "🟡 Partially Ready"
STATUS_BLOCKED = "🔴 Blocked by Shortages"


def round_n(x: float, n: int) -> float:
    """Half-up rounding shared verbatim with the TS engine (see module doc)."""
    if x != x or x in (float("inf"), float("-inf")):  # NaN/inf guard
        return 0.0
    s = 10.0 ** n
    return -math.floor(-x * s + 0.5) / s if x < 0 else math.floor(x * s + 0.5) / s


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _num(v: Any) -> float:
    try:
        f = float(v)
        return f if f == f else 0.0  # NaN → 0 (legacy fillna(0))
    except (TypeError, ValueError):
        return 0.0


def _s(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def syscode_sort_key(code: str) -> tuple:
    """Numeric-first ordering for lining-system codes (legacy sorted by int)."""
    s = _s(code)
    return (0, int(s), "") if s.isdigit() else (1, 0, s)


# ─── Model ────────────────────────────────────────────────────────────────────
def build_model(equipment: list[dict], recipes: list[dict],
                materials: list[dict], progress: list[dict]) -> dict:
    """Normalize the /sme/model-snapshot payload into the engine's model.

    Mirrors legacy load_all() steps 2–7: per-(tag, code) units with summed
    original SQM and progress-derived remaining SQM; recipe rows grouped per
    code preserving id order; one global availability pool per material.
    """
    recipes_by_code: dict[str, list[dict]] = {}
    short_name_by_code: dict[str, str] = {}
    for r in recipes:
        code = _s(r.get("Lining_System_Code"))
        row = {"Material_Code": _s(r.get("Material_Code")),
               "Material_Name": _s(r.get("Material_Name")),
               "UOM": _s(r.get("UOM")),
               "For_1_SQM": _num(r.get("For_1_SQM"))}
        recipes_by_code.setdefault(code, []).append(row)
        if code not in short_name_by_code:
            short_name_by_code[code] = _s(r.get("Lining_System_Name"))

    prog: dict[tuple[str, str], dict] = {}
    for p in progress:
        key = (_s(p.get("Equipment_Tag_No")), _s(p.get("Lining_System_Code")))
        orig = _num(p.get("Original_SQM"))
        done = _num(p.get("Done_SQM")) + _num(p.get("Done_SQM_staged"))
        prog[key] = {"original": orig, "done": done,
                     "remaining": max(orig - done, 0.0)}

    units: dict[tuple[str, str], dict] = {}
    tag_meta: dict[str, dict] = {}
    codes_by_tag: dict[str, list[str]] = {}
    for e in equipment:
        tag = _s(e.get("Equipment_Tag_No"))
        code = _s(e.get("Lining_System_Code"))
        if not tag:
            continue
        if tag not in tag_meta:
            tag_meta[tag] = {"Name": _s(e.get("Name")),
                             "Location": _s(e.get("Location")),
                             "Type": _s(e.get("Type")),
                             "Substrate": _s(e.get("Substrate"))}
            codes_by_tag[tag] = []
        u = units.get((tag, code))
        if u is None:
            units[(tag, code)] = {"total_original": _num(e.get("Surface_Area_SQM"))}
            codes_by_tag[tag].append(code)
        else:
            u["total_original"] += _num(e.get("Surface_Area_SQM"))
    for (tag, code), u in units.items():
        p = prog.get((tag, code))
        u["remaining"] = p["remaining"] if p is not None else u["total_original"]
        u["done"] = p["done"] if p is not None else 0.0
        u["short_name"] = short_name_by_code.get(code, "")
    for tag in codes_by_tag:
        codes_by_tag[tag].sort(key=syscode_sort_key)

    pool_init: dict[str, float] = {}
    mat_meta: dict[str, dict] = {}
    for m in materials:
        mat = _s(m.get("material_code"))
        pool_init[mat] = _num(m.get("available_qty"))
        mat_meta[mat] = {"Material_Name": _s(m.get("material_name")),
                         "UOM": _s(m.get("uom"))}

    return {"units": units, "codes_by_tag": codes_by_tag,
            "recipes_by_code": recipes_by_code, "pool_init": pool_init,
            "mat_meta": mat_meta, "tag_meta": tag_meta,
            "default_order": sorted(codes_by_tag)}


def _dedupe(order: list[str]) -> list[str]:
    seen, out = set(), []
    for t in order:
        t = _s(t)
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


# ─── Cascade allocation (legacy cascade_allocate port) ───────────────────────
def cascade_allocate(model: dict, order: list[str]) -> list[dict]:
    pool = dict(model["pool_init"])
    lines: list[dict] = []
    for tag in _dedupe(order):
        for code in model["codes_by_tag"].get(tag, []):
            unit = model["units"][(tag, code)]
            remaining = unit["remaining"]
            for r in model["recipes_by_code"].get(code, []):
                mat = r["Material_Code"]
                demand = r["For_1_SQM"] * remaining
                before = pool.get(mat, 0.0)
                alloc = min(demand, before)
                after = max(0.0, before - alloc)
                pool[mat] = after
                d4, a4 = round_n(demand, 4), round_n(alloc, 4)
                lines.append({
                    "Equipment_Tag_No": tag,
                    "Lining_System_Code": code,
                    "Lining_System_Short_Name": unit["short_name"],
                    "Total_SQM": round_n(remaining, 2),
                    "Material_Code": mat,
                    "Material_Name": r["Material_Name"] or
                                     model["mat_meta"].get(mat, {}).get("Material_Name", ""),
                    "UOM": r["UOM"],
                    "Demand_Qty": d4,
                    "Allocated_Qty": a4,
                    "Shortfall_Qty": round_n(demand - alloc, 4),
                    "Pool_Before": round_n(before, 4),
                    "Pool_After": round_n(after, 4),
                    "Fulfillment_Pct": round_n(_clip(a4 / d4 * 100.0, 0.0, 100.0), 2)
                                       if d4 > 0 else 100.0,
                })
    return lines


# ─── Feasibility (legacy compute_feasibility port, cascade granularity) ──────
def compute_feasibility(model: dict, lines: list[dict], order: list[str]) -> list[dict]:
    by_tag: dict[str, list[dict]] = {}
    for ln in lines:
        by_tag.setdefault(ln["Equipment_Tag_No"], []).append(ln)

    out: list[dict] = []
    for rank, tag in enumerate(_dedupe(order), start=1):
        rows = by_tag.get(tag)
        if not rows:
            continue
        demand = sum(r["Demand_Qty"] for r in rows)
        alloc = sum(r["Allocated_Qty"] for r in rows)
        short = sum(r["Shortfall_Qty"] for r in rows)
        completion = round_n(_clip(alloc / demand * 100.0, 0.0, 100.0), 2) \
            if demand > 0 else 100.0
        min_rate, bottleneck = 2.0, None
        for r in rows:
            rate = _clip(r["Allocated_Qty"] / r["Demand_Qty"], 0.0, 1.0) \
                if r["Demand_Qty"] > 0 else 1.0
            if rate < min_rate:  # strict: first line at the minimum wins ties
                min_rate, bottleneck = rate, r
        if short <= 0:
            status = STATUS_FULL
        elif min_rate == 0.0:
            status = STATUS_BLOCKED
        else:
            status = f"{STATUS_PARTIAL} ({completion:.1f}%)"
        has_bn = bottleneck is not None and bottleneck["Shortfall_Qty"] > 0
        out.append({
            "Priority_Rank": rank,
            "Equipment_Tag_No": tag,
            "Name": model["tag_meta"].get(tag, {}).get("Name", ""),
            "Total_Demand_Qty": round_n(demand, 4),
            "Total_Allocated_Qty": round_n(alloc, 4),
            "Total_Shortfall_Qty": round_n(short, 4),
            "Completion_Pct": completion,
            "Status": status,
            "Bottleneck_Material_Code": bottleneck["Material_Code"] if has_bn else "—",
            "Bottleneck_Material_Name": bottleneck["Material_Name"] if has_bn else "—",
            "Bottleneck_Shortfall": bottleneck["Shortfall_Qty"] if has_bn else 0.0,
        })
    return out


# ─── Suggestion engine (legacy run_suggestion_engine port) ───────────────────
def run_suggestion_engine(model: dict, order: list[str]) -> dict:
    order = _dedupe(order)
    base_feas = compute_feasibility(model, cascade_allocate(model, order), order)
    base_full = {f["Equipment_Tag_No"] for f in base_feas if f["Status"] == STATUS_FULL}
    base_completion = {f["Equipment_Tag_No"]: f["Completion_Pct"] for f in base_feas}
    candidates = [f["Equipment_Tag_No"] for f in base_feas if f["Status"] != STATUS_FULL]

    rows: list[dict] = []
    best_score, best_detail = (-1, -999.0), []
    for pause in candidates:
        sim_order = [t for t in order if t != pause]
        sim_feas = compute_feasibility(
            model, cascade_allocate(model, sim_order), sim_order)
        sim_full = {f["Equipment_Tag_No"] for f in sim_feas if f["Status"] == STATUS_FULL}
        sim_completion = {f["Equipment_Tag_No"]: f["Completion_Pct"] for f in sim_feas}
        newly = sorted(sim_full - base_full)
        gains = [sim_completion[f["Equipment_Tag_No"]] - f["Completion_Pct"]
                 for f in base_feas
                 if f["Equipment_Tag_No"] != pause
                 and f["Equipment_Tag_No"] in sim_completion]
        avg_gain = sum(gains) / len(gains) if gains else 0.0
        rows.append({
            "Pause_Tag": pause,
            "Pause_Name": model["tag_meta"].get(pause, {}).get("Name", "") or pause,
            "Newly_Completable_Count": len(newly),
            "Newly_Completable_Tags": ", ".join(newly) if newly else "—",
            "Avg_Completion_Gain_Pct": round_n(avg_gain, 2),
            "Net_Gain_Score": len(newly) - 1,
            "Recommended": False,
        })
        score = (len(newly), avg_gain)
        if score > best_score:
            best_score = score
            best_detail = [{**f, "Scenario": f"If '{pause}' is paused"} for f in sim_feas]

    rows.sort(key=lambda r: (-r["Newly_Completable_Count"],
                             -r["Avg_Completion_Gain_Pct"]))  # stable on ties
    if rows:
        rows[0]["Recommended"] = True
    return {"suggestions": rows, "best_detail": best_detail}


# ─── Procurement list + per-material totals ──────────────────────────────────
def build_procurement_list(model: dict, lines: list[dict]) -> list[dict]:
    shortage: dict[str, float] = {}
    for ln in lines:
        shortage[ln["Material_Code"]] = shortage.get(ln["Material_Code"], 0.0) \
            + ln["Shortfall_Qty"]
    out = []
    for mat in sorted(shortage):
        if shortage[mat] <= 0:
            continue
        meta = model["mat_meta"].get(mat, {})
        out.append({"Material_Code": mat,
                    "Material_Name": meta.get("Material_Name", ""),
                    "UOM": meta.get("UOM", ""),
                    "Available_Qty": model["pool_init"].get(mat, 0.0),
                    "Shortage_Qty_To_Buy": round_n(shortage[mat], 3)})
    out.sort(key=lambda r: (-r["Shortage_Qty_To_Buy"], r["Material_Code"]))
    return out


def build_totals(lines: list[dict]) -> list[dict]:
    totals: dict[str, dict] = {}
    for ln in lines:
        t = totals.setdefault(ln["Material_Code"], {
            "Material_Code": ln["Material_Code"],
            "Material_Name": ln["Material_Name"], "UOM": ln["UOM"],
            "Demand_Qty": 0.0, "Allocated_Qty": 0.0, "Shortfall_Qty": 0.0})
        t["Demand_Qty"] += ln["Demand_Qty"]
        t["Allocated_Qty"] += ln["Allocated_Qty"]
        t["Shortfall_Qty"] += ln["Shortfall_Qty"]
    return [{**t, "Demand_Qty": round_n(t["Demand_Qty"], 3),
             "Allocated_Qty": round_n(t["Allocated_Qty"], 3),
             "Shortfall_Qty": round_n(t["Shortfall_Qty"], 3)}
            for _, t in sorted(totals.items())]


def run_plan(model: dict, order: list[str]) -> dict:
    """One-shot plan: cascade + feasibility + totals + procurement."""
    order = _dedupe(order)
    lines = cascade_allocate(model, order)
    return {"order_used": order,
            "lines": lines,
            "feasibility": compute_feasibility(model, lines, order),
            "totals": build_totals(lines),
            "procurement": build_procurement_list(model, lines)}
