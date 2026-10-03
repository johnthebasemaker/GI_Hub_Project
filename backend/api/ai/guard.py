"""
backend/api/ai/guard.py — input/output boundaries around the Hub Assistant.

⚠️ READ THIS FIRST: THE SECURITY BOUNDARY IS NOT IN THIS FILE.

Rule 9 puts it in `manual_qa.allowed_sections()`, which filters chapters BEFORE
BM25 scores them, so a role's prompt cannot physically contain a chapter it may
not see. Nothing here is allowed to become a second, weaker version of that —
and the failure mode to be afraid of is not that this file is bypassed, it is
that people start trusting it INSTEAD of the fence, and then somebody
"simplifies" the fence because the guard is there.

So every function below is one of exactly two things:

  * a REFUSAL that could equally have been a slow, confused answer — cheaper and
    clearer to give directly (the input guard);
  * a check on text that has ALREADY passed the fence, for things the fence was
    never about: a spreadsheet formula, a phone number, a canary (the output
    guard).

Neither WIDENS anything. `topic_preflight` can only ever refuse.

────────────────────────────────────────────────────────────────────────────
WHY NOT `guardrails-ai`

Its useful validators are not in the package: `guardrails hub install
hub://guardrails/detect_jailbreak` downloads a transformer of a few hundred MB
at install time, which is a second model resident beside the one warm 7-8B the
CPX42 ruling allows. Its dependency tree (litellm, opentelemetry, langchain-core)
out-masses the 417-line module it would be guarding. And its core abstraction —
typed output with re-ask loops — is aimed at a problem this codebase solved by
hand years ago and cannot afford anyway: a re-ask on a 400-second vision read is
not a retry, it is another six minutes.

This module is the shape the codebase already trusts for exactly this job —
`ai/safety.py`: pessimistic pure functions, no I/O, no state, trivially
unit-testable, one call site each.

────────────────────────────────────────────────────────────────────────────
⚠️ FALSE REFUSALS ARE WORSE THAN THE ATTACKS THEY PREVENT

A store keeper refused at 06:00 for typing "ignore the damaged drum and issue
the rest" has been failed by the system, and has learned that the assistant is
unreliable — which costs more than any prompt injection would. Hence:

  * scores, never a single boolean. Weights accumulate; one pattern almost
    never refuses on its own;
  * every weighted pattern has a NEGATIVE TWIN in the eval suite — a legitimate
    warehouse sentence containing the same trigger, which must not be refused;
  * the thresholds live in `guard_patterns.yaml`, versioned and pinned, so
    tightening one is a reviewed diff rather than a nudge.

────────────────────────────────────────────────────────────────────────────
AND NO LLM JUDGE IN THE REQUEST PATH

Judging an answer means a second generation. On the same model that doubles the
user's wait; on a different one it cold-starts a 6 GB model on a box ruled to
hold one. Worse, a stochastic judge that can REFUSE means the same question is
answered on Monday and denied on Tuesday — which is ruling P10-7's argument
about flaky gates, applied to production instead of CI. The judge belongs in
Tier 2 of the eval suite, where its variance is measured rather than inflicted.
"""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..reports import _RISKY, _defuse

_PATTERNS_PATH = Path(__file__).with_name("guard_patterns.yaml")


# ── the pattern set ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Pattern:
    id: str
    weight: int
    rx: re.Pattern


@dataclass(frozen=True)
class _PiiRule:
    id: str
    rx: re.Pattern
    replacement: str


@dataclass(frozen=True)
class _Config:
    version: int
    warn: int
    refuse: int
    shape: dict
    patterns: tuple[_Pattern, ...]
    pii: tuple[_PiiRule, ...]
    disguise_weight: int = 0


DISGUISE_HIT = "encoded.disguised"

_FALLBACK_SHAPE = {"max_chars": 2000, "max_lines": 40,
                   "max_repeated_token": 30, "max_encoded_run": 120}


