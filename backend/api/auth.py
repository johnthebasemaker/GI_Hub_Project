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
import time as _time
import uuid
from collections import defaultdict, deque
from typing import Literal

import bcrypt
import jwt
from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import instance, is_production, jwt_secret, refresh_cookie_name
from .db import get_session
from .ratelimit import (assert_login_allowed, assert_login_allowed_shared,
                        check_bucket, clear_login_failures,
                        clear_login_failures_shared, client_ip,
                        note_login_failure, note_login_failure_shared, rate_limit,
                        strict_limits_enabled)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from backend import models  # noqa: E402

_MD = models.Base.metadata
users_t = _MD.tables["users"]
audit_t = _MD.tables["system_audit_log"]
pending_users_t = _MD.tables["pending_users"]
sessions_t = _MD.tables["auth_sessions"]        # LEGACY (pre-RTR) — revoke-only, no new writes
refresh_t = _MD.tables["refresh_sessions"]      # RTR: token families, per-client TTL
app_settings_t = _MD.tables["app_settings"]
sysset_t = _MD.tables["system_settings"]  # admin-created sites (category='Site')
phone_otp_t = _MD.tables["phone_otp"]      # self-service phone-change OTP codes


async def maintenance_on(session: AsyncSession) -> bool:
    """app_settings.maintenance_mode = '1' → non-admin login/refresh refused.
    Existing access tokens keep working for ≤ their 15-min lifetime."""
    v = (await session.execute(select(app_settings_t.c["value"])
         .where(app_settings_t.c["key"] == "maintenance_mode"))).scalar_one_or_none()
    return v == "1"

# Resolved once at import — in production a weak/absent key raises here (fail-fast).
JWT_SECRET = jwt_secret()
JWT_ALG = "HS256"
# Short-lived access + long-lived ROTATING refresh (RTR, httpOnly cookie).
# The SPA silently refreshes on 401, so a 15-minute access token never
# interrupts a shift; revoking the refresh family (logout / admin reset /
# replay detection) ends the session server-side within one access-token
# lifetime. The refresh TTL depends on the client: browsers get 7 days, the
# installed native apps (Tauri/Capacitor) get 90 — a warehouse tablet
# shouldn't demand a password every week.
ACCESS_TTL = _dt.timedelta(minutes=15)
REFRESH_TTLS = {"web": _dt.timedelta(days=7), "native": _dt.timedelta(days=90)}
# Per-instance (rule 17): Live and Practice share an origin, so they must not
# share a cookie name. Live keeps `gi_refresh`, byte-identical to before.
REFRESH_COOKIE = refresh_cookie_name()
# Which environment minted a token. Every token carries it; `_decode` refuses
# the other one's. Redundant with the per-instance JWT_SECRET BY DESIGN — it
# is what still refuses when somebody copies one secret into both env files.
INSTANCE = instance()
MFA_TTL = _dt.timedelta(minutes=5)

# Role label + hierarchy level (from config.py ROLES / ROLE_HIERARCHY).
#
# `auditor` is NEW-STACK ONLY and has no legacy counterpart — legacy/config.py
# is frozen at its six roles and stays that way.
#
# It sits at level 3 deliberately. Read scoping keys off the level ladder
# (SITE_SCOPE_MIN_LEVEL = 3), and an auditor who could only see one site — or,
# because unscoped accounts carry site_id '', fail closed and see NOTHING — is
# useless. Level 3 buys global READ reach; every write is refused by the
# read-only guard in readonly.py regardless of level, so the level grants no
# mutation power at all. See readonly.py for why the guard is method-based
# rather than a per-endpoint annotation.
#
# `qc` is NEW-STACK ONLY too, and sits at level 1 — the parallel ladder
# beside warehouse_user and supervisor. Level 1 is what makes a SITE quality
# inspector site-scoped for free (SITE_SCOPE_MIN_LEVEL = 3). It also means a
# level check can never isolate the role, exactly as it cannot isolate the
# other two, so every /qc route uses require_roles(), never require_level().
#
# `qc_hod` (2026-08-22) is the Head of Qualities: cross-site oversight of
# Surface Shield material. It sits at LEVEL 2 with an explicit cross-site
# exemption in `site_scope`, and the level is the whole decision.
#
# ⚠️ LEVEL 3 WOULD HAVE BEEN THE OBVIOUS CHOICE AND IT IS THE WRONG ONE.
# Cross-site reads key off the level ladder (SITE_SCOPE_MIN_LEVEL = 3), so 3
# buys global reach — and, with it, EVERY endpoint gated by require_level(0..3):
# ninety-seven of them, including Entry Log reads, SME, Records, Reports, Burn
# Rate and the HOD's own queues. That is not a Head of Qualities; it is a second
# Logistics account with a quality-themed sidebar.
#
# Level 2 + `QC_OVERSIGHT_ROLES` gives the reach without the inheritance, and
# every /qc-hod route uses require_roles(), never require_level(), so its
# surface is ENUMERABLE rather than inherited. This is the pattern `qc`
# established and it has held.
ROLE_META = {
    "admin":          {"label": "Admin",              "level": 4},
    "logistics":      {"label": "Logistics",          "level": 3},
    "auditor":        {"label": "Auditor (view-only)", "level": 3},
    "hod":            {"label": "Head of Department", "level": 2},
    "qc_hod":         {"label": "Head of Qualities",  "level": 2},
    "warehouse_user": {"label": "Warehouse",          "level": 1},
    "supervisor":     {"label": "Supervisor",         "level": 1},
    "qc":             {"label": "Quality Control",    "level": 1},
    "store_keeper":   {"label": "Store Keeper",       "level": 0},
}

# Roles that read across every site WITHOUT being level 3. The exemption is
# named rather than implied, so `site_scope` states the exception instead of
# leaving it to be inferred from a number.
QC_OVERSIGHT_ROLES = frozenset({"qc_hod"})

# Self-service registrants may request any role EXCEPT admin (no self-elevation);
# the approving admin can still override the role at approval time.
_REGISTERABLE_ROLES = set(ROLE_META) - {"admin"}

# T4 — role-conditional site rules for /auth/register:
#   scoped roles work AT a site → Site_ID mandatory + must be an admin-created
#   site; unscoped (global) roles must NOT carry a site — they may give a
#   free-text Location instead.
_SCOPED_REG_ROLES = {"store_keeper", "supervisor", "hod"}
# auditor is unscoped: it reads across every site, so binding it to one would
# contradict the reason it exists.
# qc_hod joins the unscoped set: cross-site by definition, so binding it to one
# site would contradict the reason it exists — the same argument that put
# `auditor` here.
_UNSCOPED_REG_ROLES = {"warehouse_user", "logistics", "auditor", "qc_hod"}
# qc is the first DUAL-scope role: a quality inspector belongs either to a
# site or to a warehouse, and which one decides everything they can see.
#
# It needs its own branch rather than a place in either set above, and the
# reason is worth stating: a role that appears in NEITHER set falls through
# /auth/register's if/elif with NO validation at all — no site required, no
# site forbidden, nothing. That is a silent hole, not a safe default, so the
# third category is explicit and requires EXACTLY ONE binding.
_DUAL_SCOPE_REG_ROLES = {"qc"}

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
                scope: str = "access", warehouse_id: str = "",
                extra: dict | None = None) -> str:
    now = _dt.datetime.now(_dt.timezone.utc)
    payload = {"sub": sub, "role": role, "site_id": site_id or "",
               "warehouse_id": warehouse_id or "",
               "scope": scope, "iat": now, "exp": now + ttl,
               **(extra or {}), "env": INSTANCE}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def _decode(token: str, scope: str) -> dict:
    try:
        p = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid or expired token")
    if p.get("scope") != scope:
        raise HTTPException(401, "wrong token scope")
    if not token_env_ok(p):
        raise HTTPException(401, "this sign-in belongs to the other environment "
                                 "(Live / Practice) — sign in again here")
    return p


