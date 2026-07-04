"""
backend/api/admin.py — Admin console: user management + audit-log viewer.

All routes require role level 4 (admin only). User management ports the old
app's auth.py helpers (add_user / reset_password / delete_user + the admin
2FA reset) — bcrypt hashing, role validation, and the last-admin lockout
guard. Secrets (password_hash, totp_secret) are NEVER returned by any route.
Every mutation is written to system_audit_log.

The credential table `users` is deliberately NOT exposed via the generic CRUD
router (isolation rule); this module is the one narrow, admin-gated seam.
"""
from __future__ import annotations

from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import ROLE_META, require_level
from .db import get_session
from .services.ledger import _MD, write_audit  # reflected metadata + audit writer

users_t = _MD.tables["users"]
audit_t = _MD.tables["system_audit_log"]
inventory_t = _MD.tables["inventory"]

# Ledger/movement tables that reference inventory by SAP_Code — an item with any
# of these rows must NOT be deleted (it would orphan history / break identity math).
_SAP_REFS = [_MD.tables[t] for t in (
    "receipts", "consumption", "returns", "lots",
    "pending_receipts", "pending_issues", "pending_returns", "pr_master")]

MIN_PW = 6  # minimum password length for create / reset

router = APIRouter(prefix="/admin", tags=["admin"],
                   dependencies=[Depends(require_level(4))])


# --- models ------------------------------------------------------------------
class CreateUserIn(BaseModel):
    username: str
    password: str
    role: str
    site_id: Optional[str] = None
    warehouse_id: Optional[str] = None
    phone_number: Optional[str] = None


class UpdateUserIn(BaseModel):
    # Every field optional: None = leave unchanged; "" = clear to NULL.
    role: Optional[str] = None
    site_id: Optional[str] = None
    warehouse_id: Optional[str] = None
    phone_number: Optional[str] = None


class PasswordIn(BaseModel):
    password: str


class InventoryCreateIn(BaseModel):
    SAP_Code: str
    Equipment_Description: Optional[str] = None
    Material_Code: Optional[str] = None
    Category: Optional[str] = None
    UOM: Optional[str] = None
    Minimum_Qty: Optional[float] = None
    Site_ID: Optional[str] = None
    Expiry_Date: Optional[str] = None
    Unit_Cost: Optional[float] = None
    Opening_Stock: Optional[float] = None


class InventoryUpdateIn(BaseModel):
    # None = leave unchanged.
    Equipment_Description: Optional[str] = None
    Material_Code: Optional[str] = None
    Category: Optional[str] = None
    UOM: Optional[str] = None
    Minimum_Qty: Optional[float] = None
    Site_ID: Optional[str] = None
    Expiry_Date: Optional[str] = None
    Unit_Cost: Optional[float] = None
    Opening_Stock: Optional[float] = None


# --- helpers -----------------------------------------------------------------
_USER_COLS = (
    users_t.c["username"], users_t.c["role"], users_t.c["Site_ID"],
    users_t.c["Warehouse_ID"], users_t.c["Phone_Number"],
    users_t.c["created_at"], users_t.c["totp_enabled"],
)


def _public(row) -> dict:
    """Shape a user row for the API — secrets are never included."""
    meta = ROLE_META.get(row.role, {"label": row.role, "level": 0})
    return {
        "username": row.username, "role": row.role,
        "label": meta["label"], "level": meta["level"],
        "Site_ID": row.Site_ID, "Warehouse_ID": row.Warehouse_ID,
        "Phone_Number": row.Phone_Number, "created_at": row.created_at,
        "totp_enabled": bool(row.totp_enabled),
    }


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


async def _get_user(session: AsyncSession, username: str):
    return (await session.execute(
        select(*_USER_COLS).where(users_t.c["username"] == username))).first()


async def _admin_count(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(users_t)
            .where(users_t.c["role"] == "admin"))).scalar_one()


# --- roles -------------------------------------------------------------------
@router.get("/roles", summary="Assignable roles (for create/edit dropdowns)")
async def roles():
    return {"roles": [
        {"value": k, "label": v["label"], "level": v["level"]}
        for k, v in sorted(ROLE_META.items(), key=lambda kv: -kv[1]["level"])
    ]}


# --- users -------------------------------------------------------------------
@router.get("/users", summary="List users (no secrets)")
async def list_users(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(*_USER_COLS).order_by(users_t.c["username"]))).all()
    return {"items": [_public(r) for r in rows]}


