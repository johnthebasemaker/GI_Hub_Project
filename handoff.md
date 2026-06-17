# GI Hub ERP — Handoff

**Last update:** 2026-06 round 4 — **Phase 6A–F (Workstream A) SHIPPED.** CV/QR foundation, employee master + bulk badge PDF, Smart Scan camera flow with adoption telemetry, YOLOv8 training pipeline + Tool Catalogue manager, hourly returnable-loan reminder sweep with Borrower → SK → Supervisor escalation. Plus emergency fixes: HOD Portal `fillna` crash, Live Dashboard visibility for global roles, warehouse binding for admin shadow.
**Test status:** **268/268 in `bug_check.py` · 16/16 in `test_ui_crawler.py`** (run `python bug_check.py && python test_ui_crawler.py`).
**Production hosting:** Self-host on `giinventory.com` via Cloudflare Tunnel + Access (email allow-list `@generalindustries.net`). Turnkey installer at `host_setup/scripts/install.sh`. See §4 "Run / Develop" and the "Production hosting" chapter.
**Purpose:** Get the next session productive in <5 minutes — architecture, what changed, what's next.
**Companion docs:** `USER_MANUAL.md` (every page/tab/button), `SOP.md` (Logistics + Warehouse operating procedure with cadences, decision trees, escalation matrix), `docs/cv_training_guide.md` (Phase 6C — capture → label → train → promote walkthrough).

---

## 1. Architecture Map (touch-tested files)

```
CNCEC PROJECT/
├── main.py                       Page routing, RBAC gate, sidebar.
│                                 + 🔔 Notifications bell with unread badge +
│                                 inbox modal (dialog), routes to Logistics +
│                                 Warehouse portals via exact-role lock.
│                                 + Hub Assistant pre-flights ollama health()
│                                 and shows a clean st.warning on offline.
├── config.py                     Constants, ROLES (now 6), ROLE_HIERARCHY (parallel
│                                 procurement ladder), PAGE_ACCESS (7 entries), brand
│                                 colours, MATERIAL_CATEGORIES, MTC_REQUIRED_CATEGORY,
│                                 RL_BL_FAMILY_TOKENS + classify_rl_bl_family(),
│                                 WHATSAPP_ENABLED master switch + WHATSAPP_TRIGGERS
│                                 per-event toggle dict (16 keys).
├── database.py     (~5,200 LOC)  ALL SQL + schema + helpers + reports (no Streamlit).
│                                 Phase C additions: 12 new procurement tables,
│                                 60+ new helpers, 3 new reports, T-2/T-1/T-0 sweep,
│                                 in-app notifications inbox query layer.
├── cache_layer.py                @st.cache_data wrappers, bust_inventory_cache()
├── auth.py                       bcrypt login, render_user_management_tab
├── ui_components.py              Custom CSS, AgGrid wrapper, charts, brand headers,
│                                 STATUS_BADGE_JS, LOAN_STATUS_BADGE_JS, status_pill_html
├── mailer.py                     Outlook (Win COM) / Mail.app (AppleScript) / mailto:
│                                 + draft_rubber_mtc_email, draft_return_logistics_email
├── reports.py                    PDF/Excel/CSV generators with narrow-column smart widths
├── whatsapp_worker.py            Twilio API (cloud) → pywhatkit (local fallback, lazy-imported).
│                                 Started as @st.cache_resource thread from main.py:78.
│                                 Phase C: 60-sec poll loop now also calls
│                                 _maybe_run_delivery_reminders() once per local day
│                                 (idempotent via app_settings.delivery_reminders_last_run
│                                 day marker + delivery_reminders_sent UNIQUE constraint).
│
├── pages_internal/
│   ├── live_dashboard.py         Hero strip (4 cards inc. Stock Value SAR).
│   │                             Grid column order: SAP_Code → Material_Code → Desc → UOM →
│   │                             Opening_Stock → Receipt → Consumption → Return →
│   │                             Closing_Stock → Min → Unit_Cost → Stock_Value → Category.
│   ├── daily_issue_log.py        6 SK tabs (unchanged) + ONE new expander at top of
│   │                             Receipt Staging: 🚚 Incoming Delivery Notes from
│   │                             Warehouse. Click "Mark as Received" → row lands in
│   │                             `receipts` ledger; DN flips to 'received'.
│   ├── hod_portal.py             15 tabs (13 unchanged + 2 NEW appended):
│   │                               original 13: EOD · Cross-Site · Burn Rate ·
│   │                               Pending Receipts · Returns · Adjustments · PRs ·
│   │                               Shelf-Life · Notifications · My Requests ·
│   │                               Site Config · DOC · QR Approval.
│   │                               NEW:
│   │                               🚚 DN Approvals — HOD approves warehouse-prepared
│   │                                 DNs → stages pending_receipts for SK confirm.
│   │                               🚚 In-Transit — 3 read-only sub-tabs: Active DNs
│   │                                 (with 🔁 reschedule popover per row), My
│   │                                 reschedule requests, Force-closures affecting me.
│   │                             Plus 1 additive expander in the existing PR tab:
│   │                             🚚 Submit PR(s) to Logistics Portal.
│   ├── admin_portal.py           11 tabs (10 unchanged + 1 NEW appended):
│   │                               🚚 Logistics Oversight — cross-site read-only
│   │                               view of every PR/PO/DN/return/force-closure with
│   │                               site + warehouse filters.
│   │                             Pending Requests tab: SQL now LEFT-JOINs inventory
│   │                             to surface Material_Code + Material_Name + UOM
│   │                             alongside SAP_Code (Bug 2 fix).
│   │                             WhatsApp Console: redundant +3h timedelta removed
│   │                             (Bug 1 fix — _localize already converts).
│   ├── logistics_portal.py  (NEW) 8 tabs role-locked to {logistics, admin}:
│   │                               📥 Incoming PRs · 🧾 Create PO (manual + PDF) ·
│   │                               📋 Open POs · 🏭 Assign to Warehouse ·
│   │                               🔁 Reschedules · 🛑 Force-Close ·
│   │                               ↩️ Vendor Returns · 📂 History.
│   │                             PO PDF upload uses pdfplumber to extract header,
│   │                             line items, and the PO Annexure delivery schedule.
│   │                             Vendor master with inline-add.
│   ├── warehouse_portal.py (NEW) 6 tabs role-locked to {warehouse_user, admin}:
│   │                               🔔 Incoming Assignments · 📦 Receive Goods ·
│   │                               📝 Prepare DN · ✈️ Outbound DNs ·
│   │                               ↩️ Returns from Site · 📂 History.
│   │                             Three independent layers guarantee Unit_Price +
│   │                             Total_Price + monetary header fields NEVER show
│   │                             in any warehouse view. Admin shadow uses a
│   │                             sidebar warehouse picker.
│   └── reports_page.py           14 reports total (11 unchanged + 3 NEW):
│                                 🧾 PO Status · 🏭 Warehouse Throughput ·
│                                 🛑 Force-Closures. Each PDF/Excel/CSV via the
│                                 existing toolchain. Site + date filters honoured.
│
├── ai/                           Unchanged (NL search, OCR, fuzzy, insights)
├── pwa/                          Unchanged
│
├── bug_check.py                  Standalone smoke harness. Run `python bug_check.py`.
│                                 Writes BUG_REPORT.md. Throwaway DB, never touches live.
├── BUG_REPORT.md    (generated)  Latest bug_check output — pass/fail per check, by area.
├── build_manual_pdf.py           Markdown → branded fpdf2 PDF (cover + TOC + headers).
│                                 Used by Admin → Settings → "Download User Manual".
├── host_setup/      (NEW)        Turnkey Path-A deployment for the host Mac.
│   ├── README.md                 45-min step-by-step install playbook
│   ├── cloudflared_config.yml.example
│   ├── launchd/                  4 plist templates with __PROJECT_DIR__ placeholders
│   │   ├── com.gi.streamlit.plist.tmpl
│   │   ├── com.gi.whatsapp-worker.plist.tmpl
│   │   ├── com.gi.cloudflared.plist.tmpl
│   │   └── com.gi.backup.plist.tmpl
│   └── scripts/
│       ├── install.sh            render + launchctl load all four
│       ├── uninstall.sh          remove without touching data
│       ├── restart_app.sh        zero-downtime restart after git pull
│       ├── run_streamlit.sh      wrapper exec'd by streamlit plist (avoids exit-126)
│       └── backup_db.sh          SQLite online backup + iCloud + 14-day prune
├── ai/manual_qa.py  (NEW)        Role-aware Q&A over USER_MANUAL.md. Sidebar widget.
├── USER_MANUAL.md                §1–13 user catalogue. §14 = host operations chapter.
└── handoff.md                    THIS FILE
```

### Critical contracts (do not break)

- **`config.utc_to_local(value, fmt)`, `config.localize_timestamps_df(df, cols)`, and `config.auto_localize_timestamps(df)`** — display-time UTC → GMT+3 conversion. DB stays UTC; helpers add `TZ_OFFSET_HOURS` (defaults to 3 / Asia/Riyadh) at render.
  - **`auto_localize_timestamps(df)`** is the one you want 99% of the time — it scans for any column matching the canonical set (`_DEFAULT_TS_COLS` in `config.py`) and converts only those. Idempotent on already-localized strings.
  - **Every display-bound `get_*` helper in `database.py` already wraps its result through `_localize()`** — `get_pending_returns`, `list_qr_requests`, `get_pending_requests`, `get_returnable_items`, `get_receipt_history`, `get_whatsapp_log`, `get_pending_stock_adjustments`, `get_stock_adjustment_history`, `get_missing_mtc_for_site`, `get_wbs_for_site`. Callers get GMT+3 strings for free.
  - For ad-hoc `pd.read_sql` calls at the page level (no helper), call `auto_localize_timestamps(df)` right after the read. Already done in Admin Pending Cross-Site, Admin Audit Log, Admin Live Activity Feed, Admin WhatsApp Console, HOD Cross-Site Incoming, HOD Pending Receipts, HOD PR tab, HOD My Requests, SK QR Requests, SK Returnable Items.
  - If you add a new timestamp column to the schema, ALSO add its name to `_DEFAULT_TS_COLS` so auto-detection picks it up everywhere.
