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
rev_t = _MD.tables["sme_consumption_revision"]
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
    '(c."Source_Ref" IS NULL OR c."Source_Ref" NOT LIKE :sme_exec_like)'
    # ⚠️ PHASE 14b — and a ledger row an execution entry SPEAKS FOR without
    # having posted it: an Excel row the entry adopted, or the one delta row a
    # sync posted for "the book shows more than the paper". Its system, tag and
    # area came from the paper too (services/reconcile.py, invariant L3).
    ' AND NOT EXISTS (SELECT 1 FROM consumption_exec_link x '
    '                 WHERE x."Consumption_ID" = c."id")')
EXCLUDE_SELF_PARAMS = {"sme_exec_like": SME_EXEC_PREFIX + "%"}


def is_self_posted(source_ref: Optional[str]) -> bool:
    """True when this ledger row was posted BY an SME execution entry.

    The Python twin of `EXCLUDE_SELF_SQL`, for callers holding a row rather
    than writing a query. Both are here so they cannot drift.
    """
    return str(source_ref or "").startswith(SME_EXEC_PREFIX)


# ─── what an attribution was measured against (2026-09-16) ───────────────────
#
# ⚠️ ONE SQL EXPRESSION, USED AT WRITE TIME AND AT READ TIME, NEVER A PYTHON
# TWIN. The Excel sync now updates a ledger row IN PLACE when the workbook
# edits it (its id survives, so the attribution's link survives). The link
# surviving is exactly why the attribution needs to know the row CHANGED: a
# variance measured against 30 units must not quietly stand beside a ledger
# row that now says 45. A Python fingerprint would have to round a float the
# way Postgres does; a single SQL expression cannot disagree with itself.
#
# The four fields are the ones an attribution depends on — when, what, how
# much, which tank. A Remarks or Issued-To edit changes none of them, so it does
# not send finished work back to the field: "if the new push is the same as the
# old entry, do not ask again".
SOURCE_FP_SQL = (
    "md5(concat_ws('|', "
    "left(COALESCE(c.\"Date\", ''), 10), "
    "REPLACE(TRIM(COALESCE(c.\"SAP_Code\", '')), ' ', ''), "
    "COALESCE(CAST(ROUND(CAST(c.\"Quantity\" AS NUMERIC), 4) AS TEXT), ''), "
    "TRIM(COALESCE(c.\"Tank_No\", ''))))")

# Is this attribution out of date with its ledger row? An attribution filed
# before fingerprints existed has none, so for those the quantity alone is
# compared — the only field Phase 13 already snapshotted.
STALE_SQL = (
    "(CASE WHEN l.\"Source_Fingerprint\" IS NOT NULL "
    f"      THEN l.\"Source_Fingerprint\" <> {SOURCE_FP_SQL} "
    "      ELSE ROUND(CAST(COALESCE(l.\"Pack_Qty\", l.\"Actual_Qty\", 0) AS NUMERIC), 4) "
    "        <> ROUND(CAST(COALESCE(c.\"Quantity\", 0) AS NUMERIC), 4) END)")

# A staged revision already answers the edit — as long as it was filed against
# the ledger row as it is NOW. A revision filed against an earlier edit is
# itself stale, and the row goes back to the field.
FRESH_REVISION_SQL = (
    "EXISTS (SELECT 1 FROM sme_consumption_revision v "
    "        WHERE v.\"Log_ID\" = l.\"id\" AND v.status = 'staged' "
    f"          AND v.\"Source_Fingerprint\" = {SOURCE_FP_SQL})")

# ─── the rejection bounce-back (2026-09-17) ──────────────────────────────────
#
# ⚠️ A REJECTED ATTRIBUTION IS NOT THE END OF THE QUESTION. Until now an HOD
# rejection on a first-time attribution was terminal: the row left the queue
# for good, and the drum it described could never be attributed again, however
# plainly the rejection reason said what was wrong ("it was TANK-A"). That
# stranded real consumption with no area against it. The consumption row is
# the identity here — there is no fresh paper to raise, unlike an execution
# entry, whose terminal rejection (ruling Q4, suite CN-02) is untouched.
#
# So a rejection BOUNCES BACK: the row returns to the field's queue, at the TOP,
# with the HOD's reason, and a resubmission sends it back to the HOD.
#
# The latest revision of an attribution, whatever its status. A revision the
# HOD rejected is a bounce too — the field's re-assignment of an edited,
# already-approved row came back — so it sorts and badges the same way.
LATEST_REVISION_JOIN = (
    "LEFT JOIN LATERAL ("
    "  SELECT v.status, v.rejected_reason, v.hod_username, v.hod_decided_at, "
    "         v.\"Lining_System_Code\", v.\"Equipment_Tag_No\", "
    "         v.\"SQM_Completed\" "
    "  FROM sme_consumption_revision v WHERE v.\"Log_ID\" = l.\"id\" "
    "  ORDER BY v.id DESC LIMIT 1) lr ON TRUE")

# Why this row is in front of the field. ONE expression, used by the SELECT,
# the ORDER BY and the counts, so the badge, the sort and the banner can never
# disagree about which rows were rejected.
REASON_SQL = (
    "(CASE WHEN l.\"id\" IS NULL THEN 'unattributed' "
    "      WHEN l.\"status\" = 'rejected' OR lr.status = 'rejected' "
    "      THEN 'rejected' ELSE 'edited' END)")