@functools.lru_cache(maxsize=1)
def config() -> _Config:
    """Load and compile the pattern set once.

    ⚠️ A MISSING OR BROKEN FILE DEGRADES TO SHAPE CHECKS ONLY, it does not fail
    the request. The guard is a convenience layer over a fence that is still
    doing its job; taking the assistant down because a YAML file is malformed
    would trade a small protection for a large outage. `version: 0` on the span
    is how an operator sees it happened.
    """
    try:
        import yaml
        raw = yaml.safe_load(_PATTERNS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:                                   # noqa: BLE001 — see above
        return _Config(0, 3, 6, dict(_FALLBACK_SHAPE), (), ())
    th = raw.get("thresholds") or {}
    pats = []
    for p in raw.get("patterns") or []:
        try:
            pats.append(_Pattern(str(p["id"]), int(p.get("weight", 1)),
                                 re.compile(p["regex"], re.MULTILINE)))
        except Exception:                               # noqa: BLE001
            continue                                    # a bad rule is skipped, not fatal
    pii = []
    for p in raw.get("pii") or []:
        try:
            pii.append(_PiiRule(str(p["id"]), re.compile(p["regex"]),
                                str(p.get("replacement", "[removed]"))))
        except Exception:                               # noqa: BLE001
            continue
    return _Config(int(raw.get("version", 0)), int(th.get("warn", 3)),
                   int(th.get("refuse", 6)),
                   {**_FALLBACK_SHAPE, **(raw.get("shape") or {})},
                   tuple(pats), tuple(pii),
                   int(th.get("disguise", 0)))


# ── input guard ─────────────────────────────────────────────────────────────

@dataclass
class InputVerdict:
    decision: str = "allow"          # allow | warn | refuse
    score: int = 0
    hits: list[str] = field(default_factory=list)
    reason: str = ""                 # a sentence for the USER, when refusing
    stage: str = ""                  # shape | patterns | topic
    patterns_version: int = 0

    @property
    def refused(self) -> bool:
        return self.decision == "refuse"

    def as_attrs(self) -> dict:
        return {"decision": self.decision, "score": self.score,
                "hits": self.hits, "stage": self.stage,
                "patterns_version": self.patterns_version}


_TOKEN_RX = re.compile(r"[A-Za-z0-9_]+")
_ENCODED_RX = re.compile(r"[A-Za-z0-9+/=_-]{40,}")


def _shape_problem(question: str, shape: dict) -> str:
    """'' when the shape is fine, else a sentence naming what to do instead."""
    q = question or ""
    if len(q) > shape["max_chars"]:
        return (f"That is {len(q):,} characters. Ask a question in a sentence "
                f"or two — I answer from the manual, so pasting a document in "
                f"does not help me find the part you need.")
    if q.count("\n") + 1 > shape["max_lines"]:
        return ("That looks like a pasted document rather than a question. "
                "Tell me what you want to know about it.")
    toks = _TOKEN_RX.findall(q.lower())
    if toks:
        counts: dict[str, int] = {}
        for t in toks:
            counts[t] = counts.get(t, 0) + 1
        worst, n = max(counts.items(), key=lambda kv: kv[1])
        if n > shape["max_repeated_token"] and len(worst) > 1:
            return "That question repeats itself — try asking it once."
    for m in _ENCODED_RX.finditer(q):
        if len(m.group(0)) > shape["max_encoded_run"]:
            return ("I can only answer questions written in words. That "
                    "contains a long block of encoded text.")
    return ""


# ── de-obfuscation (Phase 17, patterns v2) ──────────────────────────────────
#
# "I g n o r e  a l l  p r e v i o u s", "1gn0re y0ur instruct10ns" and a
# base64 blob that says "ignore all rules" are the same sentence as the plain
# one, and v1 scored all three 0. Rather than writing a second pattern set for
# each disguise, the patterns are run over the question AND over one
# normalised copy of it; a pattern hits if it matches either. The copy is never
# shown to anyone and never sent to a model.
#
# ⚠️ EACH STEP IS NARROW ON PURPOSE, because the normalised text is what the
# negative twins are scored on too. Leet is undone only inside tokens that MIX
# letters and digits ("1gn0re"), never in a number ("10 drums", "SAP 1001");
# letter-spacing is collapsed only for a run of 6+ single characters; base64 is
# decoded only when it yields printable text.
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s",
                       "7": "t", "@": "a", "$": "s"})
_MIXED_RX = re.compile(r"\b(?=[\w@$]*[A-Za-z])(?=[\w@$]*[0-9@$])[\w@$]+\b")
_SPACED_RX = re.compile(r"(?:\b\w\b[ \t]{1,2}){5,}\b\w\b")
_B64_RX = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")


