"""
backend/api/material_requests.py — Requests & Pending (Phase 22d, rulings
Q22-12/13).

    GET /material-requests     every request line from the Drive folder *Pending
                               Material Follow-up*: requested · received (GI Hub,
                               from the Receipt Log, oldest request first) ·
                               the workbook's own received · pending · PR or not ·
                               age; the lines that need a SAP code; the roll-up's
                               disagreements

Not to be confused with `/requests` (requests.py): those are inter-site stock
transfer requests made in the app. These are the purchase requests the site
mails, kept in Drive. General items only — Surface Shields are skipped.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_roles, resolve_site_param
from .db import get_session
from .services import requests_sync as R

router = APIRouter(prefix="/material-requests", tags=["procurement"])

_READERS = require_roles("store_keeper", "hod", "logistics", "admin", "auditor")


@router.get("", summary="Requests & Pending from Drive (Phase 22d)")
async def material_requests(site_id: Optional[str] = None, open_only: bool = False,
                            pr: Optional[str] = None, user: dict = Depends(_READERS),
                            session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, site_id)
    ov = await R.overview(session, site)
    items = ov["items"]
    if open_only:
        items = [i for i in items if i["pending"] > 0]
    if pr == "without":
        items = [i for i in items if i["without_pr"]]
    elif pr == "with":
        items = [i for i in items if not i["without_pr"]]
    totals = {"lines": len(items), "open": sum(1 for i in items if i["pending"] > 0),
              "without_pr_open": sum(1 for i in items if i["pending"] > 0 and i["without_pr"]),
              "differs": sum(1 for i in items if i["differs"])}
    return {"items": items, "totals": totals, "needs_sap": ov["needs_sap"],
            "summary_check": ov["summary_check"]}
