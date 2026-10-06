"""
backend/api/ai/ocr.py — handwriting/printed-log OCR (Phase AI-3).

Port of legacy ai/ocr.py + ai/image_utils.py: two input lanes, one output
shape, so the React review grid doesn't know which lane the data came from.

  Image  → qwen2.5vl vision via the async Ollama client (called by the JOB
           WORKER in jobs.py — never inline in a request handler)
  Paste  → tab/CSV parser (pure Python, zero dependencies, works offline)

Row schemas (identical to legacy):
  consumption rows : {issued_to, material_text, uom, quantity, work_type}
  delivery note    : {header: {DN_No, Date, Mob_From, Driver_Name,
                      Vehicle_No, Prepared_by, Mob_To},
                      items: [{material_text, uom, quantity}]}

`material_text` is whatever the human wrote; fuzzy.resolve_rows() turns it
into a SAP_Code (auto) or candidates (pick) downstream. Prompts are kept
byte-identical to legacy — they're calibrated against real site paperwork.
"""
from __future__ import annotations

import json
import os
import re
from io import BytesIO
from typing import Any, Optional

# --- image prep (port of ai/image_utils.py — Round 14 pipeline) ----------------
try:
    import pillow_heif  # iPhone HEIC/HEIF — optional, graceful hint when absent
    _HEIF_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised on minimal installs
    _HEIF_AVAILABLE = False

_HEIF_REGISTERED = False


class ImagePrepError(Exception):
    """Typed wrapper so callers surface a clean message, not PIL internals."""


def _looks_like_heif(raw: bytes) -> bool:
    if len(raw) < 12:
        return False
    return raw[4:12].startswith(b"ftyp") and any(
        b in raw[8:32] for b in (b"heic", b"heix", b"heif", b"mif1", b"msf1", b"hevc"))


# ⚠️ TWO CAPS, AND THE SECOND ONE IS THE ONE THAT WAS MISSING.
#
# The long-edge cap alone does not bound what reaches Ollama. A 1600x1600 photo
# of a dense ruled table re-encodes to well over a megabyte at q85, and the
# request body is that payload base64'd — 33% larger again. Worse, a JPEG that
# was ALREADY small could come back BIGGER than it went in: a 904x1280 site
# photo measured here on 2026-09-01 went 131 KB -> 135 KB, because nothing under
# the long-edge cap was resized and the re-encode was pure loss.
#
# `MAX_VISION_BYTES` is the second cap. When the encode overshoots it, quality
# steps down and then the long edge does, until the payload fits. The stepping
# is deliberate in that order: dropping JPEG quality blurs strokes far less than
# throwing away pixels, and a handwritten 4 is told from a 9 by stroke geometry.
MAX_VISION_BYTES = int(os.environ.get("GI_AI_MAX_IMAGE_BYTES",
                                      str(1_400_000)))
_QUALITY_LADDER = (85, 72, 60)
_DIM_LADDER = (1.0, 0.8, 0.65)

# ── the SOURCE cap, which is a different number for a different reason ───────
# The vision cap above is sized for the model. This one is sized for the two
# jobs the ORIGINAL bytes still have to do after the model is finished with a
# downscaled copy: decoding the QR (`ocr_form.decode_qr` reads the original
# deliberately, because 1600 px can push a small QR under the detector's
# minimum module size) and rectifying the page for per-row crops (8 px/mm over
# a 210 mm sheet = 1,680 px of useful width).
#
# 2,600 px on the long edge covers both with room to spare — a 26 mm QR lands
# at ~320 px — while bounding what a phone can push into `ai_jobs.payload_json`
# and into `sme_execution_entry.OCR_Image`. Before this, a 20 MB HEIC arrived
# base64'd at ~27 MB in a text column, per upload, kept forever.
SOURCE_MAX_DIM = int(os.environ.get("GI_AI_SOURCE_MAX_DIM", "2600"))
SOURCE_MAX_BYTES = int(os.environ.get("GI_AI_SOURCE_MAX_BYTES", str(4_000_000)))


