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
from .auth import get_current_user, require_level, require_roles, site_scope  # noqa: E402
from .auth import router as auth_router  # noqa: E402
from .config import CORS_ORIGINS  # noqa: E402
from .crud import make_read_router  # noqa: E402
from .db import engine, get_session  # noqa: E402
from .entry import router as entry_router  # noqa: E402
from .exec_summary import router as exec_summary_router  # noqa: E402
from .lining_analytics import router as lining_analytics_router  # noqa: E402
from .weekly_report import router as weekly_report_router, weekly_report_loop  # noqa: E402
from .health_monitor import router as health_router, briefing_loop  # noqa: E402
from .entry_docs import router as entry_docs_router  # noqa: E402
from .hod import router as hod_router  # noqa: E402
from .logistics import router as logistics_router  # noqa: E402
from .manhours import router as manhours_router  # noqa: E402
from .execution import router as execution_router
from .training import media_router as training_media_router
from .training import router as training_router
from .ai.router import router as ai_router  # noqa: E402
from .notifications import router as notifications_router  # noqa: E402
from .readonly import read_only_guard  # noqa: E402
from .console import admin as console_admin_router  # noqa: E402
from .sla import router as sla_router  # noqa: E402
from .console import oversight as console_oversight_router  # noqa: E402
from .console import public as console_public_router  # noqa: E402
from .console import traces as console_traces_router  # noqa: E402
from .console import xsite as xsite_router  # noqa: E402
from .documents import router as documents_router  # noqa: E402
from .report_center import router as report_center_router  # noqa: E402
from .report_center import scheduler_loop  # noqa: E402
from .services.notifications import digest_loop, reset_delivery_preference, set_delivery_preference  # noqa: E402
from .webhook import router as webhook_router  # noqa: E402
from .reports import router as reports_router  # noqa: E402
from .receiving import router as receiving_router  # noqa: E402
from .requests import router as requests_router  # noqa: E402
from .sme import router as sme_router  # noqa: E402
from .sme_master import router as sme_master_router  # noqa: E402
from .sme_actuals import router as sme_actuals_router  # noqa: E402
from .locations import router as locations_router  # noqa: E402
from .assets import router as assets_router  # noqa: E402
from .bulk_import import router as bulk_import_router  # noqa: E402
from .stock import router as stock_router  # noqa: E402
from .warehouse import router as warehouse_router  # noqa: E402
from .dashboard import router as dashboard_router  # noqa: E402
from .qc import router as qc_router  # noqa: E402
from .qc_hod import router as qc_hod_router  # noqa: E402
from .ppe import router as ppe_router  # noqa: E402
from .employees import router as employees_router  # noqa: E402

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
# ── the generated entity routers, and who may READ each one ──────────────────
#
# `read_roles` mirrors the frontend matrix one-for-one (PROPOSED_NAV_FIX.md
# §4.2, `frontend/src/config/entities.ts`). Admin is implicit everywhere —
# `require_roles` always allows it.
#
# ⚠️ These all used to be a bare `get_current_user`. The navigation manifest
# hid the pages and the API served the data to anyone who typed the URL, which
# meant the manifest was documentation rather than a control. Keep the two in
# step: a `/records/*` row narrowed in `entities.ts` and left wide here is not
# narrowed at all.
#
# `read_roles: None` means genuinely open, and exactly one entity is —
# `inventory`, the material master, which every entry form and picker in the
# product reads. Nothing about it is sensitive: it is the catalogue.
_LEDGER = ["hod", "logistics", "auditor"]      # oversight surfaces
_ROSTER = ["store_keeper", "supervisor", "hod", "auditor"]   # = employees.py

