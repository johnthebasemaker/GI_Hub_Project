"""
backend/api/ai/semantic.py — the semantic safety signal (Phase 19d, Q19-4).

Guard v3's patterns are words, so they miss a paraphrase: 11 of the 19
holdout attacks score 0 on every pattern. The 1.5B router judges meaning but is
a noisy judge (Phase 18 §2.3). This adds a third, cheap, deterministic
opinion. Embed the question with `nomic-embed-text`, look up its k nearest
labelled examples in the bank (the dev attacks, their negative twins, and
ordinary questions: `semantic_bank.json`), and say "this sounds like the
attacks" when it does.

MEASURED, NOT ASSUMED (rule 11). Chosen on the DEV set leave-one-out, then
scored ONCE on holdout v1 (counts only, P17-D3):

    k=5, top attack similarity ≥ 0.65, attack share of the neighbours' weight ≥ 0.5
    dev  (leave-one-out): fires on 19/39 attacks, 0/63 twins, 0/60 ordinary
    holdout v1:           fires on 13/19 attacks, 1/19 twins

⚠️ IT NEVER REFUSES, AND NEVER EVEN WARNS, ON ITS OWN (P18-combo). A fired
signal does two things only:

  1. the question skips stage 0 and goes to the router model, as a WARN does;
  2. it is a guard HIT, so Q17-2's rule applies: hits + the model saying
     `is_safe: false` → refuse. The model saying safe → answered as before.

So a false fire costs one router call. Turning it into a refusal takes the
model's agreement, the same evidence a pattern hit needs.

⚠️ FAIL-OPEN, BY DESIGN AND ONLY HERE. Every failure (Ollama down, the model
not pulled, a timeout, a bad bank) returns None, and the request proceeds
exactly as it would with the signal switched off. The guard's own patterns,
which are what actually refuse, are untouched by any of it.

⚠️ OFF BY DEFAULT (`ai_semantic_guard` = 0). The embedder is 578 MB resident
beside the router's 1.35 GB, past Q17-1's 1.5 GB router budget, so switching
it on is the operator's call. The CI eval reports what it WOULD do on every run
(router_eval "semantic shadow").
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import pathlib
from dataclasses import dataclass, field
from typing import Optional

from . import client as aic

log = logging.getLogger("gi.ai.semantic")

K = 5
TAU = 0.65                 # the nearest ATTACK must be at least this similar
SHARE = 0.5                # attacks' share of the k neighbours' similarity weight
PREFIX = "classification: "   # nomic-embed-text's task prefix for this use
BANK_FILE = pathlib.Path(__file__).with_name("semantic_bank.json")
HIT = "semantic.attack"


@dataclass
class SemanticVerdict:
    fired: bool
    share: float
    top_attack: float
    neighbours: list = field(default_factory=list)    # [(id, label, sim)]

    def as_attrs(self) -> dict:
        return {"semantic_fired": self.fired, "semantic_share": round(self.share, 3),
                "semantic_top": round(self.top_attack, 3),
                "semantic_nn": ",".join(f"{i}:{lab[0]}" for i, lab, _s in self.neighbours)}


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class _Bank:
    """The labelled examples and their vectors, embedded once per process and
    model (about 1 s for 162 short texts)."""

    def __init__(self) -> None:
        self.items: list[dict] = []
        self.vecs: list[list[float]] = []
        self.model: Optional[str] = None
        self._lock = asyncio.Lock()

    async def ensure(self) -> bool:
        if self.vecs and self.model == aic.MODEL_EMBED:
            return True
        async with self._lock:
            if self.vecs and self.model == aic.MODEL_EMBED:
                return True
            items = json.loads(BANK_FILE.read_text(encoding="utf-8"))["items"]
            vecs: list[list[float]] = []
            for i in range(0, len(items), 64):
                vecs += await aic.embed([PREFIX + x["text"] for x in items[i:i + 64]],
                                        timeout_s=max(aic.EMBED_TIMEOUT_S, 30.0))
            self.items, self.vecs, self.model = items, [_unit(v) for v in vecs], aic.MODEL_EMBED
            return True

    def clear(self) -> None:
        self.items, self.vecs, self.model = [], [], None


BANK = _Bank()


def knn(qv: list[float], items: list[dict], vecs: list[list[float]], *,
        exclude_exact: bool = False) -> SemanticVerdict:
    """Pure: the verdict for one unit vector against the bank."""
    sims = []
    for it, v in zip(items, vecs):
        s = sum(a * b for a, b in zip(qv, v))
        # ⚠️ leave-one-out for the EVAL: a dev case must not vote for itself.
        if exclude_exact and s >= 0.9999:
            continue
        sims.append((s, it))
    sims.sort(key=lambda x: -x[0])
    top = sims[:K]
    att = [s for s, it in top if it["label"] == "attack"]
    total = sum(max(s, 0.0) for s, _it in top) or 1.0
    share = sum(max(s, 0.0) for s, it in top if it["label"] == "attack") / total
    top_att = max(att) if att else 0.0
    return SemanticVerdict(fired=bool(att) and top_att >= TAU and share >= SHARE,
                           share=share, top_attack=top_att,
                           neighbours=[(it["id"], it["label"], round(s, 3)) for s, it in top])


async def assess(question: str, *, exclude_exact: bool = False,
                 timeout_s: Optional[float] = None) -> Optional[SemanticVerdict]:
    """The verdict for `question`, or None when the signal is unavailable."""
    q = (question or "").strip()
    if not q:
        return None
    try:
        await BANK.ensure()
        [v] = await aic.embed([PREFIX + q], timeout_s=timeout_s)
        return knn(_unit(v), BANK.items, BANK.vecs, exclude_exact=exclude_exact)
    except Exception as e:  # noqa: BLE001 — fail-open: no signal, never an error
        log.info("semantic signal unavailable: %s: %s", type(e).__name__, e)
        return None