# ⚠️ HOW MANY CONTEXT TOKENS AN IMAGE COSTS, measured rather than assumed.
#
# qwen2.5-VL tiles an image into 14 px patches and merges 2x2 of them, so one
# token covers roughly a 28x28 px square. That raw arithmetic UNDER-counts what
# Ollama actually reports, because the runner pads and adds control tokens.
# Measured on the operator's own files, 2026-09-01:
#
#   1273 x 1800  →  raw 2,990  ·  reported 3,120   (x1.04)
#    990 x 1400  →  raw 1,800  ·  reported 2,247   (x1.25)
#    792 x 1120  →  raw 1,160  ·  reported 1,617   (x1.39)
#
# The ratio is not constant, so the factor is set above the worst observed one.
# ⚠️ THIS ESTIMATE MUST ERR HIGH, ALWAYS. Under-counting sizes `num_ctx` too
# small, and too small does not degrade — it ABORTS the Ollama runner
# (`ggml_abort`, SIGABRT), taking every other queued job with it. Over-counting
# costs some KV cache and nothing else.
_TOKENS_PER_PATCH_PX = 28
_TOKEN_SAFETY = 1.45
_TOKEN_FLOOR = 64        # control tokens the tiling arithmetic does not see


def estimate_image_tokens(jpeg_bytes: bytes) -> int:
    """Upper bound on the context tokens this image will occupy.

    Reads only the JPEG header — PIL is lazy about pixel data, so `.size` costs
    nothing on a 1.4 MB file. Returns a conservative floor when the header
    cannot be read, because a failed measurement must not produce a small
    number: `0` here would size the context as if there were no image at all.
    """
    import math
    from PIL import Image
    try:
        with Image.open(BytesIO(jpeg_bytes)) as im:
            w, h = im.size
    except Exception:                                       # noqa: BLE001
        return 4500      # ~the cost of a full-page 1800 px scan
    patches = (math.ceil(w / _TOKENS_PER_PATCH_PX)
               * math.ceil(h / _TOKENS_PER_PATCH_PX))
    return int(patches * _TOKEN_SAFETY) + _TOKEN_FLOOR


def prep_source_image(raw_bytes: bytes) -> bytes:
    """Normalise an uploaded photograph ONCE, at the door.

    EXIF-orients, flattens to RGB JPEG and caps the long edge at
    `SOURCE_MAX_DIM` — large enough for the QR decode and the rectifier, small
    enough that nothing downstream has to defend itself against a 20 MB phone
    photo. Raises `ImagePrepError` (the endpoint turns it into a friendly 422)
    so a corrupt or HEIC-without-codec upload fails while the supervisor is
    still standing at the desk, rather than as a dead job minutes later.
    """
    return prep_image_for_vision(raw_bytes, max_dim=SOURCE_MAX_DIM,
                                 quality=88, max_bytes=SOURCE_MAX_BYTES)


