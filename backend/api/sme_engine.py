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
  * allocation     = one GLOBAL pool per material COMPONENT; tags consume it
                     strictly in priority order; codes within a tag in
                     numeric-first order; materials within a code in recipe
                     (id) order.
  * rounding       = quantities 4 dp, percentages 2 dp — matching the legacy
                     cascade. round_n() is half-up via floor(x·10ⁿ + 0.5) in
                     BOTH languages so ties can never diverge between runtimes
                     (Python's built-in round() is half-even; JS has no
                     built-in — this shared formula replaces both).
  * statuses       = the exact legacy label strings (✅ / 🟡 / 🔴).

2026-07-30 COMPONENT IDENTITY ruling (overturns the 2026-07-18 Material_Code
pooling rule, which read: "component lines sharing one Material_Code keep their
own demand rows but read the SAME pooled stock figure"):

  A material is identified by (Material_Code, SAP_Code), NOT by Material_Code
  alone. Multi-part chemical systems list one code as several distinct physical
  components — GI-8005765 is four rows, Comp-A/B/C/D, separated only by the
  variant SAP (1041 / 1041-1 / 1041-2 / 1041-3) — and each component is a
  different drum on a different shelf. Pooling them summed four unlike things
  into one bucket, so the earlier recipe lines drained stock that belonged to
  the later ones and the bottleneck ratio was meaningless.

  Each component therefore gets its OWN available pool, its OWN on-order pool,
  its OWN shortfall, and its OWN row in every report. `Material_Key` (below) is
  the grouping key everywhere a material used to be keyed by code.

  SAP codes are whitespace-normalized on both sides of the join, because the
  ERP carries entries like "1043 - 2" for the same component the recipe calls
  "1043-2" (same rule as _CALC_POOL_SQL in sme.py).

2026-08-05 THE SUBSET RULE (operator correction — the shape of the input data).

  In Materials_DetailsAvailable_Qty.xlsx, `Ordered_Qty` is the TOTAL quantity
  PROCURED for the project, and `Available_Qty` is the portion of that order
  which has physically ARRIVED. Available is a SUBSET of ordered, NOT a second
  bucket standing beside it. Verified on all 32 workbook rows: available <=
  ordered everywhere, and 14 of 32 are fully delivered (available == ordered).

      TIER 1  Alloc_Available / pool_init          = available_qty
      TIER 2  Alloc_Pending   / pool_pending_init  = max(ordered − available, 0)
                                                     "still on the water"
      ceiling Allocated_Qty                        = max(available, ordered)
      buy     Shortfall_Qty                        = demand − Allocated_Qty

  The ceiling and the buy list fall out of the cascade arithmetic for free once
  tier 2 is the PENDING quantity — nothing else needed changing.

2026-08-03 STRICT TIER SEGREGATION (locked). "Ready to build" is a claim about
metal, drums and shelves. Stock still on a purchase order is none of those.

  TIER 1 — PHYSICAL, `Alloc_Available` / `pool_init`. The ONLY input to
    readiness: Status, Completion_Pct, SQM_Achievable_Now, Coverage_Now_Pct,
    Fulfillment_Pct, SQM_Deficit and every "can we build it today" answer.
  TIER 2 — PENDING DELIVERY, `Alloc_Pending` / `pool_pending_init`. Feeds ONLY
    the forward-looking twins — Completion_With_Ordered_Pct,
    SQM_Achievable_With_Ordered, Coverage_With_Ordered_Pct,
    Fulfillment_With_Ordered_Pct, which measure coverage against the TOTAL
    PROCURED quantity — and the NET buy list (`Shortfall_Qty`), so stock
    already procured is not procured twice.

  `Allocated_Qty` (= tier 1 + tier 2) is a CONSERVATION field: it exists so
  `Demand = Allocated + Shortfall_Qty` holds. It is NOT a readiness quantity,
  and dividing it by `Demand_Qty` does NOT produce a coverage percentage
  anybody should colour green. Every tier-2 number a consumer might want is
  published as its own named field precisely so nobody re-derives one from it.
  Suites BB and BC pin this end to end.

Deliberate, documented deviations from legacy:
  * non-numeric system codes sort after numeric ones instead of crashing
    (legacy used int(code) and would raise ValueError), and among themselves
    they sort NATURALLY — LSC2 before LSC10, not lexically after it;
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


def sap_norm(v: Any) -> str:
    """Whitespace-stripped SAP code. The ERP writes the same variant as "1043-2"
    and "1043 - 2"; both must land in the same component pool."""
    return "".join(str(v).split()) if v is not None else ""


def mat_key(material_code: Any, sap_code: Any) -> str:
    """The component identity used as a pool / grouping / sort key.

    Sorting this string groups components under their material and orders them
    A→D by variant SAP ("M7|77" < "M7|77-1" < …), which is exactly the reading
    order the reports want. `|` never occurs in a Material_Code or SAP code.
    """
    return f"{_s(material_code)}|{sap_norm(sap_code)}"


def syscode_sort_key(code: str) -> tuple:
    """Natural ordering for lining-system codes. THE one implementation —
    `sme._syskey` and `sme_export_layouts._code_sort_key` delegate here.

    A pure-digit code keeps its historical leading slot and numeric order, so a
    numeric dataset sorts byte-identically to before this function grew a
    natural-sort tail.

    A code with an alpha prefix and a numeric tail ("LSC2", "LSC10") sorts on
    that tail. Plain string order puts LSC10 and LSC11 BETWEEN LSC1 and LSC2,
    and that is not cosmetic: `allocate()` walks `codes_by_tag` in this order
    and draws the pool down as it goes, so the order decides which system gets
    scarce material first. The 2026-08 workbooks renumbered every code from
    `1` to `LSC1`, which is what turned a numeric sort into a lexical one.

    MIRROR: `frontend/src/sme/engine.ts` → `syscodeSortKey` / `syscodeCompare`.
    Change both together or `npm run parity:sme` fails, by design.
    """
    s = _s(code)
    if s.isdigit():
        return (0, "", int(s), "")
    i = 0
    while i < len(s) and not s[i].isdigit():
        i += 1
    j = i
    while j < len(s) and s[j].isdigit():
        j += 1
    if i < j:                       # alpha prefix + numeric run (+ any tail)
        return (1, s[:i].upper(), int(s[i:j]), s[j:])
    return (2, s.upper(), 0, "")    # no digits at all


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
               "SAP_Code": sap_norm(r.get("SAP_Code")),
               "Material_Key": mat_key(r.get("Material_Code"), r.get("SAP_Code")),
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
    pool_pending_init: dict[str, float] = {}
    mat_meta: dict[str, dict] = {}
    for m in materials:
        # 2026-07-30 COMPONENT IDENTITY: one pool per (code, SAP), not per code.
        mat = mat_key(m.get("material_code"), m.get("sap_code"))
        available = _num(m.get("available_qty"))
        pool_init[mat] = available
        # 2026-08-05 SUBSET RULE (operator correction). In
        # Materials_DetailsAvailable_Qty.xlsx, `Ordered_Qty` is the TOTAL
        # quantity procured for the project and `Available_Qty` is the part of
        # it that has physically ARRIVED — so available is a SUBSET of ordered,
        # not a separate bucket beside it.
        #
        # Tier 2 is therefore the PENDING DELIVERY: what is still on the water.
        # Treating raw `ordered_qty` as tier 2 double-counted every unit that
        # had already landed. Measured on the live workbook: 22 of 30 report
        # rows had an understated buy list, 22,951 units in total, and
        # GI-8005763 (143,000 delivered of 143,000 ordered) read 286,000 and
        # reported NOTHING to buy against a demand of 152,685 — a 9,685-unit
        # shortage that was invisible.
        #
        # The rest of the contract falls out of the cascade for free:
        #   tier1 + tier2 = available + max(ordered − available, 0)
        #                 = max(available, ordered)      ← the total procured
        #   Shortfall_Qty = demand − Allocated_Qty
        #                 = max(demand − max(available, ordered), 0)
        #
        # Clamped at zero so a fully-delivered material (available == ordered)
        # has an EMPTY pipeline rather than a negative one.
        pending = _num(m.get("ordered_qty")) - available
        pool_pending_init[mat] = pending if pending > 0.0 else 0.0
        # ⚠️ PHASE 13g — `Consumed_Qty` IS AN OBSERVATION, AND IT SITS IN THE
        # METADATA ON PURPOSE (ruling Q13-5, Option B).
        #
        # It is HOD-approved physical draw of this component, reported BESIDE
        # the plan. It takes no part in any allocation: it is not spent, not
        # netted off `pool_init`, not compared, and it does not appear in a
        # single arithmetic expression below this line. `available_qty` is
        # still `Initial_Available_Qty`, full stop, and rule 1a survives
        # because the number comes from `sme_consumption_log` — an SME-owned
        # table — and only from rows a person signed for.
        #
        # ⚠️ IT IS PER COMPONENT, NOT PER LINE. The same component is drawn for
        # many units, so a report that SUMS this down a cascade multiplies it
        # by the number of units that use it. It rides on each line exactly as
        # `Material_Name` does — as a label, never as a quantity to add up.
        #
        # ⚠️ AND NOTHING MAY COLOUR IT AS COVERAGE (rule 1b). `Allocated_Qty`
        # is the scar: six presentation layers made it green because it looked
        # like progress, and that overstated buildable area by 9,118 m²,
        # 21.5 % of the programme. This field has the identical shape.
        mat_meta[mat] = {"Material_Code": _s(m.get("material_code")),
                         "SAP_Code": sap_norm(m.get("sap_code")),
                         "Material_Name": _s(m.get("material_name")),
                         "UOM": _s(m.get("uom")),
                         "Consumed_Qty": _num(m.get("consumed_qty"))}

    return {"units": units, "codes_by_tag": codes_by_tag,
            "recipes_by_code": recipes_by_code, "pool_init": pool_init,
            "pool_pending_init": pool_pending_init,
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
    """TWO-TIER cascade (2026-07-28 ruling Q1).

    Pass 1 spends PHYSICAL stock (`pool_init`) across every unit in priority
    order — byte-identical to the single-tier cascade this replaces, so
    `Alloc_Available`, `Shortfall_Available_Qty`, `Pool_*` and `Fulfillment_Pct`
    keep their exact historical values.

    Pass 2 then spends PENDING-DELIVERY stock (`pool_pending_init` =
    max(ordered − available, 0), the part of the purchase order that has not
    landed yet) against whatever is still short, walking `lines` in the order
    pass 1 produced them — which IS the priority order (ruling Q4: same order
    for both tiers).

    Field contract:
        Demand_Qty = Allocated_Qty + Shortfall_Qty          (conserved)
        Allocated_Qty = Alloc_Available + Alloc_Pending      (ruling Q6)
        Shortfall_Available_Qty = the PHYSICAL gap — what feasibility and
            "Ready to Build" judge on; a unit is not buildable because stock is
            on a truck somewhere.
        Shortfall_Qty = the NET gap — what still has to be purchased.
    With an empty pipeline — a fully-delivered material, available == ordered —
    the two shortfalls are equal and every field above collapses to the
    pre-ruling values.
    """
    pool = dict(model["pool_init"])
    pool_pending = dict(model.get("pool_pending_init") or {})
    lines: list[dict] = []
    # ── pass 1: physical stock ───────────────────────────────────────────────
    for tag in _dedupe(order):
        for code in model["codes_by_tag"].get(tag, []):
            unit = model["units"][(tag, code)]
            remaining = unit["remaining"]
            for r in model["recipes_by_code"].get(code, []):
                mat = r["Material_Key"]
                meta = model["mat_meta"].get(mat, {})
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
                    "Material_Code": r["Material_Code"],
                    "SAP_Code": r["SAP_Code"],
                    "Material_Key": mat,
                    # The STOCK master's name wins. For a multi-part system the
                    # recipe repeats one generic name on all four lines
                    # ("Cumicrete PU MF 300 - 3mm"), while the stock master
                    # names the actual component ("… (3MM) A"). Showing the
                    # generic name four times makes the rows unidentifiable in
                    # the UI, so the specific name leads and the recipe name is
                    # the fallback for a SAP with no stock row.
                    "Material_Name": meta.get("Material_Name") or r["Material_Name"],
                    "UOM": r["UOM"],
                    # ⚠️ OBSERVATION, NOT ALLOCATION. Carried from `mat_meta`
                    # unchanged; see `build_model`. It appears in no total, no
                    # ratio and no status below.
                    "Consumed_Qty": _num(meta.get("Consumed_Qty")),
                    "For_1_SQM": r["For_1_SQM"],
                    "Demand_Qty": d4,
                    "Alloc_Available": a4,
                    "Shortfall_Available_Qty": round_n(demand - alloc, 4),
                    "Pool_Before": round_n(before, 4),
                    "Pool_After": round_n(after, 4),
                    "Fulfillment_Pct": round_n(_clip(a4 / d4 * 100.0, 0.0, 100.0), 2)
                                       if d4 > 0 else 100.0,
                    "_demand": demand, "_avail": alloc,
                })
    # ── pass 2: pending-delivery stock, same priority walk ──────────────────
    for ln in lines:
        mat = ln["Material_Key"]
        gap = ln["_demand"] - ln["_avail"]
        before = pool_pending.get(mat, 0.0)
        alloc = min(gap, before) if gap > 0.0 else 0.0
        after = max(0.0, before - alloc)
        pool_pending[mat] = after
        total = ln["_avail"] + alloc
        d4 = ln["Demand_Qty"]
        t4 = round_n(total, 4)
        ln["Alloc_Pending"] = round_n(alloc, 4)
        ln["Allocated_Qty"] = t4
        ln["Shortfall_Qty"] = round_n(ln["_demand"] - total, 4)
        ln["Pending_Pool_Before"] = round_n(before, 4)
        ln["Pending_Pool_After"] = round_n(after, 4)
        ln["Fulfillment_With_Ordered_Pct"] = (
            round_n(_clip(t4 / d4 * 100.0, 0.0, 100.0), 2) if d4 > 0 else 100.0)
        del ln["_demand"], ln["_avail"]
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
        # "Ready to Build" is a PHYSICAL claim: stock that is still on order
        # cannot be applied to a tank today. Feasibility therefore judges on
        # tier 1 only, which also keeps every historical value intact.
        alloc_av = sum(r["Alloc_Available"] for r in rows)
        alloc_or = sum(r["Alloc_Pending"] for r in rows)
        short = sum(r["Shortfall_Available_Qty"] for r in rows)
        short_net = sum(r["Shortfall_Qty"] for r in rows)
        min_rate, bottleneck = 2.0, None
        min_rate_ord = 2.0
        for r in rows:
            rate = _clip(r["Alloc_Available"] / r["Demand_Qty"], 0.0, 1.0) \
                if r["Demand_Qty"] > 0 else 1.0
            if rate < min_rate:  # strict: first line at the minimum wins ties
                min_rate, bottleneck = rate, r
            # 2026-08-03 TIER SEGREGATION: the same bottleneck, computed on
            # tier 1 + tier 2. Published as its OWN field so no consumer ever
            # has to re-derive a forward-looking number from `Allocated_Qty`
            # and accidentally present it as readiness.
            rate_o = _clip(r["Allocated_Qty"] / r["Demand_Qty"], 0.0, 1.0) \
                if r["Demand_Qty"] > 0 else 1.0
            if rate_o < min_rate_ord:
                min_rate_ord = rate_o
        # 2026-07-07 STRICT BOTTLENECK ruling: coverage = the LEAST-available
        # material's rate, never the Σalloc/Σdemand average — the worst
        # component sets the ceiling for the whole system. (Was: alloc/demand.)
        completion = round_n(_clip(min_rate * 100.0, 0.0, 100.0), 2) \
            if demand > 0 else 100.0
        completion_ord = round_n(_clip(min_rate_ord * 100.0, 0.0, 100.0), 2) \
            if demand > 0 else 100.0
        if short <= 0:
            status = STATUS_FULL
        elif min_rate == 0.0:
            status = STATUS_BLOCKED
        else:
            status = f"{STATUS_PARTIAL} ({completion:.1f}%)"
        has_bn = bottleneck is not None and bottleneck["Shortfall_Available_Qty"] > 0
        out.append({
            "Priority_Rank": rank,
            "Equipment_Tag_No": tag,
            "Name": model["tag_meta"].get(tag, {}).get("Name", ""),
            "Total_Demand_Qty": round_n(demand, 4),
            "Total_Allocated_Qty": round_n(alloc, 4),
            "Total_Alloc_Available": round_n(alloc_av, 4),
            "Total_Alloc_Pending": round_n(alloc_or, 4),
            "Total_Shortfall_Qty": round_n(short, 4),
            "Total_Net_Shortfall_Qty": round_n(short_net, 4),
            "Completion_Pct": completion,
            # Forward-looking twin of Completion_Pct. NEVER drives Status.
            "Completion_With_Ordered_Pct": completion_ord,
            "Status": status,
            "Bottleneck_Material_Code": bottleneck["Material_Code"] if has_bn else "—",
            "Bottleneck_SAP_Code": bottleneck["SAP_Code"] if has_bn else "—",
            "Bottleneck_Material_Name": bottleneck["Material_Name"] if has_bn else "—",
            "Bottleneck_Shortfall": bottleneck["Shortfall_Available_Qty"] if has_bn else 0.0,
        })
    return out


