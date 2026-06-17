# Bug Check Report

**Run at:** `2026-06-17T12:51:26`  
**Throwaway DB:** `/var/folders/wc/nfgzq5_n3j126zwndxprnd_00000gn/T/gi_bugcheck_aijhxwva/bug_check.db`  
**Total checks:** 268  
**Passing:** 268  
**Failing:** 0  

_The harness writes a fresh SQLite file under your system temp dir, seeds it, exercises every flow, then deletes the temp dir. `gi_database.db` is never touched._

## ❌ Failures (0)

_None — every check passed._

## ✅ Passing by area

### Attachments — 1/1
- ✅ BLOB round-trip + disk mirror

### Audit — 1/1
- ✅ log_audit_action writes row

### Bulk Badges — 1/1
- ✅ generate_employee_qr_badges_pdf produces valid PDF

### CV Foundation — 4/4
- ✅ Employees CRUD + duplicate rejection
- ✅ import_employees_csv idempotent upsert
- ✅ register + promote CV model — only one active
- ✅ Tool catalogue CRUD + min_confidence override

### CV Inference — 5/5
- ✅ detect_tool returns [] when no active model
- ✅ detect_tool returns [] when model_path missing on disk
- ✅ detect_tool drops detections below DEFAULT threshold
- ✅ per-class min_confidence override beats default
- ✅ invalidate_model_cache clears threshold cache

### Consumption — 1/1
- ✅ Stage → commit_eod

### Logistics — 8/8
- ✅ HOD submits PR → appears in Logistics queue
- ✅ Create PO (manual) — RL/BL tagged, PR→in_po
- ✅ get_po_detail(hide_prices=True) blanks prices
- ✅ Assign PO to Warehouse — full + subset
- ✅ Reschedule request → approve updates PO date
- ✅ Force-close PR / PO / line with audit
- ✅ Vendor return reopens the closed PO
- ✅ PO PDF extraction smoke test

### MTC — 2/2
- ✅ Attached rubber MTC stored as BLOB
- ✅ Missing MTC → mark_emailed flow

### Mailer — 1/1
- ✅ Draft helpers (Outlook / mailto patched)

### Math — 1/1
- ✅ Identity: Closing = Opening + R − C − Rt

### Module load — 1/1
- ✅ import every page module

### Notifications — 1/1
- ✅ mark_all_notifications_read scopes correctly

### Procurement — 7/7
- ✅ RL/BL strict-separation classifier
- ✅ Warehouses CRUD round-trip
- ✅ Vendors CRUD round-trip
- ✅ App notifications inbox (user + role broadcast)
- ✅ WhatsApp per-event gate honours config toggles
- ✅ users CHECK accepts logistics + warehouse_user
- ✅ po_items RL/BL strict-separation persists

### QR — 1/1
- ✅ Submit → approve / reject

### QR Badges — 2/2
- ✅ encode_id_to_png produces a valid PNG
- ✅ encode → decode roundtrip preserves ID_Number

### RBAC — 24/24
- ✅ store_keeper allow 📝 Entry Log
- ✅ hod block 📝 Entry Log
- ✅ admin block 📝 Entry Log
- ✅ supervisor block 📝 Entry Log
- ✅ store_keeper block 📦 Live Dashboard
- ✅ supervisor allow 📦 Live Dashboard
- ✅ hod allow 📦 Live Dashboard
- ✅ admin allow 📦 Live Dashboard
- ✅ admin allow 🛡️ Admin Portal
- ✅ hod block 🛡️ Admin Portal
- ✅ hod allow 📋 HOD Portal
- ✅ supervisor block 📋 HOD Portal
- ✅ supervisor allow 📊 Reports
- ✅ store_keeper block 📊 Reports
- ✅ logistics allow 🚚 Logistics Portal
- ✅ admin allow 🚚 Logistics Portal
- ✅ hod block 🚚 Logistics Portal
- ✅ warehouse_user block 🚚 Logistics Portal
- ✅ warehouse_user allow 🏭 Warehouse Portal
- ✅ admin allow 🏭 Warehouse Portal
- ✅ logistics block 🏭 Warehouse Portal
- ✅ hod block 🏭 Warehouse Portal
- ✅ store_keeper block 🚚 Logistics Portal
- ✅ store_keeper block 🏭 Warehouse Portal

### Receipts — 1/1
- ✅ Stage → commit_pending_receipts

### Reminders — 1/1
- ✅ T-2 / T-1 / T-0 sweep is idempotent

### Reports — 3/3
- ✅ Every report_* runs without raising
- ✅ Daily Receipts has Material_Code column
- ✅ Phase 5 procurement reports run cleanly

