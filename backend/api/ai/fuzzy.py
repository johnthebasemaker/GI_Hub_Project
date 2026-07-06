"""
backend/api/ai/fuzzy.py — match free-text material names to inventory rows.

Pandas-free port of legacy ai/fuzzy.py (the API runtime deliberately has no
pandas — REPO_MAP contract): `inventory` is a list of row dicts as returned
by the /inventory endpoints, needing at least SAP_Code +
Equipment_Description keys. Same hybrid scorer, same thresholds, same
auto/pick/unknown state machine — the OCR review grid (Phase AI-3) and any
future matcher reuse this unchanged.

Scoring = MAX(character SequenceMatcher ratio, token Sørensen–Dice):
SequenceMatcher catches typos ("Doubel Clamp"); Dice catches reorderings
("6m pipe" vs "Pipe 6m DN50"). stdlib difflib is plenty for catalogue sizes
in the hundreds-to-thousands; swap to rapidfuzz later without changing
signatures if the catalogue ever breaks ~50k items.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable, Optional

AUTO_THRESHOLD: float = 0.85   # ≥ this → auto-fill
PICK_THRESHOLD: float = 0.45   # in [PICK, AUTO) → surface candidates
TOP_N: int = 3

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")

# Common UOM/word noise stripped so "4m pipe" matches "Pipe 4m DN50" well.
_NOISE = {"pcs", "pc", "ea", "each", "nos", "no", "set", "sets", "unit", "units"}


def normalise(s) -> str:
    """Lower-case, strip punctuation, collapse whitespace, drop UOM noise."""
    if s is None:
        return ""
    s = str(s).lower()
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return " ".join(t for t in s.split() if t not in _NOISE)


def _token_dice(a_norm: str, b_norm: str) -> float:
    if not a_norm or not b_norm:
        return 0.0
    ta, tb = set(a_norm.split()), set(b_norm.split())
    inter = ta & tb
    if not inter:
        return 0.0
    return (2.0 * len(inter)) / (len(ta) + len(tb))


def _hybrid_score(query: str, candidate: str) -> float:
    na, nb = normalise(query), normalise(candidate)
    if not na or not nb:
        return 0.0
    char = SequenceMatcher(None, na, nb).ratio()
    return max(char, _token_dice(na, nb))


def match_material(query: str, inventory: list[dict], top_n: int = TOP_N,
                   pick_threshold: float = PICK_THRESHOLD) -> list[dict]:
    """Score every inventory row against `query`; return top_n above
    pick_threshold, best first. Rows need SAP_Code + Equipment_Description;
    Material_Code / UOM are optional."""
    if not inventory or not query or not str(query).strip():
        return []
    nq = normalise(query)
    if not nq:
        return []

    scored = []
    for row in inventory:
        desc = str(row.get("Equipment_Description") or "")
        sap = row.get("SAP_Code")
        if sap is None or not desc:
            continue
        scored.append((_hybrid_score(query, desc), row))
    scored.sort(key=lambda p: p[0], reverse=True)

    out: list[dict] = []
    for score, row in scored:
        if score < pick_threshold or len(out) >= top_n:
            break
        out.append({
            "SAP_Code": str(row["SAP_Code"]),
            "Equipment_Description": str(row.get("Equipment_Description") or ""),
            "Material_Code": str(row.get("Material_Code") or ""),
            "UOM": str(row.get("UOM") or ""),
            "score": round(float(score), 3),
        })
    return out


def best_match(query: str, inventory: list[dict],
               auto_threshold: float = AUTO_THRESHOLD) -> Optional[dict]:
    """The single best match iff its score clears the auto-fill bar."""
    cands = match_material(query, inventory, top_n=1, pick_threshold=auto_threshold)
    return cands[0] if cands else None


def resolve_rows(rows: Iterable[dict], inventory: list[dict],
                 name_key: str = "material_text") -> list[dict]:
    """Augment each OCR'd row with SAP_Code / description / candidates /
    match_state ('auto' | 'pick' | 'unknown') / score. Input keys preserved."""
    out: list[dict] = []
    for raw in rows:
        row = dict(raw)
        q = str(row.get(name_key, "") or "")
        cands = match_material(q, inventory, top_n=TOP_N, pick_threshold=PICK_THRESHOLD)
        if not cands:
            row.update({"SAP_Code": "", "Equipment_Description": "",
                        "Material_Code": "", "UOM": row.get("UOM", "") or "",
                        "candidates": [], "match_state": "unknown", "score": 0.0})
        elif cands[0]["score"] >= AUTO_THRESHOLD:
            top = cands[0]
            row.update({"SAP_Code": top["SAP_Code"],
                        "Equipment_Description": top["Equipment_Description"],
                        "Material_Code": top["Material_Code"],
                        # Prefer the OCR'd UOM when provided, else inventory's.
                        "UOM": row.get("UOM") or top.get("UOM", ""),
                        "candidates": [], "match_state": "auto",
                        "score": top["score"]})
        else:
            row.update({"SAP_Code": "", "Equipment_Description": "",
                        "Material_Code": "", "UOM": row.get("UOM", "") or "",
                        "candidates": cands, "match_state": "pick",
                        "score": cands[0]["score"]})
        out.append(row)
    return out