def token_env_ok(payload: dict, inst: str | None = None) -> bool:
    """Rule 17's claim check. A token minted before the claim existed carries
    none and is read as Live, so deploying this signs nobody out of Live — and
    Practice, which never issued a claim-less token, refuses it. `inst` exists
    so suite TR can ask the Practice question without re-importing the app."""
    return payload.get("env", "production") == (inst or INSTANCE)


# ── Phase 10 Track 1: 2FA that is MANDATORY for privileged roles ────────────
#
# ⚠️ THE CAPABILITY WAS ALREADY SHIPPED; THIS IS THE ENFORCEMENT. `pyotp`
# enrol/verify/disable, the login challenge, the step-up password check and the
# SecurityPage UI have existed since Phase 2. What did not exist was any reason
# for a privileged account to turn it on, so almost none had.
#
# Both settings live in `app_settings` rather than in code, matching
# `mtc_required_category`: widening the net or moving the deadline is an admin
# action, not a deploy.
#
#   mfa_required_roles   CSV. Operator ruling 2026-09-02:
#                        admin, logistics, hod, qc_hod, auditor.
#   mfa_enforced_from    ISO date. Before it, a mandated-but-unenrolled user
#                        signs in normally and is TOLD; on and after it, they
#                        are stopped at the door. ⚠️ ABSENT MEANS WARN-ONLY,
#                        NEVER BLOCK — an unset date must not lock out a
#                        company because somebody deleted a settings row.
MFA_DEFAULT_ROLES = "admin,logistics,hod,qc_hod,auditor"
MFA_GRACE_DAYS = 14
ENROLL_TTL = _dt.timedelta(minutes=15)


async def _mfa_required_roles(session: AsyncSession) -> set[str]:
    v = (await session.execute(select(app_settings_t.c["value"])
         .where(app_settings_t.c["key"] == "mfa_required_roles"))
         ).scalar_one_or_none()
    raw = MFA_DEFAULT_ROLES if v is None else v
    return {r.strip().lower() for r in str(raw).split(",") if r.strip()}


async def _mfa_enforced_from(session: AsyncSession) -> _dt.date | None:
    """The date the hard block begins, or None for warn-only."""
    v = (await session.execute(select(app_settings_t.c["value"])
         .where(app_settings_t.c["key"] == "mfa_enforced_from"))
         ).scalar_one_or_none()
    if not v:
        return None
    try:
        return _dt.date.fromisoformat(str(v).strip()[:10])
    except ValueError:
        return None      # a malformed date must not become an outage


async def mfa_gate(session: AsyncSession, role: str, totp_enabled) -> dict | None:
    """What to do about this account's second factor, or None for "nothing".

    Returns `{"blocked": bool, "enforced_from": str|None}`. The caller mints an
    enrolment token when blocked and attaches a warning otherwise.

    ⚠️ FAILS TOWARDS ACCESS, deliberately. Every uncertain branch here — no
    settings row, an unparseable date, a role not on the list — resolves to
    "let them in". A second factor is a control on top of a password that was
    already checked; a bug in the ROLLOUT of that control must not be able to
    lock an entire company out of its own inventory system.
    """
    if totp_enabled:
        return None
    if (role or "").strip().lower() not in await _mfa_required_roles(session):
        return None
    on = await _mfa_enforced_from(session)
    return {"blocked": bool(on and _dt.date.today() >= on),
            "enforced_from": on.isoformat() if on else None}


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


def _set_refresh_cookie(response: Response, raw: str, ttl: _dt.timedelta) -> None:
    # In production the native apps (Tauri/Capacitor, origin tauri://localhost
    # etc.) hit the API cross-site, and browsers/webviews drop SameSite=lax
    # cookies on cross-site fetches — silent refresh would break after 15 min.
    # SameSite=none requires Secure, which production (HTTPS) already sets;
    # dev stays lax (http). CSRF exposure is contained: /auth/refresh only
    # returns a token in the response body, which CORS keeps unreadable to
    # non-allowed origins.
    production = is_production()
    response.set_cookie(
        REFRESH_COOKIE, raw,
        max_age=int(ttl.total_seconds()),
        httponly=True, samesite="none" if production else "lax",
        secure=production,
        path="/")


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path="/")


async def _open_session(session: AsyncSession, username: str, user_id: int,
                        client_type: str, family_id: uuid.UUID | None = None,
                        replaces: uuid.UUID | None = None) -> tuple[str, uuid.UUID]:
    """RTR: mint a refresh JWT (scope='refresh', unique jti, family claim) and
    insert its tracking row. A fresh login opens a NEW family; a rotation
    passes the existing family_id. Returns (raw_jwt, row_id) — the raw token
    only ever travels in the httpOnly cookie, never a JSON body."""
    ttl = REFRESH_TTLS[client_type]
    jti = uuid.uuid4().hex
    fam = family_id or uuid.uuid4()
    row_id = uuid.uuid4()
    raw = _make_token(username, "", "", ttl, scope="refresh",
                      extra={"jti": jti, "fam": fam.hex, "client": client_type})
    await session.execute(insert(refresh_t).values(
        id=row_id, user_id=user_id, username=username, family_id=fam,
        refresh_token_jti=jti, client_type=client_type,
        expires_at=_now() + ttl, is_revoked=False))
    if replaces is not None:
        await session.execute(update(refresh_t)
                              .where(refresh_t.c["id"] == replaces)
                              .values(is_revoked=True, revoked_at=_now(),
                                      revoke_reason="rotated", replaced_by=row_id))
    return raw, row_id


async def _revoke_family(session: AsyncSession, family_id: uuid.UUID, reason: str) -> int:
    """Revoke every active token in one family (logout, replay detection).
    Does NOT commit — the caller owns the transaction."""
    res = await session.execute(
        update(refresh_t)
        .where(refresh_t.c["family_id"] == family_id,
               refresh_t.c["is_revoked"].is_(False))
        .values(is_revoked=True, revoked_at=_now(), revoke_reason=reason))
    return res.rowcount or 0


