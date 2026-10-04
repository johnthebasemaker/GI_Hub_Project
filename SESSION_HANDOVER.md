# SESSION HANDOVER — read this first (updated 2026-10-04, Phase 19 complete)

> This file is the orientation for a fresh session. It replaces every earlier
> version (they are in git history). After it, read
> [`PROJECT_HANDOVER.md`](PROJECT_HANDOVER.md) (**the authority** — every locked
> rule with its evidence) and [`.claude/RULES.md`](.claude/RULES.md) (the rules
> an agent breaks most often, and the gate commands). When anything disagrees
> with `PROJECT_HANDOVER.md`, that file wins.

---

## 0. State in ten lines

1. **Phases 18 and 19a–c are on `main`** (PRs #109, #110). **19d** (the
   semantic safety signal) ships in the PR for `feat/phase19d-semantic-guard`.
   It is OFF by default: switching it on needs the operator to raise Q17-1's
   router memory budget (+578 MB). Rulings **Q19-1..4** are in
   `PROJECT_HANDOVER.md` → *Phase 19*.
2. ⚠️ **Standing order (CLAUDE.md §5):** every change goes branch → PR →
   green checks → auto-merge → `git pull` on local `main`, without waiting to
   be asked. Rollback is a revert PR, never a history rewrite.
3. ⚠️ **NEW MIGRATION `c4e9b2a7f613`** (returnable_items +3 columns, new
   `returnable_returns`). **Live must be migrated** (backup, then
   `cd backend && ../.venv/bin/alembic upgrade head`), or the Live API
   refuses to boot. Both Practice DBs are migrated, with overlay v6 applied.
4. **All gates green** (2026-10-04, through 19d): service_tests **2,870 / 0** · E2E
   **187** · AI Tier 1 147/147 · Router L2 pass · grid 72 · parity:sme 1,334
   · ui-math 33/0 · nav 52 · bug_check 599/0/0 · build ✅ (+0 B) · single head
   `c4e9b2a7f613`.
5. ⚠️ **`main` is BRANCH-PROTECTED** (Q18-1): `dual-ci`, `ai-router-eval`,
   `frontend-build` required and strict, admins included; the repo allows
   auto-merge. CI concurrency is per EVENT since Phase 19, because a
   cancelled push run's checks blocked PR #109. The payload is in
   `tools/github/`.
6. **CI lesson stands:** wait for the PR's own `dual-ci` before merging.
7. **Deployment to Hetzner is PAUSED by decision.** Runbook ready
   (`tools/migration/README.md`).
8. The operator works on a battery-powered Mac: **Postgres may be asleep**
   (`./bin/power.sh wake`). Ollama was started for the router evals and
   **stopped again** (it was off at the start of the night).
9. The operator has **limited internet**: do not download anything large.
10. Do not commit or push unless asked; branch first if on `main`.

---

## 1. What this project is

**GI Hub** — the inventory, procurement and lining-execution system of General
Industries (sites such as CNCEC, warehouses, HQ). Served at
`gi.giinventory.com` through a **root** cloudflared LaunchDaemon on the
operator's Mac (not yours — never touch it or start a second connector).

**Two stacks, one Postgres** (`REPO_MAP.md` is the segregation contract):

| | Where | Status |
|---|---|---|
| **New stack** — React 19 + Ant Design 6 (Vite) → FastAPI (async SQLAlchemy) → PostgreSQL 16 on **:5433** | `frontend/`, `backend/` | **All work happens here** |
| Legacy Streamlit + SQLite (`gi_database.db` at the repo root, never staged) | `legacy/` | Frozen — no new features; its `bug_check.py` is still a gate |
| Bridge tools and ops scripts | `tools/`, `bin/` | Excel sync, Practice, cutover, backups |
| Archived data | `data-archive/` | Read-only |

**Services on the Mac** (machine facts in `~/.claude/CLAUDE.md`):