- **`GI_SUPPRESS_EMBEDDED_WORKER=1`** — when set, `main.py` skips spawning the embedded WhatsApp worker thread. The Streamlit plist sets this so the standalone `com.gi.whatsapp-worker` process is the only consumer. Without this, both workers race and the embedded one (daemon thread, fails macOS Cocoa main-thread guard) flips every message to `failed`.
- **macOS WhatsApp sender uses ONLY `System Events` for input** (`whatsapp_worker._send_via_chrome_macos`). No `tell application <browser>` anywhere → no Automation permission prompt. Needs Accessibility permission once on the Python binary. If you ever add new AppleScript snippets here, keep them `System Events`-only or the prompt comes back.
- **`commit_eod(conn) → int`** — signature unchanged. Moves `pending_issues` → `consumption`, deletes staged.
- **`process_receipt_delivery(conn, date, sap, qty, supplier, remarks, site, pr_number, expiry_date, extra_fields)`** — auto-creates lot row when Lot_Number provided or Expiry_Date set. Idempotent via UNIQUE on lots. **`extra_fields` MUST be a column on `receipts`** — the function swallows OperationalErrors, so missing columns silently drop SK input. The receipts self-heal block now covers the full 15-column logistics set (see `database.py:553`).
- **Identity math:** `Closing_Stock = Opening_Stock + Σ receipts − Σ consumption − Σ returns`. **Opening_Stock is a column on `inventory`** (default 0) — admins can set it via DB Editor. Never stored as a computed total. Same pattern for `v_lot_balance` (Remaining = Received − Consumed per Lot_Number).
- **Self-healing schema** — `init_db()` adds missing cols + missing tables on every startup. No manual migrations.
- **Returns flow:** SK `submit_return_request()` → row in `pending_returns` (status `pending_hod`). HOD `approve_return_request()` is the ONLY path that writes to the `returns` ledger — never let any other code path insert into `returns` directly. Approval is idempotent (refuses to re-approve an `approved` row).
- **Attachments:** `save_entry_attachment(file_obj, …)` reads bytes once, persists BLOB to `entry_attachments.file_blob`, AND mirrors to `uploads/<Site>/<doc_type>/<doc_number>/<name>`. The disk mirror is best-effort (read-only FS → `disk_path` is empty); the BLOB is authoritative.
- **MTC docs:** `save_mtc_document(uploaded_file=None, …)` writes `status='missing'` so HOD sees the rubber item in the Pending Receipts banner. Don't filter `uploaded_file is None` upstream — let it through with status='missing'.
- **RBAC override:** `_EXACT_ROLE_PAGES` in `main.py` bypasses hierarchy for pages that need exact-role lock. As of v3.0 it carries FOUR locks: `📝 Entry Log = {store_keeper}`, `📋 HOD Portal = {hod, admin}`, `🚚 Logistics Portal = {logistics, admin}`, `🏭 Warehouse Portal = {warehouse_user, admin}`. The HOD lock was added so the procurement roles (which sit higher in the numeric hierarchy) do NOT inherit access via `ROLE_HIERARCHY` comparison.
- **Warehouse view masks prices.** `get_assignment_detail()` and every warehouse-visible PO drill-down call `get_po_detail(hide_prices=True)`, which blanks `Unit_Price` + `Total_Price` AND strips `Total_Amount`, `Freight_Charges`, `Handling_Charges`, `Discount_Amount`, `Amount_In_Words` from the header dict. The blanking is then re-applied defensively at the assignment layer in case a future caller bypasses `hide_prices`. Three layers. Don't remove any of them.
- **RL/BL strict separation.** Rubber Lining and Brick Lining items must NEVER aggregate with each other on any PO line, DN, or Warehouse split. Three enforcement points: (a) `po_items.rl_bl_family` is tagged at insert time via `config.classify_rl_bl_family(material_code, description)`; (b) `create_delivery_note()` rejects any payload that spans more than one family with the message `"Strict separation violated: this DN spans multiple RL/BL families. Prepare one DN per family."`; (c) `delivery_notes.rl_bl_family` carries the family forward so reports can group by it. Allowed values are `'RL'`, `'BL'`, or `NULL` — never a combo string.
- **DN state machine.** `draft → pending_logistics → logistics_approved → pending_hod → hod_approved → pending_sk → received`, with `rejected` as terminal from any pending state. The transient `logistics_approved` / `hod_approved` rows are observable in queries but the workflow flips straight through to the next pending state in the same UPDATE; they exist for log clarity.
- **PO over-ship guard (DN side).** `create_delivery_note()` computes available stock as `available = Delivered_Qty − Returned_Qty − Σ(qty on live DNs for this po_item)`, where "live" = DN status NOT IN (`'rejected'`, `'cancelled'`). Conflating `Delivered_Qty` (received from vendor) with "already shipped" was the bug found mid-Phase-3. Don't replace this calc with a naive `Qty − Delivered_Qty` check.
- **DN → SK → receipts handoff.** On HOD approval, `hod_decide_dn(approve=True)` mirrors the DN lines into `pending_receipts` with `status='pending_sk'` (a new status value). HOD's existing Pending Receipts tab filters by `status='pending_hod'` so the mirror rows don't bleed into that tab. The SK confirms via the new expander in Receipt Staging → `sk_mark_dn_received` writes one `receipts` row per line (with `DN_Number`, `Warehouse_ID`, `PO_Number_Source` populated), flips the DN to `'received'`, deletes the mirror rows, and busts the inventory cache.
- **Reminder dedup.** `sweep_delivery_reminders()` is idempotent across runs via TWO independent guards: (1) `delivery_reminders_sent` table has UNIQUE(`ref_type`, `ref_number`, `target_date`, `offset_days`) so INSERT-OR-IGNORE blocks per-target double-fires; (2) `app_settings.delivery_reminders_last_run` stores today's ISO date so the worker's 60-sec poll loop skips the whole sweep on subsequent ticks. Restarting the worker mid-day is safe.
- **WhatsApp triggers gate every event.** Every Phase C notification fires `queue_app_notification(...)` (always — in-app inbox) AND `fire_whatsapp_event(event_key, phone, msg)` (gated by `config.WHATSAPP_ENABLED` master + `config.WHATSAPP_TRIGGERS[event_key]` per-event). Flip a key to `False` to silence WhatsApp for that event without touching in-app behaviour. Default toggles: `warehouse_acknowledged=False` (low value), `dn_received_by_sk=False` (closure ping), everything else `True`.

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

## 2E. Tuning Round 3 (2026-06) — v3.0 Procurement Chain Architecture

The largest single feature batch since launch. Five phases, fully additive, no edits to the existing SK / HOD / Admin tabs, EOD commit path, identity math, cache layer, mailer, WhatsApp worker, or Ollama integration. Phase 1 schema + roles, Phase 2 Logistics Portal, Phase 3 Warehouse Portal, Phase 4 HOD In-Transit, Phase 5 Admin Oversight + bell + reminders + reports.

### Two new role-locked portals

- **🚚 Logistics Portal** (`pages_internal/logistics_portal.py`) — 8 tabs. Role-locked to `{logistics, admin}` via `_EXACT_ROLE_PAGES`.
- **🏭 Warehouse Portal** (`pages_internal/warehouse_portal.py`) — 6 tabs. Role-locked to `{warehouse_user, admin}` via the same lock dict.
- Admin shadow: an admin who picks either page sees a sidebar warehouse picker (warehouse portal) or just lands as logistics-equivalent (logistics portal). For the warehouse portal, the shadow warehouse picker writes to `st.sidebar.selectbox(..., key="_wh_admin_shadow_wh")` so context survives reruns.

### End-to-end procurement chain

```
Site HOD → submits PR    → Logistics: 📥 Incoming PRs
Logistics → creates PO   → Site HOD: 📋 Purchase Requests + Admin Portal
Logistics → assigns to WH → Warehouse: 🔔 Incoming Assignments
Warehouse → acks + receives from vendor
Warehouse → drafts DN (RL/BL safe) → Logistics: ✈️ approval queue
Logistics → approves date → HOD: 🚚 DN Approvals
HOD → approves content → SK: 🚚 Incoming DNs expander (in Receipt Staging)
SK → marks received → receipts row + DN closed + inventory cache busted
```

The legacy email-based PR follow-up (HOD Portal → PR tab → 📧 Draft Outlook Email + 📥 Download PR PDF) is PRESERVED and continues to work for direct-with-vendor flows. The new in-app chain is opt-in: HODs activate it per-PR via the new "🚚 Submit PR(s) to Logistics Portal" expander on the PR tab. **Both paths coexist for now; email path is marked for future deprecation once procurement chain adoption is complete.**

### Site HOD additions (additive only)

- New action in PR tab: "🚚 Submit PR(s) to Logistics Portal" expander — multi-select open PRs → submit. Calls `submit_pr_to_logistics()` which flips matching `pr_master` rows to `logistics_status='submitted'` and fires `pr_submitted_to_logistics` event to the `logistics` role.
- New 14th tab: **🚚 DN Approvals** — per-DN card with View Lines + ✅ Approve / ❌ Reject popover (mandatory rejection reason). Approval calls `hod_decide_dn(approve=True)` which mirrors DN lines into `pending_receipts.status='pending_sk'` so SK sees them on the next tab.
- New 15th tab: **🚚 In-Transit** — 3 read-only sub-tabs:
  - Active in-transit DNs with per-DN status pill, RL/BL family chip, expand-to-preview lines, and **🔁 Request reschedule popover** (date defaults to current ETA + 3 days; `min_value=today`; mandatory reason). Submits to existing `request_reschedule(po, dn, current, requested, reason, role='hod', user=username)` helper.
  - My reschedule requests — custom HTML table with status pill + decided-by + notes.
  - Force-closures affecting me — 3-way join fallback (Site_ID direct → `purchase_orders.Site_ID` → `pr_master.Site_ID`) so closures with NULL Site_ID still surface on the originating site.

### Site SK addition (additive only)

- New expander at top of Receipt Staging: **🚚 Incoming Delivery Notes from Warehouse** — per-DN container with View Lines + ✅ Mark as Received button. Calls `sk_mark_dn_received()` which:
  1. Writes one `receipts` row per DN line (with `DN_Number`, `Warehouse_ID`, `PO_Number_Source` populated for full traceback)
  2. Flips the DN to `'received'`
  3. Deletes the mirror rows from `pending_receipts`
  4. Calls `bust_inventory_cache()` so Live Dashboard reflects new stock the same minute

### Admin additions

- New 11th tab in Admin Portal: **🚚 Logistics Oversight** — KPI strip (Open PRs / Open POs / Active DNs / Vendor Returns / Reschedules / Force-Closures) + site + warehouse filters + 6 sub-tabs (PRs / POs / DNs / Vendor Returns / Force-Closures / Reschedules). 100% read-only — admin still acts in role-specific portals for any mutation.

### Sidebar: 🔔 Notifications bell

- Rendered in `main.py:_render_notifications_bell()` between role card and navigation radio. Reads `count_unread_notifications()`.
- Unread count → red pill badge (caps at "99+"). Button text changes to `"Open inbox (N unread)"` with `type='primary'` when N>0.
- `@st.dialog("🔔 Notifications")` opens modal with Only-unread toggle, per-card severity colour (info/warning/critical/success), per-row 👁 Mark read, bulk ✅ Mark all as read.
- Backed by `app_notifications` table. Visibility = `recipient_user=me OR (recipient_role=my_role AND (site/warehouse scoping matches))` — the OR clause is **parenthesised** so the optional `AND read_at IS NULL` binds to both branches (this was a bug found mid-Phase 1).

### Reminder sweep

- `database.sweep_delivery_reminders(today=None)` fires T-2 / T-1 / T-0 events for upcoming `purchase_orders.Expected_Delivery` AND `delivery_notes.DN_Date`.
- Severity: T-2 / T-1 = `warning`, T-0 = `critical`.
- PO fires: Logistics + originating Site HOD.
- DN fires: Logistics + Site HOD + warehouse_user at the receiving warehouse.
- Idempotent via UNIQUE(`ref_type`, `ref_number`, `target_date`, `offset_days`) on `delivery_reminders_sent` table.
- Triggered from `whatsapp_worker._maybe_run_delivery_reminders()` which day-marker-guards via `app_settings.delivery_reminders_last_run` — the 60-second poll loop runs the sweep at most once per local day.

### Three new reports (in the existing reports module)

- **🧾 PO Status** — per-PO ordered / delivered / returned qty totals + line count + status. Summary: Open / Delivered / Closed-or-Force / Total Value SAR.
- **🏭 Warehouse Throughput** — DN counts by warehouse, split by every pipeline state + RL/BL family. Summary: Warehouses / DNs / Received / Open pipeline / RL DNs / BL DNs.
- **🛑 Force-Closures** — every PR/PO/line closure with reason, closed-by, timestamp.

All three honour the existing site + date filters and export as PDF/Excel/CSV through the existing toolchain. Access: Supervisor + HOD + Admin (same as existing reports).

### Bug-fix patches that shipped with v3.0

- **Double TZ removed** in Admin Pending Requests, HOD My Requests, and Admin WhatsApp Console: `get_pending_requests()` and `get_whatsapp_log()` already pass results through the `_localize()` boundary helper. Page-level `localize_timestamps_df` / `auto_localize_timestamps` / `timedelta(+3)` calls were stacking. Removed.
- **Pending Requests now joins inventory.** `get_pending_requests()` LEFT JOINs `inventory` so each row carries `Material_Code` + `Material_Name` + `UOM`. Admin Pending Requests editor reorders columns + uses `column_config` to relabel headers.
- **Ollama unreachable now graceful.** `main.py` Hub Assistant pre-flights `manual_qa.health()` before streaming. Unreachable → clean `st.warning("🤖 Local AI is offline. Please run 'ollama serve' in your terminal to enable the AI assistant.")`. Mid-stream connection drops caught by `(ConnectionError, OSError, TimeoutError)`. No more raw `http://localhost:11434` strings leaking into chat.

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

### New (from v3.0 — procurement chain)