# Rows that need the field: never attributed; OR REJECTED by the HOD; OR
# attributed and then edited in Excel with no up-to-date answer waiting on the
# HOD. A rejected revision needs no arm of its own: the row it revises is still
# out of date with its ledger row, which is exactly why the revision existed.
NEEDS_FIELD_SQL = (
    f"(l.\"id\" IS NULL OR l.\"status\" = 'rejected' "
    f"OR ({STALE_SQL} AND NOT {FRESH_REVISION_SQL}))")


async def source_fingerprint(session: AsyncSession, consumption_id: int) -> Optional[str]:
    return (await session.execute(text(
        f"SELECT {SOURCE_FP_SQL} FROM consumption c WHERE c.\"id\" = :cid"),
        {"cid": consumption_id})).scalar()


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
       i."UOM"                   AS uom,
       -- ⚠️ 2026-09-16: an EDITED row carries what it was attributed as, so
       -- the field re-answers with the old answer in front of them instead of
       -- starting from nothing.
       {REASON_SQL} AS reason,
       -- ⚠️ 2026-09-17: a REJECTED row carries the HOD's reason and the values
       -- that were rejected, so the field sees what to fix without asking.
       CASE WHEN l."status" = 'rejected' THEN l."rejected_reason"
            WHEN lr.status = 'rejected' THEN lr.rejected_reason END
                                 AS rejection_reason,
       CASE WHEN l."status" = 'rejected' THEN l."hod_username"
            WHEN lr.status = 'rejected' THEN lr.hod_username END AS rejected_by,
       CASE WHEN l."status" = 'rejected' THEN l."rejected_at"
            WHEN lr.status = 'rejected' THEN lr.hod_decided_at END AS rejected_at,
       CASE WHEN l."status" = 'rejected' THEN l."Lining_System_Code"
            WHEN lr.status = 'rejected' THEN lr."Lining_System_Code" END
                                 AS rejected_code,
       CASE WHEN l."status" = 'rejected' THEN l."Equipment_Tag_No"
            WHEN lr.status = 'rejected' THEN lr."Equipment_Tag_No" END
                                 AS rejected_tag,
       CASE WHEN l."status" = 'rejected' THEN l."SQM_Completed"
            WHEN lr.status = 'rejected' THEN lr."SQM_Completed" END
                                 AS rejected_sqm,
       -- Rejected AND edited in Excel since: both badges apply.
       CASE WHEN l."id" IS NULL THEN FALSE ELSE {STALE_SQL} END AS edited_in_excel,
       l."id"                    AS log_id,
       l."status"                AS log_status,
       l."Lining_System_Code"    AS prev_code,
       l."Equipment_Tag_No"      AS prev_tag,
       l."SQM_Completed"         AS prev_sqm,
       l."Actual_Qty"            AS prev_qty,
       l."Variance_Pct"          AS prev_variance_pct,
       CASE WHEN lr.status = 'rejected' THEN lr.rejected_reason END
                                 AS last_revision_rejected_reason
FROM consumption c
JOIN inventory i
  ON REPLACE(TRIM(i."SAP_Code"), ' ', '') = REPLACE(TRIM(c."SAP_Code"), ' ', '')
LEFT JOIN ({_MAT_BY_SAP}) m
  ON m.sap = REPLACE(TRIM(c."SAP_Code"), ' ', '')
LEFT JOIN sme_consumption_log l
  ON l."Consumption_ID" = c."id"
{LATEST_REVISION_JOIN}
WHERE LOWER(TRIM(i."Category")) = LOWER(:category)
  AND {NEEDS_FIELD_SQL}
  AND {EXCLUDE_SELF_SQL}
  {{site}}
-- ⚠️ REJECTED FIRST, then oldest first. A bounced row is work the field has
-- already done once and the HOD is waiting on; left in date order it would sit
-- behind months of never-attributed history and never be seen.
ORDER BY CASE WHEN {REASON_SQL} = 'rejected' THEN 0 ELSE 1 END,
         c."Date" ASC, c."id" ASC
LIMIT :limit OFFSET :offset
'''

COUNT_SQL = f'''
SELECT COUNT(*),
       COUNT(*) FILTER (WHERE {REASON_SQL} = 'edited'),
       COUNT(*) FILTER (WHERE {REASON_SQL} = 'rejected')
FROM consumption c
JOIN inventory i
  ON REPLACE(TRIM(i."SAP_Code"), ' ', '') = REPLACE(TRIM(c."SAP_Code"), ' ', '')
