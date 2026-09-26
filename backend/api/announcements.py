"""
backend/api/announcements.py — Phase 14d routes: "What's new" and the admin's
publish / schedule / retract controls, plus the admin-only tutorial staleness
report (ruling Q14-14).

The logic lives in `services/announcements.py` and
`services/tutorial_staleness.py`; this file is the HTTP surface and its guards.

  any signed-in user   GET  /announcements/whats-new       — unread, for MY role/site
                       POST /announcements/read            — { ids: [...] }
  admin (exact)        GET  /announcements/admin
                       POST /announcements/admin/sync      — load docs/announcements/*.yaml
                       POST /announcements/admin/{key}/publish   — { at?: ISO time }
                       POST /announcements/admin/{key}/retract
                       GET  /announcements/admin/tutorials       — last staleness result
                       POST /announcements/admin/tutorials/check — run it now (needs git)

⚠️ EXACT-LOCKED TO ADMIN, NOT `require_level(3)`. An auditor reads everything
and changes nothing (rule 7); logistics is level 3 too. Publishing a sentence
to every user in the company is an admin act.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user, require_roles
from .db import get_session
from .services import announcements as A
from .services import tutorial_staleness as T

router = APIRouter(prefix="/announcements", tags=["announcements"])
_ADMIN = Depends(require_roles("admin"))


class ReadIn(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=200)


class PublishIn(BaseModel):
    at: Optional[datetime] = None


@router.get("/whats-new", summary="Published announcements for the caller's role")
async def whats_new(include_read: bool = Query(False),
                    user: dict = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
    async with session.begin():            # release_due() may publish
        return {"items": await A.whats_new(session, user=user, include_read=include_read)}


@router.post("/read", summary="Mark announcements as seen")
async def mark_read(body: ReadIn = Body(...), user: dict = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
    async with session.begin():
        return {"marked": await A.mark_read(session, user=user, ids=body.ids)}


@router.get("/admin", dependencies=[_ADMIN], summary="Every announcement and its state")
async def admin_list(session: AsyncSession = Depends(get_session)):
    async with session.begin():
        return {"items": await A.admin_list(session)}


@router.post("/admin/sync", dependencies=[_ADMIN],
             summary="Load docs/announcements/*.yaml as drafts")
async def admin_sync(user: dict = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)):
    async with session.begin():
        return await A.sync(session, username=user["username"])


@router.post("/admin/{key}/publish", dependencies=[_ADMIN],
             summary="Publish now, or schedule with `at`")
async def admin_publish(key: str, body: PublishIn = Body(default=PublishIn()),
                        user: dict = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)):
    async with session.begin():
        return await A.publish(session, key=key, username=user["username"], at=body.at)


@router.post("/admin/{key}/retract", dependencies=[_ADMIN], summary="Withdraw it")
async def admin_retract(key: str, user: dict = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)):
    async with session.begin():
        return await A.retract(session, key=key, username=user["username"])


@router.get("/admin/tutorials", dependencies=[_ADMIN],
            summary="The last tutorial-staleness result (admin only — Q14-14)")
async def admin_tutorials(session: AsyncSession = Depends(get_session)):
    return {"result": await T.last(session), "git_here": T.git_available()}


@router.post("/admin/tutorials/check", dependencies=[_ADMIN],
             summary="Run the tutorial-staleness check now")
async def admin_tutorials_check(session: AsyncSession = Depends(get_session)):
    from sqlalchemy import text
    announced = {r[0]: r[1] for r in (await session.execute(text(
        "SELECT tutorial_module, key FROM feature_announcements "
        "WHERE rerender AND tutorial_module IS NOT NULL AND status <> 'retracted'"))).all()}
    await session.rollback()
    r = T.scan(announced=announced)
    if not r.get("available"):
        # Nothing new to store; the last result from the deploy host stands.
        return {"result": await T.last(session), "ran": False, "reason": r["reason"]}
    async with session.begin():
        rang = await T.record(session, r)
    return {"result": await T.last(session), "ran": True, "bell": rang}