# ─── Reverse SQM: bottleneck-limited achievable area (2026-07-28) ────────────
def _achievable(unit_lines: list[dict], field: str, remaining: float) -> float:
    """SQM this unit can actually be built to, limited by its scarcest material.

    Material m supports `alloc_m / For_1_SQM_m` square metres on its own; the
    unit can only reach the MINIMUM across its recipe — the bottleneck. This is
    the SQM restatement of the locked 2026-07-07 STRICT BOTTLENECK ruling, not
    a new model: min_m(alloc_m/rate_m) == min_m(rate_of_fulfilment) × remaining.

    Zero-rate recipe lines impose no ceiling and are excluded from the minimum.
    A unit with NO positive-rate line is UNMODELLED, and per ruling Q5 scores
    0 — never a silent 100%, which would read as "ready to build" for a system
    nobody has written a recipe for yet.
    """
    rates = [ln for ln in unit_lines if ln["For_1_SQM"] > 0]
    if not rates:
        return 0.0
    best = min(ln[field] / ln["For_1_SQM"] for ln in rates)
    return _clip(best, 0.0, remaining)


def build_sqm_rollup(model: dict, lines: list[dict], order: list[str]) -> list[dict]:
    """Per (tag, system code): remaining SQM vs what is actually achievable.

    Units are enumerated from `codes_by_tag`, NOT from `lines`, so a system
    code with no recipe rows still appears (with 0 achievable) instead of
    vanishing from the report.
    """
    by_unit: dict[tuple[str, str], list[dict]] = {}
    for ln in lines:
        by_unit.setdefault((ln["Equipment_Tag_No"], ln["Lining_System_Code"]),
                           []).append(ln)
    out: list[dict] = []
    for tag in _dedupe(order):
        for code in model["codes_by_tag"].get(tag, []):
            unit = model["units"].get((tag, code))
            if unit is None:
                continue
            remaining = unit["remaining"]
            ul = by_unit.get((tag, code), [])
            now = _achievable(ul, "Alloc_Available", remaining)
            with_ord = _achievable(ul, "Allocated_Qty", remaining)
            out.append({
                "Equipment_Tag_No": tag,
                "Name": model["tag_meta"].get(tag, {}).get("Name", ""),
                "Lining_System_Code": code,
                "System_Name": unit["short_name"],
                "Total_SQM": round_n(unit["total_original"], 2),
                "Done_SQM": round_n(unit["done"], 2),
                "Remaining_SQM": round_n(remaining, 2),
                "SQM_Achievable_Now": round_n(now, 2),
                "SQM_Achievable_With_Ordered": round_n(with_ord, 2),
                # Ruling Q3: the deficit is measured against PHYSICAL stock —
                # that is the number procurement has to close.
                "SQM_Deficit": round_n(remaining - now, 2),
                "Coverage_Now_Pct": round_n(_clip(now / remaining * 100.0, 0.0, 100.0), 2)
                                    if remaining > 0 else 100.0,
                # 2026-08-03 TIER SEGREGATION: the forward-looking twin, so the
                # UI never has to build a "with ordered" percentage itself.
                "Coverage_With_Ordered_Pct": round_n(
                    _clip(with_ord / remaining * 100.0, 0.0, 100.0), 2)
                    if remaining > 0 else 100.0,
                "Has_Recipe": bool(ul),
            })
    return out


