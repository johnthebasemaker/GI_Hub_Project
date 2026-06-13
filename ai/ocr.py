"""
ai/ocr.py — turn an uploaded image OR pasted text into structured rows
=======================================================================
Two input lanes, one output shape:

  Image  → vision-LLM extractor (Ollama qwen2.5vl:7b by default)
  Paste  → tab/CSV parser (zero dependencies, runs instantly)

Both lanes produce the same row schemas, so the Entry Log review grid
doesn't know which lane the data came from.

Output schemas
--------------
consumption rows  : {issued_to, material_text, uom, quantity, work_type}
delivery note     : {
    header: {DN_No, Date, Mob_From, Driver_Name, Vehicle_No, Prepared_by, Mob_To},
    items:  [{material_text, uom, quantity}, ...]
  }

`material_text` is the freeform string the user wrote / printed. The
fuzzy matcher in ai/fuzzy.py turns that into a SAP_Code (auto-fill) or a
candidate list (pick).

Safety
------
- Vision lanes raise no exceptions to callers; they return a typed result
  object so the UI can react. Transport / JSON / missing-model failures
  show as `ok=False` with a friendly reason.
- Paste lanes are 100% offline / pure Python.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from ai.client import (
    MODEL_VISION,
    OLLAMA_AVAILABLE,
    OLLAMA_HOST,
    list_ollama_models,
    ollama_vision_generate,
)


def _vision_preflight(model: str) -> Optional[str]:
    """
    Return a friendly error string if image OCR can't run; None if good to go.
    Catches the two common setup mistakes: server unreachable, vision model
    not pulled.
    """
    if not OLLAMA_AVAILABLE:
        return (
            f"Ollama server not reachable at `{OLLAMA_HOST}`. "
            "Locally: run `ollama serve` (or it's already running — check `ollama ps`). "
            "On Streamlit Cloud: set `[ollama] host = \"...\"` in App Secrets "
            "pointing at your tunneled local instance."
        )
    installed = list_ollama_models()
    if installed and model not in installed:
        # Suggest a close match if user has a similar one.
        suggestion = next(
            (m for m in installed if "vl" in m.lower() or "vision" in m.lower()),
            None,
        )
        hint = (
            f"Found these vision-capable models instead: `{suggestion}` "
            "— set `OLLAMA_VISION_MODEL` env var or `[ollama] vision_model` "
            "in Streamlit Secrets to use it."
            if suggestion else
            f"Pull it once with:\n```\nollama pull {model}\n```"
        )
        return (
            f"Vision model `{model}` not installed on `{OLLAMA_HOST}`. {hint}"
        )
    return None


# ---------------------------------------------------------------------------
# RESULT TYPES
# ---------------------------------------------------------------------------
@dataclass
class ConsumptionResult:
    ok: bool
    rows: list[dict]
    message: str = ""
    raw: str = ""


@dataclass
class DeliveryNoteResult:
    ok: bool
    header: dict
    items: list[dict]
    message: str = ""
    raw: str = ""


# ---------------------------------------------------------------------------
# PROMPTS — kept short so context fits in 7B model windows comfortably
# ---------------------------------------------------------------------------
_CONSUMPTION_PROMPT = """\
You are reading a handwritten or printed warehouse consumption list.
Each line describes ONE material issued to a person.

Output STRICT JSON with this shape and no extra commentary:
{
  "rows": [
    {
      "issued_to":    "person name (if any)",
      "material_text":"material description as written",
      "uom":          "unit if shown (e.g. Nos, PCS, M, L)",
      "quantity":     <number>,
      "work_type":    "job / work type if shown"
    }
  ]
}

