"""
backend/api/services/drive_links.py — what a Drive file's NAME links to
(Phase 22, plan §1.2).

`drive_files` is filled by the Drive sync (22a). This module reads each file's
name and writes `parsed_key` / `parsed_date` / `link_status`, so the app can
find "the DN photo of this receipt", "the certificate of this batch" and "the
request this line came from". Every parser is idempotent over the whole table
and runs after each crawl (`relink_all`).

    22b  DN for CNCEC → receipts / returns by DN number  (link_dn)
    22c  MTC          → lots by batch                    (link_mtc)
    22d  Pending      → material requests                (link_requests)

⚠️ NOTHING LINKS SILENTLY WHEN THE MATCH IS NOT EXACT (plan §7): an unmatched
file is listed as unlinked, never guessed.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

LINKERS: list = []          # (name, async fn(session) -> dict) — appended by each slice


async def relink_all(session: AsyncSession) -> dict:
    out = {}
    for name, fn in LINKERS:
        out[name] = await fn(session)
    return out
