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
<= 1.5 GB beside the generation model (raised from 1 GB, deviation D1) · Q17-2 `is_safe:false` is a signal, a veto
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
    cached: bool = False                 # the model's answer came from CACHE

    def as_attrs(self) -> dict:
        return {"intent": self.intent, "is_safe": self.is_safe,
                "source": self.source, "blocked": self.blocked,
                "flagged": self.flagged, "model": self.model or None,
                "router_ms": self.ms, "router_cached": self.cached,
                "valid_json": self.valid_json,
                "error": self.error or None, "prompt_hash": prompt_hash(),
                "nav": (self.nav or {}).get("path"),
                "guard_decision": self.guard.get("decision"),
                "guard_hits": self.guard.get("hits"),
                "semantic_fired": self.guard.get("semantic_fired"),
                "semantic_nn": self.guard.get("semantic_nn")}


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


# ⚠️ THE WORDS THAT ASK FOR A VIDEO ARE NOT THE TOPIC OF THE VIDEO (17e).
# "Is there a video on staging a return?" sent whole to tutorials.match()
# landed on the OCR tutorial's beat ABOUT the tutorial gate ("Watch now or
# Watch later") — and so did "a video about booking flights" — because
# "video", "tutorial" and "watch" are that beat's own words. They say THAT the
# person wants a video, never WHICH; the topic is what is left without them.
# Only the MEDIA words are removed: the rest of the sentence stays, because
# the matcher's evidence includes joined bigrams ("staging a return" and
# "stage a return" share `areturn`) and stripping filler too destroyed them.
_VIDEO_REQUEST_RX = re.compile(
    r"\b(videos?|tutorials?|walk-?\s?throughs?|demos?|clips?|footage|screencasts?|"
    r"recordings?|recorded|lessons?|training|watch(ing)?)\b", re.I)


def video_topic(question: str) -> str:
    """The topic of a video request: the question minus the asking words."""
    return re.sub(r"\s+", " ", _VIDEO_REQUEST_RX.sub(" ", question or "")).strip(" ?.!,")


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


# ⚠️ PHASE 18: A HOW-TO QUESTION IS DECIDED WITHOUT THE MODEL. MANUAL_QA is
# the default lane — today's fully-fenced path — so routing a question there is
# never a widening, and for a question the guard ALLOWS the model's is_safe
# could not have refused it anyway (Q17-2: alone, off the SQL lane, it only
# flags). Skipping the call therefore changes no outcome and saves the whole
# of it (~350 ms on Metal, ~0.9-1.6 s on CI's CPU). Measured on routing.yaml:
# all 15 MANUAL_QA prompts match and none of the other 45 does.
#
# The shapes are the unambiguous openings of a question about HOW the app
# works. Anything that also asks for a number, a list or a period ("how do I
# see how many drums…", "…this week") or for a video is left to the model.
_HOWTO_RX = re.compile(
    r"^\s*(?:please\s+)?(?:"
    r"how\s+(?:do|does|can|should|would)\s+\S+"
    r"|what\s+(?:does|do)\s+.+\s+mean\b"
    r"|what\s+is\s+(?:a|an)\s+\w+"
    r"|what\s+is\s+the\s+(?:difference|meaning|purpose|point)\b"
    r"|what\s+(?:is|are)\s+.+\s+(?:for|used\s+for)\s*[?.!]*\s*$"
    r"|what\s+happens\s+(?:when|after|if|to)\b"
    r"|why\s+(?:is|are|does|do|did|was|were|can'?t|cannot|won'?t|isn'?t|doesn'?t)\b"
    r"|who\s+(?:can|may|should|approves|is\s+allowed|are\s+allowed)\b"
    r"|can\s+(?:a|an)\s+\w+"
    r"|explain\b)", re.I)
_DATA_WORDS_RX = re.compile(
    r"\b(how\s+many|how\s+much|quantit(?:y|ies)|qty|stock\s+(?:of|level|at|for)|"
    r"current\s+stock|balance|totals?|list|lists|report|count|"
    r"last\s+(?:week|month|year)|this\s+(?:week|month|year)|yesterday|today|"
    r"expir\w*\s+in|sap\s*\d+)\b|\d{3,}", re.I)