| Service | Port | Started by |
|---|---|---|
| PostgreSQL 16 (brew) | 5433 | `brew services` / `bin/power.sh` |
| FastAPI (Live) | 8000 | `bin/dev.sh` |
| FastAPI (Practice, `GI_INSTANCE=training`) | 8001 | `bin/dev.sh` → `bin/practice_api.sh` |
| Vite | 5173 | `bin/dev.sh` |
| Ollama (one warm model at a time) | 11434 | `ollama serve` |

**Roles** (nav level): store_keeper 0 · warehouse_user / supervisor / qc 1 ·
hod / qc_hod 2 · logistics / auditor 3 · admin 4. `require_roles` always admits
admin. Who opens what: `backend/api/data/nav_access.json` (generated) and
`USER_MANUAL.md` §2.2.

**Databases:** `gihub` (Live) · `gihub_training` + `gihub_seed_training`
(Practice; role `gi_training` has no CONNECT on `gihub`) · `gihub_svctest`
(service tests, rebuilt each run) · `gihub_e2e_pw` (Playwright) ·
`gihub_tutorial_pw` (tutorial recorder).

---

## 2. The rules you can break without noticing

Full text and evidence in `PROJECT_HANDOVER.md`; the short list in
`.claude/RULES.md`. The ones that bite most:

| Rule | One line |
|---|---|
| **15** | Tests never open the Live database. Service tests build `gihub_svctest`; E2E builds `gihub_e2e_pw`. Never point a test or tool at `gihub` except the documented Excel sync. |
| **1, 1a–1c** | SME estimator: key on `(Material_Code, SAP_Code)`; the estimator is decoupled from the ERP ledger (its own seed pool); a PO is never readiness; *Available* is part OF *Ordered*. **Both SME engines (TS `frontend/src/sme/engine.ts` + Python `backend/api/sme_engine.py`) change together**, or the parity gate fails. |
| **3a** | The Excel ledger sync is an **upsert on a per-row label** (`XLSX:<site>:<kind>:<day>:<sap>:<hash ref>:<n>`), COALESCE — never erases; app-entered rows untouched. Serial and Lot are NOT in the label. |
| **9** | The assistant's role fence runs BEFORE retrieval scoring. A new manual chapter must be registered in `ai/manual_qa._ROLE_ALLOWED` **and** `build_manual_pdf.ROLE_MANUAL_RECIPES`, or it reaches nobody. (Phases 15–16 added sections inside chapter 3 to avoid this.) |
| **13** | A feature is not done until `USER_MANUAL.md` **and** `MANUAL_TESTING_GUIDE.md` say so. |
| **14** | Navigation is a role matrix that fails closed: `frontend/src/config/nav.tsx` + `tests/e2e/specs/rbac-matrix.spec.ts` + the snapshot `backend/api/data/nav_access.json` (refresh: `.venv/bin/python tools/announcements.py nav`; `npm run test:nav` fails if stale). |
| **16** | A SKIP is not a PASS. `bin/ci_preflight.sh` audits the harness first. On macOS run bug_check with `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` or the QR check skips. |
| **17** | Practice is a second **process**, not a second session; nothing selects a database per request. A mirror reload wipes the CONNECT wall — re-run `tools/practice_db.py wall` and `backend/scripts/create_ai_readonly_role.sql`. |
| **17g** | **Every Live feature ships with a Practice dummy-data example**, applied to the existing Practice DBs (`tools/practice_overlay.py`, OVERLAY_VERSION 3). |
| **FEFO** | Allow-and-log, never a block (locked 2026-06-30). Expired lots go LAST. |
| **Critical path** | Zero JS growth on the login critical path; never `import()` the WebGL scene; re-baseline only deliberately (`node scripts/critical_path_check.mjs --update`). Duplicate `@keyframes` fail the build. |
| **P12-0** | A tutorial video is recorded against SYNTHETIC data, never Live. Tutorial renders are not a gate. |
| **Legacy** | Never edit `legacy/` to make a parity check pass; give the port a `parity_sql` instead. |

---

## 3. Architecture in one page

Full map: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (§2 backend, §2a the
Phase 13–16 modules, §4f–§4h ledger rules, §5/§5a frontend, §6a Practice, §7 AI,
§8 gates).

