"""
backend/api/services/planner.py — the overtime-minimising planner.

Answers one question: *to finish this equipment by the deadline, how many of
each role do I need, how many do I have, and what should I hire?*

THE MODEL, stated once so the arithmetic can be checked:

    shifts        = deadline_hours / SHIFT_WORKED_HOURS      (may be fractional)
    per person    = threshold x shifts   NORMAL hours
                  + (11 - threshold) x shifts   OVERTIME hours
                  = 11 x shifts = deadline_hours             (they reconcile)

`deadline_hours` is therefore HOURS AVAILABLE PER PERSON in the window, which
is the reading that makes the operator's own formula
(`headcount = manhours / deadline_hours`) come out right.

⚠️ MINIMISING OVERTIME IS A CAPACITY QUESTION, NOT A PREFERENCE. Overtime is
whatever will not fit inside the workforce's NORMAL capacity, so the way to
reduce it is to raise that capacity. A non-GI worker absorbs 10 normal hours
against a GI worker's 8 — 25% more — which is why the recommendation prefers
them. It is arithmetic, not a policy about who to employ.

⚠️ THIS FUNCTION MUTATES NOTHING. It reads progress, benchmarks and the roster
and returns advice. The operator's ruling: a suggestion, never a forced
assignment.

────────────────────────────────────────────────────────────────────────────
PHASE 8 (2026-08-20) — SELECTION, THEN SUMMATION. THEY ARE NOT THE SAME STEP.

The first version gathered every benchmark filed under a system code and
summed them. That is right for SEQUENTIAL sub-activities — finishing a system
means doing the primer AND the screed AND the buffing, so their hours add —
and catastrophically wrong for ALTERNATIVE benchmarks for ONE sub-activity,
which compete. The live workbook has both, and nothing in the old code told
them apart:

    LSC4 / ESC41   filed twice, once CV once ME, identical crew and
                   productivity                            → 2.00x too high
    LSC5 / ESC51   the same, 63 mm                          → 2.00x too high
    LSC10 / ESC101 one seal-coat code serving the 4 mm system (70 m²/shift)
                   and the 6 mm system (90 m²/shift)        → 2.29x too high
    surface prep   FOUR blasting variants plus the steel one, all summed:
                   3.6967 man-hours/m² charged where a concrete floor costs
                   0.1467                                   → 25x too high

So the pipeline is now three named steps rather than one:

    1. gather   every benchmark filed under this system code
    2. SELECT   within each Execution_Sub_Activity_Code group, choose which
                row(s) apply and in what proportion — shares summing to 1
    3. sum      across DISTINCT sub-activity groups only

Step 2 REPORTS what it chose and what it discarded (`benchmark_selection` in
the result). A planner that silently picks between two benchmarks differing 2x
is no better than one that adds them; the choice has to be visible.

Where selection cannot be resolved the plan WARNS and takes the most expensive
candidate. An overstated requirement gets argued about at the morning meeting;
an understated one is discovered when the deadline is missed.
"""
from __future__ import annotations

import math
import re
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..manhours import ot_thresholds
from .jobs import job_label, system_names
from .ledger import _MD

norm_t = _MD.tables["sme_manpower_norm"]
norm_role_t = _MD.tables["sme_manpower_norm_role"]
roles_t = _MD.tables["mh_roles"]
emp_t = _MD.tables["mh_employees"]
eq_t = _MD.tables["sme_equipment"]
progress_t = _MD.tables["sme_sqm_progress"]
prep_t = _MD.tables["sme_surface_prep_progress"]
recipe_t = _MD.tables["sme_recipe"]

# The worked half of the 12-hour shift (11 worked + 1 hour lunch).
SHIFT_WORKED_HOURS = 11.0

# Areas are compared for exact overlap; two m² figures within this are equal.
_AREA_EPS = 0.01

# Words that appear in a blasting benchmark's `Activity` but say nothing about
# WHICH surface it prepares. What is left after removing them is the part that
# has to match a lining system — 'Blasting Civil PU 6mm Area' keeps {pu, 6mm},
# and 'Blasting Civil Floor & Wall' keeps nothing at all, which is what makes
# it the default rather than a candidate. 'steel' is generic here because the
# steel/civil split is decided by the EQUIPMENT's Type, not by the word.
_BLAST_GENERIC = frozenset({
    "blasting", "blast", "civil", "steel", "surface", "surfaces", "area",
    "areas", "floor", "floors", "wall", "walls", "and", "the", "of", "on",
})

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> frozenset:
    return frozenset(_TOKEN_RE.findall(str(text or "").lower()))


def _specific_tokens(activity: str) -> frozenset:
    """What a blasting benchmark's name says about the surface it prepares."""
    return _tokens(activity) - _BLAST_GENERIC


def manhours_per_sqm(norm: dict) -> Optional[float]:
    """Man-hours needed per m² for one benchmark.

    ⚠️ DERIVED FROM THE EXACT FIGURES, not from the workbook's
    `SQ. Mtr/Hr./Person` column. That column is rounded to two decimals and the
    error is not academic: AR tile lining ships 0.13, while the exact
    99 man-hours / 13.33 m² per shift is 7.427 man-hours per m² against the
    rounded 7.692 — a 3.6% overstatement on every tile plan. The rounded column
    is used only when the exact pair is missing.
    """
    prod = float(norm.get("Standard_Productivity_Per_Shift") or 0)
    mh = float(norm.get("Manhours_Per_Shift") or 0)
    if prod > 0 and mh > 0:
        return mh / prod
    per_person = float(norm.get("SQM_Per_Hour_Per_Person") or 0)
    if per_person > 0:
        return 1.0 / per_person
    return None


def crew_shifts(norm: dict, sqm: float) -> Optional[float]:
    """Benchmark-crew shifts of work in `sqm` of this activity.

    Two forms, algebraically identical because
    `manhours_per_sqm = Manhours_Per_Shift / Standard_Productivity_Per_Shift`:

        sqm / Standard_Productivity_Per_Shift  ==  manhours / Manhours_Per_Shift

    Suite CE asserts they agree, which makes the pair a free self-check on
    every benchmark in the workbook.

    ⚠️ This is WORKLOAD, not elapsed time. It is how many shifts of the
    benchmark crew the job contains, independent of who you actually deploy.
    Elapsed shifts are `manhours / (deployed_headcount x SHIFT_WORKED_HOURS)`
    and coincide only when the crew deployed IS the benchmark crew.
    """
    prod = float(norm.get("Standard_Productivity_Per_Shift") or 0)
    return (sqm / prod) if prod > 0 else None


def shift_split(have: dict, *, site_day: int, site_night: int,
                shifts_per_day: int) -> tuple[float, float, str]:
    """(day_share, night_share, basis) for one role — ruling Q10.

    ⚠️ NEVER AN EVEN SPLIT WHERE THE ROSTER CAN SAY OTHERWISE. This operator
    runs a day shift of 20 against a night shift of 80; halving the requirement
    would understate the night crew fourfold. The roster's own proportion is the
    answer whenever it exists, and the `basis` names what was actually used so
    an assumption is never mistaken for a measurement.
    """
    if int(shifts_per_day) < 2:
        return 1.0, 0.0, "day_only"
    # A shift value that is neither Day nor Night (or a null, which roster()
    # counts as Day) must not vanish: derive the day count from the total so
    # the two shares always sum to 1.
    total = int(have.get("total", 0))
    night = int(have.get("Night", 0))
    day = max(total - night, 0)
    # ⚠️ `night > 0`, NOT `day + night > 0`. A role with 20 on days and none on
    # nights cannot describe a two-shift split: it is evidence that there is no
    # night crew YET, not that the night crew is zero per cent of the plan.
    # Reading it as a proportion would put 100% of a forced two-shift plan on
    # days and make the option do nothing at all.
    if night > 0:
        return day / (day + night), night / (day + night), "roster"
    if site_night > 0:
        # This role has no night presence but the site does. How the site
        # actually runs is a measurement of something real, unlike half.
        return (site_day / (site_day + site_night),
                site_night / (site_day + site_night), "site")
    # A two-shift plan forced onto an empty roster. There is nothing to derive
    # from, so the split is an assumption — and it is labelled as one.
    return 0.5, 0.5, "assumed_even"


