"""
backend/api/material_requests.py — Requests & Pending (Phase 22d, rulings
Q22-12/13).

    GET /material-requests     every request line from the Drive folder *Pending
                               Material Follow-up*: requested · received (GI Hub,
                               from the Receipt Log, oldest request first) ·
                               the workbook's own received · pending · PR or not ·
                               age; the lines that need a SAP code; the roll-up's
                               disagreements

    GET    /material-requests/needs-sap   the mapper's list (Phase 23c): lines with
                                         no SAP, grouped by written key, with the
                                         best item-master matches pre-ranked
    POST   /material-requests/sap-map     decide one or many keys: item · catalogue
                                         ("not stocked yet") · not_stock — Admin,
                                         HOD (own site), Logistics; NOT the store
                                         keeper (ruling Q23-4); audited
    DELETE /material-requests/sap-map/{id}  undo a decision; audited

Not to be confused with `/requests` (requests.py): those are inter-site stock
transfer requests made in the app. These are the purchase requests the site
mails, kept in Drive. General items only — Surface Shields are skipped.
"""
from __future__ import annotations

from typing import Optional

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_roles, resolve_site_param, site_scope
from .db import get_session
from .services import requests_sync as R
from .services.ledger import write_audit

router = APIRouter(prefix="/material-requests", tags=["procurement"])

_READERS = require_roles("store_keeper", "hod", "logistics", "admin", "auditor")
# ruling Q23-4: the store keeper reads the list but does not map
_MAPPERS = require_roles("hod", "logistics")
_GI_CODE = re.compile(r"^GI-\d{5,}$")


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


@router.get("/needs-sap", summary="Request lines without a SAP code, grouped (Phase 23c mapper)")
async def needs_sap(site_id: Optional[str] = None, user: dict = Depends(_READERS),
                    session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, site_id)
    out = await R.needs_sap_groups(session, site)
    out["can_map"] = user["role"] in ("admin", "hod", "logistics")
    return out


class MapIn(BaseModel):
    site_id: Optional[str] = None
    keys: list[str] = Field(..., min_length=1, max_length=200)
    decision: Literal["item", "catalogue", "not_stock"]
    SAP_Code: Optional[str] = Field(None, max_length=40)
    Material_Code: Optional[str] = Field(None, max_length=40)
    examples: dict[str, str] = Field(default_factory=dict)


@router.post("/sap-map", summary="Decide what request line(s) without a SAP code mean (Q23-4/5)")
async def map_lines(body: MapIn, user: dict = Depends(_MAPPERS),
                    session: AsyncSession = Depends(get_session)):
    site = (resolve_site_param(user, body.site_id) or "").strip()
    if not site:
        raise HTTPException(422, "site_id is required for a global role")
    keys = sorted({k.strip() for k in body.keys if k.strip()})
    if any(not (k.startswith("code:") or k.startswith("desc:")) for k in keys):
        raise HTTPException(422, "keys come from the needs-SAP list (code:… or desc:…)")
    sap = (body.SAP_Code or "").strip() or None
    code = (body.Material_Code or "").strip().upper() or None
    if body.decision == "item":
        if not sap:
            raise HTTPException(422, "choose the item (its SAP code)")
        ok = (await session.execute(text(
            'SELECT 1 FROM inventory WHERE TRIM("SAP_Code") = :p AND COALESCE("Site_ID", \'\') = :s'),
            {"p": sap, "s": site})).first()
        if not ok:
            raise HTTPException(422, f"SAP {sap} is not in {site}'s item master")
        code = None
    elif body.decision == "catalogue":
        sap = None
        catalogue = await R.catalogue_codes(session)
        for k in keys:
            c = code or (k[5:] if k.startswith("code:") else None)
            if not c:
                raise HTTPException(422, "a line with no GI code needs the catalogue code chosen")
            if catalogue and c not in catalogue:
                raise HTTPException(422, f"{c} is not in the material catalogue")
            if not catalogue and not _GI_CODE.match(c):
                raise HTTPException(422, f"“{c}” is not a GI material code (GI-7000087)")
    else:
        sap, code = None, None
    for k in keys:
        mc = code if body.decision != "catalogue" else (code or (k[5:] if k.startswith("code:") else None))
        await session.execute(text('''
            INSERT INTO request_sap_map ("Site_ID", written_key, written_example, decision,
                                         "SAP_Code", "Material_Code", created_by, updated_by)
            VALUES (:s, :k, :ex, :d, :p, :c, :u, :u)
            ON CONFLICT ("Site_ID", written_key) DO UPDATE SET decision = :d, "SAP_Code" = :p,
                "Material_Code" = :c, updated_by = :u, updated_at = CURRENT_TIMESTAMP,
                written_example = COALESCE(:ex, request_sap_map.written_example)'''),
            {"s": site, "k": k, "ex": (body.examples.get(k) or "")[:200] or None,
             "d": body.decision, "p": sap, "c": mc, "u": user["username"]})
    await write_audit(session, user["username"], "REQUEST_SAP_MAP", "request_sap_map",
                      f"site={site} {body.decision}"
                      + (f" sap={sap}" if sap else "") + (f" code={code}" if code else "")
                      + f" keys={keys}")
    await session.commit()
    return {"site_id": site, "decided": len(keys), "decision": body.decision}


@router.delete("/sap-map/{map_id}", summary="Undo a mapping decision (Phase 23c)")
async def unmap(map_id: int, user: dict = Depends(_MAPPERS),
                session: AsyncSession = Depends(get_session)):
    row = (await session.execute(text("SELECT * FROM request_sap_map WHERE id = :i"),
                                 {"i": map_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "no such mapping")
    own = site_scope(user)
    if own is not None and row["Site_ID"] != own:
        raise HTTPException(403, "that mapping belongs to another site")
    await session.execute(text("DELETE FROM request_sap_map WHERE id = :i"), {"i": map_id})
    await write_audit(session, user["username"], "REQUEST_SAP_UNMAP", "request_sap_map",
                      f"site={row['Site_ID']} key={row['written_key']} was {row['decision']}"
                      + (f" sap={row['SAP_Code']}" if row["SAP_Code"] else ""))
    await session.commit()
    return {"deleted": map_id}