LEFT JOIN sme_consumption_log l
  ON l."Consumption_ID" = c."id"
{LATEST_REVISION_JOIN}
WHERE LOWER(TRIM(i."Category")) = LOWER(:category)
  AND {NEEDS_FIELD_SQL}
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
    total, edited, rejected = (await session.execute(
        text(COUNT_SQL.format(site=where_site)),
        {k: v for k, v in params.items()
         if k not in ("limit", "offset")})).one()

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
    return {"items": items, "total": int(total), "edited": int(edited or 0),
            "rejected": int(rejected or 0),
            "category": category, "limit": int(limit), "offset": int(offset)}


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
    # Phase 15d: a surface-prep code (Garnet) is carried by NO equipment — it
    # applies to every tag whose substrate calls for it (services/prep.py).
    from . import prep as PR
    if await PR.is_prep(session, code):
        q = ('SELECT DISTINCT "Equipment_Tag_No" AS tag, "Name" AS name, "Location" AS location, '
             '"Site_ID" AS site_id, "Type", "Substrate" FROM sme_equipment'
             + (' WHERE "Site_ID" = :site' if site_id is not None else '')
             + ' ORDER BY 1')
        out, seen = [], set()
        for r in (await session.execute(text(q), {"site": site_id})).mappings().all():
            if PR.code_for(r["Type"], r["Substrate"]) == code.strip() and r["tag"] not in seen:
                seen.add(r["tag"])
                out.append({"tag": r["tag"], "name": r["name"], "location": r["location"],
                            "site_id": r["site_id"], "original_sqm": 0, "done_sqm": 0})
        return out
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
                      sap_code: str, surface_state: Optional[str] = None) -> Optional[float]:
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

    ⚠️ PHASE 15d — A SURFACE-PREP CODE (ESC1/ESC2, Garnet) IS NOT BENCHMARKED
    FROM ITS RECIPE LINE but from the Old/New baseline (services/prep.py), so
    `surface_state` decides it; without one there is no benchmark.
    """
    from . import prep as PR
    if await PR.is_prep(session, code):
        return await PR.rate(session, code.strip(), surface_state)
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


async def _units(session: AsyncSession, sap: str) -> dict:
    from . import units as U
    return await U.unit_info(session, sap)


def units_base(pack: float, uinfo: dict) -> float:
    """The BASE quantity an attribution records, or a 422.

    ⚠️ A Surface Shield pack with no known factor is REFUSED, not guessed. The
    only alternative is to record the can count as kilograms — defect D1 — and
    a refusal that says "fill Unit Size in the Inventory sheet" is fixed in a
    minute where a wrong variance is believed for a quarter.
    """
    from . import units as U
    if not uinfo.get("is_surface_shield"):
        return float(pack)
    b = U.base_qty(pack, uinfo.get("factor"))
    if b is None:
        raise HTTPException(
            422, f"SAP {uinfo.get('sap')} is counted in "
                 f"{uinfo.get('pack_uom') or 'packs'} but has no Unit Size, so its "
                 f"quantity cannot be compared with the recipe (which is per "
                 f"{uinfo.get('base_uom') or 'kg'}). Fill 'Unit Size' for it in the "
                 f"Inventory sheet and re-run the Excel sync.")
    return b


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
                 site_id: Optional[str], surface_state: Optional[str] = None) -> dict:
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
    linked = (await session.execute(text(
        'SELECT "Entry_ID" FROM consumption_exec_link WHERE "Consumption_ID" = :c'),
        {"c": consumption_id})).scalar()
    if is_self_posted(row["Source_Ref"]) or linked is not None:
        raise HTTPException(
            409, "that consumption was posted by an execution entry, which "
                 "already recorded its system, its equipment and its area from "
                 "the printed form. Attributing it again would credit the same "
                 "material against the same tag twice.")

    # ⚠️ 2026-09-16: AN EXISTING ATTRIBUTION IS REFUSED ONLY WHILE IT IS STILL
    # TRUE. When the Excel sync has since edited this ledger row (same id, new
    # quantity or tank), the attribution was measured against figures that no
    # longer exist, and the field is asked again — the operator's requirement.
    # When the sync left the row alone, the answer stands and is NOT re-asked.
    existing = (await session.execute(text(
        f'SELECT l.*, {STALE_SQL} AS stale FROM sme_consumption_log l '
        f'JOIN consumption c ON c."id" = l."Consumption_ID" '
        f'WHERE l."Consumption_ID" = :cid ORDER BY l."id" LIMIT 1'),
        {"cid": consumption_id})).mappings().first()
    # ⚠️ 2026-09-17: …AND A REJECTED ONE IS ALWAYS OPEN TO CORRECTION. The HOD's
    # rejection sent it back to the field; refusing the resubmission would make
    # the rejection terminal again by another route.
    if (existing is not None and not existing["stale"]
            and existing["status"] != "rejected"):
        raise HTTPException(
            409, f"that consumption has already been attributed (row "
                 f"{existing['id']}), and its ledger row has not changed since.")

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
    from . import prep as PR
    if not await PR.pair_ok(session, site_id=row["Site_ID"], tag=tag, code=code):
        raise HTTPException(
            422, await PR.pair_message(session, site_id=row["Site_ID"], tag=tag, code=code)
            or f"{tag} does not carry system code {code} at "
               f"{row['Site_ID']}. Pick a tag from the list — it is filtered "
               f"to the equipment that system actually applies to.")

    sap = sap_norm(row["SAP_Code"])
    # ⚠️ PHASE 15d — GARNET IS SURFACE PREPARATION. It is benchmarked per
    # surface (Old / New), under its own prep code, and never inside a lining
    # system's recipe — so each side refuses the other's material.
    prep = await PR.is_prep(session, code)
    state = PR.norm_state(surface_state) if prep else None
    garnet = sap in await PR.garnet_saps(session)
    if prep and not garnet:
        raise HTTPException(
            422, f"{code} is surface preparation (Garnet). SAP {sap} is not Garnet — "
                 f"attribute it to its lining system instead.")
    if garnet and not prep:
        raise HTTPException(
            422, f"SAP {sap} is Garnet — surface preparation, not part of {code}'s "
                 f"lining. Submit it on its Garnet card, answering Old or New surface.")
    if prep and state is None:
        raise HTTPException(
            422, "Garnet is benchmarked per surface: say whether this was an OLD "
                 "surface or a NEW surface.")
    material_code = await _resolve_material_code(session, sap)
    rate = await recipe_rate(session, code=code, material_code=material_code,
                             sap_code=sap, surface_state=state)
    # ⚠️ PHASE 14a — DEFECT D1. The ledger holds PACKS (cans, bags); the recipe
    # rate is BASE units per m². `actual` used to be the pack count, so a
    # 4.5-can draw of a 9 kg adhesive (40.5 kg) was measured as "4.5" against a
    # kilogram benchmark. It is converted in ONE place (services/units.py) and
    # the pack count and factor are snapshotted beside it.
    uinfo = await _units(session, sap)
    pack = float(row["Quantity"] or 0)
    actual = units_base(pack, uinfo)
    if prep and str(uinfo.get("base_uom") or "").strip().upper() != "KG":
        # The benchmark is KG per m²; a TON counted as 1 would read as 0.1 % of
        # the benchmark. Refused, like any unknown factor (defect D1).
        raise HTTPException(
            422, f"Garnet SAP {sap} is counted in {uinfo.get('pack_uom') or 'packs'} "
                 f"with Unit Size {uinfo.get('unit_size')}, so its quantity is not in KG "
                 f"and cannot be compared with the KG/m² benchmark. Set its Unit Size "
                 f"to 1000 (kg per TON) in the Inventory sheet and re-run the Excel sync.")
    expected = None if rate is None else round(rate * sqm, 4)
    var = variance_pct(actual, expected)
    tol = await tolerance_pct(session)
    flag = classify(var, tol)
    fp = await source_fingerprint(session, consumption_id)

    if existing is not None:
        return await _reassign(
            session, existing=dict(existing), consumption_id=consumption_id,
            code=code, tag=tag, sqm=sqm, actual=actual, expected=expected,
            var=var, rate=rate, flag=flag, tol=tol, fp=fp, notes=notes,
            username=username, pack=pack, unit_size=uinfo["factor"],
            surface_state=state)

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
        Pack_Qty=pack,
        Unit_Size_Used=uinfo["factor"],
        Surface_State=state,
        Variance_Pct=var,
        Bench_For_1_SQM=rate,
        Priority_Flag=flag,
        Variance_Tolerance_Pct=tol,
        Source_Fingerprint=fp,
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
            "Surface_State": state,
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
        # ⚠️ 2026-09-17: a pending row that carries a rejection came BACK — the
        # field corrected what the HOD sent back. Said explicitly so the HOD
        # reads it as a second look, with their own earlier reason beside it.
        i["resubmitted_after_rejection"] = (i.get("status") == "staged"
                                            and i.get("rejected_at") is not None)
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
                 site_id: Optional[str], credit: bool = True,
                 notify: bool = True) -> dict:
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

    # ⚠️ 2026-09-16: NOT AGAINST FIGURES THAT NO LONGER EXIST. If the Excel sync
    # edited the ledger row after this was filed, its variance was measured
    # against a quantity the ledger no longer holds. Approving it would credit
    # an area and publish a variance for a draw that did not happen that way.
    stale = (await session.execute(text(
        f'SELECT {STALE_SQL} FROM sme_consumption_log l '
        f'JOIN consumption c ON c."id" = l."Consumption_ID" WHERE l."id" = :i'),
        {"i": log_id})).scalar()
    if approve and stale:
        raise HTTPException(
            409, "the consumption behind this was EDITED in the Excel workbook "
                 "after it was filed, so its figures are out of date. It is back "
                 "in the field's queue marked Edited — approve it once it has "
                 "been re-assigned against the new figures.")

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
        # ⚠️ 2026-09-17: A REJECTION BOUNCES BACK, and the person who filed it
        # is told why. It is back at the top of their queue — a rejection
        # nobody hears about is a row that sits there anyway.
        if notify:
            await _notify_submitter(
                session, username=row["entered_by"], site_id=row["Site_ID"],
                log_id=log_id, reason=reason, actor=username)
        return {"id": log_id, "status": "rejected", "reason": reason,
                "bounced_back_to": row["entered_by"]}

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
    from . import prep as PR
    if not await PR.pair_ok(session, site_id=row["Site_ID"], tag=tag, code=code):
        raise HTTPException(
            422, await PR.pair_message(session, site_id=row["Site_ID"], tag=tag, code=code)
            or f"{tag} does not carry system code {code} at {row['Site_ID']}.")

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
                                     sap_code=row["SAP_Code"],
                                     surface_state=row.get("Surface_State"))
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
    # ⚠️ PHASE 14c — `credit=False` when a GROUP decides: the group credits its
    # area ONCE (services/sme_groups.decide_group). Crediting here per member
    # is defect D3 — a four-component job credited four times.
    if credit:
        await X.credit_done_sqm(session, site_id=row["Site_ID"], tag=tag,
                                code=code, sqm=sqm)

    await write_audit(session, username, "SME_LINK_APPROVE",
                      "sme_consumption_log",
                      f"id={log_id} {tag}/{code} sqm={sqm:g}"
                      + (f" (was {float(row['SQM_Completed'] or 0):g}; "
                         f"{justification.strip()[:120]})" if edited else ""))
    return {"id": log_id, "status": "committed", "Lining_System_Code": code,
            "Equipment_Tag_No": tag, "SQM_Completed": sqm,
            "hod_edited": edited,
            # a prep (Garnet) row credits no area — credit_done_sqm refuses it
            "Done_SQM_credited": sqm if credit and not await PR.is_prep(session, code) else 0.0}


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


# ══════════════════════════════════════════════════════════════════════════
# 2026-09-16 — consumption EDITED in Excel after it was attributed
# ══════════════════════════════════════════════════════════════════════════
#
# The operator's requirement, in their order:
#
#   1. the Excel sync changes a value that was already attributed →
#      the row comes back to the field, HIGHLIGHTED as edited;
#   2. the field assigns system code, equipment and SQM again;
#   3. the HOD is notified and decides;
#   4. on approval THE OLD ENTRY IS UPDATED with the new values — not a second
#      attribution beside it;
#   5. a sync that pushes the same values as before asks nothing again.
#
# (5) is the fingerprint's job. (1)–(4) are below, and they split on whether an
# HOD had already approved the attribution, because approval is what made its
# figures count.

async def _notify_hod(session: AsyncSession, *, site_id: str, log_id: int,
                      title: str, body: str, username: str) -> None:
    """Bell (and best-effort WhatsApp) to the site's HODs. Never fatal."""
    from .notifications import dispatch
    try:
        await dispatch(session, event_key="sme_link_edited", title=title,
                       body=body, recipient_role="hod", recipient_site=site_id,
                       link_page="/execution",
                       related_table="sme_consumption_log",
                       related_ref=str(log_id), created_by=username)
    except Exception:                                     # noqa: BLE001
        # A messaging failure must never undo the field's submission — the
        # same contract `dispatch` itself keeps for WhatsApp.
        pass


