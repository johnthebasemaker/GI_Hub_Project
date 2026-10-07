# PROPOSED PHASE 22 — Deep Drive integration, advanced OCR, final polish

*Drafted 2026-10-07 from the operator's brief (both the brief and the original
notes it was written from, `MyPrompt.md`) and `PHASE21_SUMMARY.md` §3.
**Planning only: no application code until the operator approves this plan and
rules on §8.** Every "today" below was read from the code, the Live database
(read-only), the Drive folder (names, plus 11 sample files) or the 8 new
photos. None of it is assumed.*

| Track | Ask |
|---|---|
| 1 | Drive: read the **DN**, **Pending Material Follow-up** and **MTC** subfolders; DN photos on received items; pending vs received for general items; MTC → lot by batch. Pull time set in the UI, a **Pull from Drive** button at the top, and **Last updated from Drive** on every page |
| 2 | OCR: learn the site's **tank list**; read **Day / Night** and fill **Prepared by** (Night = Kalied, Day = Johnson, editable per site); measure on the 8 new photos; the §4.3 names |
| 3 | The remaining **153 raw colours**; make the Practice queue seed **idempotent** |
| 4 | Everything still open in `PHASE21_SUMMARY.md` §3 |
| + | From the original notes: point to the rows behind each **lot that doesn't exist**, so you can fix them later; check that **Drive files arrive correctly** |

---

## 0. What I read and measured, and what it changed

### 0.1 The 8 new photos (5–6 Oct)

| Paper | Shift written | Lines | Workbook rows that day (not Surface Shield) | Prepared by in the workbook |
|---|---|---|---|---|
| 5 Oct, 1 page | none | 20 | 20 | **Johnson** (all 20) |
| 5 Oct, 3 pages | "(Night)" | 30 + 30 + 19 = 79 | 79 | **Kalied** (all 79) |
| 6 Oct, 1 page | none | 24 | 24 | **Johnson** (all 24) |
| 6 Oct, 3 pages | "(Night)" | 30 + 30 + 6 = 66 | 66 | **Kalied** (all 66) |

1. **Your rule holds for every line:** 189 of 189.
   - Night written next to the date → Kalied.
   - Nothing written → Day → Johnson.
2. **⚠️ This corrects Phase 21.** The Consumption Log is **one row per paper line**, in paper order, with *Received by* = the Name column. That is true from at least 26 Sep onward. Phase 21's harness assumed rows were summed per day, which is why its "finest key" score was so low (0.08–0.18).
   - Phase 22 scores **line by line**.
   - The 11 old photos are re-scored from cache, with no new vision run.
3. **Tank column as written:** `89D0-TNK-001` (also read as `84D0` / `S4D0`), `K-TNK-091`, `K-TNK-071`, `J027`, `J050`, `J091`, `others`. The workbook stores the official tag.

   | Written | Official tag |
   |---|---|
   | `K-TNK-091` | `522-8k10-TNK-091` (Train K) |
   | `K-TNK-071` | `522-8k80-TNK-071` |
   | `89D0-TNK-001` | `522-89D0-TNK-001` |
   | `J027` | `J027` |

   Most lines carry a ditto mark (″ ‚ ·) for the tank, not the tank itself.
4. **Remarks = Work Type**, as in Phase 21: `R/L`, `B/L`, `PU` (written `PV`), `Blast`, `Buffing`, `others`.
5. **The two ⚠️ names from §4.3 are now answered by the papers:**
   - "Tyvek coverall" is handwritten like **"Tyneh coverall"**, so *"wrench overall" → 1367 Tyvek Coverall* is **right**.
   - The papers write **"Safety coverall L"**, "(XL)" and "(XXXL)", so *"safety coverall 2" → 1263 L-SIZE* is a misread **L**, also **right**.

   All 11 proposed names look correct.
6. **Earlier preparers.** Four other *Prepared by* names appear in earlier months. The names change over time, which matters for Q22-16.
7. **Surface Shield rows** on those days sometimes carry a *Prepared by* (2 rows on 6 Oct). They are not on the papers and stay out of the comparison.

### 0.2 Live database (read-only, 2026-10-07)

