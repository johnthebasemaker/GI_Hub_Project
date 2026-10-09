# PROPOSED PHASE 23 — Quality, Voice & Visuals

*Drafted 2026-10-09 from the operator's Phase 23 brief and `PHASE22_SUMMARY.md`
§3. **Planning only: no application code until the operator approves this plan
and rules on §9.** Every "today" below was read from the code, the API logs, the
Live database (read-only), the Drive folder (file list plus three downloaded
workbooks) or the public model card. None of it is assumed.*

| Track | Slices | What |
|---|---|---|
| **1 — Fixes, OCR & data** | 23a · 23b · 23c | The Execution **422** and a download barricade · OCR **page tank** + **second read** · **SAP code mapper** + **earlier preparers** |
| **2 — Visuals & voice** | 23d · 23e | **Material & equipment catalogue** with pictures (6,000 codes from Drive) · **Voice input** (Whistle STT) and **read aloud** |
| **3 — Practice & docs** | 23f · 23g | Practice **credentials** + Practice examples + more **captioned demos** · **Docs overhaul** (USER_MANUAL, SOP, FINANCE_PITCH, testing guide) |

Order: 23a first, because it is a bug you hit. The rest follow the table. Each
slice is one branch → PR → three required checks → auto-merge → local pull.

---

## 0. What I read and measured

### 0.1 Your Phase 22 to-do list — confirmed

| Item | What I see |
|---|---|
| Google app published | The console shows **In production**, and `deploy/gdrive_token.json` was re-saved today at 15:54. The weekly Testing expiry no longer applies |
| First pull from the UI | Done. DN 169 files (146 linked · 14 unlinked · 9 other), MTC 24 (11 linked · 13 unlinked), Pending 6 (all linked) |
| Lot problems | Mostly fixed; the rest are yours for later (no action from me) |
| ⚠️ New data note | **5 consumption rows are dated 10 Oct** (tomorrow), workbook rows 1264–1498. Probably a typo for 09-10. Listed, not changed |

### 0.2 The Execution "422" — root cause found

The API log has the failing calls:

```
GET /execution/forms/LSC1?esc=ESC11                 422
GET /execution/forms/LSC10?esc=ESC101&copies=50     422   (three times)
```

1. **Cause.** Printing a form **registers** it against a site. A site-bound user
   (store keeper, supervisor, site HOD) gets their own site automatically. A
   **global** role (admin, logistics) must name one, and the Execution page
   **never sends `site_id`**. So every print by an admin fails with
   *"site_id is required for a global role"*. The schema and the path are both
   fine; the missing parameter is the bug.
2. **Why you saw only "Request failed with status code 422".** The download asks
   for a file (`responseType: 'blob'`). When the server answers with an error,
   the explanation arrives inside the blob, and the error helper cannot read
   it. **Seven** download helpers share this blind spot.
3. **Two more 422s in the same log, unrelated to printing:**
   - `GET /inventory?limit=600` and `/employees?limit=600`, many times. The
     Documents page asks for 600 rows, and the list endpoints cap at 500, so
     its pickers come up empty.
   - One `POST /mh/planner` 422 from the tunnel. This needs a look in 23a.

### 0.3 The material-code file in Drive

`All MATERIAL CODES-15.04.2026.xlsx` sits in the Drive root (280 KB). The sync
does not read it today; it reads only the three subfolders.

| Fact | Value |
|---|---|
| Sheets | `7-SERIES` (5,976 rows) and `6-SERIES` (2,415 rows) |
| Columns | `Material` (e.g. `GI-7000001`) · `Material Description` · `UOM` |
| **Unique codes** | **5,976**: the 7-SERIES sheet already contains every 6-SERIES row |
| Codes by prefix | `GI-6…` 4,829 · `GI-7…` 3,562 (counted over both sheets) |
| Units | EA 4,624 · NO 947 · PC 663 · KG 383 · L 298 · ROL 238 · … |
| **Pictures in the file** | **None.** No images are embedded, so "automatic" pictures have to come from somewhere else (§5.3) |
| Your item master | 507 items; 296 carry a GI code, and 283 of those are in this file (13 are not) |

**Equipment in Drive.** There are two different files:
- `Equipment list Updated as on 06-09-2026.xlsx`: 58 rows of **site plant and
  tools** (bus, telehandler, compressors …) with asset numbers and condition.
