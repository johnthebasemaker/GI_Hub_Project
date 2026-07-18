"""
backend/api/ai/handwritten.py — deterministic stages of the handwritten
consumption-form spec (docs/features/handwritten-ocr, v1.0).

The vision model (ocr.CONSUMPTION_PROMPT) only TRANSCRIBES the form; every
rule below is deterministic and idempotent, per the spec's design
principles: never fabricate, never silently drop, stock is ground truth,
substitution only by explicit rule, fuzzy match returns candidates.

Preserved exactly (spec §"preserve_exactly"):
  * fuzzy scoring + thresholds (05-fuzzy-matching.md)
  * substitution table (06-stock-validation.md — closed list)
  * simulation ordering key (07/10)
  * flag taxonomy (08-flag-system.md)
  * output row_id format + TSV column layout (09-output-format.md)
  * corrections list (11-common-corrections.md)
Change process: edit the owning spec file first, then this module, then the
suite-AM pins.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

# ── 02 · header date ─────────────────────────────────────────────────────────
_DATE_FORMATS = [  # priority order; century_assumption for 2-digit years
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), None),
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2})$"), 2000),
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2})$"), 2000),
]
_DIGIT_FIXES = str.maketrans({"l": "1", "I": "1", "O": "0", "o": "0"})
_YEAR_WINDOW = (-2, +1)  # relative to today, inclusive


def parse_form_date(text: Any, today: Optional[date] = None
                    ) -> tuple[Optional[str], Optional[str]]:
    """(date_iso, flag). DD/MM only — never swapped to MM/DD; a missing or
    invalid date is CRIT_DATE_UNPARSEABLE, never guessed."""
    today = today or date.today()
    s = str(text or "").strip().translate(_DIGIT_FIXES)
    for rx, century in _DATE_FORMATS:
        m = rx.match(s)
        if not m:
            continue
        dd, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if century:
            yy += century
        try:
            d = date(yy, mm, dd)
        except ValueError:
            return None, "CRIT_DATE_UNPARSEABLE"
        if not (today.year + _YEAR_WINDOW[0] <= d.year <= today.year + _YEAR_WINDOW[1]):
            return None, "CRIT_DATE_UNPARSEABLE"
        return d.isoformat(), None
    return None, "CRIT_DATE_UNPARSEABLE"


# ── 11 · common corrections (closed list; sequential; before fuzzy match) ────
_CORRECTIONS: list[tuple[str, str, str]] = [  # (pattern, replacement, mode)
    ("Yloues", "Gloves", "substring_ci"),
    ("Ylones", "Gloves", "substring_ci"),
    ("Yvek", "Tyvek", "substring_ci"),
    ("Tywek", "Tyvek", "substring_ci"),
    ("Yreen", "Green", "substring_ci"),
    ("Slasting", "Blasting", "substring_ci"),
    ("Head Lamp", "HANGLING LAMP", "substring_ci"),
    ("Yellow holder", "Blast nozzle Holder", "substring_ci"),
    ("Yland glass", "Blasting glass", "substring_ci"),
    (r"^Mask$", "Dust Mask", "regex_ci"),
    ("10x10 Tarpaulin", "Tarpaulin 10x10", "substring_ci"),
    ("Tarpaulin (10x10)", "Tarpaulin 10x10", "substring_ci"),
    ("50m messuring tape", "Measuring Tape 50m", "substring_ci"),
    ("Vaultex earplug", "Ear PLUG Vaultex", "substring_ci"),
    ("Stanley bag", "Tool Box STANLEY", "substring_ci"),
    ("Stanley Tool Bag", "Tool Box STANLEY", "substring_ci"),
    ("Uvex goggle", "UVEX MONO GOGGLE", "substring_ci"),
    ("KARAM helmet", "KARAM SAFETY HELMET WHITE", "substring_ci"),
]


def apply_corrections(name: str) -> tuple[str, list[str]]:
    """Sequentially apply the observed OCR-error corrections. Returns the
    corrected text and the list of applied 'pattern → replacement' notes."""
    out, applied = str(name or ""), []
    for pattern, repl, mode in _CORRECTIONS:
        if mode == "regex_ci":
            new = re.sub(pattern, repl, out, flags=re.IGNORECASE)
        else:
            new = re.sub(re.escape(pattern), repl, out, flags=re.IGNORECASE)
        if new != out:
            applied.append(f"{pattern} → {repl}")
            out = new
    return out, applied


# ── 03 · row parsing: ditto marks + quantity rules ───────────────────────────
_DITTO = {'"', "〃", ",,", "''", "`"}
_DITTO_FIELDS = ("product_name_raw", "received_by", "tank_no", "work_type")
_ADDITIVE_RX = re.compile(r"^\d+(\+\d+)+$")
_LEADING_INT_RX = re.compile(r"^(\d+)")


def resolve_ditto(rows: list[dict]) -> None:
    """In-place: ditto glyphs copy the RESOLVED value from the row above.
    A ditto on the first row has no source → null + info flag."""
    prev: dict = {}
    for row in rows:
        for f in _DITTO_FIELDS:
            v = str(row.get(f) or "").strip()
            if v in _DITTO:
                if f in prev and prev[f]:
                    row[f] = prev[f]
                    row.setdefault("ditto_fields", []).append(f)
                else:
                    row[f] = None
                    row.setdefault("flags", []).append("INFO_DITTO_WITH_NO_SOURCE")
        prev = {f: row.get(f) for f in _DITTO_FIELDS}


def parse_qty(qty_raw: Any, has_product: bool) -> tuple[Optional[float], list[str]]:
    """Spec quantity rules: additive '2+3' sums; blank defaults to 1 when a
    product is named; zero/negative rejects; '~5' is approximate."""
    flags: list[str] = []
    s = str(qty_raw if qty_raw is not None else "").strip()
    if s.startswith("~"):
        flags.append("WARN_QTY_APPROXIMATE")
        s = s[1:].strip()
    if not s:
        if has_product:
            return 1.0, flags + ["WARN_QTY_DEFAULTED"]
        return None, flags
    if _ADDITIVE_RX.match(s):
        return float(sum(int(p) for p in s.split("+"))), flags + ["WARN_ADDITIVE_QTY"]
    try:
        q = float(s)
    except ValueError:
        m = _LEADING_INT_RX.match(s)
        if not m:
            if has_product:
                return 1.0, flags + ["WARN_QTY_DEFAULTED"]
            return None, flags
        q = float(m.group(1))
    if q <= 0:
        return q, flags + ["CRIT_QTY_ZERO_OR_NEGATIVE"]
    return q, flags


# ── 05 · fuzzy matching (spec scorer — distinct from ai/fuzzy.py's hybrid) ───
AUTO_ACCEPT_MIN_CONFIDENCE = 40
AUTO_ACCEPT_MIN_LEAD = 8
TOP_N_CANDIDATES = 5
_MIN_WORD_LEN = 3
_STOPWORDS = {"a", "an", "the", "of", "for", "and", "or", "to", "with"}


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _words(s: str) -> list[str]:
    return [w for w in re.split(r"[^\w]+", s)
            if len(w) >= _MIN_WORD_LEN and w not in _STOPWORDS]


def _spec_score(query: str, candidate: str, hints: Optional[set[str]] = None) -> int:
    """05-fuzzy-matching.md weighted pass. raw → min(100, raw*2)."""
    q, c = _norm(query), _norm(candidate)
    if not q or not c:
        return 0
    raw = 0
    if q in c:
        raw += 20
    if c in q:
        raw += 15
    qw, cw = _words(q), _words(c)
    shared = [w for w in qw if w in set(cw)]
    raw += 5 * len(shared)
    if len(shared) >= 3:
        raw += 5
    if qw and cw and qw[0] == cw[0]:
        raw += 3
    for h in (hints or ()):  # optional catalogue hint words
        if h in c:
            raw += 3
    return min(100, raw * 2)


def spec_match(query: str, inventory: list[dict],
               top_n: int = TOP_N_CANDIDATES) -> list[dict]:
    """Exact pass (normalised equality → 100) then weighted pass. Returns
    top_n candidates best-first with `confidence` 0-100."""
    nq = _norm(query)
    if not nq:
        return []
    scored: list[tuple[int, dict]] = []
    for row in inventory:
        desc = str(row.get("Equipment_Description") or "")
        if not desc or row.get("SAP_Code") is None:
            continue
        conf = 100 if _norm(desc) == nq else _spec_score(query, desc)
        if conf > 0:
            scored.append((conf, row))
    scored.sort(key=lambda p: -p[0])
    return [{"SAP_Code": str(r["SAP_Code"]),
             "Equipment_Description": str(r.get("Equipment_Description") or ""),
             "Material_Code": str(r.get("Material_Code") or ""),
             "UOM": str(r.get("UOM") or ""),
             "confidence": conf}
            for conf, r in scored[:top_n]]


def decide_match(cands: list[dict]) -> tuple[Optional[dict], bool]:
    """(winner, auto_accepted). Auto-accept needs confidence ≥ 40 AND a lead
    of ≥ 8 over the runner-up; otherwise the row is needs_review."""
    if not cands:
        return None, False
    lead = cands[0]["confidence"] - (cands[1]["confidence"] if len(cands) > 1 else 0)
    if cands[0]["confidence"] >= AUTO_ACCEPT_MIN_CONFIDENCE and lead >= AUTO_ACCEPT_MIN_LEAD:
        return cands[0], True
    return None, False


# ── 06 · stock validation (closed substitution list) ─────────────────────────
SUBSTITUTIONS: dict[str, tuple[str, str]] = {  # out_sap → (in_sap, reason)
    "1107": ("1097", "Vaultex is current supplier"),
    "1137": ("1165", "Bulk-pack replacement"),
    "1176": ("1076", "Colour distinction dropped"),
    "1235": ("1163", "Consolidated cable-tie SKU"),
    "1271": ("1272", "HSE re-label"),
    "1141": ("1072", "Generic FE retired for DCP-specific"),
    "1105": ("1163", "Merged with cable-tie stock"),
    "1237": ("1163", "Merged with cable-tie stock"),
}


def validate_stock(row: dict, stock: dict[str, float]) -> None:
    """R1 pass if stock > 0 · R2 substitute (no chaining) · R3/R4 block."""
    sap = row.get("sap_code")
    if not sap:
        return
    if stock.get(sap, 0.0) > 0:
        return  # R1
    sub = SUBSTITUTIONS.get(sap)
    if sub is None:
        row.setdefault("flags", []).append("CRIT_ZERO_STOCK_NO_SUBSTITUTE")  # R3
        return
    in_sap, reason = sub
    if stock.get(in_sap, 0.0) > 0:  # R2 — substitution is always shown in UI
        row["substituted_from"] = sap
        row["substitution_reason"] = reason
        row["sap_code"] = in_sap
        row.setdefault("flags", []).append("WARN_SUBSTITUTED")
    else:
        row.setdefault("flags", []).append("CRIT_ZERO_STOCK_NO_SUBSTITUTE")  # R4


# ── 07 · stock simulation ────────────────────────────────────────────────────
LOW_STOCK_THRESHOLD = 5.0


def simulate(rows: list[dict], stock: dict[str, float]) -> None:
    """Running-total deduction across the WHOLE batch in
    (date_iso, source_form_id, source_row_no) order. Pure simulation — no
    side effects; low-stock warned once per SAP."""
    pool = dict(stock)
    warned_low: set[str] = set()
    for row in sorted(rows, key=lambda r: (str(r.get("date_iso") or ""),
                                           str(r.get("source_form_id") or ""),
                                           int(r.get("source_row_no") or 0))):
        sap, qty = row.get("sap_code"), row.get("qty")
        if not sap or qty is None or row_blocked(row):
            continue
        before = pool.get(sap, 0.0)
        after = before - float(qty)
        row["stock_before"], row["stock_after"] = before, after
        if after < 0:
            row["would_go_negative"] = True
            row.setdefault("flags", []).append("CRIT_WOULD_GO_NEGATIVE")
            row["simulation_note"] = (f"stock {before:g} − {qty:g} would go "
                                      f"negative ({after:g})")
        elif before > LOW_STOCK_THRESHOLD >= after and sap not in warned_low:
            warned_low.add(sap)
            row["crosses_low_stock"] = True
            row.setdefault("flags", []).append("WARN_LOW_STOCK_CROSSED")
        pool[sap] = max(after, 0.0) if after < 0 else after


# ── 08 · flags ───────────────────────────────────────────────────────────────
_MARKERS = {"INFO": "[?]", "WARN": "⚠️", "CRIT": "🚨"}


def flag_severity(code: str) -> str:
    return {"INFO": "info", "WARN": "warning", "CRIT": "critical"}[code.split("_", 1)[0]]


def flag_marker(code: str) -> str:
    return _MARKERS[code.split("_", 1)[0]]


def row_blocked(row: dict) -> bool:
    return any(f.startswith("CRIT_") for f in row.get("flags", ()))


# ── 09 · output: row ids + legacy TSV export ─────────────────────────────────
_TSV_COLUMNS = 17  # positional layout is a legacy VBA contract — never reorder


def row_id(row: dict) -> str:
    return f"{row.get('source_form_id')}_{row.get('source_row_no')}"


def to_tsv(rows: list[dict]) -> str:
    """The 17-column legacy TSV (09-output-format.md). Only clean rows are
    exported — blocked / critical rows never reach the legacy sheet. Empty
    columns are the VBA side's to fill (SAP, material code, UOM, spacers)."""
    lines = []
    for r in rows:
        if row_blocked(r):
            continue
        cols = [""] * _TSV_COLUMNS
        cols[0] = str(r.get("date_iso") or "")
        cols[3] = str(r.get("resolved_description") or r.get("product_name_raw") or "")
        q = r.get("qty")
        cols[5] = ("%g" % q) if isinstance(q, (int, float)) else ""
        cols[9] = str(r.get("work_type") or "")
        cols[10] = str(r.get("tank_no") or "")
        cols[15] = str(r.get("received_by") or "")
        lines.append("\t".join(cols))
    return "\n".join(lines)


