"""
backend/api/ai/manual_qa.py — role-gated Q&A over USER_MANUAL.md.

Faithful async port of legacy ai/manual_qa.py. The security model carries
over unchanged: the role filter happens at the RETRIEVAL layer, not the
prompt — a Store Keeper's context physically never contains the Admin
chapter, so the model cannot leak it. Updated for the v3.0 manual, which
grew two sections the legacy allowlist predates: §18 SME Estimator and
§19 Man-Hours (both hod/admin surfaces on the new stack).

Ollama calls go through the module object (`aic.stream`) so tests can
monkeypatch the client without a live server.

2026-08-04 — two fixes, both delegated to `manual_index`:
  * chapter parsing is now FENCE-AWARE. `# 1. Pull the new code` inside a
    ```bash block in the Operations chapter parsed as chapter 1 and, on a
    last-write-wins dict, replaced "Introduction & System Overview" with two
    lines of launchctl. Chapters 1-4 were wrong for EVERY role.
  * the prompt is now RETRIEVED, not stuffed. Admin used to receive the whole
    ~180 KB manual on every question, and every other role the first 800
    characters of each allowed chapter — the wrong 800 whenever the answer sat
    further down. The question now selects a handful of sub-sections.
The role filter still runs at the retrieval layer, BEFORE scoring, so the
security property is unchanged: a role's context cannot physically contain a
chapter it may not see.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import AsyncIterator

from . import answer_cache as _cache
from . import client as aic
from . import guard as _guard
from . import manual_index as mx
from . import route as _route
from . import trace as _trace

# Which top-level USER_MANUAL.md sections each role may see. Lower roles
# cannot see higher roles' sections. §18 (SME) + §19 (Man-Hours) are
# hod/admin-locked features, mirroring the portal locks.
#
# ⚠️ A ROLE MISSING FROM THIS MAP IS NOT LOCKED OUT — IT IS GIVEN THE STORE
# KEEPER'S CHAPTERS. `allowed_sections` falls back to "store_keeper" so an
# unknown role can never be handed the whole manual, which is the right failure
# direction, but it is still the WRONG answer: the QSEP release added the `qc`
# role and forgot this map, so for a while a Quality inspector asking about
# inspections was answered out of the Store Keeper chapter and told anything
# else was "not in your section". Add the role here in the same commit that
# adds it to `auth.ROLE_META`.
_ROLE_ALLOWED: dict[str, set[int]] = {
    "store_keeper":   {1, 2, 3, 4, 10, 11, 12, 13, 21, 22, 24, 26},
    "supervisor":     {1, 2, 3, 4, 5, 11, 12, 13, 21, 22, 24, 26},
    "hod":            {1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 16, 18, 19, 21, 22, 24, 26},
    # Strict isolation: Logistics never sees Warehouse internals (and vice
    # versa); neither sees site-level chapters 4–6.
    "logistics":      {1, 2, 3, 9, 11, 12, 13, 14, 16, 21, 22, 24, 26},
    "warehouse_user": {1, 2, 3, 9, 11, 12, 13, 15, 16, 21, 22, 24, 26},
    # QC (2026-08-09) inspects and decides, and does nothing else. Chapter 22
    # is its own; 4 and 15 are there because an inspector has to understand
    # the issue and the goods-in they sit between, not to let them do either.
    "qc":             {1, 2, 3, 4, 11, 12, 15, 21, 22, 24, 26},
    # The Head of Qualities (2026-08-22) oversees Surface Shield across every
    # site. Its chapters are orientation, the procurement chain the material
    # travels down, the quality chapter and the data model — plus §23, its own.
    # NOT the site role chapters: it does not issue, receive or approve, and
    # operational how-tos it cannot perform would only mislead it.
    "qc_hod":         {1, 2, 3, 9, 10, 11, 12, 16, 21, 22, 23, 24, 26},
    # The view-only Auditor (2026-08-03) reads across every site but can
    # open only Dashboard / Stock / Records / Reports / Lining Coverage.
    # Its chapters mirror exactly that: orientation, reporting, the data
    # model and the glossary. No role operational how-tos it could not
    # perform anyway, and not the hosting chapter.
    # §26 (Practice Mode, rule 17) goes to EVERY role: every role has a
    # practice account and is told how to switch, and a trainee asking "why
    # can't I read this photo?" must be answered out of the chapter that says.
    # §25 (AI Traces) is admin+auditor ONLY, unlike §21/§24 which went to
    # every role. Those are "what changed" chapters about features everyone
    # uses; §25 describes ONE page that only these two roles can open, and a
    # store keeper answered out of it would be told how to read a screen the
    # nav refuses them.
    "auditor":        {1, 2, 3, 8, 9, 10, 11, 12, 16, 20, 21, 22, 24, 25, 26},
    # ⚠️ THE UPPER BOUND IS EXCLUSIVE AND HAS TO MOVE WITH THE MANUAL. Adding
    # a chapter without touching this line gives the ADMIN less than everybody
    # else — the one role that is supposed to see all of it. `tests/ai_eval`
    # pins the resulting set, so the omission fails a gate rather than being
    # discovered by an administrator who cannot find a chapter.
    "admin":          set(range(1, 27)),
}

_SECTION_TITLES = {
    1: "Introduction & System Overview",
    2: "Roles, Permissions & Page Access",
    3: "Login, Sidebar & Common Elements",
    4: "Store Keeper Manual",
    5: "Supervisor Manual",
    6: "HOD Manual",
    7: "Admin Manual",
    8: "Reports Module",
    9: "Automated Notifications (WhatsApp + Email)",
    10: "Data Model & Concept Reference",
    11: "Status Codes, Reason Codes & Glossary",
    12: "FAQ — Master Index",
    13: "2026-06 Feature Update",
    14: "Logistics Portal Manual",
    15: "Warehouse Portal Manual",
    16: "Cross-Role Procurement Walk-through",
    17: "Operations & Hosting",
    18: "Material Estimator (SME) Manual",
    19: "Man-Hours & Manpower Tracking Manual",
    20: "Auditor (View-Only) Manual",
    21: "2026-08 Feature Update",
    22: "Quality, Safety, Employees & Procurement (QSEP)",
    23: "Quality Oversight (Head of Qualities) Manual",
    24: "Phase 10 — Security, Training and the Board Brief",
    25: "Phase 11 — AI Traces",
    26: "Practice Mode",
}


def _manual_path() -> Path:
    return Path(os.environ.get("GI_USER_MANUAL_PATH", "USER_MANUAL.md"))


@lru_cache(maxsize=1)
def _manual_text() -> str:
    path = _manual_path()
    return path.read_text(encoding="utf-8") if path.exists() else ""


@lru_cache(maxsize=1)
def _load_sections() -> dict[int, str]:
    """{section_number: full_section_text}, via the fence-aware parser.

    This used to split on a bare lookahead regex, which cannot tell a heading
    from a shell comment inside a fenced block — see manual_index.iter_chapters
    for the failure it caused.
    """
    md = _manual_text()
    if not md:
        return {}
    return {num: f"# {num}. {title}\n{body}".strip()
            for num, title, body in mx.iter_chapters(md)}


@lru_cache(maxsize=1)
def _index() -> mx.Index:
    """Built once per process — ~450 chunks over the live manual.

    Measured on the live 229 KB manual: 2 ms to chunk, 15 ms to build the BM25
    tables, 17 ms in total, and 0.3 ms per subsequent search. It is pre-built
    in the FastAPI lifespan (`warm()`), which is worth doing for two reasons,
    neither of which is "the assistant is slow": it keeps 17 ms of synchronous
    work off the event loop on whoever asks the first question, and it surfaces
    a missing or unparseable manual AT BOOT rather than inside somebody's chat.
    The perceived latency is token generation in Ollama, not this.
    """
    return mx.Index(mx.build_chunks(_manual_text()))


def warm() -> dict:
    """Build the index and the per-role fallback contexts up front.

    Returns a small summary so startup can print something falsifiable rather
    than "AI ready". Never raises: a box without the manual must still boot.
    """
    import time as _t
    t0 = _t.perf_counter()
    try:
        idx = _index()
        for role in _ROLE_ALLOWED:
            _context_for_role(role)
        return {"ok": True, "chunks": len(idx.chunks),
                "chapters": len(_load_sections()),
                "ms": round((_t.perf_counter() - t0) * 1000, 1)}
    except Exception as e:  # noqa: BLE001 — startup must not depend on this
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "ms": round((_t.perf_counter() - t0) * 1000, 1)}


# The FALLBACK path's per-chapter budget — used only when retrieval scores
# nothing at all, or when there is no question yet.
#
# ⚠️ 800 CHARACTERS HID THE ACCESS MATRIX FROM EVERY NON-ADMIN ROLE. §2's
# "Page access matrix" begins about 1,900 characters in, behind the role
# hierarchy table, so the head-truncated §2 stopped mid-hierarchy and the one
# table that answers "can my role open X" was never in any non-admin prompt.
# That is the mechanism behind the reported "HODs cannot access the Manpower
# portal" answer: §2.1 says the page is locked to its own role, and without the
# matrix to name that role the model inferred exclusion.
_PER_SECTION_CHAR_CAP = 3000

# Chapters that are never head-truncated for anyone. §2 is the chapter that
# says what a role may do; truncating it is how the assistant ends up guessing
# about access. It is 6.5 KB — the whole of it costs less than being wrong.
_NEVER_TRUNCATE = frozenset({2})

_SUBHEAD_LINE = re.compile(r"^#{2,4}\s+\S")


def _head_by_section(body: str, cap: int) -> str:
    """The first whole `##` sub-sections of `body` that fit inside `cap`.

    ⚠️ TRUNCATION LANDS ON A HEADING, NEVER MID-TABLE. Slicing at a character
    count cuts markdown tables in half, and half a table reads as a complete
    one — the model answers confidently from the rows that survived. Snapping
    to the next sub-heading costs a few hundred characters and removes a class
    of confident wrong answer.

    At least one sub-section is always kept, even if it alone exceeds the cap:
    an empty chapter is worse than an over-long one.
    """
    if len(body) <= cap:
        return body
    kept: list[str] = []
    size = 0
    current: list[str] = []
    for line in body.splitlines():
        if _SUBHEAD_LINE.match(line) and current:
            block = "\n".join(current)
            if kept and size + len(block) > cap:
                break
            kept.append(block)
            size += len(block) + 1
            current = [line]
        else:
            current.append(line)
    if current and (not kept or size + len("\n".join(current)) <= cap):
        kept.append("\n".join(current))
    out = "\n".join(kept).rstrip()
    return (out or body[:cap]) + \
        "\n[... later sub-sections omitted — ask for specifics ...]"


@lru_cache(maxsize=16)
def _context_for_role(role: str) -> str:
    """Concatenation of allowed sections, each labeled — the FALLBACK context,
    used when the question retrieves nothing.

    Admin still gets FULL sections. Everyone else gets whole sub-sections up to
    `_PER_SECTION_CHAR_CAP`, except the chapters in `_NEVER_TRUNCATE`, which
    are always complete."""
    allowed = _ROLE_ALLOWED.get(role, _ROLE_ALLOWED["store_keeper"])
    sections = _load_sections()
    if not sections:
        return ""
    is_admin = role == "admin"
    chunks = []
    for num in sorted(allowed):
        body = sections.get(num)
        if not body:
            continue
        if not is_admin and num not in _NEVER_TRUNCATE:
            body = _head_by_section(body, _PER_SECTION_CHAR_CAP)
        chunks.append(f"=== Section {num}: {_SECTION_TITLES.get(num, '')} ===\n{body}")
    return "\n\n".join(chunks)


# Greeting fast-path — never call the LLM for trivial pleasantries (saves the
# full prompt-eval for every "hi" / "thanks").
_GREETING_TOKENS = {
    "hi", "hii", "hello", "hey", "heya", "yo", "hola",
    "thanks", "thank you", "ty", "thx",
    "ok", "okay", "cool", "great", "nice",
    "bye", "goodbye", "cya", "see you",
    "good morning", "good afternoon", "good evening", "morning", "evening",
}


def greeting_reply(question: str) -> str | None:
    q = re.sub(r"[!?.,…]+$", "", (question or "").strip().lower())
    if not q or len(q) > 24:
        return None
    if q in _GREETING_TOKENS:
        if q.startswith("thank") or q in {"ty", "thx"}:
            return "You're welcome — ask me anything from your section of the manual."
        if q in {"bye", "goodbye", "cya", "see you"}:
            return "Goodbye! I'll be here when you need me."
        if q in {"ok", "okay", "cool", "great", "nice"}:
            return "👍 Anything else from the manual you'd like me to look up?"
        return "Hi! I'm the Hub Assistant. Ask me anything about your role's section of the manual."
    return None


_ROLE_LABEL = {
    "store_keeper": "Store Keeper",
    "supervisor": "Supervisor",
    "hod": "Head of Department",
    "logistics": "Logistics Coordinator",
    "warehouse_user": "Warehouse Operator",
    "qc": "Quality Control Inspector",
    "qc_hod": "Head of Qualities",
    "admin": "Administrator",
    "auditor": "Auditor (view-only)",
}

# Role-aware refusal phrasing (never tell the Admin to "ask your Admin").
_ROLE_REFUSAL = {
    "store_keeper": "That's not in your section of the manual — please escalate to your HOD.",
    "supervisor": "That's not in your section of the manual — please escalate to your HOD.",
    "hod": "That's in the Admin chapter — please ask your Admin.",
    "logistics": "That's outside the Logistics Portal — please ask your Admin.",
    "warehouse_user": "That's outside the Warehouse Portal — please ask your Admin.",
    "qc": "That's outside the Quality section — please ask your HOD.",
    "qc_hod": "That's outside Quality oversight — please ask your Admin.",
    "admin": "I can't find that in the manual. Check the source markdown in USER_MANUAL.md.",
    "auditor": "That's outside the read-only Auditor view — please ask your Admin.",
}

_SYSTEM_PROMPT_TMPL = """\
You are the Hub Assistant, a documentation helper for the General \
Industries Hub warehouse system. You are talking to {username}, a {role_label}.