async def _role_lookup(session: AsyncSession) -> dict:
    """Every spelling of a role → its canonical Role_Code.

    The roster types a free-text `Designation` while the benchmarks cite a
    `Role_Code`, so they have to be reconciled. Matching is case- and
    separator-insensitive on both the code and the printed name; anything that
    still does not match is REPORTED rather than silently counted as zero
    availability, because "no masons on site" and "nobody wrote down that they
    are masons" call for completely different actions.
    """
    out: dict[str, str] = {}
    for code, name in (await session.execute(
            select(roles_t.c["Role_Code"], roles_t.c["Name"]))).all():
        for spelling in (code, name):
            key = str(spelling or "").strip().lower().replace(" ", "_")
            if key:
                out[key] = str(code)
    return out


async def _tag_equipment(session: AsyncSession, *, site_id: str,
                         tag: str) -> list[dict]:
    """The equipment master rows for one tag — one per lining system on it.

    `Type` (CV/ME) lives HERE, per (tag, code), not on the system code: LSC1 is
    CV on nine concrete rows and ME on nineteen tank/vessel rows in the live
    master. That is precisely what disambiguates the CV/ME twin benchmarks, and
    it is why selection needs the equipment and not just the code.
    """
    cols = [eq_t.c["Lining_System_Code"], eq_t.c["Type"],
            eq_t.c["Surface_Area_SQM"], eq_t.c["Lining_System"],
            eq_t.c["Lining_System_Short_Name"], eq_t.c["Lining_Area_Location"],
            eq_t.c["Name"]]
    stmt = select(*cols).where(eq_t.c["Equipment_Tag_No"] == tag)
    if site_id:
        stmt = stmt.where(eq_t.c["Site_ID"] == site_id)
    return [dict(r) for r in (await session.execute(
        stmt.order_by(eq_t.c["Lining_System_Code"]))).mappings().all()]


async def _all_norms(session: AsyncSession) -> list[dict]:
    return [dict(r) for r in (await session.execute(
        select(norm_t).order_by(norm_t.c["Execution_Sub_Activity_Code"],
                                norm_t.c["Type"],
                                norm_t.c["Activity"],
                                norm_t.c["Variant_Key"]))).mappings().all()]


async def _lining_codes(session: AsyncSession) -> set:
    """System codes a recipe line names.

    ⚠️ "System-agnostic" is decided by the DATA, not by how the code is spelt.
    An earlier version read `NOT LIKE 'LSC%'` — a naming convention, and wrong:
    any lining system not spelt `LSC…` would silently have been planned as
    surface prep. A norm is system-agnostic when NO RECIPE LINE names its
    system, which is the same test `/execution/activities` uses for
    `manpower_only`. One definition, two callers.
    """
    # ⚠️ PHASE 15d: …and minus the SURFACE-PREP codes. The 2026-09-27 workbooks
    # put Garnet recipe lines under ESC1/ESC2 — the blasting codes — which by
    # the rule above would have planned blasting as a lining system.
    from . import prep as PR
    prep = await PR.prep_codes(session)
    return {str(c) for (c,) in (await session.execute(
        select(recipe_t.c["Lining_System_Code"]).distinct())).all()
        if c and str(c).strip() not in prep}


def _activity_to_codes(norms: list[dict], lining_codes: set) -> dict:
    """`Activity` text → the LINING system codes whose benchmarks carry it.

    This is what pairs LSC10's two seal-coat variants with the systems they
    seal, without a single code being named in this file:

        'PU lining 4mm' → {LSC8, LSC10}      'PU lining 6mm' → {LSC9, LSC10}

    Excluding the code being planned leaves {LSC8} and {LSC9}, whose areas on
    the tag give the split. If the operator adds a 'PU lining 8mm' system and
    names its seal-coat variant the same way, it works with no code change —
    which is the whole reason the pairing is derived rather than tabulated.
    """
    out: dict[str, set] = {}
    for n in norms:
        code = str(n["Lining_System_Code"])
        if code not in lining_codes:
            continue
        out.setdefault(str(n["Activity"] or "").strip(), set()).add(code)
    return out


def _select_variants(rows: list[dict], *, eq_type: str, self_code: str,
                     activity_to_codes: dict, area_by_code: dict,
                     report: list) -> list:
    """One sub-activity's benchmarks → [(norm, share)] with shares summing to 1.

    The rules, in the order they are tried. Each one is a statement about the
    data, and the one that fires is recorded in `report`.
    """
    if len(rows) == 1:
        return [(rows[0], 1.0)]

    label = f"{self_code}/{rows[0]['Execution_Sub_Activity_Code']}"

    # ── 1. the equipment's own Type ─────────────────────────────────────────
    # LSC4 and LSC5 are each filed twice, once for civil and once for
    # mechanical, with identical crews and identical productivity. Summing
    # them asks for two crews to do one job. The equipment master already
    # records which discipline this tag's system is.
    if eq_type:
        typed = [r for r in rows if str(r["Type"] or "").strip() == eq_type]
        if len(typed) == 1:
            report.append({
                "sub_activity": label, "rule": "equipment Type",
                "chosen": [_norm_ref(typed[0])],
                "rejected": [_norm_ref(r) for r in rows if r is not typed[0]],
                "why": (f"the equipment is {eq_type}, and exactly one benchmark "
                        f"is filed under {eq_type}"),
            })
            return [(typed[0], 1.0)]
        if typed:
            rows = typed

    # ── 2. pair each variant with the system it serves, and split by area ───
    # The variants differ by `Activity`; each Activity is also the Activity of
    # some OTHER lining system present on this tag; so the areas of those
    # systems say how much of this code's area belongs to each variant. For
    # LSC10 the split is not an approximation — its area equals LSC8 + LSC9
    # exactly on every tag in the master, which is the arithmetic proof that it
    # is one seal coat over both.
    paired: list = []
    for r in rows:
        act = str(r["Activity"] or "").strip()
        siblings = {c for c in activity_to_codes.get(act, set()) if c != self_code}
        weight = sum(float(area_by_code.get(c, 0.0)) for c in siblings)
        paired.append((r, siblings, weight))
    total_weight = sum(w for _, _, w in paired)
    if total_weight > 0 and all(s for _, s, _ in paired):
        out = [(r, w / total_weight) for r, _, w in paired if w > 0]
        report.append({
            "sub_activity": label, "rule": "paired system area",
            "chosen": [{**_norm_ref(r), "Share": round(sh, 4)} for r, sh in out],
            "rejected": [_norm_ref(r) for r, _, w in paired if w <= 0],
            "why": ("each variant names the system it serves, and those systems' "
                    "areas on this tag give the split: "
                    + ", ".join(f"{sorted(s)} {w:g} m²" for _, s, w in paired
                                if w > 0)),
        })
        return out

    # ── 3. one candidate left ───────────────────────────────────────────────
    if len(rows) == 1:
        report.append({
            "sub_activity": label, "rule": "only candidate",
            "chosen": [_norm_ref(rows[0])], "rejected": [],
            "why": "one benchmark survived the earlier filters",
        })
        return [(rows[0], 1.0)]

    # ── 4. unresolved: take the dearest, and say so ─────────────────────────
    # NEVER sum. Summing is the bug this whole function exists to remove; the
    # honest failure is to overstate ONE benchmark and put a warning on it.
    dearest = max(rows, key=lambda r: manhours_per_sqm(r) or 0.0)
    report.append({
        "sub_activity": label, "rule": "unresolved — took the dearest",
        "chosen": [_norm_ref(dearest)],
        "rejected": [_norm_ref(r) for r in rows if r is not dearest],
        "why": ("nothing in the data chooses between these benchmarks. The "
                "most expensive was taken so the plan overstates rather than "
                "understates — give the rows distinct Types or a Variant_Key, "
                "or name the system each serves in its Activity"),
        "needs_operator": True,
    })
    return [(dearest, 1.0)]


