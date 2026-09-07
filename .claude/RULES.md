# GI Hub — the rules an agent breaks without noticing

> Loaded on demand from [`CLAUDE.md`](../CLAUDE.md) line 2.
> [`PROJECT_HANDOVER.md`](../PROJECT_HANDOVER.md) is the authority and carries all
> sixteen rules plus the Phase 10, 11 and 12 rulings; this file restates the
> handful that are broken most often, in the imperative, because each one is a
> real bug whose symptom appeared a long way from its cause.
>
> **None of these are style preferences.** Every one of them was tried the other
> way and broke something measurable, and the measurement is quoted so nobody has
> to re-litigate it.

---

## Rule 15 — the tests do not open the live database

`backend/api/testdb.py` provisions a separate `gihub_svctest` and rewrites
`DATABASE_URL` **before `backend.api.db` is imported**. That import ordering *is*
the entire mechanism: the engine is built at import time from whatever the URL
says at that instant.

* **Never** `import backend.api.db`, directly or transitively, above the
  `testdb` call in a test entrypoint.
* **Never** "fix" a failing suite by pointing it at `gihub`. `DATABASE_URL` now
  supplies only the CLUSTER; its database is never opened, and provisioning
  **exits non-zero** if source and target resolve to the same name.
* If you go looking for a test's rows in `gihub` afterwards, they are not there
  and **that is the fix, not a bug**. `GI_TEST_DB=off` exists for debugging a
  failure that only reproduces against live data; it prints a warning.
  `GI_TEST_DB_REUSE=1` skips the ~1 s rebuild while iterating on one suite.

**Why cleanup was the wrong shape.** Suites B…BX drive the real ASGI app over
httpx and cannot roll back — a request that returns 201 has committed by the time
the assertion reads it, and the commit *is* the thing under test. The suites do
clean up after themselves and it does not help: a run that fails early, is
interrupted, or dies on an uncaught exception skips its own `finally`, and those
are the runs a developer does most.

⚠️ **AND ONE THING RULE 15 SAYS IS NOT TRUE.** It claims the test database is
rebuilt from `gi_database.db` and that "that file is in git … so every machine
and CI start from identical rows". The file is **gitignored** (commit `a09da0b`,
2026-07-26 — it holds real employee names and stock), so `service_tests` has
only ever passed on the operator's laptop. `tools/make_ci_fixture_db.py`
generates what CI needs for `dual_ci` and `parity_check`; the suite's remaining
master-data dependency is an open decision — `docs/PROJECT_STATUS.md` §1b.

### ⚠️ Rule 15's second half, which is easy to miss

**A constraint added in an Alembic migration must be added to `models.py` in the
same commit.** `cutover_migrate.py` builds a production box with
`metadata.create_all`, so a migration-only object is present on every migrated
database and absent from every production one. `ux_asset_transfer_open` — the
partial unique index that stops two sites claiming the same asset — existed only
in Alembic, and that race is silent by nature.

✅ **The companion gap is CLOSED** (2026-08-18, re-verified on a real cut
2026-09-05: 105 tables, 13 data steps run, both Phase 11 tables present with all
six indexes). Cutover stamps Alembic AND then replays every migration's
`data_upgrade(conn)`; `verify_data_migration_contract()` refuses in pre-flight
when a migration carries DML and declares no step.

⚠️ **So the rule for you is: a migration that writes ROWS must expose
`data_upgrade(conn)` and call it from `upgrade()`.** Both paths then run the
same code and cannot drift. The verifier catches raw SQL, `op.bulk_insert` and
SQLAlchemy Core insert/update — suite BZ-12a pins all four forms.

---

## Rule 1c — the SME subset rule (and both engines change together)

`Ordered_Qty` is the **TOTAL procured for the project**. `Available_Qty` is the
part of it that has **physically arrived**. Available is a **subset** of ordered,
never an addition to it.

