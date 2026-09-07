"""
backend/api/ai/tutorials.py — Phase 12f: the assistant answers, and points at
the frame.

WHAT THIS IS FOR. The brief that opened Phase 12 wanted tutorials generated
on demand when somebody searches for help. Pre-rendering won that argument
(`PROPOSED_PHASE12_PLAN.md` §2) for five reasons, the deciding one being that a
video generated at request time has no version and therefore cannot be evidence
(P10-6). But the NEED behind the request was real, and this is the answer to it:
the assistant replies in text, in under a second, and attaches a deep link to
the exact second of a pre-rendered tutorial where that step is on screen.

It is only possible because slice 12b made every recording carry `beats.json` —
a stamped millisecond per UI step — and 12c wrote narration next to the step it
describes. The index below is built from those two, so a link never points at a
timestamp somebody guessed.

────────────────────────────────────────────────────────────────────────────
⚠️ P12-6 — THE ROLE FENCE RUNS BEFORE THE SCORE, EXACTLY AS IN RULE 9.

`Index.search(allowed=…)` filters candidates BEFORE BM25 ranks them, and that
is the security boundary for the manual. The same machinery is reused here
rather than reimplemented, with `allowed` built from each tutorial's declared
`audience`. A tutorial a role may not watch is never a candidate, so no
question can surface it — not by phrasing, not by luck.

⚠️ AND THE LINK IS NOT A NEW WAY IN. `/training` is nav-guarded like every other
route, the player still refuses a module whose `required_roles` exclude the
viewer, and the deep link carries no content — only a module key, a language
and a number of seconds. It is a bookmark, not a grant.

────────────────────────────────────────────────────────────────────────────
⚠️ IT DEGRADES TO NOTHING, ON PURPOSE. Until the Hetzner cutover puts the
renders in object storage (ruling Q3), the manifests live in a gitignored local
directory that a production box does not have. A missing directory yields no
links and no error: the assistant answers exactly as it did before. An
assistant that 500s because a video is missing would be a worse product than one
that never had the feature.
"""
from __future__ import annotations

import json
import os
import pathlib
import threading
from dataclasses import dataclass

from .manual_index import Chunk, Index, _tokens

_ROOT = pathlib.Path(__file__).resolve().parents[3]
TUTORIAL_DIR = pathlib.Path(
    os.environ.get("GI_TUTORIAL_DIR") or (_ROOT / "docs" / "tutorials" / "out"))

# ⚠️ TWO FLOORS, AND THE SECOND ONE IS THE REAL GUARD. BM25 always ranks
# SOMETHING first, and "the closest of four videos" is not "a video about this"
# — a confidently wrong link costs more than a missing one, because the reader
# spends ninety seconds finding that out.
#
# A score alone cannot tell the difference, and the reason is worth writing
# down: `manual_index._tokens` expands SYNONYMS. "valuation" becomes
# "stock value board brief not valued", so a Store Keeper asking about the
# executive summary's valuation floor shares the single token "stock" with
# "This is Return Stock" — and with the 2.4x heading boost that one accidental
# word scored 4.95, comfortably over a floor of 4. Suite CX-04 caught it.
#
# So a candidate must ALSO share at least two distinct tokens with the beat it
# won on. One word in common is a coincidence; two is a topic. That is a
# structural rule rather than a number somebody nudges when a link fails to
# appear.
# ⚠️ THE FLOOR STAYS LOW AND THE OVERLAP RULE DOES THE WORK, on evidence.
# Raising the floor to 6.0 killed the coincidence AND a real hit — an HOD
# asking "what does not valued mean" scored 4.59 against the beat that exists
# to answer exactly that. Short honest questions score low; a coincidence
# scores low too. The two are told apart by whether the words are actually
# shared, not by how hard the winner won.
MIN_SCORE = 4.0
MIN_TOKEN_OVERLAP = 2

_LOCK = threading.Lock()
_CACHE: dict = {"key": None, "index": None, "beats": []}


@dataclass(frozen=True)
class TutorialBeat:
    tutorial_id: str
    module_key: str | None
    language: str
    title: str
    subtitle: str
    audience: tuple[str, ...]
    beat: str
    note: str
    start_s: float
    text: str
    duration_s: float

    @property
    def url(self) -> str:
        """A bookmark into the training player. No content, no token."""
        mod = self.module_key or self.tutorial_id
        return (f"/training?module={mod}&lang={self.language}"
                f"&t={self.start_s:.1f}")


