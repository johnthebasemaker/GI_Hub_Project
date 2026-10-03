# MORNING REPORT — Phase 18 "Night Shift" (2026-10-03 → 04)

> Good morning. All four tracks are **built, tested and committed** on
> `feat/phase18-night-shift`: 5 commits on top of `main` 45ddff2.
> **Nothing is pushed or merged.** You asked me to commit, and merging
> without the PR's own CI run is the lesson from Phase 16 (RULES.md). §1 is
> what was built, §2 is what I found, §3 is what I need you to decide, and §4
> is three ideas for today. The full design and every measurement are in
> `PROPOSED_PHASE18_PLAN.md`.

---

## 1. What was built

| # | Track | Result | Commit |
|---|---|---|---|
| 1 | **Router: attack block + latency** | Dev block **0.667 → 0.949** (target 0.95). Holdout, the honest number, **0.316 → 0.368**. Routing unchanged at 0.95. Plain how-to questions now skip the model (routing-mix p50 **0 ms**; the model path itself is unchanged at ~350 ms on Metal). One shipped false refusal fixed. | `fdc2025` |
| 2 | **`ai-router-eval` is a hard gate** | `continue-on-error` removed. L3 floor raised 0.60 → 0.90. | `c2bf6db` |
| 3 | **Return desk** (Store Keeper returnables) | Scan a badge, tool sticker, serial or `#id` → its loans; return with a condition (Good / Damaged / Parts missing, plus a note); damage notifies the HOD. Beep/vibration/flash feedback; the box keeps focus for keyboard-wedge scanners; KPI tiles; due-back presets. **Bug fixed: overdue loans were flagged 3 hours late.** The sign-in page is **45 KB lighter** (gzipped). | `a6f0918`, `e5c5f42` |
| 4 | **Intelligent minimum stock** | Stock → **Reorder signals** (red / amber / green, minimum, days of cover, on order, suggested order, and a *why* in words), plus a Dashboard summary. General items use the site's consumption; Surface Shields use the **SQM plan** (Old/New Garnet included), never past use. A manual minimum always wins. **Nothing is written to the database.** | `a7d0699` |

**Every gate is green on the branch:** preflight · service_tests **2,834/0**
(+31 new checks: suites 18A, 18R, 18M) · E2E **183** (+4: `returnables.spec.ts`,
`reorder-signals.spec.ts`) · AI Tier 1 147/147 · Router L2 ✅ · **Router L3 ✅**
(run twice, identical) · grid 72 · parity:sme 1,334 · ui-math 33/0 · nav 52 ·
bug_check 599/0/0 · build + critical path ✅ · single alembic head `a7d3e1f5c829`.

**Practice is in step (rule 17g).** Both Practice DBs are migrated, and overlay
v5 is applied: four loans (badge **900002** = Tomas's two-tool kit,
**PR-TW-0001** = an overdue wrench, a drill returned Damaged) and a
red/amber/green reorder trio (899981–899983). Both manuals and the testing
guide are updated: USER_MANUAL §3.12, §3.13 and §4.5.3; MANUAL_TESTING_GUIDE
§18a, §18b and §18c.

### ⚠️ Before you run the branch
**Live is not migrated.** On this branch the Live API will refuse to boot until
you take a backup and run:

```bash
./bin/backup_db.sh
```
```bash
cd ~/GI_Hub_Project/backend && ../.venv/bin/alembic upgrade head
```

The migration only adds six empty, nullable columns to `returnable_items`.
`main` is unaffected.

### How the night went (honestly)
* **Track 1 took the most time, and the obvious fix failed.** Asking the 1.5B
  model for a "second look" at suspicious messages was built three ways and
  rejected:
  - a second prompt evicts Ollama's single prompt cache (46 → 510 ms);
  - a follow-up question made it answer "attack" for everything;
  - one sentence of rewording moved its detection from 16/22 to 3/22.

  So the gain is **deterministic**: combination patterns in the guard,
  checked against 82 legitimate twin sentences, 24 of them new.
* **Two regressions of my own were caught by the gates before any commit
  could hide them:**
  - a class-name collision that made stock returns return a 500 (caught by
    E2E W1c, fixed);
  - an "order twice the whole project" suggestion (caught from an E2E
    screenshot, fixed and pinned by test 18m-05b).
* **One went into a commit red:** the Track 3 commit failed bug_check's
  schema-parity check, because I ran service tests + E2E before that commit,
  not bug_check. It is fixed in its own commit (`e5c5f42`) rather than
  rewritten, so the history shows it.

---

## 2. Bugs and architectural issues found during the night