```
tier 1  = available_qty
tier 2  = max(ordered_qty − available_qty, 0)      ← the PENDING DELIVERY
ceiling = tier1 + tier2 = max(available, ordered)
to buy  = max(demand − max(available, ordered), 0)
```

Adding them double-counts stock already on the shelf. Measured: it understated
the buy list by **22,951 units across 22 of 30 materials**, and on `GI-8005763`
— where all 143,000 ordered units had arrived — it read 286,000 and reported
**nothing to buy** against a demand of 152,685.

**Rule 1b travels with it: the UI never merges the two tiers.** Tier 1 alone
drives every "can we build it today" answer (`Status`, `Completion_Pct`,
`SQM_Achievable_Now`, `Fulfillment_Pct`). Tier 2 feeds only the
`*_With_Ordered_*` twins and the net buy list. `Allocated_Qty` (= tier 1 + tier 2)
is a **conservation field** so that `Demand = Allocated + Shortfall` — it is
**not** a coverage numerator and nothing may colour it green. The engine always
had this right; six presentation layers did not, and overstated buildable area by
**9,118 m², 21.5 % of the programme**.

### ⚠️ The standing one: both engines change together

`backend/api/sme_engine.py` and `frontend/src/sme/engine.ts` are line-for-line
mirrors, proven equal by `npm run parity:sme` (1,313 comparisons). **Any numeric
change edits BOTH and regenerates the golden, in ONE commit.**

**And rule 1 sits underneath all of it:** the component key is
`(Material_Code, SAP_Code)` — `mat_key()` / `Material_Key`. Never pool by
`Material_Code` alone. Pooling summed four unlike drums of a multi-part chemical
system into one bucket and *inverted* the shortfall: components A and B reported
fully covered while both were 10 short, D reported 10 short while holding three
times what it needed. `Material_Name + UOM` is **not** a valid discriminator —
all four PU rows share a name and a UOM, and the UOM disagrees on 25 of 32 pairs.

---

## Rule 13 — the docs are part of the Definition of Done

* A feature change updates [`MANUAL_TESTING_GUIDE.md`](../MANUAL_TESTING_GUIDE.md)
  **in the same PR**. It is also the fastest way to learn what the system
  actually promises, because it states the WHY for every behaviour.
* `USER_MANUAL.md` **at the repo root is the only manual**. It is the AI corpus,
  the in-app PDF and the ops PDF. A second `docs/USER_MANUAL.md` existed for a
  month and fell four phases behind; suite CJ fails if it comes back.
* **A role added to `auth.ROLE_META` must be added, in the same commit, to
  `ai/manual_qa._ROLE_ALLOWED` and `build_manual_pdf.ROLE_MANUAL_RECIPES`.**

⚠️ **This one is invisible when you get it wrong.** `allowed_sections()` falls
back to the **store keeper's** allowlist for an unknown role. That fails *safe*,
which is exactly why nobody noticed: the QSEP release added `qc` and did neither,
so a Quality inspector asking about inspections was answered out of the Store
Keeper chapter, told anything else was "not in your section", and had no printed
booklet. Regenerate PDFs with `.venv/bin/python build_manual_pdf.py --role all`.

---

## Rule 14 — navigation access is a ROLE MATRIX, and it fails closed

* A page names **the jobs that need it** (`anyRole`). `minLevel` is a seniority
  ladder and the roles are not one.
* `canAccessPath` **refuses any path it does not recognise**.
* `npm run test:nav` fails the build when a route has no rule (50 routes, all
  claimed).
* **Narrowing a page means narrowing its endpoints in the same commit — a menu
  is not a control.**

**Rule 7 is the same idea one layer down.** The Auditor's view-only status is
enforced **once, by ASGI middleware keyed on the HTTP method**
(`backend/api/readonly.py`), never per-endpoint. Every `POST/PUT/PATCH/DELETE`
from an `auditor` is refused unless it is on a small documented allowlist (126 of
143 mutating routes blocked). That shape is the point: a per-endpoint check is
only as good as the developer who remembers it, and the one that gets forgotten
**fails open**. **If you add an endpoint and it 403s for an auditor, that is
correct.**