ENTITIES = [
    {"name": "inventory",       "prefix": "/inventory",       "tag": "inventory",       "id_col": "SAP_Code", "site_col": "Site_ID", "read_roles": None},
    {"name": "receipts",        "prefix": "/receipts",        "tag": "receipts",        "id_col": "id",       "site_col": "Site_ID", "read_roles": _LEDGER},
    {"name": "consumption",     "prefix": "/consumption",     "tag": "consumption",     "id_col": "id",       "site_col": "Site_ID", "read_roles": _LEDGER},
    {"name": "returns",         "prefix": "/returns",         "tag": "returns",         "id_col": "id",       "site_col": "Site_ID", "read_roles": _LEDGER},
    {"name": "lots",            "prefix": "/lots",            "tag": "lots",            "id_col": "id",       "site_col": "Site_ID", "read_roles": _LEDGER},
    # The warehouse receives goods AGAINST a PO and must be able to look one up.
    {"name": "purchase_orders", "prefix": "/purchase-orders", "tag": "purchase_orders", "id_col": "id",       "site_col": "Site_ID", "read_roles": ["warehouse_user", "logistics", "auditor"]},
    {"name": "pr_master",       "prefix": "/purchase-requests", "tag": "purchase_requests", "id_col": "id",   "site_col": "Site_ID", "read_roles": _LEDGER},
    # SME equipment: the planners and the oversight role. Matches /sme/*.
    {"name": "sme_equipment",   "prefix": "/equipment",       "tag": "equipment",       "id_col": "id",       "site_col": "Site_ID", "read_roles": ["hod", "auditor"]},
    # ⚠️ THE SAME TABLE `/hr/employees` SERVES — names, phone numbers. It must
    # carry the same role set, or narrowing that endpoint was cosmetic: the
    # data sat two URLs away the whole time.
    #
    # And its WRITES drop from level 3 to admin-only, which is the one place
    # this pass departs from the frontend matrix rather than mirroring it. The
    # operator revoked the roster from Logistics for worker privacy on
    # 2026-08-12; leaving them a full create/update/delete editor over the same
    # table would have made that revocation theatre — they would simply have
    # read every name and phone number from the Master Data page instead. The
    # HOD keeps the operation that actually matters (transfers, via /hr).
    {"name": "employees",       "prefix": "/employees",       "tag": "employees",       "id_col": "id",       "site_col": "Site_ID", "read_roles": _ROSTER, "writable": True, "write_dep": require_roles()},
    {"name": "vendors",         "prefix": "/vendors",         "tag": "vendors",         "id_col": "id",       "site_col": None,       "read_roles": ["logistics"], "writable": True},
    # Warehouses are named on user accounts and on every DN, so the warehouse
    # portal and the admin user forms both need the list.
    {"name": "warehouses",      "prefix": "/warehouses",      "tag": "warehouses",      "id_col": "id",       "site_col": None,       "read_roles": ["warehouse_user", "logistics", "qc_hod"], "writable": True},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Report scheduler daemon — one asyncio task per worker; duplicate runs are
    # prevented by the atomic last_run claim in run_due_schedules(). Disable
    # with GI_SCHEDULER=0 (tests/CI import the app without running lifespan).
    task = None
    digest_task = None
    weekly_task = None
    briefing_task = None
    if os.environ.get("GI_SCHEDULER", "1") != "0":
        import asyncio
        task = asyncio.create_task(scheduler_loop())
        # Evening-digest aggregator (Phase 6): daily 16:00 local batch send of
        # staged "evening" notifications. Same GI_SCHEDULER=0 escape hatch.
        digest_task = asyncio.create_task(digest_loop())
        # Weekly executive PDF (Phase 8-3): Friday 17:00 local render+dispatch.
        weekly_task = asyncio.create_task(weekly_report_loop())
        # Morning Briefing agent: daily 07:00 local scan for operational
        # anomalies nothing else can notice, because each one is the ABSENCE
        # of an event rather than an event. Same GI_SCHEDULER=0 escape hatch.
        briefing_task = asyncio.create_task(briefing_loop())
    # AI-job orphan sweep. ⚠️ THIS USED TO FAIL EVERY UNFINISHED ROW, which
    # under `--workers 4` meant one worker's respawn killed the other three
    # workers' in-flight OCR reads. It now reaps only jobs whose owner has
    # stopped heartbeating (ai/jobs.py). The sweep also runs on a TIMER,
    # because a worker that dies while its siblings stay up leaves an orphan
    # that the respawn's startup sweep is too early to see.
    orphan_task = None
    try:
        from .ai import jobs as _ai_jobs
        n = await _ai_jobs.sweep_orphans()
        if n:
            print(f"[ai] failed {n} stranded OCR job(s) whose worker had gone")
        if os.environ.get("GI_SCHEDULER", "1") != "0":
            import asyncio as _aio_orph
            orphan_task = _aio_orph.create_task(_ai_jobs.orphan_sweep_loop())
    except Exception as e:  # never block startup on the sweep
        print(f"[ai] orphan sweep skipped: {type(e).__name__}: {e}")
    # AI request tracing (slice 11c). One drain task per worker, writing a
    # BOUNDED queue in batches. ⚠️ Started regardless of GI_SCHEDULER: it is
    # not a scheduled job, it is the writer for spans the request path is
    # already producing, and without it those spans accumulate to the queue cap
    # and are dropped. Costs nothing when nobody asks the assistant anything.
    try:
        from .ai import trace as _ai_trace
        _ai_trace.start()
    except Exception as e:  # never block startup on observability
        print(f"[ai] trace drain not started: {type(e).__name__}: {e}")
    # Pre-build the assistant's manual index (Phase 8 slice 8f). Measured on
    # the live 229 KB manual: 2 ms to chunk, 15 ms for the BM25 tables, 0.3 ms
    # per search afterwards.
    #
    # ⚠️ THIS IS NOT THE FIX FOR "THE ASSISTANT IS SLOW". 17 ms is not what a
    # person feels; token generation in Ollama is. It is here for two other
    # reasons: 17 ms of synchronous CPU no longer lands on the event loop
    # inside whoever asks the first question, and a manual that is missing or
    # unparseable now announces itself AT BOOT instead of inside somebody's
    # chat window.
    try:
        from .ai.manual_qa import warm as _warm_manual
        w = _warm_manual()
        print(f"[ai] manual index: {'OK' if w['ok'] else 'DEGRADED'} — "
              + (f"{w['chapters']} chapters, {w['chunks']} chunks, {w['ms']} ms"
                 if w["ok"] else w.get("error", "?")))
    except Exception as e:  # never block startup on the assistant
        print(f"[ai] manual index skipped: {type(e).__name__}: {e}")
    # Uniqueness Bloom filters (2026-09-01). Built at boot so the first
    # registration keystroke does not pay for the load, and refreshed on a
    # timer because a filter is a SNAPSHOT of a set — see services/bloom.py for
    # why a stale one may retire a read and may never authorise a write.
    try:
        from .services import bloom as _bloom
        built = await _bloom.refresh_all()
        print("[bloom] " + ", ".join(
            f"{n}: {st['elements']}/{st['capacity']} in {st['bytes']}B"
            for n, st in built.items() if st))
        if os.environ.get("GI_SCHEDULER", "1") != "0":
            _bloom.start_refresh_loop()
    except Exception as e:  # never block startup on an accelerator
        print(f"[bloom] skipped: {type(e).__name__}: {e}")
    # Second-wall assertion (audit A01-F3): the gi_ai_ro GRANT allowlist is
    # wiped by every mirror reload and re-applied by operator ritual. Say so at
    # boot rather than discovering it during an incident. Never fatal — a dev
    # box without the role must still start.
    try:
        from .ai.analytics import ro_wall_status
        wall = await ro_wall_status()
        print(f"[ai] read-only wall: {'OK' if wall['ok'] else 'DEGRADED'} — {wall['detail']}")
    except Exception as e:
        print(f"[ai] read-only wall check skipped: {type(e).__name__}: {e}")
    yield
    if task:
        task.cancel()
    if digest_task:
        digest_task.cancel()
    if weekly_task:
        weekly_task.cancel()
    if briefing_task:
        briefing_task.cancel()
    if orphan_task:
        orphan_task.cancel()
    try:
        from .services import bloom as _bloom
        await _bloom.stop_refresh_loop()
    except Exception:  # noqa: BLE001 — shutdown must not raise
        pass
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


@app.middleware("http")
async def _read_only_role_middleware(request, call_next):
    """View-only roles (Auditor) may not change anything. Enforced by HTTP
    method for EVERY route at once — including routes that do not exist yet —
    rather than per-endpoint, which would fail open on the next @router.post
    somebody adds. Rules and the exception list: readonly.py."""
    return await read_only_guard(request, call_next)


@app.middleware("http")
async def _delivery_preference_middleware(request, call_next):
    """Phase 6: X-Delivery-Preference ("urgent" | "evening") sets the
    request-scoped WhatsApp delivery mode for every dispatch() the request
    triggers — no per-endpoint threading. Absent header = urgent (immediate)."""
    pref = request.headers.get("X-Delivery-Preference")
    if not pref:
        return await call_next(request)
    token = set_delivery_preference(pref)
    try:
        return await call_next(request)
    finally:
        reset_delivery_preference(token)

# Auth (open): login + JWT + /auth/me.
app.include_router(auth_router)

# Inbound WhatsApp webhook (Phase 6). Unauthenticated by design — Meta calls it
# with its own verify-token (GET) / X-Hub-Signature-256 HMAC (POST). Mounted at
# both the bare path (single-origin nginx strips /api) and the /api/v1 form.
app.include_router(webhook_router, prefix="/whatsapp")
app.include_router(webhook_router, prefix="/api/v1/whatsapp")

# Everything below requires a valid bearer token. `get_current_user` guards the
# read entities + derived stock; the entry routes self-guard (they need the
# authenticated username as the ledger actor).
_auth = [Depends(get_current_user)]

for e in ENTITIES:
    app.include_router(make_read_router(
        _MD.tables[e["name"]],
        prefix=e["prefix"], tag=e["tag"],
        id_col=e["id_col"], site_col=e["site_col"],
        # Reads: the roles named on the entity above (None = any authenticated
        # user, and only `inventory` is). Writes (master data): level ≥ 3
        # (logistics/admin) unless the entity overrides it — mirrors the
        # frontend Master-Data nav gate.
        read_roles=e.get("read_roles"),
        writable=e.get("writable", False),
        write_dep=e.get("write_dep") or require_level(3),
    ), dependencies=_auth)

# Derived (computed) stock endpoints — /stock/live, /by-site, /lots, /expiring.
app.include_router(stock_router, dependencies=_auth)

# Dashboard metrics — valuation KPI + chart series (self-guarded, ≥supervisor).
app.include_router(dashboard_router)

# Data-entry (staging) endpoints — /entry/receipts, … (self-guarded).
app.include_router(entry_router)

# HOD portal — approvals (commit staged entries) + burn-rate (self-guarded, ≥hod).
app.include_router(hod_router)
# HOD portal — executive summary report (hod+admin exact lock, read-only).
app.include_router(exec_summary_router)
# Phase 8-1 — predictive RL/BL lining coverage from LIVE stock (hod/logistics).
app.include_router(lining_analytics_router)
# Phase 8-3 — weekly exec PDF: tokenized download + admin run-now.
app.include_router(weekly_report_router)

# Morning Briefing agent — preview (level 2) + manual run (admin).
app.include_router(health_router)
# Parity A1/A4 — entry documents (upload/library/download) + site WBS config.
app.include_router(entry_docs_router)

# Logistics portal — PR queue → create PO → assign to warehouse (self-guarded, ≥logistics).
app.include_router(logistics_router)

# Warehouse portal — assignment → receive → DN → outbound (self-guarded, warehouse/admin).
app.include_router(warehouse_router)

# Site receiving — in-transit DN → stage pending_receipts (closes the loop; self-guarded).
app.include_router(receiving_router)

# QSEP — Quality Control: accounts, site transfers, and the inspection ledger
# whose approvals gate what a Store Keeper may issue (self-guarded per route).
app.include_router(qc_router)

# Phase 8 slice 8d — the Head of Qualities. Cross-site oversight of Surface
# Shield material and nothing else: every route is require_roles("qc_hod"), and
# every read is filtered to the controlled category in SQL. The role is level 2
# with a NAMED cross-site exemption (auth.QC_OVERSIGHT_ROLES) precisely so its
# surface is this file and not whatever require_level(<=3) happens to reach.
app.include_router(qc_hod_router)

# QSEP — PPE usable-time rules, per-person history and the 15-day order
# forecast. There is deliberately NO issue endpoint here: PPE goes out
# through the ordinary /entry/consumption form (Option A), so it gets no
# parallel stock path.
app.include_router(ppe_router)

# QSEP — employee identity: site transfers, movement history, admin tracking.
app.include_router(employees_router)

# Supervisor material requests — create → SK approve → pending_issues (self-guarded).
app.include_router(requests_router)

# SME Material Estimator — reads over the sme_* tables (self-guarded, ≥hod).
app.include_router(sme_router)

# SME Phase S6 (cutover day) — Master Data CRUD, exact-lock {hod, admin}.
app.include_router(sme_master_router)

# Serialised asset tracking — one row per physical thing, with an append-only
# movement log and optional GPS. Reads open to every authenticated user;
# register/move/retire are require_level(1), self-guarded in the module.
app.include_router(assets_router)

# Warehouse rack locator — where a material physically lives. Reads are open
# to every authenticated user (a store keeper is level 0 and is exactly who
# needs this); writes are require_level(1) and self-guarded in the module.
app.include_router(locations_router)

# SME actual consumption + tank aliases (2026-08-04 Surface-Shield routing).
# Same {hod, admin} exact-lock. Reads/writes sme_consumption_log only — it
# never touches sme_inventory_seed, so no estimator figure can move (rule 1a).
app.include_router(sme_actuals_router)

# Bulk Excel import — dry-run/commit upserts for inventory + SME masters
# (self-guarded: SME kinds {hod, admin}; inventory/ledger admin-only).
app.include_router(bulk_import_router)

# Admin console — user management + audit-log viewer (self-guarded, admin only).
app.include_router(admin_router)

# In-app notifications — the sidebar bell feed (self-scoped to the current user).
app.include_router(notifications_router, dependencies=_auth)

# Reports — archive + schedules FIRST (literal /reports/archive|schedules paths
# must register before the /reports/{key} catch-all), then the downloads.
app.include_router(report_center_router)
app.include_router(reports_router)

# Documents — QR bin labels + employee badges + SOP/Manual + master-data exports.
app.include_router(documents_router)

# Admin console completion — sites/settings/backup/sessions (admin), oversight
# (≥logistics), cross-site requests (≥hod), feedback (any authenticated user).
app.include_router(console_admin_router)
app.include_router(console_oversight_router)
# AI request traces (slice 11c) — admin + auditor, read-only by rule 7.
app.include_router(console_traces_router)
# T2 — admin SLA tracker: >24h Overdue Actions + clear/notify nudges (admin-only).
app.include_router(sla_router)
app.include_router(xsite_router, dependencies=_auth)
app.include_router(console_public_router, dependencies=_auth)

# Man-Hours & Manpower Tracking — mh_* tables, exact-locked {hod, admin} (self-guarded).
app.include_router(manhours_router)
# Phase 5 — the SK → supervisor → HOD consumption workflow. Its own gates are
# per-route (each step names the role that performs it), so no blanket dep here.
app.include_router(execution_router)

# Track 5 — the training hub and the SOFT gate on the OCR upload. Per-route
# guards: reads are any authenticated user (everybody has training), the HOD
# dashboard is level 2, publishing an asset is admin.
app.include_router(training_router, dependencies=_auth)
# ⚠️ NO BLANKET `_auth` ON THIS ONE, DELIBERATELY, AND IT IS NOT A GAP. A
# `<video src>` cannot send an Authorization header, so the media route accepts
# a short-lived, single-tutorial TICKET in its query string instead — and the
# router-level dependency would refuse the request before the endpoint could
# look at it. `training._media_viewer` is that route's own guard: it verifies a
# signature (ticket or bearer) or raises 401, and the role fence runs after it.
# Suite CZ pins both halves.
app.include_router(training_media_router)

# Intelligence layer — /ai/health + the Hub Assistant SSE stream (self-guarded,
# any authenticated user; role-gated context inside manual_qa).
app.include_router(ai_router)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["meta"], summary="Liveness + DB connectivity")
async def health(session: AsyncSession = Depends(get_session)):
    """Anonymous liveness only. The database name, driver dialect and full
    entity inventory used to be served to any unauthenticated caller — and once
    the Cloudflare Access bypass for /api/* lands this endpoint is
    internet-reachable (audit A02-F11). Diagnostics moved to /health/detail."""
    await session.execute(text("SELECT 1"))
    from .auth import maintenance_on
    return {"status": "ok", "maintenance": await maintenance_on(session)}


