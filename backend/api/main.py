"""
backend/api/main.py — GI Hub REST API (FastAPI, async SQLAlchemy over Postgres).

v1 scope: read-only endpoints over the core business tables + a few exact-count
aggregates under /meta. This is the decoupled foundation for the React frontend;
the Streamlit app is unaffected (it stays on SQLite).

Run from the repo root:
    ./run_api.sh
    # or
    .venv/bin/uvicorn backend.api.main:app --reload --port 8000
Then open http://localhost:8000/docs
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy import distinct, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

# Make the repo root importable so `backend.models` resolves whether launched as
# `backend.api.main:app` or from elsewhere.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend import models  # noqa: E402

from .admin import router as admin_router  # noqa: E402
from .auth import get_current_user, require_level, site_scope  # noqa: E402
from .auth import router as auth_router  # noqa: E402
from .config import CORS_ORIGINS  # noqa: E402
from .crud import make_read_router  # noqa: E402
from .db import engine, get_session  # noqa: E402
from .entry import router as entry_router  # noqa: E402
from .hod import router as hod_router  # noqa: E402
from .logistics import router as logistics_router  # noqa: E402
from .notifications import router as notifications_router  # noqa: E402
from .reports import router as reports_router  # noqa: E402
from .receiving import router as receiving_router  # noqa: E402
from .requests import router as requests_router  # noqa: E402
from .sme import router as sme_router  # noqa: E402
from .stock import router as stock_router  # noqa: E402
from .warehouse import router as warehouse_router  # noqa: E402

_MD = models.Base.metadata

# --- v1 exposed entities -----------------------------------------------------
# Read-only, non-secret business tables. Adding another is a one-line entry.
# (Deliberately excluded: users / pending_users / *_tokens / qr_approval_requests
#  — they carry credentials or one-time secrets.)
# `writable: True` adds POST/PUT/DELETE. Only the master-data (reference) tables
# are writable — they have no ledger business rules. Ledger tables (receipts /
# consumption / returns / inventory / lots / purchase_orders) stay READ-ONLY
# here: their writes carry identity-math / FEFO / audit logic that must be ported
# into a services layer (a dedicated later milestone), not naive INSERTs.
ENTITIES = [
    {"name": "inventory",       "prefix": "/inventory",       "tag": "inventory",       "id_col": "SAP_Code", "site_col": "Site_ID"},
    {"name": "receipts",        "prefix": "/receipts",        "tag": "receipts",        "id_col": "id",       "site_col": "Site_ID"},
    {"name": "consumption",     "prefix": "/consumption",     "tag": "consumption",     "id_col": "id",       "site_col": "Site_ID"},
    {"name": "returns",         "prefix": "/returns",         "tag": "returns",         "id_col": "id",       "site_col": "Site_ID"},
    {"name": "lots",            "prefix": "/lots",            "tag": "lots",            "id_col": "id",       "site_col": "Site_ID"},
    {"name": "purchase_orders", "prefix": "/purchase-orders", "tag": "purchase_orders", "id_col": "id",       "site_col": "Site_ID"},
    {"name": "sme_equipment",   "prefix": "/equipment",       "tag": "equipment",       "id_col": "id",       "site_col": "Site_ID"},
    {"name": "employees",       "prefix": "/employees",       "tag": "employees",       "id_col": "id",       "site_col": "Site_ID", "writable": True},
    {"name": "vendors",         "prefix": "/vendors",         "tag": "vendors",         "id_col": "id",       "site_col": None,       "writable": True},
    {"name": "warehouses",      "prefix": "/warehouses",      "tag": "warehouses",      "id_col": "id",       "site_col": None,       "writable": True},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(
    title="GI Hub API",
    version="0.1.0",
    description=(
        "Read-only REST foundation for the GI Hub ERP, served from PostgreSQL "
        "via async SQLAlchemy. Separate process from the Streamlit app. "
        "Interactive docs: /docs · OpenAPI: /openapi.json"
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth (open): login + JWT + /auth/me.
app.include_router(auth_router)

# Everything below requires a valid bearer token. `get_current_user` guards the
# read entities + derived stock; the entry routes self-guard (they need the
# authenticated username as the ledger actor).
_auth = [Depends(get_current_user)]

for e in ENTITIES:
    app.include_router(make_read_router(
        _MD.tables[e["name"]],
        prefix=e["prefix"], tag=e["tag"],
        id_col=e["id_col"], site_col=e["site_col"],
        writable=e.get("writable", False),
        # Reads: any authenticated user. Writes (master data): level ≥ 3
        # (logistics/admin) — mirrors the frontend Master-Data nav gate.
        write_dep=require_level(3),
    ), dependencies=_auth)

# Derived (computed) stock endpoints — /stock/live, /by-site, /lots, /expiring.
app.include_router(stock_router, dependencies=_auth)

# Data-entry (staging) endpoints — /entry/receipts, … (self-guarded).
app.include_router(entry_router)

# HOD portal — approvals (commit staged entries) + burn-rate (self-guarded, ≥hod).
app.include_router(hod_router)

# Logistics portal — PR queue → create PO → assign to warehouse (self-guarded, ≥logistics).
app.include_router(logistics_router)

# Warehouse portal — assignment → receive → DN → outbound (self-guarded, warehouse/admin).
app.include_router(warehouse_router)

# Site receiving — in-transit DN → stage pending_receipts (closes the loop; self-guarded).
app.include_router(receiving_router)

# Supervisor material requests — create → SK approve → pending_issues (self-guarded).
app.include_router(requests_router)

# SME Material Estimator — READ-ONLY over the frozen sme_* tables (self-guarded, ≥hod).
app.include_router(sme_router)

# Admin console — user management + audit-log viewer (self-guarded, admin only).
app.include_router(admin_router)

# In-app notifications — the sidebar bell feed (self-scoped to the current user).
app.include_router(notifications_router, dependencies=_auth)

# Reports — downloadable Excel/PDF/CSV exports (self-guarded, ≥hod).
app.include_router(reports_router)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["meta"], summary="Liveness + DB connectivity")
async def health(session: AsyncSession = Depends(get_session)):
    await session.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "dialect": engine.dialect.name,
        "database": engine.url.database,
        "entities": [e["name"] for e in ENTITIES],
    }


@app.get("/meta/sites", tags=["meta"], summary="Distinct Site_IDs (for a site picker)")
async def sites(user: dict = Depends(get_current_user),
                session: AsyncSession = Depends(get_session)):
    scope = site_scope(user)
    if scope is not None:
        # Scoped users only ever pick their own site ('' → no site → empty).
        return {"sites": [scope] if scope else []}
    inv = _MD.tables["inventory"]
    col = inv.c["Site_ID"]
    res = await session.execute(select(distinct(col)).where(col.isnot(None)).order_by(col))
    return {"sites": [r[0] for r in res.all()]}


@app.get("/meta/work-queues", tags=["meta"],
         summary="Pending-work counts for the sidebar badges (role- and site-aware)")
async def work_queues(user: dict = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)):
    """One cheap round-trip for every badge the caller's nav actually shows.
    Counts honour site scoping exactly like the pages they link to."""
    scope = site_scope(user)  # None = global · '' = site-less scoped user (matches nothing)

    async def _cnt(tname: str, *where) -> int:
        t = _MD.tables[tname]
        stmt = select(func.count()).select_from(t)
        for cond in where:
            stmt = stmt.where(cond)
        return (await session.execute(stmt)).scalar_one()

    def _site(tname: str):
        return [] if scope is None else [_MD.tables[tname].c["Site_ID"] == scope]

    out: dict[str, int] = {}

    # HOD approvals — staged entries awaiting approve/reject (nav: level ≥ 2).
    if user["level"] >= 2:
        total = 0
        for n in ("pending_receipts", "pending_issues", "pending_returns",
                  "stock_adjustments"):
            total += await _cnt(n, _MD.tables[n].c["status"] == "pending_hod", *_site(n))
        out["approvals"] = total

    # In-transit DNs headed to the site (nav: everyone).
    out["incoming_dns"] = await _cnt(
        "delivery_notes",
        _MD.tables["delivery_notes"].c["status"] == "in_transit",
        *_site("delivery_notes"))

    # Supervisor material requests awaiting the store keeper (nav: everyone).
    out["sk_requests"] = await _cnt(
        "supervisor_material_requests",
        _MD.tables["supervisor_material_requests"].c["status"] == "pending_sk",
        *_site("supervisor_material_requests"))

    # Warehouse workload — assignments not yet fully received
    # (nav: warehouse_user / logistics / admin; warehouses aren't site-bound).
    if user["role"] in ("warehouse_user", "logistics", "admin"):
        t = _MD.tables["po_assignments"]
        out["warehouse"] = await _cnt(
            "po_assignments", t.c["status"].in_(("assigned", "acknowledged", "partial")))

    return out


@app.get("/meta/inventory-summary", tags=["meta"],
         summary="Exact inventory item counts by site and by category")
async def inventory_summary(user: dict = Depends(get_current_user),
                            session: AsyncSession = Depends(get_session)):
    inv = _MD.tables["inventory"]
    site_c, cat_c = inv.c["Site_ID"], inv.c["Category"]
    scope = site_scope(user)
    if scope == "":
        return {"total_items": 0, "by_site": [], "by_category": []}

    def _w(stmt):
        return stmt.where(site_c == scope) if scope is not None else stmt

    by_site = (await session.execute(
        _w(select(site_c, func.count())).group_by(site_c).order_by(site_c))).all()
    by_cat = (await session.execute(
        _w(select(cat_c, func.count())).group_by(cat_c).order_by(cat_c))).all()
    total = (await session.execute(
        _w(select(func.count()).select_from(inv)))).scalar_one()

    return {
        "total_items": total,
        "by_site": [{"Site_ID": r[0], "count": r[1]} for r in by_site],
        "by_category": [{"Category": r[0], "count": r[1]} for r in by_cat],
    }