---

## Rule 9 — the assistant RETRIEVES, and the fence runs before the score

* `manual_qa.allowed_sections(role)` filters chapters **before** BM25 scores
  them (`Index.search(allowed=…)`). **That is the security boundary — not the
  system prompt.** A role's context cannot physically contain a chapter it may
  not see. Never move the filter downstream, and never add a "topic filter" that
  becomes a second, weaker boundary people trust instead of it.
* **Unknown roles fall back to the LOWEST allowlist, never the highest** — a typo
  in `users.role` must lose access, not gain it.
* **Never parse `USER_MANUAL.md` chapters with a bare `^# \d+\.` match.** §17
  documents shell scripts whose comments read `# 1. Pull the new code`, and a
  line-by-line matcher read those as chapters 1–4 — overwriting Introduction,
  Roles & Permissions, Login and the Store Keeper Manual **for every role**, and
  putting `launchctl` and a developer's iCloud paths into a Store Keeper's
  context. Use `ai/manual_index.iter_chapters()`, which is fence-aware.
  `build_manual_pdf` keeps its own copy of the fence logic (it must stay
  importable without the backend package); suite BE pins both.
* The retrieval fallback (used only when nothing scores) keeps whole `##`
  sub-sections up to 3,000 chars and **never truncates §2 at all**. At the old
  800-character head truncation the access matrix — which starts ~1,900
  characters in — was in **no** non-admin prompt, which is why the assistant
  inferred that HODs could not open the Manpower portal.

---

## Rule 16 — the harness is audited, and a SKIP is not a PASS

*Locked 2026-09-02.*

`bug_check.py` reports **`N passed, N failed, N skipped`**. A skip is a third
status: counted, printed, listed in its own report section, and surfaced in CI
as a `::warning::`. **Never fold one into the pass total.**

It exists because `QR Badges` reported `2/2` for a round-trip assertion that had
**never executed on any machine** — macOS raised `ImportError` (swallowed by
`except ImportError: return`) and Linux raised `TypeError` from a
process-wide `subprocess.Popen` stub that broke `ctypes.util.find_library`. Same
absent library, opposite outcomes, and CI red for 30 consecutive runs while the
run page blamed a passing WhatsApp test.

* **`bash bin/ci_preflight.sh` runs FIRST**, in about a second. It audits the
  TEST HARNESSES, not the app: process-wide stdlib monkeypatches, unrestored
  module patches, one-exception dependency guards, silent no-ops, unrestored
  sender mocks. Ten negative controls prove each rule still fires on its bug and
  stays quiet on its fix.
* ⚠️ **On macOS the QR check SKIPS** unless you
  `export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`. With it: 599/0/0.

---

## The twelve Phase 11 AI rulings (P11-1 … P11-12)

Full text in `PROJECT_HANDOVER.md` → *Phase 11 rulings, LOCKED*. The four an
agent is most likely to undo:

| | Do not |
|---|---|
| **P11-4** | Treat `ai/guard.py` as the security boundary. **Rule 9 is.** The danger is not that the guard is bypassed — it is that somebody simplifies the fence *because* the guard exists. |
| **P11-7** | Drop `role` or the manual hash from the answer-cache key. Two roles are shown different chapters and are entitled to different answers; a question-only key serves one the other's, invisibly. |
| **P11-9** | Widen cloud fallback past vision, or make a TIMEOUT fall back. A read timeout means the model was healthy and still generating — uploading a page because our stopwatch expired is egress out of impatience. |
| **P11-10** | Move `num_ctx` into the routing policy or a gateway library. It is a computation over the image in hand; getting it wrong aborts the Ollama runner and kills every queued job. |

---

## The seven Phase 12 rulings (P12-0 … P12-6)

Full text in `PROJECT_HANDOVER.md` → *Phase 12 rulings, LOCKED*. Phase 12
produces **role video tutorials**: a Playwright screencast of the real UI plus a
HeyGen avatar, composited locally into the training hub slice 10b built.

