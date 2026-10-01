# PROPOSED PHASE 16 PLAN — FEFO Lot Management & the Lot Register workbook

> **Status: APPROVED 2026-10-01 (Q16-1…Q16-16 as defaulted, Q16-12 = (b)) and IMPLEMENTED**
> as slices 16a–16d. Where the build departed from this plan, it is recorded in §9.
> Analysed 2026-10-01 against Live (`gihub`, alembic head `c4f1a8d2e6b7`), `CNCEC_Inventory.xlsx`
> (saved 09:52 today) and `Rubber & Brick Materials  - CNCEC.xlsx` (saved 10:09 today).
> Every number below was measured with a read-only probe; assumptions are labelled.

---

## 0. Summary

| Track | What is true today (measured) | Size | Slice |
|---|---|---|---|
| **1 — the overloaded `Serial No.` column** | On Surface Shield rows the column holds the **batch / lot** (736 of 778 consumption rows, 75 of 119 receipts). On every other row it holds an **equipment / asset tag** (`GI-120237`, `GI-AMF-004`). The sync copies both into `Serial_No` and never into `Lot_Number`. So **Live has 0 lots, 0 receipts with a Lot No. and 0 expiry dates**: FEFO has nothing to choose from and always falls back to "no lot". The Return Log's `Serial No.` is dropped completely. | M | **16a** |
| **2 — the Lot Register workbook** | 30 sheets in 3 layouts. Every row that has a DN No. — **191 of 191** — matches a GI Hub receipt exactly on **SAP + DN No. + received date**. The 207 CHEMOLINE rolls match the roll receipts **day by day** (3, 1, 36, 36, 23, 12, 24, 12, 24, 36). It agrees with the receipts' quantity on all but **2** rows. The data is good enough to link reliably, with **17 inconsistencies** for you to decide (§2.4). | L | **16b** |
| **FEFO surfaces** | The lot balance ignores returns. The Issue form's Lot field is free text, and no screen shows expiry. | M | **16c** |

**The safety principle for the whole phase:** the Lot Register workbook **never moves stock**.
Stock still comes only from the Receipt / Consumption / Return logs. The lot file only *describes*
lots (MFD, expiry, batch reference) and is *cross-checked* against receipts. A mistake in it can
make an expiry date wrong, but it can never double a quantity.

### Your sync-command question

The two commands are **the same**. The first one only adds `cd ~/GI_Hub_Project &&` in front, so it
works from any folder. **Use the first one from now on.**

It does **not** rewrite all the data. It compares the workbook with GI Hub and changes only what
differs:
- rows are labelled by their position in the workbook (`XLSX:…` labels);
- a changed row is updated in place, and a new row is added;
- `--prune-vanished` removes a ledger row only when it has disappeared from the workbook and
  nothing in GI Hub references it.

That is why today's run printed `+0 ~0 =505`. Your Garnet / water-tank fix is in: SAP 1001 now
has no material code, and 1429 keeps `GI-7003055`.

Once Phase 16 ships, the same command will also read the Lot Register workbook if it is in the
root folder. There is no new flag to remember (Q16-15). Running it **before** Phase 16 is safe:
the lot file is simply ignored until then.

---

## 1. Track 1 — `Serial No.` means two different things

### 1.1 What is in the column today (`CNCEC_Inventory.xlsx`, 09:52)

| Sheet | Surface Shield rows | Other rows |
|---|---|---|
| Receipt Log | 75 / 119 filled — batches `3504`, `525106711A21425`, rolls `1O25003382191`, plus `N/A` | 390 / 629 — asset tags `GI-120237`, `GI-PB-15`, `N/A` |
| Consumption Log | **736 / 778** filled — batches + roll numbers | 2 / 4,717 |
| Return Log | 1 / 1 (`1262`, Phenacin A) | 11 / 16 — asset tags |

### 1.2 How the code treats it today

- `bulk_import._LEDGER_SHEETS`: Receipt and Consumption `Serial No.` → `Serial_No`. Return Log:
  **ignored** (`returns` has no Serial or Lot column at all).
