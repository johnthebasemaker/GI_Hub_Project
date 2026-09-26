"""
backend/api/readonly.py — the view-only (Auditor) enforcement boundary.

An `auditor` may read anything its level reaches and change nothing. That is
enforced HERE, once, as ASGI middleware keyed on the HTTP method — not as a
dependency sprinkled over the ~109 mutating routes.

**Why method-based and not per-endpoint.** A per-endpoint annotation is only as
good as the developer's memory: the next `@router.post` anyone adds is a hole,
and it fails OPEN — the endpoint works, nobody notices, and the role silently
stops being read-only. Keying on the method inverts that. Every POST / PUT /
PATCH / DELETE is refused unless it appears on the small allowlist below, so a
new endpoint is closed the moment it is written, and opening one is a
deliberate edit to this file with a reason attached.

**The allowlist** is only for requests that are read-only *despite* using a
mutating verb, plus the operations a person needs to look after their own
account. It is exhaustive, and it is matched exactly (or by an explicit
prefix), never by a substring:

  session lifecycle   /auth/login, /auth/login/2fa, /auth/refresh, /auth/logout
  own credentials     /auth/2fa/*, /auth/phone/*  (write only the caller's row)
  compute-only POSTs  /sme/plan/cascade, /sme/plan/export, /sme/export/rows
  AI reads            /ai/assistant, /ai/query, /ai/nl-search, /ai/insights,
                      /ai/eod-summary  — these stream answers over POST because
                      the question does not fit in a query string

Everything else — every entry, approval, import, sync, upload, delete, and
every future one — is a 403 with a message that names the role. Note what is
deliberately NOT here: `/reports/{key}/whatsapp` renders a report an auditor
may read, but then SENDS it to a phone number, so it stays blocked; and
`/admin/users/*/reset-password` is a credential change, not self-service.

**This is the boundary, not the UI.** The SPA hides what an auditor cannot do,
but the hiding is a courtesy; this guard is what makes it true. Suite BD drives
real HTTP against it, including a revert check that fails if the middleware is
ever unregistered.
"""
from __future__ import annotations

from starlette.responses import JSONResponse

# Read-only roles. Each may read what its level reaches and write only what
# its own allowlist names — the lists are PER ROLE, because "read-only" means
# different things to different accounts:
#
#   auditor — reads across every site and writes nothing at all. Its extras are
#             compute-only POSTs (a cascade, an export) and streamed AI answers,
#             none of which touch a row.
#   qc_hod  — Head of Qualities (2026-08-22). Oversight, not operation: it reads
#             Surface Shield material across every site and the only thing it
#             may WRITE is a message — an escalation asking somebody who can act
#             to act. It cannot approve an inspection, decide a DN, move stock
#             or raise a PR, and the fact that its allowlist is three paths long
#             is the whole security story of the role.
READ_ONLY_ROLES = frozenset({"auditor", "qc_hod"})

# Methods that cannot change server state. HEAD/OPTIONS matter for CORS
# preflight — blocking OPTIONS would break the browser before it ever asked.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Exact paths that mutate nothing but the caller's own session/credentials, or
# that only compute and render. Compared after stripping the optional /api/v1
# prefix and any trailing slash.
# Shared by EVERY read-only role: signing in, and looking after your own
# credentials. Without these an account cannot even reach the thing it may read.
_BASE_EXACT = frozenset({
    # session lifecycle — without these an auditor cannot even sign in
    "/auth/login",
    "/auth/login/2fa",
    "/auth/refresh",
    "/auth/logout",
    # own account: 2FA and phone-change OTP write only to the caller's own row
    "/auth/2fa/enroll",
    "/auth/2fa/verify",
    "/auth/2fa/disable",
    "/auth/phone/request-otp",
    "/auth/phone/verify-otp",
    # Phase 14d: "I have seen this announcement" — one row in
    # feature_announcement_reads keyed by the caller's own username. Without it
    # a view-only account would be shown the same What's-new panel forever.
    "/announcements/read",
})

