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
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import (ROLE_META, normalize_phone, require_level, require_roles,
                   revoke_all_sessions, site_scope)
from .db import get_session
from .services.ledger import _MD, write_audit  # reflected metadata + audit writer

users_t = _MD.tables["users"]
audit_t = _MD.tables["system_audit_log"]
inventory_t = _MD.tables["inventory"]
pending_users_t = _MD.tables["pending_users"]

# Ledger/movement tables that reference inventory by SAP_Code — an item with any
# of these rows must NOT be deleted (it would orphan history / break identity math).
_SAP_REFS = [_MD.tables[t] for t in (
    "receipts", "consumption", "returns", "lots",
    "pending_receipts", "pending_issues", "pending_returns", "pr_master")]

# --- the password policy, in ONE place -----------------------------------------
# Operator ruling 2026-08-11: 8 characters with complexity, down from 12 plain.
#
# Every credential-setting path calls `assert_password_ok`. It replaced five
# copies of `if len(pw) < MIN_PW: raise ...` scattered across admin create,
# admin reset, /auth/register and /qc/accounts — and that shape is the point.
# A rule expressed five times is a rule that gets updated four times; the
# weakest remaining door then sets the real policy, which is exactly how
# registration sat on a literal 6 while everything else used MIN_PW
# (audit A03-F11).
MIN_PW = 8  # minimum password length for create / reset / self-registration

_PW_SPECIALS = "!@#$%^&*()-_=+[]{};:'\",.<>/?\\|`~"


def password_problems(pw: str) -> list[str]:
    """Everything wrong with this password, as human sentences. Empty = fine.

    Returns ALL failures rather than the first, so somebody fixing a password
    is told the whole requirement once instead of discovering it one rejection
    at a time.
    """
    pw = pw or ""
    problems: list[str] = []
    if len(pw) < MIN_PW:
        problems.append(f"be at least {MIN_PW} characters (yours is {len(pw)})")
    if not any(c.isupper() for c in pw):
        problems.append("contain an uppercase letter")
    if not any(c.isdigit() for c in pw):
        problems.append("contain a number")
    if not any(c in _PW_SPECIALS for c in pw):
        problems.append("contain a special character, e.g. ! @ # $ % &")
    return problems


def assert_password_ok(pw: str) -> None:
    """422 unless the password meets the policy, naming every failure."""
    problems = password_problems(pw)
    if problems:
        raise HTTPException(
            422, "Password must " + "; ".join(problems) + ".")

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
    # Phase 15a: True = "yes, this Site / Category is NEW" — sent only after the
    # form asked the admin, following a 422 `unknown_site` / `unknown_category`.
    confirm_new: bool = False


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
    confirm_new: bool = False


class ApprovePendingIn(BaseModel):
    role: Optional[str] = None          # override the requested role
    warehouse_id: Optional[str] = None  # override the requested warehouse binding


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
    assert_password_ok(body.password)
    try:
        async with session.begin():
            if (await _get_user(session, uname)) is not None:
                raise HTTPException(409, f"user {uname!r} already exists")
            await session.execute(insert(users_t).values(
                username=uname, password_hash=_hash(body.password), role=body.role,
                Site_ID=(body.site_id or None), Warehouse_ID=(body.warehouse_id or None),
                Phone_Number=(normalize_phone(body.phone_number) if body.phone_number else None)))
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
        # '' clears the number; anything else must be valid +E.164 (global rule).
        values["Phone_Number"] = normalize_phone(body.phone_number) if body.phone_number else None
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
            # Audit A03-F9: role/site/warehouse ride inside the access token and
            # are read straight back out with no database lookup, so a demoted
            # or re-pinned user kept their OLD authority for up to 15 minutes.
            # Revoking forces an immediate re-login with the new bindings.
            authz_changed = {k: v for k, v in values.items()
                             if k in ("role", "Site_ID", "Warehouse_ID")
                             and v != getattr(row, k, None)}
            if authz_changed:
                await revoke_all_sessions(session, username, "authz-changed")
                await write_audit(session, actor["username"], "REVOKE_SESSIONS", "users",
                                  f"username={username} reason=authz-changed "
                                  f"fields={','.join(sorted(authz_changed))}")
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")
    return {"updated": True, "username": username}


