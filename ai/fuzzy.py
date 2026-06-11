"""
ai/fuzzy.py — match free-text material names to inventory rows
================================================================
The OCR step yields strings like "4m pipe" or "Joint Box (Fash Technical)".
Floor users will not type SAP codes. We need to turn each freeform name into
either:
  - a confident SAP_Code (auto-fill the row), or
  - a short list of candidates the user picks from (ambiguous), or
  - "no match — pick manually" (nothing close enough).

This module is pure Python (`difflib` from stdlib) so it is trivially
unit-testable and free of any Streamlit / Ollama dependency.

Tuning knobs
------------
AUTO_THRESHOLD  : ratio at which we auto-fill without asking.
PICK_THRESHOLD  : ratio above which we surface candidates for the user.
                  Below this, the row is left blank for manual selection.
TOP_N           : how many candidates to surface when ambiguous.

Why difflib and not rapidfuzz
-----------------------------
difflib is stdlib — zero new dependencies. For warehouse inventory sizes
(hundreds to a few thousand items) the perf is fine: ~1ms per item on the
M-series Air. If we ever break 50 000 items, swap to rapidfuzz and these
function signatures stay the same.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# THRESHOLDS — tuned for the kinds of descriptions in the GI catalogue
# ---------------------------------------------------------------------------
AUTO_THRESHOLD: float = 0.85   # ≥ this → auto-fill
PICK_THRESHOLD: float = 0.45   # in [PICK, AUTO) → surface candidates
TOP_N: int = 3


# ---------------------------------------------------------------------------
# NORMALISATION
# ---------------------------------------------------------------------------
_PUNCT = re.compile(r"[^\w\s]")
_WS    = re.compile(r"\s+")

# Common UOM/word noise we strip so "4m pipe" matches "Pipe 4m DN50" well.
_NOISE = {
    "pcs", "pc", "ea", "each", "nos", "no", "set", "sets",
    "unit", "units",
}


def normalise(s: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace, drop UOM noise."""
    if s is None:
        return ""
    s = str(s).lower()
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    tokens = [t for t in s.split() if t not in _NOISE]
    return " ".join(tokens)