- `Equipment.xlsx`: 292 rows of **tanks and areas** with SQM, which is the SME
  side.

See Q23-9.

### 0.4 The request lines without a SAP code — now 82, not 48

The first full pull brought in more requests: 391 lines, **82** without a SAP
code. The important finding:

| Group | Lines | What they are |
|---|---|---|
| Carry a **GI material code** | **51** (31 distinct codes) | **All 31 codes are in the 6,000-code file, and none is in your item master.** These are not missing codes; they are items GI Hub does not stock yet |
| Description only | 31 | e.g. *Garden Trowel (Hand Showel)*, *Measurement Cup 1L*. These need a person to pick the item |

So the catalogue (23d) and the mapper (23c) solve this together. More than
half of the lines link themselves once the catalogue is loaded.

### 0.5 Earlier preparers — read from the Live Consumption Log

Six names appear in *Prepared by* for CNCEC. The two you named are Day
(Johnson) and Night (from 26 Sep). The other four each cover a clean date
range, and on most of those days they appear **alongside** the Day name, which
is the pattern of a Night preparer. One name appears on a single day only,
which looks like a cover shift. I have **drafted** the history from this and
need you to confirm it (Q23-1). The names are in my chat message, not in this
file, as in Phase 22.

### 0.6 Tanks used in the last week (for the page tank list)

Seven tags carry every line from 30 Sep to 6 Oct: `522-89D0-TNK-001` (221),
`J027` (108), `J050` (99), `522-8k10-TNK-091` (91), `522-8k80-TNK-071` (90),
`J091` (44), `others` (4). A dropdown of this size is quick to use.

### 0.7 Whistle by Cactus — what it actually is

