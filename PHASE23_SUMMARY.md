# Phase 23 — summary: what I did, what is next, what you do

*2026-10-10. Plan: `PROPOSED_PHASE23_PLAN.md` (#135, approved 2026-10-09, rulings
Q23-1..17, recorded in `PROJECT_HANDOVER.md` → Phase 23). Local Mac only, as you
ruled; nothing was deployed to Hetzner.*

## 1. In one paragraph

Every download now works for every role. A pytest gate calls every file route
as every role, so a 422 like the Execution one fails a pull request first.

**OCR papers:**
- A tank can be set once for the whole page.
- A second read finds the numbered lines the first read skipped. It runs in
  the background, so the store keeper waits 0 seconds.
- Request lines with no SAP code are decided once, in GI Hub, and remembered.
- The preparers' history now goes back to July, with one-day covers.

**The catalogue and voice:**
- The company's 5,976 material codes and the site's plant & tools are in a
  catalogue with pictures.
- A HOD can raise a PR for an item GI Hub does not stock yet, without anyone
  inventing a SAP number.
- You can dictate into any box. Speech is turned into text on this Mac.
- Pages and answers can be read aloud.

**Practice:**
- The login page lists its accounts with one-click sign-in.
- The admin password is shown only in Live.
- There are twelve more self-driving demos, including a management tour.

## 2. What I did

### 2.1 Pull requests (each merged with every required check green)

| PR | Slice | What |
|---|---|---|
| #135 | plan | `PROPOSED_PHASE23_PLAN.md` |
| #136 | 23a | The Execution 422 fixed (an admin print sent no `site_id`). The same defect fixed in the Stock-vs-Excel upload and the Manpower Planner. Token-less `/api/…` links fixed. Readable error messages from downloads. Two pickers asked for more rows than the list cap. New gate: `tests/downloads` (250 cases, every file route × 9 roles) |
| #137 | 23b + 23c + 23d | The page tank and the background second read. The SAP-code mapper. Earlier preparers and one-day covers. The catalogue, plant & tools and pictures. Two Live migrations |
| #138 | 23e | Whistle dictation (pinned, local, telemetry off) and device read-aloud. Groundwork for the Practice sign-in details |
| #139 | 23f | The Practice accounts card with one-click sign-in. The Practice admin password and **Reset Practice passwords** in the Live Admin Console. Twelve demos. Overlay **v12**. Two tutorials |
| (this one) | 23g | `USER_MANUAL.md` (a "what's new in 22–23" box; §3.21 moved into order), `SOP.md` v2.1, `FINANCE_PITCH.md` §3A, `docs/ARCHITECTURE.md` §7m, both handovers, this summary |

### 2.2 Where I did not follow the plan exactly

- **The second read targets the gaps, not "the bottom third".**
  - The 7 lines the reader skipped on 5–6 Oct were not at the bottom. They were
    ditto rows in the middle of the page: rows 2–4 and 9–12.
  - The second read now looks for printed S.No numbers that are missing from
    the first read but have handwriting on their line. It sends just those
    strips, with the column header.
  - This costs about 14 s a page instead of the 4 minutes the plan feared.
