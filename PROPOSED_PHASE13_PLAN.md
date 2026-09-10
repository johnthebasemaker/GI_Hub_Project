# PROPOSED PHASE 13 PLAN — Bulk Execution Forms · Video Playback UX · Inventory⇄SME Unification

> **Status:** ⛔ **ANSWERS LOCKED by the operator 2026-09-10.** Every question in §9 is
> decided; the answers are folded into the sections they govern and restated verbatim in
> §9.0. Slices **13a–13d are approved to build**. Track 3 (13e/13f/13g) is planned in full
> and waits for a separate green light.
> **Written:** 2026-09-09, against `main` @ `6478456` (Phase 12 merged, nothing mid-flight).
> **Answers folded in:** 2026-09-10.
> **Authority:** [`PROJECT_HANDOVER.md`](PROJECT_HANDOVER.md) wins over this file wherever they disagree.
> **Baselines this plan must not move:** `ci_preflight` 10 controls · `service_tests` 2,328/0
> (suites A…**CX**) · Playwright 128/128 · AI eval Tier 1 147/147, recall 1.000 / precision 0.994 ·
> `parity:sme` 1,313 · `ui-math` 33/0 · `bug_check` 599/0/0 · nav 51 routes · alembic single head
> `a1c9e64b3d70`.

---

## 0. Contents

