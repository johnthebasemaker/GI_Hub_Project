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
}


def norm_work_type(v: Any) -> str:
    """The workbook's spelling of a work type, or the text as written when it
    is not one of them (never blanked — an unknown remark is still a remark)."""
    s = str(v or "").strip()
    key = re.sub(r"[\s.\-]+", "", s).upper()
    return WORK_TYPES.get(key, s)