def build_sqm_by_code(lines: list[dict], rollup: list[dict]) -> list[dict]:
    """System-code rollup for the Material-Wise Segregated Report.

    SQM is ADDITIVE across equipment, so a code's achievable area is the sum of
    its units' achievable areas — never the average of their coverage rates,
    which would ignore that each tag drew from the pool at a different point in
    the priority order.
    """
    agg: dict[str, dict] = {}
    for r in rollup:
        a = agg.setdefault(r["Lining_System_Code"], {
            "Lining_System_Code": r["Lining_System_Code"],
            "System_Name": r["System_Name"], "Equipment_Count": 0,
            "Total_SQM": 0.0, "Done_SQM": 0.0, "Remaining_SQM": 0.0,
            "SQM_Achievable_Now": 0.0, "SQM_Achievable_With_Ordered": 0.0,
            "SQM_Deficit": 0.0, "_tags": []})
        a["Equipment_Count"] += 1
        a["_tags"].append(r["Equipment_Tag_No"])
        for f in ("Total_SQM", "Done_SQM", "Remaining_SQM", "SQM_Achievable_Now",
                  "SQM_Achievable_With_Ordered", "SQM_Deficit"):
            a[f] += r[f]
    # blocking materials per code: what is still unpurchased after on-order
    block: dict[str, dict[str, dict]] = {}
    for ln in lines:
        code = ln["Lining_System_Code"]
        b = block.setdefault(code, {}).setdefault(ln["Material_Key"], {
            "Material_Code": ln["Material_Code"],
            "SAP_Code": ln["SAP_Code"],
            "Material_Name": ln["Material_Name"], "UOM": ln["UOM"],
            "Demand_Qty": 0.0, "Alloc_Available": 0.0, "Alloc_Pending": 0.0,
            "Shortfall_Available_Qty": 0.0, "Shortfall_Qty": 0.0})
        for f in ("Demand_Qty", "Alloc_Available", "Alloc_Pending",
                  "Shortfall_Available_Qty", "Shortfall_Qty"):
            b[f] += ln[f]
    out = []
    for code in sorted(agg, key=syscode_sort_key):
        a = agg[code]
        tags = a.pop("_tags")
        rem = a["Remaining_SQM"]
        mats = [{k: (round_n(v, 4) if isinstance(v, float) else v)
                 for k, v in m.items()}
                for m in block.get(code, {}).values()
                if m["Shortfall_Available_Qty"] > 0]
        mats.sort(key=lambda m: (-m["Shortfall_Qty"], m["Material_Code"],
                                 m["SAP_Code"]))
        out.append({**{k: round_n(v, 2) if isinstance(v, float) else v
                       for k, v in a.items()},
                    "Equipment_Tags": ", ".join(tags),
                    "Coverage_Now_Pct": round_n(
                        _clip(a["SQM_Achievable_Now"] / rem * 100.0, 0.0, 100.0), 2)
                        if rem > 0 else 100.0,
                    "Coverage_With_Ordered_Pct": round_n(
                        _clip(a["SQM_Achievable_With_Ordered"] / rem * 100.0,
                              0.0, 100.0), 2)
                        if rem > 0 else 100.0,
                    "Blocking_Materials": mats})
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
    """What still has to be BOUGHT. Keyed on the NET shortfall, so stock that
    is already on order no longer shows up as something to re-order — the
    single most expensive mistake this two-tier split prevents."""
    shortage: dict[str, float] = {}
    gross: dict[str, float] = {}
    ident: dict[str, dict] = {}
    for ln in lines:
        mat = ln["Material_Key"]
        shortage[mat] = shortage.get(mat, 0.0) + ln["Shortfall_Qty"]
        gross[mat] = gross.get(mat, 0.0) + ln["Shortfall_Available_Qty"]
        ident.setdefault(mat, ln)
    out = []
    for mat in sorted(shortage):
        if shortage[mat] <= 0:
            continue
        meta = model["mat_meta"].get(mat, {})
        ln = ident[mat]
        out.append({"Material_Code": ln["Material_Code"],
                    "SAP_Code": ln["SAP_Code"],
                    "Material_Name": meta.get("Material_Name") or ln["Material_Name"],
                    "UOM": meta.get("UOM") or ln["UOM"],
                    "Available_Qty": model["pool_init"].get(mat, 0.0),
                    # 2026-08-05 SUBSET RULE: `Pending_Delivery_Qty` is the
                    # UNRECEIVED part of the order; `Total_Procured_Qty` is the
                    # whole PO. The old `Ordered_Qty` column carried the raw
                    # order beside `Available_Qty`, which reads as "and this
                    # much more is coming" — the double-count in one column.
                    "Pending_Delivery_Qty":
                        (model.get("pool_pending_init") or {}).get(mat, 0.0),
                    "Total_Procured_Qty": (
                        model["pool_init"].get(mat, 0.0)
                        + (model.get("pool_pending_init") or {}).get(mat, 0.0)),
                    "Gross_Shortfall_Qty": round_n(gross.get(mat, 0.0), 3),
                    "Shortage_Qty_To_Buy": round_n(shortage[mat], 3)})
    out.sort(key=lambda r: (-r["Shortage_Qty_To_Buy"], r["Material_Code"],
                            r["SAP_Code"]))
    return out