@router.post("/users/{username}/reset-password", summary="Set a new password")
async def reset_password(username: str, body: PasswordIn,
                         actor: dict = Depends(require_level(4)),
                         session: AsyncSession = Depends(get_session)):
    assert_password_ok(body.password)
    async with session.begin():
        if (await _get_user(session, username)) is None:
            raise HTTPException(404, f"user {username!r} not found")
        await session.execute(update(users_t).where(users_t.c["username"] == username)
                              .values(password_hash=_hash(body.password)))
        # A password reset must end any live sessions for the old credential.
        await revoke_all_sessions(session, username, "admin-reset")
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
        await revoke_all_sessions(session, username, "user-deleted")
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
    """Does this SAP code already exist in the master?

    Bloom-accelerated (2026-09-01). `inventory` is the most-read table in the
    system and almost never written: 491 rows, touched by every entry form,
    every fuzzy match and every bulk import, and grown by a handful of rows a
    month. A filter over its primary key answers the common case — a code that
    is NOT there — without a round trip.

    ⚠️ THE FAST PATH IS ONLY TAKEN FOR "DEFINITELY ABSENT", never for "present".
    A Bloom filter's false positives are ~1%; its false negatives about its own
    contents are impossible. So "maybe present" still asks the database, and the
    `SAP_Code` primary key still decides — this helper is a guard that produces
    a friendlier 409 than an IntegrityError, not the uniqueness rule itself.
    """
    from .services import bloom
    bf = bloom.get(bloom.SAP_CODES)
    if bf is not None and not bf.probably_present(sap):
        return False
    return (await session.execute(select(func.count()).select_from(inventory_t)
            .where(func.trim(inventory_t.c["SAP_Code"]) == sap))).scalar_one() > 0


def _fold(v: str) -> str:
    """Case- and plural-blind comparison key: 'consumable' ~ 'Consumables'."""
    k = " ".join(str(v).split()).lower()
    return k[:-1] if k.endswith("s") else k


async def _known_sites(session: AsyncSession) -> list[str]:
    users_t = _MD.tables["users"]
    equip_t = _MD.tables["sme_equipment"]
    known: set[str] = set()
    for col in (inventory_t.c["Site_ID"], users_t.c["Site_ID"], equip_t.c["Site_ID"]):
        known |= {str(v).strip() for (v,) in (await session.execute(
            select(col).distinct().where(col.is_not(None)))).all() if str(v).strip()}
    return sorted(known)


async def _canonical_site_category(session: AsyncSession, values: dict, *,
                                   confirm_new: bool) -> None:
    """Snap `Site_ID` and `Category` onto the spelling the system already uses.

    Phase 15a. The master editor took both as free text and stored whatever
    arrived: a Practice item saved as site `cncec` / category `consumable` was
    invisible to the CNCEC store keeper (site scope compares `Site_ID`
    EXACTLY) and matched no category filter. So:

      · a case- or plural-variant of a known value is rewritten to it;
      · the sync's own `CATEGORY_CANON` applies (`Surface Shield` → …);
      · a genuinely NEW value is a 422 naming the known ones, unless the
        admin confirmed it (`confirm_new`) — opening a site is legitimate,
        typing one by accident is the bug.
    """
    from .bulk_import import CATEGORY_CANON
    if "Site_ID" in values:
        raw = " ".join(str(values["Site_ID"]).split())
        if not raw:
            values.pop("Site_ID")
        else:
            known = await _known_sites(session)
            hit = next((k for k in known if k.lower() == raw.lower()), None)
            if hit is not None:
                values["Site_ID"] = hit
            elif not confirm_new:
                raise HTTPException(422, {
                    "code": "unknown_site", "value": raw, "known": known,
                    "message": f"Site {raw!r} does not exist yet (known: "
                               f"{', '.join(known) or 'none'}). Pick one, or confirm "
                               f"it is a new site."})
            else:
                values["Site_ID"] = raw
    if "Category" in values:
        raw = " ".join(str(values["Category"]).split())
        if not raw:
            values.pop("Category")
        else:
            raw = CATEGORY_CANON.get(raw.lower(), raw)
            col = inventory_t.c["Category"]
            known = sorted({str(v).strip() for (v,) in (await session.execute(
                select(col).distinct().where(col.is_not(None)))).all() if str(v).strip()})
            hit = next((k for k in known if k == raw), None) \
                or next((k for k in known if _fold(k) == _fold(raw)), None)
            if hit is not None:
                values["Category"] = hit
            elif not confirm_new:
                raise HTTPException(422, {
                    "code": "unknown_category", "value": raw, "known": known,
                    "message": f"Category {raw!r} does not exist yet. Pick an existing "
                               f"one, or confirm it is a new category."})
            else:
                values["Category"] = raw