| Fact | Value |
|---|---|
| Model | [`Cactus-Compute/whistle`](https://hf.co/Cactus-Compute/whistle), Apache-2.0, released 2 Oct 2026; one **16.9 MB** `.cact` file |
| Runtime | Python package **`cactus-needle`** 3.1.3 (pure-Python wheel, 108 KB). It is **not** in `transformers`. The native engine is fetched from `Cactus-Compute/needle3` per platform (`macos-arm64`, Linux …) |
| Usage | `needle.transcribe("clip.wav")` returns `{text, language, …}`. Options: `keywords=[…]` (biasing), `language="en"` and `word_timestamps=True` |
| Input | **16 kHz mono**, up to **30 s** per pass |
| Footprint | CPU only, about 120 MB RAM (a third-party figure). No GPU and no Ollama |
| Languages | **English, German, French, Spanish, Italian, Dutch, Polish.** ⚠️ No Arabic, Hindi, Urdu, Tamil or Malayalam |
| ⚠️ **It cannot speak** | Whistle is speech **to text** only. "Reading the screen aloud" needs text-to-speech, which is a different tool (Q23-11) |

GI Hub already reads captions aloud in its demos with the browser's built-in
voice (`frontend/src/demo/voice.ts`), so a read-aloud feature has a working base
with no new model.

### 0.8 Practice logins — why "admin" refused you

I checked both Practice databases' password hashes against the expected values
(no password printed):
- **8 accounts** (`practice.storekeeper`, `.hod`, `.supervisor`, `.qc`,
  `.qchod`, `.logistics`, `.warehouse`, `.auditor`) all accept the published
  shared password.
- **`practice.admin` does not.** By design it has its **own** password from
  `deploy/.env` (`PRACTICE_ADMIN_PASSWORD`), because Practice is reachable from
  the internet through the tunnel, and a published admin password there is a
  published admin password.

That is the "old one not accepted". See Q23-13 for how to show it.

### 0.9 Tutorials today

| Kind | What exists |
|---|---|
| **In-app captioned demos** (Practice; the app clicks through a task itself, with captions and a spoken voice) | 6: issue → HOD approval · Surface Shield bulk · receive a lot · lend and part-return a tool · reorder pace · OCR paste |
| **Recorded MP4 tutorials** (`tools/generate_tutorial.py`, synthetic data, rule P12-0) | 4: hub assistant · HOD executive summary · OCR workflow · store-keeper return |

None of them covers Phase 22 or Phase 23.

---

## 1. Slice 23a — Fix the download 422s and barricade every download

**Track 1 · no migration · ships first**

### 1.1 Fix
1. **Execution form print.**
   - A global role gets a **Site** picker on *Print a consumption form*,
     defaulting to the site the user last used.
   - Site-bound users see no change.
   - The request always carries `site_id` when the user is global.
2. **Every file download shows the real reason.** One shared helper turns an
   error blob back into its message. *"Choose a site — the form is registered
   to one"* replaces *"Request failed with status code 422"*. All seven blob
   helpers use it.
3. **Documents page**: ask for at most 500 rows, which is the endpoint's cap.
4. **`/mh/planner` 422**: reproduce it from the request shape and fix the
   caller or the schema, whichever is wrong.
5. **Report "Excel / CSV" buttons** (`ExecutionReportTabs`). They open a new tab
   with no auth header; check, and route them through the authenticated
   download helper if they fail the same way.

### 1.2 Barricade (your "strict pytest" ask)
- **New `tests/downloads/test_download_contract.py` (pytest).**
  1. It enumerates **every route that returns a file**, found from the app's
     route table and **not** a hand-kept list, so a new download is covered the
     day it is added.
  2. For each route it calls the endpoint as each role allowed to use it, with
     the parameters the UI actually sends.
  3. It asserts a **2xx** with the right content type: PDF magic bytes `%PDF`,
     or an XLSX that opens with openpyxl. A role that is refused must get
     **403**, never 422.
  4. Forms, reports, lot problems (.xlsx), DN / MTC files, Excel exports and
     the executive PDF are all in scope.
- **A cross-check between frontend and backend.** A script reads the frontend
  download calls and fails if one omits a query parameter its route requires,
  which is today's bug.
- **Wired in as a real gate.** The test joins `dual-ci` and
  `bin/ci_preflight.sh`, and it runs against the test database only (rule 15).
  pytest is installed but no CI step runs it today, so this is the first pytest
  gate. A skipped route counts as a failure (rule 16).
- **E2E.** Admin prints a form in the Execution area, and the PDF downloads.

---

## 2. Slice 23b — OCR: page tank and a fast second read

**Track 1 · no migration**

### 2.1 "Tank for this whole page"
- **The control.** A dropdown above the OCR rows, **per page**. It offers the
  tanks used at this site in the **7 days before the paper's date** (§0.6),
  then the learned tank aliases, then "other…".
- **What it fills.** It fills rows whose tank is **ditto, blank or unknown**.
  It **never** overwrites a row the store keeper has already set, or a
  confident (`auto`) match.
- **Undo** reverts exactly the rows it filled.
- **Unchanged.** Per-row editing, the bulk tick ("tick all like this"), dittos,
  Compare and Stage keep working as they do now. The existing OCR E2E specs
  must pass unchanged, which is the "does not break" guarantee.
- **Measured.** `ocr_eval --lines` on the 8 photos, before and after, with the
  page tank set as a store keeper would set it. Target: tank accuracy from
  **0.42 → ≥ 0.85**.

### 2.2 Second read of the bottom of the page — fast, and never blocking
The 7 skipped lines are mostly the last rows of a full page. The second read
does three things to stay fast:

1. **Only when needed.** It runs on a page whose first read looks cut off: the
   last row read lies in the bottom part of the image, or the page reached the
   printed row capacity. A short page never pays for it. There is also a
   *"Read the bottom again"* button.
2. **A small image and a short answer.**
   - It crops the **bottom ~40 %** of the already-rectified page, at the same
     pixel density as the first read. That is about a third of the image tokens.
   - The prompt passes the **last row the first read saw**, so the model returns
     only the rows after it (≈ 5–10 rows) instead of re-reading the page.
   - Decode time is what dominates here (§ OCR findings: 212 s for 30 rows), so
     returning 7 rows instead of 30 is the main saving.
3. **Never in the way.** It is a separate background job. The first read's rows
   appear as they do today, the extra rows arrive later and are **marked "from
   the second read"**, and the store keeper keeps working meanwhile.
   - Rows found twice (the overlap) are dropped by the existing
     `paper_compare.align` pairing.
   - The model stays the one warm vision model (no model switch).

- **Measured on the 8 photos:** lines recovered out of 7, extra seconds per
  page, and the share of pages that trigger it.
- **Target:** ≥ 5 of 7 lines recovered, ≤ 90 s of background time on a page
  that needs it, **0 s added** to a page that does not, and **0 s** that the
  store keeper waits.
- If the measurement misses the time target, I ship it **button-only** and
  report the numbers.

---

## 3. Slice 23c — SAP code mapper and the earlier preparers

**Track 1 · migration (one table)**

### 3.1 "Needs a SAP code" mapper (Requests & Pending)
A new panel on **Requests & Pending**, *Needs a SAP code (82)*, grouped by the
written name, with three outcomes per name:

| Outcome | When | Result |
|---|---|---|
| **Link to an item** | Search the item master by name, SAP or GI code, with the best matches pre-ranked (the same matcher OCR uses) | The line counts against that SAP immediately: received, pending and the reorder signal |
| **Link to a catalogue code** | The line carries a GI code not in the item master (51 lines), or the user picks one from the 6,000-code catalogue | The line is marked *"not stocked yet — GI-7000087"*. It links **by itself** the day an item with that GI code appears in the workbook. No SAP number is invented |
| **Not a stock item** | Services, one-offs | Hidden from the list; it does not count toward reorder |

- **Remembered.** The mapping is learned per site (`request_sap_map`: written
  key → SAP or GI code). It applies to every future pull, so the same name
  never asks twice, and the **workbook is never touched**.
- **Bulk handling.** Select several lines and link them in one step, as with
  the OCR bulk tick. Every decision is audited and can be undone (*"Unlink"*).
- **Who can map.** Admin, HOD (own site) and Logistics (Q23-4).
- **Today's 82 lines.** The 51 GI-code lines are linked by the catalogue as
  soon as 23d lands, and the mapper lists the 31 description-only lines for a
  person. Because 23c ships before 23d, its catalogue outcome lights up when
  23d merges.

### 3.2 Earlier preparers
- **Data.** The confirmed history from Q23-1 is loaded with the existing
  `tools/ocr_site_setup.py` (one `--preparers` line per change), audited.
- **New: an exception day.** A history line can carry a **single date**, for
  the one-day cover, so that a cover shift does not rewrite the Night name for
  the weeks around it. The *Consumption papers — who prepares them* card gains a
  date picker instead of the free-text date box, and an "only this day"
  option.
- **Back-check.** Every Live consumption row with a *Prepared by* is re-derived
  from the history and the shift. The target is **100 %** agreement, and any
  disagreement is listed rather than changed.

---

## 4. Slice 23d — Material & equipment catalogue with pictures

**Track 2 · migration (catalogue + images) · the largest slice**

### 4.1 The catalogue from Drive
1. **Read in by the scheduled pull.** The Drive pull reads the newest
   `All MATERIAL CODES-*.xlsx` in the root, picked by the date in its name, so
   a new edition is used without any change in GI Hub.
2. **Same safety checks as every Drive file.** md5, a refusal if the file
   shrinks by more than 2 %, and every run recorded.
3. **Merged and checked.** Both sheets are merged and de-duplicated. If the
   same code carries two different descriptions, that is reported, not
   guessed.
4. **Stored in a new `material_catalog` table** (code, description, UoM,
   series, first and last seen, source file). It is **not** the item master:
   6,000 unstocked codes would swamp stock pages, reorder and counts.
5. **Linking to the item master.** An item-master row links to its catalogue
   entry by GI code.
   - The 13 item-master codes that are **not** in the catalogue are listed on
     the Drive sync card.
   - A description that differs between the master and the catalogue is shown,
     never overwritten.
6. **Equipment (Q23-9).** The site plant and tools list (58 assets) is read
   the same way into the asset register's pictures.

### 4.2 Pictures — storage
- **Kept on this Mac's disk** (later on the server), in `media/catalog/` (git
  ignored):
  - the original, plus a **512 px WebP** for display and a **128 px** WebP
    thumbnail;
  - EXIF stripped, so no GPS and no phone details;
  - content-addressed by SHA-256, so an image used for 20 variants is stored
    once.