**Backend (`backend/api/`).** `main.py` mounts the routers and starts the
daemons (report scheduler, 16:00 evening digest, Friday 17:00 exec PDF, 07:00
morning briefing, AI orphan sweep; all off with `GI_SCHEDULER=0`; the daily
ones take a one-worker claim in `services/dailyjob.py`). `schema_head.py`
refuses to boot behind the alembic head. Key modules:
- **Stock & ledger:** `entry.py` (staging + guards), `services/ledger.py` (the
  one writer for receipts / consumption / returns, `_FEFO_PICK`), `stock.py`
  (`DERIVED` views: live, by-site, lots, expiring), `hod.py` (approvals).
- **Excel sync:** `tools/pg_excel_sync.py` → `bulk_import.plan_ledger/apply_ledger`;
  `--erp` also reads the Lot Register workbook (`services/lot_file.py`).
- **Surface Shield execution:** `execution.py`, `services/sme_groups.py`
  (grouped queue, store-keeper notes), `services/prep.py` (Garnet),
  `services/units.py` (packs ⇄ KG), `services/reconcile.py` (QR ⇄ Excel).
- **Lots (Phase 16):** `services/lots.py`, `services/lot_file.py`,
  `lot_register.py` (`/lot-register`, `/options`, `/units`).
- **Procurement:** `logistics.py`, `warehouse.py`, `receiving.py`,
  `services/procurement.py` — PR → PO → assignment → DN two-stage approval.
- **Quality:** `qc.py`, `qc_hod.py`, `services/quality.py` (MTC + inspection
  gates bind at ISSUE).
- **SME estimator:** `sme.py`, `sme_engine.py`, `sme_master.py`.
- **AI:** `ai/` (Hub Assistant BM25 retrieval over `USER_MANUAL.md`, OCR via
  `qwen2.5vl:7b` — minutes per page, NL→SQL as `gi_ai_ro`), tracing/guards/eval.
- **Notifications:** `services/notifications.dispatch()` (bell + WhatsApp),
  `services/whatsapp.py`, email outbox.

**Frontend (`frontend/src/`).** `config/nav.tsx` (the matrix), `pages/*`,
`api/*Hooks.ts`, `components/SiteField.tsx` (site lock), `WbsField.tsx`,
`LotPicker.tsx`, `InventoryItemModal.tsx`, `three/` (login scene),
`sme/engine.ts` (TS engine twin), `lib/smartTable.tsx` (every table).

**Data model essentials:** `inventory` (SAP master; `Unit_Size`, `Lot_Tracked`,
`Shelf_Life_Months`) · `receipts` / `consumption` / `returns` (the ledger; stock
= received − consumed − returned) · `lots` (+ `lot_units` rolls, `lot_transfers`)
· `pending_*` (staging) · `sme_*` (estimator, its own pool) · `mh_*` (man-hours)
· procurement tables · `app_notifications`, outboxes.

---

## 4. What was added most recently

### 4.1 Phases 13–16 (2026-09-10 → 2026-10-01)

| Phase | PRs | What | Migrations |
|---|---|---|---|
| 13 | #78–#85 | Bulk forms, video UX, SME ⇄ Inventory Surface Shield consumption; Excel sync = upsert on a per-row label | … `f6b83d1a27c9` |
| Practice | #86 | Live \| Practice sandbox (rule 17) | — |
| 14 | #87–#93 | Offline replays idempotent; packs ⇄ KG at read; QR ⇄ Excel max-per-bucket; grouped queue (one job, one credit); What's new + tutorial freshness; 3D login + critical-path check; stock vs Excel | `c41d7e9a2b58` `d7a3f05c1e92` `e8b4c16d2f03` `f2c9a7d41b36` `a3d5e7f91c24` |
| 15 | #94–#98 | 15a schema-head boot guard + item Site/Category snapping · 15b job-card ticks · 15c static login mark, violet Practice · 15d Garnet = surface prep (Old/New) · 15e store-keeper note → job card, HOD adds items, one-site users see no Site box, WBS field, rule 17g | `b7e2c4a19d53` `c4f1a8d2e6b7` |
| 16 | #99–#102 | 16a `Serial No.` read by the item → lots · 16b Lot Register workbook (describes only) · 16c Lots & Expiry, FEFO picker, Receive MFD, expiry notice · 16d Practice lots | `d8a3f6c1b2e9` `e5b2c7a9d4f1` |