async def _notify_submitter(session: AsyncSession, *, username: Optional[str],
                            site_id: str, log_id: int, reason: str,
                            actor: str) -> None:
    """Tell the person who filed an assignment that the HOD sent it back.

    Addressed to that USER, not a role: the correction is theirs to make, and
    a bell to every supervisor on site would be read by nobody in particular.
    Never fatal — a messaging failure must not undo the HOD's decision.
    """
    if not username:
        return
    from .notifications import dispatch
    try:
        await dispatch(session, event_key="sme_link_rejected",
                       title="Assignment rejected — needs correction",
                       body=f"The HOD sent a Surface Shield assignment back: "
                            f"{reason[:300]}. It is at the top of your queue.",
                       recipient_user=username, recipient_site=site_id,
                       link_page="/execution",
                       related_table="sme_consumption_log",
                       related_ref=str(log_id), created_by=actor)
    except Exception:                                     # noqa: BLE001
        pass


async def _reassign(session: AsyncSession, *, existing: dict,
                    consumption_id: int, code: str, tag: str, sqm: float,
                    actual: float, expected: Optional[float],
                    var: Optional[float], rate: Optional[float], flag: str,
                    tol: float, fp: Optional[str], notes: Optional[str],
                    username: str, pack: Optional[float] = None,
                    unit_size: Optional[float] = None,
                    surface_state: Optional[str] = None) -> dict:
    """The field's new answer for a consumption the Excel sync edited.

    ⚠️ NOT YET APPROVED (staged, or rejected) → THE ATTRIBUTION IS UPDATED IN
    PLACE and stays with the HOD. Nothing it said has counted yet, so there is
    nothing to protect, and a second row would give the HOD two proposals for
    one drum.

    ⚠️ ALREADY APPROVED (committed) → A REVISION IS STAGED BESIDE IT. The
    approved figures have already credited `Done_SQM` and count in
    `Consumed_Qty`; until an HOD approves the new ones, the old ones stand —
    approval is what makes a number count, and that does not stop being true
    because a spreadsheet changed. `decide_revision` then updates THIS SAME
    ROW in place and moves the progress credit by the difference.
    """
    log_id = int(existing["id"])
    site = existing["Site_ID"]

    if existing["status"] in ("staged", "rejected"):
        bounced = existing["status"] == "rejected"
        await session.execute(update(log_t).where(log_t.c["id"] == log_id).values(
            Equipment_Tag_No=tag, Lining_System_Code=code, SQM_Completed=sqm,
            Expected_Qty=expected or 0.0, Actual_Qty=actual, Variance_Pct=var,
            Pack_Qty=pack, Unit_Size_Used=unit_size, Surface_State=surface_state,
            Bench_For_1_SQM=rate, Priority_Flag=flag,
            Variance_Tolerance_Pct=tol, Source_Fingerprint=fp,
            notes=(notes if notes is not None else existing.get("notes")),
            entered_by=username,
            # ⚠️ BACK TO THE HOD. `staged` is the PENDING state this workflow
            # has always used — the one `decide` accepts and `assigned` lists.
            status="staged",
            # ⚠️ THE LAST REJECTION IS KEPT, deliberately. `status` alone says
            # where the row is; `rejected_reason` / `rejected_at` beside a
            # `staged` status tell the HOD "you sent this back once, and why",
            # which is the first thing they need when it reappears. Nothing
            # reads them as a state — every reader keys on `status`.
            hod_username=None, hod_decided_at=None))
        await write_audit(
            session, username,
            "SME_LINK_RESUBMIT" if bounced else "SME_LINK_REASSIGN",
            "sme_consumption_log",
            f"id={log_id} consumption={consumption_id} "
            f"{'rejected (' + str(existing.get('rejected_reason') or '') + ')' if bounced else 'edited in Excel'}; was "
            f"{existing['Equipment_Tag_No']}/{existing['Lining_System_Code']} "
            f"sqm={float(existing['SQM_Completed'] or 0):g} "
            f"qty={float(existing['Actual_Qty'] or 0):g} → {tag}/{code} "
            f"sqm={sqm:g} qty={actual:g} [{flag}]")
        await _notify_hod(
            session, site_id=site, log_id=log_id,
            title=("Rejected assignment corrected and resubmitted" if bounced
                   else "Edited consumption re-assigned"),
            body=(f"A Surface Shield assignment you rejected ("
                  f"{existing.get('rejected_reason') or 'no reason recorded'}) "
                  f"was corrected to {tag} / {code}, {sqm:g} m² — review it."
                  if bounced else
                  f"A Surface Shield consumption changed in Excel was re-assigned "
                  f"to {tag} / {code}, {sqm:g} m² — review it."),
            username=username)
        return {"id": log_id, "Consumption_ID": consumption_id,
                "status": "staged", "revision": None, "reassigned": True,
                "resubmitted_after_rejection": bounced,
                "Lining_System_Code": code, "Equipment_Tag_No": tag,
                "SQM_Completed": sqm, "Actual_Qty": actual,
                "Expected_Qty": expected, "Variance_Pct": var,
                "Bench_For_1_SQM": rate, "Priority_Flag": flag,
                "Variance_Tolerance_Pct": tol}

    vals = dict(
        Log_ID=log_id, Consumption_ID=consumption_id, Site_ID=site,
        Prev_Lining_System_Code=existing["Lining_System_Code"],
        Prev_Equipment_Tag_No=existing["Equipment_Tag_No"],
        Prev_SQM_Completed=existing["SQM_Completed"],
        Prev_Actual_Qty=existing["Actual_Qty"],
        Prev_Variance_Pct=existing["Variance_Pct"],
        Prev_Source_Fingerprint=existing.get("Source_Fingerprint"),
        Lining_System_Code=code, Equipment_Tag_No=tag, SQM_Completed=sqm,
        Actual_Qty=actual, Expected_Qty=expected, Variance_Pct=var,
        Pack_Qty=pack, Unit_Size_Used=unit_size,
        Bench_For_1_SQM=rate, Priority_Flag=flag, Variance_Tolerance_Pct=tol,
        Source_Fingerprint=fp or "", notes=notes, submitted_by=username,
        status="staged")
    staged_id = (await session.execute(
        select(rev_t.c["id"]).where(rev_t.c["Log_ID"] == log_id,
                                    rev_t.c["status"] == "staged"))).scalar()
    if staged_id:
        # ONE staged revision per attribution: a resubmission replaces it.
        await session.execute(update(rev_t).where(rev_t.c["id"] == staged_id)
                              .values(**vals, submitted_at=func.now()))
        rev_id = int(staged_id)
    else:
        rev_id = (await session.execute(insert(rev_t).values(**vals)
                  .returning(rev_t.c["id"]))).scalar_one()
    await write_audit(
        session, username, "SME_LINK_REVISION", "sme_consumption_revision",
        f"rev={rev_id} log={log_id} consumption={consumption_id} edited in "
        f"Excel after approval; approved {existing['Equipment_Tag_No']}/"
        f"{existing['Lining_System_Code']} sqm="
        f"{float(existing['SQM_Completed'] or 0):g} qty="
        f"{float(existing['Actual_Qty'] or 0):g} stands until the HOD decides "
        f"→ proposed {tag}/{code} sqm={sqm:g} qty={actual:g} [{flag}]")
    await _notify_hod(
        session, site_id=site, log_id=log_id,
        title="Approved consumption changed in Excel",
        body=f"An approved Surface Shield attribution was edited in the workbook "
             f"and re-assigned ({tag} / {code}, {sqm:g} m²). The approved "
             f"figures stand until you approve the new ones.",
        username=username)
    return {"id": log_id, "Consumption_ID": consumption_id,
            "status": "committed", "revision": rev_id, "reassigned": True,
            "Lining_System_Code": code, "Equipment_Tag_No": tag,
            "SQM_Completed": sqm, "Actual_Qty": actual,
            "Expected_Qty": expected, "Variance_Pct": var,
            "Bench_For_1_SQM": rate, "Priority_Flag": flag,
            "Variance_Tolerance_Pct": tol}