def prep_image_for_vision(raw_bytes: bytes, *, max_dim: int = 1600,
                          quality: int = 85,
                          max_bytes: Optional[int] = None) -> bytes:
    """EXIF auto-orient → RGB → long-edge cap → JPEG, under a byte budget.

    Turns a 3–6 MB smartphone photo or a 300 dpi page raster into ~100–400 KB
    without hurting OCR accuracy (qwen2.5vl's tile preprocessor caps around
    1600px anyway, so pixels above the cap cost time and buy nothing).

    Two guarantees the caller can rely on, both added 2026-09-01 after a
    ReadTimeout hunt:

      * the long edge is at most `max_dim`, and
      * the result is at most `MAX_VISION_BYTES`, and never larger than the
        bytes handed in when those bytes were already a usable JPEG.

    The second is what stops an oversized page from reaching the model at all:
    the VLM's wall-clock cost is dominated by how many tiles it has to prefill,
    and an unbounded upload is an unbounded prefill.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    global _HEIF_REGISTERED
    if _HEIF_AVAILABLE and not _HEIF_REGISTERED:
        try:
            pillow_heif.register_heif_opener()
            _HEIF_REGISTERED = True
        except Exception:  # decode errors surface at use time instead
            pass

    if not isinstance(raw_bytes, (bytes, bytearray)) or not raw_bytes:
        raise ImagePrepError("Empty image bytes — nothing to process.")
    try:
        img = Image.open(BytesIO(raw_bytes))
        img.load()
    except UnidentifiedImageError as e:
        if not _HEIF_AVAILABLE and _looks_like_heif(raw_bytes):
            raise ImagePrepError(
                "This looks like an iPhone HEIC photo and pillow-heif is not "
                "installed on this server. Share as JPEG (iPhone → Settings → "
                "Camera → Formats → Most Compatible) or ask your admin to "
                "install pillow-heif.") from e
        raise ImagePrepError("Couldn't read this photo — corrupt or unsupported format.") from e
    except (OSError, ValueError) as e:
        raise ImagePrepError("Couldn't read this photo — corrupt or unsupported format.") from e
    try:
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail((int(max_dim), int(max_dim)), Image.LANCZOS)

        def _encode(im, q: int) -> bytes:
            buf = BytesIO()
            im.save(buf, format="JPEG", quality=int(q), optimize=True,
                    progressive=False)
            return buf.getvalue()

        budget = MAX_VISION_BYTES if max_bytes is None else int(max_bytes)
        out = _encode(img, quality)
        if len(out) > budget:
            base_w, base_h = img.size
            for scale in _DIM_LADDER:
                trial = (img if scale == 1.0 else img.resize(
                    (max(int(base_w * scale), 1), max(int(base_h * scale), 1)),
                    Image.LANCZOS))
                for q in _QUALITY_LADDER:
                    out = _encode(trial, q)
                    if len(out) <= budget:
                        return out
        return out
    except Exception as e:
        raise ImagePrepError(f"Image transformation failed: {e}") from e


# --- prompts (byte-identical to legacy — calibrated on real site paperwork) ----
CONSUMPTION_PROMPT = """\
You are reading a handwritten "Daily - Consumption / Safety & Production
Consumables" form (header "MPC3P1-CNCEC PROJECT"). It is a table of up to 30
rows with columns: S.No. | Name | Tank No.# | Product Name | UOM | QTY |
Remarks. The DATE is handwritten in the top-right corner.

Your job is FAITHFUL TRANSCRIPTION ONLY — downstream code resolves ditto
marks, corrects known OCR confusions and validates quantities. Do not
interpret, normalise or fix anything yourself.

Output STRICT JSON with this shape and no extra commentary:
{
  "date_text": "the date EXACTLY as written (e.g. 13/07/26, 7.6.26)",
  "rows": [
    {
      "sno":          <S.No. as written, or null>,
      "issued_to":    "Name column exactly as written",
      "tank_no":      "Tank No. column exactly as written",
      "material_text":"Product Name EXACTLY as written (keep spelling errors)",
      "uom":          "UOM if shown",
      "qty_text":     "QTY exactly as written (e.g. '5', '2+3', '~4', '')",
      "quantity":     <number if unambiguous, else null>,
      "work_type":    "Remarks column exactly as written",
      "struck_through": <true ONLY if a horizontal line is drawn through the
                         whole row (a cancelled entry), else false>
    }
  ]
}

Rules:
- Output JSON only. No markdown fences, no prose.
- A cell that says "same as the row above" — written as a ditto mark (", 〃,
  ,,), as a wavy line, or simply left to be understood from the row above —
  MUST be output as the exact token <DITTO>. Do not copy the value down
  yourself, and do not output an empty string for it. Later code resolves
  <DITTO> against the row above; an empty string is indistinguishable from a
  cell the writer left genuinely blank.
- A cell that is genuinely blank — nothing written, nothing implied — is "".
- Additive quantities like "2+3" go in qty_text verbatim; leave quantity null.
- A blank QTY cell is qty_text "" and quantity null — never invent 0 or 1.
- Use empty strings for unreadable text fields; never guess a person's name.
- Skip printed column-title rows; skip fully empty rows.
- Include struck-through rows WITH struck_through=true (do not drop them).
"""

DN_PROMPT = """\
You are reading a printed delivery note from General Industries.
It has a HEADER (Ref No, Issue Date, Customer Name, Driver Name, Vehicle No,
Prepared By, Location) and a BODY TABLE (SR No, Material Description, UOM, QTY).