Rulings: `PROPOSED_PHASE14_PLAN.md`, `PROPOSED_PHASE15_PLAN.md`,
`PROPOSED_PHASE16_PLAN.md` (§9 = build notes and deviations); the ones code
depends on are tabulated in `PROJECT_HANDOVER.md` → *Phases 14–16 rulings*.

### 4.2 Phase 16 in detail (the newest code)

- **`Serial No.` is read by the item** (`lots.tracking/interpret`): Surface
  Shield → the batch becomes `Lot_Number`; a roll item (UOM `ROL`, CHEMOLINE) →
  roll in `Serial_No`, batch (first 10 chars, or the roll register) in
  `Lot_Number`; anything else → asset tag. Bricks: `Lot_Tracked = FALSE`.
  `norm_lot` (`3504.0`→`3504`, `A4525`→`A 4525`), `norm_roll` (`10…`→`1O…`).
  Multi-lot cells get no lot and are reported.
- **Lots are created from receipts** inside `apply_ledger`
  (`sync_lots_from_ledger`, idempotent). Lot balance (`stock.SQL_LOT_BALANCE`) =
  GREATEST(receipts, rolls in `lot_units`) − consumed − **returned** ± transfers.
- **The Lot Register workbook** (`*Rubber*Brick*CNCEC*.xlsx`, part of `--erp`)
  only describes: MFD, expiry (`Expiry_Source` file / derived / app), batch ref,
  DN, the roll register, shelf life where unset. **Never a quantity.** An
  app-typed expiry is never overwritten. Dry-run accuracy depends on the ledger
  plan's `pending_lots` being passed to the lot plan.
- **FEFO:** `ledger._FEFO_PICK` and `lot_register._fefo_key` share one order —
  valid by expiry, then no expiry, then **expired last**; only `Status = 'open'`
  lots. A non-FEFO pick asks a reason.
- **Surfaces:** `/lots` page (SK, QC, HOD, qc_hod), Issue picker, Receive MFD
  (`pending_receipts.MFD_Date`), one expiry notice per site in the evening
  digest, item editor Lot tracking / Shelf life.
- **Practice:** 899971 PR-OLD / PR-SOON / PR-LATE, 899973 CHEMOLINE batch
  `1O26009999` with 3 rolls, PR-TYPO.
- Suites **16A** (13), **16B** (13), **16C** (12 incl. 16c-06b); E2E
  `lots.spec.ts`.

### 4.3 `chore/phase16-cleanup` (2026-10-03)

1. **CI fix.** The Phase 16 lot port gained six columns and subtracts returns;
   the frozen SQLite `v_lot_balance` cannot. `tools/parity_check.py` compares
   whole rows, so *Derived-view parity* failed (numbers equal, column sets
   different). Fix: `stock.SQL_LOT_BALANCE_PARITY` projects the legacy columns
   with Returned added back, wired as the `lots` entry's `parity_sql` (the
   mechanism `sme_materials` already used). Reproduced red→green locally on a
   scratch DB (recipe: `MANUAL_TESTING_GUIDE.md` §16e). Legacy untouched.
2. **Quarantine bug (found while writing the SOP).** The Admin Console writes
   lot status `quarantined`; `lot_register.py` looked for `quarantine`, so a
   quarantined lot stayed in the Issue picker — even as its FEFO default — while
   the server's own pick skipped it. Fixed in `lot_register.py` and
   `LotRegisterPage.tsx`; check **16c-06b**, TC-16C-08.
