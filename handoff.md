# GI Hub ERP — Handoff

**Last update:** End of multi-session tuning. **315/315 tests passing.**
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
│                                 — HTML table for PR logistics email
├── reports.py                    PDF/Excel/CSV generators with narrow-column smart widths
├── whatsapp_worker.py            Standalone daemon — must run separately
│
├── pages_internal/
│   ├── live_dashboard.py         Hero strip (4 cards inc. Stock Value SAR),
│   │                             AgGrid with Unit_Cost + Stock_Value cols
│   ├── daily_issue_log.py        4 tabs: Consumption / Receipt / Returnables / Stock Count
│   │                             FEFO override expander + auto-tag Lot_Number
│   ├── hod_portal.py             10 tabs (EOD / Cross-Site / Burn Rate / Pending Receipts /
│   │                             Adjustments / PRs / Receive / Shelf-Life / Notifications /
│   │                             My Requests) + type-COMMIT EOD dialog with neg-stock guard
│   ├── admin_portal.py           9 tabs (Overview / Pending Requests / Global Sites /
│   │                             Users / Master DB Editor / Audit Logs / WhatsApp Console /
│   │                             Settings / Access Control)
│   └── reports_page.py           4 tabs (Generate / Scheduled / AI Insights / Archive)
│                                 _strip_empty_columns auto-applied to every report
│
├── ai/
│   ├── nl_search.py              Ollama qwen2.5-coder → SQL
│   ├── summarize.py              Ollama llama3.1 streaming
│   ├── fuzzy.py                  Material-name → SAP matcher
│   ├── ocr.py                    Vision-LLM bulk staging
│   └── insights.py               5 fixed SQL probes + LLM commentary
│
├── pwa/
│   ├── api.py                    FastAPI service for offline mobile
│   └── (single-file PWA HTML)
│
├── USER_MANUAL.md                Full user catalogue (~14k words) — generated for PDF export
└── handoff.md                    THIS FILE
```

### Critical contracts (do not break)

- **`commit_eod(conn) → int`** — signature unchanged through all tuning. Moves `pending_issues` → `consumption`, deletes staged.
- **`process_receipt_delivery(conn, date, sap, qty, supplier, remarks, site, pr_number, expiry_date, extra_fields)`** — auto-creates lot row when Lot_Number provided or Expiry_Date set. Idempotent via UNIQUE on lots.
- **Identity math everywhere:** `Current_Stock = Σ receipts − Σ consumption − Σ returns`. Never stored. Computed via `v_site_stock` view. Same pattern for `v_lot_balance` (Remaining = Received − Consumed per Lot_Number).
- **Self-healing schema** — `init_db()` adds missing cols on every startup. No manual migrations.

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

## 3. Remaining Features — Prioritized

### P0 — Operational gaps that could surprise users

1. **Maintenance Mode actually blocks non-admins at login**
   - `app_settings.maintenance_mode` is written by the toggle but `auth.py` doesn't read it
   - Need: `if get_app_setting("maintenance_mode") == "1" and user.role != "admin": show downtime page`

2. **Returnable overdue WhatsApp — verify scheduler**
   - `get_overdue_unreported_items()` exists but I never confirmed there's a periodic call that queues the WhatsApp
   - Check whether `whatsapp_worker.py` polls this or a separate cron is expected

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

---

## 4. How to Run / Develop

```bash
# Run app
streamlit run main.py

# WhatsApp worker (separate terminal)
python whatsapp_worker.py

# Tests
.venv/bin/python -m pytest -x --tb=short -q   # 315 expected

# Smoke a specific helper without Streamlit overhead
.venv/bin/python -c "
import database as d
conn = d.get_connection(':memory:')
d.init_db(conn)
# … do stuff …
"
```

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

---

## 6. Inventory of Schema Additions (last few months)

- `app_settings(key, value)` — thresholds, maintenance_mode, last_backup_at
- `bug_reports`, `report_schedules`, `report_archive`
- `stock_adjustments` + 9 reason codes
- `lots` + `v_lot_balance` view
- `inventory.Unit_Cost`, `receipts.Unit_Cost`
- `pr_master.workflow_state, UOM, Supplier, Est_Cost_SAR, Notes`
- `pending_receipts.rejection_reason`
- `receipts.Lot_Number`, `consumption.Lot_Number`, `pending_issues.Lot_Number`, `pending_receipts.Lot_Number`
- `pending_issues.FEFO_Override`, `consumption.FEFO_Override`

All added via self-healing `ALTER TABLE` in `init_db()`. None require manual migration.

---

**End of handoff. Read this file first, then `USER_MANUAL.md` if you need exhaustive UI reference.**
