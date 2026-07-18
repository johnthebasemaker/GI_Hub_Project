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


def prep_image_for_vision(raw_bytes: bytes, *, max_dim: int = 1600,
                          quality: int = 85) -> bytes:
    """EXIF auto-orient → RGB → long-edge cap 1600px → JPEG q85. Turns a
    3–6 MB smartphone photo into ~100–200 KB without hurting OCR accuracy
    (qwen2.5vl's tile preprocessor caps around 1600px anyway)."""
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
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=int(quality), optimize=True,
                 progressive=False)
        return buf.getvalue()
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
- Ditto marks (\", 〃, ,,) mean "same as above" — transcribe the GLYPH
  itself, never copy the value down.
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

USER_PROMPTS = {"ocr_consumption": "Extract the rows.",
                "ocr_delivery_note": "Extract the header and items."}
SYSTEM_PROMPTS = {"ocr_consumption": CONSUMPTION_PROMPT,
                  "ocr_delivery_note": DN_PROMPT}


# --- model-reply parsing --------------------------------------------------------
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.+\})\s*```", re.IGNORECASE | re.DOTALL)


def extract_json_object(raw: str) -> Optional[dict]:
    """First JSON object out of a model reply, fence or no fence; trims to
    the outermost braces so trailing prose can't poison json.loads."""
    if not raw:
        return None
    m = _JSON_FENCE.search(raw)
    candidate = m.group(1) if m else raw
    first, last = candidate.find("{"), candidate.rfind("}")
    if first < 0 or last <= first:
        return None
    try:
        return json.loads(candidate[first:last + 1])
    except json.JSONDecodeError:
        return None


def _to_float(s: Any) -> float:
    try:
        return float(re.sub(r"[^\d.\-]", "", str(s)))
    except (TypeError, ValueError):
        return 0.0


def clean_consumption_row(r: dict) -> dict:
    out = {"issued_to": str(r.get("issued_to") or "").strip(),
           "material_text": str(r.get("material_text") or "").strip(),
           "uom": str(r.get("uom") or "").strip(),
           "quantity": _to_float(r.get("quantity")),
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


def parse_vision_reply(kind: str, raw: str) -> dict:
    """Model reply → the lane-agnostic result shape. Raises ValueError with a
    friendly message on unparseable output (the job worker records it)."""
    obj = extract_json_object(raw)
    if kind == "ocr_consumption":
        if not obj or not isinstance(obj.get("rows"), list):
            raise ValueError("Vision model returned an unparseable response. "
                             "Try the Paste tab.")
        rows = [clean_consumption_row(r) for r in obj["rows"] if isinstance(r, dict)]
        return {"date_text": str(obj.get("date_text") or "").strip(),
                "rows": [r for r in rows
                         if r["material_text"] or r["quantity"] or r["qty_text"]]}
    if not obj or "items" not in obj:
        raise ValueError("Vision model returned an unparseable response. "
                         "Try the Paste tab.")
    items = [clean_item_row(r) for r in obj["items"] if isinstance(r, dict)]
    return {"header": clean_dn_header(obj.get("header") or {}),
            "items": [r for r in items if r["material_text"] or r["quantity"]]}


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
    """Tab/comma/semicolon/pipe rows: Issued_To, Material, UOM, Qty, Work_Type.
    Raises ValueError when nothing parses (endpoint → 422)."""
    if not (text or "").strip():
        raise ValueError("Paste at least one line.")
    rows = []
    for raw_line in text.splitlines():
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
    return {"rows": rows}


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
