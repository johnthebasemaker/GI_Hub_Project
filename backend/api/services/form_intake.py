"""
backend/api/services/form_intake.py — Phase 9d: a photographed form becomes a draft.

Between `ai/ocr_form.read_form()` (what the camera saw) and
`execution.open_entry()` (a row a supervisor can edit) sit four checks that all
have the same shape: refuse a sheet we cannot map, rather than map it wrongly.

    1. IS THIS A GI FORM?         the QR decodes, or the upload is refused
    2. IS IT THIS SITE'S?         a scoped user cannot file another site's paper
    3. HAS IT BEEN FILED ALREADY? `Form_UUID` is consumed exactly once
    4. IS THE PAPER STILL VALID?  `Recipe_Fingerprint` still matches

⚠️ CHECK 4 IS THE ONE PEOPLE WILL WANT TO SOFTEN, AND IT IS THE ONE THAT MATTERS
MOST. Row 3 of the handwriting maps to row 3 of the recipe. If a material was
added, removed or reordered after the sheet was printed, everything past that
point lands on the wrong material — and the result LOOKS FINE: plausible
quantities, against real materials, in a real system. There is no downstream
check that would catch it. Refusing a stale sheet is the only place this can be
stopped, so it refuses.

⚠️ THE ENTRY IS CREATED AT `DRAFT_SUPERVISOR`, NOT SUBMITTED. Extraction is the
machine's opinion; the supervisor reviews every figure and supplies the two
mandatory reasons before anything moves. Creating it already-submitted would
mean the model's reading of a digit could reach an approver untouched.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai import handwritten as _hw
from ..ai import ocr_form as OF
from . import consumption_form as CF
from . import execution as X
from .ledger import _MD, write_audit

form_t = _MD.tables["sme_consumption_form"]
entry_t = _MD.tables["sme_execution_entry"]
mat_t = _MD.tables["sme_execution_entry_material"]

_DATE_RX = [
    (re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})$"), None),
    (re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2})$"), 2000),
]
# Handwritten digits the model most often mistakes for letters.
_DIGIT_FIX = str.maketrans({"l": "1", "I": "1", "O": "0", "o": "0", "S": "5"})


def parse_work_date(text: str, *, today: Optional[_dt.date] = None
                    ) -> tuple[Optional[str], Optional[str]]:
    """(iso_date, problem). DD/MM only — never swapped to MM/DD.

    ⚠️ NEVER GUESSED. An unreadable date returns None and the supervisor types
    it; a guessed one posts the work to the wrong day, and the day is what
    decides which progress row and which shift the consumption lands on. The
    ±1-year window catches a mis-read year rather than accepting 2062.

    Same DD/MM rule as `ai/handwritten.parse_form_date`, deliberately: two
    date parsers in one system that disagree about 03/04 is a bug waiting for
    the fourth of March. It now shares that function's shift-stripping too —
    a crew that writes `27/08/26 (Night)` in the box must not have its whole
    page refused for saying which shift it was. Read the shift itself with
    `ai/handwritten.parse_shift`.
    """
    today = today or _dt.date.today()
    s = _hw.strip_shift(str(text or "").strip()).translate(_DIGIT_FIX)
    for rx, century in _DATE_RX:
        m = rx.match(s)
        if not m:
            continue
        dd, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if century:
            yy += century
        try:
            d = _dt.date(yy, mm, dd)
        except ValueError:
            return None, f"{text!r} is not a real date"
        if not (today.year - 1 <= d.year <= today.year + 1):
            return None, f"{text!r} is outside the last year"
        if d > today + _dt.timedelta(days=1):
            return None, f"{text!r} is in the future"
        return d.isoformat(), None
    return None, (f"{text!r} could not be read as a date" if s
                  else "the date box was blank")


def _sheets_seen(reg: dict) -> set:
    """Which pages of this form have already been read, from `Sheets_Seen`.

    Stored as a comma-separated list of printed sheet numbers rather than a
    count, because "sheets 1 and 3 are in, 2 is missing" is the thing a
    supervisor standing in a plant needs told — a count of 2 out of 3 does not
    say which one to go and photograph.
    """
    raw = str(reg.get("Sheets_Seen") or "").strip()
    out = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


async def validate_sheet(session: AsyncSession, read: dict, *,
                         site_id: str) -> dict:
    """The four checks. Returns the registry row; raises with a plain reason.

    Every refusal names the fix, because the person holding the phone is
    standing in a plant and "invalid form" tells them nothing they can act on.
    """
    uuid = read["form_uuid"]
    reg = (await session.execute(select(form_t)
           .where(form_t.c["Form_UUID"] == uuid))).mappings().first()
    if reg is None:
        raise HTTPException(
            422, f"form {uuid} is not one this system printed. Download a fresh "
                 f"form from Execution Entries and use that — a photocopy or a "
                 f"hand-drawn sheet cannot be matched to a system's materials.")

    if reg["Site_ID"] != site_id:
        raise HTTPException(
            403, f"that form was printed for {reg['Site_ID']}, not {site_id}. "
                 f"Consumption is filed against the site the material left.")

    # ⚠️ A MULTI-PAGE FORM IS FILED A PAGE AT A TIME (Phase 13b, ruling Q13-2).
    #
    # A recipe of more than 18 materials prints on several A4 sheets, and until
    # the QR carried a sheet number every one of those pages looked identical
    # to the reader. So the second photograph of a three-page form hit exactly
    # the refusal below and was told the form was already filed — which was
    # true of the FORM and false of the PAGE, and left the supervisor with no
    # way at all to record rows 19 to 36.
    #
    # The rule is therefore per SHEET, not per form: a page already read is
    # refused, a page not yet read is merged into the draft the first page
    # opened. The `consumed` status still closes the form to a re-photograph
    # once every page is in, which is the guarantee the old check was for.
    seen = _sheets_seen(reg)
    seq = int(read.get("sheet_seq") or 1)
    of = int(read.get("sheet_of") or 1)
    if reg["status"] == "consumed" and seq in seen:
        raise HTTPException(
            409, (f"sheet {seq} of form {uuid} has already been filed as entry "
                  f"{reg['consumed_entry_id']}."
                  if of > 1 else
                  f"form {uuid} has already been filed as entry "
                  f"{reg['consumed_entry_id']}.")
            + " Each printed sheet is used once — if you meant to record a "
              "second day's work, print a new form.")
    if reg["status"] == "consumed" and of <= 1:
        # A `GIF1` sheet, or a genuinely single-page form: there is no second
        # page to be waiting for, so this is the old refusal, unchanged.
        raise HTTPException(
            409, f"form {uuid} has already been filed as entry "
                 f"{reg['consumed_entry_id']}. Each printed sheet is used once "
                 f"— if you meant to record a second day's work, print a new "
                 f"form.")
    if reg["status"] == "void":
        raise HTTPException(422, f"form {uuid} was voided and cannot be filed.")

    # ⚠️ CHECK 4 — see the module docstring for why this refuses rather than warns.
    rows = await CF.recipe_rows(session, code=reg["Lining_System_Code"],
                                esc=reg["Execution_Sub_Activity_Code"] or None)
    if CF.fingerprint(rows) != reg["Recipe_Fingerprint"]:
        raise HTTPException(
            409,
            f"the materials for {reg['Lining_System_Code']} have changed since "
            f"this form was printed, so the printed rows no longer line up with "
            f"the system's materials. Your quantities would be filed against "
            f"the wrong ones. Print a fresh form and copy the figures across.")
    return dict(reg) | {"_rows": rows}


def _match_rows(read_rows: list[dict], recipe: list[dict], *,
                sheet_seq: int = 1, sheet_of: int = 1) -> list[dict]:
    """Handwriting → materials, by printed row number.

    ⚠️ POSITIONAL, AND ONLY SAFE BECAUSE THE FINGERPRINT WAS CHECKED FIRST. A
    row number the form never printed is DROPPED rather than appended: a model
    that hallucinates row 7 on a six-row form must not be able to invent a
    seventh material.

    ⚠️ AND ON A MULTI-PAGE FORM, A SHEET MAY ONLY FILL ITS OWN ROWS (13b). The
    printed numbers run straight through the pages — page 2 of a 36-material
    form is numbered 19 to 36 — so a reading already knows which row it is.
    Confining it to the sheet's own span is the guard on top of that: a model
    looking at page 2 that reports "row 3" is describing something it cannot
    see, and believing it would file page 2's third quantity against page 1's
    third material. Dropped, exactly as an out-of-range row already was.
    """
    lo, hi = 1, len(recipe)
    if sheet_of > 1:
        lo, hi = CF.sheet_row_span(sheet_seq)
        hi = min(hi, len(recipe))
    by_row = {}
    for r in read_rows:
        n = int(r.get("row") or 0)
        if lo <= n <= hi:
            by_row.setdefault(n, r)      # first wins; a duplicate row is noise
    out = []
    for i, rec in enumerate(recipe):
        got = by_row.get(i + 1, {})
        out.append({
            "Row_Index": i,
            "Material_Code": rec["Material_Code"],
            "SAP_Code": rec.get("SAP_Code") or "",
            "UOM": rec.get("UOM"),
            "OCR_Qty": got.get("quantity"),
            "OCR_Qty_Text": got.get("qty_text") or "",
            "OCR_Lot_Text": got.get("lot_text") or "",
            # ⚠️ A NULL READING BECOMES A ZERO ON THE DRAFT, NOT A GUESS. The
            # supervisor sees the grey OCR column empty beside it and the raw
            # text they wrote, and types the real figure. Seeding the draft with
            # the model's uncertain number is how an unread digit becomes an
            # approved quantity.
            "Actual_Qty": float(got.get("quantity") or 0.0),
            "Lot_No": (got.get("lot_text") or "").strip() or None,
            "struck_through": bool(got.get("struck_through")),
        })
    return out


async def build_entry(session: AsyncSession, read: dict, *, site_id: str,
                      username: str, role: str, image_bytes: bytes,
                      job_id: Optional[int] = None) -> dict:
    """Validated sheet + extraction → a DRAFT_SUPERVISOR entry to review.

    ⚠️ ONE FORM IS ONE ENTRY, EVEN WHEN IT IS THREE PIECES OF PAPER (13b). The
    first page photographed opens the draft with every recipe row present and
    the pages nobody has sent yet left at zero; each later page MERGES into
    that same draft, touching only its own rows. Opening a second entry per
    page would split one day's consumption across three approvals and three
    variance comparisons, each measured against the whole system's benchmark.
    """
    reg = await validate_sheet(session, read, site_id=site_id)
    recipe = reg.pop("_rows")
    seq = int(read.get("sheet_seq") or 1)
    of = int(read.get("sheet_of") or 1)
    lines = _match_rows(read.get("rows") or [], recipe,
                        sheet_seq=seq, sheet_of=of)

    if of > 1:
        merged = await _merge_sheet(session, reg, lines, seq=seq, of=of,
                                    read=read, job_id=job_id,
                                    image_bytes=image_bytes)
        if merged is not None:
            return merged

    work_date, date_problem = parse_work_date(read.get("work_date_text") or "")
    equipment = (read.get("equipment_text") or "").strip()
    # ⚠️ THE SHIFT COMES FROM THE PAPER OR IT DOES NOT COME AT ALL (Q13,
    # 2026-09-02). Crews write `(Night)` beside the date; that is a statement by
    # the people who did the work, and ruling P10-9 objects to INFERRING a shift
    # from a filing timestamp, not to reading one somebody wrote down. No
    # marker → None → the column stays NULL, exactly as P10-9 requires.
    shift = _hw.parse_shift(read.get("work_date_text"))

    opened = await X.open_entry(
        session, username=username, role=role, site_id=site_id,
        # An unreadable date defaults to TODAY and is flagged — the supervisor
        # must confirm it. A null would fail the NOT NULL column; a silent
        # yesterday would be a lie.
        work_date=work_date or _dt.date.today().isoformat(),
        # Equipment is never fuzzy-matched to the master (see the plan's edge
        # case 5): a wrong tag posts area to the wrong vessel. Whatever the
        # model read is offered as text and the supervisor picks from a list.
        equipment_tag=equipment or "(unread — pick the equipment)",
        shift=shift,
        code=reg["Lining_System_Code"],
        esc=reg["Execution_Sub_Activity_Code"] or _first_esc(recipe),
        materials=lines, origin="ocr", form_uuid=reg["Form_UUID"])

    await session.execute(update(entry_t).where(entry_t.c["id"] == opened["id"])
                          .values(OCR_Job_ID=job_id, OCR_Image=image_bytes,
                                  OCR_Image_Mime="image/jpeg",
                                  OCR_Raw_JSON=json.dumps(
                                      {k: v for k, v in read.items()
                                       if k != "raw"}, ensure_ascii=False,
                                      default=str)[:60000],
                                  OCR_Model=read.get("model"),
                                  Actual_SQM=read.get("area_sqm")))
    # ⚠️ THE SHEET IS CONSUMED HERE, not at approval. A second photo of the same
    # paper must be refused while the first is still a draft — otherwise two
    # drafts of one sheet race to become two consumptions.
    #
    # ⚠️ AND `consumed` NOW MEANS "THIS PAGE IS IN", NOT "THE FORM IS DONE".
    # `Sheets_Seen` is what says which pages have arrived; the status closes
    # the form to a re-photograph of a page already read, which is exactly what
    # the check above enforces. A three-page form is `consumed` from its first
    # page onward and still accepts pages 2 and 3 — the guarantee the original
    # check bought (no two drafts racing for one sheet) is unchanged, because
    # it was always per-sheet in intent and only ever per-form by accident.
    await session.execute(update(form_t).where(form_t.c["id"] == reg["id"])
                          .values(status="consumed",
                                  consumed_entry_id=opened["id"],
                                  Sheets_Seen=str(seq),
                                  consumed_at=_dt.datetime.now(
                                      _dt.timezone.utc).replace(tzinfo=None)))

    problems = []
    if of > 1:
        missing = sorted(set(range(1, of + 1)) - {seq})
        problems.append(
            f"This form is {of} pages and only sheet {seq} has been read. "
            f"Photograph sheet(s) {', '.join(map(str, missing))} and upload "
            f"them too — their rows are set to 0 until you do, and submitting "
            f"now would report those materials as unused.")
    if date_problem:
        problems.append(f"Date: {date_problem} — confirm it before submitting.")
    if not equipment:
        problems.append("The equipment box could not be read — pick it from "
                        "the list.")
    if read.get("area_sqm") is None:
        problems.append(f"Area: {read.get('area_text') or 'blank'} could not be "
                        f"read as a number — type it in.")
    unread = [ln["Row_Index"] + 1 for ln in lines
              if ln["OCR_Qty"] is None and ln["OCR_Qty_Text"]]
    if unread:
        problems.append(
            f"Row(s) {', '.join(map(str, unread))}: the handwriting was read "
            f"but the number was not certain. Check them against the photo.")
    struck = [ln["Row_Index"] + 1 for ln in lines if ln["struck_through"]]
    if struck:
        problems.append(f"Row(s) {', '.join(map(str, struck))} look crossed "
                        f"out — set them to 0 if that is right.")

    return {"entry_id": opened["id"], "Entry_No": opened["Entry_No"],
            "status": opened["status"], "Form_UUID": reg["Form_UUID"],
            "lines": len(lines), "problems": problems, "shift": shift,
            "model": read.get("model"), "provider": read.get("provider"),
            "work_date": work_date, "equipment_text": equipment,
            "area_sqm": read.get("area_sqm")}


async def _merge_sheet(session: AsyncSession, reg: dict, lines: list[dict], *,
                       seq: int, of: int, read: dict, job_id: Optional[int],
                       image_bytes: bytes) -> Optional[dict]:
    """Fold page `seq` of a multi-page form into the draft its first page opened.

    Returns the same shape `build_entry` does, or `None` when there is nothing
    to merge into yet — in which case the caller opens the entry normally and
    this page becomes the first one.

    ⚠️ IT MERGES ONLY INTO A DRAFT. Once a supervisor has submitted, the entry
    is somebody else's to change: a store keeper may already have verified
    quantities against what left the shelf, and silently rewriting eighteen of
    them underneath that verification would put their name on numbers they
    never saw. A late page against a submitted entry is refused with the reason,
    which is a thing a person can act on — reprint, or raise the missing rows
    as their own entry.

    ⚠️ AND IT WRITES ONLY THIS SHEET'S ROWS. `_match_rows` already dropped
    everything outside the span, so the untouched rows here are pages that have
    not arrived (still 0) or pages already read (their own values). Writing the
    full set would zero them.
    """
    entry_id = reg.get("consumed_entry_id")
    if not entry_id:
        return None
    row = (await session.execute(select(entry_t)
           .where(entry_t.c["id"] == entry_id))).mappings().first()
    if row is None:
        # The draft was deleted. The form is stranded — let the caller open a
        # fresh entry rather than refusing paper for a row that no longer
        # exists.
        return None
    if row["status"] != "DRAFT_SUPERVISOR":
        raise HTTPException(
            409, f"sheet {seq} of this form belongs to entry {row['Entry_No']}, "
                 f"which has already been submitted ({row['status']}). A page "
                 f"cannot be added to an entry somebody is already checking — "
                 f"ask the store keeper or HOD to reject it, then upload all "
                 f"{of} pages together.")

    lo, hi = CF.sheet_row_span(seq)
    touched = 0
    for ln in lines:
        printed = ln["Row_Index"] + 1
        if not (lo <= printed <= hi):
            continue
        res = await session.execute(
            update(mat_t)
            .where(mat_t.c["Entry_ID"] == entry_id,
                   mat_t.c["Row_Index"] == ln["Row_Index"])
            .values(OCR_Qty=ln["OCR_Qty"], OCR_Qty_Text=ln["OCR_Qty_Text"],
                    OCR_Lot_Text=ln["OCR_Lot_Text"],
                    Actual_Qty=ln["Actual_Qty"], Lot_No=ln["Lot_No"]))
        touched += int(res.rowcount or 0)

    seen = _sheets_seen(reg) | {seq}
    await session.execute(update(form_t).where(form_t.c["id"] == reg["id"])
                          .values(Sheets_Seen=",".join(str(s) for s in sorted(seen))))

    # The area is on page 1's header band only — a later page has no header
    # fields at all, so nothing here may overwrite what page 1 established.
    missing = sorted(set(range(1, of + 1)) - seen)
    problems = []
    if missing:
        problems.append(
            f"Sheet {seq} added. Still missing sheet(s) "
            f"{', '.join(map(str, missing))} of {of} — their rows read 0 until "
            f"those pages are uploaded.")
    else:
        problems.append(f"Sheet {seq} added — all {of} pages are now in.")
    unread = [ln["Row_Index"] + 1 for ln in lines
              if lo <= ln["Row_Index"] + 1 <= hi
              and ln["OCR_Qty"] is None and ln["OCR_Qty_Text"]]
    if unread:
        problems.append(
            f"Row(s) {', '.join(map(str, unread))}: the handwriting was read "
            f"but the number was not certain. Check them against the photo.")

    await write_audit(session, row["supervisor_username"] or "ocr",
                      "CONSUMPTION_FORM_SHEET_MERGED",
                      "sme_execution_entry",
                      f"entry {entry_id} form {reg['Form_UUID']} sheet {seq}/{of} "
                      f"({touched} row(s))")
    return {"entry_id": int(entry_id), "Entry_No": row["Entry_No"],
            "status": row["status"], "Form_UUID": reg["Form_UUID"],
            "lines": touched, "problems": problems, "shift": row.get("Shift"),
            "model": read.get("model"), "provider": read.get("provider"),
            "work_date": row["Work_Date"], "equipment_text": row["Equipment_Tag_No"],
            "area_sqm": row.get("Actual_SQM"), "sheet_seq": seq, "sheet_of": of,
            "sheets_seen": sorted(seen), "merged": True}


def _first_esc(recipe: list[dict]) -> str:
    for r in recipe:
        if r.get("Execution_Sub_Activity_Code"):
            return str(r["Execution_Sub_Activity_Code"])
    return ""