def _deobfuscate(q: str) -> str:
    import base64
    import binascii
    # "I g n o r e  a l l" → "Ignore all": a double space separates words.
    out = _SPACED_RX.sub(lambda m: " ".join(
        w.replace(" ", "").replace("\t", "")
        for w in re.split(r"[ \t]{2,}", m.group(0))), q)
    out = _MIXED_RX.sub(lambda m: m.group(0).translate(_LEET), out)
    decoded = []
    for m in _B64_RX.finditer(q):
        try:
            text = base64.b64decode(m.group(0), validate=True).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            continue
        if text.isprintable() and any(c.isalpha() for c in text):
            decoded.append(text)
    return " ".join([out, *decoded])


def scan_input(question: str) -> InputVerdict:
    """Shape checks, then the scored jailbreak patterns. Pure; no I/O."""
    cfg = config()
    v = InputVerdict(patterns_version=cfg.version)
    problem = _shape_problem(question or "", cfg.shape)
    if problem:
        return InputVerdict(decision="refuse", score=cfg.refuse, stage="shape",
                            reason=problem, patterns_version=cfg.version)
    score = 0
    hits: list[str] = []
    plain = question or ""
    norm = _deobfuscate(plain)
    disguised = False
    for p in cfg.patterns:
        if p.rx.search(plain):
            hits.append(p.id)
            score += p.weight
        elif norm != plain and p.rx.search(norm):
            hits.append(p.id)
            score += p.weight
            disguised = True
    # ⚠️ Phase 18 (v3): A DISGUISE THAT HIDES A TRIGGER IS ITSELF EVIDENCE.
    # "1gn0re y0ur instruct10ns" scored exactly what the plain sentence scores,
    # as if writing it in leet were an accident. Nobody spaces out or leets a
    # question about drums; a pattern that matches ONLY the de-obfuscated copy
    # means somebody hid it. The weight is data (guard_patterns.yaml); it is
    # added once, however many patterns the disguise hid.
    if disguised and cfg.disguise_weight:
        hits.append(DISGUISE_HIT)
        score += cfg.disguise_weight
    v.score, v.hits = score, hits
    if score >= cfg.refuse:
        v.decision, v.stage = "refuse", "patterns"
        # ⚠️ THE REFUSAL DOES NOT EXPLAIN THE PATTERN THAT CAUGHT IT. Naming it
        # turns the guard into a tutorial on evading itself, and tells an
        # innocent user they have done something wrong when they have not.
        v.reason = ("I can only answer questions about your section of the "
                    "manual. Ask me what you need to know and I will look it "
                    "up.")
    elif score >= cfg.warn:
        v.decision, v.stage = "warn", "patterns"
    return v


# ── the System One router's verdict, as ONE MORE SIGNAL (Phase 17) ──────────
#
# ⚠️ RULING Q17-2 (2026-10-03): the tiny router model's `is_safe: false` is a
# scored input to this guard, NOT a standalone veto — because a stochastic
# judge that can refuse on its own answers the same question on Monday and
# denies it on Tuesday (§7f), and false refusal at 0 % is the number §7i says
# is most worth protecting. It refuses in exactly two situations:
#
#   * combined with ANY pattern hit — "one pattern warns, a combination
#     refuses", the rule every weight in guard_patterns.yaml already follows;
#   * on the SQL lane — the one lane where the stakes are live data and a
#     false refusal costs a re-phrasing, not a 06:00 store keeper's answer.
#
# Otherwise it is recorded (`router.unsafe` on the span) and the question
# proceeds to the fence, which makes an injection profitless anyway. Living
# here rather than in system_one.py keeps every refusal decision in one module.
ROUTER_UNSAFE_HIT = "router.unsafe"
ROUTER_SQL_INTENT = "SQL_QUERY"


def with_router_signal(v: InputVerdict, *, is_safe: Optional[bool],
                       intent: str) -> InputVerdict:
    """Fold the router's verdict into an input verdict. Pure; returns a copy.

    `is_safe=None` (router off, down, timed out, malformed) changes nothing:
    the request proceeds exactly as it did before Phase 17.
    """
    if v.refused or is_safe is not False:
        return v
    out = InputVerdict(decision=v.decision, score=v.score,
                       hits=[*v.hits, ROUTER_UNSAFE_HIT], reason=v.reason,
                       stage=v.stage, patterns_version=v.patterns_version)
    if v.hits or intent == ROUTER_SQL_INTENT:
        out.decision, out.stage = "refuse", "router"
        out.reason = ("I can only answer questions about your section of the "
                      "manual. Ask me what you need to know and I will look it "
                      "up.")
    return out


