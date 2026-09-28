# PROPOSED PHASE 15 PLAN — Training Polish, Garnet Integration & UI Fixes

> **Status: PLANNING ONLY — awaiting operator approval. No application code has been written.**
> Rewritten 2026-09-27 (afternoon) after a fresh re-check of the live mirror, both Practice
> databases, the Practice API log, the source, and the workbooks you saved at **13:28 today**.
> Every number below was measured in this pass. Assumptions are labelled as assumptions.

---

## 0. Summary

| Track | What is actually wrong (measured) | Size | Slice | Order |
|---|---|---|---|---|
| **3 — Practice 500s / missing items** | Both Practice databases are **6 migrations behind** the code (`f6b83d1a27c9` vs head `a3d5e7f91c24`). Every read touching a Phase 14 column or table 500s. Your items **were saved**; the list that shows them crashes. One of them was also saved with site `cncec` (lower-case), so it would stay hidden even after the fix. | S | **15a** | 1st |
| **2 — Queue checkboxes** | `SmeJobs.tsx` gives each table row a **text** key (`"123"`) but keeps the ticked list as **numbers** (`123`). antd compares them strictly, so no box ever renders ticked, and each click leaves only the clicked row selected ("Submit 1"). | XS | **15b** | 2nd |
| **1 — Login + Practice look** | The "transparent box" is the WebGL **glass slab + gold rim** (`loginScene.ts:139-157`). The big mark is placed in the margin **left of an assumed 400 px card**, and it follows the pointer, so it overlaps the card on narrower windows. Practice already has a banner, a header tag, a tab-title prefix and an amber header line, but no top-left indicator and no colour change. | M | **15c** | 3rd |
| **4 — Garnet Old/New** | Your workbooks now model Garnet as two **blasting codes**: `ESC1` (concrete, 18 KG/m²) and `ESC2` (tank/vessel, 20 KG/m²), SAP 1429. The app already uses `ESC1`/`ESC2` for the **blasting man-hour norms**. Importing the new recipe rows as they are would flip blasting into a "lining system" in the planner and the execution form. It needs an explicit "surface prep" concept, a baseline table and a split in the job card. | L | **15d** | 4th |

⚠️ **Before your next Excel sync (today, until 15d ships)** — see §4.3. Your routine command now imports the two Garnet recipe rows, which changes man-hour planning for blasting. Use this command in the meantime; it is identical except that it skips `For_1_SQM.xlsx`:

```bash
DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub .venv/bin/python tools/pg_excel_sync.py --site CNCEC --kinds inventory,ledger,sme-manpower,sme-equipment,sme-materials --prune-vanished --commit
```

I dry-ran it (no writes). It reads the same inventory and ledger changes (inventory ~3, receipts ~3 edited / 3 vanished, consumption +1 / ~3 / 1 vanished) plus **+1 Garnet row** in the materials seed. Run it **twice**: the second run fixes Garnet's base unit (H2 in §4.3).

---

## 1. Track 3 — Practice: the 500s and the "missing" items (slice 15a)

### 1.1 Findings (measured)

| Database | `alembic_version` | Code head |
|---|---|---|
| `gihub` (Live mirror) | `a3d5e7f91c24` ✅ | `a3d5e7f91c24` |
| `gihub_svctest` (tests) | `a3d5e7f91c24` ✅ | |
| **`gihub_training`** (Practice sandbox) | **`f6b83d1a27c9`** ❌ | |
| **`gihub_seed_training`** (its reset template) | **`f6b83d1a27c9`** ❌ | |

The six missing migrations are all Phase 14:
- 14a `c41d7e9a2b58` Unit_Size / Base_UOM
- 14b `d7a3f05c1e92` consumption_reconciliation
- 14c `e8b4c16d2f03` sme_attribution_group, Qty_Unit
- 14d `f2c9a7d41b36` feature_announcements
- follow-up `a3d5e7f91c24` stock_excel_checks
- (plus `Pack_Qty` on the lots view)