Rules:
- Output JSON only. No markdown fences, no prose.
- Use the number 0 for unreadable quantities.
- Use empty strings for unreadable text fields.
- Skip header rows like "Material Description" — those are column titles, not data.
"""

_DN_PROMPT = """\
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


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.+\})\s*```", re.IGNORECASE | re.DOTALL)


def _extract_json_object(raw: str) -> Optional[dict]:
    """Pull the first JSON object out of the model's reply, fence or no fence."""
    if not raw:
        return None
    # Fenced ```json ... ``` block first.
    m = _JSON_FENCE.search(raw)
    candidate = m.group(1) if m else raw
    # Trim to outermost braces so trailing prose doesn't poison json.loads.
    first = candidate.find("{")
    last = candidate.rfind("}")
    if first < 0 or last <= first:
        return None
    try:
        return json.loads(candidate[first : last + 1])
    except json.JSONDecodeError:
        return None


def _b64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("ascii")


# ---------------------------------------------------------------------------
# IMAGE LANE — vision LLM (Ollama)
# ---------------------------------------------------------------------------
def extract_consumption_from_image(
    image_bytes: bytes,
    model: str = None,
) -> ConsumptionResult:
    """
    Run the vision model on `image_bytes` and return a parsed list of
    consumption rows. Never raises — failures show as ok=False.
    """
    err = _vision_preflight(model or MODEL_VISION)
    if err:
        return ConsumptionResult(ok=False, rows=[], message=err)
    try:
        raw = ollama_vision_generate(
            model or MODEL_VISION,
            prompt="Extract the rows.",
            image_b64_list=[_b64(image_bytes)],
            system=_CONSUMPTION_PROMPT,
        )
    except RuntimeError as e:
        return ConsumptionResult(ok=False, rows=[], message=str(e))

    obj = _extract_json_object(raw)
    if not obj or "rows" not in obj or not isinstance(obj["rows"], list):
        return ConsumptionResult(
            ok=False, rows=[],
            message="Vision model returned an unparseable response. Try the Paste tab.",
            raw=raw,
        )
    rows = [_clean_consumption_row(r) for r in obj["rows"]]
    rows = [r for r in rows if r.get("material_text") or r.get("quantity")]
    return ConsumptionResult(ok=True, rows=rows, raw=raw)


def extract_delivery_note_from_image(
    image_bytes: bytes,
    model: str = None,
) -> DeliveryNoteResult:
    """Run the vision model on a delivery-note image; return header + items."""
    err = _vision_preflight(model or MODEL_VISION)
    if err:
        return DeliveryNoteResult(ok=False, header={}, items=[], message=err)
    try:
        raw = ollama_vision_generate(
            model or MODEL_VISION,
            prompt="Extract the header and items.",
            image_b64_list=[_b64(image_bytes)],
            system=_DN_PROMPT,
        )
    except RuntimeError as e:
        return DeliveryNoteResult(ok=False, header={}, items=[], message=str(e))

    obj = _extract_json_object(raw)
    if not obj or "items" not in obj:
        return DeliveryNoteResult(
            ok=False, header={}, items=[],
            message="Vision model returned an unparseable response. Try the Paste tab.",
            raw=raw,
        )
    header = _clean_dn_header(obj.get("header") or {})
    items = [_clean_item_row(r) for r in obj["items"] if isinstance(r, dict)]
    items = [r for r in items if r.get("material_text") or r.get("quantity")]
    return DeliveryNoteResult(ok=True, header=header, items=items, raw=raw)


# ---------------------------------------------------------------------------
# PASTE LANE — instant, dependency-free, works without Ollama
# ---------------------------------------------------------------------------
def parse_consumption_paste(text: str) -> ConsumptionResult:
    """
    Lenient text parser. Accepts tab-, comma-, semicolon-, or pipe-separated
    lines. Header row optional (any row whose first cell contains 'name' or
    'material' or 'qty' is treated as a header and skipped).

    Column order: Issued_To, Material, UOM, Quantity, Work_Type
    Missing trailing columns are tolerated and left blank.
    """
    if not text or not text.strip():
        return ConsumptionResult(ok=False, rows=[], message="Paste at least one line.")

    rows: list[dict] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = _split_row(line)
        if _looks_like_header(parts):
            continue
        if len(parts) < 2:
            continue
        rec = {
            "issued_to":     parts[0] if len(parts) > 0 else "",
            "material_text": parts[1] if len(parts) > 1 else "",
            "uom":           parts[2] if len(parts) > 2 else "",
            "quantity":      _to_float(parts[3]) if len(parts) > 3 else 0.0,
            "work_type":     parts[4] if len(parts) > 4 else "",
        }
        rows.append(rec)
    if not rows:
        return ConsumptionResult(ok=False, rows=[], message="No data rows found.")
    return ConsumptionResult(ok=True, rows=rows)


def parse_delivery_note_paste(text: str) -> DeliveryNoteResult:
    """
    Paste form for delivery notes. Format:

        # any line starting with `Key:` populates the header
        DN_No: 15668
        Date: 2026-06-02
        Mob_From: GI - ABU HADRIYAH
        Driver_Name: Imran
        Vehicle_No: 3909
        Prepared_by: Harshavardhan
        Mob_To: CNCEC-RAS AL KHAIR

        # item lines (Material, UOM, Qty), separator-tolerant
        6m pipe, Nos, 45
        4m pipe, Nos, 60
    """
    if not text or not text.strip():
        return DeliveryNoteResult(ok=False, header={}, items=[], message="Paste the note.")

    header: dict[str, str] = {}
    items: list[dict] = []
    _HEADER_KEYS = {
        "dn_no", "ref no", "ref_no",
        "date",
        "mob_from", "customer", "customer name", "received from",
        "driver_name", "driver", "driver name",
        "vehicle_no", "vehicle", "vehicle no",
        "prepared_by", "prepared by", "preparer",
        "mob_to", "location",
    }
    _CANONICAL = {
        "dn_no": "DN_No", "ref no": "DN_No", "ref_no": "DN_No",
        "date":  "Date",
        "mob_from": "Mob_From", "customer": "Mob_From",
        "customer name": "Mob_From", "received from": "Mob_From",
        "driver_name": "Driver_Name", "driver": "Driver_Name",
        "driver name": "Driver_Name",
        "vehicle_no": "Vehicle_No", "vehicle": "Vehicle_No",
        "vehicle no": "Vehicle_No",
        "prepared_by": "Prepared_by", "prepared by": "Prepared_by",
        "preparer":    "Prepared_by",
        "mob_to": "Mob_To", "location": "Mob_To",
    }

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line and line.split(":", 1)[0].strip().lower() in _HEADER_KEYS:
            k, v = line.split(":", 1)
            header[_CANONICAL[k.strip().lower()]] = v.strip()
            continue
        parts = _split_row(line)
        if _looks_like_header(parts):
            continue
        if len(parts) < 2:
            continue
        items.append({
            "material_text": parts[0],
            "uom":           parts[1] if len(parts) > 1 else "",
            "quantity":      _to_float(parts[2]) if len(parts) > 2 else 0.0,
        })

    if not items:
        return DeliveryNoteResult(ok=False, header=header, items=[], message="No item rows found.")
    return DeliveryNoteResult(ok=True, header=header, items=items)


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------
_SPLITTERS = re.compile(r"\t|,|;|\|")


def _split_row(line: str) -> list[str]:
    parts = [p.strip() for p in _SPLITTERS.split(line)]
    return [p for p in parts if p != ""]


def _looks_like_header(parts: list[str]) -> bool:
    if not parts:
        return False
    first = parts[0].lower()
    joined = " ".join(parts).lower()
    return any(k in first for k in ("name", "material", "description", "qty", "quantity")) \
        or "uom" in joined and any(k in joined for k in ("qty", "quantity"))


def _to_float(s: Any) -> float:
    try:
        return float(re.sub(r"[^\d.\-]", "", str(s)))
    except (TypeError, ValueError):
        return 0.0


def _clean_consumption_row(r: dict) -> dict:
    return {
        "issued_to":     str(r.get("issued_to") or "").strip(),
        "material_text": str(r.get("material_text") or "").strip(),
        "uom":           str(r.get("uom") or "").strip(),
        "quantity":      _to_float(r.get("quantity")),
        "work_type":     str(r.get("work_type") or "").strip(),
    }


def _clean_item_row(r: dict) -> dict:
    return {
        "material_text": str(r.get("material_text") or "").strip(),
        "uom":           str(r.get("uom") or "").strip(),
        "quantity":      _to_float(r.get("quantity")),
    }


def _clean_dn_header(h: dict) -> dict:
    keys = ("DN_No", "Date", "Mob_From", "Driver_Name",
            "Vehicle_No", "Prepared_by", "Mob_To")
    return {k: str(h.get(k) or "").strip() for k in keys}