async def decide_revision(session: AsyncSession, *, rev_id: int, approve: bool,
                          edits: Optional[dict], justification: str,
                          reject_reason: str, username: str,
                          site_id: Optional[str]) -> dict:
    """HOD: approve a revision onto its attribution, or reject it.

    ⚠️ APPROVAL UPDATES THE ORIGINAL ATTRIBUTION IN PLACE — the operator's
    requirement — and moves the progress credit by the DIFFERENCE. The old
    area is taken back off the (tag, system) it was credited to and the new
    area credited, both through `execution.credit_done_sqm`, so a revision that
    keeps the tag and grows 40 → 55 m² moves `Done_SQM` by exactly +15, and one
    that moves the work to another tank moves the 40 with it.

    ⚠️ REJECTION LEAVES THE APPROVED FIGURES STANDING — and the row, still out
    of date with its ledger row, goes straight back to the field with the
    reason attached. A rejected revision is not the end of the question; the
    workbook still says something the attribution does not.

    The quantity is refused here exactly as on `decide`: the drum left the
    shelf when it was issued.
    """
    from . import execution as X

    rev = (await session.execute(
        select(rev_t).where(rev_t.c["id"] == rev_id))).mappings().first()
    if rev is None or (site_id is not None and rev["Site_ID"] != site_id):
        raise HTTPException(404, f"revision {rev_id} not found")
    if rev["status"] != "staged":
        raise HTTPException(409, f"revision {rev_id} is already {rev['status']}.")
    log = (await session.execute(
        select(log_t).where(log_t.c["id"] == rev["Log_ID"]))).mappings().first()
    if log is None or log["status"] != "committed":
        raise HTTPException(409, "the attribution this revises is no longer an "
                                 "approved one.")

    edits = dict(edits or {})
    bad = [k for k in edits if k in REFUSED]
    if bad:
        raise HTTPException(
            422, f"{', '.join(sorted(bad))} cannot be edited here. The material "
                 f"left the shelf when it was issued, so this approval settles "
                 f"the area and the explanation, not the quantity. A physical "
                 f"correction is a stock adjustment.")
    unknown = [k for k in edits if k not in EDITABLE]
    if unknown:
        raise HTTPException(422, f"{', '.join(sorted(unknown))} is not something "
                                 f"this screen edits. Editable: "
                                 f"{', '.join(EDITABLE)}.")

    if not approve:
        reason = (reject_reason or "").strip()
        if not reason:
            raise HTTPException(422, "a rejection needs a reason — the field has "
                                     "to know what to do differently.")
        await session.execute(update(rev_t).where(rev_t.c["id"] == rev_id).values(
            status="rejected", rejected_reason=reason, hod_username=username,
            hod_decided_at=func.now()))
        await write_audit(session, username, "SME_LINK_REVISION_REJECT",
                          "sme_consumption_revision",
                          f"rev={rev_id} log={rev['Log_ID']} — {reason[:160]}")
        await _notify_submitter(
            session, username=rev["submitted_by"], site_id=rev["Site_ID"],
            log_id=int(rev["Log_ID"]), reason=reason, actor=username)
        return {"id": rev_id, "status": "rejected", "reason": reason,
                "bounced_back_to": rev["submitted_by"]}

    # ⚠️ THE REVISION MUST STILL DESCRIBE THE LEDGER ROW. If the workbook was
    # edited AGAIN after the field re-assigned, this proposal is itself stale.
    now_fp = await source_fingerprint(session, int(rev["Consumption_ID"]))
    if now_fp != rev["Source_Fingerprint"]:
        raise HTTPException(
            409, "the consumption changed in Excel again after this was "
                 "re-assigned, so these figures are already out of date. It is "
                 "back in the field's queue.")

    edited = bool(edits)
    if edited and not (justification or "").strip():
        raise HTTPException(422, "changing a filed figure needs a written reason.")

    code = str(edits.get("Lining_System_Code") or rev["Lining_System_Code"]).strip()
    tag = str(edits.get("Equipment_Tag_No") or rev["Equipment_Tag_No"]).strip()
    sqm = float(edits.get("SQM_Completed", rev["SQM_Completed"]) or 0)
    if sqm <= 0:
        raise HTTPException(422, "the area covered must be greater than zero. "
                                 "Reject the revision instead if the material "
                                 "was not applied.")
    from . import prep as PR
    if not await PR.pair_ok(session, site_id=rev["Site_ID"], tag=tag, code=code):
        raise HTTPException(
            422, await PR.pair_message(session, site_id=rev["Site_ID"], tag=tag, code=code)
            or f"{tag} does not carry system code {code} at {rev['Site_ID']}.")

    rate, expected, var, flag = (rev["Bench_For_1_SQM"], rev["Expected_Qty"],
                                 rev["Variance_Pct"], rev["Priority_Flag"])
    if edited:
        if code != rev["Lining_System_Code"]:
            rate = await recipe_rate(session, code=code,
                                     material_code=log["Material_Code"],
                                     sap_code=log["SAP_Code"],
                                     surface_state=log.get("Surface_State"))
        expected = None if rate is None else round(float(rate) * sqm, 4)
        var = variance_pct(float(rev["Actual_Qty"] or 0), expected)
        flag = classify(var, float(rev["Variance_Tolerance_Pct"]
                                   or DEFAULT_TOLERANCE_PCT))

    # ── move the progress credit by the difference ──────────────────────────
    # ⚠️ PHASE 14c — a row that belongs to a MULTI-material job carries the
    # job's area, and that area was credited ONCE for the whole job. Moving it
    # per row would take the job's area off once per edited material. So the
    # GROUP's credit moves, once, and only if the job's answer changed; the
    # other members take the new answer too, so the job stays one answer.
    from . import sme_groups as G
    old_sqm = float(log["SQM_Completed"] or 0)
    moved = await G.revise_group_credit(session, log=dict(log), code=code, tag=tag,
                                        sqm=sqm)
    if not moved:
        if old_sqm:
            await X.credit_done_sqm(session, site_id=log["Site_ID"],
                                    tag=log["Equipment_Tag_No"],
                                    code=log["Lining_System_Code"], sqm=-old_sqm)
        await X.credit_done_sqm(session, site_id=log["Site_ID"], tag=tag, code=code,
                                sqm=sqm)

    # ── the ORIGINAL attribution takes the new values ───────────────────────
    await session.execute(update(log_t).where(log_t.c["id"] == log["id"]).values(
        Lining_System_Code=code, Equipment_Tag_No=tag, SQM_Completed=sqm,
        Actual_Qty=rev["Actual_Qty"], Expected_Qty=expected or 0.0,
        Pack_Qty=rev.get("Pack_Qty"), Unit_Size_Used=rev.get("Unit_Size_Used"),
        Variance_Pct=var, Bench_For_1_SQM=rate, Priority_Flag=flag,
        Variance_Tolerance_Pct=rev["Variance_Tolerance_Pct"],
        Source_Fingerprint=rev["Source_Fingerprint"],
        notes=(edits.get("notes") if "notes" in edits else
               (rev["notes"] if rev["notes"] is not None else log["notes"])),
        hod_username=username, hod_decided_at=func.now(),
        committed_at=func.now(),
        hod_edited=edited or bool(log["hod_edited"]),
        HOD_Edit_Justification=((justification or "").strip() or
                                log["HOD_Edit_Justification"])))
    if moved:
        # every member of the job — this one included — measured against the
        # job's ONE answer, same-SAP draws sharing one expectation.
        await G.reshare_committed(session, group_id=int(log["group_id"]), sqm=sqm)
    await session.execute(update(rev_t).where(rev_t.c["id"] == rev_id).values(
        status="approved", hod_username=username, hod_decided_at=func.now(),
        hod_edited=edited,
        HOD_Edit_Justification=(justification or "").strip() or None,
        Lining_System_Code=code, Equipment_Tag_No=tag, SQM_Completed=sqm,
        Expected_Qty=expected, Variance_Pct=var, Bench_For_1_SQM=rate,
        Priority_Flag=flag))
    await write_audit(
        session, username, "SME_LINK_REVISION_APPROVE", "sme_consumption_log",
        f"log={log['id']} rev={rev_id}: {log['Equipment_Tag_No']}/"
        f"{log['Lining_System_Code']} sqm={old_sqm:g} qty="
        f"{float(log['Actual_Qty'] or 0):g} → {tag}/{code} sqm={sqm:g} qty="
        f"{float(rev['Actual_Qty'] or 0):g}")
    return {"id": rev_id, "log_id": int(log["id"]), "status": "approved",
            "Lining_System_Code": code, "Equipment_Tag_No": tag,
            "SQM_Completed": sqm, "Actual_Qty": rev["Actual_Qty"],
            "Done_SQM_moved": {"debited": old_sqm, "credited": sqm}}