def _norm_ref(n: dict) -> dict:
    return {"id": n.get("id"), "Type": n.get("Type"),
            "Activity": n.get("Activity"),
            "Execution_Sub_Activity_Code": n.get("Execution_Sub_Activity_Code"),
            "Variant_Key": n.get("Variant_Key") or "",
            "Standard_Productivity_Per_Shift": n.get(
                "Standard_Productivity_Per_Shift"),
            "Manhours_Per_SQM": round(manhours_per_sqm(n) or 0.0, 4)}


def _topcoat_codes(eq_rows: list[dict], activity_by_code: dict,
                   activity_to_codes: dict, report: list) -> set:
    """Codes on this tag whose area is already counted by the codes they cover.

    A surface is blasted ONCE, before the first coat goes on. LSC10 is the
    1 mm seal over the 3 mm and 5 mm PU screeds, and its area on every tag in
    the master is EXACTLY the sum of theirs:

        J027   LSC8 982 + LSC9 2,565 = LSC10 3,547     (5 of 5 tags, exact)

    Counting all three blasts the same concrete three times. So a code is
    treated as a topcoat when BOTH hold: its benchmarks name the systems it
    covers (the same pairing rule as `_select_variants`), AND its area equals
    their sum. Two independent facts have to agree, and when they do not the
    exclusion is refused and reported rather than assumed — an area that does
    not add up is a data question, not a licence to drop a surface.
    """
    area_by_code = {str(r["Lining_System_Code"]): float(r["Surface_Area_SQM"] or 0)
                    for r in eq_rows}
    out = set()
    for code, area in area_by_code.items():
        covered = set()
        for act in activity_by_code.get(code, set()):
            covered |= {c for c in activity_to_codes.get(act, set())
                        if c != code and c in area_by_code}
        if not covered:
            continue
        total = sum(area_by_code[c] for c in covered)
        # ⚠️ COVERING IS DIRECTIONAL; the Activity pairing is not. LSC10 shares
        # 'PU lining 4mm' with LSC8, so the pairing sees LSC8 → {LSC10} just as
        # readily as LSC10 → {LSC8, LSC9}. Only one of those is a topcoat, and
        # the area says which: a coat OVER several surfaces is at least as big
        # as the biggest of them. A code smaller than what it supposedly covers
        # is the mirror image of a real pair, not a data problem, so it is
        # dropped silently rather than reported as an unresolved overlap.
        if area + _AREA_EPS < max(area_by_code[c] for c in covered):
            continue
        if abs(total - area) <= _AREA_EPS:
            out.add(code)
            report.append({
                "code": code, "excluded": True, "area": round(area, 2),
                "covers": sorted(covered),
                "why": (f"{code} covers {sorted(covered)} and its {area:g} m² "
                        f"is exactly their {total:g} m² — the same surface, "
                        f"blasted once before the first coat"),
            })
        else:
            report.append({
                "code": code, "excluded": False, "area": round(area, 2),
                "covers": sorted(covered),
                "why": (f"{code} names {sorted(covered)} but its {area:g} m² is "
                        f"not their {total:g} m² — kept, because the overlap "
                        f"cannot be proved from the areas"),
                "needs_operator": True,
            })
    return out


def _surface_key(row: dict) -> tuple:
    """The physical surface an equipment row describes.

    Location text is normalised (case, punctuation spacing, repeated spaces)
    so 'Floor, Wall' and 'floor,  wall' are one surface rather than two; the
    area rides along because two systems on the same NAMED place but different
    areas are not demonstrably the same piece of steel.
    """
    loc = " ".join(str(row.get("Lining_Area_Location") or "").lower().split())
    loc = re.sub(r"\s*,\s*", ",", loc)
    return (loc, round(float(row.get("Surface_Area_SQM") or 0), 2))


def _dedupe_surfaces(eq_rows: list[dict], report: list) -> list[dict]:
    """One row per PHYSICAL surface — the operator's ruling of 2026-08-21.

    J027 files LSC1 and LSC2 at 504 m² each against an identical
    `Lining_Area_Location`. That is ONE 504 m² surface carrying two stacked
    systems, and it is blasted ONCE: the substrate is prepared before the first
    coat, and the second system goes on top of the first, not onto fresh
    concrete. Charging both rows billed the same blasting twice.

    ⚠️ THE TEST IS EXACT MATCH ON BOTH LOCATION AND AREA, and deliberately so.
    Partial overlaps exist in the master — LSC6 covers 'Pedastal Wall Side
    surface, Wall' while LSC1 covers that AND 'Floor' — and no arithmetic in
    this file can say how much of one lies inside the other. Merging on a
    partial match would silently drop real area. Exact pairs are provable;
    everything else is left alone and counted in full.

    Which benchmark survives a merge is decided the same way every other
    unresolved choice in this module is: the DEAREST. Two stacked systems can
    route to different blasting variants, nothing says which coat went on
    first, and overstating one surface is recoverable where understating it is
    not.
    """
    groups: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    for row in eq_rows:
        k = _surface_key(row)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(row)

    out: list[dict] = []
    for k in order:
        rows = groups[k]
        loc, area = k
        if len(rows) == 1 or not loc or area <= 0:
            # No location text means nothing to match on — such rows are always
            # kept whole rather than collapsed by area alone, which would merge
            # two genuinely separate 40 m² surfaces.
            out.extend(rows)
            continue
        keep = dict(rows[0])
        codes = sorted(str(r["Lining_System_Code"]) for r in rows)
        keep["_merged_codes"] = codes
        out.append(keep)
        report.append({
            "Lining_Area_Location": str(rows[0].get("Lining_Area_Location") or ""),
            "Surface_Area_SQM": area, "codes": codes,
            "counted_once": area,
            "would_have_been": round(area * len(rows), 2),
            "saved": round(area * (len(rows) - 1), 2),
            "why": (f"{' + '.join(codes)} are stacked on the same {area:g} m² — "
                    f"one surface, prepared once before the first coat"),
        })
    return out


def _prep_partition(eq_rows: list[dict], prep_norms: list[dict],
                    *, activity_by_code: dict, topcoats: set,
                    report: list) -> list:
    """Split the tag's surface across the blasting benchmarks that prepare it.

    Per equipment row, in this order:

      · the row is a topcoat over surfaces already counted  → contributes none
      · the equipment is ME (a steel tank or vessel)        → the ME benchmark
      · the row's lining Activity contains everything a CV variant's name
        specifies ('PU lining 4mm' ⊇ {pu, 4mm})             → that variant
      · a CV variant whose name specifies nothing           → the default
      · exactly one CV candidate exists                     → it
      · otherwise                                           → the dearest + warn

    ⚠️ TYPE IS READ, NEVER GUESSED — except that a blank Type is treated as
    civil. Every row in the live master is CV or ME; blank appears only in
    fixtures, and civil is both the majority and the branch that owns the
    catch-all benchmark, so it is the safe reading of an absent value.
    """
    by_type: dict[str, list] = {}
    for n in prep_norms:
        by_type.setdefault(str(n["Type"] or "").strip().upper(), []).append(n)

    out: list = []
    for row in eq_rows:
        # A merged row stands for several stacked systems on ONE surface. Every
        # one of them is a candidate for choosing the benchmark; the area is
        # counted once. See _dedupe_surfaces.
        merged = row.get("_merged_codes") or [str(row["Lining_System_Code"])]
        area = float(row["Surface_Area_SQM"] or 0)
        live = [c for c in merged if c not in topcoats]
        if area <= 0 or not live:
            continue
        eq_type = str(row["Type"] or "").strip().upper() or "CV"
        candidates = by_type.get(eq_type) or []
        if not candidates and eq_type != "CV":
            candidates = by_type.get("CV") or []
        if not candidates:
            candidates = list(prep_norms)
        picks = []
        for c in live:
            chosen, why = _pick_prep_variant(
                candidates, activities=activity_by_code.get(c, set()))
            if chosen is not None:
                picks.append((chosen, why, c))
        if not picks:
            continue
        # Stacked systems can route to different variants and nothing says which
        # coat went on first, so the dearest wins — the same direction every
        # other unresolved choice in this module takes.
        chosen, why, src = max(picks,
                               key=lambda p: manhours_per_sqm(p[0]) or 0.0)
        label = "+".join(live)
        if len(picks) > 1:
            why = (f"{why} (chosen for {src}; {label} share this surface and "
                   f"the dearest of their benchmarks was taken)")
        out.append((chosen, area, label))
        report.append({"code": label, "codes": live, "area": round(area, 2),
                       "eq_type": eq_type, "merged": len(live) > 1,
                       "benchmark": _norm_ref(chosen), "why": why})
    return out


