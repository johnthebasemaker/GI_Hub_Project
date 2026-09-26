# PROPOSED — Phase 14: Data Pipeline & UI Polish

> **Status: PROPOSAL, awaiting operator approval. No application code has been
> written for Phase 14.** Drafted 2026-09-26 against `main` @ `25a20bd`, after
> reading the updated `CNCEC_Inventory.xlsx` (10:33) and
> `Materials_DetailsAvailable_Qty.xlsx` (10:34), the live mirror, the Phase 13
> services, the Excel sync matcher and the `gi-launcher` 3D intro.
>
> Read with: `PROJECT_HANDOVER.md` rules **1, 1a (amended by Q13-5), 1b, 1c, 3a**,
> Phase 9d ruling **Q1-b**, Phase 13 rulings **Q13-5/Q13-6/Q13-10**, **P12-x**,
> and rule **17**.

---

## 0. The short version

Tracks 1 and 2 are **one problem seen from two sides**. Every Surface Shield
quantity in this system is in one of two units:

* **pack units** (Can, Bag, ROL): what the store counts, what the Excel log
  records, what the ledger holds;
* **base units** (KG, M², EA): what the recipe, the SME seed and the benchmark
  use.

Nothing converts between them today. Three latent defects follow from that and
from the per-material queue. I found all three while reading, and **none has
touched live data yet**:

| # | Defect | Where | Why it has not bitten yet |
|---|---|---|---|
| **D1** | The Phase 13 variance compares **cans** (`consumption.Quantity`) against **KG** (`For_1_SQM × SQM`). A 4.5-can draw of BC 3004 (40.5 kg) is measured against a kg benchmark as "4.5", so every Surface Shield variance percentage is wrong, and most will read as large *under*-use. | `sme_link.assign()` | `sme_consumption_log` is **empty** on the live mirror |
| **D2** | The printed QR form's UOM column shows the **recipe** unit (KG), and `execution.post_stock` posts the written number straight into a ledger kept in **cans/bags**. A supervisor who writes "40 kg" as the form asks deducts **40 cans** (360 kg). | `consumption_form.py` → `execution.post_stock` | **0** `SME_EXEC` rows and **0** execution entries live |
| **D3** | Each attributed material credits its **own** SQM to `Done_SQM` on approval. A PU job attributed per material (4 components × 13.37 m²) credits **53.48 m²** to the vessel. | `sme_link.decide()` → `credit_done_sqm` | no attributions live |

So Phase 14 can change the model **before any attribution, QR entry or
unit-converted figure exists**: no data to migrate, nothing to re-credit. That
window is the strongest argument for doing Tracks 1 and 2 first and together,
and for not running the Surface Shield attribution queue in production until
14a/14b land.

**Recommended order:** 14a units (Track 2, fixes D1 and D2) → 14b equipment and
reconciliation (Track 1 de-dup) → 14c the grouped queue (Track 1 UI, fixes D3) →
14d announcements and tutorial staleness (Track 4) → 14e 3D login (Track 3).

---

## 1. What the data says

### 1.1 Inventory sheet (`CNCEC_Inventory.xlsx` → Inventory)

* New columns **`Unit Size`** (col O) and **`4.33kg For 1 SQM`** (col P). `Unit
  Size` is filled on **exactly the 35 Surface Shield rows and no others**.
  `4.33kg For 1 SQM` is **empty on every row** (Q14-8).
* The workbook spells the category **"Surface Shield"** (singular). The live DB
  has **"Surface Shields"** (35 rows), and the sync already maps it. The
  classifier stays `inventory.Category` through `quality.controlled_category()`
  (ruling Q13-10), never the workbook spelling.
* `Unit Size` is the **base units per pack in the SME's unit**, not always kg:

  | SAP | Item | Pack UOM | Unit Size | SME UOM |
  |---|---|---|---|---|
  | 1045 | BC 3004 | Can | 9 | KG |
  | 1044 | Chemoline 4E CN rubber sheet | ROL | **11** | **M²** |
  | 1042-2 | Cumicrete PU 1 mm Comp C | Bag | 6 | KG |
  | 1036 | AR bricks 30 mm | EA | 1 | NO (each) |
  | 1418 | Toluene | KG | 1 | KG |
  | 1363 / 1429 | Garnet 30/60 | TON | 1 | — (not in SME seed) |

* **Multi-component systems share one Material Code across SAPs** (`GI-8005766`
  = SAP 1042/1042-1/-2/-3, sizes 2.52 / 2.86 / 6 / 10). So the factor is keyed
  on the **SAP** (rule 1: `(Material_Code, SAP_Code)`), never on the material.