- **Backed up** with the nightly database backup (a tarball beside the SQL
  dump), and restorable.
- **Why not on Drive?** The Drive token is **read-only** on purpose (Phase
  21–22). Writing back would mean a wider permission and a new consent. Drive
  stays an *input* (§4.3) — Q23-7.
- **Access.** Served through an authenticated endpoint with long cache headers,
  and only to signed-in users.
- **Size limits.** At most 10 MB per upload; JPG, PNG, HEIC or WebP (HEIC is
  converted the same way OCR does).

### 4.3 Pictures — where they come from
| Source | How | Automatic? |
|---|---|---|
| **A Drive folder `Material Images`** (you create it once) | Any file whose name **starts with a GI code** (`GI-7000003.jpg`, `GI-7000003 tyvek front.png`) attaches to that code on the next pull | ✅ fully |
| **Family suggestion** | Variants that share a stem (`RUBBER SHEET VE611BN-5MM / -6MM`, sizes S–XXXL) are **offered** the sibling's picture. The suggestion appears with a ✓; nothing is attached until someone accepts it | Suggested |
| **Upload in GI Hub** | From the catalogue page, the item page, or straight from the phone camera | Manual |
| ~~Web image search~~ | **Not proposed.** It picks the wrong item often enough to mislead a PR, and the images are not ours to use | — |