def _ratio(a: str, b: str) -> float:
    """SequenceMatcher ratio on the normalised forms. 0.0 if either side is empty."""
    na, nb = normalise(a), normalise(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _token_dice(a_norm: str, b_norm: str) -> float:
    """
    Sørensen–Dice coefficient over token sets:
        2 |A ∩ B| / (|A| + |B|)

    Token-aware scoring is critical for warehouse descriptions where word
    ORDER varies but the same tokens appear ("6m pipe" vs "Pipe 6m DN50").
    Pure character-level SequenceMatcher punishes reorderings unfairly.
    """
    if not a_norm or not b_norm:
        return 0.0
    ta = set(a_norm.split())
    tb = set(b_norm.split())
    inter = ta & tb
    if not inter:
        return 0.0
    return (2.0 * len(inter)) / (len(ta) + len(tb))


def _hybrid_score(query: str, candidate: str) -> float:
    """
    MAX of character-ratio and token-Dice. Each metric catches what the
    other misses:
      - SequenceMatcher handles typos / partial spellings ("Doubel Clamp").
      - Dice handles reorderings / abbreviations ("6m pipe" vs "Pipe 6m DN50").
    Bounded in [0, 1].
    """
    na, nb = normalise(query), normalise(candidate)
    if not na or not nb:
        return 0.0
    char = SequenceMatcher(None, na, nb).ratio()
    tok  = _token_dice(na, nb)
    return max(char, tok)


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------
def match_material(
    query: str,
    inventory: pd.DataFrame,
    top_n: int = TOP_N,
    pick_threshold: float = PICK_THRESHOLD,
) -> list[dict]:
    """
    Score every inventory row against `query` and return the top_n above
    `pick_threshold`, best first.

    Each returned dict has:
        SAP_Code, Equipment_Description, Material_Code (if present),
        UOM (if present), score (0.0–1.0)

    Tolerant of missing columns in the inventory DataFrame — only SAP_Code
    + Equipment_Description are required.
    """
    if inventory is None or inventory.empty or not query or not query.strip():
        return []

    if "SAP_Code" not in inventory.columns or "Equipment_Description" not in inventory.columns:
        return []

    # Vectorise the ratio computation so the page stays snappy even with
    # 5 000+ items. We score once per row, sort, then slice top_n.
    descs = inventory["Equipment_Description"].fillna("").astype(str)
    nq = normalise(query)
    if not nq:
        return []

    scores = [_hybrid_score(query, d) for d in descs]

    pairs = list(zip(range(len(scores)), scores))
    pairs.sort(key=lambda p: p[1], reverse=True)

    out: list[dict] = []
    has_mat = "Material_Code" in inventory.columns
    has_uom = "UOM" in inventory.columns
    for idx, score in pairs[: top_n * 3]:   # over-fetch then filter
        if score < pick_threshold:
            break
        row = inventory.iloc[idx]
        out.append({
            "SAP_Code":              str(row["SAP_Code"]),
            "Equipment_Description": str(row["Equipment_Description"]),
            "Material_Code":         (str(row["Material_Code"]) if has_mat and pd.notna(row.get("Material_Code")) else ""),
            "UOM":                   (str(row["UOM"]) if has_uom and pd.notna(row.get("UOM")) else ""),
            "score":                 round(float(score), 3),
        })
        if len(out) >= top_n:
            break
    return out


def best_match(
    query: str,
    inventory: pd.DataFrame,
    auto_threshold: float = AUTO_THRESHOLD,
) -> Optional[dict]:
    """
    Convenience: returns the single best match if and only if its score
    is ≥ auto_threshold (the auto-fill bar). Returns None otherwise so the
    caller knows to surface candidates instead.
    """
    cands = match_material(query, inventory, top_n=1, pick_threshold=auto_threshold)
    return cands[0] if cands else None


def resolve_rows(
    rows: Iterable[dict],
    inventory: pd.DataFrame,
    name_key: str = "material_text",
) -> list[dict]:
    """
    Walks a list of OCR'd row dicts and augments each with:
        SAP_Code           — auto-filled when confidence high enough
        Equipment_Description, Material_Code, UOM — auto-filled with SAP
        candidates         — list of dicts (empty when auto-filled)
        match_state        — 'auto' | 'pick' | 'unknown'
        score              — best score (for UI sorting / display)

    The input row's existing keys are preserved. The caller decides what to
    do when match_state != 'auto' (typically: show a picker beside the row).
    """
    out: list[dict] = []
    for raw in rows:
        row = dict(raw)
        q = str(row.get(name_key, "") or "")
        cands = match_material(q, inventory, top_n=TOP_N, pick_threshold=PICK_THRESHOLD)
        if not cands:
            row.update({
                "SAP_Code": "", "Equipment_Description": "",
                "Material_Code": "", "UOM": row.get("UOM", "") or "",
                "candidates": [], "match_state": "unknown", "score": 0.0,
            })
        elif cands[0]["score"] >= AUTO_THRESHOLD:
            top = cands[0]
            row.update({
                "SAP_Code":              top["SAP_Code"],
                "Equipment_Description": top["Equipment_Description"],
                "Material_Code":         top["Material_Code"],
                # Prefer the OCR'd UOM if it was provided, else inventory's.
                "UOM":                   row.get("UOM") or top.get("UOM", ""),
                "candidates":            [],
                "match_state":           "auto",
                "score":                 top["score"],
            })
        else:
            row.update({
                "SAP_Code": "", "Equipment_Description": "",
                "Material_Code": "", "UOM": row.get("UOM", "") or "",
                "candidates":  cands,
                "match_state": "pick",
                "score":       cands[0]["score"],
            })
        out.append(row)
    return out
