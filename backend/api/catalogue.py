"""
backend/api/catalogue.py — Materials & equipment catalogue with pictures
(Phase 23d, rulings Q23-5..9).

    GET    /catalogue/materials            the 6,000-code catalogue: search, filter
                                           (has / needs a picture, stocked / not)
    GET    /catalogue/equipment            the site plant & tools list (Q23-9)
    GET    /catalogue/{kind}/{key}         one item: pictures, removed ones (restorable),
                                           and — for a material — the family's pictures
    POST   /catalogue/{kind}/{key}/images  add a picture (JPG/PNG/WebP/HEIC ≤ 10 MB)
    POST   /catalogue/images/{id}/primary  make it the main picture
    DELETE /catalogue/images/{id}          remove (kept, restorable)
    POST   /catalogue/images/{id}/restore  put a removed picture back
    POST   /catalogue/images/{id}/assign   give the same picture to other codes (a family)
    GET    /catalogue/thumbs               {code: thumbnail link} for a list of codes / SAPs
    GET    /catalogue/img/{id}/{size}      the picture (signed link — an <img> cannot
                                           send the sign-in token)

Everybody signed in SEES pictures; Admin, HOD and Logistics CHANGE them (Q23-8),
one set per code shared by every site, every change audited. No web image
search, ever (Q23-6).
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user, require_roles, resolve_site_param
from .db import get_session
from .services import catalogue as CAT
from .services import media as M
from .services.ledger import write_audit

router = APIRouter(prefix="/catalogue", tags=["catalogue"])

_EDITORS = require_roles("hod", "logistics")          # + admin (require_roles adds it)
Kind = Literal["material", "equipment"]


def _img(row: dict) -> dict:
    return {"id": row["id"], "source": row["source"], "is_primary": bool(row["is_primary"]),
            "width": row["width"], "height": row["height"], "caption": row.get("caption"),
            "uploaded_by": row["uploaded_by"], "uploaded_at": str(row["uploaded_at"] or ""),
            "removed_at": str(row["removed_at"]) if row.get("removed_at") else None,
            "thumb": M.url(row["id"], "thumb"), "display": M.url(row["id"], "display"),
            "original": M.url(row["id"], "original")}


async def _exists(session: AsyncSession, kind: str, key: str) -> bool:
    if kind == "material":
        return bool((await session.execute(text(
            'SELECT 1 FROM material_catalog WHERE "Material_Code" = :k UNION ALL '
            'SELECT 1 FROM inventory WHERE UPPER(TRIM("Material_Code")) = :k LIMIT 1'),
            {"k": key})).first())
    return bool((await session.execute(text(
        "SELECT 1 FROM site_equipment WHERE equipment_key = :k LIMIT 1"), {"k": key})).first())


def _norm_key(kind: str, key: str) -> str:
    return key.strip().upper() if kind == "material" else key.strip()


@router.get("/materials", summary="The material catalogue, with pictures (Phase 23d)")
async def materials(q: Optional[str] = Query(None, max_length=80),
                    show: Literal["all", "no_picture", "has_picture", "stocked", "not_stocked"] = "all",
                    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                    user: dict = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
    where = ["c.removed_at IS NULL"]
    args: dict = {"lim": limit, "off": offset}
    if q and q.strip():
        where.append('(c."Material_Code" ILIKE :q OR c.description ILIKE :q OR s.saps ILIKE :q)')
        args["q"] = f"%{q.strip()}%"
    if show == "no_picture":
        where.append("p.id IS NULL")
    elif show == "has_picture":
        where.append("p.id IS NOT NULL")
    elif show == "stocked":
        where.append("s.saps IS NOT NULL")
    elif show == "not_stocked":
        where.append("s.saps IS NULL")
    base = f'''
        FROM material_catalog c
        LEFT JOIN (SELECT UPPER(TRIM("Material_Code")) AS code, string_agg(DISTINCT TRIM("SAP_Code"), ', ') AS saps
                   FROM inventory WHERE COALESCE("Material_Code", '') <> '' GROUP BY 1) s
               ON s.code = c."Material_Code"
        LEFT JOIN item_images p ON p.kind = 'material' AND p.item_key = c."Material_Code"
               AND p.is_primary AND p.removed_at IS NULL
        WHERE {" AND ".join(where)}'''
    total = (await session.execute(text("SELECT count(*) " + base), args)).scalar()
    rows = (await session.execute(text(
        'SELECT c."Material_Code" AS code, c.description, c.uom, c.series, s.saps, p.id AS image_id, '
        "(SELECT count(*) FROM item_images x WHERE x.kind = 'material' AND x.item_key = c.\"Material_Code\" "
        "AND x.removed_at IS NULL) AS pictures " + base +
        ' ORDER BY c."Material_Code" LIMIT :lim OFFSET :off'), args)).mappings().all()
    counts = (await session.execute(text('''
        SELECT count(*) AS total,
               count(*) FILTER (WHERE EXISTS (SELECT 1 FROM item_images i WHERE i.kind = 'material'
                    AND i.item_key = c."Material_Code" AND i.removed_at IS NULL)) AS with_picture
        FROM material_catalog c WHERE c.removed_at IS NULL'''))).mappings().first()
    return {"total": total, "counts": dict(counts or {}),
            "can_edit": user["role"] in ("admin", "hod", "logistics"),
            "items": [{**dict(r), "stocked": bool(r["saps"]),
                       "thumb": M.url(r["image_id"], "thumb") if r["image_id"] else None}
                      for r in rows]}


@router.get("/equipment", summary="The site plant & tools list, with pictures (Q23-9)")
async def equipment(site_id: Optional[str] = None, q: Optional[str] = Query(None, max_length=80),
                    user: dict = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, site_id)
    where, args = ["e.removed_at IS NULL"], {}
    if site:
        where.append('e."Site_ID" = :s')
        args["s"] = site
    if q and q.strip():
        where.append("(e.description ILIKE :q OR e.category ILIKE :q OR e.serials ILIKE :q)")
        args["q"] = f"%{q.strip()}%"
    rows = (await session.execute(text(f'''
        SELECT e.*, p.id AS image_id FROM site_equipment e
        LEFT JOIN item_images p ON p.kind = 'equipment' AND p.item_key = e.equipment_key
             AND p.is_primary AND p.removed_at IS NULL
        WHERE {" AND ".join(where)} ORDER BY e.source_row, e.id'''), args)).mappings().all()
    return {"can_edit": user["role"] in ("admin", "hod", "logistics"),
            "items": [{**{k: v for k, v in dict(r).items() if k not in ("first_seen", "last_seen")},
                       "thumb": M.url(r["image_id"], "thumb") if r["image_id"] else None}
                      for r in rows]}


@router.get("/thumbs", summary="Thumbnail links for a list of GI codes and/or SAP codes")
async def thumbs(codes: Optional[str] = Query(None, max_length=8000),
                 saps: Optional[str] = Query(None, max_length=8000),
                 size: Literal["thumb", "display"] = "thumb",
                 user: dict = Depends(get_current_user),
                 session: AsyncSession = Depends(get_session)):
    cs = [c.strip().upper() for c in (codes or "").split(",") if c.strip()][:300]
    ss = [s.strip() for s in (saps or "").split(",") if s.strip()][:300]
    sap_code: dict[str, str] = {}
    if ss:
        for sap, code in (await session.execute(text(
                'SELECT TRIM("SAP_Code"), UPPER(TRIM("Material_Code")) FROM inventory '
                'WHERE TRIM("SAP_Code") = ANY(:s) AND COALESCE("Material_Code", \'\') <> \'\''),
                {"s": ss})).all():
            sap_code[sap] = code
    want = sorted(set(cs) | set(sap_code.values()))
    pics = {k: i for k, i in (await session.execute(text(
        "SELECT item_key, id FROM item_images WHERE kind = 'material' AND is_primary "
        "AND removed_at IS NULL AND item_key = ANY(:k)"), {"k": want or [""]})).all()}
    return {"codes": {c: M.url(pics[c], size) for c in cs if c in pics},
            "saps": {s: M.url(pics[c], size) for s, c in sap_code.items() if c in pics}}


@router.get("/report", summary="Where the catalogue came from, and what to look at")
async def catalogue_report(user: dict = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)):
    return await CAT.report(session)


@router.get("/img/{image_id}/{size}", summary="A picture (signed link)", include_in_schema=False)
async def image(image_id: int, size: Literal["thumb", "display", "original"],
                e: str = Query(...), t: str = Query(...),
                session: AsyncSession = Depends(get_session)):
    if not M.verify(image_id, size, e, t):
        raise HTTPException(403, "this picture link has expired — reload the page")
    sha = (await session.execute(text("SELECT sha256 FROM item_images WHERE id = :i"),
                                 {"i": image_id})).scalar()
    p = M.path_for(sha, size) if sha else None
    if not p or not p.is_file():
        raise HTTPException(404, "this picture is not on the server")
    return FileResponse(p, media_type=M.media_type(size),
                        headers={"Cache-Control": "private, max-age=86400, immutable"})


@router.get("/{kind}/{key:path}", summary="One item with its pictures")
async def item(kind: Kind, key: str, user: dict = Depends(get_current_user),
               session: AsyncSession = Depends(get_session)):
    k = _norm_key(kind, key)
    if kind == "material":
        info = (await session.execute(text(
            'SELECT "Material_Code" AS code, description, uom, series FROM material_catalog '
            'WHERE "Material_Code" = :k'), {"k": k})).mappings().first()
        stock = (await session.execute(text(
            'SELECT TRIM("SAP_Code") AS sap, "Equipment_Description" AS description, "Site_ID" AS site '
            'FROM inventory WHERE UPPER(TRIM("Material_Code")) = :k'), {"k": k})).mappings().all()
        if not info and not stock:
            raise HTTPException(404, f"{k} is not in the catalogue or the item master")
        info = dict(info) if info else {"code": k, "description": stock[0]["description"]}
        info["stocked"] = [dict(s) for s in stock]
    else:
        row = (await session.execute(text(
            "SELECT * FROM site_equipment WHERE equipment_key = :k ORDER BY id LIMIT 1"),
            {"k": k})).mappings().first()
        if not row:
            raise HTTPException(404, "no such equipment line")
        info = {x: v for x, v in dict(row).items() if x not in ("first_seen", "last_seen")}
    rows = [dict(r) for r in (await session.execute(text(
        "SELECT * FROM item_images WHERE kind = :k AND item_key = :i ORDER BY removed_at NULLS FIRST, "
        "is_primary DESC, uploaded_at, id"), {"k": kind, "i": k})).mappings().all()]
    fam = []
    if kind == "material":
        fam = [{"code": f["code"], "description": f["description"], "image_id": f["image_id"],
                "thumb": M.url(f["image_id"], "thumb")} for f in await CAT.family_suggestions(session, k)]
    return {"kind": kind, "key": k, "item": info, "can_edit": user["role"] in ("admin", "hod", "logistics"),
            "images": [_img(r) for r in rows if not r["removed_at"]],
            "removed": [_img(r) for r in rows if r["removed_at"]][:20],
            "family": fam, "max": CAT.MAX_PER_ITEM}


@router.post("/{kind}/{key:path}/images", status_code=201, summary="Add a picture (Q23-8)")
async def upload(kind: Kind, key: str, file: UploadFile = File(...),
                 user: dict = Depends(_EDITORS), session: AsyncSession = Depends(get_session)):
    k = _norm_key(kind, key)
    if not await _exists(session, kind, k):
        raise HTTPException(404, f"{k} is not in the catalogue")
    data = await file.read(M.MAX_UPLOAD_BYTES + 1)
    import asyncio
    try:
        st = await asyncio.to_thread(M.store, data)
    except M.MediaError as e:
        raise HTTPException(422, str(e))
    try:
        row = await CAT.add_image(session, kind, k, st, source="upload", user=user["username"])
    except ValueError as e:
        raise HTTPException(422, str(e))
    await write_audit(session, user["username"], "CATALOGUE_IMAGE_ADD", "item_images",
                      f"{kind} {k} image={row['id']} sha={st.sha256[:12]}")
    await session.commit()
    return _img(row)


async def _image_row(session: AsyncSession, image_id: int) -> dict:
    row = (await session.execute(text("SELECT * FROM item_images WHERE id = :i"),
                                 {"i": image_id})).mappings().first()
    if not row:
        raise HTTPException(404, "no such picture")
    return dict(row)


@router.post("/images/{image_id}/primary", summary="Make a picture the main one")
async def primary(image_id: int, user: dict = Depends(_EDITORS),
                  session: AsyncSession = Depends(get_session)):
    row = await _image_row(session, image_id)
    if row["removed_at"]:
        raise HTTPException(422, "restore the picture first")
    await session.execute(text(
        "UPDATE item_images SET is_primary = (id = :i) WHERE kind = :k AND item_key = :key "
        "AND removed_at IS NULL"), {"i": image_id, "k": row["kind"], "key": row["item_key"]})
    await write_audit(session, user["username"], "CATALOGUE_IMAGE_PRIMARY", "item_images",
                      f"{row['kind']} {row['item_key']} image={image_id}")
    await session.commit()
    return {"primary": image_id}


@router.delete("/images/{image_id}", summary="Remove a picture (kept — it can be restored)")
async def remove(image_id: int, user: dict = Depends(_EDITORS),
                 session: AsyncSession = Depends(get_session)):
    row = await _image_row(session, image_id)
    if row["removed_at"]:
        return {"removed": image_id}
    await session.execute(text(
        "UPDATE item_images SET removed_at = CURRENT_TIMESTAMP, removed_by = :u, is_primary = false "
        "WHERE id = :i"), {"i": image_id, "u": user["username"]})
    if row["is_primary"]:   # the next picture becomes the main one
        await session.execute(text('''
            UPDATE item_images SET is_primary = true WHERE id = (
                SELECT id FROM item_images WHERE kind = :k AND item_key = :key AND removed_at IS NULL
                ORDER BY uploaded_at, id LIMIT 1)'''), {"k": row["kind"], "key": row["item_key"]})
    await write_audit(session, user["username"], "CATALOGUE_IMAGE_REMOVE", "item_images",
                      f"{row['kind']} {row['item_key']} image={image_id}")
    await session.commit()
    return {"removed": image_id}


@router.post("/images/{image_id}/restore", summary="Put a removed picture back")
async def restore(image_id: int, user: dict = Depends(_EDITORS),
                  session: AsyncSession = Depends(get_session)):
    row = await _image_row(session, image_id)
    if not row["removed_at"]:
        return _img(row)
    live = await CAT.live_images(session, row["kind"], row["item_key"])
    if len(live) >= CAT.MAX_PER_ITEM:
        raise HTTPException(422, f"this item already has {CAT.MAX_PER_ITEM} pictures — remove one first")
    await session.execute(text(
        "UPDATE item_images SET removed_at = NULL, removed_by = NULL, is_primary = :p WHERE id = :i"),
        {"i": image_id, "p": not live})
    await write_audit(session, user["username"], "CATALOGUE_IMAGE_RESTORE", "item_images",
                      f"{row['kind']} {row['item_key']} image={image_id}")
    await session.commit()
    return _img(await _image_row(session, image_id))


class AssignIn(BaseModel):
    codes: list[str] = Field(..., min_length=1, max_length=200)


@router.post("/images/{image_id}/assign", summary="Give this picture to other codes (a family)")
async def assign(image_id: int, body: AssignIn, user: dict = Depends(_EDITORS),
                 session: AsyncSession = Depends(get_session)):
    row = await _image_row(session, image_id)
    if row["kind"] != "material":
        raise HTTPException(422, "only a material picture can be given to other codes")
    st = M.Stored(sha256=row["sha256"], width=row["width"] or 0, height=row["height"] or 0,
                  bytes=row["bytes"] or 0, mime=row["mime"] or "image/jpeg")
    done, skipped = [], []
    for c in sorted({c.strip().upper() for c in body.codes if c.strip()} - {row["item_key"]}):
        if not await _exists(session, "material", c):
            skipped.append({"code": c, "why": "not in the catalogue"})
            continue
        try:
            await CAT.add_image(session, "material", c, st, source="family", user=user["username"])
            done.append(c)
        except ValueError as e:
            skipped.append({"code": c, "why": str(e)})
    if done:
        await write_audit(session, user["username"], "CATALOGUE_IMAGE_ASSIGN", "item_images",
                          f"image={image_id} ({row['item_key']}) → {done}")
    await session.commit()
    return {"assigned": done, "skipped": skipped}