- `Lot_Number` is filled only by the app's Receive form or by `post_receipt` (which auto-names
  `LOT-YYYYMMDD-SAP` when only an expiry is given). The sync inserts ledger rows directly, so it
  never creates a lot.
- `lots` (unique on `Lot_Number, SAP_Code, Site_ID`) is **empty** on Live.
- FEFO (`ledger._FEFO_PICK`) picks the open lot with the earliest expiry and remaining quantity
  above 0. Remaining = lot receipts − lot consumption; **returns are not subtracted**. With no
  lots, every issue posts with no lot.
- The SME engines never read lots, so rule 1c and the parity goldens are unaffected.
- QC inspections and MTC records already carry `Lot_Number` (`services/quality.py`), so real
  lots also give QC a real key.

### 1.3 Design — read the column by the item's category

For every Receipt / Consumption / Return row, the sync looks up the SAP's inventory `Category`.
It uses the same exact-match "controlled category" the QC/MTC pipeline uses (`Surface Shields`,
36 SAPs).

| Item | `Serial No.` becomes | `Serial_No` column | `Lot_Number` column |
|---|---|---|---|
| Surface Shield (batch-tracked) | **the lot** | empty | normalised batch (§1.4) |
| CHEMOLINE roll (SAP 1044) | **the roll *and* its lot** | the roll number | the roll's production batch, from the roll register (Q16-3) |
| Anything else | equipment / asset no. | as today | empty |
| `N/A`, `-`, `NA`, blank | nothing | empty | empty |

Rules that keep this safe:
- **The row's identity does not change.** The `XLSX:` label hashes Date, SAP, and DN / Tank /
  Reason, never Serial. Filling `Lot_Number` therefore **updates** the 811 existing rows in place
  (`~`); it never adds a second copy. The dry run must show `+0` on the ledger.
- **Historical consumption keeps the lot you typed.** The sync never runs FEFO on Excel rows:
  re-tagging history would silently rewrite what the floor recorded. FEFO auto-pick applies only
  to issues entered in the app. The 42 Surface Shield consumption rows with no batch stay
  lot-less.