def is_howto(question: str) -> bool:
    """True when `question` is plainly a how/what/why/who question about the app."""
    q = question or ""
    return bool(_HOWTO_RX.match(q)) and not _DATA_WORDS_RX.search(q) \
        and not _VIDEO_REQUEST_RX.search(q)


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
    if is_howto(q):
        return Decision(intent="MANUAL_QA", is_safe=True, source="rules")
    return None


# ── warming: the load must never run on a request's 3-second clock ──────────
#
# ⚠️ FOUND IN 17d, AND IT IS A SILENT, PERMANENT FAILURE. A cold load of the
# router takes ~3 s on this Mac (Ollama: weights 935 MiB + compute 300 MiB) and
# longer on a CPU box. The request budget is 3 s, and when the client gives up
# Ollama CANCELS the load ("Load failed … context canceled", HTTP 499). The next
# question starts the load again and is cancelled again — so after any Ollama
# restart the router never loads, every question falls back, and nothing looks
# wrong except that the fast lanes never fire. 133 of 176 eval calls timed out
# exactly this way before this existed.
#
# So the load happens OFF the request path, with its own generous clock: once
# at startup (main.py lifespan), and again in the background whenever a request
# falls back on a timeout or an unavailable engine. One warm per worker at a
# time; four workers asking at once is still one load inside Ollama.
WARM_TIMEOUT_S = float(os.environ.get("GI_AI_ROUTER_WARM_TIMEOUT_S", "180"))
_WARMING: dict = {"task": None}


async def warm() -> dict:
    """Load (and pin) the router model, and prime its prompt cache. Never raises.

    ⚠️ THE SYSTEM PROMPT IS SENT (Phase 18). Ollama keeps ONE prompt cache per
    loaded model (`OLLAMA_NUM_PARALLEL=1` here), and a request reuses only the
    longest prefix it shares with the previous one. v17 warmed with `system=None`,
    so the first real question after every warm paid the whole ~415-token
    prompt again: measured 46 ms with the prefix cached, 510 ms without, on
    Metal — seconds on a CPU box. Sending the real prompt leaves exactly the
    prefix every question starts with in the cache.
    """
    t0 = time.perf_counter()
    try:
        await aic.generate(aic.MODEL_ROUTER, "ok", system=prompt(), temperature=0.0,
                           num_predict=1, timeout_s=WARM_TIMEOUT_S,
                           options=OPTIONS, keep_alive=KEEP_ALIVE)
        return {"ok": True, "ms": int((time.perf_counter() - t0) * 1000), "error": ""}
    except Exception as e:                              # noqa: BLE001
        return {"ok": False, "ms": int((time.perf_counter() - t0) * 1000),
                "error": route.classify(e)}


def ensure_warm() -> None:
    """Start a background warm unless one is already running in this worker."""
    import asyncio
    t = _WARMING["task"]
    if t is not None and not t.done():
        return
    try:
        _WARMING["task"] = asyncio.get_running_loop().create_task(warm())
    except RuntimeError:                                # no running loop
        _WARMING["task"] = None


# ── the decision cache (Phase 18) ───────────────────────────────────────────
#
# The router is DETERMINISTIC by construction (temperature 0, seed 0, top_k 1,
# a pinned prompt), so the same question on the same model and prompt gets the
# same answer — and asking the model again is pure latency. Warehouse questions
# repeat ("how much primer is at CNCEC?" is asked every morning), so a hit
# turns ~350 ms (Metal) / ~1 s (CPU) into microseconds.
#
# ⚠️ WHAT IS CACHED IS THE MODEL'S ANSWER, NEVER THE DECISION. The guard runs
# on every request and the combination rule (Q17-2) is applied afresh, so a
# pattern-set change takes effect at once. The key carries the prompt hash and
# the model name, so a prompt edit or a model swap never serves an old answer.
# Only VALID replies are cached — a timeout, an outage or a malformed reply is
# retried by the next question, exactly as before.
#
# Per worker and in memory (four workers = four caches): that only lowers the
# hit rate, it cannot make an answer wrong — nothing here is shared state.
CACHE_MAX = int(os.environ.get("GI_AI_ROUTER_CACHE", "512"))
CACHE_TTL_S = float(os.environ.get("GI_AI_ROUTER_CACHE_TTL_S", "3600"))


