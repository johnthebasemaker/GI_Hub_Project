"""
backend/api/stt.py — voice input (Phase 23e, rulings Q23-10..12).

    GET  /ai/stt/status   is voice input available here? (the microphone hides itself if not)
    POST /ai/stt          a ≤ 30 s 16 kHz mono WAV → {"text": …}; the text is put in the
                          box for the person to read and send — never sent by itself

Everybody signed in may dictate (the Hub Assistant and any text field). The
recording is transcribed on this machine by Whistle (services/stt.py), held in
memory only for the call, and never stored; the log keeps its length and the
timing, not the words. The site's own words — its most-used materials, its
tanks — are passed as keywords so "Tyvek", "Remafix" or "J027" survive.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user, resolve_site_param
from .db import get_session
from .services import stt as STT

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ai/stt", tags=["ai"])

_KW_TTL_S = 600
_kw_cache: dict[str, tuple[float, list[str]]] = {}


def _short(desc: str) -> str:
    words = [w for w in (desc or "").replace("/", " ").split() if w.isalpha() or any(c.isdigit() for c in w)]
    return " ".join(words[:3]).title()


async def site_keywords(session: AsyncSession, site: Optional[str]) -> list[str]:
    """The site's own vocabulary: the 40 most-issued materials of the last 60
    days (first words of their names), the tanks of the last 14, the sites."""
    key = site or "*"
    hit = _kw_cache.get(key)
    if hit and time.time() - hit[0] < _KW_TTL_S:
        return hit[1]
    args = {"s": site}
    where = 'AND c."Site_ID" = :s' if site else ""
    mats = [r[0] for r in (await session.execute(text(f'''
        SELECT i."Equipment_Description" FROM consumption c
        JOIN inventory i ON TRIM(i."SAP_Code") = TRIM(c."SAP_Code")
        WHERE LEFT(c."Date", 10) >= to_char(CURRENT_DATE - 60, 'YYYY-MM-DD') {where}
        GROUP BY 1 ORDER BY count(*) DESC LIMIT 40'''), args)).all()]
    tanks = [r[0] for r in (await session.execute(text(f'''
        SELECT TRIM("Tank_No") FROM consumption c
        WHERE COALESCE(TRIM("Tank_No"), '') <> '' AND lower(TRIM("Tank_No")) <> 'others'
          AND LEFT(c."Date", 10) >= to_char(CURRENT_DATE - 14, 'YYYY-MM-DD') {where}
        GROUP BY 1 ORDER BY count(*) DESC LIMIT 20'''), args)).all()]
    sites = [r[0] for r in (await session.execute(text(
        'SELECT DISTINCT "Site_ID" FROM inventory WHERE COALESCE("Site_ID", \'\') <> \'\' LIMIT 10'))).all()]
    out: list[str] = []
    for w in [*(_short(m) for m in mats), *tanks, *sites, "GI Hub", "SAP", "PR", "DN", "MTC"]:
        if w and w not in out:
            out.append(w)
    _kw_cache[key] = (time.time(), out[:80])
    return _kw_cache[key][1]


@router.get("/status", summary="Is voice input available on this server?")
async def stt_status(user: dict = Depends(get_current_user)):
    st = STT.status()
    return {"available": st["provider"] == "whistle", "provider": st["provider"],
            "reason": st.get("reason"), "language": "en", "max_seconds": 30}


@router.post("", summary="Speech → text, on this machine (Whistle)")
async def stt(audio: UploadFile = File(...), site_id: Optional[str] = None,
              user: dict = Depends(get_current_user),
              session: AsyncSession = Depends(get_session)):
    from .ratelimit import check_bucket_shared
    await check_bucket_shared(session, f"stt:{user['username']}", 30, 60,
                              "that is a lot of recordings in a minute — wait a moment")
    st = STT.status()
    if st["provider"] != "whistle":
        raise HTTPException(503, st["reason"] or "voice input is not available here")
    data = await audio.read(STT.MAX_BYTES + 1)
    try:
        kw = await site_keywords(session, resolve_site_param(user, site_id))
    except Exception:  # noqa: BLE001 — the vocabulary only helps; it never blocks
        kw = []
    try:
        out = await STT.transcribe(data, kw)
    except STT.SttError as e:
        raise HTTPException(422, str(e))
    logger.info("stt: %s · %.1f s of audio · %d ms · %d chars", user["role"], out["seconds"],
                out["ms"], len(out["text"]))
    return out
