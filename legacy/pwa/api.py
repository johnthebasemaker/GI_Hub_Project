"""
pwa/api.py — FastAPI service for the offline scan-and-stage PWA
================================================================
Endpoints (all JSON, all rate-limit-light):

  POST /api/login                 — username+password → opaque bearer token
  GET  /api/inventory             — minimal catalogue (SAP_Code, desc, UOM)
  POST /api/pending_issues/batch  — bulk upload of staged consumption rows
  GET  /api/whoami                — token introspection (for the PWA UI)
  GET  /healthz                   — uptime probe

  GET  /                          — serves index.html (the PWA shell)
  GET  /sw.js, /app.webmanifest   — service worker + install manifest

The service uses only the pure-Python functions in `database.py` and
`auth.py`. It NEVER touches Streamlit. Tests run with FastAPI's TestClient
against an in-memory schema, the same fixture style as the rest of the
suite.

Why a separate process
----------------------
Streamlit and uvicorn both own event loops; mixing them is fragile. Sharing
the SQLite file (already WAL-enabled in `database.get_connection`) gives
both processes safe concurrent writes with `busy_timeout=5000`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from auth import verify_password
from database import (
    get_connection,
    init_db,
    pwa_issue_token,
    pwa_verify_token,
    pwa_stage_pending_issues,
    log_audit_action,
)


# ---------------------------------------------------------------------------
# APP SETUP
# ---------------------------------------------------------------------------
# init_db is idempotent — calling it here means the PWA service self-heals
# the schema (including the pwa_tokens table) without depending on Streamlit
# having been started first.
init_db()

app = FastAPI(
    title="GI Lightning Hub — PWA",
    version="1.0.0",
    description="Offline-capable scan-and-stage companion for the warehouse floor.",
    docs_url="/api/docs",
    redoc_url=None,
)


_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# AUTH DEPENDENCY
# ---------------------------------------------------------------------------
def _require_token(authorization: str | None = Header(default=None)) -> dict:
    """
    Extract & verify the bearer token. Returns the user dict on success.
    Raises 401 otherwise. Used as a FastAPI dependency on protected routes.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    user = pwa_verify_token(token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# ---------------------------------------------------------------------------
# SCHEMAS
# ---------------------------------------------------------------------------
class LoginIn(BaseModel):
    username: str
    password: str


class LoginOut(BaseModel):
    token: str
    username: str
    role: str
    site_id: str


class PendingIssueIn(BaseModel):
    SAP_Code: str = Field(..., min_length=1)
    Quantity: float = Field(..., gt=0)
    Date: str | None = None
    Work_Type: str | None = None
    Remarks: str | None = None
    Issued_To: str | None = None
    Tank_No: str | None = None
    PR_Number: str | None = None
    # client-generated dedupe id (lets the PWA retry safely)
    client_id: str | None = None


class BatchIn(BaseModel):
    items: list[PendingIssueIn]


class BatchOut(BaseModel):
    inserted: int


# ---------------------------------------------------------------------------
# AUTH ENDPOINTS
# ---------------------------------------------------------------------------
@app.post("/api/login", response_model=LoginOut)
def login(body: LoginIn) -> LoginOut:
    """
    Authenticate the user against the `users` table (same bcrypt check the
    Streamlit login uses) and issue a fresh bearer token.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT username, password_hash, role, Site_ID FROM users WHERE username = ?",
            (body.username,),
        ).fetchone()
        if not row or not verify_password(body.password, row[1]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )
        token = pwa_issue_token(body.username, conn=conn)
        try:
            log_audit_action(
                body.username, "PWA_LOGIN", "users",
                "Token issued via /api/login",
            )
        except Exception:
            pass
        return LoginOut(
            token=token, username=row[0], role=row[2], site_id=row[3] or "HQ"
        )
    finally:
        conn.close()


@app.get("/api/whoami")
def whoami(user: dict = Depends(_require_token)) -> dict:
    """Token introspection — used by the PWA on first load to validate."""
    return {"username": user["username"], "role": user["role"], "site_id": user["site_id"]}


# ---------------------------------------------------------------------------
# DATA ENDPOINTS
# ---------------------------------------------------------------------------
@app.get("/api/inventory")
def list_inventory(user: dict = Depends(_require_token)) -> dict:
    """
    Minimal inventory catalogue (SAP_Code, Equipment_Description, UOM).
    Cached client-side by the PWA so subsequent scans work offline.
    """
    conn = get_connection()
    try:
        df = pd.read_sql(
            "SELECT SAP_Code, Equipment_Description, UOM, Material_Code "
            "FROM inventory ORDER BY SAP_Code",
            conn,
        )
    finally:
        conn.close()
    return {"items": df.to_dict("records"), "count": len(df)}


@app.post("/api/pending_issues/batch", response_model=BatchOut)
def stage_batch(body: BatchIn, user: dict = Depends(_require_token)) -> BatchOut:
    """
    Bulk-stage rows from the phone's IndexedDB queue. Lands in `pending_issues`
    with status='draft' — exactly like the Streamlit form does. The HOD's
    EOD review will see them next time they open the portal.

    The phone supplies a client_id per row for retry-safety; today we accept
    them ignorantly (the database doesn't dedupe yet), but the field is in
    the schema so a future migration can add an index without changing the
    PWA. For now we audit-log the batch so duplicates are traceable.
    """
    rows = [item.model_dump(exclude_none=True) for item in body.items]
    n = pwa_stage_pending_issues(
        rows=rows,
        username=user["username"],
        site_id=user["site_id"],
    )
    try:
        log_audit_action(
            user["username"], "PWA_STAGE", "pending_issues",
            f"PWA bulk-staged {n} row(s) from site {user['site_id']}",
        )
    except Exception:
        pass
    return BatchOut(inserted=n)


# ---------------------------------------------------------------------------
# STATIC PWA SHELL
# ---------------------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    """Serves the PWA shell. Browser caches via the service worker."""
    html = _STATIC_DIR / "index.html"
    if not html.exists():
        return JSONResponse(
            {"error": "pwa/static/index.html missing"},
            status_code=500,
        )
    return FileResponse(str(html), media_type="text/html")


@app.get("/sw.js")
def sw() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "sw.js"), media_type="application/javascript")


@app.get("/app.webmanifest")
def manifest() -> FileResponse:
    return FileResponse(
        str(_STATIC_DIR / "app.webmanifest"),
        media_type="application/manifest+json",
    )


# ---------------------------------------------------------------------------
# OPS
# ---------------------------------------------------------------------------
@app.get("/healthz")
def healthz() -> dict[str, Any]:
    """Liveness probe — returns row counts so an admin can sanity-check the DB."""
    conn = get_connection()
    try:
        inv = conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0]
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM pending_issues").fetchone()[0]
    finally:
        conn.close()
    return {"ok": True, "inventory": inv, "users": users, "pending_issues": pending}
