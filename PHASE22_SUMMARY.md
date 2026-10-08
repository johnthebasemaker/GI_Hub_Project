# Phase 22 — summary: what I did, what is next, what you do

*2026-10-08. Plan: `PROPOSED_PHASE22_PLAN.md` (approved 2026-10-07, rulings
Q22-1..24, recorded in `PROJECT_HANDOVER.md` → Phase 22).*

## 1. In one paragraph

Google Drive is now a service. GI Hub pulls at the times you set (07:30 and
19:30). It checks every file on arrival, and it commits the ERP side by itself
only when the pull just **adds** rows. Every page shows when the data last
arrived, and Admin and HOD can pull any time.

The three subfolders are read and linked:
- **DN photos** open from receipts and returns. Deliveries with no DN get a WD
  number.
- **Certificates** land on their lots by batch **and** product, and QC confirms
  anything less certain.
- **Requests** show requested · received · pending, and requests without a PR
  count as on order.

OCR now works **line by line**:
- the shift on the paper gives *Prepared by*;
- tanks are matched like names;
- a paper already in the workbook is **compared, not staged**.

Finally, the last 153 raw colours are tokens, and Practice has examples of all
of it.

## 2. What I did

### 2.1 Pull requests (all merged, every required check green)

| PR | Slice | What |
|---|---|---|
| #127 | plan | `PROPOSED_PHASE22_PLAN.md` |
| #128 | 22a | Drive as a service: pull times in the UI, Pull button, "last updated" chip, md5 + row-count checks, a > 2 % shrink refused, retries, auto-commit of additions only, DN / MTC / Pending folders cached, every run recorded (the terminal too), lot problems as Excel + "fixed since last sync", Branding → Publish steps in `docs/GDRIVE_SETUP.md` |
| #129 | 22b | DN copies on Records → Receipts / Returns; WD numbers; the Return Log's DN. No. imported; return DNs checked by line count and total; a DN box on Receive |
| #130 | 22c | MTC certificates → lots by batch AND product (name + PDF text, no vision); certificate expiry on the lot; QC confirms proposals; "change expiry" after a retest |
| #131 | 22d | Requests & Pending page; FIFO received from the Receipt Log; the roll-up as a check; no-PR requests on order in Smart Reorder |
| #132 | 22e | Shift → Prepared by (Day/Night names per site, from-date history); `Prepared_By` column; tank matcher + bulk tick; work-type misreads; **Compare, not Stage**; `ocr_eval.py --lines` |
| #133 | 22f | Raw colours 153 → **0**; the HOD top bar fits a laptop; Practice overlay **v11**; queue seed idempotent; `tools/ocr_site_setup.py`; this summary |
| #134 | fix | The queue-seed trim looked for a `Remarks` column `pending_returns` does not have (a fresh build never reaches the trim; the existing sandboxes did) |

### 2.2 On Live (ruling Q22-23: backup first, each time)

| Step | Result |
|---|---|
| Migrations | `e2a8c4f6b1d9` → `f3b9d2e7a4c1` → `a4c7e1d9b3f2` → `b5d8f2a6c3e7` → `c6e9a3b7d4f8` → **`d7fa4c8e2b19`** |
| Backups | `.backups/gihub_2026-10-07_221610_before_phase22a_migrate.sql.gz` … `gihub_2026-10-08_075005_before_phase22e_migrate.sql.gz` (one per slice) |
| Returns' DN numbers | 18 of 20 filled from the workbook (`tools/backfill_return_dn.py`); a dry run afterwards: **no edits** — the next pull is additions only |
| Prepared by | back-filled on 5,363 of 6,139 consumption rows (the rest have none in the workbook) |
| The 11 Phase 21 names (Q22-18) | loaded for CNCEC (audited as `ocr-site-setup`). The new papers confirmed the two I doubted: "Tyvek" is handwritten like "Tyneh", and "Safety coverall **L**" was read as "2" |
| CNCEC preparers | from 2026-09-26: Day **Johnson**, Night **Kalied** |

### 2.2b Practice (rule 17g)

- Both Practice databases (`gihub_seed_training`, `gihub_training`) were
  migrated to `d7fa4c8e2b19`, backups first, then got overlay **v11**: dataset
  `2.11`, 4 drawn Drive files.
- Their approval queues are trimmed from 12 seed rows to 3 receipts, 3 issues
  and 1 return.
- The Practice admin password is unchanged (taken from `deploy/.env`), and the
  wall was re-run.
- Re-running the overlay prints "garnet example skipped". The Garnet job is
  already there and its seed is not re-runnable; nothing is lost.