RULES:
- Answer ONLY using the manual sections provided below as CONTEXT.
- If the answer is not in the CONTEXT, reply with exactly this sentence \
and nothing else: "{refusal}"
- If you are asked about a topic, feature, screen or dashboard that is NOT \
explicitly described in the CONTEXT, you MUST reply with exactly that same \
sentence and nothing else. DO NOT confabulate, guess, infer or describe it \
from general knowledge. If the CONTEXT does not name the thing being asked \
about, you do not know about it.
- Naming a feature in the question does NOT make it part of the CONTEXT. If \
the user asks "what is on the X screen?" and the CONTEXT never mentions X, \
refuse — do not repeat X back and describe what such a screen might contain.
- ANSWER THE QUESTION. Never reply by pointing at a section number: \
"see 2.1", "refer to section 19" and "check the access matrix" are not \
answers. The CONTEXT below is what the reader would find there, so give \
them the content. A section number may appear only AFTER a complete answer, \
as a citation.
- Be direct and specific. If the question is a yes/no ("can an HOD open the \
Man-Hours page?"), start with Yes or No and then give the reason. If a table \
in the CONTEXT answers it, read the row out in words — do not tell the user \
to look at the table.
- Never say "the manual does not specify" when the CONTEXT contains a table, \
list or matrix that covers it. Read it.
- Be concise. 2-4 short sentences for most questions. Bullet lists are \
fine for steps.
- Refer to UI elements using exact names from the manual (e.g. "Entry \
Log → Consumption Log").
- Do NOT reveal information about roles other than the user's own. \
You can mention that "Admin can do X" only if §2 lists it as a permission, \
never with operational steps from a higher-role section. (Admins themselves \
have access to all sections — answer their questions fully.)
- Output plain text. No markdown headings. No code fences.

