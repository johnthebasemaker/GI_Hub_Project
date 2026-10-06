"""
backend/api/ocr_names.py — the consumption-paper name matcher and the names it
learned (Phase 21d, rulings Q21-3..5).

    POST   /ai/ocr/consumption-match   written names → auto / suggested / unknown
    POST   /ai/ocr/aliases             the store keeper confirmed what a name means
    GET    /ai/ocr/aliases             what this site has learned
    DELETE /ai/ocr/aliases/{id}        HOD / Admin remove a wrong one (audited)
    POST   /ai/ocr/paper-check         the paper's date (plausible? did you mean…)
                                       and its work types in the workbook's spelling

The review grid calls `consumption-match` after a photo is read (or text is
pasted) and colours each row: green only for an EXACT or LEARNED match, gold
for a suggestion the store keeper must accept (Q21-5), red when nothing is
close. Accepting a gold suggestion, or picking an item by hand, LEARNS it for
this site (Q21-3).

Works in Practice too (rule 17): it is deterministic and reads only the
Practice database's own items and aliases — the paste lane demonstrates it.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .ai import consumption_match as CM
from .ai import paper_fields as PF
from .auth import require_roles, resolve_site_param
from .db import get_session
from .services.ledger import write_audit
from .stock import SQL_SITE_STOCK

router = APIRouter(prefix="/ai/ocr", tags=["ai"])
_SK = require_roles("store_keeper")
_READ = require_roles("store_keeper", "hod")
_DELETE = require_roles("hod")


class MatchIn(BaseModel):
    names: list[str] = Field(..., max_length=200)
    site_id: Optional[str] = None


class PaperIn(BaseModel):
    date_text: Optional[str] = Field(None, max_length=80)
    work_types: list[Optional[str]] = Field(default_factory=list, max_length=200)


class LearnIn(BaseModel):
    written: str = Field(..., min_length=1, max_length=200)
    SAP_Code: str = Field(..., min_length=1, max_length=40)
    site_id: Optional[str] = None


async def _site(user: dict, site_id: Optional[str]) -> str:
    site = resolve_site_param(user, site_id)
    if not site:
        raise HTTPException(422, "choose a site — learned names are kept per site")
    return site


async def _inventory(session: AsyncSession) -> list[dict]:
    return [dict(r) for r in (await session.execute(text(
        'SELECT TRIM("SAP_Code") AS "SAP_Code", "Equipment_Description", "UOM", "Material_Code" '
        'FROM inventory'))).mappings().all()]


async def _stock(session: AsyncSession, site: str) -> dict[str, float]:
    return {str(r[0]).strip(): float(r[1] or 0) for r in (await session.execute(text(
        f'SELECT s."SAP_Code", s."Current_Stock" FROM ({SQL_SITE_STOCK}) s '
        'WHERE s."Site_ID" = :s'), {"s": site})).all()}


async def aliases_for(session: AsyncSession, site: str) -> dict[str, dict]:
    return {r["written_key"]: dict(r) for r in (await session.execute(text(
        'SELECT written_key, "SAP_Code", confirmations FROM ocr_aliases WHERE "Site_ID" = :s'),
        {"s": site})).mappings().all()}


@router.post("/consumption-match", summary="Written product names → auto / suggested / unknown")
async def consumption_match(body: MatchIn, user: dict = Depends(_SK),
                            session: AsyncSession = Depends(get_session)):
    site = await _site(user, body.site_id)
    inv, stock = await _inventory(session), await _stock(session, site)
    al = await aliases_for(session, site)
    return {"site_id": site,
            "matches": [CM.match(n, inv, aliases=al, stock=stock) for n in body.names]}


@router.post("/paper-check", summary="The paper's date and work types, checked")
async def paper_check(body: PaperIn, user: dict = Depends(_SK)):
    """Phase 21d follow-up. Never changes anything by itself: an implausible
    date comes back with the dates it most likely is, and the store keeper
    confirms one before the sheet can be staged."""
    return {"date": PF.check_paper_date(body.date_text),
            "work_types": [PF.norm_work_type(w) for w in body.work_types]}


@router.post("/aliases", summary="Learn what a written name means at this site")
async def learn(body: LearnIn, user: dict = Depends(_SK),
                session: AsyncSession = Depends(get_session)):
    site = await _site(user, body.site_id)
    key = CM.written_key(body.written)
    sap = body.SAP_Code.strip()
    if not key:
        raise HTTPException(422, "nothing to learn from an empty name")
    if not (await session.execute(text('SELECT 1 FROM inventory WHERE TRIM("SAP_Code") = :p'),
                                  {"p": sap})).first():
        raise HTTPException(422, f"SAP {sap} is not in the inventory master")
    row = (await session.execute(text('''
        INSERT INTO ocr_aliases ("Site_ID", written_key, written_example, "SAP_Code",
                                 confirmations, created_by, updated_by)
        VALUES (:s, :k, :w, :p, 1, :u, :u)
        ON CONFLICT ("Site_ID", written_key) DO UPDATE SET
            confirmations = CASE WHEN ocr_aliases."SAP_Code" = EXCLUDED."SAP_Code"
                                 THEN ocr_aliases.confirmations + 1 ELSE 1 END,
            "SAP_Code" = EXCLUDED."SAP_Code", written_example = EXCLUDED.written_example,
            updated_by = EXCLUDED.updated_by, updated_at = CURRENT_TIMESTAMP
        RETURNING id, confirmations'''), {"s": site, "k": key, "w": body.written.strip()[:200],
                                          "p": sap, "u": user["username"]})).mappings().one()
    await write_audit(session, user["username"], "OCR_ALIAS_LEARN", "ocr_aliases",
                      f"site={site} {key!r} → {sap} (x{row['confirmations']})")
    await session.commit()
    return {"id": row["id"], "written_key": key, "SAP_Code": sap,
            "confirmations": row["confirmations"]}


@router.get("/aliases", summary="The names this site has learned")
async def list_aliases(site_id: Optional[str] = None, user: dict = Depends(_READ),
                       session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, site_id)
    w, p = ('WHERE a."Site_ID" = :s', {"s": site}) if site else ("", {})
    rows = (await session.execute(text(f'''
        SELECT a.id, a."Site_ID", a.written_key, a.written_example, a."SAP_Code",
               COALESCE(i."Equipment_Description", '') AS description, a.confirmations,
               a.updated_by, a.updated_at
        FROM ocr_aliases a
        LEFT JOIN LATERAL (SELECT x."Equipment_Description" FROM inventory x
                           WHERE TRIM(x."SAP_Code") = a."SAP_Code" LIMIT 1) i ON TRUE
        {w} ORDER BY a."Site_ID", a.written_key'''), p)).mappings().all()
    return {"items": [dict(r, updated_at=str(r["updated_at"] or "")[:16]) for r in rows]}


@router.delete("/aliases/{alias_id}", summary="Remove a wrongly learned name (HOD / Admin)")
async def delete_alias(alias_id: int, user: dict = Depends(_DELETE),
                       session: AsyncSession = Depends(get_session)):
    row = (await session.execute(text(
        'SELECT "Site_ID", written_key, "SAP_Code" FROM ocr_aliases WHERE id = :i'),
        {"i": alias_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "no such learned name")
    if user.get("role") != "admin" and resolve_site_param(user, row["Site_ID"]) != row["Site_ID"]:
        raise HTTPException(404, "no such learned name")
    await session.execute(text("DELETE FROM ocr_aliases WHERE id = :i"), {"i": alias_id})
    await write_audit(session, user["username"], "OCR_ALIAS_DELETE", "ocr_aliases",
                      f"site={row['Site_ID']} {row['written_key']!r} → {row['SAP_Code']}")
    await session.commit()
    return {"deleted": alias_id}
