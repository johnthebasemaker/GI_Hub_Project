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
import os
import sys

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from backend import models  # noqa: E402

_MD = models.Base.metadata
users_t = _MD.tables["users"]
audit_t = _MD.tables["system_audit_log"]

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-insecure-change-me")
JWT_ALG = "HS256"
ACCESS_TTL = _dt.timedelta(hours=8)
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


def _make_token(sub: str, role: str, site_id: str, ttl: _dt.timedelta, scope: str = "access") -> str:
    now = _dt.datetime.now(_dt.timezone.utc)
    payload = {"sub": sub, "role": role, "site_id": site_id or "",
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


def _public(username: str, role: str, site_id: str) -> dict:
    meta = ROLE_META.get(role, {"label": role, "level": 0})
    return {"username": username, "role": role, "site_id": site_id or "",
            "label": meta["label"], "level": meta["level"]}


async def get_current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """Route guard: validate the bearer JWT and return the user claims."""
    if cred is None:
        raise HTTPException(401, "not authenticated")
    p = _decode(cred.credentials, "access")
    return _public(p["sub"], p.get("role"), p.get("site_id"))


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


router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class TwoFAIn(BaseModel):
    mfa_token: str
    code: str


async def _fetch_user(session: AsyncSession, username: str):
    return (await session.execute(select(
        users_t.c["username"], users_t.c["password_hash"], users_t.c["role"],
        users_t.c["Site_ID"], users_t.c["totp_secret"], users_t.c["totp_enabled"],
    ).where(users_t.c["username"] == username.strip()))).first()


async def _audit(session: AsyncSession, username: str, action: str, details: str) -> None:
    await session.execute(insert(audit_t).values(
        username=username, action_type=action, target_table="users", details=details))
    await session.commit()


@router.post("/login", summary="Username + password → JWT (or a 2FA challenge)")
async def login(body: LoginIn, session: AsyncSession = Depends(get_session)):
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

    token = _make_token(row.username, row.role, row.Site_ID, ACCESS_TTL)
    await _audit(session, row.username, "LOGIN", "password")
    return {"access_token": token, "token_type": "bearer",
            "user": _public(row.username, row.role, row.Site_ID)}


@router.post("/login/2fa", summary="Complete login with a TOTP code")
async def login_2fa(body: TwoFAIn, session: AsyncSession = Depends(get_session)):
    p = _decode(body.mfa_token, "mfa")
    row = await _fetch_user(session, p["sub"])
    if row is None:
        raise HTTPException(401, "user not found")
    if not _verify_totp(row.totp_secret, body.code):
        await _audit(session, row.username, "2FA_FAILED", "invalid code")
        raise HTTPException(401, "invalid 2FA code")
    token = _make_token(row.username, row.role, row.Site_ID, ACCESS_TTL)
    await _audit(session, row.username, "LOGIN", "password+2fa")
    return {"access_token": token, "token_type": "bearer",
            "user": _public(row.username, row.role, row.Site_ID)}


@router.get("/me", summary="Current authenticated user")
async def me(user: dict = Depends(get_current_user)):
    return user


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