CONTEXT (manual sections {username} is allowed to see):
{context}
"""


def allowed_sections(role: str) -> set[int]:
    """The chapters this role may see. Unknown roles fall back to the LOWEST
    allowlist, never the highest — a typo in `users.role` must lose access,
    not gain it."""
    return _ROLE_ALLOWED.get(role, _ROLE_ALLOWED["store_keeper"])


def retrieve_context(role: str, question: str) -> str:
    """The passages worth showing for THIS question, from the chapters this
    role may see. Returns '' when nothing scores — the caller then falls back
    to the role's whole (truncated) context rather than answering blind."""
    return retrieve_context_scored(role, question)[0]


def retrieve_context_scored(role: str, question: str) -> tuple[str, dict]:
    """`(context, telemetry)` — retrieval, plus a record of how it went.

    ⚠️ THE TELEMETRY IS THE POINT OF SLICE 11c. Until now the BM25 scores were
    computed on every question and discarded, so "the assistant gave a bad
    answer" could not be split into "it retrieved the wrong passage" and "it
    ignored the right one". Those have completely different fixes, and one of
    them (the 800-character truncation that hid §2's access matrix from every
    non-admin role) went undiagnosed for a phase.

    Returns telemetry even on the empty and error paths, because "nothing
    scored" and "retrieval threw" are themselves the two most interesting
    outcomes and were previously indistinguishable from each other.
    """
    allowed = allowed_sections(role)
    if not question:
        return "", {"skipped": "no question", "allowed_chapters": sorted(allowed)}
    try:
        hits, tele = _index().search_scored(question, allowed=allowed)
    except Exception as e:  # noqa: BLE001 — retrieval must never break the chat
        return "", {"error": type(e).__name__,
                    "allowed_chapters": sorted(allowed)}
    return mx.render_context(hits), tele