| Fact | Value |
|---|---|
| Migration head | `e2a8c4f6b1d9` (Phase 21) |
| Consumption rows (CNCEC) | 6,139, **all** with sheet + row: your ERP commit landed |
| Lot problems | **36**. Every one names its sheet and row (e.g. *Consumption Log row 759, SAP 1042, lot 3502; closest lot 3508*), on **Lots & Expiry → Lot problems from the workbook** |
| Drive run record (`drive_sync_last`) | **none**. The command-line run doesn't record itself, so a "last updated" banner would be blank today |
| `mtc_documents` | 0 |
| Surface Shield lots | 50 |
| Official tank tags (`sme_equipment`) | 14: nine `522-…-TNK-…` and `J021 J022 J027 J050 J091` |
| `sme_tank_alias` | 13 mapped, 2 ignored, 5 unresolved |
| OCR Import → Stage | sends **no Tank_No**, and **Issued_By = the store keeper's login**. The HOD screen uses Issued_By to notify the submitter, so the shift name **cannot** simply go there |

### 0.3 Drive: `CNCEC PROJECT Backup` (names listed; 11 small files opened in my scratchpad only)

| Folder | Contents | What links it |
|---|---|---|
| **DN for CNCEC** (163) | 107 `DN# <no>[-ddmmyyyy].jpeg/pdf`, 28 `Cash Purchase <n>.jpeg`, 5 `Other Cash Purchase - n`, 15 return DNs (`RDN# 0nn`, `Return DN#0nn (date).xlsx`), 3 equipment photos | **Receipt Log `DN. No.`**: 104 of 107 DN photos match a receipt DN. Receipt DN values `CP 8` = *Cash Purchase 8*, `GI/RLP/SAR-348` = *DN# GI-RLP-SAR-348*. The **DN. Copy** column already holds paths like `DN for CNCEC\DN# 15623-29042026.pdf` (56 rows) |
| **MTC** (24) | `… (BNO-3633,3542,3504).pdf` (CUMI), `AB25000xxxx_4924-A1-3.1-BC 3004.pdf` (supplier batch `A1 4924`), `MTC - CUMIFURAN FN POWDER - Batch - 2802, 2826, …`, `CUMI FN 2802.jpeg`, `Carbon Filler_A1626`, `AR BRICK MTC - 40MM 1st container` | **Batch No.** in Rubber & Brick. A dry match on names alone covers **≈ 23 of 50** Surface Shield lots, with 3 to check. ⚠️ Two false hits prove a bare 4-digit suffix is unsafe (`0426` "matched" `A20426`). The PDFs have a text layer (`Batch No : 3504`, `Chargen Nr. / A1 – 4924`): **no vision model needed** |
| **Pending Material Follow-up** (6) | Request workbooks (`Request 22-09-2026.xlsx`, `August Request.xlsx`, `Material Request 03-08-26 (upd 10-08-26).xlsx`), two received-and-pending tables, and `CNCEC_Indents Over all Supply and Pending Details.xlsx` | **Material Code** (GI-7…) in most, SAP in one; *Req. Qty*, one or more *Received on dd/mm/yy* columns, *Pending Qty*, PR# or *Without PR*. **Four different layouts** |
| Waste Disposal (10) | Disposal record, photos, return DNs | Not asked for (Q22-22) |

Other findings:
- **One Drive download timed out** (`httpx.ReadTimeout`) and the retry worked. Today's sync has no retry. Crawling about 200 files needs retries and backoff.
- You changed `docs/GDRIVE_SETUP.md` locally: the `mv` line, since your file was named `client_secret.json`. It is not committed yet. I also owe the guide the **Branding page** step. Both go in 22a.

---

## 1. Track 1 — Drive

### 1.1 Schedule, Pull button, freshness (slice 22a)

**What exists:**
- Admin Console → **Drive sync** card: Run now and Commit ERP;
- one daily run at `GI_DRIVE_SYNC_AT` (env var, default 07:30), claimed once per day;
- runs are recorded in `app_settings.drive_sync_last`, but **only** when started from the API.

**Design:**

1. **Schedule in the UI** (Admin Console → Drive sync → *Schedule*):
   - one or more times a day (proposed **07:30 and 19:30**, Q22-2), plus an on/off switch, stored in `app_settings.drive_sync_schedule`;
   - the loop re-reads the setting every minute, so no restart is needed;
   - each time slot is claimed once (`dailyjob.claim("drive_sync@19:30", date)`), so two workers never run it twice;
   - the env var becomes only the default;
   - every change is audited.
