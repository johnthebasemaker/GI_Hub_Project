# GI Hub — System Architecture (the "brain" document)

> **Purpose:** a fresh AI instance (or engineer) reading ONLY this file plus
> [`PROJECT_STATUS.md`](PROJECT_STATUS.md) must understand the exact state,
> tech stack and rules of the project with no chat history. Written 2026-07-13;
> **finalized 2026-07-18 (pre-deploy batch)** at gates `service_tests 750/0
> (suites A…AO) · Playwright 39/39 · bug_check 599/0 · build+tsc ✅ · alembic
> single head c7d4e8f19a25`. Only the Hetzner deployment itself remains.

---

## 1. The two applications (segregation contract)

| | LEGACY (production, frozen) | NEW STACK (ship-ready) |
|---|---|---|
| UI | Streamlit (`main.py`, `pages_internal/*.py`) | React 19 + antd 6 + Vite (`frontend/`) |
| API | — (monolith) | FastAPI (`backend/api/`), uvicorn `:8000` |
| DB | SQLite `gi_database.db` (**system of record until cutover**) | PostgreSQL 16 — CI mirror `postgresql://postgres@127.0.0.1:5433/gihub` |
| Deploy | on-prem | Hetzner CPX42 plan + Cloudflare Tunnel (`gi.giinventory.com`), nginx, deploy/ |

Rules ([REPO_MAP.md](../REPO_MAP.md) is the contract): never edit
`legacy/database.py` / `legacy/pages_internal/` for new-stack work; new-stack
commits touch only `backend/`, `frontend/`, `deploy/`, `tests/`, `docs/`.
**Phase B executed 2026-07-13**: the legacy app lives under `legacy/`, root
data artifacts under `data-archive/`, and the bridge tools under `tools/`
(`dual_ci.py`, `migrate_sqlite_to_postgres.py`, `parity_check.py`,
`migration/cutover_migrate.py` + runbook); `gi_database.db` deliberately stays
at the repo root (bridge tools + the final production load read it there, and
it must never be staged). `tools/dual_ci.py` reloads the mirror from SQLite
and verifies 5 semantic aggregates; the production cutover script is
`tools/migration/cutover_migrate.py` (sync psycopg2 URL, `--strict --wipe`;
asyncpg URLs fail with MissingGreenlet by design). After every mirror reload,
re-run `backend/scripts/create_ai_readonly_role.sql` (grants get wiped).
**⚠️ Excel injection: PostgreSQL is permanently AHEAD of the frozen SQLite**
(2026-07-13 CNCEC workbook sync + 2026-07-18 re-sync: inventory 306→442,
full ledger backfill, stock verified **429/429** vs the workbook). A
`dual_ci`/cutover reload from `gi_database.db` WIPES that data — after ANY
reload re-run the sync (same on the production box after the final load;
the runbook says so):

```bash
tools/excel_sync.py --site CNCEC --commit            # header-NAME-driven; all 4 workbooks at repo root
tools/excel_sync_reconcile.py --commit               # zeroes superseded rows; date-less lines
tools/excel_sync.py --site CNCEC \
    --kinds sme-equipment,sme-recipes,sme-materials \
    --sme-reseed --commit                            # SME trio: wholesale replace (see below)
```

Sync mechanics (2026-07-18 final): every sheet's columns resolve **by header
name** (reorders/additions in the workbooks are safe; unknown columns warn,
never silently drop). `--kinds` restricts a run; **`--sme-reseed`** drops
recipes (global) + equipment/progress (per-site) + seeds before the SME loads
— REQUIRED whenever the workbook renumbers `Lining_System_Code`s (an upsert
would leave stale old-code rows double-counting SQM); it aborts if any
`Done_SQM > 0` would be lost unless `--force-drop-progress`. Recipe line
identity is **(code, material, SAP_Code)** — PU systems carry Comp-A/B/C/D
lines sharing one Material_Code, distinguished only by variant SAPs
(1041/-1/-2/-3); SAP-aware files merge repeated identities as coat lines
(For_1_SQM sums), legacy no-SAP files keep first-occurrence-wins.
`tools/parity_check.py` fails against the live mirror BY DESIGN (only
meaningful on CI or a freshly-reloaded mirror). Executed 2026-07-18:
inventory+ledger 429/429, SME reseed run by the operator (recipes 41, codes
1–10, all SAP-mapped).

## 2. Backend map (`backend/api/`)

