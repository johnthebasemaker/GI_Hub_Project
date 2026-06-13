# GI Hub ERP — Handoff

**Last update:** 2026-06 round — Categories, Returns, Attachments, Cloud hosting, automated bug harness, self-host guide, branded PDF generator.
**Test status:** 315/315 pytest · 114/114 in `bug_check.py` (run `python bug_check.py` any time).
**Production hosting:** Self-host on a spare Mac + Cloudflare Tunnel. See §4 "Run / Develop" → "Production hosting".
**Purpose:** Get the next session productive in <5 minutes — architecture, what changed, what's next.

---

## 1. Architecture Map (touch-tested files)

```
CNCEC PROJECT/
├── main.py                       Page routing, RBAC gate, sidebar
├── config.py                     Constants, ROLES, ROLE_HIERARCHY, PAGE_ACCESS, brand colours
├── database.py     (~3,800 LOC)  ALL SQL + schema + helpers + reports (no Streamlit)
├── cache_layer.py                @st.cache_data wrappers, bust_inventory_cache()
├── auth.py                       bcrypt login, render_user_management_tab
├── ui_components.py              Custom CSS, AgGrid wrapper, charts, brand headers,
│                                 STATUS_BADGE_JS, LOAN_STATUS_BADGE_JS, status_pill_html
├── mailer.py                     Outlook (Win COM) / Mail.app (AppleScript) / mailto:
│                                 + draft_rubber_mtc_email, draft_return_logistics_email
├── reports.py                    PDF/Excel/CSV generators with narrow-column smart widths
├── whatsapp_worker.py            Twilio API (cloud) → pywhatkit (local fallback, lazy-imported).
│                                 Started as @st.cache_resource thread from main.py:78.
│
├── pages_internal/
│   ├── live_dashboard.py         Hero strip (4 cards inc. Stock Value SAR).
│   │                             Grid column order: SAP_Code → Material_Code → Desc → UOM →
│   │                             Opening_Stock → Receipt → Consumption → Return →
│   │                             Closing_Stock → Min → Unit_Cost → Stock_Value → Category.
│   ├── daily_issue_log.py        6 tabs for the Store Keeper:
│   │                               Consumption Log · Receipt Staging · Return Items (NEW) ·
│   │                               Returnable Items (tool loans) · Stock Count ·
│   │                               QR Label Request (NEW)
│   │                             Attachment expander on Consumption + Receipt.
│   │                             Rubber-category receipts prompt for MTC number + file.
│   ├── hod_portal.py             14 tabs: EOD · Cross-Site · Burn Rate · Pending Receipts ·
│   │                             Returns (NEW) · Adjustments · PRs · Receive · Shelf-Life ·
│   │                             Notifications · My Requests · Site Config · DOC (NEW) ·
│   │                             QR Approval (NEW).
│   │                             EOD = checkbox-confirm (no more "type COMMIT").
│   │                             Pending Receipts shows Material_Code + missing-MTC banner.
│   ├── admin_portal.py           Same 9 tabs. Add-New-Entry form: Category renders as
│   │                             selectbox of MATERIAL_CATEGORIES; Opening_Stock as number.
│   └── reports_page.py           Generate tab now has Category filter + SAR-toggle.
│                                 _strip_empty_columns auto-applied to every report.
│
├── ai/                           Unchanged (NL search, OCR, fuzzy, insights)
├── pwa/                          Unchanged
│
├── bug_check.py                  Standalone smoke harness. Run `python bug_check.py`.
│                                 Writes BUG_REPORT.md. Throwaway DB, never touches live.
├── BUG_REPORT.md    (generated)  Latest bug_check output — pass/fail per check, by area.
├── build_manual_pdf.py  (NEW)    Markdown → branded fpdf2 PDF (cover + TOC + headers).
│                                 Used by Admin → Settings → "Download User Manual".
├── USER_MANUAL.md                Full user catalogue. §13 covers 2026-06 changes.
└── handoff.md                    THIS FILE
```

### Critical contracts (do not break)

- **`commit_eod(conn) → int`** — signature unchanged. Moves `pending_issues` → `consumption`, deletes staged.
- **`process_receipt_delivery(conn, date, sap, qty, supplier, remarks, site, pr_number, expiry_date, extra_fields)`** — auto-creates lot row when Lot_Number provided or Expiry_Date set. Idempotent via UNIQUE on lots. **`extra_fields` MUST be a column on `receipts`** — the function swallows OperationalErrors, so missing columns silently drop SK input. The receipts self-heal block now covers the full 15-column logistics set (see `database.py:553`).
- **Identity math:** `Closing_Stock = Opening_Stock + Σ receipts − Σ consumption − Σ returns`. **Opening_Stock is a column on `inventory`** (default 0) — admins can set it via DB Editor. Never stored as a computed total. Same pattern for `v_lot_balance` (Remaining = Received − Consumed per Lot_Number).
- **Self-healing schema** — `init_db()` adds missing cols + missing tables on every startup. No manual migrations.
- **Returns flow:** SK `submit_return_request()` → row in `pending_returns` (status `pending_hod`). HOD `approve_return_request()` is the ONLY path that writes to the `returns` ledger — never let any other code path insert into `returns` directly. Approval is idempotent (refuses to re-approve an `approved` row).
- **Attachments:** `save_entry_attachment(file_obj, …)` reads bytes once, persists BLOB to `entry_attachments.file_blob`, AND mirrors to `uploads/<Site>/<doc_type>/<doc_number>/<name>`. The disk mirror is best-effort (read-only FS → `disk_path` is empty); the BLOB is authoritative.
- **MTC docs:** `save_mtc_document(uploaded_file=None, …)` writes `status='missing'` so HOD sees the rubber item in the Pending Receipts banner. Don't filter `uploaded_file is None` upstream — let it through with status='missing'.
- **RBAC override:** `_EXACT_ROLE_PAGES` in `main.py:113` bypasses hierarchy for pages that need exact-role lock. Currently `📝 Entry Log = {store_keeper}` only.

