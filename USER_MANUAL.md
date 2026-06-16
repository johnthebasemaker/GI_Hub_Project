# General Industries Hub — Product Manual & User Catalogue

**Version 3.0** · Multi-Site Warehouse Inventory ERP + Procurement Chain
**Document Scope:** Complete operational reference for every role, page, tab, and element built into the system.
**Companion documents:** `handoff.md` (technical architecture for engineers) · `SOP.md` (daily/weekly procedure for the Logistics + Warehouse teams).

---

## Table of Contents

1. [Introduction & System Overview](#1-introduction--system-overview)
2. [Roles, Permissions & Page Access](#2-roles-permissions--page-access)
3. [Login, Sidebar & Common Elements](#3-login-sidebar--common-elements)
4. [Store Keeper Manual](#4-store-keeper-manual)
5. [Supervisor Manual](#5-supervisor-manual)
6. [HOD (Head of Department) Manual](#6-hod-head-of-department-manual)
7. [Admin Manual](#7-admin-manual)
8. [Reports Module — Detailed Reference (HOD / Admin / Supervisor)](#8-reports-module--detailed-reference)
9. [Automated Notifications — WhatsApp & Email & In-app Bell](#9-automated-notifications--whatsapp--email)
10. [Data Model & Concept Reference](#10-data-model--concept-reference)
11. [Status Codes, Reason Codes & Glossary](#11-status-codes-reason-codes--glossary)
12. [FAQ — Master Index by Role](#12-faq--master-index-by-role)
13. [2026-06 Feature Update — What Changed](#13-2026-06-feature-update--what-changed)
14. [Logistics Portal Manual (NEW in v3.0)](#14-logistics-portal-manual)
15. [Warehouse Portal Manual (NEW in v3.0)](#15-warehouse-portal-manual)
16. [Cross-Role Procurement Walk-through (NEW in v3.0)](#16-cross-role-procurement-walk-through)
17. [Operations & Hosting — the after-launch chapter](#17-operations--hosting--the-after-launch-chapter)

---

# 1. Introduction & System Overview

## 1.1 What the system does

The General Industries (GI) Hub is a **multi-site warehouse inventory ERP** built on Streamlit + SQLite. It tracks every material movement (consumption, receipt, return, adjustment) through a two-stage approval ledger, enforcing real-time stock, FEFO (First-Expiry-First-Out) lot discipline, valuation, and audit trail across every site you manage.

## 1.2 Core principles

| Principle | What it means |
|---|---|
| **Identity math, not stored counters** | `Current_Stock = Total_Received − Total_Consumed − Total_Returned`. The number is always computed from the immutable movement ledger — never written to a column. This makes drift impossible. |
| **Two-stage approval** | Store Keepers stage entries → HOD reviews and commits at End-of-Day. Nothing touches the permanent ledger without HOD approval. |
| **Site isolation** | HODs and Supervisors see only their own site's stock. Only Admin sees all sites. Cross-site moves require formal request + approval. |
| **Audit-first** | Every consequential action writes to `system_audit_log` with username + timestamp + details. Even deletions leave a trace. |
| **Self-healing schema** | The DB layer automatically adds missing columns/tables on startup. You never need to run migrations manually. |
| **Procurement chain (v3.0)** | A SQL-driven workflow from Site PR through Logistics PO, Warehouse DN, and Site SK receipt. Every state transition is audited. Logistics owns POs; Warehouse owns physical receiving + DN preparation; Site HOD approves DN content; SK confirms physical arrival. |
| **RL/BL strict separation** | Rubber Lining and Brick Lining items NEVER share a PO line group, a DN, or a warehouse aggregation. The system rejects mixed-family DNs by design and tags each line with its family on insert. |
| **Warehouse-blind pricing** | Warehouse users can see materials and quantities but NEVER see Unit_Price, Total_Price, or any monetary header field on a PO. Three independent enforcement layers guarantee this. |

## 1.3 The transaction lifecycle (the heart of the system)

```
┌─────────────────┐    ┌─────────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│ Store Keeper    │───▶│ pending_issues      │───▶│ HOD EOD Review   │───▶│ consumption      │
│ enters issue    │    │ (status='pending_   │    │ (edit/approve/   │    │ (permanent       │
│                 │    │  hod')              │    │ reject/commit)   │    │  ledger)         │
└─────────────────┘    └─────────────────────┘    └──────────────────┘    └──────────────────┘
                                                          │
                                                          ▼
                                                  ┌──────────────────┐
                                                  │ WhatsApp alerts  │
                                                  │ + audit log      │
                                                  └──────────────────┘
```

The same shape applies to receipts (`pending_receipts → receipts`) and adjustments (`stock_adjustments → consumption/receipts`).

### 1.3a The procurement chain (v3.0)

```
Site HOD          → submits PR        → Logistics: 📥 Incoming PRs
Logistics         → issues PO         → Site HOD: notifies + Admin: oversight
Logistics         → assigns PO/items  → Warehouse: 🔔 Incoming Assignments
Warehouse user    → acks + receives from vendor (records physical arrival)
Warehouse user    → drafts DN (RL/BL safe) → Logistics: ✈️ DN approval queue
Logistics         → approves date    → HOD: 🚚 DN Approvals tab
HOD               → approves content → SK: 🚚 Incoming DNs (Receipt Staging)
Store Keeper      → marks received   → receipts ledger + DN closed
```

Side-paths: vendor returns (any role can raise), reschedules (Warehouse/HOD → Logistics), force-closures (Logistics only, audited to Admin + Site HOD).

## 1.4 Currency, dates, units

- **Currency:** SAR (Saudi Riyal). All money is stored as REAL and displayed via `format_sar()`.
- **Dates:** ISO format internally (`YYYY-MM-DD`); display format `DD/MM/YYYY` or `DD MMM YYYY`.
- **Time zone:** Server local time. Audit timestamps include seconds.
- **Units of Measure (UOM):** Per-item on the `inventory` master (e.g., Pcs, Box, Roll, Can, m, kg). No automatic UOM conversion — issue UOM = receipt UOM.

---

# 2. Roles, Permissions & Page Access

## 2.1 Role hierarchy

```
store_keeper (0) < warehouse_user (1) ≈ supervisor (1) < hod (2) < logistics (3) < admin (4)
```

The hierarchy is parallel, not strictly linear — `warehouse_user` and `supervisor` sit at the same numeric level but are scoped differently (one to a warehouse, the other to a site). Procurement-chain pages (Logistics Portal, Warehouse Portal) are EXACT-role-locked in addition to the hierarchy check, so a numerically higher role (e.g. Logistics) does NOT inherit access to a lower role's page (e.g. HOD Portal) just because the hierarchy says it could. The hierarchy lives in `config.py:ROLE_HIERARCHY`; the exact locks live in `main.py:_EXACT_ROLE_PAGES`.

## 2.2 Page access matrix

| Page | Store Keeper | Warehouse User | Supervisor | HOD | Logistics | Admin |
|------|:---:|:---:|:---:|:---:|:---:|:---:|
| 📦 Live Dashboard | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 📝 Entry Log | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 📋 HOD Portal | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ (hidden — uses Admin Portal) |
| 🚚 Logistics Portal (NEW) | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ (admin shadow) |
| 🏭 Warehouse Portal (NEW) | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ (admin shadow, picks WH in sidebar) |
| 🛡️ Admin Portal | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| 📊 Reports | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ |

## 2.3 Site scope by role

| Role | What they see |
|------|---|
| Store Keeper | Their own site only — they cannot view another site's stock. |
| Warehouse User (NEW) | Their own warehouse only — POs assigned to them, DNs they've prepared, items received from vendors. Tied to `users.Warehouse_ID`. |
| Supervisor | Their own site only — Reports, Live Dashboard, Burn Rate all site-locked. |
| HOD | Their own site only — but they can REQUEST material from other sites (Cross-Site tab) and submit PRs to Logistics. |
| Logistics (NEW) | All sites globally for PRs and POs they manage. No site lock — they sit above the site boundary. |
| Admin | All sites + all warehouses globally — has the "All Sites" filter on every multi-site view; warehouse picker in sidebar when shadowing the Warehouse Portal. |

## 2.4 Default seeded accounts

The first time the app starts, these accounts are created. **Change the passwords immediately.**

| Username | Password | Role |
|---|---|---|
| admin | admin2026 | Admin |
| hod | hod2026 | HOD |
| supervisor | super2026 | Supervisor |
| worker | floor2026 | Store Keeper |

**No default `logistics` or `warehouse_user` accounts are seeded.** Both roles are strictly admin-created — go to **Admin Portal → 👥 Users → Add user**. This is intentional: the procurement chain has commercial visibility (Logistics sees prices, Warehouse routes inventory) and seeded credentials would be a security liability. When you create a `warehouse_user`, set their `Warehouse_ID` to one of the values from **Admin Portal → 🗄️ Master DB Editor → `warehouses` table** — without it, the user lands on the Warehouse Portal and sees an error telling them to ask Admin.

---

# 3. Login, Sidebar & Common Elements

## 3.1 Login screen

**Elements:**

- **Username text box** — your assigned username (case-sensitive).
- **Password text box** — masked input. Min 8 characters policy enforced on creation.
- **🔑 Sign In button** — validates credentials via bcrypt; on success, populates `st.session_state["gi_user"]` and routes to the first allowed page.
- **"Don't have an account? Request access" link** — opens a self-service registration form.

**Registration form elements:**

- **Username** — must be unique
- **Password / Confirm Password**
- **Role requested** — selectbox: Store Keeper / Supervisor / HOD (Admin role cannot be self-requested)
- **Site_ID** — which site to associate the account with
- **Phone Number** — used for WhatsApp alerts (format: `+966 5X XXX XXXX`)
- **Submit Request button** — creates a `pending_users` row + audit log + WhatsApp alert to all admins

Admin must approve via Admin Portal → Users tab before the user can log in.

## 3.2 Sidebar (visible after login)

Every page shares this sidebar. Reading top to bottom:

| Element | Purpose | Notes |
|---|---|---|
| **GI Hub bolt icon + version** | Branding | Shows `v3.0.0` |
| **Role card** | Your username + role badge | Color-coded: grey=Store Keeper, emerald=Warehouse, blue=Supervisor, indigo=HOD, sky=Logistics, gold=Admin |
| **🔔 Notifications bell (NEW v3.0)** | Unread count + inbox dialog | Red badge if N>0, primary button changes to `"Open inbox (N unread)"`. Modal shows recent procurement events with mark-read controls. See §3.6 |
| **"Navigate to:" radio** | Page picker | Only shows pages allowed by your role |
| **INVENTORY ALERTS section** | Compact stock-alert badge | Visible to Supervisor/HOD/Admin only. Shows count of items below minimum (or "All levels adequate" if clear) |
| **Bug/Feature reporting** | Opens a dialog | See §3.4 |
| **Theme toggle** | Dark / Light mode switch | Persists per-session |
| **🚪 Sign Out button** | Ends the session | Audit-logs the LOGOUT event |

## 3.3 Brand header (top of every page)

| Variant | Used by | Visual |
|---|---|---|
| `render_brand_header` | Live Dashboard, Entry Log | Gold subtitle accent + current date |
| `render_brand_header_hod` | HOD Portal | Purple subtitle accent + current date |
| `render_brand_header_admin` | Admin Portal | Gold accent + green/amber pulse chip ("All systems operational" / "Degraded") + current date |

## 3.4 Bug / Feature reporting dialog

Available to every user from the sidebar:

- **Type selectbox** — Bug Report / Feature Request
- **Page dropdown** — which page the issue/idea relates to (Live Dashboard, Entry Log, HOD Portal, Admin Portal, Reports, Other)
- **Description textarea** — up to 200 characters
- **Submit button** — writes to `bug_reports` table with user, timestamp, type, page, description. Visible to Admin in the Admin Portal Reports & Bugs tab for triage.

## 3.6 Notifications bell (NEW in v3.0)

Sits between the role card and the navigation radio. Every signed-in user sees their own personalised inbox of procurement-chain events.

**The button:**
- **No unread:** `"Open inbox"` (secondary button, no badge)
- **1+ unread:** `"Open inbox (N unread)"` (primary button, red pill badge with the count, capped at "99+")

**The inbox dialog (click the button):**
- **Only unread toggle** — on by default. Flip off to see your full history.
- **Per-notification card** — colour-coded left border by severity:
  - 🔴 Critical (red) — force-closures, T-0 delivery reminders
  - 🟡 Warning (amber) — T-1/T-2 reminders, reschedule requests
  - 🟢 Success (green) — DN approved / received successfully
  - 🔵 Info (blue) — PR submitted, PO issued, assignments
- **Per-row 👁 Mark read** — flips just that notification
- **Bulk ✅ Mark all as read** — flips every visible row (respecting the role + site + warehouse scope you'd see normally)

**What gets sent here:**
| Event you'll see | Triggered by |
|---|---|
| New PR from a site (Logistics only) | Site HOD pressing "🚚 Submit PR(s) to Logistics" |
| PO issued (Site HOD) | Logistics creating a PO against your site's PR |
| PO assigned to warehouse (Warehouse only) | Logistics routing a PO to your WH |
| DN awaiting your approval (HOD) | Logistics approving a DN delivery date |
| Incoming DN ready to receive (SK) | HOD approving DN content |
| Reschedule requested (Logistics) | Warehouse or HOD asking to push a date |
| Reschedule decided (requester) | Logistics approve/reject |
| Force-closure (Admin + originating HOD) | Logistics force-closing a PR/PO/line |
| Vendor return raised (Logistics) | Any role raising a return |
| Delivery reminder T-2 / T-1 / T-0 | The daily sweep job (see §9) |

The bell is tolerant — if a notification helper errors, the badge silently shows 0 instead of crashing the sidebar. In-app notifications ALWAYS fire; the WhatsApp side is gated by toggles in `config.WHATSAPP_TRIGGERS` (see §9).

## 3.5 Overdue Returnable banner (Store Keepers only)

When a Store Keeper logs in and there are overdue returnable items at their site, a red banner appears at the top of any page they navigate to:

> ⚠️ **OVERDUE ITEMS — Action Required:** N borrowed item(s) past expected return: **<item names>**. Go to the **Returnable Items** tab to follow up.

---

# 4. Store Keeper Manual

The Store Keeper is the warehouse-floor operator. They see only the **Entry Log** page and the sidebar shell.

## 4.1 Pages visible

- 📝 Entry Log (only)

## 4.2 Entry Log — Tab structure

The Entry Log has **four tabs**:

1. 📋 Consumption Log — record material issued out
2. 📦 Receipt Staging — record material arriving in
3. 🔄 Returnable Items — track tools/items temporarily issued
4. 🧮 Stock Count — submit physical-count reconciliations

---

## 4.3 Entry Log → 📋 Consumption Log

This is where every material consumed by site operations is recorded.

### 4.3.1 Top section: Bulk OCR upload (expander)

**📷 Upload Handwritten Consumption List (OCR)** — for when you have a handwritten list to bulk-stage instead of typing each row.

| Element | Purpose |
|---|---|
| **Input method radio** | Switch between "Image upload (vision AI)" and "Paste text" |
| **File uploader** | Upload PNG/JPG/JPEG/WEBP of handwritten list (vision model reads it) |
| **🔎 Extract rows from image button** | Triggers vision AI extraction → preview rows |
| **Paste text area** | Alternative — paste tab/comma/pipe-separated rows |
| **🔎 Parse pasted rows button** | Parse the textarea |
| **OCR Review grid** | Editable preview of extracted rows with ambiguous-match pickers |
| **Confirm All & Stage button** | Pushes all reviewed rows into `pending_issues` |

### 4.3.2 Mobile camera barcode/QR scanner (expander)

**📷 Barcode / QR Scanner** — opens the browser camera and reads SAP codes off labels.

| Element | Purpose |
|---|---|
| **Camera feed** | Live preview (requires camera permission) |
| **Manual SAP code entry text box** | Fallback if camera unavailable — also accepts pasted scanner output |
| **Detected code display** | Shows the last successfully scanned code in green |

When a code is scanned, the material selectbox auto-populates so you don't have to search.

### 4.3.3 ➕ Scan / Add New Item to Queue (main expander, expanded by default)

This is the workhorse panel. Reading top to bottom:

#### A. Recently-scanned ring buffer

| Element | Purpose |
|---|---|
| **⏱️ Recent: pills** | Up to 5 quick-tap buttons showing your last 5 items (SAP code + truncated description). Tapping one auto-fills the material selectbox. |

#### B. 1. Select Material

| Element | Purpose |
|---|---|
| **Search by SAP Code or Description selectbox** | Type-ahead search across the inventory master. Format: `[SAP_Code] Description`. |

When you pick a material, two cards appear automatically:

#### C. Item Snapshot card

A compact dark card showing:
- **SAP code** (gold monospace)
- **Material description** (bold)
- **3-stat strip:**
  - 🏠 SITE STOCK (color-coded: red=empty, amber=below min, green=ok)
  - 🔥 30-DAY BURN (units consumed last 30 days)
  - 📊 DAILY RATE
- **Inline 70×22 SVG sparkline** — last 30 days consumption trend
- **Status badge** bottom-right (OK / Low / Below Min / Empty)

#### D. FEFO panel (lot suggestion)

If lots exist for this item at your site:

| Element | Purpose |
|---|---|
| **🏷️ FEFO — First Expiry, First Out header** (amber) | Banner |
| **Per-lot row** | Lot number (monospace) · ×qty (dim) · "Exp <date>" (amber if <90d, red if expired) · **USE FIRST** badge on the first row |

The system will automatically attach the top-FEFO lot number to your consumption row when you submit.

#### E. 🔄 Pull from a different lot (FEFO override) — expander

Only renders when 2 or more open lots exist for this item at your site.

| Element | Purpose |
|---|---|
| **Lot to pull from instead selectbox** | Default: "Keep FEFO suggestion". Other options: all other open lots with expiry + remaining qty shown |
| **Reason for override text box (200 char max)** | Mandatory. Min 5 characters to activate the override |
| **Amber confirmation banner** | Appears once override is active: "FEFO override active: pulling from <chosen> instead of <suggested>" |

When activated:
- Your consumption row gets `Lot_Number = chosen` + `FEFO_Override = reason`
- Audit log entry `FEFO_OVERRIDE` is written with full context
- A WhatsApp alert is queued to the site HOD in real time

#### F. 2. Fill Entry Details (dynamic form)

Fields appear based on the `pending_issues` schema. Every required field is marked with `*`.

| Field | Type | Purpose |
|---|---|---|
| **Date** | Date picker (default: today) | When the consumption happened |
| **Quantity** | Number input (min 0.1) | How much you're issuing. Above this field is a **stock badge** showing current site stock; a red ⚠️ warning appears if you type more than available. |
| **Work_Type** | Selectbox (sourced from `system_settings.Work_Type`) | Classification: PM Work, Breakdown, Project, Shutdown, Inspection, etc. |
| **Issued_By** | Text (auto-filled with your username) | Who handed it over |
| **Issued_To** | Text (smart-defaults from your last entry) | Recipient personnel/department |
| **Tank_No** | Text | Equipment tag if applicable |
| **Serial_No** | Text (optional) | Item serial number if tracked |
| **PR_Number** | Text (smart-default) | Linked purchase request if applicable |
| **Remarks** | Text (optional) | Free-form notes |

**Hidden side-effects:**
- `Site_ID` is auto-set to your site
- `Lot_Number` is auto-set to FEFO top suggestion (or your override)
- `status` is set to `'draft'` until you submit

#### G. ⚠️ Override Expiry Warning (amber card with checkbox)

If the system detects a short-dated batch at your site for this item, it will **hard-block** the Add to Grid button until you check this box, confirming you've physically pulled from the expiring batch first.

#### H. Add to Grid ⬇️ button

Validates that:
- A material is selected
- No required fields are empty
- Quantity ≤ current site stock (over-issue guard)
- Expiry override is checked when needed

On success: inserts a `pending_issues` row with `status='draft'`, toasts `✅ Added to staging queue`, reruns the page.

### 4.3.4 Staging Queue section

| Element | Purpose |
|---|---|
| **📋 Staging Queue header + count badge** | Shows how many draft rows you've accumulated |
| **Data editor table** | Edit any row inline before submitting |
| **💾 Save Draft Edits button** | Persists edits in-place (rows stay as drafts) |
| **📨 Submit Grid to HOD button** | Flips all draft rows to `status='pending_hod'` + queues a WhatsApp alert to the site HOD with the full item list |

After submission, the rows are locked from your view — they appear in HOD Portal → EOD Commit tab for approval.

---

## 4.4 Entry Log → 📦 Receipt Staging

For when materials arrive at your site.

### 4.4.1 📷 Upload Delivery Note (OCR) — expander

Identical lane structure to consumption OCR — upload an image of the delivery note OR paste text. Vision AI extracts rows for bulk staging.

### 4.4.2 ➕ Add Receipt to Queue — expander (expanded by default)

#### A. 1. Select Material

| Element | Purpose |
|---|---|
| **Search by SAP Code or Description selectbox** | Same type-ahead search as consumption |

When picked, a blue-tinted info card shows:
- 📋 **Mat Code**
- **UOM**

#### B. 2. Fill Receipt Details (dynamic form)

| Field | Type | Purpose |
|---|---|---|
| **Date** | Date picker (today) | Delivery date |
| **Quantity** | Number input | Units received |
| **Supplier** | Text (optional) | Vendor name |
| **Lot_Number** | Text (optional) | If blank + Expiry_Date is set, system auto-generates `LOT-YYYYMMDD-SAP` |
| **Expiry_Date** | Date picker (optional) | Lot expiry — when set, triggers lot master entry |
| **PR_Number** | Text (optional) | Link to an open Purchase Request |
| **Serial_No / Vehicle_No / DN_No / Pallet_No / Mob_From / Mob_To / Prepared_by / Driver_Name** | Text (optional) | Logistics tracking fields |
| **Remarks** | Text | Free notes |

#### C. Add to Receipt Queue ⬇️ button

Validates: material picked, required fields populated. Inserts a `pending_receipts` row with `status='draft'`.

### 4.4.0 🚚 Incoming Delivery Notes from Warehouse (NEW in v3.0)

A new expander appears at the TOP of the Receipt Staging tab. It's only populated when the Warehouse has prepared a DN bound for your site AND your HOD has approved it. If empty, the expander shows: *"Nothing inbound. Logistics → HOD-approved DNs will appear here for you to confirm physical receipt."*

When DNs are inbound, each appears as its own container:

| Element | Purpose |
|---|---|
| **DN header line** | `DN <number> · PO <number> · Warehouse <id> · DN Date <date>` |
| **Line count + total qty** | At-a-glance: how many SKUs, how many units total |
| **View lines expander** | Material_Code, Description, Qty, UOM, Lot_Number, Expiry_Date, Remarks — read-only preview of every line |
| **✅ Mark as Received button** | When you confirm physical receipt: writes one `receipts` row per DN line (with DN_Number, Warehouse_ID, PO_Number_Source for full traceback), flips the DN to `received`, and clears it from your list. Inventory cache busts immediately so Live Dashboard reflects the new stock the same minute. |

**When to use:**
- The Warehouse delivered the materials physically to your site
- HOD already approved the DN content (you'll see it appear without any action)
- You've inspected the goods and they match the DN qty + lot

**When NOT to use:**
- Direct deliveries from supplier to your site (those go through the existing Add Receipt to Queue → Submit to HOD flow below; the email-driven PR/PO path remains supported)
- Partial receipts (the current flow assumes you confirm the full DN qty — for partials, raise a Vendor Return on the diff after confirming and ask your HOD)

This is the FINAL step in the procurement chain. After you click Mark as Received:
- Logistics sees `dn_received_by_sk` in their notifications
- Warehouse sees the same
- The PO and PR move toward closure if this DN completes the order

### 4.4.3 Receipt Draft Queue section

| Element | Purpose |
|---|---|
| **📋 Receipt Draft Queue header + count badge** | Number of drafts you've accumulated |
| **Data editor table** | Inline edit before submit |
| **💾 Save Draft Edits button** | Persists |
| **📨 Submit to HOD for Approval button** | Flips drafts to `status='pending_hod'` + WhatsApp alert to HOD with item list |

---

## 4.5 Entry Log → 🔄 Returnable Items

For tools, gauges, fittings temporarily handed to personnel — items that should come back.

### 4.5.1 ➕ Issue a Returnable Item — expander (expanded by default)

| Field | Type | Purpose |
|---|---|---|
| **Material / Tool Name** | Text | What you're handing out |
| **UOM** | Text (e.g., Pcs, Set) | Unit |
| **Quantity** | Number (min 0.1) | How many |
| **Borrower Name** | Text | Person taking custody |
| **Borrower WhatsApp Number** | Text (optional, `+966 ...`) | For overdue alerts |
| **Expected Return Date** | Date (default: tomorrow) | When you expect it back |
| **Expected Return Time** | Selectbox: `04:15 PM`, `06:15 PM`, `Custom Time...` | Time-of-day expectation |
| **Issue Item 📤 button** | Validates name+borrower, writes to `returnable_items` |

### 4.5.2 Currently Borrowed Items section

Shows all borrowed (not yet returned) items at your site.

- **Overdue red banner** — when one or more items are past their expected return time
- **Borrowed items table (styled HTML)** with columns: ID, Material, UOM, Qty, Borrower, Phone, Given Time, Expected Return, **Status** (pill: green "On Loan" / red "⚠️ Overdue")
- **Mark as Returned section:**
  - **Selectbox** of borrowed items
  - **✅ Mark as Returned button** — updates `status='returned'`, busts cache, toast confirms

---

## 4.6 Entry Log → 🧮 Stock Count

For reconciling physical shelf count with system stock when they don't match (damage, expiry disposal, miscount, found-extra).

### 4.6.1 1. Select Material to Count

| Element | Purpose |
|---|---|
| **Search by SAP Code or Description selectbox** | Pick the material to reconcile |
| **Help caption** | Explains when to use this tab |

### 4.6.2 2. Enter Count Details (only renders after picking a material)

#### Left column

| Element | Purpose |
|---|---|
| **📊 System Qty card** | Read-only dashboard showing current stock at your site (auto-fetched) |
| **🔢 Counted Qty input** | What you actually count on the shelf right now (defaults to system qty) |
| **Variance preview banner** | Color-coded: green "➕ Found N extra" or red "➖ Short by N" or grey "No variance" |

#### Right column

| Element | Purpose |
|---|---|
| **🏷️ Reason Code selectbox** | 9 options. Default depends on variance direction. |
| **📝 Notes textarea (300 char max)** | Free explanation: "found behind shelf 3", "damaged in transit", etc. |

**Reason codes:**

| Code | Label |
|---|---|
| `cycle_count` | 🔄 Cycle count correction |
| `damaged` | 🔨 Damaged / unusable |
| `expired_disposal` | 🗑️ Expired — disposed |
| `miscount_in` | ➕ Miscount — found extra |
| `miscount_out` | ➖ Miscount — short |
| `lost` | ❓ Lost / unaccounted |
| `theft` | 🚨 Suspected theft |
| `return_to_supplier` | ↩️ Returned to supplier |
| `other` | ❔ Other (see notes) |

### 4.6.3 Submit section

| Element | Purpose |
|---|---|
| **Amber warning banner** | "Submitting this sends the count to your HOD for approval. No stock changes until they approve." |
| **📤 Submit Count for HOD Approval button** | Disabled while variance = 0. On success: writes to `stock_adjustments` with `status='pending_hod'`, queues WhatsApp to site HOD with count details, audit-logs. |

### 4.6.4 Recent Adjustments at This Site (history)

Read-only table showing: ID, SAP Code, Material Name, Variance, Reason, Status, Submitted By, Submitted At.

---

## 4.7 Store Keeper — Use Cases

### Use Case 1: Issue 10 Pipe Gaskets to PM Work

1. Log in → Entry Log → 📋 Consumption Log tab
2. Open **➕ Scan / Add New Item to Queue**
3. Search "Pipe Gasket" → pick from dropdown
4. Read the stock badge (e.g., "🟢 156 Pcs"), confirm FEFO lot suggestion
5. Quantity: **10**
6. Work_Type: **PM Work**
7. Issued_To: name of recipient
8. Tank_No / PR_Number: as applicable
9. Click **Add to Grid ⬇️**
10. Repeat for any other items in this batch
11. Review the Staging Queue table → edit if needed → click **📨 Submit Grid to HOD**

The row(s) are now in HOD's EOD queue. Stock won't decrease until HOD commits EOD.

### Use Case 2: Receive a delivery of 200 units with expiry date

1. Entry Log → 📦 Receipt Staging tab
2. Open **➕ Add Receipt to Queue**
3. Pick material, fill Quantity (200), Supplier, **Expiry_Date** (e.g., 2027-06-11)
4. Lot_Number: leave blank (system will generate `LOT-20260611-<SAP>`) OR type the supplier's lot ID
5. PR_Number: link if applicable
6. Click **Add to Receipt Queue ⬇️**
7. Click **📨 Submit to HOD for Approval** when done staging

### Use Case 3: Found 5 extra units of an item (physical count > system)

1. Entry Log → 🧮 Stock Count tab
2. Pick the item from the dropdown
3. Read the System Qty card (e.g., "95")
4. Counted Qty: **100**
5. The variance banner turns green: "➕ Found 5 extra"
6. Reason: **➕ Miscount — found extra**
7. Notes: e.g., "Found in box behind shelf 3"
8. Click **📤 Submit Count for HOD Approval**

HOD will approve in HOD Portal → 🧮 Adjustments. After approval, your live stock matches reality and a synthetic receipt row of +5 is posted to the ledger.

### Use Case 4: Issue a tool to a borrower

1. Entry Log → 🔄 Returnable Items tab
2. ➕ Issue a Returnable Item
3. Material: **Torque Wrench 1/2"**, UOM: **Pcs**, Qty: **1**
4. Borrower Name, WhatsApp Number, Expected Return: today 06:15 PM
5. Click **Issue Item 📤**

If the item is not returned by 06:15 PM, the borrower (and you) get a WhatsApp alert.

### Use Case 5: FEFO override (the right bin is blocked)

1. Entry Log → Consumption Log → pick material with 2+ open lots
2. Scroll to FEFO panel — note the suggested lot (earliest expiry)
3. Open **🔄 Pull from a different lot** expander
4. Pick a different lot from the dropdown
5. Type a reason ≥ 5 chars (e.g., "FEFO bin blocked by pallet, clearing 1700")
6. Amber confirmation banner appears
7. Fill quantity + other fields
8. Click **Add to Grid ⬇️**

Your HOD gets a real-time WhatsApp alert documenting the override.

## 4.8 Store Keeper — FAQ

**Q: I can't see Live Dashboard, HOD Portal, or Reports. Did I lose access?**
A: No — Store Keepers only have access to Entry Log by design. If you need to view stock, ask your Supervisor or HOD.

**Q: I submitted to HOD by mistake. Can I cancel?**
A: Not directly. Contact your HOD — they can reject the row in their EOD Commit review.

**Q: The "Add to Grid" button is disabled or the form rejects my entry.**
A: Check three things: (a) is quantity > current stock? (b) is the expiry override needed but unchecked? (c) are all required `*` fields filled?

**Q: I tried to issue 20 but the system says only 15 available.**
A: Believe the system. Either physically recount, or if there is genuinely 20 and the system disagrees, use the 🧮 Stock Count tab to log a +5 adjustment.

**Q: I don't have a barcode scanner. How do I quick-find items?**
A: Type a partial SAP code or description in the selectbox — it has type-ahead search. Or use the Recently-scanned pills if you've used the item recently.

**Q: WhatsApp alerts aren't reaching the HOD when I submit.**
A: HOD's phone number must be set in their user profile. Tell the HOD to update it via Admin (or check with Admin directly).

**Q: My borrowed item is overdue but I haven't received an alert.**
A: WhatsApp alerts are sent by a background worker (`whatsapp_worker.py`) — check with Admin that it's running.

**Q: I uploaded an OCR image and it extracted wrong items.**
A: The OCR review grid lets you fix every row before staging. Pick the correct material from the suggested-matches dropdown, or type it manually.

**Q: How do I see my recent activity?**
A: Submitted/approved items don't show on your screen. Ask HOD or Admin to filter the audit log by your username.

---

# 5. Supervisor Manual

The Supervisor monitors a single site's stock, generates reports, and provides oversight. They cannot approve transactions (that's HOD).

## 5.1 Pages visible

- 📦 Live Dashboard
- 📝 Entry Log (same interface as Store Keeper — see §4)
- 📊 Reports

## 5.2 Live Dashboard

The Live Dashboard is the at-a-glance view of every catalogue item with current stock, value, and status.

### 5.2.1 Brand header

`render_brand_header("Live Warehouse Stock Dashboard")` — gold subtitle + today's date.

### 5.2.2 Hero metric strip (4 cards)

| Card | Source | Tone logic |
|---|---|---|
| **Catalogue items** | Count of rows in `inventory` | Neutral |
| **Total stock value** | `cached_total_inventory_value()` (Sum of Current_Stock × Unit_Cost, all sites) — formatted as `SAR 1,234` / `SAR 1.2M` | Neutral. Delta: "standard cost · all sites" or "set Unit_Cost in Admin → DB Editor" if 0 |
| **Below minimum** | Count from `cached_low_stock_items()` | Green=0, Amber<10, Red>=10. Delta: "all healthy" or "needs reorder" |
| **Expiring / expired** | Count from `cached_short_dated_stock()` | Green=0, Amber<10, Red>=10. Delta: "shelf-life clear" or "review HOD Portal" |

### 5.2.3 🤖 Ask in plain English (AI search) — expander (if AI_ENABLED)

If Ollama is running locally with the `qwen2.5-coder:7b` model:

| Element | Purpose |
|---|---|
| **Your question text input** | Natural language query, e.g., "items below minimum at site B" |
| **Search button** | Translates to SQL via local LLM, runs read-only |
| **Clear button** | Resets the panel |
| **Result table** | Returned rows |
| **"Show SQL the AI generated" expander** | Shows the safe SELECT the LLM produced |

If Ollama is NOT running: an amber card displays setup instructions (`ollama serve`).

### 5.2.4 Burn alert banner

Appears at the top of the table area when there are items burning to zero within the configured window (default 7 days). Color-coded amber/red.

### 5.2.5 Main inventory grid (AgGrid)

| Column | Source | Notes |
|---|---|---|
| SAP_Code | `inventory.SAP_Code` | Primary key |
| Equipment_Description | `inventory.Equipment_Description` | Material name |
| UOM | `inventory.UOM` | Unit of measure |
| Total_Returned | Computed | Sum from `returns` table |
| Current_Stock | Computed identity math | `Received - Consumed - Returned` |
| Minimum_Qty | `inventory.Minimum_Qty` | Reorder threshold |
| Unit_Cost | `inventory.Unit_Cost` | Set via Admin → DB Editor |
| Stock_Value | Computed | `Current_Stock × Unit_Cost`, rounded 2 dp |
| **Status** | Computed badge | OK / Low / Below Min / Empty with colored pill via `STATUS_BADGE_JS` |

You can sort by any column — sorting by `Stock_Value` shows your biggest SAR exposure first.

### 5.2.6 Expanders below the grid

| Expander | Content |
|---|---|
| **📉 Stock vs Minimum Threshold** | Horizontal bar chart, sorted by criticality. Each bar = item; gold line markers = minimum threshold. Color: red=empty, amber=below min, orange=close, green=ok. Capped at 20 items. |
| **🔥 Burn Rate Forecast (30-Day)** | Plotly chart with daily-burn-rate bars + vertical line at the 30-day alert threshold. Colors: red <10 days, amber <30, green >30. |
| **📊 Top Consumed Items** | Bar chart of top 10 items by 30-day consumption, blue→gold gradient. |

## 5.3 Reports

Supervisor sees the full Reports module but with site scope locked to their site (cannot pick "All Sites"). See §8 for the full Reports reference.

## 5.4 Supervisor — Use Cases

### Use Case 1: Daily morning check

1. Log in → Live Dashboard
2. Read the 4 hero cards — note any below-minimum or expiring items
3. Sort grid by Stock_Value descending → check no over-stocked items
4. Sort by Status → spot Empty / Below Min items
5. Open 🔥 Burn Rate Forecast expander → check the next 30 days

### Use Case 2: Generate end-of-month report for management

1. Reports → 📊 Generate Report
2. Pick **📅 Monthly Summary**
3. From date: 1st of month; To: today
4. Site: locked to your site
5. Format: **PDF**
6. Click **▶ Generate Report**
7. Review the preview (includes SAR-value columns: Issued_Value_SAR, Received_Value_SAR, Closing_Value_SAR)
8. Click **↓ Download PDF**

### Use Case 3: Investigate why an item ran out

1. Live Dashboard → search the item (or click its row)
2. Open 🔥 Burn Rate expander — see the daily rate trend
3. Reports → **📈 Burn Rate Analysis** → date range last 30 days → generate
4. Filter the result table for the SAP code → see daily breakdown
5. Reports → **📋 Daily Consumption** → narrow down which Work_Type drove the spike

## 5.5 Supervisor — FAQ

**Q: I can only see my own site. Why can't I see other sites' stock?**
A: By design — site isolation. Only Admin sees all sites. If you need to know another site's stock for a transfer, ask your HOD to file a Cross-Site Request.

**Q: Total stock value shows SAR 0. What's wrong?**
A: Items have no `Unit_Cost` set. Ask Admin to enter costs in Admin → Master DB Editor → inventory table.

**Q: AI search says "Ollama not running."**
A: Local AI is optional. Admin must install Ollama and pull the `qwen2.5-coder:7b` model. Without it, the system still works — you just type into the standard grid filters.

**Q: The Stock_Value column is empty for some items.**
A: Those items have `Unit_Cost = 0`. The valuation is correct — they're tracked in qty but not in money.

**Q: I can't approve any transactions.**
A: Correct. Supervisors monitor; HODs approve. Talk to your HOD.

**Q: Can I export the Live Dashboard grid?**
A: Yes — AgGrid has a built-in CSV export (right-click the grid). For a formal report, use Reports → Generate.

---

# 6. HOD (Head of Department) Manual

The HOD owns their site's inventory ledger. Every transaction flows through their approval. The HOD Portal has **15 tabs** as of v3.0 — the original 13 (covering EOD, Cross-Site, Burn Rate, Receipts, Returns, Adjustments, PRs, Shelf-Life, Notifications, My Requests, Site Config, DOC, QR Approval) plus two new procurement-chain tabs: 🚚 DN Approvals and 🚚 In-Transit. Existing tabs are unchanged; the new tabs are documented in §6.15 and §6.16 below.

## 6.1 Pages visible

- 📦 Live Dashboard (see §5.2)
- 📝 Entry Log (see §4 — HODs can also stage entries)
- 📋 HOD Portal — **detailed below**
- 📊 Reports (see §8)

## 6.2 HOD Portal overview

### 6.2.1 Page header

`render_brand_header_hod("HOD Management Portal")` — purple subtitle + today's date.
- **Page title:** "📋 HOD Portal" + "Managing Site: <SITE_ID>"

### 6.2.2 Hero metric strip (4 cards)

| Card | Source | Notes |
|---|---|---|
| **Site stock value** | `cached_total_inventory_value(site_id)` | Delta: `<SAR>` consumed (30d) — site-scoped |
| **Below minimum (site)** | `cached_low_stock_items(site_id)` count | Green/amber/red |
| **Expiring / expired** | `cached_short_dated_stock(site_id)` count | Green/amber/red |
| **Pending receipts to approve** | Count from `pending_receipts` where `status='pending_hod'` at your site | Neutral or amber |

### 6.2.3 Tab strip (10 tabs)

1. 📤 EOD Commit
2. 🌐 Cross-Site
3. 📈 Burn Rate
4. 📬 Pending Receipts
5. 🧮 Adjustments
6. 📋 Purchase Requests
7. 📥 Receive Material
8. ⚠️ Shelf-Life
9. 🔔 Notifications
10. ✅ My Requests

---

## 6.3 HOD Portal → 📤 EOD Commit

The single most consequential action in the system: committing the day's staged consumption to the permanent ledger.

### 6.3.1 Top section

| Element | Purpose |
|---|---|
| **Header** | "📤 End-of-Day Commit (<SITE>)" with intro caption |

### 6.3.2 4-card stat strip

| Card | Source |
|---|---|
| 📋 Total entries | Total pending rows at your site |
| ⏳ Pending | Status = pending |
| ⚠️ Flagged | Status = flagged |
| ✅ Approved | Status = approved |

### 6.3.3 Filter pills

| Pill | Action |
|---|---|
| All / Pending / Flagged / Approved / Rejected | Filters the table below to that status |

### 6.3.4 Top action bar

| Button | Action |
|---|---|
| **✅ Approve All Pending** | Sets every pending row to `status='approved'`. Disabled when count = 0. |
| **📤 Commit EOD to Master** | Opens the type-COMMIT modal. See §6.3.7 |

### 6.3.5 Main pending-issues table

Custom dark-themed HTML table showing **every column from the staging row** (joined with inventory for descriptions):

| Column | Source |
|---|---|
| Date | pending_issues.Date |
| SAP | pending_issues.SAP_Code |
| Mat Code | inventory.Material_Code |
| Material | inventory.Equipment_Description |
| UOM | inventory.UOM |
| Qty | pending_issues.Quantity |
| Work Type | pending_issues.Work_Type |
| PR | pending_issues.PR_Number |
| Tank | pending_issues.Tank_No |
| Serial | pending_issues.Serial_No |
| Issued By | pending_issues.Issued_By |
| Issued To | pending_issues.Issued_To |
| Remarks | pending_issues.Remarks |
| **Status** | Colored pill (pending / flagged / approved / rejected / committed) |

The table also reflects `Lot_Number` and `FEFO_Override` columns when present (visible by editing the row).

### 6.3.6 Row Actions panel (per-row Approve/Reject)

Below the table, top 20 actionable rows render as cards:

| Element | Purpose |
|---|---|
| **Card line 1** | SAP · Material Code · Material name · **Qty UOM** |
| **Card line 2** | Date · Work Type · PR · Tank · Serial · By · To · Remarks (only fields with values) |
| **✓ button** | Approve this single row (sets `status='approved'`) |
| **✗ button** | Reject this single row (sets `status='rejected'`) |

### 6.3.7 EOD Commit confirmation modal (type-COMMIT dialog)

When you click **📤 Commit EOD to Master**:

**Step 1 — pre-flight: negative-stock guard**

The system calls `validate_eod_no_negative_stock()`. If any items would push stock below zero:

| Element | Purpose |
|---|---|
| **🛑 Red error banner** | "Cannot commit — N item(s) would go negative" |
| **Violation table** | SAP · Material · Current Stock · Trying to Consume · Deficit |
| **💡 Hint banner** | "Common fixes: receive inbound stock first, raise a stock adjustment, or reduce the consumption to fit available stock." |
| **Close button** | Cancels — fix the staging rows and try again |

**Step 2 — confirm (only if no violations)**

| Element | Purpose |
|---|---|
| **Warning banner** | "You are about to commit N pending row(s)..." |
| **Type COMMIT text input** | Must type exactly `COMMIT` to enable the button |
| **Cancel button** | Abort |
| **Confirm Commit button (red)** | Calls `commit_eod()` — moves rows to `consumption`, deletes from `pending_issues`, busts caches, queues a post-EOD low-stock alert to the HOD if applicable. |

After commit: 🎈 balloons animation + success toast.

### 6.3.8 Flagged-items banner

If there are flagged rows: amber banner appears below the table reminding the HOD to verify with the store keeper before committing.

---

## 6.4 HOD Portal → 🌐 Cross-Site

For requesting material from another branch.

### 6.4.1 Top section

| Element | Purpose |
|---|---|
| **Intro caption** | Explains the > 5-item escalation rule |

### 6.4.2 Single-target inquiry flow (left/right columns)

**Left column:**

| Element | Purpose |
|---|---|
| **Select Target Branch selectbox** | List of all sites except yours |
| **Select Material selectbox** | Type-ahead search across inventory |
| **Quantity Needed number input** | Min 1 |
| **Justification / Notes textarea** | Why you need this transfer |

**Right column:**

| Element | Purpose |
|---|---|
| **📊 Live Stock at <target> header** | Shows availability at the target site |
| **Available Quantity metric** | Live stock at target |
| **Suggested Transfer Qty metric** | `min(requested, available)` |
| **➕ Add to List button** | Adds to the cart |

### 6.4.3 🛒 Your Request Cart section

| Element | Purpose |
|---|---|
| **Cart table (styled HTML)** | Columns: Target Site · SAP · Description · Qty · Notes |
| **📨 Submit All Requests to Admin button** | Creates a `requests` row per cart item, queues WhatsApp to all admins, **AND** (if >5 items) escalates to the target-site HOD via separate WhatsApp + audit log |
| **🗑️ Clear List button** | Empties the cart |

### 6.4.4 📥 Incoming Cross-Site Requests panel (bottom)

Where a HOD sees requests **addressed to their site** from other HODs.

| Element | Purpose |
|---|---|
| **Intro caption** | "Requests addressed to <site>" |
| **Incoming table (styled HTML)** | From · SAP · Material · Qty · By · Notes · When |
| **Per-row Approve / Reject controls** (top 10) | ✓ Approve and ✗ Reject buttons |
| Approval action | Calls `update_request_status(status='approved')` + audit |
| Rejection action | Same with `status='rejected'` |

---

## 6.5 HOD Portal → 📈 Burn Rate

Predictive analysis of which items will run out and when.

### 6.5.1 Compact horizontal-bar chart (top 10)

A sleek card with:
- **Header row:** "Monthly Consumption · Top 10" + legend chips (red=High / amber=Mid / green=Low)
- **Per-row line:** material name (truncated) · gradient bar (color reflects burn intensity proportional to max) · monthly figure with UOM · days-remaining badge (red if ≤7d, amber if ≤30d, green if >30d)

### 6.5.2 Burn alert banner (above the Plotly chart)

Auto-renders if items are projected to hit zero within the alert window.

### 6.5.3 Full Plotly burn rate chart

Daily-burn-rate bars with vertical line at 30-day alert threshold. Color-coded.

### 6.5.4 Detailed Forecast Table (AgGrid)

Columns: SAP_Code · Equipment_Description · UOM · Current_Stock · Daily_Burn_Rate · Days_Remaining

Sortable + exportable.

---

## 6.6 HOD Portal → 📬 Pending Receipts

Approve or reject receipts staged by Store Keepers.

### 6.6.1 Header + count banner

Amber banner: "⏳ N pending receipt(s) require your approval before stock levels update."

### 6.6.2 ✅ Approve All button

Bulk: calls `commit_pending_receipts(site_id, username)` which:
1. Iterates every pending receipt
2. Calls `process_receipt_delivery` (which creates lots if Expiry_Date set)
3. Posts to `receipts` table
4. Auto-closes PR if fulfilled
5. Deletes the staged rows
6. Busts caches, audit-logs, success toast

### 6.6.3 Receipt table

Columns: Date · SAP · Equipment_Description · UOM · Quantity · Supplier · PR_Number · Site_ID · **Status pill**

### 6.6.4 Per-row Reject controls (top 10)

| Element | Purpose |
|---|---|
| **Card line** | SAP · Qty UOM · Supplier |
| **✗ Reject button** | Soft-reject (row stays as `status='rejected'`), preserves audit, audit-logs with reason |

---

## 6.7 HOD Portal → 🧮 Adjustments

Approve physical-count reconciliations submitted by Store Keepers from the Entry Log → Stock Count tab.

### 6.7.1 3-card stat strip

| Card | Source |
|---|---|
| ⏳ Pending | Count of pending adjustments at your site |
| ➖ Shortfalls | Variance < 0 count |
| ➕ Surpluses | Variance > 0 count |

### 6.7.2 Pending adjustments table

Columns: # · SAP · Material · UOM · System · Counted · **Variance** (red for negative, green for positive) · Reason · By · When

### 6.7.3 Per-row Approve/Reject

Below the table:

| Element | Purpose |
|---|---|
| **Card line 1** | SAP · Material · System X → Counted Y · Variance |
| **Card line 2** | Reason label · submitted by · notes |
| **✓ button** | Calls `approve_stock_adjustment(adj_id, approver)`. **Atomically posts a synthetic ledger row** — if variance < 0, inserts into `consumption` with Work_Type=`STOCK_ADJUSTMENT`; if > 0, inserts into `receipts` with Supplier=`STOCK_ADJUSTMENT`. The `posted_txn_ref` ('C:rowid' or 'R:rowid') links back to the adjustment for full audit. |
| **✗ button** | Soft-reject. Row stays as `status='rejected'` for audit. |

### 6.7.4 Recent Adjustments History (last 30)

Columns: # · SAP · Material · Variance · Reason · **Status pill** · Submitted By · Approved By · Ledger Ref

---

## 6.8 HOD Portal → 📋 Purchase Requests

Manage Purchase Requests (PRs) for your site.

### 6.8.1 ➕ Create New PR (manual entry) — expander

| Field | Purpose |
|---|---|
| **PR Number** | Free text, required (e.g., `3001234567`) |
| **Material selectbox** | Picks from inventory; auto-fills Material_Code, Material_Name, UOM via blue-tinted info card |
| **Requested Qty** | Min 0.01 |
| **Preferred Supplier** | Optional |
| **Estimated Cost (SAR)** | Optional |
| **UOM** | Auto-filled, editable |
| **Notes** | Optional |
| **📋 Create PR Draft button** | Calls `insert_manual_pr(...)`. Sets `status='open'`, `workflow_state='draft'` |

### 6.8.2 📄 Upload PR PDF (auto-extract via pdfplumber) — expander

| Element | Purpose |
|---|---|
| **File uploader (PDF only)** | Upload Purchase Request PDF |
| **Process Upload button** | Calls `process_pr_pdf()` — extracts PR number + Material Codes (GI-XXXXXXX pattern), matches to inventory's `Material_Code`, inserts pr_master rows |
| **Result banner** | Green success or amber warning (if some materials unmatched) |

### 6.8.3 Current PRs table

Columns: PR No. · SAP · Material · UOM · Qty Req · Qty Pending · Supplier · Est. SAR · **Workflow pill**

`Pending_Qty = Requested_Qty − SUM(receipts where PR_Number matches)`

### 6.8.4a 🚚 Submit PR(s) to Logistics Portal (NEW in v3.0)

A new expander sits between the PR list and the email/PDF block. It opens the procurement-chain path — handing off your PR to the in-app Logistics queue instead of (or in addition to) the legacy email path.

| Element | Purpose |
|---|---|
| **Multi-select** | All open PRs at your site that haven't yet been submitted to Logistics |
| **📨 Submit Selected to Logistics button** | Calls `submit_pr_to_logistics()` for each picked PR. Their `logistics_status` flips to `submitted` and the row appears in Logistics Portal → 📥 Incoming PRs. A `pr_submitted_to_logistics` notification fires to the Logistics role inbox + WhatsApp (if enabled). |

The legacy email path (§6.8.4) **still works** — your team can use it for direct-to-vendor relationships that don't go through central Logistics. Both paths coexist. The email path is marked for future deprecation once procurement chain adoption reaches the agreed threshold.

### 6.8.4 📧 Notify Logistics section

When at least one PR has `status='open'`:

| Element | Purpose |
|---|---|
| **Select PR for Actions selectbox** | Pick from open PRs |
| **📧 Draft Outlook Email button** | Opens email client with HTML table of pending items. Mac: opens Mail.app via AppleScript with formatted HTML table. Windows: opens Outlook via COM with HTMLBody. Fallback: mailto: with monospace plain-text table. |
| **📥 Download PR PDF button** | Generates a PDF record with columns: Material Code · Description · Req. Qty · **Received** · **Pending** · Status. Auto-saved metadata: Site, Generated By, Timestamp. |

---

## 6.9 HOD Portal → 📥 Receive Material

For HOD-direct receipts (when a HOD logs a delivery themselves, e.g., direct purchases or HQ deliveries).

### 6.9.1 PR linker

| Element | Purpose |
|---|---|
| **🔗 Link to Open PR selectbox** | Filters the material list to items in that PR, or "None (Direct Purchase)" for free-form |

### 6.9.2 Receipt form (st.form, clears on submit)

| Field | Type | Purpose |
|---|---|---|
| **Select Material** | Selectbox | From inventory or filtered to PR items |
| **Quantity Received** | Number (min 0.1) | Units arrived |
| **Delivery Date** | Date (today) | When |
| **Expiry Date** | Date (optional) | Lot expiry trigger |
| **Logistics extras** | Text inputs | All non-system columns on `receipts` (Vehicle_No, Driver_Name, DN_No, Supplier, Remarks, etc.) |
| **💾 Save Receipt button** | Calls `process_receipt_delivery()` directly (no staging) — instantly posts to `receipts` AND creates a `lots` master row if Expiry_Date is set. Auto-closes PR if fulfilled. Busts caches. |

### 6.9.3 📋 Receipt History (last 50)

Below the form, an AgGrid showing recent receipts for this site: rowid · Date · SAP · Material · Quantity · Supplier · PR_Number · Expiry_Date.

---

## 6.10 HOD Portal → ⚠️ Shelf-Life

Lots at risk of expiry.

### 6.10.1 3-card stat strip

| Card | Source |
|---|---|
| 🔴 Expired | Lots with days_left < 0 |
| 🟠 Critical (≤30d) | Days_left 0–30 |
| 🟡 Warning (≤90d) | Days_left 31–90 |

### 6.10.2 Action-required banner

When `expired_n > 0`: red banner instructing physical isolation + disposal.

### 6.10.3 Shelf-life table (top 30, sorted by days-left ascending)

Columns: SAP · Material · Lot · Qty · Expiry (color-coded) · **Days Left** · **Status pill**

### 6.10.4 🗑️ Log Disposal button

Bulk-logs an audit event for the expired count. Note: this does NOT post a stock adjustment automatically — for proper inventory reduction, use the Adjustments flow with reason `expired_disposal`.

---

## 6.11 HOD Portal → 🔔 Notifications

Manual WhatsApp sends + alert threshold tuning.

### 6.11.1 📤 Send Manual WhatsApp card

| Field | Purpose |
|---|---|
| **Recipient phone number** | E.g., `+966 5X XXX XXXX` |
| **Message textarea** | Up to several hundred characters |
| **📱 Send WhatsApp button** | Queues to `whatsapp_queue` with status='pending'; the background worker picks it up. Audit-logs `MANUAL_WHATSAPP`. |

### 6.11.2 ⚙️ Alert Thresholds card

3 sliders writing to `app_settings`:

| Slider | Range | Default | Stored key |
|---|---|---|---|
| Low stock alert (days of supply) | 1–60 | 5 | `low_stock_days` |
| Burn-rate warning (days remaining) | 1–60 | 7 | `burn_alert_days` |
| Expiry warning (days before) | 1–120 | 30 | `expiry_warn_days` |

**💾 Save Thresholds button** — persists + audit-logs `UPDATE_THRESHOLDS`.

> Note: The Notification Log table moved to Admin Portal → WhatsApp Console for global visibility.

---

## 6.12 HOD Portal → ✅ My Requests

Outbound cross-site requests YOU have made.

### 6.12.1 Outbound requests table (styled HTML)

Columns: # · To Site · SAP · Qty · **Status pill** · Created

### 6.12.2 Mark Incoming Transfers as Received

When at least one request has `status='approved'`:

| Element | Purpose |
|---|---|
| **Select Approved Request selectbox** | List of approved request IDs |
| **Confirm Delivery Received button** | Calls `update_request_status(status='fulfilled')` + busts caches. This is where you confirm the transfer physically arrived. |

---

## 6.15 HOD Portal → 🚚 DN Approvals (NEW in v3.0)

This is your approval queue for Delivery Notes inbound to your site. The Warehouse has prepared them, Logistics has approved the delivery date — now you confirm the **content** (what's actually arriving, in what qty).

### 6.15.1 Empty state

If nothing's pending: an empty-state card reads *"No DNs awaiting your approval — They appear here after Logistics signs off the delivery date."* No action needed.

### 6.15.2 Per-DN cards

Each pending DN renders as its own bordered container with two columns:

**Left column:**
- **Header line:** `DN <number> · PO <number> · From Warehouse <id>`
- **Subline:** DN Date · Vehicle No · Driver Name
- **View lines expander:** Read-only preview of every line item (Material_Code, Description, Qty, UOM, rl_bl_family, Lot_Number, Expiry_Date, Remarks)

**Right column (Decide popover):**
- **Notes textbox** (required if rejecting)
- **✅ Approve button** (primary) — flips DN to `pending_sk` and mirrors lines into `pending_receipts` so SK sees them
- **❌ Reject button** — flips DN to `rejected` with your reason; Warehouse gets pinged to redo

### 6.15.3 When to approve

- DN qty matches what your site needs (matches the PR line you originally raised)
- RL/BL family is correct
- Lot + expiry are acceptable (not over-expiry)
- The originating PO is the right one (not a misroute)

### 6.15.4 When to reject

- Wrong qty (e.g. site asked for 100, DN says 200 — too much exposure)
- Wrong material (the Material_Code doesn't match the PR you raised)
- Lot has insufficient remaining shelf life
- Vehicle / driver details suggest a routing problem

**Always include a clear rejection reason.** The Warehouse user sees the reason verbatim in their bell inbox and on the bounced DN. Sloppy reasons cost a rebuild cycle.

---

## 6.16 HOD Portal → 🚚 In-Transit (NEW in v3.0)

A read-only window onto the procurement chain for your site. Use this tab when a user asks *"when is X arriving?"* or *"why didn't Y arrive yesterday?"*. Three sub-tabs.

### 6.16.1 Sub-tab: 🚚 Active in-transit

KPI strip at the top with counts per pipeline state:
- **At Logistics** — DN drafted by Warehouse, waiting on Logistics date approval
- **Logistics approved** — date confirmed, waiting on YOU
- **Awaiting my approval** — same as Logistics approved (highlighted gold)
- **Pending SK receipt** — you've approved, SK has it in their tab

Below the KPI strip, each in-transit DN renders as a card:

| Element | Purpose |
|---|---|
| **DN header** | `DN <number>` (gold) + RL/BL chip (orange for RL, purple for BL) |
| **Subline** | `PO <number>` · `Warehouse <id>` · `ETA <date>` · `<line count>` line(s), `<total qty>` units |
| **Status pill (right)** | Colour-coded pipeline state |
| **View lines expander** | Read-only line preview |
| **🔁 Request reschedule popover** | Date picker (defaults to ETA + 3 days, min=today) + reason textarea + Submit button. Submits to Logistics. |

The reschedule UI is deliberately frictionless:
- Date is pre-filled so a quick "+3 days" submission is one click
- `min_value=today` prevents accidentally picking yesterday
- Same-date submission warns instead of wasting a round-trip
- Caption "📨 Goes to Logistics" makes the destination unambiguous

### 6.16.2 Sub-tab: 🔁 My reschedule requests

KPI strip: Pending / Approved / Rejected counts.

Custom table showing your full reschedule history for THIS site:

| Column | Source |
|---|---|
| PO No. | the linked PO |
| DN No. | the linked DN (if any) |
| From | current_date when you raised the request |
| Requested | the new date you asked for (gold, monospace, with arrow) |
| Reason | your justification (tooltip-truncated) |
| Status | pill — pending / approved / rejected |
| Decided by | Logistics user who handled it |
| Notes | decision notes from Logistics |

### 6.16.3 Sub-tab: 🛑 Force-closures affecting me

Read-only audit table showing every PR / PO / line that Logistics force-closed, scoped to your site. Three-way fallback join means closures on records with NULL Site_ID still appear correctly via their PR or PO parent.

| Column | Source |
|---|---|
| Type | "PR closed" / "PO closed" / "Line closed" badge |
| Target | The closed ref (PR number / PO number / line id) |
| PR No. | Linked PR (if applicable) |
| PO No. | Linked PO (if applicable) |
| Reason | Logistics' force-close reason |
| Closed by | Logistics user who closed it |
| When | Timestamp |

50 most recent shown.

---

## 6.13 HOD — Use Cases

### Use Case 1: Approve and commit the day's consumption

1. HOD Portal → 📤 EOD Commit
2. Review the 4-card stat strip
3. Sort/filter using the filter pills
4. For each row: read the full detail card (line 1 = essentials, line 2 = all fields)
5. Click ✓ to approve or ✗ to reject individual rows
6. Optionally click ✅ Approve All Pending for a clean batch
7. Click 📤 Commit EOD to Master
8. **If pre-flight blocks:** read the violation table, close, fix the offending rows (reduce qty, drop, or receive stock first), then re-open
9. **If pre-flight passes:** type `COMMIT` exactly, click Confirm Commit
10. Watch the 🎈 balloons. 🎉

### Use Case 2: Approve a Store Keeper's physical count adjustment

1. HOD Portal → 🧮 Adjustments
2. Read the pending table — variance and reason are color-coded
3. For each adjustment, decide: did the Store Keeper count accurately?
4. ✓ Approve → system posts a synthetic ledger row (receipt or consumption), live stock updates immediately
5. ✗ Reject → audit-logged; Store Keeper sees a status update

### Use Case 3: Request material from another site

1. HOD Portal → 🌐 Cross-Site
2. Pick target site → pick material → see live availability
3. Add to cart, repeat
4. **If cart > 5 items:** an automatic WhatsApp escalation to the target HOD will fire on submit. Document why in Justification.
5. Click 📨 Submit All Requests to Admin
6. Watch the ✅ My Requests tab — when status flips to "approved", you can mark it as fulfilled once received

### Use Case 4: Approve an incoming bulk request from another HOD

1. HOD Portal → 🌐 Cross-Site → scroll to 📥 Incoming Cross-Site Requests
2. Review the request table
3. ✓ Approve → physical handoff begins
4. ✗ Reject → use when the request is unjustified or your stock is too tight

### Use Case 5: Raise a Purchase Request

**Option A: manual entry**
1. HOD Portal → 📋 Purchase Requests
2. ➕ Create New PR
3. Type PR Number, pick Material (auto-fills SAP, MatCode, UOM), Qty, Supplier, Est. Cost SAR, Notes
4. Click 📋 Create PR Draft

**Option B: PDF upload**
1. 📄 Upload PR PDF
2. Drop the PDF in
3. Click Process Upload
4. System auto-extracts PR number + items, creates pr_master rows

### Use Case 6: Notify logistics about a pending PR

1. HOD Portal → 📋 Purchase Requests
2. Select open PR in the email dropdown
3. 📧 Draft Outlook Email → Mail/Outlook opens with HTML table pre-filled
4. Review/send
5. Also: 📥 Download PR PDF for your records

## 6.14 HOD — FAQ

**Q: The EOD commit modal won't let me commit — keeps showing the violation table.**
A: Pre-flight is blocking because consuming the staged amount would create negative stock. Either: (a) reduce qty on the violating rows in the EOD table, (b) commit pending receipts first to top up stock, (c) raise a stock adjustment to correct any system-vs-physical discrepancy.

**Q: A Store Keeper claims they submitted but I don't see anything.**
A: Check the filter pills (you may be filtering by Approved/Rejected only). Switch to "All" or "Pending".

**Q: I approved an adjustment but the live stock didn't change.**
A: It should change within 30 seconds (cache TTL). Refresh the page. If it still doesn't, check audit log for the APPROVE_ADJUSTMENT entry to confirm the synthetic ledger row was posted.

**Q: I got a WhatsApp about a "bulk cross-site request" — what is it?**
A: Another HOD at a different site has requested more than 5 items from your site. Go to Cross-Site → 📥 Incoming to review.

**Q: My burn rate forecast looks wrong — items aren't appearing.**
A: Items with fewer than ~3 consumption events in the last 30 days have insufficient data and may be omitted. Increase the window via Settings if needed.

**Q: I can't see a receipt I just approved. Did it work?**
A: Approving via "Approve All" commits all pending receipts at once. Check the Live Dashboard for updated stock. The receipt now lives in `receipts` table, not `pending_receipts`.

**Q: The Outlook email button does nothing on my Mac.**
A: Make sure Mail.app or Outlook is installed and the system has permission to open it. The fallback is mailto: (plain-text monospace table) which works in any browser default mail handler.

**Q: PR PDF upload didn't recognize some items.**
A: The extractor matches material codes in the format `GI-XXXXXXX`. Items not in your inventory's `Material_Code` field will be flagged in the warning. Add the missing items to inventory first.

**Q: Cross-site request stuck in 'pending' — why isn't Admin approving?**
A: Admin sees all pending in Admin Portal → 📨 Pending Requests. If urgent, queue a WhatsApp from Notifications. Or for >5 items, the target-site HOD can also approve directly.

**Q: I want to see all FEFO overrides logged by my Store Keepers.**
A: Filter Admin → 📜 Audit Logs by action `FEFO_OVERRIDE`. Or query the `consumption` table for `FEFO_Override IS NOT NULL`.

---

# 7. Admin Manual

The Admin is the system owner. The Admin Portal has **11 tabs** as of v3.0 — the original 10 (Overview, Pending Requests, Global Sites, Users, Master DB Editor, Audit Logs, WhatsApp Console, Settings, Access Control, Reports & Bugs) plus the new **🚚 Logistics Oversight** tab. Admins do NOT see HOD Portal (intentional — Admin uses Admin Portal for cross-site work). Admins CAN see the Logistics Portal and Warehouse Portal as shadow access — when entering the Warehouse Portal, a sidebar dropdown lets the admin pick which warehouse to view as.

## 7.1 Pages visible

- 📦 Live Dashboard (global — sees all sites combined)
- 📝 Entry Log (can stage entries themselves)
- 🛡️ Admin Portal — **detailed below**
- 📊 Reports (with "All Sites" filter unlocked)

## 7.2 Admin Portal overview

### 7.2.1 Page header

`render_brand_header_admin(...)` — gold subtitle + pulse status chip (green "All systems operational" or amber "Degraded — see Overview" if global low-stock count > 20).

### 7.2.2 Hero strip (3 cards)

| Card | Source |
|---|---|
| **Sites managed** | Count of distinct sites |
| **Pending cross-site requests** | Count from `requests` where `status='pending'` |
| **Critical items (all sites)** | `low + expiry` count summed globally |

### 7.2.3 Tab strip (9 tabs)

1. 🖥️ Overview
2. 📨 Pending Requests
3. 🏢 Global Sites
4. 👥 Users
5. 🗄️ Master DB Editor
6. 📜 Audit Logs
7. 📱 WhatsApp Console
8. ⚙️ Settings
9. 🔑 Access Control

---

## 7.3 Admin Portal → 🖥️ Overview

System health at a glance.

### 7.3.1 4-card KPI strip (technical)

| Card | Source |
|---|---|
| 🗄️ **DB size** | File size of `gi_database.db` on disk (MB) |
| 👥 **Users** | Count of non-suspended users |
| 📊 **Total transactions** | `consumption + receipts` row count |
| 📜 **Audit events** | All-time count from `system_audit_log` |

### 7.3.2 4-card valuation strip (financial)

| Card | Source |
|---|---|
| 💰 **Total stock value** | `cached_total_inventory_value()` (all sites) |
| 🏭 **Biggest-value site** | `cached_value_by_site().iloc[0]` + share of total |
| 🔥 **30-day consumption value** | `cached_consumption_value(days=30)` |
| 📦 **Pending receipts value** | Placeholder showing source: `pr_master.Est_Cost_SAR` |

### 7.3.3 🔧 Service Health card

Per-service row with a pulse dot + status + note:

| Service | Up indicator | Source |
|---|---|---|
| SQLite Database | Always up if reached | WAL mode · DB size MB |
| WhatsApp Queue | If `whatsapp_queue` reachable | Pending count or "queue clear" |
| Ollama / AI | `OLLAMA_AVAILABLE` if AI_ENABLED | "ready" / "not reachable" |
| Mail / SMTP | Informational | "Outlook + mailto fallback" |

### 7.3.4 📊 Database Stats card

8 row counts:
- Inventory items
- Consumption rows
- Receipt rows
- Pending issues
- Pending receipts
- Open PR lines
- WhatsApp queue size
- Audit events

### 7.3.5 📋 Live Activity Feed (last 12)

Per-row card from `system_audit_log`:
- 🔴/🟡/🟢 severity icon (derived from action_type via `_severity_from_action()`)
- Timestamp (gold monospace)
- Action name
- Target table pill
- "<user> · <details>" line

---

## 7.4 Admin Portal → 📨 Pending Requests

Approve or reject cross-site material requests from HODs.

### 7.4.1 Pending requests data editor

Editable table with a `☑️ Select` checkbox column (all other columns disabled). Source: `get_pending_requests(status='pending')`.

### 7.4.2 Admin Notes text input

Optional for approve · **required for reject**. Free text passed to `update_request_status()`.

### 7.4.3 Action buttons

| Button | Action |
|---|---|
| **✅ Approve Selected** | For each selected row: looks up Material Code/Description, updates `requests.status='approved'`. Then for each unique requester: queues a WhatsApp with item list + admin notes. For each unique target site: queues a "TRANSFER ORDER" WhatsApp to the target HOD with packing instructions. |
| **❌ Reject Selected** | Same multi-row loop but sets `status='rejected'`. Notes are MANDATORY for rejection. WhatsApp queued to each rejected requester with reason. |

---

## 7.5 Admin Portal → 🏢 Global Sites

Cross-site inventory viewer (read-only).

| Element | Purpose |
|---|---|
| **Site selectbox** | "All Sites (Global)" or pick one |
| **Inventory dataframe** | Calls `cached_live_inventory(site_id=…)` |

Useful before approving a cross-site request to confirm the target site really has the stock.

---

## 7.6 Admin Portal → 👥 Users

Delegates entirely to `auth.render_user_management_tab()` which provides:

| Section | Purpose |
|---|---|
| **Pending registration requests** | Approve / reject self-registered users |
| **Active users table** | All users with role, site, phone |
| **Edit user form** | Change role, site, phone, suspend/activate |
| **Add user form** | Create user directly (Admin shortcut) |
| **Password reset action** | Generates temporary password, audit-logged |

---

## 7.7 Admin Portal → 🗄️ Master DB Editor

**This is the most powerful tab. Use with care.** Lets Admin view, edit, and modify any table's data and schema.

### 7.7.1 Table selector

| Element | Purpose |
|---|---|
| **Select Table dropdown** | Lists every user table in the SQLite DB. Includes new ones: `lots`, `stock_adjustments`, `bug_reports`, `app_settings`, etc. |

### 7.7.2 Action radio

3 modes:

#### Mode A: 📝 View / Edit Data

| Element | Purpose |
|---|---|
| **Row count caption** | "<N> rows in `<table>`" |
| **📄 Export as PDF button** | Generates `generate_universal_pdf()` of the whole table |
| **Data editor table** | Edit any cell. Password hashes are masked as `••••••••`. |
| **🏷️ Print Label checkbox column** | Only on `inventory` table — select items to print QR labels |
| **💾 Save Table Updates button** | **DELETE the table, INSERT_ALL from the edited dataframe.** Audit-logs `DB_EDIT`. Busts inventory + settings caches. |
| **🖨️ Generate QR Labels for Selected button** | Only on inventory — `generate_qr_labels_pdf()` for items where label checkbox is checked |
| **📥 Download QR Labels PDF button** | Appears after generation |

> ⚠️ **The Save action is destructive.** It deletes all rows and re-inserts. If anything fails mid-write you may lose data. Industry best-practice is to make ledger tables immutable; this tab bypasses that — Admin discretion required.

#### Mode B: ➕ Add New Entry

Form generation depends on the table:

- **For `users`**: warns to use User Management tab instead (safer)
- **For `receipts`**: opens the full Logistics Receipt form (Site, Open PR linker, Material, Qty, Date, Expiry, Supplier, Remarks). Posts via `process_receipt_delivery` → also creates lot if Expiry set.
- **For any "transaction table"** (has SAP_Code, isn't inventory): 2-section form — Section 1 picks material (shows MatCode + UOM info card), Section 2 dynamically generates inputs for every editable column.
- **For inventory itself**: dynamic 3-column form for all editable columns.

#### Mode C: ⚙️ Manage Columns

| Element | Purpose |
|---|---|
| **➕ Add Column section** | Column name text input + Add button → `ALTER TABLE ADD COLUMN <name> TEXT` |
| **✏️ Rename Column section** | Column dropdown + new name + Rename button → `ALTER TABLE RENAME COLUMN` |
| **🗑️ Drop Column section** | Column dropdown + Delete button → `ALTER TABLE DROP COLUMN` (SQLite supports this only on recent versions) |

> The `users` table is protected — schema changes are blocked here ("managed by auth.py").

---

## 7.8 Admin Portal → 📜 Audit Logs

Complete forensic record.

### 7.8.1 Filter row

| Filter | Source |
|---|---|
| **User selectbox** | DISTINCT usernames + "All Users" |
| **Action selectbox** | DISTINCT action_types + "All Actions" |
| **Target selectbox** | DISTINCT target_tables + "All Targets" |
| **Limit selectbox** | 50 / 100 / 500 / 1000 |

### 7.8.2 Search text input

Substring match across details + username + action_type.

### 7.8.3 Audit table (styled HTML)

Columns: 🔴/🟡/🟢 severity · Timestamp · User · **Action** (color-coded by severity) · Target pill · Detail (truncated, tooltipped)

**Severity heuristic** (from action_type):
- **Critical (🔴):** any action containing FAIL, REJECT, DELETE, PURGE, EMERG, ROLLBACK, RESET, DESTRUCTIVE
- **Warning (🟡):** SUSPEND, REVOKE, ROTATE, FLAG, DOWNGRADE, WARNING
- **Info (🟢):** everything else

Expired-row red tint on critical events.

---

## 7.9 Admin Portal → 📱 WhatsApp Console

Outbound message queue + manual sends + thresholds + event mapping.

### 7.9.1 Queue stats strip (4 cards)

| Card | Source |
|---|---|
| ✅ Sent | `whatsapp_queue` where status='sent' |
| ⏳ Pending | status='pending' |
| ⚙️ Processing | status='processing' |
| ❌ Failed | status='failed' |

### 7.9.2 📤 Send Manual WhatsApp card

| Field | Purpose |
|---|---|
| **Recipient phone** | E.g., `+966 5X XXX XXXX` |
| **Message textarea** | The message to send |
| **📱 Send WhatsApp button** | Queues to `whatsapp_queue` + audit-logs `MANUAL_WHATSAPP` |

### 7.9.3 ⚙️ Alert Thresholds card (global)

Same 3 sliders as HOD Notifications (low_stock_days / burn_alert_days / expiry_warn_days). Persists to `app_settings`. Admin's value is the global default.

### 7.9.4 ⚡ Event → Recipient (current wiring) — read-only summary

A reference table showing **what auto-triggers exist in the codebase**:

| Event | Recipient | Role |
|---|---|---|
| Issue staging submitted | Site HOD | hod |
| Pending receipt submitted | Site HOD | hod |
| EOD committed | Site HOD | hod |
| Cross-site request created | All admins | admin |
| Cross-site bulk (>5 items) | Target site HOD | hod |
| Cross-site request approved | Requesting HOD | hod |
| Cross-site request rejected | Requesting HOD | hod |
| Returnable item overdue | Store Keeper | store_keeper |
| New access request | All admins | admin |
| Access request approved | Requesting user | store_keeper |
| Post-EOD low stock alert | Site HOD | hod |

### 7.9.5 📋 Outbound Queue Log (last 80)

Styled HTML table from `get_whatsapp_log(limit=80)`:
**Status pill** · Recipient (monospace) · Message (truncated with tooltip) · Queued at · Sent at

---

## 7.10 Admin Portal → ⚙️ Settings

### 7.10.1 📋 Dropdown Manager — Work Types (expander, expanded by default)

| Element | Purpose |
|---|---|
| **Current Work Types caption** | Comma-separated list from `system_settings` |
| **New Work Type Name text input** | Add a new option |
| **Add to Dropdown button** | INSERT into `system_settings` (category='Work_Type') + busts settings cache |

### 7.10.2 🔧 Maintenance Mode card

| Element | Purpose |
|---|---|
| **Enable maintenance mode toggle** | Persists to `app_settings.maintenance_mode='1'` |
| **Status caption** | "ACTIVE — Non-admin sessions will be told to come back later" or "Off" |

Audit-logs `TOGGLE_MAINTENANCE`. (Enforcement at login is in `auth.py` and `main.py`.)

### 7.10.3 🗄️ Database Backup card

| Element | Purpose |
|---|---|
| **Last manual backup caption** | From `app_settings.last_backup_at` |
| **💾 Backup Now button** | Calls `shutil.copy2()` of `gi_database.db` into `backups/gi_database_YYYYMMDD_HHMMSS.db`. Updates last_backup_at + audit `DB_BACKUP`. |

### 7.10.4 🏭 Site Management

| Element | Purpose |
|---|---|
| **Sites table (styled HTML)** | Site Name · Code (first 4 chars upper) · Users count · Status (always "Active") |
| **➕ Add New Site expander** | Type name + button → INSERT into `system_settings` (category='Site') + audit `ADD_SITE` |

### 7.10.5 ⚠️ Danger Zone (red bordered card)

| Element | Purpose |
|---|---|
| **Purge old draft pending_issues card** | "Delete all `pending_issues` rows older than 30 days that are still `status='draft'`" |
| **Type PURGE to confirm** | Text input must equal `PURGE` to enable |
| **Run Purge button** | Executes the DELETE, audit-logs `PURGE_DRAFTS` |

---

## 7.11 Admin Portal → 🔑 Access Control

### 7.11.1 🖥️ Recent Sign-Ins (last 10)

Per-row card from `system_audit_log` filtered to LOGIN/LOGIN_SUCCESS/LOGIN_FAILED/LOGOUT:
- Pulse dot (green=success, red=failed)
- Username
- Action label
- Timestamp + details

### 7.11.2 🔑 Force Password Reset card

| Field | Purpose |
|---|---|
| **Target user selectbox** | From `users` table |
| **New password** | Type input (password mask) |
| **Confirm** | Must match |
| **Amber warning** | "User must log in again immediately." |
| **🔑 Reset Password button** | Validates length ≥ 8 + match → `hash_password(new_pwd)` → UPDATE users SET password_hash + audit `FORCE_PASSWORD_RESET` |

### 7.11.3 🛡️ Security Policy

Read-only 2-column grid showing:
- Auth backend (bcrypt cost=12)
- Session storage (Streamlit session_state in-memory)
- RBAC hierarchy (store_keeper < supervisor < hod < admin)
- WAL mode + busy_timeout=5000ms
- Password min length (8 characters)
- Audit retention (indefinite, manual purge only)

---

## 7.11a Admin Portal → 🚚 Logistics Oversight (NEW in v3.0)

Cross-site, read-only window onto the entire procurement chain. For actions, jump to the Logistics Portal (shadow access) — this tab is observation-only by design.

### 7.11a.1 KPI strip

Six cards at the top:

| Card | Source |
|---|---|
| **OPEN PRs** | Count from `list_prs_for_logistics()` — awaiting PO issuance |
| **OPEN POs** | Count from `list_pos(open_only=True)` |
| **ACTIVE DNs** | Count of DNs in pipeline states (pending_logistics, logistics_approved, pending_hod, pending_sk) |
| **VENDOR RETURNS** | Open returns from `list_vendor_returns(open_only=True)` |
| **RESCHEDULES** | Pending reschedule decisions |
| **FORCE-CLOSURES** | Lifetime audit count |

### 7.11a.2 Filters

| Element | Purpose |
|---|---|
| **Site dropdown** | "All sites" or pick one — narrows every sub-tab |
| **Warehouse dropdown** | "All warehouses" or pick one — narrows DN view |

### 7.11a.3 Six sub-tabs

| Sub-tab | What's shown |
|---|---|
| **📥 PRs** | Every active PR in the Logistics queue, filterable by site |
| **📋 POs** | Every open PO with vendor, dates, total, status, source (manual/PDF) |
| **🚚 DNs** | Every active DN with warehouse, site, status, family |
| **↩️ Vendor Returns** | Open returns awaiting vendor acknowledgement |
| **🛑 Force-Closures** | 100 most recent force-closure records with reason + closed-by |
| **🔁 Reschedules** | Pending reschedule decisions Logistics hasn't acted on |

### 7.11a.4 What it's NOT

- This tab cannot create / approve / reject anything. All mutation happens in role-specific portals.
- Admins who need to ACT (e.g. approve a reschedule because Logistics is on leave) should switch to the Logistics Portal — admin has shadow access there.

## 7.12 Admin — Use Cases

### Use Case 1: Set initial Unit_Costs for valuation reports

1. Admin Portal → 🗄️ Master DB Editor
2. Select Table: **inventory**
3. Action: 📝 View / Edit Data
4. In the data editor, find `Unit_Cost` column (rightmost)
5. Type SAR cost for each item
6. Click 💾 Save Table Updates
7. Wait ~60 sec for caches to refresh, then check Live Dashboard hero card "Total stock value"

### Use Case 2: Approve a self-registered new user

1. Admin Portal → 👥 Users
2. Pending requests section → review the request
3. Edit role/site if needed
4. Click ✅ Approve → user can now log in. WhatsApp alert auto-fires to the requester.

### Use Case 3: Backup the database before a risky operation

1. Admin Portal → ⚙️ Settings
2. 💾 Backup Now → confirms file path: `backups/gi_database_YYYYMMDD_HHMMSS.db`
3. Note the timestamp in "Last manual backup" field
4. Now perform the risky operation (bulk import, schema change, etc.)
5. If something breaks: restore by copying the backup back over `gi_database.db` (with the app stopped)

### Use Case 4: Investigate a suspicious series of FEFO overrides

1. Admin Portal → 📜 Audit Logs
2. Action filter: **FEFO_OVERRIDE**
3. Search: e.g., a specific username
4. Review the detail column — see SAP, site, chosen lot vs FEFO-suggested, reason
5. If a pattern of unjustified overrides emerges, talk to the Store Keeper / HOD

### Use Case 5: Configure WhatsApp alert thresholds

1. Admin Portal → 📱 WhatsApp Console
2. ⚙️ Alert Thresholds card
3. Adjust sliders: low_stock_days, burn_alert_days, expiry_warn_days
4. 💾 Save Thresholds → audit-logged. HOD threshold sliders read the same values.

### Use Case 6: Approve a batch of cross-site transfers

1. Admin Portal → 📨 Pending Requests
2. Review the editor table — sort by created_at, target_site, or item
3. Tick checkboxes for the rows you want to approve as a batch
4. Type Admin Notes (e.g., "Per Tuesday meeting, expedite to Site B")
5. ✅ Approve Selected → triggers two WhatsApp dispatches per unique requester + target HOD
6. Watch the Notification log in WhatsApp Console to confirm sends

### Use Case 7: Drop a deprecated column from a custom table

1. Admin Portal → 🗄️ Master DB Editor
2. Select the table
3. Action: ⚙️ Manage Columns
4. 🗑️ Drop Column section → pick the column → Delete Column

### Use Case 8: Generate QR labels for new inventory items

1. Master DB Editor → inventory
2. View / Edit Data mode
3. In the leftmost "🏷️ Print Label" column, tick items
4. Scroll to 🖨️ QR Code Label Generator section below
5. Click 🖨️ Generate QR Labels for Selected
6. 📥 Download QR Labels PDF
7. Print on label paper, stick to shelf bins → store keepers can now scan them on mobile

## 7.13 Admin — FAQ

**Q: How do I add a new site?**
A: Admin Portal → ⚙️ Settings → 🏭 Site Management → ➕ Add New Site. New sites are immediately available for user assignment.

**Q: How do I rotate a user's password without their input?**
A: Admin Portal → 🔑 Access Control → 🔑 Force Password Reset. Pick user, type new password twice, click reset. They'll need to log in again with the new credentials.

**Q: Audit log filter has too many users — how do I find a specific user's activity?**
A: Use the search text input at the bottom of the audit filter row — searches across user + action + details.

**Q: WhatsApp queue is stuck — messages aren't being sent.**
A: The background worker (`whatsapp_worker.py`) must be running separately (not via Streamlit). Check via terminal: `python whatsapp_worker.py`. The queue table will fill but won't drain otherwise.

**Q: My DB Editor save crashed mid-way and lost rows.**
A: Restore from `backups/`. The Save action does DELETE-then-INSERT and is not crash-safe. Always backup before bulk edits.

**Q: Maintenance Mode is on but non-admins can still log in.**
A: Enforcement requires a check in `auth.py`. The toggle persists; ensure the auth gate respects `app_settings.maintenance_mode`.

**Q: Can I delete an audit log entry?**
A: No — they're meant to be immutable. If you need to free space, do a SQL purge with a date filter via DB Editor → Manage Columns... but this is itself audited.

**Q: A cross-site request is stuck pending and the requester is asking why.**
A: Admin Portal → 📨 Pending Requests. Confirm it's actually in the queue. If yes, approve or reject with notes. The HOD will get a WhatsApp.

**Q: I want to set per-user 2FA.**
A: Currently unsupported in the security model (placeholder shown in Access Control). Future enhancement.

**Q: How do I know if Ollama (local AI) is up?**
A: Admin Portal → 🖥️ Overview → 🔧 Service Health card. If "Ollama / AI" pulses green, it's reachable.

**Q: What's the difference between approving a Pending Receipt vs Pending Issue?**
A: Receipts ADD to stock (your team RECEIVED material). Issues SUBTRACT from stock (your team CONSUMED material). Both flow through approval. Issues commit via EOD (batch); Receipts commit individually via Pending Receipts tab.

**Q: Stock_Value column shows nothing on the dashboard.**
A: Inventory items have `Unit_Cost = 0`. Use DB Editor to set costs.

**Q: A HOD set thresholds different from mine — whose wins?**
A: Both HOD and Admin write to the same `app_settings` keys. Last-writer-wins. Decide a policy and lock it via convention.

**Q: How big can the database grow before performance degrades?**
A: SQLite with WAL comfortably handles 10–25 concurrent users. The codebase is structured so `database.py` is the only SQL surface — migrating to Postgres later is a contained change.

---

# 8. Reports Module — Detailed Reference

Available to: **Supervisor, HOD, Admin**. Site scope: locked for Supervisor + HOD; "All Sites" available to Admin.

## 8.1 Page header + 4 tabs

`render_brand_header("Reports & Analytics")`. Tabs:

1. 📊 Generate Report
2. 📅 Scheduled
3. 🤖 AI Insights
4. 📁 Archive

---

## 8.2 Reports → 📊 Generate Report

### 8.2.1 Date + filter row

| Field | Purpose |
|---|---|
| **From date** | Start of date window |
| **To date** | End of date window |
| **Site filter** | Locked to own site for non-Admin; "All Sites" + dropdown for Admin |
| **Format** | PDF / Excel / CSV |

### 8.2.2 Report type selector

A grid of 9 selectable cards:

| Report | Description | Source |
|---|---|---|
| 📋 **Daily Consumption** | All material issues in the window, grouped by work type | `report_daily_consumption()` |
| 📅 **Monthly Summary** | Per-SAP opening, issued, received, closing stock + **SAR value columns** (Issued, Received, Closing) | `report_monthly_summary()` |
| ⚠️ **Low Stock Alert** | Materials below minimum with shortfall | `get_low_stock_items()` |
| 📈 **Burn Rate Analysis** | 30-day trends, days-of-supply per item | `cached_burn_rate_and_forecast()` |
| 💰 **Inventory Valuation** | Per-item Stock_Value sorted descending; summary with top-10 share | `get_inventory_valuation()` |
| 🏷️ **Shelf-Life / Expiry** | Lots by expiry — expired/critical/warning buckets | `get_short_dated_stock()` |
| 📋 **PR Status Report** | All PRs with status, workflow_state, Est_Cost_SAR | `report_pr_status()` |
| ✅ **FEFO Compliance** | Audit of FEFO adherence — picks vs oldest lot | `report_fefo_compliance()` |
| 📜 **Full Audit Report** | Complete `system_audit_log` for the date range | `report_audit_export()` |

### 8.2.3 ▶ Generate Report button

Calls `_run_report(type, from, to, site_id)` → returns `(DataFrame, summary_dict)`. Loading state shows shimmer.

### 8.2.4 Preview section (after generation)

| Element | Purpose |
|---|---|
| **Report title + subtitle** | "<Type> — <site>" + "<from> → <to>" |
| **↓ Download <fmt> button** | Encodes via `generate_report_pdf/excel/csv` and triggers `st.download_button` |
| **Summary cards** | Auto-generated from the summary dict keys |
| **Bar chart** | For Burn Rate and Daily reports |
| **Preview table** | Styled rows with status-color cells |

### 8.2.5 📧 Email Delivery section

| Field | Purpose |
|---|---|
| **Recipients text input** | Comma-separated emails |
| **📧 Send Email button** | Generates and emails the report |
| **📱 WhatsApp button** | Sends a brief summary via WhatsApp |

---

## 8.3 Reports → 📅 Scheduled

Manage automated recurring reports.

### 8.3.1 + New Schedule button (expands form)

| Field | Purpose |
|---|---|
| Report Type | One of the 9 |
| Frequency | Daily 06:00 / Daily 17:00 / Weekly Mon 07:00 / Monthly 1st 06:00 |
| Format | PDF / Excel / CSV |
| Recipients | Comma-separated users |

### 8.3.2 Schedule cards

Each schedule renders as a card with:
- Report icon + label
- Frequency · recipients
- Last run timestamp
- **Active/Paused** pill + toggle
- **▶ Run Now button** (manual trigger)
- **🗑️ Delete button**

---

## 8.4 Reports → 🤖 AI Insights

If AI_ENABLED and Ollama is running with `llama3.1:8b`:

### 8.4.1 Header bar

🤖 + "AI-Powered Inventory Analysis" + BETA pill + intro text. Shows "Analysing data…" shimmer during regen.

| Button | Action |
|---|---|
| **🔄 Regenerate** | Re-runs all 5 fixed SQL probes + LLM commentary |

### 8.4.2 5 Insight cards (collapsible)

Each card has a left-border in severity color, then:

| Element | Purpose |
|---|---|
| Icon + Title | "Abnormal Consumption Spike — MAT-XXXX" |
| Severity pill (Critical/Warning/Positive) | Color-coded |
| Confidence % with progress bar | LLM-reported confidence |
| Right-side metric callout | Headline number with sub-label |
| Body paragraph | LLM-generated explanation grounded in the SQL probe result |
| 💡 Recommendations list | Up to 3 numbered actions |
| 📧 Share button | Shares with HOD team |
| ✅ Add to Actions button | Logs as a follow-up |

The 5 fixed probes:
1. Abnormal consumption spike (vs trailing average)
2. Items approaching reorder
3. FEFO compliance rate
4. Procurement cost optimization (supplier consolidation)
5. Inventory health score

If Ollama is not running: a graceful fallback explains setup steps.

---

## 8.5 Reports → 📁 Archive

Permanent record of previously generated reports.

### 8.5.1 Search row

| Element | Purpose |
|---|---|
| **Search archive text input** | Substring match on name / type |
| **Total counter caption** | "N reports · X KB total" |

### 8.5.2 Archive table

Columns: Report Name · Type pill (colored by report type) · Generated date · By · Format icon + label · Size · Actions

Per-row Actions:
- ↓ Download
- 📧 Re-email
- 🗑️ Delete (audit-logged)

---

# 9. Automated Notifications — WhatsApp & Email & In-app Bell

The system automatically queues messages on key events. There are now THREE notification surfaces:
1. **WhatsApp queue** — `whatsapp_queue` table, drained by `whatsapp_worker.py`. Gated by `config.WHATSAPP_ENABLED` master switch + `config.WHATSAPP_TRIGGERS` per-event dict. Flip a key to `False` to silence WhatsApp for that event without touching in-app behaviour.
2. **In-app notifications bell (NEW in v3.0)** — `app_notifications` table, surfaced via the sidebar bell described in §3.6. ALWAYS fires regardless of WhatsApp toggle.
3. **Email** — same Outlook / Mail.app / mailto: + SMTP paths as before.

## 9.1 WhatsApp triggers (auto-fire)

| Event | Recipient | Trigger location | Notes |
|---|---|---|---|
| Store Keeper submits issue batch | Site HOD | Entry Log Submit button | Includes item list |
| Store Keeper submits receipt batch | Site HOD | Entry Log Submit button | Includes item list |
| Store Keeper submits stock count | Site HOD | Entry Log Stock Count Submit | Includes variance + reason |
| Store Keeper performs FEFO override | Site HOD | Entry Log Add to Grid | Includes chosen vs FEFO-suggested lot + reason |
| HOD commits EOD | Site HOD (self) | post-commit | Only if low-stock items result |
| HOD cross-site request created | All Admins | Submit cart | Includes target site + count |
| HOD cross-site request, > 5 items | Target Site HOD | Submit cart | Bulk escalation |
| Admin approves cross-site requests (batch) | Requesting HOD | Approve Selected | Includes approved items + admin notes |
| Admin approves cross-site requests (batch) | Target Site HOD | Approve Selected | "TRANSFER ORDER — pack and ship" |
| Admin rejects cross-site requests (batch) | Requesting HOD | Reject Selected | Includes rejection reason |
| Returnable item overdue | Borrower + Store Keeper | Background scheduler | Time-driven, not in current scope |
| New self-registration request | All Admins | Registration form | Includes username + role + site |
| Self-registration approved | Requesting user | Admin User Mgmt | "ACCESS GRANTED · Welcome" |
| Manual send | Free | HOD Notifications / Admin WhatsApp Console | Audit `MANUAL_WHATSAPP` |
| **PR submitted to Logistics (v3.0)** | Logistics role | HOD PR tab → 🚚 Submit PR(s) to Logistics | Event key `pr_submitted_to_logistics` |
| **PO issued (v3.0)** | Site HOD | Logistics → 💾 Save PO | Event key `po_issued` |
| **PO assigned to Warehouse (v3.0)** | Warehouse users at that WH | Logistics → 📨 Assign | Event key `po_assigned_to_warehouse` |
| **Warehouse acknowledged (v3.0)** | Logistics | Warehouse → ✅ Acknowledge | Event key `warehouse_acknowledged` (off by default) |
| **Warehouse received goods (v3.0)** | Logistics | Warehouse → 📥 Record receipt | Event key `warehouse_received` |
| **DN logistics approved (v3.0)** | Site HOD | Logistics → ✅ Approve DN | Event key `dn_logistics_approved` |
| **DN HOD approved → SK (v3.0)** | Site SK | HOD → 🚚 DN Approvals → ✅ Approve | Event key `dn_auto_generated` (reuses slot for SK ping) |
| **DN received at site (v3.0)** | Logistics + Warehouse | SK → ✅ Mark as Received | Event key `dn_received_by_sk` (off by default) |
| **Reschedule requested (v3.0)** | Logistics | Warehouse / HOD → 🔁 Request reschedule | Event key `reschedule_requested` |
| **Reschedule decided (v3.0)** | Requester | Logistics → ✅ Approve / ❌ Reject | Event key `reschedule_decided` |
| **Vendor return raised (v3.0)** | Logistics | Any role → ↩️ Raise return | Event key `vendor_return_raised` |
| **PR force-closed (v3.0)** | Admin + originating Site HOD | Logistics → 🛑 Force-Close | Event key `pr_force_closed`, severity `critical` |
| **PO force-closed (v3.0)** | Admin + originating Site HOD | Logistics → 🛑 Force-Close | Event key `po_force_closed`, severity `critical` |
| **Delivery reminder T-2 / T-1 / T-0 (v3.0)** | Logistics + HOD + Warehouse (per DN) | Daily sweep job — see §9.3 | Event keys `delivery_reminder_t_minus_2/_minus_1/_zero` |

## 9.2 Email triggers

| Event | Trigger location | Mechanism |
|---|---|---|
| Logistics PR follow-up | HOD PR tab → 📧 Draft Outlook Email | Mac: AppleScript-driven Mail.app with HTML table. Windows: COM Outlook with HTMLBody. Fallback: mailto: with monospace plain-text table |
| EOD report email | mailer.py SMTP | Configurable via `.env` (SMTP_SERVER, SMTP_USER, SMTP_PASS) |
| Report delivery (scheduled) | Reports → Scheduled | Same SMTP pipeline |

---

## 9.3 In-app notifications bell (NEW in v3.0)

See §3.6 for the UI. Backed by the `app_notifications` table; queried with role + site + warehouse scoping. Per-event severity (`info` / `warning` / `success` / `critical`) drives the colour-coded left border on each card.

## 9.4 Delivery reminder daily sweep (NEW in v3.0)

A once-per-day job in `whatsapp_worker.run_worker_loop()` calls `sweep_delivery_reminders()` which fires T-2 / T-1 / T-0 reminders for upcoming deliveries:

| Watched | When | Severity |
|---|---|---|
| `purchase_orders.Expected_Delivery` | T-2 / T-1 / T-0 | `warning` / `warning` / `critical` |
| `delivery_notes.DN_Date` | T-2 / T-1 / T-0 | `warning` / `warning` / `critical` |

PO reminders ping: Logistics + originating Site HOD.
DN reminders ping: Logistics + Site HOD + Warehouse user(s) at the receiving warehouse.

**Idempotency** — the sweep cannot double-fire for the same target on the same day. Two guards:
1. UNIQUE(`ref_type`, `ref_number`, `target_date`, `offset_days`) on the `delivery_reminders_sent` table.
2. Day-marker stored in `app_settings.delivery_reminders_last_run` — the 60-sec worker poll loop skips the sweep query entirely on second-and-later ticks of the same day.

Restarting the worker mid-day is safe. Re-running the sweep manually on the same day fires zero new notifications.

**Customising the cadence** — currently the offsets are hardcoded `(2, 1, 0)` in `database.sweep_delivery_reminders()`. A configurable offsets list is on the v3.0 backlog (see `handoff.md` §3 item 25).

# 10. Data Model & Concept Reference

## 10.1 Core movement tables

| Table | What it stores | Identity-math role |
|---|---|---|
| `inventory` | Master catalogue: SAP_Code (PK), Description, Material_Code, UOM, Minimum_Qty, Unit_Cost | The "items that exist" — defines what can be moved |
| `receipts` | Every received unit (post-commit). Includes Lot_Number, Expiry_Date, Supplier, PR_Number, Unit_Cost | + adds to stock |
| `consumption` | Every consumed unit (post-EOD-commit). Includes Lot_Number, FEFO_Override, Work_Type | − subtracts from stock |
| `returns` | Tools and equipment returned to inventory | + adds back |
| `pending_issues` | Pre-commit staging for consumption (status: draft → pending_hod → approved/rejected → committed) | — does NOT affect stock |
| `pending_receipts` | Pre-commit staging for receipts | — does NOT affect stock |

**Identity formula:** `Current_Stock = Total_Received − Total_Consumed − Total_Returned` — computed live in `v_site_stock` view.

## 10.2 Document-type tables

| Table | What it stores | Lifecycle |
|---|---|---|
| `stock_adjustments` | Physical-count reconciliations | pending_hod → approved (posts synthetic ledger row) / rejected |
| `requests` | Cross-site material transfers | pending → approved / rejected → fulfilled |
| `pr_master` | Purchase Request lines (manual or PDF-extracted) | status: open / closed; workflow_state: draft → submitted → approved → in_progress → received |
| `returnable_items` | Tools temporarily issued out | borrowed → returned / overdue |
| `lots` | Lot master metadata (FEFO source-of-truth) | open → exhausted / expired / disposed / quarantine |

## 10.3 Supporting tables

| Table | Purpose |
|---|---|
| `users` | Auth + role + site + phone |
| `pending_users` | Self-registration queue |
| `system_audit_log` | Immutable activity record |
| `system_settings` | Dropdown values (Work_Type, Site) |
| `app_settings` | Key/value config (thresholds, maintenance_mode, last_backup_at) |
| `whatsapp_queue` | Outbound message queue |
| `pwa_tokens` | API tokens for offline PWA |
| `bug_reports` | User-submitted issues/ideas |
| `report_schedules` | Scheduled report definitions |
| `report_archive` | Generated report metadata |

## 10.4a Procurement chain tables (NEW in v3.0)

| Table | What it stores | State machine |
|---|---|---|
| `warehouses` | Master of receiving locations | `active` / `inactive` |
| `vendors` | Supplier master | `active` / `inactive` |
| `purchase_orders` | PO header (PO_Number UNIQUE) | `open` → `partially_delivered` → `delivered` → `closed` / `force_closed` / `cancelled` |
| `po_items` | PO line items with `rl_bl_family` tag | `open` → `partially_delivered` → `delivered` / `returned` / `closed` / `force_closed` |
| `po_shipment_schedule` | Parsed PO Annexure rows | `pending` / `shipped` / `delivered` / `delayed` / `cancelled` |
| `po_assignments` | Logistics → Warehouse routing | `assigned` → `acknowledged` → `partial` / `received` / `closed` / `cancelled` |
| `delivery_notes` | DN header (DN_Number UNIQUE) | DN state machine: `draft` → `pending_logistics` → `pending_hod` → `pending_sk` → `received` (or `rejected` from any pending) |
| `dn_items` | DN line items | `pending` / `received` / `partial` / `returned` / `cancelled` |
| `po_returns` | Vendor returns (raised by any role) | `open` → `vendor_acknowledged` / `resupplied` / `cancelled` |
| `po_reschedule_requests` | Date-change asks | `pending` → `approved` / `rejected` |
| `po_force_closures` | Force-closure audit log | (terminal — write-once) |
| `app_notifications` | In-app bell inbox | `read_at IS NULL` (unread) → timestamp (read) |
| `delivery_reminders_sent` | T-2/T-1/T-0 idempotency log | (terminal — UNIQUE constraint) |

## 10.4 Views

| View | Purpose |
|---|---|
| `v_live_stock` | Per-SAP global stock (sum across all sites) |
| `v_site_stock` | Per-(SAP, Site) live stock — the canonical "what's at this site" |
| `v_expiring_stock` | Lots/receipts in expiry buckets |
| `v_supplier_activity` | Per-supplier receipt rollups |
| `v_lot_balance` | Per-lot Received_Qty / Consumed_Qty / Remaining_Qty (identity math) |

---

# 11. Status Codes, Reason Codes & Glossary

## 11.1 Common status pills

| Status | Color | Meaning |
|---|---|---|
| **pending** | grey | Awaiting decision |
| **flagged** | amber | Has a concern (e.g., zero stock at site) |
| **approved** | green | Approved, ready for commit |
| **rejected** | red | Soft-rejected (row preserved for audit) |
| **committed** | gold | Posted to permanent ledger |
| **draft** | grey | User can still edit |
| **submitted** | blue | Sent forward; awaiting next stage |
| **in_progress** | amber | Active (e.g., PR with supplier) |
| **received** | green | Fully delivered |
| **open** | blue | Active, has pending balance |
| **closed** | green | Fully fulfilled |
| **OK / Low / Below Min / Empty** | green/amber/orange/red | Stock vs minimum status |
| **Expired / Critical / Warning** | red/red/amber | Expiry buckets |
| **sent** | green | WhatsApp delivered |

## 11.2 Adjustment reason codes

| Code | Label | When to use |
|---|---|---|
| `cycle_count` | 🔄 Cycle count correction | Routine periodic count |
| `damaged` | 🔨 Damaged / unusable | Physical damage |
| `expired_disposal` | 🗑️ Expired — disposed | Expired and physically discarded |
| `miscount_in` | ➕ Miscount — found extra | Found more than system shows |
| `miscount_out` | ➖ Miscount — short | Found less than system shows |
| `lost` | ❓ Lost / unaccounted | Genuinely missing |
| `theft` | 🚨 Suspected theft | Deliberate removal |
| `return_to_supplier` | ↩️ Returned to supplier | Quality issue, sent back |
| `other` | ❔ Other (see notes) | Anything else (must explain in notes) |

## 11.3 PR workflow states

- `draft` — manual creation, not yet sent
- `submitted` — sent to procurement
- `approved` — procurement greenlit
- `in_progress` — supplier engaged
- `received` — fully received

## 11.4 Lot statuses

- `open` — has remaining quantity, available for FEFO
- `exhausted` — fully consumed (auto)
- `expired` — past expiry date (manual or background)
- `disposed` — physically removed (manual)
- `quarantine` — held pending inspection (manual)

## 11.4a DN states (NEW v3.0)

`draft` → `pending_logistics` → `logistics_approved` → `pending_hod` → `hod_approved` → `pending_sk` → `received`
With `rejected` as terminal from any pending state.

## 11.4b PO + PO line states (NEW v3.0)

PO header: `open` → `partially_delivered` → `delivered` → `closed` / `force_closed` / `cancelled`
PO item line: `open` → `partially_delivered` → `delivered` → `returned` / `closed` / `force_closed`

## 11.4c Force-closure target types (NEW v3.0)

| Code | Label |
|---|---|
| `pr` | Whole PR closed |
| `po` | Whole PO closed |
| `po_item` | Single line on a PO closed |

## 11.4d Reschedule + vendor return states (NEW v3.0)

Reschedule: `pending` → `approved` / `rejected`
Vendor return: `open` → `vendor_acknowledged` / `resupplied` / `cancelled`

## 11.4e RL/BL family tags (NEW v3.0)

| Tag | Meaning | Detection rule |
|---|---|---|
| `RL` | Rubber Lining | Substring `RL-`, `RUBBER LINING`, `RUBBER-LINING` in Material_Code OR Description |
| `BL` | Brick Lining | Substring `BL-`, `BRICK LINING`, `BRICK-LINING`, `BRICK MATERIAL` |
| `NULL` | Neither family | Default |

Logic in `config.classify_rl_bl_family()`. NEVER a combo string — RL takes precedence if both tokens are present.

## 11.4f Notification severity (NEW v3.0)

| Severity | Visual | When used |
|---|---|---|
| `info` (🔵 blue) | Info pings — PR submitted, PO issued, assignments |
| `warning` (🟡 amber) | T-2/T-1 reminders, reschedule requests, rejections |
| `success` (🟢 green) | DN approved successfully, delivery completed |
| `critical` (🔴 red) | T-0 reminder, force-closures, urgent escalation |

## 11.4g Logistics-status on PR rows (NEW v3.0)

| Code | Meaning |
|---|---|
| `site_draft` | HOD has the PR but hasn't submitted to Logistics yet |
| `submitted` | Sitting in Logistics queue waiting for PO issuance |
| `in_po` | A PO has been issued against this PR line |
| `closed` | PR fulfilled normally |
| `force_closed` | Logistics force-closed the PR with a reason |

## 11.5 Glossary

| Term | Meaning |
|---|---|
| **FEFO** | First-Expiry-First-Out — issue oldest-expiring lot first |
| **EOD** | End-of-Day commit (HOD action that finalizes the day's transactions) |
| **PR** | Purchase Request |
| **DN** | Delivery Note (logistics document) |
| **OCR** | Optical Character Recognition — used for bulk-staging from images |
| **PWA** | Progressive Web App — the offline mobile companion |
| **RBAC** | Role-Based Access Control |
| **WAL** | Write-Ahead Logging (SQLite concurrency mode) |
| **Standard cost** | Per-item Unit_Cost on inventory master (vs weighted-average cost which would require receipts-history) |
| **Identity math** | Stock derived from movements, never stored as a counter |

---

# 12. FAQ — Master Index by Role

## 12.1 General (any role)

**Q: I forgot my password.**
A: Contact your Admin. Admin → 🔑 Access Control → 🔑 Force Password Reset.

**Q: I get "Permission denied" when clicking a page.**
A: Your role doesn't have access. See §2.2 for the access matrix.

**Q: I want to suggest a feature or report a bug.**
A: Any page → sidebar → Bug/Feature reporting dialog. Admin reviews in Admin Portal → Reports & Bugs.

**Q: The page is blank or broken.**
A: Hard-refresh (Cmd+Shift+R / Ctrl+F5). If it persists, check with Admin — they can see audit logs to diagnose.

## 12.2 Store Keeper — see §4.8

## 12.3 Supervisor — see §5.5

## 12.4 HOD — see §6.14

## 12.5 Admin — see §7.13

---

# 13. 2026-06 Feature Update — What Changed

This section documents the upgrades shipped in the 2026-06 release. Everything above remains accurate except where this section overrides it.

## 13.1 Field-level changes

| Change | Where | Behaviour |
|---|---|---|
| **All form fields mandatory** | Entry Log (Consumption / Receipt Staging), HOD Receive Material, Admin Add Entry forms | Every text/number/select input is required. Validation lists missing fields on submit. |
| **Expiry Date is optional** | SK Receipt Staging, HOD Receive Material | Marked `(Optional)`. Leave blank for non-perishable items. |
| **Remarks, Tank No., Serial No., PR Number** | Same forms | No longer optional. |

## 13.2 Live Dashboard column order

The Live Dashboard table now renders in this order (when columns exist):

`SAP_Code → Material_Code → Equipment_Description → UOM → Opening_Stock → Receipt → Consumption → Return → Closing_Stock → Minimum_Qty → Unit_Cost → Stock_Value → Category → Status`

- **Opening_Stock** is now a configurable column on `inventory`. Default 0; admin can edit in DB Editor.
- **Identity formula** updated to `Closing_Stock = Opening_Stock + Total_Received − Total_Consumed − Total_Returned`.
- `Material_Code` now appears after `SAP_Code` in **every** report (Daily Consumption, Daily Receipts, Monthly Summary, PR Status, etc.) and in the HOD Pending Receipts approval list.

## 13.3 Entry Log access — Store Keeper only

The Entry Log page is now visible **only to the `store_keeper` role**. HODs review submissions in HOD Portal; Admins use Admin Portal. The page is hidden in the sidebar for other roles.

## 13.4 EOD Commit — checkbox confirmation

The "Confirm EOD Commit" dialog no longer requires typing `COMMIT`. Tick the confirmation checkbox and click **Confirm Commit**. Cancel still drops all pending state.

## 13.5 Material Category

Every inventory item now carries a **Category**. Categories: `Consumable`, `Equipments`, `Utilities`, `Maintenance`, `Others` (default), `Rubber materials`, `Tools`, `QC items`.

- Admin Portal **Add New Entry** → renders Category as a selectbox.
- Reports page → **Filter by Category** dropdown alongside the SAR / cost-columns toggle. "All Categories" disables the filter.

## 13.6 Rubber MTC workflow

When a Store Keeper stages a receipt and the selected material's category is **Rubber materials**, the system shows:

- **MTC Number** text field (e.g. `MTC-2026-001234`)
- **MTC Document** file uploader (PDF / JPEG / JPG / XLSX)

Either field can be blank — the receipt still goes through. What changes is HOD-side visibility:

- **HOD Portal → Pending Receipts**: a red banner lists rubber items received without an MTC.
- Click **✉️ Draft Logistics Email** to open a pre-filled email to the logistics team listing SAP, description, lot, qty.
- Sending (or clicking "Mark all as sent") flips the rubber rows to `sent_to_logistics`.

The logistics email recipient defaults to `LOGISTICS_EMAIL` env var (`logistics@generalindustries.net` if unset). On Windows it opens Outlook; on macOS it opens Mail.app; on Linux it opens the default mailto handler.

## 13.7 Document attachments — Entry Log + HOD DOC tab

Store Keepers can attach reference documents (PDF / JPEG / JPG / XLSX) on:

| SK form | Doc number used | Notes |
|---|---|---|
| **Consumption Log** | Auto = `DDMMYY` of the date | Pick scope: "Whole entry (batch)" or "Specific date". |
| **Receipt Staging** | DN No. of the row (or manual override) | Falls back to `DN-DDMMYY` if no DN_No found. |

Files are stored **as BLOBs inside the database** (authoritative copy) and **mirrored to `uploads/<Site_ID>/<doc_type>/<doc_number>/`** for local browsing. The disk mirror is gitignored; only the DB BLOB is portable.

**HOD Portal → 📎 DOC** is a new tab with three sub-tabs: **📋 Consumption / 📥 Receipt / ↩️ Return**. Each shows period (From/To dates) and Doc Number text filters, with a per-file ⬇️ download button.

The **↩️ Return** sub-tab pulls from the new Return Items workflow (see §13.10), not from Returnable Items.

## 13.8 QR Label approval flow

The Admin DB Editor's QR generator (single-user, single-item) is unchanged. The new flow is two-step:

1. **Store Keeper → Entry Log → 🏷️ QR Label Request** (new tab)
   - Multi-select materials in one form.
   - Per-item label quantity.
   - Click **📨 Submit Batch for HOD Approval**.

2. **HOD Portal → 🏷️ QR Approval** (new tab)
   - **⏳ Pending** sub-tab: select rows via checkbox, then **✓ Approve Selected** or **✗ Reject Selected**.
   - **✅ Approved** sub-tab: **📥 Download QR Labels PDF for ALL approved** generates one consolidated PDF.

## 13.9 Returnable Items — clarification

The **🔄 Returnable Items** tab is for **temporary tool loans only** (e.g. issuing a torque wrench to a worker who'll return it before EOD). It is *not* a way to return stock to the warehouse. No DN No., no document attachment.

For real returns (defective material going back to logistics), use the new Return Items tab — see §13.10.

## 13.10 Return Items workflow (NEW)

The new **↩️ Return Items** tab (between Receipt Staging and Returnable Items in the Entry Log) handles real returns to the warehouse / logistics.

### Store Keeper flow

1. The material picker is restricted to materials **received in the last 30 days** at the user's site.
2. If multiple receipts exist for the same SAP code, the SK is asked which receipt is being returned (Date / DN No. / Received Qty).
3. The system shows a locked summary of the original receipt: Date, DN No., PR, Lot, Received Qty.
4. The SK enters:
   - **Return Quantity** (capped at the original Received Qty)
   - **Reason** (work-types dropdown)
   - **Return DN No.**
   - **Attachment** — mandatory: the Return DN + any photos
5. To return material older than 30 days, tick **"Override 30-day window"** — the picker widens to 12 months and an Override Justification field appears. This routes to HOD as an explicit override request.
6. Submit → request lands in HOD Portal **↩️ Returns** tab. A WhatsApp ping goes to the site HOD if a phone number is on file.

### HOD flow

1. **HOD Portal → ↩️ Returns** lists every pending return with a card per row.
2. Rows that required an override are highlighted in red and show the SK's justification.
3. **✓ Approve** → writes a row to the `returns` ledger (so `Current_Stock` reduces by the returned qty, the dashboard `Return` column ticks up, and the entry shows up in monthly / consumption reports). Then automatically opens the **logistics email draft** with item, qty, reason, and the original receipt's DN/PR/Lot context.
4. **✗ Reject** → marks the request rejected. The SK sees this in their request history.

### Dashboard / report impact

- The **Return** column on the Live Dashboard reflects approved returns (since `returns` is the source of truth).
- All existing reports (Daily Consumption, Daily Receipts, Monthly Summary, Audit) include returns via the same identity math.
- The HOD DOC tab **↩️ Return** sub-tab lets the HOD browse all attached return documents.

## 13.11 Per-site Work Type and Tank No.

HOD Portal → **⚙️ Site Config** lets the site HOD add or delete Work Types and Tank Numbers scoped to their site. Empty per-site lists fall back to the global defaults.

## 13.12 WhatsApp worker — startup fix

The worker module no longer imports `pywhatkit` at module load (that pulled in heavy GUI deps and stalled local launch by tens of seconds). It now lazily imports `pywhatkit` only when an outbound message has no Twilio fallback. On Streamlit Cloud, Twilio handles delivery; on local desktop, `pywhatkit` is loaded the first time a message is queued.

If you see the spinner sitting on `_start_whatsapp_worker()` for more than ~3 seconds locally, check that you ran `pip install -r requirements.txt` after the update.

## 13.13 Category rename — Rubber Materials → Surface Shields

The category that triggers the MTC-required workflow on Receipt Staging is now **Surface Shields** instead of "Rubber Materials". Everywhere the system used to look for `Rubber materials`, it now looks for `Surface Shields`. The behaviour is identical:
- SK selects a Surface Shields item on Receipt Staging → MTC Number + MTC File uploader appear.
- Missing MTC = HOD sees the red banner on Pending Receipts with **✉️ Draft Logistics Email**.

The internal constant is now `MTC_REQUIRED_CATEGORY` in `config.py`. If you ever need to apply MTC enforcement to another category, change one string and the rest of the system follows.

## 13.14 WBS Master + WBS-aware Entry Log + WBS Report

- **HOD Portal → Site Config → 📐 WBS Numbers** lets the HOD add, close, or re-open WBS numbers for their site. Each WBS carries an optional Description and an `active` / `closed` status.
- **Entry Log → Consumption Log and Receipt Staging** show a **WBS Number** dropdown filtered to the SK's site. If the HOD hasn't added any WBS yet, the SK sees a warning and a free-text fallback so work isn't blocked.
- **Reports → 📐 WBS Report** rolls everything up by WBS for a chosen date range, scoped to the user's site. Columns: `WBS_Number`, `Consumption_Rows`, `Consumption_Qty`, `Consumption_Value_SAR`, `Receipt_Rows`, `Receipt_Qty`, `Receipt_Value_SAR`. Sorted by consumption value descending.
- DB column `wbs` is now self-healed on `consumption`, `receipts`, `pending_issues`, `pending_receipts`. Old rows show as `(no WBS)` in the report; new rows carry the picked WBS through SK → HOD commit untouched.

## 13.15 Site_ID badge in sidebar

Every signed-in user now sees their site code as a gold pill in the sidebar user card, next to the role label. This makes "wait, which site am I logged into?" impossible to get wrong, which matters when an Admin shadows a site for support.

## 13.16 Live Dashboard — live-typing filter on key columns

The dashboard filter row no longer waits for Enter. Typing into any of the four searchable columns — **SAP_Code**, **Material_Code**, **Equipment_Description**, **Category** — narrows the table on every keystroke (~180 ms debounce). The numeric columns deliberately have no filter input: text filters on numbers don't help and they made the page laggy.

Header reads "Filters (live) — searchable on SAP / Mat Code / Description / Category".

The package `streamlit-keyup` powers the live updates; if it's not installed, the dashboard falls back to plain `st.text_input` (still works, just needs Enter).

## 13.17 Sidebar Hub Assistant — role-aware Q&A

A new sidebar expander **💬 Ask Hub Assistant** lets any signed-in user ask plain-English questions about the system. The answer is generated by the local Ollama `llama3.1:8b` model. **Role filtering happens at the RAG context layer, not just the system prompt** — a Store Keeper's question never sees the Admin chapter, so the model physically cannot answer about admin features even if asked.

Section visibility:
- **Store Keeper**: §1, §2, §3, §4, §11, §13
- **Supervisor**: SK list + §5, §8
- **HOD**: Supervisor list + §6, §9
- **Admin**: everything

The widget streams the answer token-by-token. Click **Clear** to reset.

## 13.18 GMT+3 (Asia/Riyadh) timestamps

Timestamps shown in the UI are now Riyadh local time. Background:
- New rows written through Python (`datetime.datetime.now()`) write Riyadh time directly because `TZ=Asia/Riyadh` is set in every host launchd plist.
- Rows written through SQLite's `DEFAULT CURRENT_TIMESTAMP` (still UTC, per SQLite spec) are converted at display time via `config.utc_to_local()`.
- Affected views so far: Admin Pending Cross-Site Requests, Admin Audit Logs, Admin Live Activity Feed, Admin WhatsApp Console, HOD Cross-Site Incoming, HOD Pending Receipts. Other tabs still show UTC; tell the admin to file a ticket if any specific column matters and isn't converted yet.

Override the offset via the env var `GI_TZ_OFFSET_HOURS` if you ever roll out to a site outside KSA.

## 13.19 Self-host setup — `host_setup/` folder

Path A (Mac + Cloudflare Tunnel) is now turnkey. The repo ships:
- `host_setup/launchd/*.plist.tmpl` — templates for the 4 background services
- `host_setup/scripts/install.sh` — one-shot install + load
- `host_setup/scripts/run_streamlit.sh` — wrapper that exec's Streamlit (avoids macOS exit-126 caused by direct caffeinate chain)
- `host_setup/scripts/restart_app.sh` — zero-downtime restart after `git pull`
- `host_setup/scripts/uninstall.sh` — unload + remove without touching data
- `host_setup/scripts/backup_db.sh` — SQLite online backup + iCloud Drive mirror + 14-day prune
- `host_setup/cloudflared_config.yml.example` — tunnel config template
- `host_setup/README.md` — full 45-minute setup playbook

See §14 below for the operations guide.

## 13.20 WhatsApp on macOS — Chrome via Accessibility-only AppleScript

The Mac worker now uses `open -a <browser>` + `osascript` with **only `System Events` keystrokes** (no `tell application <browser>`). This means:
- macOS asks for Accessibility permission **once**, on first send. Grant it. Done forever.
- No more "Python wants to control Google Chrome" Automation prompt on every message.
- Works with Chrome OR Safari — set `GI_WHATSAPP_BROWSER` env var. The worker plist defaults to `chrome`.

Two new env-controlled knobs:
- `GI_WHATSAPP_BROWSER` (default `Google Chrome`)
- `GI_WHATSAPP_WAIT_S` (default `15` — seconds between opening the URL and pressing Enter; raise on slow Macs)

When the standalone worker is running (launchd `com.gi.whatsapp-worker`), the embedded thread inside the Streamlit process is suppressed via `GI_SUPPRESS_EMBEDDED_WORKER=1` so the two don't race for the same queue rows.

## 13.21 v3.0 Procurement chain — what changed (2026-06 round 3)

The largest single feature batch since launch. Fully additive — no edits to existing SK / HOD / Admin tabs, EOD commit, identity math, cache layer, mailer, WhatsApp worker, or Ollama integration.

**New role-locked portals:**
- 🚚 Logistics Portal — 8 tabs (Incoming PRs · Create PO · Open POs · Assign to Warehouse · Reschedules · Force-Close · Vendor Returns · History). Documented in §14.
- 🏭 Warehouse Portal — 6 tabs (Incoming Assignments · Receive Goods · Prepare DN · Outbound DNs · Returns from Site · History). Documented in §15.

**New tabs / expanders on existing pages:**
- HOD Portal → 🚚 DN Approvals (§6.15) and 🚚 In-Transit (§6.16) — 14th and 15th HOD tabs.
- HOD Portal → 📋 Purchase Requests → new "🚚 Submit PR(s) to Logistics Portal" expander (§6.8.4a). Coexists with the existing email path.
- SK Entry Log → 📦 Receipt Staging → new "🚚 Incoming Delivery Notes from Warehouse" expander (§4.4.0).
- Admin Portal → 🚚 Logistics Oversight (§7.11a) — 11th admin tab.

**New sidebar component:**
- 🔔 Notifications bell with unread badge + inbox dialog (§3.6).

**New background job:**
- T-2 / T-1 / T-0 delivery reminders fired by `whatsapp_worker._maybe_run_delivery_reminders()` once per local day (§9.4).

**New reports:** PO Status / Warehouse Throughput / Force-Closures (added to the existing Reports module, available to Supervisor + HOD + Admin).

**RL/BL strict separation:** Rubber Lining and Brick Lining never aggregate. Enforced in `po_items.rl_bl_family` tagging, DN splitter rejection, and DN header family tag. See §11.4e.

**Warehouse-blind pricing:** Three independent enforcement layers ensure Warehouse users never see Unit_Price, Total_Price, or any monetary header field. See §15.

**Bug fixes that shipped with v3.0:**
- Double GMT+3 addition removed from Admin Pending Requests, HOD My Requests, Admin WhatsApp Console (`_localize()` already converts at the data-layer boundary).
- Admin Pending Requests now joins inventory to display Material_Code + Material_Name + UOM alongside SAP_Code.
- Sidebar Hub Assistant gracefully handles unreachable Ollama with `st.warning("🤖 Local AI is offline. Please run 'ollama serve'…")`.

---

# 14. Logistics Portal Manual

The Logistics Portal sits between Site HOD (who creates PRs) and Warehouse (which physically receives goods). Role-locked to `{logistics, admin}` — exact-role lock means no other role inherits access via the hierarchy. Eight tabs.

## 14.1 Pages visible to Logistics

- 📦 Live Dashboard (read-only — all sites)
- 🚚 Logistics Portal (this page)
- 📊 Reports (incl. the 3 new procurement reports)

## 14.2 Tab 1: 📥 Incoming PRs

Site HODs submit PRs to Logistics from their HOD Portal → PR tab. They land here.

### 14.2.1 Site filter

Dropdown of all sites + "All sites". Defaults to all.

### 14.2.2 Hero strip (3 cards)

| Card | Source |
|---|---|
| **OPEN PRs** | Active queue count |
| **TOTAL QTY** | Sum across all open PR lines |
| **SITES** | Distinct sites currently submitting |

### 14.2.3 Queue table

AgGrid with columns: PR No. · Site · Lines · Total Qty · Submitted · Earliest Delivery · Status. Sortable, filterable, exportable.

### 14.2.4 Per-PR drilldown

Selectbox under the queue table. Pick a `PR + Site` combination and a section card appears:

| Card row | Source |
|---|---|
| PR Number | the row |
| Site | the row |
| Lines | line count |
| Total Qty | summed across lines |
| Earliest Delivery | min of Delivery_Date across lines |

Below: full line items grid with Material_Code, Material_Name, Requested_Qty, UOM, WBS_Number, Network, Plant, Delivery_Date, Supplier, Est_Cost_SAR.

### 14.2.5 🧾 Use this PR to create a PO button

Loads the PR into Tab 2 (Create PO). Selectbox state is preserved so you can switch tabs without losing the chosen PR.

## 14.3 Tab 2: 🧾 Create PO

Two sub-tabs: **✍️ Manual entry** and **📄 PDF upload**. Both funnel into the same `purchase_orders` insert path.

### 14.3.1 Vendor picker

Selectbox of all active vendors `<code> · <name>`, plus a "➕ Add new vendor" option that opens an inline expander with Vendor Code, Name, Address, Default Inco Terms, Default Payment Terms. Save → re-renders the parent form with the new vendor pre-selected.

### 14.3.2 PO header form

Three-column layout:

| Column | Fields |
|---|---|
| Left | PO Number * · PO Date · PO Type |
| Middle | PR Number · Quotation No. · Quotation Date |
| Right | Expected Delivery · Inco Terms · Payment Terms |

Plus a second three-column row for Contact (vendor), Contact Email, Mobile, Our Reference, Your Reference, Our Email.

Vendor defaults (Inco / Payment Terms) auto-fill from the master row but remain editable.

### 14.3.3 Manual sub-tab: line items

If a PR was loaded via the Tab 1 shortcut, every PR line pre-fills with `Include = True` and zero Unit_Price + Total_Price (you set them). Otherwise an empty editable grid opens.

Editable columns: Include · Material_Code · Description · Qty · UOM · Unit_Price · Total_Price · WBS_Number · Network · Plant. Rows with `Include = False` are dropped on save.

### 14.3.4 PDF upload sub-tab

| Element | Purpose |
|---|---|
| **File uploader** | Drop the PO PDF |
| **🔎 Extract from PDF button** | Calls `process_po_pdf()` — regex-based extraction of header (PO Number, Vendor, Inco/Payment, Quotation refs, totals) + line items (Sr. No, Material_Code, Description, Qty, UOM, Unit_Price, Total_Price) + PO Annexure delivery schedule (Shipment N / Material Group / Date) |
| **Review extracted PO** | Editable preview of every extracted field. The header form pre-fills. The line items grid pre-fills. Edit anything before saving. |
| **Delivery schedule** | If Annexure parsed, a table shows shipment_no · material_group · target_date |
| **💾 Save PO (from PDF)** | Persists the PO + items + shipment schedule. The original PDF is stored as a BLOB on the `purchase_orders` row (`attachment_blob`/`_name`/`_mime`) for audit. |

**On PO numbers with X-masking:** the sample PDF has the last 4 digits masked as `XXXX` for security. The extractor preserves whatever is on the page verbatim. In production, vendors send full 10-digit numbers and those pass through unchanged.

### 14.3.5 Side-effects on save

- New row in `purchase_orders` with status `'open'`
- One row per item in `po_items` with `rl_bl_family` auto-tagged
- Linked PR rows (matching `PR_Number` + `Site_ID`) flip to `logistics_status='in_po'` so they leave your Incoming PRs queue
- Site HOD gets `po_issued` notification (in-app + WhatsApp gated)

## 14.4 Tab 3: 📋 Open POs

Browse + drill into every open PO.

### 14.4.1 Filters

Site dropdown · Vendor dropdown · PR Number exact-match textbox.

### 14.4.2 KPI strip

OPEN POs count · Total value SAR · PENDING (not yet delivered) count.

### 14.4.3 Per-PO drilldown

Select a PO → section card with PR Number, Vendor, Inco/Payment Terms, PO Date, Expected, Source (manual/pdf_upload), Status, Total. Below: items grid with an RL/BL family chip per row, the parsed delivery schedule (if any), and a list of warehouse assignments.

## 14.5 Tab 4: 🏭 Assign to Warehouse

Route a PO (or a subset of items) to a warehouse for receiving.

### 14.5.1 PO + Warehouse pickers

Two-column row: PO selectbox · Warehouse selectbox (shows `<id> · <name>`).

### 14.5.2 Items selector

Editable grid showing every PO line with an `Include` checkbox (default `True`). Disable lines you don't want to route — they stay with this PO for a future assignment.

### 14.5.3 Header

Expected Delivery date + Notes (visible to Warehouse).

### 14.5.4 📨 Assign to Warehouse button

- Subset = if all items included, encoded as `None` (cleaner audit)
- Subset = if partial, encoded as `JSON list of po_items.id` in `po_assignments.items_subset_json`
- Warehouse user(s) at the chosen WH get a `po_assigned_to_warehouse` notification (in-app + gated WhatsApp)
- PO header's Expected_Delivery auto-fills with this date if not set
- Audit log entry: `ASSIGN_PO_TO_WAREHOUSE`

## 14.6 Tab 5: 🔁 Reschedules

Incoming reschedule requests from Warehouse / Site HOD.

### 14.6.1 Empty state

If no pending requests: empty-state card.

### 14.6.2 Per-request card

| Element | Purpose |
|---|---|
| **Header line** | `PO <number> · DN <number> · From <current_date> → <requested_date>` |
| **Subline** | requested_by + role + reason |
| **Decide popover** | Decision notes textbox + ✅ Approve / ❌ Reject buttons (reject requires reason) |

Approval auto-pushes the new date to: the PO header `Expected_Delivery`, the `po_assignments.Expected_Delivery`, and the DN `DN_Date` (if linked).

## 14.7 Tab 6: 🛑 Force-Close

Use sparingly. Force-closing notifies Admin + originating Site HOD immediately with the reason.

### 14.7.1 Target radio

`PR (entire)` · `PO (entire)` · `PO line (single item)`

### 14.7.2 Reason textbox

Mandatory, minimum 3 characters. Logged verbatim to `po_force_closures` + audit.

### 14.7.3 Target picker (changes per radio)

- **PR:** dropdown of open PR numbers in Logistics queue
- **PO:** dropdown of open POs
- **Line:** PO dropdown → then line dropdown showing only `open` / `partially_delivered` lines

### 14.7.4 🛑 Force-close button

Single confirm-and-execute. Behind it:
- `pr_master.status='closed'` + `logistics_status='force_closed'` (for PR target)
- `purchase_orders.status='force_closed'` + line statuses for `force_closed` (for PO target)
- `po_items.line_status='force_closed'` (for line target)
- New row in `po_force_closures` with reason
- Notifications fan-out: Admin (`recipient_role='admin'`) + originating Site HOD (`recipient_role='hod', recipient_site=<site>`), severity = `critical`

### 14.7.5 Recent force-closures (audit)

AgGrid of the last 50 closures. Read-only.

## 14.8 Tab 7: ↩️ Vendor Returns

Raise a return to the vendor against a PO. Returning a line REOPENS the PO so it shows in your active queue again.

### 14.8.1 PO picker + scope

PO selectbox (all POs incl. closed). Then radio: `Whole PO` or `Single line`. If single line, a line picker appears.

### 14.8.2 Return details form

- **Return quantity** (number, > 0)
- **Reason** (textarea, mandatory)
- **Expected resupply** (date picker, defaults to today + 14)
- **Notes** (optional)

### 14.8.3 ↩️ Raise vendor return button

- New row in `po_returns` with `raised_by_role='logistics'`
- `po_items.Returned_Qty` bumps; `line_status` flips back to `partially_delivered` or `open`
- If the PO had been closed, header flips back to `partially_delivered` and `closed_by` / `closed_at` / `close_reason` are nulled
- Notification: `vendor_return_raised` to logistics inbox + WhatsApp gated

### 14.8.4 Open returns table

AgGrid of all open returns. Read-only.

## 14.9 Tab 8: 📂 History

Read-only archive. Two sub-tabs: **Closed POs** and **Closed PRs**.

- Closed POs: status in `closed` / `force_closed` / `cancelled`
- Closed PRs: any PR where `pr_status='closed'` OR `logistics_status` in `force_closed`/`closed`/`in_po`

For force-closures with the reason history, use Tab 6 → Recent force-closures table.

## 14.10 Logistics — Use Cases

### Use Case 1: Issue PO from a fresh PR

1. 📥 Incoming PRs → pick PR → 🧾 Use this PR to create a PO
2. 🧾 Create PO → ✍️ Manual entry → pick / add vendor, fill header, edit Unit Price per line
3. 💾 Save PO → balloon animation → notification fires to Site HOD

### Use Case 2: Issue PO from a vendor's emailed PDF

1. Receive PO PDF from vendor (sample format: 🧾 Create PO → 📄 PDF upload)
2. Drop the PDF → 🔎 Extract from PDF
3. Review extracted header + line items + delivery schedule
4. Correct anything the parser missed
5. 💾 Save PO (from PDF) — original PDF is archived

### Use Case 3: Route a PO to a warehouse

1. 🏭 Assign to Warehouse → pick PO + warehouse
2. Disable lines you want to route separately later
3. Pick Expected Delivery, add a routing note
4. 📨 Assign — warehouse user is pinged

### Use Case 4: Handle a Warehouse reschedule ask

1. 🔁 Reschedules → review the request (reason text + current vs requested date)
2. Decide → notes + ✅ Approve
3. PO Expected_Delivery auto-updates; Warehouse + requester get the decision notification

### Use Case 5: Force-close a stale PR

1. 🛑 Force-Close → radio = `PR (entire)`
2. Write the reason (e.g. "Project cancelled by site management 2026-06-15")
3. Pick PR → 🛑 Force-close PR
4. Admin + Site HOD see a critical notification in their bell

## 14.11 Logistics — FAQ

**Q: A PR appears in Incoming PRs but I can't issue a PO against it — Material_Code is wrong.**
A: PR lines come from the Site HOD's inventory catalogue. If the Material_Code is wrong, the SK created it wrong upstream. Don't fix in your PO — bounce the PR back via WhatsApp / chat, ask the site to re-submit. There's no in-app "reject PR" button (yet) because PR rejection should be a conversation, not a click.

**Q: PDF extraction picked up wrong qty.**
A: The Review preview is editable. Always check Qty / Unit_Price / Total_Price before saving. The extractor is calibrated against the General Industries sample layout; other vendor templates may need template additions.

**Q: I want to assign a PO to multiple warehouses.**
A: Two separate 🏭 Assign actions — one per warehouse. The `items_subset_json` field on each assignment row keeps them distinct.

**Q: Why can't I see prices in Tab 4 / on assignment cards?**
A: You CAN see prices in Tab 3 (Open POs drilldown). Tab 4 (Assign to Warehouse) shares the read with Warehouse users, so prices are hidden there for consistency.

**Q: Force-closure undo?**
A: Not yet. Once force-closed, you'd have to either raise a Vendor Return (reopens the line) or admin-edit via Master DB Editor. A 24-hour undo window is on the v3.0 backlog.

---

# 15. Warehouse Portal Manual

The Warehouse Portal is the physical-receiving and DN-preparation side. Role-locked to `{warehouse_user, admin}`. Six tabs. **Prices are completely hidden in every view** — three independent enforcement layers guarantee `Unit_Price`, `Total_Price`, `Total_Amount`, `Freight_Charges`, `Handling_Charges`, `Discount_Amount`, `Amount_In_Words` are never visible to a warehouse user.

## 15.1 Pages visible to Warehouse User

- 📦 Live Dashboard
- 🏭 Warehouse Portal (this page)
- 📊 Reports

## 15.2 Sidebar warehouse resolution

- A `warehouse_user` is bound to a single warehouse via `users.Warehouse_ID`. The portal auto-resolves from the user's profile.
- An `admin` shadowing the portal gets a sidebar `"🏭 Shadow warehouse"` selectbox listing every active warehouse.
- If neither resolves: red error card `"🛑 Your account is not bound to a warehouse. Ask Admin to set your Warehouse_ID in Admin Portal → Users."`

## 15.3 Page title

`🏭 Warehouse <ID>` in gold, with `(prices hidden — Logistics-only)` muted caption next to it as a permanent visual reminder.

## 15.4 Tab 1: 🔔 Incoming Assignments

POs Logistics has routed to this warehouse.

### 15.4.1 Hero strip

| Card | Source |
|---|---|
| **AWAITING ACK** | Count of assignments with `status='assigned'` |
| **ACKED** | Status = `acknowledged` (you've seen them, waiting on goods) |
| **IN/RECEIVED** | Status in `partial` / `received` |

### 15.4.2 Assignments grid

AgGrid: Assign # · PO No. · PR No. · Vendor · Dest Site · Expected · Assigned · Acked · Status. **No price columns.**

### 15.4.3 Acknowledge action

Selectbox of `status='assigned'` rows. Picking one → ✅ Acknowledge button → flips to `acknowledged` and pings Logistics with `warehouse_acknowledged` notification (off by default in WhatsApp toggles to keep noise low).

## 15.5 Tab 2: 📦 Receive Goods

Record qty actually received at this warehouse against an acknowledged assignment.

### 15.5.1 Assignment picker

Selectbox of `status IN ('acknowledged', 'partial')` rows.

### 15.5.2 PO snapshot card (no prices)

| KV row | Value |
|---|---|
| PO Number | the PO |
| Vendor | `<code> · <name>` |
| Inco / Payment | the terms |
| Expected | the date |

Total_Amount, Freight_Charges, Handling_Charges, Discount_Amount, Amount_In_Words are stripped from the header dict before render. **Never visible.**

### 15.5.3 Receive grid

Editable grid showing every line on the assignment:

| Column | Source |
|---|---|
| id | po_items.id (disabled — read-only) |
| Material_Code | line code |
| Description | line description |
| UOM | unit |
| rl_bl_family | family chip (RL / BL / blank) |
| Qty | ordered (read-only) |
| Delivered_Qty | already received cumulative (read-only) |
| Open_Qty | computed `Qty − Delivered + Returned` (read-only) |
| **Receive Now** | EDITABLE — type the qty you physically received this event |

### 15.5.4 📥 Record receipt button

- Validates: at least one row with `Receive Now > 0`
- Over-deliver guard: `Delivered_Qty − Returned_Qty + new_qty` may NOT exceed ordered Qty. If it does, the entire batch is rejected with a friendly message naming the line.
- On success: bumps each line's `Delivered_Qty`, flips `line_status` (`delivered` if `Delivered − Returned ≥ Qty`, else `partially_delivered`)
- Rolls assignment status (`received` if every line on the parent PO is `delivered`, else `partial`)
- Rolls PO header status to match
- Notification: `warehouse_received` to Logistics with line count

## 15.6 Tab 3: 📝 Prepare DN

Build a Delivery Note for a site. **RL/BL strict separation is enforced here** — a DN cannot span both families. If you try, the action is rejected with: *"Strict separation violated: this DN spans multiple RL/BL families. Prepare one DN per family."*

### 15.6.1 Source pickers

- PO No. selectbox (assignments with status `acknowledged` / `partial` / `received`)
- Destination Site selectbox

### 15.6.2 Items grid

Editable grid with read-only inventory columns (Material_Code, Description, UOM, rl_bl_family, Qty, Delivered_Qty, Returned_Qty) and editable shipping columns:

| Column | Purpose |
|---|---|
| **Ship Qty** | What you're sending on this DN (must be > 0 to include) |
| **Lot_Number** | If you tracked the lot at receive time |
| **Expiry_Date** | Lot expiry |
| **Remarks** | Free text |

### 15.6.3 DN header

Three-column row: DN Date · Vehicle No · Driver Name · Driver Phone · Prepared By (auto-filled with your username) · Remarks.

### 15.6.4 📝 Save DN draft button

- Validates: at least one row with Ship Qty > 0
- Over-ship guard: `available = Delivered_Qty − Returned_Qty − Σ(live DN qty)` per line; ship qty cannot exceed available
- RL/BL strict-separation check: if the items span both families, REJECTED
- On success: new `delivery_notes` row with `status='draft'` and one `dn_items` row per line. The DN header carries `rl_bl_family` if non-NULL.
- Toast: 📝 DN <number>

### 15.6.5 DN numbering convention

`DN-<WAREHOUSE_ID>-<YYYYMMDD>-<seq>` — seq resets per (warehouse, day). Example: `DN-WH-A-20260616-003`.

## 15.7 Tab 4: ✈️ Outbound DNs

Track every DN this warehouse has prepared.

### 15.7.1 Status filter

Multi-select of every DN status. Defaults to `draft`, `pending_logistics`, `pending_hod`, `pending_sk` (the active ones).

### 15.7.2 DN grid

AgGrid of every matching DN with full state metadata.

### 15.7.3 Submit-to-Logistics action

When the filter shows any `draft` row, a "Submit a draft to Logistics" section appears with a selectbox of drafts + 📨 Submit to Logistics button. Submission flips status to `pending_logistics` and pings Logistics with the DN-approval-queue notification.

### 15.7.4 Per-DN drilldown

Select a DN → section card showing PO, Site, Status, RL/BL family, Vehicle/Driver, and the full signature trail (Logistics decided by/decision, HOD decided by, SK received by). Items grid below.

### 15.7.5 🔁 Request reschedule expander

If the date you targeted isn't going to work, raise a reschedule from here. Same flow as the HOD's In-Transit tab (defaults, min_value=today, mandatory reason).

## 15.8 Tab 5: ↩️ Returns from Site

Raise a return when a site flags defective material from a DN this warehouse delivered.

### 15.8.1 DN picker

Selectbox of DNs in `received` / `pending_sk` / `hod_approved` states.

### 15.8.2 Items grid

Editable grid with a `Return Qty` column. Filling > 0 on a line includes it in the return.

### 15.8.3 Reason textarea

Mandatory.

### 15.8.4 ↩️ Raise return to vendor button

- Internally calls `record_internal_return()` which fans out to `raise_vendor_return()` per affected line
- Each line: `po_returns` row written with `raised_by_role='warehouse_user'`, dn_item flagged `returned`, parent po_item's `Returned_Qty` bumps and `line_status` flips back to `partially_delivered` or `open`
- PO header reopens if it had been `closed`
- Notification fires to Logistics

## 15.9 Tab 6: 📂 History

Read-only. Two sub-tabs: **Completed DNs** (status in `received` / `rejected` / `cancelled`) and **Closed assignments**.

## 15.10 Warehouse — Use Cases

### Use Case 1: Receive a vendor delivery

1. 🔔 Incoming Assignments → ✅ Acknowledge the assignment when you see it
2. Goods physically arrive
3. 📦 Receive Goods → pick the assignment → type the actual received qty per line → 📥 Record receipt

### Use Case 2: Ship to a site (single family)

1. 📝 Prepare DN → pick PO + destination site
2. Type Ship Qty per line — keep all RL or all BL
3. Fill DN header (Vehicle, Driver, Date)
4. 📝 Save DN draft → status = `draft`
5. ✈️ Outbound DNs → select the draft → 📨 Submit to Logistics

### Use Case 3: Ship RL + BL to the same site on the same day

You need TWO DNs:
1. Prepare DN with only RL lines → save + submit
2. Prepare second DN with only BL lines → save + submit
The system rejects any attempt to combine them on a single DN.

### Use Case 4: A site rejects part of a DN — raise to vendor

1. ↩️ Returns from Site → pick the DN
2. Type Return Qty on the offending line
3. Reason: "Site reported defective surface coating"
4. ↩️ Raise return to vendor — PO + po_item reopen; Logistics sees the return in their tab

### Use Case 5: Request a reschedule

1. ✈️ Outbound DNs → drill into the affected DN
2. 🔁 Request reschedule for this DN → pick new date + reason → Submit

## 15.11 Warehouse — FAQ

**Q: Why don't I see prices anywhere?**
A: Warehouse role is intentionally blind to commercial data. Three independent enforcement layers strip every monetary field. If you genuinely need a price (e.g. to file a damage claim), ask Logistics or Admin.

**Q: My over-ship guard rejected a DN. I just received everything from the vendor.**
A: Probably another DN is already drafted (or submitted) shipping a slice of that line. Check ✈️ Outbound DNs filter for live DNs against the same PO. If one was abandoned, ask Logistics to reject/cancel it so the qty frees back up.

**Q: RL and BL are going to the same site on the same truck. Can I combine?**
A: No. Two DNs. The strict separation is by design (different testing standards, different storage requirements). The site receives them as two separate DNs in their Receipt Staging queue.

**Q: Where did the "Receive Goods" assignment go? It was here this morning.**
A: Once every line on the parent PO is fully delivered, the assignment moves from `partial` to `received` and stops appearing in Tab 2 (which filters to `acknowledged` + `partial`). Check the assignment grid in Tab 1.

**Q: Can I edit a DN after submitting to Logistics?**
A: Not directly. Ask Logistics to reject it (✈️ Outbound DNs shows the rejection if it happens) — then it returns to `draft` for you to edit.

---

# 16. Cross-Role Procurement Walk-through

A single happy-path narrative threading all five roles together, useful for onboarding.

## 16.1 The scenario

Site GI-PS01 needs 50 RL panels and 20 BL bricks. Total 3 working days from PR raise to physical receipt.

## 16.2 Hour by hour

| When | Who | What |
|---|---|---|
| Day 1 — 8:00 AM | **Site HOD** | Opens HOD Portal → 📋 Purchase Requests. Creates 2 PR lines (50 RL panels, 20 BL bricks) with PR Number 3000099999, WBS Number 4003951, Network 4003951-PROJ-A, Plant GI-PS01, Delivery_Date Day 3. |
| Day 1 — 8:15 AM | **Site HOD** | Opens the **🚚 Submit PR(s) to Logistics Portal** expander → multi-selects PR 3000099999 → 📨 Submit Selected to Logistics. |
| Day 1 — 8:15 AM | (auto) | `pr_submitted_to_logistics` notification → Logistics inbox (red badge). WhatsApp ping if enabled. |
| Day 1 — 9:00 AM | **Logistics** | Sees PR 3000099999 in 📥 Incoming PRs queue. Drills in — 2 lines, RL + BL. Clicks 🧾 Use this PR to create a PO. |
| Day 1 — 9:30 AM | **Logistics** | Switches to 🧾 Create PO → manual entry. Picks vendor "Carborundum Universal" (0000110341), Inco/Payment auto-fill, fills PO Number 4720033030, Unit_Price per line, Total_Price computed. 💾 Save PO. |
| Day 1 — 9:30 AM | (auto) | PR rows flip to `logistics_status='in_po'` → disappear from Logistics queue. Site HOD gets `po_issued` notification with vendor + PO number. |
| Day 1 — 10:00 AM | **Logistics** | 🏭 Assign to Warehouse → picks PO 4720033030 + Warehouse WH-A + Expected Delivery Day 2 evening. 📨 Assign. |
| Day 1 — 10:00 AM | (auto) | `po_assigned_to_warehouse` notification → WH-A users' inbox. WhatsApp ping if enabled. |
| Day 1 — 11:00 AM | **Warehouse user (WH-A)** | 🔔 Incoming Assignments → ✅ Acknowledge assignment #N. Goes about their day. |
| Day 2 — 5:00 PM | **Warehouse user** | Physical truck arrives from vendor. 📦 Receive Goods → assignment #N → type 50 in Receive Now for the RL line, 20 for the BL line. 📥 Record receipt. Both lines flip to `delivered`. |
| Day 2 — 5:30 PM | **Warehouse user** | 📝 Prepare DN → pick PO 4720033030, destination GI-PS01. Types Ship Qty 50 for RL line, attempts to add BL line — rejected (RL/BL strict separation). Saves DN-WH-A-20260617-001 for RL only. |
| Day 2 — 5:35 PM | **Warehouse user** | Repeats: new DN with only the BL line → DN-WH-A-20260617-002. |
| Day 2 — 5:40 PM | **Warehouse user** | ✈️ Outbound DNs → submits both drafts to Logistics. |
| Day 2 — 5:45 PM | **Logistics** | Sees DN-WH-A-20260617-001 + 002 in their DN approval queue. Confirms delivery date Day 3 AM. Approves both. |
| Day 2 — 5:45 PM | (auto) | DN status flips to `pending_hod`. Site HOD gets `dn_logistics_approved` notification. |
| Day 2 — 6:00 PM | **Site HOD** | Logs in, sees red bell badge `2 unread`. Opens inbox → drills into the DNs via 🚚 DN Approvals tab. Approves both. |
| Day 2 — 6:00 PM | (auto) | DN status `pending_sk`. Two mirror rows in `pending_receipts.status='pending_sk'`. Site SK gets `dn_auto_generated` notification. |
| Day 3 — 8:30 AM | **Truck arrives at site GI-PS01** | |
| Day 3 — 8:45 AM | **Site SK** | Opens Entry Log → 📦 Receipt Staging. The new **🚚 Incoming Delivery Notes from Warehouse** expander is open at the top, showing both DNs. Inspects both, confirms physical match, clicks ✅ Mark as Received on each. |
| Day 3 — 8:45 AM | (auto) | Two `receipts` rows written (with DN_Number, Warehouse_ID, PO_Number_Source). Both DNs flip to `received`. `pending_receipts` mirror rows deleted. Inventory cache busted. Live Dashboard shows the new stock immediately. |

## 16.3 Side-path examples

### A site rejects a DN

Same scenario, but on Day 2 at 6:00 PM, Site HOD inspects the line preview on DN-002 and notices the BL qty doesn't match the PR. Rejects DN-002 with note "BL qty in DN is 25, PR asked for 20 — bounce back".

- DN-002 status → `rejected`, `rejection_reason` stored
- Warehouse user gets the rejection notification with the reason
- The BL line on the PO frees up qty (the over-ship guard recalculates available since DN-002 is no longer "live")
- Warehouse prepares DN-003 with correct qty

### Logistics force-closes the PR mid-flight

Project cancelled by management on Day 1 evening:

- Logistics → 🛑 Force-Close → radio = PR → reason "Project cancelled per CFO email 2026-06-16" → 🛑 Force-close PR
- Admin + Site HOD see critical-severity notification in their bell
- The pending PO can either stay (if vendor already shipped) or also be force-closed
- Site HOD's 🚚 In-Transit → Force-closures sub-tab shows the closure with the reason

### Warehouse asks for reschedule

Receiving day Truck doesn't show up — vendor delay:

- Warehouse → ✈️ Outbound DNs → drill into DN-001 → 🔁 Request reschedule → new date Day 4 → reason "Vendor delivery delayed 24h confirmed by vendor email"
- Logistics → 🔁 Reschedules → reviews → ✅ Approve with notes "Confirmed with vendor"
- PO Expected_Delivery, po_assignments.Expected_Delivery, AND DN_Date all flip to Day 4 in one transaction
- Site HOD sees the decision notification

---

# 17. Operations & Hosting — the after-launch chapter

This chapter is what the host operator (you, today: johnsonandrew) needs every day after the system is live. Everything before this chapter is about the application; this chapter is about keeping the application running on a real Mac that real users hit through `https://gi.giinventory.com`.

If you're reading this and you haven't deployed yet, do **§14.2 First-time setup** straight through, then come back to this chapter as needed.

## 14.1 The system in one diagram

```
                    ┌──────────────────────────┐
                    │ User on phone / laptop   │
                    │ (any @generalindustries  │
                    │  .net email)             │
                    └────────┬─────────────────┘
                             │ HTTPS (TLS 1.3)
                             ▼
                    ┌──────────────────────────┐
                    │ Cloudflare Edge          │
                    │ • Access email allow-list│
                    │ • DDoS + WAF             │
                    │ • Cert auto-renewal      │
                    └────────┬─────────────────┘
                             │ encrypted tunnel
                             │ (outbound from Mac, no open port)
                             ▼
   ┌─────────────────────────────────────────────────────────┐
   │ Your Mac (johnsonandrew, FileVault on)                  │
   │                                                         │
   │  ┌─ launchd ────────────────────────────────────────┐   │
   │  │  com.gi.streamlit        → Streamlit on :8501    │   │
   │  │  com.gi.whatsapp-worker  → drives Chrome / WA Web│   │
   │  │  com.gi.cloudflared      → outbound tunnel       │   │
   │  │  com.gi.backup           → nightly @ 02:00       │   │
   │  └──────────────────────────────────────────────────┘   │
   │                                                         │
   │  ┌─ files ──────────────────────────────────────────┐   │
   │  │  gi_database.db          (sqlite, FileVault-     │   │
   │  │                           encrypted at rest)     │   │
   │  │  uploads/<Site>/<doc>/   (BLOBs mirrored)        │   │
   │  │  ~/.cloudflared/         (tunnel creds)          │   │
   │  └──────────────────────────────────────────────────┘   │
   │                                                         │
   │  ┌─ local services ─────────────────────────────────┐   │
   │  │  Ollama (qwen2.5-coder, llama3.1, qwen2.5vl)     │   │
   │  │  Google Chrome (signed-in WhatsApp Web tab)      │   │
   │  └──────────────────────────────────────────────────┘   │
   └─────────────────────────────────────────────────────────┘
                             │
                             ▼ nightly 02:00
                    ┌──────────────────────────┐
                    │ iCloud Drive backup      │
                    │ ~/…/GI_Hub_Backups/      │
                    │ • 14-day retention       │
                    │ • DB + uploads/ mirror   │
                    └──────────────────────────┘
```

**No part of this stack stores your inventory data anywhere except the Mac itself.** Cloudflare forwards encrypted bytes. Apple stores encrypted backups. The application database lives on a FileVault-encrypted disk on your machine, in your office.

## 14.2 First-time setup (compressed checklist)

The full step-by-step is in `host_setup/README.md`. Compressed checklist for cross-referencing:

1. **Install cloudflared** (binary download, no Brew):
   ```bash
   sudo rm -f /usr/local/bin/cloudflared
   cd ~/Downloads
   curl -L -o cloudflared.tgz https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz
   tar -xzf cloudflared.tgz
   sudo mv cloudflared /usr/local/bin/cloudflared
   sudo chmod +x /usr/local/bin/cloudflared
   ```
2. **Create the tunnel:**
   ```bash
   cloudflared tunnel login                    # opens browser → pick giinventory.com
   cloudflared tunnel create gi-hub            # prints UUID — copy it
   cloudflared tunnel route dns gi-hub gi.giinventory.com
   ```
3. **Write tunnel config:** copy `host_setup/cloudflared_config.yml.example` to `~/.cloudflared/config.yml` and substitute the UUID + your username.
4. **Install launchd services:**
   ```bash
   cd "/Users/johnsonandrew/Downloads/CNCEC PROJECT"
   ./host_setup/scripts/install.sh
   ./host_setup/scripts/install.sh --status
   ```
   All four should be ✓ green (backup is ⏸ yellow until 02:00 — that's correct).
5. **Set up Cloudflare Access** (email allow-list — see §14.5 for details and rationale):
   - Cloudflare → **Zero Trust** → Access → Applications → Add → Self-hosted
   - Domain: `gi.giinventory.com`
   - Policy → Action: Allow → Include: **Emails ending in** `@generalindustries.net`
6. **Pair WhatsApp Web on the host Mac** (Chrome recommended; Safari works too):
   - Open `https://web.whatsapp.com` → scan QR with phone → Pin Tab.
7. **Hardening (mandatory before sharing the URL):**
   - **FileVault on:** System Settings → Privacy & Security → FileVault → On.
   - **Change every default password** in the app (User Management).
   - **Screensaver off / no lock:** System Settings → Screen Saver → Start after = Never.
   - **No automatic sleep on power:** System Settings → Battery → Prevent automatic sleeping = On.
8. **Pull a baseline backup** before letting anyone in:
   ```bash
   ./host_setup/scripts/backup_db.sh
   ```

## 14.3 Daily operations — the 4 commands you'll actually use

| Need | Command |
|---|---|
| Show service status | `./host_setup/scripts/install.sh --status` |
| Stream all four log files | `./host_setup/scripts/install.sh --logs` |
| One-shot backup right now | `./host_setup/scripts/backup_db.sh` |
| Restart Streamlit after `git pull` | `./host_setup/scripts/restart_app.sh` |

Logs live in `~/Library/Logs/`:
- `gi-streamlit.{log,err}`
- `gi-whatsapp-worker.{log,err}`
- `gi-cloudflared.{log,err}`
- `gi-backup.{log,err}`

## 14.4 Updating the host with new code or features

This is the most common operation. **Average downtime: ~3 seconds.**

```bash
cd "/Users/johnsonandrew/Downloads/CNCEC PROJECT"

# 1. Pull the new code
git pull

# 2. If requirements.txt changed (added/upgraded a Python package)
.venv/bin/pip install -r requirements.txt

# 3. Restart Streamlit only
./host_setup/scripts/restart_app.sh

# If worker code (whatsapp_worker.py) changed too:
./host_setup/scripts/restart_app.sh --worker

# If you also changed cloudflared or backup config (rare):
./host_setup/scripts/restart_app.sh --all

# 4. Verify everything came back
./host_setup/scripts/install.sh --status
```

`init_db()` runs on every Streamlit start, so any new schema columns or tables in the pulled commit are auto-applied **before** any user can sign in. You never run SQL migrations by hand.

For schema changes that need backfilling (rare), the relevant `init_db()` block also handles the backfill — those are clearly commented "one-time backfill" in `database.py`.

### Rolling back if a release breaks something

```bash
git log --oneline -5                                  # find the last-known-good commit hash
git checkout <good-commit-hash>
./host_setup/scripts/restart_app.sh
```

Your database has not been migrated backwards because schema changes are additive (new columns / tables — old columns are never dropped). Old code can read the newer schema fine.

## 14.5 Security & data safety — what to tell management

The full audit-quality version is in `handoff.md`. The customer-friendly version follows.

### Where does our data physically live?

- **On the Mac in our office.** The database file (`gi_database.db`) and every uploaded attachment (delivery notes, MTC certificates, photos) sit on the Mac's own disk. Nothing is uploaded to a third-party database service.
- **FileVault encryption** scrambles the entire disk. A stolen Mac is a brick — the database is unreadable without the disk password.
- **Encrypted backups to iCloud Drive** add a second copy at Apple, also encrypted, also under your control.

### Who can reach the application?

- **Only the email addresses we explicitly whitelist.** Cloudflare Access checks the user's email at the edge — anyone NOT on the list never reaches the Mac, never sees the login page, never causes a single log line on our server. The list is one-click manageable in the Cloudflare dashboard.
- **And inside the app:** users still sign in with the role-based bcrypt login. Two locks on the door, not one.

### How is the connection secured?

- Every byte travels over HTTPS (TLS 1.3 — the same encryption layer used by banks).
- Cloudflare forwards encrypted bytes; they cannot read the contents. (They terminate TLS at their edge, then re-encrypt to our Mac via the Tunnel's own mTLS channel. The application body is opaque to them.)
- Cloudflare certifications relevant to this: SOC 2 Type II, ISO 27001, PCI DSS. Public reference: https://www.cloudflare.com/trust-hub/

### What's the attack surface?

- **Zero public ports.** Our Mac never accepts an inbound TCP connection. The Tunnel is an outbound connection from us to Cloudflare. A hacker port-scanning the internet sees nothing.
- The application gates every consequential action behind a role check (HOD/Admin/etc.). Even an authenticated user can't escalate privileges from the UI.
- Every action writes a permanent audit log row (`system_audit_log`) with username + timestamp + details. Admin can review or export at any time.

### What happens if X fails?

| Failure | Impact | Recovery |
|---|---|---|
| Power outage | App offline for the duration | UPS battery (recommended, ~$50) keeps it up; otherwise it restarts on power-on |
| Internet outage | External users blocked; office network still reaches `http://localhost:8501` | None needed — auto-resumes when ISP returns |
| Cloudflare incident | External access blocked | Switch to Tailscale Funnel in ~5 minutes (documented in handoff.md) |
| Host Mac dies | App down until replacement | Restore last backup to a new Mac, ~30 min, lose ≤24h of data |
| Disk corruption | DB unusable | Restore from a backup (14 days kept), ~5 min |
| Accidental delete | Single record gone | Restore from a backup, ~5 min |
| Internal user steals data | Limited scope (own role) | Audit log shows username + exact action + timestamp for investigation |

### What's the actual cost?

- Domain (`giinventory.com`): ~$8 / year on Cloudflare Registrar (at-cost).
- Cloudflare Tunnel: free, unlimited bandwidth.
- Cloudflare Access ≤50 users: free.
- iCloud storage (5 GB free, enough for this app): free.
- WhatsApp notifications via WhatsApp Web on the host Mac: free.
- Local Ollama AI models: free, computed on the Mac.
- Mac electricity: ~$50–80 / year.

**Total: $58–88 / year, all-in.**

### Non-negotiables for the operator

These are not optional. Skipping any of them defeats the security model.

1. **FileVault must be on.**
2. **The host Mac stays in a physically secured location** (locked IT room or office).
3. **Default passwords (`admin/admin2026` etc.) must be changed** on day one.
4. **The Cloudflare Access policy must be set BEFORE the URL is shared** with any user.
5. **The Mac must not be used for personal browsing.** Keep it as a single-purpose appliance.
6. **Apple Software Updates** for macOS get installed within 7 days of release.

## 14.6 Backup & restore

### Automatic nightly

`com.gi.backup` fires at **02:00** every day via `StartCalendarInterval` in the launchd plist. The script (`host_setup/scripts/backup_db.sh`) does:

1. **SQLite online backup** to `~/Library/Mobile Documents/com~apple~CloudDocs/GI_Hub_Backups/db/gi_database_YYYYMMDD_HHMMSS.db`. The online backup API takes a consistent snapshot even while Streamlit is reading + writing, with no downtime.
2. **rsync mirror** of `uploads/` to `~/…/GI_Hub_Backups/uploads_latest/` (incremental — only changed files transfer).
3. **Prune** snapshots older than 14 days.
4. **Optional second destination** if you've set the env var `GI_BACKUP_EXTRA` (e.g. an external SSD path or rclone-mounted Google Drive).

Verify the most recent backup any time:
```bash
ls -lht ~/Library/Mobile\ Documents/com~apple~CloudDocs/GI_Hub_Backups/db/ | head -5
```

### Manual snapshot before risky work

Always run a snapshot before:
- Major version upgrade (`git pull` that touches `database.py`)
- Bulk edits via Admin → Master DB Editor
- Switching the host Mac to a new machine
- Migrating to a different hosting path

```bash
./host_setup/scripts/backup_db.sh
```

### Restore

```bash
# 1. Stop the app first (the live DB must not be open during a restore)
launchctl unload ~/Library/LaunchAgents/com.gi.streamlit.plist

# 2. Copy the chosen snapshot into place
cp "~/Library/Mobile Documents/com~apple~CloudDocs/GI_Hub_Backups/db/gi_database_20260613_020000.db" \
   "/Users/johnsonandrew/Downloads/CNCEC PROJECT/gi_database.db"

# 3. Start the app again
launchctl load -w ~/Library/LaunchAgents/com.gi.streamlit.plist
./host_setup/scripts/install.sh --status
```

For `uploads/` files, restore from the latest mirror:
```bash
rsync -a "~/Library/Mobile Documents/com~apple~CloudDocs/GI_Hub_Backups/uploads_latest/" \
         "/Users/johnsonandrew/Downloads/CNCEC PROJECT/uploads/"
```

### Off-site copy (optional but recommended)

iCloud Drive is one copy on Apple's servers. For a truly off-Apple second copy:

| Option | Free quota | One-time setup |
|---|---|---|
| **Backblaze B2** | 10 GB free, 1 GB/day egress | 15 min — `brew install rclone`, configure B2 backend, point `GI_BACKUP_EXTRA` at a mounted rclone mount or add a second `rclone sync` step in `backup_db.sh` |
| **Cloudflare R2** | 10 GB free, no egress fees | 20 min — same as B2, point rclone at the R2 endpoint |
| **External SSD** | depends on disk | 0 min — `export GI_BACKUP_EXTRA="/Volumes/Backup_SSD/GI_Hub"` |
| **Private GitHub repo** | unlimited <100 MB files | 5 min — add a `git add gi_database.db && git commit && git push` step in `backup_db.sh` |

Recommended pair: **iCloud (always on, encrypted) + Backblaze B2 (off-Apple, off-Mac, off-Cloudflare)**. Three independent failure domains.

## 14.7 Cloudflare cheatsheet

You don't need to be a Cloudflare expert. The four things you'll actually touch:

### Add or remove an allowed email

1. Cloudflare dashboard → **Zero Trust** (left sidebar, near bottom)
2. Access → Applications → click **GI Hub Warehouse**
3. Policies → click **GI Staff only** (or whatever you named it)
4. Edit the **Include** rule:
   - To allow specific addresses: switch selector to **Emails** and add the address comma-separated.
   - To allow a whole domain: keep **Emails ending in** and edit the value (e.g. `@generalindustries.net`).
5. Save.

Changes take effect within 60 seconds globally. No need to restart anything on the Mac.

### Change the tunnel hostname

```bash
cloudflared tunnel route dns gi-hub anotherhost.giinventory.com
./host_setup/scripts/restart_app.sh --all
```

Cloudflare automatically updates DNS. The old hostname keeps working until you delete its CNAME.

### Check tunnel health from the Cloudflare side

Cloudflare dashboard → Zero Trust → Networks → Tunnels → `gi-hub`.

You'll see one or more "Connectors" (one per running `cloudflared` instance). Each shows latency, packet loss, and uptime. If a Connector is missing or grey, your `com.gi.cloudflared` service isn't healthy — check `~/Library/Logs/gi-cloudflared.err`.

### Add custom block / allow rules

Cloudflare → giinventory.com → Security → WAF. You can:
- Block known bad IPs / countries (not usually necessary — Access does the heavy lifting).
- Rate-limit specific paths (e.g. limit `/login` to 10 attempts/minute per IP — recommended).

## 14.8 Troubleshooting cheat sheet

| Symptom | Most likely cause | First thing to try |
|---|---|---|
| `https://gi.giinventory.com` shows Cloudflare 502 | Streamlit isn't running | `./host_setup/scripts/install.sh --status` — if Streamlit is ⏸ red, check `gi-streamlit.err` |
| `https://gi.giinventory.com` shows Cloudflare 530 | Tunnel isn't connected | `tail ~/Library/Logs/gi-cloudflared.err` — usually a typo in `~/.cloudflared/config.yml` |
| Cloudflare Access login page rejects a valid email | Email not on the policy | Zero Trust → Access → Applications → GI Hub → policy → confirm address is in the Include list |
| App loads but WhatsApp messages don't deliver | WhatsApp Web tab closed or signed out | `open -a "Google Chrome" "https://web.whatsapp.com"` — re-pair, leave the tab pinned |
| WhatsApp delivers, but Cmd+W AppleScript fails first time | Accessibility permission not granted | macOS prompt should appear; click Allow once. Verify under System Settings → Privacy & Security → Accessibility |
| Backup directory empty | iCloud Drive disabled | System Settings → Apple Account → iCloud → iCloud Drive → On. Re-run `backup_db.sh` |
| AI features dead | Ollama not running | `ollama serve` (or it's already running — check `ollama ps`). Confirm via Admin → Settings → 🤖 AI Connection |
| Timestamps wrong | TZ env var not picked up | `launchctl unload ~/Library/LaunchAgents/com.gi.streamlit.plist && launchctl load -w ~/Library/LaunchAgents/com.gi.streamlit.plist` |
| Streamlit `exit 126` | Permissions on the venv binary | `chmod +x .venv/bin/streamlit` then `./host_setup/scripts/restart_app.sh` |
| Need to free disk fast | Old backups | `find ~/Library/Mobile\ Documents/com~apple~CloudDocs/GI_Hub_Backups/db -name 'gi_database_*.db' -mtime +7 -delete` |
| Want a "full uninstall, keep data" | — | `./host_setup/scripts/uninstall.sh` — services removed, DB + uploads + backups untouched |

If you hit something that's not in this table, capture three things and send to the developer:

```bash
./host_setup/scripts/install.sh --status > /tmp/status.txt
tail -200 ~/Library/Logs/gi-streamlit.err > /tmp/streamlit-err.txt
tail -200 ~/Library/Logs/gi-whatsapp-worker.err > /tmp/worker-err.txt
```

---

## Document end

This manual covers every page, tab, button, table, and field built into the General Industries Hub v3.0 as of the latest commit, including the new Logistics and Warehouse portals and the procurement chain. For technical reference (function signatures, table schemas, full SQL), see `database.py`, `auth.py`, and `pages_internal/*.py` source files. For day-to-day operating procedure across all five roles, see `SOP.md`.

For PDF export: use any markdown-to-PDF converter (Typora, pandoc, marp, or VSCode's "Markdown PDF" extension). Suggested pandoc command:
```bash
pandoc USER_MANUAL.md -o USER_MANUAL.pdf --pdf-engine=xelatex \
  --toc --number-sections \
  -V geometry:margin=2cm -V mainfont="Helvetica" -V monofont="Menlo"
```