FastAPI app in `main.py` (lifespan starts 3 daemons — report scheduler,
16:00 evening digest, **Friday 17:00 weekly exec PDF** — all disabled by
`GI_SCHEDULER=0`). `models.py` (repo `backend/models.py`) is the single schema
contract; alembic migrations in `backend/alembic/versions` (single head
**`c7d4e8f19a25`** = `feedback_triage`; before it `b3f2a9c47d18` =
SME SAP codes, `e7c31a9f24d5` = generated_reports). Modules:

| Module | Owns |
|---|---|
| `auth.py` | bcrypt login, 15-min JWT access + rotating httpOnly refresh cookie (reuse ⇒ all sessions revoked), TOTP 2FA, role levels (SK 0 · warehouse/supervisor 1 · hod 2 · logistics 3 · admin 4; `require_roles` always admits admin; `site_scope` pins level <3), registration + admin approval, **dual-OTP phone change** (`phone_otp.stage` 'old'→'new'; commit only after the NEW number verifies) |
| `entry.py` | SK staging: receipts/consumption/returns/adjustments + `/entry/bulk`. Guards: **MTC hard-block for `Category == "Surface Shields"`** (setting `mtc_required_category`; missing MTC also emails logistics), pack→base UoM conversion (`uom_conversions`), **WBS required when the site has active `wbs_master` rows**, **supporting-document gate** (below), return source-receipt gates, FEFO auto-pick w/ allow-and-log override alerts. **`GET /entry/lining-systems`** (2026-07-18): recipe SAP lists per system code + site Done/Pending SQM — powers the Surface-Shields system-first Issue workflow (UI enforces: shield SAP without a selected system is refused; the code travels as an `LS <code>` Remarks suffix) |
| `bulk_import.py` | **Bulk Excel Import** (`POST /import/{kind}`; kinds `inventory`/`ledger` admin-only, `sme-*` {hod,admin}): dry-run→commit, upsert-only, header-name-driven, category canonicalisation, 3-tier ledger reconcile (exact-match / qty-correction / insert), Material_Code uniqueness resolution — the same plan/apply code `tools/excel_sync.py` drives |
| `entry_docs.py` | **Entry document system (parity A1/A4)**: `entry_attachments` upload/list/download/delete, `require_entry_documents` gate, WBS config endpoints |
| `hod.py` | pending queues, per-row approve/reject(+reason)/edit (`{"fields":{...}}`), `bulk-approve` (≤200), submitter bell dispatch (receipts have NO submitter column by design — returns/issues/adjustments do), return-approval → logistics email |
| `exec_summary.py` + `exec_pdf.py` | Executive Summary JSON/xlsx/**server-rendered fpdf2 PDF** (content-measured tables; nothing clips) |
| `weekly_report.py` | Friday 17:00 auto exec-PDF → `generated_reports` + sha256-tokenized 72-h link `/reports/weekly-exec/{token}` → WhatsApp+bell to every admin/HOD; `POST /admin/reports/weekly-exec/run`; needs `PUBLIC_BASE_URL` in deploy/.env |
| `lining_analytics.py` | `GET /analytics/lining-coverage` — read-only SME engine with **live-ledger availability pool**; RL/BL family coverage + 90-day-burn depletion dates (hod/logistics; scoped site-pinned, default CNCEC) |
| `logistics.py` / `warehouse.py` / `receiving.py` | PR→PO→assignment→DN two-stage approval state machine (`draft→pending_logistics→…→received`), RL/BL family separation, reschedules, force-close + 24 h undo, vendor returns |
| `requests.py` | supervisor SMRs (worker must be an active employee at the site) → SK approve → HOD issue queue |
| `sme.py` + `sme_engine.py` | SME read layer + planning engine — **dual TS/Python engines with golden parity; change BOTH or neither** (frontend twin: `frontend/src/sme/engine.ts`). **`GET /sme/calculator?code&sqm`** (2026-07-18, level ≥2): recipe demand math `For_1_SQM × SQM` per component line, pack counts from Package_Size, **live ERP stock via the `sme_recipe.SAP_Code` join** + per-line shortfall + human explanation strings — the 🧮 Smart Calculator tab's backend |
| `sme_master.py` | **Phase S6 (cutover day): Master Data CRUD** — `/sme/master/*` equipment/recipes/materials-seed/progress/settings, exact-lock {hod, admin}, HOD site-pinned, every write audited; equipment create seeds `sme_sqm_progress`, delete cascades it; materials write `sme_inventory_seed` ONLY (Canon Rule 2) |
| `ai/` | Hub Assistant SSE, OCR lanes, PDF extract, `/ai/nl-search` (unscoped, Ollama→safety gate→`gi_ai_ro` read-only PG login), **`/ai/query` two-lane chat-with-your-data** (below), **`ai/handwritten.py`** — the handwritten-consumption-form spec implementation (below §7) |
| `notifications.py` + `services/notifications.py` | in-app bell (`app_notifications`) + unified `dispatch()` (bell ALWAYS + best-effort WhatsApp; `X-Delivery-Preference: evening` stages into `pending_summary_notifications` for the 16:00 digest; critical always immediate) |
| `services/whatsapp.py` | Meta Cloud API v2 outbox (`whatsapp_outbox`), approved templates `gi_action_required/gi_status_update/gi_critical_alert/gi_otp_code/gi_evening_summary` (lang **`en`**), friendly #131030 sandbox handling |
| `webhook.py` | inbound Meta webhook (`/whatsapp/webhook` + `/api/v1/…`): verify-token handshake, **X-Hub-Signature-256 HMAC**, STOCK/RESET PASSWORD commands, session-text replies |
| `ratelimit.py` | see §6 |
| `console.py` | admin settings (whitelist incl. `maintenance_mode`, `require_entry_documents`, `mtc_required_category`), pg_dump backup, sessions revoke, outbox retries, lot lifecycle. **Bug Tracking Engine (2026-07-18)**: `bug_reports` + `title/severity/rollback_notes/safety_constraints/triage_notes`; admin triage drawer; **`GET /admin/feedback/{id}/prompt`** renders a self-contained coding-agent implementation prompt (report + triage + rollback plan + the project's non-negotiable gates) and `GET /admin/feedback-export.md` a batch digest — the portal never mutates code itself |
| `service_tests.py` | the 750-check gate (suites A…AO), see §8 |

## 3. Database facts that bite

- Mixed-case column names are real (`"SAP_Code"`, `"Site_ID"`) — always quote.
- Ledger identity: **stock = Σreceipts − Σconsumption − Σreturns** per SAP/site
  (`v_live_stock`, `v_site_stock` views). Dates are ISO **text**.
- The 3 rowid-ledger tables keep `id := sqlite rowid` through migration so
  `posted_txn_ref` (`C:{rowid}`/`R:{rowid}`) stays valid.
- pending vs ledger naming traps: pending_returns `Return_Reason` → ledger
  returns `Reason`; pending `wbs` (lowercase) → ledger `WBS`; pending_returns
  has `override_required/override_reason/received_*` provenance columns.
- `entry_attachments` (BLOB-authoritative), `mtc_documents`, `wbs_master`,
  `form_drafts` were migrated from legacy; `generated_reports`,
  `phone_otp`, `auth_sessions`, `app_notifications`, `whatsapp_outbox`,
  `email_outbox`, `pending_summary_notifications` are new-stack-only.
- Locked rulings: **FEFO + over-issue/negative stock are allow-and-log, never
  hard-block** (2026-06-30); legacy hard-blocked — deliberate divergence.

## 4. Entry gates (parity sprint, 2026-07-13)

Master switch **`require_entry_documents`** (app_settings, **default ON** when
the row is absent; admin-editable):
- ON ⇒ Issue / Receipt / Return submission (single + bulk) requires ≥1
  uploaded supporting document (`attachment_ids`); returns additionally
  require **Return DN No.** + a **source receipt** (`GET /entry/return-sources`,
  30-day window; 365-day override needs a justification → `override_required=1`
  red-flagged in HOD approvals); qty capped to the source receipt.
- OFF ⇒ legacy-optional behaviour (tests run this way).
Independent of the switch: MTC hard-block for `Surface Shields` receipts,
WBS requirement once a site has active WBS rows, UoM conversion.

## 5. Frontend map (`frontend/src/`)

React Router routes in `App.tsx`; **`config/nav.tsx` is the single
source of truth for nav + route guards** (exact-lock `anyRole` / `minLevel`;
duplicate menu keys across groups are forbidden — use route aliases like
`/logistics/lining-coverage`). API via axios `api` (`api/client.ts`): Vite
proxies `/api` → `:8000` (`VITE_API_PROXY` overrides for E2E), token in
localStorage `gi_token`, silent refresh on 401, **429 → `gi-rate-limited`
event → RateLimitToast deadline countdown**. TanStack Query hooks in
`api/hooks.ts`.

**PWA/offline:** vite-plugin-pwa autoUpdate SW (build-only; dev unaffected),
NetworkFirst cache for read APIs. **Offline mutation queue**
(`offline/queue.ts`, IndexedDB `gi-offline`): only entry-form POSTs opt in via
`postWithOfflineFallback()` → `{queued:true}` + amber toast + header
`OfflineSyncBadge`; replay on reconnect with `X-Offline-Replay: 1`; rejected
rows are dropped+surfaced. **Entry documents:** `EntryDocsUpload` (file +
`capture="environment"` camera), `DocumentLibraryPage` (/hod/documents) with
inline image/PDF preview reused by the ApprovalsPage 📎 drawer. **Draft
recovery:** `lib/formDraft.ts` (localStorage, debounced) + DraftBanner on the
three entry forms. SME engine twin lives in `sme/engine.ts` (golden parity).

**2026-07-18 UI polish:** every antd Table carries `sticky={{ offsetHeader:
64 }}` (SmePage grids use the live-measured pinned-band offset instead);
**smart decimals** via `lib/format.ts` (`fmtQty`/`fmtCell` — `5.00`→`5`,
fractions keep ≤4 dp) wired into the generic `lib/columns.tsx` renderer (SME
coverage percentages keep their fixed `.1f` style; `sme/engine.ts` strings
are golden-parity-pinned). New pages/components: `BulkImportPage` (dry-run →
commit), `sme/SmartCalculator.tsx`, IssuePage Surface-Shields system-first
flow, OcrImportPage "Validate (handwritten spec)" + TSV export, Admin Console
feedback triage drawer + 📋 Prompt copy.

## 6. Security & rate limiting

`ratelimit.py` (in-house, no slowapi — resolves client IP
**CF-Connecting-IP → X-Real-IP → peer**, per-process store):
- per-endpoint dependencies: login 10/60, register 30/60, OTP burst 5/60 …
- `check_bucket(key,…)` arbitrary-key windows: **OTP 3/hour per source IP AND
  3/hour per target phone** (checked before anything else; failed sends burn
  quota; 429 + Retry-After).
- `PenaltyBox`: **5 invalid webhook HMAC signatures / 10 min ⇒ 15-min IP ban**
  (refused pre-parse, even for later valid signatures).
- `strict_limits_enabled()`: strict rules ON in production, **relaxed when
  `GI_DOTENV=0`** (hermetic tests), force-enabled by `GI_FORCE_STRICT_LIMITS=1`
  (suite AF).
JWTs: 15-min access (`JWT_SECRET`), rotating refresh cookie, reuse-detection
nukes the user's sessions. Secrets live ONLY in gitignored `deploy/.env`
(`config.py` dotenv-loads it on bare metal unless `GI_DOTENV=0` — that pin in
service_tests must NEVER be removed). Secret-scan every push range for the Meta token prefix (`EAA…`) and the
WhatsApp phone-number ID before pushing (the exact grep lives in the project
memory — deliberately not reproduced here).

## 7. AI routing layers

1. **Hub Assistant** (`/ai/assistant`, SSE) + insights/EOD — same-box Ollama,
   one warm model.
2. **`POST /ai/query` (chat-with-your-data, level ≥2)** — two lanes:
   **template lane** = deterministic intent router (`ai/query_router.py`:
   returns/receipts/issues/stock/low-stock/expiring/top-suppliers/PRs/POs +
   time windows + site mention **+ 2026-07-18 deep filters: category
   aliases → bound `ILIKE :cat`, and material-family keywords ("furan",
   "remafix"…) → ILIKE over description/material-code PLUS the SME tables
   via the `sme_recipe.SAP_Code` join**), fully bound-param SQL, **scoped
   users' site enforced from the JWT** (safe for HODs, works with Ollama
   down; count questions return a `metric`); **NL lane** = unmatched
   questions from UNSCOPED roles only → `/ai/nl-search` machinery (Ollama
   coder → SCHEMA_HINT incl. SME schema + deep-ILIKE rule →
   `is_safe_select` gate → `gi_ai_ro` read-only login). The AI-5 ruling
   stands: generated SQL never runs for a scoped user.
3. Doc-intel: PR/PO PDF extract (preview-only), vision-OCR job queue, badge
   verify. LocateAnything is RETIRED.
4. **Handwritten consumption forms** (spec: `docs/features/handwritten-ocr`,
   v1.0 — vendored; "preserve exactly" list inside): the vision model
   (`ocr.CONSUMPTION_PROMPT`) TRANSCRIBES faithfully (ditto glyphs verbatim,
   struck-through flagged, raw `qty_text`, top-right `date_text`); every
   rule is deterministic in `ai/handwritten.py` — 3-format date parsing w/
   digit fixes, the 18-entry corrections list, ditto resolution, qty rules
   (additive `2+3` sums, blank→1, zero rejects), the spec fuzzy scorer
   (auto-accept conf ≥40 + lead ≥8, top-5 candidates), the CLOSED 8-rule
   substitution table (R1–R4), whole-batch stock simulation
   ((date,form,row) order, low-stock 5, negative → blocked), the
   [?]/⚠️/🚨 flag taxonomy, and the **17-column legacy TSV export**
   (blocked rows never exported). Endpoint: SK-locked
   `POST /ai/ocr/handwritten-process` — READ-ONLY (posting stays in the
   Issue flow). Changing a preserved rule: edit the owning spec file first,
   then the module, then the suite-AM pins.

## 8. Testing — the gates

```bash
# 1. service tests (750 checks, suites A…AO) — CI mirror, hermetic
DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub \
JWT_SECRET=ci-only-service-test-secret-key-32bytes-min \
.venv/bin/python -u -m backend.api.service_tests

# 2. SQLite↔PG parity oracle (5 aggregates) — same env vars (Phase B: tools/)
.venv/bin/python tools/parity_check.py

# 3. frontend
npm run build --prefix frontend && cd frontend && npx tsc --noEmit

# 4. headless E2E (Playwright — builds/destroys its own gihub_e2e_pw stack)
cd tests/e2e && npm test        # 39 tests, ~15 s

# 5. alembic single head
.venv/bin/python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; c=Config('backend/alembic.ini'); c.set_main_option('script_location','backend/alembic'); print(ScriptDirectory.from_config(c).get_heads())"
```
Test-compat switches: service_tests sets `require_entry_documents='0'` first
(suite AH tests it ON); Playwright global-setup does the same in its clone —
the `gated` project (entry-docs.spec) runs AFTER the parallel pack because it
flips the global setting. Legacy `legacy/bug_check.py` (599, self-rooted —
run `.venv/bin/python legacy/bug_check.py`) guards the frozen Streamlit app;
its models-parity check carries an allowlist for new-stack-only columns
(SME `SAP_Code`s, bug_reports triage fields). New suites 2026-07-18:
**AJ** bulk import · **AK** OCR doc assist + prompt pins · **AL** QR/
returnables · **AM** handwritten-OCR stages + ask-data filters · **AN**
Surface-Shields workflow + Smart Calculator + report scoping · **AO** Bug
Tracking Engine. The NL round-trip check needs the `gi_ai_ro` role — CI
provisions it in the workflow; locally re-run
`backend/scripts/create_ai_readonly_role.sql` after any reload/DDL drift.
Manual matrix: [automatic_test.md](automatic_test.md).

**CI/CD (fixed 2026-07-18):** `postgres-dual-ci.yml` = bug_check + dual_ci +
parity + **gi_ai_ro provisioning step** + service_tests (with
`GI_AI_RO_URL`) + frontend build. `deploy.yml` (v1 Streamlit) is
**manual-only** — it used to auto-fire on every push against the
not-yet-provisioned Hetzner box (the perpetual "Deploy to Hetzner" failure).
`deploy-v2.yml` (manual, gated) is the cutover pipeline — its gate now uses
the post-restructure `tools/` paths + the same RO-role step.

## 9. Operational notes

- Local dev: `./run_api.sh` (:8000, asyncpg → :5433/gihub) + `npm run dev
  --prefix frontend` (:5173). Hermetic: prefix `GI_DOTENV=0 GI_SCHEDULER=0`.
- Mirror Postgres runs on brew postgresql@16 :5433 (autostart).
- Meta/WhatsApp is LIVE (templates approved, lang `en`); operator TODOs that
  remain: approve `gi_evening_summary`, set webhook env + subscribe URL,
  set `PUBLIC_BASE_URL`.
- Remaining program work: **ONLY the Hetzner production deployment**
  (runbook `tools/migration/README.md` — includes the post-load Excel
  re-sync + SME reseed). Everything else through the 2026-07-18 pre-deploy
  batch is SHIPPED (C3 OCR doc assist landed 2026-07-13 PM; B2/B3/B4/B7
  remain documented-optional). Ops handoff PDFs live in `docs/export/`
  (regenerate: `python tools/export_docs_pdf.py`).
