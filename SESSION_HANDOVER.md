# SESSION HANDOVER — read this first (rewritten 2026-10-03, end of Phase 16)

> This file is the orientation for a fresh session. It replaces every earlier
> version (they are in git history). After it, read
> [`PROJECT_HANDOVER.md`](PROJECT_HANDOVER.md) (**the authority** — every locked
> rule with its evidence) and [`.claude/RULES.md`](.claude/RULES.md) (the rules
> an agent breaks most often, and the gate commands). When anything disagrees
> with `PROJECT_HANDOVER.md`, that file wins.

---

## 0. State in ten lines

1. **Everything through Phase 16 is merged to `main`** (PRs #78–#102). Branch
   `chore/phase16-cleanup` (2026-10-03) carries the CI fix, a quarantine bug fix
   and the documentation sweep. Check whether it is merged: `gh pr list --state all --head chore/phase16-cleanup`.
2. **Nothing is mid-flight.** No half-done slice, no pending migration.
3. **Alembic single head `e5b2c7a9d4f1`** — Live (`gihub`) and both Practice
   databases are on it.
4. **All gates green** (2026-10-03): service_tests **2,763/0** · E2E **178** ·
   AI Tier 1 147/147, recall 1.000 / precision 1.000 · grid 72 · parity:sme
   1,334 · ui-math 33/0 · nav 52 · bug_check 599/0/0 · build + critical path
   +0 B · **CI derived-view parity 5/5**.
5. **CI (`Postgres dual-CI`) was red on all eight Phase 16 runs** at
   *Derived-view parity*; fixed on `chore/phase16-cleanup` (§4.2). Lesson,
   now in RULES.md: **wait for the PR's own `dual-ci` before merging.**
6. **Live data:** the operator committed the Phase 16 lot sync on 2026-10-03 —
   49 lots, 207 CHEMOLINE rolls, no expired lot with stock, 12 lots *used but
   never received* (workbook typing slips — the operator's to fix).
7. **Deployment to Hetzner is PAUSED by decision.** Runbook ready
   (`tools/migration/README.md`).
8. The operator works on a battery-powered Mac: **Postgres may be asleep**
   (`./bin/power.sh wake`). Ask before starting or stopping shared services.
9. The operator has **limited internet**: do not download anything large (Docker
   images, models) without asking.
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

---

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

| Gate | Baseline |
|---|---|
| preflight | clean |
| service_tests | 2,763 / 0 (its own `gihub_svctest`) |
| AI eval | Tier 1 147/147, 0 leaks; recall 1.000, precision 1.000 |
| grid | 72 cases, current |
| parity:sme | 1,334 comparisons |
| ui-math | 33 / 0 |
| nav | 52 routes, snapshot current |
| bug_check | 599 / 0 / 0 |
| build | ✅, critical path +0 B |
| E2E | 178 passed |
| alembic | single head `e5b2c7a9d4f1` |
| **CI-only: derived-view parity** | 5/5 — run it after touching `stock.py` (MANUAL_TESTING_GUIDE §16e) |

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

## 8. Open items — none blocking

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

## 10. Start here next session — choose ONE track

Nothing is half-finished, so this is a choice, not a queue.

- **Track A — operator data clean-up support.** Walk the operator through the
  12 unknown lots and any sync report items; re-run the sync dry run with them.
- **Track B — the qc_hod Reports question** (§8.1): confirm intent, then fix or
  document, with an rbac-matrix case.
- **Track C — Tier 1 security hardening** (`SECURITY_SUGGESTIONS.md`): 2FA for
  admin/logistics (enrol two admins first), shared OTP limiter, non-blocking
  dependency scanning in CI.
- **Track D — Hetzner deployment** when the operator lifts the pause.

Whatever the track: branch first, keep Practice in step (rule 17g), update both
manuals (rule 13), run every gate, **and wait for the PR's own CI before
merging.**
