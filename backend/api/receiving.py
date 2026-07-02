"""
backend/api/receiving.py — site-side DN receiving (closes the procurement loop).

A warehouse DN that's in_transit to a site is received here by the site's Store
Keeper: each line becomes a pending_receipts row (status=pending_hod) at the
site, which then flows into the HOD Approvals → Receipts queue (approve →
commit_receipt → ledger). Any authenticated user may receive DNs for their OWN
site; admins (global) may receive any.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user
from .db import get_session
from .services import warehouse as wh

router = APIRouter(prefix="/site", tags=["site receiving"])


def _actor_site(user: dict) -> Optional[str]:
    """A site-bound user's site ('' for global roles like admin -> None)."""
    return user["site_id"] or None


@router.get("/incoming-dns", summary="In-transit DNs headed to a site")
async def incoming_dns(site_id: Optional[str] = None,
                       user: dict = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    # Default to the user's own site; admins (no site) see all unless they filter.
    scope = site_id or _actor_site(user)
    return {"items": await wh.incoming_dns(session, scope)}


@router.get("/incoming-dns/{dn_number}/items", summary="DN line items")
async def dn_items(dn_number: str, user: dict = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session)):
    return {"items": await wh.dn_lines(session, dn_number)}


@router.post("/dns/{dn_number}/receive", status_code=201,
             summary="Receive a DN → stage receipts for HOD approval")
async def receive_dn(dn_number: str, user: dict = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)):
    try:
        async with session.begin():
            res = await wh.stage_dn_receipt(session, username=user["username"],
                                            dn_number=dn_number, actor_site=_actor_site(user))
        if res.get("error"):
            raise HTTPException(409, res["error"])
        return res
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")