def _manifest_key() -> tuple:
    """(path, mtime, size) for every manifest — the cache's invalidation."""
    if not TUTORIAL_DIR.is_dir():
        return ()
    out = []
    for p in sorted(TUTORIAL_DIR.glob("*.manifest.json")):
        try:
            st = p.stat()
        except OSError:
            continue
        out.append((p.name, int(st.st_mtime), st.st_size))
    return tuple(out)


def _read_manifests() -> list[TutorialBeat]:
    beats: list[TutorialBeat] = []
    if not TUTORIAL_DIR.is_dir():
        return beats
    for path in sorted(TUTORIAL_DIR.glob("*.manifest.json")):
        try:
            m = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a half-written manifest is not fatal
            continue
        # ⚠️ FALLS BACK TO THE RECORDED ROLE, NEVER TO "EVERYONE". A manifest
        # written before `audience` existed offers its tutorial to the one role
        # it was recorded as, which is the narrow answer. The wide one would
        # hand a Store Keeper an HOD walk-through the day an old file appeared.
        audience = tuple(m.get("audience") or [m.get("hub_role")] or [])
        if not audience or audience == (None,):
            continue
        dur = float((m.get("video") or {}).get("duration_s") or 0.0)
        for b in m.get("beats") or []:
            beats.append(TutorialBeat(
                tutorial_id=m.get("tutorial_id", path.stem),
                module_key=m.get("training_module_key"),
                language=m.get("language", "en"),
                title=m.get("title", ""),
                subtitle=m.get("subtitle", ""),
                audience=audience,
                beat=b.get("id", ""),
                note=str(b.get("note") or ""),
                start_s=float(b.get("start_s") or 0.0),
                text=str(b.get("text") or ""),
                duration_s=dur,
            ))
    return beats


def _build() -> tuple[Index | None, list[TutorialBeat]]:
    """
    One `Chunk` per BEAT, so a hit is a moment rather than a video.

    `chapter` is the beat's position in the list — a synthetic number that the
    fence then filters on, which is precisely how `allowed` is used for the
    manual. `chapter_title` and `heading` feed the index's heading boost, so a
    question that names the screen ("executive summary") ranks the beat on that
    screen above a beat that merely mentions it in passing.
    """
    beats = _read_manifests()
    if not beats:
        return None, []
    chunks = [Chunk(chapter=i, chapter_title=b.title,
                    heading=f"{b.note} {b.beat}", text=b.text)
              for i, b in enumerate(beats)]
    return Index(chunks), beats


def _index() -> tuple[Index | None, list[TutorialBeat]]:
    key = _manifest_key()
    with _LOCK:
        if _CACHE["key"] != key:
            idx, beats = _build()
            _CACHE.update(key=key, index=idx, beats=beats)
        return _CACHE["index"], _CACHE["beats"]


def match(question: str, role: str) -> dict | None:
    """
    The best moment in a tutorial this role may watch, or None.

    Deterministic: BM25 over a fixed corpus, no model, no clock. The same
    question from the same role returns the same second every time — which
    matters, because a person shown a different timestamp on Tuesday concludes
    the link is random.
    """
    idx, beats = _index()
    if idx is None or not (question or "").strip():
        return None

    # THE FENCE. Built before anything is scored.
    allowed = {i for i, b in enumerate(beats)
               if role in b.audience or role == "admin"}
    if not allowed:
        return None

    hits, tele = idx.search_scored(question, allowed=allowed, k=1,
                                   char_budget=4000)
    if not hits or not tele.get("hits"):
        return None
    top = tele["hits"][0]
    if float(top.get("score") or 0.0) < MIN_SCORE:
        return None
    b = beats[int(top["chapter"])]
    shared = set(_tokens(question)) & set(_tokens(f"{b.note} {b.beat} {b.text}"))
    if len(shared) < MIN_TOKEN_OVERLAP:
        return None
    return {
        "tutorial_id": b.tutorial_id,
        "module_key": b.module_key,
        "language": b.language,
        "title": b.title,
        "beat": b.beat,
        "note": b.note,
        "t": round(b.start_s, 1),
        "duration_s": round(b.duration_s, 1),
        "url": b.url,
        "score": round(float(top["score"]), 2),
    }


def stats() -> dict:
    """What `/ai/health` reports, so an empty index is visible rather than silent."""
    idx, beats = _index()
    return {
        "dir": str(TUTORIAL_DIR),
        "present": TUTORIAL_DIR.is_dir(),
        "tutorials": len({b.tutorial_id for b in beats}),
        "beats": len(beats),
        "indexed": idx is not None,
    }
