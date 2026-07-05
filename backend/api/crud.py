"""
backend/api/crud.py — generic read-only router factory.

Given a SQLAlchemy Core Table (from `models.Base.metadata`), builds an APIRouter
with two endpoints:

    GET  {prefix}            -> paginated list  {total, limit, offset, count, items}
    GET  {prefix}/{item_id}  -> a single row by primary key

Design choices:
  * Core Table + result.mappings() is used (not ORM attributes) so columns with
    awkward names — e.g. "Approved By" (a space), "Dia_L" — serialise cleanly by
    their real DB name. JSON keys are the true column names.
  * Rows are ordered by the explicit primary key (architecture rule #2 — never
    rely on rowid/physical order, which does not exist on Postgres).
  * Site scoping (rule #4): entities with a Site_ID column accept ?site_id=.
  * Binary (LargeBinary/BYTEA) columns are dropped from the response, and any
    obviously-secret column names are scrubbed as a guardrail.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import (
    DateTime, Integer, LargeBinary, delete, func, insert, select, update,
)
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user, resolve_site_param, site_scope
from .db import get_session

# Case-insensitive substrings that mark a column as secret; never serialised.
_SENSITIVE = (
    "password", "passwd", "totp", "secret", "token", "hash", "salt",
    "api_key", "apikey", "private_key",
)


def _is_sensitive(name: str) -> bool:
    n = name.lower()
    return any(s in n for s in _SENSITIVE)


def make_read_router(table, *, prefix: str, tag: str, id_col: str,
                     site_col: Optional[str] = None,
                     writable: bool = False, write_dep=None) -> APIRouter:
    """Reads are open to any authenticated user (the router is included behind
    get_current_user in main). Writes (when `writable`) are additionally gated
    by `write_dep` — a role dependency (e.g. require_level(3)) so a low-privilege
    user cannot mutate master data via the API even though the nav hides it."""
    # Columns safe to emit: everything except binary blobs and secret-named cols.
    out_cols = [
        c for c in table.columns
        if not isinstance(c.type, LargeBinary) and not _is_sensitive(c.name)
    ]
    if id_col not in table.c:
        raise ValueError(f"{table.name}: id_col {id_col!r} not a column")
    id_column = table.c[id_col]
    id_is_int = isinstance(id_column.type, Integer)
    site_column = table.c[site_col] if site_col else None

    # Columns a client may set (writes): non-blob, non-secret. Timestamp columns
    # named created_at / updated_at are auto-managed below.
    writable_names = {
        c.name for c in table.columns
        if not isinstance(c.type, LargeBinary) and not _is_sensitive(c.name)
    }
    created_col = "created_at" if "created_at" in table.c else None
    updated_col = "updated_at" if "updated_at" in table.c else None

    router = APIRouter(prefix=prefix, tags=[tag])

    def _coerce_id(raw: str):
        if id_is_int:
            try:
                return int(raw)
            except (TypeError, ValueError):
                raise HTTPException(422, f"{id_col} must be an integer")
        return raw

    @router.get("", summary=f"List {tag}")
    async def list_items(
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        site_id: Optional[str] = Query(
            None,
            description=(f"Filter by {site_col}" if site_col
                         else "(this entity has no site scoping)"),
        ),
        user: dict = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
    ):
        base = select(*out_cols)
        cnt = select(func.count()).select_from(table)
        if site_column is not None:
            # Site scoping: below logistics the filter is forced to the user's
            # own site (403 if they ask for another; '' = no site → no rows).
            site_id = resolve_site_param(user, site_id)
            if site_id == "":
                return {"total": 0, "limit": limit, "offset": offset,
                        "count": 0, "items": []}
            if site_id is not None:
                base = base.where(site_column == site_id)
                cnt = cnt.where(site_column == site_id)
        base = base.order_by(id_column).limit(limit).offset(offset)

        items = [dict(m) for m in (await session.execute(base)).mappings().all()]
        total = (await session.execute(cnt)).scalar_one()
        return {"total": total, "limit": limit, "offset": offset,
                "count": len(items), "items": items}

    @router.get("/{item_id}", summary=f"Get one {tag} by {id_col}")
    async def get_item(item_id: str,
                       user: dict = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
        stmt = select(*out_cols).where(id_column == _coerce_id(item_id))
        row = (await session.execute(stmt)).mappings().first()
        if row is None:
            raise HTTPException(404, f"{tag} {item_id!r} not found")
        if site_column is not None:
            # A scoped user gets 404 (not 403) for another site's row — the
            # response must not leak that the id exists.
            scope = site_scope(user)
            if scope is not None:
                row_site = (row.get(site_col) or "").strip()
                if scope == "" or row_site != scope:
                    raise HTTPException(404, f"{tag} {item_id!r} not found")
        return dict(row)

    if not writable:
        return router

    # ---- writes (only for entities flagged writable) ------------------------
    # Role guard applied to every mutating route (create/update/delete).
    _wguard = [Depends(write_dep)] if write_dep is not None else []

    def _clean_body(body: dict) -> dict:
        if not isinstance(body, dict):
            raise HTTPException(422, "request body must be a JSON object")
        cleaned = {}
        for k, v in body.items():
            if k not in table.c:
                raise HTTPException(422, f"unknown column: {k!r}")
            if k not in writable_names:
                raise HTTPException(422, f"column not writable: {k!r}")
            if id_is_int and k == id_col:
                continue          # ignore client-supplied serial PK
            cleaned[k] = v
        return cleaned

    @router.post("", status_code=201, dependencies=_wguard, summary=f"Create {tag}")
    async def create_item(body: dict = Body(...),
                          session: AsyncSession = Depends(get_session)):
        data = _clean_body(body)
        if created_col and created_col not in data:
            data[created_col] = func.now()
        if updated_col and updated_col not in data:
            data[updated_col] = func.now()
        try:
            row = (await session.execute(
                insert(table).values(**data).returning(*out_cols))).mappings().first()
            await session.commit()
        except (IntegrityError, DataError) as e:
            await session.rollback()
            raise HTTPException(400, f"{type(e).__name__}: {e.orig}")
        return dict(row)

    @router.put("/{item_id}", dependencies=_wguard, summary=f"Update {tag} by {id_col}")
    async def update_item(item_id: str, body: dict = Body(...),
                          session: AsyncSession = Depends(get_session)):
        data = _clean_body(body)
        if not data:
            raise HTTPException(422, "no writable fields provided")
        if updated_col:
            data[updated_col] = func.now()
        try:
            row = (await session.execute(
                update(table).where(id_column == _coerce_id(item_id))
                .values(**data).returning(*out_cols))).mappings().first()
            if row is None:
                await session.rollback()
                raise HTTPException(404, f"{tag} {item_id!r} not found")
            await session.commit()
        except (IntegrityError, DataError) as e:
            await session.rollback()
            raise HTTPException(400, f"{type(e).__name__}: {e.orig}")
        return dict(row)

    @router.delete("/{item_id}", dependencies=_wguard, summary=f"Delete {tag} by {id_col}")
    async def delete_item(item_id: str,
                          session: AsyncSession = Depends(get_session)):
        try:
            res = await session.execute(
                delete(table).where(id_column == _coerce_id(item_id)))
            if res.rowcount == 0:
                await session.rollback()
                raise HTTPException(404, f"{tag} {item_id!r} not found")
            await session.commit()
        except (IntegrityError, DataError) as e:
            await session.rollback()
            raise HTTPException(400, f"{type(e).__name__}: {e.orig}")
        return {"deleted": item_id, "rowcount": res.rowcount}

    return router