⚠️ **P12-0 is the one to read, and it is not about the API.**

> **A tutorial is recorded against SYNTHETIC data
> (`tools/make_tutorial_db.py`), or it is not published.**

The brief asked how to stop proprietary data reaching HeyGen. That is the easy
half and one function closes it. The hard half is that **the MP4 is the
sensitive artefact** — a file people forward, put on a phone and send to a
contractor — and it is sensitive whether or not it ever touches a cloud.
`tests/e2e/global-setup.ts` loads its throwaway database from the REAL
`gi_database.db`, so the prototype's first frame carried real employee names,
real material descriptions, real SAP codes and real quantities.
**The redaction hook is a SECOND LINE, not the boundary** — it only masks what
somebody thought to name.

| | Do not |
|---|---|
| **P12-1** | Replace the payload's provenance whitelist with a scrubber. A scrubber asks "does this look dangerous?"; the whitelist asks "did a person approve this exact sentence in a diff?" and is complete on day one. It refused its own first payload. |
| **P12-2** | Send the SCREENCAST anywhere. The avatar comes back as a clip and ffmpeg composites LOCALLY. This rules out HeyGen's *video translate* and *avatar from a screen recording* — those are not integrations, they are the exact egress this design prevents. |
| **P12-3** | Let the real model answer during a recording. It answers differently every render, so a re-render would silently change what the video TEACHES — and this model invents UI no chapter describes when it is unsure. |
| **P12-4** | Give the recorder its own stack builder. It reuses the E2E lifecycle on its own database and ports; a second builder is a second place for rule 15 to be got wrong, quietly. |
| **P12-5** | Drop `script_sha256` from the manifest, or make the dataset non-deterministic. Ruling Q4 bumps a compliance version from that hash; a dataset built from `date.today()` would move every number on screen while the hash said nothing changed (P10-6). |
| **P12-6** | Re-implement the deep-link role filter. It reuses `Index.search(allowed=…)` — rule 9's own fence, applied BEFORE the score. And a manifest with no `audience` falls back to the recorded role, never to everyone. |

⚠️ **And the matcher's second floor is a RULE, not a threshold.** A candidate
must clear `MIN_SCORE` **and** share **two distinct tokens** with the beat it
won on. `manual_index._tokens` expands synonyms, so one accidental word on a
2.4× boosted heading scored 4.95 over a floor of 4.0 — and raising the floor to
kill it also killed a real hit scoring 4.59. One word in common is a
coincidence; two is a topic.

⚠️ **Three things about the video toolchain that FAIL SILENTLY on this
machine**, all of them rule 16 in a new domain:

* Homebrew's ffmpeg 8.1.2 has **no `drawtext`** — captions are Pillow PNGs.
* It **discards video alpha without a warning**: `-pix_fmt yuva420p` exits 0
  and writes `yuv420p`. The `pix_fmt` is read back; an exit code is not a result.
* Playwright's `recordVideo.size` **does not scale the page** — a smaller
  viewport is drawn 1:1 at the top-left with black over the rest.

---

## Rulings that look like oversights and are not

Full list in `PROJECT_HANDOVER.md` → *Phase 10 rulings, LOCKED*. The four an
agent is most likely to "improve":

| | Do not |
|---|---|
| **P10-1** | Replace the Postgres-backed limiters (`rate_buckets`, `login_attempts`) with Redis. It is a new daemon and a new 3 a.m. failure mode, for counters that tick a few times a minute. |
| **P10-2** | Make the fail directions "consistent". The rate limiters **fail open**, the daily-job claim **fails closed**, and the access matrix **fails closed** — three different answers, each correct for its own blast radius. |
| **P10-4** | Value un-costed inventory at zero. Every `Unit_Cost` on the live data is 0; summing them produces `SAR 0.00` for a site holding 731 units — arithmetically correct and a lie a board would act on. Count them as "Not Valued (N items)" and label the total a **floor**. |
| **P10-7** | Wire a stochastic (model-answer) eval into CI. Tier 1 audits the prompt and is deterministic, so it gates. Tier 2 audits the answer and is not, so it does not — a flaky gate is one people re-run rather than read. |