**Errors in `.dev/practice.log`, counted:**

| Error | Count |
|---|---|
| `column inventory.Unit_Size does not exist` | 390 |
| `column i.Unit_Size does not exist` | 219 |
| `relation "feature_announcements" does not exist` | 219 |
| `column l.Pack_Qty does not exist` | 18 |
| `relation "stock_excel_checks" does not exist` | 12 |
| `sme_attribution_group` / `Qty_Unit` / `consumption_reconciliation` missing | 6 / 6 / 3 |

**Endpoints returning 500:**
- `GET /inventory` ×130: the Inventory page, **the SK issue/receipt item pickers**, and the HOD portal's stock panels all read it.
- `/meta/unit-sizes` ×73
- `/announcements/whats-new` ×73
- `/stock/excel-check` ×4
- `/execution/sme-link/groups` ×4
- `/sme-link/queue`, `/groups/staged`, `/execution/entries` ×2 each
- `/sme/actuals/reconciliation` ×1

**Your two items are in `gihub_training`** (309 rows vs the seed's 307):

| SAP | Name | Site_ID | Category | Visible after the migration? |
|---|---|---|---|---|
| 9999999999 | Basasasasas | `CNCEC` | `Consumables` | ✅ yes |
| 800000000 | aaaaaaaaaaaaaaaaa | **`cncec`** | **`consumable`** | ❌ no: site scope compares `Site_ID` exactly, so the CNCEC store keeper never sees it, and the category matches no existing one |

The second row got in because `InventoryAdminPage.tsx:132,141` takes **Site** and **Category** as free-text inputs, and `POST /inventory` (`admin.py:386`) stores whatever it receives. The same gap exists on Live; nobody has tripped it yet.

**Live is not affected.** The Practice process still talks only to its own database, and the `gi_training` role is still walled off from `gihub`. This is a schema-lag bug, not an isolation breach.

### 1.2 Why it happened, and why no gate caught it

- I applied the Phase 14 migrations to the Live mirror and the test DB only. On a server, the deploy script migrates both instances; on this Mac nothing does.
- `tools/practice_db.py` states that Practice "never runs migrations-behind" **because `build` creates the seed at head**. That is true only on the day of the build. The seed was built before Phase 14 and nothing rebuilt or migrated it afterwards, and `practice_db.py verify` does not check the migration version.
- `service_tests` run against `gihub_svctest`, which was at head, so they passed.

### 1.3 Fix

**A. Data fix (needs your OK: Q15-0).** Additive migrations only; trainee data is kept.
1. `pg_dump` both Practice databases to `.backups/` first.
2. `alembic upgrade head` on `gihub_seed_training`, then on `gihub_training`, as `gi_training`.
   - The seed matters too: otherwise the next **Reset Practice** clones a stale template and the 500s return.
3. Re-run `practice_db.py wall` and `practice_db.py verify`.
4. Re-check every endpoint in §1.1 returns 200 on `:8001`.
5. Normalise SAP 800000000 to `CNCEC` / `Consumables`, **or** leave it for you to edit. (Q15-0 asks which.)

The alternative, `practice_db.py build` + `reset`, rebuilds from scratch but **wipes trainee data**. I recommend against it.

**B. Make it impossible to recur.**
- **Practice boot guard (rule 17's boot check, extended).**
  - The Practice API refuses to start when its database's `alembic_version` ≠ the code's head, with a one-line message naming the fix.
  - `/health` reports `schema: behind` for the Live process as a warning, not a refusal (the operator owns that data).
- **`practice_db.py migrate`**: new subcommand that upgrades seed and sandbox in the right order, with the dump taken first.
- **`practice_db.py verify`** gains two checks: seed at head, sandbox at head.
- **`bin/dev.sh`** runs `practice_db.py migrate` before starting the Practice API. It never auto-migrates Live; it prints a red warning if Live is behind.
- **Docs:** correct the false sentence in the `practice_db.py` docstring; add "migrate BOTH instances" to rule 17 in `.claude/RULES.md` and the deploy runbook.

**C. Input validation (applies to Live too, since it is the same code).**
- `InventoryAdminPage`: **Site** becomes a `Select` of existing sites. **Category** becomes a `Select` of existing categories; a brand-new category is still allowed but asks for confirmation.
- `POST`/`PATCH /inventory`:
  - canonicalise `Site_ID` to an existing site's spelling, case-insensitively, else 422;
  - canonicalise `Category` through the same canonicaliser the Excel sync already uses (`Surface Shield → Surface Shields`).

**D. Tests.**
- service_tests:
  - boot guard refuses a behind-head DB;
  - `verify` reports a behind seed;
  - `POST /inventory` with `cncec` stores `CNCEC`;
  - an unknown site gets 422.
- E2E (Practice project):
  - admin adds an item → it is visible on Inventory, in the SK Issue and Receipt pickers, and the HOD portal loads with 200s;
  - the same SAP is **absent** on Live.

---

## 2. Track 2 — Supervisor queue checkboxes (slice 15b)

### 2.1 Root cause (confirmed in source)

`frontend/src/pages/SmeJobs.tsx`:

```tsx
75  const defaultSel = (c?: Candidate) => job.rows ... .map((r) => Number(r.consumption_id))
78  const [sel, setSel] = useState<number[]>(() => defaultSel(cand))
141 rowKey={(r) => String(r.consumption_id)}                              // keys are "123"
142 rowSelection={{ selectedRowKeys: sel, onChange: (k) => setSel(k.map(Number)) }}   // sel holds 123
```

antd checks `selectedRowKeys.includes(rowKey)` strictly, and `123 !== "123"`. So:
- **No box ever shows as ticked**, not even the default recipe selection.
- Clicking a box: antd starts from its own (empty) view of the selection and adds the clicked key, so `sel` becomes just that one row. A second click replaces it. You can never build a multi-row group, which is exactly the **"Submit 1 to the HOD"** in your screenshot.
- The header "select all" box behaves the same way.

**Second, smaller defect.** `useState(() => defaultSel(cand))` runs once. When the queue refetches and a job gains or loses rows (a store keeper posts another draw for the same tag and day), the selection keeps stale ids. **Fix:** key the card by `job.key + row-id signature`, or reconcile `sel` against `job.rows` in an effect.

**Scope.** I checked every `rowSelection` in the app:
- `ApprovalsPage.tsx:240` already converts (`selected.map(String)`);
- the two in `ManHoursPage.tsx` pass keys through unchanged;
- **only `SmeJobs.tsx` is broken.**

My Phase 14c E2E submitted the default selection and never clicked a checkbox, which is why it passed.

### 2.2 Fix + tests
- Keep one key type end-to-end: `selectedRowKeys: sel.map(String)`, `onChange: k => setSel(k.map(Number))`. This is the same idiom as ApprovalsPage.
- Reconcile the selection when `job.rows` changes.
- E2E (`sme-jobs.spec`):
  - the default recipe rows render **checked**;
  - untick one, tick another, and the button reads "Submit N";
  - the POST body carries **exactly** those `consumption_ids`;
  - "select all" / "none" work;
  - after a refetch that adds a row, the new row is unticked and the old ticks survive.

---

## 3. Track 1 — Login redesign + Practice look (slice 15c)

### 3.1 Findings
- **The transparent box** is Tier 1's `glass` `MeshPhysicalMaterial` slab (`roundedRect(1.75, 1.2)`) plus a gold `rimMat` outline (`loginScene.ts:139-157`). It was meant to sit behind the mark, but it reads as an empty pane behind the card.
- **The overlap:**
  - `loginScene.ts:215-218` centres the rig "in the margin LEFT of the centred 400 px card".
  - On any window where that margin is narrower than the mark, the two collide.
  - The rig also turns with the pointer (`pointermove` → parallax), and the **card tilts with it** (`.gi-login[data-gi3d='on'] .gi-login-card` in `index.css`).
- **Tier 0** (no WebGL: native apps, SwiftShader, low-power) draws two floating, tilted marks (`.gi-login::before/::after`) plus a small turning mark above the wordmark.
- **Practice already has:**
  - the amber `PracticeBanner`;
  - an orange `PRACTICE` tag in the header **next to "Warehouse & Inventory"** (not near the logo, not animated);
  - a `PRACTICE ·` tab-title prefix;
  - an amber inset line under the header;
  - an amber ring on the login card;
  - a print watermark.

  All of these are driven by `GET /instance` (vector V10). There is **no colour-scheme change**.

### 3.2 Login changes
1. **Delete the slab and the rim** from the scene, keeping the gold mark, the key light and the dust. This is lazy-chunk code only; the critical-path check is unaffected and `LAZY_3D_BUDGET` gets smaller.
2. **New layout:** a vertical stack, not a centred card with a side margin.
   - A **top band** (`clamp(120px, 24vh, 240px)`) holds the large GI mark, **static and centred**.
   - The **card moves down** below the band.
   - The scene reads the card's real `getBoundingClientRect().top` (plus `ResizeObserver`) and fits the mark into the space above it, so it can never overlap whatever the card's height or the window's size.
   - On short landscape phones, where the band would be under ~110 px, the big mark is hidden and the small one above the wordmark stays.
3. **Static:**
   - remove the pointer parallax on the rig **and** the card tilt;
   - the mark does not move. (Q15-1: keep the one-time rise-in on load and a slow light sweep across the gold, or nothing at all?)
4. **Tier 0 matches Tier 1:** the same static mark in the same top band via CSS (`/brand/gi-mark.svg`), so native apps and old GPUs get the identical layout. The two floating background planes go (Q15-2 lets you keep them faint).
5. Budgets: CSS tolerance 2 KB, JS critical path +0 B, both checked by `npm run build`.

### 3.3 Practice indicator on every page (top-left, "blinking")
- **`PracticeBadge`**: a new component placed **in the sider brand block, directly under the "GI Hub" wordmark** (top-left, every page).
  - Amber pill, `PRACTICE`, with a **pulse** (opacity and glow, about 1 Hz), not a hard on/off blink.
  - Under `prefers-reduced-motion` it is steady (WCAG 2.2.2 / 2.3.1). Assumption: a pulse satisfies "blinking"; a true hard blink is a one-line change.
- **Mobile:** the sider is a drawer, so the existing header `PracticeTag` gets the same pulse and moves to the far left of the header.
- **Login page:** the badge sits top-left once Practice is selected. It comes from `/instance`, which is unauthenticated, so the server still decides.
- The **header tag, banner, title prefix and watermark stay**. The badge is added, not a replacement.

### 3.4 Practice colour scheme
- `theme/themes.ts` gets `practiceLight` / `practiceDark`, derived from the Live themes with overrides; `main.tsx` and the sider's `siderTheme` pick them.
- **Proposed palette (Q15-3):** a deep **plum/violet** sider and header (`#2A1B3D` family), a violet primary (`#8E6CEF`), and amber reserved for the Practice badge and stripes.
  - I advise against an all-amber theme: Live's primary is **gold `#D4AF37`**, and amber next to gold is not "instantly different".
  - Red, green and orange stay semantic (error, success, warning).
- The login background gradient gets a violet variant.
- **Which signal drives the colour:**
  - The theme follows `CURRENT_ENV`, known synchronously at load, so there is no Live-coloured flash on a Practice page.
  - The banner stays server-driven. A mismatch still shows the red banner and every write is refused, so a wrong colour can never go unnoticed.
- E2E: Practice pages have the practice class and badge; Live pages have neither; reduced-motion shows no animation.

---

## 4. Track 4 — Garnet: Old vs New surface benchmark (slice 15d)

### 4.1 What your workbooks say now (saved 13:28 today)

**`For_1_SQM.xlsx`**: two new rows (47, 48).

| Code | Sub-activity | Substrate | System | SAP | Material | For_1_SQM | UOM | Package size |
|---|---|---|---|---|---|---|---|---|
| `ESC1` | `ESC1` | CONCRETE SUBSTRATE | Blasting Civil Floor & Wall | 1429 | AUSTRALIAN GARNET 30/60 MESH | **18** | KG | **2000** |
| `ESC2` | `ESC2` | TANK/VESSEL | Blasting Steel Surface | 1429 | AUSTRALIAN GARNET 30/60 MESH | **20** | KG | **2000** |

**`Materials_DetailsAvailable_Qty.xlsx`**: new row, 1429 `Garnet`: KG, available 54 000, ordered 74 000, received 54 000, package size **1000**.

**`CNCEC_Inventory.xlsx`**:

| SAP | Name | UOM | Unit Size | Stock | Material Code |
|---|---|---|---|---|---|
| 1429 | AUSTRALIAN GARNET | TON | **1000** (was 1) | 9 | GI-7003055 |
| 1363 | AREEJ GARNET | TON | **1** | 0 | |

Inconsistencies to settle (Q15-4):
- **Package size** is 2000 in For_1_SQM, 1000 in Materials, and 1000 in Inventory. Unit Size wins per Q14-4, so the app uses 1000 KG per TON. The 2000 is only a warning, but it is probably a typo.
- **1363 AREEJ Garnet** is in no recipe and keeps Unit Size 1, so an AREEJ draw would have no benchmark and be counted in TON.
- **Material Code clash:** 1429's `GI-7003055` is also on **SAP 1001 "WATER STORAGE TANK 10000 L"** in the Inventory sheet. The sync keeps it on 1001 and imports 1429 without a material code. Garnet is matched by SAP so it still works, but the workbook has a clear error.
- **Old/New:** the workbook gives **one** figure per substrate. The **Old/New split exists only in the new setup page.**

### 4.2 How the app works today (facts that shape the design)
- **Equipment:** 85 rows, one `Lining_System_Code` per row. The `Type`/`Substrate` pairs are `CV`/CONCRETE SUBSTRATE ×54, `ME`/TANK ×17, `ME`/VESSEL ×14. They map one-to-one onto ESC1 (concrete) and ESC2 (tank/vessel).
- **ESC1/ESC2 already exist** as the **blasting man-hour norms**:
  - `sme_manpower_norm` rows with `Lining_System_Code` = `Execution_Sub_Activity_Code` = ESC1 (3 variants) and ESC2 (1).
  - They are "system-agnostic" (surface prep) by the rule **"a norm is surface prep when NO recipe line names its code"** (`planner.py:240`, same test as `/execution/activities` `manpower_only`).
  - Blasting entries post to **`sme_surface_prep_progress`**, never to lining `Done_SQM` (`execution.py:631`). That table has 0 rows today.
- **The grouped queue (14c)** offers as candidates **only the codes the equipment itself carries** (`sme_groups.suggest`). No equipment carries ESC1/ESC2, so Garnet rows always show **"not in recipe"**. HOD approval credits `sme_sqm_progress.Done_SQM` for (tag, code) and snapshots `Bench_For_1_SQM`; variance uses `variance_pct` / `classify` with `DEFAULT_TOLERANCE_PCT = 10`.
- **Units:** Surface Shields convert pack → base with `Unit_Size` (`services/units.py`). 1429 becomes 1000 KG per TON once synced.
- **History:** 19 Garnet draws, 47 TON in total, on 14 tanks, all currently unattributed in the queue.

### 4.3 ⚠️ Hazards if today's workbooks are synced before 15d

| # | What happens | Effect |
|---|---|---|
| **H1** | The recipe rows ESC1/ESC1 and ESC2/ESC2 make blasting "named by a recipe", so the planner counts ESC1/ESC2 as **lining codes** and `/execution/activities` drops `manpower_only` for blasting. | The man-hour plan changes; the supervisor's blasting entry form asks for a lining system; blasting area could post to **lining progress**. This is exactly the class of bug `planner.py` warns about (the "25× too high" prep case). |
| **H2** | Inventory runs **before** sme-materials in the same sync, so 1429 gets Unit Size 1000 but no seed row yet. `default_base_uom` then treats TON as "its own base", giving **Base_UOM = TON with a factor of 1000**. | Garnet stock shows as **9000 TON** until a second run, when the seed row exists and gives KG. The rule itself is wrong: a measure pack is only its own base when Unit Size is 1. |
| **H3** | Every "system codes" list built from `sme_recipe` (Man-Hours, Execution, consumption form, SME Master) gains ESC1/ESC2. | Supervisors can pick "ESC2" as if it were a lining system. |
| **H4** | The GI-7003055 clash (§4.1). | Warning only. |

The interim command at the top of this file avoids H1 and H3; running it twice avoids H2. Slice 15d fixes all three in code.

### 4.4 Design

**(a) "Surface prep" becomes explicit data, not a naming rule.**
- New table **`sme_prep_baseline`** (alembic, both instances):

  | Column | Meaning |
  |---|---|
  | `id` | key |
  | `Prep_Code` | `ESC1` \| `ESC2` |
  | `Substrate_Class` | `CONCRETE` \| `STEEL_VESSEL`, derived from the code and stored for readability |
  | `Surface_State` | `OLD` \| `NEW` |
  | `Material_Group` | `GARNET` |
  | `KG_Per_SQM` | numeric > 0, nullable until set |
  | `updated_by`, `updated_at` | who and when |

  Unique on `(Prep_Code, Surface_State, Material_Group)`, giving **4 rows**. Every edit is audited (old → new).
- **One helper**, `services/prep.py::prep_codes(session)`: a code is surface prep when it has a baseline row **or** is a system-agnostic norm code. Every caller uses it:
  - `planner.lining_codes`
  - `/execution/activities` `manpower_only`
  - the Man-Hours, Execution, consumption-form and SME Master system lists
  - `sme_groups.suggest`

  This fixes H1 and H3 permanently. Blasting stays surface prep even with Garnet recipe lines present.
- **Rule 1c:** if the TS SME engine reads recipe codes, it must exclude prep codes in the **same commit**. The goldens must stay **byte-identical**, because Garnet is not in the estimator (Q15-6).

**(b) Garnet materials** are the SAPs on recipe rows whose code is a prep code (1429 today). If you confirm, 1363 AREEJ joins through a recipe row or an alias (Q15-4).

**(c) Setup page: "Garnet baseline" (one-time, editable later).**
- Lives in SME → Master Data, a new tab; exact-lock `{hod, admin}`, like S6.
- A 2 × 2 grid:

  |  | Old surface | New surface |
  |---|---|---|
  | **Steel / Vessel (ESC2)** | KG/m² | KG/m² |
  | **Concrete (ESC1)** | KG/m² | KG/m² |

- The workbook figure (20 / 18) is shown beside each cell as a reference (Q15-9).
- The first visit is an empty-state "set up once" form.
- Until all four are set, the HOD dashboard shows *"Garnet benchmark not set: variance can't be computed"*.
- Edit history (who, when, old → new) is shown under the grid.

**(d) The Old/New question at assignment.**
- **Grouped job card (14c).** When a day+tag job contains Garnet rows, they are **split into their own "Surface prep — Garnet" sub-card** under the lining job, because blasting area ≠ lining area. The sub-card has:
  - **Prep code** auto-selected from the equipment's substrate (`CV`/CONCRETE → ESC1; `ME`/TANK/VESSEL → ESC2). It is editable only if the tag is ambiguous or unknown.
  - **"Old surface / New surface?"**: a required radio with **no default**. The equipment's last answer shows as a hint (Q15-7).
  - **Area blasted (m²)**: its own field, pre-filled from the store keeper's note if any.
- The **single-row assign** path and the **Execution daily-entry** material lines ask the same question when the material is Garnet.

**(e) Approval and variance.**
- Submit creates an `sme_attribution_group` with `Lining_System_Code = ESC1|ESC2` and new columns `Surface_State`, `Prep_Baseline_ID`.
- The **benchmark is snapshotted** at submit: `Bench_For_1_SQM = KG_Per_SQM`. Editing the baseline later never rewrites past variances; the HOD sees the snapshot and the current value.
- **Formulas:**
  - `expected_KG = KG_Per_SQM(code, state) × area`
  - `actual_KG = Σ packs × Unit_Size`
  - `variance% = (actual − expected) / expected`, through the existing `variance_pct` and `classify`, ±10 %.
- The HOD's over/under notification goes through the existing `dispatch()`.
- On HOD approval the area credits **`sme_surface_prep_progress`** (`Execution_Sub_Activity_Code = ESC1|ESC2`, `Variant_Key = OLD|NEW`), **never** lining `Done_SQM`. Rejection sends it back the same way it does today.
- **No baseline set:** submission is still allowed and marked "no benchmark" for the HOD, the same way "not in recipe" is handled today. Blocking it would stall the store floor.

**Worked example** (illustrative numbers, not your baseline): tank `522-89D0-TNK-001` (ME/TANK → ESC2), *New surface*, baseline 20 KG/m², 3 TON drawn (3 × 1000 = 3000 KG), 140 m² blasted.
- Expected: 20 × 140 = 2800 KG.
- Actual: 3000 KG.
- Variance: **+7.1 %**, within ±10 %.

If the same job were *Old surface* with baseline 28 KG/m², expected = 3920 KG, variance −23.5 %, and the HOD is flagged as **under**.

**(f) Units.**
- Fix `default_base_uom`: a measure pack is its own base only when Unit Size is 1. For TON/MT with Unit Size ≠ 1, the base is the seed's UOM, else KG.
- The sync plans the SME materials seed **before** inventory so the first run is right (H2).
- Suite 14A pins it.

**(g) Import.** The `ESC1`/`ESC2` recipe rows import normally but are classified as prep by (a). The sync report prints *"2 surface-prep recipe rows (Garnet): benchmark comes from the Garnet baseline page"*, and warns on the 2000-vs-1000 package size and the GI-7003055 clash.

**(h) Practice** gets the same migration. The synthetic seed gains one Garnet material, the four baseline rows and a sample blasting job, so the tutorial and trainees can exercise it.

### 4.5 Risks (15d)

| Risk | Mitigation |
|---|---|
| Prep codes leak into lining maths (the H1 class) | One `prep_codes()` helper; a service test per caller; planner man-hours for ESC1/ESC2 pinned to today's values; SME parity goldens unchanged. |
| Double-counting area (lining + blast) | Separate sub-card, separate area field, separate ledger (`sme_surface_prep_progress`). |
| Baseline edited mid-project rewrites history | Snapshot at submit; HOD view shows both values. |
| TON/KG confusion | Base from Unit Size only; the job card shows "3 TON = 3000 KG". |
| A tag's substrate is ambiguous | Prep code is editable only then; the choice is validated server-side against the baseline table. |
| 14 tanks / 47 TON of past Garnet sit in the queue | They appear as Garnet sub-cards after 15d; the supervisor answers Old/New per job. No automatic back-fill. |

---

## 5. Slices, branches, gates, docs

| Slice | Branch | Contents | Proof |
|---|---|---|---|
| **15a** | `fix/phase15a-practice-schema` | Data fix (after Q15-0), boot guard, `practice_db.py migrate` + `verify` checks, `dev.sh` step, inventory Site/Category validation | service_tests; E2E Practice add-item → Inventory, SK pickers, HOD 200s; Live unaffected |
| **15b** | `fix/phase15b-queue-select` | Key-type fix + selection reconcile | `sme-jobs.spec` multi-select cases |
| **15c** | `feat/phase15c-login-practice-theme` | Slab removal, top-band layout, static mark, Tier 0 parity, PracticeBadge, practice themes | `login-3d.spec` (no overlap at 1280×720 / 1024×640 / 390×844, no slab), practice-theme spec, critical-path check +0 B JS |
| **15d** | `feat/phase15d-garnet-baseline` | Migration (both instances), `prep_codes()`, baseline page + API, job-card sub-card, variance and snapshot, units fix, sync ordering and messages, docs | service_tests (prep, variance, units, sync), `parity:sme` unchanged, E2E Garnet flow |

- **Every slice passes all ten gates** in CLAUDE.md §3 before I call it done. A SKIP is not a PASS.
- **Every slice updates `USER_MANUAL.md` and `MANUAL_TESTING_GUIDE.md`** (rule 13). 15d also updates `docs/ARCHITECTURE.md` (prep codes, baseline table) and a new What's-new announcement (14d YAML).
- 15a and 15b are small and independent, so they can ship the same day. 15c and 15d stack after them.
- **No downloads needed** (no Docker, no new npm or pip packages).

---

## 6. Clarifying questions

| # | Question | My default if you just say "go" |
|---|---|---|
| **Q15-0** | May I **migrate both Practice databases now** (backup first, keeps trainee data, about 1 minute)? Should I normalise SAP 800000000 to `CNCEC` / `Consumables`, or will you edit or delete it? | Yes, and normalise it |
| **Q15-1** | Login mark: completely still, or a one-time rise-in on load plus a slow light sweep across the gold (the mark itself never moves)? | Rise-in + slow sweep |
| **Q15-2** | Tier 0: remove the two faint tilted background marks, or keep them very faint? | Remove |
| **Q15-3** | Practice colours: plum/violet with amber badge (my suggestion), or another colour you prefer? Is a soft **pulse** OK for "blinking"? | Violet + amber pulse |
| **Q15-4** | Garnet data: (a) Is For_1_SQM's package size **2000** a typo for 1000? (b) Should **1363 AREEJ Garnet** be treated as Garnet too (Unit Size 1000, same benchmark)? (c) Fix the **GI-7003055** clash with SAP 1001 in the workbook? | (a) yes, 1000 (b) yes (c) you fix it in Excel |
| **Q15-5** | Substrate mapping: `CV`/CONCRETE SUBSTRATE → **ESC1 Concrete**, `ME`/TANK and VESSEL → **ESC2 Steel/Vessel**. Correct? Does rubber-lined steel ever use a different garnet rate? | As written; rubber = steel |
| **Q15-6** | Should Garnet enter the **estimator** (readiness, buy list, "SQM achievable now")? | No in Phase 15; benchmark and variance only |
| **Q15-7** | Is Old/New chosen **per job** (pre-filled from that equipment's last answer), or fixed **per equipment**? | Per job, with a hint |
| **Q15-8** | ±10 % tolerance for Garnet, same as the lining materials, or its own? | Same ±10 % |
| **Q15-9** | The four KG/m² numbers: do you have Old/New for both substrates? Should the workbook's 20 / 18 pre-fill **New** surface (and Old start empty)? | Pre-fill New from 20 / 18; Old empty until you set it |

*Postgres on :5433 is running and in use by your `bin/dev.sh` servers, so I have left it up.*
