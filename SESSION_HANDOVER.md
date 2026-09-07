# SESSION HANDOVER — read this first, then `PROJECT_HANDOVER.md`

> ## 🆕 PHASE 12 SHIPPED (2026-09-06 → 09-07) — read this block first
>
> Five slices, all merged to `main` (PRs #73–#76). **Nothing is mid-flight.**
> Every gate is green. The plan, with the generation-strategy argument and the
> operator's locked answers, is
> [`PROPOSED_PHASE12_PLAN.md`](PROPOSED_PHASE12_PLAN.md).
>
> **Automated role-based video tutorials.** A tutorial is a Playwright
> screencast of the real UI plus a narrating avatar, composited locally into an
> MP4 + WebVTT + manifest, for the training hub slice 10b already built.
>
> | Slice | Branch | What it did |
> |---|---|---|
> | **prototype** | `chore/phase12-prototype` | The pipeline end to end, minus one HTTP call |
> | **12a** | `feat/phase12-tutorial-dataset` | `tools/make_tutorial_db.py` — the SYNTHETIC dataset, its own DB `gihub_tutorial_pw` on :8011/:5184 |
> | **12b** | `feat/phase12-recorder` | The manifest, WebVTT, freeze-padding, the batch runner, the rule-14 route lint |
> | **12c** | `feat/phase12-scripts` | A **declarative** recorder (`steps:` in YAML) + the first four tutorials |
> | **12f** | `feat/phase12-deeplinks` | The assistant answers **and** deep-links to the second the step is on screen. Suite **CX** |
>
> ### ⚠️ The five things that will bite you first
>
> **1. P12-0 — A TUTORIAL IS RECORDED AGAINST SYNTHETIC DATA, OR IT IS NOT
> PUBLISHED.** `tests/e2e/global-setup.ts` loads its throwaway database from
> the REAL `gi_database.db`. Correct for a test suite whose rows never leave
> the machine; wrong for a video, which is a file people forward. The
> prototype's first frame carried real employee names, real material
> descriptions and real SAP codes. **The redaction hook is a second line, not
> the boundary** — it only masks what somebody thought to name.
>
> **2. The renders are GITIGNORED and the feature is allowed to be absent.**
> `docs/tutorials/out/` is local until the Hetzner cutover (ruling Q3). On a
> box with no manifests the assistant's deep links simply never appear, and
> that is a supported state: `GET /ai/health` reports
> `tutorials: {present, tutorials, beats, indexed}`. Suite CX-13 pins it.
>
> **3. Pass A runs BEFORE the browser, and it is not an implementation detail.**
> The narration is rendered and MEASURED first, then each UI step is held for
> the length of its own line. Built the other way round the first run overran
> all six beats — 40.2 s of speech over a 19.6 s recording. With a real HeyGen
> key the ordering is unchanged.
>
> **4. Adding a tutorial is a YAML file, not a spec file.** `tutorial.spec.ts`
> is the one recorder; the steps live in `tools/tutorials/*.yaml` next to the
> narration they illustrate. `--dry-run` lints a script (routes, manual fence,
> redaction) without opening a browser.
>
> **5. Two floors guard the deep-link matcher, and the second is the real one.**
> A candidate must clear `MIN_SCORE = 4.0` **and** share **two** distinct
> tokens with the beat it won on. `manual_index._tokens` expands synonyms, so
> one accidental word on a boosted heading scored 4.95 — raising the floor
> instead would have killed a real hit scoring 4.59.
>
> ### What to read for what
>
> | Topic | Read |
> |---|---|
> | The seven `P12-x` rulings | `PROJECT_HANDOVER.md` → *Phase 12 rulings, LOCKED* |
> | The pipeline and the deep links | `docs/ARCHITECTURE.md` §7j · §7k |
> | Why pre-render beat on-demand generation | `PROPOSED_PHASE12_PLAN.md` §2 |
> | What a USER sees | `USER_MANUAL.md` §24.2.3 |
> | How to test it by hand | `MANUAL_TESTING_GUIDE.md` §14ab (TC-DL-01…06) |
> | The recorder's own contract | `tests/video_gen/README.md` |
>
> ### Gates, as of 2026-09-07
>
> ```
> bash bin/ci_preflight.sh                                  ✅ 10 controls
> GI_DOTENV=0 .venv/bin/python -m backend.api.service_tests  2328 / 0  (A…CX)
> .venv/bin/python -m tests.ai_eval.runner                   Tier 1 147/147 · recall 1.000 · precision 0.994
> .venv/bin/python tools/gen_eval_grid.py --check            72 verified cases
> DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib \
>   .venv/bin/python legacy/bug_check.py                     599 / 0 / 0
> cd tests/e2e && npm test                                   128 / 128
> npm run test:nav --prefix frontend                         51 routes
> npm run parity:sme --prefix frontend                       1313 comparisons
> npm run test:ui-math --prefix frontend                     33 / 0
> alembic single head                                        a1c9e64b3d70  (Phase 12 added NO migration)
> ```
>
> ### Still open after Phase 12
>
> * **HeyGen submit/poll/download** — needs a key. Narration egress is signed
>   off (2026-09-06); `heygen_live()` is marked UNVERIFIED and the poll half is
>   deliberately unwritten.
> * **Object storage for the renders** — coupled to the Hetzner cutover.
> * **The rest of the ~60-clip catalogue** (ruling Q2). Four are done.
> * Everything listed under Phase 11 below is **still open and unchanged**.
>
> *(Everything below this block predates Phase 12 and is still accurate about
> what it describes.)*
>
> ---

> ## 🆕 PHASE 11 SHIPPED (2026-09-02 → 09-05) — read this block, then §1 below
>
> Six slices, all merged to `main`. **Nothing is mid-flight.** Every gate is
> green. The phase plan, with the tool-by-tool rejection reasoning, is
> [`PROPOSED_PHASE11_PLAN.md`](PROPOSED_PHASE11_PLAN.md).
>
> | Slice | Branch | What it did |
> |---|---|---|
> | **11a** | `fix/ci-and-guardrails` | Fixed CI (red for 30 runs), added `bin/ci_preflight.sh` + `tools/harness_hygiene.py`, wrote `CLAUDE.md` + `.claude/RULES.md` |
> | **11b** | `feat/phase11-ocr-upgrades` | OCR elapsed timer + interrupted state, the `<DITTO>` fix, the `(Night)` shift, the cloud seam |
> | **11c** | `feat/phase11-ai-tracing` | `ai_traces` + retrieval telemetry + the AI Traces page |
> | **11d** | `feat/phase11-guardrails` | `ai/guard.py` — input/output boundaries, suite CT |
> | **11e** | `feat/phase11-gateway` | `ai/route.py` lane policies + `ai_answer_cache` |
> | **11f** | `feat/phase11-evals` | 147-case dataset, deterministic gates at 0.85, Tier 2 ratchet |
>
> ### ⚠️ The four things that will bite you first
>
> **1. A SKIP IS NOT A PASS (new rule 16).** `bug_check.py` now reports
> `N passed, N failed, N skipped`. On macOS the QR round-trip SKIPS unless you
> `export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` — with it you get
> **599/0/0**, without it **598/0/1**. Both are green; the skip is honest.
> It had been counted as a pass since the check was written, for an assertion
> that had never executed on any machine.
>
> **2. `bash bin/ci_preflight.sh` is the FIRST gate now**, and it audits the
> TEST HARNESSES, not the app. If it fails, do not work around it — it is
> finding the class of defect that made CI lie for two months.
>
> **3. The assistant now CACHES answers per role.** If a test asks a question
> an earlier suite already asked, it gets the cached answer and the model is
> never called. Two suites hit this during Phase 11. **Use a unique question
> string in any new assistant test.**
>
> **4. `run_tier1()` returns a TUPLE** (`results, telemetry`) since 11f, and
> `build_system_prompt_scored()` / `retrieve_context_scored()` are the scored
> variants of the old functions. The old names still work and delegate.
>
> ### What to read for what
>
> | Topic | Read |
> |---|---|
> | The twelve `P11-x` rulings + rule 16 | `PROJECT_HANDOVER.md` → *Phase 11 rulings, LOCKED* |
> | Tracing, guards, gateway, eval gates | `docs/ARCHITECTURE.md` §7e · §7f · §7g · §7h |
> | Why Portkey/LangSmith/Guardrails-AI/RAGAS were all rejected | `PROPOSED_PHASE11_PLAN.md` §3–§6, and each module's header |
> | What a USER sees | `USER_MANUAL.md` §25 |
> | How to test it by hand | `MANUAL_TESTING_GUIDE.md` §14x · §14y · §14z · §14aa |
> | The eval suite's own contract | `tests/ai_eval/README.md` |
>
> ### Gates, as of 2026-09-05
>
> ```
> bash bin/ci_preflight.sh                                  ✅ 10 controls
> GI_DOTENV=0 .venv/bin/python -m backend.api.service_tests  2309 / 0   ← Phase 11; now 2328 (A…CX)
> .venv/bin/python -m tests.ai_eval.runner                   Tier 1 147/147 · recall 1.000 · precision 0.994
> DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib \
>   .venv/bin/python legacy/bug_check.py                     599 / 0 / 0
> cd tests/e2e && npm test                                   128 / 128
> npm run test:nav --prefix frontend                         51 routes
> npm run parity:sme --prefix frontend                       1313 comparisons
> alembic single head                                        a1c9e64b3d70
> ```
>
> ### Still open after Phase 11
>
> * **The Hetzner deployment** — unchanged, still paused by decision, but the
>   tooling is now VERIFIED rather than assumed. ✅ The cutover's "stamps
>   Alembic without running it" gap is **closed** and was re-proved end-to-end
>   on 2026-09-05 against a throwaway database: 105 tables copied, stamped to
>   `a1c9e64b3d70`, **13 data steps executed**, and both Phase 11 tables present
>   with all six of their indexes on a `create_all`-built box. Run
>   `tools/migration/cutover_migrate.py --wipe` against a scratch database
>   before the real cut and read the `[3] Post-load` block — it names every data
>   step it ran.
> * **Tier 2's score is 64% against a 95% target** — now written up as a
>   **KNOWN MODEL CONSTRAINT** (`docs/PROJECT_STATUS.md` §1a,
>   `docs/ARCHITECTURE.md` §7i), not a backlog item. Tier 1 is 147/147 with zero
>   leaks, recall/precision are 1.000/0.994 and false refusal is 0%, so the gap
>   is neither the fence nor retrieval: it is an 8B model inventing UI no
>   chapter describes. Prompt work already bought +21 points and is past its
>   point of diminishing return. **The trigger is a GPU upgrade** — re-run
>   `bash bin/ai_eval_tier2.sh --record` on the new host and compare.
>   ⚠️ Do not lower the target, add prompt text, or wire Tier 2 into CI.
> * **The semantic cache is deliberately unbuilt.** `answer_cache.stats()`
>   publishes the hit rate that would justify it, and
>   `tests/ai_eval/cases/nearmiss.yaml` is the acceptance test it must pass.
> * **`fixtures/ocr/` is gitignored** and holds the operator's three real
>   documents; `fixtures/ocr_ground_truth.yaml` IS tracked. ✅ All 30 handwritten
>   rows are now transcribed and `verified: true`, so per-cell OCR accuracy is
>   scoreable — and suite AM feeds the RAW column through `resolve_ditto` and
>   asserts it reproduces the human's resolution on all 30 rows, which is the
>   11b ditto bug expressed as an assertion against a real document. **Six cells
>   are genuinely ambiguous** (three names, one tank number) and are commented
>   inline; an operator who knows the crew should confirm them — correct them
>   in the YAML, never by relaxing a scorer.
>
> *(Everything below this block predates Phase 11 and is still accurate about
> what it describes.)*
>
> ---

> **Updated 2026-09-03** by the Phase 10 documentation sweep
> (branch `chore/phase10-docs`). ⚠️ **Phases 9 and 10 have both shipped since
> the 2026-08-13 body of this file was written** — everything below the next
> block is still accurate about the things it describes, but it does not know
> about the paper-first OCR workflow, mandatory 2FA, the training hub or the
> board brief. For those, read `docs/PROJECT_STATUS.md` §0b/§0c first.
>
> **Where Phase 9 and 10 are written up:**
>
> | Topic | Read |
> |---|---|
> | What shipped, per phase, with the bugs each one surfaced | `docs/PROJECT_STATUS.md` §0b, §0c |
> | The ten Phase 10 rulings that must not be "improved" | `PROJECT_HANDOVER.md` → *Phase 10 rulings, LOCKED* |
> | The vision envelope, the orphan heartbeat, Bloom filters, `rate_buckets`, `dailyjob` | `docs/ARCHITECTURE.md` §7a–§7d |
> | What a USER sees | `USER_MANUAL.md` §21.12–§21.13 (Phase 9) and §24 (Phase 10) |
> | The AI guardrail audit | `tests/ai_eval/README.md` |
>
> **Three production bugs were found by building on the code rather than by
> looking for them, and all three shared a cause — `uvicorn --workers 4`:**
> the OCR orphan sweep reaped other workers' live jobs; three daily loops
> dispatched four copies of every message; and four rate limiters were 4×
> looser than their own documentation. If you add anything that runs on a
> timer or holds state in memory, assume four of it.
>
> *(Original note, 2026-08-13 — the workflow-polish and test-isolation pass,
> branch `feat/workflow-polish-and-test-isolation`.)*
> The project is **feature-complete, stable and security-audited**.
> Every live gate is green. **Nothing is mid-flight — there is no half-finished
> work to pick up.**
>
> 🔄 **The one thing that changes your habits: the backend suite no longer
> writes to your database.** It builds and runs against its own
> `gihub_svctest`, and **refuses to start** if that resolves to the same
> database as `DATABASE_URL`. If you go looking for a test's rows in `gihub`
> afterwards, they are not there and that is the fix, not a bug. See
> `PROJECT_HANDOVER.md` rule 15.
>
> ⚠️ **Two production-cutover gaps were found by that change** — one fixed
> (a partial unique index that lived only in Alembic, so a cutover-built
> production box would not have had it), one **still open** (cutover stamps
> Alembic to head without running it, so data backfills are skipped on a fresh
> cut). Both are written up under *FUTURE* in `PROJECT_HANDOVER.md`. Read them
> before the Hetzner deployment.
>
> **Shipped version is `1.2.0`**, and three files must always agree on it:
> `frontend/src-tauri/tauri.conf.json`, `frontend/package.json`,
> `frontend/src-tauri/Cargo.toml`. They had drifted to `0.1.0 / 0.0.0 / 0.1.0`,
> which is why the v1.2.0 release published installers named `0.1.0`.
> `release-desktop.yml` now **fails the build** on drift or on a tag mismatch.
>
> **Read [`MANUAL_TESTING_GUIDE.md`](MANUAL_TESTING_GUIDE.md) before you change a
> feature.** Since 2026-08-09 keeping it current is rule 13 — part of the
> Definition of Done, not a nicety. It is also the fastest way to learn what the
> system actually promises, because it states the WHY for every behaviour.
>
> **Your first decision next session is a CHOICE, not a queue** — Tier 1 security
> hardening or the Hetzner deployment. Both are spelled out in §8 below.

---

## 1. What this project is

A multi-site warehouse inventory ERP + procurement chain for General Industries,
running as **two stacks against one PostgreSQL database**:

| | Stack | Where |
|---|---|---|
| **Live / current** | React 19 + Ant Design v6 SPA → FastAPI (async SQLAlchemy) → PostgreSQL 16 on `:5433` | `frontend/`, `backend/` |
| **Frozen** | The original Streamlit app. Still runs, still gated by its own 599-check regression suite, but **no new features** | `legacy/` |

Bridge tools live in `tools/`, archived data in `data-archive/`. `REPO_MAP.md` is
the segregation contract between them.

**Deployment is PAUSED by decision, not by blocker.** Everything needed for the
Hetzner rollout is built and documented (`tools/migration/README.md`,
`docs/DEPLOY.md`); it simply has not been executed yet.

---

## 2. ⚠️ The rules you can break without noticing

Full text in `PROJECT_HANDOVER.md` → *PAST — critical architecture rules, LOCKED*.
Each of these was a real bug whose symptom showed up far from its cause.

### SME Subset Rule (rule 1c)
`Ordered_Qty` is the **TOTAL procured for the project**; `Available_Qty` is the
part of it that has **physically arrived**. Available is a *subset* of ordered.

```
tier 1  = available_qty
tier 2  = max(ordered_qty − available_qty, 0)     ← the PENDING DELIVERY
ceiling = tier1 + tier2 = max(available, ordered)
to buy  = max(demand − max(available, ordered), 0)
```

Treating them additively double-counts everything already on the shelf. It
understated the buy list by **22,951 units across 22 of 30 materials**, and on
`GI-8005763` — where all 143,000 ordered units had arrived — it read 286,000 and
reported **nothing to buy** against a demand of 152,685.

### SME Tier Segregation (rule 1b)
**The UI never merges physical and pipeline stock.** Tier 1 alone drives every
"can we build it today" answer: `Status`, `Completion_Pct`, `SQM_Achievable_Now`,
`Fulfillment_Pct`. Tier 2 feeds only the `*_With_Ordered_*` twins and the net buy
list.

`Allocated_Qty` (= tier 1 + tier 2) is a **conservation field** so that
`Demand = Allocated + Shortfall`. It is **not** a coverage numerator and nothing
may colour it green. The engine always had this right; six presentation layers
did not, and overstated buildable area by **9,118 m² — 21.5 % of the programme**.

### RBAC — the Auditor role (rule 7)
View-only is enforced **once, by ASGI middleware keyed on the HTTP method**
(`backend/api/readonly.py`) — never per-endpoint.

Every `POST` / `PUT` / `PATCH` / `DELETE` from an `auditor` is refused with 403
unless it appears on a small documented allowlist (**126 of 143** mutating routes
blocked). This shape is the point: a per-endpoint check is only as good as the
developer who remembers it, and the one that gets forgotten **fails open**. Keying
on the method means a route added next year is closed from the moment it is
written.

> **If you add an endpoint and it 403s for an auditor, that is correct.** Only add
> to the allowlist in `readonly.py` if the route genuinely changes nothing.

### Two more that bite just as hard
* **Component identity (rule 1)** — the key is `(Material_Code, SAP_Code)`.
  Never pool by `Material_Code` alone.
* **SME ⇄ ERP decoupling (rule 1a)** — every SME number comes from
  `sme_inventory_seed`. A warehouse receipt must not move an SME figure.

### The QSEP two-gate rule (2026-08-09, **certificate gate MOVED 2026-08-12**)
**Both gates now bind at ISSUE.** The certificate used to bind at DISPATCH; the
operator moved it on 2026-08-12 after it blocked live work (see
`PROJECT_HANDOVER.md` R3b). Nothing about RECEIVING or SHIPPING controlled
material is gated any more.

| Gate | Where it fires | Refuses whom | Fixed by |
|---|---|---|---|
| Material Test Certificate | Store Keeper's issue (`stage_consumption` + `approve_smr`) | the store keeper | **Logistics** |
| QC approval | the same two places, and again at HOD approval | the store keeper | **the QC** |

Two gates, one moment, **two different people to chase** — a refusal message
that does not say which gate sends the SK to the wrong person.

**Receiving is never refused.** A receipt states that something physically
arrived; refusing to record it does not un-arrive it, it just hides real stock
from the shelf report and from planning. The receipt block was traded for a
notification to Logistics (`quality.warn_mtc_missing`) — if you ever remove
that, the ruling has deleted a control rather than moved it.

**Certificates are inherited, not re-uploaded** (`quality.visible_mtc`): PO line
→ DN → warehouse → site, ranked most-specific-first, and scoped to the site the
goods actually reached. If a store keeper is ever uploading a second copy of a
PDF Logistics already has, that is the bug.

**Material MAY travel to site uninspected** — the original ruling (R3). What it
may not do is reach a worker. And the QC block **does not overturn FEFO**:
`assert_qc_cleared` is about QUALITY STATUS on 36 SAPs, while FEFO and
over-issue stay allow-and-log on everything. Never implement one by promoting
the other's warning to an error.

### And the standing one
**Both SME engines change together.** `backend/api/sme_engine.py` and
`frontend/src/sme/engine.ts` are line-for-line mirrors proven equal by
`npm run parity:sme`. Any numeric change = change BOTH + regenerate the golden,
**in one commit**.

### And the newest one — rule 13
**`MANUAL_TESTING_GUIDE.md` is part of the Definition of Done.** Change a
feature, update the guide, same PR. And a role added to `auth.ROLE_META` must be
added in the same commit to `ai/manual_qa._ROLE_ALLOWED` and to
`build_manual_pdf.ROLE_MANUAL_RECIPES` — QSEP added `qc` and did neither, so a
Quality inspector was answered from the Store Keeper chapter and had no printed
booklet. The role map **falls back to `store_keeper`** for an unknown role,
which fails safe and is precisely why nobody noticed.

---

## 3. What was added most recently

> **2026-08-13 — test isolation, and four workflow refinements.** Branch
> `feat/workflow-polish-and-test-isolation`.
>
> * **The suite stopped writing to the live database (rule 15).** Suite A rolls
>   back; suites B…BX drive the real ASGI app and **cannot** — a request that
>   returns 201 has committed by the time the assertion reads it. Cleanup was
>   never the answer: an interrupted run skips its own `finally`, and those are
>   the runs developers do most. `backend/api/testdb.py` rewrites
>   `DATABASE_URL` **before `db.py` is imported** (that ordering is the whole
>   mechanism) and exits non-zero if source and target are the same name.
>   Rebuild costs ~1 s.
> * ⚠️ **It immediately found two production-cutover bugs**, because the test
>   database is now built the way production's will be. `ux_asset_transfer_open`
>   existed only in Alembic, so `create_all` never made it — **a production box
>   would have shipped without the guard that stops two sites claiming the same
>   asset**, and that race is silent. Fixed in `models.py`. The second is still
>   open: cutover STAMPS Alembic without running it, so **data** backfills are
>   skipped on a fresh cut.
> * **The suite was depending on one laptop's data.** The 2026-08-13 wipe turned
>   1474/0 into an `IndexError` on the FIRST suite, because employee `30001` had
>   been in the live table since June and nothing recreated it.
> * **PO assignment**: `po_list` never returned the assignment, so the grid
>   offered `Assign` forever and a second click wrote a second row, a second
>   notification, and two warehouses each expecting the goods. The grid now
>   names the warehouse; the API refuses a re-route and treats a repeat of the
>   same warehouse as idempotent. **An audit of every other action button found
>   no second instance** — the rest are already gated on row status.
> * **Shipping carries paperwork.** `ship_dn` took no arguments; a truck left
>   and the site got goods whose physical delivery note existed only in the cab.
>   The number on that document is the carrier's, unrelated to our `DN_Number`
>   — tying the two together is the point. Visible in all five portals through
>   one shared column, because five hand-maintained column lists is how three
>   show it and the fourth quietly does not.
> * **QC could not see what it was judging.** The queue showed `1032` and
>   "certificate #41" — a code instead of a material, and proof a document
>   exists with no way to open it. Both fixed; the certificate download hangs
>   off the *inspection* so scoping is inherited rather than re-derived.
> * **A rejection now mints a Return No** the SK pastes into Return Stock to
>   fill the form. Capped at the rejected quantity, DN + document mandatory
>   **regardless of `require_entry_documents`**, single-use, and site-scoped
>   (the number is a date plus a small integer, so an unscoped lookup would
>   enumerate every rejection in the company). ⚠️ It does **not** overturn the
>   "rejected stock is not auto-returned" ruling — it is an invitation for a
>   human, not an automatic vendor return.
> * **The return dropdown was measuring the wrong date.** The 30-day window ran
>   on the delivery date typed off the vendor's paperwork, so goods received
>   that morning against a six-week-old document were invisible while older
>   ones were listed. `receipts.posted_at` fixes it; **no backfill**, so
>   historical rows stay NULL and fall back to `Date`.
> * **A ninth Morning Briefing probe** for uncertified Surface Shields, routed
>   by location — warehouse → Logistics/Warehouse/warehouse QC, site →
>   SK/HOD/site QC/Logistics. Logistics is on both lists because they are the
>   only role who can actually get the document.
>
> **2026-08-12 (c) — the generated CRUD reads.** Branch
> `fix/crud-rbac-alignment`, closing the finding the previous pass recorded
> and left open.
>
> * `crud.make_read_router` stamped a bare `get_current_user` on every GET it
>   generated, for eleven entities. **`read_roles` is now a required-in-practice
>   parameter** and each entity states its own; suite BV fails if a row omits it.
>   Exactly one entity is open — `inventory`, the catalogue every picker reads.
> * ⚠️ **Worker identity had FOUR doors**, not one. Narrowing `/hr/employees`
>   last pass left `/employees` (the same table), the badge PDFs and
>   `/documents/master/employees` (the roster as a spreadsheet) wide open. All
>   four now agree. A privacy control with one door open is not a control.
> * **`/master/employees` is admin-only** — the single place this pass departs
>   from the frontend matrix rather than mirroring it. Logistics keeps vendors
>   and warehouses; leaving them a create/update/delete editor over the table
>   whose READ was just revoked would have made the revocation theatre.
> * **Bulk identity is narrower than single-name identity, on purpose.** An SK
>   reads a name to type an employee ID on every PPE issue; printing or
>   exporting the whole roster stays with `{hod, auditor, admin}`. Suite AL has
>   asserted since Phase 5 that an SK cannot print a badge — that judgement was
>   left standing rather than overturned in passing.
> * **The `sme-tiers` E2E flake is fixed at the root**, and it was never the
>   SME grid. `waitFor(WEB_URL)` proved the dev server *answered*, not that it
>   had *transformed* anything, so on a cold `node_modules/.vite` four workers
>   raced Vite's on-demand transform and the whole suite ran 5× slower — 30s
>   warm, 2.8m cold — which only the heaviest page's `beforeAll` noticed.
>   `global-setup` now warms the entry graph once. Reproduced deterministically
>   before, gone across four cold runs after.
>   ⚠️ Also: `test.setTimeout()` at describe scope does **not** raise a hook's
>   budget. The previous attempt to raise it read as though it had worked.
>
> **2026-08-12 (b) — the strict-RBAC pass.** Branch
> `fix/strict-rbac-navigation`, executing the approved
> [`PROPOSED_NAV_FIX.md`](PROPOSED_NAV_FIX.md) in five steps.
>
> * **`canAccessPath` now fails CLOSED.** It used to `return true` for any path
>   it did not recognise, and it ignored group-level access entirely — so the
>   sidebar and the route guard enforced two different policies. Nothing
>   exploited either; the failure mode was simply "let them in".
> * **`npm run test:nav`** (a TypeScript-AST parse of `App.tsx` vs the
>   manifest) fails the build when a route has no access rule. Failing closed
>   turns a silent LEAK into a silent LOCKOUT — this is the part that makes it
>   loud. Wired into CI.
> * **`minLevel` is gone from the nine contested pages.** It is a ladder and
>   the roles are not one: `minLevel: 1` admitted six of eight roles, which is
>   how seven roles held the staff roster and how the store keeper — the person
>   who physically holds the stock — was the one role locked out of the Stock
>   page. Pages now name the jobs that need them.
> * **API gates narrowed to match**: `/hr/employees` (was `get_current_user`,
>   i.e. anybody), `/ppe/forecast` (was level 0), `/sme/*` (was level 2, which
>   is why Logistics could call every Estimator endpoint while seeing no SME
>   page). Suite **BU** pins all of it, and is deliberately almost all NEGATIVE
>   checks — the positives were already true and would survive a revert.
> * **`tests/e2e/specs/rbac-matrix.spec.ts`** asserts all 8 roles × 44 pages
>   through the SHIPPED access functions (`window.__giNav`), both for the menu
>   and for the route guard. A matrix that re-implemented the rules would agree
>   with itself forever while the guard did something else.
> * ⚠️ **Two E2E assertions were INVERTED on purpose** (SK reaching `/stock`;
>   the MTC receipt block). Both are commented in place. If either flips back,
>   read the comment before "fixing" it.
>
> **2026-08-12 — the MTC gate moved, and an RBAC plan written.** One branch,
> `fix/mtc-issuance-gate-and-nav-audit`. This is the first change to a locked
> QSEP ruling since the programme shipped, and it was made on the operator's
> instruction after the old rule blocked live work.
>
> * **The certificate gate moved from DISPATCH to ISSUE** (ruling R3b in
>   `PROJECT_HANDOVER.md`). `warehouse.receive()` and `create_dn()` now RECORD a
>   certificate and demand nothing; `stage_consumption` and `approve_smr` refuse
>   without one. Removing the DN block was not optional — leaving it would have
>   reproduced the same stall one hop later, with the warehouse able to receive
>   material and unable to send it anywhere.
> * **The block was traded for a notification, not deleted.**
>   `quality.warn_mtc_missing` tells Logistics whenever controlled material is
>   booked in uncertified, because they are the only people who can produce the
>   document and the material is unissuable until they do.
> * **Certificates are inherited down the chain** (`quality.visible_mtc`) —
>   PO line → DN → warehouse → site, ranked, and scoped to the site the goods
>   actually reached. The person who hits the gate is not the person holding the
>   document, and without this the SK would be re-uploading Logistics' PDF.
> * **`QcClearanceBanner`** on the issue form shows BOTH halves of the gate at
>   once. Showing one at a time sends the store keeper to fix the wrong thing,
>   and they come back and get refused again by the other.
> * **`PROPOSED_NAV_FIX.md` — a plan only, no navigation code touched.** The
>   headline is not in the brief: `canAccessPath()` **fails open** — an
>   unrecognised path is allowed, and group-level access is ignored entirely, so
>   the sidebar and the route guard enforce different policies. Nothing exploits
>   it today; the next page added without a manifest entry is open to everyone.
>
> **2026-08-10 — the agentic & optimisation pass.** One branch,
> `chore/overnight-agentic-optimizations`. Nothing here changed a business
> rule; the two behaviour changes are both bug fixes and both are pinned.
>
> * **A Morning Briefing agent** (`backend/api/health_monitor.py`) — eight
>   probes at 07:00 local, dispatched per scope (admin all-sites, each HOD
>   their own). **Every problem it finds is the ABSENCE of an event**, which is
>   why no existing trigger could catch any of them. Three design rules, all
>   load-bearing: a probe that raises degrades the digest instead of cancelling
>   it; a clean run dispatches NOTHING but still writes an audit row; the body
>   is one line because Meta rejects a newline in a template parameter.
>   ⚠️ On live data its first run found **28 materials at negative stock** and
>   **28 controlled materials with stock nobody can issue** — real, pre-existing,
>   and worth an operator's attention.
> * **A fail-open scope leak, fixed.** `entry.item_snapshot` gated its stock
>   query on `site_filter_applies()` and the consumption sparkline six lines
>   below on `if site_id:` — same scope value, two rules, one endpoint. A store
>   keeper with no site of their own saw a correct zero for stock and **every
>   site's consumption** in the trend beside it. Found by an AST sweep for that
>   exact shape; it was the ONLY remaining instance in the codebase.
> * **A PPE forecast correctness bug, fixed.** The all-sites forecast passed
>   `site_id or ""` for on-hand, so it summed expiring gear from everywhere
>   against stock from almost nowhere and suggested re-ordering PPE already on
>   the shelf. Site-scoped forecasts were always right, which is why it had
>   never been reported.
> * **Query-count reductions, all proven answer-identical by suite BT:**
>   `/ppe/eligible` N+1 → 1 (`rules_for_many`), the forecast 2N+1 → 3,
>   `_cleared_totals` 5 → 3 (three same-predicate scans became one conditional
>   aggregate, on the hot issue path), `/hod/pending` 4 → 1.
> * **E2E 57 → 75**, translating the guide's highest-value cases: the QC gates,
>   PPE distribution, and `TC-SEC-05` fail-closed scoping. The throwaway DB now
>   seeds three QC accounts covering both scoping axes **and the unbound case**.
> * ⚠️ **`asyncpg` cannot infer the type of a parameter used only in `IS NULL`
>   or `= ANY()`.** Both new bulk queries needed `CAST(:p AS text)` /
>   `CAST(:p AS text[])`. The failure is `AmbiguousParameterError` and it kills
>   the process without a traceback — re-run with `-X faulthandler -u` to see
>   it. It also does not reproduce on an empty table, so verify with rows.
>
> **2026-08-09 — QSEP (Quality · Safety · Employees · Procurement),** three
> merged PRs (#36 `9f8be2e`, #37 `d481a37`, #38 `6447ddb`), then a documentation
> and stabilisation pass. Plan: [`PROPOSED_PHASE_6_PLAN.md`](PROPOSED_PHASE_6_PLAN.md).
> User-facing account: `USER_MANUAL.md` chapter 22. Test coverage:
> [`MANUAL_TESTING_GUIDE.md`](MANUAL_TESTING_GUIDE.md).
>
> * **A `qc` role at level 1 with DUAL scoping** — a site **or** a warehouse,
>   never both. `auth.qc_scope()` fails closed: neither binding, or both, yields
>   `{"site": "", "warehouse": ""}` and the inspector sees **nothing**, not
>   everything. ⚠️ `warehouse_scope` needed the matching fix: a site-bound QC
>   gets `''` (matches nothing), not `None` (matches everything).
> * **The hard issuance block** — `services/quality.assert_qc_cleared` at
>   **both** `stage_consumption` and `approve_smr`, enforcing
>   `Σ approved − Σ already issued or staged`. Clearance pools at
>   `(Site_ID, SAP_Code)`, not per-lot: the lot is unknown at stage time and
>   FEFO resolves it at commit. ⚠️ **It counts only from the earliest
>   inspection's date onward** — counting all 1,133 historical consumption rows
>   would block controlled material forever.
> * **PPE rides the ORDINARY issue form (Option A).** No PPE page, no PPE stock
>   ledger. Quantity still leaves via `pending_issues` → `consumption`, which is
>   why stock, FEFO, burn rate, reports and the QC gate need no PPE-shaped
>   exception. Suite BO checks that negative property first.
> * **The PPE distribution is written at STAGE, not at approval** — the boots are
>   on the worker's feet when the SK hands them over. So the duplicate guard is
>   true during the approval gap, and a rejection **voids the new row AND
>   restores its predecessor**; without the restore the worker holds nothing on
>   record while visibly wearing the old gear.
> * **`ppe_rules` is empty in production today.** Consequence, and the most
>   likely false bug report: every PPE issue demands a safety document, nothing
>   gets an expiry, and **the 15-day forecast is permanently empty** until rules
>   are configured.
> * **OCR routes on whether text came out, never on MIME type.** The reference
>   `PO#4710003121_PR681.pdf` is a real signed-and-scanned PO with **0 text
>   characters and 5 full-page images**; the old endpoint answered **HTTP 200
>   with an empty item list**, which is worse than an error because nothing
>   distinguished "no items" from "I could not read it". Uploads are stored
>   **before** parsing — the document that defeats the parser is the one somebody
>   will need to look at.
> * **One hammer, one row.** Asset identity narrowed to `(SAP_Code, Serial_No)`
>   **globally**, with transfers approved by the **source** site's HOD.
> * **Password policy 12 → 8 with complexity, in ONE place.** It had five copies,
>   and self-registration sat on a literal 6 while everything else used `MIN_PW`.
>
> **2026-08-09 (later) — the documentation and PDF pass.** See §4a: the manual
> table renderer had been printing rows on top of each other, and two
> documentation surfaces had never been told the `qc` role exists.
>
> **2026-08-06 — the security audit.** A full-codebase review (`SECURITY_REVIEW_2026-08-05.md`),
> then two fix branches: PR #32 `fix/formula-injection-and-csp` and PR #33
> `fix/sme-export-and-nginx-headers`. Forward roadmap in
> [`SECURITY_SUGGESTIONS.md`](SECURITY_SUGGESTIONS.md).
>
> * **The audit result is the headline: no High-severity findings.** No SQL
>   injection, no authentication bypass, no XSS sink, no unsafe deserialization.
>   Auth, RBAC, secrets handling and the AI NL→SQL lane all held up under
>   deliberate attack review. Several defenses are better than typical — refresh
>   families that revoke on replay, view-only enforced by middleware rather than
>   per-endpoint, scoping that fails closed on `''`.
> * **One Medium finding, now fixed and pinned: spreadsheet formula injection.**
>   `consumption."Remarks"` is typed by a store keeper (level 0) and exported to
>   an HOD's Excel. `=HYPERLINK(...)` in that field exfiltrated the row on one
>   click. See **rule 12** in `PROJECT_HANDOVER.md` — it is the one with a trap.
> * ⚠️ **THE TRAP, worth reading before you touch an export.** The guard must
>   never apostrophe-prefix a NUMBER, including a numeric string. In the SME
>   workbooks `_cell()` runs BEFORE `_num()` sums those rows into GRAND TOTAL,
>   and `_num()` parses with `float()`. Defusing `"-5"` makes every negative
>   subtotal silently become `0.0` in a total that still looks plausible. So a
>   string that IS a number is left alone; `-1+1` is not a number and IS defused.
> * **Three export writers, three different libraries, all must be hooked** —
>   `csv.writer`, openpyxl and xlsxwriter. The SME path was missed on the first
>   pass precisely because xlsxwriter does its own type dispatch and could never
>   inherit the openpyxl guard.
> * **A Content-Security-Policy now ships** in `deploy/nginx.conf`, tuned against
>   the real build. `style-src` keeps `'unsafe-inline'` because Ant Design v5 is
>   CSS-in-JS and the UI blanks without it. `X-Frame-Options` stays `SAMEORIGIN`,
>   not `DENY` — the Document Library previews PDFs in a same-origin iframe.
> * ⚠️ **nginx `add_header` REPLACES, it does not merge.** `location /assets/`
>   declares `Cache-Control` and so had been silently dropping every security
>   header from the bundles. The three are now repeated verbatim inside it.
>   **Add a header to the server block and you must add it there too.**
> * **Gate moved: service tests 1228 → 1245** (suite BK, 17 checks).
>
> **2026-08-05 (late) — locations from the workbook, and the documentation pass.**
> `feat/excel-location-sync-and-ui` (PR #30) and
> `chore/version-bump-and-docs-polish`. Full account in
> [`EXCEL_LOCATION_SYNC_RUNLOG.md`](EXCEL_LOCATION_SYNC_RUNLOG.md).
>
> * **THE GOLDEN RULE — a `Location` on a Consumption Log row is what MAKES it
>   a reusable asset.** Blank means consumable. Nothing else is consulted, and
>   that matters: 1,165 of 1,166 real rows are blank, so any looser test would
>   manufacture a thousand phantom assets on the first run.
> * **THE WORKBOOK SEEDS, THE APP OWNS.** `storage_locations` upserts
>   `DO NOTHING`; a SAP with any existing rack assignment is skipped; an
>   existing asset keeps its status, rack and GPS fix. One narrow exception,
>   guarded on `last_seen_by = 'excel-sync'` **and** no coordinates: a unit the
>   app has never touched does take a corrected Location text.
> * ⚠️ **Both new columns are effectively EMPTY today** — `Rack/Current
>   Location` is blank in 453 of 453 rows, `Location` is filled on 1 of 1,166
>   (and that row has no serial, so it cannot be keyed and is reported back
>   every run). A sync seeds nothing. That is correct, not a failure.
> * **`asset_units.status` gained the operator's vocabulary** — `working`,
>   `not_in_use`, `repair` — beside the custody values, in ONE field. The
>   workbook has no Status column, so the app is where condition is recorded.
> * **Material NAMES now sit beside every bare code** in six tables. The SME
>   name comes from `sme_recipe`, **never** `sme_inventory_seed` — rule 1a makes
>   the seed the sole source of every SME quantity, and a label lookup is how a
>   quantity read sneaks in later. Suite BJ pins that.
> * **Manuals rewritten for non-technical readers.** Every ASCII-art diagram,
>   shell command and developer identifier is gone; **zero fenced code blocks
>   remain**. `build_manual_pdf.py` gained three real fixes — see §4a.
>
> **2026-08-05 (earlier) — the overnight asset/SME programme.** Six phases on
> `feat/overnight-asset-and-sme-upgrades`; the full account is
> [`OVERNIGHT_ASSET_TRACKING_RUNLOG.md`](OVERNIGHT_ASSET_TRACKING_RUNLOG.md).
> The headlines a fresh session needs:
>
> * **Surface-Shield consumption now lands in `sme_consumption_log`** and is
>   shown as a SIDE NOTE beside the estimator. **Rule 1a is intact by ruling** —
>   `sme_inventory_seed` is never written, and suite BF proves the SME payload
>   is byte-identical across a routing write. Do not "fix" the estimator to
>   subtract it.
> * **`Tank No.` is ambiguous, not just dirty.** `TNK-091` matches both TRAIN J
>   and TRAIN K. `sme_tank_alias` holds unresolved aliases for a human; nothing
>   is ever guessed.
> * **THE APP WINS on `Surface_Area_SQM`** — `SQM_Override` survives
>   `--sme-reseed` and the sync reports the divergence.
> * **New surfaces:** `/locator` (rack locator, minLevel 0), `/assets`
>   (serialised units + GPS), SME → 🧾 Actual Consumption.
> * **`For_1_SQM` is hidden from the UI and every export**, but is still in
>   `/sme/snapshot` — the TS engine computes demand in the browser.
> * **No engine change was made**, which is why parity is 1,313 unchanged.

| Feature | Where | The short version |
|---|---|---|
| **BM25 chatbot retrieval** | `backend/api/ai/manual_index.py` | The assistant used to be handed its whole allowed manual per question (~180 KB for an Admin). It now retrieves ~6 relevant passages: **admin prompt 178,146 → 4,075 chars (97.7 %)**, 0.37 ms, no vector store and no new dependency. The role filter runs **before** scoring — that is the security boundary. |
| **Fence-aware manual parsing** | same | Shell comments inside ```` ```bash ```` blocks (`# 1. Pull the new code`) were parsing as chapters 1-4 and **overwriting Introduction, Roles, Login and the Store Keeper manual for every role**. Never parse chapters with a bare `^# \d+\.`. |
| **Idle sign-out** | `frontend/src/auth/useIdleLogout.ts` | 30 minutes, 2-minute warning, cross-tab via localStorage. The client timer is the trigger; the **revocation** is the substance — it calls the normal logout, which kills the refresh family server-side. |
| **Per-account login throttle** | `backend/api/ratelimit.py` | The existing limit was per-IP and blind to credential stuffing across hosts. 8 failures per username per 15 min. **Throttles, never locks** — a per-account limit is a DoS vector. |
| **Global ⌘K search** | `frontend/src/components/CommandPalette.tsx` | Pages *and* live stock: type a SAP code, material code or description and jump to the material card. Reuses `/stock/by-site`, so site scoping stays server-side. |
| **DB indexes** | alembic `e7c3b95a41d2` | 7 hot-path indexes, **benchmarked** on a clone inflated to 260k/240k/429k rows: 20×, 92×, 6×, 6×. Four candidates were **rejected on evidence** (two cost 9.5 MB each for zero planner uses). |
| **Branded exports** | `pdf_tables.py`, `xlsx_style.py` | Overflow-proof PDFs (columns wrap, nothing truncated) and the premium logo layout on **every** xlsx. ⚠️ **The xlsx header row moved to row 6, data to row 7.** |

---

## 4. The gates — LOCKED baselines

A change that lowers any of these is a regression, not a new normal.

| Gate | Baseline | Command |
|---|---|---|
| Backend service tests | **2,328 / 0** (suites A…CX, **own throwaway DB**) | `GI_DOTENV=0 .venv/bin/python -m backend.api.service_tests` |
| Playwright E2E | **128 / 128** | `cd tests/e2e && npm test` |
| **AI evals — Tier 1** | **147 / 147, 0 leaks** · recall **1.000** / precision **0.994** (floor 0.85). Also inside suite CQ | `.venv/bin/python -m tests.ai_eval.runner` |
| AI eval grid freshness | **current, 72 verified cases** (P11-12: generated, never hand-edited) | `.venv/bin/python tools/gen_eval_grid.py --check` |
| **Harness hygiene — runs FIRST** | **10 controls, 0 failed** (rule 16) | `bash bin/ci_preflight.sh` |
| SME UI math | **33 / 0** | `npm run test:ui-math --prefix frontend` |
| SME TS↔PY parity | **1,313 comparisons** | `npm run parity:sme --prefix frontend` |
| Legacy regression | **599 / 0 / 0** ⚠️ needs `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` on macOS, else the QR check SKIPS — and a SKIP is not a PASS | `.venv/bin/python legacy/bug_check.py` |
| Navigation route coverage | **51 routes, all claimed** | `npm run test:nav --prefix frontend` |
| Frontend | `tsc -b` + build + `oxlint` clean | `npm run build --prefix frontend` |
| Alembic | single head **`a1c9e64b3d70`** | `cd backend && alembic heads` |
| 🎬 Tutorial scripts | **NOT A GATE, by design** (Phase 12) — a video that renders badly is one to re-render, not a red build. `--dry-run` lints every script without a browser | `.venv/bin/python tools/generate_tutorial.py --all --dry-run` |
| **Manual PDFs** | **0 overlapping text pairs** × 8 booklets | `.venv/bin/python build_manual_pdf.py --role all` |
| `gi_database.db` | sha256 `00652932…ba038` **unchanged** | `shasum -a 256 gi_database.db` |

> ⚠️ **`tools/parity_check.py` can no longer pass and is NOT a gate.** It
> compares the frozen legacy SQLite against Postgres, and they have diverged
> permanently: `consumption` holds **1** row in SQLite against **1,133** in
> Postgres, `inventory` 306 against 466. Every sync since cutover has written
> Postgres only. Its failure carries no information about any code change —
> **do not spend a session "fixing" it.** Retiring or re-baselining it is an
> operator decision that has not been made.

### 4a. `build_manual_pdf.py` — the table overlap, fixed 2026-08-09

**The symptom:** in a table, a cell holding several lines of wrapped text
printed the next row *on top of itself*. The master manual carried **104
overlapping word pairs**, which is how the interleaved nonsense —
`rSutnonrein`, `cRounbfbiremrs` — got onto the page.

**The cause is worth internalising, because the project already knew it.**
`_wrap_cell` measured each trial line with `get_string_width` against the FULL
cell width. But a cell's usable text width is that width **minus `c_margin` on
each side** — 2 mm at fpdf2's default. Lines that measured as fitting re-wrapped
when they were actually drawn, so a row measured five lines tall rendered six,
and the sixth landed in the row below. **Rule 7 in `PROJECT_HANDOVER.md` has
documented this exact trap for `pdf_tables.py` since 2026-08-03**; the manual
builder simply never got the memo.

**The fix, and why it cannot drift again:** measurement is now performed by the
engine that draws — `multi_cell(dry_run=True, output="LINES")`. The measurement
*is* the split, so the two cannot disagree. Every line is then drawn at explicit
coordinates with auto page-break switched off for the table, because relying on
`multi_cell` to advance the Y cursor was the other half of the problem. Three
further cases now survive: a header cell wraps instead of being truncated at 60
characters, a **row taller than a whole page** splits across pages with the
header repeated, and a code line wider than its box wraps instead of running
through the border.

**A geometry audit now runs on the builder's own output** and prints a per-file
line, so this cannot silently return: the build reopens each PDF and counts
pairs of words whose boxes intersect. `--no-verify` skips it.

Also new here: the QSEP `qc` role had **no booklet recipe at all**, and chapter
22 has been added to every role's recipe.

### 4b. `build_manual_pdf.py` — three defects fixed 2026-08-05

Worth knowing because each one silently degraded every PDF:

1. **`_ascii()` turned unmappable characters into `?`, silently.** Emoji in tab
   names ("📥 Incoming PRs") meant a page describing eight tabs carried eight
   question marks. Now: maths symbols are spelled out, decorative symbols are
   dropped **by Unicode category** rather than by a hand-written list that every
   new emoji defeated, and anything still unrepresentable is **named in the build
   output**. That warning found 56 characters on its first run.
2. **Wrapped list items were split into a bullet plus an orphan paragraph.** The
   parser read only a bullet's first line and let its indented continuation fall
   through to the paragraph branch.
3. **Table cells were capped at four lines and hard-truncated with `...`.** Cells
   are now measured with real font metrics (`get_string_width`) and the row grows.

Also: `_strip_md_punct` removed `**` but not single `*`, so `*italic*` printed
literal asterisks while `**bold**` printed clean. Both are stripped now — body
text renders in one font by design, because mixing fonts mid-paragraph made
fpdf2 overflow the right margin.

---

## 5. Daily commands

```bash
./bin/dev.sh localhost      # Postgres + API + Vite → http://localhost:5173
./bin/dev.sh stop           # kill API + Vite + our connector
./bin/power.sh wake         # bring the shared services up after a sleep
./bin/backup_db.sh          # snapshot the database into .backups/
```

Regenerate the manual PDFs (master + one booklet per role):

```bash
.venv/bin/python build_manual_pdf.py --role all
```

### Re-cutting a release whose installers were mis-versioned

The v1.2.0 assets were named `0.1.0`. To rebuild them under the correct version,
after the version bump is merged to `main`:

```bash
gh release delete-asset v1.2.0 --yes $(gh release view v1.2.0 --json assets --jq '.assets[].name')
git tag -d v1.2.0 && git push origin :refs/tags/v1.2.0
git tag v1.2.0 && git push origin v1.2.0
```

Deleting the assets first is what makes this safe to repeat: the release job
attaches files, and a re-run against a release that still holds the old
`GI Hub_0.1.0_*` files leaves both versions sitting side by side with no
indication which is current. Deleting the **tag** is what makes the workflow fire
again — pushing an existing tag is a no-op.

> ⚠️ **Bump the three version files BEFORE re-tagging.** `release-desktop.yml`
> now refuses to build when they disagree with each other or with the tag, so a
> premature re-tag fails fast rather than publishing another mislabelled binary.

---

## 6. Open items — none of them blocking

> Re-verified 2026-08-05. **The two items that used to head this list are now
> DONE:** the Auditor reads the HOD executive-summary endpoints
> (`require_roles("hod", "auditor")`, GET-only, `readonly.py` untouched), and the
> login throttle is now **cross-worker**, backed by the `login_attempts` table
> rather than per-process memory.

0. **The QSEP tables are live but EMPTY, and one of them has a visible
   consequence.** `ppe_rules`, `ppe_distributions`, `qc_inspections` and
   `employee_movements` all hold 0 rows; `employees` holds 2. That is the
   expected state, not a defect — but ⚠️ **with no `ppe_rules` row, every PPE
   issue demands a safety document, nothing is given an expiry date, and the
   15-day PPE Forecast is permanently empty.** Configure a usable-time rule
   before concluding the forecast is broken. `inventory."Category"` does carry
   **9 PPE** items and **36 Surface Shields**, so both pipelines have something
   to act on.
1. **Nothing has been registered in the location tables yet.**
   `storage_locations` and `material_locations` are empty, and `asset_units`
   holds only **3 real operator rows** (`created_by` = `excel-sync` / `Akilan`)
   — ⛔ **not test residue; do not clear them.** They stay sparse until either
   the workbook columns are filled or somebody registers a tool in the app.
2. **Two workbook data-quality items** the sync reports on every run:
   Consumption Log row 9 (SAP 1169, `"At site"`) has a Location but **no serial**,
   so no asset can be keyed from it; and `Tank No.` `J092` matches no equipment.
3. **GPS capture needs HTTPS.** Over plain HTTP the browser refuses a position —
   the move still saves, without coordinates. Fine on the hosted address and on
   the native apps; a caveat only for local HTTP testing.
4. **Three SAPs disagree with the workbook's Current Stock**, and two are
   impossible states worth investigating: `1137` (workbook 0, DB **−15**), `1358`
   (workbook 0, DB **−2**), `1190` (workbook 142, DB 134). Negative stock means
   more was issued than ever arrived.
5. **The Android APK's internal version is stamped by CI, not by a file.**
   `frontend/android/` is gitignored and regenerated on every build, so
   `release-android.yml` patches `versionName`/`versionCode` after
   `cap add android`. `versionCode` packs the semver positionally (1.2.0 → 10200)
   because Android requires a monotonically increasing integer.
6. **Hetzner deployment** — paused by decision. Runbook is ready.

---

## 7. Where to read next

| File | What it holds |
|---|---|
| [`PROJECT_HANDOVER.md`](PROJECT_HANDOVER.md) | **The authority.** All locked rules with their evidence, the baselines, developer utilities, caveats |
| [`MANUAL_TESTING_GUIDE.md`](MANUAL_TESTING_GUIDE.md) | **Every manual test, ordered by business workflow**, with the 5 W's + 1 H for each feature and Given/When/Then per case. Also the fastest way to learn what the system PROMISES. Keeping it current is rule 13 |
| [`PROPOSED_PHASE_6_PLAN.md`](PROPOSED_PHASE_6_PLAN.md) | The QSEP plan as approved, with the operator's rulings inline |
| [`PROPOSED_NAV_FIX.md`](PROPOSED_NAV_FIX.md) | **APPROVED AND EXECUTED 2026-08-12.** The reasoning behind every grant and revocation in the role matrix — why `minLevel` was the wrong tool, why the route guard failed open, and the seven UI↔API disagreements. The *rule* is `PROJECT_HANDOVER.md` rule 14; the *enforced matrix* is `tests/e2e/specs/rbac-matrix.spec.ts`. Read this when you want the WHY. ⚠️ Its header records one finding deliberately left open: `crud.py`'s read routers are still `get_current_user` |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | The system brain — backend/frontend/DB/testing/security map |
| [`REPO_MAP.md`](REPO_MAP.md) | The `legacy/` ⇄ `tools/` ⇄ `data-archive/` segregation contract |
| [`OVERNIGHT_OPTIMIZATION_RUNLOG.md`](OVERNIGHT_OPTIMIZATION_RUNLOG.md) | Chatbot retrieval, idle logout, throttle, indexes, ⌘K |
| [`docs/EXPORTS_ROLES_SYSADMIN_RUNLOG.md`](docs/EXPORTS_ROLES_SYSADMIN_RUNLOG.md) | PDF/xlsx engines, the Auditor role, `bin/` scripts |
| [`docs/SME_ORDERED_SUBSET_RULE_RUNLOG.md`](docs/SME_ORDERED_SUBSET_RULE_RUNLOG.md) | The subset rule, end to end |
| [`docs/SME_TIER_SEGREGATION_RUNLOG.md`](docs/SME_TIER_SEGREGATION_RUNLOG.md) | Tier segregation, per tab |
| [`EXCEL_LOCATION_SYNC_RUNLOG.md`](EXCEL_LOCATION_SYNC_RUNLOG.md) | Rack + asset seeding, "app wins", and what to type in the spreadsheet |
| [`OVERNIGHT_ASSET_TRACKING_RUNLOG.md`](OVERNIGHT_ASSET_TRACKING_RUNLOG.md) | Asset schema, the GPS scanner, tank aliases |
| [`USER_MANUAL.md`](USER_MANUAL.md) | 22 chapters, user-facing (ch. 22 = QSEP). **Also the chatbot's corpus and the source of the printed booklets, so a new chapter must be registered in `manual_qa._ROLE_ALLOWED` AND `build_manual_pdf.ROLE_MANUAL_RECIPES` or it reaches nobody.** Written for non-technical readers: no ASCII art, no shell commands, no source identifiers — keep it that way |
| [`SECURITY_REVIEW_2026-08-05.md`](SECURITY_REVIEW_2026-08-05.md) | The audit itself — the one Medium finding, and an eleven-category "verified clean" list so the next audit does not re-tread it |
| [`SECURITY_SUGGESTIONS.md`](SECURITY_SUGGESTIONS.md) | Forward hardening roadmap in three tiers, sequenced, with a "deliberately NOT recommended" section |
| [`docs/GI_Hub_Executive_Presentation.html`](docs/GI_Hub_Executive_Presentation.html) | 18-slide management deck. Open in a browser; ← → to navigate, `F` for fullscreen |

---

## 8. Start here next session — choose ONE track

Nothing is half-finished, so this is a genuine choice rather than a queue.

### Track A — Tier 1 security hardening (~3 days)

From [`SECURITY_SUGGESTIONS.md`](SECURITY_SUGGESTIONS.md). None of these fix a
known-exploitable hole; they reduce residual risk.

1. **Enforce 2FA on `admin` and `logistics`.** The single highest-value item on
   the whole list. TOTP is fully built and correctly hardened — including the
   step-up password check most implementations miss — but it is **opt-in**, so
   an admin password is currently the only factor guarding user creation, every
   other account's password reset, and the database backup.
   ⚠️ **Enrol at least two admin accounts BEFORE enforcement flips**, or you lock
   yourselves out of your own console.
2. **Move the OTP and 2FA limiters to a shared store.** `deploy/Dockerfile.api`
   runs `--workers 4` and those budgets are per-process dictionaries, so the real
   limits are roughly 4x what the code says. The OTP guard is the one worth doing
   first — it is the limiter with a direct financial cost per bypass. The login
   throttle already shows the pattern (`login_attempts` table).
3. **Add dependency + static scanning to CI.** There is none today, and no
   Dependabot. Add `pip-audit`, `npm audit`, `bandit` as a **non-blocking** job
   first — a blocking gate on day one against an Ant Design tree just teaches
   everyone to bypass it.

### Track B — Hetzner production deployment

Unchanged, ready, paused by decision. Runbook
[`tools/migration/README.md`](tools/migration/README.md), kit
[`docs/DEPLOY.md`](docs/DEPLOY.md) + [`deploy/`](deploy/). Operator items are
listed under *FUTURE* in `PROJECT_HANDOVER.md` — generate the real `JWT_SECRET`
and `POSTGRES_PASSWORD`, set `GI_ENV=production`, approve the last Meta template,
add the Cloudflare Access bypass for the native apps.

> **If deployment is imminent, do A1 and A3 first.** Both are cheap, and both are
> harder to retrofit once real users are on the box.

---

**Status: stable, fully documented, security-audited, all gates green. Safe to restart.**

> **Before you change anything, read [`MANUAL_TESTING_GUIDE.md`](MANUAL_TESTING_GUIDE.md)
> §15 "Do's and Don'ts".** It lists the behaviours that look like bugs and are
> rulings — uninspected material reaching site, FEFO warning instead of
> blocking, rejected stock not auto-returning to the vendor, expired PPE not
> alerting anyone, an empty PPE forecast. Roughly a third of reported defects in
> this system have been one of those.