### 2.3 Measured

- **Your Day/Night rule:** 189 of 189 lines on 5–6 Oct; 8 of 8 pages get the
  right preparer.
- **OCR, line by line** (the 8 new photos; the store keeper accepts the first
  suggestion):

  | Measure | Value |
  |---|---|
  | Lines paired with the workbook (recall) | 0.735 |
  | Precision | 0.764 |
  | Quantity right | 0.94 |
  | Work type right | 0.99 (0.56 before the misread map) |
  | Tank right | 0.42 |

  Tank is low because the reader garbles the **first** tank cell and every
  ditto inherits it. That is why the screen has "tick all like this" and one
  bulk tank.
- **Drive folder, 2026-10-07:**
  - DN: 104 of 107 photos match a receipt; 28 receipt DNs have no photo; 3
    photos match no receipt (below).
  - MTC: names alone link 24 lots exactly. The text layer adds what it can
    prove after the first pull.
  - Requests: 313 lines, 265 matched to a SAP, 48 need one, 128 without a PR.

### 2.4 Gates at the last run (2026-10-08)

| Gate | Result |
|---|---|
| service_tests | **3,003 / 0** (new suites 22A–22E: 52 checks) |
| E2E | **207 / 0** |
| legacy bug_check | 599 / 0 / 0 |
| AI Tier 1 + retrieval + Router L2 | pass |
| grid / semantic bank | 72 / 162 |
| parity:sme / ui-math | 1,334 / 33 |
| nav | 54 routes, 53 tours |
| design | raw hex **0** |
| build | green; critical path re-baselined in small steps, each one explained, about 1.3 KB raw in all |
| alembic | one head |

Two E2E specs flaked once each during the phase (`table-tools` and
`sme-mp-link`, both unrelated). Each passed on re-run, and the final full run is
207 / 0.

## 3. What I would do next (not started; each needs your go-ahead)

1. **The tank's first cell.** A per-page "tank for this whole page" choice,
   offered from the tanks used that week, would fix most of the 0.42. It is a
   small change on the OCR page.
2. **The 7 lines the reader skips** (182 of 189 read). They are mostly the last
   rows of a full page. A second read of the bottom third could catch them, but
   it costs about 4 more minutes a page here.
3. **A "needs a SAP code" editor** for the 48 request lines. Today you fix them
   in the workbook; GI Hub could hold a mapping instead, the way OCR names are
   learned.
4. **Earlier preparers.** Four other names prepared papers before 26 Sep. If
   you tell me who and from when, the history covers the older papers too.
5. **Hetzner.** Everything here assumes this Mac is awake at the pull times.

## 4. What you need to do

### 4.1 Publish the Google app (or re-sign-in every week)
Your token was saved on **2026-10-07 at 17:14**. If the app is still in
**Testing**, Google ends it around **14 Oct**, and the top-bar chip turns red
(*sign-in ended*). Follow `docs/GDRIVE_SETUP.md` step 6 (the Branding page
first, then Publish). After publishing, run the sign-in once more:

```bash
.venv/bin/python tools/gdrive_sync.py --auth
```

### 4.2 Let the first full pull run
The scheduled pulls run only while the API is up. Start the API as usual, then
press **Pull** (or wait for 07:30 / 19:30). The first pull caches about 40 MB
of DN photos, certificates and requests. After it:
- **Admin Console → Drive sync**: check *Delivery notes* (the DN coverage) and
  the *Recent pulls* line.
- **Lots & Expiry → Certificates (MTC) from Drive**:
  - assign the AR-brick and CHEMOLINE certificates to their lots (by container
    / order);
  - QC then confirms them;
  - check which Surface Shield lots still have **no** certificate.
- **Requests & Pending**: add the SAP code in the workbook for the lines in the
  yellow box (48).

### 4.3 Things only you can check
- DN photos **13627**, **13672** and **14746** match no receipt. Are they 15627,
  15672 …? (Q22-7: listed, not guessed.)
- The **36 lot problems**: Lots & Expiry → *Download as Excel*. Fix the rows in
  the workbook. After the next pull, the fixed ones show ✅ for a day.

### 4.4 Try it in Practice (overlay v11)
- Records → Receipts: search `90001` (a DN to open) and `WD`.
- Lots & Expiry: confirm the "container 1" certificate as `practice.qc`.
- Requests & Pending.
- OCR Import: paste a paper dated two days ago with "(Night)" to see Compare
  (USER_MANUAL §3.21).

## 5. Services on this Mac when I finished

Postgres stopped again (the sleep state you left it in); Ollama stopped. No dev servers running.
