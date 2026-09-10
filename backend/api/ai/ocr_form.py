"""
backend/api/ai/ocr_form.py — Phase 9d: reading a photographed consumption form.

THE JOB IS SMALL BY DESIGN, and that is the whole point of slice 9c. The form
prints every material name, so the model never reads one. The QR carries the
site, the system, the sub-activity and the sheet's identity, so the model never
reads header context either. What is left is: a date, an equipment tag, an area,
and per numbered row a quantity and a lot — all handwritten, all in ruled boxes.

⚠️ THE QR IS DECODED, NOT INTERPRETED. `cv2.QRCodeDetector` returns a string or
it does not. There is no "probably LSC8". Everything the QR carries is therefore
exact, and the four things most damaging to get wrong — which site, which system,
which sub-activity, which sheet — cannot be got wrong at all.

⚠️ A NULL IS AN ANSWER; A GUESS IS NOT. `quantity` comes back null whenever the
digits are not unambiguous, and the raw string is kept beside it. The existing
consumption prompt already states the rule — "NEVER invent 0 or 1 … a null is a
question" — and it matters more here, because these numbers post straight to
stock on approval. A null renders as an empty required field the supervisor must
fill; an invented 1 renders as a number nobody questions.

⚠️ ROW MAPPING IS POSITIONAL, AND THE FINGERPRINT IS WHAT MAKES THAT SAFE.
Row 3 on the paper is row 3 of `consumption_form.recipe_rows()`. If the recipe
changed after the sheet was printed the hash no longer matches and the upload is
REFUSED — reading row 3's handwriting into row 3 of a different list would file
a real quantity against the wrong material, and the result would look entirely
reasonable.

────────────────────────────────────────────────────────────────────────────
RECTIFICATION, and why the form has corner marks.

A phone photo of a sheet on a bench is a perspective projection, not a scan. To
show a human "here is the box your number came from" the photo has to be mapped
onto the page's millimetre grid — a homography, which needs four point
correspondences. Slice 9c prints four corner fiducials for exactly this; the QR
alone would also give four points, but all within 26 mm of one corner, so every
pixel of detection error is multiplied by the distance across an A4 sheet.

When the corners cannot be found (a folded page, a thumb over one) the crop
falls back to the QR quad, and when that fails too the row crop is simply
unavailable — the review UI then shows the whole image and says so. A crop
offered as "line 3" that is actually line 4 would be worse than no crop at all.
"""
from __future__ import annotations

import base64
import json
import re
from typing import Optional

from fastapi import HTTPException

from ..services import consumption_form as CF
from . import client as aic
from .ocr import (ImagePrepError, estimate_image_tokens,
                  extract_json_object, prep_image_for_vision)

# How many pixels wide the rectified page is rendered at. 8 px/mm gives a
# ~1680x2380 page, which is enough for a legible crop of an 8 mm-tall box
# without holding a 40 MB array per upload.
PX_PER_MM = 8.0

# ⚠️ SIZED FOR THE LONGEST RECIPE, NOT THE SAMPLE SHEET. The output is five
# short fields per printed row plus a small header block. A 30-row system is
# ~1,900 tokens, and the old 1,400 default clipped it — which `_clean_json`
# then reported as "did not return a readable result", sending a supervisor to
# retake a photo that was fine. `ocr.salvage_truncated_json` is the second
# guard behind this one; a budget that fits is the first.
FORM_NUM_PREDICT = 2600

_FENCE = re.compile(r"^\s*```(?:json)?|```\s*$", re.M)
_ADDITIVE = re.compile(r"^\d+(?:\.\d+)?(?:\s*\+\s*\d+(?:\.\d+)?)+$")