# ── the role-aware topic pre-flight ─────────────────────────────────────────
#
# ⚠️ THIS IS A UX AFFORDANCE, NOT A SECURITY CONTROL, AND THE DISTINCTION IS THE
# WHOLE DESIGN. `allowed_sections()` already guarantees a role's context cannot
# contain a forbidden chapter; that guarantee does not need help. What this adds
# is the case where retrieval found NOTHING and the question's own words point
# squarely at a chapter the role may not see — the one situation in which the
# fallback hands the model a broad, truncated context and asks it about a
# subject deliberately excluded from it. That is precisely when a model
# confabulates, and answering "that is not in your section" directly is both
# faster and more honest than letting it try.
#
# ⚠️ IT FIRES ONLY ON A POSITIVE MATCH AGAINST A FORBIDDEN CHAPTER, never on a
# bare retrieval miss. A question whose answer IS in an allowed chapter that
# BM25 simply failed to rank must still reach the model with the fallback
# context — refusing it would convert a retrieval weakness into a denial of a
# question the user is entitled to have answered.

_STOP = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are",
    "was", "were", "be", "been", "do", "does", "did", "how", "what", "when",
    "where", "who", "why", "which", "can", "i", "my", "me", "you", "your",
    "it", "this", "that", "with", "from", "at", "as", "by", "if", "not",
    "there", "here", "about", "into", "out", "up", "down", "get", "got",
    "see", "show", "tell", "page", "screen", "system", "hub", "gi",
})

# ⚠️ THE TRIGGER IS A WEAK ALLOWED MATCH, NOT AN EMPTY ONE, AND THE FIRST
# VERSION OF THIS FUNCTION WAS WRONG ABOUT THAT.
#
# It fired only when retrieval returned NOTHING. Measured against the live
# manual, that condition is very nearly empty: BM25 over ~450 chunks scores
# something above zero for almost any English sentence, so "how do I restore a
# database backup" asked by a store keeper retrieved happily (weak, irrelevant
# passages) and the pre-flight never ran. A guard that cannot fire is not a
# conservative guard, it is a dead one — and worse, it looks like coverage.
#
# The real signal is the RATIO. "Your question matches the Admin chapter
# strongly and matches everything you may read only weakly" is the condition
# that matters, and it is exactly the condition under which the fallback hands
# the model a broad context and asks it about a subject deliberately excluded
# from it — which is when a model invents an answer.
_TOPIC_MIN_FORBIDDEN = 6.0   # the forbidden match must be substantial at all
_TOPIC_RATIO = 2.5           # …and must beat the best allowed one by this much


def topic_preflight(role: str, question: str, *,
                    top_allowed_score: float = 0.0) -> InputVerdict:
    """Refuse — never allow — when a question is squarely about a chapter this
    role may not see, and only weakly about anything it may.

    `top_allowed_score` is the best BM25 score retrieval achieved inside the
    fence (`telemetry["top_score"]`). Passing it rather than recomputing keeps
    this decision and the prompt describing the SAME ranking.

    ⚠️ CAN ONLY REFUSE. There is no code path here that adds a chapter, widens
    an allowlist, or changes what retrieval returned. That is the property that
    keeps this an affordance rather than a second, weaker copy of rule 9's
    fence — and it is the property to check first if anyone edits this.
    """
    cfg = config()
    v = InputVerdict(patterns_version=cfg.version, stage="topic")
    if not (question or "").strip():
        return v
    try:
        from . import manual_index as mx
        from . import manual_qa as mq
        idx = mq._index()
        allowed = mq.allowed_sections(role)
    except Exception:                                   # noqa: BLE001
        return v                                        # never break the chat
    q = [t for t in mx._tokens(question) if t not in _STOP]
    if not q:
        return v
    if not ({c.chapter for c in idx.chunks} - allowed):
        return v                                        # admin: nothing to protect

    best_forbidden = 0.0
    best_chapter: Optional[int] = None
    for i, c in enumerate(idx.chunks):
        if c.chapter in allowed:
            continue
        s = idx.score(i, q)
        if s > best_forbidden:
            best_forbidden, best_chapter = s, c.chapter

    # ⚠️ BOTH CONDITIONS, AND THE SECOND IS WHAT KEEPS §2 ANSWERING. "How do I
    # add a user" scores in the Admin chapter AND in §2's access matrix, which
    # every role may read and which answers it correctly — "you cannot; an
    # admin does". Requiring the forbidden chapter to beat the best allowed one
    # by a wide margin is what stops this refusing questions §2 exists to
    # answer, and those are the questions people ask most.
    if (best_forbidden >= _TOPIC_MIN_FORBIDDEN
            and best_forbidden > max(top_allowed_score, 0.0) * _TOPIC_RATIO):
        v.decision = "refuse"
        v.score = int(best_forbidden)
        v.hits = [f"chapter:{best_chapter}"]
        v.reason = ""       # the caller uses the role's own refusal sentence
    return v