| § | |
|---|---|
| [1](#1-the-one-sentence-verdict-on-each-track) | The one-sentence verdict on each track |
| [2](#2-track-1--bulk-execution-form-generation) | Track 1 — bulk form generation with unique QRs |
| [3](#3-track-2--hub-assistant-video-playback-ux) | Track 2 — why the "Watch it" button lands on nothing |
| [4](#4-track-3--unifying-sme-and-inventory-surface-shield-consumption) | Track 3 — the core rule change, and the four ways it can go wrong |
| [5](#5-risk-register-against-the-locked-rules) | Risk register against the locked rules |
| [6](#6-sequencing--seven-slices) | Sequencing — seven slices |
| [7](#7-gates-and-how-each-track-is-pinned) | Gates, and how each track is pinned |
| [8](#8-visual-verification-strategy) | Visual verification strategy |
| [9](#9-clarifying-questions--all-answered-and-locked-operator-2026-09-10) | ⛔ The locked answers |
| [10](#10-execution-status) | Execution status |

---

## 1. The one-sentence verdict on each track

**Track 1 is small and safe.** The renderer already paginates and the registry already
mints one `Form_UUID` per download. Bulk is a loop plus a batch id, and the only real
decision is what the word "page" means when a recipe has more than 18 materials.

**Track 2 is not a routing bug.** The deep link works exactly as designed. It lands on a
page that truthfully says the video is not published, because **no `training_assets` row
exists for any module and three of the four rendered tutorials have no `training_modules`
row at all**. The work is a publish path and a byte-serving route, not a UI fix.

**Track 3 amends a locked rule (1a, STRICT DECOUPLING, locked 2026-08-02), and the
operator has ruled how far.** ⛔ Resolution **B**: a `Consumed_Qty` column for visibility
that leaves readiness and `Allocated_Qty` untouched. The feature is buildable and largely
already scaffolded — `sme_consumption_log` + `sme_actuals.py` is the same shape — but the
sentence *"it MUST reflect in the SME quantity as consumed"* has two readings that differ
by roughly the whole readiness surface of the Estimator, and one of them creates a
self-feeding loop with `execution.post_stock`.

---

## 2. Track 1 — Bulk Execution Form Generation

### 2.1 What exists today

`GET /execution/forms/{system_code}` ([backend/api/execution.py:428](backend/api/execution.py:428))
is a **GET that writes**. Every download calls `consumption_form.generate()`, which mints a
`Form_UUID`, inserts one `sme_consumption_form` row, hashes the recipe into
`Recipe_Fingerprint`, then renders. The registry row rides back on the
`X-Form-UUID` / `X-Form-Rows` response headers.

Three facts that shape the whole track:

- **The renderer already paginates.** `render_pdf` computes
  `pages = ceil(len(rows) / 18)` and calls `_page_header(page_no)` per page. A recipe of
  40 materials is already a 3-page PDF today.
- **Every page of that PDF carries the SAME QR**, because the QR is drawn from one
  `qr_payload(form_uuid=…)` computed once, outside the page loop
  ([consumption_form.py:280](backend/api/services/consumption_form.py:280)).
- **The QR payload is `GIF1|site|system|sub-activity|uuid`** — five fields, version-tagged,
  deliberately not JSON because a QR's capacity is the binding constraint.

The UI is `FormPrintCard` in [ExecutionPage.tsx:939](frontend/src/pages/ExecutionPage.tsx:939):
a system picker, an optional sub-activity picker, and a Download button.

### 2.2 The design

Add `copies: int = Query(1, ge=1, le=200)` to the download endpoint and a **"How many
forms?"** number input before the Download button (the brief said "how many pages"; §2.3
is why the label changed).

`copies=50` mints **50 registry rows, 50 UUIDs, 50 QRs**, and concatenates 50 renderings
into one PDF stream. The registry rows are written in **one transaction**: 50 sheets that
half-registered would leave paper in a plant that the intake side refuses.

```
generate_batch(session, site, code, esc, n)          # new, in consumption_form.py
   └─ for i in 1..n:
        form_uuid = uuid4()
        INSERT sme_consumption_form(..., Batch_UUID=b, Batch_Seq=i, Batch_Size=n)
        render_pdf(rows, form_uuid=form_uuid, seq=(i, n))
   └─ merge the n PDFs into one byte stream
```

**Two columns are added to `sme_consumption_form`** (migration + `models.py` in the same
commit — rule 15's second half):

| Column | Why |
|---|---|
| `Batch_UUID` | The thing the operator prints, hands to a supervisor and later asks about. Without it, "where did the 50 sheets I printed on Tuesday go?" is unanswerable — the registry would hold 50 unrelated rows. |
| `Batch_Seq` | Printed on the page as **"Sheet 7 of 50"**, so a human can sort a pile of paper. Also what makes a missing sheet visible in `/execution/forms/generated`. |

`Batch_Size` is derivable from `Batch_Seq` but is stored anyway, so a partially-deleted
batch still says how big it was meant to be.

### 2.3 ⚠️ The word "page" is ambiguous and the ambiguity is expensive

A recipe with ≤ 18 materials is one A4 sheet. **LSC8-style systems are not.** If a system
has 40 recipe lines, one *form* is three *sheets of A4*.

So `copies=50` means one of two things:

- **(a) 50 FORMS** → 50 unique QRs → for a 40-line recipe, **150 sheets of A4**.
- **(b) 50 SHEETS of A4** → for a 40-line recipe, 16 forms and 2 wasted pages.

⛔ **LOCKED (Q13-1): (a) — 50 FORMS.** The input is labelled "How many forms?" and carries
live helper text of the shape `50 forms × 3 pages = 150 A4 sheets`. Reason: the QR
identifies a FORM, the fingerprint pins a FORM's row order, and slice 9d's intake consumes
a FORM. Counting in sheets would make the user's number mean something the system has no
concept of.

### 2.4 ⚠️ Multi-page forms and the intake side

If (a) is chosen, a multi-sheet form still has **one QR repeated on each of its own
sheets** — which is correct, because those sheets are one form — but the 9d intake reads
a photograph of **one** page. Today a supervisor photographing page 2 of a 3-page form
gets the same `Form_UUID` as page 1, and `form_intake.validate_sheet`'s *"already filed"*
refusal will reject the second photograph.

That is a **pre-existing defect that bulk printing will make common**, because bulk is what
makes long recipes practical to print. Two options:

- **(i) Extend the QR payload to `GIF2|site|system|esc|uuid|seq|of`** and let intake match
  positionally within the sheet. `parse_qr` already refuses an unknown prefix rather than
  guessing, so a `GIF2` decoder is a clean version bump. Cost: `Row_Index` mapping in
  `_match_rows` must offset by `(seq-1) × 18`.
- **(ii) Cap a form at 18 lines** — one system, one sub-activity, one sheet — and require
  a sub-activity selection for any system over 18 lines.

⛔ **LOCKED (Q13-2): (i) — the payload becomes `GIF2` and carries the sheet sequence.**
(ii) would hide a real recipe behind a UI restriction and refuse LSC8 outright. `parse_qr`
already refuses an unknown prefix rather than guessing, so the version bump is clean:
`GIF1` sheets already in a plant keep decoding, and slice 9d's intake stops rejecting sheet
2 of a multi-sheet form as a duplicate. Built as slice **13b**.

### 2.5 Limits, and why they are not arbitrary

| Limit | Proposed | Reason |
|---|---|---|
| `copies` max | **200** | 200 forms × 3 pages × ~55 KB ≈ 33 MB. Above that a mobile browser on plant Wi-Fi times out mid-download and the operator has 200 registry rows for paper they never received. |
| Rate | one bulk call per user per 60 s | A double-click on Download is 100 registry rows. The existing per-process limiters in `ratelimit.py` already have the shape; this reuses `rate_buckets` (P10-1: Postgres, never Redis). |
| Transaction | all-or-nothing | See §2.2. |

### 2.6 What Track 1 does NOT change

- The fingerprint contract. Every sheet in a batch shares the same `Recipe_Fingerprint`
  because they are the same recipe at the same instant.
- The GET-that-writes decision. It stays a GET so the native shells can hand the PDF to
  the OS viewer.
- `_row_label`'s duplicate-material rule, the fiducials, the blank work date (ruling Q6),
  the no-write-in-rows rule (ruling Q9).

---

## 3. Track 2 — Hub Assistant Video Playback UX

### 3.1 The finding: nothing is broken, and that is the problem

I traced the whole path and it is intact end to end:

1. `ai/tutorials.py` reads `docs/tutorials/out/*.manifest.json`, indexes one BM25 chunk per
   beat, applies rule 9's own fence before scoring, and returns a `url` of the form
   `/training?module=<key>&lang=en&t=61.2`.
2. `HubAssistant.tsx:157` renders the **Watch it** button from the SSE `tutorial` frame.
3. `TrainingPage.tsx:229` reads `?module&lang&t`, matches the card, and
   `ModuleCard`'s effect seeks the `<video>` once, clamped a second inside the duration.

**There is a real `<video>` element and a real seek.** What there is not is anything to play:

| Fact | Evidence |
|---|---|
| Four MP4s exist, rendered, on this machine | `docs/tutorials/out/*.mp4`, 3.6–8.8 MB, dated 2026-09-07 |
| That directory is **gitignored** | `.gitignore:123` — `/docs/tutorials/out/` |
| **Zero `training_assets` rows exist**, by design | the slice-10b migration explicitly seeds none: *"an asset row pointing at a URI that does not exist yet would render a broken player"* |
| So every card renders **"Not published yet"** | `training.py:148` — `"published": bool(assets)` |
| **Only ONE `training_modules` row is seeded**: `ocr_workflow_v1` | the same migration |
| But **four** tutorials declare a `training_module_key` | `sk_stage_return_v1`, `hub_assistant_v1`, `hod_executive_summary_v1`, `ocr_workflow_v1` |

So three of the four deep links point at a module key that **matches no card at all** —
the page renders, the URL is honoured, and nothing scrolls into view. The fourth points at
a card that says the video is not published. Both are the truthful state of the data.

### 3.2 ⚠️ The constraint that shapes the fix: ruling Q5.3

`POST /training/assets` takes **a URI, not an upload**
([training.py:343](backend/api/training.py:343)):

> *A `LargeBinary` column would put a 200-600 MB tutorial set into every nightly `pg_dump`
> forever and could not serve HTTP Range requests, so the viewer could not seek.*

A `<video>` element that cannot seek is a `<video>` element the deep link cannot use. So
whatever serves the bytes **must honour `Range` and return `206`**. Nothing in the backend
does that today — I grepped; `training.py:351` is the only place the words appear, and it
is the comment explaining why.

### 3.3 The design — three parts, in order

**Part A — a media route that serves Range.** `GET /training/media/{module_key}/{language}.{ext}`
(`ext` ∈ `mp4|vtt`), reading from a configured `GI_TRAINING_MEDIA_DIR` (defaulting to
`docs/tutorials/out/`), with:

- `Accept-Ranges: bytes`, `206 Partial Content`, correct `Content-Range`.
- **The role fence applied at the byte level.** The deep link grants nothing today because
  the module list is server-filtered; a raw media URL would be a second door if it did not
  re-check. It calls the same `_roles_of(module)` the list uses — not a copy of it.
- Path traversal closed by construction: the filename is *derived* from the module key and
  language, never taken from the request.

Alternative considered and rejected for now: **nginx `X-Accel-Redirect`**. It is the right
production answer and `deploy/nginx.conf` already serves static, but it moves the access
decision out of the app and into a config file that CI does not test. Recommend adding it
as a deployment optimisation **after** the FastAPI route is pinned by tests.

**Part B — the missing module registry.** A migration seeding the three absent
`training_modules` rows, with `required_roles` taken from each script's `audience:`
(`store_keeper`, `[supervisor, store_keeper]`, `hod`). This migration **writes rows**, so
per rule 15's second half it must expose `data_upgrade(conn)` and call it from `upgrade()`,
or `verify_data_migration_contract()` refuses it in cutover pre-flight.

⚠️ Note the mismatch: `store_keeper_hub_assistant.yaml` has `tutorial_id:
store_keeper_hub_assistant` but `training_module_key: hub_assistant_v1`. The matcher
already prefers `module_key` and falls back to `tutorial_id`
([tutorials.py:98](backend/api/ai/tutorials.py:98)) — so the seeded key must be
`hub_assistant_v1`, not the tutorial id. Getting this backwards produces a link that
resolves to nothing and looks like a UI bug.

**Part C — a publish step, and it must be one command.**
`tools/generate_tutorial.py` currently *prints* the curl-shaped instructions
(line 1265). Add `--publish` which POSTs `/training/assets` for every rendered manifest,
filling `storage_uri` from Part A's route and `duration_s` from the manifest's measured
video duration. A publish that a human retypes is a publish that drifts from the render.

### 3.4 The UX question: modal, or the page?

You suggested "a modal or an auto-playing player". My recommendation is **neither, quite**:

- **Keep the destination `/training`**, because the compliance record (`training_progress`,
  the 90 % acknowledge bar) lives there and a modal player elsewhere would either bypass
  the beacon or duplicate it. Duplicating it is worse: two writers to one compliance number.
- **Add a "focused" mode** when `?module=` is present — the matched card renders first and
  alone, with the other modules collapsed behind a "Show my other training" link. Today the
  card scrolls into view but sits in a list; on a phone that reads as "it went to the
  training page and nothing happened".
- **Do not autoplay with sound.** Browsers block it, and the block is silent. Autoplay
  *muted* with a visible play affordance is the only version that behaves the same on every
  device.
- **Show a truthful empty state.** If the deep link names a module with no published asset,
  say *"This step has a video, but it has not been published on this server yet"* — not the
  generic "Not published yet". The assistant promised a video; the page must explain the gap.

⛔ **LOCKED (Q13-3): all four, as written.** Focused mode on `?module=`, muted autoplay
with a visible play affordance, the specific "not published on this server" empty state,
and the destination stays `/training` so the compliance beacon keeps one writer. Built as
slice **13d**.

### 3.5 ⚠️ Where the bytes come from

The renders are **local and gitignored by ruling Q3** (until the Hetzner cutover). Serving
them from `docs/tutorials/out/` on the dev box is fine. **On a production box that directory
is empty**, and the assistant's deep links correctly never appear (suite CX-13 pins this as
a supported state).

⛔ **LOCKED (Q13-4): build the plumbing now, serving the local `.mp4` files.** Object
storage comes later and changes only the `storage_uri` prefix — the route, the module rows
and `--publish` are needed either way.

⚠️ **Which keeps a supported state supported.** On a box where the render directory is
empty the media route 404s, the modules render their empty state, and the assistant's deep
links never appear at all. That is the same absent-feature contract suite CX-13 already
pins, and slice 13c must not turn it into an error.

---

## 4. Track 3 — Unifying SME and Inventory Surface Shield Consumption

This is the one that needs your ruling. I am going to state what already exists, then the
conflict, then four ways to resolve it.

### 4.1 What already exists, and it is closer than it looks

**`sme_consumption_log` + `backend/api/sme_actuals.py` is already 70 % of Track 3.** Read
its module docstring — it describes your feature almost exactly:

- Rows arrive carrying a `Tank No.` and a quantity but **no system code and no area**.
- A human resolves each one: pick the equipment, pick the system code, type the SQM.
- Assignment computes `Expected_Qty = For_1_SQM × SQM_Completed` and therefore
  `Variance_Pct`.
- It validates that the tag actually carries that system code at that site
  ([sme_actuals.py:276](backend/api/sme_actuals.py:276)) — your "equipment dropdown filtered
  by System Code", enforced server-side.
- It is `require_roles("hod")`, exact-locked.

What it does **not** have: a live source (rows come from a workbook sync, not from an
issue), an approval workflow, and any effect on the estimator's numbers.

**And the second half already exists in the other direction.** `execution.post_stock`
([execution.py:719](backend/api/services/execution.py:719)) is the ONLY writer for lining
consumption: an approved SME execution entry writes `consumption` rows with
`Source_Ref = SME_EXEC:<entry id>:<line id>`. So SME → Inventory is already wired. Track 3
is asking for Inventory → SME.

### 4.2 ⚠️ The conflict: rule 1a, locked 2026-08-02

> **The estimator and the warehouse are two separate pools of data, calculated completely
> separately. An ERP receipt, issue or return is a warehouse event and must not move a
> single SME number.**

Your brief says the opposite for one direction: *"If a Surface Shield item is consumed in
the general Inventory, it MUST reflect in the SME quantity as consumed (Received details
remain as they are now)."*

That is an **asymmetric re-coupling**: consumption flows, receipts do not. It is a
coherent position — but it is a new rule, not a clarification of an old one, and it has
four specific hazards.

#### Hazard 1 — ⚠️ THE SELF-FEEDING LOOP

`post_stock` writes `consumption` rows for approved SME execution entries. If SME
availability is then reduced by `Σconsumption`, an SME execution entry **reduces the
estimator by its own posting**. Add the new Track 3 intake on top and the same drum is
counted twice: once as an execution entry and once as an inventory issue.

**The fix is mandatory whichever reading you choose:** every read and every intake must
exclude `Source_Ref LIKE 'SME_EXEC:%'`. That predicate is the single most important line
of code in Track 3, and it must live in exactly one place.

#### Hazard 2 — the seed is already net of history

`Initial_Available_Qty` is what the workbook says is **on the shelf now**. It is not an
opening balance. Netting the live `consumption` ledger against it subtracts every issue
that the workbook snapshot had already accounted for. The live figures make this concrete:
`GI-8005762` holds 10,920 received against 95,200 ordered, and the last time an engine
subtracted receipts from a workbook figure (ruling Q2a, overturned) the result was to strip
units that had never been added.

**So a start date is not optional.** Only consumption posted *after* the seed's
`Document_Date` — or after an explicit `SME_NET_FROM` — may net.

#### Hazard 3 — component identity (rule 1)

`sme_consumption_log` has `Material_Code` and **no `SAP_Code` column**
([models.py:924](backend/models.py:924)). The `consumption` ledger is keyed on `SAP_Code`.
Rule 1 is unambiguous: the component key is `(Material_Code, SAP_Code)`, never
`Material_Code` alone — pooling by code alone once *inverted* a shortfall across the four
Cumicrete PU components. **`sme_consumption_log` must gain `SAP_Code` before it carries
ERP-sourced rows**, and the assignment's `For_1_SQM` lookup (which today sums recipe lines
matching `Material_Code` only, `sme_actuals.py:279`) must join on the pair.

#### Hazard 4 — the parity gate and the poison pills

`sme_parity_fixture.json` carries `received_qty` values (M1 = 40, M2 = 15, M6 = 25) as
**deliberate poison pills**: the golden proves both engines *ignore* the field. If the
engines start reading a consumption figure, the fixture, the golden and **both engines**
change in one commit — `parity:sme` (1,313 comparisons) and `ui-math` (33) both gate this,
and the TS/Python mirror rule is absolute.

### 4.3 The four resolutions, ranked

| | What "reflect as consumed" means | Rule 1a | Blast radius |
|---|---|---|---|
| **A. Side note (status quo shape)** | The draw appears beside the plan; `available_qty` is untouched. | intact | zero |
| **B. New field, never a numerator** | Engines gain `Consumed_Qty` / `Available_After_Consumption`, displayed as its own column. Readiness (`Status`, `Completion_Pct`, `SQM_Achievable_Now`, `Fulfillment_Pct`) still reads `Alloc_Available`. | amended, not broken | both engines + golden; presentation only |
| **C. Asymmetric net (my reading of your brief)** | `available_qty = Initial_Available_Qty − Σ eligible consumption`. Ordered untouched. Receipts still ignored. | **overturned** | every SME number, every export, every KPI |
| **D. Full recouple** | Availability = seed + receipts − consumption + returns. | **overturned**, and Q2a netting returns with it | everything, plus the 2026-07-28 bug comes back |

⛔ **LOCKED (Q13-5): B, and B only for now.** A new `Consumed_Qty` column, for visibility.
It **must not** alter the core estimator readiness logic and **must not** touch
`Allocated_Qty`.

That sentence is the acceptance criterion, so state it as a test rather than an intention:

> Post an eligible consumption against an SME component, then re-read `/sme/model-snapshot`.
> Every field except `Consumed_Qty` must be **byte-identical**. `Allocated_Qty`,
> `Alloc_Available`, `Alloc_Pending`, `Status`, `Completion_Pct`, `SQM_Achievable_Now`,
> `Coverage_Now_Pct`, `Fulfillment_Pct` and the buy list included.

Why B is the right first step, restated because a future agent will want to "finish" it:

- It satisfies *"reflect in the SME quantity as consumed"* literally — the consumed quantity
  is reflected, in the SME, per component — without silently moving reported buildable area
  the way the last presentation-layer error moved 9,118 m² (rule 1b).
- It lets the operator **see** what C would do before doing it: the column shows exactly how
  much the estimator would drop.
- C stays one predicate away, because the plumbing, the `SME_EXEC` exclusion and the start
  date are identical under both.

⚠️ **`Consumed_Qty` is an OBSERVATION FIELD, in the same category as `Allocated_Qty`
(rule 1b).** Nothing may colour it as coverage, nothing may divide by it, and no KPI may
name it. `Allocated_Qty` was already made green by six presentation layers that each thought
they were being helpful; this column has the identical shape and must not repeat it.

⚠️ **Because readiness does not move under B, the start-date question (Q13-5b) does not
arise yet.** `Consumed_Qty` reports the ledger it reads, and the reader can see the window.
The moment anyone proposes C, the start date becomes blocking again — the seed's
`Initial_Available_Qty` is *"what is on the shelf now"*, not an opening balance, so netting
the whole ledger against it double-subtracts everything the workbook already accounted for.

### 4.4 The intake — where the three questions get asked

Your brief asks the user for System Code (+ Date), SQM filled, and an equipment tag filtered
by that system code. **Where** to ask is the second design decision.

**Option 1 — at issue time, in the Issue form.** The SK already picks a Lining System Code
before issuing a Surface Shields SAP ([IssuePage.tsx:187](frontend/src/pages/IssuePage.tsx:187))
and the code already travels as an `LS <code>` suffix in `Remarks`. Adding SQM and an
equipment tag is three fields on a form that already has the first one.
⚠️ **But the store keeper does not know the SQM at issue time.** The drum leaves the store
before the area is covered. This is the exact reasoning that made Phase 9d paper-first.

**Option 2 — a queue, after the fact.** Every eligible consumption row lands in an
assignment queue; a supervisor or HOD resolves it with system code, SQM and tag. This is
`sme_actuals.py`'s existing shape, and it matches the physical reality.

**Option 3 — both.** The Issue form captures what the SK genuinely knows (system code,
equipment tag, work date). The queue captures what only the field knows (SQM). A row is
`unassigned` until the SQM arrives.

⛔ **LOCKED (Q13-6): Option 3 — both.** The SK gives the system code and the equipment tag
at issue; the field gives the SQM in a queue. Each person is asked only for what they can
actually know, and a row with no SQM stays visible and chaseable rather than invented.

#### 4.4a ⚠️ THE QUEUE IS A LEDGER SWEEP, NOT AN INBOX (operator requirement, 2026-09-10)

This is the part of Track 3 most likely to be built too small, so it is stated as a rule:

> **Any Surface Shield consumption entry lacking an assigned SQM or System Code appears in
> the queue, sorted by date, no matter how it got into the ledger.**

An inbox holds what arrived after it was switched on. A sweep holds everything that is
missing an attribution, including rows that predate the feature. The four sources that must
all land in the same queue:

| Source | How it reaches `consumption` today | What it is missing |
|---|---|---|
| **Historical rows** | already in the ledger, some pre-migration | usually both — 1,674 live consumption rows carry a blank WBS, and none carry an SQM |
| **Bulk Excel / Postgres sync** | `bulk_import.py` `/import/ledger`, `tools/pg_excel_sync.py` | both; the workbook states a Tank No. and a quantity and nothing else |
| **Ordinary SK issue** | `entry.py` → `ledger.stage_consumption` → HOD approve | the SQM (the system code arrives as the `LS <code>` Remarks suffix) |
| **OCR paper upload** | `ai/form_jobs.py` → `sme_execution_entry` → `post_stock` | ⚠️ **nothing — these are EXCLUDED** (see below) |

**The sweep is therefore a QUERY, not a trigger.** A trigger only fires on rows created
after it exists, which is precisely the set the operator asked to exclude. The queue is a
view over `consumption` filtered to the controlled category, left-joined to its attribution
row, showing everything with no match — so a row imported by a workbook sync next month and
a row posted in 2026-06 arrive in the same list, oldest first.

⚠️ **AND THE `SME_EXEC` EXCLUSION IS WHAT KEEPS THE SWEEP FROM EATING ITS OWN TAIL.** An
approved execution entry already carries a system code, an equipment tag and an
`Actual_SQM`, and `post_stock` writes its consumption rows with
`Source_Ref = SME_EXEC:<entry id>:<line id>`. Those rows are **already attributed** and must
never enter the queue: putting them there would ask a supervisor to type an area the paper
form already recorded, and answering it would double-count the same drum against the same
tag. One predicate, in one place, used by the sweep and by every read.

**When the system cannot infer the System Code, it asks rather than guesses.** The queue row
offers a dropdown of the valid system codes for that material's SAP — the recipe join
already knows which systems list that component — with the `LS <code>` Remarks suffix
pre-selected when one is present. A guessed system code produces a variance against the
wrong benchmark, which is worse than a blank, because a blank is visibly unfinished and a
wrong benchmark looks finished.

**Submission updates the equipment tag's completed SQM.** On approval the assignment
increments `sme_sqm_progress.Done_SQM` for that `(Site_ID, Equipment_Tag_No,
Lining_System_Code)` — the same ledger `execution.post_progress` writes, through the same
helper, never a second copy of the increment.

⚠️ **Which makes the double-count question a real one, and it is why 13g is a separate
slice.** `post_progress` and this new path both increment `Done_SQM`. They are disjoint
today only because `SME_EXEC` rows never reach the queue. That disjointness is an invariant
to test, not a fact to assume: suite DA asserts that approving an execution entry moves
`Done_SQM` exactly once, and that no queue row is created for it.

### 4.5 The benchmark and the HOD approval

Your brief: *"If the actual consumption SQM is more or less than the benchmark, it must
highlight and send to the HOD for approval. The HOD can edit values and submit (exactly
like the current flow)."*

**"Exactly like the current flow" is ambiguous — there are two current flows**, and they
behave differently:

- **`execution.hod_decide`** ([execution.py:797](backend/api/services/execution.py:797)) —
  approve/reject with per-line `edits`, a mandatory `HOD_Edit_Justification` the moment any
  number moves, `hod_edited` flag, a snapshotted benchmark, the QSEP gate, and
  `post_stock`. Rejection is **terminal** with no bounce-back (ruling Q4).
- **`hod.py`** — the pending-entry queues, per-row approve / reject-with-reason / edit
  (`{"fields": {...}}`), bulk-approve up to 200.

The benchmark-variance language points at the first. **My recommendation is to reuse
`hod_decide`'s contract**: a mandatory justification on any edit, an `Original_Qty`
preserved, a snapshotted benchmark (`Bench_For_1_SQM` at assignment time, never re-joined —
correcting a recipe next quarter must not rewrite last quarter's variance), and terminal
rejection.

⚠️ **But note the stock is already gone.** Unlike an execution entry, where approval is what
deducts stock, here the `consumption` row was posted the moment the SK issued it. So HOD
approval in Track 3 approves **the SQM attribution and the variance explanation**, not the
deduction.

⛔ **LOCKED (Q13-7): the HOD edits the SQM or the explanation. Never the physical
quantity.** `Actual_Qty` is rendered read-only on this screen and the endpoint **refuses**
a quantity field rather than ignoring it — an ignored field is a silent data loss, and a
supervisor who typed a correction into a box that discarded it will believe it was applied.
A genuine quantity correction goes through the stock-adjustment path, where it is a ledger
event with its own audit line, not a silent `UPDATE` to a posted row.

So the HOD's editable surface is exactly three things: `SQM_Completed`, the system code /
equipment attribution, and the explanation. Everything else on the row is evidence.

#### 4.5a ⛔ LOCKED (Q13-8): every entry goes to the HOD, and ±10 % decides its priority

The operator's ruling has two halves and the second one is the one that will get built
wrong, so both are stated plainly:

1. **ALL Surface Shield consumption entries go to the HOD for approval, regardless of
   variance.** There is no auto-commit band. A 0 % variance still requires a decision.
2. **The global tolerance is ±10 %.** It does not gate approval; it sets **priority**. A row
   whose `|Variance_Pct| > 10` is flagged and rendered prominently as **"High Priority"** at
   the top of the HOD queue.

⚠️ **This inverts my own recommendation and the operator is right.** I had argued that a
queue containing every row is a queue nobody reads. The counter is stronger: this material
is the one the MTC gate exists for, the stock has already left the shelf, and an
auto-committed attribution is one nobody ever looks at. Sorting by priority solves the
volume problem that auto-commit was solving, without the cost — the HOD sees the outliers
first and still signs for the rest.

**Which means ±10 % is a PRESENTATION threshold, not a gate.** Three consequences that the
implementation must honour, because each one is a way to get this subtly wrong:

- Changing the tolerance **never** changes what needs approving. It re-sorts a queue. So the
  number can be edited without a migration and without re-opening settled rows.
- A row already approved at 12 % does **not** reopen if the tolerance later moves to 15 %.
  The flag is computed and **stored** at submission alongside the snapshotted benchmark, the
  same way `Bench_*` is snapshotted on an execution entry — a variance report that re-derives
  its own threshold rewrites history the first time somebody tunes it.
- ⚠️ **A `NULL` variance is not a low variance.** `Expected_Qty = 0` (no recipe line for that
  component in that system) yields `Variance_Pct = NULL`, never `0`. A NULL sorts with the
  High Priority group, not with the compliant rows: "we cannot compute this" is a thing an
  HOD must look at, and treating it as 0 % would file the least-understood rows at the bottom.

**Where the number lives:** an `app_settings` row (`sme_variance_tolerance_pct`, default
`10`), read through the existing settings whitelist, so an admin can change it without a
deploy and every read gets the same value. Not a constant in two engines.

### 4.6 Where it lives in the UI

`SmePage` already has a **🧾 Actual Consumption** tab ([SmePage.tsx:233](frontend/src/pages/SmePage.tsx:233))
that renders exactly this data. My recommendation is to extend that tab rather than add a
page: a new route means a new nav manifest entry, a new role-matrix row, and `npm run
test:nav` claiming a 52nd route — all for a screen that belongs beside the one it compares
against.

⚠️ **But the SME router is exact-locked to `{hod, auditor}`** and the whole `/sme` nav group
with it. If a **supervisor** is to type the SQM (Option 3 above), the intake endpoint cannot
live under `/sme`. It would mount under `/execution` — which already belongs to exactly SK,
supervisor and HOD, and which is where the printed form was mounted for precisely this
reason ([execution.py:387](backend/api/execution.py:387)). Rule 14: narrowing or widening a
page means changing its endpoints in the same commit; a menu is not a control.

---

## 5. Risk register against the locked rules

| Rule | Track | Risk | Mitigation |
|---|---|---|---|
| **1** — component key is `(Material_Code, SAP_Code)` | 3 | `sme_consumption_log` has no `SAP_Code`; pooling by code alone once inverted a shortfall | Add the column and the recipe join before any ERP row lands. Pin with a four-component fixture (the Cumicrete PU shape). |
| **1a** — strict decoupling | 3 | ⛔ **B is locked**, so readiness never moves — but a `Consumed_Qty` column beside `available_qty` is a standing invitation to subtract one from the other | `PROJECT_HANDOVER.md` §1a is rewritten in the same PR as 13g: the column is an OBSERVATION, readiness is unmoved, and the exclusion predicate is named. Suite DA asserts byte-identity of every other field. |
| **Sweep completeness** — Q13-6 | 3 | Built as a trigger it fires only on new rows and is **empty of exactly the history it was asked for** — 1,674 unattributed consumption rows already in the ledger | The sweep is a QUERY over `consumption` left-joined to its attribution, not a trigger. Suite DA seeds a row by each of the four routes and requires all three eligible ones to appear. |
| **±10 % misread as a gate** — Q13-8 | 3 | Auto-committing inside the band, or reopening settled rows when the number is tuned | The flag is computed and **stored** at submission beside the snapshotted benchmark. Every row routes to the HOD regardless. Tolerance changes re-sort; they never reopen. |
| **1b** — strict tier segregation | 3 | A `Consumed_Qty` column that anything colours green becomes a second `Allocated_Qty` | `Consumed_Qty` is a conservation/observation field. It never enters `Status`, `Completion_Pct`, `SQM_Achievable_Now`, `Coverage_Now_Pct` or `Fulfillment_Pct`. Pinned in `ui-math`. |
| **1c** — the subset rule | 3 | Untouched by design — consumption nets availability, never the ordered tier | Explicit test: ordered pool is byte-identical before and after a consumption. |
| **1 (standing)** — both engines change together | 3 | A `Consumed_Qty` added to Python only | One commit, both engines, regenerated golden, `parity:sme` green. Non-negotiable. |
| **7 / 14** — the auditor wall, nav fails closed | 2, 3 | A new media route or a new intake endpoint that an auditor can POST to | The readonly middleware is method-keyed and needs no per-endpoint work — but if a new endpoint 403s for an auditor, that is correct. New routes get nav rules in the same commit. |
| **9 / P12-6** — the fence runs before the score | 2 | A media route that serves bytes without re-checking `required_roles` | Part A calls the same `_roles_of`, never a copy. |
| **13** — docs are Done | all | Three tracks, two manuals | `MANUAL_TESTING_GUIDE.md` and `USER_MANUAL.md` updated in the same PRs. PDFs regenerated (`build_manual_pdf.py --role all`). |
| **15** — tests never open the live DB; migrations that write rows expose `data_upgrade` | 1, 2, 3 | Two of the three tracks add migrations, and Track 2's **seeds rows** | Every new migration declares its objects in `models.py` in the same commit. Track 2's module seed exposes `data_upgrade(conn)`. |
| **16** — a SKIP is not a PASS | all | A new PDF/media test that silently skips on a missing library | `bin/ci_preflight.sh` runs first. Any new dependency guard is written so the skip is *reported*, never folded into the pass total. |
| **P10-1** — no Redis | 1 | The bulk rate limit | `rate_buckets` in Postgres, the existing shape. |
| **P10-4** — never value at zero | — | not touched | |
| **Four workers** | 1 | Nothing in Track 1 is a timer, but the bulk transaction is long | The insert loop is one transaction; no in-memory batch state. |

---

## 6. Sequencing — seven slices

Each is a branch off `main`, each ships green, each is independently revertable.

| Slice | Branch | Scope | Status |
|---|---|---|---|
| **13a** | `feat/phase13-bulk-forms` | `copies` param, `Batch_UUID`/`Batch_Seq`/`Batch_Size` migration + models, "Sheet n of N" on the page, the UI number input with live sheet-count helper, limits, one transaction. Suite **CY**. | ✅ **approved to build** |
| **13b** | `feat/phase13-qr-v2` | `GIF2` payload with `seq`/`of`, `parse_qr` version dispatch, `_match_rows` row offset, intake accepting a multi-sheet form without a duplicate refusal. Suite **CY** (extends). | ✅ **approved to build** |
| **13c** | `feat/phase13-training-media` | Range-serving media route with the role fence, the three missing `training_modules` rows (with `data_upgrade`), `generate_tutorial.py --publish`. Suite **CZ**. | ✅ **approved to build** |
| **13d** | `feat/phase13-training-media` (same branch) | Focused mode on `?module=`, muted autoplay with a visible affordance, the truthful "not published on this server" empty state, deep-link E2E. | ✅ **approved to build** |
| **13e** | `feat/phase13-sme-link-read` | `sme_consumption_log.SAP_Code` + the `(Material_Code, SAP_Code)` recipe join, the `SME_EXEC` exclusion predicate in one place, **the ledger sweep as a read-only query** over all four sources. Suite **DA**. | ⏸️ awaits green light |
| **13f** | `feat/phase13-sme-link-intake` | Issue-form capture (system code + tag), the queue with its system-code dropdown, the SQM intake, the variance against a **snapshotted** `Bench_For_1_SQM`, the stored ±10 % priority flag. Suite **DA** (extends). | ⏸️ awaits green light |
| **13g** | `feat/phase13-sme-link-approval` | HOD queue with High Priority first, SQM/explanation-only editing (a quantity field is **refused**), mandatory justification, `Done_SQM` increment through `post_progress`'s helper, notification dispatch, and the `Consumed_Qty` column — **both engines + golden in one commit**. Suite **DB**. | ⏸️ awaits green light |

⚠️ **13d shares 13c's branch** at the operator's instruction. They are one deliverable: a
media route with no UI to reach it, or a focused player with no bytes, is half a feature in
either direction.

⚠️ **13e is deliberately read-only.** It builds and proves the join, the exclusion and the
component identity **before** anything writes. The `SME_EXEC` self-feeding loop is the
highest-severity risk in the phase and it is cheapest to catch against a read.

---

## 7. Gates, and how each track is pinned

Every gate in [`CLAUDE.md`](CLAUDE.md) §3 must be green before any slice is called done. In
addition:

**Track 1 — suite CY**
- `copies=1` is byte-comparable to today's single download (minus the new "Sheet 1 of 1").
- `copies=50` mints exactly 50 registry rows, **50 distinct `Form_UUID`s**, one `Batch_UUID`.
- The 50 QR payloads are **all different** — decoded, not merely compared as strings.
- A failure at sheet 37 leaves **zero** registry rows (transaction).
- `copies=0`, `copies=-1`, `copies=201` are 422.
- A 40-line recipe at `copies=3` produces the sheet count the helper text promised.
- ⚠️ The QR round-trip needs `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` on this Mac or
  it **skips**, and a skip is not a pass (rule 16).

**Track 2 — suite CZ**
- A `Range: bytes=1000-2000` request returns `206` with a correct `Content-Range`.
- A store keeper requesting the HOD tutorial's media gets `403` — the fence, at the byte level.
- A module key with no file returns `404`, not a zero-byte `200`.
- `?module=` naming an unpublished module renders the specific empty state.
- Playwright: assistant answer → Watch it → `/training` → the matched card is focused and the
  player's `currentTime` is within a second of `t`.

**Track 3 — suites DA / DB**
- ⚠️ **The `SME_EXEC` exclusion, both ways.** Post an execution entry, approve it, confirm the
  estimator's consumed figure did **not** move; then post an ordinary issue and confirm it did.
- The ordered pool is **byte-identical** across any consumption (rule 1c untouched).
- A four-component material (the Cumicrete PU shape: one `Material_Code`, four SAPs) attributes
  each SAP's consumption to its own component — the rule-1 trap.
- ⛔ **Resolution B, asserted as bytes.** Readiness figures are byte-identical across a
  consumption; `Allocated_Qty` is byte-identical; only `Consumed_Qty` moves.
- **The sweep sees all four sources.** A row inserted directly (standing for a historical
  row), one landed by `bulk_import`, and one from an ordinary SK issue all appear, oldest
  first — and an `SME_EXEC` row appears in **none** of them.
- **`Done_SQM` moves exactly once.** Approving an execution entry increments it through
  `post_progress`; approving a queue row increments it through the same helper; neither
  path double-counts the other.
- A tag that does not carry the selected system code is 422 (the existing `sme_actuals` check).
- Variance against a zero benchmark is `NULL`, never `0` and never `Infinity` — **and a NULL
  sorts with High Priority**, not with the compliant rows.
- **±10 % is priority, not a gate.** A 2 % row still requires approval. Moving the tolerance
  re-sorts the queue and reopens nothing already decided.
- An HOD edit with no justification is 422, and **an HOD edit carrying a quantity field is
  422** — refused, never silently ignored.

---

## 8. Visual verification strategy

Three layers, cheapest first. **Nothing in this section asks you to check something manually.**

### 8.1 Layer 1 — the dev stack in the preview browser (during development)

`bin/dev.sh` raises uvicorn `:8000` and Vite `:5173`; the preview tooling drives them.
Never a bare `Bash` dev server — all three dev modes want `:5173` and Vite's `strictPort`
makes the second one fail loudly.

⚠️ **The preview browser runs with `document.hidden = true`**, so React live updates lag and
a stale render looks like a bug. Verified the way that memo says: reload, then confirm
against the API or the database rather than the DOM alone.

| Track | What I will actually look at |
|---|---|
| **1** | Render `copies=3` on a 40-line recipe, save the PDF, decode **every** QR with `pyzbar` and assert three distinct UUIDs. A screenshot of a PDF proves the layout; only a decode proves the uniqueness, which is the whole feature. |
| **2** | Ask the assistant a question that matches a beat, click **Watch it**, screenshot the training page with the player visible, then read `video.currentTime` and `video.readyState` via the JS tool. A screenshot of a black `<video>` box is indistinguishable from a working one. |
| **3** | Issue a Surface Shield through the real Issue form, screenshot the queue row it creates, complete the assignment, screenshot the variance highlight, then read the estimator's numbers from `/sme/model-snapshot` **before and after** and diff them. The diff is the evidence, not the screenshot. |

### 8.2 Layer 2 — Playwright, which is the gate

`tests/e2e/` builds and destroys its own `gihub_e2e_pw` stack (128 specs, ~55 s). New specs:

- `bulk-forms.spec.ts` — the number input, the live sheet-count helper, the download, the
  registry rows.
- `training-deeplink.spec.ts` — assistant → button → focused card → `currentTime` asserted.
- `sme-inventory-link.spec.ts` — issue → queue → assign → variance → HOD approve, plus the
  **negative** case that an `SME_EXEC`-sourced consumption never appears in the queue.

⚠️ Playwright's `recordVideo.size` does not scale the page, and viewport is pinned equal in
the tutorial recorder for that reason. Any new spec that screenshots follows the same pin.

### 8.3 Layer 3 — the manual-screenshot capture, for the docs

`tests/e2e/capture-manual-shots.mjs` captures `USER_MANUAL.md`'s screenshots from the running
dev stack into `docs/screenshots/v2/`. Rule 13 makes the manual part of Done, so each track
adds its shots here rather than a human pasting them.

⚠️ **And P12-0 applies the moment a screenshot might become a tutorial frame.** The manual
capture runs against the dev database, which is real data. If any Phase 13 screen is to be
recorded as a tutorial, it is recorded against `tools/make_tutorial_db.py`'s synthetic
dataset or it is not published — and `--dry-run` lints the script without opening a browser.

### 8.4 What I will NOT do

- I will not call a tutorial render a gate (Phase 12 ruling — a video that renders badly is
  one to re-render, not a red build).
- I will not start or stop Postgres, cloudflared or Ollama without asking. The Mac is often
  left in a deliberate `bin/power.sh sleep` state, and there is a **root** cloudflared
  LaunchDaemon serving `gi.giinventory.com` that is not mine to touch.
- I will not screenshot-and-declare. Every visual claim in this phase is backed by a decode,
  a DOM read or a database diff.

---

## 9. Clarifying questions — ⛔ ALL ANSWERED AND LOCKED (operator, 2026-09-10)

### 9.0 The answers, verbatim

| # | Question | ⛔ Locked answer |
|---|---|---|
| **Q13-1** | 50 pages = 50 forms or 50 A4 sheets? | **50 FORMS**, with the live helper text (`50 forms × 3 pages = 150 A4 sheets`). |
| **Q13-2** | Bump the QR to carry a sheet sequence, or cap a form at 18 lines? | **Upgrade to `GIF2`** with `seq`/`of`. |
| **Q13-3** | Focused card, modal, or autoplay? | **Focused mode + muted autoplay + the truthful empty state**, destination stays `/training`. |
| **Q13-4** | Ship the media plumbing now, or wait for object storage? | **Now**, serving the local `.mp4` files. Object storage is a later migration. |
| **Q13-5** | What does "reflect as consumed" mean numerically? | **Option B.** A new `Consumed_Qty` column for visibility. It must **NOT** alter the core estimator readiness logic and must **NOT** touch `Allocated_Qty`. |
| **Q13-5b** | If C, from what date? | **Does not arise under B.** Re-opens the moment anyone proposes C. |
| **Q13-6** | Where are the three questions asked? | **Both** — SK gives the code and tag at issue, the field gives the SQM in a queue. ➕ **The queue is a LEDGER SWEEP** (§4.4a): historical rows, bulk Excel/Postgres syncs and OCR uploads all included, sorted by date; a system-code dropdown when the code is missing; submission updates that equipment tag's completed SQM. |
| **Q13-7** | What happens when an HOD edits a quantity? | **They cannot.** The HOD edits the SQM or the explanation only. The physical quantity is read-only and the endpoint **refuses** the field. |
| **Q13-8** | What is the tolerance, and does anything auto-commit? | **Nothing auto-commits — ALL entries go to the HOD regardless of variance.** The global tolerance is **±10 %**, and it sets **priority**: outside it, the row is flagged **"High Priority"** and rendered prominently at the top of the queue. |
| **Q13-9** | Does the queue notify? | Answered by building — dispatch on a High Priority row, pull-only otherwise. Raised again in 13f if the volume argues otherwise. |
| **Q13-10** | Which classifier decides "Surface Shield"? | `inventory.Category` exact match, the same test the MTC gate uses (`config.MTC_REQUIRED_CATEGORY`). ⚠️ **Not** `consumption.Item_Type`, which is a different table and is spelled singular. |

### 9.1 ⚠️ The two answers that changed my design, and why the operator was right

**Q13-6's sweep requirement.** I had designed the queue as an inbox — rows arriving from the
issue path once the feature existed. The operator's requirement makes it a **query over the
whole ledger**, which is a different thing: an inbox cannot see the 1,674 consumption rows
already sitting in the ledger with no attribution, and those are the rows the business
actually needs reconciled. Built as a trigger it would have looked correct and been empty of
exactly the history it was asked for.

**Q13-8's "everything goes to the HOD".** I argued for auto-committing inside the band on the
grounds that a queue containing every row is a queue nobody reads. The operator overruled it,
and the reasoning is stronger than mine: the stock has already left the shelf, this is the
material the MTC gate exists for, and an auto-committed attribution is one nobody ever looks
at. Priority sorting solves the volume problem that auto-commit was solving, without the cost.
⚠️ The consequence for the implementation is that **±10 % is a presentation threshold, never a
gate** — see §4.5a for the three ways that distinction can be built wrong.

---

## 10. Execution status

| Slice | State |
|---|---|
| Plan updated with the locked answers | ✅ done, 2026-09-10 |
| **13a** bulk forms | ✅ built on `feat/phase13-bulk-forms` |
| **13b** `GIF2` sheet sequence | ✅ built on `feat/phase13-qr-v2` |
| **13c + 13d** training media + focused UX | ✅ built on `feat/phase13-training-media` |
| **13e / 13f / 13g** Track 3 | ⏸️ planned in full, awaiting the operator's green light |

⚠️ **Two things Track 3 must carry when it is approved**, recorded here so they are not
rediscovered late:

1. **`PROJECT_HANDOVER.md` §1a is rewritten in the same PR as 13g**, stating that
   `Consumed_Qty` is an observation field, that readiness is unmoved, and that
   `Source_Ref LIKE 'SME_EXEC:%'` is excluded — with the measurement. Without it the next
   agent reads the locked decoupling rule and unwinds Track 3 on sight, and will be right to.
2. **The engines change together or not at all.** `Consumed_Qty` lands in
   `backend/api/sme_engine.py` and `frontend/src/sme/engine.ts` in ONE commit with a
   regenerated golden, and `parity:sme` (1,313 comparisons) is what proves it.

---

🤖 Generated with [Claude Code](https://claude.com/claude-code)