Output STRICT JSON with this exact shape:
{
  "header": {
    "DN_No":        "ref or s.no",
    "Date":         "ISO YYYY-MM-DD if convertible, else the literal date string",
    "Mob_From":     "customer name (the 'received from' party)",
    "Driver_Name":  "driver",
    "Vehicle_No":   "vehicle number",
    "Prepared_by":  "preparer name",
    "Mob_To":       "location (where the material is mobilised to)"
  },
  "items": [
    {"material_text": "...", "uom": "...", "quantity": <number>}
  ]
}

Rules:
- Output JSON only. No markdown fences, no prose.
- Skip the SR NO column — re-index from 1 implicitly.
- Skip footer rows (Prepared By signature, Received By signature, totals).
- Use empty strings for missing header values; use 0 for missing quantities.
"""

# QSEP slice 6 — the SCANNED purchase document (PR or PO).
#
# Calibrated against a real file: `PO#4710003121_PR681.pdf` is a General
# Industries purchase order that was printed, signed and scanned back in. It
# has ZERO extractable text, so pdfplumber's three layout regexes match
# nothing and the old endpoint answered 200 with an empty item list. This is
# the lane that file needs.
#
# ONE prompt for both PR and PO on purpose. The two documents share a
# header/table shape, a scan does not reliably say which it is, and asking
# the model to TELL US which it read is more robust than making the caller
# guess before upload — a PR filed under "PO" would otherwise be parsed
# against the wrong prompt and quietly mangled.
PURCHASE_DOC_PROMPT = """\
You are reading a scanned purchase document from General Industries. It is
either a PURCHASE REQUISITION (PR) or a PURCHASE ORDER (PO).

Output STRICT JSON with this exact shape:
{
  "doc_type": "PR" or "PO"   (whichever this document is),
  "header": {
    "PR_Number":  "the Purch. Req. No. if shown, else ''",
    "PO_Number":  "the Purch. Order No. if shown, else ''",
    "Date":       "ISO YYYY-MM-DD if convertible, else the literal string",
    "Vendor_Name":"supplier/vendor name if shown",
    "Vendor_Code":"vendor number if shown",
    "Total_Amount":"grand total as written, else ''"
  },
  "items": [
    {
      "material_code": "the GI-NNNNNNN code EXACTLY as printed, or ''",
      "material_text": "the description as printed",
      "uom":           "unit of measure if shown",
      "quantity":      <number, or null if unreadable>,
      "unit_price":    <number, or null>
    }
  ]
}

Rules:
- Output JSON only. No markdown fences, no prose.
- Material codes look like GI-7000009. Transcribe the digits EXACTLY; do not
  correct a code that looks wrong.
- A quantity you cannot read is null. NEVER invent 0 or 1 — a wrong quantity
  on a purchase order is an ordering error, and a null is a question.
