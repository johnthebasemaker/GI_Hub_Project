"""
backend/api/services/execution.py — the consumption workflow. PAPER FIRST.

    DRAFT_SUPERVISOR ─→ PENDING_SK ─→ PENDING_HOD ─→ APPROVED
                                                  └─→ REJECTED (terminal)

⚠️ THE DIRECTION REVERSED IN PHASE 9d, AND SO DID WHO MAY EDIT A NUMBER.
Phase 5 ran SK → supervisor → HOD and forbade the supervisor from touching a
material line, on the reasoning that "a supervisor whose numbers look bad has
both the motive and the opportunity to adjust the consumption they are being
measured against". That reasoning has not stopped being true. What changed is
where the record starts: the supervisor fills a printed form in the field and
photographs it, so they now AUTHOR the quantities and the store keeper VERIFIES
them.

THE REPLACEMENT CONTROL IS FOUR LAYERS, NOT ONE. The operator asked for the
SK's edits in red so an HOD can see the store keeper altered the supervisor's
claim. That is half of it. Nothing in that scheme shows what the SUPERVISOR
changed from what the camera read — so a supervisor could overwrite the
machine's reading of their own handwriting and no one could tell. Every material
line therefore carries:

    OCR_Qty          what the camera saw          grey    never editable
    Supervisor_Qty   what the supervisor filed    amber   when != OCR_Qty
    SK_Qty           what the store keeper set    red     when != Supervisor_Qty
    Actual_Qty       what the HOD settled         purple  when != SK_Qty

Four numbers, four owners, four timestamps. The colour is the UI's business;
the chain is this module's.

⚠️ APPROVAL NOW MOVES STOCK, WHICH IT NEVER DID BEFORE (ruling Q1-b).
Phase 5's `post_progress` posted AREA to `sme_sqm_progress` and nothing else;
material left the shelf on the separate `pending_issues → consumption` path.
From 9d this entry is the ONLY way lining material is deducted, and
`post_stock()` writes the `consumption` rows. `Consumption_ID` per line makes
that idempotent: a second approval finds the row already posted rather than
deducting twice. That is the single highest-severity risk in this phase and the
reason the guard is a stored id rather than a status check.

⚠️ THE QSEP GATE BLOCKS BY DEFAULT AND CAN BE OVERRIDDEN (ruling Q2-D).
MTC and QC clearance used to be enforced before material left the store. On a
paper-first flow the drum was emptied days ago, so a hard block prevents
nothing — it only strands the record, and stock then silently overstates. The
gate therefore refuses by default, and an HOD may override it explicitly at the
price of a written reason and a notification to the Head of Qualities. An
uncertified application becomes a visible, attributed exception instead of an
invisible one.

⚠️ REJECTION IS TERMINAL AND THERE IS NO BOUNCE-BACK (ruling Q4). An entry that
is wrong is rejected and raised again from a fresh form. Bounce-back loops were
considered and declined: they multiply the states without adding a decision
anybody actually makes, and the paper has to be re-photographed either way.

⚠️ THE MANPOWER-ONLY BYPASS SURVIVES. Blasting and buffing consume no Surface
Shield, so there is no form to fill and no material to verify. A supervisor
opens those entries directly and they skip the SK step entirely — a store
keeper's queue must not fill with entries that have nothing for them to check.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .ledger import _MD, write_audit
from .notifications import dispatch

entry_t = _MD.tables["sme_execution_entry"]
mat_t = _MD.tables["sme_execution_entry_material"]
man_t = _MD.tables["sme_execution_entry_manpower"]
norm_t = _MD.tables["sme_manpower_norm"]
recipe_t = _MD.tables["sme_recipe"]
prep_t = _MD.tables["sme_surface_prep_progress"]
sqm_progress_t = _MD.tables["sme_sqm_progress"]
# Phase 9d: approval writes here now. See post_stock().
cons_t = _MD.tables["consumption"]

DRAFT_SUPERVISOR = "DRAFT_SUPERVISOR"
PENDING_SK = "PENDING_SK"
PENDING_HOD = "PENDING_HOD"
APPROVED = "APPROVED"
REJECTED = "REJECTED"

# ⚠️ RETIRED, NOT DELETED (ruling Q3). No new entry ever enters these, and the
# 9d migration rejected any that were sitting in them. The names stay so that a
# historical row still renders with a label instead of a raw string, and so
# that `assert_transition` can give it a reason rather than a lookup miss.
DRAFT_SK = "DRAFT_SK"
PENDING_SUPERVISOR = "PENDING_SUPERVISOR"
RETIRED_STATES = frozenset({DRAFT_SK, PENDING_SUPERVISOR})

# Every legal move. Written as data so an illegal one is a lookup miss with a
# readable message, not an `if` somebody forgets to add.
#
# ⚠️ NOTHING LEAVES REJECTED. Ruling Q4: a wrong entry is rejected and raised
# again from a fresh form. There is deliberately no edge back to an earlier
# state — see the module docstring.
TRANSITIONS = {
    DRAFT_SUPERVISOR: {PENDING_SK, PENDING_HOD, REJECTED},
    PENDING_SK: {PENDING_HOD, REJECTED},
    PENDING_HOD: {APPROVED, REJECTED},
    APPROVED: set(),
    REJECTED: set(),
    # Drained. Present so a stranded historical row gets an explanation.
    DRAFT_SK: set(),
    PENDING_SUPERVISOR: set(),
}


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def assert_transition(current: str, target: str) -> None:
    if target in TRANSITIONS.get(current, set()):
        return
    if current in RETIRED_STATES:
        raise HTTPException(
            409, f"{current} belongs to the store-keeper-first workflow, which "
                 f"was replaced on 2026-08-27. This entry cannot be advanced; "
                 f"raise a new one from a printed consumption form.")
    raise HTTPException(409, f"an entry in {current} cannot move to "
                             f"{target} (allowed: "
                             f"{sorted(TRANSITIONS.get(current, set())) or 'nothing — it is final'})")


async def is_manpower_only(session: AsyncSession, code: str, esc: str) -> bool:
    """True when no Surface Shield recipe exists for this system + sub-activity.

    Computed, never stored: it is a fact ABOUT the recipe table and would go
    stale the moment a recipe line is added. A system-agnostic entry (code '')
    can never have a recipe, so it is manpower-only by construction.
    """
    if not (code or "").strip():
        return True
    n = (await session.execute(
        select(func.count()).select_from(recipe_t)
        .where(recipe_t.c["Lining_System_Code"] == code,
               recipe_t.c["Execution_Sub_Activity_Code"] == esc))).scalar_one()
    return n == 0


async def next_entry_no(session: AsyncSession, site_id: str) -> str:
    """EXE-YYYYMMDD-N, site-scoped.

    Scoped to the site on purpose: an unscoped sequence is a date plus a small
    integer, which lets anyone holding one number enumerate every other site's
    entries by guessing neighbours.
    """
    day = _dt.date.today().strftime("%Y%m%d")
    prefix = f"EXE-{day}-"
    n = (await session.execute(
        select(func.count()).select_from(entry_t)
        .where(entry_t.c["Site_ID"] == site_id,
               entry_t.c["Entry_No"].like(prefix + "%")))).scalar_one()
    return f"{prefix}{int(n) + 1}"


def compute_variance(entry: dict, materials: list[dict],
                     manpower: list[dict]) -> dict:
    """Actual vs the SNAPSHOTTED benchmark. Never re-reads master data.

    Material — each line's expectation is `Bench_For_1_SQM x Actual_SQM`.
    Manpower — the benchmark is stated per shift, so:
        shifts   = Actual_SQM / Bench_Productivity_Per_Shift
        expected = shifts x Bench_Manhours_Per_Shift
    Actual man-hours are `headcount x hours` summed, kept unmultiplied in the
    table so a corrected headcount cannot silently carry the old hours.

    A benchmark of zero yields `None`, not infinity and not 0%: "we cannot
    compare this" and "this matched perfectly" must never render the same.
    """
    sqm = float(entry.get("Actual_SQM") or 0.0)

    mat_lines, mat_exp_total, mat_act_total = [], 0.0, 0.0
    for m in materials:
        per = m.get("Bench_For_1_SQM")
        act = float(m.get("Actual_Qty") or 0.0)
        exp = (float(per) * sqm) if per is not None else None
        pct = (round((act - exp) / exp * 100.0, 2)
               if exp not in (None, 0) else None)
        mat_lines.append({
            "Material_Code": m.get("Material_Code"),
            "SAP_Code": m.get("SAP_Code"),
            "UOM": m.get("UOM"),
            "Actual_Qty": round(act, 4),
            "Benchmark_Qty": None if exp is None else round(exp, 4),
            "Variance_Qty": None if exp is None else round(act - exp, 4),
            "Variance_Pct": pct,
        })
        if exp is not None:
            mat_exp_total += exp
            mat_act_total += act

    prod = entry.get("Bench_Productivity_Per_Shift")
    mh_shift = entry.get("Bench_Manhours_Per_Shift")
    exp_mh = None
    if prod not in (None, 0) and mh_shift is not None:
        exp_mh = (sqm / float(prod)) * float(mh_shift)
    act_mh = sum(float(r.get("Headcount") or 0.0) * float(r.get("Hours") or 0.0)
                 for r in manpower)
    act_heads = sum(float(r.get("Headcount") or 0.0) for r in manpower)

    return {
        "materials": mat_lines,
        "material_total": {
            "Actual": round(mat_act_total, 4),
            "Benchmark": round(mat_exp_total, 4),
            "Variance": round(mat_act_total - mat_exp_total, 4),
            "Variance_Pct": (round((mat_act_total - mat_exp_total)
                                   / mat_exp_total * 100.0, 2)
                             if mat_exp_total else None),
        },
        "manpower": {
            "Actual_Manhours": round(act_mh, 2),
            "Actual_Headcount": round(act_heads, 2),
            "Benchmark_Manhours": None if exp_mh is None else round(exp_mh, 2),
            "Benchmark_Crew_Size": entry.get("Bench_Crew_Size"),
            "Variance_Manhours": (None if exp_mh is None
                                  else round(act_mh - exp_mh, 2)),
            "Variance_Pct": (round((act_mh - exp_mh) / exp_mh * 100.0, 2)
                             if exp_mh else None),
        },
    }


async def _lines(session: AsyncSession, entry_id: int) -> tuple[list, list]:
    mats = [dict(r) for r in (await session.execute(
        select(mat_t).where(mat_t.c["Entry_ID"] == entry_id)
        .order_by(mat_t.c["Material_Code"]))).mappings().all()]
    mans = [dict(r) for r in (await session.execute(
        select(man_t).where(man_t.c["Entry_ID"] == entry_id)
        .order_by(man_t.c["Role_Code"]))).mappings().all()]
    return mats, mans


async def get_entry(session: AsyncSession, entry_id: int,
                    site_id: Optional[str]) -> dict:
    stmt = select(entry_t).where(entry_t.c["id"] == entry_id)
    if site_id is not None:
        stmt = stmt.where(entry_t.c["Site_ID"] == site_id)
    row = (await session.execute(stmt)).mappings().first()
    if row is None:
        raise HTTPException(404, "execution entry not found")
    entry = dict(row)
    mats, mans = await _lines(session, entry_id)
    entry["materials"] = mats
    entry["manpower"] = mans
    entry["variance"] = compute_variance(entry, mats, mans)
    return entry


# ─── SK: open a draft, or a supervisor opens one directly ────────────────────
async def open_entry(session: AsyncSession, *, username: str, role: str,
                     site_id: str, work_date: str, equipment_tag: str,
                     code: str, esc: str, variant: str = "",
                     materials: list[dict] | None = None,
                     origin: str = "manual", form_uuid: str | None = None,
                     shift: str | None = None) -> dict:
    """Create an entry at DRAFT_SUPERVISOR.

    ⚠️ THE SUPERVISOR OPENS IT NOW, not the store keeper. The record starts
    with the paper the supervisor filled in, so there is no earlier step for
    anybody else to perform. An SK or HOD may still open one — somebody has to
    be able to enter a form for a supervisor who is off site — and `role` is
    recorded so that a stand-in is visible rather than implied.
    """
    code = (code or "").strip()
    esc = (esc or "").strip()
    if not esc:
        raise HTTPException(422, "Execution_Sub_Activity_Code is required")
    if not (equipment_tag or "").strip():
        raise HTTPException(422, "Equipment_Tag_No is required")

    manpower_only = await is_manpower_only(session, code, esc)
    entry_no = await next_entry_no(session, site_id)
    new_id = (await session.execute(insert(entry_t).values(
        Site_ID=site_id, Entry_No=entry_no, Work_Date=str(work_date)[:10],
        Equipment_Tag_No=equipment_tag.strip(), Lining_System_Code=code,
        Execution_Sub_Activity_Code=esc, Variant_Key=(variant or "").strip(),
        status=DRAFT_SUPERVISOR, supervisor_username=username,
        Entry_Origin=origin, Form_UUID=form_uuid,
        # NULL unless the filer said which shift. Never inferred from the clock:
        # an entry is filed when somebody reaches a desk, not when the work
        # happened. See models.SmeExecutionEntry.Shift.
        Shift=(shift if shift in ("Day", "Night") else None),
        created_by=username).returning(entry_t.c["id"]))).scalar_one()

    for i, m in enumerate(materials or []):
        qty = float(m.get("Actual_Qty") or 0.0)
        await session.execute(insert(mat_t).values(
            Entry_ID=new_id, Material_Code=str(m["Material_Code"]).strip(),
            SAP_Code=str(m.get("SAP_Code") or "").strip(),
            Actual_Qty=qty, Original_Qty=qty,
            # The grey layer. Present only for a scanned entry — a hand-typed
            # one leaves it null, and `Entry_Origin` is what says which.
            OCR_Qty=m.get("OCR_Qty"), OCR_Qty_Text=m.get("OCR_Qty_Text"),
            OCR_Lot_Text=m.get("OCR_Lot_Text"),
            Row_Index=m.get("Row_Index", i),
            UOM=m.get("UOM"), Lot_No=m.get("Lot_No")))

    await write_audit(session, username, "SME_EXEC_OPEN", "sme_execution_entry",
                      f"{entry_no} {equipment_tag}/{code or '(none)'}/{esc} "
                      f"origin={origin}"
                      + (f" form={form_uuid}" if form_uuid else ""))
    return {"id": new_id, "Entry_No": entry_no, "status": DRAFT_SUPERVISOR,
            "manpower_only": manpower_only}


def _plausibility(qty: float, bench_per_sqm: Optional[float],
                  sqm: float) -> Optional[str]:
    """Whether a quantity is credible for the area reported.

    ⚠️ A GUARD AGAINST MISREAD DIGITS, NOT AGAINST PEOPLE. `4` read as `9`,
    `1` as `7`, or a lost decimal point are the failure modes of a 7B vision
    model on handwriting, and every one of them lands an order of magnitude
    away from the recipe. Real over-use rarely does. The flag is advisory and
    NEVER blocks: an unusual day is a thing that happens, and refusing it would
    teach people to write the expected number.
    """
    if not bench_per_sqm or bench_per_sqm <= 0 or sqm <= 0:
        return None
    expected = bench_per_sqm * sqm
    if expected <= 0:
        return None
    ratio = qty / expected
    if ratio >= 5.0:
        return f"{ratio:.1f}x the benchmark ({expected:.2f} expected)"
    if qty > 0 and ratio <= 0.2:
        return f"{ratio:.2f}x the benchmark ({expected:.2f} expected)"
    return None


async def supervisor_submit(session: AsyncSession, *, username: str,
                            entry_id: int, site_id: Optional[str],
                            actual_sqm: float, manpower: list[dict],
                            material_reason: str, manpower_reason: str,
                            materials: list[dict] | None = None,
                            esc: Optional[str] = None,
                            variant: Optional[str] = None) -> dict:
    """The supervisor files the form: area, crew, quantities and lots.

    ⚠️ THIS NOW ACCEPTS MATERIAL FIGURES, which Phase 5 explicitly refused. The
    supervisor is the author of the paper, so refusing their numbers would mean
    refusing the record. What replaces the old control is that their figure is
    kept as `Supervisor_Qty` FOREVER, beside what the camera read — an HOD can
    see any gap between the two, which is the thing the Phase 5 rule was
    actually protecting against.

    ⚠️ BOTH REASONS STAY MANDATORY, whatever the variance (Phase 5 ruling, still
    good). A reason demanded only past a threshold trains people to aim just
    under it, and a zero-variance entry carrying a stated reason is evidence
    the supervisor actually looked at the comparison.
    """
    entry = await get_entry(session, entry_id, site_id)
    if float(actual_sqm or 0) <= 0:
        raise HTTPException(422, "actual SQM must be greater than zero")
    if not (material_reason or "").strip():
        raise HTTPException(422, "a material variance reason is required on "
                                 "every entry, including a zero variance")
    if not (manpower_reason or "").strip():
        raise HTTPException(422, "a manpower variance reason is required on "
                                 "every entry, including a zero variance")
    if not manpower:
        raise HTTPException(422, "record the crew that did the work")

    esc = (esc or entry["Execution_Sub_Activity_Code"]).strip()
    variant = (variant if variant is not None
               else entry.get("Variant_Key") or "").strip()
    code = entry["Lining_System_Code"]

    # ⚠️ WHERE IT GOES NEXT DEPENDS ON WHETHER THERE IS ANYTHING TO VERIFY.
    # Blasting and buffing consume no Surface Shield, so a store keeper has
    # nothing to check and their queue must not fill with entries they cannot
    # action. Those skip straight to the HOD.
    target = PENDING_SK if entry["materials"] else PENDING_HOD
    assert_transition(entry["status"], target)

    # ── the benchmark snapshot ──────────────────────────────────────────────
    norm = (await session.execute(
        select(norm_t).where(
            norm_t.c["Lining_System_Code"] == code,
            norm_t.c["Execution_Sub_Activity_Code"] == esc,
            norm_t.c["Variant_Key"] == variant))).mappings().first()
    if norm is None:
        norm = (await session.execute(
            select(norm_t).where(
                norm_t.c["Execution_Sub_Activity_Code"] == esc,
                norm_t.c["Variant_Key"] == variant)
            .order_by(norm_t.c["id"]).limit(1))).mappings().first()

    snap = {}
    if norm is not None:
        snap = {
            "Norm_ID": norm["id"],
            "Bench_Crew_Size": norm["Crew_Size"],
            "Bench_Hours_Per_Shift": norm["Hours_Per_Shift"],
            "Bench_Manhours_Per_Shift": norm["Manhours_Per_Shift"],
            "Bench_Productivity_Per_Shift": norm["Standard_Productivity_Per_Shift"],
            "Bench_SQM_Per_Hour_Per_Person": norm["SQM_Per_Hour_Per_Person"],
        }

    by_id = {int(m["id"]): m for m in (materials or []) if m.get("id") is not None}

    # ⚠️ THE RECIPE IS FETCHED ONCE, NOT ONCE PER LINE. This loop used to issue
    # TWO SELECTs against `sme_recipe` for every material on the entry — the
    # exact-match lookup and its fallback — so a 30-line recipe cost up to 60
    # round trips inside a single supervisor submit, all of them reading the
    # same handful of rows. One query for the whole system code, then dictionary
    # lookups.
    #
    # The two dictionaries preserve the two original queries EXACTLY, including
    # their difference: the first keys on (sub-activity, material, SAP) and the
    # second deliberately ignores the sub-activity, which is what let a material
    # shared across sub-activities still find its benchmark. Collapsing them
    # into one lookup would silently change which number a line is measured
    # against.
    #
    # `_RECIPE_MISSING` separates "no such recipe row" from "a row whose
    # For_1_SQM is NULL". The original `.scalar()` could not tell them apart —
    # it returned None for both and fell through to the fallback for both — so
    # the two are deliberately treated the same way on the next line. The
    # sentinel is here to make that sameness a decision somebody can see, rather
    # than an accident of `dict.get` returning None for a missing key.
    _RECIPE_MISSING = object()
    exact: dict[tuple, object] = {}
    by_material: dict[str, object] = {}
    for rr in (await session.execute(
            select(recipe_t.c["Execution_Sub_Activity_Code"],
                   recipe_t.c["Material_Code"], recipe_t.c["SAP_Code"],
                   recipe_t.c["For_1_SQM"])
            .where(recipe_t.c["Lining_System_Code"] == code)
            .order_by(recipe_t.c["id"]))).all():
        exact.setdefault((rr[0], rr[1], rr[2]), rr[3])
        by_material.setdefault(rr[1], rr[3])

    for m in entry["materials"]:
        per = exact.get((esc, m["Material_Code"], m["SAP_Code"] or None),
                        _RECIPE_MISSING)
        if per is _RECIPE_MISSING or per is None:
            per = by_material.get(m["Material_Code"])
        sent = by_id.get(int(m["id"]), {})
        qty = (float(sent["Actual_Qty"]) if sent.get("Actual_Qty") is not None
               else float(m["Actual_Qty"] or 0.0))
        lot = sent.get("Lot_No", m.get("Lot_No"))
        await session.execute(update(mat_t).where(mat_t.c["id"] == m["id"])
                              .values(Bench_For_1_SQM=per,
                                      Actual_Qty=qty,
                                      Supervisor_Qty=qty,
                                      Lot_No=(str(lot).strip() if lot else None),
                                      Plausibility_Flag=_plausibility(
                                          qty, per, float(actual_sqm))))

    await session.execute(delete(man_t).where(man_t.c["Entry_ID"] == entry_id))
    bench_crew = {}
    if norm is not None:
        bench_crew = {str(r[0]): float(r[1]) for r in (await session.execute(
            select(_MD.tables["sme_manpower_norm_role"].c["Role_Code"],
                   _MD.tables["sme_manpower_norm_role"].c["Headcount"])
            .where(_MD.tables["sme_manpower_norm_role"].c["Norm_ID"]
                   == norm["id"]))).all()}
    for r in manpower:
        rc = str(r["Role_Code"]).strip()
        head = float(r.get("Headcount") or 0.0)
        hours = float(r.get("Hours") or 0.0)
        await session.execute(insert(man_t).values(
            Entry_ID=entry_id, Role_Code=rc, Headcount=head, Hours=hours,
            Bench_Headcount=bench_crew.get(rc),
            Original_Headcount=head, Original_Hours=hours))

    await session.execute(update(entry_t).where(entry_t.c["id"] == entry_id)
                          .values(status=target, Actual_SQM=float(actual_sqm),
                                  Execution_Sub_Activity_Code=esc,
                                  Variant_Key=variant,
                                  supervisor_username=username,
                                  supervisor_submitted_at=_now(),
                                  Material_Variance_Reason=material_reason.strip(),
                                  Manpower_Variance_Reason=manpower_reason.strip(),
                                  Bench_Snapshot_At=_now(), updated_at=_now(),
                                  **snap))
    await write_audit(session, username, "SME_EXEC_SUPERVISOR_SUBMIT",
                      "sme_execution_entry",
                      f"{entry['Entry_No']} sqm={actual_sqm} → {target}")
    if target == PENDING_SK:
        await dispatch(session, event_key="sme_exec_to_sk",
                       title="Consumption entry to verify",
                       body=f"{entry['Entry_No']}: {entry['Equipment_Tag_No']} "
                            f"{esc}, {actual_sqm} m² — check the quantities and "
                            f"lots against what left the store.",
                       recipient_role="store_keeper",
                       recipient_site=entry["Site_ID"], link_page="/execution",
                       related_table="sme_execution_entry",
                       related_ref=str(entry_id), created_by=username)
    else:
        await dispatch(session, event_key="sme_exec_to_hod",
                       title="Execution entry awaiting approval",
                       body=f"{entry['Entry_No']}: {entry['Equipment_Tag_No']} "
                            f"{esc}, {actual_sqm} m² reported (labour only).",
                       recipient_role="hod", recipient_site=entry["Site_ID"],
                       link_page="/hod", related_table="sme_execution_entry",
                       related_ref=str(entry_id), created_by=username)
    return await get_entry(session, entry_id, site_id)


async def sk_verify(session: AsyncSession, *, username: str, entry_id: int,
                    site_id: Optional[str], materials: list[dict] | None = None,
                    reason: str = "") -> dict:
    """The store keeper checks the supervisor's figures against the store.

    ⚠️ AN EDIT HERE IS THE POINT OF THE STEP, NOT AN EXCEPTION. The store keeper
    knows what physically left the shelf; the supervisor knows what went onto
    the wall. When they disagree, the store keeper's number is the one with a
    stock movement behind it — so they may change it, and every change is kept
    as `SK_Qty` and shown to the HOD in red.

    ⚠️ AND AN EDIT COSTS A REASON. Not because the store keeper is suspected,
    but because the HOD is about to approve a number the supervisor did not
    write, and "the store keeper changed it" without a why is not something an
    approver can weigh.
    """
    entry = await get_entry(session, entry_id, site_id)
    assert_transition(entry["status"], PENDING_HOD)

    changed: list[str] = []
    by_id = {int(m["id"]): m for m in (materials or []) if m.get("id") is not None}
    for row in entry["materials"]:
        sent = by_id.get(int(row["id"]))
        if sent is None:
            continue
        vals: dict = {}
        if sent.get("Actual_Qty") is not None:
            new_q = float(sent["Actual_Qty"])
            if abs(new_q - float(row["Actual_Qty"] or 0)) > 1e-9:
                changed.append(f"{row['Material_Code']} "
                               f"{row['Actual_Qty']} → {new_q}")
                vals.update(Actual_Qty=new_q, SK_Qty=new_q, sk_edited=True)
            else:
                vals["SK_Qty"] = new_q
        if "Lot_No" in sent:
            lot = str(sent.get("Lot_No") or "").strip() or None
            if lot != (row.get("Lot_No") or None):
                changed.append(f"{row['Material_Code']} lot "
                               f"{row.get('Lot_No') or '—'} → {lot or '—'}")
                vals.update(Lot_No=lot, sk_edited=True)
        if vals:
            await session.execute(update(mat_t).where(mat_t.c["id"] == row["id"])
                                  .values(**vals))

    if changed and not (reason or "").strip():
        raise HTTPException(
            422, "you changed " + "; ".join(changed[:3])
                 + (" and more" if len(changed) > 3 else "")
                 + " — say why. The HOD is about to approve a number the "
                   "supervisor did not write.")

    await session.execute(update(entry_t).where(entry_t.c["id"] == entry_id)
                          .values(status=PENDING_HOD, sk_username=username,
                                  sk_verified_at=_now(),
                                  sk_edited=bool(changed),
                                  SK_Edit_Reason=(reason.strip() if changed
                                                  else None),
                                  updated_at=_now()))
    await write_audit(session, username, "SME_EXEC_SK_VERIFY",
                      "sme_execution_entry",
                      f"{entry['Entry_No']}"
                      + (f" edited: {'; '.join(changed)}" if changed else " (no change)"))
    await dispatch(session, event_key="sme_exec_to_hod",
                   title="Execution entry awaiting approval",
                   body=f"{entry['Entry_No']}: {entry['Equipment_Tag_No']}"
                        + (f" — the store keeper changed {len(changed)} figure(s)"
                           if changed else " — verified unchanged"),
                   severity="warning" if changed else "info",
                   recipient_role="hod", recipient_site=entry["Site_ID"],
                   link_page="/hod", related_table="sme_execution_entry",
                   related_ref=str(entry_id), created_by=username)
    if changed:
        # The supervisor learns their figure moved BEFORE it is approved, not
        # after — otherwise the first they hear of it is a variance report.
        await dispatch(session, event_key="sme_exec_sk_edited",
                       title="The store keeper changed your figures",
                       severity="warning",
                       body=f"{entry['Entry_No']}: {'; '.join(changed[:3])}"
                            + (" …" if len(changed) > 3 else "")
                            + f" — {reason.strip()[:140]}",
                       recipient_user=entry.get("supervisor_username"),
                       recipient_role=None if entry.get("supervisor_username")
                       else "supervisor",
                       recipient_site=entry["Site_ID"], link_page="/execution",
                       related_table="sme_execution_entry",
                       related_ref=str(entry_id), created_by=username)
    return await get_entry(session, entry_id, site_id)


async def post_progress(session: AsyncSession, entry_id: int) -> dict:
    """Post an approved entry's area to the ledger it belongs on.

    ⚠️ SURFACE PREP IS NOT LINING PROGRESS. Blasting 100 m² of a tank is not
    100 m² of lining done — the surface is merely ready to be lined. A
    system-agnostic entry therefore lands on `sme_surface_prep_progress`, and
    NEVER on `sme_sqm_progress.Done_SQM`, which drives Completion_Pct,
    SQM_Achievable_Now, the shortfall and the buy list. Adding prep there would
    report a vessel as part-lined the moment it was cleaned.

    The test is the entry's OWN system code, not a lookup: an entry that stored
    '' was opened as system-agnostic and stays that way even if a recipe line
    for its sub-activity is added tomorrow.
    """
    row = (await session.execute(select(entry_t)
           .where(entry_t.c["id"] == entry_id))).mappings().first()
    if row is None:
        return {"posted": None}
    sqm = float(row["Actual_SQM"] or 0.0)
    if sqm <= 0:
        return {"posted": None}
    code = (row["Lining_System_Code"] or "").strip()

    if not code:
        activity = (await session.execute(
            select(norm_t.c["Activity"])
            .where(norm_t.c["id"] == row["Norm_ID"]))).scalar() \
            if row["Norm_ID"] else None
        existing = (await session.execute(select(prep_t).where(
            prep_t.c["Site_ID"] == row["Site_ID"],
            prep_t.c["Equipment_Tag_No"] == row["Equipment_Tag_No"],
            prep_t.c["Execution_Sub_Activity_Code"]
            == row["Execution_Sub_Activity_Code"],
            prep_t.c["Variant_Key"] == (row["Variant_Key"] or "")
        ))).mappings().first()
        if existing is None:
            await session.execute(insert(prep_t).values(
                Site_ID=row["Site_ID"], Equipment_Tag_No=row["Equipment_Tag_No"],
                Execution_Sub_Activity_Code=row["Execution_Sub_Activity_Code"],
                Variant_Key=row["Variant_Key"] or "", Activity=activity,
                Done_SQM=sqm, Entry_Count=1, Last_Entry_No=row["Entry_No"],
                updated_at=_now()))
        else:
            await session.execute(update(prep_t)
                                  .where(prep_t.c["id"] == existing["id"])
                                  .values(Done_SQM=float(existing["Done_SQM"] or 0) + sqm,
                                          Entry_Count=int(existing["Entry_Count"] or 0) + 1,
                                          Last_Entry_No=row["Entry_No"],
                                          Activity=activity or existing["Activity"],
                                          updated_at=_now()))
        return {"posted": "surface_prep", "sqm": sqm}

    # Lining work — the ordinary progress ledger.
    await credit_done_sqm(session, site_id=row["Site_ID"],
                          tag=row["Equipment_Tag_No"], code=code, sqm=sqm)
    return {"posted": "lining", "sqm": sqm}


async def credit_done_sqm(session: AsyncSession, *, site_id: str, tag: str,
                          code: str, sqm: float) -> None:
    """Add `sqm` to one (site, tag, system)'s completed area. ONE writer.

    ⚠️ EXTRACTED IN PHASE 13g BECAUSE THERE ARE NOW TWO CALLERS, AND TWO COPIES
    OF AN INCREMENT IS HOW A VESSEL GETS CREDITED TWICE. `post_progress` credits
    an approved execution entry's area; `sme_link.decide` credits an approved
    Surface Shield attribution's. Those two paths are disjoint ONLY because
    `SME_EXEC`-sourced consumption never reaches the attribution queue — and
    that disjointness is an invariant to test, not a fact to assume (suite DB).

    ⚠️ ONLY EVER INCREMENTED HERE. `Original_SQM` belongs to the equipment
    master and is not ours to set: a progress row created by this function
    carries 0, and the master supplies the denominator.
    """
    existing = (await session.execute(select(sqm_progress_t).where(
        sqm_progress_t.c["Site_ID"] == site_id,
        sqm_progress_t.c["Equipment_Tag_No"] == tag,
        sqm_progress_t.c["Lining_System_Code"] == code))).mappings().first()
    if existing is None:
        await session.execute(insert(sqm_progress_t).values(
            Site_ID=site_id, Equipment_Tag_No=tag,
            Lining_System_Code=code, Original_SQM=0.0, Done_SQM=float(sqm),
            updated_at=_now()))
    else:
        await session.execute(update(sqm_progress_t).where(
            sqm_progress_t.c["Site_ID"] == site_id,
            sqm_progress_t.c["Equipment_Tag_No"] == tag,
            sqm_progress_t.c["Lining_System_Code"] == code
        ).values(Done_SQM=float(existing["Done_SQM"] or 0) + float(sqm),
                 updated_at=_now()))


# ─── QSEP: the certificate gate, now with a way past it ──────────────────────
async def qsep_status(session: AsyncSession, entry_id: int) -> dict:
    """What would block this entry's approval, per material line.

    ⚠️ REPORTS, NEVER RAISES. The HOD screen shows this BEFORE the approve
    button is pressed, so a blocked entry is a thing you can see coming and
    chase Logistics about — not a refusal that arrives after you have decided.
    The blocking itself happens in `hod_decide`.
    """
    from . import quality

    row = (await session.execute(select(entry_t)
           .where(entry_t.c["id"] == entry_id))).mappings().first()
    if row is None:
        return {"blocked": [], "clear": True}
    mats = (await session.execute(select(mat_t)
            .where(mat_t.c["Entry_ID"] == entry_id))).mappings().all()

    blocked = []
    # Read once. The controlled category is an `app_settings` row; asking for it
    # per material added one query per line and could not return a different
    # answer within the loop.
    cat = await quality.controlled_category(session)
    for m in mats:
        sap = str(m["SAP_Code"] or "").strip()
        qty = float(m["Actual_Qty"] or 0)
        if not sap or qty <= 0:
            continue
        if not await quality.is_controlled(session, sap_code=sap, category=cat):
            continue
        for kind, fn in (
                ("MTC", lambda: quality.assert_mtc_for_issue(
                    session, sap_code=sap, site_id=row["Site_ID"],
                    actor="qsep-check")),
                ("QC", lambda: quality.assert_qc_cleared(
                    session, sap_code=sap, site_id=row["Site_ID"], qty=qty,
                    lot=(m["Lot_No"] or None), actor="qsep-check"))):
            try:
                await fn()
            except HTTPException as e:
                blocked.append({"line_id": int(m["id"]),
                                "Material_Code": m["Material_Code"],
                                "SAP_Code": sap, "gate": kind,
                                "Lot_No": m["Lot_No"],
                                "detail": str(e.detail)})
                break
    return {"blocked": blocked, "clear": not blocked}


async def post_stock(session: AsyncSession, entry_id: int, *,
                     username: str) -> dict:
    """⚠️ THIS IS WHERE LINING MATERIAL LEAVES THE SHELF (ruling Q1-b).

    Until Phase 9d nothing in this module moved a quantity: `post_progress`
    posted AREA, and material was issued on the separate
    `pending_issues → consumption` path. From 9d this entry is the ONLY writer
    for lining consumption, and the store keeper no longer raises a parallel
    issue for it. Two writers would have meant two deductions for one drum.

    ⚠️ IDEMPOTENT ON `Consumption_ID`, NOT ON STATUS. A status check ("only
    post if PENDING_HOD") is a check-then-write with a window in it; a stored
    id is a fact.

    ⚠️ `Source_Ref` KEYS ON THE ENTRY'S id, NOT ITS `Entry_No`. It follows the
    existing `SMR:<no>:<item>` shape but deliberately not its choice of key:
    `next_entry_no` is `COUNT(*) + 1` over surviving rows, so deleting an entry
    makes the next one REUSE its number. That is harmless while Entry_No is a
    label — the unique constraint still rejects a live duplicate — but a
    `consumption` row outlives the entry that produced it, and two of them
    carrying one Source_Ref would make the ledger unreconcilable. The id is a
    surrogate key and is never reissued. The human-readable number rides in
    `Remarks`, where being pretty matters and being unique does not.

    ⚠️ IT CALLS `ledger.post_consumption`, NOT AN INSERT OF ITS OWN. That
    function owns FEFO lot selection, the allow-and-log over-issue warning and
    the audit line, and a second copy here would drift from it the first time
    any of the three changed.
    """
    from .ledger import post_consumption

    row = (await session.execute(select(entry_t)
           .where(entry_t.c["id"] == entry_id))).mappings().first()
    if row is None:
        return {"posted": 0, "lines": []}
    mats = (await session.execute(select(mat_t)
            .where(mat_t.c["Entry_ID"] == entry_id))).mappings().all()

    posted, warnings = [], []
    for m in mats:
        if m["Consumption_ID"]:
            continue                      # already posted — see the docstring
        sap = str(m["SAP_Code"] or "").strip()
        qty = float(m["Actual_Qty"] or 0)
        if not sap or qty <= 0:
            continue
        res = await post_consumption(session, username=username, data={
            "Date": row["Work_Date"], "SAP_Code": sap, "Quantity": qty,
            "Site_ID": row["Site_ID"],
            "Work_Type": row["Execution_Sub_Activity_Code"],
            "Tank_No": row["Equipment_Tag_No"],
            "Issued_To": row.get("supervisor_username"),
            "Issued_By": username,
            "Lot_Number": m["Lot_No"] or None,
            "Remarks": f"Execution entry {row['Entry_No']}",
            "Source_Ref": f"SME_EXEC:{row['id']}:{m['id']}",
        })
        cid = res.get("consumption_id")
        await session.execute(update(mat_t).where(mat_t.c["id"] == m["id"])
                              .values(Consumption_ID=cid))
        # `post_consumption` does not persist Source_Ref or WBS itself — the
        # commit path sets those afterwards, and so do we, for the same reason:
        # the id is only known once the row exists.
        await session.execute(update(cons_t).where(cons_t.c["id"] == cid).values(
            Source_Ref=f"SME_EXEC:{row['id']}:{m['id']}",
            **({"WBS": row["WBS_Number"]} if row.get("WBS_Number") else {})))
        posted.append({"line_id": int(m["id"]), "consumption_id": cid,
                       "SAP_Code": sap, "Quantity": qty})
        if res.get("warning"):
            warnings.append(res["warning"])

    if posted:
        await session.execute(update(entry_t).where(entry_t.c["id"] == entry_id)
                              .values(Stock_Posted_At=_now()))
    return {"posted": len(posted), "lines": posted, "warnings": warnings}


# ─── HOD: approve (optionally correcting), or reject ─────────────────────────
async def hod_decide(session: AsyncSession, *, username: str, entry_id: int,
                     site_id: Optional[str], approve: bool,
                     reject_reason: str = "",
                     edits: dict | None = None,
                     justification: str = "",
                     qsep_override: bool = False,
                     qsep_reason: str = "") -> dict:
    """Approve or reject, with the HOD's power to correct either side.

    ⚠️ AN EDIT COSTS A JUSTIFICATION AND A NOTIFICATION. The moment any number
    differs from what the store keeper verified, `justification` is required and
    the supervisor is told what changed. Without that the supervisor is left
    answering for figures they never entered.

    ⚠️ APPROVAL POSTS AREA *AND* STOCK (ruling Q1-b), in that order and inside
    the caller's transaction, so a failure in either leaves neither.

    ⚠️ THE QSEP GATE BLOCKS BY DEFAULT AND CAN BE OVERRIDDEN (ruling Q2-D). On
    a paper-first flow the material was applied days ago, so refusing outright
    only strands the record and lets stock overstate. `qsep_override=True`
    demands a written reason and notifies the Head of Qualities — an
    uncertified application becomes a visible, attributed exception rather than
    an invisible one.
    """
    entry = await get_entry(session, entry_id, site_id)
    assert_transition(entry["status"], APPROVED if approve else REJECTED)

    if not approve:
        if not (reject_reason or "").strip():
            raise HTTPException(422, "a rejection needs a reason — the "
                                     "supervisor has to know what to fix")
        await session.execute(update(entry_t).where(entry_t.c["id"] == entry_id)
                              .values(status=REJECTED, hod_username=username,
                                      hod_decided_at=_now(),
                                      Reject_Reason=reject_reason.strip(),
                                      updated_at=_now()))
        await write_audit(session, username, "SME_EXEC_REJECT",
                          "sme_execution_entry", entry["Entry_No"])
        await dispatch(session, event_key="sme_exec_rejected",
                       title="Execution entry rejected", severity="warning",
                       body=f"{entry['Entry_No']}: {reject_reason.strip()[:180]}"
                            " — rejection is final; raise a new entry from a "
                            "fresh form.",
                       recipient_user=entry.get("supervisor_username"),
                       recipient_role=None if entry.get("supervisor_username")
                       else "supervisor",
                       recipient_site=entry["Site_ID"], link_page="/execution",
                       related_table="sme_execution_entry",
                       related_ref=str(entry_id), created_by=username)
        return await get_entry(session, entry_id, site_id)

    # ── apply the edits, recording what actually changed ────────────────────
    edits = edits or {}
    changed: list[str] = []

    if "Actual_SQM" in edits and edits["Actual_SQM"] is not None:
        new_sqm = float(edits["Actual_SQM"])
        if abs(new_sqm - float(entry.get("Actual_SQM") or 0)) > 1e-9:
            changed.append(f"SQM {entry.get('Actual_SQM')} → {new_sqm}")
            await session.execute(update(entry_t)
                                  .where(entry_t.c["id"] == entry_id)
                                  .values(Actual_SQM=new_sqm))

    for m in (edits.get("materials") or []):
        row = next((x for x in entry["materials"] if x["id"] == m.get("id")), None)
        if row is None:
            raise HTTPException(422, f"material line {m.get('id')} is not on "
                                     f"this entry")
        vals: dict = {}
        if m.get("Actual_Qty") is not None:
            new_q = float(m["Actual_Qty"])
            if abs(new_q - float(row["Actual_Qty"] or 0)) > 1e-9:
                changed.append(f"{row['Material_Code']} {row['Actual_Qty']} → {new_q}")
                vals["Actual_Qty"] = new_q
        if "Lot_No" in m:
            lot = str(m.get("Lot_No") or "").strip() or None
            if lot != (row.get("Lot_No") or None):
                changed.append(f"{row['Material_Code']} lot "
                               f"{row.get('Lot_No') or '—'} → {lot or '—'}")
                vals["Lot_No"] = lot
        if vals:
            await session.execute(update(mat_t).where(mat_t.c["id"] == row["id"])
                                  .values(**vals))

    for r in (edits.get("manpower") or []):
        row = next((x for x in entry["manpower"] if x["id"] == r.get("id")), None)
        if row is None:
            raise HTTPException(422, f"crew line {r.get('id')} is not on this entry")
        vals, bits = {}, []
        if r.get("Headcount") is not None and \
                abs(float(r["Headcount"]) - float(row["Headcount"] or 0)) > 1e-9:
            vals["Headcount"] = float(r["Headcount"])
            bits.append(f"{row['Headcount']} → {r['Headcount']} head")
        if r.get("Hours") is not None and \
                abs(float(r["Hours"]) - float(row["Hours"] or 0)) > 1e-9:
            vals["Hours"] = float(r["Hours"])
            bits.append(f"{row['Hours']} → {r['Hours']} h")
        if vals:
            changed.append(f"{row['Role_Code']} " + ", ".join(bits))
            await session.execute(update(man_t).where(man_t.c["id"] == row["id"])
                                  .values(**vals))

    if changed and not (justification or "").strip():
        raise HTTPException(
            422, "you changed " + "; ".join(changed[:3])
                 + (" and more" if len(changed) > 3 else "")
                 + " — a justification is required, and the supervisor is "
                   "notified of it")

    # ── ⚠️ THE QSEP GATE, on the figures as they now stand ──────────────────
    # Checked AFTER the edits, because an HOD correcting a lot number is the
    # ordinary way a blocked entry becomes approvable — running it first would
    # refuse the very fix being applied.
    qsep = await qsep_status(session, entry_id)
    if qsep["blocked"]:
        if not qsep_override:
            names = "; ".join(f"{b['Material_Code']} ({b['gate']})"
                              for b in qsep["blocked"][:3])
            raise HTTPException(
                422,
                f"{len(qsep['blocked'])} material line(s) are not cleared for "
                f"issue: {names}"
                + (" and more" if len(qsep["blocked"]) > 3 else "")
                + ". The material has already been applied, so this is a "
                  "paperwork gap rather than something you can prevent — chase "
                  "the certificate, or approve with an override and say why. "
                  "An override notifies the Head of Qualities.")
        if not (qsep_reason or "").strip():
            raise HTTPException(
                422, "an override needs a written reason. It is what the Head "
                     "of Qualities reads, and what an auditor sees beside an "
                     "uncertified application.")
        await session.execute(update(entry_t).where(entry_t.c["id"] == entry_id)
                              .values(QSEP_Override=True,
                                      QSEP_Override_Reason=qsep_reason.strip(),
                                      QSEP_Override_By=username,
                                      QSEP_Override_At=_now()))

    # ⚠️ AREA, THEN STOCK. Both inside the caller's transaction.
    await post_progress(session, entry_id)
    stock = await post_stock(session, entry_id, username=username)

    await session.execute(update(entry_t).where(entry_t.c["id"] == entry_id)
                          .values(status=APPROVED, hod_username=username,
                                  hod_decided_at=_now(),
                                  hod_edited=bool(changed),
                                  HOD_Edit_Justification=(justification.strip()
                                                          if changed else None),
                                  updated_at=_now()))
    await write_audit(session, username, "SME_EXEC_APPROVE",
                      "sme_execution_entry",
                      f"{entry['Entry_No']} stock_lines={stock['posted']}"
                      + (f" edited: {'; '.join(changed)}" if changed else "")
                      + (" QSEP_OVERRIDE" if qsep["blocked"] else ""))

    if qsep["blocked"]:
        # ⚠️ THE HEAD OF QUALITIES IS TOLD, EVERY TIME. An override that only
        # appeared in an audit table would be an exception nobody reads.
        await dispatch(session, event_key="sme_exec_qsep_override",
                       title="Uncertified material approved on an execution entry",
                       severity="critical",
                       body=f"{entry['Entry_No']} at {entry['Site_ID']}: "
                            + "; ".join(f"{b['Material_Code']} ({b['gate']})"
                                        for b in qsep["blocked"][:3])
                            + f" — {qsep_reason.strip()[:160]}",
                       recipient_role="qc_hod", link_page="/qc-hod",
                       related_table="sme_execution_entry",
                       related_ref=str(entry_id), created_by=username)

    if changed:
        await dispatch(session, event_key="sme_exec_hod_edited",
                       title="Your execution entry was corrected",
                       severity="warning",
                       body=f"{entry['Entry_No']}: {'; '.join(changed[:3])}"
                            + (" …" if len(changed) > 3 else "")
                            + f" — {justification.strip()[:140]}",
                       recipient_user=entry.get("supervisor_username"),
                       recipient_role=None if entry.get("supervisor_username")
                       else "supervisor",
                       recipient_site=entry["Site_ID"], link_page="/execution",
                       related_table="sme_execution_entry",
                       related_ref=str(entry_id), created_by=username)
    out = await get_entry(session, entry_id, site_id)
    out["stock"] = stock
    return out


async def list_entries(session: AsyncSession, *, site_id: Optional[str],
                       statuses: list[str] | None = None,
                       limit: int = 200) -> list[dict]:
    stmt = select(entry_t)
    if site_id is not None:
        stmt = stmt.where(entry_t.c["Site_ID"] == site_id)
    if statuses:
        stmt = stmt.where(entry_t.c["status"].in_(statuses))
    rows = [dict(r) for r in (await session.execute(
        stmt.order_by(entry_t.c["id"].desc()).limit(limit))).mappings().all()]
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    mats: dict[int, list] = {}
    for m in (await session.execute(
            select(mat_t).where(mat_t.c["Entry_ID"].in_(ids)))).mappings().all():
        mats.setdefault(m["Entry_ID"], []).append(dict(m))
    mans: dict[int, list] = {}
    for m in (await session.execute(
            select(man_t).where(man_t.c["Entry_ID"].in_(ids)))).mappings().all():
        mans.setdefault(m["Entry_ID"], []).append(dict(m))
    # The discipline (CV/ME) each entry belongs to, read from the equipment
    # master (Phase 8). It is a property of the (tag, code) ROW, not of the
    # code — LSC1 is CV on concrete and ME on tanks — so it is looked up per
    # pair rather than per code. One query for the whole page.
    types = await _entry_types(session, rows)
    for r in rows:
        r["materials"] = mats.get(r["id"], [])
        r["manpower"] = mans.get(r["id"], [])
        r["variance"] = compute_variance(r, r["materials"], r["manpower"])
        r["Type"] = types.get((str(r.get("Equipment_Tag_No") or ""),
                               str(r.get("Lining_System_Code") or "")), "")
    return rows


async def _entry_types(session: AsyncSession, rows: list[dict]) -> dict:
    """`(tag, code)` → CV/ME for the tags these entries name.

    Surface-prep entries carry code '' and take the tag's set, printed 'CV/ME'
    where a tag spans both — never whichever row was read first, which would
    be an invented aggregate presented as fact.
    """
    eq = _MD.tables["sme_equipment"]
    tags = {str(r.get("Equipment_Tag_No") or "") for r in rows}
    tags.discard("")
    if not tags:
        return {}
    out: dict = {}
    by_tag: dict[str, set] = {}
    for tag, code, typ in (await session.execute(
            select(eq.c["Equipment_Tag_No"], eq.c["Lining_System_Code"],
                   eq.c["Type"]).where(eq.c["Equipment_Tag_No"].in_(tags)))).all():
        t = str(typ or "").strip().upper()
        if not t:
            continue
        out[(str(tag), str(code))] = t
        by_tag.setdefault(str(tag), set()).add(t)
    for tag, ts in by_tag.items():
        out[(tag, "")] = "/".join(sorted(ts))
    return out


# ─── Phase 6: reporting ──────────────────────────────────────────────────────
def _flatten_for_report(entry: dict) -> dict:
    """One entry → one flat row of comparison figures.

    Everything here comes from the entry's OWN snapshot, never from a fresh
    read of master data, so a report run today and the same report run after
    somebody corrects a benchmark answer identically.
    """
    v = entry.get("variance") or compute_variance(
        entry, entry.get("materials") or [], entry.get("manpower") or [])
    mt = v["material_total"]
    mp = v["manpower"]
    return {
        "Entry_No": entry.get("Entry_No"),
        "Work_Date": entry.get("Work_Date"),
        "Site_ID": entry.get("Site_ID"),
        "Equipment_Tag_No": entry.get("Equipment_Tag_No"),
        # '' renders as a word, never a blank cell — a blank reads as missing
        # data, and this is a real category.
        "Lining_System_Code": entry.get("Lining_System_Code") or "(surface prep)",
        # CV/ME, from the equipment master. Stamped in list_entries so exports
        # carry it too — a printed variance sheet that does not say which
        # discipline a row belongs to cannot be checked against a crew.
        "Type": entry.get("Type") or "",
        "Execution_Sub_Activity_Code": entry.get("Execution_Sub_Activity_Code"),
        "Variant_Key": entry.get("Variant_Key") or "",
        "status": entry.get("status"),
        "Actual_SQM": entry.get("Actual_SQM"),
        "Material_Actual": mt["Actual"],
        "Material_Benchmark": mt["Benchmark"],
        "Material_Variance": mt["Variance"],
        "Material_Variance_Pct": mt["Variance_Pct"],
        "Manpower_Actual_Manhours": mp["Actual_Manhours"],
        "Manpower_Benchmark_Manhours": mp["Benchmark_Manhours"],
        "Manpower_Variance_Manhours": mp["Variance_Manhours"],
        "Manpower_Variance_Pct": mp["Variance_Pct"],
        "Actual_Headcount": mp["Actual_Headcount"],
        "Benchmark_Crew_Size": mp["Benchmark_Crew_Size"],
        "Material_Variance_Reason": entry.get("Material_Variance_Reason"),
        "Manpower_Variance_Reason": entry.get("Manpower_Variance_Reason"),
        "supervisor_username": entry.get("supervisor_username"),
        "sk_username": entry.get("sk_username"),
        "hod_username": entry.get("hod_username"),
        "hod_edited": bool(entry.get("hod_edited")),
        "HOD_Edit_Justification": entry.get("HOD_Edit_Justification"),
        "Reject_Reason": entry.get("Reject_Reason"),
    }


async def variance_report(session: AsyncSession, *, site_id: Optional[str],
                          date_from: Optional[str] = None,
                          date_to: Optional[str] = None,
                          statuses: list[str] | None = None) -> dict:
    """Actual vs benchmark, per entry, plus the totals that matter.

    ⚠️ The totals SUM the absolute figures and derive one percentage from the
    sums — they do not average the per-entry percentages. Averaging percentages
    weights a 2 m² entry the same as a 2,000 m² one, which is how a programme
    that is 8% over reports itself as on target.
    """
    rows = await list_entries(session, site_id=site_id,
                              statuses=statuses, limit=5000)
    if date_from:
        rows = [r for r in rows if str(r.get("Work_Date") or "") >= date_from]
    if date_to:
        rows = [r for r in rows if str(r.get("Work_Date") or "") <= date_to]
    flat = [_flatten_for_report(r) for r in rows]

    def _sum(key):
        return round(sum(float(r[key] or 0) for r in flat), 4)

    mat_a, mat_b = _sum("Material_Actual"), _sum("Material_Benchmark")
    man_a, man_b = _sum("Manpower_Actual_Manhours"), _sum("Manpower_Benchmark_Manhours")
    return {
        "items": flat,
        "totals": {
            "Entries": len(flat),
            "Actual_SQM": _sum("Actual_SQM"),
            "Material_Actual": mat_a, "Material_Benchmark": mat_b,
            "Material_Variance": round(mat_a - mat_b, 4),
            "Material_Variance_Pct": (round((mat_a - mat_b) / mat_b * 100, 2)
                                      if mat_b else None),
            "Manpower_Actual_Manhours": man_a,
            "Manpower_Benchmark_Manhours": man_b,
            "Manpower_Variance_Manhours": round(man_a - man_b, 2),
            "Manpower_Variance_Pct": (round((man_a - man_b) / man_b * 100, 2)
                                      if man_b else None),
        },
    }


async def reason_log(session: AsyncSession, *, site_id: Optional[str],
                     limit: int = 1000) -> list[dict]:
    """Every stated reason, and every HOD correction, as an audit trail.

    Ships the ORIGINAL alongside the corrected value. An audit line saying a
    quantity changed without saying from what is not an audit trail.
    """
    rows = await list_entries(session, site_id=site_id, limit=limit)
    out = []
    for r in rows:
        if not (r.get("Material_Variance_Reason")
                or r.get("Manpower_Variance_Reason")
                or r.get("HOD_Edit_Justification") or r.get("Reject_Reason")):
            continue
        edits = []
        for m in r.get("materials") or []:
            if m.get("Original_Qty") is not None and \
                    abs(float(m["Original_Qty"]) - float(m["Actual_Qty"] or 0)) > 1e-9:
                edits.append(f"{m['Material_Code']}: "
                             f"{m['Original_Qty']} → {m['Actual_Qty']}")
        for c in r.get("manpower") or []:
            if c.get("Original_Headcount") is not None and \
                    abs(float(c["Original_Headcount"]) - float(c["Headcount"] or 0)) > 1e-9:
                edits.append(f"{c['Role_Code']} head: "
                             f"{c['Original_Headcount']} → {c['Headcount']}")
            if c.get("Original_Hours") is not None and \
                    abs(float(c["Original_Hours"]) - float(c["Hours"] or 0)) > 1e-9:
                edits.append(f"{c['Role_Code']} hours: "
                             f"{c['Original_Hours']} → {c['Hours']}")
        out.append({
            "Entry_No": r.get("Entry_No"), "Work_Date": r.get("Work_Date"),
            "Equipment_Tag_No": r.get("Equipment_Tag_No"),
            "Execution_Sub_Activity_Code": r.get("Execution_Sub_Activity_Code"),
            "status": r.get("status"),
            "supervisor_username": r.get("supervisor_username"),
            "Material_Variance_Reason": r.get("Material_Variance_Reason"),
            "Manpower_Variance_Reason": r.get("Manpower_Variance_Reason"),
            "hod_username": r.get("hod_username"),
            "hod_edited": bool(r.get("hod_edited")),
            "HOD_Edit_Justification": r.get("HOD_Edit_Justification"),
            "Changed": "; ".join(edits),
            "Reject_Reason": r.get("Reject_Reason"),
        })
    return out


async def surface_prep_report(session: AsyncSession, *,
                              site_id: Optional[str]) -> dict:
    """Prep area done per equipment + sub-activity, beside the area that EXISTS.

    The denominator is `sme_equipment.Surface_Area_SQM` — the equipment's own
    area — because prep has no planned figure of its own and inventing one
    would give two numbers that drift. Coverage can legitimately exceed 100%:
    a surface can be re-blasted, and clamping it would hide rework rather than
    show it.
    """
    eq_t = _MD.tables["sme_equipment"]
    stmt = select(prep_t)
    if site_id is not None:
        stmt = stmt.where(prep_t.c["Site_ID"] == site_id)
    rows = [dict(r) for r in (await session.execute(
        stmt.order_by(prep_t.c["Equipment_Tag_No"],
                      prep_t.c["Execution_Sub_Activity_Code"]))).mappings().all()]

    area_stmt = select(eq_t.c["Equipment_Tag_No"],
                       func.sum(eq_t.c["Surface_Area_SQM"]))
    if site_id is not None:
        area_stmt = area_stmt.where(eq_t.c["Site_ID"] == site_id)
    areas = {str(t): float(a or 0) for t, a in (await session.execute(
        area_stmt.group_by(eq_t.c["Equipment_Tag_No"]))).all()}

    for r in rows:
        total = areas.get(str(r["Equipment_Tag_No"]), 0.0)
        r["Equipment_Area_SQM"] = round(total, 2)
        r["Coverage_Pct"] = (round(float(r["Done_SQM"] or 0) / total * 100, 2)
                             if total else None)
    return {
        "items": rows,
        "totals": {
            "Activities": len(rows),
            "Prep_SQM": round(sum(float(r["Done_SQM"] or 0) for r in rows), 2),
            "Entries": sum(int(r["Entry_Count"] or 0) for r in rows),
        },
    }