# ── the prompt ──────────────────────────────────────────────────────────────
# ⚠️ TUNED HARD FOR DIGITS (ruling Q7). Everything a 7B VLM is bad at has been
# removed from the page, so the prompt spends its whole budget on the one task
# left. The rules about ambiguity are not politeness: they are what turns a
# wrong number into a null, and a null into a question a human answers.
FORM_SYSTEM = """\
You are reading a photographed "DAILY CONSUMPTION - SURFACE SHIELD" form from
General Industries. The material names are ALREADY PRINTED on the form. You must
NOT read, transcribe or correct any material name — only the handwriting.

The table has one numbered row per material, with two hand-filled boxes at the
right of each row: QTY USED, then LOT / BATCH No.

Output STRICT JSON, no markdown fences, no prose:
{
  "work_date_text": "the date box EXACTLY as written, or ''",
  "equipment_text": "the Equipment / Tank No. box exactly as written, or ''",
  "area_text":      "the Area done (m2) box exactly as written, or ''",
  "area_sqm":       <number if unambiguous, else null>,
  "filled_by":      "the 'Filled in by' name, or ''",
  "rows": [
    {
      "row": <the printed number at the LEFT of the row>,
      "qty_text": "the QTY USED box exactly as written, '' if empty",
      "quantity": <number if unambiguous, else null>,
      "lot_text":  "the LOT / BATCH box exactly as written, '' if empty",
      "struck_through": <true only if the whole row is crossed out>
    }
  ]
}

RULES — the first three matter more than the rest:
- If a digit is ambiguous, set "quantity" to null and still fill "qty_text".
  NEVER guess between 4 and 9, 1 and 7, or 3 and 8. A null is a question a
  human will answer; a wrong number is posted to stock and nobody asks.
- An EMPTY box is qty_text "" and quantity null. Never invent 0 or 1.
- Return one object per PRINTED row number, in order, including rows left
  blank. Do not skip, merge or re-number them.
- "2+3" goes in qty_text verbatim with quantity null. Decimals use a point.
- Lot numbers are copied character by character. Do not tidy or expand them.
- Ignore the QR code, the header text, the printed material names and the
  footer entirely.
"""

FORM_USER = "Read the handwriting on this form."


def _clean_json(text: str) -> dict:
    """Parse the model's reply, tolerating fences and leading prose.

    ⚠️ RAISES RATHER THAN RETURNING AN EMPTY SHAPE. A form that produced no
    readable rows must fail loudly at extraction; returning `{"rows": []}` would
    hand the supervisor a blank entry that looks merely unfilled, and a blank
    entry submitted is a consumption of zero silently recorded.
    """
    raw = _FENCE.sub("", str(text or "")).strip()
    start = raw.find("{")
    if start < 0:
        raise HTTPException(
            422, "the vision model did not return a readable result for this "
                 "photo. Retake it with the whole page in frame and even "
                 "lighting, or type the entry in by hand.")
    # ⚠️ A CLIPPED REPLY IS NOT AN UNREADABLE PHOTO, and telling the supervisor
    # it was sent them out to re-photograph a sheet the model had read
    # correctly. `extract_json_object` parses the reply and, when it simply
    # stops, rebuilds the complete prefix and drops the unfinished row — see
    # `ocr.salvage_truncated_json` for why the cut is only ever made at a
    # closing bracket.
    out = extract_json_object(raw[start:])
    if out is None:
        raise HTTPException(
            422, "the vision model's answer could not be parsed. Retake the "
                 "photo, or type the entry in by hand.")
    if not isinstance(out, dict) or not isinstance(out.get("rows"), list):
        raise HTTPException(
            422, "the vision model returned no rows for this form. Check the "
                 "whole table is in frame.")
    return out


def _num(value, text: str) -> Optional[float]:
    """A quantity, or None. Never a guess.

    The model is asked for null on ambiguity and usually complies, but the
    string is re-checked here because the two failure modes it does not always
    catch — an additive "2+3" and a stray unit — are both cheap to detect and
    expensive to get wrong.
    """
    if value is not None:
        try:
            f = float(value)
            return f if f >= 0 else None
        except (TypeError, ValueError):
            pass
    t = str(text or "").strip()
    if not t or _ADDITIVE.match(t):
        return None            # additive stays a question for the human
    m = re.fullmatch(r"(\d+(?:\.\d+)?)", t)
    return float(m.group(1)) if m else None


