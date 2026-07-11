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

import datetime as _dt
import logging
import os
from contextvars import ContextVar

from sqlalchemy import and_, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import whatsapp as _wa
from .ledger import _MD  # shared reflected metadata

notifications_t = _MD.tables["app_notifications"]
pending_summary_t = _MD.tables["pending_summary_notifications"]
_c = notifications_t.c
_VALID_SEV = {"info", "warning", "critical", "success"}

_log = logging.getLogger("gi.digest")

# ── delivery preference (Phase 6) ────────────────────────────────────────────
# Per-request WhatsApp delivery mode, set by the X-Delivery-Preference header
# middleware in main.py: "urgent" (default) sends immediately; "evening" stages
# the event in pending_summary_notifications for the 16:00 digest. Critical
# alerts ALWAYS go out immediately regardless of the preference.
_VALID_DELIVERY = ("urgent", "evening")
_delivery_pref: ContextVar[str] = ContextVar("gi_delivery_pref", default="urgent")


def set_delivery_preference(value: str):
    """Set the request-scoped delivery preference; returns the reset token."""
    v = (value or "").strip().lower()
    return _delivery_pref.set(v if v in _VALID_DELIVERY else "urgent")


def reset_delivery_preference(token) -> None:
    _delivery_pref.reset(token)


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


async def dispatch(session: AsyncSession, *, event_key: str, title: str, body: str = "",
                   severity: str = "info", recipient_user: str | None = None,
                   recipient_role: str | None = None, recipient_site: str | None = None,
                   recipient_warehouse: str | None = None, link_page: str | None = None,
                   link_anchor: str | None = None, related_table: str | None = None,
                   related_ref: str | None = None, wa_template: str = "status_update",
                   wa: bool = True, created_by: str = "system",
                   delivery: str | None = None) -> int | None:
    """Fire an in-app notification AND (best-effort) an identical WhatsApp
    message to the same recipient(s).

    The in-app row is always written (via notify) — the bell stays real-time.
    The WhatsApp send is best-effort: it is skipped entirely when WhatsApp is
    not configured, and any failure is swallowed (recorded in whatsapp_outbox
    as 'failed') so a messaging problem can never break the business action
    that triggered it. `wa_template` picks the reusable template; the
    title/body become {{1}}/{{2}}.

    Phase 6 delivery preference: `delivery` (or, when None, the request-scoped
    X-Delivery-Preference contextvar) chooses "urgent" (send now) vs "evening"
    (stage in pending_summary_notifications for the 16:00 digest). Critical
    events (severity="critical" or the critical_alert template) always send
    immediately — an urgent alert must never sit in a digest queue.
    """
    ref = str(related_ref) if related_ref is not None else None
    nid = await notify(session, event_key=event_key, title=title, body=body,
                       severity=severity, recipient_user=recipient_user,
                       recipient_role=recipient_role, recipient_site=recipient_site,
                       recipient_warehouse=recipient_warehouse, link_page=link_page,
                       link_anchor=link_anchor, related_table=related_table,
                       related_ref=ref)
    if wa and _wa.enabled():
        mode = (delivery or _delivery_pref.get() or "urgent").strip().lower()
        if mode not in _VALID_DELIVERY:
            mode = "urgent"
        if mode == "evening" and (severity == "critical" or wa_template == "critical_alert"):
            mode = "urgent"
        try:
            if mode == "evening":
                for uname in await _resolve_usernames(
                        session, recipient_user=recipient_user, recipient_role=recipient_role,
                        recipient_site=recipient_site, recipient_warehouse=recipient_warehouse):
                    await session.execute(insert(pending_summary_t).values(
                        recipient_user=uname, event_key=event_key, title=title,
                        body=body or "", related_table=related_table, related_ref=ref))
            else:
                numbers = await _wa.resolve_numbers(
                    session, recipient_user=recipient_user, recipient_role=recipient_role,
                    recipient_site=recipient_site, recipient_warehouse=recipient_warehouse)
                for n in numbers:
                    await _wa.send_template(
                        session, to=n, template_key=wa_template,
                        variables=[title, body or title], event_key=event_key,
                        related_table=related_table, related_ref=ref, created_by=created_by)
        except Exception:  # noqa: BLE001 — WhatsApp is best-effort, never fatal
            pass
    return nid