@app.get("/health/detail", tags=["meta"], summary="Deployment diagnostics (admin)",
         dependencies=[Depends(require_level(4))])
async def health_detail(session: AsyncSession = Depends(get_session)):
    await session.execute(text("SELECT 1"))
    from .ai.analytics import ro_wall_status
    from .auth import maintenance_on
    return {
        "status": "ok",
        "dialect": engine.dialect.name,
        "database": engine.url.database,
        "maintenance": await maintenance_on(session),
        "entities": [e["name"] for e in ENTITIES],
        "ai_readonly_wall": await ro_wall_status(),
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


@app.get("/meta/categories", tags=["meta"],
         summary="Distinct inventory categories (for search/filter dropdowns)")
async def categories(session: AsyncSession = Depends(get_session),
                     user: dict = Depends(get_current_user)):
    inv = _MD.tables["inventory"]
    col = inv.c["Category"]
    res = await session.execute(
        select(distinct(func.trim(col))).where(col.isnot(None))
        .where(func.trim(col) != "").order_by(func.trim(col)))
    return {"categories": [r[0] for r in res.all()]}


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

    # Overdue tool loans (nav: store keeper's Returnable Items).
    if user["role"] in ("store_keeper", "admin"):
        import datetime as _dt
        t = _MD.tables["returnable_items"]
        now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
        out["returnables_overdue"] = await _cnt(
            "returnable_items", t.c["status"] == "borrowed",
            t.c["expected_return_time"] < now, *_site("returnable_items"))

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
