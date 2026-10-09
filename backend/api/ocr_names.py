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

from fastapi import APIRouter, Depends, HTTPException, Query
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
    # Phase 22e: the site (for its tanks and its Day/Night preparers), the
    # paper's tank cells, and the date the store keeper settled on
    site_id: Optional[str] = None
    tanks: list[Optional[str]] = Field(default_factory=list, max_length=200)
    date_iso: Optional[str] = Field(None, max_length=10)


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


async def site_tanks(session: AsyncSession, site: str) -> tuple[list[str], dict[str, str]]:
    """The official tank tags of the site (`sme_equipment`) and the spellings
    already mapped to them (`sme_tank_alias` — the workbook's and the ones a
    store keeper accepted)."""
    tags = [r[0] for r in (await session.execute(text(
        'SELECT DISTINCT "Equipment_Tag_No" FROM sme_equipment WHERE "Site_ID" = :s '
        "AND COALESCE(\"Equipment_Tag_No\", '') <> ''"), {"s": site})).all()]
    aliases = {r[0]: r[1] for r in (await session.execute(text(
        'SELECT alias_norm, "Equipment_Tag_No" FROM sme_tank_alias WHERE "Site_ID" = :s '
        "AND status = 'mapped' AND \"Equipment_Tag_No\" IS NOT NULL"), {"s": site})).all()}
    return tags, aliases


@router.post("/paper-check", summary="The paper's date, shift, preparer, work types and tanks, checked")
async def paper_check(body: PaperIn, user: dict = Depends(_SK),
                      session: AsyncSession = Depends(get_session)):
    """Phase 21d follow-up, extended in 22e. Never changes anything by itself:
    an implausible date comes back with the dates it most likely is; the shift
    is read from the date box ("(Night)"; NO mark = Day — ruling Q22-14) and
    gives the site's preparer for that date (Q22-16); each tank cell is matched
    against the site's tank tags (Q22-17), a ditto taking the tank above."""
    import datetime as _dtm

    from .services import preparers as PREP
    d = PF.check_paper_date(body.date_text)
    out: dict = {"date": d, "work_types": PF.fill_work_types(body.work_types)}
    if not body.site_id and not user.get("site_id"):
        return out
    site = resolve_site_param(user, body.site_id) or ""
    shift, marked = PREP.shift_of(d.get("shift") if d else None)
    day = None
    for iso in (body.date_iso, d.get("date_iso") if d else None):
        try:
            day = _dtm.date.fromisoformat(iso) if iso else None
        except ValueError:
            day = None
        if day:
            break
    out["shift"] = {"shift": shift, "marked": marked}
    out["prepared_by"] = await PREP.preparer_for(session, site, day, shift)
    # Phase 23c (Q23-1): a one-day cover is offered beside the regular name
    out["covers"] = PREP.covers_on(await PREP.history(session, site), day, shift)
    if body.tanks:
        tags, aliases = await site_tanks(session, site)
        out["tanks"] = PF.fill_dittos([PF.match_tank(t, tags, aliases) for t in body.tanks])
        out["tank_tags"] = sorted(tags)
    return out


PAGE_TANK_DAYS = 7


async def recent_tanks(session: AsyncSession, site: str, day, days: int = PAGE_TANK_DAYS) -> list[dict]:
    """The tanks the site's consumption lines name in the `days` days BEFORE
    `day` (the paper's own date is not included — ruling Q23-2), busiest
    first, `others` last. Spellings are folded case-insensitively so the
    workbook's `Others` and `others` are one choice."""
    import datetime as _dtm
    lo = (day - _dtm.timedelta(days=days)).isoformat()
    hi = day.isoformat()
    rows = (await session.execute(text(
        'SELECT TRIM("Tank_No") AS t, count(*) AS n FROM consumption '
        'WHERE "Site_ID" = :s AND COALESCE(TRIM("Tank_No"), \'\') <> \'\' '
        'AND LEFT("Date", 10) >= :lo AND LEFT("Date", 10) < :hi '
        'GROUP BY 1'), {"s": site, "lo": lo, "hi": hi})).all()
    folded: dict[str, dict] = {}
    for t, n in rows:
        key = t.lower()
        cur = folded.setdefault(key, {"tag": t, "lines": 0})
        cur["lines"] += int(n)
        if t != key and cur["tag"] == key:   # prefer the workbook's own casing
            cur["tag"] = t
    out = sorted(folded.values(), key=lambda r: (r["tag"].lower() == "others", -r["lines"], r["tag"]))
    return out


@router.get("/page-tanks", summary="Tanks for the 'tank for this whole page' choice (Q23-2)")
async def page_tanks(date: str = Query(..., max_length=10, description="the paper's date, YYYY-MM-DD"),
                     site_id: Optional[str] = None,
                     user: dict = Depends(_SK),
                     session: AsyncSession = Depends(get_session)):
    """Phase 23b (ruling Q23-2). The reader garbles the FIRST tank cell of a
    page and every ditto below inherits it (tank right 0.42 on the 8 photos).
    The store keeper sets the tank once for the page instead, chosen from the
    tanks the site actually used in the 7 days before the paper's date, then
    the learned tanks. Read-only: filling the rows happens on the page, and
    only ditto / blank / unknown rows are filled."""
    import datetime as _dtm
    try:
        day = _dtm.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(422, "date must be YYYY-MM-DD")
    site = resolve_site_param(user, site_id) or ""
    if not site:
        raise HTTPException(422, "site_id is required for a global role")
    recent = await recent_tanks(session, site, day)
    tags, aliases = await site_tanks(session, site)
    seen = {r["tag"].lower() for r in recent}
    learned = sorted({t for t in [*aliases.values(), *tags] if t and t.lower() not in seen})
    return {"site_id": site, "from": (day - _dtm.timedelta(days=PAGE_TANK_DAYS)).isoformat(),
            "to": (day - _dtm.timedelta(days=1)).isoformat(),
            "recent": recent, "learned": learned}