| # | Finding | Status |
|---|---|---|
| 1 | **Tool-loan overdue ran 3 h late.** `/entry/returnables` and the nav badge compared local due times with UTC wall-clock; the health monitor used local time, so the three disagreed. Proven: suite 18R fails on the old clock. | ✅ Fixed |
| 2 | **The assistant refused a legitimate question.** *"How do I wipe the column filters on the receipts table?"* warned on `sql.destructive_intent`, and the router's `is_safe:false` then refused it. | ✅ Fixed (guard v3) |
| 3 | **The router's SQL-lane veto still refuses one legitimate command.** The model reads *"Wipe the saved filters on my stock table"* as data + unsafe, and Q17-2 refuses it with no pattern hit at all. That is 1/82 twins (gate ≤ 2 %). | ⚖️ Needs a ruling (§3 Q4) |
| 4 | **Ollama keeps ONE prompt cache** (`OLLAMA_NUM_PARALLEL=1`). Any other prompt sent to the router model evicts its prefix. `warm()` also loaded with no system prompt, so the first question after every warm paid ~0.5 s (seconds on CPU). | ✅ `warm()` fixed; rule P18-prefix added |
| 5 | **jsQR was on the sign-in critical path**, because the header QR scanner was imported eagerly. | ✅ Lazy-loaded: −131 KB raw |
| 6 | **`main` has no branch protection** (`gh api …/protection` → 404). No CI job, including dual-ci, can block a merge. | ⚖️ §3 Q1 |
| 7 | **The holdout set is no longer blind.** Every CI scorecard prints the ids of missed holdout cases (`ho.atk.inject.json`…), and ids name categories. I saw them before designing v3. I did not read the prompts or tune on them, but the holdout is weaker now. | ⚖️ §3 Q5 |
| 8 | **`lining_analytics.py`** (Lining Coverage) compares ledger stock in **packs** with recipe demand in **KG**, and keys by `Material_Code` alone (rule 1). | ❌ Not fixed: different page, needs its own PR |
| 9 | **`inventory_site_overrides`** (a per-site minimum table) exists in the schema, but no code reads or writes it. **`low_stock_days` and `burn_alert_days`** are admin-editable in the new stack, but only the frozen legacy portal ever read them (verified with `rg` over `backend/`, `frontend/src`, `tools/`). | Reported (and see idea §4.2) |
| 10 | **The test snapshot is staler than Live.** Its `sme_recipe` has no `SAP_Code` (Live gained them in July), and the E2E fixture has negative stock (`E2EGAR-1` = −3). Smart-min resolves SAPs through `Material_Code` for this reason. | Reported |
| 11 | **The Practice tutorial dataset has fixed dates** (P12-5), so anything windowed (the 90-day reorder signals) decays as time passes. | Mitigated: a Practice trio dated from today |
| 12 | `entry.py` is ~1,400 lines with two "return" concepts (stock returns, tool loans) in one namespace. That is how my `ReturnIn` shadowing happened. | Suggest splitting `loans.py` out |
| 13 | `service_tests` cannot run one suite. I used a scratch runner all night; a `GI_SUITES=18R,18M` filter would save minutes per iteration. | Suggestion |

---

## 3. Questions for you — answer these and I will adjust

Each lists **what I did by default** (⚖️) and the alternatives.

**Q1 — Branch protection.** "Hard gate" today only means "goes red". Shall I
mark `dual-ci`, `ai-router-eval` and `frontend-build` as **required checks** on
`main`? ⚖️ I did not change repository settings unattended.

**Q2 — Router probation.** You ordered the gate hard. The record was **4/4**
green runs (Q17-3 asked for 10). Keep it, or let it finish 10 runs first? ⚖️
Hard, as you ordered.

**Q3 — The 24 new twins on CPU.** They have never run on CI's CPU, and one
CPU-only false refusal would make the now-hard gate red. ⚖️ My suggestion:
push the branch and read the scorecard before merging.