2. **Pull from Drive** button in the top bar (admin and HOD, Q22-3).
   - It runs the same `run_once` as the card, with a spinner, and a toast when done: *"3 files changed · ERP: 41 new rows · 0 problems"*.
   - If a run is already going, it says so.
3. **Last updated from Drive** in the top bar, for every signed-in user. It comes from a new read-only `GET /drive/freshness`, open to all roles.

   | Shown | Meaning |
   |---|---|
   | `Drive · 07:30 today` (navy) | last successful check; tooltip: each workbook's Drive *modified* time, when its data reached GI Hub, and when the ERP side was last committed |
   | amber | last success older than 26 h, or ERP changes waiting for your Commit |
   | red | the last run failed, or the Google token is invalid/expired (with the fix: *re-run `--auth`*) |
   | `Practice data` (grey) | in Practice, which never syncs from Drive (rule 17) |

4. **Every run records itself.** The CLI, the button and the schedule all write the same `drive_sync_last`/history row, so the banner is never blank after a terminal run like today's.
5. **ERP auto-commit (Q22-1).** Today a scheduled run only dry-runs the ERP side (ruling Q21-8: you commit).
   - Proposed: a scheduled run **commits by itself when the dry run only *adds* rows** (new days of paper). It never edits or removes existing rows.
   - A run that would edit or remove rows, or create a new stock mismatch, **waits for your Commit**: amber banner, plus a bell notice to admin.
   - Lot problems never block (Q21-18); they are listed.
6. **Files arrived correctly (your note).** Each run stores, per file:
   - the Drive `md5Checksum`;
   - size;
   - the `.xlsm → .xlsx` value check, which exists;
   - sheet row counts.

   The Drive card shows a per-file line, ✅/❌, and *"Consumption Log 6,139 → 6,201 rows"*. A file that shrank by more than 2 % is refused, and the old copy kept (the existing rule is "refused" for a bad file).
7. **Retries:** 3 tries with backoff on each Drive call, and a run-level time budget.

### 1.2 The three subfolders (slices 22b–22d)

**Shared plumbing (22a):**
- **Recursive listing:** `drive_files` index table (migration). It holds the Drive id, folder, name, mime, size, md5, modifiedTime, `kind`, the parsed key and date, the local cache path, and the link status.
- **Downloads** are only for new or changed md5s. They go into a git-ignored cache (`.cache/drive/`, about 40 MB today), served through an authenticated endpoint.
- **Read-only, still.** The token stays `drive.readonly`, and Live only: Practice gets dummy files (§6).
- **Index, don't import.** A DN photo is never copied into `entry_attachments` blobs.

**22b — DN photos on received items (and returns)**
- **Link rules, in order:**
  1. the receipt's **DN. Copy** path when present (exact file);
  2. else `DN_No` ↔ `DN# <no>[-date]` (digits compare);
  3. `CP <n>` ↔ `Cash Purchase <n>`;
  4. `GI/RLP/SAR-348` ↔ `DN# GI-RLP-SAR-348`.

  One DN may have several files (`DN# 15724` and `DN# 15724 - 1`), and all are shown.
- **Where it shows:**
  - a **📎 DN** button on Records → Receipts lines;
  - the Material Card's movements;
  - Lots & Expiry (the receipt that brought a lot in);
  - the PR status report.

  It opens an in-app viewer (image or PDF) with *Open original in Drive* for admins.
- **Returns:** the Return Log's `DN. No.` is **not imported today** (`returns` has no DN column). Add `returns."DN_No"` (migration), map it in the sync, and link `RDN# 0nn` / `Return DN#0nn (date).xlsx` the same way.
- **The DN-vs-Return-Log line check** from the Phase 21 plan (§11.1) applies: the Return DN workbook's lines compared with the Return Log for that DN, with differences listed with sheet and row, never fixed.
- **Coverage on the Drive card** (reported, never fixed):
  - receipt DNs with no file (**28** today);
  - files that match no receipt (**3**: `13627`, `13672`, `14746`, possibly typos for 15xxx, Q22-7);
  - `WD` (43 receipt rows, Q22-8).

