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

from .auth import get_current_user  # noqa: E402
from .auth import router as auth_router  # noqa: E402
from .config import CORS_ORIGINS  # noqa: E402
from .crud import make_read_router  # noqa: E402
from .db import engine, get_session  # noqa: E402
from .entry import router as entry_router  # noqa: E402
from .hod import router as hod_router  # noqa: E402
from .logistics import router as logistics_router  # noqa: E402
from .receiving import router as receiving_router  # noqa: E402
from .requests import router as requests_router  # noqa: E402
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


@app.get("/meta/sites", tags=["meta"], summary="Distinct Site_IDs (for a site picker)",
         dependencies=[Depends(get_current_user)])
async def sites(session: AsyncSession = Depends(get_session)):
    inv = _MD.tables["inventory"]
    col = inv.c["Site_ID"]
    res = await session.execute(select(distinct(col)).where(col.isnot(None)).order_by(col))
    return {"sites": [r[0] for r in res.all()]}


@app.get("/meta/inventory-summary", tags=["meta"],
         summary="Exact inventory item counts by site and by category",
         dependencies=[Depends(get_current_user)])
async def inventory_summary(session: AsyncSession = Depends(get_session)):
    inv = _MD.tables["inventory"]
    site_c, cat_c = inv.c["Site_ID"], inv.c["Category"]

    by_site = (await session.execute(
        select(site_c, func.count()).group_by(site_c).order_by(site_c))).all()
    by_cat = (await session.execute(
        select(cat_c, func.count()).group_by(cat_c).order_by(cat_c))).all()
    total = (await session.execute(select(func.count()).select_from(inv))).scalar_one()

    return {
        "total_items": total,
        "by_site": [{"Site_ID": r[0], "count": r[1]} for r in by_site],
        "by_category": [{"Category": r[0], "count": r[1]} for r in by_cat],
    }
