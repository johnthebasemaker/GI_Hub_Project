# Phase 21: what was done, what is left, and what you need to do

*Phase 21, "Auto-Pilot, OCR fine-tuning and Enterprise UI". The plan was
approved on 2026-10-06 (rulings Q21-1..24). This file is the summary at the end
of the phase, written on 2026-10-06. `PROPOSED_PHASE21_PLAN.md` is the plan and
`PROJECT_HANDOVER.md` → *Phase 21* holds the rulings.*

---

## 1. In one paragraph

All seven slices (21a–21g) are on `main`, plus two fixes found along the way.
Lots show only what still has stock, every Lots column sorts, and bad workbook
lots are named with their sheet and row. The reorder maths counts approved
Surface Shield jobs and shows its working. The Drive sync is built and waits
only for your token. The OCR reader matches names in three colours, learns
names, and checks the paper's date. The UI has a written design contract and a
build check that enforces it. Practice now has a self-driving demo: six flows,
a narrated tour of every page, a voice, and a reset. **Live is migrated.** What
is left for you is short: the Drive token, one ERP Commit, a review of 11 OCR
names, and some checks on real data (§4).

---

## 2. What I did

### 2.1 Pull requests (all merged into `main` with every required check green)

| PR | Slice | What it did |
|---|---|---|
| [#116](https://github.com/johnthebasemaker/GI_Hub_Project/pull/116) | plan | `PROPOSED_PHASE21_PLAN.md`, then §11 (DN / follow-up / MTC ingestion, planned) |
| [#117](https://github.com/johnthebasemaker/GI_Hub_Project/pull/117) | **21a** lots | Top 5 Expiring by **balance** (no empty lots). Every Lots column sorts, kept in the URL. Bad workbook lots listed with **sheet + Excel row**, still pushed (Q21-18). Migration `d1f7a3c9e2b4` |
| [#118](https://github.com/johnthebasemaker/GI_Hub_Project/pull/118) | **21b** reorder | Root cause: the SQM pace ignored approved **jobs**, so sites fell back to "order the whole plan". Fixed, with exact days of cover, *set pace* instead of a huge order, and a **how?** working on every row |
| [#119](https://github.com/johnthebasemaker/GI_Hub_Project/pull/119) | **21c** Drive | `tools/gdrive_sync.py` plus Admin Console → **Drive sync** (on demand and daily at 07:30). Converts `.xlsm` structurally, so formula values survive. SME files are committed; ERP files get a dry run and wait for your **Commit** (Q21-8) |
| [#120](https://github.com/johnthebasemaker/GI_Hub_Project/pull/120) | **21d** OCR | Measured against the workbook. Exact or learned names go **green**, near names go **gold** (Accept, never automatic), nothing close goes **red**. Names are learned per site; the HOD deletes wrong ones. **Received by** is editable. Migration `e2a8c4f6b1d9` |
| [#121](https://github.com/johnthebasemaker/GI_Hub_Project/pull/121) | **21e** UI | `docs/DESIGN_SYSTEM.md`, the `test:design` build gate, self-hosted IBM Plex Sans and Source Serif 4, motion tokens, the 7 pinned UI skills, and the brutalism comparison PDF |
| [#122](https://github.com/johnthebasemaker/GI_Hub_Project/pull/122) | fix | **The router tests no longer depend on a local Ollama**, from the separate session *Make the System One router tests hermetic*. Its work was committed but never pushed; I verified and shipped it (2,930/0 with Ollama running) |
| [#123](https://github.com/johnthebasemaker/GI_Hub_Project/pull/123) | **21d+** | **Paper date check.** A date more than 14 days old, or later than tomorrow, is not taken: *"did you mean 01/10/26?"*, and you confirm. **Work-type spelling** (PV→PU, RIL→R/L, BLL→B/L, Blaster→Blast). **Bug fixed:** the photo lane had been dropping the paper's date. The scorecard is committed |
| [#124](https://github.com/johnthebasemaker/GI_Hub_Project/pull/124) | **21f** demo | **▶ Auto demo** (Practice only). It drives the real screens with a gold ring, spotlight, subtitles and voice, and signs out and in as the next role (never admin). Flows 1–2. The assistant offers **▶ Run this demo**. Reset demo data |
| [#125](https://github.com/johnthebasemaker/GI_Hub_Project/pull/125) | **21g** | Flows 3–6, **a narrated tour for every sidebar page** (52, checked by the build), and the polish pass: raw colours 195 → 153. **Bug fixed:** OCR Import's Stage was refused in Live, because it never attached the paper |
| [#126](https://github.com/johnthebasemaker/GI_Hub_Project/pull/126) | docs | This file, `SESSION_HANDOVER.md`, `PROJECT_HANDOVER.md` |

### 2.2 The things you asked me to do from the "needs you" list

- ✅ **Live migration (item 1).** I took a backup first:
  `.backups/gihub_2026-10-06_230148_before_phase21_migrate.sql.gz` (330 KB; it
  passes `gzip -t` and has 116 tables). Then I ran `alembic upgrade head` on
  `gihub`: `c4e9b2a7f613 → d1f7a3c9e2b4 → e2a8c4f6b1d9`. Live is at head; the
  new columns and the `ocr_aliases` table are there.
- ✅ **The Excel sync on Live works again.** It had failed because the
  `Source_Sheet` column was missing. The ERP dry run is in §4.2. I did **not**
  commit the ERP side (Q21-8 says you do).
- ⏭️ **Drive token (item 2):** left for you, as you asked.
- ✅ **Reorder (item 3):** you said it is OK. Nothing changed.
- ✅ **The paper dates:** you confirmed every paper is 1–4 Oct, recorded as
  ruling **Q21-1a**. The three misread pages now get the right date as the
  first suggestion.

### 2.3 OCR, measured on your 11 photos (1–4 Oct, 248 rows)

| Day totals | Precision | Recall |
|---|---|---|
| date + SAP, raw (before) | 0.417 | 0.462 |
| date + SAP, **with the date check** | **0.614** | **0.538** |
| …plus the 11 proposed names, if you approve them (§4.3) | 0.672 | 0.600 |
| finest key (date, SAP, work type, tank) | 0.079 → 0.161 | 0.091 → 0.182 |

| Page | Read as | Suggested first | Truth |
|---|---|---|---|
| 1 | 01/01/26 | **01/10/26** | 01/10/26 |
| 3 | 01/07/26 | **01/10/26** | 01/10/26 |
| 7 | 09/10/26 | **04/10/26** | 04/10/26 |

- **Names:** 154 of 248 rows were exact. 93 were gold (a near name: Accept)
  and 1 was red.
- **Embeddings (Q21-4):** no gain. 156 of 160 resolvable rows came out right
  with or without them, so they are **not built into the app**.
- **What still pulls the fine score down: tank numbers.** The reader garbles
  them. They are not used when stock is staged, so this is a scoring limit,
  not a data problem.

### 2.4 The self-driving demo (Practice only)

| # | Flow | Roles |
|---|---|---|
| 1 | Issue stock → HOD approves | store keeper → HOD |
| 2 | Surface Shield jobs: bulk submit → bulk approve (`DEMO-TANK-1`) | supervisor → HOD |
| 3 | Receive a batch with its MFD → Lots & Expiry | store keeper → HOD |
| 4 | Lend 3 tools, 1 comes back (*2 still out*) | store keeper |
| 5 | Set the SQM pace → accept a minimum | HOD |
| 6 | A paper: misread date confirmed, gold name accepted, staged with its paper | store keeper → HOD |
| — | **Page tours**: one page, this page, or all my pages (52 pages covered) | any |

- **Controls:** Pause, Skip, Mute and Stop (Esc). Touching the page pauses it
  (*You took over — Resume*).
- **Voice:** 100 clips recorded with the Mac's Samantha voice (2.0 MB) for the
  six flows. The browser's on-device voice reads the tours. Nothing goes to a
  paid or online voice service.
- **Reset demo data** (HOD/Admin) removes every `DEMO-` entry. It also puts the
  demo tank, the pace, the accepted minimum and the learned name back exactly
  as they were.
- **Assistant (Practice):** *"show me how to issue stock and get it approved"*
  → **▶ Run this demo**. A request no demo covers gets *"I can't show that
  visually yet — I've sent your request to the admin"*, and Feedback gets one
  row per person, per question, per day.
- **Live has none of it.** The endpoints return 404 and there is no button.

### 2.5 Two real bugs found and fixed on the way

1. **The photo lane dropped the paper's date** (`jobs._resolve`). Every
   photographed page opened on *today's* date. Fixed in #123.
2. **OCR Import → Stage was refused in Live.** Live requires a supporting
   document (`require_entry_documents` is on by default), and the stage sent
   none; the test database switches that setting off, so no test saw it. A
   photographed page now **attaches itself** as the document. Pasted text asks
   you to attach the paper, and a site with WBS numbers asks for the WBS. Fixed
   in #125.

### 2.6 Gates, at the last run

| Gate | Result |
|---|---|
| service tests | **2,951 / 0** (new suites 21D2 ×8, 21F ×8, 21G ×5) |
| E2E | **202 passed** across three full runs (`demo.spec.ts` 9 Practice-leg tests, `ocr-match` +1) |
| AI Tier 1 | 147/147, 0 leaks |
| retrieval | recall 1.000 · precision 0.994 |
| Router L2 | pass |
| grid / semantic bank | 72 / 162 |
| parity:sme | 1,334 |
| UI math | 33/0 |
| nav | 53 routes + **page tours 52** |
| legacy | bug_check green |
| build | design contract green (raw hex **153**); critical path green at a new, lower baseline |
| alembic | single head `e2a8c4f6b1d9` |

### 2.7 On this Mac

- **Overlay v10** (the demo tank) is applied to both Practice databases
  (`gihub_training`, `gihub_seed_training`). The `practice.admin` password
  still matches `deploy/.env`; I checked.
- Postgres and Ollama were started for this work and put back to sleep at the
  end (§6).

---

## 3. What I would do next (not started; each needs your go-ahead)

1. **Plan §11 / Q21-10: DN copies, Pending Material Follow-up and MTC files
   from Drive.**
   - A DN opens from any return.
   - Requests raised without a PR appear in the supply summary.
   - Each batch gets its exact certificate expiry from its MTC.

   This is planned only. It needs the Drive token first.
2. **Real photos in Practice.** Practice has no vision (ruling Q5), so the OCR
   demo uses the paste box. Your 11 photos could be used as a *cached* demo
   result, but they show workers' names, so it needs your yes and a
   blurred copy.
3. **The remaining 153 raw colours.** They are mostly in the SME estimator's
   charts (SessionReport 25, ExecutionPlan 21, TotalOverview 17, SmeDashboard
   16). That is one polish PR, with screenshots before and after.
4. **Tank-number reading.** Teach the matcher the site's tank list (as it
   learns names), so a garbled tank is suggested like a name. That is the next
   OCR gain after dates.
5. **The Practice overlay's queue seed adds 4 rows per run.** It is not
   idempotent, and now holds 12 rows per queue. That is harmless, but a
   one-line guard would keep the queues tidy.

---

## 4. What you need to do

### 4.1 Connect Google Drive (you said you'll do this tomorrow)

Follow `docs/GDRIVE_SETUP.md`:
1. In Google Cloud, create a **Desktop** OAuth client with scope
   `drive.readonly`.
2. Save the client file as `deploy/gdrive_client.json`.
3. Run the sign-in once, in a terminal:

   ```bash
   .venv/bin/python tools/gdrive_sync.py --auth
   ```

   This writes `deploy/gdrive_token.json`. Both files are git-ignored.
4. Check the connection:

   ```bash
   .venv/bin/python tools/gdrive_sync.py --list
   ```

From then on it runs daily at 07:30, or on demand from Admin Console → **Drive
sync**.

### 4.2 Commit the ERP workbook side once, after fixing what you want fixed

The Live dry run (2026-10-06, 23:05) is clean: 0 new, 0 edited, 759 receipts,
5,897 consumption rows and 17 returns unchanged. It warns about:

| Count | What | Fix |
|---|---|---|
| 35 | consumption rows naming a lot that no receipt brought in, or that is already used up (e.g. Consumption Log **row 759**: lot 3502 of SAP 1042) | Fix in the workbook; the Lots page lists them all with sheet and row once committed |
| 1 | **stock mismatch**: SAP 1004, workbook 4, DB 3 | Check which is right |
| 14 | Rubber & Brick rows with no batch (e.g. row 4, DN 13320, the PU MF300 components) | Fill the batch if known |
| 3 | lots whose rows disagree on dates (SAP 1033 lot 2803; 1041 / 1043-2 lot 3504). The earliest expiry is used | Correct the MFD/expiry cells |
| 3 | asset rows with a location but no serial (rows 1, 5, 11) | Add the serial or leave it |
| 4 | duplicate Material_Code on variant SAPs (1398, 1043-2, 1043-3, 1040-1) | Expected for variants; nothing to do |
| 6 | pack-size disagreements (1041-1 description; recipe 1044/1034/1033/1037/1038) | Unit Size wins (Q14-4); fix the description or recipe if you want them quiet |

Then commit: Admin Console → **Drive sync → Commit ERP**, or:

```bash
.venv/bin/python tools/pg_excel_sync.py --site CNCEC --kinds inventory,ledger,lots --commit
```

The commit also writes each ledger row's **sheet and row** (21a). Until then,
Live's *Lot problems* card can't show row numbers for older rows.

### 4.3 Review the 11 OCR names your papers teach (I did not load them)

| Written on the paper | Would become | My view |
|---|---|---|
| clear glass | 1106 SAFETY GLASS (Clear) | ✅ |
| duct tape | 1192 DUCT TAPE 2" | ✅ |
| dust mask | 1131 DUST Mask | ✅ |
| leather gloves | 1165 Leather Gloves | ✅ |
| marker white | 1132 White Marker | ✅ |
| marking tape 2 | 1190 MASKING TAPE 2" | ✅ (a misread of "masking") |
| masking tape 2 | 1190 MASKING TAPE 2" | ✅ |
| s04 sand paper | 1101 SANDING PAPER 80 G | ✅ probably |
| **safety coverall 2** | 1263 SAFETY COVERALL **L-SIZE** | ⚠️ is "2" a size? check |
| trash bag | 1099 TRASH BAG (BLACK) | ✅ if it's always black |
| **wrench overall** | 1367 **Tyvek Coverall** | ⚠️ looks wrong; probably a misread of another word |

The list is in `.cache/ocr_eval/proposed_aliases.json`. You don't have to load
it: pressing **Accept** on OCR Import teaches each name the first time it
appears, which is the safe way (Q21-5). If you want them all loaded at once,
tell me which to drop.

### 4.4 Quick checks on Live (5 minutes, when the API is running)

1. **OCR Import → photograph a paper.**
   - The date picker shows the **paper's** date, not today's.
   - **Supporting document** already lists the photo.
   - **Stage** works.

   This is TC-21G-06. It is the bug from §2.5, now fixed, and it can only be
   seen on Live with a real photo.
2. **Dashboard → Top 5 Expiring:** no lot with zero left.
3. **Lots & Expiry:** sort by expiry and by left; the order sticks after a
   reload.
4. **Reorder signals:** you already said this is OK.

### 4.5 Practice, before you show the demo to anyone

- Sign in to Practice as `practice.hod` → **▶ Auto demo**.
- Run a flow once, then **Reset demo data**.
- Voice and 1×/1.5× are in the chooser.
- If a flow ever stops with *The demo stopped: …*, press **Reset demo data**
  and start it again. The message names the button it could not find; send me
  that line.

---

## 5. Decisions recorded in this phase

- **Q21-1..24:** as approved (in `PROJECT_HANDOVER.md`).
- **Q21-1a (new, 2026-10-06):** every paper is dated within 1–4 Oct. A paper
  date outside the last 14 days (or later than tomorrow) needs the store
  keeper's confirmation.
- **Q21-4, outcome:** the embedding layer is not built (no measured gain).
- **Voice:** the six flows use recorded Mac clips. The 52 page tours use the
  browser's on-device voice, which keeps several MB of audio out of the
  repository.
- **Critical path:** re-baselined deliberately once in 21f (+1.2 KB gz for the
  Practice-only launcher, session adoption and the assistant button), then
  **lowered** in 21g (−1 KB raw).

## 6. Services on this Mac when I finished

- **Postgres** was started for this work and **stopped again** at the end.
- **Ollama** was started (one request and one model at a time, as the machine
  notes require) for the router test check and **stopped again**.
- **cloudflared:** I never touched it.
- **No dev server** was left running.

Use `./bin/power.sh wake` to start Postgres again.