# Per-role extras. Adding a path here is a deliberate edit with a reason
# attached, which is the point — the default for anything not listed is 403.
_ROLE_EXACT = {
    "auditor": frozenset({
        # pure computation / document rendering — no row is touched
        "/sme/plan/cascade",
        "/sme/plan/export",
        "/sme/export/rows",
    }),
    "qc_hod": frozenset(),
}

# Prefixes for read-shaped POSTs. Each is a question whose body is too big for
# a query string; none of them write. Kept short and reviewed as a unit.
_ROLE_PREFIXES = {
    "auditor": (
        "/ai/assistant",
        "/ai/query",
        "/ai/nl-search",
        "/ai/insights",
        "/ai/eod-summary",
    ),
    # ⚠️ THREE PATHS, AND THEY ALL SEND A MESSAGE. A QC-HOD raises an
    # escalation, resolves one they raised, and tunes their own stagnation
    # thresholds. Every one of those writes to a qc_hod-owned table; none
    # touches stock, an inspection decision, a DN or a PR. `/qc-hod/` is NOT
    # listed as a bare prefix on purpose — that would open any future POST
    # under it by accident, which is exactly the fail-open shape this whole
    # module exists to avoid.
    "qc_hod": (
        "/qc-hod/escalations",
        "/qc-hod/settings",
        "/ai/assistant",
    ),
}

_API_PREFIX = "/api/v1"


def normalize_path(path: str) -> str:
    """Strip the single-origin /api/v1 mount and any trailing slash, so a rule
    written once covers both the bare and the proxied form of a route."""
    p = path or "/"
    if p.startswith(_API_PREFIX):
        p = p[len(_API_PREFIX):] or "/"
    if len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/") or "/"
    return p


def is_read_only_role(role: str | None) -> bool:
    return (role or "") in READ_ONLY_ROLES


def is_allowed_write(path: str, role: str = "auditor") -> bool:
    """True if this mutating-verb path is one of THIS ROLE's documented
    exceptions. The role defaults to `auditor` so the original single-role
    callers keep their meaning."""
    p = normalize_path(path)
    if p in _BASE_EXACT:
        return True
    if p in _ROLE_EXACT.get(role, frozenset()):
        return True
    prefixes = _ROLE_PREFIXES.get(role, ())
    return bool(prefixes) and p.startswith(prefixes)


def blocks_request(role: str | None, method: str, path: str) -> bool:
    """The whole decision, in one pure function so tests can exercise every
    branch without spinning up an app."""
    if not is_read_only_role(role):
        return False
    if (method or "").upper() in SAFE_METHODS:
        return False
    return not is_allowed_write(path, role or "")


def _role_from_request(request) -> str | None:
    """Best-effort role from the bearer token. Deliberately tolerant: a missing
    or bad token is NOT this middleware's problem — the route's own auth
    dependency will 401 it. We only care about a VALID token whose role is
    read-only, so a decode failure simply means "not our business"."""
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    try:
        from .auth import JWT_ALG, JWT_SECRET
        import jwt as _jwt
        payload = _jwt.decode(auth[7:].strip(), JWT_SECRET, algorithms=[JWT_ALG])
    except Exception:  # noqa: BLE001 — invalid token → let the route reject it
        return None
    if payload.get("scope") != "access":
        return None
    return payload.get("role")


async def read_only_guard(request, call_next):
    """ASGI middleware: refuse state-changing requests from a read-only role."""
    if request.method.upper() not in SAFE_METHODS:
        role = _role_from_request(request)
        if blocks_request(role, request.method, request.url.path):
            label = "Head of Qualities" if role == "qc_hod" else "Auditor"
            return JSONResponse(
                status_code=403,
                content={"detail": f"your account is view-only ({label}) — "
                                   f"this action changes data and is not "
                                   f"permitted"},
            )
    return await call_next(request)