# ── the QR, and the geometry it anchors ─────────────────────────────────────
def _cv():
    try:
        import cv2
        import numpy as np
        return cv2, np
    except ImportError as e:
        raise HTTPException(
            503, "this server cannot decode QR codes (opencv is not "
                 "installed), so a photographed form cannot be matched to the "
                 "sheet it came from. Ask your admin to install opencv-python, "
                 "or type the entry in by hand.") from e


def _detectors(cv2) -> list:
    """The QR detectors to try, best first.

    ⚠️ THE ARUCO DETECTOR IS NOT AN OPTIMISATION — IT IS THE FIX FOR A MEASURED
    1 % OF PRINTED FORMS (2026-09-10, Phase 13a).

    `cv2.QRCodeDetector` — the only detector this function used until now —
    **cannot decode a version-4 symbol at error-correction level Q**, which is
    exactly what `consumption_form._qr_png` emits for a payload of the length a
    form id happens to produce. Measured over 400 freshly-minted forms: 4 of
    them, 1.0 %, were undecodable. The failure is STRUCTURAL, not a resolution
    problem — the existing 2× upscale below does not rescue them, and neither
    does rendering the symbol at 410 px instead of 246. Every one of those same
    symbols decodes first time with `QRCodeDetectorAruco`, and every one is a
    perfectly valid QR (`pyzbar` reads them all).

    ⚠️ IT WAS ALWAYS BROKEN AND BULK PRINTING IS WHAT REVEALED IT. At one sheet
    per download a 1 % failure is a supervisor being told, once in a hundred
    trips, that their photo had no QR on it — indistinguishable from a bad
    photograph, and blamed on the camera. Printing fifty at once turned it into
    a page that failed on every attempt while its forty-nine neighbours worked.

    ⚠️ AND THE FIX BELONGS IN THE READER, NOT THE ENCODER. Changing what is
    printed would repair only paper printed after the change; every sheet
    already in the plant would stay unreadable. Reading better repairs the
    entire backlog, including the run somebody printed this morning.

    Ordered best-first and applied in order rather than replacing the old one:
    the legacy detector is what every historical form was verified against, and
    `QRCodeDetectorAruco` arrived in OpenCV 4.7 — a box with an older build
    still has to work. A build with neither is impossible (the class is only
    absent, never broken), and an absent one is skipped rather than raising.
    """
    out = []
    aruco = getattr(cv2, "QRCodeDetectorAruco", None)
    if aruco is not None:
        try:
            out.append(("aruco", aruco()))
        except Exception:                   # noqa: BLE001 — an old build; fall through
            pass
    out.append(("legacy", cv2.QRCodeDetector()))
    return out


def decode_qr(image_bytes: bytes) -> dict:
    """The four things the model must never have to read.

    ⚠️ EXACT OR NOTHING. A QR either decodes or it does not; there is no
    partial credit and no fuzzy match, which is the entire reason site, system,
    sub-activity and sheet identity travel this way instead of through the
    language model.
    """
    cv2, np = _cv()
    arr = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise HTTPException(422, "that file could not be read as an image.")

    dets = _detectors(cv2)
    payload, points = "", None
    for _name, det in dets:
        payload, points, _ = det.detectAndDecode(arr)
        if payload:
            break
    if not payload:
        # Second pass on a bigger image: a QR photographed from far away can be
        # under the detector's minimum module size at native resolution. Both
        # detectors get the larger image too — the two failure modes are
        # independent, so a symbol can be both far away AND a version the
        # legacy detector cannot read.
        h, w = arr.shape[:2]
        if max(h, w) < 2400:
            big = cv2.resize(arr, None, fx=2.0, fy=2.0,
                             interpolation=cv2.INTER_CUBIC)
            for _name, det in dets:
                payload, points, _ = det.detectAndDecode(big)
                if payload:
                    break
            if points is not None:
                points = points / 2.0
    if not payload:
        raise HTTPException(
            422, "no QR code was found on this photo. The whole page has to be "
                 "in frame, including the square code in the top-right corner "
                 "— it is what tells us which form this is.")
    out = CF.parse_qr(payload)
    out["qr_points"] = (points.reshape(-1, 2).tolist()
                        if points is not None else None)
    return out