def _pick_prep_variant(candidates: list[dict], *, activities: set):
    """Which blasting benchmark prepares a surface that will receive
    `activities`. Returns (norm | None, why)."""
    if len(candidates) == 1:
        return candidates[0], "the only benchmark for this discipline"

    lining_tokens = frozenset().union(*(_tokens(a) for a in activities)) \
        if activities else frozenset()
    specific = [(n, _specific_tokens(str(n["Activity"] or ""))) for n in candidates]

    named = [(n, t) for n, t in specific if t and t <= lining_tokens]
    if len(named) == 1:
        return named[0][0], (f"the benchmark names {sorted(named[0][1])}, which "
                             f"this system's own activity also names")
    if named:
        best = max(named, key=lambda p: len(p[1]))
        return best[0], (f"most specific match on {sorted(best[1])} among "
                         f"{len(named)} candidates")

    generic = [n for n, t in specific if not t]
    if len(generic) == 1:
        return generic[0], ("the catch-all benchmark — its name specifies no "
                            "particular surface")
    if not generic and len(candidates) == 1:
        return candidates[0], "the only candidate"
    if generic:
        dearest = max(generic, key=lambda n: manhours_per_sqm(n) or 0.0)
        return dearest, ("several catch-all benchmarks; took the dearest so the "
                         "plan overstates rather than understates")
    dearest = max(candidates, key=lambda n: manhours_per_sqm(n) or 0.0)
    return dearest, ("nothing in the data chooses between these benchmarks; "
                     "took the dearest so the plan overstates rather than "
                     "understates")


def _overlap_diagnostic(eq_rows: list[dict], topcoats: set,
                        merges: list) -> dict:
    """Gross versus deduplicated prep area, and what was merged to get there.

    ⚠️ THE DEDUPLICATED FIGURE IS NOW THE ONE THE PLAN USES (operator ruling,
    2026-08-21: a surface carrying two stacked systems is blasted ONCE). The
    gross is still published beside it so a reader can see what changed and
    reconcile against a plan printed before the ruling.
    """
    gross = sum(float(r["Surface_Area_SQM"] or 0) for r in eq_rows
                if str(r["Lining_System_Code"]) not in topcoats)
    saved = sum(m["saved"] for m in merges)
    return {"gross_sqm": round(gross, 2),
            "deduplicated_sqm": round(gross - saved, 2),
            "double_counted_sqm": round(saved, 2),
            "shared_surfaces": sorted(merges, key=lambda m: -m["saved"])}


async def _remaining_sqm(session: AsyncSession, *, site_id: str, tag: str,
                         code: str, eq_rows: list[dict], topcoats: set,
                         merges: list, prep_area: float) -> dict:
    """How much area is still to do, and where the figure came from."""
    if code:
        row = (await session.execute(select(progress_t).where(
            progress_t.c["Site_ID"] == site_id,
            progress_t.c["Equipment_Tag_No"] == tag,
            progress_t.c["Lining_System_Code"] == code))).mappings().first()
        if row is None:
            return {"remaining_sqm": 0.0, "original_sqm": 0.0, "done_sqm": 0.0,
                    "source": "sme_sqm_progress",
                    "note": f"no progress row for {tag} / {code} at this site"}
        orig = float(row["Original_SQM"] or 0)
        done = float(row["Done_SQM"] or 0)
        return {"remaining_sqm": round(max(orig - done, 0.0), 2),
                "original_sqm": round(orig, 2), "done_sqm": round(done, 2),
                "source": "sme_sqm_progress", "note": ""}

    # Surface prep has no planned area of its own — the area to prepare is the
    # equipment's, which sme_equipment already states. See
    # models.SmeSurfacePrepProgress for why there is no Original_SQM twin.
    #
    # `prep_area` is the DEDUPLICATED total the partition actually charges, so
    # the workload figure and the activity rows can never disagree: they are
    # the same number, produced once.
    overlap = _overlap_diagnostic(eq_rows, topcoats, merges)
    area = float(prep_area)
    done = float((await session.execute(
        select(func.coalesce(func.sum(prep_t.c["Done_SQM"]), 0.0))
        .where(prep_t.c["Site_ID"] == site_id,
               prep_t.c["Equipment_Tag_No"] == tag))).scalar() or 0)
    return {"remaining_sqm": round(max(area - done, 0.0), 2),
            "original_sqm": round(area, 2), "done_sqm": round(done, 2),
            "source": "sme_equipment - sme_surface_prep_progress",
            "overlap": overlap,
            "note": "" if area else f"no equipment area recorded for {tag}"}


async def roster(session: AsyncSession, *, site_id: str) -> tuple[dict, dict]:
    """Active workers per role, split by contract and by shift.

    Returns `(available, unmapped)`. `unmapped` holds the designations that
    match no role — reported rather than counted as zero availability, because
    "nobody wrote down that they are masons" and "there are no masons" call for
    completely different actions.

    Lifted out of `plan_many` in slice 8e so the session plan reads the SAME
    roster by the SAME rules. A second copy of this loop would have drifted the
    first time a designation spelling was added to one of them.
    """
    lookup = await _role_lookup(session)
    emp_rows = (await session.execute(
        select(emp_t.c["Designation"], emp_t.c["Worker_Type"], emp_t.c["Shift"],
               func.count())
        .where(emp_t.c["Site_ID"] == site_id, emp_t.c["status"] == "active")
        .group_by(emp_t.c["Designation"], emp_t.c["Worker_Type"],
                  emp_t.c["Shift"]))).all()

    available: dict[str, dict] = {}
    unmapped: dict[str, int] = {}
    for desig, wtype, shift, cnt in emp_rows:
        key = str(desig or "").strip().lower().replace(" ", "_")
        rc = lookup.get(key)
        if rc is None:
            label = str(desig or "").strip() or "(no designation recorded)"
            unmapped[label] = unmapped.get(label, 0) + int(cnt)
            continue
        slot = available.setdefault(rc, {"GI": 0, "NON_GI": 0, "Day": 0,
                                         "Night": 0, "total": 0})
        wt = str(wtype or "GI")
        slot[wt] = slot.get(wt, 0) + int(cnt)
        slot[str(shift or "Day")] = slot.get(str(shift or "Day"), 0) + int(cnt)
        slot["total"] += int(cnt)
    return available, unmapped


def unmapped_warning(unmapped: dict) -> str:
    return ("roster designations that match no role: "
            + ", ".join(f"{k} x{v}" for k, v in sorted(unmapped.items()))
            + " — these workers are NOT counted as available. \'nobody wrote "
              "down that they are masons\' is a different problem from \'there "
              "are no masons\', so they are reported rather than assumed")