@router.post("/users", status_code=201, summary="Create a user")
async def create_user(body: CreateUserIn,
                      actor: dict = Depends(require_level(4)),
                      session: AsyncSession = Depends(get_session)):
    uname = (body.username or "").strip()
    if not uname:
        raise HTTPException(422, "username is required")
    if body.role not in ROLE_META:
        raise HTTPException(422, f"unknown role {body.role!r}")
    if len(body.password or "") < MIN_PW:
        raise HTTPException(422, f"password must be at least {MIN_PW} characters")
    try:
        async with session.begin():
            if (await _get_user(session, uname)) is not None:
                raise HTTPException(409, f"user {uname!r} already exists")
            await session.execute(insert(users_t).values(
                username=uname, password_hash=_hash(body.password), role=body.role,
                Site_ID=(body.site_id or None), Warehouse_ID=(body.warehouse_id or None),
                Phone_Number=(body.phone_number or None)))
            await write_audit(session, actor["username"], "CREATE_USER", "users",
                              f"username={uname} role={body.role} site={body.site_id or '-'}")
    except HTTPException:
        raise
    except IntegrityError:
        raise HTTPException(409, f"user {uname!r} already exists")
    except DataError as e:
        raise HTTPException(400, f"DataError: {e.orig}")
    return {"created": True, "username": uname, "role": body.role}


@router.patch("/users/{username}", summary="Update a user's role / bindings (not password)")
async def update_user(username: str, body: UpdateUserIn,
                      actor: dict = Depends(require_level(4)),
                      session: AsyncSession = Depends(get_session)):
    if body.role is not None and body.role not in ROLE_META:
        raise HTTPException(422, f"unknown role {body.role!r}")
    values: dict = {}
    if body.role is not None:
        values["role"] = body.role
    if body.site_id is not None:
        values["Site_ID"] = body.site_id or None
    if body.warehouse_id is not None:
        values["Warehouse_ID"] = body.warehouse_id or None
    if body.phone_number is not None:
        values["Phone_Number"] = body.phone_number or None
    if not values:
        raise HTTPException(422, "no fields to update")
    try:
        async with session.begin():
            row = await _get_user(session, username)
            if row is None:
                raise HTTPException(404, f"user {username!r} not found")
            # Lockout guard: don't demote the last admin out of the admin role.
            if (body.role is not None and row.role == "admin" and body.role != "admin"
                    and (await _admin_count(session)) <= 1):
                raise HTTPException(409, "cannot change the role of the last admin")
            await session.execute(update(users_t)
                                  .where(users_t.c["username"] == username).values(**values))
            await write_audit(session, actor["username"], "UPDATE_USER", "users",
                              f"username={username} " + " ".join(f"{k}={v}" for k, v in values.items()))
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")
    return {"updated": True, "username": username}


@router.post("/users/{username}/reset-password", summary="Set a new password")
async def reset_password(username: str, body: PasswordIn,
                         actor: dict = Depends(require_level(4)),
                         session: AsyncSession = Depends(get_session)):
    if len(body.password or "") < MIN_PW:
        raise HTTPException(422, f"password must be at least {MIN_PW} characters")
    async with session.begin():
        if (await _get_user(session, username)) is None:
            raise HTTPException(404, f"user {username!r} not found")
        await session.execute(update(users_t).where(users_t.c["username"] == username)
                              .values(password_hash=_hash(body.password)))
        await write_audit(session, actor["username"], "RESET_PASSWORD", "users",
                          f"username={username}")
    return {"reset": True, "username": username}


@router.post("/users/{username}/reset-2fa", summary="Clear a user's 2FA (lost-device reset)")
async def reset_2fa(username: str,
                    actor: dict = Depends(require_level(4)),
                    session: AsyncSession = Depends(get_session)):
    async with session.begin():
        if (await _get_user(session, username)) is None:
            raise HTTPException(404, f"user {username!r} not found")
        await session.execute(update(users_t).where(users_t.c["username"] == username)
                              .values(totp_secret=None, totp_enabled=0))
        await write_audit(session, actor["username"], "RESET_2FA", "users",
                          f"username={username}")
    return {"reset_2fa": True, "username": username}


@router.delete("/users/{username}", summary="Delete a user (last-admin & self guards)")
async def delete_user(username: str,
                      actor: dict = Depends(require_level(4)),
                      session: AsyncSession = Depends(get_session)):
    async with session.begin():
        row = await _get_user(session, username)
        if row is None:
            raise HTTPException(404, f"user {username!r} not found")
        if username == actor["username"]:
            raise HTTPException(409, "you cannot delete your own account")
        if row.role == "admin" and (await _admin_count(session)) <= 1:
            raise HTTPException(409, "cannot delete the last admin")
        await session.execute(delete(users_t).where(users_t.c["username"] == username))
        await write_audit(session, actor["username"], "DELETE_USER", "users",
                          f"username={username} role={row.role}")
    return {"deleted": True, "username": username}


# --- audit log ---------------------------------------------------------------
@router.get("/audit/meta", summary="Distinct action types / target tables (filters)")
async def audit_meta(session: AsyncSession = Depends(get_session)):
    acts = (await session.execute(select(audit_t.c["action_type"]).distinct()
            .order_by(audit_t.c["action_type"]))).scalars().all()
    tbls = (await session.execute(select(audit_t.c["target_table"]).distinct()
            .order_by(audit_t.c["target_table"]))).scalars().all()
    return {"action_types": [a for a in acts if a], "target_tables": [t for t in tbls if t]}


