# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the app
streamlit run main.py

# Run all tests
pytest

# Run a single test file
pytest tests/test_database.py

# Run the WhatsApp background worker (separate terminal, NOT via Streamlit)
python whatsapp_worker.py
```

## Architecture

This is a **Streamlit multi-page ERP app** for warehouse/inventory management across multiple sites. Entry point is `main.py`, which handles page routing behind an RBAC gate.

**Module responsibilities:**
- `main.py` — Page routing + RBAC gate. All page functions (`page_live_dashboard`, `page_daily_issue_log`, `page_hod_portal`, `page_admin_portal`, `page_reports`) live here.
- `database.py` — Pure Python data access layer. **Zero Streamlit imports.** All SQL lives here. Accepts an optional `conn` parameter so tests inject an in-memory SQLite connection instead of touching `gi_database.db`.
- `auth.py` — bcrypt login, session state (`st.session_state["gi_user"]`), user management UI, and `seed_default_users()`. Pure crypto helpers are Streamlit-free and testable.
- `config.py` — Single source of truth for all constants: brand colours, `ROLES`, `ROLE_HIERARCHY`, `PAGE_ACCESS`, `SYSTEM_COLS`, `OPTIONAL_ISSUE_COLS`.
- `ui_components.py` — Custom CSS injection, AgGrid wrapper, Plotly chart renderers, burn-rate banners.
- `mailer.py` — SMTP EOD reports + Outlook email drafting. OS-aware: uses `win32com` on Windows, `mailto:` on Mac.
- `reports.py` — `fpdf2`-based PDF generation (PR PDFs, universal table exports, QR label sheets).
- `whatsapp_worker.py` — Standalone background script (run separately). Polls `whatsapp_queue`, sends via PyWhatKit with state-lock (`pending → processing → sent/failed`) to prevent double-sends.

## RBAC

Role hierarchy (`config.py:ROLE_HIERARCHY`): `store_keeper(0) < supervisor(1) < hod(2) < admin(3)`. `PAGE_ACCESS` maps each page to its minimum required role. `_can_access()` in `main.py` enforces this at routing time.

Default seeded credentials (change after first login): `admin/admin2026`, `hod/hod2026`, `supervisor/super2026`, `worker/floor2026`.

## Key Workflows

**Two-stage inventory loop:** Store Keepers write to `pending_issues` (consumption) or `pending_receipts` (receipts) with `status='draft'`. Submitting changes status to `'pending_hod'` and queues a WhatsApp alert to the site HOD. HOD commits via EOD tab, which moves records to `consumption`/`receipts` and recalculates live stock.

**Live stock formula:** `Current_Stock = Total_Received - Total_Consumed - Total_Returned` (identity math, not a stored column).

**Shelf-Life Gatekeeper:** When adding a consumption entry, the system calls `get_short_dated_stock()` and hard-blocks with `st.stop()` if expiring batches exist — until the user checks the override checkbox.

**Dynamic forms:** Store Keeper and Admin forms are generated at runtime via `PRAGMA table_info`, excluding columns in `SYSTEM_COLS`. Adding a column to the DB table automatically adds it to the form.

## Database

SQLite file: `gi_database.db`. `init_db()` is self-healing — it runs `ALTER TABLE ADD COLUMN` for any columns missing from existing tables. Always use parameterized queries (`?`) — never f-string SQL with user input.

Key tables: `inventory`, `consumption`, `pending_issues`, `receipts`, `pending_receipts`, `pr_master`, `returnable_items`, `cross_site_requests`, `whatsapp_queue`, `system_audit_log`.

## Testing

Tests use in-memory SQLite via `get_connection(":memory:")` — they never touch `gi_database.db`. Fixtures are in `tests/conftest.py`. `database.py` functions accept an injected `conn` argument for this purpose.

## Dashboard UI Rule

The Live Dashboard must show the raw data table as the primary visual. Aggregate metrics ("Total Consumed", etc.) are **prohibited** on the dashboard — see `ARCHITECTURE.md` for the rationale.

## Preservation Rule

**Never modify, delete, or break existing working features (e.g., cross-site inquiries, user registration, PDF generation, existing WhatsApp triggers) when adding new changes. Only use surgical appends.**