async def _plan_one(session: AsyncSession, *, site_id: str, tag: str, code: str,
                    all_norms: list[dict], lining_codes: set,
                    activity_to_codes: dict) -> dict:
    """One job — the SELECT half. Returns work items and everything that
    explains them; no roster, no deadline, no overtime.

    Split out of `plan()` in slice 8b so several jobs can be planned against
    ONE roster and ONE deadline. Nothing in here depends on the deadline, which
    is what makes re-costing a different Target Days cheap.
    """
    warnings: list[str] = []
    selection: list = []
    prep_report: list = []
    topcoat_report: list = []
    merge_report: list = []

    eq_rows_raw = await _tag_equipment(session, site_id=site_id, tag=tag)
    area_by_code = {str(r["Lining_System_Code"]): float(r["Surface_Area_SQM"] or 0)
                    for r in eq_rows_raw}
    activity_by_code: dict[str, set] = {}
    for n in all_norms:
        activity_by_code.setdefault(str(n["Lining_System_Code"]), set()).add(
            str(n["Activity"] or "").strip())

    topcoats = _topcoat_codes(eq_rows_raw, activity_by_code, activity_to_codes,
                              topcoat_report)
    # ONE ROW PER PHYSICAL SURFACE (operator ruling 2026-08-21). Stacked systems
    # on the same location are blasted once. Lining work is unaffected — a
    # second system on the same concrete is a second lining job.
    eq_rows = _dedupe_surfaces(
        [r for r in eq_rows_raw if str(r["Lining_System_Code"]) not in topcoats],
        merge_report)

    items: list = []
    prep_area = 0.0
    # The discipline this job belongs to. For a lining job it is the (tag, code)
    # row's own Type; for surface prep it is the SET across the tag, printed as
    # 'CV/ME' where a tag carries both — never whichever row was read first.
    if code:
        eq_type = ""
        for r in eq_rows_raw:
            if str(r["Lining_System_Code"]) == code:
                eq_type = str(r["Type"] or "").strip().upper()
                break
    else:
        eq_type = "/".join(sorted({str(r["Type"] or "").strip().upper()
                                   for r in eq_rows_raw
                                   if str(r["Type"] or "").strip()}))
    if code:
        if not eq_type:
            # Without an equipment row there is no Type, so the CV/ME twins
            # cannot be told apart and selection falls through to its
            # last-resort rule. Say that here rather than let the reader meet
            # only the generic "nothing chooses between these benchmarks",
            # which points at the workbook when the gap is in the master.
            warnings.append(
                f"the equipment master has no {code} row for {tag} at this "
                f"site, so the plan cannot read its CV/ME type — any benchmark "
                f"that exists once for civil and once for mechanical will fall "
                f"back to the dearest")
        groups: dict[str, list] = {}
        for n in all_norms:
            if str(n["Lining_System_Code"]) == code:
                groups.setdefault(str(n["Execution_Sub_Activity_Code"]), []).append(n)
        if not groups:
            warnings.append(
                f"no manpower benchmark exists for {code} — the requirement "
                f"cannot be computed, and a zero here means 'unknown', not "
                f"'none'")
        chosen: list = []
        for esc in sorted(groups):
            for norm, share in _select_variants(
                    groups[esc], eq_type=eq_type, self_code=code,
                    activity_to_codes=activity_to_codes,
                    area_by_code=area_by_code, report=selection):
                chosen.append((norm, share))
        mismatched = {str(n["Type"] or "").strip().upper()
                      for n, _ in chosen} - {eq_type, ""}
        if eq_type and mismatched:
            warnings.append(
                f"{tag}/{code} is {eq_type} in the equipment master but the "
                f"only benchmarks for it are {sorted(mismatched)} — they are "
                f"being used, which may be right, but nobody has said so")
    else:
        prep_norms = [n for n in all_norms
                      if str(n["Lining_System_Code"]) not in lining_codes]
        if not prep_norms:
            warnings.append(
                "no system-agnostic manpower benchmark exists — surface prep "
                "cannot be costed, and a zero here means 'unknown', not 'none'")
        partition = _prep_partition(eq_rows, prep_norms,
                                    activity_by_code=activity_by_code,
                                    topcoats=topcoats, report=prep_report)
        # Several systems on one tag can route to the SAME blasting benchmark —
        # J027 sends LSC1, LSC2, LSC6 and LSC7 all to Floor & Wall. They are one
        # line of work, not four, so they are folded into a single activity row
        # carrying the codes that fed it. Four identical rows in a report is how
        # a reader concludes the planner is double-counting when it is not.
        folded: dict = {}
        for norm, area, src in partition:
            slot = folded.setdefault(int(norm["id"]), [norm, 0.0, []])
            slot[1] += area
            slot[2].extend(src.split("+"))
        prep_area = sum(a for _, a, _ in folded.values())
        chosen = []
        for norm, area, srcs in folded.values():
            share = (area / prep_area) if prep_area > 0 else 0.0
            chosen.append((norm, share, "+".join(sorted(set(srcs)))))

    workload = await _remaining_sqm(session, site_id=site_id, tag=tag, code=code,
                                    eq_rows=eq_rows_raw, topcoats=topcoats,
                                    merges=merge_report, prep_area=prep_area)
    remaining = workload["remaining_sqm"]
    if workload["note"]:
        warnings.append(workload["note"])

    for entry in chosen:
        if len(entry) == 2:
            norm, share = entry
            src = code
        else:
            norm, share, src = entry
        # Remaining area is scaled by each benchmark's share. For prep, progress
        # is recorded per tag rather than per surface, so nothing says WHICH
        # part was prepped first; proportional is the only reading the data
        # supports.
        items.append((norm, remaining * share, share, src))

    if not code:
        overlap = workload.get("overlap") or {}
        if overlap.get("double_counted_sqm", 0) > 0:
            warnings.append(
                f"{overlap['double_counted_sqm']:g} m² of stacked surface on "
                f"{tag} is counted ONCE, not once per system — "
                + "; ".join(f"{'+'.join(d['codes'])} share "
                            f"{d['Surface_Area_SQM']:g} m² at "
                            f"{d['Lining_Area_Location'][:48]}"
                            for d in overlap["shared_surfaces"][:3])
                + f". Prep area is {overlap['deduplicated_sqm']:g} m², not the "
                  f"{overlap['gross_sqm']:g} m² the rows add up to.")

    for entry in topcoat_report:
        if entry.get("needs_operator"):
            warnings.append(entry["why"])
    for entry in selection:
        if entry.get("needs_operator"):
            warnings.append(f"{entry['sub_activity']}: {entry['why']}")

    return {"equipment_tag": tag, "lining_system_code": code,
            "system_agnostic": not code, "eq_type": eq_type,
            "workload": workload, "items": items, "warnings": warnings,
            "benchmark_selection": {
                "rules_applied": selection,
                "surface_prep_partition": prep_report,
                "topcoats": topcoat_report,
                "merged_surfaces": merge_report,
            }}


async def resolve_jobs(session: AsyncSession, *, site_id: str,
                       equipment_tags: list[str], lining_system_codes: list[str],
                       include_surface_prep: bool) -> tuple[list[tuple], list[str]]:
    """A tag selection x a code selection → the jobs that actually exist.

    ⚠️ NOT THE CROSS PRODUCT. Picking tags {A, B} and codes {X, Y} is not four
    jobs; it is the INTERSECTION WITH REALITY — the (tag, code) pairs that
    `sme_sqm_progress` actually holds. Building the product would invent work
    on pairs nobody ever planned, and it would look entirely plausible in the
    output.

    ⚠️ SURFACE PREP IS PER TAG, NOT PER (TAG, CODE). A tag carrying six lining
    systems has ONE surface to prepare, so prep is added once per selected tag.
    Adding it per pair would bill six blastings for one vessel.
    """
    warnings: list[str] = []
    tags = [t for t in (s.strip() for s in equipment_tags) if t]
    codes = [c for c in (s.strip() for s in lining_system_codes) if c]
    if not tags:
        return [], ["no equipment selected"]

    stmt = select(progress_t.c["Equipment_Tag_No"],
                  progress_t.c["Lining_System_Code"])
    if site_id:
        stmt = stmt.where(progress_t.c["Site_ID"] == site_id)
    real = {(str(t), str(c)) for t, c in (await session.execute(stmt)).all()}

    jobs: list[tuple] = []
    if codes:
        for tag in tags:
            for code in codes:
                if (tag, code) in real:
                    jobs.append((tag, code))
    else:
        # ⚠️ NO CODE SELECTED MEANS EVERY CODE ON THESE TAGS, not none. The
        # filter's placeholder reads "All systems on the selected equipment"
        # and an empty list has to mean what the label says — the alternative
        # is a dead end where picking equipment and pressing Plan returns
        # "nothing to plan", which is a promise the UI made and the API broke.
        for tag in tags:
            for t, c in sorted(real):
                if t == tag:
                    jobs.append((tag, c))
    if codes:
        missing = [f"{t}/{c}" for t in tags for c in codes
                   if (t, c) not in real]
        if missing:
            warnings.append(
                f"{len(missing)} selected combination(s) do not exist in the "
                f"progress master and were dropped rather than invented: "
                + ", ".join(missing[:8])
                + (f" (+{len(missing) - 8} more)" if len(missing) > 8 else ""))
    if include_surface_prep:
        for tag in tags:
            jobs.append((tag, ""))
    if not jobs:
        warnings.append(
            "nothing to plan — none of the selected equipment carries any of "
            "the selected systems, and surface prep was not included")
    return jobs, warnings


