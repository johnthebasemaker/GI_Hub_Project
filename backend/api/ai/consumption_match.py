"""
backend/api/ai/consumption_match.py — a written product name → an item (Phase 21d).

Rulings Q21-3..5. The store keepers' consumption papers name items the way
people say them — *Dust Mash*, *Tyvek coverall*, *Trash Bag*, *Clear glass* —
and the inventory master names them the way the PR did — *DUST Mask*,
*TRASH BAG (BLACK)*, *SAFETY GLASSES CLEAR*. This module decides, per written
name, one of three states the review grid shows:

    auto       green   EXACT (after normalising) or LEARNED (a store keeper
                       confirmed this written form before, at this site)
    suggested  gold    the closest item — NEVER accepted by itself (Q21-5);
                       the store keeper clicks Accept, which LEARNS it (Q21-3)
    unknown    red     nothing close enough: the SAP must be chosen by hand

Layers, cheapest first, each saying WHY it matched (`source`):

    exact → learned → string (the spec scorer + the hybrid scorer, the higher)
          → semantic (optional, `nomic-embed-text`; measured first, Q21-4)

⚠️ STOCK-AWARE, NEVER STOCK-HIDING. An item with no stock at the site ranks
below an in-stock one of similar score and is labelled — never removed: the
store keeper may know the shelf better than the ledger (spec 06).

⚠️ `ai/handwritten.decide_match` auto-accepts a spec score ≥ 40 with a lead
≥ 8 — that is the SPEC's TSV output and stays pinned by suite AM. The review
grid uses THIS module's state instead, because ruling Q21-5 says a fuzzy
suggestion is never green.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, Optional

from . import fuzzy as FZ
from . import handwritten as HW

SUGGEST_FLOOR = 0.45        # below this the best candidate is not shown as a suggestion
NO_STOCK_PENALTY = 0.08     # an out-of-stock item ranks below an in-stock near-tie
TOP_N = 5
SEMANTIC_FLOOR = 0.62       # cosine; only consulted when the string layer is weak
STRING_WEAK = 0.60

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def written_key(text: str) -> str:
    """The form an alias is stored and looked up under: the spec's known OCR
    corrections applied, lower-case, punctuation and spacing collapsed.
    "Dust  Mash." and "dust mash" are one key."""
    corrected, _ = HW.apply_corrections(str(text or ""))
    s = _PUNCT.sub(" ", corrected.lower())
    return _WS.sub(" ", s).strip()


def _cand(row: dict, score: float, source: str, stock: dict[str, float]) -> dict:
    sap = str(row["SAP_Code"]).strip()
    qty = stock.get(sap)
    return {"SAP_Code": sap, "Equipment_Description": str(row.get("Equipment_Description") or ""),
            "UOM": str(row.get("UOM") or ""), "score": round(float(score), 3), "source": source,
            "in_stock": qty is None or qty > 0, "stock": qty}


def string_candidates(written: str, inventory: list[dict], stock: dict[str, float]) -> list[dict]:
    """Both existing scorers, the higher per item; stock-aware order."""
    corrected, _ = HW.apply_corrections(written)
    best: dict[str, tuple[float, dict]] = {}
    for c in HW.spec_match(corrected, inventory, top_n=25):
        best[c["SAP_Code"]] = (c["confidence"] / 100.0, c)
    by_sap = {str(r["SAP_Code"]).strip(): r for r in inventory if r.get("SAP_Code") is not None}
    for row in inventory:
        desc = str(row.get("Equipment_Description") or "")
        if row.get("SAP_Code") is None or not desc:
            continue
        sc = FZ._hybrid_score(corrected, desc)
        sap = str(row["SAP_Code"]).strip()
        if sc > best.get(sap, (0.0, None))[0]:
            best[sap] = (sc, row)
    out = [_cand(by_sap.get(sap, r), sc, "fuzzy", stock) for sap, (sc, r) in best.items()
           if sap in by_sap]
    out.sort(key=lambda c: -(c["score"] - (0 if c["in_stock"] else NO_STOCK_PENALTY)))
    return out[:TOP_N]


def match(written: str, inventory: list[dict], *, aliases: Optional[dict[str, dict]] = None,
          stock: Optional[dict[str, float]] = None,
          semantic: Optional[Callable[[str], list[tuple[str, float]]]] = None) -> dict:
    """One written name → {state, sap, description, source, score, candidates,
    written, written_key, learned_count}. `aliases` maps written_key →
    {"SAP_Code", "confirmations"} for THIS site; `stock` maps SAP → quantity on
    hand at the site; `semantic(text)` returns [(SAP, cosine)] best first."""
    stock = stock or {}
    aliases = aliases or {}
    key = written_key(written)
    base = {"written": written, "written_key": key, "learned_count": 0}
    if not key:
        return {**base, "state": "unknown", "sap": None, "description": None,
                "source": None, "score": 0.0, "candidates": []}
    by_sap = {str(r["SAP_Code"]).strip(): r for r in inventory if r.get("SAP_Code") is not None}
    # 1 · exact
    for sap, r in by_sap.items():
        if written_key(r.get("Equipment_Description") or "") == key:
            c = _cand(r, 1.0, "exact", stock)
            return {**base, "state": "auto", "sap": sap, "description": c["Equipment_Description"],
                    "source": "exact", "score": 1.0, "candidates": [c]}
    # 2 · learned
    a = aliases.get(key)
    if a and str(a.get("SAP_Code")) in by_sap:
        c = _cand(by_sap[str(a["SAP_Code"])], 1.0, "learned", stock)
        return {**base, "state": "auto", "sap": c["SAP_Code"], "description": c["Equipment_Description"],
                "source": "learned", "score": 1.0, "candidates": [c],
                "learned_count": int(a.get("confirmations") or 1)}
    # 3 · string
    cands = string_candidates(written, inventory, stock)
    # 4 · semantic, only where the string layer is weak (and only if wired)
    if semantic is not None and (not cands or cands[0]["score"] < STRING_WEAK):
        seen = {c["SAP_Code"] for c in cands}
        for sap, cos in semantic(written)[:TOP_N]:
            if cos >= SEMANTIC_FLOOR and sap in by_sap and sap not in seen:
                cands.append(_cand(by_sap[sap], cos, "semantic", stock))
        cands.sort(key=lambda c: -(c["score"] - (0 if c["in_stock"] else NO_STOCK_PENALTY)))
        cands = cands[:TOP_N]
    if cands and cands[0]["score"] >= SUGGEST_FLOOR:
        top = cands[0]
        return {**base, "state": "suggested", "sap": top["SAP_Code"],
                "description": top["Equipment_Description"], "source": top["source"],
                "score": top["score"], "candidates": cands}
    return {**base, "state": "unknown", "sap": None, "description": None, "source": None,
            "score": cands[0]["score"] if cands else 0.0, "candidates": cands}


def match_rows(rows: Iterable[dict], inventory: list[dict], **kw) -> list[dict]:
    """`match` for each row's written product name (`product_name_raw`, else
    `material_text`), returned beside the row."""
    out = []
    for r in rows:
        written = str(r.get("product_name_raw") or r.get("material_text") or "").strip()
        out.append({**r, "match": match(written, inventory, **kw)})
    return out