async def _sap_movements(session: AsyncSession, sap: str) -> int:
    total = 0
    for t in _SAP_REFS:
        total += (await session.execute(select(func.count()).select_from(t)
                  .where(func.trim(t.c["SAP_Code"]) == sap))).scalar_one()
    return total


def _own_site(scope: Optional[str], site: Optional[str]) -> None:
    """A site-scoped editor (the HOD, 2026-09-30) writes their own site only."""
    if scope is None:
        return
    if not scope:
        raise HTTPException(403, "your account has no site — ask an admin to add this item")
    if site is not None and str(site).strip().lower() != scope.lower():
        raise HTTPException(403, f"you can add or edit items for your own site ({scope}) only")


@router.post("/inventory", status_code=201, summary="Add an inventory master item")
async def create_inventory(body: InventoryCreateIn,
                           actor: dict = Depends(require_level(4)),
                           session: AsyncSession = Depends(get_session)):
    return await _create_item(session, body, actor, scope=None)


async def _create_item(session: AsyncSession, body: InventoryCreateIn, actor: dict, *,
                       scope: Optional[str]) -> dict:
    """Shared by the admin editor and the HOD's (`item_router`). `scope` None =
    any site; a string = the only site this actor may write."""
    sap = (body.SAP_Code or "").strip()
    if not sap:
        raise HTTPException(422, "SAP_Code is required")
    values = {k: v for k, v in body.model_dump(exclude={"confirm_new"}).items()
              if v is not None}
    values["SAP_Code"] = sap
    if scope is not None:
        _own_site(scope, values.get("Site_ID"))
        values["Site_ID"] = scope
    try:
        async with session.begin():
            if await _sap_exists(session, sap):
                raise HTTPException(409, f"SAP_Code {sap!r} already exists")
            if not str(values.get("Site_ID") or "").strip():
                raise HTTPException(422, {
                    "code": "site_required",
                    "message": "Site is required — an item with no site is invisible "
                               "to every site-scoped user (store keeper, supervisor)."})
            await _canonical_site_category(session, values, confirm_new=body.confirm_new)
            await session.execute(insert(inventory_t).values(**values))
            await write_audit(session, actor["username"], "CREATE_INVENTORY", "inventory",
                              f"SAP={sap} opening={values.get('Opening_Stock', 0)}")
    except HTTPException:
        raise
    except IntegrityError as e:
        raise HTTPException(409, f"IntegrityError: {e.orig}")
    except DataError as e:
        raise HTTPException(400, f"DataError: {e.orig}")
    from .services import bloom as _bloom
    _bloom.add(_bloom.SAP_CODES, sap)
    return {"created": True, "SAP_Code": sap}


@router.patch("/inventory/{sap_code}", summary="Edit an inventory master item")
async def update_inventory(sap_code: str, body: InventoryUpdateIn,
                           actor: dict = Depends(require_level(4)),
                           session: AsyncSession = Depends(get_session)):
    return await _update_item(session, sap_code, body, actor, scope=None)


async def _update_item(session: AsyncSession, sap_code: str, body: InventoryUpdateIn,
                       actor: dict, *, scope: Optional[str]) -> dict:
    values = {k: v for k, v in body.model_dump(exclude={"confirm_new"}).items()
              if v is not None}
    if not values:
        raise HTTPException(422, "no fields to update")
    sap = sap_code.strip()
    if scope is not None:
        _own_site(scope, values.get("Site_ID"))
        values.pop("Site_ID", None)       # their own site, which it already is
    try:
        async with session.begin():
            await _canonical_site_category(session, values, confirm_new=body.confirm_new)
            if not values:
                raise HTTPException(422, "no fields to update")
            cur = (await session.execute(select(inventory_t.c["Opening_Stock"],
                                                inventory_t.c["Site_ID"])
                   .where(func.trim(inventory_t.c["SAP_Code"]) == sap))).first()
            if cur is None:
                raise HTTPException(404, f"SAP_Code {sap!r} not found")
            if scope is not None:
                _own_site(scope, cur[1] or "")
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


