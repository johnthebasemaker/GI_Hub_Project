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

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Integer, LargeBinary, func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
                     site_col: Optional[str] = None) -> APIRouter:
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
        session: AsyncSession = Depends(get_session),
    ):
        base = select(*out_cols)
        cnt = select(func.count()).select_from(table)
        if site_column is not None and site_id is not None:
            base = base.where(site_column == site_id)
            cnt = cnt.where(site_column == site_id)
        base = base.order_by(id_column).limit(limit).offset(offset)

        items = [dict(m) for m in (await session.execute(base)).mappings().all()]
        total = (await session.execute(cnt)).scalar_one()
        return {"total": total, "limit": limit, "offset": offset,
                "count": len(items), "items": items}

    @router.get("/{item_id}", summary=f"Get one {tag} by {id_col}")
    async def get_item(item_id: str,
                       session: AsyncSession = Depends(get_session)):
        stmt = select(*out_cols).where(id_column == _coerce_id(item_id))
        row = (await session.execute(stmt)).mappings().first()
        if row is None:
            raise HTTPException(404, f"{tag} {item_id!r} not found")
        return dict(row)

    return router
