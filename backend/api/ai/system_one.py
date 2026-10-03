"""
backend/api/ai/system_one.py — the "System One" router (Phase 17).

A fast decision BEFORE the slow one: which lane does this question belong to,
and does it look like an attempt to manipulate the assistant? It answers in two
stages and never generates prose.

    stage 0  deterministic, < 1 ms, no model
               guard.scan_input  — a refusal ends it here
               "open the lots page"  → UI_COMMAND, resolved against the menu
               "is there a video…"   → TUTORIAL_SEARCH
    stage 1  the tiny router model, only when stage 0 did not decide
               route.call_structured("router") — JSON Schema-constrained,
               temperature 0, seed 0, top_k 1, pinned (keep_alive -1)
             → guard.with_router_signal folds `is_safe` into the guard verdict

⚠️ THIS MODULE NARROWS, IT NEVER WIDENS. The security boundary is rule 9's
fence (`manual_qa.allowed_sections()`, applied before BM25 scores anything) and,
on the data lane, `safety.is_safe_select` plus the read-only `gi_ai_ro` login.
None of them is touched here, and none of them may ever be simplified because
this exists (P11-4). An intent is a SUGGESTION about which lane to try; whether
a role may use that lane is decided afterwards by `lane_for()`, from the same
rules the lanes already enforce. Suite 17C pins that this file imports none of
`manual_qa`, `safety` or `analytics` — it decides a lane, it never touches what
the lane may see.

⚠️ IT FAILS TO TODAY, NEVER TO "OPEN". Router switched off, Ollama down, model
not pulled, a timeout, a malformed reply → `Decision(intent="MANUAL_QA",
source="fallback")`, and the request proceeds exactly as it did before Phase
17: fence, deterministic guard, cache. That path is already fully guarded, so
an Ollama hiccup can never become an assistant outage (P11-3) and never a
bypass. A TIMEOUT is not retried and never reaches a cloud (P11-9).

Rulings (PROPOSED_PHASE17_PLAN.md, LOCKED 2026-10-03): Q17-1 one pinned router
<= 1 GB beside the generation model · Q17-2 `is_safe:false` is a signal, a veto
only on the SQL lane · Q17-5 `MANUAL_QA` is the fourth intent · Q17-7
`UI_COMMAND` is navigation only.

Named `system_one` because `ai/router.py` (the FastAPI router), `ai/route.py`
(the model gateway) and `ai/query_router.py` (the data templates) already exist,
and a fourth `*rout*` module is a wrong import waiting to happen.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import pathlib
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from . import client as aic
from . import guard
from . import route

INTENTS = ("SQL_QUERY", "TUTORIAL_SEARCH", "UI_COMMAND", "MANUAL_QA")
DEFAULT_INTENT = "MANUAL_QA"

# ⚠️ THE MODEL SPEAKS IN WORDS, THE CONTRACT IN CODES. The router is asked for
# `{"intent": "data" | "video" | "open_page" | "question", "is_safe": bool}` and
# the words are mapped 1:1 onto INTENTS here. Measured in the 17b spike on the
# same eval set: the codes themselves scored 0.915 macro / 38 % detection on
# qwen2.5:1.5b and 0.35 / 0 % on qwen2.5:0.5b; these words, 0.95 / 49 %.
# Everything downstream — Decision, traces, lanes, evals — sees only the codes.
WIRE_TO_INTENT: dict[str, str] = {
    "data": "SQL_QUERY",
    "video": "TUTORIAL_SEARCH",
    "open_page": "UI_COMMAND",
    "question": "MANUAL_QA",
}
assert set(WIRE_TO_INTENT.values()) == set(INTENTS)

# The reply contract. Ollama makes decoding follow it (structured outputs); it
# is re-checked by `_validate` all the same, because a future Ollama or model
# swap must not be trusted blindly.
SCHEMA: dict = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(WIRE_TO_INTENT)},
        "is_safe": {"type": "boolean"},
    },
    "required": ["intent", "is_safe"],
    "additionalProperties": False,
}

# Determinism controls (plan §3.4): greedy decoding with a fixed seed, so the
# same question on the same model digest gets the same lane every time. The
# context is small on purpose — prompt (~530 tokens) + question + 48 out.
OPTIONS: dict = {"seed": 0, "top_k": 1, "num_ctx": 2048}

# ⚠️ PINNED (ruling Q17-1). Without this the router is evicted after the
# default idle window and the next question pays a cold load. The generation
# lanes keep their own 30-minute keep_alive; only this one is pinned.
KEEP_ALIVE: object = int(os.environ.get("GI_AI_ROUTER_KEEP_ALIVE", "-1"))

# The question the model sees. guard's shape check already refuses anything
# over 2,000 characters; a question needs far less than that to be classified,
# and the context window above is sized for this.
MAX_QUESTION_CHARS = 600

_PROMPT_PATH = pathlib.Path(__file__).with_name("system_one_prompt.md")
_COMMENT_RX = re.compile(r"<!--.*?-->", re.S)


@functools.lru_cache(maxsize=1)
def prompt() -> str:
    """The system prompt — the .md file with its leading comment removed."""
    return _COMMENT_RX.sub("", _PROMPT_PATH.read_text(encoding="utf-8")).strip()


@functools.lru_cache(maxsize=1)
def prompt_hash() -> str:
    return hashlib.sha256(prompt().encode("utf-8")).hexdigest()[:12]


# ── the decision ────────────────────────────────────────────────────────────

@dataclass
class Decision:
    intent: str = DEFAULT_INTENT
    is_safe: Optional[bool] = None       # None = the model was not consulted / failed
    source: str = "fallback"             # rules | model | fallback
    blocked: bool = False
    reason: str = ""                     # the sentence the USER sees when blocked
    flagged: bool = False                # model said unsafe, allowed (Q17-2)
    nav: Optional[dict] = None           # {"path", "label"} when UI_COMMAND resolved
    model: str = ""
    ms: int = 0
    valid_json: Optional[bool] = None
    error: str = ""
    guard: dict = field(default_factory=dict)

    def as_attrs(self) -> dict:
        return {"intent": self.intent, "is_safe": self.is_safe,
                "source": self.source, "blocked": self.blocked,
                "flagged": self.flagged, "model": self.model or None,
                "router_ms": self.ms, "valid_json": self.valid_json,
                "error": self.error or None, "prompt_hash": prompt_hash(),
                "nav": (self.nav or {}).get("path"),
                "guard_decision": self.guard.get("decision"),
                "guard_hits": self.guard.get("hits")}


def _validate(text: str) -> Optional[tuple[str, bool]]:
    """(intent, is_safe) when `text` honours SCHEMA exactly, else None."""
    try:
        obj = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(obj, dict) or set(obj) != {"intent", "is_safe"}:
        return None
    intent, safe = WIRE_TO_INTENT.get(obj.get("intent")), obj.get("is_safe")
    if intent is None or not isinstance(safe, bool):
        return None
    return intent, safe


# ── stage 0: navigation, resolved against the menu itself ───────────────────
#
# The page names come from `backend/api/data/nav_access.json`, which
# `tools/announcements.py nav` generates FROM `frontend/src/config/nav.tsx` —
# labels and the roles that may open each route. So "open lots and expiry"
# resolves against the words on the user's own sidebar, and a page the role may
# not open is never a candidate (rule 14: the same matrix, not a copy of it).

_NAV_SNAPSHOT = (pathlib.Path(__file__).resolve().parents[1]
                 / "data" / "nav_access.json")

_NAV_VERB_RX = re.compile(
    r"^\s*(?:please\s+|can\s+you\s+|could\s+you\s+)?"
    r"(?:open(?:\s+up)?|go\s+(?:back\s+)?to|take\s+me\s+(?:back\s+)?to|navigate\s+to|"
    r"switch\s+to|bring\s+up|jump\s+to|show\s+me\s+the)\s+(?P<target>.+?)\s*[.!?]*\s*$",
    re.I)
_FILLER = {"the", "my", "a", "an", "page", "pages", "screen", "tab", "portal",
           "section", "please", "now", "app", "view", "menu"}
_TUTORIAL_RX = re.compile(
    r"\b(videos?|tutorials?|walk-?\s?through|demos?|clips?|footage|screencasts?|"
    r"training\s+(?:film|movie|recording))\b", re.I)


def _tokens(s: str) -> list[str]:
    s = s.lower().replace("&", " and ")
    out = []
    for t in re.findall(r"[a-z0-9]+", s):
        if t in _FILLER:
            continue
        out.append(t[:-1] if len(t) > 3 and t.endswith("s") else t)
    return out


@functools.lru_cache(maxsize=1)
def _nav() -> tuple[dict, dict, dict]:
    """(routes → roles, routes → label, routes → group label). Empty when the
    snapshot is missing — navigation then never resolves (→ MANUAL_QA)."""
    try:
        d = json.loads(_NAV_SNAPSHOT.read_text(encoding="utf-8"))
    except Exception:                                   # noqa: BLE001
        return {}, {}, {}
    return (d.get("routes") or {}, d.get("labels") or {},
            d.get("groupLabels") or {})


def resolve_page(text: str, role: str) -> Optional[dict]:
    """The one menu page `text` names that `role` may open, or None.

    Exact label match beats "the label is inside what was typed" beats "what
    was typed is a distinctive part of the label". A tie at the top score is
    AMBIGUOUS and resolves to nothing — a wrong page is worse than no button.
    """
    m = _NAV_VERB_RX.match(text or "")
    target = _tokens(m.group("target") if m else (text or ""))
    if not target:
        return None
    routes, labels, groups = _nav()
    tset = set(target)

    def _score(name: str) -> int:
        lset = set(_tokens(name))
        if not lset:
            return 0
        if lset == tset:
            return 3
        if lset <= tset:
            return 2
        if tset <= lset and len("".join(tset)) >= 3:
            return 1
        return 0

    scored: list[tuple[int, int, str]] = []
    for path, label in labels.items():
        if role not in (routes.get(path) or []):
            continue
        # The page's own label, or the group it sits in ("Warehouse" for
        # /warehouse). A group name shared by many pages ties, and a tie
        # resolves to nothing.
        score = max(_score(label), _score(groups.get(path, "")))
        if score:
            scored.append((score, -len(label), path))
    if not scored:
        return None
    scored.sort(reverse=True)
    # A tie on SCORE is ambiguous ("open admin" matches every Admin page by its
    # group). Label length only orders the list; it never breaks a tie, because
    # "the shortest name wins" would quietly pick a page nobody asked for.
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    path = scored[0][2]
    return {"path": path, "label": labels[path]}


def classify_rules(question: str, role: str) -> Optional[Decision]:
    """Stage 0. A lane only when the wording leaves no doubt; else None."""
    q = question or ""
    if _NAV_VERB_RX.match(q):
        page = resolve_page(q, role)
        if page:
            return Decision(intent="UI_COMMAND", is_safe=True, source="rules",
                            nav=page)
    if _TUTORIAL_RX.search(q):
        return Decision(intent="TUTORIAL_SEARCH", is_safe=True, source="rules")
    return None


# ── stage 1: the model ──────────────────────────────────────────────────────

async def classify_model(question: str) -> Decision:
    """One call to the router model. Never raises: a failure is a fallback."""
    d = Decision(model=aic.MODEL_ROUTER, source="fallback")
    t0 = time.perf_counter()
    try:
        out = await route.call_structured(
            "router", (question or "")[:MAX_QUESTION_CHARS], system=prompt(),
            schema=SCHEMA, options=OPTIONS, keep_alive=KEEP_ALIVE)
    except Exception as e:                              # noqa: BLE001
        d.error = route.classify(e)
        d.ms = int((time.perf_counter() - t0) * 1000)
        return d
    d.ms = out.ms
    parsed = _validate(out.text)
    d.valid_json = parsed is not None
    if parsed is None:
        d.error = "malformed"
        return d
    d.intent, d.is_safe = parsed
    d.source = "model"
    return d


async def decide(question: str, role: str, *, enabled: bool = True) -> Decision:
    """Stage 0, then stage 1 if needed, then the guard's combination rule."""
    v = guard.scan_input(question or "")
    if v.refused:
        return Decision(source="rules", blocked=True, reason=v.reason,
                        guard=v.as_attrs())
    if not enabled:
        return Decision(source="fallback", error="disabled", guard=v.as_attrs())
    # A question that already WARNS goes to the model even when the wording
    # names a page: a warn plus the model's `is_safe:false` is a refusal, and
    # stage 0 deciding first would skip the one check that could make it one.
    if v.decision == "allow":
        d = classify_rules(question, role)
        if d is not None:
            d.guard = v.as_attrs()
            return d
    d = await classify_model(question)
    if d.source != "model":
        d.guard = v.as_attrs()
        return d
    combined = guard.with_router_signal(v, is_safe=d.is_safe, intent=d.intent)
    d.guard = combined.as_attrs()
    if combined.refused:
        d.blocked, d.reason = True, combined.reason
    elif d.is_safe is False:
        d.flagged = True
    return d