class _AnswerCache:
    def __init__(self, maxsize: int, ttl_s: float) -> None:
        self.maxsize, self.ttl_s = maxsize, ttl_s
        self.enabled = maxsize > 0
        self._d: "dict[tuple, tuple[float, str, bool]]" = {}
        self.hits = self.misses = 0

    @staticmethod
    def key(question: str) -> tuple:
        q = re.sub(r"\s+", " ", (question or "")[:MAX_QUESTION_CHARS]).strip().lower()
        return (aic.MODEL_ROUTER, prompt_hash(), q)

    def get(self, question: str) -> Optional[tuple[str, bool]]:
        if not self.enabled:
            return None
        k = self.key(question)
        hit = self._d.get(k)
        if hit is None or time.monotonic() - hit[0] > self.ttl_s:
            self._d.pop(k, None)
            self.misses += 1
            return None
        self._d[k] = self._d.pop(k)              # most recently used goes last
        self.hits += 1
        return hit[1], hit[2]

    def put(self, question: str, intent: str, is_safe: bool) -> None:
        if not self.enabled:
            return
        self._d[self.key(question)] = (time.monotonic(), intent, is_safe)
        while len(self._d) > self.maxsize:
            self._d.pop(next(iter(self._d)))     # least recently used

    def clear(self) -> None:
        self._d.clear()
        self.hits = self.misses = 0


CACHE = _AnswerCache(CACHE_MAX, CACHE_TTL_S)


# ── stage 1: the model ──────────────────────────────────────────────────────

async def classify_model(question: str) -> Decision:
    """One call to the router model. Never raises: a failure is a fallback."""
    d = Decision(model=aic.MODEL_ROUTER, source="fallback")
    cached = CACHE.get(question)
    if cached is not None:
        d.intent, d.is_safe = cached
        d.source, d.valid_json, d.error = "model", True, ""
        d.cached = True
        return d
    t0 = time.perf_counter()
    try:
        out = await route.call_structured(
            "router", (question or "")[:MAX_QUESTION_CHARS], system=prompt(),
            schema=SCHEMA, options=OPTIONS, keep_alive=KEEP_ALIVE)
    except Exception as e:                              # noqa: BLE001
        d.error = route.classify(e)
        d.ms = int((time.perf_counter() - t0) * 1000)
        if d.error in (route.TIMEOUT, route.UNAVAILABLE):
            ensure_warm()       # a cold or restarted engine: load it off-path
        return d
    d.ms = out.ms
    parsed = _validate(out.text)
    d.valid_json = parsed is not None
    if parsed is None:
        d.error = "malformed"
        return d
    d.intent, d.is_safe = parsed
    d.source = "model"
    CACHE.put(question, d.intent, d.is_safe)
    return d


async def decide(question: str, role: str, *, enabled: bool = True,
                 semantic: bool = False) -> Decision:
    """Stage 0, then stage 1 if needed, then the guard's combination rule.

    `semantic` (Phase 19d, setting `ai_semantic_guard`, OFF by default): ask
    ai/semantic.py whether the question sounds like the attack bank. A match
    is one more guard HIT (never a refusal on its own), and it sends the
    question past stage 0 to the model, as a warn does."""
    v = guard.scan_input(question or "")
    if v.refused:
        return Decision(source="rules", blocked=True, reason=v.reason,
                        guard=v.as_attrs())
    if not enabled:
        return Decision(source="fallback", error="disabled", guard=v.as_attrs())
    sem_attrs: dict = {}
    if semantic:
        from . import semantic as _sem
        sv = await _sem.assess(question)
        if sv is not None:
            sem_attrs = sv.as_attrs()
            if sv.fired:
                v = guard.with_semantic_signal(v)
    # A question that already WARNS (or sounds like an attack) goes to the
    # model even when the wording names a page: a warn plus the model's
    # `is_safe:false` is a refusal, and stage 0 deciding first would skip the
    # one check that could make it one.
    if v.decision == "allow" and guard.SEMANTIC_HIT not in v.hits:
        d = classify_rules(question, role)
        if d is not None:
            d.guard = {**v.as_attrs(), **sem_attrs}
            return d
    d = await classify_model(question)
    if d.source != "model":
        d.guard = {**v.as_attrs(), **sem_attrs}
        return d
    combined = guard.with_router_signal(v, is_safe=d.is_safe, intent=d.intent)
    d.guard = {**combined.as_attrs(), **sem_attrs}
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