def build_totals(lines: list[dict]) -> list[dict]:
    totals: dict[str, dict] = {}
    for ln in lines:
        t = totals.setdefault(ln["Material_Key"], {
            "Material_Code": ln["Material_Code"],
            "SAP_Code": ln["SAP_Code"],
            "Material_Name": ln["Material_Name"], "UOM": ln["UOM"],
            "Demand_Qty": 0.0, "Allocated_Qty": 0.0, "Alloc_Available": 0.0,
            "Alloc_Pending": 0.0, "Shortfall_Available_Qty": 0.0,
            "Shortfall_Qty": 0.0})
        for f in ("Demand_Qty", "Allocated_Qty", "Alloc_Available",
                  "Alloc_Pending", "Shortfall_Available_Qty", "Shortfall_Qty"):
            t[f] += ln[f]
    return [{**t, **{f: round_n(t[f], 3) for f in
                     ("Demand_Qty", "Allocated_Qty", "Alloc_Available",
                      "Alloc_Pending", "Shortfall_Available_Qty", "Shortfall_Qty")}}
            for _, t in sorted(totals.items())]


def run_plan(model: dict, order: list[str]) -> dict:
    """One-shot plan: cascade + feasibility + totals + procurement + SQM."""
    order = _dedupe(order)
    lines = cascade_allocate(model, order)
    rollup = build_sqm_rollup(model, lines, order)
    return {"order_used": order,
            "lines": lines,
            "feasibility": compute_feasibility(model, lines, order),
            "totals": build_totals(lines),
            "procurement": build_procurement_list(model, lines),
            "sqm_units": rollup,
            "sqm_by_code": build_sqm_by_code(lines, rollup)}
