"""
backend/api/services/preparers.py — who prepares a site's consumption papers,
Day and Night, and since when (Phase 22e, rulings Q22-14..16).

The operator's rule, measured 189/189 on the 5–6 Oct papers: a paper with
"(Night)" written next to the date was prepared by the night-shift preparer
(Kalied at CNCEC), an UNMARKED paper is a Day paper (Johnson). The names change
over time — four other names prepared papers in earlier months — so each site
keeps a HISTORY: a list of `{from, day, night}`, and a paper of a given date is
mapped to the pair in force that day. An older paper keeps its old names.

Stored in `app_settings` as `consumption_preparers:<site>` (JSON list), edited
by the Admin or the site's HOD (Admin Console → Sites), audited.

Phase 23c (ruling Q23-1) adds ONE-DAY COVERS: `{on, cover, shift}` — somebody
who prepared papers on a single date (Imtiyaz, 28 Sep 2026, 3 lines between
the Day and Night preparers' own). A cover never changes the pair in force; it
is one more name for that date (offered on OCR Import, counted by the
back-check). `shift` is "" when the paper does not say which.
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

KEY = "consumption_preparers:{site}"


def _key(site: str) -> str:
    return KEY.format(site=site)


async def history(session: AsyncSession, site: str) -> list[dict]:
    raw = (await session.execute(text("SELECT value FROM app_settings WHERE key = :k"),
                                 {"k": _key(site)})).scalar()
    try:
        rows = json.loads(raw) if raw else []
    except ValueError:
        rows = []
    out = [r for r in rows if isinstance(r, dict)
           and (r.get("day") or r.get("night") or (r.get("on") and r.get("cover")))]
    return sorted(out, key=lambda r: (r.get("from") or r.get("on") or "", "on" in r))


def regular(rows: list[dict]) -> list[dict]:
    """The from-date pairs, without the one-day covers."""
    return [r for r in rows if not r.get("on")]


def covers_on(rows: list[dict], day: Optional[_dt.date], shift: Optional[str] = None) -> list[str]:
    """Names covering `day` (any shift, or the given one)."""
    if day is None:
        return []
    iso = day.isoformat()
    return [r["cover"] for r in rows if r.get("on") == iso and r.get("cover")
            and (not shift or not r.get("shift") or r["shift"] == shift)]


def names_on(rows: list[dict], day: Optional[_dt.date]) -> set[str]:
    """Everybody who may have prepared a paper on `day` — the pair in force
    plus that day's covers (the back-check's question)."""
    p = pick(rows, day) or {}
    return {n for n in (p.get("day"), p.get("night"), *covers_on(rows, day)) if n}


def pick(rows: list[dict], day: Optional[_dt.date]) -> Optional[dict]:
    """The pair in force on `day` (the latest `from` on or before it); with no
    date, the newest pair."""
    rows = regular(rows)
    if not rows:
        return None
    if day is None:
        return rows[-1]
    iso = day.isoformat()
    cur = None
    for r in rows:
        if (r.get("from") or "") <= iso:
            cur = r
    return cur


def shift_of(marker: Optional[str]) -> tuple[str, bool]:
    """(shift, marked): "(Night)" → Night; no marker → Day (ruling Q22-14 —
    for the consumption paper only; the handwritten-spec pass still records
    no marker as no shift)."""
    if marker and marker.lower().startswith("night"):
        return "Night", True
    if marker and marker.lower().startswith("day"):
        return "Day", True
    return "Day", False


async def preparer_for(session: AsyncSession, site: str, day: Optional[_dt.date],
                       shift: str) -> Optional[str]:
    p = pick(await history(session, site), day)
    if not p:
        return None
    return (p.get("night") if shift == "Night" else p.get("day")) or None


def clean(rows: list[dict]) -> list[dict]:
    """Validated history: ISO `from` dates (or empty for "always"), names
    trimmed, no two entries on the same date. ValueError with a sentence."""
    seen = set()
    out = []
    covers: list[dict] = []
    for r in rows:
        if r.get("on") or r.get("cover"):
            on = str(r.get("on") or "").strip()[:10]
            try:
                _dt.date.fromisoformat(on)
            except ValueError:
                raise ValueError(f"a one-day cover needs its date — “{on}” is not one (YYYY-MM-DD)")
            name = str(r.get("cover") or "").strip()[:80]
            if not name:
                raise ValueError(f"the cover on {on} needs a name")
            shift = str(r.get("shift") or "").strip().title()
            if shift not in ("", "Day", "Night"):
                raise ValueError("a cover's shift is Day, Night or blank")
            if any(c["on"] == on and c["cover"].lower() == name.lower() for c in covers):
                raise ValueError(f"{name} is already a cover on {on}")
            covers.append({"on": on, "cover": name, "shift": shift})
            continue
        frm = str(r.get("from") or "").strip()[:10]
        if frm:
            try:
                _dt.date.fromisoformat(frm)
            except ValueError:
                raise ValueError(f"“{frm}” is not a date (YYYY-MM-DD)")
        if frm in seen:
            raise ValueError(f"two entries start on {frm or 'the first day'}")
        seen.add(frm)
        day, night = str(r.get("day") or "").strip()[:80], str(r.get("night") or "").strip()[:80]
        if not day and not night:
            raise ValueError("each entry needs a Day or a Night name")
        out.append({"from": frm, "day": day, "night": night})
    return sorted(out, key=lambda r: r["from"]) + sorted(covers, key=lambda r: r["on"])


async def save(session: AsyncSession, site: str, rows: list[dict]) -> list[dict]:
    rows = clean(rows)
    await session.execute(text(
        "INSERT INTO app_settings (key, value) VALUES (:k, :v) "
        "ON CONFLICT (key) DO UPDATE SET value = :v"), {"k": _key(site), "v": json.dumps(rows)})
    return rows