@router.get("/audit", summary="Audit-log feed (filterable, newest first)")
async def audit_log(username: Optional[str] = None, action_type: Optional[str] = None,
                    target_table: Optional[str] = None, q: Optional[str] = None,
                    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                    session: AsyncSession = Depends(get_session)):
    stmt = select(audit_t.c["id"], audit_t.c["timestamp"], audit_t.c["username"],
                  audit_t.c["action_type"], audit_t.c["target_table"], audit_t.c["details"])
    if username:
        stmt = stmt.where(audit_t.c["username"] == username)
    if action_type:
        stmt = stmt.where(audit_t.c["action_type"] == action_type)
    if target_table:
        stmt = stmt.where(audit_t.c["target_table"] == target_table)
    if q:
        stmt = stmt.where(audit_t.c["details"].ilike(f"%{q}%"))
    total = (await session.execute(
        select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await session.execute(
        stmt.order_by(audit_t.c["id"].desc()).limit(limit).offset(offset))).all()
    return {"total": total, "limit": limit, "offset": offset,
            "items": [dict(r._mapping) for r in rows]}


# --- inventory master editor -------------------------------------------------
# Reads still go through the open /inventory router; these admin-only writes add
# the safety the generic CRUD lacks: opening-stock audit + a delete guard that
# refuses to orphan an item that already has ledger movements.
async def _sap_exists(session: AsyncSession, sap: str) -> bool:
    return (await session.execute(select(func.count()).select_from(inventory_t)
            .where(func.trim(inventory_t.c["SAP_Code"]) == sap))).scalar_one() > 0


async def _sap_movements(session: AsyncSession, sap: str) -> int:
    total = 0
    for t in _SAP_REFS:
        total += (await session.execute(select(func.count()).select_from(t)
                  .where(func.trim(t.c["SAP_Code"]) == sap))).scalar_one()
    return total


@router.post("/inventory", status_code=201, summary="Add an inventory master item")
async def create_inventory(body: InventoryCreateIn,
                           actor: dict = Depends(require_level(4)),
                           session: AsyncSession = Depends(get_session)):
    sap = (body.SAP_Code or "").strip()
    if not sap:
        raise HTTPException(422, "SAP_Code is required")
    values = {k: v for k, v in body.model_dump().items() if v is not None}
    values["SAP_Code"] = sap
    try:
        async with session.begin():
            if await _sap_exists(session, sap):
                raise HTTPException(409, f"SAP_Code {sap!r} already exists")
            await session.execute(insert(inventory_t).values(**values))
            await write_audit(session, actor["username"], "CREATE_INVENTORY", "inventory",
                              f"SAP={sap} opening={values.get('Opening_Stock', 0)}")
    except HTTPException:
        raise
    except IntegrityError as e:
        raise HTTPException(409, f"IntegrityError: {e.orig}")
    except DataError as e:
        raise HTTPException(400, f"DataError: {e.orig}")
    return {"created": True, "SAP_Code": sap}


@router.patch("/inventory/{sap_code}", summary="Edit an inventory master item")
async def update_inventory(sap_code: str, body: InventoryUpdateIn,
                           actor: dict = Depends(require_level(4)),
                           session: AsyncSession = Depends(get_session)):
    values = {k: v for k, v in body.model_dump().items() if v is not None}
    if not values:
        raise HTTPException(422, "no fields to update")
    sap = sap_code.strip()
    try:
        async with session.begin():
            cur = (await session.execute(select(inventory_t.c["Opening_Stock"])
                   .where(func.trim(inventory_t.c["SAP_Code"]) == sap))).first()
            if cur is None:
                raise HTTPException(404, f"SAP_Code {sap!r} not found")
            # Opening_Stock feeds the identity math — audit any change explicitly.
            if "Opening_Stock" in values and float(values["Opening_Stock"]) != float(cur[0] or 0):
                await write_audit(session, actor["username"], "OPENING_STOCK_EDIT", "inventory",
                                  f"SAP={sap} {cur[0]} → {values['Opening_Stock']}")
            await session.execute(update(inventory_t)
                                  .where(func.trim(inventory_t.c["SAP_Code"]) == sap).values(**values))
            await write_audit(session, actor["username"], "UPDATE_INVENTORY", "inventory",
                              f"SAP={sap} fields={','.join(values)}")
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")
    return {"updated": True, "SAP_Code": sap}


@router.delete("/inventory/{sap_code}", summary="Delete an inventory item (only if it has no movements)")
async def delete_inventory(sap_code: str,
                           actor: dict = Depends(require_level(4)),
                           session: AsyncSession = Depends(get_session)):
    sap = sap_code.strip()
    async with session.begin():
        if not await _sap_exists(session, sap):
            raise HTTPException(404, f"SAP_Code {sap!r} not found")
        moves = await _sap_movements(session, sap)
        if moves:
            raise HTTPException(409, f"cannot delete {sap!r}: it has {moves} ledger movement(s)")
        await session.execute(delete(inventory_t)
                              .where(func.trim(inventory_t.c["SAP_Code"]) == sap))
        await write_audit(session, actor["username"], "DELETE_INVENTORY", "inventory", f"SAP={sap}")
    return {"deleted": True, "SAP_Code": sap}