def _find_fiducials(arr, np, cv2) -> Optional[list]:
    """The four corner marks, as (x, y) in photo pixels, TL/TR/BL/BR.

    Found by thresholding and keeping near-square dark blobs of roughly the
    right size, then taking the one closest to each corner of the page. Returns
    None unless all four are convincing — three good corners and one wrong one
    is worse than falling back, because a homography accepts it silently.
    """
    h, w = arr.shape[:2]
    grey = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(grey, 0, 255,
                            cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    # A fiducial is 6 mm on a ~210 mm page: between 1.5% and 6% of the shorter
    # edge, and close to square once the perspective is mild.
    lo, hi = 0.012 * min(h, w), 0.070 * min(h, w)
    cands = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        if not (lo <= cw <= hi and lo <= ch <= hi):
            continue
        if not (0.6 <= cw / max(ch, 1) <= 1.6):
            continue
        if cv2.contourArea(c) < 0.55 * cw * ch:      # solid, not an outline
            continue
        cands.append((x + cw / 2.0, y + ch / 2.0))
    if len(cands) < 4:
        return None
    corners = [(0, 0), (w, 0), (0, h), (w, h)]
    picked = []
    for cx, cy in corners:
        best = min(cands, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
        d = ((best[0] - cx) ** 2 + (best[1] - cy) ** 2) ** 0.5
        # A real mark sits within ~15% of the page from its corner. Anything
        # further is some other dark blob and would skew the whole map.
        if d > 0.28 * max(h, w):
            return None
        picked.append(best)
    if len({(round(p[0]), round(p[1])) for p in picked}) != 4:
        return None
    return picked


def rectify(image_bytes: bytes, qr_points=None):
    """Map the photo onto the page's millimetre grid. Returns (array, ok).

    `ok=False` means no reliable map was found and the array is the ORIGINAL
    photo — callers must then decline to offer per-row crops rather than crop
    the wrong strip. See the module docstring.
    """
    cv2, np = _cv()
    arr = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        return None, False
    dst_w = int(CF.PAGE_W * PX_PER_MM)
    dst_h = int(CF.PAGE_H * PX_PER_MM)

    src = _find_fiducials(arr, np, cv2)
    if src is not None:
        dst = [(x * PX_PER_MM, y * PX_PER_MM) for x, y in CF.fiducial_points()]
    elif qr_points and len(qr_points) == 4:
        # Fallback: the QR quad. Its corners are known in page mm because the
        # renderer places it at a fixed size in a fixed corner.
        qr_mm = 26.0
        x0 = CF.PAGE_W - CF.MARGIN - qr_mm
        y0 = CF.MARGIN
        src = [tuple(p) for p in qr_points]
        dst = [((x0) * PX_PER_MM, y0 * PX_PER_MM),
               ((x0 + qr_mm) * PX_PER_MM, y0 * PX_PER_MM),
               ((x0 + qr_mm) * PX_PER_MM, (y0 + qr_mm) * PX_PER_MM),
               ((x0) * PX_PER_MM, (y0 + qr_mm) * PX_PER_MM)]
    else:
        return arr, False

    try:
        m = cv2.getPerspectiveTransform(np.float32(src), np.float32(dst))
        return cv2.warpPerspective(arr, m, (dst_w, dst_h)), True
    except Exception:
        return arr, False


def crop_mm(rect_arr, box: tuple, *, pad_mm: float = 1.5) -> bytes:
    """A PNG of one millimetre rectangle of the rectified page."""
    cv2, np = _cv()
    x, y, w, h = box
    x0 = int(max((x - pad_mm) * PX_PER_MM, 0))
    y0 = int(max((y - pad_mm) * PX_PER_MM, 0))
    x1 = int(min((x + w + pad_mm) * PX_PER_MM, rect_arr.shape[1]))
    y1 = int(min((y + h + pad_mm) * PX_PER_MM, rect_arr.shape[0]))
    if x1 <= x0 or y1 <= y0:
        raise HTTPException(404, "that crop is outside the page")
    ok, buf = cv2.imencode(".png", rect_arr[y0:y1, x0:x1])
    if not ok:
        raise HTTPException(500, "the crop could not be encoded")
    return buf.tobytes()


# ── the whole read ──────────────────────────────────────────────────────────
async def read_form(image_bytes: bytes) -> dict:
    """Decode the QR, then ask the model for the handwriting.

    Returns the QR fields, the header values, and one row per printed row.
    Raises rather than returning a half-answer — see `_clean_json`.
    """
    try:
        prepped = prep_image_for_vision(image_bytes, max_dim=1800)
    except ImagePrepError as e:
        raise HTTPException(422, str(e)) from e

    # ⚠️ THE QR IS READ FROM THE ORIGINAL, NOT THE DOWNSCALED COPY. Capping the
    # long edge at 1800 px is right for the model and can push a small QR under
    # the detector's minimum module size.
    qr = decode_qr(image_bytes)

    b64 = base64.b64encode(prepped).decode()
    try:
        raw, model_id = await aic.vision_json(
            FORM_USER, system=FORM_SYSTEM, image_b64=b64,
            num_predict=FORM_NUM_PREDICT,
            # This lane renders the page at 1800 px — the largest image the
            # system sends, and therefore the one whose context window is most
            # worth measuring rather than assuming.
            image_tokens=estimate_image_tokens(prepped))
    except RuntimeError as e:
        # ⚠️ "NOT REACHABLE" WAS THE WRONG DIAGNOSIS FOR THE COMMON FAILURE.
        # Every long read used to die on a 240 s read timeout and be reported
        # as an outage, so the operator went and checked a service that was
        # running perfectly while the real cause — a page that needed longer
        # than the budget allowed — went unnamed for a whole phase. The client
        # now distinguishes the two; this passes the distinction through.
        raise HTTPException(
            503, f"the form could not be read — {e}. You can still type the "
                 f"entry in by hand.") from e

    parsed = _clean_json(raw)
    rows = []
    for r in parsed.get("rows", []):
        if not isinstance(r, dict):
            continue
        qty_text = str(r.get("qty_text") or "").strip()
        try:
            idx = int(r.get("row"))
        except (TypeError, ValueError):
            continue
        rows.append({
            "row": idx,
            "qty_text": qty_text,
            "quantity": _num(r.get("quantity"), qty_text),
            "lot_text": str(r.get("lot_text") or "").strip(),
            "struck_through": bool(r.get("struck_through")),
        })
    rows.sort(key=lambda r: r["row"])

    area_text = str(parsed.get("area_text") or "").strip()
    return {
        **{k: v for k, v in qr.items() if k != "qr_points"},
        "qr_points": qr.get("qr_points"),
        "work_date_text": str(parsed.get("work_date_text") or "").strip(),
        "equipment_text": str(parsed.get("equipment_text") or "").strip(),
        "area_text": area_text,
        "area_sqm": _num(parsed.get("area_sqm"), area_text),
        "filled_by": str(parsed.get("filled_by") or "").strip(),
        "rows": rows,
        "model": model_id,
        # Which engine ACTUALLY answered, derived from the model id it
        # returned rather than from the configuration — after a cloud fallback
        # those two disagree, and the fact is what belongs beside a quantity.
        "provider": aic.provider_of(model_id),
        "raw": raw,
    }
