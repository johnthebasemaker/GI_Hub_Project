# PROPOSED PHASE 21 — Auto-Pilot, OCR fine-tuning and Enterprise UI polish

*Drafted 2026-10-05 from the operator's brief. **Planning only: no application
code until the operator approves this plan and rules on §9.** Each track says
what exists today (read from the code and the data, not assumed), the design,
the safety rails, the tests and the docs. §8 answers the two questions the brief
asked directly: the voiceover and the Drive conversion.*

The brief, in one line each:

| Track | Ask |
|---|---|
| 1 | Measure the consumption-paper OCR against the workbook; match a written name that is not in stock to the closest item; highlight the suggestion, or the field when there is none |
| 2 | `tools/gdrive_sync.py`: fetch the latest workbooks from Drive → *CNCEC PROJECT Backup*, convert `.xlsm` → `.xlsx`, pick the newest dated Rubber & Brick file |
| 3 | A "self-driving" Practice demo: the UI drives itself (entry → log out → HOD → approve) with subtitles and a voice; the assistant can start it, or say gracefully that it cannot |
| 4 | Lots: no empty lots in *Top 5 Expiring*; sortable headers; a synced consumption naming a missing or used-up lot shows the **sheet and row** |
| 5 | Reorder for Surface Shield: quantity too high, days of cover too low |
| 6 | Turn the 9-skill cheat sheet into a GI-Hub design contract, write it into the rules, and plan a safe polish pass |

---

## 0. What I read, and what it changed in the plan