**22c — MTC → lots, by batch (first of the three, as §11.3 said: it feeds the QC gate and the exact expiry)**
1. **Name first, with one parser per pattern** (fixtures for each):

   | Pattern | Example | Rule |
   |---|---|---|
   | CUMI | `(BNO-3633,3542,3504)`, `Batch - 2802, 2826, …`, `CUMI FN 2802` | **exact** batch, **within the product family in the name** (PU MF 300 → SAP 1041*/1042*/1043*; FN → 1034) |
   | RTT / supplier certificate | `AB250001410_4924-A1-3.1-BC 3004` | batch `A1` + `4924` matches workbook `5254143`**`A14924`** (letter + digit + 4 digits, all six) and the product in the name |
   | Other | `Carbon Filler_A1626`, `0426_0926` | an **exact** batch only; never a 4-digit suffix |

2. **Then the text layer** (`ai/pdf_extract.py`, pdfplumber, under 1 s): *Batch No / Chargen Nr. / Lot*, *Date of production / MFD*, *Expiry / Shelf life*. A scan with no text is queued for vision **only on request**, since it takes minutes per page.
3. **Each confident match** creates an `mtc_documents` row (SAP, Lot_Number, Drive file, `mtc_number`) → the lot shows **MTC ✓**, and the QC gate (`assert_qc_cleared`) sees it.
4. **Anything less than exact** goes to a QC **"Confirm MTC"** list. It is never linked silently.
5. **Expiry from the certificate:**
   - fills `lots.Expiry_Date` with `Expiry_Source='mtc'`;
   - ranks above *file* and *derived*;
   - never overwrites an expiry typed in the app (Phase 16 rule);
   - a disagreement is listed for QC (Q22-11).
6. **Coverage:** Surface Shield lots with **no MTC** (≈ 27 today). Examples: COROFLAKE primers, PHENACIN, CUMIFLOOR ECO, Garnet, CHEMOLINE rolls (linked by order number? Q22-10), AR bricks (by container? Q22-10). QC sees the list. Nothing is blocked beyond today's gate.