# ── batch orchestration (04 column map + 10 batch rules) ─────────────────────
ROWS_PER_BATCH = 300


def process_batch(forms: list[dict], inventory: list[dict],
                  stock: dict[str, float],
                  today: Optional[date] = None) -> dict:
    """forms: [{form_id, date_text?, date_iso?, rows: [{source_row_no,
    received_by, tank_no, product_name_raw, qty (raw), work_type,
    struck_through?}]}] → {rows, summary, tsv}. Idempotent; no DB writes
    (the OCR endpoint never writes — posting is the Issue flow's job)."""
    out_rows: list[dict] = []
    struck = 0
    batch_flags: list[str] = []
    for form in forms:
        fid = str(form.get("form_id") or "form")
        d_iso = form.get("date_iso")
        d_flag = None
        if not d_iso:
            d_iso, d_flag = parse_form_date(form.get("date_text"), today)
        rows = [dict(r) for r in (form.get("rows") or [])]
        resolve_ditto(rows)
        for i, r in enumerate(rows, start=1):
            if r.get("struck_through"):
                struck += 1  # excluded from output, counted in the summary
                continue
            name_raw = str(r.get("product_name_raw") or "").strip() or None
            qty, qflags = parse_qty(r.get("qty"), has_product=name_raw is not None)
            if name_raw is None and qty is None:
                continue  # row_keep_condition
            row = {"row_id": None,
                   "source_form_id": fid,
                   "source_row_no": int(r.get("source_row_no") or i),
                   "date_iso": d_iso,
                   "received_by": r.get("received_by") or None,
                   "tank_no": r.get("tank_no") or None,
                   "work_type": r.get("work_type") or None,
                   "product_name_raw": name_raw,
                   "qty": qty,
                   "flags": list(r.get("flags", [])) + qflags}
            if d_flag:
                row["flags"].append(d_flag)
            if name_raw is None:
                row["flags"].append("CRIT_PRODUCT_NAME_MISSING")
                row["candidates"] = []
            else:
                corrected, notes = apply_corrections(name_raw)
                row["product_name_corrected"] = corrected
                if notes:
                    row["corrections_applied"] = notes
                cands = spec_match(corrected, inventory)
                winner, auto = decide_match(cands)
                if auto:
                    row["sap_code"] = winner["SAP_Code"]
                    row["resolved_description"] = winner["Equipment_Description"]
                    row["match_confidence"] = winner["confidence"]
                    row["candidates"] = []
                    validate_stock(row, stock)
                else:
                    row["sap_code"] = None
                    row["candidates"] = cands
                    row["flags"].append("INFO_MATCH_UNCERTAIN")
            row["row_id"] = row_id(row)
            out_rows.append(row)
            if len(out_rows) >= ROWS_PER_BATCH:
                batch_flags.append("BATCH_ROW_LIMIT_REACHED")
                break
    simulate(out_rows, stock)
    for r in out_rows:
        r["blocked"] = row_blocked(r)
        r["markers"] = sorted({flag_marker(f) for f in r["flags"]})
    if struck and len(out_rows) and struck >= max(3, len(out_rows) // 2):
        batch_flags.append("BATCH_HIGH_STRUCK_THROUGH_COUNT")
    if not out_rows:
        batch_flags.append("BATCH_EMPTY")
    return {
        "rows": out_rows,
        "tsv": to_tsv(out_rows),
        "summary": {
            "total_rows": len(out_rows),
            "auto_matched": sum(1 for r in out_rows if r.get("sap_code")),
            "needs_review": sum(1 for r in out_rows
                                if "INFO_MATCH_UNCERTAIN" in r["flags"]),
            "blocked": sum(1 for r in out_rows if r["blocked"]),
            "substituted": sum(1 for r in out_rows if r.get("substituted_from")),
            "struck_through_excluded": struck,
            "batch_flags": batch_flags,
        },
    }
