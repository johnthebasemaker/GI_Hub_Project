# PROPOSED — "Training Mode" sandbox environment

> **Status: APPROVED and IMPLEMENTED (2026-09-24), branch `feat/practice-sandbox`.**
> Drafted against `main` @ `e518221`. The operator approved Option C, the
> CONNECT wall, distinct JWT secrets and rule 17, and answered Q1–Q10 (recorded
> in §12 below). What shipped differs from this proposal in the places listed
> in §12 — everything else is as written.
>
> ⚠️ Naming: the internal name stayed `training` (`GI_INSTANCE`, `*_training`
> databases, `/training-api/`); the user-facing label is **Practice** (ruling Q1).
>
> Read with: `PROJECT_HANDOVER.md` (rule 15, P10-x, P11-x, P12-x),
> `.claude/RULES.md`, `tools/make_tutorial_db.py`.

---

## 0. The recommendation in one paragraph

Run Training as a **second, separate API process** (Option C) with its own
`DATABASE_URL` naming its own database, `gihub_training`. It connects as its own
Postgres role, which **does not have CONNECT on `gihub`**, and it signs tokens
with its own `JWT_SECRET`. nginx mounts it at **`/training-api/`** next to the
existing `/api/`. The same SPA build serves both, and the login toggle only
chooses which of the two bases the app talks to. On top of that, borrow the good
part of Option B: every token carries an `env` claim, and each process refuses a
token that names the other. Borrow one part of Option A as well, but only as a
**tripwire, never as a router**: every request carries `X-GI-Instance`, and a
mismatch is refused with a 409. Seed the sandbox from the Phase 12 synthetic
dataset through the same `cutover_migrate.py` path the tutorials use. Reset it
by **cloning a Postgres template database**, which takes about a second, from a
button that exists **only in the Training instance**.

Why this shape, briefly: rule 15 already found the isolation mechanism that
holds in this codebase. The engine is built at import time from whatever
`DATABASE_URL` says, so you get isolation by giving a process a different URL
and refusing to boot when the names collide. Option C applies that same
mechanism to a second long-running process. Options A and B would need what rule
15 rejected: switching databases at runtime inside one process. There, a single
forgotten call site writes to the wrong database silently, and this codebase has
**30 such call sites in 12 files** that never see a request (§2).

---

## 1. What I read, and the facts that decide this

