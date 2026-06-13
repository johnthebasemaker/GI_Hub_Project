# Bug Check Report

**Run at:** `2026-06-12T16:23:54`  
**Throwaway DB:** `/var/folders/wc/nfgzq5_n3j126zwndxprnd_00000gn/T/gi_bugcheck_qo5_vg9_/bug_check.db`  
**Total checks:** 114  
**Passing:** 114  
**Failing:** 0  

_The harness writes a fresh SQLite file under your system temp dir, seeds it, exercises every flow, then deletes the temp dir. `gi_database.db` is never touched._

## ❌ Failures (0)

_None — every check passed._

## ✅ Passing by area

### Attachments — 1/1
- ✅ BLOB round-trip + disk mirror

### Audit — 1/1
- ✅ log_audit_action writes row

### Consumption — 1/1
- ✅ Stage → commit_eod

### MTC — 2/2
- ✅ Attached rubber MTC stored as BLOB
- ✅ Missing MTC → mark_emailed flow

### Mailer — 1/1
- ✅ Draft helpers (Outlook / mailto patched)

### Math — 1/1
- ✅ Identity: Closing = Opening + R − C − Rt

### Module load — 1/1
- ✅ import every page module

### QR — 1/1
- ✅ Submit → approve / reject

### RBAC — 14/14
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

### Receipts — 1/1
- ✅ Stage → commit_pending_receipts

### Reports — 2/2
- ✅ Every report_* runs without raising
- ✅ Daily Receipts has Material_Code column

### Returnable — 1/1
- ✅ Tool loan → mark returned

### Returns — 2/2
- ✅ Submit → approve → ledger row
- ✅ Reject removes from pending list

### Schema — 83/83
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
- ✅ init_db() is idempotent

### Sites — 1/1
- ✅ HQ visible to get_sites()

### WhatsApp — 1/1
- ✅ queue_whatsapp_alert writes pending row
