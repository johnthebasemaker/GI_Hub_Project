"""
backend/api/services/consumption_form.py — Phase 9c: the paper the field fills in.

THE POINT OF PRE-PRINTING, stated once because every decision below follows
from it. Slice 9d reads these sheets with `qwen2.5vl:7b`. A 7B vision model's
weakest task by far is reading handwritten NAMES; its strongest is reading a
digit in a box. So the form prints every material name itself and asks the
supervisor for nothing but numbers. That deletes the hardest half of the
existing OCR pipeline — `ai/handwritten.py`'s 18-entry corrections table
(`Yloues → Gloves`), the fuzzy matcher and the candidate-picking UI all exist
to recover from misread names, and a pre-printed name cannot be misread.

⚠️ AND THE QR DELETES THE OTHER HALF. Site, system, sub-activity and the form's
identity are read by a DECODER, not a language model — zero error rate rather
than a low one. The model's whole remaining job is: for each numbered row, what
is written in the QTY box, plus three free fields (Equipment, Area, Lot).

────────────────────────────────────────────────────────────────────────────
⚠️ FOUR ROWS CAN SHARE ONE MATERIAL NAME, AND THE FORM MUST NOT.

LSC8 lists `GI-8005765` — "Cumicrete PU MF 300 - 3mm" — FOUR times, once per
SAP code (1041, 1041-1, 1041-2, 1041-3), at four different rates. Printed by
name alone that is four identical rows, and a supervisor writing 20 in "the
Cumicrete one" has no way to say which. What separates them is
`Material_Description`: Comp-A, Comp-B, Comp-C, Comp-D. The row label therefore
carries the description whenever the system has more than one row for that
material code, and the SAP code is always printed small beside it so the
machine-readable identity is on the paper too.

Seven (system, material) pairs in the live recipe are in this shape. A form
that ignored it would be wrong on all seven and look fine on the other four.

────────────────────────────────────────────────────────────────────────────
⚠️ THE ROW ORDER IS THE CONTRACT. The QR carries no material list — it would
not fit — so slice 9d maps handwriting to materials POSITIONALLY: row 3 on the
paper is the third row of the fingerprint. `_recipe_rows` therefore sorts
deterministically, and `fingerprint()` hashes exactly what was printed, in that
order. If somebody edits `sme_recipe` after a sheet is printed, the hash stops
matching and 9d refuses the sheet instead of reading row 3's handwriting into a
different material.

⚠️ THE LOT/BATCH IS A COLUMN, NOT A HEADER FIELD (operator correction,
2026-08-26). One lining system draws several materials and each arrives from a
different batch: LSC8's Primer Comp-A and Mortar Comp-C are separate deliveries
with separate certificates. A single box at the top of the page can only record
one of them, so it would either be left blank or filled with whichever batch the
supervisor happened to think of — and the QSEP gate in slice 9d checks the
certificate PER MATERIAL. A lot recorded against the wrong line is worse than
none: it clears a gate for a batch that was never used.

⚠️ THE DATE FIELD IS BLANK, AND THAT IS DELIBERATE. Forms are printed in
batches and used same-day or next-day (ruling Q6), so a pre-printed work date
would be wrong on half of them — and wrong in the one direction that matters,
since the date drives which shift and which progress row the consumption lands
on. The GENERATION date is printed small in the footer instead: it tells you
how old a blank sheet is without claiming to be the day the work happened.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import io
import uuid
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..reports import _latin
from .ledger import _MD, write_audit

# ⚠️ TRANSLITERATE, DO NOT DROP. fpdf's core fonts are latin-1, and
# `reports._latin` encodes with errors="ignore" — which turns
# "Cumifloor ECO Primer — Primer - Comp-A" into "Cumifloor ECO Primer  Primer -
# Comp-A", quietly deleting the one character that separated the material from
# the component that distinguishes it. On a form whose whole job is telling four
# identical-looking rows apart, a silently vanishing separator is the worst
# possible failure. Map the typography we actually emit to ASCII first.
_TYPO = {"\u2014": "-", "\u2013": "-", "\u00b7": "*", "\u2022": "*",
         "\u2026": "...", "\u2018": "'", "\u2019": "'",
         "\u201c": '"', "\u201d": '"', "\u00b2": "2", "\u00b3": "3",
         "\u00d7": "x", "\u00a0": " "}


def _txt(s: str) -> str:
    out = str(s or "")
    for bad, good in _TYPO.items():
        out = out.replace(bad, good)
    return _latin(out)

form_t = _MD.tables["sme_consumption_form"]
recipe_t = _MD.tables["sme_recipe"]

# The QR payload's version tag. A decoder that meets an unknown prefix should
# say "this form was made by a newer version of the app" rather than guess at
# the field order — so the version leads, and 9d checks it.
# ⚠️ TWO VERSIONS ARE LIVE AT ONCE, ON PURPOSE. `GIF2` (Phase 13b) adds the
# sheet number and the sheet count so a multi-page form can be filed a page at
# a time; `GIF1` is every blank printed before that and is still in people's
# pockets. New forms are always emitted as `GIF2`; `parse_qr` reads both and
# refuses anything else rather than guessing at the field order.
QR_PREFIX = "GIF1"
QR_PREFIX_V2 = "GIF2"
QR_SEP = "|"

# A4 portrait, millimetres.
PAGE_W, PAGE_H = 210.0, 297.0
MARGIN = 12.0

# ⚠️ THE RENDERER AND THE READER SHARE ONE GEOMETRY. Slice 9d rectifies a phone
# photo onto this coordinate system and crops each row by these numbers, so a
# layout tweak that lived only in `render_pdf` would silently start cropping the
# wrong strip of somebody's handwriting. Everything positional is here.
ROW_H = 11.0
ROWS_PER_PAGE = 18
HEADER_FIELD_Y = MARGIN + 30.0
FIELD_BOX_H = 9.0
TABLE_HEAD_H = 7.0
FIRST_ROW_Y = HEADER_FIELD_Y + 3.6 + FIELD_BOX_H + 6.0 + TABLE_HEAD_H

C_NO, C_QTY, C_UOM, C_LOT = 11.0, 30.0, 16.0, 38.0
C_NAME = (PAGE_W - 2 * MARGIN) - C_NO - C_UOM - C_QTY - C_LOT

# Corner registration marks. Four known points let a photo be rectified onto the
# page; the QR alone gives four points too, but they are all within 26 mm of one
# corner, so extrapolating a homography from them across an A4 sheet magnifies
# every pixel of detection error. Marks at the corners bound the error instead
# of amplifying it.
FIDUCIAL = 6.0
FIDUCIAL_INSET = 4.0


def qr_payload(*, form_uuid: str, site_id: str, code: str, esc: str,
               sheet_seq: int = 1, sheet_of: int = 1) -> str:
    """`GIF2|<site>|<system>|<sub-activity>|<uuid>|<sheet>|<of>`.

    Deliberately NOT JSON: a QR's capacity is the constraint that matters on a
    printed page, and braces and quotes buy nothing a decoder cannot get from
    six separators. `esc` may be empty (a whole-system form) and the field is
    still present, so the payload always has exactly seven parts.

    ⚠️ WHY THE VERSION MOVED FROM `GIF1` TO `GIF2` (Phase 13b, ruling Q13-2).
    A form of more than 18 materials prints as several A4 pages, and until now
    every one of those pages carried the SAME payload. The reader identifies a
    sheet by that payload, so it could not tell page 2 from page 1: the second
    photograph of a three-page form was refused as "already filed", and a
    supervisor who photographed only page 2 got an entry with page 1's rows
    silently zeroed. Adding the sheet number is what makes a multi-page form
    filable a page at a time.

    ⚠️ THE FORM IDENTITY DID NOT CHANGE. All pages of one form still carry ONE
    `form_uuid` — they are one sheet of paper's worth of work in the registry,
    one fingerprint, one entry. `sheet_seq` says WHICH PAGE of it this is; it
    is not a second identity, and `parse_qr` is where the two are kept apart.

    ⚠️ AND `GIF1` PAPER STILL WORKS. `parse_qr` accepts both, because the
    version tag exists exactly so a decoder can say "this is older" rather than
    guess at the field order — and because a plant can hold months of printed
    blanks. See `parse_qr`.
    """
    parts = [QR_PREFIX_V2, site_id, code, esc or "", form_uuid,
             str(int(sheet_seq)), str(int(sheet_of))]
    for p in parts:
        if QR_SEP in str(p):
            raise HTTPException(
                422, f"{p!r} contains the QR separator {QR_SEP!r} and cannot be "
                     f"encoded on a form — rename it or use a different code")
    return QR_SEP.join(str(p) for p in parts)


def parse_qr(payload: str) -> dict:
    """The decoder half, written here beside the encoder so the two cannot
    drift. Slice 9d calls this on every upload.

    ⚠️ TWO VERSIONS, AND BOTH ARE LIVE PAPER.

    * `GIF2` (Phase 13b) — seven fields, the last two being the sheet number
      and the sheet count.
    * `GIF1` (Phase 9c) — five fields, no sheet number. Read as **sheet 1 of
      1**, which is what it always meant: one QR for the whole form.

    A plant holds months of printed blanks, so refusing `GIF1` on the day
    `GIF2` shipped would have invalidated every sheet already in somebody's
    pocket for no gain. The version tag exists precisely so a decoder can say
    "this is an older form" instead of guessing at the field order — an
    unknown prefix is still refused rather than parsed hopefully.

    ⚠️ A `GIF1` SHEET READ AS 1-OF-1 IS NOT A LIE — it is what the paper says.
    An old multi-page form genuinely carries no way to tell its pages apart,
    which is the defect 13b fixes; the intake treats such a form exactly as it
    did before, refusing the second photograph. Reading a missing field as
    "sheet 1 of many" would be the guess, and would file page 2's quantities
    against page 1's materials.
    """
    parts = str(payload or "").split(QR_SEP)
    if len(parts) == 7 and parts[0] == QR_PREFIX_V2:
        try:
            seq, of = int(parts[5]), int(parts[6])
        except ValueError:
            raise HTTPException(
                422, f"this {QR_PREFIX_V2} code carries a sheet number that is "
                     f"not a number ({parts[5]!r} of {parts[6]!r})") from None
        if seq < 1 or of < 1 or seq > of:
            raise HTTPException(
                422, f"this {QR_PREFIX_V2} code says it is sheet {seq} of {of}, "
                     f"which cannot be right. Print a fresh form.")
        return {"site_id": parts[1], "lining_system_code": parts[2],
                "esc": parts[3], "form_uuid": parts[4],
                "sheet_seq": seq, "sheet_of": of, "qr_version": QR_PREFIX_V2}
    if len(parts) == 5 and parts[0] == QR_PREFIX:
        return {"site_id": parts[1], "lining_system_code": parts[2],
                "esc": parts[3], "form_uuid": parts[4],
                "sheet_seq": 1, "sheet_of": 1, "qr_version": QR_PREFIX}
    raise HTTPException(
        422, "this QR code is not a GI consumption form (expected a "
             f"{QR_PREFIX_V2} payload with seven fields, or a {QR_PREFIX} one "
             f"with five)")


def page_count(n_rows: int) -> int:
    """How many A4 sheets a form of `n_rows` materials actually prints on.

    ⚠️ IT IS NOT ALWAYS `ceil(n / 18)`. The "filled in by / signature" block is
    laid out after the last row, and when the last page has no room left for it
    the block moves to a page of its own. Slice 9c's renderer already did that
    — and then labelled the extra page **"page 4 of 3"**, because the count and
    the layout were computed in two places.

    ⚠️ THAT COSMETIC BUG BECAME A REAL ONE IN 13b. `sheet_of` travels inside
    the QR now, and `parse_qr` refuses a payload claiming to be sheet 4 of 3 —
    correctly, because a sheet numbered past the end of its own form is exactly
    the corruption that check exists to catch. So the count has one home, here,
    and the renderer asks it rather than deriving a second answer.
    """
    n_rows = max(0, int(n_rows))
    pages = max(1, (n_rows + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE)
    on_last = n_rows - (pages - 1) * ROWS_PER_PAGE
    y = FIRST_ROW_Y + on_last * ROW_H + 6.0
    # Mirrors the renderer's own test, and is the only copy of these numbers.
    return pages + 1 if y + 20.0 > PAGE_H - MARGIN - 8 else pages


def sheet_row_span(sheet_seq: int) -> tuple[int, int]:
    """The 1-based PRINTED row numbers that live on sheet `sheet_seq`.

    ⚠️ THE ROW NUMBERS ON A FORM RUN STRAIGHT THROUGH ITS PAGES — page 2 of a
    36-material form is numbered 19 to 36, not 1 to 18 again. That is what lets
    `_match_rows` stay positional across a multi-page form: the model reads the
    number PRINTED at the left of each row, so a quantity from page 2 already
    knows it is row 23. This function is the range check on top of that, so a
    model that hallucinates row 5 while looking at page 2 is dropped rather
    than believed.
    """
    lo = (int(sheet_seq) - 1) * ROWS_PER_PAGE + 1
    return lo, lo + ROWS_PER_PAGE - 1


async def recipe_rows(session: AsyncSession, *, code: str,
                      esc: Optional[str] = None) -> list[dict]:
    """The materials to print, in the order they will be printed.

    ⚠️ THE SORT IS PART OF THE DATA CONTRACT, not presentation. Row N on the
    paper is row N of this list forever, because that is how 9d maps a
    handwritten quantity back to a material. Sorted by sub-activity, then
    material code, then SAP — all three, so a system with two rows sharing a
    material code (seven of them do) still has a total order rather than a
    stable-sort accident.
    """
    stmt = select(
        recipe_t.c["Execution_Sub_Activity_Code"], recipe_t.c["Material_Code"],
        recipe_t.c["SAP_Code"], recipe_t.c["Material_Name"],
        recipe_t.c["Material_Description"], recipe_t.c["UOM"],
        recipe_t.c["For_1_SQM"], recipe_t.c["Package_Size"],
    ).where(recipe_t.c["Lining_System_Code"] == code)
    if esc:
        stmt = stmt.where(recipe_t.c["Execution_Sub_Activity_Code"] == esc)
    rows = [dict(r) for r in (await session.execute(stmt)).mappings().all()]
    rows.sort(key=lambda r: (str(r["Execution_Sub_Activity_Code"] or ""),
                             str(r["Material_Code"] or ""),
                             str(r["SAP_Code"] or "")))

    # Which material codes appear more than once in THIS form. Only those need
    # their description printed to stay distinguishable; adding it everywhere
    # would push the real names off the line for no gain.
    seen: dict[str, int] = {}
    for r in rows:
        mc = str(r["Material_Code"] or "")
        seen[mc] = seen.get(mc, 0) + 1
    for r in rows:
        r["needs_qualifier"] = seen.get(str(r["Material_Code"] or ""), 0) > 1
    return rows


def fingerprint(rows: list[dict]) -> str:
    """A hash of exactly what the paper says, in the order it says it.

    ⚠️ ROW ORDER IS INSIDE THE HASH, because the mapping is positional. Two
    forms listing the same materials in a different order are NOT the same
    form, and a recipe edit that merely reorders would otherwise pass a
    same-set check and mis-file every quantity by one.

    `For_1_SQM` is deliberately NOT hashed: the rate affects the benchmark
    comparison, never which box the supervisor writes in. Including it would
    invalidate printed paper for a change that cannot mis-map anything.
    """
    h = hashlib.sha256()
    for i, r in enumerate(rows):
        h.update(f"{i}\x1f{r.get('Execution_Sub_Activity_Code') or ''}\x1f"
                 f"{r.get('Material_Code') or ''}\x1f{r.get('SAP_Code') or ''}\x1f"
                 f"{r.get('UOM') or ''}\x1e".encode())
    return h.hexdigest()[:32]


def _row_label(r: dict) -> tuple[str, str]:
    """(what the material is, the small print under it).

    The qualifier only appears where it disambiguates — see the module
    docstring on LSC8's four identical Cumicrete rows.
    """
    name = str(r.get("Material_Name") or r.get("Material_Code") or "").strip()
    desc = str(r.get("Material_Description") or "").strip()
    if r.get("needs_qualifier") and desc:
        name = f"{name} — {desc}"
    bits = [str(r.get("Material_Code") or "").strip()]
    if r.get("SAP_Code"):
        bits.append(f"SAP {str(r['SAP_Code']).strip()}")
    if r.get("Package_Size"):
        bits.append(f"pack {str(r['Package_Size']).strip()}")
    return name, "   ·   ".join(b for b in bits if b)


def _qr_png(data: str, box: int = 6):
    import qrcode
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_Q,
                       box_size=box, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    buf.seek(0)
    return buf


def _field(pdf, x: float, y: float, w: float, label: str, *,
           h: float = 9.0) -> None:
    """A labelled box for a human to write in.

    A RULED BOX, not an underline: a box tells the writer where the field ends,
    and it gives the vision model a rectangle to look inside instead of a
    baseline to guess from. Both readers are better off.
    """
    pdf.set_font("helvetica", "", 7)
    pdf.set_text_color(110, 110, 110)
    pdf.set_xy(x, y)
    pdf.cell(w, 3.4, _txt(label.upper()))
    pdf.set_draw_color(60, 60, 60)
    pdf.set_line_width(0.35)
    pdf.rect(x, y + 3.6, w, h)


def _new_pdf():
    """An empty A4 document with this module's page settings.

    ⚠️ ONE CONSTRUCTOR, because Phase 13a draws MANY forms into ONE document
    and a second set of `set_margins` / `set_auto_page_break` calls is a second
    place for the page geometry to drift from `row_boxes()`. The reader crops
    handwriting off these numbers.
    """
    from fpdf import FPDF

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    return pdf


def _draw_form(pdf, *, rows: list[dict], site_id: str, code: str, esc: str,
               system_name: str, form_uuid: str, generated_on: _dt.date,
               batch_seq: int = 1, batch_size: int = 1) -> None:
    """Draw ONE form — all of its A4 pages — into an existing document.

    ⚠️ NO WRITE-IN ROWS (ruling Q9). Supervisors use only recipe-defined
    materials — they may write 0, but they never introduce an outside one — so
    a blank row would be an invitation to write a name the system cannot map
    and 9d cannot resolve. Its absence is the rule made physical.

    ⚠️ PHASE 13a SPLIT THIS OUT OF `render_pdf` AND CHANGED NO GEOMETRY. Fifty
    forms in one download are fifty calls to this function against one `pdf`,
    each with its own `form_uuid` and therefore its own QR. Every constant the
    rectifier uses — `FIRST_ROW_Y`, `ROW_H`, `HEADER_FIELD_Y`, the fiducials —
    is untouched, and the only new ink is a sheet label drawn in the empty band
    between the QR and the header fields. A layout tweak here silently starts
    cropping the wrong strip of somebody's handwriting, so the sheet label was
    placed where nothing had to move.
    """
    inner = PAGE_W - 2 * MARGIN

    # Column geometry, shared by the header band and every row.

    # ⚠️ ONE COUNT, ASKED FOR — never re-derived. It includes the signature
    # page when the last row leaves no room for the block, which is why the
    # old inline `ceil(n / 18)` printed "page 4 of 3" on those forms.
    pages = page_count(len(rows))

    # ⚠️ ONE QR PER PAGE, NOT ONE PER FORM (Phase 13b). All pages share the
    # `form_uuid` — they are one form — but each carries its own sheet number,
    # so the reader can tell page 2 from page 1. Before this, every page of a
    # 36-material form was byte-identical to the reader: photographing the
    # second one was refused as "already filed", and photographing ONLY the
    # second one produced an entry with page 1's eighteen rows silently zeroed.
    qr_bufs = [_qr_png(qr_payload(form_uuid=form_uuid, site_id=site_id,
                                  code=code, esc=esc, sheet_seq=p,
                                  sheet_of=pages))
               for p in range(1, pages + 1)]

    def _fiducials() -> None:
        """Four solid corner squares, at known page coordinates.

        ⚠️ NOT DECORATION. Slice 9d rectifies a hand-held photo onto this page's
        millimetre grid before it crops a row, and a homography needs four
        correspondences. The QR supplies four — but all within 26 mm of one
        corner, so every pixel of detection error is multiplied by the distance
        to the far side of an A4 sheet. Corner marks bound that error instead of
        amplifying it. Kept solid black and clear of every writable box.
        """
        pdf.set_fill_color(0, 0, 0)
        for fx, fy in ((FIDUCIAL_INSET, FIDUCIAL_INSET),
                       (PAGE_W - FIDUCIAL_INSET - FIDUCIAL, FIDUCIAL_INSET),
                       (FIDUCIAL_INSET, PAGE_H - FIDUCIAL_INSET - FIDUCIAL),
                       (PAGE_W - FIDUCIAL_INSET - FIDUCIAL,
                        PAGE_H - FIDUCIAL_INSET - FIDUCIAL)):
            pdf.rect(fx, fy, FIDUCIAL, FIDUCIAL, style="F")

    def _page_header(page_no: int) -> float:
        pdf.add_page()
        _fiducials()
        qr_size = 26.0
        # ⚠️ THIS PAGE'S OWN QR. Indexed rather than shared: the sheet number
        # inside it is the only thing that tells the reader which page of the
        # form it is looking at.
        pdf.image(qr_bufs[min(page_no, len(qr_bufs)) - 1],
                  x=PAGE_W - MARGIN - qr_size, y=MARGIN, w=qr_size, h=qr_size)

        pdf.set_xy(MARGIN, MARGIN)
        pdf.set_font("helvetica", "B", 14)
        pdf.set_text_color(10, 25, 47)
        pdf.cell(inner - 30, 7, "GENERAL INDUSTRIES", new_x="LMARGIN",
                 new_y="NEXT")
        pdf.set_font("helvetica", "B", 10)
        pdf.set_text_color(60, 60, 60)
        pdf.cell(inner - 30, 5, "DAILY CONSUMPTION - SURFACE SHIELD",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(90, 90, 90)
        pdf.cell(inner - 30, 5, _txt(
            f"{code}   {system_name}" + (f"   {esc}" if esc else "")),
            new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("helvetica", "", 8)
        pdf.cell(inner - 30, 4, _txt(f"Site {site_id}"), new_x="LMARGIN",
                 new_y="NEXT")

        # ── "SHEET 7 OF 50" (Phase 13a) ───────────────────────────────────
        # ⚠️ FOR THE HUMAN SORTING THE PILE, NOT FOR THE MACHINE. The QR is
        # what identifies this sheet to slice 9d; this label is what lets a
        # supervisor holding fifty pieces of paper see that number 23 is
        # missing. Printed only for a real batch — a single download is a
        # batch of one and saying so on the page would be noise.
        #
        # Drawn in the band between the QR (which ends at MARGIN + 26) and
        # HEADER_FIELD_Y (MARGIN + 30). Nothing else occupies it, so no box
        # the rectifier knows about moves by a millimetre.
        if batch_size > 1:
            pdf.set_font("helvetica", "B", 8)
            pdf.set_text_color(10, 25, 47)
            pdf.set_xy(MARGIN, MARGIN + 26.2)
            pdf.cell(inner, 3.4, f"SHEET {batch_seq} OF {batch_size}",
                     align="R")

        # ── the fields a human fills in ────────────────────────────────────
        # ⚠️ THREE header fields, not four. The Lot/Batch is a per-row COLUMN —
        # see the module docstring. Each material comes from its own batch, and
        # one box at the top can only ever be right about one of them.
        y = HEADER_FIELD_Y
        gap = 5.0
        w3 = (inner - 2 * gap) / 3
        _field(pdf, MARGIN, y, w3, "Date (dd/mm/yy)")
        _field(pdf, MARGIN + (w3 + gap), y, w3, "Equipment / Tank No.")
        _field(pdf, MARGIN + 2 * (w3 + gap), y, w3, "Area done (m2)")
        y += 3.6 + 9.0 + 6.0

        # ── the table head ────────────────────────────────────────────────
        pdf.set_fill_color(232, 236, 242)
        pdf.set_draw_color(140, 140, 140)
        pdf.set_line_width(0.25)
        pdf.rect(MARGIN, y, inner, TABLE_HEAD_H, style="FD")
        pdf.set_font("helvetica", "B", 8)
        pdf.set_text_color(30, 40, 60)
        for label, x, w, align in (
                ("#", MARGIN, C_NO, "C"),
                ("MATERIAL", MARGIN + C_NO, C_NAME, "L"),
                ("UOM", MARGIN + C_NO + C_NAME, C_UOM, "C"),
                ("QTY USED", MARGIN + C_NO + C_NAME + C_UOM, C_QTY, "C"),
                ("LOT / BATCH No.", MARGIN + C_NO + C_NAME + C_UOM + C_QTY,
                 C_LOT, "C")):
            pdf.set_xy(x + 1, y + 2)
            pdf.cell(w - 2, 3, label, align=align)
        if pages > 1:
            pdf.set_font("helvetica", "", 7)
            pdf.set_text_color(130, 130, 130)
            pdf.set_xy(MARGIN, y - 4.5)
            pdf.cell(inner, 3, f"page {page_no} of {pages}", align="R")
        return y + TABLE_HEAD_H

    y = _page_header(1)
    row_h = ROW_H

    for idx, r in enumerate(rows):
        if idx and idx % ROWS_PER_PAGE == 0:
            _footer(pdf, form_uuid, generated_on, len(rows))
            y = _page_header(idx // ROWS_PER_PAGE + 1)

        name, small = _row_label(r)
        # A very light alternating ground. Both readers of this page track
        # across a 186 mm row; a human loses the line and the model loses the
        # row boundary, and one shade fixes both. Kept at 249/250/252 so it
        # survives a photocopy without ever competing with handwriting.
        pdf.set_draw_color(170, 170, 170)
        pdf.set_line_width(0.2)
        if idx % 2:
            pdf.set_fill_color(247, 248, 250)
            pdf.rect(MARGIN, y, inner, row_h, style="FD")
        else:
            pdf.rect(MARGIN, y, inner, row_h)
        pdf.line(MARGIN + C_NO, y, MARGIN + C_NO, y + row_h)
        pdf.line(MARGIN + C_NO + C_NAME, y, MARGIN + C_NO + C_NAME, y + row_h)
        pdf.line(MARGIN + C_NO + C_NAME + C_UOM, y,
                 MARGIN + C_NO + C_NAME + C_UOM, y + row_h)

        # The number the OCR maps by. Printed large and alone in its column so
        # it is never confused with a handwritten figure.
        pdf.set_font("helvetica", "B", 10)
        pdf.set_text_color(40, 40, 40)
        pdf.set_xy(MARGIN, y + 3)
        pdf.cell(C_NO, 5, str(idx + 1), align="C")

        pdf.set_font("helvetica", "B", 8.5)
        pdf.set_text_color(20, 20, 20)
        pdf.set_xy(MARGIN + C_NO + 2, y + 1.6)
        pdf.cell(C_NAME - 4, 4, _txt(_fit(pdf, name, C_NAME - 4)))
        pdf.set_font("helvetica", "", 6.5)
        pdf.set_text_color(120, 120, 120)
        pdf.set_xy(MARGIN + C_NO + 2, y + 6.0)
        pdf.cell(C_NAME - 4, 3.5, _txt(_fit(pdf, small, C_NAME - 4)))

        pdf.set_font("helvetica", "", 8)
        pdf.set_text_color(60, 60, 60)
        pdf.set_xy(MARGIN + C_NO + C_NAME, y + 3)
        pdf.cell(C_UOM, 5, _txt(str(r.get("UOM") or "")), align="C")

        # ⚠️ TWO WHITE BOXES ARE ALL THE MODEL HAS TO READ. Clean rectangles
        # with a heavy border and nothing inside — no rule, no hint text, no
        # shading for a digit to sit on top of. The zebra ground stops at their
        # edge for the same reason.
        pdf.set_fill_color(255, 255, 255)
        pdf.set_draw_color(40, 40, 40)
        pdf.set_line_width(0.4)
        pdf.rect(MARGIN + C_NO + C_NAME + C_UOM + 2.5, y + 1.6,
                 C_QTY - 5, row_h - 3.2, style="FD")
        pdf.rect(MARGIN + C_NO + C_NAME + C_UOM + C_QTY + 2.5, y + 1.6,
                 C_LOT - 5, row_h - 3.2, style="FD")
        y += row_h

    # ── who filled it in ───────────────────────────────────────────────────
    # ⚠️ NOT DECORATION. The supervisor's name on the paper is the only
    # cross-check slice 9d has that the person who UPLOADS a form is the person
    # who filled it — the QR proves which sheet, never who wrote on it. It also
    # matches how these forms already work on site: an unsigned one is not a
    # record anybody there would accept.
    y += 6.0
    if y + 20.0 > PAGE_H - MARGIN - 8:
        _footer(pdf, form_uuid, generated_on, len(rows), batch_seq, batch_size)
        # `page_count` already reserved this page — see its docstring for the
        # "page 4 of 3" it used to print here.
        y = _page_header(pages)
    half = (inner - 6.0) / 2
    _field(pdf, MARGIN, y, half, "Filled in by (name)")
    _field(pdf, MARGIN + half + 6.0, y, half, "Signature")

    _footer(pdf, form_uuid, generated_on, len(rows), batch_seq, batch_size)


def render_pdf(*, rows: list[dict], site_id: str, code: str, esc: str,
               system_name: str, form_uuid: str,
               generated_on: Optional[_dt.date] = None,
               batch_seq: int = 1, batch_size: int = 1) -> bytes:
    """One form, as a standalone PDF. One A4 page per 18 materials."""
    pdf = _new_pdf()
    _draw_form(pdf, rows=rows, site_id=site_id, code=code, esc=esc,
               system_name=system_name, form_uuid=form_uuid,
               generated_on=generated_on or _dt.date.today(),
               batch_seq=batch_seq, batch_size=batch_size)
    return bytes(pdf.output())


def render_batch_pdf(*, rows: list[dict], site_id: str, code: str, esc: str,
                     system_name: str, form_uuids: list[str],
                     generated_on: Optional[_dt.date] = None) -> bytes:
    """N forms in ONE document, each with its own `Form_UUID` and its own QR.

    ⚠️ THE UUIDS ARE THE DELIVERABLE. A supervisor who needs fifty sheets used
    to print one and photocopy it, which duplicated the QR — and slice 9d maps
    handwriting to materials POSITIONALLY off that identity, so fifty identical
    QRs meant the intake could not tell one tank's page from another's, nor a
    re-print from a re-photograph. This function is the answer to that, and the
    invariant worth testing is simply `len(set(form_uuids)) == len(form_uuids)`.

    ⚠️ ONE DOCUMENT, NOT N DOCUMENTS MERGED. fpdf2 cannot merge, and pulling in
    a merge library to concatenate pages it just wrote would be a dependency
    bought for nothing — the pages are drawn straight into one `FPDF`. It also
    keeps the output a single stream the browser can hand to a printer, which
    is what the operator actually asked for.
    """
    generated_on = generated_on or _dt.date.today()
    pdf = _new_pdf()
    n = len(form_uuids)
    for seq, form_uuid in enumerate(form_uuids, start=1):
        _draw_form(pdf, rows=rows, site_id=site_id, code=code, esc=esc,
                   system_name=system_name, form_uuid=form_uuid,
                   generated_on=generated_on, batch_seq=seq, batch_size=n)
    return bytes(pdf.output())


def _fit(pdf, text: str, width: float) -> str:
    """Truncate to `width` mm at the current font. `cell()` does NOT clip — a
    long chemical name is simply drawn over its neighbour — which is the exact
    defect `pdf_tables.py` was written to end. Same rule, one column."""
    t = _txt(text or "")
    if pdf.get_string_width(t) <= width:
        return t
    # "..." not "…" — the ellipsis is appended AFTER transliteration, so a
    # single-character one would reach fpdf's latin-1 core font unconverted and
    # raise on the one row long enough to need truncating.
    while t and pdf.get_string_width(t + "...") > width:
        t = t[:-1]
    return t + "..."


def _footer(pdf, form_uuid: str, generated_on: _dt.date, n: int,
            batch_seq: int = 1, batch_size: int = 1) -> None:
    """The generation date and the form id, in words as well as in the QR.

    ⚠️ THIS IS NOT THE WORK DATE. It says when the blank was printed, so a
    supervisor can see they are holding last month's paper. The work date is
    the empty box at the top, and pre-filling it would be wrong on every form
    used the day after it was printed — see the module docstring.
    """
    sheet = f"   ·   sheet {batch_seq} of {batch_size}" if batch_size > 1 else ""
    pdf.set_font("helvetica", "", 6.5)
    pdf.set_text_color(150, 150, 150)
    pdf.set_xy(MARGIN, PAGE_H - MARGIN - 4)
    pdf.cell(PAGE_W - 2 * MARGIN, 3.5, _txt(
        f"Form {form_uuid}{sheet}   ·   {n} material line(s)   ·   blank printed "
        f"{generated_on.isoformat()} — write the WORK date in the box above   "
        f"·   photograph the whole page, including the QR code"))


# ── the page geometry, as data the reader can use ────────────────────────────
def fiducial_points() -> list[tuple[float, float]]:
    """The four registration marks' CENTRES, in page millimetres, in the order
    top-left, top-right, bottom-left, bottom-right. The rectifier matches these
    against what it finds in the photo."""
    h = FIDUCIAL / 2
    return [(FIDUCIAL_INSET + h, FIDUCIAL_INSET + h),
            (PAGE_W - FIDUCIAL_INSET - h, FIDUCIAL_INSET + h),
            (FIDUCIAL_INSET + h, PAGE_H - FIDUCIAL_INSET - h),
            (PAGE_W - FIDUCIAL_INSET - h, PAGE_H - FIDUCIAL_INSET - h)]


def row_boxes(row_index: int) -> dict:
    """Where row `row_index` (0-based, across pages) sits on its page, in mm.

    ⚠️ COMPUTED FROM THE SAME CONSTANTS THE RENDERER USES, never from a second
    copy of the numbers. A crop offered to a human as "what the camera saw on
    line 3" that is actually line 4 is worse than no crop at all: it invites
    them to confirm a quantity against the wrong material.

    Returns page (1-based) plus rectangles for the whole row, the quantity box
    and the lot box.
    """
    page = row_index // ROWS_PER_PAGE + 1
    slot = row_index % ROWS_PER_PAGE
    y = FIRST_ROW_Y + slot * ROW_H
    inner = PAGE_W - 2 * MARGIN
    qx = MARGIN + C_NO + C_NAME + C_UOM
    lx = qx + C_QTY
    return {
        "page": page,
        "row": (MARGIN, y, inner, ROW_H),
        "qty": (qx + 2.5, y + 1.6, C_QTY - 5, ROW_H - 3.2),
        "lot": (lx + 2.5, y + 1.6, C_LOT - 5, ROW_H - 3.2),
    }


def header_boxes() -> dict:
    """The three hand-filled header fields, in page millimetres."""
    inner = PAGE_W - 2 * MARGIN
    gap = 5.0
    w3 = (inner - 2 * gap) / 3
    y = HEADER_FIELD_Y + 3.6
    return {
        "work_date": (MARGIN, y, w3, FIELD_BOX_H),
        "equipment": (MARGIN + w3 + gap, y, w3, FIELD_BOX_H),
        "area_sqm": (MARGIN + 2 * (w3 + gap), y, w3, FIELD_BOX_H),
    }


# ⚠️ THE CEILING IS A DELIVERY CONSTRAINT, NOT A ROUND NUMBER. 200 forms of a
# 40-line recipe is 600 A4 pages, each carrying its own QR bitmap — tens of
# megabytes over plant Wi-Fi. A download that dies at 80 % leaves the registry
# holding 200 rows for paper nobody ever received, and every one of them then
# shows as an outstanding sheet in `/execution/forms/generated`. The cap is
# where the failure stops being recoverable by pressing the button again.
MAX_COPIES = 200


async def generate(session: AsyncSession, *, site_id: str, code: str,
                   esc: Optional[str], username: str, role: str,
                   copies: int = 1) -> tuple[bytes, dict]:
    """Register `copies` forms, then render them into ONE PDF.

    Returns `(pdf_bytes, registry_row)` where the row describes the FIRST form
    and carries the batch fields, so a caller that only ever printed one sheet
    sees exactly what it saw before Phase 13a.

    ⚠️ REGISTERED BEFORE THEY ARE RENDERED, and the rows are what the QRs point
    at. A PDF that reached somebody's printer without rows would be paper the
    system cannot recognise on the way back in — and the failure would surface
    in slice 9d as an unreadable upload, a long way from the cause.

    ⚠️ AND ALL OF IT IS ONE TRANSACTION, which is the caller's (`session.begin()`
    in the router). Fifty half-registered sheets is the worst outcome available
    here: the paper exists, the plant fills it in, and the intake refuses the
    ones whose rows were rolled back. Because the render runs INSIDE that
    transaction, a renderer that raises on sheet 37 takes all 37 rows with it.

    ⚠️ EVERY FORM GETS ITS OWN `Form_UUID`, WHICH IS THE ENTIRE FEATURE. The
    thing this replaces is a photocopier, and a photocopy duplicates the QR.
    """
    if copies < 1 or copies > MAX_COPIES:
        raise HTTPException(
            422, f"forms must be between 1 and {MAX_COPIES} — {copies} is "
                 f"outside that. Print in runs rather than one very large "
                 f"download: a transfer that fails halfway leaves paper the "
                 f"system has no rows for.")

    rows = await recipe_rows(session, code=code, esc=esc)
    if not rows:
        raise HTTPException(
            404, f"{code}" + (f" / {esc}" if esc else "")
                 + " has no materials in the recipe, so there is nothing to "
                   "print. Check the system code, or add its materials in the "
                   "Material Estimator first.")

    sysname = (await session.execute(
        select(recipe_t.c["Lining_System_Name"])
        .where(recipe_t.c["Lining_System_Code"] == code).limit(1))).scalar() or code

    # One fingerprint for the whole run: these are the same recipe rows, in the
    # same order, read once. Re-reading per sheet would let a master-data edit
    # land mid-batch and hand out two different row orders under one batch id.
    fp = fingerprint(rows)
    batch_uuid = uuid.uuid4().hex[:16].upper()
    form_uuids = [uuid.uuid4().hex[:16].upper() for _ in range(copies)]

    ids: list[int] = []
    for seq, form_uuid in enumerate(form_uuids, start=1):
        ids.append((await session.execute(insert(form_t).values(
            Form_UUID=form_uuid, Site_ID=site_id, Lining_System_Code=code,
            Execution_Sub_Activity_Code=(esc or ""), Recipe_Fingerprint=fp,
            Row_Count=len(rows), status="open", created_by=username,
            created_by_role=role, Batch_UUID=batch_uuid, Batch_Seq=seq,
            Batch_Size=copies).returning(form_t.c["id"]))).scalar_one())

    if copies == 1:
        pdf = render_pdf(rows=rows, site_id=site_id, code=code, esc=(esc or ""),
                         system_name=str(sysname), form_uuid=form_uuids[0])
    else:
        pdf = render_batch_pdf(rows=rows, site_id=site_id, code=code,
                               esc=(esc or ""), system_name=str(sysname),
                               form_uuids=form_uuids)

    # One audit line per RUN, naming the batch and the count — not one line per
    # sheet. Fifty audit rows for one button press buries the next real event.
    await write_audit(session, username, "CONSUMPTION_FORM_PRINT",
                      "sme_consumption_form",
                      f"{batch_uuid} x{copies} {code}"
                      f"{'/' + esc if esc else ''} @{site_id} "
                      f"{len(rows)} rows · first {form_uuids[0]}")
    return pdf, {"id": ids[0], "Form_UUID": form_uuids[0], "Site_ID": site_id,
                 "Lining_System_Code": code,
                 "Execution_Sub_Activity_Code": esc or "",
                 "Recipe_Fingerprint": fp, "Row_Count": len(rows),
                 "status": "open", "Batch_UUID": batch_uuid,
                 "Batch_Size": copies, "Form_UUIDs": form_uuids,
                 "ids": ids}


async def available_systems(session: AsyncSession) -> list[dict]:
    """Lining systems that have a recipe, and their sub-activities.

    Drives the picker. A system with no recipe is omitted rather than offered
    and then refused — a menu entry that always errors is worse than no entry.
    """
    rows = (await session.execute(
        select(recipe_t.c["Lining_System_Code"],
               recipe_t.c["Lining_System_Name"],
               recipe_t.c["Execution_Sub_Activity_Code"]))).mappings().all()
    by_code: dict[str, dict] = {}
    for r in rows:
        code = str(r["Lining_System_Code"] or "").strip()
        if not code:
            continue
        slot = by_code.setdefault(code, {
            "Lining_System_Code": code,
            "Lining_System_Name": str(r["Lining_System_Name"] or code),
            "sub_activities": set(), "materials": 0})
        slot["materials"] += 1
        if r["Execution_Sub_Activity_Code"]:
            slot["sub_activities"].add(str(r["Execution_Sub_Activity_Code"]))
    return [{**v, "sub_activities": sorted(v["sub_activities"])}
            for v in sorted(by_code.values(),
                            key=lambda x: x["Lining_System_Code"])]