24. **Vendor master maintenance UI** — vendors get inline-added during PO creation, but there's no top-level "vendors admin" tab. Admin currently has to use Master DB Editor → `vendors` table. Build a dedicated vendor manager in Admin Portal with bulk import + duplicate detection.
25. **Reminder cadence is hardcoded T-2 / T-1 / T-0.** Some sites want T-7 / T-3 / T-1 / T-0 (longer lead time). Add `app_settings.reminder_offsets` as a JSON list and read it in `sweep_delivery_reminders()`.
26. **Per-warehouse SLA dashboards.** Throughput report exists but no live "warehouse health" page. Add: average ack time, average receive time, partial-delivery rate, RL/BL split ratio per WH.
27. **Mobile-optimised warehouse PWA.** Warehouse floor users would benefit from a barcode-scanner-first PWA for receive + DN preparation. PWA framework already lives in `pwa/`.
28. **Force-close UNDO window.** Currently force-closing is terminal. Add a 24-hour grace where admin can revert (with audit trail).
29. **Email path deprecation timer.** Once procurement chain adoption is >80% of PRs, formally deprecate the "📧 Draft Outlook Email" + "📥 Download PR PDF" buttons on HOD PR tab. Track adoption via `pr_master.logistics_status='in_po'` count vs. total.
30. **DN line auto-FEFO.** Warehouse currently types Lot_Number on the DN draft form. Could pre-fill from the FEFO suggestion at the destination site (mirroring SK Entry Log behaviour).
31. **PO PDF extractor — multi-vendor template support.** Current `process_po_pdf()` is calibrated against the GI sample layout. Different vendors send different layouts. Build a template registry where Logistics can train new layouts in-app.
32. **Procurement chain analytics in AI Insights.** Add an LLM probe for "stuck POs", "vendor delay patterns", "RL/BL throughput imbalance".

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

### Production hosting — full comparison + giinventory.com playbook

You own `giinventory.com` on Cloudflare. That's the front door — pick a back door (where the app actually runs) from this table.

| Option | Always-on without Mac? | DB persists? | WhatsApp | AI / Ollama | Free? | Best for |
|---|---|---|---|---|---|---|
| **A. Self-host on Mac + Cloudflare Tunnel** | ❌ Mac must stay on | ✅ on Mac disk | pywhatkit OR Twilio | ✅ local (when Mac on) | Yes (~$8 domain only) | True warehouse rollout with Mac as the server |
| **B. Fly.io free tier + Cloudflare DNS** | ✅ Always-on cloud VM | ✅ Fly volume + nightly B2 backup | Twilio (cloud) OR tunneled pywhatkit | ❌ unless Mac+Tailscale | Yes | Always-on, no Mac dependency |
| **C. Streamlit Cloud + Litestream → B2** | ✅ but sleeps weekly | ✅ via Litestream replication | Twilio | ❌ unless Mac+Tailscale | Yes | Minimum-effort cloud, accept brief restarts |
| **D. Render free tier** | ⚠ sleeps after 15 min, wakes ~30 s | ✅ persistent disk add-on | Twilio | ❌ unless Mac+Tailscale | Yes (1 sleeping service) | Low-traffic, idle-tolerant |
| **E. Cheap VPS** ($5/mo Hetzner/Contabo) | ✅ Always-on, no sleep | ✅ permanent | Twilio OR own Ollama+Mac for AI | $5/month + $8 domain | Tightest control, mid-cost | Mid-scale rollout where Mac is awkward |