| Source | Finding that matters |
|---|---|
| `SESSION_HANDOVER.md`, `.claude/RULES.md`, `docs/ARCHITECTURE.md` | Head `c4e9b2a7f613`, gates 2,890 / 190, rules 15, 17/17g, 9, 13, 14, P12-0…6, Q17-1, critical-path zero growth, four workers |
| **The 11 photos** (`WhatsApp … 10.02.59 PM.zip`) | *Safety & Production Consumables* form: S.No · **Name** · Tank No. · Product Name · UOM · QTY · Remarks; date with shift (`01/10/26 (Night)`); heavy ditto marks in every column; colloquial names (*Dust Mash*, *Tyvek coverall*, *Trash Bag*, *12" Fan*, *Clear glass*, *80G sand paper*) |
| **`CNCEC_Inventory.xlsx` → Consumption Log, 1–4 Oct** | ⚠️ The workbook does **not** hold one row per paper line. It holds **one row per (date, item, Work Type, tank) with the quantities summed**: e.g. `2026-10-01 · DUST Mask · EA · R/L · 522-8k80-TNK-071 · 4`. The paper's **Remarks** column is the workbook's **Work Type** (`R/L`, `B/L`, `PU`, `Blast`). The paper's `PV` is the workbook's `PU`. The paper's `K-TNK-071` is the workbook's `522-8k80-TNK-071`. **So the evaluation must compare per-day totals, not lines** (§1.2) |
| The Drive folder (connector) | `CNCEC_Inventory_Smart.xlsm` (956 KB) · `Rubber & Brick Materials  - CNCEC(04-10-2026).xlsx` (two spaces, date suffix; older copies with `(020726)` and `(15-09-2026)` show **two date formats**) · `For_1_SQM.xlsx` · `Equipment.xlsx` · `Materials_DetailsAvailable_Qty.xlsx` · `Manpower_Hour_Details.xlsx` · plus Excel lock files `~$…` that must be ignored |
| `backend/api/bulk_import.py:180`, `services/lot_file.py:127` | Every workbook is read with `data_only=True`, i.e. the **cached values** Excel saved. This decides how the `.xlsm` conversion must work (§8.2) |
| `backend/api/dashboard.py:92` | *Top 5 Expiring* filters `Status = 'open'` and **never looks at the balance**, which is the bug in Track 4 |
| `backend/api/services/smart_min.py:395–493` | Already divides by `Unit_Size`. Two real defects found anyway (§5.1); the rest needs one example from you |
| `frontend/src/pages/OcrImportPage.tsx:61` | The review grid already colours a match `auto` green / `pick` gold / `unknown` red, and `ai/handwritten.py` already has a spec scorer, ditto handling and the shift-in-the-date fix. **Track 1 extends this; it does not start over** |
| The YouTube link | *"9 UI Skills Every AI Developer MUST Learn in 2026"* (The AI Dude – Tamil): the video the cheat sheet summarises. Its transcript service was out of credits, so I worked from the PDF |

---

## 1. Track 1: consumption-paper OCR, measured and matched

### 1.1 What exists

- **Vision:** `qwen2.5vl:7b` (`ai/ocr.py`, `CONSUMPTION_PROMPT`) only transcribes. Every rule after it is deterministic (`ai/handwritten.py`, spec `docs/features/handwritten-ocr/01…11`).
- **Matching:**
  - `ai/fuzzy.py` is the hybrid SequenceMatcher + token Dice scorer (auto ≥ 0.85, pick ≥ 0.45).
  - `handwritten.spec_match` is the spec scorer (auto at confidence ≥ 40 with a lead ≥ 8, top 5).
- **Review grid:** green/gold/red per row, a candidate picker, then staging.
- **Measured speed** (`ocr-real-document-findings`): **≈ 212 s for a 30-row handwritten sheet** on this Mac. Eleven pages take **≈ 40 minutes** a run.

### 1.2 The evaluation harness (`tools/ocr_eval.py`)

"Fine-tuning" here should mean **tuning the prompt and the deterministic rules against measured ground truth**, not re-training the 7B vision model. Re-training needs hundreds of labelled pages, a GPU and a large download, and would produce a model this Mac would then have to serve. Eleven pages is an evaluation set, not a training set.

1. **Inputs.** Photos in a git-ignored folder (`data-archive/ocr_ground_truth/<date>/`). This keeps them off GitHub because they show workers' names (§1.6). The workbook is the ground truth.
2. **Run.** Each photo goes through the **same pipeline the app uses** (`ocr.py` → `handwritten.py` → matcher). The vision output is cached per image hash under `.cache/ocr_eval/`. A rule or matcher change then re-scores in seconds, and only a **prompt** change pays the 40 minutes again.
3. **Compare per day, not per line.** Aggregate the OCR rows to `(date, SAP, Work Type, tank)` sums, exactly the shape of the Consumption Log. Normalise them with the existing tank-alias table and the work-type map (`PV`→`PU`).
4. **Scores, per stage, so a failure is located:**

   | Stage | Metric |
   |---|---|
   | Header | date and shift read correctly |
   | Rows | count, and ditto expansion (a `"` resolved to the row above) |
   | Name → SAP | top-1 accuracy, top-3 recall, "unknown" rate, **wrong-auto rate** (the dangerous one: green and wrong) |
   | Quantity / UOM | exact match after expansion |
   | Day totals | precision / recall of `(date, SAP, work type, tank)` keys, and quantity error |

5. **Output.** A scorecard (`tests/ai_eval/ocr/scorecard.json`, numbers only, **no names**) plus a confusion list: written word → chosen SAP → true SAP.
   - Each prompt or rule change is A/B'd against the cached baseline.
   - **Not a CI gate** (P10-7: the vision answer is stochastic). The deterministic half (aggregation, alias, matcher) on **frozen OCR JSON** *is* a gate, as a new service-test suite **21O**.

⚠️ The ground truth only lines up if **all** papers for a day are present. The workbook total for 1 Oct includes every paper that day. If some papers are missing, the harness reports **coverage** (each OCR total ≤ the workbook total) instead of recall. See Q21-1.

### 1.3 Matching a name that is not exactly in stock

A layered matcher, cheapest first. Each layer says *why* it matched:

1. **Exact** (normalised) → **auto**, green.
2. **Learned alias** (new table `ocr_aliases`: site, written form, SAP, confirmed_by, count).
   - When a store keeper picks or confirms a SAP for a written name, the pair is remembered. *Dust Mash* → `DUST Mask` is learned once.
   - The harness **seeds** the table from the 11 papers: their aligned day totals tell us which SAP each written word became.
   - This is the single biggest win. The store keepers' vocabulary is small and repetitive.
3. **String fuzzy:** the existing scorers, unchanged in contract.
4. **Embedding similarity** (`nomic-embed-text`, the same model as 19d) over the **in-stock** catalogue of that site. It catches *Clear glass* → *SAFETY GLASSES CLEAR* that string scores miss.
   - Vectors for the catalogue are precomputed and refreshed when inventory changes.
   - ⚠️ Memory budget: see Q21-4. I propose building layers 1–3 first, measuring, and adding layer 4 only if the scorecard shows it earns its 578 MB.
5. **No candidate above the floor** → **unknown**, red. The SAP cell is highlighted and says *"Not found in stock — choose the item or type its SAP"*.

**Stock-aware.** A candidate with zero stock at the site ranks below one in stock and is labelled *"no stock at CNCEC"* (spec 06 already says stock is the ground truth). It is never hidden: the store keeper may know better.

### 1.4 The store keeper's screen (OCR import → review grid)

| State | Look | Words on the row |
|---|---|---|
| auto | green tick | *Matched* (or *Learned from 12 earlier sheets*) |
| suggested | **gold cell + the paper's word struck under the suggestion** | *Paper says "Dust Mash" — did you mean **DUST Mask** (94 %)?* · one-click **Accept** / pick another |
| unknown | **red outlined cell**, focus lands here first | *"12" Fan" is not in stock. Choose the item, or type its SAP.* |

- **Navigation:** *Next unresolved* (keyboard `N`). The **Stage** button stays disabled until no red row remains (or the row is removed).
- **Learning:** accepting a gold suggestion records the alias.
- **Hidden columns:** Work Type and Tank show the normalised value with the paper's text as a hint (`PV` → **PU**).

### 1.5 Tests and docs

- **Tests:**
  - Suite **21O**: aggregation, the work-type map, the alias learn/apply/delete, stock-aware ranking, and the "no candidate" floor, on frozen OCR JSON.
  - E2E: a review grid with one gold and one red row; Stage stays disabled until both are resolved.
- **Docs:** USER_MANUAL §3 (OCR review) and MANUAL_TESTING_GUIDE §21a.
- **Practice (17g):** a synthetic photo with invented names, already-learned aliases, one red row.

### 1.6 Privacy

The photos carry **workers' names**. They stay on this Mac (git-ignored), the harness never prints a name, and the Practice example uses an invented sheet (P12-0 spirit). Should OCR also capture the name into the workbook's *Received by*? See Q21-6.

---

## 2. Track 2: Google Drive sync (`tools/gdrive_sync.py`)

### 2.1 What exists

`tools/pg_excel_sync.py` reads workbooks from a folder (`--dir`, default the repo root). It uses fixed names plus one pattern (`*Rubber*Brick*CNCEC*.xlsx`), and **picks the newest by file mtime**. All of them are git-ignored (`*.xlsx`). The root copies are dated 5 Oct 15:09, so today you download and convert by hand.

### 2.2 ⚠️ How the script gets into Drive (decision Q21-7)

The Google Drive connector I used to look at the folder belongs to **this chat session**. A script on the Mac cannot use it, and it stops when the session ends. An automatic script needs its own way in:

| Option | How | Cost / risk |
|---|---|---|
| **A. Drive API, read-only** (recommended) | Create a Google Cloud "Desktop app" client once. The first run opens a browser for your consent. A refresh token is kept in git-ignored `deploy/gdrive_token.json`. Scope `drive.readonly`, and the script only ever lists the one folder ID | About 15 minutes of setup with me guiding you. Small downloads only: it asks Drive for each file's `modifiedTime` first and downloads only what changed (about 1 MB on a busy day; limited internet friendly) |
| B. Google Drive for desktop app | The folder syncs to `~/Library/CloudStorage/…`, and the script reads it like a disk | A large app download; not installed today |
| C. Ask me in a session | I fetch through the connector when you ask | Not automatic |

### 2.3 What the script does

1. **List** the folder (ID `1rhTdX3UuBvwheXZwzBwmCViDkO9EMZR9`, not its name, so a renamed or duplicated folder cannot redirect it).
2. **Pick, per target:**

   | Target in the sync dir | Drive source | Rule |
   |---|---|---|
   | `CNCEC_Inventory.xlsx` | `CNCEC_Inventory_Smart.xlsm` (or `.xlsx`) | newest `modifiedTime`; `.xlsm` converted (§8.2) |
   | `Rubber & Brick Materials  - CNCEC.xlsx` | `Rubber & Brick Materials*CNCEC*(<date>).xlsx` | **newest date in the name**; both `DD-MM-YYYY` and `DDMMYY`, day first; an unreadable date falls back to `modifiedTime` and is reported |
   | `For_1_SQM.xlsx`, `Equipment.xlsx`, `Materials_DetailsAvailable_Qty.xlsx`, `Manpower_Hour_Details.xlsx` | same names | newest `modifiedTime` |
   | — | `~$…` lock files, PDFs, DN/PO/return workbooks | ignored, listed once as "not used" |

3. **Download and validate** each file: it must be a real zip and openable by openpyxl, with the sheets the importer needs (`Consumption Log`, `Receipt Log`, `Return Log`, `Inventory`). A partial download never replaces a good file.
4. **Swap atomically** into the sync dir. The previous copies go to `.backups/workbooks/<timestamp>/` (rollback is a copy back). Only **one** Rubber & Brick file is left in the dir, so `pg_excel_sync`'s pattern cannot pick a stale twin.
5. **Run `pg_excel_sync` as a DRY RUN** and report: *"Drive → 2 files changed (Inventory 05-10 11:25, Rubber & Brick 04-10). Dry run: +38 consumption, +4 receipts, 2 lot problems (sheet/row listed)."* Whether it then **commits** is Q21-8.
6. **Write a manifest** (`.backups/workbooks/last_sync.json`: Drive ID, `modifiedTime`, sha256, converted-from), so a re-run with nothing new does nothing.

**Where it runs** (Q21-9):
- `bin/gdrive_sync.sh`, on demand;
- an Admin Console **Fetch from Drive** button that calls the same code;
- optionally a daily slot behind the `daily_job_runs` claim (four workers; P10-2 fails closed).

**Live only:** Practice never syncs from Drive (rule 17).

### 2.4 Tests and docs

- **Suite 21G**, offline with a fake Drive client:
  - date parsing (both formats, ties, junk);
  - lock-file and stale-twin exclusion;
  - partial-download refusal;
  - manifest idempotence;
  - **conversion fidelity** (§8.2) on a generated `.xlsm` with formulas.
- **Docs:** USER_MANUAL admin chapter; MANUAL_TESTING_GUIDE §21b; `tools/migration/README.md` cross-reference.

---

## 3. Track 3: the self-driving Practice demo

### 3.1 The shape

An in-browser **demo runner** drives the *real* UI. Playwright cannot run inside a viewer's browser, so this is not Playwright; it is a small engine that does what Playwright does, visibly.

- **A visible cursor and a spotlight.** A gold ring moves to the target with a 400 ms ease-out, the rest of the page dims, and the click ripples.
- **Real actions:**
  - clicks go through `element.click()`;
  - typing goes through React's native value setter plus an `input` event, one character at a time;
  - selects open the dropdown and choose the option.
- **Targets are `data-testid`s**, the same ones E2E uses, so a renamed button breaks a test before it breaks a demo.
- **Scripts are data** (`frontend/src/demo/scripts/*.ts`). A script is a list of beats such as:

  ```
  { say: 'The store keeper records {qty} {uom} of {item} for {tank}.', target: 'issue-qty', action: 'type', value: '4' }
  ```

- **Dynamic subtitles.** `{…}` placeholders are filled from what is on screen at that moment: the job's m², the item picked, the HOD's queue count. The **sentences** are written in the script and reviewed in a diff. A model never writes narration (P12-1 / P12-3: a demo must say the same thing every time).
- **Controls:** Pause · Resume · Skip beat · Stop (`Esc`), plus a speed setting (1×, 1.5×). Touching the mouse or keyboard **pauses** the demo ("You took over — Resume?").

**Example, the flagship flow** (`consumption-to-approval`):
- a store keeper stages an issue;
- **Log out → log in as `practice.hod`** (§3.3);
- the HOD opens Approvals, reviews and approves;
- the stock figure updates on screen.

### 3.2 Safety rails (non-negotiable)

1. **Practice only, checked twice.** The runner chunk loads only when `isPractice()` is true **and** `/health` reports `instance = training`. In the Live process the role-switch endpoint does not exist: it is not mounted, so it returns 404 rather than 403.
2. **Zero bytes on the login critical path.** The runner, scripts and subtitles are one lazy chunk, fetched on the first click of **▶ Auto demo**.
3. **Demo data is tagged** (`DEMO-` in the note and reference) and the HOD approves only what the demo created. A **Reset demo data** action removes it, so the shared Practice stays tidy (§3.5).
4. **CI keeps every script honest.** Each script also runs as an E2E spec in the **Practice leg**, headless with the voice muted. A demo that would stall in front of management fails a PR first.

### 3.3 Logging out and in as the HOD

Practice accounts share passwords your trainer holds. The demo must not type a real password on screen, and it must not ship one in JavaScript. Proposal (Q21-13):

- **`POST /practice/demo/switch`**, mounted only when `GI_INSTANCE=training`.
- **Who may call it:** a logged-in `practice.*` user, with a short-lived **demo ticket** issued when the demo starts.
- **What it does:** it swaps the session to `practice.<role>` for one of the roles the script names. Never `practice.admin`. Every switch writes an audit row.
- **On screen:** the runner shows the login page, "types" the username, and fills the password field with dots that are only dots. It then calls the endpoint. The audience sees a real logout and login.

### 3.4 "For maximum all pages"

There are about 50 routes. Two tiers keep this honest and finishable:

| Tier | What | Pages |
|---|---|---|
| **Flows** (auto-execute, cross-role) | Data is entered, roles switch, things are approved | 1 Issue → HOD approval · 2 Surface Shield job → bulk submit → HOD bulk approve · 3 Receive with lot and MFD → Lots & Expiry · 4 Return (partial) → slip · 5 Reorder: set pace → accept minimum · 6 OCR paper → review → stage (uses a cached result, never a live 4-minute vision call) |
| **Tours** (narrated, read-only) | Spotlight on each region of the page, said aloud: what it is, who uses it, what to look at | **every** other page, generated from a registry (route → regions → sentences), so a new page without a tour is listed by a check |

Order of build (Q21-14): flows 1 and 2 first (the Finance demo story), then 3–6, then tours.

### 3.5 The assistant starts a demo

- **Matching:** in Practice, a request like *"show me how to approve a consumption"* or *"do a return for me"* is matched to the demo catalogue. The matcher is the **existing tutorial matcher** (`manual_index`: two-token rule, rule 9 role fence before scoring), over each demo's title and keywords.
  - **No new router intent.** A fifth intent would change the System One prompt, its eval sets and its cache prefix (P18-prefix). The TUTORIAL_SEARCH / UI_COMMAND lanes carry it.
- **Supported:** a **demo frame** with **▶ Run this demo** (and the matching video, if one exists).
  - Why a button and not instant start: browsers refuse to play audio without a user gesture, so the click is also what lets the voice speak.
- **Not supported:** *"I can't show that visually yet — I've sent your request to the admin."* The request is written to the **existing Feedback inbox** (`console.py`, kind `demo_request`) and rings the admin bell. Duplicates are merged per text per day.
- **In Live:** the assistant never offers to *perform* anything. It offers the video/manual as today, plus *"Try it in Practice"*.

### 3.6 Tests and docs

- **Tests:**
  - suite **21D**: the switch endpoint is absent in Live, refuses admin, refuses without a ticket, and audits;
  - demo-data reset;
  - assistant demo/fallback frames and feedback dedupe;
  - router L2 stays green;
  - E2E Practice leg: every flow script runs end to end.
- **Docs:** USER_MANUAL §26 (Practice → Auto demo), the trainer's checklist, MANUAL_TESTING_GUIDE §21c.

---

## 4. Track 4: lot management fixes

### 4.1 Top 5 Expiring shows empty lots

**Cause:** `dashboard.py:92` selects open lots by expiry with no quantity at all. A lot whose stock was all issued keeps `Status = 'open'`.

**Fix:** read from the one balance the Lots page already uses, `stock.SQL_LOT_BALANCE` (received or rolls − consumed − **returned** ± transfers). Keep `WHERE Remaining_Qty > 0`, and show *Remaining* in the widget. Your brief says *Received − Consumed*; the existing balance is that **minus returns**. Using it keeps the dashboard and the Lots page from ever disagreeing.

**Same defect elsewhere:** I will audit every other "expiring" reader for it and fix them in the same slice:
- the 07:00 briefing (`health_monitor.py`);
- `weekly_report.py`;
- `reports.py`;
- the assistant's quick table (`ai/query_router.py`);
- the evening expiry notice (`services/lots.py`).

Expired lots with stock still show (FEFO allow-and-log; somebody has to act on them).

### 4.2 Sorting on the Lots & Expiry page

Every column header becomes sortable:
- Material (A–Z);
- Lot;
- MFD and **Expiry** (date; empty dates always last);
- Status (by urgency: expired → soon → ok);
- the quantities;
- Site.

The default stays oldest-expiry-first. The chosen sort is kept in the URL (`?sort=expiry&dir=desc`), so a shared link opens the same view. The Rolls and *Lots used but never received* tables get the same. The endpoint already returns the whole list, so this is client-side with no API change.

### 4.3 A synced consumption names a bad lot: say the sheet and the row

**Today:**
- `services/lots.unknown_lots` lists lots the logs name that no receipt brought in, *after* the sync, grouped, with **no row numbers**;
- nothing flags a lot that is **already used up**.

**Design:**
1. **In the dry run** (`bulk_import.plan_ledger`, used by both `pg_excel_sync` and Bulk Import), walk each lot in date order and report two kinds of problem:

   | Kind | Meaning |
   |---|---|
   | `unknown_lot` | No receipt of this SAP at this site carries this lot, in the database or in this file |
   | `lot_used_up` | The lot's running balance would go below zero at this row: everything received was already consumed or returned |

   Each problem carries **`Consumption Log`, row 5,581**, the date, SAP, lot, quantity and a hint: *"lot 3504 exists under SAP 1045"*, or *"closest lot: A 4525"* via the existing `norm_lot`.
2. **Before writing:**
   - the CLI dry run prints the list;
   - Bulk Import's preview shows it as a table;
   - the Drive sync's report includes it.
3. **After writing:** two new nullable columns, `Source_Sheet` and `Source_Row`, on `consumption` and `returns`, filled by the sync.
   - This needs a migration, which you run on Live, backup first. The `bug_check` models-parity allowlist is updated.
   - The Lots page card becomes **Lot problems from the workbook**, visible to the store keeper and listing Sheet · Row · Date · SAP · Lot · Qty · Problem · Hint. The row is labelled *"row at the last sync (05-10 11:25)"*, because inserting rows in Excel moves them.
4. **Warn, don't block** (Q21-18). The locked rules say lot exceptions are *"reported, never blocked"* and FEFO is allow-and-log. The consumption still counts toward stock. The commit asks for an explicit *"Push anyway (2 lot problems)"*.

### 4.4 Tests and docs

- **Tests:**
  - suite **21L**: an empty lot is not in the widget; an expired lot with stock still is; sort order with empty dates; `unknown_lot` and `lot_used_up` carry the right sheet and row (including the header offset); a return re-opens a balance;
  - E2E: sort by Expiry descending; the problem card shows a row number.
- **Docs and Practice:**
  - USER_MANUAL §3.10 (Lots);
  - MANUAL_TESTING_GUIDE §21d;
  - Practice: one used-up lot (to prove the filter) and one typo lot with a row number.

---

## 5. Track 5: Smart Reorder calibration for Surface Shield

### 5.1 What the code does, and two defects found by reading it

For a Surface Shield row, `smart_min.compute` takes:

- the plan's remaining base quantity, `remaining_m² × For_1_SQM` (in KG, L, …);
- times the share of it the cover period will use, `min(1, pace × 30 / remaining_m²)`;
- **divided by `Unit_Size`**, giving packs.

So the division the brief asks for exists. It is still wrong in two places:

| # | Defect | Effect you see |
|---|---|---|
| **D1** | When the site has **no SQM pace** (`basis = plan_all`), the minimum is the **whole remaining plan**, and the daily rate is then `whole plan ÷ 30` (`smart_min.py:492`). | **Days of cover far too low**: a year of work is treated as 30 days of use |
| **D2** | With `plan_all`, the suggested order is `min(2 × min, whole plan) − stock`: **order the whole remaining plan now** | **Quantity too high** |

**Other suspects** I can only confirm with your data. Each one would inflate the number:

| # | Suspect | Effect |
|---|---|---|
| (a) | `Unit_Size` holds one component's weight while the recipe's `For_1_SQM` is per **kit** (A + B), or the reverse | Recipe and pack size count different things |
| (b) | The recipe's UOM (`L`) differs from the pack's base (`KG`) | No density conversion between the two |
| (c) | The same SAP sits on two recipe lines of one system | It is counted twice |
| (d) | The pace is a whole-site figure applied to every system's remaining m² | The share is overstated |

### 5.2 The fix

1. **D1/D2.**
   - Without a pace, the recommendation becomes **"set a pace"** rather than "buy everything". The yellow **whole plan** tag from Q18-6 stays, but it no longer drives the order or the days of cover.
   - Days of cover becomes `stock in base units ÷ (pace × For_1_SQM per system)`, in the **same units** on both sides.
2. **A math trail on every Surface Shield row** (the *Why* popover):
   - `412 m² left × 0.85 kg/m² = 350 kg`;
   - `× 30 days at 6 m²/day ÷ 412 m² = 153 kg`;
   - `÷ 20 kg per pail = 7.7 → 8 pails`.

   A wrong number then shows *which* factor is wrong.
3. **A data check** lists:
   - every Surface Shield item whose recipe UOM and pack base disagree;
   - every item with a missing `Unit_Size`;
   - every duplicate recipe line.
4. **Goldens:** three hand-computed items (a pail chemical, a roll, Garnet) pinned in suite **21R**, with you confirming the expected numbers (Q21-20).

`smart_min` is not one of the two SME engines, so the dual-engine parity rule does not apply. If a fix touches `sme_engine.py`, the TypeScript twin changes in the same commit.

---

## 6. Track 6: the Enterprise UI design contract

### 6.1 What the cheat sheet's nine skills actually contribute

| Skill | The principle worth keeping | How it becomes a GI-Hub rule |
|---|---|---|
| frontend-design (Anthropic) | Commit to **one** aesthetic direction before coding; a real type and colour story; no generic centred hero | `docs/DESIGN_SYSTEM.md` names the direction (Q21-21) |
| UI UX Pro Max | Derive a full system (style, palette, fonts, spacing, *what to avoid*) from the product | The "Avoid" list in the contract |
| Taste Skill | **Metric** rules for layout, type, spacing, motion; "redesign existing projects" audits | Numeric tokens: type scale, 4 px grid, radii |
| Impeccable | Work in passes: critique → typeset → colorize → animate → polish | The order of the polish slices (§6.3) |
| Emil Kowalski | Motion with the **right easing and duration**, animate only `transform` / `opacity`; nothing that blocks a frequent action | Motion tokens (`--gi-ease-out: cubic-bezier(0.23, 1, 0.32, 1)`, 150 / 200 / 300 ms), `prefers-reduced-motion` honoured everywhere |
| Interface Design | Write decisions to a file every session reads, so pages do not drift | `docs/DESIGN_SYSTEM.md` + `RULES.md` pointer |
| shadcn/ui | Theme via CSS variables, variants, dark mode | We are on Ant Design: the same idea lives in `theme/themes.ts` tokens + `index.css` variables. **We do not add shadcn** (a second component library) |
| Web Design Guidelines (Vercel) | 100+ pre-ship checks: focus rings, labels, hit targets, reduced motion, number/locale formatting, no layout shift | A pre-ship checklist + an automated `npm run test:design` gate (§6.2) |
| webapp-testing (Anthropic) | Playwright screenshots to *see* the UI | We already have this (E2E + the preview browser); we add screenshot baselines for key pages |

### 6.2 The contract (written into `docs/DESIGN_SYSTEM.md`, enforced where it can be)

- **Direction** (recommended, Q21-21): **industrial luxury corporate**. GI navy and gold on a warm paper/ink pair, the identity of the Finance deck. Quiet surfaces; gold only for the one thing that matters on a screen.
- **Type:**
  - at most two families: IBM Plex Sans for UI, plus a serif for page titles only, if you agree (Q21-23);
  - one scale: 12 / 14 / 16 / 20 / 24 / 32;
  - **`tabular-nums` on every quantity column**, so numbers line up in a stock table.
- **Space and shape:** a 4 px grid (4, 8, 12, 16, 24, 32, 48), three radii (4, 8, 12), one shadow scale.
- **Colour:** tokens only. Semantic status colours (ok, warning, danger, info) with AA contrast in light **and** dark.
- **Motion:**
  - 150 ms for hovers and presses, 200 ms for menus, 300 ms for modals, ease-out on enter;
  - only `transform` and `opacity`, never `transition: all`;
  - no animation on keyboard-repeated actions;
  - everything off under `prefers-reduced-motion`.
- **Interaction:** visible focus ring, hit targets ≥ 32 px on desktop and 44 px on touch, a loading state for every async button, no layout shift when data arrives.
- **Enforced, not just written.** A new gate, `npm run test:design`, fails a PR that adds:
  - a raw hex colour or `transition: all` outside the token files;
  - a duration outside the scale;
  - a `@keyframes` without a reduced-motion guard;
  - a quantity column without the numeric class.

  It runs beside `test:nav` and goes into the gate list in `CLAUDE.md` §3.

### 6.3 Rules and files

- **`.claude/RULES.md`:** a new section, *The UI design contract*, with a short "do not" table:
  - no second component library;
  - no animation that blocks;
  - no colour outside tokens;
  - no new font on the critical path without a measured budget.
- **`CLAUDE.md`:** one line in §2 pointing to it.
- **`~/.claude/CLAUDE.md` is not changed.** It says itself that it holds machine facts only and that project rules belong in the repo. So "global markdown files" here means the repo's `CLAUDE.md`, `RULES.md`, `REPO_MAP.md` and `docs/ARCHITECTURE.md` §5.
- **Installing the skills** (Q21-22): I recommend installing **frontend-design** and **webapp-testing** (Anthropic), plus **Emil Kowalski's** and **Vercel's guidelines**, into the project's `.claude/skills/` at a pinned commit, **after I read every file** (they are instructions an agent will follow, i.e. a supply-chain input). The others duplicate what the contract already says.

### 6.4 The polish pass, without breaking anything

1. **Tokens first:** `theme/themes.ts` + `index.css`. One change re-skins every AntD component consistently.
   - **Screenshot baselines** of 12 key pages in light and dark, taken *before*, are diffed *after*.
   - E2E (190) must stay green throughout, since it asserts behaviour, not pixels.
2. **Shared pieces:**
   - `lib/smartTable.tsx` (every table: numeric alignment, sticky header, empty states);
   - page header, KPI cards, status tags, modals.
3. **Pages:** in order of daily use (Dashboard, Issue / Receive / Return, Execution, Approvals, Lots, Stock, Reorder, then the rest), one PR per group.
   - Each one gets the critique → polish loop, a screenshot in the PR, the critical-path check at +0 B and the reduced-motion check.
4. **The 3D login scene is untouched** (critical-path rule; never `import()` it).

---

## 7. Slices, order and gates

| Slice | Track | Why this order | Migration |
|---|---|---|---|
| **21a** | 4 — lots: widget, sorting, sheet/row | Small, high value, no open questions except Q21-18 | ✅ `Source_Sheet`, `Source_Row` on `consumption` and `returns` (**you run it on Live, backup first**) |
| **21b** | 5 — reorder calibration | Needs your example (Q21-20) | — |
| **21c** | 2 — Drive sync | Needs your Google consent (Q21-7); feeds fresh workbooks to 21d | — |
| **21d** | 1 — OCR harness + matcher + review grid | Needs Ollama for the 40-min baseline run (I will ask before starting it) | ✅ `ocr_aliases` |
| **21e** | 6 — design contract, tokens, shared components | Before the demo, so the demo shows the polished UI | — |
| **21f** | 3 — demo engine + flows 1–2 + assistant hook | The Finance story | — (feedback reuses the existing table) |
| **21g** | 3 + 6 — flows 3–6, tours for every page, page-group polish | The long tail; several PRs | — |

Every slice follows the standing order:
1. Branch, commit and PR. The three required checks run (strict), auto-merge, then `git pull`.
2. All gates must be green: `CLAUDE.md` §3 plus the new `test:design` from 21e.
3. Every slice includes USER_MANUAL and MANUAL_TESTING_GUIDE updates (rule 13) and a Practice example applied to both Practice DBs (17g, overlay v8+).
4. A PR that adds a migration says so at the top.

---

## 8. The two things the brief asked me to explain

### 8.1 The voiceover (Track 3)

**Recommendation: the browser's built-in speech (Web Speech API), on-device voices only, with subtitles always on.**

| Option | Quality | Cost | Data leaves the building? | Verdict |
|---|---|---|---|---|
| **Web Speech API, `localService` voices** | Good on Mac/iPhone (Siri voices) and Windows; plainer on Android | Free | **No**: speech is made on the viewer's device | ✅ **Default** |
| Web Speech, cloud voices (Chrome's "Google …" voices) | Good | Free | Yes: the sentence goes to Google | ❌ Filtered out |
| Pre-recorded clips (made once on this Mac with the macOS voice, shipped as small audio files) | Same voice on every device | Free, about 1–2 MB per flow, lazy-loaded | No | ✅ **Optional add-on** for the Finance presentation, so the voice is identical on the projector laptop |
| Cloud TTS (HeyGen / ElevenLabs / OpenAI) | Best | Paid, needs a key | The script text only (synthetic, P12-1 whitelist) | Only if you want a premium voice (Q21-12) |

**How it fits together:**
- Each beat's sentence is spoken **and** shown as a subtitle. The subtitle is the source of truth, so a muted laptop or a missing voice still works.
- The runner waits for the sentence to finish (or a reading-time estimate when muted) before the next action, so voice and action stay in step.
- **English only**, as Q20-15 ruled for the deck, unless you say otherwise (Q21-16).

### 8.2 The `.xlsm` → `.xlsx` conversion (Track 2)

⚠️ **The obvious way loses data silently.** Opening the `.xlsm` with openpyxl and saving it as `.xlsx` writes formulas **without their last calculated values**. Our importer reads with `data_only=True`, the saved values. Every formula cell, `Current Stock` included, would then read as **empty**. Nothing would error; the numbers would just vanish. (Rule 16's lesson: an exit code is not a result.)

**What the script does instead: a structural conversion.** An `.xlsm` and an `.xlsx` are the same zip format and differ in three places only. The script rewrites those three and copies **every other byte unchanged**: sheets, formulas, cached values, styles, data validation.

1. In `[Content_Types].xml`, the workbook's type changes from `…sheet.macroEnabled.main+xml` to `…sheet.main+xml`.
2. `xl/vbaProject.bin` (the macros) is dropped, with its content-type entry.
3. In `xl/_rels/workbook.xml.rels`, the relationship that pointed at the macros is removed.

**Then it proves the conversion.** It opens the original and the converted file with `data_only=True` and compares **every cell of every sheet**. Any difference aborts and keeps the old file. The macros are not needed for the sync, and your `.xlsm` in Drive is never modified (the token is read-only).

**Fallback:** if a future Excel version adds something unexpected, the script refuses and says so. It does not guess.

---

## 9. Questions for you (recommended answer first)

**Track 1: OCR**

| # | Question | Recommended |
|---|---|---|
| Q21-1 | Are these 11 photos **all** the safety-consumable papers for 1–4 Oct (day and night)? The workbook sums every paper of a day, so missing papers only allow a coverage check | Tell me which dates are complete; send more days later for a bigger set |
| Q21-2 | The paper's **Remarks** = the workbook's **Work Type** (`R/L`, `B/L`, `PU`, `Blast`), and handwritten `PV` means `PU`? Is a night-shift paper dated `01/10` booked on 1 Oct? | Yes / yes |
| Q21-3 | When a store keeper confirms a match, remember it (learned alias) and auto-match next time? Who may delete a wrong alias? | Yes; HOD and admin can delete |
| Q21-4 | Embedding layer (`nomic-embed-text`, ≈ 578 MB) loaded after the vision model finishes. Build it only if the harness shows it beats string + alias? | Yes: measure first |
| Q21-5 | A gold suggestion is never auto-accepted; only exact or learned matches go green? | Yes |
| Q21-6 | The papers have workers' names. Keep the photos only on this Mac (git-ignored), and should OCR fill the workbook's **Received by** with the name? | Keep local; do **not** capture names for now |

**Track 2: Drive**

| # | Question | Recommended |
|---|---|---|
| Q21-7 | How should the script reach Drive: **A** Drive API read-only (one-time Google consent), **B** Drive for desktop app, **C** only when you ask me? | **A** |
| Q21-8 | After fetching: dry-run and notify you (you click **Commit**), or commit automatically? | Dry-run + notify; SME-only kinds may auto-commit, ERP ledger never without a click |
| Q21-9 | When: on demand (command + Admin button), plus a daily run at a set time (when the Mac is awake)? | On demand + daily 07:30 |
| Q21-10 | Files: the six the sync reads (§2.3). Ignore DN, PO, return and received-details workbooks for now? | Yes |
| Q21-11 | Dates in file names are **day first** (`04-10-2026`, `020726` = 2 Jul 2026)? | Yes |

**Track 3: demo**

| # | Question | Recommended |
|---|---|---|
| Q21-12 | Voice: on-device browser speech (free, no data leaves), plus pre-recorded Mac-voice clips for the presentation laptop? Or a paid cloud voice? | On-device + pre-recorded clips |
| Q21-13 | Practice-only role switch without typing a password (§3.3), never to admin, audited? | Yes |
| Q21-14 | Flows first: 1 Issue → HOD approval, 2 Surface Shield bulk submit → bulk approve; then 3–6; then narrated tours of every page? | Yes |
| Q21-15 | The assistant shows **▶ Run this demo** rather than starting by itself (browsers block sound without a click). Unsupported requests go to the Feedback inbox + admin bell? | Yes / yes |
| Q21-16 | English only? Default speed 1×? | Yes / yes |
| Q21-17 | Demo entries are tagged `DEMO-` and cleared by **Reset demo data** (the HOD and admin in Practice can press it)? | Yes |

**Track 4: lots**

| # | Question | Recommended |
|---|---|---|
| Q21-18 | A bad lot in the workbook: **warn and still push** (with an explicit "push anyway"), or **block** the sync until fixed? The locked rules say lot problems are reported, never blocked | Warn |
| Q21-19 | Also close a lot automatically when it reaches zero? | No: a return can bring it back; filter by balance instead |

**Track 5: reorder**

| # | Question | Recommended |
|---|---|---|
| Q21-20 | Please send **one real example**: SAP, site, what the Reorder screen shows (suggested order, days of cover) and what you expected. And confirm `Unit_Size` = base units per pack (e.g. `20` = 20 kg per pail, or 20 kg per **kit** of A + B?) | — |

**Track 6: UI**

| # | Question | Recommended |
|---|---|---|
| Q21-21 | Direction: **industrial luxury corporate** (navy/gold, as the Finance deck) or **minimalist brutalism**? | Luxury corporate |
| Q21-22 | Install frontend-design, webapp-testing, Emil Kowalski's skills and Vercel's guidelines into the project at a pinned version, after I read them? | Yes, those four |
| Q21-23 | Ship IBM Plex Sans (and a serif for page titles) as self-hosted font files, ≈ 120 KB, loaded after first paint? Or keep the system font? | Plex Sans for UI; serif for titles only |
| Q21-24 | Add screenshot baselines for 12 key pages as a **report**, not a gate (pixels change for good reasons)? | Report |

---

## 10. What this plan will not do

- **Retrain the vision model** (§1.2: eleven pages is an evaluation set; prompt and rules are tuned instead).
- **Let a demo touch Live**, or let the assistant perform actions outside Practice.
- **Store a Drive credential in the repo**, or write to your Drive.
- **Block a sync, a receipt or a FEFO pick** (the allow-and-log rulings stand).
- **Add a second component library**, or grow the login critical path.
- **Start or stop shared services without asking** (Ollama for the OCR baseline, Postgres when asleep).

---

## 11. Planned next (ruling Q21-10): DN copies, follow-up workbooks and MTCs from Drive

*Added 2026-10-06 after the rulings. **Planning only.** Track 2's sync reads
just the six workbooks. These three folders come next, as their own slices
after 21g, on the same read-only Drive client.*

What the folders hold (listed through the connector on 2026-10-06; names only,
nothing opened):

| Folder | What is in it | What GI Hub already has to receive it |
|---|---|---|
| **DN for CNCEC** | One workbook per return delivery note, named by number and date (`Return DN#023  (05-10-2026).xlsx`, `RDN# 013.xlsx`), plus photos of cash purchases | `returns."Return_DN_No"`, `delivery_notes` (warehouse → site DNs), the Return Log's `DN. No.` column |
| **Pending Material Follow-up** | Requests made without a PR/PO (mail requests: `Request 22-09-2026.xlsx`, `August Request.xlsx`), received-and-pending tables (`Received and Pending supply … (21-07-26).xlsx`), and the summary `CNCEC_Indents Over all Supply and Pending Details.xlsx` | `pr_master` / PO tables, `services/procurement.py`, the PR-status report. A request without a PR has no home today |
| **MTC** | Mill test certificates (PDF). The batch is often in the name (`… (BNO-3633,3542,3504).pdf`, `Carbon Filler_A1626.pdf`, `… 0426_0926.pdf`); sometimes only inside | `mtc_documents` (SAP, `Lot_Number`, file blob), the QC gate at issue (`assert_qc_cleared`), `lots."Expiry_Date"` with `Expiry_Source` |

### 11.1 DN copies → "open the DN" from any return

- **Index, don't import.** For each `Return DN#NNN (date).xlsx`, the sync keeps
  a row `dn_files` (DN number, date from the name, Drive file ID,
  `modifiedTime`).
- **Linking.** A return line whose `Return_DN_No` matches gets a **View DN** link,
  in Records → Returns and on the Return slip.
- **Mismatches are reported, never fixed:**
  - the DN workbook's lines are compared with the Return Log's lines for that DN
    (SAP + qty);
  - a difference is listed, as Track 4.3 does for lots, with sheet and row.
- **Cash-purchase photos** are listed as attachments only.

### 11.2 Requests without a PR, and the supply summary

- **A new kind of demand: "Request (no PR)".** Read from the request workbooks:
  date, material, qty, requester and the mail reference when present. It sits
  beside PRs in the PR-status report, so "requested → received → pending" covers
  everything ordered, however it was ordered.
- **Received / pending:** computed from the ledger's receipts against the
  request, by SAP and date window.
- **The summary workbook** (*Over all Supply and Pending*) is used as a
  **check**, not a source. Where its pending figure disagrees with GI Hub's, the
  row is listed with sheet and row, as in 21a.
- **Open question for then:** do these requests also reduce the reorder
  suggestion (as on-order quantity does, Phase 19b), or only appear in reports?

### 11.3 MTCs → certificate and exact expiry per batch

1. **Match by name first.** Extract batch numbers from the file name (`BNO-3633,3542,3504` → three batches; `A1626`; `0426_0926` read as MFD 04/26, expiry 09/26) with the Phase 16 `norm_lot`. Each batch that matches a lot attaches the certificate to that lot (`mtc_documents.Lot_Number`).
2. **Then look inside** when the name has no batch. Read the PDF's text layer (`ai/pdf_extract.py`, no vision model) for *Batch / Lot No.*, *Mfg / MFD*, *Exp / Expiry*. A scanned PDF with no text layer is queued for the OCR model (minutes per page) only on request.
3. **Expiry from the certificate.**
   - A certificate expiry fills `lots.Expiry_Date` with `Expiry_Source = 'mtc'`.
   - It ranks above *derived* and the Lot Register file. It never overwrites an expiry typed in the app (the Phase 16 rule stands).
   - A disagreement between sources is listed for QC, not resolved silently.
4. **Coverage report.** A list of Surface Shield lots with **no certificate**, which the QC gate needs (your note: "maybe some materials MTC not available"). QC sees it; nothing is blocked beyond today's gate.

**Why this order:** 11.3 first, since it feeds the QC gate and the exact expiry, which matters most for stock that ages. Then 11.1, a link and a check. Then 11.2, which needs a ruling on reorder.