# ── output guard ────────────────────────────────────────────────────────────

@dataclass
class OutputVerdict:
    text: str = ""
    redactions: list[str] = field(default_factory=list)
    canaries: list[str] = field(default_factory=list)
    defused: bool = False

    @property
    def clean(self) -> bool:
        return not (self.redactions or self.canaries or self.defused)

    def as_attrs(self) -> dict:
        return {"redactions": self.redactions, "canaries": self.canaries,
                "defused": self.defused}


def defuse_formula(text: str) -> tuple[str, bool]:
    """Neutralise a leading spreadsheet formula character.

    ⚠️ THE CHARACTER SET IS `reports._RISKY`, IMPORTED RATHER THAN RETYPED.
    Rule 12 already documents this attack and already owns the six characters a
    spreadsheet evaluates; a second copy here would be two lists that agree
    until one of them is updated. The assistant's answers are pasted into
    spreadsheets by HODs, so the same exposure exists — an answer beginning
    `=HYPERLINK(...)` is one paste away from being live.

    Applied per LINE, because a formula only evaluates when it is the first
    thing in a cell, and a paste puts each line in one.
    """
    out, changed = [], False
    for line in (text or "").split("\n"):
        stripped = line.lstrip()
        if stripped[:1] in _RISKY and _defuse(stripped) != stripped:
            out.append(line[:len(line) - len(stripped)] + "'" + stripped)
            changed = True
        else:
            out.append(line)
    return "\n".join(out), changed


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Strip anything that looks like a secret or a personal identifier.

    ⚠️ A HIT IS AN ALARM, NOT A TIDY-UP. The manual corpus contains none of
    these shapes, so one in an answer means the model invented it or something
    reached the context that should not have. The caller records the ids on the
    trace span for exactly that reason.
    """
    out, hit = text or "", []
    for rule in config().pii:
        new = rule.rx.sub(rule.replacement, out)
        if new != out:
            hit.append(rule.id)
            out = new
    return out, hit


@functools.lru_cache(maxsize=16)
def runtime_canaries(role: str) -> frozenset[str]:
    """Canary phrases that must never appear in an answer to `role`.

    Reuses the eval suite's own table: `tests/ai_eval/cases/*.yaml` already
    maintains substrings that are unique to ONE chapter, and `audit_canaries()`
    verifies that uniqueness on every run — so these are the only strings in the
    system whose presence in an answer actually proves a leak. Scanning for them
    at runtime closes the gap between "we test for leaks nightly" and "we would
    notice one in production".

    ⚠️ DEFENCE IN DEPTH, NOT THE DEFENCE. Returns an empty set when the case
    files are absent (a deployment that ships only `backend/`), and that is
    fine: the control is rule 9's fence, which makes a leak impossible rather
    than detectable. This is the smoke alarm, not the fire door — and a smoke
    alarm that took the building down when its battery was flat would be a
    worse design than no alarm.

    Only canaries belonging to chapters THIS role may not see are returned; a
    phrase from a chapter it is entitled to read is not a leak and flagging it
    would train people to ignore the flag.
    """
    try:
        import yaml
        from . import manual_qa as mq
        allowed = mq.allowed_sections(role)
        root = Path(__file__).resolve().parents[3]
        cases_dir = root / "tests" / "ai_eval" / "cases"
        if not cases_dir.is_dir():
            return frozenset()
        manual = mq._manual_text()
        if not manual:
            return frozenset()
        from . import manual_index as mx
        chapters = {n: body for n, _t, body in mx.iter_chapters(manual)}
        out: set[str] = set()
        for f in sorted(cases_dir.glob("*.yaml")):
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                continue                                # policy.yaml is a mapping
            for case in data:
                for canary in (case or {}).get("forbidden_substrings") or []:
                    hits = [n for n, body in chapters.items()
                            if str(canary).lower() in body.lower()]
                    # Unique to exactly one chapter, and that chapter is one
                    # this role may not see. Anything else proves nothing.
                    if len(hits) == 1 and hits[0] not in allowed:
                        out.add(str(canary))
        return frozenset(out)
    except Exception:                                   # noqa: BLE001 — see above
        return frozenset()


def find_canaries(text: str, canaries: frozenset[str]) -> list[str]:
    """Which forbidden-chapter canaries appear in this answer.

    The eval suite already maintains canary strings unique to one chapter and
    verifies that uniqueness at load time. Reusing that table at RUNTIME closes
    the gap between "we test for leaks nightly" and "we would notice one in
    production" — a substring scan over a few hundred phrases, on text we were
    about to send anyway.
    """
    low = (text or "").lower()
    return sorted(c for c in canaries if c and c.lower() in low)


def scan_output(text: str, *, canaries: frozenset[str] = frozenset()
                ) -> OutputVerdict:
    """The whole output guard, for a complete answer. See `StreamGuard` for SSE."""
    out, defused = defuse_formula(text)
    out, red = redact_pii(out)
    return OutputVerdict(text=out, redactions=red, defused=defused,
                         canaries=find_canaries(out, canaries))


class StreamGuard:
    """The output guard over a token stream, without waiting for the end.

    ⚠️ `/ai/assistant` IS SSE, TOKEN BY TOKEN, and a guard that needs the whole
    answer cannot run before the first token has already left. So this holds
    back a sliding tail — long enough that no canary or PII shape can be split
    across the boundary and escape unseen — scans what is now safely complete,
    and emits it. The cost is one buffer of latency, not one answer's.

    Feed with `push()`, finish with `close()`. `verdict` accumulates what was
    found so the caller can record one span for the whole stream.
    """

    # ⚠️ THE HOLD-BACK IS SIZED, NOT GUESSED, AND THE FIRST VERSION WAS A FLAT
    # 240 THAT BROKE STREAMING.
    #
    # A typical assistant answer is two to four sentences — 200 to 400
    # characters. Holding 240 of them back meant a short answer arrived in ONE
    # burst at the end, which is the opposite of what the SSE endpoint exists
    # for. Caught immediately: suite A asserts that "Go to " and "Entry Log."
    # arrive as separate events, and after the guard they arrived as one.
    #
    # The window only has to be as long as the longest thing that must not be
    # split. Measured: the longest live canary is 21 characters ("Admin Portal
    # Overview"), and the longest bounded PII shape is a bcrypt hash at 60. So
    # it is `max(longest canary, PII_SPAN) + 1`, which lands near 64 rather
    # than 240 — a delay of about one short clause instead of a whole answer.
    #
    # ⚠️ A JWT CAN EXCEED THIS AND MAY THEREFORE BE SPLIT. That is an accepted
    # trade, stated rather than hidden: a token in a manual answer is already
    # an alarm, whichever half is caught raises it, and sizing the buffer for
    # the longest conceivable secret would cost every user their streaming to
    # protect against something the fence makes impossible anyway.
    PII_SPAN = 64

    def __init__(self, canaries: frozenset[str] = frozenset()) -> None:
        self.canaries = canaries
        self.verdict = OutputVerdict()
        self._buf = ""
        self._first = True
        longest = max((len(c) for c in canaries), default=0)
        self.window = max(longest, self.PII_SPAN) + 1

    def _scan(self, chunk: str) -> str:
        if not chunk:
            return chunk
        # Formula defusal only matters at the START of a line, and the only
        # line whose start we can be sure of mid-stream is the first one plus
        # any that follow a newline we have already emitted.
        if self._first:
            chunk, changed = defuse_formula(chunk)
            self.verdict.defused = self.verdict.defused or changed
            self._first = False
        chunk, red = redact_pii(chunk)
        for r in red:
            if r not in self.verdict.redactions:
                self.verdict.redactions.append(r)
        for c in find_canaries(chunk, self.canaries):
            if c not in self.verdict.canaries:
                self.verdict.canaries.append(c)
        return chunk

    def push(self, chunk: str) -> str:
        """Absorb a model chunk; return what is safe to send now (may be '')."""
        self._buf += chunk or ""
        if len(self._buf) <= self.window:
            return ""
        emit, self._buf = self._buf[:-self.window], self._buf[-self.window:]
        return self._scan(emit)

    def close(self) -> str:
        """Flush the held tail through the guard."""
        emit, self._buf = self._buf, ""
        return self._scan(emit)