### Returnable — 1/1
- ✅ Tool loan → mark returned

### Returnable Reminders — 4/4
- ✅ sweep fires once per offset across all four windows
- ✅ sweep is idempotent within an hour
- ✅ phone resolution prefers CV → manual → audit
- ✅ T+24h escalates to supervisor (NOT HOD)

### Returns — 2/2
- ✅ Submit → approve → ledger row
- ✅ Reject removes from pending list

### Schema — 180/180
- ✅ table · inventory
- ✅ table · consumption
- ✅ table · receipts
- ✅ table · returns
- ✅ table · pending_issues
- ✅ table · pending_receipts
- ✅ table · pending_returns
- ✅ table · returnable_items
- ✅ table · users
- ✅ table · pending_users
- ✅ table · pr_master
- ✅ table · lots
- ✅ table · stock_adjustments
- ✅ table · system_settings
- ✅ table · system_audit_log
- ✅ table · app_settings
- ✅ table · whatsapp_queue
- ✅ table · bug_reports
- ✅ table · report_schedules
- ✅ table · report_archive
- ✅ table · qr_approval_requests
- ✅ table · entry_attachments
- ✅ table · mtc_documents
- ✅ table · warehouses
- ✅ table · vendors
- ✅ table · purchase_orders
- ✅ table · po_items
- ✅ table · po_shipment_schedule
- ✅ table · po_assignments
- ✅ table · delivery_notes
- ✅ table · dn_items
- ✅ table · po_returns
- ✅ table · po_reschedule_requests
- ✅ table · po_force_closures
- ✅ table · app_notifications
- ✅ table · delivery_reminders_sent
- ✅ table · employees
- ✅ table · tool_catalogue
- ✅ table · cv_model_versions
- ✅ column · inventory.SAP_Code
- ✅ column · inventory.Material_Code
- ✅ column · inventory.Equipment_Description
- ✅ column · inventory.UOM
- ✅ column · inventory.Minimum_Qty
- ✅ column · inventory.Unit_Cost
- ✅ column · inventory.Category
- ✅ column · inventory.Opening_Stock
- ✅ column · inventory.Site_ID
- ✅ column · consumption.Date
- ✅ column · consumption.SAP_Code
- ✅ column · consumption.Quantity
- ✅ column · consumption.Work_Type
- ✅ column · consumption.Remarks
- ✅ column · consumption.Site_ID
- ✅ column · consumption.Tank_No
- ✅ column · receipts.Date
- ✅ column · receipts.SAP_Code
- ✅ column · receipts.Quantity
- ✅ column · receipts.Supplier
- ✅ column · receipts.Expiry_Date
- ✅ column · receipts.PR_Number
- ✅ column · receipts.Site_ID
- ✅ column · receipts.Unit_Cost
- ✅ column · receipts.DN_No
- ✅ column · receipts.Lot_Number
- ✅ column · receipts.DN_Number
- ✅ column · receipts.Warehouse_ID
- ✅ column · receipts.PO_Number_Source
- ✅ column · returns.Date
- ✅ column · returns.SAP_Code
- ✅ column · returns.Quantity
- ✅ column · returns.Reason
- ✅ column · returns.Remarks
- ✅ column · returns.Site_ID
- ✅ column · pending_returns.SAP_Code
- ✅ column · pending_returns.Quantity
- ✅ column · pending_returns.Return_Reason
- ✅ column · pending_returns.Return_DN_No
- ✅ column · pending_returns.override_required
- ✅ column · pending_returns.status
- ✅ column · pending_returns.Material_Code
- ✅ column · pending_returns.Equipment_Description
- ✅ column · qr_approval_requests.SAP_Code
- ✅ column · qr_approval_requests.Quantity
- ✅ column · qr_approval_requests.requested_by
- ✅ column · qr_approval_requests.status
- ✅ column · qr_approval_requests.approved_by
- ✅ column · entry_attachments.doc_type
- ✅ column · entry_attachments.doc_number
- ✅ column · entry_attachments.file_blob
- ✅ column · entry_attachments.uploaded_by
- ✅ column · entry_attachments.Site_ID
- ✅ column · mtc_documents.SAP_Code
- ✅ column · mtc_documents.mtc_number
- ✅ column · mtc_documents.status
- ✅ column · mtc_documents.pending_receipt_id
- ✅ column · mtc_documents.Site_ID
- ✅ column · users.username
- ✅ column · users.role
- ✅ column · users.Site_ID
- ✅ column · users.Phone_Number
- ✅ column · users.Warehouse_ID
- ✅ column · warehouses.Warehouse_ID
- ✅ column · warehouses.Name
- ✅ column · warehouses.status
- ✅ column · vendors.Vendor_Code
- ✅ column · vendors.Vendor_Name
- ✅ column · vendors.status
- ✅ column · vendors.Default_Inco_Terms
- ✅ column · vendors.Default_Payment_Terms
- ✅ column · purchase_orders.PO_Number
- ✅ column · purchase_orders.PR_Number
- ✅ column · purchase_orders.Vendor_Code
- ✅ column · purchase_orders.PO_Date
- ✅ column · purchase_orders.Expected_Delivery
- ✅ column · purchase_orders.status
- ✅ column · purchase_orders.Inco_Terms
- ✅ column · purchase_orders.Payment_Terms
- ✅ column · purchase_orders.source
- ✅ column · po_items.PO_Number
- ✅ column · po_items.Material_Code
- ✅ column · po_items.Qty
- ✅ column · po_items.UOM
- ✅ column · po_items.Unit_Price
- ✅ column · po_items.Total_Price
- ✅ column · po_items.rl_bl_family
- ✅ column · po_items.Delivered_Qty
- ✅ column · po_items.line_status
- ✅ column · po_items.WBS_Number
- ✅ column · po_items.Network
- ✅ column · po_shipment_schedule.PO_Number
- ✅ column · po_shipment_schedule.shipment_no
- ✅ column · po_shipment_schedule.target_date
- ✅ column · po_shipment_schedule.status
- ✅ column · po_assignments.PO_Number
- ✅ column · po_assignments.Warehouse_ID
- ✅ column · po_assignments.assigned_by
- ✅ column · po_assignments.Expected_Delivery
- ✅ column · po_assignments.status
- ✅ column · delivery_notes.DN_Number
- ✅ column · delivery_notes.PO_Number
- ✅ column · delivery_notes.Warehouse_ID
- ✅ column · delivery_notes.Site_ID
- ✅ column · delivery_notes.status
- ✅ column · delivery_notes.rl_bl_family
- ✅ column · dn_items.DN_Number
- ✅ column · dn_items.po_item_id
- ✅ column · dn_items.Qty
- ✅ column · dn_items.UOM
- ✅ column · dn_items.rl_bl_family
- ✅ column · dn_items.status
- ✅ column · po_returns.PO_Number
- ✅ column · po_returns.Qty
- ✅ column · po_returns.Reason
- ✅ column · po_returns.raised_by_role
- ✅ column · po_returns.status
- ✅ column · po_reschedule_requests.PO_Number
- ✅ column · po_reschedule_requests.requested_date
- ✅ column · po_reschedule_requests.reason
- ✅ column · po_reschedule_requests.requested_by_role
- ✅ column · po_reschedule_requests.status
- ✅ column · po_force_closures.target_type
- ✅ column · po_force_closures.target_ref
- ✅ column · po_force_closures.reason
- ✅ column · po_force_closures.closed_by
- ✅ column · app_notifications.event_key
- ✅ column · app_notifications.title
- ✅ column · app_notifications.severity
- ✅ column · app_notifications.recipient_user
- ✅ column · app_notifications.recipient_role
- ✅ column · pr_master.WBS_Number
- ✅ column · pr_master.Network
- ✅ column · pr_master.Plant
- ✅ column · pr_master.Delivery_Date
- ✅ column · pr_master.logistics_status
- ✅ column · returnable_items.cv_detected
- ✅ column · returnable_items.cv_confidence
- ✅ column · returnable_items.cv_employee_id
- ✅ column · returnable_items.cv_tool_class
- ✅ init_db() is idempotent

### Site Visibility — 3/3
- ✅ In-Transit DNs filtered + sorted per site
- ✅ HOD reschedule → Logistics → outcome reflected
- ✅ Force-closure visibility scoped to site

### Sites — 1/1
- ✅ HQ visible to get_sites()

### Smart Scan — 4/4
- ✅ bucket_detections returns 'auto' for ≥0.75
- ✅ bucket_detections caps candidates at 3 and floors at 0.30
- ✅ lookup_employee_by_qr rejects suspended / unknown / blank
- ✅ get_open_loans_for_employee matches CV + manual loans

### Warehouse — 6/6
- ✅ Acknowledge + receive (partial + over-deliver guard)
- ✅ Warehouse view strictly hides prices (items + header)
- ✅ DN splitter enforces RL/BL strict separation
- ✅ Full DN flow → SK confirms → receipts row
- ✅ Internal return reopens PO line
- ✅ HOD rejection terminates the DN cleanly

### WhatsApp — 1/1
- ✅ queue_whatsapp_alert writes pending row