**And the ones that are deliberately soft:** FEFO and over-issue are
**allow-and-log**, never a hard block. The QC block (`assert_qc_cleared`) is
about QUALITY STATUS on 36 SAPs and does **not** overturn FEFO — never implement
one by promoting the other's warning to an error. Receiving is **never** refused:
a receipt states that something physically arrived, and refusing to record it
does not un-arrive it.

---

## ⚠️ Four workers

`uvicorn --workers 4` has produced three production bugs — the OCR orphan sweep
reaped other workers' live jobs, three daily loops dispatched four copies of
every message, and four rate limiters were 4× looser than their own
documentation. **If you add anything that runs on a timer or holds state in
memory, assume four of it.** Shared state goes in Postgres; a job that must run
once takes the `daily_job_runs` claim.

---

## Gates

Run these before saying anything is done. Baselines as of 2026-09-03.

```bash
# Harness hygiene — ~1 s, no services. Runs FIRST in CI for a reason: it catches
# process-wide stdlib monkeypatches and silent skips, the class of defect that
# hid a real bug_check failure behind an innocent log line for 30 CI runs.
bash bin/ci_preflight.sh
```
```bash
# Backend service tests — 2,328 checks, suites A…CX, its OWN throwaway DB (rule 15)
GI_DOTENV=0 DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub \
JWT_SECRET=ci-only-service-test-secret-key-32bytes-min \
.venv/bin/python -u -m backend.api.service_tests
```
```bash
# SME maths — the dual-engine parity oracle (1,313 comparisons) and the UI math (33)
npm run parity:sme --prefix frontend
npm run test:ui-math --prefix frontend
```
```bash
# Route-to-endpoint alignment (rule 14) — 50 routes, every one must be claimed
npm run test:nav --prefix frontend
```
```bash
# AI guardrail audit — Tier 1 (147/147, 0 leaks) is the hard gate and also runs
# inside suite CQ. Tier 2 needs a live model, is stochastic, and NEVER gates.
.venv/bin/python -m tests.ai_eval.runner
.venv/bin/python -m tests.ai_eval.runner --tier2 --json scorecard.json
```
```bash
# AI eval — Tier 1 (147 cases) AND the deterministic retrieval gates (>= 0.85).
# Needs no model. The grid is GENERATED; --check fails if the manual moved.
.venv/bin/python -m tests.ai_eval.runner
.venv/bin/python tools/gen_eval_grid.py --check
```
```bash
# Frozen Streamlit regression suite — self-rooted, 599 checks.
# ⚠️ On macOS the QR round-trip SKIPS without this (rule 16): 598/0/1 vs 599/0/0.
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python legacy/bug_check.py
```
```bash
# Frontend build + typecheck
npm run build --prefix frontend
```
```bash
# Headless E2E — 128 specs, ~55 s, builds and destroys its own gihub_e2e_pw stack
cd tests/e2e && npm test
```
```bash
# Alembic must have exactly one head
.venv/bin/python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; c=Config('backend/alembic.ini'); c.set_main_option('script_location','backend/alembic'); print(ScriptDirectory.from_config(c).get_heads())"
```

**Two notes on reading a green run:**

* ⚠️ **A SKIP is not a PASS.** `bug_check.py` reports
  `N passed, N failed, N skipped` and lists every skip with its reason. On macOS
  the QR round-trip skips because Homebrew's libzbar is not on dyld's search
  path; `export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` makes it run for
  real (599/0/0). CI installs `libzbar0` and runs it either way. Before
  2026-09-02 that skip was silently counted as a pass and the assertion had
  never executed on any machine.
* `tools/parity_check.py` **fails against the live mirror by design** —
  PostgreSQL is permanently ahead of the frozen SQLite. It is meaningful only on
  CI or a freshly-cutover database.