**Q4 — Q17-2's SQL-lane veto.** It refuses an allow-tier sentence when the
1.5B model alone says "unsafe" (finding #3). Options:
- **(a)** keep it (1/82 false refusals);
- **(b)** veto on the SQL lane only with a pattern hit too;
- **(c)** veto, but only for roles that can reach the SQL lane.

⚖️ (a), unchanged.

**Q5 — A new holdout.** Will you write a fresh `security_holdout_v2.yaml`
(about 20 attacks + 20 twins, including **warn-tier twins**)? It must come
from you, not an agent, or it stops being a holdout. Should I also stop CI
printing holdout case ids (counts only)?

**Q6 — Surface Shield minimum with no SQM pace.** No approved execution work
in the last 30 days means the minimum is the **whole remaining plan**, so most
Surface Shields show red. Options:
- **(a)** keep it;
- **(b)** you set `ss_planned_sqm_per_day` per site (now global: per-site needs a small change);
- **(c)** use a default planning rate.

⚖️ (a), with a yellow note on the page saying so.

**Q7 — Garnet assumption.** Every m² still to be lined is blasted first, at the
equipment's last Old/New answer, or the **higher** rate if never answered.
OLD has no benchmark set yet, so the NEW workbook rate is used. Correct? And
will you set the OLD rates (SME → Prep baseline)?

**Q8 — Cover days and the formula.** Is **30 days** right for both kinds (lead
time + safety)? Should Surface Shields have their own lead time? Amber = within
50 % above the minimum; suggested order = up to **2 × minimum**. ⚖️ All admin
settings except the 1.5 / 2 factors.

**Q9 — On order.** Open POs are matched through `po_items.Material_Code` and
are **not per site** (`po_items` has no site). Is there one buying site, or
should POs carry a site?

**Q10 — Apply the recommendations?** The signals are advice, and nothing
writes `Minimum_Qty`. Should the HOD be able to **accept** a recommended
minimum per site? `inventory_site_overrides` already exists for this; see
idea §4.2.

**Q11 — Return desk defaults.**
- **One-scan return** (a single-match tool scan returns it in good order with
  no second press) is **OFF** by default. Should it be on?
- A **damaged return** notifies the HOD but **does not adjust stock**. Should
  it stage a write-off adjustment?
- **Partial returns** (3 of 5) are still not modelled. Are they needed?

**Q12 — Loan slips.** Should a loan print a small **QR slip** (`#57`) for the
borrower, so the return is one scan even without a badge?

---

## 4. Three ideas worth building today

### 4.1 A semantic safety layer from a model you already have (`nomic-embed-text`)
The 11 holdout attacks that score 0 are paraphrases, which regexes cannot
generalise to and the 1.5B judge is too unstable to catch. Embed a labelled
bank of attacks and twins once (the dev set, plus rejected traces, never the
holdout). At request time, take the **k-nearest neighbours** of the question's
embedding.
- Deterministic, about 20 ms, no new model (it is already pulled and tiny).
- It fits Q17-2's shape as one more *signal*: "near 3 attacks, 0 twins".
- Each refusal can name its nearest examples in the trace.

This is the most promising path from 0.37 toward 0.95 on the holdout without a
bigger model.

### 4.2 Close the reorder loop: accept a minimum → per-site override → auto-drafted PR
1. The HOD **accepts** (or edits) a recommended minimum per site. Store it in
   the unused `inventory_site_overrides`, with who, when and from which
   recommendation (an audit trail of planning decisions).
2. Point the existing `/hod/prs/auto-draft` (today: manual minimums, 30-day
   burn) at `smart_min`, so red rows become a **draft PR** with the suggested
   quantity and the *why* on each line.
3. Logistics approves as today.

Store a nightly snapshot of the signals (`daily_job_runs` claim, one worker)
and you also get a **stock-out forecast trend**: how many reds this week
versus last.

### 4.3 One scan service and one sticker standard for everything
There are now five scan resolvers, each with its own parsing:
`barcode.ts`, `stock._scan_tokens`, `assets/resolve`, `returnables/resolve`
and the consumption-form `GIF2|…` parser. Merge them into one
**`GET /scan?code=`** that returns a typed answer, plus the role-filtered
actions available for it: *material → open card / issue / receive*;
*loan → return*; *lot → FEFO status*; *roll → its batch*;
*employee → loans + PPE*; *form → OCR job*.

Give every new printed label a self-describing payload (`GI1|LOAN|57`,
`GI1|LOT|899971|PR-SOON`, `GI1|ASSET|T-0042`). A scan then names its kind with
no guessing, and the header QR button can do the right thing anywhere. The
`ScanBox` component from Track 3 is the ready-made front end for it.

*(A fourth, smaller one: `SQL_SITE_STOCK` re-sums the whole ledger on every
call — the reorder signals do it per request. A `stock_balance` table
maintained by `services/ledger` and reconciled nightly would make every stock
page and signal O(items) instead of O(ledger rows).)*

---

## 5. Where everything is

| What | Where |
|---|---|
| Plan, measurements, every default | `PROPOSED_PHASE18_PLAN.md` |
| Orientation for the next session | `SESSION_HANDOVER.md` (rewritten: §0, §4.5, §5, §8, §10) |
| Decisions now in the authority | `PROJECT_HANDOVER.md` → *Phase 18 — Night Shift* |
| Rules for agents | `.claude/RULES.md` → *Phase 18 additions* |
| Manual tests | `MANUAL_TESTING_GUIDE.md` §18a (router) · §18b (return desk) · §18c (reorder) |
| User docs | `USER_MANUAL.md` §3.12, §3.13, §4.5.3 |
| Commits | `git log --oneline main..feat/phase18-night-shift` |

**Services left as found:** Postgres up (it was up at the start). Ollama was
started for the router evals and stopped again (it was off at the start). No
dev server is running, and nothing touched the root cloudflared daemon or the
Live database.