- **The Return Log's `Serial No.` is mapped for the first time.** A Surface Shield return gives
  back to its lot (Phenacin A lot `1262`, RDN#14). An equipment return keeps its asset tag.
  This needs `Lot_Number` and `Serial_No` columns on `returns`.
- **Lots are created from receipts.** For every (Lot, SAP, Site) received, the sync upserts one
  row into `lots`: Received_Date = first receipt date, Status `open`, Source `receipt`. The
  existing unique key makes re-runs converge.
- **A lot consumed but never received is not invented.** It is listed in the sync report and on a
  *Lot exceptions* view (§2.4 #12). The consumption still counts toward stock, as allow-and-log
  FEFO has always done.
- **Garnet (1363 / 1429), Toluene (1418) and Solvent T200 (1051)** carry no batches today and are
  not in the lot file. They simply stay lot-less. Nothing forces a lot onto them.

### 1.4 Lot-number normalisation (one function, used everywhere)

```
trim → collapse inner spaces → upper-case
integer-looking numbers lose the ".0"      3504.0      → "3504"
"A4325" and "A 4325" compare equal          stored as "A 4325" (the lot file's spelling)
```

The **letter O / digit 0** confusion in roll numbers (`1O25…` vs `1025…`) is **not** silently
merged. It is reported as "did you mean `1O25003382157`?" (Q16-12). A number Excel turned into a
decimal (`0.926`) cannot be repaired by code (Q16-11).

---

## 2. Track 2 — the Lot Register workbook

### 2.1 Anatomy (30 sheets, header on row 2, a total in row 1)

| Layout | Sheets | Key columns | Lot? | Expiry? |
|---|---|---|---|---|
| **A. Batch** (26 sheets) | PU MF300 1/3/5 MM COMP A–D, ECO PRIMER A/B, CUMIFURAN powder/syrup, PHENACIN A/ACP, SOLVENT CF-CE, COROFLAKE A/B, CARBON FILLER, CEMENT BC 3004, PRIMER PR 304, HARDENER E-40, SRI Incast 13 CG | `Batch No.`, `Size Of Package`, `MFG. DATE`, `QUANTITY`, `RECEIVED DATE`, `EXPIRY DATE`, `DN No.`, `Materil Code` | yes | mostly |
| **B. Brick / pallet** (3 sheets) | AR BRICKS 30MM, AR BRICKS 40MM, CUMIBON CARBON BRICK 63MM | `Package No.` (`80 OF 327`), `Pallet`, `Location`, `MFG. DATE`, `DN No.`, `Materil Code` | **no** batch | **never** |
| **C. Roll** (1 sheet) | CHEMOLINE 4ECN — 207 rolls | `Order No.` (batch `1O25003382`), `Roll No.` (`1O25003382191`), `SQM`, `Pallet`, `Location`, `Tank No.`, `Lining Date` | per roll | 4 / 207 |

Garnet, Toluene, Solvent T200 and Carbon Bricks 30 MM (SAP 1032) have no sheet. "SRI Incast 13 CG"
is not in the inventory at all.

### 2.2 The relational problem, and how it is solved

`Material_Code` cannot be the key. `GI-8005765/6/7` each cover four or five SAPs (COMP A–D,
two D pack sizes). Only the base SAP carries the code, because `inventory.Material_Code` is
unique. The lot file's own code column is also wrong in places (§2.4 #1).

**Linking is done in three steps. Each step must succeed, or the row is reported and nothing is
written for it.**

1. **Sheet → SAP.** The best key is a **SAP column in each lot sheet** (Q16-14, recommended:
   one column, typed once per sheet). Until that exists, a deterministic resolver is used:
   - match the material family (product name and thickness, e.g. `MF300(3MM)`) against the
     inventory description;
   - match the **component letter** (`COMP C`);
   - match the **pack size** (`10 Kg/Can` vs `2.5 Kg/Can` against `Unit_Size`). Only pack size
     tells 1041-3 from 1041-4.
   - The sheet's Material Code is used as a cross-check: a disagreement is warned about, never
     obeyed.
   - A short alias table covers the two names that do not match the description
     (`AR BRICKS 30MM` → 1036, `HARDENER E-40` → 1046 "HARDNER").
   - Measured: **every row of every sheet resolves to exactly one SAP**, except SRI Incast.

2. **Row → receipt.** Match on **(SAP, DN No., received date)**. Measured: **191 / 191** rows
   with a DN match a receipt. CHEMOLINE has no DN, so it matches on **(SAP 1044, received date)**,
   and the roll count per day equals the receipt quantity on all 10 days. Each lot-file row is
   linked to the receipt row(s) it describes, and the quantities are compared.

3. **Row → lot.** `(normalised Batch No., SAP, Site)` is the lot. The sync **upserts** into
   `lots`: MFD, expiry, batch reference (`Order No.`), DN and Source `lotfile`. It also writes
   the lot onto the matched receipt row's `Lot_Number` when the receipt's own Serial cell was
   blank, and the lot file names exactly one batch for that receipt. The 14 qty-1 receipts on
   `DN 13320` (one per PU / ECO component, 2026-07-11) have **no batch in either file**, so they
   stay lot-less and are listed in the report.

The file is read as **metadata only**. Its quantities are checked, never added. Every run prints
a reconciliation (*illustrative layout — the counts are not measured yet*):

```
▶ lots  (Rubber & Brick Materials  - CNCEC.xlsx)
      sheets 30 · rows 400 · resolved to SAP 399 · matched to a receipt 398
      lots  +N new  ~N changed  =N · expiry from file N · derived from MFD N · none N
      ⚠ quantity: 1041-4 DN 15717 2026-07-07 — lot file 0.25 vs receipt 1
      ⚠ expiry before MFD: HARDENER 525106711A20426 (MFD 2026-01-19, expiry 2018-01-19) — expiry NOT loaded
      …
```

### 2.3 Schema (one migration, additive only)

| Table | Change | Why |
|---|---|---|
| `lots` | + `MFD_Date`, `Expiry_Source` (`file` / `derived` / `app`), `Batch_Ref` (Order No.), `DN_No`, `Source` (`receipt` / `lotfile` / `app`), `updated_at` | describe the lot; know where each date came from |
| `returns` | + `Lot_Number`, `Serial_No` | a return gives back to a lot; an equipment return keeps its tag |
| `lot_units` (new, Q16-3) | `Unit_No` (roll), `Lot_Number`, `SAP_Code`, `Site_ID`, `Received_Date`, `SQM`, `Pallet`, `Location`, unique (`SAP_Code`, `Unit_No`) | the CHEMOLINE roll register: which roll belongs to which batch, and which tank it went to |
| `inventory` | + `Shelf_Life_Months` (Q16-5) | derive an expiry from MFD when the file has none |
| `v_lot_balance` / `SQL_LOT_BALANCE` | remaining = received − consumed − **returned** | a returned can is not still on the shelf |

Every column is also added to `models.py` and to the legacy models-parity allowlist (rule 15,
`bug_check`). There are no data steps: lots are created by the sync, not by the migration.

### 2.4 Inconsistencies found in the files — and how I propose to handle each

| # | What | Where | Proposed handling |
|---|---|---|---|
| 1 | **Wrong Material Code** on two sheets | `PU MF300(1MM) COMP D` says `GI-8005765` (the 3 MM family; 1 MM is `GI-8005766`). `COMP B COROFLAE EP PRIMER` says `GI-6002243` (COMP A's code; SAP 1050 is `GI-6002244`). | Resolve by name + component + size; warn on the code. **Please correct the two cells.** |
| 2 | **Code column mislabelled** | `CARBON FILLER`, `CEMENT BC 3004`, `PRIMER PR 304`, `HARDENER E-40`: header says `Material Name`, but the column holds codes (`GI-6002242`). Header typo `Materil Code` on 25 sheets. | Read both spellings; no action needed. |
| 3 | **Lettered batches vs plain** | COROFLAKE COMP A: `A 4525` (MFD 2025-11-05), `B 4525` (11-06), `C 1823`, `D 1823`. The Receipt Log says `4525 = 55 Cans; 1823 = 2 Cans`. Consumption says `A4525`. | Q16-1 |
| 4 | **Several lots in one receipt cell** | Receipts 1049 & 1050, DN 13342: `4525 = 55 Cans; 1823 = 2 Cans` | Q16-2 |
| 5 | **Same lot, two different dates** | `PU MF300(3MM) COMP A` lot 3504: MFD 2026-03-26 / expiry 2026-12-25 on DN 15717, but MFD 2026-03-16 / no expiry on DN 15807. `5MM COMP C` lot 3504 has MFDs 03-16 and 03-26. | Q16-6 |
| 6 | **Expiry before MFD** | `HARDENER E-40` lot `525106711A20426`: MFD 2026-01-19, expiry **2018**-01-19 (two rows) | Refuse that expiry and report it; probably 2028-01-19 — please correct. |
| 7 | **Expiry missing although MFD is known** | ECO PRIMER A/B (3441), CARBON FILLER `0.926`, COROFLAKE `C/D 1823`, some PU rows | Q16-5 |
| 8 | **Quantity zeroed after a return** | `PHENACIN A SYRUP` lot 1262 DN 13548: QUANTITY **0**, remark "Returned on 14/09/26 RDN#14". The receipt is 20, and the return is already in the Return Log. | Q16-8 |
| 9 | **Quantity disagrees with the receipt** | `PU MF300(3MM) COMP D` 2.5 Kg/Can, DN 15717: lot file **0.25**, Receipt Log **1** (SAP 1041-4) | Q16-9 |
| 9b | **Sample receipts with no batch** | 14 receipts of qty 1 on `DN 13320` (2026-07-11), one per PU / ECO component; the lot file has the same rows, also with no batch | They stay lot-less, and FEFO never picks them. Are they samples? |
| 10 | **Orphan row** | `PU MF300(5MM) COMP A`, last row: only `3504`, MFD 2026-03-26, expiry 2026-12-25 — no material, quantity, DN or date | Skip and report — a note, or a forgotten line? |
| 11 | **Batch number turned into a decimal** | `CARBON FILLER` batch `0.926` (two rows, and in the Receipt Log) | Q16-11 |
| 12 | **Consumption lots never received for that SAP** | `3502` (1042, 1042-1/2/3 — no receipt of 3502 anywhere); 1037 Phenacin A uses `2477` (ACP powder's batch) and 1038 ACP uses `2611` (Phenacin A's) — **swapped?**; 1043 / 1043-1 / 1043-3 (5 MM) use `3504` (a 3 MM batch); 1040 / 1040-1 ECO PRIMER use `3633` (a PU batch); 1041-3 uses `3542`; 1035 carbon brick 63 MM `2815`, `2928` | Import as typed, flag as **unknown lot** in the report and on a Lot-exceptions list; you correct the Excel. Never blocked (FEFO stays allow-and-log). |
| 13 | **Roll number typed with a zero** | One CHEMOLINE consumption row: `1025003382157` (digit 0); the register says `1O25003382157` (letter O) | Q16-12 |
| 14 | **Item not in inventory** | `SRI Incast 13 CG`, 50 × 20 kg bag, DN 13477, batch `023D/2026 AUG.` (batch and MFD in one cell) | Q16-13 |
| 15 | **Bricks have no batch and never expire** | AR 30 MM, AR 40 MM, CUMIBON 63 MM: `EXPIRY DATE` empty on every row; `Package No.` / `Pallet` only | Q16-4 |
| 16 | **Roll sheet's Tank / Lining Date are empty** | All 207 CHEMOLINE rows have no `Tank No.` / `Lining Date`, but the Consumption Log already records which tank each of 190 rolls went to | Idea §7.3 — GI Hub can fill these for you |

---

## 3. FEFO surfaces (slice 16c)

- **Lot balance** subtracts returns (§2.3).
- **Issue form.** The free-text Lot field becomes a **lot picker** for lot-tracked items: open lots
  in FEFO order, each with its expiry, days left and remaining quantity. It defaults to the FEFO
  lot. Picking another lot still asks for the FEFO-override reason (Parity B1). The picker is
  hidden for non-lot items. CHEMOLINE: pick the **roll**; its batch follows.
- **Lot Register page** (Stock → Lots; store keeper, HOD, QC). Per SAP: each lot with MFD,
  expiry (and whether it was *derived*), received, consumed, returned, remaining, and status
  (open, expiring within 30/60/90 days, expired, exhausted). It also lists the lot exceptions
  from §2.4 #12. Filtered by site as usual.
- **Expiry warnings**, using the existing `dispatch()` notifications: one evening-digest line per
  lot expiring within 30 days, to the HOD and the store keeper. Never per lot per day.
- **Receive form.** For a lot-tracked SAP, Lot No. and Expiry become prominent, and MFD is added.
  An expiry before the MFD is refused.
- **Unchanged:** FEFO stays **allow-and-log**, never a hard block (locked 2026-06-30). The stock
  identity, the SME engines and the parity goldens do not move.

---

## 4. Slices, order, gates

| Slice | Content | Data step on Live |
|---|---|---|
| **16a** | Migration (§2.3). Category-aware Serial → Lot in the ledger sync (receipts, consumption, **returns**). Lot normalisation. Lots created from receipts. Lot balance includes returns. Unknown-lot report. | backup → migrate → sync dry run: must show the ledger `~` only, `+0` |
| **16b** | Lot-file reader (3 layouts) → SAP resolver → receipt match → lot upsert, plus the reconciliation report. CHEMOLINE roll register (`lot_units`). Included in the default `--erp` run when the file is present. | dry run reviewed with you before `--commit` |
| **16c** | Issue-form lot picker, Lot Register page, Receive-form MFD/expiry, expiry digest | none |
| **16d** | Practice (rule 17g): a lot file + receipts + one expiring lot + one unknown-lot exception in `practice_overlay`, applied to both Practice DBs. USER_MANUAL, MANUAL_TESTING_GUIDE, announcements. | Practice DBs |

All ten gates run after every slice. New service suite **16A–16C** covers:
- the normaliser on every real shape;
- the category switch;
- **no duplicate rows on a second sync**;
- returns reducing the lot balance;
- the resolver on all 30 real sheet names;
- refusal of an expiry before its MFD;
- the FEFO pick order.

E2E covers the Issue-form lot picker and the Lot Register page. Each slice is committed on its
own branch, as in Phase 15.

### Risks

| Risk | Guard |
|---|---|
| A second sync duplicates ledger rows | The `XLSX:` label excludes Serial/Lot; 16A asserts `+0` on a re-run against the real workbook shape |
| The lot file moves stock | It never writes `receipts` quantities; only `lots` and a `Lot_Number` on an *existing* matched receipt |
| A wrong SAP picked for a sheet | Three-way check (name, component, pack size) plus the code cross-check; ambiguity = refuse that sheet, never guess. Q16-14 removes the guesswork entirely |
| FEFO starts tagging history | Excel rows keep the lot as typed; FEFO auto-pick only on app-entered issues |
| Old Live data before 16a | Nothing changes until the migration and first sync; a backup is taken first |

---

## 7. Ideas (yours to take or leave)

1. **Add one `SAP` column to each lot sheet** (Q16-14). It turns every heuristic in §2.2 step 1
   into an exact lookup, and it is the one thing that makes COMP A–D and the two D pack sizes
   unambiguous for good.
2. **Format the `Batch No.` / `Serial No.` columns as Text** in both workbooks. Excel then
   cannot turn `0926` into `0.926` or drop leading zeros.
3. **The roll register fills itself.** GI Hub already knows from the Consumption Log which tank
   each roll went to, and on which day. The Lot Register page (and an export) can show
   `Tank No.` / `Lining Date` per roll, so you no longer type them into the CHEMOLINE sheet.
4. **A lot check like the stock-vs-Excel check.** A "marked copy" of the lot file, with every row
   GI Hub could not match or that disagrees, highlighted. The original is never touched.
5. **QC on lots.** QC inspections already store a Lot No.; once real lots exist, an expired or
   un-inspected lot can show a badge on the Issue picker.
6. **Shelf life once, per material.** Store it on the item (e.g. PU 9 months, Phenacin 6,
   Furan 12, BC 3004 24). Every missing expiry is then derived and shown as *derived*, never
   presented as if it came from the label.

---

## 8. Clarifying questions — defaults in **bold**; answer "all defaults" or override by number

| # | Question | Options |
|---|---|---|
| **Q16-1** | COROFLAKE `A 4525` and `B 4525` have different MFD / expiry. Are they **two lots**, while the Receipt Log just says `4525`? | **(a) two lots, as the lot file says**; consumption `A4525` → `A 4525` · (b) one lot `4525` (the earliest expiry wins) |
| **Q16-2** | A receipt cell with several lots (`4525 = 55 Cans; 1823 = 2 Cans`, 2 rows) | **(a) you split each into one row per lot in the Receipt Log** (clean, and no hidden splitting) · (b) GI Hub splits it using the lot file's quantities |
| **Q16-3** | CHEMOLINE: what is the lot? | **(a) the production batch (`Order No.` `1O25003382`), with each roll as a unit inside it (roll register)**; FEFO picks the batch, the store keeper picks the roll · (b) every roll is its own lot (207 lots) |
| **Q16-4** | Bricks (AR 30/40, CUMIBON 63) — no batch, no expiry | **(a) not lot-tracked; their sheets are used only for the receipt cross-check** · (b) lot = DN No., FIFO order |
| **Q16-5** | Expiry missing but MFD known | **(a) derive from a per-item shelf life you set once (shown as *derived*)** · (b) leave blank — such lots go last in FEFO |
| **Q16-6** | Same lot, two different dates (3 MM COMP A lot 3504) | **(a) the earliest expiry wins (safest for FEFO), with a warning** · (b) the latest row wins |
| **Q16-7** | HARDENER `525106711A20426` expiry 2018-01-19 | **(a) refuse that date until you fix it (probably 2028-01-19)** · (b) I set 2028-01-19 for you |
| **Q16-8** | Lot-file QUANTITY after a return (Phenacin A lot 1262 = 0) | **(a) keep the RECEIVED quantity in the lot file (20); returns live only in the Return Log** · (b) GI Hub accepts a net quantity and skips the check |
| **Q16-9** | PU 3 MM COMP D 2.5 kg, DN 15717: is it **0.25** (lot file) or **1** (Receipt Log, SAP 1041-4)? | you tell me; **until then the Receipt Log wins** |
| **Q16-10** | `5MM COMP A` orphan row (`3504`, no material / qty) | **(a) skip and report** · (b) it is a real line — please fill it in |
| **Q16-11** | CARBON FILLER batch `0.926` | **(a) you retype the real batch as text (`0926`?) in both files; until then it is kept as `0.926`** · (b) keep `0.926` permanently |
| **Q16-12** | Roll typed `1025003382157` (zero) | **(a) you correct the cell; the sync suggests the near match but never auto-merges** · (b) auto-correct a leading `10` → `1O` on roll numbers |
| **Q16-13** | `SRI Incast 13 CG` is not in inventory | **(a) skip the sheet until you add the item to the Inventory sheet (with its SAP)** · (b) it should not be in this file |
| **Q16-14** | Add an `SAP` column to every lot sheet? | **(a) yes — the sync uses it first and the name rules only as a fallback** · (b) no — names only |
| **Q16-15** | The file name `Rubber & Brick Materials  - CNCEC.xlsx` (two spaces) | **(a) keep it; the sync finds any `*Rubber*Brick*CNCEC*.xlsx` in the root and includes it in the normal `--erp` run** · (b) rename to `Lot_Register_CNCEC.xlsx` |
| **Q16-16** | Who sees the Lot Register page and the expiry digest? | **(a) Store Keeper, HOD, QC, QC-HOD (read) — admin as always** · (b) HOD and admin only |

---

## 9. Implementation notes — where the build departed from the plan

1. **FEFO puts an EXPIRED lot last** (new). Earliest-expiry-first would have
   suggested the COROFLAKE `C 1823` / `D 1823` lots, which expired in 2024 and
   still have stock. `ledger._FEFO_PICK` and the picker share the order: valid
   lots by expiry, then lots with no expiry, then expired lots. An expired lot
   is still selectable (allow-and-log).
2. **Shelf life is learned automatically** where an item has none, as the
   median of its own MFD → expiry pairs in the lot file (PU 9, Phenacin 6,
   BC 3004 24, …). The plan had the operator type it first. An admin or HOD can
   change it in the item editor, and the sync never overwrites a value that is
   already set.
3. **The manual is §3.10, not a new chapter.** Chapter 3 reaches every role; a
   new chapter would have needed the per-role chapter maps, the printed
   booklets and the AI-eval pins changed.
4. **A roll batch's received quantity** is `GREATEST(receipts naming it, rolls in
   the register)`, because the roll receipts carry no roll numbers.
5. **`Serial_No` keeps the cell as typed** for a lot item (the plan said empty).
   The sync never erases a stored value (COALESCE upsert), so clearing it would
   have been a special case; the lot is in `Lot_Number`.
6. **Two migrations, not one:** `d8a3f6c1b2e9` (16a: all lot schema) and
   `e5b2c7a9d4f1` (16c: `pending_receipts.MFD_Date` for the Receive form).
7. **The critical-path baseline was re-recorded once (+229 B)** for the Lots &
   Expiry page's nav entry and route. The lot hooks live in `api/lotHooks.ts`, off
   the critical path.
8. **`sme-tiers.spec` was made more robust:** it clicks its tab until the tab is
   selected (the click was being lost under load).