---

## 2. Tuning Changes — What We Did (in order)

### #1 — Stock Adjustment Module (Tier 1 P1)

**Problem:** Master DB Editor was the only way to correct system-vs-shelf discrepancies, which bypassed the audit ledger.

- New `stock_adjustments` table with 9 reason codes (`ADJUSTMENT_REASONS` in database.py)
- Helpers: `insert_stock_adjustment` / `approve_stock_adjustment` / `reject_stock_adjustment` / `get_pending_stock_adjustments` / `get_stock_adjustment_history`
- **`approve_stock_adjustment` atomically posts a synthetic ledger row** — `consumption` for shortfall, `receipts` for surplus, with `Work_Type='STOCK_ADJUSTMENT'` or `Supplier='STOCK_ADJUSTMENT'` + `posted_txn_ref` ('C:rowid' or 'R:rowid')
- UI: Entry Log → new **"🧮 Stock Count"** tab (Store Keeper). HOD Portal → new **"🧮 Adjustments"** tab.

### #2 — Negative-Stock Guard at EOD Commit

**Problem:** Entry Log over-issue guard was the only check; HOD editing in EOD review, OCR bulk staging, and PWA queue all bypassed it.

- New `validate_eod_no_negative_stock(conn, site_id, edited_df) → list[violations]` in database.py
- Wired as **first step** in `_eod_commit_dialog`, before the type-COMMIT box
- If any violation: dialog shows red banner + violation table (SAP, Material, Current, ToConsume, Deficit) + Close button. Commit path locked.

### #3 — Standard-Cost Inventory Valuation

**Problem:** No money. Couldn't answer "what's the stock at Site A worth?"

- Self-heal: `inventory.Unit_Cost` (default 0) + `receipts.Unit_Cost` (nullable, captured at receive time for future weighted-average)
- 5 helpers: `get_inventory_valuation` / `get_total_inventory_value` / `get_value_by_site` / `get_consumption_value_window` / `format_sar` ("SAR 1.2M" / "SAR 125K" / "SAR 875")
- 4 cache wrappers in `cache_layer.py`
- KPI cards: Live Dashboard hero (Total stock value), HOD Portal hero (Site stock value + 30d consumption), Admin Portal Overview (Total + Biggest-value site + 30d consumption + Pending receipts value)
- Live Dashboard grid: `Unit_Cost` and `Stock_Value` columns merged in
- New report: **💰 Inventory Valuation**. Monthly Summary now carries Issued_Value_SAR / Received_Value_SAR / Closing_Value_SAR columns.

### #4 — Lot Master Table

**Problem:** Lots were inferred by date from receipts. No traceability for recalls / expiry audits.

- New `lots` table (Lot_Number, SAP, Site, Received_Date, Expiry_Date, Supplier, PR_Number, Status, UNIQUE constraint)
- Self-heal: `Lot_Number` column on `receipts` / `consumption` / `pending_issues` / `pending_receipts`
- New view `v_lot_balance` — Received_Qty / Consumed_Qty / Remaining_Qty per lot, identity math
- One-time backfill in `init_db`: legacy receipts with Expiry_Date get synthetic `LOT-YYYYMMDD-SAP` numbers
- Helpers: `create_or_get_lot` / `get_lots_for_item` / `get_all_lots` / `mark_lot_status` / `auto_generate_lot_number` / `suggest_fefo_lot_for_consumption`
- `process_receipt_delivery` + `commit_pending_receipts` now auto-create lots
- `get_fefo_lots` now prefers hard lots (from `v_lot_balance`); falls back to date-allocation for un-lotted legacy data
- Entry Log: auto-attaches FEFO Lot_Number to staged consumption

### #4.5 — Hard FEFO Override (Audit Trail)

**Problem:** Auto-FEFO is silent; if the suggested bin is physically blocked, the store keeper has no audited way to override.

- Self-heal: `FEFO_Override` column on `pending_issues` + `consumption`
- Entry Log → new **"🔄 Pull from a different lot"** expander — only renders when 2+ open lots exist
- Requires reason ≥ 5 chars to activate; sets `Lot_Number` + `FEFO_Override`, fires `log_audit_action("FEFO_OVERRIDE", ...)` + WhatsApp to site HOD in real time

### Bug fixes during tuning

- `report_monthly_summary` closed-conn crash → moved costs lookup inside the original `try` block
- `_eod_commit_dialog` `to_sql` crash → filter `edited_admin_df` to columns that exist in `pending_issues` (the EOD tab merges inventory metadata for display)
- `get_receipt_history` SQL referencing non-existent `r.id` / `r.Timestamp` → use `r.rowid DESC` (receipts table has neither)
- Mac PR email body — was pipe-text, now AppleScript-driven Mail.app HTML table with fixed-width plain-text fallback
- PR PDF — added Received Qty + Pending Qty columns
- Burn Rate compact chart — color-coded by intensity (red/amber/green) + monospace tabular numerics + legend
- Reports `_site_filter` — supervisor + HOD locked to own site; admin only gets "All Sites" dropdown
- Reports duplicate `_rep_site` key — site filter takes `key_suffix` per tab
- Cross-Site tab — reverted matrix removal; >5-item escalation queues separate WhatsApp to target HOD + audit
- HOD Notifications tab — removed the Notification Log; it moved to Admin → WhatsApp Console