async def revoke_all_sessions(session: AsyncSession, username: str, reason: str) -> int:
    """Revoke every active session for a user across ALL families/devices
    (password reset, user delete, admin action). Covers the legacy
    auth_sessions rows too until they age out. Does NOT commit — the caller
    owns the transaction."""
    res = await session.execute(
        update(refresh_t)
        .where(refresh_t.c["username"] == username,
               refresh_t.c["is_revoked"].is_(False))
        .values(is_revoked=True, revoked_at=_now(), revoke_reason=reason))
    legacy = await session.execute(
        update(sessions_t)
        .where(sessions_t.c["username"] == username,
               sessions_t.c["revoked_at"].is_(None))
        .values(revoked_at=_now(), revoke_reason=reason))
    return (res.rowcount or 0) + (legacy.rowcount or 0)


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
    (store_keeper 0 · warehouse/supervisor 1 · hod 2 · logistics 3 · admin 4).

    ⚠️ OVERSIGHT ROLES ARE NOT ON THE LADDER AND NEVER SATISFY A LEVEL CHECK.
    `qc_hod` carries level 2 so it does NOT inherit the level-3 tier, but the
    number alone still admitted it to every `require_level(0|1|2)` endpoint —
    eighty-five of them, including the HOD's approval queue, the Estimator and
    the Man-Hours portal. That is the same trap the role's level was chosen to
    avoid, one rung lower down, and suite BU caught it.

    The level on an oversight role exists to say what it must NOT reach; its
    actual grants are enumerated with `require_roles`. So a level check refuses
    it outright, and anything it genuinely needs (the site list, the warehouse
    names its escalations are addressed to) says so by name.
    """
    async def _dep(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") in QC_OVERSIGHT_ROLES:
            raise HTTPException(
                403, "this action is granted by role, not by rank — an "
                     "oversight account reaches only what names it explicitly")
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
    # The named exception (2026-08-22). A Head of Qualities is level 2 —
    # deliberately BELOW the level-3 ladder, so it inherits none of the
    # require_level endpoints — but its whole job is comparing one site's
    # Surface Shield stock against another's, which a site lock makes
    # impossible. The exemption buys READ reach and nothing else: every write
    # is refused by readonly.py, whatever the level says.
    if user.get("role") in QC_OVERSIGHT_ROLES:
        return None
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


# --- Consuming a resolved scope safely ----------------------------------------
# A scoped user with no site of their own resolves to '', and '' is falsy, so
# `if scope:` silently drops the site filter and hands back every site's rows.
# That is not a corner case: registration forbids warehouse_user and logistics
# accounts from carrying a site, so '' is the steady state for a whole role
# class. Consume a scope through these three helpers rather than testing it.

def site_filter_applies(scope: str | None) -> bool:
    """True when a Site_ID predicate must be emitted. Only an unrestricted
    caller (None) may query without one — '' still filters, and the bound
    value of '' deliberately matches no row."""
    return scope is not None


def site_row_visible(scope: str | None, row_site: str | None) -> bool:
    """Row-level counterpart for direct fetches by id: unrestricted callers see
    every row, a scoped caller only their own site's, and a site-less scoped
    caller none at all."""
    if scope is None:
        return True
    return (row_site or "").strip() == scope


def resolve_site_write(user: dict, requested: str | None) -> str | None:
    """`resolve_site_param` for write paths. Same 403 on a foreign site, plus a
    403 when a scoped caller has no site of their own — such a user must never
    fall back to a client-supplied Site_ID, which would attribute the write to
    a site they do not belong to."""
    site = resolve_site_param(user, requested)
    if site == "":
        raise HTTPException(
            403, "your account is not bound to a site — ask an admin to assign one")
    return site


# --- Warehouse scoping (parallel to site scoping) ------------------------------
def warehouse_scope(user: dict) -> str | None:
    """None → unrestricted (logistics/admin oversight). warehouse_user accounts
    are pinned to their bound Warehouse_ID — '' (unbound) matches nothing.

    Audit A02-F9: this used to be a bare `role != "warehouse_user" → None`, so
    ANY unrecognised role string returned unrestricted. Combined with _public()'s
    unknown-role fallback (level 0), a typo in `users.role` — 'warehouse',
    'Warehouse_User' — produced the worst possible pair: lowest privilege on the
    level ladder AND global warehouse visibility. Unknown roles now fail closed.
    """
    role = user.get("role")
    if role not in ROLE_META:
        return ""                     # unknown role → matches nothing
    if role == "qc":
        # QSEP. A warehouse-bound QC is pinned to its warehouse exactly like a
        # warehouse_user; a SITE-bound QC has no warehouse business at all, so
        # it gets '' (matches nothing) rather than None (sees everything).
        #
        # This branch is not optional. The line below reads "any known role
        # that is not warehouse_user is unrestricted", so the moment 'qc'
        # appeared in ROLE_META it would have inherited GLOBAL warehouse
        # visibility — for a level-1 role whose whole point is being scoped.
        # Adding a role to ROLE_META and forgetting this file is the mistake
        # this comment exists to prevent.
        return (user.get("warehouse_id") or "").strip()
    if role in QC_OVERSIGHT_ROLES:
        # DELIBERATE, not inherited. A Head of Qualities has to see Surface
        # Shield held in a WAREHOUSE as well as on a site — uncertified stock
        # sitting at goods-in is exactly what the role exists to chase. It
        # would have fallen through to `None` below anyway; stating it here is
        # the point, because the line below reads "any known role that is not
        # warehouse_user is unrestricted" and that is how `qc` nearly inherited
        # global warehouse visibility by accident.
        return None
    if role != "warehouse_user":
        return None
    return (user.get("warehouse_id") or "").strip()


# --- QC dual scoping (QSEP) ----------------------------------------------------
def qc_scope(user: dict) -> dict:
    """Where this caller's quality work lives: {"site": …, "warehouse": …}.

    A QC belongs to EITHER a site or a warehouse/logistics department, so
    neither site_scope() nor warehouse_scope() answers the question on its
    own. Returns, for each axis, the single value the caller may read or
    None for "unrestricted on this axis".

    Fails closed, in the same shape as the rest of this module: a `qc`
    account with neither binding resolves to {"site": "", "warehouse": ""},
    and '' is a value that matches no row — never a wildcard. That is the
    class of bug suite AR exists for, and a half-configured QC account is
    exactly how it would arrive here.

    Oversight roles (admin, logistics, auditor — level ≥ 3) are unrestricted
    on both axes. HOD sees its own site's inspections and no warehouse's.
    """
    role = user.get("role")
    if role not in ROLE_META:
        return {"site": "", "warehouse": ""}      # unknown role → nothing
    if role == "qc":
        site = (user.get("site_id") or "").strip()
        wh = (user.get("warehouse_id") or "").strip()
        if site and not wh:
            return {"site": site, "warehouse": ""}
        if wh and not site:
            return {"site": "", "warehouse": wh}
        # Neither binding, or BOTH: fail closed. Both is not a richer
        # permission, it is a misconfigured account — /qc/accounts and
        # /auth/register each refuse to create one, so a row in that state
        # was hand-edited and should not be trusted with either scope.
        return {"site": "", "warehouse": ""}
    if role in QC_OVERSIGHT_ROLES or user.get("level", 0) >= SITE_SCOPE_MIN_LEVEL:
        # qc_hod is level 2 and would otherwise fall to the site-scoped line at
        # the bottom, resolving to '' — a Head of Qualities carries no site, so
        # that reads as "matches nothing" and the role would see an empty
        # dashboard while every number it needed sat one query away.
        return {"site": None, "warehouse": None}
    if role == "warehouse_user":
        return {"site": None, "warehouse": (user.get("warehouse_id") or "").strip()}
    return {"site": (user.get("site_id") or "").strip(), "warehouse": None}


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
    # 'native' (Tauri/Capacitor installs) buys a 90-day refresh family;
    # browsers keep the 7-day default. Anything else is a 422.
    client_type: Literal["web", "native"] = "web"


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
    location: str | None = None  # unscoped roles only (free-text place of work)


async def _fetch_user(session: AsyncSession, username: str):
    return (await session.execute(select(
        users_t.c["id"],
        users_t.c["username"], users_t.c["password_hash"], users_t.c["role"],
        users_t.c["Site_ID"], users_t.c["Warehouse_ID"],
        users_t.c["totp_secret"], users_t.c["totp_enabled"],
    ).where(users_t.c["username"] == username.strip()))).first()


async def _audit(session: AsyncSession, username: str, action: str, details: str) -> None:
    await session.execute(insert(audit_t).values(
        username=username, action_type=action, target_table="users", details=details))
    await session.commit()


# --- phone-number self-service (OTP over WhatsApp) ---------------------------
_OTP_TTL_MIN = 10          # a code is valid for 10 minutes
_OTP_MAX_ATTEMPTS = 5      # wrong guesses before a code is burned


def _gen_otp() -> str:
    """A 6-digit numeric code. Isolated so service_tests can monkeypatch it."""
    return f"{secrets.randbelow(1_000_000):06d}"


def normalize_phone(raw: str) -> str:
    """Canonical GLOBAL phone format: strict E.164 WITH the leading '+'
    (`+<country_code><number>`). Accepts '+', spaces, dashes and parentheses on
    input; stores `+` + 8–15 digits (country code first, so no leading 0).
    Every write path (OTP, admin create/update, register) goes through this;
    only the Meta send boundary strips the '+' (whatsapp._meta_to)."""
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    if not (8 <= len(digits) <= 15) or digits.startswith("0"):
        raise HTTPException(422, "enter a valid phone number in international format, "
                                 "e.g. +966512345678 (country code + 8–15 digits)")
    return "+" + digits


_normalize_phone = normalize_phone  # back-compat alias (older call sites/tests)


class PhoneRequestIn(BaseModel):
    new_number: str


class PhoneVerifyIn(BaseModel):
    new_number: str
    code: str


@router.post("/login", summary="Username + password → JWT (or a 2FA challenge)",
             dependencies=[rate_limit(10, 60)])
async def login(body: LoginIn, response: Response,
                session: AsyncSession = Depends(get_session)):
    # Per-ACCOUNT throttle, checked BEFORE the password is verified: the
    # endpoint's rate_limit() is per-IP and therefore blind to credential
    # stuffing spread across many hosts. See ratelimit.assert_login_allowed.
    assert_login_allowed(body.username)
    # …and the SHARED budget, which is the one that is true across workers.
    # The in-process check above stays because it costs nothing and trips first
    # inside a hot worker; this row is what stops N workers meaning N × 8.
    await assert_login_allowed_shared(session, body.username)
    row = await _fetch_user(session, body.username)
    if row is None:
        _verify_password(body.password, _DUMMY_HASH)  # constant-time-ish
        note_login_failure(body.username)
        await _audit(session, body.username.strip(), "LOGIN_FAILED", "unknown user")
        await note_login_failure_shared(session, body.username)
        raise HTTPException(401, "invalid username or password")
    if not _verify_password(body.password, row.password_hash):
        note_login_failure(body.username)
        await _audit(session, row.username, "LOGIN_FAILED", "bad password")
        await note_login_failure_shared(session, body.username)
        raise HTTPException(401, "invalid username or password")
    # Correct password → the account's failure budget is released immediately,
    # so a burst of wrong guesses never leaves the real owner locked out.
    clear_login_failures(body.username)
    await clear_login_failures_shared(session, body.username)

    if row.role != "admin" and await maintenance_on(session):
        raise HTTPException(503, "GI Hub is in maintenance mode — please try again later")

    if row.totp_enabled:
        # Carry client_type inside the signed MFA token so the 2FA completion
        # opens the right refresh family (the client can't upgrade it later).
        mfa = _make_token(row.username, row.role, row.Site_ID, MFA_TTL, scope="mfa",
                          extra={"client": body.client_type})
        return {"mfa_required": True, "mfa_token": mfa}

    # ── the THIRD outcome: mandated role, no authenticator ──────────────────
    #
    # ⚠️ THE ENROLMENT TOKEN IS SCOPE-LIMITED, AND THAT IS THE WHOLE CONTROL.
    # `_decode` matches the scope exactly and `get_current_user` asks for
    # "access", so this token opens `/auth/2fa/*` and nothing else. Minting a
    # normal access token here — the obvious shortcut — would turn "you must
    # set up 2FA" into a way to skip 2FA, which is worse than not mandating it
    # at all: the account would be exempt AND believed protected.
    gate = await mfa_gate(session, row.role, row.totp_enabled)
    if gate and gate["blocked"]:
        await _audit(session, row.username, "LOGIN_MFA_REQUIRED",
                     f"role={row.role} enforced_from={gate['enforced_from']}")
        return {"enrollment_required": True,
                "enroll_token": _make_token(row.username, row.role, row.Site_ID,
                                            ENROLL_TTL, scope="enroll",
                                            warehouse_id=row.Warehouse_ID),
                "enforced_from": gate["enforced_from"],
                "message": ("Two-factor authentication is required for your "
                            "role. Set up an authenticator app to continue.")}

    token = _make_token(row.username, row.role, row.Site_ID, ACCESS_TTL,
                        warehouse_id=row.Warehouse_ID)
    raw_refresh, _ = await _open_session(session, row.username, row.id, body.client_type)
    await _audit(session, row.username, "LOGIN", f"password client={body.client_type}")  # commits
    _set_refresh_cookie(response, raw_refresh, REFRESH_TTLS[body.client_type])
    out = {"access_token": token, "token_type": "bearer",
           "user": _public(row.username, row.role, row.Site_ID, row.Warehouse_ID)}
    # Inside the grace period the sign-in succeeds and carries the deadline, so
    # the SPA can show a banner. A silent grace period is a grace period nobody
    # uses, and then the deadline arrives as an outage.
    if gate and not gate["blocked"]:
        out["mfa_enrollment_due"] = gate["enforced_from"]
    return out


@router.post("/login/2fa", summary="Complete login with a TOTP code",
             dependencies=[rate_limit(10, 60)])
async def login_2fa(body: TwoFAIn, response: Response,
                    session: AsyncSession = Depends(get_session)):
    p = _decode(body.mfa_token, "mfa")
    row = await _fetch_user(session, p["sub"])
    if row is None:
        raise HTTPException(401, "user not found")
    # A wrong TOTP is a failed sign-in: without this the second factor would
    # be the one unthrottled step, brute-forceable at 10/min/IP over 1e6 codes.
    assert_login_allowed(row.username)
    await assert_login_allowed_shared(session, row.username)
    if not _verify_totp(row.totp_secret, body.code):
        note_login_failure(row.username)
        await _audit(session, row.username, "2FA_FAILED", "invalid code")
        await note_login_failure_shared(session, row.username)
        raise HTTPException(401, "invalid 2FA code")
    clear_login_failures(row.username)
    await clear_login_failures_shared(session, row.username)
    if row.role != "admin" and await maintenance_on(session):
        raise HTTPException(503, "GI Hub is in maintenance mode — please try again later")
    client_type = p.get("client") if p.get("client") in REFRESH_TTLS else "web"
    token = _make_token(row.username, row.role, row.Site_ID, ACCESS_TTL,
                        warehouse_id=row.Warehouse_ID)
    raw_refresh, _ = await _open_session(session, row.username, row.id, client_type)
    await _audit(session, row.username, "LOGIN", f"password+2fa client={client_type}")  # commits
    _set_refresh_cookie(response, raw_refresh, REFRESH_TTLS[client_type])
    return {"access_token": token, "token_type": "bearer",
            "user": _public(row.username, row.role, row.Site_ID, row.Warehouse_ID)}


@router.post("/refresh", summary="Rotate the refresh cookie → a fresh access token (RTR)",
             dependencies=[rate_limit(30, 60)])
async def refresh(response: Response,
                  gi_refresh: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
                  session: AsyncSession = Depends(get_session)):
    if not gi_refresh:
        raise HTTPException(401, "no refresh token")
    # The refresh token is a signed JWT (scope='refresh') carrying its own
    # jti + family claims; a forged/expired/pre-RTR cookie dies right here.
    try:
        p = _decode(gi_refresh, "refresh")
    except HTTPException:
        _clear_refresh_cookie(response)
        raise
    row = (await session.execute(select(refresh_t).where(
        refresh_t.c["refresh_token_jti"] == p.get("jti", "")))).first()
    if row is None:
        _clear_refresh_cookie(response)
        raise HTTPException(401, "invalid refresh token")
    if row.is_revoked:
        # REPLAY: a rotated/revoked token came back. Someone is holding a
        # stale copy (theft evidence) — revoke the ENTIRE family, including
        # the live successor, so both the thief and the stolen-from session
        # die. Other families (the user's other devices) stay untouched.
        n = await _revoke_family(session, row.family_id, "reuse-detected")
        await _audit(session, row.username, "SESSION_REUSE",
                     f"refresh-token replay → revoked family "
                     f"{row.family_id.hex[:8]}… ({n} tokens)")  # commits
        _clear_refresh_cookie(response)
        raise HTTPException(401, "refresh token reuse detected — session family revoked")
    if row.expires_at is not None and row.expires_at <= _now():
        _clear_refresh_cookie(response)
        raise HTTPException(401, "refresh token expired")
    user_row = await _fetch_user(session, row.username)
    if user_row is None:
        await revoke_all_sessions(session, row.username, "user-deleted")
        await session.commit()
        _clear_refresh_cookie(response)
        raise HTTPException(401, "user no longer exists")
    if user_row.role != "admin" and await maintenance_on(session):
        raise HTTPException(503, "GI Hub is in maintenance mode — please try again later")

    # Rotate: new jti in the SAME family, old row revoked pointing at it.
    # The successor inherits the client_type, so a native session keeps its
    # 90-day sliding window.
    raw_new, _ = await _open_session(
        session, row.username, row.user_id, row.client_type,
        family_id=row.family_id, replaces=row.id)
    await session.commit()
    _set_refresh_cookie(response, raw_new, REFRESH_TTLS[row.client_type])
    token = _make_token(user_row.username, user_row.role, user_row.Site_ID, ACCESS_TTL,
                        warehouse_id=user_row.Warehouse_ID)
    return {"access_token": token, "token_type": "bearer",
            "user": _public(user_row.username, user_row.role, user_row.Site_ID, user_row.Warehouse_ID)}


@router.post("/logout", summary="Revoke the current refresh-token family")
async def logout(response: Response,
                 gi_refresh: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
                 session: AsyncSession = Depends(get_session)):
    if gi_refresh:
        try:
            p = jwt.decode(gi_refresh, JWT_SECRET, algorithms=[JWT_ALG])
            if not token_env_ok(p):
                raise jwt.InvalidTokenError("other environment")
            row = (await session.execute(select(refresh_t.c["family_id"]).where(
                refresh_t.c["refresh_token_jti"] == p.get("jti", "")))).first()
            if row is not None:
                # The whole family: logout means THIS device's session chain
                # ends, not just the newest token in it.
                await _revoke_family(session, row.family_id, "logout")
        except jwt.PyJWTError:
            # Pre-RTR opaque cookie — best-effort revoke of the legacy row.
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


async def _admin_site_names(session: AsyncSession) -> list[str]:
    """Admin-created sites (system_settings category='Site') — the ONLY values
    a scoped registrant may pick (same source as the admin console CRUD)."""
    rows = (await session.execute(
        select(sysset_t.c["value"]).where(sysset_t.c["category"] == "Site")
        .order_by(sysset_t.c["id"]))).all()
    return [r[0] for r in rows]


@router.get("/register/sites",
            summary="Public site list for the Request Access form "
                    "(IDs only; scoped roles must pick from these)",
            dependencies=[rate_limit(30, 60)])
async def register_sites(session: AsyncSession = Depends(get_session)):
    return {"sites": await _admin_site_names(session)}


@router.get("/register/warehouses",
            summary="Public warehouse list for the Request Access form "
                    "(a warehouse-bound QC must pick from these)",
            dependencies=[rate_limit(30, 60)])
async def register_warehouses(session: AsyncSession = Depends(get_session)):
    """Sibling of /register/sites, added with the QC role (QSEP).

    `qc` is the first role that may bind to a WAREHOUSE from the public
    Request Access form, and a free-text warehouse would land an
    unmatchable string in `pending_users.Warehouse_ID` for an admin to
    puzzle over at approval time. IDs and names only — no contacts, no
    counts: this endpoint is unauthenticated, and a warehouse list is
    already the minimum an applicant must see to choose one.
    """
    wh = _MD.tables["warehouses"]
    rows = (await session.execute(
        select(wh.c["Warehouse_ID"], wh.c["Name"])
        .where(func.coalesce(wh.c["status"], "active") == "active")
        .order_by(wh.c["Warehouse_ID"]))).all()
    return {"warehouses": [{"id": r[0], "name": r[1]} for r in rows]}


@router.get("/username-available",
            summary="Is this username free? (Bloom-accelerated, advisory)",
            dependencies=[rate_limit(60, 60)])
async def username_available(u: str,
                             session: AsyncSession = Depends(get_session)):
    """Live availability for the registration form, as the person types.

    ⚠️ ADVISORY, AND THE ANSWER IS DELIBERATELY ASYMMETRIC. A Bloom filter can
    say "definitely not in the set" exactly, and "maybe" only probabilistically
    — so a FREE name is answered from memory with no query at all, and a name
    that looks taken is CONFIRMED against both registries before this endpoint
    will say so. That is the right way round: somebody trying candidate names is
    asking about free ones, and being told "taken" about a name that is actually
    free would be the one wrong answer they cannot work around.

    Nothing is reserved by asking. `POST /auth/register` re-checks, and the
    `UNIQUE` indexes on `users.username` and `pending_users.username` remain the
    only things that decide — see `services/bloom.py` for why that separation is
    load-bearing rather than merely tidy.
    """
    from .services import bloom
    uname = (u or "").strip()
    if not uname:
        raise HTTPException(422, "username is required")

    bf = bloom.get(bloom.USERNAMES)
    if bf is not None and not bf.probably_present(uname):
        # DEFINITELY absent from the snapshot — no query needed.
        return {"username": uname, "available": True, "checked": "bloom"}

    # Either the filter is not built yet, or it said "maybe". Confirm.
    taken = (await session.execute(
        select(func.count()).select_from(users_t)
        .where(func.lower(users_t.c["username"]) == uname.lower()))).scalar_one()
    if not taken:
        taken = (await session.execute(
            select(func.count()).select_from(pending_users_t)
            .where(func.lower(pending_users_t.c["username"])
                   == uname.lower()))).scalar_one()
    return {"username": uname, "available": not taken, "checked": "db"}


@router.post("/register", status_code=201,
             summary="Request access → a pending_users row for an admin to approve",
             dependencies=[rate_limit(5, 60)])
async def register(body: RegisterIn, session: AsyncSession = Depends(get_session)):
    uname = (body.username or "").strip()
    if not uname:
        raise HTTPException(422, "username is required")
    # One policy for every credential-setting path (audit A03-F11). Registration
    # used to carry its own literal 6 while admin create/reset used MIN_PW, so
    # the weakest door set the real floor. Existing passwords keep working —
    # login has no length check; the policy binds new and reset credentials.
    from .admin import assert_password_ok
    assert_password_ok(body.password)
    if body.role not in _REGISTERABLE_ROLES:
        raise HTTPException(422, f"role must be one of {sorted(_REGISTERABLE_ROLES)}")

    taken = (await session.execute(select(func.count()).select_from(users_t)
             .where(users_t.c["username"] == uname))).scalar_one()
    if taken:
        raise HTTPException(409, "username already exists")

    # T4 — role-conditional site rules (mirrored in the React form; enforced
    # here so the API fails closed regardless of client). AFTER the username
    # check so a taken name keeps its historical 409 contract.
    site = (body.site_id or "").strip()
    location = (body.location or "").strip()
    warehouse = (body.warehouse_id or "").strip()
    if body.role in _SCOPED_REG_ROLES:
        if not site:
            raise HTTPException(422, f"{body.role} requires a site")
        if site not in await _admin_site_names(session):
            raise HTTPException(422, f"unknown site {site!r} — pick an admin-created site")
        location = ""  # scoped users are identified by their site, not free text
    elif body.role in _DUAL_SCOPE_REG_ROLES:
        # QSEP — a quality inspector works at a site OR at a warehouse.
        # EXACTLY one, because qc_scope() reads the pair to decide what the
        # account can see and treats "both" as a misconfiguration it must
        # fail closed on. Rejecting it here is how that state never exists.
        if bool(site) == bool(warehouse):
            raise HTTPException(
                422, f"{body.role} needs EXACTLY ONE of site or warehouse — "
                     "a quality inspector belongs to a site or to a warehouse, "
                     "not to both and not to neither")
        if site and site not in await _admin_site_names(session):
            raise HTTPException(422, f"unknown site {site!r} — pick an admin-created site")
        location = "" if site else location
    elif body.role in _UNSCOPED_REG_ROLES:
        if site:
            raise HTTPException(422,
                                f"{body.role} is a global role — no site; "
                                "use the location field instead")

    pw_hash = bcrypt.hashpw(body.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    values = dict(username=uname, password_hash=pw_hash, role=body.role,
                  Site_ID=site,
                  Phone_Number=(normalize_phone(body.phone_number) if body.phone_number else None),
                  Warehouse_ID=(warehouse or None), status="pending",
                  Location=(location or None))
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
    # The write happened here, so this process's filter learns about it now
    # rather than at the next refresh. Other workers close the gap on their
    # own timer; the UNIQUE index covers the interval for both.
    from .services import bloom as _bloom
    _bloom.add(_bloom.USERNAMES, uname)
    await _audit(session, uname, "REQUEST_ACCESS",
                 f"role={body.role} site={site or '-'} location={location or '-'}")
    return {"requested": True, "username": uname}


# --- 2FA self-enrollment -----------------------------------------------------
# Login already *verifies* TOTP and an admin can *reset* it; this lets a user
# turn 2FA on for their own account. The secret is stored on enroll but 2FA is
# only enabled once a code is verified, so a half-finished enroll never locks
# anyone out (login only challenges when totp_enabled = 1).
class CodeIn(BaseModel):
    code: str


class TwoFaEnrollIn(BaseModel):
    password: str


# --- 2FA guess budget (audit A03-F3) ------------------------------------------
# A per-USERNAME failure counter sitting under the per-IP rate limit on the
# /2fa/* routes. The IP limit alone is defeatable by rotating CF-Connecting-IP
# (audit A03-F6); a username is not header-controllable, so this is the ceiling
# that actually holds. Only FAILURES count — a correct code costs nothing and
# clears the record. Per-process like the rest of ratelimit.py; a shared store
# is the documented Phase 3 fix.
_TOTP_MAX_ATTEMPTS = 5
_TOTP_WINDOW_SECONDS = 900
_totp_failures: dict[str, deque[float]] = defaultdict(deque)


def _totp_recent(username: str) -> deque[float]:
    now = _time.time()
    q = _totp_failures[username]
    while q and now - q[0] > _TOTP_WINDOW_SECONDS:
        q.popleft()
    return q


async def _check_totp_attempts_shared(session, username: str) -> None:
    """The cross-worker half of the second-factor attempt budget.

    ⚠️ THE MOST IMPORTANT OF THE FOUR LIMITERS THAT WERE PER-PROCESS. Under
    `uvicorn --workers 4` the in-memory budget of 5 became an effective 20, and
    `_verify_totp` runs at `valid_window=1` — three 6-digit codes are acceptable
    at any instant. A ceiling on brute-forcing the SECOND factor is not a place
    to be four times looser than documented.

    Keyed on the USERNAME, which is not header-controllable, so unlike the
    per-IP limit it cannot be reset by rotating `CF-Connecting-IP`.
    """
    from .ratelimit import check_bucket_shared
    await check_bucket_shared(
        session, f"totp:{(username or '').strip().lower()}",
        _TOTP_MAX_ATTEMPTS, _TOTP_WINDOW_SECONDS,
        message="too many 2FA attempts — please wait a few minutes and try again")


async def _clear_totp_attempts_shared(session, username: str) -> None:
    """A correct code ends the throttle immediately, across every worker."""
    from .ratelimit import clear_bucket_shared
    await clear_bucket_shared(session, f"totp:{(username or '').strip().lower()}")


def _check_totp_attempts(username: str) -> None:
    q = _totp_recent(username)
    if len(q) >= _TOTP_MAX_ATTEMPTS:
        retry = int(_TOTP_WINDOW_SECONDS - (_time.time() - q[0])) + 1
        raise HTTPException(429, "too many invalid 2FA codes — try again later",
                            headers={"Retry-After": str(retry)})


def _burn_totp_attempt(username: str) -> None:
    _totp_recent(username).append(_time.time())


def _clear_totp_attempts(username: str) -> None:
    _totp_failures.pop(username, None)


def _qr_data_uri(uri: str) -> str:
    import base64
    import io

    import qrcode
    buf = io.BytesIO()
    qrcode.make(uri).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


async def enroll_or_current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """Accepts a normal access token OR an enrolment-scoped one.

    ⚠️ USED ONLY BY THE THREE `/2fa/*` ROUTES, and that limit is the control.
    An `enroll`-scoped token exists so a mandated user who has been refused a
    session can still reach the enrolment flow; it must reach NOTHING ELSE, or
    "you must set up 2FA" becomes a way to skip it. Everything else in the app
    keeps `get_current_user`, whose `_decode(..., "access")` rejects this token
    outright.
    """
    if cred is None:
        raise HTTPException(401, "not authenticated")
    try:
        p = _decode(cred.credentials, "access")
    except HTTPException:
        p = _decode(cred.credentials, "enroll")
    return _public(p["sub"], p.get("role"), p.get("site_id"),
                   p.get("warehouse_id", ""))


@router.get("/2fa/status", summary="Is 2FA enabled for the current user?")
async def twofa_status(user: dict = Depends(enroll_or_current_user),
                       session: AsyncSession = Depends(get_session)):
    row = await _fetch_user(session, user["username"])
    return {"enabled": bool(row and row.totp_enabled)}


@router.post("/2fa/enroll", summary="Begin 2FA enrollment → secret + QR (not enabled yet)",
             dependencies=[rate_limit(5, 60)])
async def twofa_enroll(body: TwoFaEnrollIn = Body(...),
                       user: dict = Depends(enroll_or_current_user),
                       session: AsyncSession = Depends(get_session)):
    import pyotp
    row = await _fetch_user(session, user["username"])
    if row is None:
        raise HTTPException(404, "user not found")
    if row.totp_enabled:
        raise HTTPException(409, "2FA is already enabled")
    # Step-up (audit A03-F8): a bearer token alone used to be enough to bind a
    # NEW authenticator to the account, so a stolen 15-minute access token could
    # be converted into durable persistence the owner would only notice at their
    # next login. Re-prove the password before writing a secret.
    if not _verify_password(body.password, row.password_hash):
        await _audit(session, user["username"], "2FA_ENROLL_DENIED",
                     "step-up password check failed")
        raise HTTPException(403, "password re-entry required to enroll 2FA")
    secret = pyotp.random_base32()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user["username"], issuer_name="GI Hub")
    await session.execute(update(users_t).where(users_t.c["username"] == user["username"])
                          .values(totp_secret=secret))
    await session.commit()
    await _audit(session, user["username"], "2FA_ENROLL", "enrollment started")
    return {"secret": secret, "otpauth_uri": uri, "qr": _qr_data_uri(uri)}


@router.post("/2fa/verify", summary="Confirm a code to enable 2FA",
             dependencies=[rate_limit(5, 60)])
async def twofa_verify(body: CodeIn, user: dict = Depends(enroll_or_current_user),
                       session: AsyncSession = Depends(get_session)):
    row = await _fetch_user(session, user["username"])
    if row is None or not row.totp_secret:
        raise HTTPException(409, "no enrollment in progress — call /2fa/enroll first")
    _check_totp_attempts(user["username"])
    await _check_totp_attempts_shared(session, user["username"])
    if not _verify_totp(row.totp_secret, body.code):
        _burn_totp_attempt(user["username"])
        raise HTTPException(400, "invalid 2FA code")
    _clear_totp_attempts(user["username"])
    await _clear_totp_attempts_shared(session, user["username"])
    await session.execute(update(users_t).where(users_t.c["username"] == user["username"])
                          .values(totp_enabled=1))
    await session.commit()
    await _audit(session, user["username"], "2FA_ENABLED", "verified + enabled")
    # No session is opened here. A user who arrived on an enrolment token has
    # been refused one, and minting it now would make enrolment itself the
    # bypass; they sign in again, this time through the TOTP challenge, which
    # also proves the authenticator they just bound actually works.
    return {"enabled": True, "next": "sign in again with your 2FA code"}


@router.post("/2fa/disable", summary="Disable 2FA (requires a valid current code)",
             dependencies=[rate_limit(5, 60)])
async def twofa_disable(body: CodeIn, user: dict = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)):
    row = await _fetch_user(session, user["username"])
    if row is None or not row.totp_enabled:
        raise HTTPException(409, "2FA is not enabled")
    # Per-user attempt budget on top of the per-IP rate limit: _verify_totp runs
    # valid_window=1, so three 6-digit codes are acceptable at any instant, and
    # an attacker rotating CF-Connecting-IP gets a fresh IP bucket per request.
    # The username is not header-controllable, so this ceiling actually holds.
    _check_totp_attempts(user["username"])
    await _check_totp_attempts_shared(session, user["username"])
    if not _verify_totp(row.totp_secret, body.code):
        _burn_totp_attempt(user["username"])
        await _audit(session, user["username"], "2FA_DISABLE_FAILED", "invalid code")
        raise HTTPException(400, "invalid 2FA code")
    _clear_totp_attempts(user["username"])
    await _clear_totp_attempts_shared(session, user["username"])
    await session.execute(update(users_t).where(users_t.c["username"] == user["username"])
                          .values(totp_secret=None, totp_enabled=0))
    await session.commit()
    await _audit(session, user["username"], "2FA_DISABLED", "disabled by user")
    return {"disabled": True}


@router.get("/phone", summary="My phone number on file")
async def get_phone(user: dict = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
    n = (await session.execute(select(users_t.c["Phone_Number"])
         .where(users_t.c["username"] == user["username"]))).scalar_one_or_none()
    return {"phone_number": (n or None)}


async def _issue_phone_code(session: AsyncSession, *, username: str, number: str,
                            stage: str, send_to: str) -> tuple[bool, str | None]:
    """Supersede any active code, insert a fresh one for `stage`, and
    (best-effort) WhatsApp it to `send_to`. Returns (sent, error)."""
    from .services import whatsapp as wa  # lazy: avoids an auth↔whatsapp import cycle
    code = _gen_otp()
    code_hash = bcrypt.hashpw(code.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    expires = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None) + _dt.timedelta(minutes=_OTP_TTL_MIN)
    # Single active code per user across both stages.
    await session.execute(update(phone_otp_t).where(
        (phone_otp_t.c["username"] == username) & phone_otp_t.c["consumed_at"].is_(None)
    ).values(consumed_at=func.now()))
    await session.execute(insert(phone_otp_t).values(
        username=username, new_number=number, code_hash=code_hash,
        stage=stage, expires_at=expires, attempts=0))
    # Send is best-effort; the row is committed either way so a transient send
    # failure doesn't strand the user mid-flow.
    sent, err = False, None
    try:
        res = await wa.send_otp(session, to=send_to, code=code, created_by=username)
        sent = res.get("status") == "sent"
        err = res.get("error")
    except Exception as e:  # noqa: BLE001
        sent, err = False, str(e)[:200]
    await session.commit()
    return sent, err


@router.post("/phone/request-otp", summary="Send a 6-digit code to verify a phone change",
             dependencies=[rate_limit(5, 60)])
async def request_phone_otp(body: PhoneRequestIn, request: Request,
                            user: dict = Depends(get_current_user),
                            session: AsyncSession = Depends(get_session)):
    """Dual-OTP phone change (UAT refinement).
    Step A (stage='old'): with a number on file, the first code goes to the
    OLD (currently registered) number — only whoever holds the existing device
    can authorize moving the account (a stolen web session can't silently
    redirect WhatsApp alerts). Verifying it does NOT save anything; it issues
    a second code to the NEW number (stage='new').
    Step B (stage='new'): the code proves the NEW number actually receives
    WhatsApp before it is committed, so a typo can never lock the user out.
    First-time setup (no number on file) has no old device to prove — it
    starts directly at stage='new'."""
    from .services import whatsapp as wa  # lazy: avoids an auth↔whatsapp import cycle
    number = normalize_phone(body.new_number)
    # Phase 8-2 — SMS-toll-fraud guard: max 3 OTP requests per HOUR, counted
    # BOTH per source IP and per target phone number (an attacker rotating
    # IPs still exhausts the number's budget, and vice versa). Checked BEFORE
    # anything else so even misconfigured/failed sends burn quota. Relaxed in
    # hermetic test envs — see ratelimit.strict_limits_enabled().
    if strict_limits_enabled():
        from .ratelimit import check_bucket_shared
        check_bucket(f"otp:ip:{client_ip(request)}", 3, 3600,
                     "too many verification codes requested from this address — try again later")
        check_bucket(f"otp:phone:{number}", 3, 3600,
                     "too many verification codes for this number — try again later")
        # ⚠️ AND THE SAME TWO BUDGETS ACROSS WORKERS. Per-process, "3 codes per
        # hour" was 12 on a four-worker box — and this is the toll-fraud guard,
        # where every extra send is a real message somebody pays for.
        await check_bucket_shared(
            session, f"otp:ip:{client_ip(request)}", 3, 3600,
            "too many verification codes requested from this address — try again later")
        await check_bucket_shared(
            session, f"otp:phone:{number}", 3, 3600,
            "too many verification codes for this number — try again later")
    if not wa.enabled():
        # Fail BEFORE creating a code row — nothing to strand, clear guidance.
        raise HTTPException(503, "WhatsApp is not configured on the server — "
                                 "ask an admin to set your number directly.")
    current = ((await session.execute(select(users_t.c["Phone_Number"])
                .where(users_t.c["username"] == user["username"]))).scalar_one_or_none()
               or "").strip()
    stage = "old" if current else "new"
    sent, err = await _issue_phone_code(session, username=user["username"],
                                        number=number, stage=stage,
                                        send_to=current or number)
    await _audit(session, user["username"], "PHONE_OTP_REQUEST",
                 f"stage={stage} → {'old number on file' if current else 'new number (first-time)'} sent={sent}")
    return {"sent": sent, "expires_in": _OTP_TTL_MIN * 60, "stage": stage,
            "sent_to": "current" if current else "new",
            **({} if sent else {"error": err or "send failed"})}


@router.post("/phone/verify-otp", summary="Verify the code → next stage or save the number",
             dependencies=[rate_limit(10, 60)])
async def verify_phone_otp(body: PhoneVerifyIn, user: dict = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)):
    """Dual-OTP: a correct stage='old' code authorizes the change and issues a
    second code to the NEW number (nothing saved yet); a correct stage='new'
    code commits users.Phone_Number. See request_phone_otp for the flow."""
    number = _normalize_phone(body.new_number)
    now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    row = (await session.execute(select(
        phone_otp_t.c["id"], phone_otp_t.c["code_hash"], phone_otp_t.c["expires_at"],
        phone_otp_t.c["attempts"], phone_otp_t.c["stage"]
    ).where(
        (phone_otp_t.c["username"] == user["username"])
        & (phone_otp_t.c["new_number"] == number)
        & phone_otp_t.c["consumed_at"].is_(None)
    ).order_by(phone_otp_t.c["id"].desc()).limit(1))).first()
    if row is None:
        raise HTTPException(404, "no pending code for this number — request a new one")
    if row.expires_at < now:
        await session.execute(update(phone_otp_t).where(phone_otp_t.c["id"] == row.id)
                              .values(consumed_at=func.now()))
        await session.commit()
        raise HTTPException(400, "this code has expired — request a new one")
    if (row.attempts or 0) >= _OTP_MAX_ATTEMPTS:
        await session.execute(update(phone_otp_t).where(phone_otp_t.c["id"] == row.id)
                              .values(consumed_at=func.now()))
        await session.commit()
        raise HTTPException(429, "too many attempts — request a new code")
    if not bcrypt.checkpw(body.code.encode("utf-8"), row.code_hash.encode("utf-8")):
        await session.execute(update(phone_otp_t).where(phone_otp_t.c["id"] == row.id)
                              .values(attempts=(row.attempts or 0) + 1))
        await session.commit()
        raise HTTPException(400, "incorrect code")
    # Correct → consume this code.
    await session.execute(update(phone_otp_t).where(phone_otp_t.c["id"] == row.id)
                          .values(consumed_at=func.now()))
    if (row.stage or "new") == "old":
        # Step A passed: the old device authorized the change. Now prove the
        # NEW number can receive WhatsApp before anything is committed.
        sent, err = await _issue_phone_code(session, username=user["username"],
                                            number=number, stage="new", send_to=number)
        await _audit(session, user["username"], "PHONE_OTP_STAGE2",
                     f"old-number code verified → second code to {number} sent={sent}")
        return {"updated": False, "stage": "new", "sent": sent, "sent_to": "new",
                "expires_in": _OTP_TTL_MIN * 60,
                **({} if sent else {"error": err or "send failed"})}
    # Step B passed: the new number verified end-to-end — commit it.
    await session.execute(update(users_t).where(users_t.c["username"] == user["username"])
                          .values(Phone_Number=number))
    await session.commit()
    await _audit(session, user["username"], "PHONE_UPDATED", f"verified → {number}")
    return {"updated": True, "phone_number": number}