### 4.4 Pictures — the screens
- **Catalogue page.** A new page, *Materials & equipment catalogue*:
  - search by code, name or UoM;
  - filter by "has a picture / no picture / stocked / not stocked";
  - a **"needs a picture"** count, so the gaps can be worked down.
- **Picture editor (one item).**
  - Upload or replace, crop or rotate, and set the main picture (up to **4** per
    item).
  - **Assign one picture to many codes** in one step, for a family.
  - Every change is audited, the **previous picture is kept**, and *Restore*
    puts it back.
- **Where pictures show.** Thumbnails, lazy-loaded so they never slow the first
  paint:
  - HOD's PR form and PR lines;
  - Logistics' PR review and dispatch;
  - the item search (⌘K) and the inventory table;
  - the stock card;
  - the Requests & Pending lines.
- **Who may change pictures.** Admin, HOD and Logistics (Q23-8). Everyone else
  sees them.
- **Practice (17g).** Drawn placeholder pictures for the dummy items, with no
  real photos (Q22-21).

---

## 5. Slice 23e — Voice input (Whistle) and read aloud

**Track 2 · no migration · ⚠️ needs a resource ruling (Q23-12)**

### 5.1 Spike first (half a day, reported before building)
- **Install.** `cactus-needle` goes into the venv, and the macOS-arm64 engine
  and `whistle.cact` are fetched **once** by a setup tool. The files are
  pinned by version **and SHA-256**, so nothing downloads during a request.
- **Measure on this Mac:**
  - the time to transcribe 5, 10 and 30 s of site-style English (material
    names, quantities, tank tags);
  - the RAM used while running;
  - the word error rate on 20 short phrases you or I record.
- **Check for the server.** Confirm a Linux build exists for the future server
  (Hetzner, x86-64), so the choice does not trap us.
- **If Whistle fails here** (no engine for the platform, or a poor result on
  site vocabulary), the service keeps a **provider interface**:
  `whistle | none`. The microphone hides itself when no provider works, rather
  than sending audio anywhere else. I report back before choosing any other
  model.

### 5.2 The service
- **Endpoint.** `POST /ai/stt` takes audio up to 30 s. The **browser converts**
  the recording to **16 kHz mono WAV** itself (Web Audio), so the server needs
  no ffmpeg.
- **Handling.**
  - Rate-limited per user.
  - The audio is processed **in memory and never stored**; only the length,
    duration and timings are logged.
  - The model loads on first use and unloads after 10 idle minutes.
- **Keyword biasing.** Each call passes your site's own words (the most-used
  material names, tank tags and site names), so *"Tyvek"*, *"Remafix"* or
  *"J027"* survive.
- **Eval.** A small set of recorded clips with their texts, scored by WER, joins
  the AI eval runner. Synthetic clips are generated with the macOS `say`
  command, so no real voices are committed.

### 5.3 The screens
- **Hub Assistant.**
  - A **microphone** button beside Send: hold to talk, or tap to start and stop,
    with a 30 s limit and a level meter.
  - The text lands **in the input box**, and the user reads it and presses
    **Send**. It is never sent automatically, so a misheard quantity cannot
    become a question by itself.
- **Read aloud (accessibility).** Uses the device's own voice (Q23-11):
  - a 🔊 button on assistant answers;
  - on the page help panel and on announcements;
  - a *"Read this page's summary"* action on dashboards;
  - offline on Mac, iPhone and Android voices, using the same code the demos
    already use;
  - speed control, and it stops when the page changes.