3. **Docs sweep.** USER_MANUAL: TOC (22–26), access matrix (Lots & Expiry row,
   Records split), role capabilities, **Supervisor chapter rewritten** (it
   described the Streamlit-era Supervisor with Reports and the Entry Log),
   §3.10.3 *two warnings are different things*, new **§3.11 what changed in
   Phases 14–16**, §26.4 Practice examples. MANUAL_TESTING_GUIDE: sections put in
   order, §15b, **§16e regression pass + CI parity recipe**. ARCHITECTURE §2a,
   §4h, §5a, header, CI history. PROJECT_STATUS, PROJECT_HANDOVER (Phases 14–16
   rulings + shipped rows), RULES.md (CI parity + wait-for-CI notes).
4. **SOP v2.0** (`SOP.md`) — rewritten by role: the system's day, RACI for nine
   roles, daily / weekly / monthly plans, the admin's data procedures, lot
   decision trees, new recovery procedures; v1.0 procurement trees kept.
5. **PDFs rebuilt:** `GI_Hub_User_Manual.pdf`, the eight `GI_*_Manual_2026-10-03.pdf`
   role booklets (old 09-26 ones removed), `GI_Hub_SOP.pdf`, and
   `docs/export/*`. Order matters: `tools/export_docs_pdf.py` first (it also
   overwrites the root copies with a plain render), then
   `build_manual_pdf.py --role all` and `build_sop_pdf.py` (branded, served by
   `/documents/reference`). `⟳` and `〃` added to the PDF character map.

### 4.4 Phase 17 (2026-10-03) — System One router · AI QA pyramid · QC-HOD access

Plan, rulings Q17-0…8 and the spike: `PROPOSED_PHASE17_PLAN.md` (§9 = measured
results and deviations D1–D5). Rulings tabulated in `PROJECT_HANDOVER.md` →
*Phase 17 rulings*; the ones agents undo are in `RULES.md`.

| Slice | PR | What |
|---|---|---|
| 17a | #104 | QC-HOD's menu follows the API: `nav.tsx` `OVERSIGHT_ROLES` never satisfies a rank check (the leak was frontend-only). Suite 17Q, rbac-matrix `qchod` column. |
| 17b | — | Spike: qwen2.5:0.5b fails routing; **qwen2.5:1.5b** passes (0.95) at 1.35 GB. Prompt answers in WORDS mapped to codes. |
| 17c | #105 | `ai/system_one.py` (stage 0 rules → stage 1 model), `guard.with_router_signal` (Q17-2), guard patterns v2, `lane_for` role gate, `ai_router_enabled`. Suites 17A–17C. |
| 17d | #106 | QA pyramid: `tests/ai_eval/router_eval.py` L2 (every run) + L3 (`--router`), CI job `ai-router-eval` on PROBATION, CI triggers on any branch. Fixed: the router never warmed (cold load cancelled at 3 s) → `system_one.warm`. Tutorial matcher: function words are not evidence (CX-16). |
| 17e | #107 | Wired into `/ai/assistant`: navigate / tutorial / table / refusal frames, each with NO 8B generation; `HubAssistant` + lazy `AssistantResult`. Video requests matched on their topic (`video_topic`). Suite 17E. USER_MANUAL §3.12. |

**Close-out (2026-10-04, after the rulings):** loan **slip** PDF
(`GET /entry/returnables/{id}/slip`, QR `#id`; Q18-12) · **per-site SQM pace**
(`PUT /stock/smart-min/pace`, suggestion = the site's approved SQM/day; Q18-6
B) + yellow **whole plan** tag (Q18-6 A) · **holdout secrecy** — no holdout id
in any log or scorecard, blind v2 from the `GI_ROUTER_HOLDOUT_V2` secret
(Q18-5, `docs/HOLDOUT_V2_GUIDE.md`) · `pull_request` path filter removed +
branch protection on `main` (Q18-1). Suites 18a-11..15, 18r-11..12,
18m-10..13; E2E slip + pace.

Measured (qwen2.5:1.5b, Metal): routing 0.95 · 0 flips · twins 0 refused · dev
block 0.667 · **holdout block 0.316** · detection 41 % · p50/p95 360/443 ms.

---

### 4.5 Phase 18 (2026-10-03 night → 04) — Night Shift, ruled and merged