async def plan(session: AsyncSession, *, site_id: str, equipment_tag: str,
               lining_system_code: str = "", deadline_hours: float = 11.0,
               ) -> dict:
    """One job, one deadline. Thin wrapper over `plan_many`."""
    tag = (equipment_tag or "").strip()
    if not tag:
        raise HTTPException(422, "equipment_tag is required")
    return await plan_many(session, site_id=site_id,
                           jobs=[(tag, (lining_system_code or "").strip())],
                           deadline_hours=deadline_hours)


async def plan_many(session: AsyncSession, *, site_id: str, jobs: list,
                    deadline_hours: Optional[float] = None,
                    target_days: Optional[float] = None,
                    shifts_per_day: Optional[int] = None) -> dict:
    """Many jobs, one roster, one deadline: workload → gap → overtime strategy.

    ── THE DEADLINE ────────────────────────────────────────────────────────
    `target_days` and `deadline_hours` are two spellings of one quantity and
    exactly one may be given:

        deadline_hours = target_days x SHIFT_WORKED_HOURS

    A person works ONE shift a day, so over D days each offers D x 11 hours no
    matter how many shifts the site runs. That substitution makes every figure
    the planner already computed read "per day" without a single formula
    changing.

    ── SHIFTS PER DAY (operator ruling Q10, 2026-08-25: NIGHTS BUY TIME) ───
    Two shifts a day still means two DISJOINT crews — nobody works both — so
    for a FIXED deadline the total headcount is unchanged:

        Total_Required_Headcount = manhours / (days x 11)     (independent of s)

    What running nights actually buys is CALENDAR TIME. The site delivers
    `(day_crew + night_crew) x 11` man-hours per calendar day instead of
    `day_crew x 11`, so the same work finishes sooner:

        Days_Day_Only   = manhours / (day_in_scope x 11)
        Days_Both       = manhours / (roster_in_scope x 11)
        Days_Saved      = Days_Day_Only - Days_Both

    ⚠️ THE PER-SHIFT SPLIT IS READ FROM THE ROSTER, NEVER FROM `/ shifts_per_day`.
    Until Phase 9b this divided the requirement evenly, which is wrong wherever
    the shifts are not the same size — and at this operator they routinely are
    not: a day shift of 20 against a night shift of 80, on different equipment
    and different tasks. An even split understates the night requirement by a
    factor of four and overstates the day one by the same. The shares come from
    the roster's OWN day/night proportion, per role, and `Shift_Split_Basis`
    says which basis was available:

        roster        this role's own day/night counts       — the good case
        site          the site's proportion, this role having no roster
        assumed_even  a two-shift plan forced with no roster to derive from
        day_only      one shift a day; the night share is zero

    Only the last two are ever an assumption, and both are named in the output
    rather than blended silently into a number.
    """
    if deadline_hours is not None and target_days is not None:
        raise HTTPException(422, "give either target_days or deadline_hours, "
                                 "not both — they are the same quantity")
    if target_days is not None:
        if float(target_days) <= 0:
            raise HTTPException(422, "target_days must be greater than zero")
        deadline_hours = float(target_days) * SHIFT_WORKED_HOURS
    if deadline_hours is None:
        deadline_hours = SHIFT_WORKED_HOURS
    if float(deadline_hours) <= 0:
        raise HTTPException(422, "deadline_hours must be greater than zero")
    deadline_hours = float(deadline_hours)
    shifts = deadline_hours / SHIFT_WORKED_HOURS
    days = shifts   # one shift per person per day — see the docstring

    if shifts_per_day is not None and int(shifts_per_day) not in (1, 2):
        raise HTTPException(422, "shifts_per_day must be 1 (day only) or 2 "
                                 "(day and night)")

    warnings: list[str] = []
    all_norms = await _all_norms(session)
    lining_codes = await _lining_codes(session)
    activity_to_codes = _activity_to_codes(all_norms, lining_codes)
    names = await system_names(session)

    planned: list[dict] = []
    for tag, code in jobs:
        planned.append(await _plan_one(
            session, site_id=site_id, tag=tag, code=code, all_norms=all_norms,
            lining_codes=lining_codes, activity_to_codes=activity_to_codes))

    items: list = []
    for p in planned:
        for n, sqm, share, src in p["items"]:
            items.append((n, sqm, share, src, p))
        warnings.extend(p["warnings"] if len(planned) == 1 else
                        [f"{_short_job(p, names)}: {w}" for w in p["warnings"]])

    # ── 1. workload → required man-hours, per sub-activity ──────────────────
    norm_ids = [n["id"] for n, _, _, _, _ in items]
    crew_rows = (await session.execute(
        select(norm_role_t.c["Norm_ID"], norm_role_t.c["Role_Code"],
               norm_role_t.c["Headcount"])
        .where(norm_role_t.c["Norm_ID"].in_(norm_ids or [-1])))).all()
    crew_by_norm: dict[int, dict] = {}
    for nid, rc, head in crew_rows:
        crew_by_norm.setdefault(int(nid), {})[str(rc)] = float(head or 0)

    activities: list[dict] = []
    role_manhours: dict[str, float] = {}
    role_jobs: dict[str, dict] = {}
    total_manhours = 0.0
    total_crew_shifts = 0.0
    per_job_mh: dict[int, float] = {}
    for n, sqm, share, src, owner in items:
        per_sqm = manhours_per_sqm(n)
        if per_sqm is None:
            warnings.append(
                f"{n['Execution_Sub_Activity_Code']} has no usable productivity "
                f"figure — excluded from the requirement rather than counted "
                f"as free")
            continue
        mh = sqm * per_sqm
        total_manhours += mh
        per_job_mh[id(owner)] = per_job_mh.get(id(owner), 0.0) + mh
        cs = crew_shifts(n, sqm)
        if cs is not None:
            total_crew_shifts += cs
        crew = crew_by_norm.get(int(n["id"]), {})
        crew_total = sum(crew.values())
        split = {}
        if crew_total > 0:
            # Distribute this activity's man-hours in the crew's own
            # proportions. Multiplying man-hours BY a headcount (the literal
            # reading) is dimensionally wrong — it yields person²·hours.
            for rc, head in crew.items():
                sh = head / crew_total
                split[rc] = round(mh * sh, 2)
                role_manhours[rc] = role_manhours.get(rc, 0.0) + mh * sh
                jl = _short_job(owner, names)
                role_jobs.setdefault(rc, {})[jl] = \
                    role_jobs.setdefault(rc, {}).get(jl, 0.0) + mh * sh
        else:
            warnings.append(
                f"{n['Execution_Sub_Activity_Code']} has no crew composition, "
                f"so its {round(mh, 2)} man-hours cannot be attributed to a "
                f"role")
        activities.append({
            "Equipment_Tag_No": owner["equipment_tag"],
            "Lining_System_Code": owner["lining_system_code"],
            "Job_Label": _short_job(owner, names),
            "Execution_Sub_Activity_Code": n["Execution_Sub_Activity_Code"],
            "Activity": n["Activity"], "Sub_Activity": n["Sub_Activity"],
            "Variant_Key": n["Variant_Key"] or "",
            "Type": n["Type"],
            "Applies_To": src,
            "Share": round(share, 4),
            "Applied_SQM": round(sqm, 2),
            "Benchmark_Crew_Size": n["Crew_Size"],
            "Standard_Productivity_Per_Shift": n["Standard_Productivity_Per_Shift"],
            "Manhours_Per_SQM": round(per_sqm, 4),
            "Required_Manhours": round(mh, 2),
            "Crew_Shifts": round(cs, 3) if cs is not None else None,
            "Required_Headcount": round(mh / deadline_hours, 2),
            "Role_Manhours": split,
        })

    # ── 2. the roster ───────────────────────────────────────────────────────
    thresholds = await ot_thresholds(session)
    available, unmapped = await roster(session, site_id=site_id)
    if unmapped:
        warnings.append(unmapped_warning(unmapped))

    # ── 2b. how many shifts a day ───────────────────────────────────────────
    # Auto: nights are already staffed IN A ROLE THE JOB NEEDS, so running them
    # is a fact rather than a proposal. The HOD can force 2 anyway (operator
    # ruling Q6) — "we could put a night crew on" is a decision the roster
    # cannot make, so the override is not a debug switch.
    night_in_scope = sum(int(available.get(rc, {}).get("Night", 0))
                         for rc in role_manhours) if role_manhours else \
        sum(int(v.get("Night", 0)) for v in available.values())
    auto_shifts = 2 if night_in_scope > 0 else 1
    shifts_per_day = int(shifts_per_day) if shifts_per_day else auto_shifts
    shift_source = "operator" if shifts_per_day != auto_shifts else "roster"
    if shifts_per_day == 2 and night_in_scope == 0:
        warnings.append(
            "a two-shift plan was requested but no active worker in the "
            "required roles is on the night shift. There is no roster to derive "
            "a day/night proportion from, so the split below is an assumed even "
            "one — and the days it saves are days you would have to staff a "
            "night crew to save")

    # ── 3. the gap, per role ────────────────────────────────────────────────
    # The site's own day/night proportion, used as the fallback basis for a role
    # that has nobody on the roster. Derived from the total rather than summed
    # from the two buckets, so a worker whose Shift is neither Day nor Night is
    # still counted somewhere.
    site_night = sum(int(v.get("Night", 0)) for v in available.values())
    site_day = max(sum(int(v.get("total", 0)) for v in available.values())
                   - site_night, 0)
    split_bases: set[str] = set()

    gap_rows = []
    for rc in sorted(set(role_manhours) | set(available)):
        mh = role_manhours.get(rc, 0.0)
        need_exact = mh / deadline_hours
        have = available.get(rc, {})
        have_total = int(have.get("total", 0))
        gap = need_exact - have_total
        need_round = math.ceil(need_exact - 1e-9)
        # ⚠️ THE SPLIT COMES FROM THE ROSTER, NOT FROM DIVIDING BY THE SHIFT
        # COUNT (Phase 9b, ruling Q10). Day 20 / night 80 is this operator's
        # normal, and an even split would understate the night crew fourfold.
        d_share, n_share, basis = shift_split(
            have, site_day=site_day, site_night=site_night,
            shifts_per_day=shifts_per_day)
        split_bases.add(basis)
        # Rounded UP on each shift rather than apportioned from the total,
        # because you cannot roster a fraction of a mason and each shift has to
        # stand on its own crew.
        need_day = math.ceil(need_exact * d_share - 1e-9)
        need_night = math.ceil(need_exact * n_share - 1e-9)

        gap_rows.append({
            "Role_Code": rc,
            "Required_Manhours": round(mh, 2),
            "Required_Headcount": round(need_exact, 2),
            "Required_Headcount_Rounded": need_round,
            "Required_Day_Headcount": need_day,
            "Required_Night_Headcount": need_night,
            "Shift_Split_Basis": basis,
            # Kept for every existing caller. It is now the LARGER of the two
            # shifts — the crew you actually have to be able to field at once —
            # rather than the total divided evenly, which was only ever right
            # when the shifts happened to be the same size.
            "Headcount_Per_Shift": max(need_day, need_night),
            "Available_Headcount": have_total,
            "Available_GI": int(have.get("GI", 0)),
            "Available_NON_GI": int(have.get("NON_GI", 0)),
            "Available_Day": int(have.get("Day", 0)),
            "Available_Night": int(have.get("Night", 0)),
            "Gap_Headcount": round(gap, 2),
            "To_Procure": max(math.ceil(gap - 1e-9), 0),
            # Which jobs asked for this role, biggest first — the collapsible
            # detail behind the headline row.
            "Jobs": [{"Job": j, "Required_Manhours": round(v, 2)}
                     for j, v in sorted(role_jobs.get(rc, {}).items(),
                                        key=lambda kv: -kv[1])],
        })

    # ── 4. the overtime strategy ────────────────────────────────────────────
    gi_thr = float(thresholds.get("GI", 8.0))
    ng_thr = float(thresholds.get("NON_GI", 10.0))
    # The whole payroll, for the roster panel — this is what the site employs.
    n_gi = sum(int(v.get("GI", 0)) for v in available.values())
    n_ng = sum(int(v.get("NON_GI", 0)) for v in available.values())

    # ⚠️ CAPACITY IS THE ROLES THIS JOB NEEDS, NOT EVERYONE ON THE PAYROLL
    # (Phase 9b). `days_with_roster` below has always filtered to `role_manhours`
    # — "idle blasters do not shorten a brick-lining job" — and the overtime
    # arithmetic did not. An idle blaster inflated normal capacity, which
    # understated the overtime AND understated the hiring advice that clears it:
    # the two numbers an HOD actually acts on. Same reasoning, same filter.
    #
    # The one exception is a workload that could not be attributed to any role
    # at all (a benchmark with no crew composition — already warned about
    # above). There, an in-scope capacity of zero would report the entire job as
    # unmet on top of a warning that says why, so the payroll figure stands in.
    if role_manhours:
        cap_gi = sum(int(available.get(rc, {}).get("GI", 0)) for rc in role_manhours)
        cap_ng = sum(int(available.get(rc, {}).get("NON_GI", 0)) for rc in role_manhours)
    else:
        cap_gi, cap_ng = n_gi, n_ng

    normal_capacity = (cap_gi * gi_thr + cap_ng * ng_thr) * shifts
    ot_capacity = (cap_gi * (SHIFT_WORKED_HOURS - gi_thr)
                   + cap_ng * (SHIFT_WORKED_HOURS - ng_thr)) * shifts
    normal_used = min(total_manhours, normal_capacity)
    ot_used = min(max(total_manhours - normal_capacity, 0.0), ot_capacity)
    unmet = max(total_manhours - normal_capacity - ot_capacity, 0.0)
    overflow = max(total_manhours - normal_capacity, 0.0)

    # To ERASE the overtime you have to raise NORMAL capacity, and each hire
    # raises it by their own threshold. A non-GI worker brings 10 hours where a
    # GI worker brings 8, so fewer of them clear the same overflow — that is
    # the whole of the "prefer non-GI" advice, and it is arithmetic.
    ng_needed = math.ceil(overflow / (ng_thr * shifts) - 1e-9) if overflow > 0 and ng_thr > 0 else 0
    gi_needed = math.ceil(overflow / (gi_thr * shifts) - 1e-9) if overflow > 0 and gi_thr > 0 else 0

    # Days the CURRENT roster would take, which is a different question from
    # the target and the one people actually argue about. Only the roles this
    # job needs count — idle blasters do not shorten a brick-lining job.
    roster_in_scope = sum(int(available.get(rc, {}).get("total", 0))
                          for rc in role_manhours)
    days_with_roster = (total_manhours / (roster_in_scope * SHIFT_WORKED_HOURS)
                        if roster_in_scope > 0 and total_manhours > 0 else None)

    # ⚠️ WHAT RUNNING NIGHTS ACTUALLY BUYS (ruling Q10). Not fewer people — the
    # total is `manhours / (days x 11)` whatever the shift count, because a
    # person works one shift a day. What it buys is CALENDAR TIME: the site
    # delivers `(day + night) x 11` man-hours per day instead of `day x 11`.
    # Reporting the saving is the whole point of the ruling; a planner that
    # shows only the unchanged headcount reads as though nights bought nothing.
    day_in_scope = max(roster_in_scope - night_in_scope, 0)
    days_day_only = (total_manhours / (day_in_scope * SHIFT_WORKED_HOURS)
                     if day_in_scope > 0 and total_manhours > 0 else None)
    days_saved = (round(days_day_only - days_with_roster, 2)
                  if days_day_only is not None and days_with_roster is not None
                  else None)

    if len(planned) == 1:
        workload = planned[0]["workload"]
        selection_out = planned[0]["benchmark_selection"]
    else:
        workload = {
            "remaining_sqm": round(sum(p["workload"]["remaining_sqm"]
                                       for p in planned), 2),
            "original_sqm": round(sum(p["workload"]["original_sqm"]
                                      for p in planned), 2),
            "done_sqm": round(sum(p["workload"]["done_sqm"] for p in planned), 2),
            "source": f"{len(planned)} job(s)", "note": "",
        }
        selection_out = {
            "rules_applied": [r for p in planned
                              for r in p["benchmark_selection"]["rules_applied"]],
            "surface_prep_partition": [
                r for p in planned
                for r in p["benchmark_selection"]["surface_prep_partition"]],
            "topcoats": [r for p in planned
                         for r in p["benchmark_selection"]["topcoats"]],
            "merged_surfaces": [
                r for p in planned
                for r in p["benchmark_selection"]["merged_surfaces"]],
        }

    return {
        "inputs": {
            "site_id": site_id,
            "equipment_tag": planned[0]["equipment_tag"] if planned else "",
            "lining_system_code": planned[0]["lining_system_code"] if planned else "",
            "system_agnostic": planned[0]["system_agnostic"] if planned else True,
            "jobs": [{"Equipment_Tag_No": p["equipment_tag"],
                      "Lining_System_Code": p["lining_system_code"],
                      "Job_Label": _short_job(p, names),
                      "Remaining_SQM": p["workload"]["remaining_sqm"],
                      "Required_Manhours": round(per_job_mh.get(id(p), 0.0), 2)}
                     for p in planned],
            "deadline_hours": deadline_hours,
            "target_days": round(days, 3),
            "shifts_per_day": shifts_per_day,
            "shifts_per_day_source": shift_source,
            "shifts_in_window": round(shifts, 3),
            "shift_worked_hours": SHIFT_WORKED_HOURS,
            "ot_thresholds": thresholds,
        },
        "workload": workload,
        "jobs": [{"Equipment_Tag_No": p["equipment_tag"],
                  "Lining_System_Code": p["lining_system_code"],
                  "Job_Label": _short_job(p, names),
                  "Job": job_label(p["equipment_tag"], p["lining_system_code"],
                                   names, p.get("eq_type", "")),
                  "workload": p["workload"],
                  "Required_Manhours": round(per_job_mh.get(id(p), 0.0), 2)}
                 for p in planned],
        "activities": activities,
        "benchmark_selection": selection_out,
        "requirement": {
            "Total_Required_Manhours": round(total_manhours, 2),
            "Total_Required_Headcount": round(
                total_manhours / deadline_hours, 2) if deadline_hours else None,
            # Summed from the per-role figures rather than divided from the
            # total: each role's split follows its OWN roster proportion, so
            # there is no single site-wide ratio to divide by.
            "Required_Day_Headcount": sum(r["Required_Day_Headcount"]
                                          for r in gap_rows),
            "Required_Night_Headcount": sum(r["Required_Night_Headcount"]
                                            for r in gap_rows),
            # The largest crew that has to stand on the deck at one time:
            # each shift summed across roles, then the bigger of the two. NOT a
            # max over per-role figures — that would report the biggest single
            # trade and call it the crew.
            "Headcount_Per_Shift": max(
                sum(r["Required_Day_Headcount"] for r in gap_rows),
                sum(r["Required_Night_Headcount"] for r in gap_rows)),
            "Shift_Split_Basis": ("roster" if split_bases == {"roster"}
                                  else "day_only" if split_bases <= {"day_only"}
                                  else "mixed" if len(split_bases) > 1
                                  else next(iter(split_bases), "day_only")),
            "Total_Crew_Shifts": round(total_crew_shifts, 3),
            "Total_Days": round(days, 2),
            "Total_Calendar_Shifts": round(days * shifts_per_day, 2),
            "Days_With_Current_Roster": round(days_with_roster, 2)
            if days_with_roster is not None else None,
            # The Q10 answer, in the three numbers an HOD compares.
            "Days_Day_Shift_Only": round(days_day_only, 2)
            if days_day_only is not None else None,
            "Days_Both_Shifts": round(days_with_roster, 2)
            if days_with_roster is not None else None,
            "Days_Saved_By_Nights": days_saved,
            "Activities": len(activities),
            "Jobs": len(planned),
        },
        "roster": {
            "GI": n_gi, "NON_GI": n_ng, "Total": n_gi + n_ng,
            "In_Scope": roster_in_scope,
            "Day_In_Scope": day_in_scope,
            "Night_In_Scope": night_in_scope,
            # What the overtime arithmetic actually counted — the roles this job
            # needs, not the payroll. Published so the two can be compared when
            # they disagree, which is exactly when somebody is confused.
            "Capacity_GI": cap_gi, "Capacity_NON_GI": cap_ng,
            "Unmapped": unmapped,
        },
        "gap": gap_rows,
        "strategy": {
            "Normal_Capacity_Manhours": round(normal_capacity, 2),
            "Overtime_Capacity_Manhours": round(ot_capacity, 2),
            "Normal_Hours_Used": round(normal_used, 2),
            "Overtime_Hours_Incurred": round(ot_used, 2),
            "Unmet_Manhours": round(unmet, 2),
            "Feasible": unmet <= 1e-9,
            "Hire_NON_GI_To_Clear_Overtime": ng_needed,
            "Hire_GI_To_Clear_Overtime": gi_needed,
            "Recommendation": _recommend(total_manhours, normal_capacity,
                                         unmet, ng_needed, gi_needed,
                                         ng_thr, gi_thr),
        },
        "warnings": warnings,
    }


def _short_job(p: dict, names: dict) -> str:
    return job_label(p["equipment_tag"], p["lining_system_code"], names,
                     p.get("eq_type", ""))["Short"]


def _recommend(required: float, normal_capacity: float, unmet: float,
               ng: int, gi: int, ng_thr: float, gi_thr: float) -> str:
    if required <= 0:
        return ("Nothing to plan — there is no remaining area, or no benchmark "
                "to measure it with. Check the warnings before reading this "
                "as 'finished'.")
    if required <= normal_capacity:
        return (f"The current roster absorbs all {round(required)} man-hours "
                f"inside normal time. No overtime and no hiring needed.")
    parts = [
        f"{round(required - normal_capacity)} man-hours fall outside normal "
        f"capacity."]
    if ng:
        parts.append(
            f"Adding {ng} non-GI worker(s) clears it inside normal time — "
            f"each absorbs {ng_thr:g} h against a GI worker's {gi_thr:g} h, so "
            f"{gi} GI worker(s) would be needed for the same result.")
    if unmet > 0:
        parts.append(
            f"⚠️ {round(unmet)} man-hours cannot be covered even with everyone "
            f"on full overtime — the deadline is not reachable with this "
            f"workforce.")
    return " ".join(parts)