- **Dictation in other fields (optional, Q23-11).** The same microphone on
  remarks, search and the OCR "edit row" box.
- **Who can use it.** Every role that sees the Hub Assistant. It also works in
  Practice.

---

## 6. Slice 23f — Practice: credentials and more captioned demos

**Track 3 · no migration**

### 6.1 Credentials, visible
- **On the Practice login page.** A **"Practice accounts"** card:
  - every role, its username and what it can do;
  - the shared password shown, with a copy button;
  - a **one-click "Sign in as …"** per role (the existing practice ticket, so no
    typing).
- **The admin account.** Shown per your answer to Q23-13.
- **Reset on demand.** A **"Reset Practice passwords"** action in Live Admin
  Console → Practice puts every account back to the published value, in case
  someone changed one inside the sandbox.

### 6.2 Captioned demos — more areas (Practice, synthetic data only)
The app clicks through the task itself, with captions and the device voice,
and stops at each step so a manager can follow. New scripts:

| # | Demo | Roles shown |
|---|---|---|
| 1 | Pull from Drive and read the "last updated" chip | Admin |
| 2 | A receipt and its delivery-note photo; a WD number | Store keeper |
| 3 | A certificate from Drive confirmed onto a lot | QC |
| 4 | Requests & Pending: requested · received · pending; **map a SAP code** | HOD |
| 5 | OCR paper **Compare**, the **page tank** and the second read | Store keeper |
| 6 | Raise a PR **with pictures**, Logistics reviews it | HOD → Logistics |
| 7 | Add a picture to a material, and to a family | Logistics |
| 8 | Print a consumption form (the 23a fix) and upload it back | Supervisor |
| 9 | Ask the Hub Assistant **by voice** | Any |
| 10 | Return to vendor, with the return DN | Store keeper → HOD |
| 11 | Smart reorder and requests without a PR | HOD |
| 12 | **"Management tour"**: a 6–8 minute chained run of the headline demos, for higher management | — |

- **Recorded MP4s.** Two through `generate_tutorial.py` (synthetic data, with
  captions and a `.vtt`): the management tour, and catalogue + PR with
  pictures. Tutorials remain **not a gate**.
- **Practice data (17g).** A dummy example for every Phase 23 feature:
  - mapped and unmapped request lines;
  - catalogue items with drawn pictures;
  - a page-tank OCR paper;
  - preparers' history.
  This is overlay **v12**, applied to both Practice databases.

---

## 7. Slice 23g — Documentation overhaul

**Track 3**

| Document | Changes |
|---|---|
| `USER_MANUAL.md` | New sections: catalogue & pictures · SAP code mapper · voice & read-aloud · page tank & second read · Practice accounts. Phase 22 Drive sections re-read and tightened. A cleaner table of contents with a "what's new in 22–23" box |
| `SOP.md` | Daily procedures: the 07:30 / 19:30 pulls and what to do when the chip is amber or red · mapping new request lines · adding pictures · printing forms as admin (site) · the OCR paper routine with the page tank |
| `FINANCE_PITCH.md` | A Phase 22–23 page with **measured** numbers only (DN coverage, OCR accuracy before and after, hours of re-typing saved per paper), plus the visual catalogue's purchasing benefit. **No projected savings unless you give me the figures** (Q23-16) |
| `MANUAL_TESTING_GUIDE.md` | §23a–23g, a step list each (rule 13) |
| `docs/ARCHITECTURE.md` · `PROJECT_HANDOVER.md` · `SESSION_HANDOVER.md` | The new services, rulings Q23-x, and the gates |
| `PHASE23_SUMMARY.md` | Same shape as Phase 22's |

The house style is consistent throughout: numbered steps, one action per step,
tables for reference, and every screen named as it appears. Exported PDFs are
regenerated where the repo already keeps them.

---

## 8. Cross-cutting