class TankLearnIn(BaseModel):
    written: list[str] = Field(..., min_length=1, max_length=200)
    tag: str = Field(..., min_length=1, max_length=80)
    site_id: Optional[str] = None


_TANK_UPSERT = """
    INSERT INTO sme_tank_alias ("Site_ID", alias_raw, alias_norm, "Equipment_Tag_No",
                                status, resolved_by, resolved_at)
    VALUES (:s, :raw, :n, :t, 'mapped', :u, CURRENT_TIMESTAMP)
    ON CONFLICT ("Site_ID", alias_norm) DO UPDATE SET "Equipment_Tag_No" = :t,
        status = 'mapped', resolved_by = :u, resolved_at = CURRENT_TIMESTAMP"""


@router.post("/tank-alias", summary="Learn what written tank number(s) mean at this site")
async def learn_tank(body: TankLearnIn, user: dict = Depends(_SK),
                     session: AsyncSession = Depends(get_session)):
    """Phase 22e (Q22-17): Accept on a gold tank teaches the spelling, in the
    same table the Excel sync's tank aliases live in. Several spellings at once
    — the store keeper ticks every row with that tank (or its ditto marks)."""
    import re as _re
    site = await _site(user, body.site_id)
    tags, _ = await site_tanks(session, site)
    if body.tag not in tags:
        raise HTTPException(422, f"{body.tag} is not one of this site's tanks")
    learned = []
    for w in sorted({x.strip() for x in body.written if x and x.strip()}):
        if not _re.search(r"[A-Za-z0-9]", w):
            continue                                 # a ditto mark teaches nothing
        norm = PF.tank_key(w)
        if norm == PF.tank_norm(body.tag):
            continue                                 # already the tag itself
        await session.execute(text(_TANK_UPSERT), {"s": site, "raw": w[:80], "n": norm,
                                                   "t": body.tag, "u": user["username"]})
        learned.append(norm)
    if learned:
        await write_audit(session, user["username"], "OCR_TANK_LEARN", "sme_tank_alias",
                          f"site={site} {learned} → {body.tag}")
    await session.commit()
    return {"learned": learned, "tag": body.tag}


class CompareRow(BaseModel):
    SAP_Code: Optional[str] = None
    quantity: Optional[float] = None
    tank: Optional[str] = None
    work_type: Optional[str] = None
    issued_to: Optional[str] = None


class CompareIn(BaseModel):
    site_id: Optional[str] = None
    date: str = Field(..., min_length=10, max_length=10)
    prepared_by: Optional[str] = Field(None, max_length=120)
    rows: list[CompareRow] = Field(default_factory=list, max_length=200)


@router.post("/compare", summary="Is this paper already in the workbook? Line by line")
async def compare(body: CompareIn, user: dict = Depends(_SK),
                  session: AsyncSession = Depends(get_session)):
    """Phase 22e (ruling Q22-19): a paper already typed into the workbook is
    COMPARED, never staged again — staging it as well would take the stock
    down twice. The workbook's rows of that date and preparer (Surface Shields
    excluded — never on these papers) are paired one to one with the paper's,
    so two identical lines (same item, tank, quantity, worker) pair with two
    rows, never one."""
    from .ai import paper_compare as PC
    site = await _site(user, body.site_id)
    wb = await PC.workbook_rows(session, site, body.date, body.prepared_by)
    res = PC.align([r.model_dump() for r in body.rows], wb)
    return {"site_id": site, "date": body.date, "prepared_by": body.prepared_by,
            "in_workbook": len(wb), **res}


class PreparersIn(BaseModel):
    site_id: str = Field(..., min_length=1, max_length=40)
    history: list[dict] = Field(..., max_length=50)


@router.get("/preparers", summary="Who prepares the site's consumption papers, Day and Night")
async def get_preparers(site_id: Optional[str] = None,
                        user: dict = Depends(require_roles("store_keeper", "hod", "admin")),
                        session: AsyncSession = Depends(get_session)):
    from .services import preparers as PREP
    site = resolve_site_param(user, site_id)
    if not site:
        raise HTTPException(422, "choose a site")
    return {"site_id": site, "history": await PREP.history(session, site)}


@router.put("/preparers", summary="Set the Day / Night preparers (from a date) — Admin, the site's HOD")
async def put_preparers(body: PreparersIn, user: dict = Depends(require_roles("hod", "admin")),
                        session: AsyncSession = Depends(get_session)):
    """Ruling Q22-16: a HISTORY per site, so a paper keeps the names in force on
    its own date when the shift changes hands."""
    import json as _json

    from .services import preparers as PREP
    site = resolve_site_param(user, body.site_id)
    try:
        rows = await PREP.save(session, site, body.history)
    except ValueError as e:
        raise HTTPException(422, str(e))
    await write_audit(session, user["username"], "PREPARERS_SET", "app_settings",
                      f"site={site} {_json.dumps(rows)}")
    await session.commit()
    return {"site_id": site, "history": rows}


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