* Current stock is fractional (158.5 cans, 12.43 bags): part-packs are real.

### 1.2 Cross-check against the SME seed (`Materials_DetailsAvailable_Qty.xlsx`)

The SME seed is **already in base units** (`UOM` KG / M2 / EA / NO) and carries
its own **`Package Size`**. **`Unit Size` equals `Package Size` for all 32 SAPs
the two files share.** Gaps to resolve (Q14-8):

* **1041-4** (Cumicrete 3 mm Comp D, 2.5 kg can) and both **garnets** are in
  Inventory but not in the SME seed.
* **SAP 1000** is in the SME seed but not in Inventory.
* **1041-1**'s description says "2.82 kg" while both files say **2.86**.
* The **recipe's** `Package_Size` disagrees. SAP 1038 Phenacin powder is **1**
  in `sme_recipe`, **25** in both workbooks. There are three copies of one fact
  today; Phase 14 should make one of them authoritative.

### 1.3 Consumption Log, Surface Shield rows

* **686** Surface Shield rows in the workbook (live mirror: **624**; the file
  has not been synced since it was updated). Dates run from 2026-05-18 to
  2026-09-24. **190** rows have fractional pack quantities (e.g. 0.23 can).
* **`Tank No.`** matches `sme_equipment.Equipment_Tag_No` **exactly** for most
  rows (`J091` 187, `J050` 90, `522-8J10-TNK-091` 74 …).
  * ⚠️ `J091` (*Granulation Aids, tank area*) and `522-8J10-TNK-091`
    (*Micronutrient Feed Tank*) are **different equipment**, not aliases.
  * Values that are **not equipment**: blank (33), `Others`, `R/L`,
    `Sample Plate`, `SAMPLE PIECE`, `Sweep blast`, `B/L` (Q14-9).
* **A tag carries several systems** (`J091`: LSC1, 2, 6, 7, 8). The system code
  therefore cannot come from the tag; it has to come from **which materials
  were drawn** (§3.3).
* **`Remarks` already carries the answer in free text**:
  *"Floor – 13.37 SQM Done"*, *"LS LSC5 …"*. The existing
  `hint_system_code()` reads `LS <code>`; an SQM hint reader is the natural
  twin (a pre-fill, never the record).
* **`Work Type` is blank on 278 of the 686 rows.**

### 1.4 How the ledger de-duplicates today (rule 3a)

`bulk_import._claim_ledger_rows` matches workbook lines to DB rows inside a
**(day, SAP, Tank No.) group**. App-written rows (`SME_EXEC:`) may **absorb** an
identical workbook line, and a quantity disagreement becomes a **CONFLICT**
(reported, never overwritten). That is the right foundation, but it leaves
**four double-count paths** for Surface Shields:

| Path | What happens today |
|---|---|
| **Tank No. spelled differently** on the form vs the workbook (or blank / "Others") | different group → the workbook line is **inserted** → the drum is deducted twice |
| **Split quantities** (form: 9 cans on one line; workbook: 4.5 + 4.5) | first 4.5 → CONFLICT, second 4.5 → **inserted** → 4.5 cans deducted twice |
| **Date differs** (night shift filed next morning) | different group → **inserted** |
| **Excel first, QR second** (the order the operator described) | `post_stock` never looks at the ledger → **deducts again**; the XLSX row also stays in the attribution queue and can be credited a second time |

---

## 2. The invariants Phase 14 must hold

Everything below is designed to make these true, and each becomes a named
service-test check:

| # | Invariant |
|---|---|
| **L1** | For any **bucket** (Site, Date, Equipment, SAP), the ledger deducts **one** source's quantity: never the QR claim **plus** the Excel line. |
| **L2** | A vessel's `Done_SQM` is credited **once per job** (Site, Date, Equipment, System), however many materials the job drew. |
| **L3** | A consumption row is attributed **at most once**. A row covered by a QR execution entry never enters the queue. |
| **L4** | Every Surface Shield comparison is made in **one unit**. Variance is `base_actual / (For_1_SQM × SQM)`, with `base_actual = pack_qty × Unit_Size`. |
| **L5** | **Readiness maths does not move** (rules 1a, 1b, 1c; Q13-5). `sme_inventory_seed` is already in base units, so tier 1/tier 2, `Status`, `Completion_Pct` and `SQM_Achievable_Now` are **byte-identical**. Only the observation `Consumed_Qty` and the variance change unit, from wrong to right. |
| **L6** | Re-running the Excel sync, re-uploading a QR form, or replaying an offline submit **converges** to zero writes. |