def build_system_prompt(role: str, username: str = "",
                        question: str = "") -> str:
    """System prompt for one turn. See `build_system_prompt_scored`."""
    return build_system_prompt_scored(role, username, question)[0]


def build_system_prompt_scored(role: str, username: str = "",
                               question: str = "") -> tuple[str, dict]:
    """`(prompt, retrieval telemetry)`.

    With a question, CONTEXT is the retrieved passages — a few KB instead of
    the whole allowed manual, which is both much faster to evaluate and much
    more likely to contain the answer than a fixed head-truncation. Without
    one (or if nothing scores) it falls back to the role's full context, so
    the assistant degrades to its previous behaviour rather than to nothing.

    ⚠️ THE FALLBACK IS RECORDED. It is the quiet path: the answer still arrives,
    from a head-truncated dump of every allowed chapter rather than from the
    passage that answers the question — which is precisely the condition under
    which the model confabulates. Without `fallback: true` in the trace, a
    systematic retrieval failure looks like a model that has got worse.
    """
    context, tele = retrieve_context_scored(role, question)
    fallback = not context
    if fallback:
        context = _context_for_role(role)
    tele["fallback"] = fallback
    tele["context_chars"] = len(context)
    prompt = _SYSTEM_PROMPT_TMPL.format(
        username=(username or "").strip() or "the user",
        role_label=_ROLE_LABEL.get(role, role.title() if role else "user"),
        refusal=_ROLE_REFUSAL.get(role, _ROLE_REFUSAL["store_keeper"]),
        context=context or "(manual not found on disk)",
    )
    tele["prompt_chars"] = len(prompt)
    return prompt, tele


