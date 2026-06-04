# ⚡ General Industries Lightning Hub v2.0 (CNCEC-System)
**Enterprise Inventory, Logistics & Tracking System**

## 📖 Project Overview
This is a multi-site enterprise resource planning (ERP) application built for heavy industry material tracking. It facilitates real-time inventory management, cross-site material requests, role-based approval workflows, predictive analytics, and automated background notifications.

**Core Tech Stack:**
* **Frontend:** Streamlit (Custom CSS injected for Dark/Navy/Gold branding)
* **Backend:** Python 3.12
* **Database:** SQLite3 (Local DB file with self-healing migrations)
* **Data Processing:** Pandas
* **Automation:** PyWhatKit (WhatsApp)
* **Reporting:** fpdf2 (PDF generation for reports and QR labels)
* **Authentication:** bcrypt (Hash-based passwords)

---

## 📂 File Architecture
The project is modularized to separate UI, database transactions, background workers, and reporting.

* `main.py`: The entry point. Handles routing, session state checks, and renders the main dashboards.
* `database.py`: The single source of truth for all SQL transactions. Handles connection pooling, migrations, commits, predictive analytics, and WhatsApp queue insertions.
* `auth.py`: Handles all RBAC, bcrypt hashing, login forms, and user registrations.
* `ui_components.py`: Contains custom CSS injections and UI rendering functions (like the Burn Rate charts and Gatekeeper banners).
* `mailer.py`: Handles SMTP automated emails for logistics and EOD report delivery. Includes OS-aware checks (`win32com` for Windows, `mailto:` for Mac).
* `reports.py`: Uses `fpdf2` to generate immutable PDF records and physical QR code label sheets.
* `whatsapp_worker.py`: Asynchronous background script that scans `whatsapp_queue` and overdue `returnable_items`. Implements strict state-locking (pending -> processing -> sent/failed) to prevent infinite loops.
* `config.py`: Stores global variables, brand colour hex codes, role hierarchy, and `SYSTEM_COLS` exclusions for dynamic forms.

---

## 🔐 Role-Based Access Control (RBAC)
1. **Admin (HQ):** Global "God-View". Manages users, edits Master Database, approves cross-site transfers via Bulk Actions.
2. **Head of Department (HOD):** Site-specific management. Approves pending consumption logs, approves pending receipts, requests materials from other sites.
3. **Supervisor:** Can view live stock and staging queues.
4. **Store Keeper:** The primary floor operator. Manages the "Daily Issue Log" (consumption staging), "Receipt Entry" (inbound staging), and the "Returnable Items" tool-tracking tab. 

---

## ⚙️ Core Workflows & Logic

### 1. The Live Inventory Dashboard (UI Standard)
* **Strict Rule:** The Global / Live Inventory Dashboard must prioritize the pure Data Table (AgGrid/DataFrame). Abstract metrics like "Total Consumed" or "Total Received" are strictly prohibited as they provide blind data and do not make sense for logistics operations. The table is the primary visual.

### 2. The Two-Way Staging Loop (Consumption & Receipts)
* **Store Keepers** scan materials for Consumption OR Receipts into temporary staging tables (`pending_issues` or `pending_receipts`) with `status = 'draft'`.
* Drafts are persistent on the Store Keeper's local machine/site.
* Forms are generated dynamically via `PRAGMA table_info` (excluding `SYSTEM_COLS`).
* Store Keeper clicks "Submit to HOD", updating status to `pending_hod`. A single batched WhatsApp receipt is dispatched to the HOD.
* HOD reviews the queues and commits them to the master tables (`consumption` or `receipts`), altering the live `Current_Stock`.

### 3. Returnable Items (Tool Tracking)
* Store Keepers log temporary items given to personnel with an expected return time.
* If the item is not returned by the deadline, `whatsapp_worker.py` dispatches alerts to the borrower and the Store Keeper.
* Overdue items trigger UI popup reminders for the Store Keeper upon login.

### 4. Shelf-Life Gatekeeper
* When a Store Keeper adds an item to the Daily Issue Log, the system checks `receipts` for short-dated/expired batches.
* If older stock exists at their site, a hard `st.stop()` warning blocks the entry until they check an override box confirming they pulled the old stock.

### 5. Cross-Site Material Requests
* HOD adds requested materials to a session-state "Shopping Cart".
* Submitting the cart queues cross-site requests for Admin approval.
* Admin approves via Bulk Checkboxes, triggering a consolidated WhatsApp receipt to the HOD. Admin notes are saved, and the `queue_whatsapp_alert` function writes to the DB, triggering `whatsapp_worker.py` to notify the requesting HOD.


### 6. Predictive Analytics (Burn Rate)
* The system calculates a 30-day trailing Burn Rate to predict `Days_Remaining` for stock.
* Items with < 7 days of runway are flagged with a critical red banner on the Admin and HOD dashboards.

### 7. Universal PDF & QR Generation and Audit
* Any database table can be exported to a branded PDF via `reports.py`.
* The `inventory` table allows Admins to generate 3x4 grids of physical QR Code stickers to label warehouse bins.
* **Audit Ledger:** Every critical action (Approval, Rejection, DB Edit) is stamped in `system_audit_log` with User ID, Timestamp, and Action Type.


### 8. Purchase Requests (PR) & Logistics
* HOD uploads a PR PDF or logs PR details into `pr_master`.
* When physical goods arrive, Site receives against the PR into the `receipts` table.
* The system calculates `Requested_Qty - Received_Qty = Pending_Qty`.
* **Automation:** An email is dispatched to Logistics alerting them of partial or full delivery.


---

## 🗄️ Database Schema Summary
* `users`: Active system accounts.
* `pending_users`: Registration queue.
* `inventory`: Master material list (SAP Code, Material Code, Material Name, UOM, Limits).
* `consumption`: Permanent record of all issued materials.
* `pending_issues`: Staging queue for consumption (`status` tracked).
* **`pr_master`: Open purchase requests.**
* `receipts`: Permanent record of inbound deliveries (tracks `Expiry_Date`).
* `pending_receipts`: Staging queue for inbound deliveries.
* `returnable_items`: Ledger for tracking temporarily loaned tools/materials.
* `cross_site_requests`: Material transfer workflows.
* `whatsapp_queue`: Automated message queue.
* `system_audit_log`: Immutable enterprise event tracking.


**Development Note:** Always ensure database queries utilize parameterized inputs (`?`) to prevent SQL injection. When interacting with Streamlit, prioritize state management (`st.session_state`) and minimize unnecessary `st.rerun()` calls.