| Fact | Where | Why it matters here |
|---|---|---|
| The engine is a **module-level singleton** built from `DATABASE_URL` at import. | `backend/api/db.py:18` | Under Option A or B, a per-request switch would have to replace this singleton everywhere it is used. Option C reuses it unchanged. |
| **30** `async with SessionLocal()` sites in **12** files open sessions **outside** request dependency injection: AI jobs, OCR form jobs, trace drain, answer cache, bloom filters, rate limiter, notifications/digest, weekly report, report scheduler, health briefing. | `ai/jobs.py` (8), `ai/answer_cache.py` (4), `services/bloom.py` (3), … | None of these has a request, so none has a header or a JWT. Under A or B each one would need its own way to know which environment it is serving, and the one that gets forgotten **falls back to production**. |
| The AI NL→SQL path has a **second engine**, `gi_ai_ro`, derived from `DATABASE_URL` by swapping the username. | `ai/analytics.py:38-51` | Under A or B this is a third engine to switch. Under C it follows the process URL automatically. |
| Several things are **process-global in memory**: Bloom filters (snapshot of usernames etc.), in-process login throttle, manual index, trace queue. | `services/bloom.py`, `auth.py:1027`, `ratelimit.py` | In one process serving two databases, the username-availability Bloom filter would answer for the wrong register. |
| Background loops (`scheduler_loop`, `digest_loop`, `weekly_report_loop`, `briefing_loop`, OCR orphan sweep) start in `lifespan`, **once per worker**. | `main.py:143-250` | Under A or B, each loop would need to run twice (once per database) or be taught about environments. |
| JWTs are HS256 with one `JWT_SECRET`. Claims are `sub, role, site_id, warehouse_id, scope, iat, exp`, with no issuer or environment. | `auth.py:179-197` | With one secret, a token minted by Training is **valid in Production** unless something else stops it. |
| The refresh cookie is `gi_refresh`, `path="/"`. | `auth.py:283-298` | Two backends on one origin would **overwrite each other's cookie**. Not a leak (the secrets differ) but it logs people out. The fix is cheap. |
| The SPA already has a **runtime API-base switch** (`setApiBase`, persisted in `localStorage`), whose documented contract is "sign out and reload after switching". | `frontend/src/api/client.ts:57-70` | The login toggle is mostly already built. It is a second preset base next to one that exists today. |
| The **offline mutation queue** stores a relative `path` and replays it against **whatever base is current**, using **whatever token is current**. | `frontend/src/offline/queue.ts:24-125` | ⚠️ **The most dangerous vector found, and it is in the CLIENT.** A receipt queued offline in Training is replayed into **Production** under a perfectly valid Production session once the user flips the toggle and signs in. No server-side wall stops it, because the request is legitimate in every way the server can check. §3.4 closes it. |
| The PWA read cache matches `/\/api\/(stock\/|inventory|…)/`. | `frontend/vite.config.ts:62-75` | With a **distinct URL prefix** (`/training-api/`), Training reads can never be served from a Production cache entry. Under Option A the URLs are identical and **they would be**: a cached Production stock list could appear inside Training, or the reverse. |
| WhatsApp/SMTP turn on when `WHATSAPP_TOKEN`/`SMTP_HOST` are set, and `config.py` **dotenv-loads `deploy/.env`** on bare-metal runs unless `GI_DOTENV=0`. | `services/whatsapp.py:96`, `services/emailer.py:64`, `config.py:14-34` | A Training process started carelessly on the Mac would pick up the **live Meta token** and text whatever number a trainee types. The synthetic phone `+966500000000` is a plausible real Saudi number. |
| `POST /admin/backup` names its file `gihub-<ts>.dump` **whatever database it dumped**, into the shared `GI_BACKUPS_DIR`. | `console.py:185-210` | A Training dump would sit beside the Production dumps **under a Production-looking name**, so someone could restore it over Production. |
| Uploaded documents are stored **in the database** (`LargeBinary`). Report PDFs go to `GI_REPORTS_ARCHIVE_DIR` on disk. Tutorials are read from `GI_TUTORIAL_DIR`. | `documents.py`, `report_center.py:55`, `training.py:367` | Documents follow the database for free. The report archive must be separate per instance. Tutorials can be shared read-only. |
| `make_tutorial_db.py` builds a **legacy-shaped SQLite** file, and `generate_tutorial.py` loads it into Postgres through `cutover_migrate.py --source … --target … --wipe`. | `tools/make_tutorial_db.py`, `tools/generate_tutorial.py:79-91` | This is the seed path, already proven. P12-4 forbids a second stack builder, and the sandbox won't need one. |
| The fixture has **5** accounts (admin, hod, supervisor, worker, Logistics), but `ROLE_META` has **9** roles. `pending_*` queues are **not seeded**, so the HOD approval queue is empty (a known gap noted in the fixture's own docstring). | `make_tutorial_db.py:84-87, 159-165` | "100 % of roles" needs 4 more accounts and seeded queues. Doing that **inside** `make_tutorial_db.py` would change the tutorial dataset and invalidate every rendered video's `DATASET_VERSION` (P12-5). So it goes in a separate overlay (§5). |
| `/training` already means the **Training Hub** (videos + `training_compliance`, P10-6). | `frontend/src/config/nav.tsx:411`, `backend/api/training.py` | ⚠️ **A naming collision, and a real correctness issue.** A trainee who watches a tutorial inside the sandbox writes a compliance row into the **sandbox** database, and it does not count in Production. See Q2. |

---

## 2. Threat model: every way data could cross, in both directions

"Zero contamination" has two directions, and the second is the one that costs
money:

* **Production → Training**: real names, stock or phone numbers visible to
  trainees. That breaks P12-0's reasoning, since the sandbox is where people are
  *told* the data is fake.
* **Training → Production**: a practice receipt, issue or approval lands in the
  real ledger. It is a real stock movement that nobody made.

| # | Vector | Direction | Wall that stops it | Layer |
|---|---|---|---|---|
| V1 | A code path in the API opens the wrong database. | both | The Training **process** has no URL for `gihub`, and its **Postgres role has no CONNECT on it**. | process + DB |
| V2 | The Training process is misconfigured with the Production URL. | T→P | **Boot refusal**: `GI_INSTANCE=training` + a database name that is not `*_training` → exit non-zero. The reverse check also exists. (Same shape as `testdb.py` refusing source == target.) | process |
| V3 | A Training token is replayed against the Production API. | T→P | **Separate `JWT_SECRET`**, plus an `env` claim checked in `_decode`. | auth |
| V4 | The offline queue replays a Training mutation into Production. | T→P | Queue **namespaced per environment** (`gi-offline` / `gi-offline@training`), entries **stamped** with their environment, and the server **refuses** `X-GI-Instance` ≠ itself (409). | client + API |
| V5 | The PWA cache serves one environment's reads to the other. | both | Distinct URL prefix (`/training-api/`). The existing regex cannot match it, and a spec pins that. | client |
| V6 | Refresh cookies collide on the same origin. | (UX) | Cookie name per instance (`gi_refresh` / `gi_refresh_training`). | auth |
| V7 | Training sends a real WhatsApp message or email. | T→world | Training **refuses to boot** if any `WHATSAPP_*`/`SMTP_*` value is present. It also runs `GI_DOTENV=0` and `GI_OUTBOUND=off`, checked inside `whatsapp.enabled()` / `emailer.enabled()`. Outbox rows still appear in the Console, so the feature is visible and nothing leaves. | process + service |
| V8 | A Training backup is mistaken for a Production one. | T→P | The dump filename carries the **database name**. Training writes to its own `GI_BACKUPS_DIR`, and the nightly `backup` service never dumps Training. | ops |
| V9 | Real data is copied into Training to "make it realistic". | P→T | The seed is **synthetic only** (P12-0). No path reads `gihub` or `gi_database.db`, and `--collision-check` proves the names are disjoint. | seed |
| V10 | The user believes they are in Training but they are in Production. | T→P (human) | The banner and theme are driven by what the **server says it is** (`GET /instance`), **never by the toggle's state**. Training is visually loud: a permanent amber bar, and "PRACTICE" in the tab title. | UI |
| V11 | The reset button drops the wrong database. | T→P | The reset router is **mounted only when `GI_INSTANCE=training`**. The target name comes from config, never from the request, and must end `_training`. The DB role **cannot** drop `gihub` because it does not own it. | API + DB |
| V12 | Both instances share Ollama and a Training OCR job starves Production. | (availability) | Q5. Default: Training runs `GI_AI_CONCURRENCY=1` and cloud fallback **off**. | ops |

⚠️ **V4 is why a "server-side only" design is not enough.** Every server wall
above is correct and still would not stop V4, because the replay arrives
**authenticated as a real Production user**. The fix has to live in the client
**and** be enforced by a server check that does not trust the client to have
done it.

---

## 3. Routing strategy: A vs B vs C

### Option A: `X-Environment` header selects the DB session per request

**Rejected as the router.** The header is client-controlled on every request,
so isolation depends on every caller sending it correctly for ever. Its failure
mode is the worst one available: a missing header has to default to *something*,
and whichever default you pick, some path reaches the other database. It also
does nothing for the 30 session sites that have no request (§1), leaves the
in-memory Bloom filters and throttles mixed, and puts both environments behind
**identical URLs**, which breaks the PWA cache (V5). In rule 15's own words,
this is the per-endpoint shape that "fails open".

*Kept only as a tripwire:* `X-GI-Instance` is **asserted**, never used to
**select**. Each process knows what it is from its own config, and a request
declaring the other environment gets a 409. That catches V4 and any
misconfigured client, and it can never *route* anything anywhere.

### Option B: environment encoded in the JWT

**Rejected as the router, adopted as a claim.** It is stronger than A because
the value is signed, but it still switches databases *inside one process*, so
it inherits every problem A has except forgery. On top of that, the requests
that matter most carry no token at all: `/auth/login` has to pick a database
**before** a token exists, and so do `/auth/register` and every background loop.

*Kept as defense-in-depth:* every token (access, refresh, mfa, enroll) carries
`env`, and `_decode` rejects a mismatch. With separate secrets that check is
redundant by design. It is there so that an operator who copies one
`JWT_SECRET` into both env files still gets a refusal, not a silent pass.
Existing Production tokens without the claim are read as `production`, so
deploying this logs nobody out.

### Option C: two backend instances ✅ RECOMMENDED

The same image and code, started twice with different environments:

```
                    ┌──────────── nginx (web) ─────────────┐
 browser / native → │  /                → SPA (one build)  │
                    │  /api/            → api:8000         │──► gihub           (role: gihub_app)
                    │  /training-api/   → api-training:8000│──► gihub_training  (role: gi_training)
                    └──────────────────────────────────────┘        ▲
                                                                     │ CREATE DATABASE … TEMPLATE
                                                              gihub_training_tpl
```

| Env var | `api` (Production) | `api-training` |
|---|---|---|
| `GI_ENV` | `production` | `production` (the strong-secret check still applies) |
| `GI_INSTANCE` *(new)* | `production` (default when unset) | `training` |
| `DATABASE_URL` | `…gihub_app@db/gihub` | `…gi_training@db/gihub_training` |
| `JWT_SECRET` | secret A | **secret B** (boot refuses if equal to A's hash, when both are visible) |
| `GI_REFRESH_COOKIE` *(new)* | `gi_refresh` | `gi_refresh_training` |
| `WHATSAPP_*`, `SMTP_*` | set | **absent** (boot refuses if present) |
| `GI_DOTENV` | as today | `0` |
| `GI_OUTBOUND` *(new)* | `on` | `off` |
| `GI_BACKUPS_DIR` | `/backups` | `/backups-training` (or unset) |
| `GI_REPORTS_ARCHIVE_DIR` | as today | `reports_archive_training` |
| `GI_AI_CONCURRENCY` / vision fallback | as today | `1` / off (Q5) |
| workers | 4 | **1**: trainees are few, and a single worker keeps the Postgres connection budget (4×15 + 1×15 < 100) and RAM in check |

**Why this is the rule-15-compliant answer.** Rule 15 did not isolate tests
by teaching code about two databases. It isolated them by making sure the
process **never holds the other URL**, and by refusing loudly when names
collide. Option C does exactly that for a second long-running process, adds a
database-level wall rule 15 does not have (CONNECT privilege), and needs **no
change to the 390 `Depends(get_session)` endpoints or the 30 direct session
sites**. Their code is unchanged, and that means the feature parity you asked
for is structural, not maintained by hand: the same binary, so Training has
every feature Production has, including the ones added next year.

---

## 4. Architecture in detail

### 4.1 Database layer: the wall that does not depend on our code

On the same Postgres cluster (`:5433` locally, the `db` container on Hetzner):

```sql
-- once, as superuser
CREATE ROLE gi_training LOGIN PASSWORD '…' CREATEDB;   -- CREATEDB: for the template reset (§6)
REVOKE CONNECT ON DATABASE gihub FROM PUBLIC;          -- gihub: only its own role(s)
GRANT  CONNECT ON DATABASE gihub TO gihub_app, gi_ai_ro, postgres;
CREATE DATABASE gihub_training_tpl OWNER gi_training;
CREATE DATABASE gihub_training     OWNER gi_training TEMPLATE gihub_training_tpl;
```

⚠️ **The local mirror uses `trust` auth**, which lets any client *claim* any
role. The CONNECT privilege is still enforced **after** authentication, so
`gi_training` is refused on `gihub` even under trust. Suite TR-02 proves this
rather than asserting it. Note for the operator: `REVOKE CONNECT … FROM PUBLIC`
on the live mirror has to be re-applied after every `dual_ci` reload, the same
ritual `create_ai_readonly_role.sql` already needs, so both belong in one
script.

⚠️ **Production currently connects as `postgres` (superuser) locally**, and
that role can open anything. The wall protects Production *from Training*,
which is the direction asked for. Hardening Production's own role is a separate
item and is out of scope here (Q7).

### 4.2 Backend changes (small, all additive)

1. `config.py`: `instance()` returns `production` or `training` from
   `GI_INSTANCE`, and **`assert_instance_safe()`** runs at import, **before**
   `db.py` builds the engine:
   * `training` and the DB name does not end `_training` → **exit non-zero**
   * `production` and the DB name ends `_training` → **exit non-zero**
   * `training` and any outbound credential is present → **exit non-zero**
2. `auth.py`: add an `env` claim in `_make_token`, check it in `_decode`
   (missing is read as `production`), and take the cookie name from config.
3. A new ~20-line ASGI middleware next to `readonly.py`: if `X-GI-Instance` is
   present and ≠ `instance()`, return **409** "this request was prepared for the
   other environment". It is method-agnostic and runs before routing, so it
   cannot be forgotten on a new endpoint (same design as rule 7).
4. `GET /instance` (unauthenticated) returns `{instance, dataset_version,
   seeded_at, reset_at}`. It is the **only** source the SPA uses to decide
   whether to show the banner (V10).
5. `whatsapp.enabled()` / `emailer.enabled()` also return False when
   `GI_OUTBOUND=off`. Belt and braces for V7: boot already refuses, and this
   covers someone editing the env at runtime.
6. `console.run_backup`: put the database name in the dump filename (V8).
7. `training_sandbox.py` *(new)*: `POST /sandbox/reset`, **mounted only when
   `instance() == 'training'`** (§6).
8. Training Hub, **inside Training only**: a notice that compliance is recorded
   in Production, and the compliance write turned into a no-op (Q2).

No change to `db.py`, `get_session`, any router's queries, SME engines, or the
parity golden. **Rule 1c is not touched.**

### 4.3 Frontend changes

1. **Login toggle.** An antd `Segmented` control (already imported in
   `LoginPage.tsx`), *Production | Training*, above the username field. It
   calls a new `setEnvironment('production' | 'training')`, which derives the
   base from the current one (`…/api` → `…/training-api`). That works for web,
   Vite dev, **and** the native builds' absolute `VITE_API_URL`, and it
   composes with the existing `ServerConfigModal` override instead of fighting
   it.
2. **Storage namespaced per environment**: `gi_token` / `gi_token@training`,
   plus the idle-logout and `sessionState` keys. A stale token from one
   environment is then never sent to the other.
3. **Every request sends `X-GI-Instance`** (one axios interceptor, plus the SSE
   `fetch` path in `api/sse.ts`).
4. **Offline queue (V4)**: IndexedDB name per environment, each entry stamped
   `{env}`, and `flushQueue()` skips (and surfaces) entries whose environment is
   not current. The stamped header rides the replay, so the server's 409 is the
   second wall.
5. **Banner + theme**, driven by `GET /instance`: a permanent amber bar
   *"TRAINING — practice data, nothing here is real"*, amber header accent,
   "PRACTICE ·" prefix in `document.title`, and a watermark on printed PDFs. It
   cannot be dismissed.
6. **Switching environments signs you out.** This is the existing
   `setApiBase` contract, kept deliberately. One environment per browser at a
   time (Q3).
7. The PWA `navigateFallbackDenylist` gains `/^\/training-api\//`.

### 4.4 Infrastructure

* **Hetzner (`deploy/docker-compose.prod.yml`)**: a new `api-training` service
  (same `build:`, the env from §3, **no `env_file: .env`** so no Meta or SMTP
  secrets can reach it, 1 worker), and a `location /training-api/` block in
  `deploy/nginx.conf` mirroring `/api/`. The nightly `backup` service is left
  alone: the sandbox is disposable and rebuildable, so backing it up would only
  create V8.
* **Local Mac (`bin/dev.sh`)**: a second uvicorn on **:8001**, plus a Vite
  proxy rule `/training-api` → `:8001` with prefix rewrite. It is started by
  `dev.sh` like everything else, never by hand (machine rule). `bin/power.sh
  sleep` stops it with the rest.
* **cloudflared**: nothing to change. The tunnel already routes the whole
  hostname to nginx/Vite, which does the path split.

---

## 5. Seed data: reuse Phase 12, add a Training overlay

```
make_tutorial_db.py --today <reset date>  →  tutorial-shaped SQLite (synthetic, P12-0)
          │
          ▼
cutover_migrate.py --source <that file> --target gihub_training_tpl --wipe
          │                                    (the SAME path tutorials + E2E use — P12-4)
          ▼
tools/training_overlay.py  →  adds what a SANDBOX needs and a VIDEO does not
          │
          ▼
create_ai_readonly_role.sql on the template  →  NL→SQL works in Training too
```

**Why `make_tutorial_db.py` itself is not edited.** P12-5 pins its output. Its
`DATASET_VERSION` is written into every tutorial manifest, and adding trainee
accounts or seeded queues would change the dataset every video was recorded
against. The overlay is a separate, idempotent step that writes **only
Postgres-side** rows the legacy schema cannot hold anyway (Phase 13 tables, QC,
assets, execution entries).

**A side benefit worth having on purpose:** the sandbox has *the same*
materials, SAP codes and people as the tutorials. A trainee who watches "Return
Stock" and then opens Training finds *Tutorial Demo Gasket Set / 899001*
exactly where the video showed it.

**The overlay adds:**

| Gap | Overlay |
|---|---|
| 4 of 9 roles have no account | `qc`, `qc_hod`, `warehouse_user`, `auditor` practice accounts, bound to `CNCEC` / `WH-01` exactly as their scope rules require (the auth.py `_SCOPED_/_UNSCOPED_/_DUAL_SCOPE_` sets) |
| Empty approval queues | `pending_issues/receipts/returns` rows in `pending_hod`, a draft PR, a DN awaiting HOD, an open QC inspection, a Phase-13 attribution awaiting approval. Each role opens its queue and finds real work to practise on. |
| Dates age out (the fixture is anchored 2026-09-06) | The sandbox build passes **`--today <build date>`**. The tutorial render keeps its pinned `ANCHOR`, so P12-5 is untouched. Only the sandbox moves with the clock, and it is rebuilt on every reset. |
| `app_settings` | `require_entry_documents` kept ON (parity). MFA policy per Q4. `maintenance_mode` off. |
| Phones | Every synthetic phone becomes `+000…` (not a routable E.164 number) **in addition** to outbound being off, so there are two walls on V7 |

**Collision proof:** the build runs `make_tutorial_db.py --collision-check`
when `gi_database.db` is present (read-only, as today) and **fails the build**
on any hit.

---

## 6. Data lifecycle

| Event | What happens | Who / how |
|---|---|---|
| **First build** | Role + template + sandbox created (§4.1), seeded (§5). | `tools/training_db.py build`, run by the operator once |
| **Deploy / schema change** | The **template is rebuilt from scratch** (`cutover_migrate` builds at head via `create_all`, so no Alembic run on Training), then a reset. Training never runs migrations-behind the way the local mirror does. | `deploy-v2.sh` gets one line: `training_db.py build --reset` |
| **Admin reset** | `DROP DATABASE gihub_training WITH (FORCE); CREATE DATABASE gihub_training TEMPLATE gihub_training_tpl;` (**~1 s**, PG 13+). The pool's `pre_ping` reconnects transparently, and Bloom filters are refreshed right after. | 🔄 **Reset sandbox** button in the Training Admin Console → `POST /sandbox/reset` |
| **Scheduled reset** | Optional nightly or weekly, as a `daily_job_runs`-claimed job (four-workers rule; it is one worker, but the claim costs nothing). | Q6 |
| **Re-anchor dates** | Only on template rebuild, not on every reset, so a reset always returns the **same** starting state until the next deploy. | automatic |

**The reset button: yes, and here is how it is contained (V11).**

* It exists **only** in the Training instance. The router is not *included* in
  Production's app, so it is not merely a hidden button. It is a 404, and suite
  TR-08 proves that.
* `require_roles('admin')` **in Training**, confirmed with a typed phrase
  ("RESET PRACTICE DATA") because it wipes every trainee's work in progress.
* The target name comes from `DATABASE_URL`, **never from the request body**,
  and must end `_training`. The template name is derived from it.
* It connects to the maintenance DB `postgres` as `gi_training`, which **owns
  only** the two training databases. Even a bug that computed `gihub` would be
  refused by Postgres (`must be owner of database`).
* The reset is audited **into the fresh database** after the clone, so the
  first audit row a trainee sees says who reset it and when.

---

## 7. Feature parity: what is identical, and what is deliberately not

**Identical, because it is the same binary:** every page, every endpoint, every
role and nav rule (rule 14), the auditor's read-only middleware (rule 7), SME
engines (rule 1c), FEFO allow-and-log, QC block, MTC gate, 2FA machinery, rate
limits, the AI assistant (rule 9's fence is per-role, not per-environment), OCR,
exports, and the notification **bell**. The outbox Console still shows every
WhatsApp and email that *would* have gone out.

**Deliberately different, and each difference is stated in the banner's
tooltip and the manual:**

| Behaviour | Training | Why |
|---|---|---|
| WhatsApp / email delivery | queued, never sent | V7 |
| Meta inbound webhook | not routed | Meta calls one URL, Production's |
| Nightly backup | none | disposable; V8 |
| Training Hub compliance | not recorded (notice shown) | P10-6: a certificate earned in the sandbox would be evidence nobody can produce in Production (Q2) |
| Cloud vision fallback | off | P11-9's two switches stay off; see Q5 for local OCR |
| Excel ledger sync (`pg_excel_sync.py`) | never pointed at Training | it is a CLI with its own `DATABASE_URL`; `training_db.py` does not wire it and the runbook says so |

---

## 8. Gates and tests (rules 15, 16, 14, 13)

A new `service_tests` suite, **TR** (or the next free letter). It runs against
`gihub_svctest` and a throwaway `gihub_svctest_training`, **never** against
`gihub` or a real `gihub_training` (rule 15):

| Case | Asserts |
|---|---|
| TR-01 | `GI_INSTANCE=training` + DB `gihub` → process exits non-zero (subprocess; negative control: `…_training` boots) |
| TR-02 | role `gi_training` **cannot CONNECT** to the Production-named database, even under trust auth |
| TR-03 | a Training-minted token is 401 on a Production app and vice versa; separately, with the **same** secret the `env` claim still refuses it |
| TR-04 | a claim-less legacy token is accepted by Production (no mass logout) and refused by Training |
| TR-05 | `X-GI-Instance: training` → 409 on Production, on GET **and** POST, before any handler runs |
| TR-06 | Training with `WHATSAPP_TOKEN` set → exit non-zero; with `GI_OUTBOUND=off`, `enabled()` is False |
| TR-07 | refresh cookie name differs per instance |
| TR-08 | `/sandbox/reset` is **404** on Production; on Training it refuses a non-`_training` target and succeeds on a throwaway one |
| TR-09 | the dump filename contains the database name |
| TR-10 | overlay idempotence: run twice, same row counts; every role in `ROLE_META` has an account (so a 10th role added later fails this, rule 13's pattern) |

**E2E** (`tests/e2e`, its own stack as today): toggle → banner visible → login
as the Training HOD → approve a seeded issue → assert the row exists in the
Training DB **and not** in the E2E Production DB. One more spec **queues an
offline mutation in Training, switches to Production and asserts it is NOT
replayed** (V4, the one a server-only test cannot see).

**Nav (rule 14)**: no new route. The Reset button lives on the existing Admin
Console, and `test:nav` stays at 50.

**Preflight (rule 16)**: the suite spawns subprocesses for TR-01/06. If
Postgres cannot create the throwaway database, those cases **SKIP with a
reason**, and they are never counted as a pass.

**Docs (rule 13), in the same PR:** `USER_MANUAL.md` gets a new *Practice Mode*
section. Because the manual is the AI corpus, `gen_eval_grid.py` is regenerated
and **not** hand-edited (P11-12). `MANUAL_TESTING_GUIDE.md` gets the V1–V12
walk-through, `docs/ARCHITECTURE.md` a topology section, `PROJECT_HANDOVER.md`
a proposed rule (below), and `REPO_MAP.md` the new tool files.

**Proposed new locked rule (17), for you to accept or not:**
*"Training is a second process, not a second session. Nothing in the API selects
a database per request; an instance knows what it is from its own environment
and refuses to boot when its name and its database disagree. A request header
may assert an environment and be refused, never choose one."*

---

## 9. Implementation slices (after approval)

| Slice | Content | Visible result |
|---|---|---|
| **S1: walls** | `config.instance()` + boot refusals, `env` claim, cookie name, 409 middleware, outbound kill, dump filename. Suite TR-01…07, 09. | Nothing user-facing; Production is **byte-identical** in behaviour |
| **S2: data** | `training_db.py build/reset/verify`, `training_overlay.py`, DB roles script, reset endpoint (Training-only). TR-08, 10. | A Training API on :8001 serving synthetic data |
| **S3: client** | toggle, namespaced storage, header interceptor, offline-queue stamping, banner/theme via `/instance`, reset button. E2E specs. | The feature, end to end, locally |
| **S4: deploy + docs** | `dev.sh`, Vite proxy, compose service, nginx block, `deploy-v2.sh` line, manual + testing guide + architecture + eval grid regen. | Deployable |

Each slice ends with **every** gate in `.claude/RULES.md` §Gates green. S1 alone
is safe to merge, because it only adds refusals.

---

## 10. Clarifying questions

**Q1: What is it called?** "Training" collides with the existing **Training
Hub** (`/training`, videos, compliance). A trainee told to "go to Training" has
two places to go. *Recommend:* toggle labels **Live | Practice**, and call it
"Practice Mode" everywhere, keeping "Training" for the video hub. If you prefer
"Training", I will rename nothing but will disambiguate in the manual.

**Q2: Should watching a tutorial *inside* the sandbox count toward
compliance?** *Recommend: no.* Show a notice ("Certificates are recorded in
Live") and skip the write. The alternative, writing across into Production, is
exactly the cross-database path this whole design exists to forbid.

**Q3: One environment per browser at a time, OK?** Switching signs you out.
This is simpler and it is the existing `setApiBase` contract. The alternative
(Production in one tab, Practice in another at the same moment) is feasible with
`sessionStorage` scoping, but it doubles the V10 risk: the tab you are looking
at is not necessarily the one you think.

**Q4: How do trainees sign in to Practice?**
* **(a)** Shared per-role practice accounts with published passwords (e.g.
  `practice.hod`), printed on a training card. *Recommended for classroom use.*
* **(b)** A Practice admin creates named trainee accounts inside Practice.
* **(c)** Copy real usernames and password hashes from Live so people use
  their normal credentials. *Recommend against:* it copies Production data
  (identities and hashes) into the sandbox, which is contamination in the P→T
  direction, and it keeps working after someone's Live account is disabled.

And should **mandatory 2FA** (admin/logistics/hod/qc_hod/auditor) be enforced
in Practice? With (a), shared accounts cannot share an authenticator, so I
suggest it is **off** in Practice (an `app_settings` value in the overlay) and
the manual says so.

**Q5: The AI in Practice.** Ollama is one box, one warm model, and 90–400 s per
OCR page. A class of ten trainees each photographing a form would queue behind
(and ahead of) Production's real forms. Options: **(a)** full AI, concurrency
1, sharing the queue; **(b)** the assistant and NL→SQL on, **OCR off** in
Practice with an explanatory notice; **(c)** all AI off. *Recommend (b).*

**Q6: Reset policy.** Admin button only, or also an automatic reset (nightly
at 02:00, or weekly on Friday)? And who may press it? *Recommend:* the Practice
admin only, plus an optional weekly automatic reset you can switch on in
`app_settings`.

**Q7: Scope of hardening Production's own DB role.** Locally the API connects
as `postgres` (superuser). The wall in §4.1 protects Live *from Practice*
regardless, but a least-privilege `gihub_app` role for Live is a natural
follow-on. In this work, or a separate ticket?

**Q8: One shared sandbox, or one per trainee?** This plan is **one shared
sandbox**: trainees see each other's practice entries, and a reset wipes
everyone's. Per-trainee sandboxes (a template clone per user) are possible
with the same mechanism but add lifecycle and connection-pool questions. Worth
it now?

**Q9: Native apps (Tauri/Capacitor).** Should the installed apps show the
toggle too? It works unchanged (the base is derived from `VITE_API_URL`), but a
warehouse tablet that can be flipped to Practice is one more V10 risk. Some
sites may want Live-only binaries, which would be one build flag.

**Q10: The legacy Streamlit app** gets no Practice mode (it is frozen). Please
confirm.

---

## 11. Rejected alternatives, recorded so they are not re-proposed

| Idea | Why not |
|---|---|
| Header-selected DB (Option A) | client-controlled selector; fails open; 30 request-less sites; identical URLs poison the PWA cache (§3) |
| JWT-selected DB (Option B) as the router | login, register and background loops have no token; still per-request switching inside one process (§3) |
| One DB, a `is_training` column on every table | every query in 390 endpoints and both SME engines would need the predicate; one miss mixes ledgers, and rule 1c's parity golden would need re-deriving |
| One DB, a Postgres `training` **schema** + `search_path` per request | same per-request switch as A/B, just at the connection level; pooled connections keep `search_path` across checkouts unless every checkout resets it |
| A separate Postgres **cluster** for Training | the strongest wall, but a second daemon with its own backups, upgrades and RAM on a 16 GB box. The CONNECT-privilege wall gives the isolation that matters for a fraction of that. It is still available if you want it (say so in Q7). |
| Seeding Training from a scrubbed copy of Live | P12-0: redaction only masks what somebody thought to name. Synthetic, or not at all. |

---

## 12. Operator rulings and what shipped (2026-09-24)

| Q | Ruling | Where it lives |
|---|---|---|
| Q1 | Toggle reads **Live \| Practice** | `LoginPage.tsx`, `environment.ts` `ENV_LABEL` |
| Q2 | No compliance writes in Practice; notice shown | `training._upsert` no-op, acknowledge 409, `PracticeNotice` |
| Q3 | One environment per browser; switching signs out | `switchEnvironment()` logs out + reloads |
| Q4 | Shared `practice.<role>` accounts; 2FA off | `tools/practice_overlay.py`, `auth.mfa_gate`, 2FA enroll 403 |
| Q5 | Assistant + NL→SQL on, OCR off with a notice | `practice.assert_ocr_available` at every vision entry + `vision_json` |
| Q6 | Reset: Practice admin button only, no schedule | `POST /practice/reset`, Admin Console → Practice |
| Q7 | Hardening Live's DB role: out of scope | — (Live still connects as a superuser locally) |
| Q8 | One shared sandbox | — |
| Q9 | Toggle on native apps too | `practiceBase()` derives from `VITE_API_URL` |
| Q10 | No Practice mode in legacy Streamlit | — |

**Differences from the proposal as written:**

* **Seed database name** is `gihub_seed_training`, not `gihub_training_tpl`:
  the overlay runs AS a Practice process against the seed, so the seed must
  pass rule 17's own boot check (name ends `_training`). Live refuses any name
  *containing* `_training`, which covers it.
* **The JWT "hash equality" boot check** became a deploy-time check
  (`deploy-v2.sh` aborts if `PRACTICE_JWT_SECRET == JWT_SECRET`), plus
  distinct dev fallback keys per instance. A process cannot see the other's
  secret, so a boot-time comparison had nothing to compare.
* **Phones** in the overlay became `+000000000000` for employees and NULL for
  practice accounts.
* **The five tutorial harness logins are removed** from Practice: `admin /
  admin2026` is published in the repo.
* **`practice.admin` has its own password** (generated and printed once unless
  `PRACTICE_ADMIN_PASSWORD` is set), because it is the one account that can
  wipe everyone's work.
* **Server-side operations text** went into admin-only `USER_MANUAL.md` §17.9;
  the every-role chapter §26 is user-facing.
* **Outbox rows are not produced** in Practice (dispatch sees no configured
  channel), so the bell is the visible trace, not the WhatsApp Console.
* **Not verified locally:** `docker compose config` and `nginx -t` (Docker is
  not installed on the development Mac). The compose file parses as YAML and
  the nginx block mirrors `/api/` with request-time resolution.