| Topic | Plan |
|---|---|
| **Live migrations** | 23c (`request_sap_map`) and 23d (`material_catalog`, `item_images`), each **backup first**, one at a time, if you allow me again (Q23-15) |
| **Gates** | All ten gates, plus the new pytest download gate. The critical path stays **zero-growth**: microphone, pictures and the catalogue page are all lazy-loaded |
| **Security** | Pictures and audio are served or accepted only when signed in, with size and type checks. Audio is never stored. EXIF is stripped. The STT engine binary is pinned by checksum. Drive stays read-only |
| **Design** | Tokens only (raw hex stays 0). The microphone and image components follow `docs/DESIGN_SYSTEM.md`, with reduced motion honoured |
| **Nav** | A new *Catalogue* page gets a role-matrix entry, a tour and an `nav_access.json` regeneration (rule 14) |
| **Rule 17g** | Every new feature has its Practice example (overlay v12) |

---

## 9. Questions for you

**Q23-1 — Earlier preparers.** Please confirm the drafted history in my chat
message: the names, which shift each covered, and the from-dates. Also:
- Is Johnson the **Day** preparer for the whole period?
- Should the one-day name be recorded as a **single-day cover** (my suggestion)?
- Were there any Night papers before the first Night name appears?

**Q23-2 — Page tank list.** Should it offer the tanks used in the **7 days**
before the paper's date, then the learned aliases? It fills only ditto, blank
or unknown rows.

**Q23-3 — Second read.** Do you accept **automatic background** second reads
on pages that look cut off (≈ 60–90 s of machine time each, no waiting for the
store keeper), plus a button? Or would you prefer **button only**?

**Q23-4 — Mapper permissions.** Admin, HOD (own site) and Logistics map names
to items. Should the **store keeper** be allowed too?

**Q23-5 — Not-stocked items.** A request line linked to a catalogue code that
GI Hub doesn't stock waits until the item appears in your workbook, and **no
SAP number is invented**. Is that right? And should HODs be able to raise a
**PR for a catalogue item that isn't stocked yet**?

**Q23-6 — Picture sources.** Do you already have item photos anywhere
(supplier catalogues, a phone album, a folder)? Will you create a Drive folder
**`Material Images`** with files named by GI code? I recommend against web image
search (§4.3). Do you agree?

**Q23-7 — Picture storage.** Pictures are stored on disk in GI Hub and backed
up nightly, and the Drive token stays read-only. Is that OK?

**Q23-8 — Who changes pictures.** Admin, HOD and Logistics, with no approval
step, audited and restorable. Is that right? One picture set per **code**,
shared by all sites?

**Q23-9 — "Equipment".** Does it mean the **site plant and tools list** (58
assets: vehicles, compressors …), the **tanks and areas** from `Equipment.xlsx`,
or both?

**Q23-10 — Voice language.** Whistle understands English (plus 6 European
languages), **not Arabic, Hindi, Urdu, Tamil or Malayalam**. Is English-only
voice acceptable? The text always lands in the box and the user presses Send.

**Q23-11 — "Text reading as accessibility".** Whistle can only *listen*, not
speak. Did you mean:
- **(a)** a 🔊 **read-aloud** button using the device's built-in voice
  (recommended);
- **(b)** **dictation** into other text fields using Whistle;
- or **both**?

**Q23-12 — Resource ruling.** Whistle runs on the CPU (≈ 120 MB), **outside
Ollama**. It loads on first use and unloads after 10 idle minutes. Do you
accept this as an addition to the "one warm model" rule (Q17-1)? The engine
is downloaded once from Hugging Face, pinned by checksum.

**Q23-13 — Practice admin password.** Practice is reachable from the internet.
Choose one:
- **(a)** the Practice login page shows the 8 shared accounts with one-click
  sign-in, and the **admin** password is shown only inside **Live** Admin
  Console (behind your login) — recommended;
- **(b)** show the admin password on the Practice login page too;
- **(c)** give `practice.admin` the shared password.

**Q23-14 — Demos.** Is the list of 12 in §6.2 right? Should anything be added
or dropped? Should the management tour be **English captions + voice** in one
language only?

**Q23-15 — Live migrations.** May I again take a backup and run the two Phase
23 migrations on Live myself (as Q22-23)?

**Q23-16 — Finance pitch.** Should it use measured numbers only, or do you
have cost figures (hourly rate, PR cycle time, losses) you want in it?

**Q23-17 — The 5 rows dated 10 Oct** (§0.1). Are they a typo you will fix in
the workbook? I will not change them.

---

*After your answers I execute 23a → 23g in order: each on its own branch with
its PR, every required check green, auto-merged, and your local `main` pulled.
Live is migrated with a backup first if you approve Q23-15.*