- **23b, 23c and 23d went in one PR (#137).** They share the migrations and
  the OCR page, and splitting them would have meant migrating Live three
  times instead of twice.
- **One extra cover.** Next to Imtiyaz on 28 Sep (Q23-1), I added **Mydeen as
  the Night cover on 26 Sep**, the handover day to Kalied. The back-check
  matches all 5,363 papers either way. If it is wrong, delete it in the
  preparers card (Admin Console, or the HOD's WBS & Work Types page).
- **Voice in CI.** CI has no voice model, so the E2E there checks that the
  microphone is **hidden**. The full dictation test runs on this Mac.
- **Reset Practice passwords** is a Live button that runs
  `tools/practice_db.py passwords`. That tool works on each Practice database
  as a Practice process, so Live still never opens a Practice database
  (rule 17).

### 2.3 On Live (Q23-15: backup first)

| Step | Result |
|---|---|
| Migrations | `d7fa4c8e2b19` → `e8ab5d9f3c21` (request_sap_map) → **`f9bc6e1a4d32`** (catalogue, equipment, pictures) |
| Backup | `.backups/gihub_2026-10-09_225500_before_phase23cd_migrate.sql.gz` |
| Preparers (Q23-1) | Loaded. Back-check: **5,363 / 5,363** papers get the right name. Covers: Imtiyaz 28 Sep (either shift), Mydeen 26 Sep Night |
| Drive pull | Catalogue **5,976** codes, equipment **37** lines. Request lines needing a SAP code **82 → 15**; 33 more are *not stocked yet* (they carry a catalogue code) |
| The 5 rows dated 10 Oct (Q23-17) | Left alone |

**Practice:** both databases were migrated to `f9bc6e1a4d32` and given overlay
**v12**. The wall was re-applied, and their passwords were re-set to their
values.

### 2.4 Measured

**OCR, against what the store keepers typed** (`tools/ocr_eval.py`):

| Papers | Lines found, before → after the second read | Precision |
|---|---|---|
| 1–4 Oct | 0.742 → **0.761** (12 lines added) | 0.79 → 0.773 |
| 5–6 Oct | 0.735 → **0.767** (7 lines added) | 0.764 → 0.767 |
| 7–8 Oct | 0.855 → **0.867** (6 lines added) | 0.876 → 0.856 |

- **Page tank (5–6 Oct):** the tank is right **0.42 → 0.81**, and lines that
  are entirely right go from **32 → 52**.
- **Voice** (`tools/stt_eval.py`, 20 site phrases): word error **0.144** with
  the site's own words as keywords. 15 ms median per phrase, 88 MB, no
  telemetry file created.
- **Delivery notes:** **152 of 160** DN photos in Drive open from their
  receipt (Live, 10 Oct).

### 2.5 Gates at the last run (2026-10-10)

| Gate | Result |
|---|---|
| preflight | clean |
| service_tests | **3,036 / 0** (new: 23A–23F) |
| downloads (pytest) | **250** |
| E2E | **226**. The full pack: 201 passed, 5 failed, 20 serial-chained not run. The 5 failures and the 20 not run were all in the four specs re-run, which passed 27/27 |
| AI Tier 1 + Router L2 | 147/147 · pass |
| grid | 72 (regenerated; the manual moved) |
| parity:sme | 1,334 |
| ui-math + OCR page rules | 33 + 15 |
| nav | 55 routes, 54 tours |
| legacy bug_check | 599 / 0 / 0 |
| build | green. Critical path re-baselined five times in Phase 23 (+506, +2,242, +2,154, +144 and +306 B raw), each for a named lazy loader |
| alembic | one head |

The 5 E2E failures:
- **4** were Practice specs that clicked "Sign in" by name, which now also
  matched the eight one-click buttons. The specs now match exactly, and the
  buttons have their own accessible names ("Sign in as Store Keeper").
- **1** was a timeout under load.

### 2.6 Tutorials (not a gate)

Two new MP4s are in `docs/tutorials/out/` (git-ignored, with `.vtt` captions),
recorded in Practice:
- `hod_management_tour_v1.mp4` (2 min 9 s)
- `hod_catalogue_pr_pictures_v1.mp4` (1 min 22 s)

The avatar is still the mock: no HeyGen key exists.

## 3. What I would do next (not started; each needs your go-ahead)

1. **Pictures.** Once the *Material Images* folder exists, the next pull
   brings its pictures in by GI code. A short list of the 50 most-issued codes
   would be the place to start.
2. **The precision dip.** On 1–4 and 7–8 Oct the second read added lines and
   precision fell slightly (0.79 → 0.773, 0.876 → 0.856): some added lines do
   not match the workbook. A confidence floor on added rows would trade a
   little recall for precision. I would measure before changing it.
3. **Hetzner.** Everything still assumes this Mac is awake at 07:30 and
   19:30.
4. **A real avatar** for the tutorials, if you get a HeyGen key (P12-1: only
   the narration text would be sent).

## 4. What you need to do

1. **Create Drive's `Material Images` folder** (Q23-6). Name each picture by
   its GI code, for example `GI-7000003.jpg` (`docs/GDRIVE_SETUP.md` Part 4).
2. **The 15 request lines** still needing a SAP code: decide them in
   *Requests & Pending → Needs a SAP code*, or add the item to the workbook.
3. **The 7–8 Oct consumption rows have no Prepared_By** in the workbook. Until
   they do, OCR's Compare cannot pair those papers with their preparer.
4. **12 GI-8xxx codes** in the item master are not in the catalogue file
   (`All MATERIAL CODES-15.04.2026.xlsx`), and **GI-7003055** has two
   different descriptions in it. *Catalogue → the report line* lists them.
5. **Try it in Practice:**
   - the sign-in card;
   - **▶ Auto demo → Management tour: GI Hub in one walk-through**;
   - *A picture for a material, and for its family*;
   - as an HOD afterwards, **Reset demo data**.

## 5. Services on this Mac when I finished

Postgres is running, as it was. Ollama, which I had started for the OCR
measurements, is not running. The dev servers you started (API :8000/:8001, Vite) were
left as they were. No E2E or tutorial stack is running.
