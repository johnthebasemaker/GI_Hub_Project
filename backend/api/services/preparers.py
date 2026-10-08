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
    out = [r for r in rows if isinstance(r, dict) and (r.get("day") or r.get("night"))]
    return sorted(out, key=lambda r: r.get("from") or "")


def pick(rows: list[dict], day: Optional[_dt.date]) -> Optional[dict]:
    """The pair in force on `day` (the latest `from` on or before it); with no
    date, the newest pair."""
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
    for r in rows:
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
    return sorted(out, key=lambda r: r["from"])


async def save(session: AsyncSession, site: str, rows: list[dict]) -> list[dict]:
    rows = clean(rows)
    await session.execute(text(
        "INSERT INTO app_settings (key, value) VALUES (:k, :v) "
        "ON CONFLICT (key) DO UPDATE SET value = :v"), {"k": _key(site), "v": json.dumps(rows)})
    return rows