# ── the role gate: an intent never widens a lane ────────────────────────────

def may_query(user: dict) -> bool:
    """Exactly who may call POST /ai/query: `require_level(2)`, which refuses
    the oversight roles outright (auth.QC_OVERSIGHT_ROLES)."""
    from ..auth import QC_OVERSIGHT_ROLES
    return (user.get("role") not in QC_OVERSIGHT_ROLES
            and int(user.get("level") or 0) >= 2)


def lane_for(d: Decision, user: dict, question: str) -> tuple[str, Optional[dict]]:
    """The lane this user's request actually takes: ("BLOCKED" | an intent,
    nav target or None). Every special lane degrades to MANUAL_QA — today's
    path — when the role could not reach it before Phase 17."""
    if d.blocked:
        return "BLOCKED", None
    if d.intent == "SQL_QUERY":
        return ("SQL_QUERY", None) if may_query(user) else (DEFAULT_INTENT, None)
    if d.intent == "UI_COMMAND":
        page = d.nav or resolve_page(question, user.get("role") or "")
        return ("UI_COMMAND", page) if page else (DEFAULT_INTENT, None)
    if d.intent == "TUTORIAL_SEARCH":
        return "TUTORIAL_SEARCH", None      # the caller falls back on no match
    return DEFAULT_INTENT, None