**22d — Requests and pending (general items, never Surface Shield)**
- **New tables** `material_requests` (file, request date, PR# or *Without PR*, mail ref) and `material_request_lines` (Material Code → SAP, description, UOM, requested qty, the workbook's received columns as written, the workbook's pending qty, source sheet/row). Migration.
- **One reader per layout** (4 today). The request date comes from the file name (`Request 22-09-2026`) or the *Date* column. Rows with a Surface Shield category are skipped.
- **GI Hub's own received figure:** Receipt Log rows of that SAP after the request date, allocated first-request-first (FIFO). It is shown **beside** the workbook's *Received on…* columns, and differences are flagged with sheet and row.
- **`CNCEC_Indents Over all Supply and Pending Details.xlsx` is a check, not a source** (Phase 21 §11.2): its pending figure is compared with GI Hub's, and differences are listed.
- **UI:** a **Requests & Pending** page (store keeper, HOD, admin):

  | Request | Date | PR | Item | Requested | Received (GI Hub / workbook) | Pending | Age |
  |---|---|---|---|---|---|---|---|

  Filters: open only, PR / without PR, type. Requests also appear in the PR-status report as **"Request (no PR)"**, so *requested → received → pending* covers everything ordered.
- **Reorder (Q22-13):** pending no-PR requests count as *on order* in Smart Reorder (Phase 19b), labelled, so it stops suggesting what was already asked for.

### 1.3 Lot problems: the rows to fix (from your notes)

This already works since 21a: **Lots & Expiry → Lot problems from the workbook** lists all **36**, each with **sheet · row · date · SAP · lot · qty · problem · hint**. Phase 22 makes it hard to miss and easy to work through:
- a count chip in the top-bar Drive tooltip and on the Dashboard (*"36 workbook rows name a bad lot →"*);
- **Download as Excel** (sheet, row, what to change, the hint) to fix the workbook from;
- **"Fixed since last sync"**: rows that disappeared on the next pull show ✅ for a day, so you see progress.

---

## 2. Track 2 — OCR (slice 22e)

### 2.1 Line-level evaluation on the new photos
- **`tools/ocr_eval.py --lines`:**
  - each page is aligned to its workbook block (date + Prepared by, in order) with sequence alignment over the SAP;
  - each field is scored per line: **item (SAP), qty, tank, work type, Received-by (names compared, never printed), page date, shift → Prepared by**.

  The day-total score stays as a second number for continuity.
- **Sets:** 2026-10-01_04 (11 photos, cached) and **2026-10-05_06 (8 photos)**. Both stay git-ignored, and the scorecard holds counts only.
- **Baseline:** see §2.6. It was measured today with the Phase 21 code, before any change.

### 2.2 Day / Night → Prepared by, editable per site
- **A per-site setting:** Admin Console → Sites → **Consumption paper**, *Day prepared by* / *Night prepared by*, with a **from-date history** (Q22-16) so older papers map to who was on shift then. `app_settings` key `consumption_preparers:<site>`. CNCEC is seeded with Day = Johnson and Night = Kalied, from today.
- **Reading the shift:**
  - `parse_shift` already reads "(Night)" / "(Day)";
  - **an unmarked consumption paper defaults to Day** (your rule, Q22-14). It shows as a chip, **Day (no mark)**, that the store keeper can flip.
  - A struck-out date with the shift still written (the 6 Oct night page) is handled by the date check.
- **Where it lands:** a new nullable **`Prepared_By`** column on `pending_issues` and `consumption` (migration, Q22-15).
  - The sync maps the workbook's *Prepared by* into it, while still writing `Issued_By` as today, so nothing that reads `Issued_By` changes.
  - The app shows *Prepared by* from `Prepared_By`, falling back to `Issued_By`.
  - `Issued_By` stays the submitter, so the HOD's approve/reject notices still reach the store keeper.
- **The eval checks** that every page's shift maps to the workbook's Prepared by: 189/189 is the truth today.

### 2.3 Tank numbers, learned like names
- **Source of truth:** `sme_equipment` tags (14) plus `sme_tank_alias` (mapped aliases).
- **New `paper_fields.match_tank(text)`:**
  - normalise (`O→0`, `S/8→8`, `l/I→1`, spaces and dashes out);
  - expand the written short forms: `K-TNK-091` → Train K tag, `89D0-TNK-001` → `522-89D0-TNK-001`, `J0xx` exact;
  - score by edit distance on the normalised form;
  - return {tag, confidence, candidates ≤ 3}.

  ⚠️ A bare `TNK-091` matches both trains (J and K): it is **never** auto-picked. It is offered as two choices.
- **Ditto marks** inherit the tank from the line above (the existing ditto handling).
- **On OCR Import:**
  - a **Tank** column with the same green/gold/red as names;
  - gold = "did you mean `522-8k10-TNK-091`?";
  - **Accept teaches the alias** (an `sme_tank_alias` row, audited), exactly like names (Q21-5).
- **Stage now sends `Tank_No`** (today it is dropped) and `Prepared_By`.

### 2.4 Names
- **Load the 11 Phase 21 names** (§0.1 shows the two ⚠️ were right) plus any new ones this run proposes, after your OK (Q22-18).
- Phase 21's list is kept in `.cache/ocr_eval/proposed_aliases_phase21.json`: today's run overwrites the usual file.
- **Seen on these papers, to watch:**
  - "Ear plugs" → 1097 Ear PLUG Vaultex;
  - "Olfa blade" → 1191;
  - "Sanding disc 36" → 1095;
  - sizes in the name: Safety coverall L/XL/XXXL;
  - "Harness with lanyard";
  - "Cotton waste" (UOM *Bundle*).

### 2.5 Don't count a paper twice (Q22-19)
The 5–6 Oct papers are **already** in the workbook and in Live. If a store keeper stages a photo **and** the same paper is typed into Excel, the stock goes down twice.
- **Proposed:** before staging, OCR Import checks Live for workbook rows with the same **date + Prepared by**.
  - If they exist, the page switches to **Compare with workbook**: line by line ✅ / ≠ / missing, no staging, and differences to fix in Excel.
  - Staging stays for papers not yet typed.

  This is your "dummy run, compared with the original entries", built into the app.

### 2.6 Baseline on the 8 new photos (Phase 21 code, measured 2026-10-07)

8 pages read by `qwen2.5vl:7b` on this Mac in about 32 minutes (≈ 4 min a page). Day totals, Surface Shield excluded:

| What | Result |
|---|---|
| Page date | **8 / 8 right**. No page needed the date check |
| Night marker | read on all 4 night pages (one written "night", lower case) |
| Lines read | **182 of 189** (7 lines missed) |
| Names | **90 exact**, **92 gold** (suggested), 0 red |
| Day totals, date + SAP | precision **0.646**, recall **0.689** (Phase 21's 11 photos: 0.614 / 0.538 with the date check) |
| Day totals, finest key (date, SAP, work type, tank) | **0.23 / 0.244**. Tanks still pull this down (§2.3) |

What this run teaches:
1. **Most gold names are one handwriting habit read many ways.**
   - "Dust Mash" ×15 and "Dust Mary" → Dust Mask;
   - "Tyche / Tyeh / Tyre / Tychei coverall" → **Tyvek**;
   - "Mashing / Punching Tape 2″" → Masking tape.

   One learned name each removes most of the gold.
2. **The day-total alias proposer guesses wrong when a day has two similar items.** It proposed **22** names and **3 are wrong**:
   - *chemical gloves* → Leather Gloves;
   - *earphones* (= ear plugs) → Leather Gloves;
   - *5l bucket* → Roller brush sleeve.

   "Disco Plugs" was also suggested as SAP 1130, not ear plugs 1097. That is why §2.1 aligns **line by line**: with the real line beside it, each of these is unambiguous. **No name is loaded without your OK** (Q22-18); the line-level run will re-propose them.
3. **Sizes are in the name:** *Safety coverall L / (XL) / XXL* → 1263 / 1264 / 1266. The matcher has to keep the size and not round it to the nearest coverall.

---

## 3. Track 3 — Polish and queue (slice 22f)

### 3.1 The remaining 153 raw colours → 0
- **Where they are:**

  | Component | Raw colours |
  |---|---|
  | SessionReport | 25 |
  | ExecutionPlan | 21 |
  | TotalOverview | 17 |
  | SmeDashboard | 16 |
  | TagDetail | 12 |
  | EfficiencyChartTab | 9 |
  | ManpowerPlanner | 8 |
  | CoverageGauge | 7 |
  | 16 more files | 1–5 each |

- **Mostly semantic.** Emerald `#10B981` / amber `#F59E0B` / red `#EF4444` → `status.ok/low/critical`.
- **Charts** need series colours, so add a **`tokens.chart`** categorical palette (8 colours from navy/gold/status, AA-checked in light and dark) and use it everywhere. Recharts/AntD charts read the palette from the tokens.
- **Ratchet:** design baseline **153 → 0** (or near it, any exception listed in `DESIGN_SYSTEM.md`).
- **Checks:** screenshots before and after for every touched page, in light and dark, plus `parity:sme` (no numbers move).
- ⚠️ Some greens change shade slightly: emerald → `status.ok` (Q22-20).

### 3.2 Practice queue seed, idempotent
- **The guard:** `seed_queues` skips staging when pending rows with remark *"Practice seed — approve or reject me"* already exist for that queue.
- **Clean-up:** a one-time step trims each queue from 12 back to 4 in **both** Practice DBs (`gihub_training`, `gihub_seed_training`), oldest kept.
- The overlay goes to **v11** (it also carries §6's dummy data).

---

## 4. `PHASE21_SUMMARY.md` §3 — every item, and where it lands

| §3 item | Phase 22 |
|---|---|
| 1. DN copies, Pending follow-up, MTC from Drive | §1.2, slices 22b / 22d / 22c |
| 2. Real photos in Practice (cached demo, **blurred** names) | Needs your yes (Q22-21). If yes: names blurred on a copy (local, never committed unblurred), the cached reading replayed in Practice's OCR demo, and the paste box kept as fallback |
| 3. The remaining 153 raw colours | §3.1, slice 22f |
| 4. Tank-number reading | §2.3, slice 22e |
| 5. Overlay queue seed adds 4 rows per run | §3.2, slice 22f |
| also §4.3, the 11 names | §2.4 (Q22-18) |

---

## 5. Slices, order, gates

| Slice | Content | Migration (Live, backup first) |
|---|---|---|
| **22a** | Schedule UI, Pull button, freshness banner, run history from CLI too, retries, per-file arrival check, auto-commit rule, `drive_files` index + cache, lot-problems chip/export, GDRIVE_SETUP fix | `drive_files`, `drive_sync_runs` |
| **22c** | MTC parsers, text layer, `mtc_documents` links, `Expiry_Source='mtc'`, Confirm-MTC list, coverage | none (or `mtc_documents.drive_file_id`) |
| **22b** | DN/RDN/CP linking, 📎 DN viewer, `returns.DN_No`, coverage | `returns."DN_No"` |
| **22d** | Requests & Pending page, PR-status integration, reorder on-order | `material_requests`, `material_request_lines` |
| **22e** | Line-level eval, shift → Prepared by + site setting, tank matcher, Stage sends Tank/Prepared_By, compare-with-workbook, aliases | `Prepared_By` on `pending_issues`, `consumption` |
| **22f** | Colours → tokens + chart palette, overlay v11 (guard + dummy data), Practice photo demo if Q22-21 = yes | none |

- **Order:** 22a → 22c → 22b → 22d → 22e → 22f.
  - 22e does not depend on Drive and could go second if you prefer the OCR first.
- **Each slice** ships branch → PR → three required checks → auto-merge → pull.
- **Done for each slice means:**
  - its service-test suite (22A…22F);
  - Playwright specs on the local gate;
  - `MANUAL_TESTING_GUIDE` + `USER_MANUAL` (rule 13);
  - `ARCHITECTURE.md`;
  - every gate in `CLAUDE.md` §3 green.
- **Drive in tests:** the tests never call Google. A fake `DriveClient` serves fixture listings and files (sample names only, no real DN images), and tests never open Live (rule 15).

## 6. Practice (rule 17 / 17g)
- **No Drive in Practice.** The freshness banner says *Practice data*, and the Pull button is hidden.
- **Overlay v11 seeds dummy examples of everything new**, all synthetic, all tagged for reset:
  - three synthetic DN images (a drawn "DELIVERY NOTE DN# 90001", no real company);
  - one MTC PDF for a Practice lot;
  - one no-PR request with a pending line;
  - a tank alias;
  - the Day/Night preparer names *Practice Day* / *Practice Night*;
  - one OCR paper in *Compare with workbook* state.
- **The demo** gets a tour for the new pages (tour check stays green). A self-driving flow for *Requests & Pending* is optional.

## 7. Safety and privacy
- **Drive stays read-only**, one folder tree, Live only. The cache is git-ignored, and files are served only to signed-in users of that site.
- **Photos and Received-by names** never leave this Mac, are never printed by tools, and never reach a scorecard (counts only).
- **Nothing links silently** when the match is not exact: MTC, DN typos and tank `TNK-091` always ask a person.
- **Never overwritten:** an expiry typed in the app; the workbook (GI Hub never writes to Drive).

---

## 8. Questions for you (my recommendation first)

| # | Question | Recommended |
|---|---|---|
| Q22-1 | Scheduled pulls: may they **commit the ERP side by themselves** when the dry run only *adds* rows? Edits, removals and new stock mismatches still wait for you | **Yes** |
| Q22-2 | Pull times | **07:30 and 19:30** (after each shift's entries); you can change them in the UI |
| Q22-3 | Who gets the **Pull from Drive** button? | **Admin + HOD**; everyone sees the freshness banner |
| Q22-4 | "Stale" threshold for the amber banner | **26 hours** |
| Q22-5 | Is the Google app **published** (In production) or still **Testing**? In Testing the token expires 7 days after sign-in (≈ **14 Oct**) and the scheduled pulls stop | **Publish** (Branding page, as I described); the banner turns red if the token dies |
| Q22-6 | DN/MTC files: cache a copy on the server, or only link to Drive? Staff don't have Drive access | **Cache** (read-only copy, ≈ 40 MB) |
| Q22-7 | DN photos `13627-02052026`, `13672-13052026`, `14746` match no receipt. Typos for 15627 / 15672? | You check; I list them |
| Q22-8 | What is `WD` in the Receipt Log's DN column (43 rows)? | — |
| Q22-9 | Import the Return Log's **DN. No.** into returns (new column), and link RDN files? | **Yes** |
| Q22-10 | MTCs with no batch in the name: **CHEMOLINE 4ECN** (`AB250001409_…`) and **AR bricks** ("1st / 3rd container"). Link by order no. / container → DN? And `Carbon Filler_A1626` vs the workbook's `A 4325`: a different batch? For new files, please put the batch in the name (`… (BNO-xxxx).pdf`) | Order/container as you tell me; otherwise QC confirms |
| Q22-11 | A certificate's expiry goes into the lot (`Expiry_Source = mtc`), above the Lot Register file, never over an app-typed one? | **Yes** |
| Q22-12 | Pending: request workbooks are the **source**, and the *Over all Supply and Pending* summary is a **check**? | **Yes** |
| Q22-13 | Do pending no-PR requests reduce the reorder suggestion (count as on order)? | **Yes**, labelled |
| Q22-14 | An unmarked consumption paper = **Day** (overrides "no mark = no shift" for this paper only; flippable chip) | **Yes**, your rule |
| Q22-15 | Store *Prepared by* in a **new column** rather than reusing `Issued_By` (which the HOD screen needs for the submitter)? | **New column** |
| Q22-16 | Day/Night names: keep a **from-date history** per site, or just the current pair? | **History** (names have changed before) |
| Q22-17 | Tanks: is a written `K-` always Train K, and is `J0xx` always the J-series tag? Who may add a tank alias: store keeper on Accept, or HOD only? | Yes / **store keeper on Accept, audited** (like names) |
| Q22-18 | Load all **11** §4.3 names now (both ⚠️ are confirmed by the new papers), plus the new run's proposals after you see them? | **Yes** |
| Q22-19 | Going forward, are papers typed into **Excel** (OCR = check), or **staged from photos** (OCR = entry)? I propose the app decides per paper: already in the workbook → *Compare*, not yet → *Stage* | **Per paper** |
| Q22-20 | SME chart colours may shift slightly to the token shades (screenshots before/after) | **Yes** |
| Q22-21 | Real photos in Practice's OCR demo, names **blurred** on a local copy? | Your call. **No** is safe; the paste box stays |
| Q22-22 | **Waste Disposal** folder: ignore for now? | **Ignore** (listed only) |
| Q22-23 | May I run the Phase 22 **Live migrations** myself again (backup first), as in Phase 21? | Your call |
| Q22-24 | *Surface Shield Material Received Details* (root, not used): later, for Surface Shield "to be delivered to site"? | **Later** (Phase 23) |

## 9. What this plan will not do
- Write to Drive, or widen the token beyond `drive.readonly`.
- Change the SME engines or their numbers (parity untouched).
- Auto-link anything that isn't an exact match.
- Train or fine-tune the vision model: all the gains are in the matching around it, as in Phase 21.
- Read Waste Disposal, PO PDFs or the equipment list (Q22-22/24).

## 10. Coverage check against the brief and your notes

| Point | Where |
|---|---|
| DN photos → received items | §1.2 22b |
| Request & Pending → pending vs received, general items, not Surface Shield | §1.2 22d |
| MTC → lots strictly by batch | §1.2 22c |
| Pull time customisable in the UI | §1.1 (1) |
| Manual Pull button at the top | §1.1 (2) |
| Global "Last updated from Drive" | §1.1 (3) |
| Check the files arrive correctly | §1.1 (6), §0.3 |
| Lot that doesn't exist → show the row to change | §1.3 (works today; chip, export, progress) |
| Tank list learning | §2.3 |
| OCR eval on the new photos | §2.1, §2.6 |
| Day/Night → Prepared by (Kalied / Johnson) | §2.2, §0.1 |
| Names editable per site in the UI | §2.2 |
| §4.3 names "mostly correct" | §0.1 (5), §2.4 |
| 153 raw colours | §3.1 |
| Overlay queue guard | §3.2 |
| PHASE21_SUMMARY §3, all five | §4 |
| Surface Shield items not on the papers | §0.1 (7), excluded from scoring |
| File naming for DN/MTC (your question) | §0.3, Q22-7/10 |
| Planning only, no app code | this document |