async def staged_revisions(session: AsyncSession, *,
                           site_id: Optional[str]) -> list[dict]:
    """Revisions awaiting the HOD, High Priority first — with before/after."""
    params: dict = {}
    where = "v.status = 'staged'"
    if site_id is not None:
        where += ' AND v."Site_ID" = :site'
        params["site"] = site_id
    rows = (await session.execute(text(
        'SELECT v.*, l."entry_date", l."Material_Code", l."SAP_Code", '
        f'       l."entered_by", ({SOURCE_FP_SQL}) <> v."Source_Fingerprint" '
        '         AS stale '
        'FROM sme_consumption_revision v '
        'JOIN sme_consumption_log l ON l."id" = v."Log_ID" '
        'JOIN consumption c ON c."id" = v."Consumption_ID" '
        f'WHERE {where} '
        "ORDER BY CASE WHEN v.\"Priority_Flag\" = 'HIGH' THEN 0 ELSE 1 END, "
        '         l."entry_date" ASC, v."id" ASC'), params)).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("submitted_at", "hod_decided_at"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        out.append(d)
    return out


async def stale_attributions(session: AsyncSession, *,
                             consumption_ids: list[int]) -> list[dict]:
    """Attributions the ledger rows `consumption_ids` have just made out of date.

    For the Excel sync's report and its notification. Read-only.
    """
    if not consumption_ids:
        return []
    rows = (await session.execute(text(
        'SELECT l."id", l."Consumption_ID", l."Site_ID", l."status", '
        '       l."Equipment_Tag_No", l."Lining_System_Code", l."Actual_Qty", '
        '       c."Quantity" AS now_qty '
        'FROM sme_consumption_log l '
        'JOIN consumption c ON c."id" = l."Consumption_ID" '
        'WHERE l."Consumption_ID" = ANY(:ids) '
        "  AND l.\"status\" IN ('staged', 'committed') "
        f' AND {STALE_SQL}'), {"ids": list(consumption_ids)})).mappings().all()
    return [dict(r) for r in rows]


async def notify_sync_edits(session: AsyncSession, *, stale: list[dict],
                            username: str) -> int:
    """One bell per site: 'N attributed consumption rows changed in Excel'.

    One message per SYNC, not per row — a re-sync that edits forty rows is one
    event to an HOD, and forty identical bells is how a notification stops
    being read.
    """
    by_site: dict[str, list[dict]] = {}
    for s in stale:
        by_site.setdefault(s["Site_ID"], []).append(s)
    for site, rows in by_site.items():
        approved = sum(1 for r in rows if r["status"] == "committed")
        await _notify_hod(
            session, site_id=site, log_id=int(rows[0]["id"]),
            title="Excel sync changed attributed consumption",
            body=(f"{len(rows)} Surface Shield consumption row(s) that already "
                  f"had a system code, equipment and area were edited in the "
                  f"workbook ({approved} of them approved). They are back in the "
                  f"field's queue marked Edited; approved figures stand until "
                  f"you approve the re-assignment."),
            username=username)
    return len(by_site)
