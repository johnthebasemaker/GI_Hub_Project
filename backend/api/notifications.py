"""
backend/api/notifications.py — the in-app notification feed (sidebar bell).

Every route is scoped to the CURRENT user: what they can see is decided by
services/notifications._visible (user-targeted OR a matching role broadcast,
narrowed by site/warehouse). The JWT doesn't carry the warehouse binding, so
we read the live Site_ID / Warehouse_ID from the users table per request.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user
from .db import get_session
from .services import notifications as notif
from .services.ledger import _MD

users_t = _MD.tables["users"]

router = APIRouter(prefix="/notifications", tags=["notifications"])


async def _ctx(user: dict = Depends(get_current_user),
               session: AsyncSession = Depends(get_session)) -> dict:
    """Resolve the recipient context — role from the JWT, site/warehouse from
    the live users row (bindings can change after the token was issued)."""
    row = (await session.execute(select(
        users_t.c["role"], users_t.c["Site_ID"], users_t.c["Warehouse_ID"]
    ).where(users_t.c["username"] == user["username"]))).first()
    return {
        "username": user["username"],
        "role": (row.role if row else user.get("role")),
        "site_id": (row.Site_ID if row else user.get("site_id")) or None,
        "warehouse_id": (row.Warehouse_ID if row else None),
    }


@router.get("", summary="My notifications (newest first)")
async def list_notifications(unread_only: bool = False, limit: int = 50,
                             ctx: dict = Depends(_ctx),
                             session: AsyncSession = Depends(get_session)):
    limit = max(1, min(limit, 200))
    items = await notif.list_for(session, unread_only=unread_only, limit=limit, **ctx)
    return {"items": items}


@router.get("/unread-count", summary="Unread badge count")
async def unread(ctx: dict = Depends(_ctx),
                 session: AsyncSession = Depends(get_session)):
    return {"unread": await notif.unread_count(session, **ctx)}


@router.post("/{notif_id}/read", summary="Mark one notification read")
async def read_one(notif_id: int, ctx: dict = Depends(_ctx),
                   session: AsyncSession = Depends(get_session)):
    # _ctx already opened a read on this (shared) session, so commit rather
    # than open a nested session.begin() (which would raise "already begun").
    ok = await notif.mark_read(session, notif_id=notif_id, **ctx)
    if not ok:
        raise HTTPException(404, "notification not found or not visible to you")
    await session.commit()
    return {"read": True, "id": notif_id}


@router.post("/read-all", summary="Mark all my notifications read")
async def read_all(ctx: dict = Depends(_ctx),
                   session: AsyncSession = Depends(get_session)):
    n = await notif.mark_all_read(session, **ctx)
    await session.commit()
    return {"read": n}
