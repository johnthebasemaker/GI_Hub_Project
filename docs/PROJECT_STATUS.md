# PROJECT STATUS — resume here (updated 2026-09-07 · Phase 12 COMPLETE · deployment PAUSED)

> 🔄 **2026-08-13 — the backend suite runs against its OWN database.**
> `gihub_svctest`, rebuilt from `gi_database.db` before the engine is created,
> because suites B…BX commit through the real ASGI app and cannot roll back.
> Your `gihub` is never opened. `PROJECT_HANDOVER.md` rule 15.
> ⚠️ Two **production-cutover** gaps surfaced the moment the tests ran against
> a database built the way production builds one — one fixed (a partial unique
> index that lived only in Alembic), one still open (cutover stamps Alembic
> without running it, so data backfills are skipped). Both under *FUTURE* in
> `PROJECT_HANDOVER.md`.

> 📌 **A fresh session should read [`PROJECT_HANDOVER.md`](../PROJECT_HANDOVER.md)
> FIRST** — it carries the locked architecture rules, the current baselines and
> the next phase in one page. This file is the deeper state + gotchas reference.

**This is the single source of truth for "where we left off."** A fresh chat
should read this file, then [`ARCHITECTURE.md`](ARCHITECTURE.md) (**the full
system map: backend modules, DB traps, entry gates, rate limiting, AI routing,
Excel-sync mechanics, PWA/offline, test commands — read it before touching
code**), then [`REPO_MAP.md`](../REPO_MAP.md) (segregation contract),
[`NEW_STACK_HANDOFF.md`](NEW_STACK_HANDOFF.md) (how-to-work rules), and
[`POSTGRES_MIGRATION.md`](POSTGRES_MIGRATION.md) §8 (the complete per-slice
run log — the project's full history lives THERE, not here).
Legacy/SME rules: [`handoff.md`](../handoff.md) (SME Canon).

---

## 0d. Phase 12 — COMPLETE (2026-09-06 → 2026-09-07)

**Automated Role-Based Video Tutorials.** Five branches, all merged (PRs
#73–#76). Baselines now **service tests 2,328 / 0** (suites A…CX) · **E2E 128**
· legacy 599/0/0 · nav 51 · **AI evals Tier 1 147/147, 0 leaks** · alembic head
unchanged at **`a1c9e64b3d70`** — Phase 12 added **no migration**, because it
fills the training tables slice 10b already created.

A tutorial is a **Playwright screencast of the real UI** plus a **narrating
avatar**, composited locally into an MP4 with a WebVTT sidecar and a manifest.
Full architecture in [`ARCHITECTURE.md`](ARCHITECTURE.md) §7j (the pipeline)
and §7k (the assistant's deep links); the seven locked **P12-x** rulings are in
`PROJECT_HANDOVER.md`.

> ⚠️ **THE FINDING THAT REFRAMED THE PHASE.** The brief asked how to stop
> proprietary data reaching HeyGen's API. That is the easy half —
> `assert_text_only()` closes it by refusing any free-text field that is not
> character-for-character a reviewed line from the tracked script. **The hard
> half is that the MP4 is the sensitive artefact**, and it is sensitive whether
> or not it ever touches a cloud: `tests/e2e/global-setup.ts` loads its
> throwaway database from the REAL `gi_database.db`, so the prototype's first
> frame carried real employee names, real material descriptions, real SAP codes
> and real quantities — into a file people forward. Hence **P12-0**: a tutorial
> is recorded against SYNTHETIC data, or it is not published.

**What shipped**

| Slice | What |
|---|---|
| prototype | The pipeline end to end, minus one HTTP call. Egress guard, two-pass timing, ffmpeg composite |
| **12a** | `tools/make_tutorial_db.py` — real classification structure, every name/description/SAP/quantity/date invented, deterministic on a pinned `ANCHOR`. Its own database `gihub_tutorial_pw` on :8011/:5184 with **no file under `tests/e2e/` edited** |
| **12b** | The manifest (P12-5), **WebVTT** into the `captions_uri` column empty since 10b, freeze-padding, the batch runner, the rule-14 route lint |
| **12c** | The **declarative** recorder (`steps:` in the YAML) and the first four tutorials, narrated from role-fenced manual chapters |
| **12f** | The assistant answers **and** deep-links to the second the step is on screen. Suite **CX** (15 checks) |

**Four tutorials rendered** (1920×1080, worst narration overrun +0.00 s):
`ocr_workflow_v1` 1:31 · `sk_stage_return_v1` 1:31 · `hod_executive_summary_v1`
1:53 · `store_keeper_hub_assistant` 0:47.

### 0d-i. ⚠️ What is deliberately NOT done, and why it blocks nothing

* **The HeyGen submit/poll/download half.** `heygen_live()` is written from the
  published v2 contract and marked **UNVERIFIED** in the source — no key has
  ever been used against this repository — and the poll half is deliberately
  unwritten, because guessing at a job-status shape produces code that looks
  finished and is not. Narration egress is **signed off by the operator
  (2026-09-06)**; it needs a key, not a decision.
* **Object storage for the renders.** `docs/tutorials/out/` is gitignored and
  local. This is coupled to the Hetzner cutover (operator ruling Q3).
  ⚠️ **Consequence worth knowing on a fresh box:** with no manifests, the
  assistant's deep links simply never appear. That is a supported state, not a
  fault — `GET /ai/health` reports `tutorials: {present, tutorials, beats,
  indexed}` so an empty index is visible rather than a mystery, and suite CX-13
  pins the behaviour.
* **The rest of the ~60-clip catalogue** (operator ruling Q2). The recorder is
  declarative, so a new tutorial is a YAML file, not a spec file.

## 0c. Phase 10 — COMPLETE (2026-09-02 → 2026-09-03)

**Enterprise Security, Automated Analytics & Ecosystem Integration.** Three
branches, all merged. Baselines **as at the end of Phase 10** were
**service tests 2,188 / 0** (suites A…CS) · **E2E 125** · legacy 599 · nav 50 ·
**AI guardrail Tier 1 24/24, 0 leaks** · alembic head **`e7f2a4c916b8`**.
*(Current numbers are in §1; this line is what this phase left behind.)*

> ⚠️ **The phase as briefed would have rebuilt shipped code.** The planning pass
> (`PROPOSED_PHASE10_PLAN.md`) found 2FA already complete, the MTC chase sweep
> already in `health_monitor`, and the branded PDF engine already in
> `exec_pdf`. Operator agreed the re-scope: **extend three tracks, build two.**
> Twilio and reportlab were dropped — Meta WhatsApp Cloud API and fpdf2 are
> already live.

| Slice | Branch | What shipped |
|---|---|---|
| pre | `fix/ocr-orphan-crash` | 🐛 the orphan sweep was reaping OTHER workers' live OCR jobs on a respawn; now heartbeat-based (`ai_jobs.worker_id` + `heartbeat_at`). `num_ctx` became a measurement (`ocr.estimate_image_tokens`) rather than a constant |
| 10a | `feat/phase10-security-and-evals` | `tests/ai_eval/` — Tier 1 hard gate (suite CQ) + policy pin; mandatory 2FA for admin/logistics/hod/qc_hod/auditor with a 14-day grace and a scope-limited `enroll` token; `rate_buckets` giving the four per-process limiters a cross-worker half |
| 10b | `feat/phase10-ecosystem` | 🐛 `services/dailyjob.py` — the atomic claim **three daily loops never had**; day-shift MTC chase (email to Logistics, WhatsApp DRAFT externally); the Valuation & 30-Day Burn board brief; the Training hub and its SOFT gate |
| docs | `chore/phase10-docs` | this sweep — USER_MANUAL §24, ARCHITECTURE §7b–§7d, the CJ doc-drift needles, and the retrieval aliases that make §24 findable |

**Three production bugs were found by building on top of the code, not by
looking for them:**

1. **The orphan sweep killed live jobs.** `UPDATE ai_jobs SET status='error'
   WHERE status IN ('queued','running')` with no owner filter — correct for one
   process, wrong for `--workers 4`. A single worker respawn failed the
   in-flight OCR reads of the other three.
2. **The daily loops fired four times a day.** Only `report_center` claimed its
   work. The 07:00 briefing, the 16:00 digest and the Friday exec PDF each
   dispatched in every worker, so every recipient got four copies — invisible in
   dev (one worker) and in the tests (`GI_SCHEDULER=0`).
3. **Four rate limiters were 4× looser than documented**, worst of all the
   second-factor attempt budget (5 documented, 20 actual).

📌 **Open, not a regression:** the AI assistant's Tier 2 generation score is
**64%** against a 95% target (was 43% before the anti-confabulation prompt
rule). Tier 1 passes with zero leaks on the same run, so nothing forbidden
reaches the model — the gap is an 8B model preferring to answer rather than
refuse. The lever is a larger chat model, not more prompt text. The threshold
was deliberately not lowered.

## 0b. Phase 9 — complete (2026-08-25 → 2026-09-01)

**Paper-first OCR, QSEP gates, analytics.** Baselines at close: service tests
2,064 / 0 · E2E 119 · legacy 599 · alembic head `a2c9f5e81b43`.

| Slice | What shipped |
|---|---|
| 9a–9b | WBS numbers + canonical work types; the manpower planner's night-shift arithmetic (nights buy calendar time, not a smaller payroll) |
| 9c–9d | the printed consumption form (QR + corner fiducials) and the **paper-first workflow** — supervisor → SK → HOD, with four colour layers (OCR grey → supervisor amber → SK red → HOD purple) and approval as the ONLY writer of lining consumption |
| 9e–9f | Efficiency by Day (the running figure, and its two divisions by zero); "Labor" → "Manpower" on screen only — the JSON keys did not move |
| post | `feat/optimization-and-fixes` — the OCR envelope (per-lane token budgets, a 900 s vision timeout, truncation salvage), Bloom filters, and the QSEP documentation correction |

⚠️ **The QSEP audit found the code already correct and the DOCS wrong.**
Material without an MTC can be received and sent to site; it cannot be issued or
consumed there. `docs/ARCHITECTURE.md` had carried the pre-2026-08-12 sentence
"MTC hard-block for Surface Shields receipts" for three phases.

## 0a. Phase 8 — complete (2026-08-20 → 2026-08-24)

Six slices, all merged. Baselines now **service tests 1,868 / 0** (suites
A…CJ) · **E2E 107** · legacy 599 · SME parity 1,313 · UI math 33 · nav 48 ·
alembic head **`c7e1a4b92d63`**.

| Slice | Branch | What shipped |
|---|---|---|
| 8a | `feat/phase8-planner-math` | benchmark **selection** before summation — four live planner defects (2.00× · 2.00× · 2.29× · 25×), the orphan norm pruned |
| 8b | `feat/phase8-planner-ux` | surface dedup (a stacked surface is blasted once), one shared job label, CV/ME chips, Target Days, multi-select, per-role dashboard |
| 8c | `feat/phase8-procurement-lock` | `pr_registry`, asserted state transitions, **per-LINE** PO lock (a PR may carry several POs), idempotency keys |
| 8d | `feat/phase8-qc-hod` | the `qc_hod` role — level 2 with a *named* cross-site exemption, category-bounded reads, escalations, stagnation/expiry, a 7-tab portal |
| 8e | `feat/phase8-sme-mp-link` | the SME session costed in labour (can do · overall · blocked), 60 s cascade cache, exports, URL handoff; **Track 5** full-width KPI rows |
| 8f | `feat/phase8-docs-ai` | **one** manual (the stale `docs/USER_MANUAL.md` deleted), §2 restructured for retrieval, all 12 Man-Hours tabs written up, alias map, index warmed at boot, prompt rules, suite CJ |

⚠️ **The assistant's "outdated answers" were a CORPUS problem, not a pipeline
problem.** Measured before anything changed: the index costs 17 ms to build
and 0.3 ms per search, while §19 documented five Man-Hours tabs against a page
that had eleven, and the access matrix was invisible to every non-admin role.
Suite CJ compares the manual against the CODE so the drift cannot come back
quietly.


## 0. Current state in one paragraph

**The GI Hub v2 stack (React 19 + FastAPI + PostgreSQL 16) is
FEATURE-COMPLETE and deployment-ready.** *(Current through **Phase 12**,
2026-09-07 — role video tutorials and the assistant's deep links; see §0d.)* Everything through the 2026-07-18
five-phase pre-deploy batch is shipped and green: the full legacy parity
program, Man-Hours, the Intelligence layer (AI-0…AI-5), the SME rebuild
S1–S6 + Smart Calculator, native WhatsApp/email/notifications (inbound
webhook included), the entry-document/MTC/WBS gates, the Playwright E2E
suite, the production cutover script, the Bulk Excel Import feature, the
handwritten-OCR spec pipeline (17-column TSV export), the Surface-Shields
system-first issue workflow, global UI polish (sticky headers, smart
decimals, scoped reports), fixed CI/CD, and the Admin Bug Tracking Engine
with its coding-agent prompt generator. The CNCEC tracking workbooks are
fully injected (stock **429/429** vs the workbook; SME reseeded to the
renumbered system codes 1–10 with exact SAP joins). On top of that, the
**2026-07-24…26 native program** shipped: Capacitor (Android/iOS) + Tauri
(Windows/macOS) wrappers with an automated GitHub-Releases pipeline,
**RTR auth** (refresh-token families; 90-day native sessions), the
Outlook-style **Send/Receive sync engine**, the **QR sticker + scan-to-
dashboard ecosystem**, and native production API routing (`VITE_API_URL` +
CSP/CORS). The repo is now PUBLIC at `johnthebasemaker/GI_Hub_Project`
with tags v0.1.0–v1.0.1 (desktop installers already on the Releases page).
**2026-07-30 — the SME allocation lane was overhauled and the deployment
was PAUSED by decision.** Three programmes landed on top of the above:
`tools/pg_excel_sync.py` (atomic, idempotent, Postgres-native Excel sync),
the **two-tier Available-vs-Ordered allocation with reverse-SQM bottleneck
maths**, and the **component-identity fix** — SME stock is now pooled per
`(Material_Code, SAP_Code)` instead of per `Material_Code`, because a
multi-part system is four physical drums sharing one code. Alongside them,
**every table in the app gained sorting and filtering** via
`frontend/src/lib/smartTable.tsx`.

**The Hetzner production deployment is PAUSED (not blocked)** — the next
phase is *Feature Fine-Tuning and UI Polish*. Everything the deployment
needs is built and documented (runbook: `tools/migration/README.md`; deploy
kit: `docs/DEPLOY.md` + `deploy/`), plus one Cloudflare dashboard action for
the native apps (§3.6). Locked rules + baselines:
[`PROJECT_HANDOVER.md`](../PROJECT_HANDOVER.md).

## 1. Gates (all green locally — 2026-09-07)

| Gate | Result | Command |
|---|---|---|
| **Harness hygiene (runs FIRST in CI)** | ✅ clean · 10 negative controls | `bash bin/ci_preflight.sh` |
| Backend service tests | **2328/0** (suites A…CX, **own throwaway DB**) | `GI_DOTENV=0 .venv/bin/python -m backend.api.service_tests` |
| Playwright E2E | **128/128** (~50 s, own throwaway DB) | `cd tests/e2e && npm test` |
| **AI guardrail — Tier 1** | **147/147, 0 leaks** (also runs inside suite CQ) | `.venv/bin/python -m tests.ai_eval.runner` |
| **AI retrieval — recall / precision** | **1.000 / 0.994** (HARD GATE ≥ 0.85) | same command |
| **AI eval grid freshness** | ✅ current, 72 verified cases | `.venv/bin/python tools/gen_eval_grid.py --check` |
| AI guardrail — Tier 2 | 📌 **scored, NOT a gate** — security 64% vs a 95% target, false-refusal 0%. ⚠️ **KNOWN MODEL CONSTRAINT — see §1a below.** Stochastic; needs a live model | `bash bin/ai_eval_tier2.sh` |
| Legacy regression | **599/0/0** ⚠️ needs `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` on macOS, else 598/0/**1 skipped** (rule 16) | `.venv/bin/python legacy/bug_check.py` |
| Frontend | build + `tsc -b` + `oxlint` ✅ | `npm run build --prefix frontend` |
| SME engine parity | **1,313 comparisons** | `npm run parity:sme --prefix frontend` |
| SME UI math | **33/0** | `npm run test:ui-math --prefix frontend` |
| Navigation route coverage | **51 routes, all claimed** | `npm run test:nav --prefix frontend` |
| Alembic | single head **`a1c9e64b3d70`** (11e: `ai_answer_cache`; 11c added `ai_traces` `f8a3c05d1b27`). ⚠️ **Phase 12 added NO migration** — it fills tables slice 10b already created | see ARCHITECTURE §8 |
| 🎬 Tutorial scripts | **NOT A GATE, by design.** `--dry-run` lints every script (rule-14 routes, rule-9 manual fence, redaction) without opening a browser | `.venv/bin/python tools/generate_tutorial.py --all --dry-run` |
| Derived-view parity | **5/5** ⚠️ fresh cutover / CI only | `DATABASE_URL=… .venv/bin/python tools/parity_check.py` |
| Release pipeline | desktop ✅ (dmg/exe/msi on v0.1.0–v1.0.1) · Android fixed (JDK 21) — next tag should attach the `.apk` | `git tag vX.Y.Z && git push origin vX.Y.Z` |

✅ **`postgres-dual-ci.yml`'s 30-run failure was diagnosed and fixed on
2026-09-02** (Phase 11 slice 11a, now **rule 16**). It was never
Linux-specific magic: `bug_check.py` replaced `subprocess.Popen` with a stub
returning `None` for the whole run, which broke `ctypes.util.find_library` —
and therefore `import pyzbar` — on Linux only, because macOS's `find_library`
probes dyld and never shells out. The `::error::` shim compounded it by
grepping stdout for `❌`, which matched `whatsapp_worker`'s ordinary
terminal-failure log line and blamed a passing test for two months. Both are
fixed; the shim now parses `BUG_REPORT.md`'s failure headings. ⚠️ And the check
that was failing had **never actually run on any machine** — see rule 16.

⚠️ `tools/parity_check.py` **fails against the live mirror BY DESIGN** —
PostgreSQL is permanently ahead of the frozen SQLite since the Excel
injection. It stays meaningful only on CI / a freshly-reloaded mirror.

## 1a. ⚠️ KNOWN HARDWARE / MODEL CONSTRAINT — the Tier 2 ceiling

**Tier 2 security is 64% against a 95% target, and the gap is the MODEL, not
this codebase.** It is recorded here as a constraint with an owner and a
trigger, not as a bug on a backlog.

**Why it is not a pipeline defect** — three measurements say so:

| Evidence | Value | What it rules out |
|---|---|---|
| Tier 1 leaks | **0 across 147 cases** | the fence |
| Contextual recall / precision | **1.000 / 0.994** | retrieval |
| False-refusal rate | **0%** | over-refusal / policy |

What is left is the model **inventing UI that no chapter describes**. Asked
"what is on the Service Health card?", `llama3.1:8b` describes one — that phrase
lives only in chapter 7, and Tier 1 proves it was never in the prompt. The model
echoes the question's wording and confabulates the rest.

**Prompt engineering is already spent.** Slice 10b's anti-confabulation rule
bought **+21 points** (43% → 64%) with false refusal staying at 0%. Further
prompt text buys less each time and risks pushing false refusal up, which is the
number most worth protecting.

**The fix is a larger chat model, which is a SERVER decision.** The standing
ruling is one warm 7-8B model on the box; a 30B-class model needs GPU memory the
CPX42 does not have.

> **Trigger for a future phase:** when the host gains a GPU (or the deployment
> target changes), re-run `bash bin/ai_eval_tier2.sh --record` on the new
> hardware and compare. That single command turns this from an assertion into a
> measurement.

⚠️ **Three things NOT to do in the meantime**, all considered and rejected:
lower the 95% target (it is left unmet deliberately — a threshold tuned to pass
measures nothing); add more prompt text (past its point of diminishing return);
or wire Tier 2 into CI (ruling P10-7 — it would fail every run until somebody
switched it off). The deterministic gates carry CI instead, at 1.000 / 0.994.

Full reasoning: `docs/ARCHITECTURE.md` §7i.

## 1b. ⚠️ OPEN DECISION — `service_tests` cannot run on CI, and rule 15 says it can

**Rule 15 states that the test database is rebuilt from `gi_database.db` and
that "that file is in git and is itself a gate, so every machine and CI start
from identical rows".** The file has been gitignored since **2026-07-26**
(commit `a09da0b`, "security(secrets): untrack tracked SQLite databases"), so
that guarantee has been false for six weeks and **`backend/api/service_tests.py`
has only ever passed on the operator's laptop.**

It was invisible because `legacy/bug_check.py` failed first on all 30 recorded
CI runs. Fixing that in Phase 11a did not break anything — it advanced the job
far enough to reach what was already broken.

**What the suite needs that CI does not have:** four login accounts with known
passwords, five SAP codes, employee `30001`, seven `GI-*` material codes, and
enough SME scopes for the man-hours scorecard and auto-draft to be meaningful.
`tools/make_ci_fixture_db.py` supplies the unambiguous ones and takes the suite
from ~30 failures to 6.

⚠️ **Reconstructing the rest synthetically was tried and abandoned on evidence.**
Inventing the seven material codes and a second SME scope took it from 6
failures to **16** — the invented rows changed what assertions like
"2 matched / 1 unmatched" and "auto-draft save → 2 estimates" mean. Every
guessed row is a guess about INTENT, and a suite that tests guesses is worse
than one honestly skipped.

**Meanwhile:** the step emits a `::warning::` naming this decision and does not
fail the build (rule 16 — a skip that says so, not a silent pass; a permanently
red build is one everybody learns to ignore). It runs for real the moment a
snapshot is present, with no second edit.

### The decision — two options, both yours

| | What it costs | What it buys |
|---|---|---|
| **(a) Commit a SANITISED snapshot** — real structure and rows, names/phones/vendors scrubbed | A data-disclosure judgement: it would still carry stock quantities, the project structure and the site names | Restores rule 15's actual intent — every machine and CI start from identical rows, and the suite runs everywhere |
| **(b) Accept it as a LOCAL / self-hosted gate** — remove it from the GitHub workflow and run it where the data lives | CI never runs 2,300 checks; a regression is caught at the desk rather than on the PR | No data leaves the machine; honest about what the runner can prove |

⚠️ **Whichever you choose, rule 15's sentence must be corrected** — it currently
promises a property the repository does not have, and a rule that is
provably false is worse than a rule that is merely strict.

The two steps this workflow is actually named for — `dual_ci` and
`parity_check` — are **fixed and verified** against the generated fixture on a
real PostgreSQL 16.

## 2. What shipped (compressed — full history in POSTGRES_MIGRATION.md §8)

- **Phase 12 — Automated Role-Based Video Tutorials (2026-09-06 → 09-07).**
  Five branches, plan in `PROPOSED_PHASE12_PLAN.md`, seven `P12-x` rulings in
  `PROJECT_HANDOVER.md`, architecture in `ARCHITECTURE.md` §7j/§7k. Full
  write-up in **§0d** above. In one line: a tutorial is a Playwright screencast
  of the real UI plus a narrating avatar, composited **locally**, recorded
  against a **synthetic** database, narrated from **role-fenced** manual
  chapters, and deep-linked from the assistant to the second the step happens.
  ⚠️ The finding that reframed it: the API boundary was the easy half — **the
  MP4 is the sensitive artefact**, and it is sensitive whether or not it ever
  touches a cloud.
- **Phase 11 — Enterprise AI Engineering, Observability & Security Gateways
  (2026-09-02 → 09-05).** Six slices, plan in `PROPOSED_PHASE11_PLAN.md`,
  twelve `P11-x` rulings + **rule 16** in `PROJECT_HANDOVER.md`.
  1. **11a — the CI fix.** Root-caused the 30-run dual-CI failure: a
     process-wide `subprocess.Popen` stub broke `ctypes.util.find_library` on
     Linux, and the `::error::` shim blamed a passing WhatsApp test for two
     months. ⚠️ The check it was failing had **never run on any machine** — a
     `except ImportError: return` reported a SKIP as a PASS. Added a **SKIP
     status**, `bin/ci_preflight.sh` + `tools/harness_hygiene.py` (5 rules, 10
     negative controls), `CLAUDE.md` + `.claude/RULES.md`.
  2. **11b — the OCR upgrades.** Ran the operator's three real documents
     through the live pipeline: all read CORRECTLY, and the printed form took
     **399 s against a card promising "under a minute"** — the whole of "it
     only loads and never gets results". Added a live elapsed counter, an
     **Interrupted** state from the job heartbeat with retry, lane routing for
     QR-less papers, the **`<DITTO>`** fix (the model returned `""` for ditto
     cells, so 19/30 tank numbers, 14/30 names and 8/30 products were silently
     dropped), the **`(Night)` shift** (which had been making the whole page
     `CRIT_DATE_UNPARSEABLE`), and the two-switch cloud seam.
  3. **11c — request tracing.** `ai_traces` (alembic `f8a3c05d1b27`), six
     spans, and **retrieval telemetry**: BM25 scores that had been computed on
     every question since the assistant shipped and thrown away. AI Traces page
     for admin **and auditor** (mounted twice — the Console is `minLevel: 4`).
  4. **11d — the guards.** `ai/guard.py` + `guard_patterns.yaml`: shape checks,
     13 scored jailbreak patterns, a role-aware topic pre-flight, and an output
     guard (formula defusal reusing `reports._RISKY`, PII redaction, runtime
     canaries) over a **sliding buffer sized from the data** for SSE. Suite CT,
     50 checks, 7 of them negative twins.
  5. **11e — the gateway + cache.** `ai/route.py` lane policies (and
     `jobs.NUM_PREDICT` derived from them); `ai_answer_cache` keyed on
     **question · role · manual hash · prompt hash**. Found that 11b's cloud
     fallback had covered **one of five** vision lanes.
  6. **11f — the eval gates.** 147-case dataset (72 GENERATED + verified grid,
     24 fence probes, 15 jailbreak, 12 near-miss); **Contextual Recall and
     Precision gate CI at ≥ 0.85** (deterministic, so P10-7 is not overturned);
     Tier 2 trended against a recorded baseline with a bug row on regression.

  **Rejected, with reasons in each module's header:** Portkey (a Node daemon in
  the request path), LangSmith (spans carry the prompt — manual chapters, live
  SQL, OCR'd delivery notes), Guardrails-AI (its jailbreak validator downloads a
  second resident transformer), LiteLLM (its Ollama adapter would have taken
  over `num_ctx`), RAGAS/DeepEval (both judge with OpenAI by default; their
  metric definitions were vendored instead).

- **Parity + platform (…2026-07-12):** 10-slice parity build · Man-Hours ·
  AI-0…AI-5 (assistant, OCR lanes, NL→SQL with the `gi_ai_ro` second wall,
  two-lane `/ai/query`) · SME S1–S5 dual-engine rebuild · feature-gap P0–P6 +
  I-A/I-B + deferred-MED · Phase 7/7b/7c WhatsApp + SMTP + ubiquitous
  `dispatch()` · inbound webhook (STOCK / RESET PASSWORD, HMAC + penalty box)
  · evening digest · dual-OTP phone change · UAT rounds (E.164, exec PDF,
  cutover script `tools/migration/cutover_migrate.py` verified `--strict`).
- **Cutover day (2026-07-13):** SME **S6 Master Data CRUD** · **Phase B
  restructure** (`legacy/` · `tools/` · `data-archive/`) · Excel injection
  #1 · Bulk Excel Import (`/import/{kind}` + BulkImportPage) · C3 OCR doc
  assist · QR/returnables parity · role-based manual content folded into the root `USER_MANUAL.md`.
- **Pre-deploy batch (2026-07-18):**
  1. **SME SAP-code overhaul** — `sme_recipe.SAP_Code` +
     `sme_inventory_seed.SAP_Code` (alembic `b3f2a9c47d18`); recipe identity
     (code, material, SAP); system codes RENUMBERED 1–10 via
     `excel_sync.py --sme-reseed` (guarded wholesale SME replace); header-
     name-driven sync w/ unknown-column warnings; stock 429/429.
  2. **AI** — ask-data deep filters (category ILIKE + material-family
     keywords joined through the SME SAP codes, template lane = safe for
     scoped users) · **handwritten-OCR spec** implemented stage-for-stage
     (`ai/handwritten.py`, spec vendored at `docs/features/handwritten-ocr`)
     with the 17-column legacy TSV export.
  3. **SME/SK portals** — Surface-Shields **system-first issue workflow**
     (`/entry/lining-systems`; Done vs Pending SQM; `LS <code>` remark) ·
     **🧮 Smart Calculator** (`/sme/calculator`: For_1_SQM × SQM demand,
     pack counts, live stock coverage, explanations).
  4. **UI polish** — sticky headers on all ~93 tables · smart decimals
     (`lib/format.ts`) · report column scoping (+Material description).
  5. **Infra** — CI diagnosed & fixed (see ARCHITECTURE §8) · **Bug
     Tracking Engine** (severity/rollback/safety triage on `bug_reports`,
     `GET /admin/feedback/{id}/prompt` self-contained coding-agent prompt,
     `.md` digest export).
- **Native-app program (2026-07-24…26, commits ff3ce5b…65e2653):**
  1. **Diagnostics** — `tools/diagnose_sync.py` sync doctor (+
     `docs/DEBUGGING.md`).
  2. **Native shells + sync engine** — Capacitor scaffold (`android/`/`ios/`
     gitignored, regenerated per build) + Tauri v2 (`frontend/src-tauri/`
     COMMITTED, identifier `com.giinventory.hub.desktop`) · strict OTA (SW
     poll every 15 min + on refocus) · **Send/Receive** header button
     (`SyncControls.tsx`: flushQueue + invalidate ALL queries) · sync-cap
     setting `gi_sync_interval_min` (1–120 min, live re-arm).
  3. **QR ecosystem** — `GET /documents/material-stickers` (hod/admin;
     2×6 full-bleed A4 replica of the operator's CNCEC sticker sheet;
     name auto-shrink; QR = SAP_Code) · 📷 header scanner →
     `MaterialCardModal` via `GET /stock/material-card` (site-scoped
     stock + 30-day gap-free Recharts trend; suite AP).
  4. **Release pipeline** — `release-desktop.yml` (macos-14 + windows,
     `npx tauri build`) + `release-android.yml` (ubuntu, **JDK 21** —
     Capacitor 8 hardcodes Java 21) → assets on the tag's GitHub Release;
     triggers = `v*` tags + workflow_dispatch ONLY.
  5. **RTR auth (`refresh_sessions`, alembic `f1a7c9e83b52`)** — refresh
     cookie is a signed JWT (jti/fam/client claims); login `client_type`
     'web' = 7-day family / 'native' = 90-day; rotation stays in-family;
     **replaying a revoked token revokes the WHOLE family** (other
     devices survive) + SESSION_REUSE audit; logout + admin revoke are
     family-wide; `auth_sessions` = legacy revoke-only; table is in the
     `gi_ai_ro` REVOKE set + AI safety FORBIDDEN_TABLES; suite AQ.
  6. **Native API routing** — builds inject
     `VITE_API_URL=https://gi.giinventory.com/api` (`API_BASE` in
     client.ts; web builds stay relative `/api`); axios withCredentials;
     CORS defaults include the fixed shell origins; refresh cookie
     `SameSite=None;Secure` under `GI_ENV=production`; explicit Tauri CSP;
     `logApiFailure()` console diagnostics incl. Cloudflare-Access
     detection; Apple-Silicon 3-step install fix documented.
- **SME allocation overhaul + global table tools (2026-07-27…30):**
  1. **`tools/pg_excel_sync.py`** — ONE atomic transaction across all five
     workbook kinds, Postgres-native `ON CONFLICT` upserts with
     `COALESCE(excluded, table)`, dry-run by default, PG-only guards, and a
     preflight that names the wrong interpreter. Mapping logic is NOT
     duplicated — it imports `bulk_import.py`'s planners and replaces only
     the write path. **No Pandas** (not in `backend/requirements.txt`).
     Log: `docs/PG_EXCEL_SYNC_RUNLOG.md`.
  2. **Session-report aggregation** — a "Total Material Demand" sheet leads
     the workbook and a "Material-Wise Summary" leads the PDF, with
     download buttons in Combined Procurement.
     Log: `docs/SESSION_REPORT_SUMMARY_RUNLOG.md`.
  3. **Two-tier allocation + reverse SQM** — `Allocated_Qty` splits into
     `Alloc_Available` + `Alloc_Ordered`; on-order was netted
     `max(Initial_Ordered_Qty − Σreceipts, 0)` against double-counting
     (**superseded 2026-08-02 by strict decoupling** — the on-order pool is
     `Initial_Ordered_Qty` verbatim now that no receipt reaches availability);
     material availability is restated as **SQM achievable**, bottlenecked
     by the scarcest component. Feasibility judges PHYSICAL stock only.
     New Material-Wise Segregated Report (PDF + Excel).
     Log: `docs/SME_SQM_BOTTLENECK_RUNLOG.md`.
  4. **Component identity (alembic `a4e9b1c73f28`)** — SME stock pools are
     keyed `(Material_Code, SAP_Code)`. A multi-part system lists ONE code
     as four physical drums; pooling them reported covered components as
     short and short ones as covered. Suite **AZ** (20 checks) incl. four
     revert-verifications. Display names now come from the STOCK master
     (the recipe repeats one generic name on all four lines) and wrap
     instead of ellipsing. Log: `docs/SME_COMPONENT_POOLING_RUNLOG.md`.
  5. **Global table sorting + filtering** — `frontend/src/lib/smartTable.tsx`
     wraps antd's `Table` for all 99 grids across 45 files; sorters and
     filters are derived from the column definitions with zero call-site
     change and zero added chrome. Server-paginated grids opt out
     automatically. Log: `docs/TABLE_TOOLS_RUNLOG.md`.

## 3. Deployment — ⏸️ PAUSED by decision (2026-07-30)

**The next phase is *Feature Fine-Tuning and UI Polish*.** Phase 3 (Hetzner
Ubuntu + Docker) is paused, **not blocked** — everything below is built and
documented, and it resumes once fine-tuning is done.

Follow `tools/migration/README.md` end-to-end. Highlights:
1. Provision Hetzner CPX42 · `deploy/` kit (`docs/DEPLOY.md`) · TLS ·
   Cloudflare Tunnel (`gi.giinventory.com`; rate-limiter reads
   CF-Connecting-IP).
2. `ollama pull` the 3 models; `create_ai_readonly_role.sql` + set
   `GI_AI_RO_URL` (password-protected in production).
3. Final data load: `cutover_migrate.py --strict --wipe` from
   `gi_database.db`, **then the Excel re-sync + SME reseed** (the runbook's
   exact commands — the injection lives only in PG).
4. `deploy/.env` secrets (`GI_ENV=production`, `JWT_SECRET`, `WHATSAPP_*`
   incl. webhook verify-token/app-secret, `SMTP_*`, `EMAIL_LOGISTICS_TO`,
   `PUBLIC_BASE_URL`) — never in git.
5. Smoke gates against production; point users at React; `deploy-v2.yml`
   (manual) thereafter.
6. **Native apps (Cloudflare dashboard, one-time):** add a Zero Trust
   Access application for **`gi.giinventory.com/api/*`** with a **Bypass
   (Everyone)** policy — the installed apps have no Access SSO session, so
   without it every native API call dies as a CORS-killed 302 ("Server
   unreachable" with a working web portal). The API self-guards (JWT +
   roles + rate limits); Access stays on the HTML portal. Details:
   `docs/NATIVE_APPS.md` §6. Then drop the Release installers into
   `downloads/` and real-link USER_MANUAL §1.2.

**Operator TODOs still open (Meta side):** approve `gi_evening_summary`
(2 body vars, lang `en`); subscribe the webhook URL in Meta. The other
four templates are LIVE (lang `en`).

**Operator TODOs still open (server side):** generate strong `JWT_SECRET`
and `POSTGRES_PASSWORD` — both currently `CHANGE_ME` in `deploy/.env`.
`GI_ENV=production` must be set to arm the JWT boot guard (see item 4).

**Secrets already populated in `deploy/.env`:**
`WHATSAPP_WEBHOOK_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`,
`PUBLIC_BASE_URL`, `SMTP_*`, `EMAIL_LOGISTICS_TO`. Verified 2026-07-26
via Phase 1 Audit 04.

`.env.example` git history verified clean of Meta credentials (single
commit `f3d706b`, placeholders only) — Phase 1 Audit 04.

_Last verified against reality: 2026-07-26 via Phase 1 Audit 04._

## 4. Hard-won gotchas a fresh session must know

- **Never delete `system_audit_log` rows** — audit assertions are
  DELTA-counted.
- **SME engine changes** = change BOTH engines (`backend/api/sme_engine.py`
  + `frontend/src/sme/engine.ts`) + regenerate the golden in ONE commit.
  Shared half-up rounding `floor(x·10ⁿ+0.5)` — never "fix" to half-even.
- **Recipe identity is (code, material, SAP_Code)** — PU component lines
  share a Material_Code; don't collapse them. CRUD dup-checks the triple.
- **STOCK identity is (Material_Code, SAP_Code) too** (2026-07-30, locked —
  overturns the 2026-07-18 pooling rule). `sme_inventory_seed`'s PK is the
  pair; the engines key every pool, total, shortfall and report row on
  `Material_Key` = `"CODE|SAP"`. **Never revert to pooling by Material_Code
  alone** — it reported covered components as short and short ones as
  covered. `Material_Name + UOM` is NOT a usable discriminator (all four PU
  recipe rows share both). SAP codes are whitespace-normalized on both
  sides of every join (`"1043 - 2"` ≡ `"1043-2"`).
- **SCOPE-WIDE BOTTLENECK (2026-08-04):** a scope's coverage is
  `Σ buildable m² ÷ Σ remaining m²` (`session.ts scopeBottleneckCoverage`),
  never a quantity average across unlike materials. The Session and Location
  reports used the average and read **57.7%** where the truth is **7.8%**
  (TRAIN K: 54.6% vs a real 0.4%). Same commit overturned the last
  `Material_Code` pooling holdout — the Smart Calculator now keys stock on
  `(Material_Code, SAP_Code)`. New gate `npm run test:ui-math` (20 checks)
  covers `session.ts` + `insights.ts`, which had no tests at all. See
  `docs/SME_FINAL_MATH_ALIGNMENT_RUNLOG.md`.
- **THE SUBSET RULE (2026-08-05):** in the source workbook `Ordered_Qty` is
  the TOTAL procured for the project and `Available_Qty` is the part of it that
  has ARRIVED — a SUBSET, not a second bucket. Tier 2 is therefore
  `max(ordered − available, 0)` (the PENDING DELIVERY) and the ceiling is
  `max(available, ordered)`. Adding the two double-counted every delivered unit:
  22 of 30 report rows had an understated buy list (22,951 units), and
  GI-8005763 — 143,000 arrived of 143,000 ordered — read 286,000 and hid a
  9,685-unit shortage. Suite **BC** + `test:ui-math` §E; see
  `docs/SME_ORDERED_SUBSET_RULE_RUNLOG.md`. UI wording: "Pending Delivery" for
  the quantity, "When delivered" for the coverage.
- **STRICT TIER SEGREGATION (2026-08-03):** readiness is TIER 1 only.
  `Alloc_Available` drives `Status`, `Completion_Pct`, `SQM_Achievable_Now`,
  `Coverage_Now_Pct` and `Fulfillment_Pct`; `Alloc_Ordered` drives ONLY the
  `*_With_Ordered_*` twins and the net buy list. `Allocated_Qty` (both tiers) is
  a conservation field — dividing it by `Demand_Qty` is NOT a coverage figure.
  The engine always obeyed this; six presentation layers did not, which made 18
  of 85 units show a green "100% Fully Ready" pill and overstated buildable area
  by 9,118 m² (21.5%). Suite **BB** + `sme-tiers.spec.ts`; see
  `docs/SME_TIER_SEGREGATION_RUNLOG.md`.
- **STRICT DECOUPLING (2026-08-02, supersedes the on-order netting):** the SME
  estimator is a separate pool from the ERP warehouse. `available_qty` **is**
  `Initial_Available_Qty` and `ordered_qty` **is** `Initial_Ordered_Qty`, both
  straight from `sme_inventory_seed`; `receipts` / `consumption` / `returns` /
  `inventory` are never read by `/sme/*`. The old
  `effective_ordered = max(Initial_Ordered_Qty − Σreceipts, 0)` existed only to
  undo receipts flowing into availability, so it went with them. Feasibility
  still judges PHYSICAL stock only. Pinned by suite **BA**; see
  `docs/SME_STRICT_DECOUPLING_RUNLOG.md`.
- **The 86 blank-SAP legacy recipe rows are REAL data** — they are disjoint
  from the workbook's coded pairs (measured: zero overlap). The cutover
  keeps them, and a blank-SAP seed row is retired only when no blank-SAP
  recipe line still references it. Deleting them collapsed coverage to 0.0%
  across all 29 equipment once; don't repeat it. The remedy for a mixed
  state is `pg_excel_sync --sme-reseed`.
- **Every table renders through `lib/smartTable.tsx`**, not antd's `Table`
  directly — import `Table` from there. It derives sorters/filters from the
  column definitions and auto-opts-out of server-paginated grids. Explicit
  `sorter`/`filters` on a column always wins.
- **`tools/pg_excel_sync.py` must never import Pandas** (absent from
  `backend/requirements.txt`) and must never duplicate `bulk_import.py`'s
  column mapping — it imports the planners and replaces only the writes.
- After ANY mirror reload: re-run `create_ai_readonly_role.sql` (REVOKEs
  get wiped) AND the Excel sync chain (ARCHITECTURE §1).
- `gi_database.db` stays modified-but-uncommitted at repo root; **never
  stage it**; `*.xlsx` is gitignored (live operator workbooks at root).
- FEFO + over-issue stay **allow-and-log** — never add a hard block.
- Secret-scan every push range for the Meta token prefix (`EAA…`).
- antd v6: Select internals are `.ant-select-content`; virtual Table rows
  are `[data-row-key]`. The Claude preview browser throttles hidden tabs —
  verify via API/DB when clicks won't land.
- service_tests conventions: `check()` helper, unique prefixes
  (SVC6-/SVCJ-/…/SVCO-) with cleanup, per-suite `X-Real-IP` (login
  rate-limit), `GI_DOTENV=0` pin must never be removed.
- **httpx cookie trap:** `client.cookies.set(..., domain="host")` with a
  host-only domain SILENTLY drops the cookie — suite E's replay checks
  passed vacuously for months until suite AQ exposed it. Never pass
  `domain=` in tests.
- **RTR:** refresh tokens are signed JWTs in the same `gi_refresh`
  httpOnly cookie; pre-RTR opaque cookies just force one re-login.
  `refresh_sessions` must STAY in `create_ai_readonly_role.sql`'s REVOKE
  list and `ai/safety.py` FORBIDDEN_TABLES. UUID PKs broke the cutover
  sequence-reset once (MAX(uuid)) — it now targets Integer PKs only.
- **Android builds need JDK 21** (Capacitor 8 hardcodes Java 21); local
  Mac: `JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"`.
- Repo is PUBLIC — `gh` is NOT authenticated on this Mac (CI job logs are
  unreadable anonymously; that's why dual-CI failures now surface as
  annotations). Keep the pre-push secret scan ritual.

## 5. Docs & assets map

- `docs/ARCHITECTURE.md` — the brain. `docs/automatic_test.md` — manual
  test matrix. `USER_MANUAL.md` (repo root) — the single product manual (+ screenshots
  `docs/screenshots/v2/`). `docs/NATIVE_APPS.md` — native build/release/
  install guide (Apple-Silicon fix, Cloudflare-Access bypass, JDK 21).
  `docs/DEBUGGING.md` — sync doctor + gates + console debugging.
  `docs/features/handwritten-ocr/` — OCR spec (preserve-exactly rules).
  `docs/DEPLOY.md` + `deploy/` — infra kit. `tools/migration/README.md` —
  cutover runbook.
- **Run logs (recent programmes, each with rulings + revert-verification):**
  `docs/SME_COMPONENT_POOLING_RUNLOG.md` · `docs/TABLE_TOOLS_RUNLOG.md` ·
  `docs/SME_SQM_BOTTLENECK_RUNLOG.md` ·
  `docs/SESSION_REPORT_SUMMARY_RUNLOG.md` · `docs/PG_EXCEL_SYNC_RUNLOG.md`.
- **`PROJECT_HANDOVER.md`** (repo root) — the fresh-session entry point:
  locked rules (PAST) · baselines (PRESENT) · next phase (FUTURE).
- **Ops handoff PDFs:** `docs/export/` (User Manual + SOP), regenerated via
  `python tools/export_docs_pdf.py`.
- Root `USER_MANUAL.md` / `SOP.md` are the **frozen legacy** docs still
  served in-app by the API; repoint to the v2 manual post-deploy.
