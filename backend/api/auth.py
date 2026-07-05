"""
backend/api/auth.py — authentication (login + JWT) for the API.

Ports the Streamlit app's auth (auth.py): bcrypt password verify, opt-in TOTP
2FA (pyotp, ±30s window), and the role set from config.py. Issues a JWT the SPA
sends as `Authorization: Bearer <token>`; `get_current_user` is the dependency
that guards protected routes.

  POST /auth/login       {username, password} → {access_token, user}  OR  {mfa_required, mfa_token}
  POST /auth/login/2fa   {mfa_token, code}    → {access_token, user}
  GET  /auth/me          (bearer)             → the current user

JWT signing key comes from JWT_SECRET (a dev default is used if unset — set a
real secret in any shared/deployed environment).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import os
import secrets
import sys

import bcrypt
import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import jwt_secret
from .db import get_session
from .ratelimit import rate_limit

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from backend import models  # noqa: E402

_MD = models.Base.metadata
users_t = _MD.tables["users"]
audit_t = _MD.tables["system_audit_log"]
pending_users_t = _MD.tables["pending_users"]
sessions_t = _MD.tables["auth_sessions"]

# Resolved once at import — in production a weak/absent key raises here (fail-fast).
JWT_SECRET = jwt_secret()
JWT_ALG = "HS256"
# Short-lived access + long-lived rotating refresh (httpOnly cookie). The SPA
# silently refreshes on 401, so a 15-minute access token never interrupts a
# shift; revoking the refresh session (logout / admin reset / reuse detection)
# ends the session server-side within one access-token lifetime.
ACCESS_TTL = _dt.timedelta(minutes=15)
REFRESH_TTL = _dt.timedelta(days=7)
REFRESH_COOKIE = "gi_refresh"
MFA_TTL = _dt.timedelta(minutes=5)

# Role label + hierarchy level (from config.py ROLES / ROLE_HIERARCHY).
ROLE_META = {
    "admin":          {"label": "Admin",              "level": 4},
    "logistics":      {"label": "Logistics",          "level": 3},
    "hod":            {"label": "Head of Department", "level": 2},
    "warehouse_user": {"label": "Warehouse",          "level": 1},
    "supervisor":     {"label": "Supervisor",         "level": 1},
    "store_keeper":   {"label": "Store Keeper",       "level": 0},
}

# Self-service registrants may request any role EXCEPT admin (no self-elevation);
# the approving admin can still override the role at approval time.
_REGISTERABLE_ROLES = set(ROLE_META) - {"admin"}

_bearer = HTTPBearer(auto_error=False)
_DUMMY_HASH = "$2b$12$0000000000000000000000000000000000000000000000000000"


def _verify_password(plain: str, hashed: str | None) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), (hashed or "").encode("utf-8"))
    except Exception:  # noqa: BLE001
        return False


def _verify_totp(secret: str | None, code: str) -> bool:
    if not secret or not code:
        return False
    try:
        import pyotp
        return pyotp.TOTP(secret).verify(str(code).strip(), valid_window=1)
    except Exception:  # noqa: BLE001
        return False


def _make_token(sub: str, role: str, site_id: str, ttl: _dt.timedelta,
                scope: str = "access", warehouse_id: str = "") -> str:
    now = _dt.datetime.now(_dt.timezone.utc)
    payload = {"sub": sub, "role": role, "site_id": site_id or "",
               "warehouse_id": warehouse_id or "",
               "scope": scope, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def _decode(token: str, scope: str) -> dict:
    try:
        p = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid or expired token")
    if p.get("scope") != scope:
        raise HTTPException(401, "wrong token scope")
    return p


def _public(username: str, role: str, site_id: str, warehouse_id: str = "") -> dict:
    meta = ROLE_META.get(role, {"label": role, "level": 0})
    return {"username": username, "role": role, "site_id": site_id or "",
            "warehouse_id": warehouse_id or "",
            "label": meta["label"], "level": meta["level"]}


# --- refresh-token sessions ---------------------------------------------------
def _now() -> _dt.datetime:
    """Naive UTC — consistent with how expires_at/revoked_at are written."""
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def _hash_refresh(raw: str) -> str:
    # Only the hash is stored; a DB leak never yields usable refresh tokens.
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _set_refresh_cookie(response: Response, raw: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE, raw,
        max_age=int(REFRESH_TTL.total_seconds()),
        httponly=True, samesite="lax",
        secure=os.environ.get("GI_ENV", "").lower() == "production",
        path="/")


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path="/")


async def _open_session(session: AsyncSession, username: str) -> str:
    """Insert a session row; returns the RAW refresh token (only ever sent in
    the httpOnly cookie — never in a JSON body, never stored raw)."""
    raw = secrets.token_urlsafe(48)
    await session.execute(insert(sessions_t).values(
        username=username, refresh_hash=_hash_refresh(raw),
        expires_at=_now() + REFRESH_TTL))
    return raw


async def revoke_all_sessions(session: AsyncSession, username: str, reason: str) -> int:
    """Revoke every active session for a user (password reset, user delete,
    refresh-token reuse). Does NOT commit — the caller owns the transaction."""
    res = await session.execute(
        update(sessions_t)
        .where(sessions_t.c["username"] == username,
               sessions_t.c["revoked_at"].is_(None))
        .values(revoked_at=_now(), revoke_reason=reason))
    return res.rowcount or 0


async def get_current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """Route guard: validate the bearer JWT and return the user claims."""
    if cred is None:
        raise HTTPException(401, "not authenticated")
    p = _decode(cred.credentials, "access")
    return _public(p["sub"], p.get("role"), p.get("site_id"), p.get("warehouse_id", ""))


def require_level(min_level: int):
    """Dependency factory: 403 unless the user's role level ≥ min_level
    (store_keeper 0 · warehouse/supervisor 1 · hod 2 · logistics 3 · admin 4)."""
    async def _dep(user: dict = Depends(get_current_user)) -> dict:
        if user["level"] < min_level:
            raise HTTPException(403, "insufficient role for this action")
        return user
    return _dep


def require_roles(*roles: str):
    """Dependency factory: 403 unless the user's role is one of `roles`
    (admin is always allowed). For the parallel-ladder roles (warehouse_user,
    supervisor) that a level check can't isolate."""
    allowed = set(roles) | {"admin"}
    async def _dep(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in allowed:
            raise HTTPException(403, "this action is restricted to: " + ", ".join(sorted(allowed)))
        return user
    return _dep


# --- Site scoping (reads) -----------------------------------------------------
# Multi-site isolation (Tier-2 hardening): below logistics (level 3), a user may
# only read rows belonging to their own Site_ID. admin + logistics stay global.
SITE_SCOPE_MIN_LEVEL = 3


def site_scope(user: dict) -> str | None:
    """None → unrestricted (admin/logistics). Otherwise the only Site_ID this
    user may read — possibly '' for a site-less scoped user (e.g. a warehouse
    account), which every consumer must treat as *matches nothing* (fail-closed),
    never as a wildcard."""
    if user.get("level", 0) >= SITE_SCOPE_MIN_LEVEL:
        return None
    return (user.get("site_id") or "").strip()


def resolve_site_param(user: dict, requested: str | None) -> str | None:
    """Resolve a ?site_id= query param under scoping. Unrestricted users get
    exactly what they asked for (None = no filter). Scoped users always get
    their own site; explicitly requesting a different one is a 403 so the
    boundary is visible rather than silently rewritten."""
    scope = site_scope(user)
    if scope is None:
        return requested
    if requested is not None and requested != scope:
        raise HTTPException(403, "you may only read data for your own site")
    return scope


# --- Warehouse scoping (parallel to site scoping) ------------------------------
def warehouse_scope(user: dict) -> str | None:
    """None → unrestricted (logistics/admin oversight). warehouse_user accounts
    are pinned to their bound Warehouse_ID — '' (unbound) matches nothing."""
    if user.get("role") != "warehouse_user":
        return None
    return (user.get("warehouse_id") or "").strip()


def resolve_warehouse_param(user: dict, requested: str | None) -> str | None:
    """Resolve a warehouse_id under scoping: warehouse users always get their
    own warehouse (403 asking for another); others pass through."""
    scope = warehouse_scope(user)
    if scope is None:
        return requested
    if requested is not None and requested != scope:
        raise HTTPException(403, "you may only access your own warehouse")
    return scope


router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class TwoFAIn(BaseModel):
    mfa_token: str
    code: str


class RegisterIn(BaseModel):
    username: str
    password: str
    role: str
    site_id: str | None = None
    phone_number: str | None = None
    warehouse_id: str | None = None


async def _fetch_user(session: AsyncSession, username: str):
    return (await session.execute(select(
        users_t.c["username"], users_t.c["password_hash"], users_t.c["role"],
        users_t.c["Site_ID"], users_t.c["Warehouse_ID"],
        users_t.c["totp_secret"], users_t.c["totp_enabled"],
    ).where(users_t.c["username"] == username.strip()))).first()


async def _audit(session: AsyncSession, username: str, action: str, details: str) -> None:
    await session.execute(insert(audit_t).values(
        username=username, action_type=action, target_table="users", details=details))
    await session.commit()


@router.post("/login", summary="Username + password → JWT (or a 2FA challenge)",
             dependencies=[rate_limit(10, 60)])
async def login(body: LoginIn, response: Response,
                session: AsyncSession = Depends(get_session)):
    row = await _fetch_user(session, body.username)
    if row is None:
        _verify_password(body.password, _DUMMY_HASH)  # constant-time-ish
        await _audit(session, body.username.strip(), "LOGIN_FAILED", "unknown user")
        raise HTTPException(401, "invalid username or password")
    if not _verify_password(body.password, row.password_hash):
        await _audit(session, row.username, "LOGIN_FAILED", "bad password")
        raise HTTPException(401, "invalid username or password")

    if row.totp_enabled:
        mfa = _make_token(row.username, row.role, row.Site_ID, MFA_TTL, scope="mfa")
        return {"mfa_required": True, "mfa_token": mfa}

    token = _make_token(row.username, row.role, row.Site_ID, ACCESS_TTL,
                        warehouse_id=row.Warehouse_ID)
    raw_refresh = await _open_session(session, row.username)
    await _audit(session, row.username, "LOGIN", "password")  # commits
    _set_refresh_cookie(response, raw_refresh)
    return {"access_token": token, "token_type": "bearer",
            "user": _public(row.username, row.role, row.Site_ID, row.Warehouse_ID)}


@router.post("/login/2fa", summary="Complete login with a TOTP code",
             dependencies=[rate_limit(10, 60)])
async def login_2fa(body: TwoFAIn, response: Response,
                    session: AsyncSession = Depends(get_session)):
    p = _decode(body.mfa_token, "mfa")
    row = await _fetch_user(session, p["sub"])
    if row is None:
        raise HTTPException(401, "user not found")
    if not _verify_totp(row.totp_secret, body.code):
        await _audit(session, row.username, "2FA_FAILED", "invalid code")
        raise HTTPException(401, "invalid 2FA code")
    token = _make_token(row.username, row.role, row.Site_ID, ACCESS_TTL,
                        warehouse_id=row.Warehouse_ID)
    raw_refresh = await _open_session(session, row.username)
    await _audit(session, row.username, "LOGIN", "password+2fa")  # commits
    _set_refresh_cookie(response, raw_refresh)
    return {"access_token": token, "token_type": "bearer",
            "user": _public(row.username, row.role, row.Site_ID, row.Warehouse_ID)}


@router.post("/refresh", summary="Rotate the refresh cookie → a fresh access token",
             dependencies=[rate_limit(30, 60)])
async def refresh(response: Response,
                  gi_refresh: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
                  session: AsyncSession = Depends(get_session)):
    if not gi_refresh:
        raise HTTPException(401, "no refresh token")
    row = (await session.execute(select(sessions_t).where(
        sessions_t.c["refresh_hash"] == _hash_refresh(gi_refresh)))).first()
    if row is None:
        _clear_refresh_cookie(response)
        raise HTTPException(401, "invalid refresh token")
    if row.revoked_at is not None:
        # A rotated/revoked token came back — assume theft and kill every
        # active session for this user (rotation reuse detection).
        await revoke_all_sessions(session, row.username, "reuse-detected")
        await _audit(session, row.username, "SESSION_REUSE",
                     "revoked all sessions (refresh-token replay)")  # commits
        _clear_refresh_cookie(response)
        raise HTTPException(401, "refresh token reuse detected — sessions revoked")
    if row.expires_at is not None and row.expires_at <= _now():
        _clear_refresh_cookie(response)
        raise HTTPException(401, "refresh token expired")
    user_row = await _fetch_user(session, row.username)
    if user_row is None:
        await revoke_all_sessions(session, row.username, "user-deleted")
        await session.commit()
        _clear_refresh_cookie(response)
        raise HTTPException(401, "user no longer exists")

    # Rotate: open the successor first, then revoke the old row pointing at it.
    raw_new = secrets.token_urlsafe(48)
    new_id = (await session.execute(insert(sessions_t).values(
        username=row.username, refresh_hash=_hash_refresh(raw_new),
        expires_at=_now() + REFRESH_TTL).returning(sessions_t.c["id"]))).scalar_one()
    await session.execute(update(sessions_t).where(sessions_t.c["id"] == row.id)
                          .values(revoked_at=_now(), revoke_reason="rotated",
                                  replaced_by=new_id))
    await session.commit()
    _set_refresh_cookie(response, raw_new)
    token = _make_token(user_row.username, user_row.role, user_row.Site_ID, ACCESS_TTL,
                        warehouse_id=user_row.Warehouse_ID)
    return {"access_token": token, "token_type": "bearer",
            "user": _public(user_row.username, user_row.role, user_row.Site_ID, user_row.Warehouse_ID)}


@router.post("/logout", summary="Revoke the current refresh session")
async def logout(response: Response,
                 gi_refresh: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
                 session: AsyncSession = Depends(get_session)):
    if gi_refresh:
        await session.execute(
            update(sessions_t)
            .where(sessions_t.c["refresh_hash"] == _hash_refresh(gi_refresh),
                   sessions_t.c["revoked_at"].is_(None))
            .values(revoked_at=_now(), revoke_reason="logout"))
        await session.commit()
    _clear_refresh_cookie(response)
    return {"logged_out": True}


@router.get("/me", summary="Current authenticated user")
async def me(user: dict = Depends(get_current_user)):
    return user


@router.post("/register", status_code=201,
             summary="Request access → a pending_users row for an admin to approve",
             dependencies=[rate_limit(5, 60)])
async def register(body: RegisterIn, session: AsyncSession = Depends(get_session)):
    uname = (body.username or "").strip()
    if not uname:
        raise HTTPException(422, "username is required")
    if len(body.password or "") < 6:
        raise HTTPException(422, "password must be at least 6 characters")
    if body.role not in _REGISTERABLE_ROLES:
        raise HTTPException(422, f"role must be one of {sorted(_REGISTERABLE_ROLES)}")

    taken = (await session.execute(select(func.count()).select_from(users_t)
             .where(users_t.c["username"] == uname))).scalar_one()
    if taken:
        raise HTTPException(409, "username already exists")

    pw_hash = bcrypt.hashpw(body.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    values = dict(username=uname, password_hash=pw_hash, role=body.role,
                  Site_ID=(body.site_id or ""), Phone_Number=(body.phone_number or None),
                  Warehouse_ID=(body.warehouse_id or None), status="pending")
    # username is UNIQUE in pending_users — if a prior (rejected) request exists,
    # revive it rather than colliding.
    prior = (await session.execute(select(pending_users_t.c["id"], pending_users_t.c["status"])
             .where(pending_users_t.c["username"] == uname))).first()
    if prior is not None:
        if prior.status == "pending":
            raise HTTPException(409, "a request for this username is already pending")
        await session.execute(update(pending_users_t)
                              .where(pending_users_t.c["id"] == prior.id).values(**values))
    else:
        await session.execute(insert(pending_users_t).values(**values))
    await session.commit()
    await _audit(session, uname, "REQUEST_ACCESS", f"role={body.role} site={body.site_id or '-'}")
    return {"requested": True, "username": uname}


# --- 2FA self-enrollment -----------------------------------------------------
# Login already *verifies* TOTP and an admin can *reset* it; this lets a user
# turn 2FA on for their own account. The secret is stored on enroll but 2FA is
# only enabled once a code is verified, so a half-finished enroll never locks
# anyone out (login only challenges when totp_enabled = 1).
class CodeIn(BaseModel):
    code: str


def _qr_data_uri(uri: str) -> str:
    import base64
    import io

    import qrcode
    buf = io.BytesIO()
    qrcode.make(uri).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@router.get("/2fa/status", summary="Is 2FA enabled for the current user?")
async def twofa_status(user: dict = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    row = await _fetch_user(session, user["username"])
    return {"enabled": bool(row and row.totp_enabled)}


@router.post("/2fa/enroll", summary="Begin 2FA enrollment → secret + QR (not enabled yet)")
async def twofa_enroll(user: dict = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    import pyotp
    row = await _fetch_user(session, user["username"])
    if row is None:
        raise HTTPException(404, "user not found")
    if row.totp_enabled:
        raise HTTPException(409, "2FA is already enabled")
    secret = pyotp.random_base32()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user["username"], issuer_name="GI Hub")
    await session.execute(update(users_t).where(users_t.c["username"] == user["username"])
                          .values(totp_secret=secret))
    await session.commit()
    await _audit(session, user["username"], "2FA_ENROLL", "enrollment started")
    return {"secret": secret, "otpauth_uri": uri, "qr": _qr_data_uri(uri)}


@router.post("/2fa/verify", summary="Confirm a code to enable 2FA")
async def twofa_verify(body: CodeIn, user: dict = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    row = await _fetch_user(session, user["username"])
    if row is None or not row.totp_secret:
        raise HTTPException(409, "no enrollment in progress — call /2fa/enroll first")
    if not _verify_totp(row.totp_secret, body.code):
        raise HTTPException(400, "invalid 2FA code")
    await session.execute(update(users_t).where(users_t.c["username"] == user["username"])
                          .values(totp_enabled=1))
    await session.commit()
    await _audit(session, user["username"], "2FA_ENABLED", "verified + enabled")
    return {"enabled": True}


@router.post("/2fa/disable", summary="Disable 2FA (requires a valid current code)")
async def twofa_disable(body: CodeIn, user: dict = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)):
    row = await _fetch_user(session, user["username"])
    if row is None or not row.totp_enabled:
        raise HTTPException(409, "2FA is not enabled")
    if not _verify_totp(row.totp_secret, body.code):
        raise HTTPException(400, "invalid 2FA code")
    await session.execute(update(users_t).where(users_t.c["username"] == user["username"])
                          .values(totp_secret=None, totp_enabled=0))
    await session.commit()
    await _audit(session, user["username"], "2FA_DISABLED", "disabled by user")
    return {"disabled": True}