---

## 3. Track 2 — the Unit Size multiplier (do this FIRST)

### 3.1 The decision: the ledger stays in packs; base units are derived in ONE place

**Recommended: convert at READ, store packs.** The ledger (`consumption`,
`receipts`, `returns`, `lots`, stock views) keeps the **pack quantity**, which is
what the store counts, what the workbook logs and what FEFO lots hold. One
function, used everywhere, derives the base quantity:

```
base_qty(sap, pack_qty) = pack_qty × Unit_Size(sap)   -- Surface Shields only
```

This comes in two twins, a SQL expression and a Python function, defined in
**one module** (the `EXCLUDE_SELF_SQL` pattern), plus a TypeScript formatter for
display.

**Why not convert at write (store kg in the ledger):**

* **Rule 3a breaks.** The Excel sync matches workbook lines (in packs) against
  DB rows by **quantity**; stored kg would never match again, and every re-sync
  would see every Surface Shield row as "quantity edited".
* **FEFO lots, over-issue warnings, minimums and valuation** are all per pack
  today, and all would change meaning.
* **624 historical rows** would need a one-way migration with no undo.

Convert-at-read touches **none** of the ledger arithmetic. Convert-at-write
touches all of it.

**Where the multiplied value is used and shown** (the operator's list):

| Surface | Change |
|---|---|
| **SME estimator** | `Consumed_Qty` is summed in base units. The attribution **snapshots** `Unit_Size_Used` and `Base_Actual_Qty` at the moment of attribution, like `Bench_For_1_SQM`, so a later Unit Size edit cannot rewrite last month's variance. **Both engines** receive base units as input; engine arithmetic is unchanged; parity golden unchanged (rule 1c, verified by `npm run parity:sme`). |
| **Inventory dashboard / Stock / Material Card** | Surface Shield rows show **"21 Can · 189 KG"**, base first if Q14-5 says so. Stock value is unchanged (Unit_Cost is per pack). |
| **Receipts / Issue / Return forms** | Entry stays in packs (what is physically handled, Q14-6), with a live "= 40.5 KG" read-out beside the quantity box. |
| **HOD approvals, Records, Entry Log** | Surface Shield lines gain the base-unit figure. |
| **Reports and exports** | A **Base Qty / Base UOM** column for Surface Shield lines (xlsx and PDF), numbers never defused (rule 12). |
| **Smart Calculator / Session Builder** | Already counts packages from the recipe's `Package_Size`; switched to the authoritative `Unit_Size`. |
| **QR paper form (fixes D2)** | The QTY column asks for **packs** (Q14-3). The small print carries "pack 9 KG". `post_stock` posts packs, and the execution variance uses `base_qty`. |

### 3.2 Schema and sync

* `inventory.Unit_Size` (Float, nullable) — **Alembic migration + `models.py`
  in the same commit** (rule 15, second half). `inventory.Base_UOM` (Text,
  nullable), the unit `Unit_Size` converts to (KG / M2 / EA), taken from the SME
  seed's UOM when present.
* The sync maps the **`Unit Size`** header by name (all columns resolve by
  header, so this is additive). ⚠️ With the COALESCE rule, a blank cell never
  erases a stored size.
* **Validation report on every sync:** Unit Size ≠ SME `Package Size`; a
  Surface Shield row with no Unit Size; Unit Size ≤ 0; a description whose
  "(9KG)" disagrees with the column (catches 1041-1's 2.82/2.86). Reported, not
  blocking (the P10-2 fail-direction argument: a sync that refuses a workbook
  over a label typo stops the ledger).
* **One authority (Q14-4):** Inventory `Unit Size`. The SME seed `Package Size`
  and `sme_recipe.Package_Size` become **checked copies**; a mismatch is a
  sync warning, never silently preferred.

### 3.3 Risks specific to Track 2

| Risk | Mitigation |
|---|---|
| A Surface Shield SAP has **no Unit Size** | `base_qty` returns **None, not the pack count**. The UI shows "— (no unit size)", variance becomes "cannot compute" (the same stance as `recipe_rate` returning None), and the sync reports it. Never silently treat 1 can as 1 kg. |
| **Unit Size edited later** | Attributions snapshot the factor they used. Live displays use the current factor. Pack sizes that change per delivery should become a new SAP (the workbook already does this: 1041-3 at 10 kg vs 1041-4 at 2.5 kg). |
| **Mixed units in one PU system** (A/B in cans, C in bags, D in cans) | Handled per SAP; each component converts with its own factor. |
| **Garnet (TON) and bricks (EA)** | Unit Size 1, so the conversion is a no-op; the base UOM is shown as-is. |
| **Consumed_Qty meaning changes** (cans → kg) | Nothing is live (§0). The column header gains the unit, and the MANUAL states it. |

---

## 4. Track 1 — Excel vs QR de-duplication and the grouped SME intake

### 4.1 Equipment resolution (the grouping key has to be trustworthy)

* A small **`equipment_aliases`** table, (Site, raw Tank No.) → `Equipment_Tag_No`
  or **`NON_EQUIPMENT`**. It is seeded automatically from exact, case-insensitive
  matches against `sme_equipment` (which covers the bulk of the 686 rows today).
* Unresolved values (blank, `Others`, `R/L`, `Sample Plate` …) appear in a
  **"Map these equipment names"** strip at the top of the queue. They are mapped
  **once** by an HOD and never asked again. `NON_EQUIPMENT` rows (sample plates,
  sweep blasting) are **excluded from SQM attribution** but stay in the ledger
  and the stock (Q14-9).
* The resolver is used by **both** the sync's bucket key and the queue's group
  key, from one module, so the two can never disagree about what "J091" is.

### 4.2 The grouped queue (the operator's streamlined UI; fixes D3)

**Group key = (Site, Date, Equipment).** One card per group:

```
┌ 24 Sep 2026 · J050 — Micronutrient Area (TRAIN J) ─────────────────────────┐
│ Materials drawn                              Packs    Base      Benchmark*  │
│  Cumicrete PU MF300 1mm Comp A   (1042)      4.5 Can  11.34 KG  …           │
│  Cumicrete PU MF300 1mm Comp B   (1042-1)    4.5 Can  12.87 KG  …           │
│  Cumicrete PU MF300 1mm Comp C   (1042-2)    9 Bag    54.00 KG  …           │
│  Cumicrete PU MF300 1mm Comp D   (1042-3)    0.23 Can  2.30 KG  …           │
│ System code  [ LSC10 — PUL1  ▾ ]  ✓ suggested: all 4 materials in recipe  │
│ SQM done     [ 13.37 ]  ← read from Remarks "…13.37 SQM Done"               │
│ Variance: A −3% · B +1% · C +4% · D −9%          [ Submit to HOD ]          │
└───────────────────────────────────────────────────────────────────────────┘
```
\* the benchmark column is `For_1_SQM × SQM`, recalculated live as the SQM is typed.

* **One submission** writes **one `sme_attribution_group`** row (Site, Date, Tag,
  System, SQM, status) plus **one log row per material** (its own pack qty, base
  qty, expected, variance, snapshots). Each log row references `group_id`.
* **`Done_SQM` is credited once, on group approval** (L2). Per-material log rows
  carry the group's SQM **only for their variance**, never for area credit.
  `consumed_sqm` sums **per group**, not per row.
* **The HOD approves or rejects the group as a whole**, with a per-material
  flag. The Phase 13 rejection loop, "edited in Excel" staleness and
  fingerprints carry over at **group** level: a group whose member row was
  edited in Excel returns badged, as a row does today.
* **Split and merge.** A group occasionally holds two jobs (a primer for system
  A and an adhesive for system B on one tag on one day). "Split" moves chosen
  materials into a second card with its own system and SQM (Q14-10). The
  default is one system per group.
* The per-row API stays for **single rows** (a group of one), so nothing that
  calls it breaks.

### 4.3 The system-code suggestion

For each group: candidates are the systems **the tag carries**
(`sme_equipment`), ranked by:

1. **recipe coverage**, the fraction of the group's SAPs that appear in that
   system's recipe (a PU 1 mm job drawing all four 1042-x components scores 1.0
   for **LSC10 (PUL1)** — the only system whose recipe holds them, and one J050 carries — and 0 for J050's rubber-lining codes);
2. the **`LS <code>` hint** in Remarks (existing `hint_system_code`);
3. **recency**, the system most recently approved on this tag.

The top candidate is pre-selected for one-click submission. A tie or coverage
below 1.0 shows the dropdown **open**, with the reason ("2 of 3 materials are in LSC1's recipe; Toluene is not"). **It is a suggestion; the pair is re-validated
server-side** exactly as `assign()` validates tag × system today.

### 4.4 QR ⇄ Excel reconciliation — the idempotency strategy

**The bucket.** Key = (Site, Date, resolved Equipment, SAP), computed by one
function used by the QR path, the sync and the queue. It is deterministic, so
every re-run lands in the same bucket.

**Who owns what (recommended, Q14-1):**

* **QR (execution entry) owns the ATTRIBUTION**: system, tag, SQM, from the
  paper.
* **The ledger holds ONE quantity per bucket** (L1). The first writer posts it;
  the second writer **reconciles** instead of posting.

**The two orders, both covered:**

| Order | What happens |
|---|---|
| **QR first, Excel later** (the order the sync partly handles today) | Excel lines in the bucket are **summed** (fixes split quantities) and compared with the QR rows' sum. **Equal** → all absorbed, nothing inserted. **Excel > QR** → only the **difference** is inserted, as an XLSX row auto-attached to the entry's group (extra drums on the same job, no second SQM). **Excel < QR** → **CONFLICT**: nothing inserted, the HOD is notified, and the QR quantity is corrected through the entry (the existing rule: an execution entry's posted quantity is corrected through the entry). |
| **Excel first, QR later** (**not handled today**) | `post_stock` reads the bucket first and **adopts** the XLSX rows already there: links them to the entry, attributes them from the form, removes them from the queue, and posts only **QR − Excel** if positive. Excel > QR → CONFLICT, as above. |

**A reconciliation record per bucket** (`consumption_reconciliation`: bucket,
qr_qty, xlsx_qty, status `matched | excel_extra | qr_extra | conflict |
resolved`, by and at). It makes the outcome **auditable** and the re-run
**idempotent**: an unchanged bucket is a no-op, and a changed one re-evaluates
from the sums, never from the previous decision.

**Date tolerance (Q14-2).** Exact day only for automatic merging. A same-tag,
same-SAP bucket on **±1 day** is reported as a **"possible duplicate"** for the
HOD, never auto-merged, because two genuine consecutive-day draws are ordinary.

**Units.** Buckets compare in **packs** (both sides are packs once D2 is fixed),
with a 0.01-pack tolerance for rounding.

**What this amends, stated plainly:**

* Rule 3a (*app rows are never rewritten*) **stands**. The QR row is never
  rewritten; the Excel side is absorbed or posted as a delta.
* Ruling **Q1-b** (Phase 9d: *the execution entry is the ONLY writer for lining
  consumption*) is **extended**. The entry becomes the only writer **for its
  bucket**, and it now defers to a bucket the workbook already filled. This is a
  **ruling change you would sign** (Q14-1).

### 4.5 Track 1 risks

| Risk | Mitigation |
|---|---|
| Two genuinely separate jobs on one tag, one day, one SAP (morning and evening crews) | Both go to one bucket, which is correct for **quantity** (the sums still reconcile). For **attribution**, "Split" (§4.2) separates the SQM. |
| A mis-mapped alias merges two vessels | Aliases are HOD-set, audited and editable; a remap re-buckets and re-reconciles (L6). |
| The Excel sync runs while a group is awaiting HOD approval | The existing staleness fingerprint moves to group level: a changed member row returns the group, badged "edited in Excel". |
| The conflict queue is never looked at | Conflicts go to the Morning Briefing (the health monitor already probes for absences) and to the HOD bell. |

---

## 5. Track 3 — 3D glassmorphism login

### 5.1 What the launcher does, and what GI Hub can afford

`gi-launcher`'s intro is a scroll-driven **Three.js r186** scene: the GI mark
extruded from `gi-mark.svg` (10 KB SVG) over `backdrop-filter` glass panels. It
has a still-frame fallback under `prefers-reduced-motion` or without WebGL,
renders only while visible, and drops to 24 fps when idle. Its vendored Three.js
is about 2 MB unminified.

GI Hub's **login is the first thing every user loads**, on warehouse tablets and
the Android/iOS apps too. Today's critical path is about **385 KB gzipped**
(`index` + `client`).

### 5.2 Recommendation: progressive and tiered, with a hard budget

| Tier | Who gets it | What | Cost on the critical path |
|---|---|---|---|
| **0 — Glass (always)** | everyone | CSS glassmorphism card (`backdrop-filter`, gold rim, soft noise) over an **inline SVG GI mark** with a CSS 3D tilt / float (`transform-style: preserve-3d`, layered SVG for depth). Parallax follows the pointer, or device orientation on tablets. | **~3–6 KB** CSS/SVG, **0 KB JS**. The sign-in form is interactive at the same moment as today. |
| **1 — WebGL (upgrade)** | desktop, WebGL available, no reduced motion, not a native shell, not a low-memory device (`navigator.deviceMemory` ≥ 4) | The extruded gold mark in a glass scene, loaded by `import()` **after first paint and idle** (`requestIdleCallback`). The canvas cross-fades in over tier 0. | **0 KB** on the critical path. A lazy chunk (Three.js from npm with tree-shaking, plus the ported `logo.js`) budgeted at **≤ 180 KB gzipped**, enforced by a build check. |

**Performance guards, carried from the launcher's rules:**

* renders only while the login is visible;
* stops **entirely** on sign-in (the scene is disposed, not hidden);
* caps at 30 fps and drops to 0 when idle;
* never loads on any route except `/login`.

Ollama shares the GPU on the Mac, and the same idle discipline applies.

**The "scrolling" part.** A sign-in page has nothing to scroll, and making a
store keeper at 06:00 scroll to find the password box would be the wrong trade.
So on the login the motion is **pointer or tilt driven**. The launcher's
scroll-driven storytelling fits the places below, where people read rather than
act.

### 5.3 Where else 3D and scroll effects would earn their place

**Recommended (read-and-understand pages with a management audience):**

1. **Executive Summary / Board brief**: a scroll-driven narrative (headline
   KPIs → SME readiness → buy list → valuation floor) with glass section cards.
   This is the page closest to the launcher's purpose.
2. **"What's new" announcement panel** (Track 4): a glass card carousel, one
   card per feature, each with a "Watch the tutorial" link.
3. **Training Hub landing**: a glass header with the GI mark and module tiles.
4. **SME Lining Coverage**: an optional **3D vessel/area progress** view, shaded
   by done / buildable-now / pipeline and strictly obeying rule 1b's tier
   segregation (buildable-now and with-ordered **never merged** into one fill).
   ⚠️ This is the one place a pretty visual could re-introduce the 21.5 %
   overstatement, so it is **Phase 14+ at the earliest**, behind its own review.

**Not recommended:** entry forms, approvals, tables, the Store Keeper's pages
or anything used on a tablet in the field. They must stay dense and instant;
rule 5's `smartTable` pages lose more to animation than they gain.

### 5.4 Asset reuse

`gi-mark.svg` / `gi-logo.svg` (traced from the PNG, 99 % pixel overlap) and
`logo.js`, the SVG-to-Three.js shape parser with no DOM, which also runs under
Node. Three.js is **MIT**. It comes into GI Hub **from npm** (tree-shaken by
Vite), not as the launcher's vendored copy. The CSP is unchanged, since
`script-src 'self'` covers a bundled chunk.

---

## 6. Track 4 — targeted feature announcements and tutorial sync

### 6.1 Announcements that reach only the people they concern

**The audience comes from the navigation matrix, not from a new list.** An
announcement names the **route(s)** the feature lives on. Its audience is
`canAccessPath(route)` for each role, computed server-side from the same
manifest `npm run test:nav` pins (rule 14), optionally narrowed by site. There
is no second access list to drift from the first; the P12-6 argument (reuse the
fence, never re-implement it) applied to notifications.

* **Authoring (Q14-13): as code.** `docs/announcements/<date>-<key>.yaml`
  holds title, body, routes, optional roles, optional tutorial module and
  optional manual section. It is reviewed in the PR that ships the feature and
  loaded at deploy. An admin can **preview**, **publish**, **schedule** or
  **retract**. This is the same "a person approved this sentence in a diff"
  shape as P12-1.
* **Delivery:**
  * the **bell** (existing `dispatch()`, category `feature`);
  * a **"What's new" panel** on the user's next sign-in, shown once and
    dismissible, with read receipts in `feature_announcement_reads`;
  * optionally **WhatsApp**, which needs a Meta-approved template (Q14-13).
* **Rule 13 tie-in.** An announcement links to its `USER_MANUAL.md` section. A
  test fails if the linked section does not exist.
* **Practice (rule 17).** Announcements are deployed into both databases, since
  a trainee needs to see what is new too. Read receipts are per database.

### 6.2 Flagging tutorials that the app has outgrown

Each tutorial manifest already records its **routes** (declared and actually
visited), its **git SHA**, its **script hash** and its **dataset version**
(P12-5). That is enough to detect staleness **mechanically**:

* `tools/tutorial_staleness.py` maps each manifest's routes to the page
  components that render them (the `App.tsx` route table plus their imports).
  It then diffs those files **since the manifest's git SHA**. A tutorial whose
  pages changed is **"possibly stale"**, with the changed files listed.
* **When it runs:** in the deploy pipeline, and on demand from Admin Console →
  Training. The result is an **admin bell notification** ("3 tutorials may be
  out of date: sk_stage_return_v1 — IssuePage.tsx changed …") and a column on
  the Training Hub admin view.
* **It never gates** (Phase 12: *tutorial renders are NOT a gate*). It is a
  warning with a named reason.
* An announcement can declare `tutorial: <module_key>` and `rerender: true`,
  which raises the same flag explicitly when the author already knows.
* **A correction to the brief:** re-recording a tutorial is
  **`tools/generate_tutorial.py`**. `make_tutorial_db.py` only builds the
  synthetic dataset, and it changes only when the **data** a tutorial shows must
  change. The flag names the right command. Per P10-6/Q4, the module's
  `version` bumps **only** when the narration script's hash changes, never for
  a cosmetic re-render.

---

## 7. Implementation slices

| Slice | Content | Gate additions |
|---|---|---|
| **14a — Units** | `inventory.Unit_Size`/`Base_UOM` (+ migration + models), sync mapping and validation report, `base_qty` SQL/Python/TS twins, SME `Consumed_Qty` and `assign()` variance in base units with a snapshot (**D1**), QR form asks for packs and `post_stock` posts packs (**D2**), pack + base display on stock/entry/approvals/records/exports. | new suite (**14A**): conversion twins agree; None when no factor; variance on the BC 3004 example; readiness byte-identical with and without Unit Size (L5); parity 1334 unchanged |
| **14b — Equipment and reconciliation** | `equipment_aliases` plus the auto-seed, the bucket function, sync bucket summing (split quantities), `post_stock` adopting existing XLSX rows (Excel-first order), `consumption_reconciliation`, conflict notifications, the ±1-day possible-duplicate report. | suite (**14B**): all four double-count paths of §1.4 as regressions (each must FAIL on today's code); both orders; re-run converges (L6) |
| **14c — Grouped queue** | `sme_attribution_group`, group submit/approve/reject, `Done_SQM` once per group (**D3**), system-code suggestion, SQM hint from Remarks, split/merge, group-level staleness and rejection loop, the new queue UI. | suite (**14C**): L2 on a four-component job, L3, suggestion ranking; E2E: one-click submit of a PU group |
| **14d — Announcements and tutorial staleness** | tables, loader, bell and What's-new panel, nav-matrix audience, staleness tool and admin view. | suite: audience = nav matrix for every role (a role added later fails it); staleness on a synthetic diff |
| **14e — 3D login** | Tier 0 CSS glass plus inline SVG; tier 1 lazy WebGL chunk; budget check in the build. | build check: critical-path bytes unchanged, lazy chunk ≤ 180 KB gz; E2E: sign-in works with WebGL disabled and under reduced motion |

Every slice runs every gate in `.claude/RULES.md` §Gates, updates
`USER_MANUAL.md` and `MANUAL_TESTING_GUIDE.md` (rule 13), and, where a slice
touches Surface Shield numbers, **both SME engines together** (rule 1c).

---

## 8. Risk register (ledger maths first)

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | A unit conversion leaks into **readiness** | Critical | Rule 1a/1b fence. The seed is already in base units, so conversion touches only `Consumed_Qty` and variance. Byte-identical readiness check (as DB-19 does today). |
| R2 | Converting at **write** by mistake somewhere | High | One conversion module; a source guard (as suite BA's) greps for `× Unit_Size` outside it. |
| R3 | **Reconciliation deletes or rewrites** a QR row | High | Never rewrites app rows (rule 3a); only absorbs or posts deltas; suite pins it. |
| R4 | **Double SQM credit** survives in an edge path (per-row API) | High | The per-row API becomes "a group of one" and goes through the same group credit function; a single writer for `credit_done_sqm` stays. |
| R5 | Bucket **alias errors** merge vessels | Medium | HOD-owned mapping, audited; remap re-reconciles. |
| R6 | 3D login slows first load | Medium | 0 KB on the critical path; budget enforced in the build. |
| R7 | Announcements spam or leak (wrong roles see a feature) | Medium | Audience from the nav matrix (fails closed like rule 14); show-once receipts. |
| R8 | The workbook's **62 new Surface Shield rows** enter the old per-row queue before 14c | Low | Nothing live uses the queue yet; hold the Surface Shield queue (not the sync) until 14c (Q14-15). |

---

## 9. Clarifying questions

**Track 1 — reconciliation and the queue**

* **Q14-1 — Which source owns the QUANTITY when QR and Excel disagree for the
  same bucket?**
  * *Recommended:* the ledger holds one quantity; the first writer posts it;
    Excel extra → delta row; QR extra → conflict for the HOD, corrected through
    the entry. This extends ruling Q1-b.
  * *Alternatives:* Excel always wins (QR becomes attribution-only and never
    moves stock), or QR always wins (Excel lines in a QR bucket are ignored).
* **Q14-2 — Date matching:** exact day only, with ±1 day reported as "possible
  duplicate" (*recommended*), or allow a ±1-day automatic merge?
* **Q14-9 — Tank No. values that are not equipment** (blank, Others, R/L,
  Sample Plate, Sweep blast, B/L): exclude from SQM attribution but keep in stock
  (*recommended*), or require a real tag for every Surface Shield draw?
* **Q14-10 — A group containing two systems:** allow "Split" in the UI
  (*recommended*), or always one system per (date, equipment)?
* **Q14-11 — Who submits and who approves groups:** supervisor submits, HOD
  approves the whole group (*recommended*, as Phase 13)?
* **Q14-15 — Your updated workbook:** run the Excel sync now (only ledger rows
  land; Unit Size is ignored until 14a) or hold it until 14a? Either is safe
  for stock; I'd sync now so the ledger is current, and keep the Surface Shield
  attribution queue unused until 14c.
* **Q14-16 — The Phase 13 per-row queue:** retire it in favour of groups
  (keeping the API as a group of one), or keep both views?

**Track 2 — units**

* **Q14-3 — The QR paper form:** should supervisors write **packs** (Can/Bag,
  as the store and the workbook do; *recommended*) or **KG**? And has anyone
  already filled paper forms in KG? If so they need a one-off note before
  upload.
* **Q14-4 — One authority for the factor:** Inventory **`Unit Size`**
  (*recommended*), with the SME seed's `Package Size` and the recipe's
  `Package_Size` checked against it?
* **Q14-5 — Display:** show both ("21 Can · 189 KG", base first) or the base
  value only?
* **Q14-6 — Entry:** store keepers keep entering **packs** with a live KG
  read-out (*recommended*), or enter KG?
* **Q14-7 — Minimum_Qty / low-stock for Surface Shields:** stay in packs as the
  workbook has them (*recommended*)?
* **Q14-8 — Data to fix or confirm:**
  1. what is the new **`4.33kg For 1 SQM`** column for (it is empty)?
  2. SAP **1041-4** and the two **garnets** are missing from the SME seed;
  3. SAP **1000** is in the SME seed but not in Inventory;
  4. is **1041-1** 2.86 kg (the column) or 2.82 kg (the description)?

**Track 3 — 3D**

* **Q14-12 — 3D login:**
  1. tiered (CSS glass for everyone, lazy WebGL on capable desktops;
     *recommended*) or CSS-only?
  2. should the **native apps** get WebGL, or tier 0 only?
  3. which of §5.3's pages do you want: the Executive Summary, What's-new and
     Training Hub landing are my picks?

**Track 4 — announcements and tutorials**

* **Q14-13 — Announcements:**
  1. authored **as code** in the feature's PR plus an admin publish button
     (*recommended*), or an admin-only UI?
  2. in-app bell and What's-new only, or **WhatsApp** too (needs a new Meta
     template approved)?
* **Q14-14 — Tutorial staleness:** a warning to **admins** only
  (*recommended*), or also to the module's audience ("this video may show an
  older screen")?

---

## 10. Things done in this session that bear on Phase 14

* **Docker is installed and working** (Homebrew `docker` + `docker-compose` +
  **colima**, the open-source VM runtime). I used it instead of Docker Desktop,
  which needs an admin password for its helper and a licence acceptance.
  * It verified the Practice deploy: compose with and without the profile,
    `nginx -t`, and a live nginx container.
  * It found a real defect: the compose file rejected Live deploys whenever
    Practice was unconfigured. Fixed in PR #87.
  * Phase 14 can now build and run the production images locally.
* **PR #87 (open, CI green):** offline-queue replays are idempotent (one
  `Idempotency-Key` per submission) and the compose fix above. It is relevant
  here because Track 1's "converges on re-run" invariant (L6) has the same
  shape, and the reconciliation reuses the same claim-then-fill discipline.
