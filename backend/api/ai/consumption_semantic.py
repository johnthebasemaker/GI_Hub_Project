"""
backend/api/ai/consumption_semantic.py — the OPTIONAL embedding layer of the
consumption matcher (Phase 21d, ruling Q21-4: measured first, built into the
app only if it beats string + alias significantly).

`nomic-embed-text` (≈ 578 MB resident, Phase 19d) embeds every item
description once, and each written name at match time; the nearest items by
cosine become extra candidates when the string layer is weak. It is consulted
by `tools/ocr_eval.py --semantic`; the app does not load it unless the
scorecard earns it a ruling.
"""
from __future__ import annotations

import math
from typing import Callable

PREFIX_DOC, PREFIX_Q = "search_document: ", "search_query: "   # nomic's task prefixes


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


async def build_local(inventory: list[dict], batch: int = 64) -> Callable[[str], list[tuple[str, float]]]:
    """Embed the catalogue now; return a SYNC lookup for `consumption_match`
    (which is synchronous) that embeds one query per call through a fresh
    event-loop-free HTTP call."""
    import httpx

    from . import client as aic
    items = [(str(r["SAP_Code"]).strip(), str(r.get("Equipment_Description") or ""))
             for r in inventory if r.get("SAP_Code") is not None and r.get("Equipment_Description")]
    vecs: list[list[float]] = []
    for i in range(0, len(items), batch):
        vecs += await aic.embed([PREFIX_DOC + d for _, d in items[i:i + batch]])

    def lookup(text: str) -> list[tuple[str, float]]:
        r = httpx.post(f"{aic.OLLAMA_HOST}/api/embed",
                       json={"model": aic.MODEL_EMBED, "input": [PREFIX_Q + text]}, timeout=60)
        r.raise_for_status()
        q = r.json()["embeddings"][0]
        scored = sorted(((sap, _cos(q, v)) for (sap, _), v in zip(items, vecs)),
                        key=lambda p: -p[1])
        return scored[:10]
    return lookup