The honest verdict: **A** is best when the Mac CAN stay on (it can; it's a laptop in a server room with FileVault). **B (Fly.io)** is the best free always-on alternative. **C (Streamlit Cloud + Litestream)** is the easiest if you accept ~5 sec cold-start after weekly auto-restarts. Don't use D for a real warehouse — the sleep wake is painful for SK who scan and walk away.

#### What "safe for our data" actually means (the brief for management)

Same on every option above:

| Risk | Defence | Where it lives |
|---|---|---|
| External attacker | Inbound port = 0 (Tunnel/proxy). No public IP, no SSH port, no Redis/Postgres exposed. | Network layer |
| Eavesdropping in transit | TLS 1.3 end-to-end. Cloudflare provides the cert. | Wire |
| Eavesdropping at the edge | Cloudflare cannot decrypt the *body* — they terminate TLS at their POP and re-encrypt to your origin via the Tunnel's mutual-TLS connection. Body is opaque to them. (Documented at https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) | Cloudflare ↔ origin |
| Unauthorised user | Cloudflare Access email allow-list at the edge (FREE ≤50 users) + the app's own bcrypt login. Two locks, not one. | Identity layer |
| Data leak via screenshot | Audit log records every consequential action with username + timestamp. Reviewable in Admin Portal → Audit Logs. | App layer |
| Data leak via stolen Mac (option A) | macOS FileVault (AES-256). Without the disk password the SQLite file is unreadable. Backups (next row) are independent. | Disk layer |
| Disk failure / accidental delete | Nightly backups to **two** independent destinations (e.g. iCloud Drive + Backblaze B2). 14-day retention. Restore = `sqlite3 db ".restore <file>"`. | Backup layer |
| Compliance / data residency | Data lives on YOUR machine (Mac, Fly Frankfurt, Streamlit US — you pick) — not on a vendor-owned DB. Full deletion at will. | Architecture |

If management asks "where does the data go when we type into the app?" the chain is: **browser → TLS → Cloudflare POP → encrypted Tunnel → your origin's local SQLite file**. The data never sits on anyone else's database service.

#### Daily backup options (free, pick any two)

| Destination | Free quota | Setup time | Restore complexity |
|---|---|---|---|
| **Backblaze B2** | 10 GB free, 1 GB/day egress | 15 min (rclone or `b2 sync`) | One `b2 download-file-by-name` |
| **Cloudflare R2** | 10 GB free, no egress charges | 20 min (rclone with S3 endpoint) | One `aws s3 cp` (R2 is S3-compat) |
| **Private GitHub repo** | Unlimited <100 MB files | 5 min (git commit cron) | `git pull` and copy in |
| **Google Drive** via rclone | 15 GB on personal account | 10 min (rclone config; one-time OAuth) | `rclone copyto` |
| **iCloud Drive** (Mac only) | 5 GB free | 0 min (it's already mounted) | Drag and drop |

Recommended pair: **iCloud Drive (always there) + Backblaze B2 (off-Apple, off-Mac, off-Cloudflare)**. If any one party (you, Apple, Cloudflare) is compromised, the other two snapshots survive.

#### Setting up giinventory.com — pick A, B, or C and follow

##### Path A — point giinventory.com at your Mac (self-host)

You already finished the tunnel install. The DNS step inside Cloudflare's dashboard:

1. **Dash → giinventory.com → DNS → Records** (you're already here per your screenshot).
2. **Add record** → Type `CNAME` → Name `gi` (so URL is `gi.giinventory.com`) or `@` (apex `giinventory.com`) → Target: leave for the tunnel command.
3. Actually skip step 2 and let `cloudflared` do it for you:
   ```bash
   cloudflared tunnel route dns gi-hub gi.giinventory.com
   ```
   Refresh the Cloudflare DNS page — a CNAME for `gi` appears automatically, pointing at `<tunnel-uuid>.cfargotunnel.com`. **Proxied** (orange cloud) should be ON.
4. Visit `https://gi.giinventory.com` — app loads.

##### Path B — point giinventory.com at Fly.io (always-on, no Mac)

1. **Sign up at fly.io** (free, no credit card for the smallest tier).
2. **Install flyctl locally:** `curl -L https://fly.io/install.sh | sh`
3. In the repo root:
   ```bash
   fly launch --no-deploy           # asks: app name → gi-hub, region → fra (Frankfurt closest to KSA)
   fly volumes create gi_data --size 1 --region fra
   ```
4. Edit the generated `fly.toml`:
   ```toml
   [[mounts]]
     source = "gi_data"
     destination = "/data"
   [env]
     STREAMLIT_SERVER_PORT = "8080"
     # gi_database.db will live at /data/gi_database.db
   ```
5. Add a tiny `Dockerfile`:
   ```dockerfile
   FROM python:3.12-slim
   WORKDIR /app
   COPY requirements.txt .
   RUN pip install --no-cache-dir -r requirements.txt
   COPY . .
   ENV STREAMLIT_SERVER_HEADLESS=true
   CMD ["streamlit", "run", "main.py", "--server.address=0.0.0.0", "--server.port=8080"]
   ```
6. `fly deploy`. After ~3 min, Fly prints `https://gi-hub.fly.dev`.
7. **Point giinventory.com at Fly:** Cloudflare dash → DNS → Add record → Type `CNAME` → Name `gi` → Target `gi-hub.fly.dev` → **Proxied** ON.
8. In Fly: `fly certs create gi.giinventory.com` — Fly auto-issues a TLS cert via Cloudflare's CDN.

Cost: $0 until you exceed 3 small VMs or 160 GB egress/month — you won't.

##### Path C — point giinventory.com at Streamlit Cloud + Litestream

1. Add **Litestream** sidecar to the repo. Create `litestream.yml`:
   ```yaml
   dbs:
     - path: /mount/src/cncec-project/gi_database.db
       replicas:
         - type: s3
           bucket: gi-hub-db-backup
           path: gi_database.db
           endpoint: https://s3.eu-central-003.backblazeb2.com
           region: us-east-1
   ```
2. In Streamlit Cloud → Secrets, add B2 credentials:
   ```toml
   [s3]
   access_key_id     = "..."
   secret_access_key = "..."
   ```
3. Modify `main.py` to call `litestream restore -if-replica-exists` before `init_db()` on startup, then `litestream replicate &` to mirror writes.
4. Point Cloudflare DNS: dashboard → DNS → Add `CNAME` → `gi` → `your-app.streamlit.app` → Proxied ON. Streamlit ignores the host header so it works.

This path is the most fragile (Streamlit Cloud restart kills `litestream replicate` and you risk seconds of writes). Only pick C if you absolutely cannot run a VM.

#### Cloudflare Access — email-only login (FREE ≤50 users)

Works for any path above where giinventory.com goes through Cloudflare DNS.

1. Cloudflare dash → **Zero Trust** (left sidebar bottom) → on first visit it asks for a Team name → choose `giinventory` → Free plan (no card).
2. **Access → Applications → Add application** → Self-hosted.
3. Application name: `GI Hub Warehouse`. Application domain: `gi.giinventory.com`. Session duration: 24 hours.
4. **Add a policy** → Name: "Staff allow-list" → Action: Allow → Selector: **Emails** → paste comma-separated emails OR Selector: **Emails ending in @yourcompany.com**.
5. Save. Now anyone hitting `gi.giinventory.com` sees a Cloudflare-branded login page first. They enter their email → receive a one-time PIN → only then reach your app's login. Non-allow-listed emails see "You don't have permission" with no further info leaked.

#### The 8 things to do before sharing the URL with users

1. Change every default password (`admin/admin2026` etc.). They're public knowledge from the repo.
2. Enable FileVault on the host Mac (path A only): System Settings → Privacy & Security → FileVault → On.
3. Set up Cloudflare Access with the staff email list.
4. Add Twilio credentials to App Secrets (cloud paths) so WhatsApp doesn't depend on the Mac for path B/C/D.
5. Test the nightly backup script restores cleanly to a fresh DB before you start collecting real data.
6. Run `python bug_check.py` — make sure it shows 114/114.
7. Run `python build_manual_pdf.py` and email the resulting PDF to management for review.
8. Take a "day 0" backup before letting anyone log in: `cp gi_database.db gi_database_day0_$(date +%Y%m%d).db`.

#### Quick tunnel for testing TODAY without buying anything

While you decide between A/B/C, you can already share a URL with one teammate to validate the app:

```bash
# Terminal 1 — Streamlit
.venv/bin/streamlit run main.py --server.headless true

# Terminal 2 — Cloudflare quick tunnel
cloudflared tunnel --url http://localhost:8501
```

Terminal 2 prints `https://random-words.trycloudflare.com`. Share that URL. When you Ctrl-C, the URL dies. Perfect for "does it work end-to-end" before committing to a permanent setup.

---

### Production hosting — self-host on a spare Mac/laptop + Cloudflare Tunnel

**Turnkey installer:** `host_setup/` ships everything as runnable scripts. After installing `cloudflared` and creating the tunnel, you just run:

```bash
./host_setup/scripts/install.sh
```

That renders four `launchd` plists into `~/Library/LaunchAgents/`, loads them, and shows a coloured status table. See `host_setup/README.md` for the full step-by-step (45 minutes from zero to live, including Cloudflare Access setup for the `@generalindustries.net` email allow-list).

The narrative description below is kept for reference but the scripts are the authoritative path.

---

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
| `com.gi.streamlit exit 126` | launchd can't exec the venv binary. The wrapper at `host_setup/scripts/run_streamlit.sh` works around it. If you bypassed the wrapper, look for missing exec bit (`chmod +x .venv/bin/streamlit`) or Gatekeeper quarantine (`xattr -d com.apple.quarantine .venv/bin/streamlit`). |
| WhatsApp messages all `failed` even though worker is running | Embedded thread in main.py is racing the standalone process. Confirm `GI_SUPPRESS_EMBEDDED_WORKER=1` appears in the rendered `~/Library/LaunchAgents/com.gi.streamlit.plist`. Reinstall via `./host_setup/scripts/install.sh` if missing. |
| "Python wants to control Google Chrome" popup repeats | macOS Automation prompt. Means a `tell application <X>` slipped back into the AppleScript path. The current sender uses `System Events` only; check `whatsapp_worker._send_via_chrome_macos` hasn't been edited. |
| Timestamps still UTC on a specific page | `localize_timestamps_df(df, [...])` not yet applied there. Add the import + one-liner call right after the `pd.read_sql(...)`. The helper is idempotent and safe to apply on already-converted DataFrames (it returns the input on the second pass — strings don't re-parse as timestamps). |
| Ask Hub Assistant returns "That isn't covered in your section of the manual" | Either (a) the user IS asking about a section above their role — that's the security feature working, or (b) USER_MANUAL.md has drifted away from the role-section allow-list in `ai/manual_qa._ROLE_ALLOWED`. Re-check the section numbering matches `# N. ` headings. |
| Logistics user can't see HOD Portal in the sidebar | Working as intended. v3.0 added `📋 HOD Portal` to `_EXACT_ROLE_PAGES = {hod, admin}` so the procurement roles (numerically higher than HOD in the hierarchy) don't inherit access. |
| Warehouse_user opens a PO and sees prices | Bug. Check `get_assignment_detail()` is the helper being called (not `get_po_detail(hide_prices=False)` directly). All three layers must blank: (a) items, (b) defensive re-blank, (c) header dict pops. Run `python -c "from database import get_assignment_detail; ..."` to inspect. |
| Mixed-family RL+BL DN was accepted | Bug. `create_delivery_note()` must reject. Check: (a) `po_items.rl_bl_family` is populated for the offending rows — if not, `classify_rl_bl_family()` failed to detect (token mismatch); (b) the splitter's `if len(families - {None}) > 1` guard is intact at top of `create_delivery_note`. |
| DN approved by HOD but SK doesn't see "Incoming DNs" | `pending_receipts` mirror row didn't insert OR SK is on a different Site_ID. Inspect `SELECT * FROM pending_receipts WHERE DN_Number = ?` — if no row, `hod_decide_dn(approve=True)` failed silently (look at audit log for `DN_HOD_APPROVE`). If row exists but with wrong Site_ID, the DN's `Site_ID` was wrong at WH-prepare time. |
| Reminder fired twice in one day | Either `delivery_reminders_sent` UNIQUE was dropped/violated OR `app_settings.delivery_reminders_last_run` was manually wiped. Check both. The double-fire is recoverable but a sign of guard regression. |
| Notification bell shows N unread but inbox is empty | Race condition: bell count cached before a mark-all-read elsewhere in the same session. Click the bell once to refresh; it re-reads. If persistent, check `app_notifications.read_at` was actually written for the rows. |
| Force-closure doesn't appear in HOD's In-Transit "affecting me" tab | Closure was on a PR/PO with NULL Site_ID. Check: `SELECT * FROM po_force_closures WHERE id=?` — if `Site_ID` is NULL, the 3-way fallback join needs `pr_master.Site_ID` OR `purchase_orders.Site_ID` to be populated. Backfill Site_ID on the parent row if missing. |
| `whatsapp_worker` log shows "delivery_reminders crashed: no such table delivery_reminders_sent" | `init_db()` didn't run before worker started. On a fresh DB, ensure `main.py` (which calls `init_db()`) runs at least once before the standalone worker, OR have the worker call `init_db()` defensively at startup. |

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
  - `mtc_documents` — Surface Shields MTC; `status IN ('attached','missing','sent_to_logistics')`
  - `pending_returns` — SK return staging → HOD approval; `override_required` flag for >30-day returns
- Inventory: `Category` (default `'Others'`) + `Opening_Stock` (default 0)
- System settings: `system_settings.Site_ID` (NULL = global, non-NULL = site-specific)
- Receipts: `DN_No`, `Serial_No`, `PR`, `Location`, `Vehicle_No`, `Driver_Name`, `Pallet_No`, `Mob_From`, `Prepared_by`, `Mob_To`, `Received_by`, `DN_Copy` — closes the silent-drop bug
- `whatsapp_queue`: `error_message`, `attempts` columns for failure visibility + retry

**2026-06 round 4 — Phase 6A–F (Workstream A):**
- New tables:
  - `employees` — physical-labour master (`ID_Number` UNIQUE, `Name`, `Phone_Number`, `Department`, `status IN ('active','inactive','suspended')`, `created_by`, `created_at`, `updated_at`). NOT a login — separate from `users`.
  - `cv_model_versions` — YOLO model registry (`version` UNIQUE, `model_path`, `classes_json`, `mAP`, `trained_at`, `is_active`). Partial unique index `ix_cv_active` guarantees ≤1 active row.
  - `tool_catalogue` — class registry (`class_name` UNIQUE, `display_name`, `category`, `model_version_id` FK, `min_confidence` REAL default 0.75, `created_by`).
- `returnable_items` self-heal — `cv_detected` (INTEGER 0/1), `cv_confidence` (REAL), `cv_employee_id` (TEXT), `cv_tool_class` (TEXT). Manual entries leave them NULL so adoption telemetry stays honest.
- `delivery_reminders_sent` — `CHECK(ref_type IN ('po','dn'))` constraint **dropped** via self-heal table rebuild so Phase 6E can reuse the dedup table with `ref_type='returnable_loan'` + signed-hour `offset_days` ∈ {−2, 0, 2, 24}. UNIQUE(ref_type, ref_number, target_date, offset_days) preserved. Existing rows carried over verbatim.
- `app_settings` keys: `returnable_reminders_last_run_hour` (worker hourly-gate marker, format `YYYY-MM-DDTHH`).
- `users` (legacy, no migration): admin / logistics / warehouse_user rows now allowed to carry empty `Site_ID = ""` (global roles). The existing seeded `admin` row was migrated from `"CNCEC"` → `""`.
- Inventory data cleanup (one-off, via `scripts/clean_inventory_sites.py`): all `Site_ID = "HQ"` rows across `inventory` / `receipts` / `pending_receipts` / `users` flipped to `"CNCEC"` (357 rows); `system_settings` `("Site","HQ")` row deleted.

**2026-06 round 2:**
- New tables:
  - `wbs_master` — per-site allowed WBS numbers; UNIQUE(WBS_Number, Site_ID); `active`/`closed` status
- Self-heal: `wbs` column on `consumption`, `receipts`, `pending_issues`, `pending_receipts`
- Config rename: `RUBBER_CATEGORY` → `MTC_REQUIRED_CATEGORY = "Surface Shields"` (old name kept as alias)

All added via self-healing `ALTER TABLE` in `init_db()`. None require manual migration. The bug harness asserts every column listed here.

**Phase C round (2026-06 round 3) — Procurement chain:**

New tables (12) — column-by-column summary:

| Table | Key columns | Purpose |
|---|---|---|
| `warehouses` | `Warehouse_ID` UNIQUE, `Name`, `Location`, `Contact_*`, `status` | Physical receiving locations master |
| `vendors` | `Vendor_Code` UNIQUE, `Vendor_Name`, `Address`, `Contact_*`, `Default_Inco_Terms`, `Default_Payment_Terms`, `status` | Vendor master, auto-fills PO creation form |
| `purchase_orders` | `PO_Number` UNIQUE, `PR_Number`, `Site_ID`, `Vendor_Code`, `Vendor_Name`, `Inco_Terms`, `Payment_Terms`, `PO_Date`, `PO_Type`, `Quotation_No/Date`, `Your_Reference`, `Our_Reference`, `Contact_Person`, `Contact_Email`, `Mobile`, `Our_Email`, `Expected_Delivery`, `Freight_Charges`, `Handling_Charges`, `Discount_Amount`, `Total_Amount`, `Amount_In_Words`, `source` (`manual`/`pdf_upload`), `attachment_blob`/`_name`/`_mime`, `status` (`open`/`partially_delivered`/`delivered`/`closed`/`force_closed`/`cancelled`), `created_by`, `closed_by`, `close_reason` | PO header; one row per PO |
| `po_items` | `PO_Number`, `line_no`, `Material_Code`, `Description`, `Qty`, `UOM`, `Unit_Price`, `Total_Price`, `PR_Number`, `WBS_Number`, `Network`, `Plant`, **`rl_bl_family`** (`RL`/`BL`/NULL), `Delivered_Qty`, `Returned_Qty`, `line_status` (`open`/`partially_delivered`/`delivered`/`returned`/`closed`/`force_closed`), `close_reason` | PO line items; one row per line. `SAP_Code` intentionally absent here — Logistics works with Material_Code, SAP joins at SK receipt |
| `po_shipment_schedule` | `PO_Number`, `shipment_no`, `material_group`, `target_date`, `actual_date`, `status`, `notes` | Parsed from PO Annexure delivery schedule (PDF page 3 of the sample) |
| `po_assignments` | `PO_Number`, `Warehouse_ID`, `items_subset_json` (NULL = all items), `Expected_Delivery`, `assigned_by`/`_at`, `acknowledged_by`/`_at`, `status` (`assigned`/`acknowledged`/`received`/`partial`/`closed`/`cancelled`), `notes` | Logistics → Warehouse routing |
| `delivery_notes` | `DN_Number` UNIQUE, `PO_Number`, `Warehouse_ID`, `Site_ID`, **`rl_bl_family`**, `DN_Date`, `Vehicle_No`, `Driver_Name`, `Driver_Phone`, `Prepared_By`, `Remarks`, `status` (DN state machine — see Critical Contracts), `logistics_decided_by`/`_at`/`_decision`, `hod_decided_by`/`_at`, `sk_received_by`/`_at`, `rejection_reason`, `created_by` | DN header. One PO can produce many DNs |
| `dn_items` | `DN_Number`, `po_item_id`, `Material_Code`, `Description`, `Qty`, `UOM`, `Lot_Number`, `Expiry_Date`, `Remarks`, `rl_bl_family`, `sk_received_qty`, `status` (`pending`/`received`/`partial`/`returned`/`cancelled`) | DN line items |
| `po_returns` | `PO_Number`, `po_item_id`, `DN_Number`, `Material_Code`, `Qty`, `Reason`, `raised_by_role`, `raised_by`, `raised_at`, `Expected_Resupply`, `status` (`open`/`vendor_acknowledged`/`resupplied`/`cancelled`), `closed_by`/`_at`, `notes` | Vendor returns + site→warehouse returns (both flow through here) |
| `po_reschedule_requests` | `PO_Number`, `DN_Number`, `current_date`, `requested_date`, `reason`, `requested_by_role` (`warehouse_user`/`hod`/`admin`), `requested_by`/`_at`, `status` (`pending`/`approved`/`rejected`), `decided_by`/`_at`, `decision_notes` | Warehouse / Site HOD → Logistics reschedule asks |
| `po_force_closures` | `target_type` (`pr`/`po`/`po_item`), `target_ref`, `Site_ID`, `PR_Number`, `PO_Number`, `reason`, `closed_by`/`_at`, `notes` | Audit log of every force-closure with reason |
| `app_notifications` | `recipient_user` OR (`recipient_role` + optional `recipient_site`/`recipient_warehouse`), `event_key`, `severity` (`info`/`warning`/`critical`/`success`), `title`, `body`, `link_page`, `link_anchor`, `related_table`, `related_ref`, `read_at`, `created_at` | In-app bell inbox. Always fires alongside any WhatsApp event |
| `delivery_reminders_sent` | UNIQUE(`ref_type` (`po`/`dn`), `ref_number`, `target_date`, `offset_days`), `fired_at` | Idempotency log for the T-2/T-1/T-0 sweep |

Extended existing tables (column additions):

| Table | New columns | Purpose |
|---|---|---|
| `pr_master` | `WBS_Number`, `Network`, `Plant`, `Delivery_Date`, `submitted_to_logistics_at`, `submitted_to_logistics_by`, `logistics_status` (`site_draft`/`submitted`/`in_po`/`closed`/`force_closed`) | Procurement chain handoff state on PR rows |
| `receipts` | `DN_Number`, `Warehouse_ID`, `PO_Number_Source` | Traceback so a `receipts` row can be mapped to its originating DN/PO/warehouse |
| `pending_receipts` | `DN_Number`, `Warehouse_ID`, `PO_Number_Source` (mirrored from receipts schema) | DN-driven mirror rows arrive here with `status='pending_sk'` |
| `users` | `Warehouse_ID` (nullable) | Scopes a `warehouse_user` to a warehouse |
| `pending_users` | `Warehouse_ID` | Same, for self-registration queue |
| `users` (CHECK) | Role CHECK rebuilt to include `'logistics'` + `'warehouse_user'` via the worker→store_keeper migration pattern | New roles accepted by INSERT |

New roles (2): `logistics` (icon 🚚, hierarchy=3), `warehouse_user` (icon 🏭, hierarchy=1). `ROLE_HIERARCHY` revised to `{store_keeper:0, warehouse_user:1, supervisor:1, hod:2, logistics:3, admin:4}`. New `PAGE_ACCESS` entries: `🚚 Logistics Portal → logistics`, `🏭 Warehouse Portal → warehouse_user`. Both exact-locked in `_EXACT_ROLE_PAGES`.

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
9. **launchd direct exec of `caffeinate <abs-path>` is unreliable on Apple Silicon / newer macOS** — silent `exit 126`. Use the `host_setup/scripts/run_streamlit.sh` wrapper. Don't "simplify" the plist back to direct exec.
10. **`tell application <browser>` in AppleScript triggers Automation permission, NOT Accessibility**. They're separate macOS prompts. The Chrome WhatsApp sender uses only `System Events` keystrokes to stay under Accessibility (one-time grant per binary). If you ever add `tell application` you'll be back to per-message popups.
11. **`Ask Hub Assistant` role-filters at the RAG layer, not just system prompt.** A Store Keeper's request never sees the Admin chapter in the prompt context — that's why the model can't leak privileged info even under prompt-injection attack. Preserve this when extending. The role allow-list lives in `ai/manual_qa._ROLE_ALLOWED`.
12. **SQLite `CURRENT_TIMESTAMP` is UTC by spec.** New rows we write through Python use Riyadh time (because `TZ=Asia/Riyadh` in launchd plists). Mixed timestamps in the DB are normal; display layer normalises via `config.utc_to_local()`. Don't try to "fix" the DB to all-local — portability across timezones depends on UTC at rest.
13. **`streamlit-keyup` is a Streamlit component**, not a pip dep we can install on Streamlit Cloud's container if there's no network. It's in `requirements.txt` and works on Streamlit Cloud. The dashboard gracefully degrades to plain `st.text_input` if the import fails.
14. **`pending_receipts.status='pending_sk'` is a NEW status value (v3.0).** HOD's existing Pending Receipts tab filters by `status='pending_hod'`, so the DN-driven mirror rows (which use `'pending_sk'`) don't bleed into that tab. There is NO CHECK constraint on `pending_receipts.status`, so the new value inserts cleanly. Do NOT add one without also updating `hod_decide_dn()`.
15. **DN over-ship guard counts LIVE DNs only.** `create_delivery_note()` excludes DNs with status IN (`'rejected'`, `'cancelled'`) from the "already shipped" calculation. A rejected DN frees its qty back to be used by the next DN. Don't change this — it's how reschedules + rejections recover stock allocation cleanly.
16. **Force-closure site visibility uses a 3-way join fallback.** `list_force_closures_for_site()` matches on (a) `po_force_closures.Site_ID` direct OR (b) via `purchase_orders.Site_ID` OR (c) via `pr_master.Site_ID`. Closures landed before Site_ID resolution still surface on the correct site. Same pattern in `report_force_closures()`.
17. **`users` role CHECK constraint rebuild reuses the worker→store_keeper pattern.** Adding the procurement roles required a CHECK rebuild, which means `ALTER TABLE users RENAME TO _users_old2; CREATE TABLE users (...); INSERT FROM _users_old2;`. The rebuild block in `init_db()` detects via `"'logistics'" not in sql` so it's idempotent. Don't add a new role without extending this CHECK and the rebuild check, OR you'll get `IntegrityError: CHECK constraint failed`.
18. **`delivery_reminders_sent` has both a UNIQUE constraint AND a separate day-marker.** UNIQUE blocks per-(ref, date, offset) double-fires inside one sweep. The `app_settings.delivery_reminders_last_run` day-marker skips the SQL queries entirely on the second-and-later worker ticks of the same day. Removing either guard means more work (UNIQUE alone) OR risk of double-fires across worker restarts (day-marker alone).
19. **Sidebar Notifications bell tolerates DB errors silently.** `_render_notifications_bell()` wraps `count_unread_notifications` in a try/except that defaults to 0. A notification helper failure must NEVER stop the rest of the sidebar (or the page) from rendering. If you add bell features, preserve this contract.
20. **`app_notifications` visibility OR-group MUST be parenthesised.** The query is `SELECT * FROM app_notifications WHERE (recipient_user = ? OR (recipient_role = ? AND ...)) [AND read_at IS NULL]`. Without the outer parens, the optional `AND read_at IS NULL` binds only to the second OR branch, leaking read user-targeted notifications back as "unread". This was a bug found in Phase 1 testing — see `get_app_notifications()`.
21. **Warehouse view price hiding has THREE layers, not one.** (a) `get_po_detail(hide_prices=True)` blanks the item columns. (b) `get_assignment_detail()` re-blanks them defensively. (c) `get_assignment_detail()` also pops monetary keys from the header dict. Never trust just one. The bug_check test `check_warehouse_view_strict_price_hiding` asserts all three.
22. **RL/BL classifier is substring-based, NOT exact-match.** `config.classify_rl_bl_family()` matches `RUBBER LINING` / `BRICK LINING` / `BRICK MATERIAL` / `RL-` / `BL-` against the concatenated `Material_Code + Description` (case-insensitive). Adding new family tokens? Add them to `RL_BL_FAMILY_TOKENS` in `config.py`. RL takes precedence by dict insertion order if both tokens are present — never combo.

---

---

## Phase 6 — Enterprise Deployment & Computer Vision (forward roadmap)

> **Status as of this handoff:** **Workstream A (Phase 6A–F) SHIPPED 2026-06.** Phases 1–5 (procurement chain) and Phase 6A–F (CV + Smart Scan + reminders) are stable at **268/268 bug_check + 16/16 UI crawler**. Workstream B (Phase 6G–K — Docker, dynamic WhatsApp provider, Ollama containerization, NAS backups, deployment playbook) remains PLANNED.
>
> **What shipped in Workstream A (one-line each):**
> - **6A** — `employees`, `tool_catalogue`, `cv_model_versions` tables + CRUD helpers + `returnable_items.cv_*` self-heal columns.
> - **6B** — `ai/cv/qr.py` (encode + decode with macOS Homebrew libzbar patch) + 👷 Employees admin tab (Add/Edit · CSV import · Roster + per-badge PNG).
> - **6C** — `ai/cv/train.py` CLI (auto-versioning, mAP harvest, DB registration) + `ai/cv/inference.py` (lazy YOLO, per-class min_confidence, cache invalidation) + 🛠️ Tool Catalogue admin tab. Companion: `docs/cv_training_guide.md`.
> - **6D** — Smart Scan in SK Returnable Items tab (badge → tool → write-through to manual form, session-state hash dedup, auto/candidates/manual buckets) + return-by-scan grid filter.
> - **6E** — `sweep_returnable_reminders` (T−2h / T−0 / T+2h / T+24h with Borrower → SK → Supervisor escalation; signed-hour `offset_days`; hourly worker gate via `app_settings.returnable_reminders_last_run_hour`).
> - **6F** — `reports.generate_employee_qr_badges_pdf` (multi-page A4 grid with HR header band) + Admin Portal bulk download + documentation rollup (this section, `USER_MANUAL.md` §4.5.0, `SOP.md` §7.4 SK card).
>
> **Read first if you are picking this up cold:** This entire chapter is self-contained. You do not need any prior chat context — every decision is recorded here, every file path is named, every env var is documented.
>
> **Start here for Workstream B (Phase 6G+):** Read §Phase 6.0 (preflight) — Twilio creds, NAS path, Docker layer cache. Then proceed serially through Phase 6G→K.

### Phase 6 — Why this exists

Two milestones requested in v3.1+:
1. **Enterprise Docker deployment** so the system can move from local Mac + Cloudflare to a corporate intranet server (specs unknown — built for the lowest-common-denominator Linux CPU VM).
2. **Computer vision for Returnable Items** — QR-scan an employee badge + object-detect the borrowed tool so the Store Keeper can check out tools without typing.

Two workstreams, decoupled by design. Workstream A (CV) pilots on the current Mac. Workstream B (Docker) is the deployment chassis. They can be tackled in parallel or in series, but the recommended order is **A first, then B**, because A introduces new heavy deps (`ultralytics`, `opencv-python-headless`, `pyzbar`) that B's `requirements-server.txt` will need to know about.

### Critical operating constraint — DO NOT BREAK THE MAC

Until management approves the company server, the user demonstrates Phases 1–5 to leadership from the current **Mac + Cloudflare Tunnel** setup. **Therefore:**

- **`pywhatkit` MUST remain installed and functional** on the local Mac throughout Phase 6.
- **The macOS Chrome + AppleScript WhatsApp path MUST continue to work** when the user runs `streamlit run main.py` locally.
- **No code path may hard-delete or hard-block `pywhatkit`** — Phase 6.H uses a runtime-evaluated env var (`WHATSAPP_PROVIDER`) to switch behavior, NOT a delete.
- The Docker build excludes `pywhatkit` from its requirements layer because it has heavy GUI deps that fail on a slim Linux image — but that exclusion is per-image, NOT per-codebase.

If you find yourself about to write code that removes `pywhatkit` or breaks the AppleScript path, **stop**. Reread this section. The toggle is the only acceptable design.

---

## Phase 6 decisions register (set in v3.0 brainstorm)

These are FROZEN — proceed without re-litigating:

| ID | Decision | Rationale |
|---|---|---|
| A1 | Linux Docker target (Windows out of scope) | Standard Docker base, easy CI |
| A2 | Intranet only — no Cloudflare on the server | Corporate security |
| A3 | Keep bcrypt + app-level RBAC | SSO/AD = separate future phase |
| A4 | Keep SQLite | WAL handles 10–25 concurrent fine |
| A5 | CPU-only assumed; GPU is bonus | Lowest common denominator |
| A6 | Corporate NAS for backups | IT-controlled, no cloud dep |
| A7 | Twilio for server WhatsApp; pywhatkit stays for Mac via env-var toggle | Mac demo must keep working |
| B1 | Mixed CV: small fixed YOLO catalogue + manual fallback for everything else | Realistic accuracy expectations |
| B2 | Custom YOLOv8 trained locally | Internal AI, no cloud calls |
| B3 | Server-side Python inference | Centralised model updates |
| B4 | `st.camera_input` (snapshot, not WebRTC) | Corporate Wi-Fi friendly |
| B5 | New `employees` master (NOT `users`) | Separates physical workers from app users |
| B6 | QR contains only the `ID_Number` | Privacy — no PII in scannable code |
| B7 | NO images stored on disk; process in-memory, log only metadata | GDPR / IT compliance |
| B8 | Hourly returnable-reminder sweep: T-2h, T-0, T+2h, T+24h | Tool-loan cadence ≠ delivery cadence |
| B9 | Confidence ≥ 0.75 auto-fills; < 0.75 shows top-3; manual fallback always | Robustness |
| X1 | CV and Docker decoupled; CV pilots on Mac first | De-risk one at a time |
| X2 | Pilot CV at one storeroom, then expand | Real-world model accuracy before scale |

---

## Phase 6.0 — Preflight (do BEFORE any code)

Must be resolved by the user, not the engineer, before Workstream A or B begin:

| Item | Owner | Output |
|---|---|---|
| Acquire Twilio production credentials (Account SID + Auth Token + paid WhatsApp Business number) | User + Twilio account admin | Three secrets to drop into `/Users/.../.streamlit/secrets.toml` for local Docker testing, then into Docker secrets / env vars for the server |
| HR employee export format | User + HR | CSV with these column names AT MINIMUM: `ID_Number`, `Name`, `Phone_Number`, `Department`. Sample file with 5+ real rows |
| Decide on initial tool catalogue (20 items) | User + Pilot SK | Tab-separated list of tool class names + display names + categories |
| Confirm badge QR convention | User + IT/HR | Either (a) existing badges encode the `ID_Number` directly — share an example PNG/decoded value, OR (b) we will print new badges using our QR generator |
| Pilot storeroom + SK volunteer | User | Site_ID + SK username for the pilot |

If any of these are blocked, **delay Workstream A code**. The pilot site decision in particular drives where training data is captured.

---

# WORKSTREAM A — Computer Vision Pilot (Mac first, ~2 weeks effort)

Build, train, deploy locally on the user's current Mac+Cloudflare setup. Pilot at one storeroom with ~20 tools. Tune on real data. Roll out to other sites once accuracy is proven.

## Phase 6A — CV data model + employees master (~2 days)

### Goal
DB foundation so the rest of Workstream A has something to read/write. No UI yet.

### New tables (added via self-healing `init_db()` per existing pattern)

| Table | Key columns | Purpose |
|---|---|---|
| `employees` | `ID_Number` UNIQUE, `Name`, `Phone_Number`, `Department`, `status` (`active`/`inactive`/`suspended`), `created_by`, `created_at`, `updated_at` | Physical-labour employee master. NOT a system login (no `password_hash`). |
| `tool_catalogue` | `class_name` UNIQUE (YOLO class id — e.g. `torque_wrench_12`), `display_name`, `category`, `model_version_id` (FK to `cv_model_versions.id`), `min_confidence` (per-tool override of the 0.75 default), `created_by`, `created_at` | Catalogue of tools the YOLO model can recognise. |
| `cv_model_versions` | `version` (e.g. `v1`, `v2`), `model_path`, `classes_json` (list of class names in this model), `mAP` (mean Average Precision from training), `trained_at`, `is_active` (only one row at a time) | Versioning so an admin can swap to a new trained model without restarting. |

### Self-heal extensions on `returnable_items`

| New column | Type | Purpose |
|---|---|---|
| `cv_detected` | INTEGER (0/1) | Was this loan started via the Smart Scan flow? |
| `cv_confidence` | REAL | YOLO confidence at issue time |
| `cv_employee_id` | TEXT | Employee ID Number from the QR scan |
| `cv_tool_class` | TEXT | YOLO class name that was detected |

Audit trail of which loans were CV-assisted vs manual.

### New helpers in `database.py`
- `add_employee()`, `update_employee()`, `list_employees()`, `get_employee_by_id_number()`, `import_employees_csv()`
- `add_tool_class()`, `list_tool_catalogue()`, `set_tool_class_min_confidence()`
- `register_cv_model_version()`, `promote_cv_model_version()`, `get_active_cv_model()`

### `bug_check.py` additions (~8 new checks)
- Schema verification: all 3 new tables + 4 extended columns
- `add_employee` + duplicate `ID_Number` rejection
- `import_employees_csv` round-trip (5 rows, then idempotent re-import with one UPDATE)
- `register_cv_model_version` + `promote_cv_model_version` (only one active at a time)

### Files touched
- `database.py` (add `CREATE TABLE IF NOT EXISTS` blocks + helpers)
- `bug_check.py` (new checks)
- `requirements.txt` (no new deps yet — schema only)

---

## Phase 6B — Employee QR scanning (~1 day)

### Goal
QR encode + decode helpers + Admin Portal CRUD UI for employees. Still no detection — just QR.

### New module `ai/cv/qr.py`
```
encode_id_to_png(id_number: str) -> bytes
decode_png_to_id(image_bytes: bytes) -> str | None
```
- Encode via `qrcode[pil]`. Content = just the `ID_Number` literal string (per B6).
- Decode via `pyzbar`. Returns `None` if no QR or unreadable.
- Both functions tolerate noise / rotation up to 30° and accept the common phone-camera image sizes.

### New Admin Portal tab: 👷 Employees (12th admin tab, appended)
- Search box + paginated grid (similar pattern to existing 👥 Users tab)
- Add Employee form (ID_Number, Name, Phone, Department)
- Edit / Suspend / Reactivate per-row
- 📁 CSV Import expander (drop a CSV that matches HR's export columns → preview → confirm)
- 📥 Download QR PNG button per row (calls `encode_id_to_png` + `st.download_button`)

### `bug_check.py` additions (~3 checks)
- Encode→decode round-trip with a deterministic `ID_Number`
- CSV import with 5 rows + idempotent re-import
- QR PNG bytes are a valid PNG (header check, no crash on Pillow.open)

### Dependencies added to `requirements.txt`
```
qrcode[pil]>=7.4
pyzbar>=0.1.9
Pillow>=10.0
```

### System dependency (will land in Docker, NOT installed on the Mac since macOS already has libzbar via brew if user has it; user can `brew install zbar` if missing)
- Linux: `libzbar0` — added to Dockerfile in Workstream B

### Files touched
- `ai/__init__.py` (extend if needed)
- `ai/cv/__init__.py` (new dir)
- `ai/cv/qr.py` (new)
- `pages_internal/admin_portal.py` (new tab + renderer)
- `database.py` (the CRUD helpers from 6A wire up here)
- `bug_check.py` (new checks)

---

## Phase 6C — YOLO training pipeline (~3–4 days)

### Goal
A reproducible pipeline that takes labelled images and produces a versioned model in the DB. Admin can promote a model version to active.

### Dataset layout convention
```
data/cv_training/
  ├── torque_wrench_12/
  │     ├── img_001.jpg
  │     ├── img_001.txt   (YOLO label: class_id x y w h)
  │     ├── img_002.jpg
  │     └── ...
  ├── multimeter_fluke/
  └── ...
data.yaml   (auto-generated by the training CLI)
```

Documented in a new file `docs/cv_training_guide.md`:
- How to capture ~50 images per class under realistic storeroom lighting (overhead fluorescent, partial shadow, varied angles)
- How to label with LabelImg (or Roboflow if user prefers a hosted tool) — the CLI accepts both YOLO and COCO label formats
- How to run the training CLI

### New module `ai/cv/train.py` (CLI)
```
python ai/cv/train.py --dataset data/cv_training --epochs 50 --device cpu --out models/cv_returnable/v1/
```
- Internally uses `ultralytics` `YOLO('yolov8n.pt')` as the base model
- Trains on the dataset, validates on a 20% holdout
- Writes `models/cv_returnable/v{n}/best.pt` + `models/cv_returnable/v{n}/training_log.json`
- Registers a row in `cv_model_versions` via `register_cv_model_version()` (NOT active yet)
- Reports mAP@0.5 to stdout and saves it on the version row

### New module `ai/cv/inference.py`
```
load_active_model() -> ultralytics.YOLO     (cached via @lru_cache; invalidated by promote)
detect_tool(image_bytes: bytes) -> list[(class_name, confidence, bbox)]
```
- Lazy-loads the active model from `cv_model_versions WHERE is_active=1`
- Returns top-K detections sorted by confidence
- Honors per-tool `min_confidence` override
- Gracefully returns `[]` if no active model (e.g. before first training)

### New Admin Portal tab: 🛠️ Tool Catalogue (13th admin tab, appended)
- Add Tool Class form (class_name, display_name, category, min_confidence)
- List of model versions with mAP, trained_at, "is active" pill, **✅ Promote** button per row
- Promoting clears active on all other versions, sets the chosen one active, invalidates the inference cache

### Confidence threshold doc
- Default `min_confidence = 0.75` (per B9)
- Per-tool override available in `tool_catalogue.min_confidence`
- Documented: "Raise to 0.85+ for safety-critical tools (e.g. respirators). Lower to 0.65 for high-volume low-value items."

### `bug_check.py` additions (~5 checks — model is MOCKED to avoid GPU/dataset deps in tests)
- `register_cv_model_version` + `promote_cv_model_version` exclusivity
- `get_active_cv_model` returns expected row when promoted
- `detect_tool` returns `[]` cleanly when no active model
- `detect_tool` honors `min_confidence` filter (with mock inference)
- `min_confidence` per-tool override beats the default

### Dependencies added to `requirements.txt`
```
ultralytics>=8.1
opencv-python-headless>=4.9
```

### Files touched
- `ai/cv/train.py` (new — CLI)
- `ai/cv/inference.py` (new)
- `ai/cv/__init__.py` (exports)
- `pages_internal/admin_portal.py` (new Tool Catalogue tab)
- `database.py` (cv_model helpers)
- `docs/cv_training_guide.md` (new)
- `requirements.txt`
- `bug_check.py`

### Storage
- `models/cv_returnable/v{n}/best.pt` — `models/` dir is gitignored; backed up by Workstream B's backup service
- `data/cv_training/` — also gitignored; admin uploads images via shell/scp, not through the app

---

## Phase 6D — Camera UI integration (~3 days)

### Goal
The SK Returnable Items tab gets a new **📷 Smart Scan** expander at the top. Manual entry form below is unchanged.

### Flow design
The expander is collapsed by default. Opening it reveals a two-step camera workflow:

**Step 1: Scan Employee ID**
- `st.camera_input("Scan employee QR badge", key="_sk_cv_emp_cam")` returns a snapshot
- Pass to `ai/cv/qr.decode_png_to_id` → get `ID_Number`
- Look up via `get_employee_by_id_number(id_number)` → display Name + Phone + Department in a green card
- If no match: red card "Employee not found. Ask Admin to add them, or use manual entry below."

**Step 2: Scan Tool**
- Only shown after Step 1 succeeded
- `st.camera_input("Scan tool", key="_sk_cv_tool_cam")` → bytes
- Pass to `ai/cv/inference.detect_tool` → list of detections
- Render based on confidence tier:
  - **≥ 0.75** (high) — auto-fills the existing material picker below the expander, shows green "✓ Recognised: <display_name> (conf 0.82)" caption. SK confirms qty + expected return + clicks the existing Submit button.
  - **0.30 to 0.75** (medium) — shows top-3 candidates with confidence bars; SK picks one. Auto-fills the picker.
  - **< 0.30 or no detection** — falls through to existing manual material selectbox. Caption: "Couldn't auto-identify. Pick the tool manually below."

### Return flow (same expander, different sub-tab)
- Scan QR → look up employee → fetch their open loans from `returnable_items WHERE status='borrowed' AND borrower_match_via_employees=id_number`
- Scan tool → match against open loans for this employee (the YOLO class is in `cv_tool_class` on the loan row)
- If match → ✅ Mark Returned button
- If no match (e.g. wrong tool) → show all open loans for this employee, SK picks

### Image lifecycle (per B7)
- The image bytes from `st.camera_input` live in Streamlit's per-session memory
- After `detect_tool` returns, the bytes go out of scope
- Nothing written to disk. No image columns added to `returnable_items`. Only the detection METADATA (`cv_confidence`, `cv_tool_class`, `cv_employee_id`) persists.

### Preservation Rule
- The existing **Returnable Items tab structure** is unchanged
- The existing **manual material selectbox + borrower name + qty + expected return** form below the expander is unchanged
- The new expander is purely additive
- The existing **Mark as Returned** dropdown below remains as the manual fallback

### `bug_check.py` additions (~4 checks — mocking the inference + QR layers)
- End-to-end issue flow: mock QR → mock detection at 0.85 → assert `returnable_items` row written with `cv_detected=1`
- Low-confidence flow: mock detection at 0.55 → assert top-3 picker shown, manual confirm wires correctly
- No-detection flow: mock empty detection → assert fallback to manual picker
- Return flow: mock QR + matching tool → assert `status='returned'`

### Files touched
- `pages_internal/daily_issue_log.py` (new expander INSIDE the existing Returnable Items tab; existing code below unchanged)
- `ai/cv/inference.py` (already in 6C)
- `ai/cv/qr.py` (already in 6B)
- `database.py` (helper: `find_open_loans_for_employee(id_number)`)
- `bug_check.py`

---

## Phase 6E — Hourly returnable reminder sweep (~1 day)

### Goal
Automatic WhatsApp reminders to the borrower as the expected return time approaches and passes.

### New helper `database.sweep_returnable_reminders(now: datetime | None = None)`

Iterates open `returnable_items` rows and fires events at four offsets relative to `expected_return_time`:

| Offset | Severity | Recipient |
|---|---|---|
| T−2h (2 hours before) | `info` | Borrower (via `employees.Phone_Number`) |
| T−0 (at the expected return time) | `warning` | Borrower |
| T+2h (overdue by 2 hours) | `warning` | Borrower + Site SK |
| T+24h (escalation) | `critical` | Borrower + Site SK + Site HOD |

### Dedup
Reuses the existing `delivery_reminders_sent` table with a new `ref_type='returnable_loan'`. UNIQUE(`ref_type`, `ref_number`, `target_date`, `offset_days`) blocks per-(loan, offset) double-fires.

Note: `offset_days` is overloaded here — for returnable loans we use HOURS not days, encoded as negative integers for clarity (e.g. `offset_days=-2` means T−2h). Documented in the helper docstring.

### Wired into `whatsapp_worker.run_worker_loop()`
- Runs every hour (NOT every day like the delivery sweep)
- New helper `_maybe_run_returnable_reminders()` mirrors `_maybe_run_delivery_reminders()` but with an hour-bucket marker in `app_settings.returnable_reminders_last_run_hour` to skip 60-sec poll repeats inside the same hour
- Independent of the daily delivery sweep — failures in one don't block the other

### WhatsApp recipient resolution
- Borrower phone comes from `employees.Phone_Number` (looked up via `cv_employee_id` on the loan row)
- If the loan was created MANUALLY (no CV scan), fall back to the existing `borrower_phone` column on `returnable_items`
- If neither resolves: skip the WhatsApp ping, still queue the in-app notification

### `bug_check.py` additions (~4 checks)
- Sweep fires expected count at each offset
- Idempotent: re-running same sweep with same `now` fires zero new events
- Borrower phone resolution: CV-loan path uses `employees`; manual-loan path uses `returnable_items.borrower_phone`
- T+24h escalation pings SK + HOD (not just borrower)

### `config.py` additions
```python
WHATSAPP_TRIGGERS = {
    ...existing keys...
    # Phase 6E — returnable loan reminders
    "returnable_reminder_t_minus_2h": True,
    "returnable_reminder_t_zero":     True,
    "returnable_reminder_t_plus_2h":  True,
    "returnable_reminder_t_plus_24h": True,
}
```

### Files touched
- `database.py` (sweep helper)
- `whatsapp_worker.py` (hourly-bucket marker + call site)
- `config.py` (4 new trigger keys)
- `bug_check.py`

---

## Phase 6F — Tests + pilot (~1–2 days)

### Goal
End-to-end validation + first real-world model train at the pilot storeroom.

### Acceptance criteria for v3.1 release
- 315/315 pytest still green
- 270+/270+ bug_check (Phase 6A–E adds ~24 checks)
- ≥ 85% of high-confidence (≥0.75) detections are correct on real footage at the pilot site
- Zero crashes on `requirements.txt` install for both Mac and Linux Docker (with `requirements-server.txt`)
- Manual fallback works when CV is disabled via env var `GI_CV_ENABLED=0` — the Returnable Items tab still functions exactly as v3.0

### Pilot procedure
1. SK volunteer captures ~50 images per chosen tool under storeroom conditions (varied lighting, angles, partial occlusion)
2. Engineer labels via LabelImg (~1 hour per class)
3. `python ai/cv/train.py --dataset data/cv_training/ --epochs 50` (3–4 hours CPU on M-series Mac)
4. Admin Portal → 🛠️ Tool Catalogue → promote v1 to active
5. SK runs the Smart Scan flow for 1 week, with manual fallback always available
6. Review at end of week: confusion matrix + per-tool accuracy → adjust `min_confidence` per tool

### Documentation updates
- `USER_MANUAL.md` §4.5 (Returnable Items): append §4.5.0 "📷 Smart Scan workflow" subsection (additive — existing §4.5.1+ unchanged)
- `handoff.md` §6 Schema Additions: append Phase 6A schema block
- `SOP.md`: SK quick reference card adds a "When CV detection is wrong" bullet

---

# WORKSTREAM B — Enterprise Docker Deployment (~1.5 weeks effort)

Independent of Workstream A. Can be tackled in parallel or after. Strictly Linux Docker per A1. **Does NOT break the Mac dev path.**

## Phase 6G — Docker foundation (~2 days)

### Goal
Single `docker compose up -d` brings the entire stack live on a fresh Linux box.

### New files
- `Dockerfile` (multi-stage):
  - Stage 1 `builder`: `python:3.12-slim` + build deps + `pip install -r requirements-server.txt --target /install`
  - Stage 2 `runtime`: `python:3.12-slim` + runtime deps (`libzbar0`, `libgomp1`, `tini`) + copy `/install` from builder + copy app code. Final image ~1.5GB. Drops to non-root user `gihub`.
- `docker-compose.yml`: three services on a shared internal bridge network `gi-net`:
  - `app` — the Streamlit app, port 8501 exposed to host (intranet routes here)
  - `ollama` — official `ollama/ollama:latest` image, **NO port mapping** (internal-only per A2 + "AI stays safe")
  - `backup` — alpine + busybox + sqlite3 + rsync, no port, runs cron
- `requirements-server.txt` — derived from `requirements.txt` MINUS `pywhatkit` and any macOS-specific deps (e.g. `pyobjc`). Mac developers still use `requirements.txt`.
- `.dockerignore` — excludes `.venv`, `.git`, `gi_database.db` (volume), `reports_archive/`, `data/cv_training/`, `models/cv_returnable/` (mounted), `BUG_REPORT.md`, `*.pdf`
- `docker-compose.override.yml.example` — template for admin to add their NAS path, custom env, etc.
- `docs/DEPLOY.md` — 6.K landing page

### Volumes
```yaml
volumes:
  gi-data:     # SQLite DB + uploads + entry attachments
  gi-models:   # CV model artifacts (Workstream A output)
  gi-ollama:   # Ollama model cache (qwen2.5-coder, llama3.1, etc.)
  gi-backups:  # backup destination (admin's override binds this to NAS)
```

### Volume binds vs named volumes
Use **named volumes** for `gi-data`, `gi-models`, `gi-ollama` (Docker manages location). Use **bind mount** for `gi-backups` so admin can point it at a NAS share via `docker-compose.override.yml`.

### `bug_check.py` not affected by 6G (Dockerfile is infra, not code).

### Smoke test gate
`docker compose build && docker compose up -d && docker compose ps` → all three services `running`. App reachable at `http://localhost:8501`.

---

## Phase 6H — Dynamic WhatsApp provider toggle (~1 day) **[REVISED per user constraint]**

### Goal
Runtime-evaluated env var `WHATSAPP_PROVIDER` selects the sender backend. **`pywhatkit` is NOT removed from the codebase.** The current Mac+Cloudflare demo path keeps working exactly as today. Docker sets the env var to `twilio` so the server uses the API path.

### Decision matrix
| Env value | Sender backend used | Where typically set |
|---|---|---|
| unset (default) | `pywhatkit` (current Mac AppleScript+Chrome flow) | local Mac (`streamlit run main.py` with no env var) |
| `pywhatkit` (explicit) | `pywhatkit` | local Mac, optional explicit form |
| `twilio` | Twilio API | Docker `docker-compose.yml` env block |
| `auto` (reserved) | Try Twilio if creds present, else `pywhatkit` | not used in Phase 6 — kept for future flexibility |

### Code change in `whatsapp_worker.py`
The worker already lazy-imports `pywhatkit` (see existing Hidden Surprise #4). Phase 6H formalises the switch:

```python
# whatsapp_worker.py — provider toggle at module top, evaluated once
import os

WHATSAPP_PROVIDER = os.environ.get("WHATSAPP_PROVIDER", "pywhatkit").lower()
if WHATSAPP_PROVIDER not in ("pywhatkit", "twilio", "auto"):
    print(f"⚠️  Unknown WHATSAPP_PROVIDER={WHATSAPP_PROVIDER!r}; defaulting to 'pywhatkit'")
    WHATSAPP_PROVIDER = "pywhatkit"
```

`_send_whatsapp()` becomes a router:
```python
def _send_whatsapp(phone, message):
    if WHATSAPP_PROVIDER == "twilio":
        return _send_via_twilio(phone, message)
    if WHATSAPP_PROVIDER == "pywhatkit":
        return _send_via_pywhatkit_macos(phone, message)  # current AppleScript path
    if WHATSAPP_PROVIDER == "auto":
        sid, _, _ = _twilio_config()
        if sid:
            return _send_via_twilio(phone, message)
        return _send_via_pywhatkit_macos(phone, message)
    return False  # unreachable thanks to the module-top normalisation
```

### Critical: imports stay guarded
The `pywhatkit` import remains lazy and inside `_send_via_pywhatkit_macos`. If the worker is running in `WHATSAPP_PROVIDER=twilio` mode, `pywhatkit` is never imported even if it's installed. This is what lets the Docker image safely OMIT `pywhatkit` from its requirements while the Mac dev install keeps it.

### docker-compose.yml env block (in 6G)
```yaml
services:
  app:
    environment:
      - WHATSAPP_PROVIDER=twilio
      - GI_DEPLOYMENT_MODE=server
      - OLLAMA_HOST=http://ollama:11434
    secrets:
      - twilio_account_sid
      - twilio_auth_token
      - twilio_from_number
```

### Mac dev — no change required
The user keeps running `streamlit run main.py` exactly as today. `WHATSAPP_PROVIDER` is unset → defaults to `pywhatkit` → current Mac AppleScript flow runs unchanged. **Management demo is preserved.**

### `bug_check.py` additions (~3 checks)
- With `WHATSAPP_PROVIDER` unset → router selects pywhatkit branch (mock the sender call, assert function name)
- With `WHATSAPP_PROVIDER=twilio` → router selects twilio branch (mock Twilio client, assert function name)
- With `WHATSAPP_PROVIDER=bogus` → falls back to pywhatkit with warning log

### Files touched
- `whatsapp_worker.py` (provider toggle + router; existing `_send_via_chrome_macos` and Twilio helpers stay)
- `config.py` (document the env var alongside `WHATSAPP_ENABLED` / `WHATSAPP_TRIGGERS`)
- `bug_check.py`

### What 6H deliberately does NOT do
- ❌ Delete `pywhatkit` from `requirements.txt`
- ❌ Remove `_send_via_chrome_macos` or its AppleScript helpers
- ❌ Break the launchd plist install path
- ❌ Force users to pick a provider — the unset-default = current behavior

---

## Phase 6I — Ollama containerization (~2 days)

### Goal
Ollama runs as its own service, INTERNAL-ONLY (no host port mapping), accessed by the app via container DNS. Models persist across container restarts.

### docker-compose.yml `ollama` service
```yaml
ollama:
  image: ollama/ollama:latest
  volumes:
    - gi-ollama:/root/.ollama
  networks: [gi-net]
  # NO ports: — internal only per A2 + "AI stays safe"
  restart: unless-stopped
  healthcheck:
    test: ["CMD", "ollama", "list"]
    interval: 30s
    start_period: 30s
```

### Init script `ollama/init.sh` (run once on first start via compose `command:` or a one-shot init container)
- `ollama pull qwen2.5-coder:7b` (NL search)
- `ollama pull llama3.1:8b` (chat + insights)
- Optional: `ollama pull qwen2.5vl:7b` if `GI_ENABLE_VISION_MODEL=1` (gated because the vision model is heavy and many sites won't use OCR)
- Idempotent (Ollama skips already-pulled models)

### App-side config
- `ai/client.py` already reads `OLLAMA_HOST` from env — verified to work with container hostname `http://ollama:11434`
- For local Mac dev, `OLLAMA_HOST` defaults to `http://localhost:11434` — unchanged

### Security guarantee (per "AI stays safe internal")
- Ollama service has NO `ports:` block → not bound to host network
- Only reachable via internal `gi-net` bridge → only `app` container can talk to it
- Reverse proxy (corporate Nginx / Traefik fronting the app, if any) does NOT route to Ollama
- Cloudflare Tunnel (if still used) only proxies the app container, never Ollama

### `bug_check.py` additions (~2 checks)
- `ai/client.py` reads `OLLAMA_HOST` from env (test by setting + asserting the parsed URL)
- Falls back to localhost when env unset

### Files touched
- `docker-compose.yml` (in 6G — Ollama service block)
- `ollama/init.sh` (new)
- `ai/client.py` (verify env var read; likely no code change needed)
- `bug_check.py`

---

## Phase 6J — Backup automation + NAS persistence (~1 day)

### Goal
Nightly backups land on the corporate NAS via a bind-mounted volume. 14-day retention. Surface failures in the Admin Overview.

### `backup` service
- Image: `alpine:3.19` + apk install `sqlite tini rsync` + a small `crond` config
- Cron entry: `0 2 * * * /backup.sh`
- `backup.sh`:
  1. `sqlite3 /data/gi_database.db ".backup /backups/sqlite_$(date +%Y%m%d_%H%M%S).db"`
  2. `rsync -a /data/uploads/ /backups/uploads_latest/`
  3. `rsync -a /models/ /backups/models_latest/`
  4. `find /backups -name 'sqlite_*.db' -mtime +14 -delete`
  5. Write timestamp to a marker file `/backups/.last_success`
  6. On any failure: write `/backups/.last_failure` with error log

### Admin Overview enhancement
- Admin Portal → 🖥️ Overview → 🔧 Service Health card adds a row:
  - "Backup" — green if `.last_success` is within 48h, amber if 48–72h, red if >72h or `.last_failure` newer than `.last_success`
- Source: a new helper `get_backup_health()` that reads the two marker files

### NAS mount
- `docker-compose.override.yml.example` shows: `volumes: gi-backups: { driver: local, driver_opts: { type: nfs, o: addr=corp-nas.internal,rw, device: ":/exports/gi_hub" } }`
- Admin replaces with their NAS path. If no NAS, falls back to a local bind-mount.

### Restore procedure (documented in `DEPLOY.md`)
1. `docker compose down`
2. `cp /backups/sqlite_<latest>.db /var/lib/docker/volumes/gi-data/_data/gi_database.db` (or use a `docker run --rm -v gi-data:/data alpine cp` invocation)
3. `rsync -a /backups/uploads_latest/ /var/lib/docker/volumes/gi-data/_data/uploads/`
4. `docker compose up -d`
5. Run smoke test (§6K)

### `bug_check.py` not affected (backup is infra cron, tested manually).

### Files touched
- `backup/Dockerfile` (or inline image override in compose)
- `backup/backup.sh` (new)
- `backup/crontab` (new)
- `docker-compose.yml` (backup service)
- `docker-compose.override.yml.example` (NAS template)
- `database.py` (`get_backup_health()` helper)
- `pages_internal/admin_portal.py` (extend Service Health card)
- `docs/DEPLOY.md` (restore procedure)

---

## Phase 6K — Deployment playbook + smoke test (~1 day)

### Goal
A single document that a corporate sysadmin can follow from a blank Linux VM to a running GI Hub.

### `docs/DEPLOY.md` structure
1. **System requirements** — Linux (Ubuntu 22.04+ recommended; RHEL 9+ also fine), Docker 24+, Docker Compose v2, 4 vCPU / 8GB RAM / 50GB disk minimum (16GB+ recommended if vision model enabled)
2. **Network requirements** — intranet DNS entry (e.g. `gi.corp.local` → server IP), port 8501 reachable from intranet users, NAS share path
3. **Twilio setup** — sign-up steps, paid number provisioning, credentials acquisition
4. **First start** — clone repo, edit `docker-compose.override.yml` for NAS + secrets, `docker compose up -d`, wait for Ollama init (~15 min on first run for model pulls), smoke test
5. **User onboarding** — admin login, change default password, create logistics + warehouse user accounts, import HR employee CSV (Phase 6A), train first YOLO model (Phase 6C)
6. **Backups + monitoring** — backup verification, log file locations, common failure modes
7. **Updates** — `git pull && docker compose build && docker compose up -d` (config-only updates skip the build step)
8. **Rollback** — restore-from-backup procedure (mirror of §6J restore)

### Smoke test checklist (also in `DEPLOY.md`)
- [ ] `docker compose ps` — all 3 services `running`
- [ ] Login as admin succeeds; sidebar bell renders without error
- [ ] Create a test PR → submit to logistics → in-app notification fires
- [ ] Sidebar Hub Assistant returns an answer (validates Ollama reachability)
- [ ] Admin Portal → 📱 WhatsApp Console → send manual test → `whatsapp_queue.status='sent'` (validates Twilio)
- [ ] After 24h wait OR `docker compose exec backup /backup.sh` → check `/backups/` has fresh `sqlite_*.db` (validates backup pipeline)
- [ ] If CV pilot deployed: scan a test QR + tool → loan recorded with `cv_detected=1`
- [ ] `docker logs gi-app | grep "delivery_reminders"` shows daily sweep (validates worker)

### Rollback procedure
1. `docker compose down`
2. Restore SQLite via the §6J procedure
3. `docker compose up -d`
4. Re-run smoke test

### Acceptance gate for v3.2 release
- All smoke test items pass on a fresh Linux VM with no prior state
- `docker compose down && up -d` cycle preserves all data
- Mac `streamlit run main.py` still works identically (regression check)
- bug_check + pytest still green

### Files touched
- `docs/DEPLOY.md` (new, comprehensive)

---

## Phase 6 — schema additions consolidated (column-by-column per `handoff.md` §6 format)

New tables (3) from Workstream A:

| Table | Key columns | Purpose |
|---|---|---|
| `employees` | `ID_Number` UNIQUE, `Name`, `Phone_Number`, `Department`, `status`, `created_by`, `created_at`, `updated_at` | Physical-labour master, separate from `users` |
| `tool_catalogue` | `class_name` UNIQUE, `display_name`, `category`, `model_version_id`, `min_confidence` (REAL, NULL = use global 0.75) | What the YOLO model can recognise |
| `cv_model_versions` | `version`, `model_path`, `classes_json`, `mAP`, `trained_at`, `is_active` (only one TRUE at a time) | Model versioning |

Extended existing tables:

| Table | New columns | Purpose |
|---|---|---|
| `returnable_items` | `cv_detected` (INTEGER), `cv_confidence` (REAL), `cv_employee_id` (TEXT), `cv_tool_class` (TEXT) | Audit which loans went through Smart Scan |

Reuses existing tables (no schema change):

| Table | What's added | Purpose |
|---|---|---|
| `delivery_reminders_sent` | New `ref_type='returnable_loan'` value | Dedup for the hourly returnable reminder sweep |
| `app_settings` | New keys: `returnable_reminders_last_run_hour` | Hour-bucket marker so the 60-sec poll loop skips repeats |

---

## Phase 6 — new dependencies consolidated

### Python (added to `requirements.txt`)
```
qrcode[pil]>=7.4
pyzbar>=0.1.9
Pillow>=10.0
ultralytics>=8.1
opencv-python-headless>=4.9
```

### Python (server-only, kept OUT of `requirements-server.txt`)
- None new — all Phase 6 Python deps are cross-platform

### Python (server-only, REMOVED from `requirements-server.txt` — but stays in `requirements.txt` for Mac)
- `pywhatkit` (Mac WhatsApp via AppleScript+Chrome)
- `pyobjc-framework-*` (if any are pulled in by pywhatkit transitively)

### Linux system packages (Dockerfile `apt-get install`)
```
libzbar0      # pyzbar runtime
libgomp1      # OpenCV runtime
libglib2.0-0  # OpenCV
tini          # PID 1 reaper for clean container shutdown
```

### macOS system packages (documented in `docs/cv_training_guide.md`)
```bash
brew install zbar     # only if pyzbar import fails
```

---

## Phase 6 — new env vars consolidated

| Variable | Default | Allowed values | Purpose |
|---|---|---|---|
| `WHATSAPP_PROVIDER` | `pywhatkit` (unset) | `pywhatkit` / `twilio` / `auto` | Phase 6H — sender backend |
| `GI_DEPLOYMENT_MODE` | unset | `server` / unset | Phase 6G — tells code it's in Docker so it can disable Mac-only paths |
| `GI_CV_ENABLED` | `1` | `0` / `1` | Phase 6D — hard kill switch for the Smart Scan expander |
| `GI_ENABLE_VISION_MODEL` | `0` | `0` / `1` | Phase 6I — whether to pull qwen2.5vl:7b for OCR |
| `OLLAMA_HOST` | `http://localhost:11434` | URL | Phase 6I — set to `http://ollama:11434` in Docker |
| `TWILIO_ACCOUNT_SID` | unset | Twilio SID | Phase 6H — Twilio creds (also can come from Docker secrets or secrets.toml) |
| `TWILIO_AUTH_TOKEN` | unset | Twilio token | ↑ |
| `TWILIO_FROM_NUMBER` | unset | `whatsapp:+...` | ↑ |

---

## Phase 6 — risk register

| Risk | Mitigation | Owner |
|---|---|---|
| **MUST NOT happen:** Mac WhatsApp path broken before management demo | Phase 6H is **explicit env-var toggle**, default = pywhatkit, NO code path deletes pywhatkit | Engineer |
| Tool images in real storeroom lighting are noisy | Capture training set under actual conditions; aggressive YOLO augmentation | Pilot SK + Engineer |
| Ollama container OOM on 8GB box if vision model loads | `GI_ENABLE_VISION_MODEL=0` by default; document 16GB min if enabled | Sysadmin |
| Twilio sandbox requires per-recipient opt-in | Use paid number before go-live; sandbox only for dev | User (procurement) |
| NAS unmounted silently → backup fails for days | Admin Overview Service Health amber/red on stale `.last_success` marker | Admin |
| QR codes on existing badges have a different format | Phase 6.0 preflight resolves this; system has manual-entry fallback regardless | User + HR |
| Employee table drifts when HR onboards new staff | CSV re-import is idempotent (UPDATE on existing `ID_Number`, INSERT on new); document monthly sync | Admin |
| YOLO model accuracy too low at pilot site | Confidence threshold raised; manual fallback always available; iterate dataset | Engineer + Pilot SK |
| Streamlit `st.camera_input` blocked by corporate browser policy | Per B4 decision — fallback to manual entry always present; no broken UX | — |
| pywhatkit Linux pip install fails inside Docker | `requirements-server.txt` omits it entirely; combined with Phase 6H router never imports it in server mode | Engineer |

---

## Phase 6 — execution order summary for a fresh session

When you (the next session) read this cold, this is your starting checklist:

1. **Confirm Phase 6.0 preflight items are resolved.** If any block is still red, surface it to the user before writing code.
2. **Decide workstream order with the user.** Recommended: Workstream A first (CV pilot on Mac) → then Workstream B (Docker). Both can also be parallel if the user has bandwidth.
3. **Workstream A starting point:** Open `database.py`, locate the existing `init_db()` body, append the Phase 6A schema block (mirror the existing Phase C round structure). Then `add_employee()` helpers. Then `bug_check.py` schema verification.
4. **Workstream B starting point:** Create `Dockerfile` + `requirements-server.txt` (`requirements.txt` minus `pywhatkit`). Then `docker-compose.yml`. Verify `docker compose build` succeeds before continuing to 6H.
5. **Run `bug_check.py` + `pytest` after EVERY phase.** Phase 6 must not regress 241/241 + 315/315. After Workstream A is done, expect ~270/270 + 315/315.
6. **Update `USER_MANUAL.md` + `SOP.md` after each user-visible change.** Maintain the additive Preservation Rule pattern from Phases 1–5.
7. **Touch `pywhatkit` only via the Phase 6H router.** If you find yourself about to delete the import or the AppleScript helper, you've misunderstood the constraint — re-read the "Critical operating constraint" subsection above.

---

## Phase 6 — out of scope (intentionally deferred)

The following were considered and **explicitly deferred** to Phase 7+ to keep Phase 6 shippable:

- SSO / Active Directory integration (A3 decision — bcrypt stays for now)
- Postgres migration (A4 decision — SQLite stays for now)
- Multi-warehouse CV rollout (X2 decision — pilot one site first)
- GPU acceleration for YOLO (A5 decision — CPU-first design)
- Live video WebRTC for camera capture (B4 decision — `st.camera_input` snapshot only)
- Cloudflare Tunnel on the company server (A2 decision — intranet only)
- Storing image bytes for audit (B7 decision — in-memory processing only)
- Force-close UNDO (deferred from Phase 5 §3)
- Vendor master maintenance UI (deferred from Phase 5 §3)

---

**End of handoff. Read this file first, then `USER_MANUAL.md` §13 + §15 + §16 for the latest UI reference (including the new Logistics + Warehouse portals). For day-to-day operating procedure across all 5 roles, read `SOP.md`. For the next development milestones, read `Phase 6 — Enterprise Deployment & Computer Vision (forward roadmap)` above. Run `python bug_check.py` before and after any database/mailer/page change.**