# --- the item editor for the HOD (2026-09-30) --------------------------------
# The operator asked for "add material" in the HOD's portal as well as the
# admin's. It is a SECOND router because `router` above is admin-only as a
# whole (its dependency is level 4). Same models, same Site/Category snapping,
# same audit rows — the one difference is scope: a HOD adds and edits items of
# THEIR OWN site only. DELETE stays admin-only: removing a master row is the
# irreversible one.
item_router = APIRouter(prefix="/inventory-items", tags=["inventory"])


@item_router.post("", status_code=201, summary="Add an inventory item (HOD: own site; admin: any)")
async def create_item(body: InventoryCreateIn,
                      actor: dict = Depends(require_roles("hod")),
                      session: AsyncSession = Depends(get_session)):
    return await _create_item(session, body, actor, scope=site_scope(actor))


@item_router.patch("/{sap_code}", summary="Edit an inventory item (HOD: own site; admin: any)")
async def update_item(sap_code: str, body: InventoryUpdateIn,
                      actor: dict = Depends(require_roles("hod")),
                      session: AsyncSession = Depends(get_session)):
    return await _update_item(session, sap_code, body, actor, scope=site_scope(actor))


# --- access requests (pending_users) -----------------------------------------
# Self-service /auth/register creates pending_users rows; an admin approves
# (→ a real users row) or rejects them here. Secrets are never returned.
_PENDING_COLS = (
    pending_users_t.c["id"], pending_users_t.c["username"], pending_users_t.c["role"],
    pending_users_t.c["Site_ID"], pending_users_t.c["Warehouse_ID"],
    pending_users_t.c["Phone_Number"], pending_users_t.c["Location"],
    pending_users_t.c["status"], pending_users_t.c["created_at"],
)


@router.get("/pending-users", summary="Access requests (default: pending)")
async def list_pending_users(status: str = "pending",
                             session: AsyncSession = Depends(get_session)):
    stmt = select(*_PENDING_COLS)
    if status:
        stmt = stmt.where(pending_users_t.c["status"] == status)
    stmt = stmt.order_by(pending_users_t.c["id"].desc())
    return {"items": [dict(m) for m in (await session.execute(stmt)).mappings().all()]}


@router.post("/pending-users/{pid}/approve", summary="Approve a request → create the user")
async def approve_pending_user(pid: int, body: ApprovePendingIn = Body(default=ApprovePendingIn()),
                               actor: dict = Depends(require_level(4)),
                               session: AsyncSession = Depends(get_session)):
    try:
        async with session.begin():
            row = (await session.execute(select(pending_users_t)
                   .where(pending_users_t.c["id"] == pid))).mappings().first()
            if row is None:
                raise HTTPException(404, f"request {pid} not found")
            if row["status"] != "pending":
                raise HTTPException(409, f"request already {row['status']}")
            role = body.role or row["role"]
            if role not in ROLE_META:
                raise HTTPException(422, f"unknown role {role!r}")
            if (await _get_user(session, row["username"])) is not None:
                raise HTTPException(409, f"username {row['username']!r} already exists")
            wh = body.warehouse_id if body.warehouse_id is not None else row["Warehouse_ID"]
            await session.execute(insert(users_t).values(
                username=row["username"], password_hash=row["password_hash"], role=role,
                Site_ID=row["Site_ID"], Phone_Number=row["Phone_Number"], Warehouse_ID=wh,
                Location=row["Location"]))
            await session.execute(update(pending_users_t)
                                  .where(pending_users_t.c["id"] == pid).values(status="approved"))
            await write_audit(session, actor["username"], "APPROVE_USER", "users",
                              f"username={row['username']} role={role}")
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")
    return {"approved": True, "username": row["username"], "role": role}


@router.post("/pending-users/{pid}/reject", summary="Reject an access request")
async def reject_pending_user(pid: int, actor: dict = Depends(require_level(4)),
                              session: AsyncSession = Depends(get_session)):
    async with session.begin():
        row = (await session.execute(select(pending_users_t.c["username"], pending_users_t.c["status"])
               .where(pending_users_t.c["id"] == pid))).first()
        if row is None:
            raise HTTPException(404, f"request {pid} not found")
        if row.status != "pending":
            raise HTTPException(409, f"request already {row.status}")
        await session.execute(update(pending_users_t)
                              .where(pending_users_t.c["id"] == pid).values(status="rejected"))
        await write_audit(session, actor["username"], "REJECT_USER", "pending_users",
                          f"username={row.username}")
    return {"rejected": True, "id": pid}
