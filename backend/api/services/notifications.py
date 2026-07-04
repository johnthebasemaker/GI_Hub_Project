"""
backend/api/services/notifications.py — in-app notifications (the sidebar bell).

Ports the old app's queue_app_notification + the bell-inbox queries. A
notification targets either a specific user (recipient_user) or a role
broadcast (recipient_role), optionally narrowed by site and/or warehouse.

Visibility rule (ported verbatim from get_app_notifications) — a row is
visible to (username, role, site, warehouse) when:

    recipient_user = username
    OR (recipient_role = role
        AND (recipient_site IS NULL      OR recipient_site = site)
        AND (recipient_warehouse IS NULL OR recipient_warehouse = warehouse))

The OR-group is fully parenthesised so an `AND read_at IS NULL` binds to BOTH
branches (the un-parenthesised form leaked read rows on the user branch).
"""
from __future__ import annotations

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .ledger import _MD  # shared reflected metadata

notifications_t = _MD.tables["app_notifications"]
_c = notifications_t.c
_VALID_SEV = {"info", "warning", "critical", "success"}


async def notify(session: AsyncSession, *, event_key: str, title: str, body: str = "",
                 severity: str = "info", recipient_user: str | None = None,
                 recipient_role: str | None = None, recipient_site: str | None = None,
                 recipient_warehouse: str | None = None, link_page: str | None = None,
                 link_anchor: str | None = None, related_table: str | None = None,
                 related_ref: str | None = None) -> int | None:
    """Insert one notification (ports queue_app_notification).

    Requires a recipient_user OR recipient_role — otherwise a silent no-op
    (returns None), so a missing recipient can never crash the parent event.
    """
    if severity not in _VALID_SEV:
        severity = "info"
    if not (recipient_user or recipient_role):
        return None
    res = await session.execute(insert(notifications_t).values(
        recipient_user=recipient_user, recipient_role=recipient_role,
        recipient_site=recipient_site, recipient_warehouse=recipient_warehouse,
        event_key=event_key, severity=severity, title=title, body=body,
        link_page=link_page, link_anchor=link_anchor,
        related_table=related_table, related_ref=related_ref,
    ).returning(_c["id"]))
    return res.scalar_one()


def _visible(username: str, role: str, site_id: str | None, warehouse_id: str | None):
    return (
        (_c["recipient_user"] == username)
        | (
            (_c["recipient_role"] == role)
            & (_c["recipient_site"].is_(None) | (_c["recipient_site"] == site_id))
            & (_c["recipient_warehouse"].is_(None) | (_c["recipient_warehouse"] == warehouse_id))
        )
    )


async def list_for(session: AsyncSession, *, username: str, role: str, site_id: str | None,
                   warehouse_id: str | None, unread_only: bool = False, limit: int = 50):
    stmt = select(
        _c["id"], _c["event_key"], _c["severity"], _c["title"], _c["body"],
        _c["link_page"], _c["link_anchor"], _c["related_table"], _c["related_ref"],
        _c["read_at"], _c["created_at"],
    ).where(_visible(username, role, site_id, warehouse_id))
    if unread_only:
        stmt = stmt.where(_c["read_at"].is_(None))
    stmt = stmt.order_by(_c["created_at"].desc(), _c["id"].desc()).limit(limit)
    return [dict(m) for m in (await session.execute(stmt)).mappings().all()]


async def unread_count(session: AsyncSession, *, username: str, role: str,
                       site_id: str | None, warehouse_id: str | None) -> int:
    stmt = select(func.count()).select_from(notifications_t).where(
        _visible(username, role, site_id, warehouse_id) & _c["read_at"].is_(None))
    return (await session.execute(stmt)).scalar_one()


async def mark_read(session: AsyncSession, *, notif_id: int, username: str, role: str,
                    site_id: str | None, warehouse_id: str | None) -> bool:
    """Mark one notification read — but only if it's visible to this user
    (so nobody can mark someone else's row by guessing an id)."""
    res = await session.execute(update(notifications_t).where(
        (_c["id"] == notif_id) & _c["read_at"].is_(None)
        & _visible(username, role, site_id, warehouse_id)
    ).values(read_at=func.now()))
    return res.rowcount > 0


async def mark_all_read(session: AsyncSession, *, username: str, role: str,
                        site_id: str | None, warehouse_id: str | None) -> int:
    res = await session.execute(update(notifications_t).where(
        _c["read_at"].is_(None) & _visible(username, role, site_id, warehouse_id)
    ).values(read_at=func.now()))
    return res.rowcount