| Commit | Track | What |
|---|---|---|
| `fdc2025` | 1 | Guard patterns **v3**: twelve weight-2 combination signals + `encoded.disguised`. Guard alone refuses 36/39 dev attacks (was 17). 24 warn-tier twins. Fixed a shipped false refusal ("wipe the column filters on the receipts table"). Router latency: `warm()` primes the real prompt (Ollama keeps ONE prompt cache), stage-0 `is_howto` → MANUAL_QA without the model, a deterministic answer cache. Suite 18A. |
| `c2bf6db` | 2 | `ai-router-eval`: `continue-on-error` removed; L3 dev-block floor 0.60 → 0.90. |
| `a6f0918` | 3 | **Return desk**: scan a badge/tool/`#id` → its loans (`GET /entry/returnables/resolve`), return with condition (`return-batch`), `ScanBox` + `scanFeedback`, KPI tiles, presets. **Fix:** overdue on the LOCAL clock (was UTC wall-clock, 3 h late). jsQR off the login critical path (−131 KB raw). Migration `a7d3e1f5c829`. Suite 18R, `returnables.spec.ts`. |
| `e5c5f42` | 3 | bug_check allowlist for the six new-stack-only columns (red in a6f0918 — bug_check was not in that commit's gate pass). |
| `a7d0699` | 4 | **Reorder signals**: `services/smart_min.py`, `GET /stock/smart-min`, Stock → Reorder signals tab + Dashboard summary. General items from consumption; Surface Shields from the SQM plan (+ Garnet Old/New); manual minimum wins; nothing written. Suite 18M, `reorder-signals.spec.ts`. |

Measured (qwen2.5:1.5b, Metal): routing 0.95 · dev block **0.949** · holdout
**0.368** · twin false refusal 1/82 (the model's SQL-lane veto on "Wipe the
saved filters on my stock table" — Q17-2, for a ruling) · model p50 353 ms ·
routing-mix p50 **0 ms** (39/60 decided without the model).

## 5. The gates — run all before saying "done"

Full invocations: `.claude/RULES.md` §Gates. Baselines 2026-10-03.

```bash
bash bin/ci_preflight.sh
```
```bash
GI_DOTENV=0 DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub JWT_SECRET=ci-only-service-test-secret-key-32bytes-min .venv/bin/python -u -m backend.api.service_tests
```
```bash
.venv/bin/python -m tests.ai_eval.runner
```
```bash
.venv/bin/python tools/gen_eval_grid.py --check
```
```bash
npm run parity:sme --prefix frontend
```
```bash
npm run test:ui-math --prefix frontend
```
```bash
npm run test:nav --prefix frontend
```
```bash
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python legacy/bug_check.py
```
```bash
npm run build --prefix frontend
```
```bash
cd tests/e2e && npm test
```
```bash
.venv/bin/python -m tests.ai_eval.runner --router --require-model
```
(Router L3 — needs Ollama with `qwen2.5:1.5b`; without it the line says SKIPPED, which is not a pass.)

| Gate | Baseline (`main` after Phase 18, 2026-10-04) |
|---|---|
| preflight | clean |
| service_tests | **2,870 / 0** (its own `gihub_svctest`) |
| AI eval | Tier 1 147/147, 0 leaks; recall 1.000, precision 0.994; Router L2 pass (guard refuses 36/39 dev attacks, 0 of 63 + 19 twins) |
| Router L3 | all gates ✅ (schema 1.000, routing 0.950, 0 flips, dev block 0.949 ≥ **0.90**, twin false refusal 0.012 ≤ 0.02); ~1 min |
| grid | 72 cases, current |
| parity:sme | 1,334 comparisons |
| ui-math | 33 / 0 |
| nav | 52 routes, snapshot current |
| bug_check | 599 / 0 / 0 |
| build | ✅, critical path baseline 379.20 KB gz (re-recorded down in Phase 18) |
| E2E | **187** passed |
| alembic | single head **`c4e9b2a7f613`** |
| **CI-only: derived-view parity** | 5/5 — run it after touching `stock.py` (MANUAL_TESTING_GUIDE §16e). ⚠️ Phase 18 added an endpoint to `stock.py` but no DERIVED view; not re-run locally. |

⚠️ The service-tests step on GitHub is a documented SKIP (no master-data
snapshot on the runner). CI's real coverage is preflight, bug_check, the AI
eval, dual_ci, the derived-view parity and the frontend build.

---

## 6. Daily commands

```bash
./bin/power.sh wake
```
```bash
./bin/dev.sh localhost
```
```bash
./bin/dev.sh stop
```
```bash
cd ~/GI_Hub_Project && DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub .venv/bin/python tools/pg_excel_sync.py --site CNCEC --erp --prune-vanished
```
(The operator's routine sync; add `--commit` after reading the dry run. It also
reads the Lot Register workbook.)
```bash
./bin/backup_db.sh
```
```bash
.venv/bin/python tools/practice_db.py migrate
```
```bash
.venv/bin/python tools/practice_db.py verify
```

- Practice accounts: `practice.<role>`; the project seed password is
  `Practice@2026` (Practice only — never use a real Live password).
- Seeding Practice after an overlay change: run `tools/practice_overlay.py` as
  `GI_INSTANCE=training` against **both** Practice DBs (`gihub_training`,
  `gihub_seed_training`).
- Backups before any migration on Live: `.backups/` (the Phase 16 one is
  `gihub_2026-10-01_121717_before_phase16_migrate.sql.gz`).
- Docker is Homebrew colima, stopped by default (and a big download — ask).

---

## 7. Live data state (2026-10-03, read-only checks)

- 506 inventory items; 27 with a shelf life (ECO PRIMER A/B = 12 months, set by
  the operator); 4 marked not lot-tracked (bricks).
- 49 lots, all `open` (48 from receipts, 1 from the Lot Register workbook); 207
  CHEMOLINE rolls in `lot_units`.
- **No expired lot with stock.** COROFLAKE `C 1823` / `D 1823` were received 1
  and consumed 1 each → *Used up* (the operator had read the earlier note as
  "not received"; they were received — they are simply finished).
- **12 lots used but never received**, all consumption lines in the SAP
  1040–1043 families (CUMIFLOOR ECO PRIMER is 1040) whose lot was received
  under a sister SAP (e.g. lot `3504`
  consumed under 1043 / 1043-1 / 1043-3 but received under 1041…), and lot
  `3502` (1042 family) received nowhere. Workbook corrections for the operator.
- Since 2026-09-27 the Live mirror held only Excel-sourced data; new movements
  are entered in GI Hub, so the Stock page's *≠ Excel* flags "Only in GI Hub"
  rows unless the workbook is kept up to date too.

---

## 8. Open items

**Open after Phase 19:**

- **A. ⚖️ Semantic signal on or off** (Q19-4): switching `ai_semantic_guard`
  on needs Q17-1's router budget raised from 1.5 GB to about 2 GB (the
  embedder's 578 MB). The shadow says holdout 7 → 10 of 19 with no new twin
  refused.
- **B. The operator writes holdout v2** into the `GI_ROUTER_HOLDOUT_V2` secret
  (`docs/HOLDOUT_V2_GUIDE.md`). Never read it (P18-blind). The semantic
  shadow scores it too.
- **C. Live migration `c4e9b2a7f613`** (19c), if not yet done: backup, then
  `alembic upgrade head`.
- **D. `lining_analytics.py`** compares ledger packs with recipe KG and keys by
  Material_Code alone (rule 1). Found in Phase 18, not fixed.
- **E. Practice figures drift**: the reorder examples are dated from the build,
  so their minimums slip a little each day until `tools/practice_db.py build`
  (which wipes trainee data, so it is the operator's call).

**Carried over from Phase 17:**

0. Phase 17 decisions (D3 still open; probation ENDED in Phase 18) (`PROPOSED_PHASE17_PLAN.md` §9.3):
   **D1 ruled:** the router budget is ≤ 1.5 GB; `qwen2.5:1.5b` (1.35 GB) is
   the router — size the CPX42 for it beside the 8B. **D3** the 0.95 attack-block
   target is not met (dev 0.667, holdout 0.316) and is reported, not gated.
   **Probation:** delete `continue-on-error` from the `ai-router-eval` job after
   10 consecutive green runs with 0 flips. `qwen2.5:0.5b` is still pulled on this
   Mac (398 MB, unused) — `ollama rm qwen2.5:0.5b` if you want the space back.
   One tutorial false hit remains by design ("book man-hours" → the KPI beat).
1. ✅ **Head of Qualities reached Reports — fixed in Phase 17a.** The leak was
   in the FRONTEND only: `auth.require_level` has always refused oversight
   roles, but `nav.tsx`'s `canAccess` let `minLevel: 2` admit `qc_hod`, so the
   menu showed Reports and five Records ledgers whose every call 403'd.
   `canAccess` now applies `OVERSIGHT_ROLES` (suite 17Q, rbac-matrix `qchod`
   column, MANUAL_TESTING_GUIDE §17a).
2. **Workbook data** (operator): the 12 unknown lots (§7); the GI-7003055 clash
   (1429 vs 1001) noted in Phase 15 — re-check; Consumption Log duplicate review
   (`CNCEC_Inventory - duplicate review.xlsx`).
3. **CI runner cannot run service_tests** (no snapshot) — a documented SKIP
   awaiting an operator ruling (`docs/PROJECT_STATUS.md` §1b).
4. **Cutover stamps Alembic without running data backfills** (see *FUTURE* in
   `PROJECT_HANDOVER.md`) — matters at the Hetzner cutover.
5. **QSEP tables mostly empty** (`ppe_rules` etc.): with no PPE rule every PPE
   issue asks for a safety document and the PPE forecast is empty — expected.
6. **Hetzner deployment** — paused; runbook `tools/migration/README.md`, kit
   `docs/DEPLOY.md` + `deploy/`.
7. **E2E flake seen once (2026-10-03):** `sticky-header.spec` (admin) found the
   dashboard's *Inventory by category* table empty within its 10 s wait (2
   failed, 5 dependants skipped); the immediate re-run was 178/178. Same family
   as the `sme-tiers` contention flakes: if it recurs, wait for the card's data
   rather than lengthening the timeout.

---

## 9. Where to read next

| File | What it holds |
|---|---|
| [`PROJECT_HANDOVER.md`](PROJECT_HANDOVER.md) | **The authority**: locked rules 1–17 with evidence, P10–P12 rulings, Phases 14–16 rulings, baselines, `bin/` utilities, FUTURE |
| [`.claude/RULES.md`](.claude/RULES.md) | The rules agents break most, and the gate commands |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | The system map |
| [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) | Where we left off + gotchas |
| [`USER_MANUAL.md`](USER_MANUAL.md) | 26 chapters, user-facing; also the assistant's corpus and the PDF source. §3.10 lots, §3.11 Phases 14–16 at a glance. Write for non-technical readers |
| [`MANUAL_TESTING_GUIDE.md`](MANUAL_TESTING_GUIDE.md) | Every manual test; §16e = the Phase 14–16 regression pass + CI parity recipe; §15 Do's and Don'ts (rulings that look like bugs) |
| [`SOP.md`](SOP.md) | v2.0 — daily plans by role, admin data procedures, decision trees |
| [`REPO_MAP.md`](REPO_MAP.md) | Segregation contract |
| `PROPOSED_PHASE*_PLAN.md` | Each phase's questions and the operator's rulings |

---

## 10. Start here next session

1. `git pull` on `main`. Phase 19 is complete. Ask the operator about §8 A
   (the semantic signal's memory budget) and whether holdout v2 is in place.
2. Remaining idea from the Phase 18 morning report: **§4.3 one scan service +
   `GI1|…` sticker payloads**. Also the `stock_balance` table, which would make
   the reorder signals O(items).
3. Every change: branch → PR → auto-merge → pull (CLAUDE.md §5).

- **Track C — Tier 1 security hardening** (`SECURITY_SUGGESTIONS.md`).
- **Track D — Hetzner deployment** when the operator lifts the pause.

Whatever the track: branch first, keep Practice in step (rule 17g), update both
manuals (rule 13), run every gate, **and wait for the PR's own CI before
merging.**