### Recently-added reports

- **📥 Daily Receipts** — mirror of Daily Consumption with Receipt_Value_SAR
- **💰 Inventory Valuation** — standard-cost rollup with top-10 share

### Recently-added column hygiene

- **Material_Code in every report** (right after SAP_Code) — joined into `load_live_inventory`, `report_daily_consumption`, `report_daily_receipts`, `report_monthly_summary`, `report_fefo_compliance`, `report_pr_status`, `get_short_dated_stock`, `get_inventory_valuation`
- **`_strip_empty_columns` helper in `pages_internal/reports_page.py`** — auto-drops columns where every row is null/blank/NaN. Always keeps SAP_Code + Material_Code + Date. Never drops numeric columns (0 is data).

### Recently-added PDF formatter

- `reports.py:generate_report_pdf` — narrow fixed widths for short/code/numeric columns (Date 18mm, UOM 12mm, Qty 16mm, SAP 22mm, Lot_Number 30mm, etc.). Remaining width spreads across descriptive columns. Numeric columns right-aligned. Per-cell char budget scales with column width. Header redraws on every page break.

---

## 2C. Tuning Round 2 (2026-06) — Operational Polish & Cloud Hosting

### Streamlit Cloud hardening (no more startup crashes)

- `get_connection()` now uses `sqlite3.connect(timeout=30)`, applies WAL/synchronous/busy_timeout via per-PRAGMA try/except (Streamlit Cloud's FUSE filesystem rejects WAL), and runs a corruption probe (`SELECT name FROM sqlite_master LIMIT 1`) that wipes-and-recreates the file on `DatabaseError`. Caught: `malformed database schema (MASTER EQUIPMENTS)` from prior crashed deploys.
- `auth.seed_default_users()` switched to `INSERT OR IGNORE` (race-safe across the cloud's multiple uvicorn workers) while keeping the count-gate so `test_seed_does_not_run_if_users_exist` still passes.
- `requirements.txt`: `twilio` added; `pywhatkit` now `; sys_platform == 'win32' or sys_platform == 'darwin'` (it pulls in display libs that fail on cloud Linux).

### WhatsApp worker — Twilio + embedded thread

- `whatsapp_worker._send_whatsapp()` tries Twilio first (reads `st.secrets['twilio']` or `TWILIO_*` env vars), falls back to `pywhatkit` on local desktop. **`pywhatkit` is lazy-imported** — module-level import was stalling Streamlit startup by ~30 seconds because of pywhatkit's GUI deps.
- `main.py:78` starts the worker via `@st.cache_resource` so it spawns exactly once per server lifecycle.
- Sandbox: every Twilio recipient must `join <code>` to the sandbox number once before they can receive (out-of-band onboarding step — document in your rollout playbook).

### Material categories + rubber-MTC workflow

- New `inventory.Category` column; values from `config.MATERIAL_CATEGORIES` (`Consumable`, `Equipments`, `Utilities`, `Maintenance`, `Others`, `Rubber materials`, `Tools`, `QC items`).
- SK Receipt Staging: when selected SAP is in `Rubber materials`, show MTC Number + MTC file uploader. `save_mtc_document()` always writes a row — `status='attached'` or `status='missing'` — so HOD can act on missing ones.
- HOD Pending Receipts tab: red banner lists items with `status='missing'`, with **✉️ Draft Logistics Email** button (`mailer.draft_rubber_mtc_email`). Successful send flips status to `sent_to_logistics`.
- Reports page → Generate tab: **Filter by Category** dropdown next to the SAR toggle.

### Attachments + HOD DOC tab

- Schema: `entry_attachments` (BLOB + `disk_path` mirror; `doc_type IN ('consumption','receipt','return')`).
- SK forms: attachment expander on Consumption Log, Receipt Staging, and (via new) Return Items. Per-batch OR per-date scope. Allowed: PDF / JPEG / JPG / XLSX (`config.ATTACHMENT_ALLOWED`).
- Doc number = DDMMYY for consumption; DN_No (or manual override) for receipts; Return DN No. for returns.
- HOD Portal **📎 DOC** tab — sub-tabs Consumption / Receipt / Return, period filter, doc-number text filter, per-file ⬇️ download button.

### Real returns workflow (NEW — distinct from Returnable Items)

- Schema: `pending_returns`. Approval writes to the existing `returns` ledger so dashboard math + reports auto-update.
- SK **↩️ Return Items** tab in Entry Log: multi-row staging grid pattern. Material picker is restricted to last-30-day receipts at the SK's site. If multiple receipts of the same SAP exist, the SK picks the exact one (Date / DN / Received Qty). Qty hard-capped to that receipt's `received_qty`. Mandatory: Return DN No. + reason (work-types) + at least one attachment. Override checkbox widens picker to 12 months and requires written justification — flagged red in the HOD's queue.
- **Add to Grid** captures file bytes immediately (Streamlit UploadedFile is single-use) into `st.session_state["_ret_queue"]`. Submit Batch fires one consolidated WhatsApp ping.
- HOD **↩️ Returns** tab: one card per pending return, override rows in red. **✓ Approve** → `approve_return_request()` (idempotent) → `returns` row + `mailer.draft_return_logistics_email()` opens an Outlook/Mail.app draft. **✗ Reject** removes from pending list.
- Returnable Items remains **tool-loan only** — no DN, no attachment. Don't conflate the two.

### QR label approval flow

- Schema: `qr_approval_requests` (status `pending|approved|rejected`).
- SK **🏷️ QR Label Request** tab: `st.multiselect` materials, per-item label quantity, one Submit Batch button.
- HOD **🏷️ QR Approval** tab: checkbox-column data editor → Approve Selected / Reject Selected. Approved sub-tab has **📥 Download QR Labels PDF for ALL approved** which generates a consolidated PDF via `reports.generate_qr_labels_pdf`.

### Field-level changes

- `config.OPTIONAL_ISSUE_COLS = set()` — every field on every Entry Log / Admin / HOD form is mandatory now. **Exception:** `Expiry_Date` on receipt staging — explicitly optional.
- Live Dashboard column order (see Architecture map above). Identity formula now includes `Opening_Stock`.
- HOD Pending Receipts: `Material_Code` + `Equipment_Description` shown.
- SK Receipt Staging: `rejection_reason` filtered out (HOD-side state was leaking into the SK form).
- HOD Receive Material form rebuilt into a half/half column split so both columns are the same height regardless of how many dynamic fields exist.
- Toast emojis: Streamlit rejects "✓" / "✗" — use "✅" / "🚫" / specific glyphs.

### Per-site dropdowns (Work Type, Tank No.)

- `system_settings.Site_ID` column (NULL = global default, non-NULL = site override). `get_work_types(conn, site_id)` and `get_tank_nos(conn, site_id)` fall back to global if no site-specific values exist.
- HOD **⚙️ Site Config** tab → add/delete per-site values. `bust_settings_cache()` clears the relevant `@st.cache_data` wrappers.

### Bug found by the harness

- `receipts` table was missing the full logistics column set (`DN_No`, `Serial_No`, `Vehicle_No`, `Driver_Name`, `Pallet_No`, `Mob_From`, `Prepared_by`, `Mob_To`, `Received_by`, `DN_Copy`, `Location`, `PR`). `process_receipt_delivery` builds INSERTs from `extra_fields`, but its outer try/except swallowed the `OperationalError`. **In production, SK-staged receipts with any logistics field were silently dropped on HOD commit.** Fixed at `database.py:553` — full self-heal block.

---

## 2D. Automated bug harness — `bug_check.py`

**Run:** `python bug_check.py` (add `--verbose` to stream each check). Finishes in ~5 seconds.

Covers (114 checks across 13 areas):

- **Schema:** every table + every critical column. `init_db()` idempotency.
- **RBAC matrix:** 14 role × page combinations including the SK-only Entry Log lock.
- **Module import smoke:** every `pages_internal/*` and top-level module loads without raising.
- **Math identity:** seed → receipts/consumption/returns → `Current_Stock == 113` for SAP-001 (Opening 100 + 30 − 12 − 5).
- **Workflows:** consumption stage→commit, receipt stage→commit, return submit→approve→ledger, return reject, returnable tool loan, QR approve/reject, MTC attached + missing, attachment BLOB round-trip.
- **Reports:** every `report_*` runs; Daily Receipts has `Material_Code`.
- **Mailer:** rubber-MTC + return-logistics drafts (subprocess patched — no actual emails).
- **Audit + WhatsApp queue + Sites.**

Safety: a throwaway DB under `tempfile.mkdtemp()` is patched into `database.DB_FILE` and `database.UPLOADS_ROOT` **before** the project modules import. `subprocess.Popen` is monkey-patched to a no-op so mailer helpers never actually launch. The tmp dir is wiped on exit. `gi_database.db` is never touched.

Output: `BUG_REPORT.md` at repo root. Exit code `0` on full pass, `1` on any failure (CI-ready).

When you add a new database function or schema column, add a check at the bottom of `bug_check.py:main()` and re-run.

---

## 3. Remaining Features — Prioritized

### P0 — Operational gaps that could surprise users

1. **Maintenance Mode actually blocks non-admins at login**
   - `app_settings.maintenance_mode` is written by the toggle but `auth.py` doesn't read it
   - Need: `if get_app_setting("maintenance_mode") == "1" and user.role != "admin": show downtime page`

2. ~~**Returnable overdue WhatsApp — verify scheduler**~~ ✅ Confirmed wired. `whatsapp_worker.run_worker_loop()` calls `check_overdue_returnables()` every 60 s alongside `process_queue()`. Started from `main.py:78` via `@st.cache_resource`.

3. **WhatsApp queue retries**
   - Failed messages stay `status='failed'` with no automatic retry
   - Suggest: admin button "Retry all failed" + max-3-attempts auto-retry in worker

### P1 — Inventory discipline (what's left from original audit)

4. **Stock reservations on approved cross-site requests**
   - Today: an approved transfer doesn't earmark stock; a store keeper can consume it before it ships
   - Fix: `reservations` table + reduce available stock by sum of pending reservations in `v_site_stock`

5. **Hard FEFO enforcement (currently advisory)**
   - The override flow exists but the system never *blocks* a non-FEFO consumption — it just records the reason
   - Decision needed: do you want silent allow-and-log (current) OR hard-block-without-override?

6. **UoM conversion** (buy in Box of 100, issue in Pcs)
   - Today: 1 UoM per item, no conversion
   - Adds: `uom_conversions(SAP, from_uom, to_uom, factor)` table + form in Receive Material + DB editor

7. **Bin/location within a site**
   - Today: stock is "at Site A". No shelf/bay/bin
   - Becomes painful past ~1,000 SKUs per warehouse. Adds: `bins` table + `location` column on receipts/consumption

8. **Auto-PR drafting from below-minimum**
   - Today: shows low-stock alert; HOD types PR manually
   - Adds: button "Auto-draft PRs for all below-minimum items" → pre-fills `pr_master` rows ready to send

### P2 — Platform / scale

9. **PostgreSQL migration path**
   - SQLite + WAL handles ~10-25 concurrent comfortably; past that, contention bites
   - Already structured for it: all SQL lives in `database.py`. The PWA FastAPI layer in `pwa/api.py` is the bridge

10. **Master DB Editor save = DELETE then INSERT-all**
    - Crash-unsafe; one mid-write failure loses rows
    - Industry pattern: posted ledger rows are immutable. Corrections via reversal documents (which our Stock Adjustment flow already implements correctly — extend the discipline to other tables)

11. **Lot splitting / merging / quarantine UI**
    - Schema supports `Status` transitions but there's no UI for them — admin/HOD has to use Master DB Editor
    - Lot disposal workflow with HOD approval is sketched in the manual but unimplemented

12. **2FA**
    - Manual says "Access Control" tab has 2FA — actually placeholder only. Need TOTP library + `users.totp_secret` column + login challenge

### P3 — Polish / nice-to-have

13. **Scheduled report cron** — `report_schedules` table + UI exist but there's no actual cron daemon firing them
14. **Dashboard tile editor** — admin can pick which KPI cards appear in the hero strips
15. **Per-site Unit_Cost** (today: one cost per item across sites; SAP allows site-specific). Touches valuation math everywhere — high impact
16. **AI Insights regen scheduling** — currently on-demand only
17. **Export the USER_MANUAL.md to PDF programmatically** — the pandoc command is documented; could add a one-click button in Admin → Settings

### New (from 2026-06 round)

18. **Twilio paid number** vs sandbox — sandbox is fine for the rollout but requires every recipient to opt-in. A WhatsApp Business Account + paid number removes that step and unlocks template messages, ~$5–10/month + Meta approval.
19. **`uploads/` disk-mirror rotation** — files accumulate forever under `uploads/<Site>/<doc_type>/<doc_no>/`. Add a periodic cleanup of rows older than N months OR move the disk mirror behind a feature flag (BLOBs are authoritative anyway).
20. **`pending_returns` cleanup of rejected rows** — they sit in the table forever. Either auto-archive to a `returns_history` table after N days or surface a "Cleanup rejected" admin button.
21. **HOD QR / Return reject reason input** — currently hardcoded to "Rejected by HOD". Add a small reason textbox in the bulk-reject flow (mirrors the override-justification pattern from Returns).
22. **Categories on legacy items** — existing inventory rows default to `'Others'`. Add an Admin → DB Editor banner that flags items still on `'Others'` so the team can backfill.
23. **`Opening_Stock` audit trail** — admins can edit the value freely in DB Editor. Either log it via `log_audit_action` on save or treat it as a one-time SET at item creation.

---

## 4. How to Run / Develop

```bash
# Run app (worker auto-starts as a background thread via @st.cache_resource)
streamlit run main.py

# Standalone worker (only needed if you want to run the worker without
# the Streamlit UI, e.g. for offline desktop usage)
python whatsapp_worker.py

# Pytest — 315 expected, ~22 s
.venv/bin/python -m pytest -x --tb=short -q

# Automated bug harness — 114 checks, ~5 s, writes BUG_REPORT.md
python bug_check.py               # quiet
python bug_check.py --verbose     # streams each check

# Smoke a specific helper without Streamlit overhead
.venv/bin/python -c "
import database as d
conn = d.get_connection(':memory:')
d.init_db(conn)
# … do stuff …
"
```

### Twilio (cloud) — one-time setup

Add to Streamlit Cloud → App Settings → Secrets:

```toml
[twilio]
account_sid  = "AC..."
auth_token   = "..."
from_number  = "whatsapp:+14155238886"
```

Every recipient must join the Twilio sandbox once by sending `join <code>` to `+1 415 523 8886` on WhatsApp. Sandbox is free; a paid Twilio number removes the join step but needs an approved WhatsApp Business Account.

### Production hosting — self-host on a spare Mac/laptop + Cloudflare Tunnel

This is the recommended path for a real warehouse rollout. Free, permanent, no API costs, full control. The trade-off is that the host machine must stay on.

**Why not Streamlit Community Cloud for production**
- Disk is ephemeral. `gi_database.db` is wiped on every redeploy / weekly auto-restart / sleep wake. All consumption, receipts, returns, audit log, attachments — gone.
- Streamlit Cloud is fine as a **demo** environment. Not as the system of record.

**Components**

| Component | What it does | How to run |
|---|---|---|
| `streamlit run main.py` | The web app + embedded WhatsApp worker thread | `caffeinate -dis streamlit run main.py` (keeps machine awake without locking the display) |
| `python whatsapp_worker.py` | Standalone WhatsApp sender (needed on macOS — pywhatkit can't run from Streamlit's thread; Twilio works either way) | Second terminal |
| `cloudflared tunnel` | Exposes localhost:8501 as a public HTTPS URL | `cloudflared tunnel run gi-hub` |
| `backup_db.sh` (cron) | Nightly copy of `gi_database.db` + `uploads/` to an external disk + iCloud Drive | macOS launchd plist below |

**One-time setup (Mac)**

```bash
# 1. Install
brew install cloudflared
# 2. Authenticate (opens browser)
cloudflared tunnel login
# 3. Create a named tunnel
cloudflared tunnel create gi-hub
# 4. Route a subdomain on a Cloudflare-managed domain (you need a domain on Cloudflare for this)
cloudflared tunnel route dns gi-hub gi.yourdomain.com
# 5. Config file
mkdir -p ~/.cloudflared
cat > ~/.cloudflared/config.yml <<'EOF'
tunnel: gi-hub
credentials-file: /Users/johnsonandrew/.cloudflared/<tunnel-uuid>.json
ingress:
  - hostname: gi.yourdomain.com
    service: http://localhost:8501
  - service: http_status:404
EOF
# 6. Test it manually
cloudflared tunnel run gi-hub
# Then visit https://gi.yourdomain.com — Streamlit should answer.
```

**Auto-start everything on login (macOS launchd)**

Create three `.plist` files under `~/Library/LaunchAgents/`:

```xml
<!-- com.gi.streamlit.plist -->
<plist version="1.0"><dict>
  <key>Label</key><string>com.gi.streamlit</string>
  <key>WorkingDirectory</key><string>/Users/johnsonandrew/Downloads/CNCEC PROJECT</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string><string>-lc</string>
    <string>caffeinate -dis ./.venv/bin/streamlit run main.py --server.headless true</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/johnsonandrew/Library/Logs/gi-streamlit.log</string>
  <key>StandardErrorPath</key><string>/Users/johnsonandrew/Library/Logs/gi-streamlit.err</string>
</dict></plist>
```

(Equivalent `.plist` files for `whatsapp_worker.py` and `cloudflared tunnel run gi-hub` — same pattern, different ProgramArguments.)

Load them once:
```bash
launchctl load -w ~/Library/LaunchAgents/com.gi.streamlit.plist
launchctl load -w ~/Library/LaunchAgents/com.gi.whatsapp-worker.plist
launchctl load -w ~/Library/LaunchAgents/com.gi.cloudflared.plist
```

After reboot they auto-restart. Check with `launchctl list | grep gi`.

**Nightly backup script**

Save as `~/bin/gi_backup.sh`, `chmod +x`, then add a launchd plist that fires it daily at 02:00.

```bash
#!/usr/bin/env bash
set -euo pipefail
PROJECT="/Users/johnsonandrew/Downloads/CNCEC PROJECT"
DEST="$HOME/Library/Mobile Documents/com~apple~CloudDocs/GI_Hub_Backups"
mkdir -p "$DEST"
STAMP=$(date +%Y%m%d_%H%M%S)
# SQLite online backup (safe even while Streamlit holds the file)
sqlite3 "$PROJECT/gi_database.db" ".backup '$DEST/gi_database_$STAMP.db'"
# Attachments (mirror the BLOBs to disk too, in case the DB ever corrupts)
rsync -a --delete "$PROJECT/uploads/" "$DEST/uploads_latest/"
# Prune old backups — keep last 14 days of DB snapshots
find "$DEST" -name 'gi_database_*.db' -mtime +14 -delete
```

Restore is `sqlite3 gi_database.db ".restore '<backup>.db'"` while the app is stopped.

**Security checklist**

- Change every default password (`/admin admin2026`, etc.) on first login.
- Set `[ollama]` and `[twilio]` secrets via Streamlit's `secrets.toml` (in `~/.streamlit/secrets.toml`) — never commit them.
- Cloudflare Tunnel terminates HTTPS at the edge, so traffic from the user's browser to your Mac is encrypted end-to-end. No port forwarding needed.
- Optional: enable Cloudflare Access policies (free for 50 users) so only emails on your team's domain can reach the URL.
- Run `git pull` periodically; the self-healing `init_db()` handles schema migrations automatically.

**Updates without downtime**

```bash
cd "Downloads/CNCEC PROJECT"
git pull
launchctl kickstart -k gui/$UID/com.gi.streamlit    # restart Streamlit only
```

`launchd` brings it back within 2 seconds. Tunnel + worker stay up.

---

### Why we do NOT recommend a PyInstaller `.exe` for production

The packaging is technically possible (PyInstaller can bundle Streamlit) but:

- Bundle size: ~500 MB. Slow first launch.
- Single user, single DB. Defeats the multi-site / multi-role design.
- Streamlit's dynamic template loading fights PyInstaller; you'll spend hours on `--add-data` flags and copy-fix loops.
- No live updates. Every code change → re-bundle → reinstall.
- WhatsApp: only Twilio works reliably inside a frozen binary (pywhatkit's browser automation is fragile inside `.exe`).

**Where `.exe` IS reasonable:** a single-laptop offline demo for management who can't access the network app. For that, run a one-off bundle:

```bash
.venv/bin/pip install pyinstaller
pyinstaller --onefile --add-data "USER_MANUAL.md:." \
    --hidden-import="streamlit.runtime.scriptrunner.magic_funcs" \
    -n GIHub main.py
```

…and accept that updates require re-bundling and DB is local to that machine. The hosted path is strictly better for real use.

---

### Branded User Manual PDF — `build_manual_pdf.py`

The repo ships a standalone PDF generator that converts `USER_MANUAL.md` into a designed, management-presentable PDF using fpdf2 (already in `requirements.txt` — no new deps).

```bash
python build_manual_pdf.py                       # writes GI_Hub_User_Manual.pdf
python build_manual_pdf.py --out report.pdf      # custom output
```

Or from inside the app: **Admin → Settings → 📄 Download User Manual (Branded PDF)** → click *Build PDF now* → download.

What's in the PDF:
- Cover page with navy + gold brand panel, version, date
- Auto-generated table of contents with dotted leaders and page numbers
- Per-page header (chapter title) + footer (page X of N + brand)
- Hierarchical headings, paragraphs, bullet lists, **GFM tables**, fenced code blocks
- All non-Latin-1 characters (em-dash, smart quotes, emoji) sanitised to ASCII so fpdf's core fonts render without crashing

Current output: 65 pages, ~130 KB.

---

### Ollama — local + Streamlit-Cloud tunnel

| Where | What runs | How |
|---|---|---|
| Local Mac | Ollama on `localhost:11434` | `ollama serve` (or it's already running) |
| Streamlit Cloud | No Ollama. Tunnels to your Mac. | Tailscale Funnel **or** ngrok |

**Models used in this project** (see Admin Portal → Settings → 🤖 AI Connection for a live status panel):

| Purpose | Model id | Source |
|---|---|---|
| NL search → SQL | `qwen2.5-coder:7b` | `ai/nl_search.py` |
| Summaries / AI Insights / chat | `llama3.1:8b` | `ai/summarize.py`, `ai/insights.py` |
| **OCR (handwritten consumption + delivery notes)** | `qwen2.5vl:7b` ⚠ vision model, pull separately | `ai/ocr.py` |
| RAG / embeddings (reserved) | `nomic-embed-text:latest` | unused so far |

Pull once locally: `ollama pull qwen2.5vl:7b` (otherwise the SK Entry Log "📷 Upload Handwritten Consumption List" expander shows a `Vision model not installed` error with the exact pull command).

**Streamlit Cloud → local Ollama via Tailscale Funnel** (recommended — encrypted, free, no opening firewall ports):

1. On your Mac: `brew install tailscale` (or download from tailscale.com), sign in.
2. Enable Funnel on port 11434:
   ```bash
   tailscale funnel --bg 11434
   ```
   Tailscale prints a public HTTPS URL like `https://johnsons-air.tail1234.ts.net`.
3. In Streamlit Cloud → App Settings → Secrets, add:
   ```toml
   [ollama]
   host         = "https://johnsons-air.tail1234.ts.net"
   vision_model = "qwen2.5vl:7b"
   ```
4. Reload the app. Admin Portal → Settings → **🤖 AI Connection** should show "✅ Reachable" plus a per-model INSTALLED / MISSING table.

**Alternative — ngrok** (faster setup, free tier rotates URLs daily):

```bash
brew install ngrok && ngrok config add-authtoken <your_token>
ngrok http 11434
```

Copy the `https://...ngrok-free.app` URL into Streamlit Secrets as `[ollama] host`. Note: free ngrok URLs change on every restart.

Caveats: AI features only work while your Mac is on, awake, and connected. Put `caffeinate -d` in front of `ollama serve` if you want the display to stay off but the server to stay up.

### Default credentials (change immediately)

- admin / admin2026 · hod / hod2026 · supervisor / super2026 · worker / floor2026

---

## 5. Where to Look When You're Stuck

| Symptom | First place to check |
|---|---|
| "Cannot operate on closed database" | A helper closes its own conn then keeps using it after `finally` (see the `report_monthly_summary` fix pattern) |
| `to_sql` crash on append | DataFrame has columns the table doesn't (see EOD dialog filter pattern: `PRAGMA table_info(...)` then column-filter) |
| Streamlit duplicate key error | Two tabs/widgets share a `key=` — pass a `key_suffix` per call site |
| Live stock wrong / stuck | `bust_inventory_cache()` not called after the write that mutated receipts / consumption / returns |
| Report column wrong / missing | Source query in `database.py` — `report_*` functions or fetcher (`get_*`). Material_Code is now in all the JOINs. |
| FEFO panel shows nothing | `lots` table empty for that SAP+Site AND no receipts with Expiry_Date → check `process_receipt_delivery` was called with expiry; otherwise legacy fallback in `get_fefo_lots` should trigger |
| WhatsApp not delivered | `whatsapp_worker.py` not running; check Admin → WhatsApp Console → status pills |
| Audit log filter shows nothing | `system_audit_log` populated by `log_audit_action(username, action_type, target_table, details)` — check the function was actually called from the code path you expect |
| Streamlit startup hangs on `_start_whatsapp_worker()` | Something at module-level inside `whatsapp_worker.py` pulled in a heavy dep. Lazy-import the offender like we did for `pywhatkit` (`whatsapp_worker.py:32-50`) |
| `OperationalError: no such column: X` on receipt commit | The column is missing from the `receipts` self-heal block. See `database.py:553`. **The error gets swallowed** by `process_receipt_delivery`'s try/except, so it shows up as a silent drop, not a crash. Always extend the self-heal when `commit_pending_receipts` starts propagating a new column. |
| Streamlit `StreamlitAPIException: not a valid emoji` | `st.toast(icon=...)` accepts a real emoji, NOT a glyph like "✓" or "✗". Use "✅" / "🚫" / a specific emoji. |
| Return approved but stock didn't move | Cache. `approve_return_request` writes to `returns`; the dashboard reads via `cached_live_inventory`. The approve flow calls `bust_inventory_cache()`. If a future caller forgets that, the dashboard stays stale until the next bust. |
| Attachment file missing from `uploads/` but downloads from HOD DOC tab still work | Expected. The DB BLOB is authoritative; the disk mirror is best-effort (read-only FS / Streamlit Cloud restart). |
| Tests pass but UI crashes on render | pytest doesn't render the page. Run `python bug_check.py` for the data-layer coverage, then click through the affected page manually. Streamlit AppTest harness (Tier B) is an unbuilt option in this repo. |
| `FPDFUnicodeEncodingException: Character X outside the range` | fpdf2 core fonts are Latin-1 only. Add the offender's glyph → ASCII mapping in `build_manual_pdf.py:_REPLACE`. Don't switch to a Unicode TTF font unless you're OK with the 1-3 MB binary footprint that adds. |
| Self-host: app unreachable from outside | `launchctl list \| grep gi` — all three (streamlit, whatsapp-worker, cloudflared) should show non-zero PIDs. Check `~/Library/Logs/gi-*.err` for crashes. Test `curl http://localhost:8501` first to isolate Streamlit vs tunnel. |
| Self-host: backup script fails | Run it manually under your shell to see the error. Common: `sqlite3` not on $PATH (use `/opt/homebrew/bin/sqlite3`); iCloud Drive path not present (system migration?). |

---

## 6. Inventory of Schema Additions

**Pre-2026-06 round (Tier-1 tuning):**
- `app_settings(key, value)` — thresholds, maintenance_mode, last_backup_at
- `bug_reports`, `report_schedules`, `report_archive`
- `stock_adjustments` + 9 reason codes
- `lots` + `v_lot_balance` view
- `inventory.Unit_Cost`, `receipts.Unit_Cost`
- `pr_master.workflow_state, UOM, Supplier, Est_Cost_SAR, Notes`
- `pending_receipts.rejection_reason`
- `receipts.Lot_Number`, `consumption.Lot_Number`, `pending_issues.Lot_Number`, `pending_receipts.Lot_Number`
- `pending_issues.FEFO_Override`, `consumption.FEFO_Override`

**2026-06 round:**
- New tables:
  - `qr_approval_requests` — SK label requests → HOD approval → consolidated PDF download
  - `entry_attachments` — BLOB + disk-mirror path; `doc_type IN ('consumption','receipt','return')`
  - `mtc_documents` — rubber-material MTC; `status IN ('attached','missing','sent_to_logistics')`
  - `pending_returns` — SK return staging → HOD approval; `override_required` flag for >30-day returns
- Inventory: `Category` (default `'Others'`) + `Opening_Stock` (default 0)
- System settings: `system_settings.Site_ID` (NULL = global, non-NULL = site-specific)
- Receipts: `DN_No`, `Serial_No`, `PR`, `Location`, `Vehicle_No`, `Driver_Name`, `Pallet_No`, `Mob_From`, `Prepared_by`, `Mob_To`, `Received_by`, `DN_Copy` — closes the silent-drop bug

All added via self-healing `ALTER TABLE` in `init_db()`. None require manual migration. The bug harness asserts every column listed here.

---

## 7. Hidden surprises a future session needs to know

1. **`process_receipt_delivery` swallows OperationalError.** Any new column it propagates via `extra_fields` MUST exist on `receipts` — otherwise SK input silently disappears at HOD commit time. Add to the self-heal at `database.py:553` and to the `bug_check.py` schema list.
2. **Streamlit `UploadedFile` is single-use.** If you stash files in `st.session_state` (e.g. multi-row staging), read `.read()` immediately at Add-to-Grid time and store the bytes. The `_BytesBlob` wrapper in the Return Items submit handler exists for exactly this reason.
3. **`@st.cache_resource` is the only safe place to start daemon threads.** Module-level threads spawn duplicates because Streamlit re-execs `main.py` on every interaction.
4. **`pywhatkit` import is heavy.** Keep it lazy. If you import any other GUI lib (`tkinter`, `pyautogui`, …) at module level, expect the same hang.
5. **The `returns` ledger has only one writer in approved flows:** `approve_return_request`. Anything else that needs to reduce stock should go through Stock Adjustments, not direct `INSERT INTO returns`.
6. **Toast icon emojis are validated by Streamlit.** Stick to actual single emojis (✅ 🚫 📨 📦 ⚠️ …), not shortcodes or geometric glyphs.
7. **Streamlit Cloud filesystem rejects WAL mode.** `get_connection()` applies PRAGMAs in a per-pragma try/except. Don't refactor that block back into a single statement.
8. **HOD Portal hides itself from Admin in the sidebar** but `_can_access('admin', '📋 HOD Portal')` returns `True` — admin can navigate there if the URL is set. That's deliberate (Admin can shadow for support). Don't "fix" it.

---

**End of handoff. Read this file first, then `USER_MANUAL.md` §13 for the latest UI reference. Run `python bug_check.py` before and after any database/mailer/page change.**