async def health() -> tuple[bool, str]:
    """(ok, msg) — server reachable, chat model pulled, manual on disk."""
    if not await aic.health():
        return False, (f"Local AI server unreachable at {aic.OLLAMA_HOST}. "
                       "Ask your admin to start the Ollama service.")
    installed = await aic.list_models()
    if installed and aic.MODEL_CHAT not in installed:
        return False, (f"Chat model {aic.MODEL_CHAT} is not pulled on the AI host "
                       f"(ollama pull {aic.MODEL_CHAT}).")
    if not _manual_path().exists():
        return False, f"USER_MANUAL.md not found at {_manual_path()}."
    return True, "ready"


async def answer_manual_question(question: str, role: str,
                                 username: str = "",
                                 trace_id: str = "") -> AsyncIterator[str]:
    """Stream the answer token-by-token. On failure, yield a single friendly
    string rather than raising — the SSE endpoint never breaks mid-chat.

    `trace_id` groups this turn's spans (slice 11c). Optional and defaulted so
    every existing caller — and every test that monkeypatches this — keeps
    working untouched; tracing is something the request path may do, never
    something it depends on.
    """
    ok, msg = await health()
    if not ok:
        yield msg
        return
    question = (question or "").strip()
    if not question:
        yield "Type a question and I'll answer from your section of the manual."
        return
    # ── input guard (slice 11d): shape, then the scored jailbreak patterns ──
    # ⚠️ BEFORE THE GREETING FAST PATH WOULD BE WRONG — "hi" must never be
    # scored — and after retrieval would be too late, because the point of a
    # refusal here is that the model is never called at all.
    iv = _guard.scan_input(question)
    if trace_id:
        _trace.emit("ai.guard.input", trace_id=trace_id, lane="assistant",
                    role=role, username=username, ok=not iv.refused,
                    outcome=iv.decision, attrs=iv.as_attrs())
    if iv.refused:
        yield iv.reason or _ROLE_REFUSAL.get(role, _ROLE_REFUSAL["store_keeper"])
        return

    canned = greeting_reply(question)
    if canned is not None:
        # The greeting fast path never reaches the model, and saying so is
        # worth a span: an "assistant is slow" report is answered instantly by
        # a trace showing which turns were and were not generations.
        if trace_id:
            _trace.emit("ai.generate", trace_id=trace_id, lane="assistant",
                        role=role, username=username, duration_ms=0,
                        outcome="greeting", attrs={"greeting": True})
        yield canned
        return

    if trace_id:
        with _trace.Span("ai.retrieve", trace_id=trace_id, lane="assistant",
                         role=role, username=username) as sp:
            system, tele = build_system_prompt_scored(role, username, question)
            sp.attrs(**tele).outcome("fallback" if tele.get("fallback") else "ok")
    else:
        system, tele = build_system_prompt_scored(role, username, question)

    # ── the role-aware topic pre-flight ────────────────────────────────────
    # ⚠️ AFTER RETRIEVAL, BECAUSE IT NEEDS TO KNOW HOW WELL THE QUESTION DID
    # INSIDE THE FENCE. It refuses only when the question matches a chapter this
    # role may NOT see far better than anything it may — the exact condition
    # under which the model is handed a broad, truncated context and asked about
    # a subject deliberately excluded from it, which is when it invents. It can
    # only ever refuse; nothing here can widen what retrieval returned.
    tv = _guard.topic_preflight(role, question,
                                top_allowed_score=float(tele.get("top_score") or 0.0))
    if trace_id:
        _trace.emit("ai.guard.input", trace_id=trace_id, lane="assistant",
                    role=role, username=username, ok=not tv.refused,
                    outcome=f"topic:{tv.decision}", attrs=tv.as_attrs())
    if tv.refused:
        yield _ROLE_REFUSAL.get(role, _ROLE_REFUSAL["store_keeper"])
        return

    # ── the answer cache (slice 11e) ───────────────────────────────────────
    # ⚠️ KEYED ON THE ROLE, and the reason is not performance. Two people can
    # type a byte-identical question and be entitled to different answers,
    # because rule 9 showed them different chapters. A cache keyed on the
    # question alone would walk around that boundary from the side. See
    # answer_cache.key_for — the one place the key is built.
    #
    # ⚠️ AND IT IS CONSULTED AFTER BOTH GUARDS. A cached answer must not be a
    # way to skip a refusal, and a question that would be refused today must
    # not be answered because it was allowed last week.
    cached = await _cache.lookup(question, role)
    if trace_id:
        _trace.emit("ai.cache", trace_id=trace_id, lane="assistant", role=role,
                    username=username, outcome="hit" if cached else "miss",
                    attrs={"hit": bool(cached),
                           "age_s": (cached or {}).get("age_s"),
                           "hit_count": (cached or {}).get("hit_count")})
    if cached:
        yield cached["answer"]
        return

    prompt = f"User question: {question}\n\nAnswer:"
    gen = _trace.Span("ai.generate", trace_id=trace_id, lane="assistant",
                      role=role, username=username) if trace_id else None
    if gen:
        gen.__enter__()
        gen.attrs(model=aic.MODEL_CHAT, num_predict=512, temperature=0.2,
                  system_chars=len(system))
    chars = 0
    # ⚠️ THE OUTPUT GUARD RUNS ON A SLIDING BUFFER, because this is SSE and a
    # guard that needs the whole answer cannot run before the first token has
    # already left. It holds back a tail long enough that no canary or PII shape
    # can be split across a chunk boundary and escape unseen. The cost is one
    # buffer of latency, not one answer's.
    sg = _guard.StreamGuard(canaries=_guard.runtime_canaries(role))
    # ⚠️ WHAT IS CACHED IS WHAT THE USER SAW — the GUARDED text, assembled from
    # the same chunks that were yielded. Caching the raw model output would
    # replay a redaction-worthy answer to the next person unredacted, which is
    # the guard being undone by the cache one turn later.
    served: list[str] = []
    ok_complete = False
    pol = _route.policy("assistant")
    try:
        async for chunk in aic.stream(aic.MODEL_CHAT, prompt, system=system,
                                      temperature=0.2,
                                      num_predict=pol.num_predict):
            chars += len(chunk)
            safe = sg.push(chunk)
            if safe:
                served.append(safe)
                yield safe
        tail = sg.close()
        if tail:
            served.append(tail)
            yield tail
        ok_complete = True
    except RuntimeError as e:
        if gen:
            gen.outcome("error").attrs(error_type=type(e).__name__)
        # Whatever is still held back is guarded and released before the error
        # sentence — dropping it would lose the answer the model DID produce
        # before it failed.
        tail = sg.close()
        if tail:
            served.append(tail)
            yield tail
        yield f"\n\n[Hub Assistant error: {e}]"
    finally:
        if gen:
            # ⚠️ THE LENGTH, NEVER THE ANSWER. How much was generated is a
            # latency and truncation diagnostic; what was generated is content
            # this table has no business holding (see trace.py's header).
            gen.attrs(answer_chars=chars)
            gen.__exit__(None, None, None)
        if trace_id:
            ov = sg.verdict
            _trace.emit("ai.guard.output", trace_id=trace_id, lane="assistant",
                        role=role, username=username,
                        # ⚠️ A CANARY HIT IS NOT OK. It means a phrase unique to
                        # a chapter this role may not see reached an answer,
                        # which should be impossible — the fence makes it so.
                        # If this is ever not-ok, the fence has a hole and the
                        # trace is the only record that it did.
                        ok=not ov.canaries, outcome=(
                            "leak" if ov.canaries
                            else "redacted" if ov.redactions or ov.defused
                            else "clean"),
                        attrs=ov.as_attrs())
        # ⚠️ ONLY A COMPLETE, CLEAN GENERATION IS REMEMBERED. A half-streamed
        # answer that hit an error would be served whole to the next person; a
        # redacted or canary-carrying one would make an incident permanent.
        if ok_complete and pol.cacheable and sg.verdict.clean and served:
            await _cache.store(question, role, "".join(served),
                               model=aic.MODEL_CHAT)
