"""
Parity A1/A4 — the legacy entry-document system + per-site WBS master,
rebuilt for the new stack.

Documents (`entry_attachments`, BLOB-authoritative — the table and 35 legacy
files were already migrated; this module finally gives them endpoints):
  · SK uploads supporting documents (hand-written notes / delivery notes /
    photos) per batch from the Issue / Receive / Return forms.
  · doc_number defaults to DDMMYY of the submission date (legacy rule);
    receipts may override it with a DN number.
  · The admin setting **require_entry_documents** ('1' by default) turns the
    upload into a HARD GATE: Issue / Receipt / Return submissions are refused
    without at least one attached document. ('0' restores the legacy-optional
    behaviour for issue/receipt.)
  · HODs browse everything in the Document Library (legacy HOD TAB 12).

WBS (`wbs_master`, migrated): legacy blocked SK consumption/receipts without
an active WBS *when the site has WBS numbers configured* — same semantics
here: the gate only bites once an HOD adds WBS rows for the site.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user, require_roles, resolve_site_param, site_scope
from .db import get_session
from .services.ledger import _MD, write_audit

router = APIRouter(tags=["entry-docs"])

attachments_t = _MD.tables["entry_attachments"]
wbs_t = _MD.tables["wbs_master"]
settings_t = _MD.tables["app_settings"]

_DOC_TYPES = ("consumption", "receipt", "return")
_MAX_FILE_MB = 15
_ALLOWED_MIME_PREFIXES = ("image/", "application/pdf",
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ── settings-driven gate ──────────────────────────────────────────────────────
async def docs_required(session: AsyncSession) -> bool:
    """require_entry_documents app setting — DEFAULT ON (approved parity plan:
    stricter than legacy, where only Return uploads were mandatory)."""
    v = (await session.execute(select(settings_t.c["value"]).where(
        settings_t.c["key"] == "require_entry_documents"))).scalar_one_or_none()
    return (v if v is not None else "1").strip() != "0"


async def assert_entry_docs(session: AsyncSession, *, doc_type: str,
                            attachment_ids: list[int] | None, username: str) -> list[int]:
    """The submit gate: when required, at least one attachment must exist,
    belong to the submitter, and carry the right doc_type."""
    ids = [int(i) for i in (attachment_ids or [])]
    if not ids:
        if await docs_required(session):
            raise HTTPException(
                422, f"a supporting document (hand-written note / delivery note) "
                     f"must be attached before submitting a {doc_type} entry")
        return []
    rows = (await session.execute(select(
        attachments_t.c["id"], attachments_t.c["doc_type"], attachments_t.c["uploaded_by"]
    ).where(attachments_t.c["id"].in_(ids)))).all()
    found = {r.id for r in rows}
    if missing := [i for i in ids if i not in found]:
        raise HTTPException(422, f"unknown attachment id(s): {missing}")
    for r in rows:
        if r.doc_type != doc_type:
            raise HTTPException(422, f"attachment {r.id} is a {r.doc_type} document, not {doc_type}")
        if r.uploaded_by != username:
            raise HTTPException(403, f"attachment {r.id} was uploaded by someone else")
    return ids


async def link_attachments(session: AsyncSession, ids: list[int], *,
                           entry_table: str, entry_date: str | None) -> None:
    if ids:
        await session.execute(update(attachments_t)
                              .where(attachments_t.c["id"].in_(ids))
                              .values(entry_table=entry_table, entry_date=entry_date))


# ── WBS gate ──────────────────────────────────────────────────────────────────
async def active_wbs(session: AsyncSession, site_id: str) -> list[str]:
    rows = (await session.execute(select(wbs_t.c["WBS_Number"]).where(
        (wbs_t.c["Site_ID"] == site_id) & (wbs_t.c["status"] == "active")
    ).order_by(wbs_t.c["WBS_Number"]))).all()
    return [r[0] for r in rows]


async def assert_wbs(session: AsyncSession, *, site_id: str, wbs: str | None) -> None:
    """Legacy rule (hod_portal.py WBS Manager): once a site has ACTIVE WBS
    numbers, consumption/receipt entries must carry one of them. Sites with
    no WBS configured are unaffected."""
    options = await active_wbs(session, site_id)
    if not options:
        return
    if not (wbs or "").strip():
        raise HTTPException(422, f"site {site_id} requires a WBS Number "
                                 f"({len(options)} active) — pick one on the form")
    if wbs.strip() not in options:
        raise HTTPException(422, f"WBS {wbs!r} is not an active WBS for {site_id}")


# ── attachment endpoints ─────────────────────────────────────────────────────
@router.post("/entry/attachments", status_code=201,
             summary="Upload a supporting document for an entry batch (SK)")
async def upload_attachment(file: UploadFile = File(...),
                            doc_type: str = Form(...),
                            site_id: str = Form(...),
                            doc_number: Optional[str] = Form(None),
                            entry_date: Optional[str] = Form(None),
                            user: dict = Depends(require_roles("store_keeper")),
                            session: AsyncSession = Depends(get_session)):
    if doc_type not in _DOC_TYPES:
        raise HTTPException(422, f"doc_type must be one of {_DOC_TYPES}")
    mime = (file.content_type or "").lower()
    if mime and not any(mime.startswith(p) for p in _ALLOWED_MIME_PREFIXES):
        raise HTTPException(422, "only images, PDFs and XLSX files are accepted")
    blob = await file.read()
    if len(blob) > _MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(413, f"file exceeds {_MAX_FILE_MB} MB")
    if not blob:
        raise HTTPException(422, "empty file")
    scope = site_scope(user)
    site = scope if scope else site_id.strip()
    # legacy default doc number: DDMMYY of the submission date
    doc_no = (doc_number or "").strip() or _dt.date.today().strftime("%d%m%y")
    aid = (await session.execute(insert(attachments_t).values(
        Site_ID=site, doc_type=doc_type, doc_number=doc_no,
        entry_date=(entry_date or None), file_name=file.filename or "document",
        mime_type=mime or None, file_size=len(blob), file_blob=blob,
        uploaded_by=user["username"],
    ).returning(attachments_t.c["id"]))).scalar_one()
    await write_audit(session, user["username"], "ENTRY_DOC_UPLOAD", "entry_attachments",
                      f"#{aid} {doc_type} {doc_no} {file.filename}")
    await session.commit()
    return {"id": aid, "file_name": file.filename, "doc_number": doc_no}


@router.get("/entry/attachments", summary="Browse entry documents (Document Library)")
async def list_attachments(doc_type: Optional[str] = Query(None),
                           site_id: Optional[str] = Query(None),
                           doc_number: Optional[str] = Query(None),
                           date_from: Optional[str] = Query(None),
                           date_to: Optional[str] = Query(None),
                           mine: bool = Query(False, description="only my uploads (any role)"),
                           limit: int = Query(200, le=1000),
                           user: dict = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)):
    # SKs may always list their OWN uploads (the form needs it); the full
    # library is level ≥2 (hod / logistics / admin), site-scoped.
    if not mine and user["level"] < 2:
        raise HTTPException(403, "the Document Library is for HOD/logistics/admin")
    stmt = select(attachments_t.c["id"], attachments_t.c["Site_ID"],
                  attachments_t.c["doc_type"], attachments_t.c["doc_number"],
                  attachments_t.c["entry_table"], attachments_t.c["entry_date"],
                  attachments_t.c["file_name"], attachments_t.c["mime_type"],
                  attachments_t.c["file_size"], attachments_t.c["uploaded_by"],
                  attachments_t.c["uploaded_at"])
    if mine:
        stmt = stmt.where(attachments_t.c["uploaded_by"] == user["username"])
    else:
        site = resolve_site_param(user, site_id)
        if site == "":
            return {"items": []}
        if site:
            stmt = stmt.where(attachments_t.c["Site_ID"] == site)
    if doc_type:
        stmt = stmt.where(attachments_t.c["doc_type"] == doc_type)
    if doc_number:
        stmt = stmt.where(attachments_t.c["doc_number"].ilike(f"%{doc_number.strip()}%"))
    if date_from:
        stmt = stmt.where(attachments_t.c["uploaded_at"] >= date_from)
    if date_to:
        stmt = stmt.where(attachments_t.c["uploaded_at"] < date_to + " 23:59:59")
    rows = (await session.execute(
        stmt.order_by(attachments_t.c["id"].desc()).limit(limit))).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.get("/entry/attachments/{aid}/download", summary="Download / preview one document")
async def download_attachment(aid: int, inline: bool = Query(False),
                              user: dict = Depends(get_current_user),
                              session: AsyncSession = Depends(get_session)):
    row = (await session.execute(select(
        attachments_t.c["Site_ID"], attachments_t.c["file_name"],
        attachments_t.c["mime_type"], attachments_t.c["file_blob"],
        attachments_t.c["uploaded_by"]
    ).where(attachments_t.c["id"] == aid))).first()
    if row is None:
        raise HTTPException(404, "no such document")
    if user["level"] < 2 and row.uploaded_by != user["username"]:
        raise HTTPException(403, "not your document")
    scope = site_scope(user)
    if scope is not None and scope and row.Site_ID != scope and row.uploaded_by != user["username"]:
        raise HTTPException(403, "document belongs to another site")
    import io
    disp = "inline" if inline else "attachment"
    return StreamingResponse(
        io.BytesIO(row.file_blob or b""),
        media_type=row.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'{disp}; filename="{row.file_name}"'})


@router.delete("/entry/attachments/{aid}", summary="Remove an UNLINKED upload (uploader only)")
async def delete_attachment(aid: int, user: dict = Depends(get_current_user),
                            session: AsyncSession = Depends(get_session)):
    row = (await session.execute(select(
        attachments_t.c["uploaded_by"], attachments_t.c["entry_table"]
    ).where(attachments_t.c["id"] == aid))).first()
    if row is None:
        raise HTTPException(404, "no such document")
    if row.uploaded_by != user["username"] and user["level"] < 4:
        raise HTTPException(403, "only the uploader can remove it")
    if row.entry_table:
        raise HTTPException(409, "already linked to a submitted entry — cannot remove")
    await session.execute(delete(attachments_t).where(attachments_t.c["id"] == aid))
    await session.commit()
    return {"deleted": aid}


# ── WBS endpoints ────────────────────────────────────────────────────────────
@router.get("/entry/wbs", summary="Active WBS numbers for a site (entry-form options)")
async def wbs_options(site_id: str = Query(...),
                      user: dict = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)):
    scope = site_scope(user)
    site = scope if scope else site_id
    return {"site_id": site, "wbs": await active_wbs(session, site or "")}


class WbsIn(BaseModel):
    WBS_Number: str
    Description: Optional[str] = None
    site_id: Optional[str] = None


@router.get("/hod/site-config/wbs", summary="All WBS rows for the site (HOD manager)")
async def wbs_all(site_id: Optional[str] = Query(None),
                  user: dict = Depends(require_roles("hod")),
                  session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, site_id)
    stmt = select(wbs_t)
    if site:
        stmt = stmt.where(wbs_t.c["Site_ID"] == site)
    rows = (await session.execute(
        stmt.order_by(wbs_t.c["status"], wbs_t.c["WBS_Number"]))).mappings().all()
    return {"items": [{k: v for k, v in dict(r).items() if k != "created_at"}
                      | {"created_at": str(dict(r).get("created_at") or "")} for r in rows]}


@router.post("/hod/site-config/wbs", status_code=201, summary="Add a WBS number (HOD)")
async def wbs_add(body: WbsIn, user: dict = Depends(require_roles("hod")),
                  session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, body.site_id)
    if not site:
        raise HTTPException(422, "site_id required")
    number = body.WBS_Number.strip()
    if not number:
        raise HTTPException(422, "WBS Number cannot be empty")
    exists = (await session.execute(select(wbs_t.c["id"]).where(
        (wbs_t.c["WBS_Number"] == number) & (wbs_t.c["Site_ID"] == site)))).first()
    if exists:
        raise HTTPException(409, f"WBS {number!r} already exists at {site}")
    wid = (await session.execute(insert(wbs_t).values(
        WBS_Number=number, Description=(body.Description or "").strip(),
        Site_ID=site, status="active", created_by=user["username"],
    ).returning(wbs_t.c["id"]))).scalar_one()
    await write_audit(session, user["username"], "WBS_ADD", "wbs_master", f"{number}@{site}")
    await session.commit()
    return {"id": wid, "WBS_Number": number, "Site_ID": site}


@router.patch("/hod/site-config/wbs/{wid}", summary="Open/close a WBS number (HOD)")
async def wbs_status(wid: int, status: str = Query(..., pattern="^(active|closed)$"),
                     user: dict = Depends(require_roles("hod")),
                     session: AsyncSession = Depends(get_session)):
    row = (await session.execute(select(wbs_t.c["Site_ID"]).where(wbs_t.c["id"] == wid))).first()
    if row is None:
        raise HTTPException(404, "no such WBS row")
    scope = site_scope(user)
    if scope is not None and scope and row.Site_ID != scope:
        raise HTTPException(403, "WBS belongs to another site")
    await session.execute(update(wbs_t).where(wbs_t.c["id"] == wid).values(status=status))
    await write_audit(session, user["username"], "WBS_STATUS", "wbs_master", f"#{wid}→{status}")
    await session.commit()
    return {"id": wid, "status": status}
