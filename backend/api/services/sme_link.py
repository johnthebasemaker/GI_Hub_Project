"""
backend/api/services/sme_link.py — Phase 13, Track 3: the bridge between the
ERP warehouse and the SME estimator, and the four rules that keep it honest.

WHAT THE OPERATOR ASKED FOR. When a Surface Shield item is consumed in the
general Inventory, it must show up in the SME as consumed, and the system must
ask WHO it was for: a system code with a date, the square metres that material
covered, and an equipment tag drawn from the equipment that system code
actually applies to. Anything off benchmark goes to the HOD.

════════════════════════════════════════════════════════════════════════════
⚠️ RULE 1a IS AMENDED, NOT OVERTURNED (ruling Q13-5 — Option B).

Rule 1a, locked 2026-08-02, says the estimator and the warehouse are two
separate pools and an ERP movement must not move a single SME number. Track 3
crosses that line in ONE direction and by ONE column:

    the estimator gains `Consumed_Qty` — an OBSERVATION, for visibility

and its readiness maths does not move at all. `Status`, `Completion_Pct`,
`SQM_Achievable_Now`, `Coverage_Now_Pct`, `Fulfillment_Pct` and `Allocated_Qty`
are byte-identical across any consumption; `sme_inventory_seed` is not read,
not written and not netted. Suite DA asserts that as bytes rather than as an
intention, because "we did not mean to change readiness" is not a property a
future reader can check.

⚠️ `Consumed_Qty` IS IN THE SAME CATEGORY AS `Allocated_Qty` (rule 1b). Nothing
may colour it as coverage, nothing may divide by it, and no KPI may name it.
`Allocated_Qty` was already made green by six presentation layers that each
thought they were being helpful, and that overstated buildable area by
9,118 m² — 21.5 % of the programme. This column has the identical shape.

════════════════════════════════════════════════════════════════════════════
⚠️ THE `SME_EXEC` EXCLUSION IS THE HIGHEST-SEVERITY RULE IN THIS PHASE, AND IT
LIVES IN EXACTLY ONE PLACE: `EXCLUDE_SELF_SQL` below.

`execution.post_stock` is the ONLY writer for lining consumption (ruling Q1-b),
and it writes `consumption` rows stamped `Source_Ref = 'SME_EXEC:<entry>:<line>'`.
Those rows are ALREADY attributed — the paper form they came from carried a
system code, an equipment tag and an area, and `post_progress` has already
credited that area to the tag.

So if the sweep saw them:

  * a supervisor would be asked to type an area the form already recorded, and
  * answering would credit the SAME drum against the SAME tag a second time,
    inflating `Done_SQM` and every completion figure derived from it.

One predicate, one home, used by the sweep and by every read. A second copy is
how it drifts, and the drift would look like ordinary double work rather than
like a bug.

════════════════════════════════════════════════════════════════════════════
⚠️ THE QUEUE IS A LEDGER SWEEP, NOT AN INBOX (ruling Q13-6).

    Any Surface Shield consumption entry lacking an assigned SQM or System
    Code appears in the queue, sorted by date, no matter how it got there.

An inbox holds what arrived after it was switched on. A sweep holds everything
missing an attribution, including rows that predate the feature — and those are
the rows the business actually needs reconciled: 1,674 live consumption rows
carry a blank WBS and none carries an SQM. Built as a trigger this would look
correct and be empty of exactly the history it was asked for.

Hence a QUERY: `consumption` LEFT JOINed to `sme_consumption_log.Consumption_ID`,
filtered to the misses. Four sources, one list:

  · historical rows already in the ledger, some pre-migration
  · rows landed by `bulk_import` / `tools/pg_excel_sync.py`
  · an ordinary store-keeper issue through `entry.py`
  · an OCR paper upload — ⚠️ EXCLUDED, see above

⚠️ AND THE CLASSIFIER IS `inventory.Category` (ruling Q13-10), the same exact
match the MTC gate uses (`quality.controlled_category`). NOT
`consumption.Item_Type`, which is a different column in a different table,
spelled "Surface Shield" singular, and populated only by the workbook. Two
classifiers for one question is one more than can be kept in agreement.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..sme_engine import mat_key, sap_norm
from .ledger import _MD, write_audit

log_t = _MD.tables["sme_consumption_log"]
recipe_t = _MD.tables["sme_recipe"]
equipment_t = _MD.tables["sme_equipment"]

# ⚠️ THE PREFIX IS A CONTRACT WITH `execution.post_stock`. It writes
# `f"SME_EXEC:{entry_id}:{line_id}"`; anything that changes there changes here,
# and the two are named together so a grep for either finds both.
SME_EXEC_PREFIX = "SME_EXEC:"

# ⚠️ ONE PREDICATE, ONE HOME. Every read of eligible consumption goes through
# this string. Inlining it a second time is how the sweep and the reports drift
# apart, and the symptom — an execution entry appearing in a queue that should
# never have seen it — reads as ordinary double work rather than as a bug.
#
# `LIKE` rather than `starts_with`: `Source_Ref` is nullable and NULL must PASS
# the filter (an unstamped row is an ordinary issue), which `NOT LIKE` alone
# would silently drop. The `IS NULL` arm is not redundant.
EXCLUDE_SELF_SQL = (
    '(c."Source_Ref" IS NULL OR c."Source_Ref" NOT LIKE :sme_exec_like)')
EXCLUDE_SELF_PARAMS = {"sme_exec_like": SME_EXEC_PREFIX + "%"}


def is_self_posted(source_ref: Optional[str]) -> bool:
    """True when this ledger row was posted BY an SME execution entry.

    The Python twin of `EXCLUDE_SELF_SQL`, for callers holding a row rather
    than writing a query. Both are here so they cannot drift.
    """
    return str(source_ref or "").startswith(SME_EXEC_PREFIX)


# The sweep. Every Surface Shield consumption row with no attribution row
# pointing at it, oldest first.
#
# ⚠️ `posted_at` IS NOT THE DELIVERY DATE AND `Date` IS NOT THE FILING TIME.
# `consumption."Date"` is the work date typed by a human; it is the one a
# supervisor recognises, so it is what the queue sorts and displays. Ordered
# with `id` as a tiebreak because `Date` is ISO TEXT and a day's rows would
# otherwise come back in whatever order the scan produced.
# ⚠️ THE MATERIAL CODE COMES FROM THE RECIPE FIRST, AND THIS IS NOT A
# PREFERENCE — IT IS THE ONLY PLACE IT EXISTS FOR THREE COMPONENTS IN FOUR.
#
# `inventory."Material_Code"` is UNIQUELY CONSTRAINED, and a multi-part system
# is four inventory rows sharing one material. The live data resolves that the
# only way it can: the first variant carries the code and the rest are NULL.
#
#     SAP 1041    Material_Code GI-8005765
#     SAP 1041-1  Material_Code NULL
#     SAP 1041-2  Material_Code NULL
#     SAP 1041-3  Material_Code NULL
#
# Reading the material off `inventory` therefore returns NULL for Comp-B, C and
# D — and rule 1's key is `(Material_Code, SAP_Code)`, so those three would key
# on `(None, sap)`, match no recipe line, and price against no benchmark. They
# would appear in the queue looking perfectly ordinary and be un-assignable.
#
# `sme_recipe` is where the component identity is complete, because that is the
# table the rule was written for. A SAP belongs to exactly one material there
# (the unique key is (system, ESC, material, SAP) and no live SAP spans two
# materials), so MIN() picks the one value rather than choosing between
# several.
_MAT_BY_SAP = '''
    SELECT REPLACE(TRIM(r."SAP_Code"), ' ', '') AS sap,
           MIN(r."Material_Code") AS material_code
    FROM sme_recipe r
    WHERE COALESCE(TRIM(r."SAP_Code"), '') <> ''
    GROUP BY 1
'''

SWEEP_SQL = f'''
SELECT c."id"            AS consumption_id,
       c."Date"          AS work_date,
       c."SAP_Code"      AS sap_code,
       c."Quantity"      AS quantity,
       c."Site_ID"       AS site_id,
       c."Tank_No"       AS tank_no,
       c."Work_Type"     AS work_type,
       c."Remarks"       AS remarks,
       c."Lot_Number"    AS lot_number,
       c."Issued_To"     AS issued_to,
       c."Issued_By"     AS issued_by,
       c."Source_Ref"    AS source_ref,
       i."Equipment_Description" AS material_name,
       COALESCE(m.material_code, i."Material_Code") AS material_code,
       i."UOM"                   AS uom
FROM consumption c
JOIN inventory i
  ON REPLACE(TRIM(i."SAP_Code"), ' ', '') = REPLACE(TRIM(c."SAP_Code"), ' ', '')
LEFT JOIN ({_MAT_BY_SAP}) m
  ON m.sap = REPLACE(TRIM(c."SAP_Code"), ' ', '')
LEFT JOIN sme_consumption_log l
  ON l."Consumption_ID" = c."id"
WHERE LOWER(TRIM(i."Category")) = LOWER(:category)
  AND l."id" IS NULL
  AND {EXCLUDE_SELF_SQL}
  {{site}}
ORDER BY c."Date" ASC, c."id" ASC
LIMIT :limit OFFSET :offset
'''

COUNT_SQL = f'''
SELECT COUNT(*)
FROM consumption c
JOIN inventory i
  ON REPLACE(TRIM(i."SAP_Code"), ' ', '') = REPLACE(TRIM(c."SAP_Code"), ' ', '')
LEFT JOIN sme_consumption_log l
  ON l."Consumption_ID" = c."id"
WHERE LOWER(TRIM(i."Category")) = LOWER(:category)
  AND l."id" IS NULL
  AND {EXCLUDE_SELF_SQL}
  {{site}}
'''


async def sweep(session: AsyncSession, *, site_id: Optional[str],
                limit: int = 100, offset: int = 0) -> dict:
    """Unattributed Surface Shield consumption, oldest first.

    Read-only by construction — slice 13e writes nothing at all, on purpose.
    The self-feeding loop is the highest-severity risk in this phase and it is
    cheapest to catch against a read.
    """
    from . import quality

    category = await quality.controlled_category(session)
    params = {"category": category, "limit": int(limit), "offset": int(offset),
              **EXCLUDE_SELF_PARAMS}
    where_site = ""
    if site_id is not None:
        where_site = 'AND c."Site_ID" = :site'
        params["site"] = site_id

    rows = (await session.execute(
        text(SWEEP_SQL.format(site=where_site)), params)).mappings().all()
    total = (await session.execute(
        text(COUNT_SQL.format(site=where_site)),
        {k: v for k, v in params.items()
         if k not in ("limit", "offset")})).scalar_one()

    items = []
    for r in rows:
        d = dict(r)
        d["sap_code"] = sap_norm(d.get("sap_code"))
        d["Material_Key"] = mat_key(d.get("material_code"), d["sap_code"])
        # The `LS <code>` suffix a store keeper's Issue form already writes
        # into Remarks. A hint for the dropdown, never an assignment: see
        # `hint_system_code`.
        d["hinted_system_code"] = hint_system_code(d.get("remarks"))
        items.append(d)
    return {"items": items, "total": int(total), "category": category,
            "limit": int(limit), "offset": int(offset)}


def hint_system_code(remarks: Optional[str]) -> Optional[str]:
    """The lining system code a store keeper already typed, if they did.

    `IssuePage` writes `LS <code>` (and often `LS <code> (Short Name)`) into
    `Remarks` when a Surface Shield SAP is issued against a chosen system —
    that has been true since 2026-07-18. Reading it back saves the field
    re-picking what somebody already picked.

    ⚠️ A HINT, NOT AN ASSIGNMENT. It PRE-SELECTS the dropdown and nothing else:
    the code is still submitted explicitly, and the equipment/system pair is
    still validated server-side. Remarks is free text an HOD can edit, so a
    value read out of it is a convenience and must never be the record.
    """
    s = str(remarks or "")
    i = s.find("LS ")
    if i < 0:
        return None
    tail = s[i + 3:].strip()
    code = tail.split()[0].strip() if tail.split() else ""
    code = code.rstrip(",;·").strip()
    return code or None


async def system_codes_for_sap(session: AsyncSession, sap_code: str) -> list[dict]:
    """The lining systems whose recipe actually lists this component.

    ⚠️ THE DROPDOWN ASKS RATHER THAN GUESSES (ruling Q13-6). When the system
    code cannot be inferred, the queue offers the codes that could legitimately
    have drawn this material and lets a human choose. A guessed code produces a
    variance against the WRONG benchmark, which is worse than a blank: a blank
    is visibly unfinished, and a wrong benchmark looks finished.

    ⚠️ AND IT JOINS ON `(Material_Code, SAP_Code)`, NOT ON THE MATERIAL ALONE
    (rule 1). One `Material_Code` can be four physical components separated
    only by the variant SAP, and they belong to different coats of different
    systems at different rates.
    """
    rows = (await session.execute(text('''
        SELECT DISTINCT TRIM(r."Lining_System_Code") AS code,
               r."Lining_System_Name" AS name,
               r."Material_Code"      AS material_code,
               r."Execution_Sub_Activity_Code" AS esc,
               r."For_1_SQM"          AS for_1_sqm
        FROM sme_recipe r
        WHERE REPLACE(TRIM(r."SAP_Code"), ' ', '') = :sap
          AND TRIM(COALESCE(r."Lining_System_Code", '')) <> ''
        ORDER BY 1
    '''), {"sap": sap_norm(sap_code)})).mappings().all()
    return [dict(r) for r in rows]


async def equipment_for_system(session: AsyncSession, *, site_id: Optional[str],
                               code: str) -> list[dict]:
    """Equipment tags that carry this lining system code at this site.

    ⚠️ THE FILTER THE OPERATOR ASKED FOR, ENFORCED SERVER-SIDE AS WELL AS
    OFFERED. `sme_equipment` is unique on (Site_ID, Equipment_Tag_No,
    Lining_System_Code), so "which tanks does LSC8 apply to" is a direct read —
    and `assign` re-checks the pair rather than trusting the list it handed
    out. A dropdown is a convenience; it is never a control.
    """
    params = {"code": code.strip()}
    where_site = ""
    if site_id is not None:
        where_site = 'AND e."Site_ID" = :site'
        params["site"] = site_id
    rows = (await session.execute(text(f'''
        SELECT e."Equipment_Tag_No" AS tag, e."Name" AS name,
               e."Location" AS location, e."Site_ID" AS site_id,
               COALESCE(p."Original_SQM", e."Surface_Area_SQM", 0) AS original_sqm,
               COALESCE(p."Done_SQM", 0) AS done_sqm
        FROM sme_equipment e
        LEFT JOIN sme_sqm_progress p
          ON p."Site_ID" = e."Site_ID"
         AND p."Equipment_Tag_No" = e."Equipment_Tag_No"
         AND p."Lining_System_Code" = e."Lining_System_Code"
        WHERE TRIM(e."Lining_System_Code") = :code {where_site}
        ORDER BY e."Equipment_Tag_No"
    '''), params)).mappings().all()
    return [dict(r) for r in rows]


async def recipe_rate(session: AsyncSession, *, code: str, material_code: str,
                      sap_code: str) -> Optional[float]:
    """`For_1_SQM` for ONE component of ONE system, or None.

    ⚠️ KEYED ON `(Material_Code, SAP_Code)` — rule 1, again, and this is the
    join the old `sme_actuals.assign_consumption` got wrong: it summed every
    recipe line matching the MATERIAL CODE, which for a PU system adds four
    components' rates into one number and measures a Comp-A draw against the
    total of A+B+C+D.

    Returns None rather than 0 when the component is not in that system's
    recipe. A zero would read as "the benchmark says none of this material" and
    turn every variance into a divide-by-zero dressed as a percentage; None
    says "we cannot compute this", which is a state the HOD must look at.
    """
    rows = (await session.execute(text('''
        SELECT SUM(r."For_1_SQM") AS rate
        FROM sme_recipe r
        WHERE TRIM(r."Lining_System_Code") = :code
          AND r."Material_Code" = :mat
          AND REPLACE(TRIM(r."SAP_Code"), ' ', '') = :sap
    '''), {"code": code.strip(), "mat": material_code,
           "sap": sap_norm(sap_code)})).scalar()
    return None if rows is None else float(rows)


# ══════════════════════════════════════════════════════════════════════════
# Phase 13f — the intake: who the draw was for, and how it compares
# ══════════════════════════════════════════════════════════════════════════

DEFAULT_TOLERANCE_PCT = 10.0
PRIORITY_HIGH = "HIGH"
PRIORITY_NORMAL = "NORMAL"


async def tolerance_pct(session: AsyncSession) -> float:
    """The variance band, in percent. Admin-editable, default 10 (Q13-8).

    ⚠️ IT SETS PRIORITY, NOT APPROVAL. Every Surface Shield consumption goes to
    the HOD regardless of variance; outside this band the row is rendered as
    **High Priority** at the top of the queue. So moving it re-sorts a list and
    can never gate, un-gate or reopen anything.

    A setting rather than a constant, matching `mtc_required_category`: tuning
    a threshold is an admin action, not a deploy. Read once per submission and
    STORED on the row, so a later change cannot rewrite what a settled row
    said (see `classify`).
    """
    v = (await session.execute(text(
        "SELECT value FROM app_settings WHERE key = 'sme_variance_tolerance_pct'"
    ))).scalar()
    try:
        pct = float(str(v).strip()) if v is not None else DEFAULT_TOLERANCE_PCT
    except (TypeError, ValueError):
        # A typo in a settings row must not stop the field filing work. Fall
        # back to the documented default and carry on — the same fail-open
        # direction `controlled_category` takes, for the same reason.
        return DEFAULT_TOLERANCE_PCT
    return pct if pct > 0 else DEFAULT_TOLERANCE_PCT


def variance_pct(actual: float, expected: Optional[float]) -> Optional[float]:
    """100 x (actual - expected) / expected, or None.

    ⚠️ NONE, NEVER ZERO, AGAINST A ZERO OR ABSENT EXPECTATION. A benchmark of
    zero means the recipe does not list this component for this system, which
    is a thing an HOD must look at — not a row that happens to be exactly on
    target. Publishing a divide-by-zero dressed as 0 % would file the
    least-understood rows among the most compliant ones.
    """
    if expected is None or expected == 0:
        return None
    return round(100.0 * (float(actual) - float(expected)) / float(expected), 4)


def classify(var_pct: Optional[float], tol_pct: float) -> str:
    """HIGH or NORMAL — the priority the queue sorts on.

    ⚠️ A NULL VARIANCE IS **HIGH**, NOT LOW. "We cannot compute this" is
    precisely the state worth an HOD's attention, and treating it as 0 % would
    file the rows nobody understands at the bottom of the list, under every row
    that is merely slightly off. The same reasoning as ruling P10-4's refusal
    to value un-costed stock at zero: arithmetically tidy, and a lie somebody
    would act on.
    """
    if var_pct is None:
        return PRIORITY_HIGH
    return PRIORITY_HIGH if abs(var_pct) > tol_pct else PRIORITY_NORMAL


async def _resolve_material_code(session: AsyncSession, sap: str) -> str:
    """The component's material code — from the RECIPE first (rule 1).

    ⚠️ `inventory."Material_Code"` IS UNIQUELY CONSTRAINED, so a multi-part
    system stores the code on the FIRST variant SAP and NULL on the rest
    (measured: SAP 1041 carries GI-8005765, 1041-1/-2/-3 carry nothing).
    Reading it off `inventory` alone keys three components in four on
    `(None, sap)`, matching no recipe line and pricing against no benchmark.
    """
    mat = (await session.execute(text(
        'SELECT MIN(r."Material_Code") FROM sme_recipe r '
        "WHERE REPLACE(TRIM(r.\"SAP_Code\"), ' ', '') = :sap"
    ), {"sap": sap})).scalar()
    if mat:
        return str(mat)
    mat = (await session.execute(text(
        'SELECT "Material_Code" FROM inventory '
        "WHERE REPLACE(TRIM(\"SAP_Code\"), ' ', '') = :sap LIMIT 1"
    ), {"sap": sap})).scalar()
    return str(mat or "")


async def assign(session: AsyncSession, *, consumption_id: int, code: str,
                 tag: str, sqm: float, work_date: Optional[str],
                 notes: Optional[str], username: str,
                 site_id: Optional[str]) -> dict:
    """Attribute one ledger row to a system, an equipment tag and an area.

    ⚠️ THIS IS AN ATTRIBUTION, NOT A DEDUCTION. The stock left the shelf when
    the store keeper issued it; nothing here moves a quantity, and nothing here
    touches `sme_inventory_seed`. Rule 1a stands — the estimator's readiness
    maths does not move (ruling Q13-5, Option B).

    ⚠️ AND THE BENCHMARK IS SNAPSHOTTED HERE, NEVER RE-JOINED LATER. An HOD may
    correct a recipe rate next quarter; a variance that re-derived its
    benchmark would turn last quarter's 12 % overrun into 4 % with no edit to
    the row and nothing to point at. Same rule, same reason, as
    `sme_execution_entry.Bench_*`.
    """
    from . import quality

    row = (await session.execute(text(
        'SELECT c."id", c."SAP_Code", c."Quantity", c."Site_ID", c."Date", '
        '       c."Source_Ref", c."Lot_Number", i."Category" '
        'FROM consumption c '
        'JOIN inventory i '
        "  ON REPLACE(TRIM(i.\"SAP_Code\"), ' ', '') "
        "   = REPLACE(TRIM(c.\"SAP_Code\"), ' ', '') "
        'WHERE c."id" = :cid'
    ), {"cid": consumption_id})).mappings().first()
    if row is None:
        raise HTTPException(404, f"consumption row {consumption_id} not found")
    if site_id is not None and row["Site_ID"] != site_id:
        raise HTTPException(
            404, f"that consumption was posted at {row['Site_ID']}, not "
                 f"{site_id}. Consumption is attributed at the site the "
                 f"material left.")

    category = await quality.controlled_category(session)
    if str(row["Category"] or "").strip().lower() != category.strip().lower():
        raise HTTPException(
            422, f"{row['SAP_Code']} is not in the {category} category, so it "
                 f"is not lining material and has no square metres to record.")

    # ⚠️ THE SAME EXCLUSION, ENFORCED ON THE WRITE. The queue already hides
    # these, but a queue is a list and never a control: an id typed by hand, or
    # held over from a stale page, must not be able to attribute an execution
    # entry's own posting a second time.
    if is_self_posted(row["Source_Ref"]):
        raise HTTPException(
            409, "that consumption was posted by an execution entry, which "
                 "already recorded its system, its equipment and its area from "
                 "the printed form. Attributing it again would credit the same "
                 "material against the same tag twice.")

    dup = (await session.execute(
        select(log_t.c["id"]).where(log_t.c["Consumption_ID"] == consumption_id)
    )).scalar()
    if dup:
        raise HTTPException(
            409, f"that consumption has already been attributed (row {dup}).")

    code = (code or "").strip()
    tag = (tag or "").strip()
    if not code or not tag:
        raise HTTPException(422, "a system code and an equipment tag are both "
                                 "required — the area belongs to one piece of "
                                 "equipment doing one system.")
    sqm = float(sqm or 0)
    if sqm <= 0:
        raise HTTPException(
            422, "the area covered must be greater than zero. If none was "
                 "covered, this material was not applied and the draw needs a "
                 "different explanation.")

    # ⚠️ THE PAIR IS RE-CHECKED, NOT TRUSTED. `/sme-link/equipment` filters the
    # dropdown to the tags carrying this code — and a dropdown is a
    # convenience. An area posted against a tag that does not carry the system
    # credits progress to a vessel nobody is lining that way.
    pair = (await session.execute(
        select(func.count()).select_from(equipment_t)
        .where(equipment_t.c["Site_ID"] == row["Site_ID"],
               equipment_t.c["Equipment_Tag_No"] == tag,
               equipment_t.c["Lining_System_Code"] == code))).scalar_one()
    if not pair:
        raise HTTPException(
            422, f"{tag} does not carry system code {code} at "
                 f"{row['Site_ID']}. Pick a tag from the list — it is filtered "
                 f"to the equipment that system actually applies to.")

    sap = sap_norm(row["SAP_Code"])
    material_code = await _resolve_material_code(session, sap)
    rate = await recipe_rate(session, code=code, material_code=material_code,
                             sap_code=sap)
    actual = float(row["Quantity"] or 0)
    expected = None if rate is None else round(rate * sqm, 4)
    var = variance_pct(actual, expected)
    tol = await tolerance_pct(session)
    flag = classify(var, tol)

    new_id = (await session.execute(insert(log_t).values(
        batch_id=f"SWEEP:{consumption_id}",
        Site_ID=row["Site_ID"],
        entry_date=(work_date or row["Date"] or ""),
        entered_by=username,
        Equipment_Tag_No=tag,
        Lining_System_Code=code,
        Material_Code=material_code,
        SAP_Code=sap,
        Consumption_ID=consumption_id,
        SQM_Completed=sqm,
        Expected_Qty=expected or 0.0,
        Actual_Qty=actual,
        Variance_Pct=var,
        Bench_For_1_SQM=rate,
        Priority_Flag=flag,
        Variance_Tolerance_Pct=tol,
        notes=notes,
        # ⚠️ `staged`, NOT `committed`. EVERY row goes to the HOD regardless of
        # variance (ruling Q13-8) — there is no auto-commit band, so nothing
        # here may write a settled status.
        status="staged",
    ).returning(log_t.c["id"]))).scalar_one()

    await write_audit(session, username, "SME_LINK_ASSIGN",
                      "sme_consumption_log",
                      f"id={new_id} consumption={consumption_id} -> {tag}/{code} "
                      f"sqm={sqm:g} actual={actual:g} "
                      f"expected={'?' if expected is None else format(expected, 'g')} "
                      f"var={'n/a' if var is None else format(var, '.2f') + '%'} "
                      f"[{flag} @ +/-{tol:g}%]")
    return {"id": new_id, "Consumption_ID": consumption_id,
            "Lining_System_Code": code, "Equipment_Tag_No": tag,
            "Material_Code": material_code, "SAP_Code": sap,
            "SQM_Completed": sqm, "Actual_Qty": actual,
            "Expected_Qty": expected, "Variance_Pct": var,
            "Bench_For_1_SQM": rate, "Priority_Flag": flag,
            "Variance_Tolerance_Pct": tol, "status": "staged"}


async def assigned(session: AsyncSession, *, site_id: Optional[str],
                   status: Optional[str] = None, limit: int = 200) -> dict:
    """Attributed rows — HIGH PRIORITY FIRST, then oldest first.

    ⚠️ THE SORT IS THE WHOLE ANSWER TO "A QUEUE HOLDING EVERY ROW IS A QUEUE
    NOBODY READS" (ruling Q13-8). The operator overruled auto-committing inside
    the band, and was right to: the stock has already left the shelf, this is
    the material the MTC gate exists for, and an auto-committed attribution is
    one nobody ever looks at. Sorting by priority solves the volume problem
    auto-commit was solving, without the cost.

    ⚠️ AND THE FLAG IS READ, NOT RECOMPUTED. `Priority_Flag` was stored at
    submission beside the tolerance it was measured against, so moving the
    tolerance re-sorts new rows and cannot change the character of one already
    decided. Recomputing here would make a row approved at 12 % silently become
    compliant the day somebody tuned the number.
    """
    params: dict = {"limit": int(limit)}
    where = ["1=1"]
    if site_id is not None:
        where.append('l."Site_ID" = :site')
        params["site"] = site_id
    if status:
        where.append('l."status" = :status')
        params["status"] = status
    rows = (await session.execute(text(
        'SELECT l.*, c."Date" AS ledger_date, c."Tank_No" AS ledger_tank '
        'FROM sme_consumption_log l '
        'LEFT JOIN consumption c ON c."id" = l."Consumption_ID" '
        'WHERE ' + " AND ".join(where) + ' '
        # HIGH sorts before NORMAL because a CASE puts it there explicitly —
        # not because 'HIGH' < 'NORMAL' alphabetically, which is true today and
        # is an accident nobody should build on.
        "ORDER BY CASE WHEN l.\"Priority_Flag\" = 'HIGH' THEN 0 ELSE 1 END, "
        '         l."entry_date" ASC, l."id" ASC '
        'LIMIT :limit'), params)).mappings().all()
    items = [dict(r) for r in rows]
    for i in items:
        for k in ("created_at", "committed_at", "rejected_at", "hod_decided_at"):
            if i.get(k) is not None:
                i[k] = str(i[k])
    return {"items": items,
            "high_priority": sum(1 for i in items
                                 if i.get("Priority_Flag") == PRIORITY_HIGH),
            "tolerance_pct": await tolerance_pct(session)}


# ══════════════════════════════════════════════════════════════════════════
# Phase 13g — the HOD decides, and the area reaches the progress ledger
# ══════════════════════════════════════════════════════════════════════════

# ⚠️ THE EDITABLE SURFACE, STATED AS DATA (ruling Q13-7). Three things, and the
# quantity is not one of them.
#
# Unlike an execution entry — where APPROVAL is what deducts stock — the drum
# here left the shelf when the store keeper issued it. So this approval settles
# the ATTRIBUTION and the EXPLANATION, never the deduction. An HOD who wants to
# correct the physical quantity is correcting a POSTED LEDGER ROW, and that goes
# through the stock-adjustment path, where it is an event with its own audit
# line — not a silent UPDATE that leaves the ledger and the store disagreeing.
EDITABLE = ("SQM_Completed", "Lining_System_Code", "Equipment_Tag_No", "notes")

# ⚠️ REFUSED, NOT IGNORED. An ignored field is a silent data loss: an HOD who
# typed a correction into a box that discarded it will believe it was applied,
# and the number they were correcting stays wrong with their name against the
# approval. Named explicitly so the refusal can say what to do instead.
REFUSED = ("Actual_Qty", "Quantity", "quantity", "Expected_Qty", "Variance_Pct",
           "Bench_For_1_SQM", "Priority_Flag", "SAP_Code", "Material_Code",
           "Consumption_ID")


async def decide(session: AsyncSession, *, log_id: int, approve: bool,
                 edits: Optional[dict], justification: str,
                 reject_reason: str, username: str,
                 site_id: Optional[str]) -> dict:
    """Approve (optionally correcting the ATTRIBUTION) or reject.

    ⚠️ APPROVAL IS WHAT CREDITS THE AREA. Until an HOD approves, the row is an
    unreviewed claim and `sme_sqm_progress.Done_SQM` has not moved. The credit
    goes through `execution.credit_done_sqm` — the SAME function
    `post_progress` uses — because two copies of an increment is how a vessel
    gets credited twice.

    ⚠️ AND `Consumed_Qty` COUNTS ONLY COMMITTED ROWS. A staged attribution is
    somebody's claim; the estimator's observation column reports what has been
    REVIEWED. That is the same rule the whole codebase runs on — approval is
    what makes a figure count — and it is why rule 1a's amendment is narrow:
    the estimator never sees raw ledger movement, only an attribution a person
    signed for.
    """
    from . import execution as X

    row = (await session.execute(
        select(log_t).where(log_t.c["id"] == log_id))).mappings().first()
    if row is None:
        raise HTTPException(404, f"attribution {log_id} not found")
    if site_id is not None and row["Site_ID"] != site_id:
        raise HTTPException(404, f"attribution {log_id} not found")
    if row["status"] != "staged":
        raise HTTPException(
            409, f"attribution {log_id} is already {row['status']} — a "
                 f"decision is taken once. Raise a stock adjustment if the "
                 f"physical figure is wrong.")

    edits = dict(edits or {})
    bad = [k for k in edits if k in REFUSED]
    if bad:
        raise HTTPException(
            422, f"{', '.join(sorted(bad))} cannot be edited here. The material "
                 f"left the shelf when it was issued, so this approval settles "
                 f"the area and the explanation, not the quantity. A physical "
                 f"correction is a stock adjustment, which is a ledger event "
                 f"with its own audit line.")
    unknown = [k for k in edits if k not in EDITABLE]
    if unknown:
        raise HTTPException(
            422, f"{', '.join(sorted(unknown))} is not something this screen "
                 f"edits. Editable: {', '.join(EDITABLE)}.")

    if not approve:
        reason = (reject_reason or "").strip()
        if not reason:
            raise HTTPException(
                422, "a rejection needs a reason — the person who filed this "
                     "has to know what to do differently.")
        await session.execute(update(log_t).where(log_t.c["id"] == log_id).values(
            status="rejected", rejected_at=func.now(), rejected_reason=reason,
            hod_username=username, hod_decided_at=func.now()))
        await write_audit(session, username, "SME_LINK_REJECT",
                          "sme_consumption_log", f"id={log_id} — {reason[:160]}")
        return {"id": log_id, "status": "rejected", "reason": reason}

    vals: dict = {}
    edited = False
    if edits:
        # ⚠️ MANDATORY THE MOMENT ANY NUMBER MOVES. An approval that silently
        # rewrote the figures would leave the person who filed them answering
        # for numbers they never entered — the same rule, and the same reason,
        # as `sme_execution_entry.HOD_Edit_Justification`.
        if not (justification or "").strip():
            raise HTTPException(
                422, "changing a filed figure needs a written reason. The "
                     "person who reported it will be answering for what is "
                     "recorded here.")
        edited = True
        vals["HOD_Edit_Justification"] = justification.strip()
        vals["hod_edited"] = True

    code = str(edits.get("Lining_System_Code") or row["Lining_System_Code"]).strip()
    tag = str(edits.get("Equipment_Tag_No") or row["Equipment_Tag_No"]).strip()
    sqm = float(edits.get("SQM_Completed", row["SQM_Completed"]) or 0)
    if sqm <= 0:
        raise HTTPException(
            422, "the area covered must be greater than zero. Reject the row "
                 "instead if the material was not applied.")
    if "notes" in edits:
        vals["notes"] = edits["notes"]

    # The pair is re-checked on the HOD's edit too — an HOD moving a row to a
    # different system may pick a tag that does not carry it.
    pair = (await session.execute(
        select(func.count()).select_from(equipment_t)
        .where(equipment_t.c["Site_ID"] == row["Site_ID"],
               equipment_t.c["Equipment_Tag_No"] == tag,
               equipment_t.c["Lining_System_Code"] == code))).scalar_one()
    if not pair:
        raise HTTPException(
            422, f"{tag} does not carry system code {code} at "
                 f"{row['Site_ID']}.")

    # ⚠️ THE BENCHMARK IS RE-SNAPSHOTTED ONLY WHEN THE ATTRIBUTION MOVED, and
    # then it is re-read for the NEW component/system pair — which is a
    # different benchmark, not a refresh of the old one. An unedited row keeps
    # the rate it was measured against when it was filed.
    rate = row["Bench_For_1_SQM"]
    if edited and (code != row["Lining_System_Code"]
                   or float(sqm) != float(row["SQM_Completed"] or 0)):
        if code != row["Lining_System_Code"]:
            rate = await recipe_rate(session, code=code,
                                     material_code=row["Material_Code"],
                                     sap_code=row["SAP_Code"])
        expected = None if rate is None else round(float(rate) * sqm, 4)
        var = variance_pct(float(row["Actual_Qty"] or 0), expected)
        tol = float(row["Variance_Tolerance_Pct"] or DEFAULT_TOLERANCE_PCT)
        vals.update({"Bench_For_1_SQM": rate,
                     "Expected_Qty": expected or 0.0,
                     "Variance_Pct": var,
                     # ⚠️ RE-CLASSIFIED AGAINST THE ROW'S OWN STORED TOLERANCE,
                     # never against today's setting. The row is being corrected,
                     # not re-measured against a band that has since moved.
                     "Priority_Flag": classify(var, tol)})

    if edited:
        vals["Original_SQM_Completed"] = float(row["SQM_Completed"] or 0)
    vals.update({"Lining_System_Code": code, "Equipment_Tag_No": tag,
                 "SQM_Completed": sqm, "status": "committed",
                 "committed_at": func.now(), "hod_username": username,
                 "hod_decided_at": func.now()})
    await session.execute(update(log_t).where(log_t.c["id"] == log_id).values(**vals))

    # ⚠️ AND THE AREA REACHES THE PROGRESS LEDGER, THROUGH THE SAME FUNCTION
    # `post_progress` USES. Two copies of this increment is how one vessel gets
    # credited twice; suite DB asserts the two paths stay disjoint.
    await X.credit_done_sqm(session, site_id=row["Site_ID"], tag=tag,
                            code=code, sqm=sqm)

    await write_audit(session, username, "SME_LINK_APPROVE",
                      "sme_consumption_log",
                      f"id={log_id} {tag}/{code} sqm={sqm:g}"
                      + (f" (was {float(row['SQM_Completed'] or 0):g}; "
                         f"{justification.strip()[:120]})" if edited else ""))
    return {"id": log_id, "status": "committed", "Lining_System_Code": code,
            "Equipment_Tag_No": tag, "SQM_Completed": sqm,
            "hod_edited": edited, "Done_SQM_credited": sqm}


# ══════════════════════════════════════════════════════════════════════════
# ⚠️ RESOLUTION B — `Consumed_Qty`, an OBSERVATION and nothing more
# ══════════════════════════════════════════════════════════════════════════
#
# Ruling Q13-5: the estimator gains a consumed column FOR VISIBILITY. It must
# not alter readiness logic and must not touch `Allocated_Qty`.
#
# ⚠️ IT READS `sme_consumption_log`, WHICH IS AN SME-OWNED TABLE — NOT THE ERP
# LEDGER. That distinction is the whole reason rule 1a survives this. Suite BA
# guards `SQL_SME_MATERIALS` and `_CALC_POOL_SQL` — the two QUANTITY queries —
# against naming an ERP table and against reading this one; neither is touched.
# `available_qty` is still `Initial_Available_Qty`, full stop.
#
# ⚠️ AND IT COUNTS ONLY **COMMITTED** ROWS. A staged attribution is somebody's
# claim; this reports what an HOD has reviewed. So the estimator never sees raw
# warehouse movement — only a draw a person attributed and a second person
# signed for. Posting a consumption moves NOTHING here until that happens,
# which is what keeps suite BA's byte-identical probe green.
#
# ⚠️ IT IS AN OBSERVATION FIELD, IN THE SAME CATEGORY AS `Allocated_Qty`
# (rule 1b). Nothing may colour it as coverage, nothing may divide by it, and
# no KPI may name it. `Allocated_Qty` was already made green by six
# presentation layers that each thought they were being helpful, and that
# overstated buildable area by 9,118 m² — 21.5 % of the programme.
#
# ⚠️ AND IT IS PER COMPONENT, NOT PER LINE. The same component is drawn for
# many units, so a report that SUMS `Consumed_Qty` down a cascade multiplies it
# by the number of units. It is metadata carried onto each line, exactly like
# `Material_Name` — suite DB asserts no total contains it.
SQL_SME_CONSUMED = '''
SELECT l."Material_Code" AS material_code,
       l."SAP_Code"      AS sap_code,
       SUM(l."Actual_Qty") AS consumed_qty,
       SUM(l."SQM_Completed") AS consumed_sqm,
       COUNT(*)          AS rows_committed
FROM sme_consumption_log l
WHERE l."status" = 'committed'
GROUP BY 1, 2
'''


async def consumed_by_component(session: AsyncSession,
                                site_id: Optional[str] = None) -> list[dict]:
    """Observed, HOD-approved draw per `(Material_Code, SAP_Code)`.

    Keyed on the component (rule 1), because one material code can be four
    physical drums and reporting their sum against any one of them is the
    pooling error that inverted a shortfall.
    """
    sql = SQL_SME_CONSUMED
    params: dict = {}
    if site_id is not None:
        sql = sql.replace("WHERE l.\"status\" = 'committed'",
                          "WHERE l.\"status\" = 'committed' AND l.\"Site_ID\" = :site")
        params["site"] = site_id
    rows = (await session.execute(text(sql), params)).mappings().all()
    return [{"material_code": r["material_code"],
             "sap_code": sap_norm(r["sap_code"]),
             "Material_Key": mat_key(r["material_code"], r["sap_code"]),
             "consumed_qty": float(r["consumed_qty"] or 0),
             "consumed_sqm": float(r["consumed_sqm"] or 0),
             "rows_committed": int(r["rows_committed"] or 0)} for r in rows]