async def _resolve_usernames(session: AsyncSession, *, recipient_user: str | None = None,
                             recipient_role: str | None = None,
                             recipient_site: str | None = None,
                             recipient_warehouse: str | None = None,
                             limit: int = 25) -> list[str]:
    """Same recipient descriptor as resolve_numbers, but → usernames (for the
    digest staging table; the phone is re-resolved at digest-send time so a
    number change between staging and 16:00 still delivers correctly)."""
    users_t = _wa.users_t
    if recipient_user:
        return [recipient_user]
    if recipient_role or recipient_warehouse:
        conds = [users_t.c["Phone_Number"].isnot(None)]
        if recipient_role:
            conds.append(users_t.c["role"] == recipient_role)
        if recipient_site:
            conds.append(func.coalesce(users_t.c["Site_ID"], "") == recipient_site)
        if recipient_warehouse:
            conds.append(func.coalesce(users_t.c["Warehouse_ID"], "") == recipient_warehouse)
        rows = (await session.execute(select(users_t.c["username"])
                .where(and_(*conds)).limit(limit))).scalars().all()
        return [r for r in rows if r]
    return []


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


# ── evening digest — the batch aggregator (Phase 6) ──────────────────────────
def _compile_digest(items: list[dict], max_chars: int = 950) -> str:
    """Compile staged events into ONE clean bulleted line-up.

    Meta template body parameters may not contain newlines (#132000), so the
    "bullets" are •-separated segments on a single line. Kept under the 1024
    param cap with an explicit "(+N more)" tail instead of silent truncation.
    """
    parts: list[str] = []
    for it in items:
        t = " ".join(str(it.get("title") or "").split())
        b = " ".join(str(it.get("body") or "").split())
        line = f"• {t}" if not b or b == t else f"• {t} — {b}"
        parts.append(line)
    out = ""
    for i, p in enumerate(parts):
        nxt = p if not out else f"{out}  {p}"
        if len(nxt) > max_chars:
            return f"{out}  …(+{len(parts) - i} more)" if out else p[:max_chars]
        out = nxt
    return out or "-"


async def send_evening_digests(session: AsyncSession, *, now: _dt.datetime | None = None) -> dict:
    """One digest run: group unprocessed staging rows by recipient, send ONE
    gi_evening_summary template per person, and mark rows processed only after
    a successful send (failed sends stay pending → retried next run).

    Rows are claimed FOR UPDATE SKIP LOCKED so concurrent workers can't
    double-send a recipient's digest."""
    now = now or _dt.datetime.now()
    p = pending_summary_t.c
    rows = (await session.execute(
        select(p["id"], p["recipient_user"], p["event_key"], p["title"], p["body"])
        .where(p["processed_at"].is_(None)).order_by(p["recipient_user"], p["id"])
        .with_for_update(skip_locked=True))).mappings().all()
    by_user: dict[str, list[dict]] = {}
    for r in rows:
        by_user.setdefault(r["recipient_user"], []).append(dict(r))

    sent = failed = no_phone = 0
    for uname, items in by_user.items():
        number = (await session.execute(
            select(_wa.users_t.c["Phone_Number"])
            .where(_wa.users_t.c["username"] == uname))).scalar_one_or_none()
        ids = [it["id"] for it in items]
        if not (number or "").strip():
            # Undeliverable forever — retire the rows so the queue can't grow
            # unbounded, and leave a clear trace in the log.
            _log.warning("evening digest: %s has no phone on file — retiring %d staged event(s)",
                         uname, len(ids))
            await session.execute(update(pending_summary_t)
                                  .where(p["id"].in_(ids)).values(processed_at=func.now()))
            no_phone += 1
            continue
        header = f"GI Hub evening summary {now.strftime('%Y-%m-%d')} — {len(items)} update(s)"
        res = await _wa.send_template(
            session, to=number.strip(), template_key="evening_summary",
            variables=[header, _compile_digest(items)], event_key="evening_summary",
            related_table="pending_summary_notifications", created_by="digest-worker")
        if res.get("status") == "sent":
            await session.execute(update(pending_summary_t).where(p["id"].in_(ids)).values(
                processed_at=func.now(), digest_outbox_id=res.get("id")))
            sent += 1
        else:  # outbox row records the error; rows stay pending for tomorrow
            failed += 1
    return {"recipients": len(by_user), "sent": sent, "failed": failed,
            "retired_no_phone": no_phone}


async def digest_loop() -> None:
    """Daemon: fire the evening digest once per day at GI_DIGEST_HOUR (default
    16:00 server-local time — UTC+3 in production). Started from the FastAPI
    lifespan next to the report scheduler; disabled by GI_SCHEDULER=0."""
    import asyncio

    from ..db import SessionLocal  # lazy: keeps service imports cycle-free

    hour = int(os.environ.get("GI_DIGEST_HOUR", "16"))
    minute = int(os.environ.get("GI_DIGEST_MINUTE", "0"))
    _log.info("evening-digest scheduler started (daily %02d:%02d local)", hour, minute)
    while True:
        now = _dt.datetime.now()
        nxt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if nxt <= now:
            nxt += _dt.timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        try:
            async with SessionLocal() as s:
                res = await send_evening_digests(s)
                await s.commit()
            _log.info("evening digest run: %s", res)
        except Exception:  # noqa: BLE001 — one bad run must not kill the loop
            _log.exception("evening digest run failed")