- Skip page headers, footers, signature blocks and totals rows.
- Use empty strings for header fields the document does not show.
"""

USER_PROMPTS = {"ocr_consumption": "Extract the rows.",
                "ocr_delivery_note": "Extract the header and items.",
                "ocr_purchase_doc": "Extract the header and the line items."}
SYSTEM_PROMPTS = {"ocr_consumption": CONSUMPTION_PROMPT,
                  "ocr_delivery_note": DN_PROMPT,
                  "ocr_purchase_doc": PURCHASE_DOC_PROMPT}


# --- model-reply parsing --------------------------------------------------------
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.+\})\s*```", re.IGNORECASE | re.DOTALL)


def extract_json_object(raw: str) -> Optional[dict]:
    """First JSON object out of a model reply, fence or no fence; trims to
    the outermost braces so trailing prose can't poison json.loads.

    Falls back to `salvage_truncated_json` when the reply is a well-formed
    object that simply STOPS — see that function for why losing the whole read
    to a clipped tail was the single most damaging bug in this lane.
    """
    if not raw:
        return None
    m = _JSON_FENCE.search(raw)
    candidate = m.group(1) if m else raw
    first = candidate.find("{")
    last = candidate.rfind("}")
    if first < 0:
        return None
    if last > first:
        try:
            return json.loads(candidate[first:last + 1])
        except json.JSONDecodeError:
            pass
    return salvage_truncated_json(candidate[first:])


def salvage_truncated_json(text: str) -> Optional[dict]:
    """Rebuild the complete prefix of a JSON object the model never finished.

    ⚠️ THIS IS THE FIX FOR THE BUG THE OPERATOR CALLED "FAILING SILENTLY".
    Reproduced 2026-09-01 on the real Consumption Log photo: `qwen2.5vl:7b` read
    the page correctly, emitted thirteen good rows, and was cut off in the
    middle of the fourteenth by the 1024-token budget. The reply therefore had
    no closing brace; `json.loads` refused all of it; and the store keeper was
    told "Vision model returned an unparseable response. Try the Paste tab."
    Thirteen correctly-read rows were thrown away to punish one clipped row.

    Delivery Notes never hit this — a DN is four items and finishes inside the
    budget — which is exactly why the failure looked like "the model cannot read
    free-form tables" rather than "the answer did not fit in the envelope".

    ⚠️ THE CUT IS ONLY EVER MADE AT A CLOSING BRACKET, and that restriction is
    the whole safety argument. A closing `}` or `]` is proof that the value
    before it was written in full; a comma is not, and cutting at the last comma
    would keep a row whose `material_text` had been read but whose `quantity`
    had not — a row that looks complete and is missing the number. The
    incomplete trailing element is DROPPED, never patched: a half-read row is a
    half-read quantity, and inventing the rest of it is the one thing this
    module refuses to do.

    Returns None when nothing coherent survives — a reply that was garbage from
    the first character must still fail, and fail loudly.
    """
    if not text or text[0] != "{":
        return None
    stack: list[str] = []
    in_string = False
    escaped = False
    cut = -1                    # index AFTER the last completed nested value
    # ⚠️ AND THE CONTAINERS THAT WERE OPEN AT THAT INSTANT, which is not the
    # same as the containers open when the text ran out. The clipped row opens
    # a `{` after the cut point; closing it would weld the abandoned fragment
    # back on and produce `[{"a": 1}}]` — invalid, and the salvage would then
    # fail for a reply it could have rescued.
    cut_stack: list[str] = []
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or stack[-1] != ch:
                return None                  # structurally broken, not merely cut
            stack.pop()
            if not stack:                    # the reply was complete after all
                try:
                    out = json.loads(text[:i + 1])
                except json.JSONDecodeError:
                    return None
                return out if isinstance(out, dict) else None
            cut = i + 1
            cut_stack = list(stack)
    if cut <= 0 or not cut_stack:
        return None
    repaired = text[:cut] + "".join(reversed(cut_stack))
    try:
        out = json.loads(repaired)
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


def _to_float(s: Any) -> float:
    try:
        return float(re.sub(r"[^\d.\-]", "", str(s)))
    except (TypeError, ValueError):
        return 0.0


def _to_float_or_none(s: Any) -> Optional[float]:
    """Like `_to_float`, but an unreadable value stays None.

    ⚠️ The 0.0 default above is right for the consumption and DN lanes —
    their prompts say "use 0 for missing quantities" and a missing DN line
    quantity is a transcription gap on a document that already happened.

    It is WRONG for a purchase document, and that distinction is the reason
    this exists. A quantity the model could not read becoming 0 on a PURCHASE
    ORDER is an ordering error wearing the clothes of a real number: it looks
    answered, so nobody asks. None is a question the reviewer has to close.
    """
    if s is None or (isinstance(s, str) and not s.strip()):
        return None
    try:
        cleaned = re.sub(r"[^\d.\-]", "", str(s))
        return float(cleaned) if cleaned not in ("", "-", ".", "-.") else None
    except (TypeError, ValueError):
        return None


def clean_consumption_row(r: dict) -> dict:
    """One handwritten log row, normalised for the review grid.

    ⚠️ `quantity` STAYS None WHEN THE MODEL COULD NOT READ IT. The prompt above
    tells the model in as many words: "A blank QTY cell is qty_text '' and
    quantity null — never invent 0 or 1." This function used to invent it
    anyway: `_to_float(None)` is 0.0, so every ambiguous and every empty box
    arrived in the grid as a confident `0`. A store keeper scanning thirty rows
    does not stop at a zero — it reads as a number somebody wrote. An empty box
    is a question, and the grid already renders None as one (`InputNumber` shows
    blank, and the submit filter needs `> 0`).

    This is the vision lane only. `parse_consumption_paste` builds its own rows
    and keeps the 0.0 default, which is right there: a pasted cell that is empty
    was typed empty by a human who was looking at it.
    """
    out = {"issued_to": str(r.get("issued_to") or "").strip(),
           "material_text": str(r.get("material_text") or "").strip(),
           "uom": str(r.get("uom") or "").strip(),
           "quantity": _to_float_or_none(r.get("quantity")),
           "work_type": str(r.get("work_type") or "").strip()}
    # 2026-07-18 handwritten-form spec fields (additive — old consumers see
    # the same keys as before; ai/handwritten.py consumes the extras)
    out["tank_no"] = str(r.get("tank_no") or "").strip()
    out["qty_text"] = str(r.get("qty_text") if r.get("qty_text") is not None else "").strip()
    out["struck_through"] = bool(r.get("struck_through"))
    if r.get("sno") is not None:
        out["sno"] = r.get("sno")
    return out


def clean_item_row(r: dict) -> dict:
    return {"material_text": str(r.get("material_text") or "").strip(),
            "uom": str(r.get("uom") or "").strip(),
            "quantity": _to_float(r.get("quantity"))}


_DN_HEADER_KEYS = ("DN_No", "Date", "Mob_From", "Driver_Name",
                   "Vehicle_No", "Prepared_by", "Mob_To")


def clean_dn_header(h: dict) -> dict:
    return {k: str(h.get(k) or "").strip() for k in _DN_HEADER_KEYS}


# --- scanned purchase document (QSEP slice 6) ---------------------------------
_PURCHASE_HEADER_KEYS = ("PR_Number", "PO_Number", "Date", "Vendor_Name",
                         "Vendor_Code", "Total_Amount")
_GI_CODE = re.compile(r"(GI-\d{6,8})", re.IGNORECASE)


def clean_purchase_row(r: dict) -> dict:
    """One scanned line item, normalised to the shape the review grid uses.

    The material code is re-extracted with the same `GI-\\d{6,8}` pattern the
    pdfplumber lane uses, from EITHER field: a vision model routinely returns
    "GI-7000009 ELECTRIC INSULATION" in `material_text` and leaves
    `material_code` empty, and dropping the code because it arrived in the
    wrong key would send a perfectly matchable line to the "unmatched" pile.

    `quantity` stays None when unreadable and is NEVER defaulted. A wrong
    quantity on a purchase order is an ordering error; a null is a question
    the reviewer answers.
    """
    code = str(r.get("material_code") or "").strip().upper()
    text = str(r.get("material_text") or "").strip()
    if not _GI_CODE.fullmatch(code):
        m = _GI_CODE.search(code) or _GI_CODE.search(text)
        code = m.group(1).upper() if m else ""
    return {"material_code": code, "material_text": text,
            "uom": str(r.get("uom") or "").strip(),
            # None-preserving on purpose — see _to_float_or_none.
            "quantity": _to_float_or_none(r.get("quantity")),
            "unit_price": _to_float_or_none(r.get("unit_price"))}


def clean_purchase_header(h: dict) -> dict:
    return {k: str(h.get(k) or "").strip() for k in _PURCHASE_HEADER_KEYS}


def parse_vision_reply(kind: str, raw: str) -> dict:
    """Model reply → the lane-agnostic result shape. Raises ValueError with a
    friendly message on unparseable output (the job worker records it)."""
    obj = extract_json_object(raw)
    if kind == "ocr_consumption":
        if not obj or not isinstance(obj.get("rows"), list):
            raise ValueError("Vision model returned an unparseable response. "
                             "Try the Paste tab.")
        rows = [clean_consumption_row(r) for r in obj["rows"] if isinstance(r, dict)]
        kept = [r for r in rows
                if r["material_text"] or r["quantity"] or r["qty_text"]]
        # ⚠️ NOTHING READ IS A FAILURE, NOT A RESULT. A parse that yields zero
        # rows used to finish the job at status='done' with an empty grid, which
        # is indistinguishable from a photo of a blank form — so the store
        # keeper retried the same picture, or worse, submitted the emptiness.
        # The lane has to say it could not read the page.
        if not kept:
            raise ValueError(
                "The model read this photo but found no rows on it. Check the "
                "whole table is in frame and in focus, then retake it — or use "
                "the Paste tab.")
        return {"date_text": str(obj.get("date_text") or "").strip(),
                "rows": kept}
    if not obj or "items" not in obj:
        raise ValueError("Vision model returned an unparseable response. "
                         "Try the Paste tab.")
    if kind == "ocr_purchase_doc":
        items = [clean_purchase_row(r) for r in obj["items"] if isinstance(r, dict)]
        doc_type = str(obj.get("doc_type") or "").strip().upper()
        # A row with neither a code nor a description is a table artefact,
        # not a line item.
        kept = [r for r in items if r["material_code"] or r["material_text"]]
        if not kept:
            raise ValueError(
                "The model read this document but found no line items on it. "
                "Check the item table is in frame, then re-upload — or enter "
                "the lines manually.")
        return {"doc_type": doc_type if doc_type in ("PR", "PO") else "",
                "header": clean_purchase_header(obj.get("header") or {}),
                "items": kept}
    items = [clean_item_row(r) for r in obj["items"] if isinstance(r, dict)]
    kept = [r for r in items if r["material_text"] or r["quantity"]]
    header = clean_dn_header(obj.get("header") or {})
    # ⚠️ THE DELIVERY NOTE FAILS ONLY WHEN BOTH HALVES ARE EMPTY, and that is a
    # deliberate difference from the consumption lane above.
    #
    # A consumption sheet IS its rows: no rows means nothing was read. A DN's
    # header is independently useful — parity C3 feeds it straight into the
    # Receive form's DN No. / driver / vehicle fields, so a note whose item
    # table was cropped out of frame still saves the store keeper four fields
    # of typing. Refusing that read would delete a working feature in order to
    # report a partial one, which is the wrong trade in the one direction the
    # operator asked us not to break.
    #
    # Neither half readable is still a failure, for the same reason as above: a
    # job finishing at status='done' with nothing in it is indistinguishable
    # from a photo of a blank page.
    if not kept and not any(header.values()):
        raise ValueError(
            "The model read this note but found neither a header nor any items "
            "on it. Check the whole note is in frame and in focus, then retake "
            "it — or use the Paste tab.")
    return {"header": header, "items": kept}


# --- paste lane (offline twin — identical output shapes) -------------------------
_SPLITTERS = re.compile(r"\t|,|;|\|")


def _split_row(line: str) -> list[str]:
    return [p.strip() for p in _SPLITTERS.split(line) if p.strip() != ""]


def _looks_like_header(parts: list[str]) -> bool:
    if not parts:
        return False
    first = parts[0].lower()
    joined = " ".join(parts).lower()
    return any(k in first for k in ("name", "material", "description", "qty", "quantity")) \
        or ("uom" in joined and any(k in joined for k in ("qty", "quantity")))


def parse_consumption_paste(text: str) -> dict:
    """Tab/comma/semicolon/pipe rows: Issued_To, Material, UOM, Qty, Work_Type,
    plus an optional `Date: DD/MM/YY` line (the paper's date box).
    Raises ValueError when nothing parses (endpoint → 422)."""
    if not (text or "").strip():
        raise ValueError("Paste at least one line.")
    rows = []
    date_text = ""
    for raw_line in text.splitlines():
        # "Date: 01/10/26" — the paper's date box, checked like a photo's
        m = re.match(r"^\s*date\s*[:=]\s*(.+?)\s*$", raw_line, re.IGNORECASE)
        if m:
            date_text = m.group(1)
            continue
        parts = _split_row(raw_line.strip())
        if not parts or _looks_like_header(parts) or len(parts) < 2:
            continue
        rows.append({"issued_to": parts[0],
                     "material_text": parts[1] if len(parts) > 1 else "",
                     "uom": parts[2] if len(parts) > 2 else "",
                     "quantity": _to_float(parts[3]) if len(parts) > 3 else 0.0,
                     "work_type": parts[4] if len(parts) > 4 else ""})
    if not rows:
        raise ValueError("No data rows found.")
    return {"rows": rows, "date_text": date_text}


_DN_CANONICAL = {
    "dn_no": "DN_No", "ref no": "DN_No", "ref_no": "DN_No",
    "date": "Date",
    "mob_from": "Mob_From", "customer": "Mob_From",
    "customer name": "Mob_From", "received from": "Mob_From",
    "driver_name": "Driver_Name", "driver": "Driver_Name",
    "driver name": "Driver_Name",
    "vehicle_no": "Vehicle_No", "vehicle": "Vehicle_No", "vehicle no": "Vehicle_No",
    "prepared_by": "Prepared_by", "prepared by": "Prepared_by",
    "preparer": "Prepared_by",
    "mob_to": "Mob_To", "location": "Mob_To",
}


def parse_delivery_note_paste(text: str) -> dict:
    """`Key: value` lines fill the header (synonyms mapped); other lines are
    Material, UOM, Qty items. Raises ValueError when no items parse."""
    if not (text or "").strip():
        raise ValueError("Paste the note.")
    header: dict[str, str] = {}
    items: list[dict] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line and line.split(":", 1)[0].strip().lower() in _DN_CANONICAL:
            k, v = line.split(":", 1)
            header[_DN_CANONICAL[k.strip().lower()]] = v.strip()
            continue
        parts = _split_row(line)
        if not parts or _looks_like_header(parts) or len(parts) < 2:
            continue
        items.append({"material_text": parts[0],
                      "uom": parts[1] if len(parts) > 1 else "",
                      "quantity": _to_float(parts[2]) if len(parts) > 2 else 0.0})
    if not items:
        raise ValueError("No item rows found.")
    return {"header": clean_dn_header(header), "items": items}


# --- Phase AI-4: tool identification (Smart Scan tier-2, vision-LLM based) -------
# The legacy tier-2 was a YOLO model behind an admin train→promote lifecycle
# that was never populated on this stack (tool_catalogue is empty). Ruling
# 2026-07-06: qwen2.5vl covers identification instead — catalogue-OPTIONAL:
# when tool_catalogue rows exist the prompt constrains to those classes; when
# empty the model names the tool freeform.

TOOL_PROMPT_BASE = """\
You are identifying a warehouse tool or equipment item from a photo taken by
a store keeper recording a tool loan.

Output STRICT JSON with this exact shape and no extra commentary:
{
  "name":         "the most likely tool name",
  "alternatives": ["second guess", "third guess"],
  "description":  "one short sentence describing what you see"
}

Rules:
- Output JSON only. No markdown fences, no prose.
- Keep names short and practical (e.g. "Angle Grinder 9in", "Torque Wrench").
- Use [] for alternatives if you are confident.
"""

TOOL_PROMPT_CATALOGUE_SUFFIX = """\

This warehouse tracks these known tool classes — when the photo matches one,
use its EXACT class name for "name" (and for alternatives that also match):
{catalogue}
"""


def tool_prompt(catalogue: list[dict]) -> str:
    """catalogue rows: {class_name, display_name}. Empty list → freeform."""
    if not catalogue:
        return TOOL_PROMPT_BASE
    listing = "\n".join(f"- {c['class_name']} ({c['display_name']})"
                        for c in catalogue)
    return TOOL_PROMPT_BASE + TOOL_PROMPT_CATALOGUE_SUFFIX.format(catalogue=listing)


def parse_tool_reply(raw: str, catalogue: list[dict]) -> dict:
    """Model reply → {"tool": {name, class_name, alternatives, description}}.
    Names matching a catalogue class_name are mapped to the display name and
    keep the class reference; unmatched names pass through freeform."""
    obj = extract_json_object(raw)
    if not obj or not str(obj.get("name") or "").strip():
        raise ValueError("Vision model could not identify the tool — "
                         "type the name manually.")
    by_class = {str(c["class_name"]).strip().lower(): c for c in catalogue}

    def _entry(name: str) -> dict:
        hit = by_class.get(str(name).strip().lower())
        if hit:
            return {"name": hit["display_name"] or hit["class_name"],
                    "class_name": hit["class_name"]}
        return {"name": str(name).strip(), "class_name": None}

    best = _entry(obj["name"])
    alts = [_entry(a) for a in (obj.get("alternatives") or [])
            if str(a or "").strip()][:3]
    return {"tool": {**best, "alternatives": alts,
                     "description": str(obj.get("description") or "").strip()}}
