"""
backend/api/ai/paper_fields.py — the two header-ish fields of a consumption
paper the vision model gets wrong most often (Phase 21d follow-up, measured on
the operator's 11 photos of 1–4 Oct 2026).

1. THE DATE. 3 of 11 pages came back with a wrong date — `01/01/26` and
   `01/07/26` for 01/10/26, `09/10/26` for 04/10/26 — and every row of such a
   page lands on the wrong day. `check_paper_date()` says whether the date read
   is plausible for a paper uploaded today (the last 14 days, or tomorrow for a
   night shift written ahead) and, when it is not, which nearby dates the
   handwriting most likely says. It NEVER changes the date by itself: the store
   keeper confirms one (the operator confirmed the papers follow these dates —
   2026-10-06). Day first, always (ruling Q21-11).

2. THE WORK TYPE (the paper's Remarks column = the workbook's Work Type,
   Q21-2). `norm_work_type()` maps the spellings the model produced
   (`PV`, `RIL`, `BLL`, `Blaster`, `R.L` …) onto the workbook's own values.

Pure functions; no I/O. Pinned by service-test suite 21D2 and used by the
harness `tools/ocr_eval.py`, so the app and the measurement agree. Applied on
the OCR page (`POST /ai/ocr/paper-check`), NOT inside the handwritten-spec
pass, whose TSV export keeps the remark exactly as written (suite AM).
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Optional

from .handwritten import parse_shift, strip_shift

# ── 1 · the date ─────────────────────────────────────────────────────────────
WINDOW_BACK_DAYS = 14          # a paper older than two weeks is a question, not an error
WINDOW_AHEAD_DAYS = 1          # a night shift sometimes dates the paper tomorrow
MAX_COST = 2.0                 # two of the three parts misread at most

# Digits a handwritten form confuses (each way). Cost 0.5 instead of 1 — "9"
# for "4" is one stroke, "9" for "2" is a different number.
_CONFUSED = {frozenset(p) for p in ("17", "49", "06", "38", "56", "08", "58", "68", "27", "09", "14")}
_DATE_RX = re.compile(r"(\d{1,2})\s*[/.\-\\|,\s]\s*(\d{1,2})\s*[/.\-\\|,\s]\s*(\d{2,4})")


def _sub(a: str, b: str) -> float:
    if a == b:
        return 0.0
    return 0.5 if frozenset((a, b)) in _CONFUSED else 1.0


def digit_distance(a: str, b: str) -> float:
    """Weighted Damerau–Levenshtein over two digit strings: a confusable digit
    costs 0.5, any other substitution / insertion / deletion 1, two swapped
    neighbours 1 (`01/01` ↔ `01/10` is one swap)."""
    n, m = len(a), len(b)
    d = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = float(i)
    for j in range(m + 1):
        d[0][j] = float(j)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + _sub(a[i - 1], b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[n][m]


def _parts(day: int | str, month: int | str, year: int | str) -> tuple[str, str, str]:
    return f"{int(day):02d}", f"{int(month):02d}", f"{int(year) % 100:02d}"


def date_cost(seen: tuple[str, str, str], cand: tuple[str, str, str]) -> float:
    """Day, month and year are written as three separate numbers, so they are
    scored separately and a wholly misread part counts ONE mistake: `01/07`
    for `01/10` (the month misread, the day intact) costs 1, while `06/10`
    for it (both parts different) costs 2."""
    return sum(min(digit_distance(a, b), 1.0) for a, b in zip(seen, cand))


def _label(d: _dt.date) -> str:
    return d.strftime("%d/%m/%y")


def check_paper_date(date_text: Any, today: Optional[_dt.date] = None) -> dict:
    """{read, date_iso, shift, plausible, days_from_today, candidates, window}.

    `date_iso` is the date AS READ (None when it is not a real date);
    `plausible` is True only when it falls inside the window. `candidates` —
    up to three dates inside the window that differ from what was read in at
    most two of day / month / year, the most alike first — are offered ONLY when the read
    date is not plausible."""
    today = today or _dt.date.today()
    lo = today - _dt.timedelta(days=WINDOW_BACK_DAYS)
    hi = today + _dt.timedelta(days=WINDOW_AHEAD_DAYS)
    raw = str(date_text or "").strip()
    out: dict = {"read": raw, "date_iso": None, "shift": parse_shift(raw) if raw else None,
                 "plausible": False, "days_from_today": None, "candidates": [],
                 "window": [lo.isoformat(), hi.isoformat()]}
    m = _DATE_RX.search(strip_shift(raw)) if raw else None
    if not m:
        return out                                   # nothing to read: the store keeper picks
    dd, mm, yy = m.group(1), m.group(2), m.group(3)
    year = int(yy) + (2000 if len(yy) == 2 else 0)
    try:
        read = _dt.date(year, int(mm), int(dd))
        out["date_iso"] = read.isoformat()
        out["days_from_today"] = (read - today).days
        if lo <= read <= hi:
            out["plausible"] = True
            return out
    except ValueError:
        read = None                                  # 31/02 — still worth a suggestion
    seen = _parts(dd, mm, yy)
    scored = []
    d = lo
    while d <= hi:
        cost = date_cost(seen, _parts(d.day, d.month, d.year))
        if cost <= MAX_COST:
            scored.append((cost, abs((d - today).days), d))
        d += _dt.timedelta(days=1)
    scored.sort(key=lambda x: (x[0], x[1]))
    out["candidates"] = [{"date_iso": c.isoformat(), "label": _label(c), "cost": cost}
                         for cost, _gap, c in scored[:3]]
    return out


# ── 2 · the work type ────────────────────────────────────────────────────────
# Keys are compared upper-case with spaces, dots and dashes removed.
WORK_TYPES = {
    "PU": "PU", "PV": "PU", "P/U": "PU", "PIU": "PU",
    "RL": "R/L", "R/L": "R/L", "RIL": "R/L", "R1L": "R/L", "R|L": "R/L", "2L": "R/L", "2/L": "R/L",
    "BL": "B/L", "B/L": "B/L", "BLL": "B/L", "B1L": "B/L", "BIL": "B/L", "B|L": "B/L", "8/L": "B/L",
    # the workbook itself writes both "Blast" and "Blasting": a correct
    # "Blasting" is left alone; only the misspellings become "Blast"
    "BLAST": "Blast", "BLASTER": "Blast", "BLASTING": "Blasting",
    "OTHERS": "Others", "OTHER": "Others",
    # Phase 22e — what the vision model read on the 5–6 Oct papers, line by line
    # against the workbook: a handwritten B as "15"/"13", an R as "12", "Blast"
    # as "Best", "Buffing" as "Buffy"
    "15/L": "B/L", "15L": "B/L", "13/L": "B/L", "13L": "B/L",
    "12/L": "R/L", "12L": "R/L", "12LL": "R/L",
    "BEST": "Blast", "BLST": "Blast", "BUFFY": "Buffing", "BUFF": "Buffing", "BUFFING": "Buffing",
}
# A Remarks cell that is only a DITTO mark as the model reads it: "1", "11",
# "n", "h", "″" — never a work type, always "same as above".
_WT_DITTO = re.compile(r'^(?:1{1,2}|[nNhH]|[″"〃\'`,.·\-]+)$')


def norm_work_type(v: Any) -> str:
    """The workbook's spelling of a work type, or the text as written when it
    is not one of them (never blanked — an unknown remark is still a remark)."""
    s = str(v or "").strip()
    key = re.sub(r"[\s.\-]+", "", s).upper()
    return WORK_TYPES.get(key, s)


def fill_work_types(values: list[Any]) -> list[str]:
    """Each Remarks cell in the workbook's spelling, a ditto mark (or a blank
    under a written one) taking the work type above it (Phase 22e)."""
    out, prev = [], ""
    for v in values:
        s = str(v or "").strip()
        if (not s or _WT_DITTO.match(s)) and prev:
            out.append(prev)
            continue
        w = norm_work_type(s)
        out.append(w)
        if w:
            prev = w
    return out


# ── 3 · the tank (Phase 22e, ruling Q22-17) ─────────────────────────────────
# The paper's Tank No. column, as the crew writes it: `K-TNK-091` (Train K's
# tank 091 = `522-8k10-TNK-091`), `89D0-TNK-001` (= `522-89D0-TNK-001`),
# `J027`, `others` — and most lines a DITTO mark. The vision model garbles the
# handwriting: `84D0`, `S4D0`, `K-TNK-04L`. Matched like names are (Q21-5):
#
#   auto       an official tag, a tag by the written short form (`K-` is Train K,
#              `J0xx` the J series — Q22-17), or a LEARNED spelling — green
#   suggested  the nearest tag(s) after the confusable strokes (4↔9, S↔8, O↔0,
#              L↔1 …) — gold, the store keeper accepts; and a bare `TNK-091`,
#              which is a tank in BOTH trains, is never picked: both are offered
#   unknown    nothing close — red
#   ditto      only marks (″ " 〃 ,, ·) — the row inherits the tank above it
#   blank      nothing written
_TANK_CONFUSED = {frozenset(p) for p in ("49", "S8", "S5", "B8", "O0", "D0", "L1", "I1", "Z2",
                                         "G6", "71", "Q0")}
_TANK_RULE = re.compile(r"^(?P<train>[JK])?\W*(?:TNK|TANK|TK)\W*(?P<num>\d{1,4})$")
_AREA_RULE = re.compile(r"^(?P<area>[0-9A-Z]{4})\W*(?:TNK|TANK|TK)\W*(?P<num>\d{1,4})$")


def tank_norm(v: Any) -> str:
    """The sync's `alias_norm`: upper, separators gone, leading zeros inside each
    digit run dropped (`J091`, `J0091`, `J-0091` → `J91`)."""
    s = re.sub(r"[^A-Za-z0-9]", "", str(v or "")).upper()
    return re.sub(r"0*(\d+)", lambda m: m.group(1), s)


def tank_key(written: Any) -> str:
    """What a WRITTEN tank is matched and learned by: a letter O touching a
    digit is a zero ("Jo27" = J027), then `tank_norm`. Nothing bolder — an L or
    an I stays a question (gold), never a quiet guess."""
    raw = re.sub(r"(?<=\d)[oO]|[oO](?=\d)", "0", str(written or "").strip())
    return tank_norm(raw)


def tank_forms(tag: str) -> set[str]:
    """Every short form a tag is written as: itself, without the `522-` project
    prefix, and the train form (`522-8k10-TNK-091` → `KTNK91`)."""
    n = tank_norm(tag)
    forms = {n}
    if n.startswith("522"):
        forms.add(n[3:])
    m = re.search(r"8([JK])\d+TNK(\d+)$", n)
    if m:
        forms.add(f"{m.group(1)}TNK{m.group(2)}")
    return forms


def _tank_cost(a: str, b: str) -> float:
    """Weighted edit distance: a confusable stroke costs ½."""
    n, m = len(a), len(b)
    d = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = float(i)
    for j in range(m + 1):
        d[0][j] = float(j)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            x, y = a[i - 1], b[j - 1]
            sub = 0.0 if x == y else (0.5 if frozenset((x, y)) in _TANK_CONFUSED else 1.0)
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + sub)
    return d[n][m]


def match_tank(written: Any, tags: list[str], aliases: Optional[dict[str, str]] = None,
               *, max_cost: float = 1.5) -> dict:
    """One written tank → {written, state, tag, source, candidates: [{tag, cost}]}."""
    raw = str(written or "").strip()
    out: dict = {"written": raw, "state": "unknown", "tag": None, "source": None, "candidates": []}
    if not raw:
        return {**out, "state": "blank"}
    if not re.search(r"[A-Za-z0-9]", raw):
        return {**out, "state": "ditto"}
    if raw.lower().rstrip("s") == "other":
        return {**out, "state": "auto", "tag": "others", "source": "exact"}
    raw_n = re.sub(r"(?<=\d)[oO]|[oO](?=\d)", "0", raw)
    n = tank_key(raw)
    al = aliases or {}
    if n in al:
        return {**out, "state": "auto", "tag": al[n], "source": "learned"}
    for t in tags:
        if n == tank_norm(t):
            return {**out, "state": "auto", "tag": t, "source": "exact"}
    m = _TANK_RULE.match(raw_n.upper().replace(" ", ""))
    if m:
        num, train = str(int(m.group("num"))), m.group("train")
        hits = [t for t in tags if tank_norm(t).endswith(f"TNK{num}")
                and (not train or f"8{train}" in tank_norm(t))]
        if len(hits) == 1 and train:
            return {**out, "state": "auto", "tag": hits[0], "source": "rule"}
        if hits:
            return {**out, "state": "suggested", "tag": None, "source": "rule",
                    "candidates": [{"tag": t, "cost": 0.0} for t in hits]}
    m = _AREA_RULE.match(raw_n.upper().replace(" ", ""))
    if m:
        area, num = tank_norm(m.group("area")), str(int(m.group("num")))
        hits = [t for t in tags if area in tank_norm(t) and tank_norm(t).endswith(f"TNK{num}")]
        if len(hits) == 1:
            return {**out, "state": "auto", "tag": hits[0], "source": "rule"}
    scored = sorted(((min(_tank_cost(n, f) for f in tank_forms(t)), t) for t in tags),
                    key=lambda x: (x[0], x[1]))
    out["candidates"] = [{"tag": t, "cost": c} for c, t in scored[:3]]
    if scored and scored[0][0] <= max_cost:
        best = scored[0][0]
        tied = [t for c, t in scored if c == best]
        out.update(state="suggested", source="fuzzy", tag=tied[0] if len(tied) == 1 else None)
    return out


def fill_dittos(matches: list[dict]) -> list[dict]:
    """A ditto (or a blank under a written tank) takes the tank of the line
    above — the way the paper is read."""
    prev = None
    out = []
    for m in matches:
        if m["state"] in ("ditto", "blank") and prev is not None:
            # `group` = the written tank this line repeats, so the store keeper
            # can tick every line of one tank at once (Q22-17)
            out.append({**prev, "written": m["written"], "inherited": True,
                        "group": prev["written"]})
            continue
        out.append({**m, "group": m["written"]})
        if m["state"] not in ("ditto", "blank"):
            prev = m
    return out
