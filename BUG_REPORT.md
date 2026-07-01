# Bug Check Report

**Run at:** `2026-07-01T17:21:19`  
**Throwaway DB:** `/var/folders/wc/nfgzq5_n3j126zwndxprnd_00000gn/T/gi_bugcheck_yagawz4t/bug_check.db`  
**Total checks:** 591  
**Passing:** 571  
**Failing:** 20  

_The harness writes a fresh SQLite file under your system temp dir, seeds it, exercises every flow, then deletes the temp dir. `gi_database.db` is never touched._

## ❌ Failures (20)

### Module load · import every page module
- **Error:** `Module import failures:
  mailer → ModuleNotFoundError: No module named 'dotenv'
  auth → ModuleNotFoundError: No module named 'bcrypt'
  reports → ModuleNotFoundError: No module named 'fpdf'
  pages_internal.admin_portal → ModuleNotFoundError: No module named 'bcrypt'
  pages_internal.reports_page → ModuleNotFoundError: No module named 'fpdf'`
- **Hint:** Look for missing imports or top-level NameErrors.
- **Trace:**
```
Traceback (most recent call last):
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 73, in run_check
    fn()
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 3270, in check_module_imports
    assert not failed, "Module import failures:\n  " + "\n  ".join(failed)
           ^^^^^^^^^^
AssertionError: Module import failures:
  mailer → ModuleNotFoundError: No module named 'dotenv'
  auth → ModuleNotFoundError: No module named 'bcrypt'
  reports → ModuleNotFoundError: No module named 'fpdf'
  pages_internal.admin_portal → ModuleNotFoundError: No module named 'bcrypt'
  pages_internal.reports_page → ModuleNotFoundError: No module named 'fpdf'
```

### Mailer · Draft helpers (Outlook / mailto patched)
- **Error:** `ModuleNotFoundError: No module named 'dotenv'`
- **Trace:**
```
Traceback (most recent call last):
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 73, in run_check
    fn()
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 1476, in check_mailer_drafts
    import mailer
  File "/Users/johnsonandrew/GI_Hub_Project/mailer.py", line 25, in <module>
    from dotenv import load_dotenv
ModuleNotFoundError: No module named 'dotenv'
```

### Auth · user-mgmt tab: no shadowed log_audit_action (Reject user)
- **Error:** `ModuleNotFoundError: No module named 'bcrypt'`
- **Hint:** inline 'from database import log_audit_action' made it a local → UnboundLocalError when rejecting a pending user.
- **Trace:**
```
Traceback (most recent call last):
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 73, in run_check
    fn()
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 1517, in check_user_mgmt_no_unbound_log_audit
    import auth
  File "/Users/johnsonandrew/GI_Hub_Project/auth.py", line 24, in <module>
    import bcrypt
ModuleNotFoundError: No module named 'bcrypt'
```

### Auth/2FA · TOTP lifecycle (stage→verify→enable→reset)
- **Error:** `ModuleNotFoundError: No module named 'bcrypt'`
- **Hint:** Opt-in; staged secret stays disabled until a code confirms; admin reset clears it; enable requires a secret (no lock-in).
- **Trace:**
```
Traceback (most recent call last):
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 73, in run_check
    fn()
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 3165, in check_2fa_flow
    auth = importlib.import_module("auth")
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Library/Frameworks/Python.framework/Versions/3.12/lib/python3.12/importlib/__init__.py", line 90, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
ModuleNotFoundError: No module named 'bcrypt'
```

### Round 15 · process_po_pdf extracts 3 items from sample PDF
- **Error:** `pdfplumber not installed`
- **Hint:** Regression guard against PO#4710003114.pdf two-line layout.
- **Trace:**
```
Traceback (most recent call last):
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 73, in run_check
    fn()
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 6041, in check_r15_po_pdf_three_items
    assert ok, msg
           ^^
AssertionError: pdfplumber not installed
```

### Round 15 · _ALWAYS_KEEP includes UOM for PR report
- **Error:** `ModuleNotFoundError: No module named 'fpdf'`
- **Hint:** UoM column survives the strip-empty helper.
- **Trace:**
```
Traceback (most recent call last):
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 73, in run_check
    fn()
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 6204, in check_r15_pr_report_keeps_uom_column
    rp = importlib.import_module("pages_internal.reports_page")
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Library/Frameworks/Python.framework/Versions/3.12/lib/python3.12/importlib/__init__.py", line 90, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
ModuleNotFoundError: No module named 'fpdf'
```

### Round 16 · generate_pr_pdf renders new PO # + UoM columns
- **Error:** `ModuleNotFoundError: No module named 'fpdf'`
- **Hint:** Back-compat path (no po_map kwarg) still produces a valid PDF.
- **Trace:**
```
Traceback (most recent call last):
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 73, in run_check
    fn()
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 6368, in check_r16_generate_pr_pdf_has_new_columns
    from reports import generate_pr_pdf
  File "/Users/johnsonandrew/GI_Hub_Project/reports.py", line 1, in <module>
    from fpdf import FPDF
ModuleNotFoundError: No module named 'fpdf'
```

### Phase 7F · ROLE_MANUAL_RECIPES covers all 6 production roles
- **Error:** `ModuleNotFoundError: No module named 'fpdf'`
- **Hint:** Every role from config.ROLES must have an entry.
- **Trace:**
```
Traceback (most recent call last):
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 73, in run_check
    fn()
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 7147, in check_7f_recipes_cover_all_roles
    from build_manual_pdf import ROLE_MANUAL_RECIPES
  File "/Users/johnsonandrew/GI_Hub_Project/build_manual_pdf.py", line 39, in <module>
    from fpdf import FPDF
ModuleNotFoundError: No module named 'fpdf'
```

### Phase 7F · slice_markdown_for_role('store_keeper') keeps SK chapter
- **Error:** `ModuleNotFoundError: No module named 'fpdf'`
- **Hint:** Slicer extracts chapter 4 for the SK booklet.
- **Trace:**
```
Traceback (most recent call last):
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 73, in run_check
    fn()
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 7156, in check_7f_slice_sk_keeps_own
    from build_manual_pdf import slice_markdown_for_role
  File "/Users/johnsonandrew/GI_Hub_Project/build_manual_pdf.py", line 39, in <module>
    from fpdf import FPDF
ModuleNotFoundError: No module named 'fpdf'
```

### Phase 7F · slice_markdown_for_role('store_keeper') drops Logistics
- **Error:** `ModuleNotFoundError: No module named 'fpdf'`
- **Hint:** Cross-role chapters must NOT bleed into SK booklet.
- **Trace:**
```
Traceback (most recent call last):
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 73, in run_check
    fn()
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 7163, in check_7f_slice_sk_drops_logistics
    from build_manual_pdf import slice_markdown_for_role
  File "/Users/johnsonandrew/GI_Hub_Project/build_manual_pdf.py", line 39, in <module>
    from fpdf import FPDF
ModuleNotFoundError: No module named 'fpdf'
```

### Phase 7F · slice_markdown_for_role('supervisor') keeps Supervisor chapter
- **Error:** `ModuleNotFoundError: No module named 'fpdf'`
- **Hint:** Chapter 5 must appear in supervisor booklet.
- **Trace:**
```
Traceback (most recent call last):
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 73, in run_check
    fn()
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 7172, in check_7f_slice_supervisor_keeps_own
    from build_manual_pdf import slice_markdown_for_role
  File "/Users/johnsonandrew/GI_Hub_Project/build_manual_pdf.py", line 39, in <module>
    from fpdf import FPDF
ModuleNotFoundError: No module named 'fpdf'
```

### Phase 7F · slice_markdown_for_role('hod') keeps Reports chapter
- **Error:** `ModuleNotFoundError: No module named 'fpdf'`
- **Hint:** HOD booklet must include chapter 8 per recipe.
- **Trace:**
```
Traceback (most recent call last):
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 73, in run_check
    fn()
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 7179, in check_7f_slice_hod_keeps_reports
    from build_manual_pdf import slice_markdown_for_role
  File "/Users/johnsonandrew/GI_Hub_Project/build_manual_pdf.py", line 39, in <module>
    from fpdf import FPDF
ModuleNotFoundError: No module named 'fpdf'
```

### Phase 7F · slice_markdown_for_role('admin') returns full markdown
- **Error:** `ModuleNotFoundError: No module named 'fpdf'`
- **Hint:** Admin recipe == 'ALL' → unchanged passthrough.
- **Trace:**
```
Traceback (most recent call last):
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 73, in run_check
    fn()
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 7186, in check_7f_slice_admin_full
    from build_manual_pdf import slice_markdown_for_role
  File "/Users/johnsonandrew/GI_Hub_Project/build_manual_pdf.py", line 39, in <module>
    from fpdf import FPDF
ModuleNotFoundError: No module named 'fpdf'
```

### Phase 7F · parse_markdown recognises image syntax
- **Error:** `ModuleNotFoundError: No module named 'fpdf'`
- **Hint:** ![alt](path) on its own line → Block(kind='img').
- **Trace:**
```
Traceback (most recent call last):
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 73, in run_check
    fn()
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 7193, in check_7f_parse_image_block
    from build_manual_pdf import parse_markdown
  File "/Users/johnsonandrew/GI_Hub_Project/build_manual_pdf.py", line 39, in <module>
    from fpdf import FPDF
ModuleNotFoundError: No module named 'fpdf'
```

### Phase 7F · render_image handles missing file (placeholder)
- **Error:** `ModuleNotFoundError: No module named 'fpdf'`
- **Hint:** Missing PNG renders the grey placeholder card; never raises.
- **Trace:**
```
Traceback (most recent call last):
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 73, in run_check
    fn()
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 7205, in check_7f_render_image_missing_no_crash
    from build_manual_pdf import ManualPDF
  File "/Users/johnsonandrew/GI_Hub_Project/build_manual_pdf.py", line 39, in <module>
    from fpdf import FPDF
ModuleNotFoundError: No module named 'fpdf'
```

### Phase 7F · build_role_manual_pdf returns valid PDF bytes
- **Error:** `ModuleNotFoundError: No module named 'fpdf'`
- **Hint:** Output must start with %PDF- magic bytes.
- **Trace:**
```
Traceback (most recent call last):
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 73, in run_check
    fn()
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 7216, in check_7f_role_pdf_starts_with_magic
    from build_manual_pdf import build_role_manual_pdf
  File "/Users/johnsonandrew/GI_Hub_Project/build_manual_pdf.py", line 39, in <module>
    from fpdf import FPDF
ModuleNotFoundError: No module named 'fpdf'
```

### Phase 7F · build_role_manual_pdf('admin') == build_manual_pdf
- **Error:** `ModuleNotFoundError: No module named 'fpdf'`
- **Hint:** Admin recipe is the master full PDF — identical chapter content.
- **Trace:**
```
Traceback (most recent call last):
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 73, in run_check
    fn()
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 7229, in check_7f_admin_equals_master
    from build_manual_pdf import build_manual_pdf, build_role_manual_pdf
  File "/Users/johnsonandrew/GI_Hub_Project/build_manual_pdf.py", line 39, in <module>
    from fpdf import FPDF
ModuleNotFoundError: No module named 'fpdf'
```

### Phase 7F · build_role_manual_pdf(unknown role) falls back to master
- **Error:** `ModuleNotFoundError: No module named 'fpdf'`
- **Hint:** Unknown role_key → master PDF, no exception.
- **Trace:**
```
Traceback (most recent call last):
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 73, in run_check
    fn()
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 7238, in check_7f_unknown_role_falls_back
    from build_manual_pdf import build_role_manual_pdf
  File "/Users/johnsonandrew/GI_Hub_Project/build_manual_pdf.py", line 39, in <module>
    from fpdf import FPDF
ModuleNotFoundError: No module named 'fpdf'
```

### Phase 8D · _render_locate_anything_panel doesn't crash with sidecar down
- **Error:** `ModuleNotFoundError: No module named 'bcrypt'`
- **Hint:** Streamlit import surface + helper invocation under AppTest.
- **Trace:**
```
Traceback (most recent call last):
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 73, in run_check
    fn()
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 7986, in check_8d_panel_renders_with_sidecar_down
    mod = importlib.import_module("pages_internal.admin_portal")
          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Library/Frameworks/Python.framework/Versions/3.12/lib/python3.12/importlib/__init__.py", line 90, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
ModuleNotFoundError: No module named 'bcrypt'
```

### Material Estimator · pages_internal exports resolve (no cold-start ImportError)
- **Error:** `ModuleNotFoundError: No module named 'bcrypt'`
- **Hint:** every page_* export is importable; SME portal no longer re-enters the half-built package at module level.
- **Trace:**
```
Traceback (most recent call last):
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 73, in run_check
    fn()
  File "/Users/johnsonandrew/GI_Hub_Project/bug_check.py", line 3556, in check_pages_internal_exports_resolve
    assert hasattr(pkg, name), f"pages_internal missing export: {name}"
           ^^^^^^^^^^^^^^^^^^
  File "/Users/johnsonandrew/GI_Hub_Project/pages_internal/__init__.py", line 51, in __getattr__
    module = importlib.import_module(f".{mod}", __name__)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
ModuleNotFoundError: No module named 'bcrypt'
```

## ✅ Passing by area

### Attachments — 1/1
- ✅ BLOB round-trip + disk mirror

### Audit — 2/2
- ✅ Opening_Stock edits logged, not new items (#23)
- ✅ log_audit_action writes row

### Auth — 0/1
- ❌ user-mgmt tab: no shadowed log_audit_action (Reject user)

### Auth/2FA — 1/2
- ❌ TOTP lifecycle (stage→verify→enable→reset)
- ✅ 2FA login gate + self-service + admin reset wired

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

### DB Editor — 1/1
- ✅ Crash-safe replace preserves rows on failure (#10)

### DN FEFO — 1/1
- ✅ Material→SAP map + earliest-expiry suggestion (#30)

### Force-close — 1/1
- ✅ Undo restores prior state within window (#28)

### Hub Assistant — 6/6
- ✅ system prompt injects username + role label
- ✅ empty username does not crash the prompt builder
- ✅ admin gets FULL §7 with the 👥 Users content
- ✅ logistics role gets §14 (Logistics Portal)
- ✅ warehouse_user role gets §15 (Warehouse Portal)
- ✅ admin refusal phrase points to Settings download bay

### Logistics — 8/8
- ✅ HOD submits PR → appears in Logistics queue
- ✅ Create PO (manual) — RL/BL tagged, PR→in_po
- ✅ get_po_detail(hide_prices=True) blanks prices
- ✅ Assign PO to Warehouse — full + subset
- ✅ Reschedule request → approve updates PO date
- ✅ Force-close PR / PO / line with audit
- ✅ Vendor return reopens the closed PO
- ✅ PO PDF extraction smoke test

### Lots — 4/4
- ✅ quarantine drops a lot out of FEFO
- ✅ disposal via HOD adjustment (approve + reject)
- ✅ split/merge reclassify within-SAP (stock unchanged)
- ✅ Lot Management UI module mounts in both portals

### MTC — 2/2
- ✅ Attached rubber MTC stored as BLOB
- ✅ Missing MTC → mark_emailed flow

### Mailer — 0/1
- ❌ Draft helpers (Outlook / mailto patched)

### Maintenance — 1/1
- ✅ maintenance_mode toggle actually blocks non-admins

### Man-Hour — 7/7
- ✅ schema — 5 mh_ tables + comparison view exist
- ✅ hours math — 8h normal + 1h break, OT, overnight
- ✅ employee upsert is idempotent + validates Worker_Type
- ✅ timesheet insert + team-SQM distribution (even/by_hours)
- ✅ estimate-vs-actual view math + reason join
- ✅ attendance workbook parser (shared by UI + bootstrap)
- ✅ bulk import — replace-by-date idempotent vs append

### Material Estimator — 6/7
- ❌ pages_internal exports resolve (no cold-start ImportError)
- ✅ Dashboard filters cross-filter Code <-> Substrate
- ✅ KPI drill-down is a centered modal (not clipped popover)
- ✅ equipment loader: Substrate≠Lining_Type, area SQM sums
- ✅ System Code Report tab (per-code equipments + SQM)
- ✅ Sub_Location surfaced (helper + detail card)
- ✅ admin site picker + SME sidebar suppressed

### Math — 1/1
- ✅ Identity: Closing = Opening + R − C − Rt

### Module load — 0/1
- ❌ import every page module

### Notifications — 1/1
- ✅ mark_all_notifications_read scopes correctly

### Phase 7A — 15/15
- ✅ employees.Site_ID column self-heals
- ✅ ix_employees_site index exists
- ✅ add_employee(site_id=) persists binding
- ✅ add_employee() without site_id writes NULL (back-compat)
- ✅ update_employee(site_id=) reassigns site
- ✅ update_employee(site_id='') clears binding to NULL
- ✅ update_employee(site_id=None) leaves binding untouched
- ✅ list_employees() returns Site_ID column
- ✅ list_employees(site_id_filter='HQ') filters
- ✅ list_employees(site_id_filter='__UNASSIGNED__') gets NULL rows
- ✅ list_employees_for_site(site, status='active') excludes inactive
- ✅ import_employees_csv with Site_ID column persists site
- ✅ import_employees_csv without Site_ID column is back-compat
- ✅ import_employees_csv preserves existing binding when col absent
- ✅ bulk_assign_employees_to_site sets Site_ID for N rows

### Phase 7B — 21/21
- ✅ generate_smr_request_no returns SMR-YYYYMMDD-0001 day-empty
- ✅ generate_smr_request_no increments on same day
- ✅ create_supervisor_request happy path inserts header + items
- ✅ rejects worker not bound to site
- ✅ rejects empty item list
- ✅ rejects PPE=No without reason
- ✅ rejects unknown SAP_Code
- ✅ Stock_At_Request snapshot is captured
- ✅ Available_Flag = 0 when requested qty > stock
- ✅ approve mirrors lines → pending_issues draft (Round 12)
- ✅ approve flips status + captures posted_pending_ids JSON
- ✅ approve is idempotent (refuses second call)
- ✅ approve drops SK_Adjusted_Qty=0 lines
- ✅ reject requires reason + flips status, no pending_issues
- ✅ end-to-end: approve → commit_eod → consumption row with Source_Ref
- ✅ update_supervisor_request_item only works while pending_sk
- ✅ cancel_supervisor_request only works while pending_sk
- ✅ delete_supervisor_request_item drops a pending line
- ✅ report_supervisor_intent_vs_actual joins on Source_Ref
- ✅ get_open_returnables_for_employee finds matching loans
- ✅ config.WHATSAPP_TRIGGERS has 4 smr_* keys

### Phase 7C — 14/14
- ✅ ix_csv_target_date index exists
- ✅ ix_csv_viewer_date index exists
- ✅ UNIQUE(viewer,target,date) enforced
- ✅ record_cross_site_view first call returns True
- ✅ record_cross_site_view dedupe returns False
- ✅ different target same day returns True
- ✅ different viewer same target returns True
- ✅ self-view returns False
- ✅ blank inputs return False
- ✅ notify_cross_site_view admin role → silent
- ✅ notify_cross_site_view queues notification on first fire
- ✅ notify_cross_site_view writes audit row on first fire
- ✅ notify_cross_site_view dedupe → no new notification
- ✅ config.WHATSAPP_TRIGGERS['cross_site_viewed'] = False

### Phase 7D — 16/16
- ✅ PO_VENDOR_MASK_FIELDS has 17 entries
- ✅ get_po_detail() default returns commercial fields populated
- ✅ get_po_detail(hide_vendor=True) blanks all 17 fields
- ✅ get_po_detail(hide_vendor=True) preserves PO_Type + PO_Date
- ✅ get_po_detail combines hide_prices + hide_vendor
- ✅ build_po_site_notification — title + site_id correct
- ✅ build_po_site_notification — PR list deduped from items
- ✅ build_po_site_notification — Expected_Delivery surfaced
- ✅ build_po_site_notification — body has top 5 lines + 'and N more'
- ✅ build_po_site_notification body has NO Vendor_Name
- ✅ build_po_site_notification body has NO financial figure
- ✅ build_po_site_notification — WhatsApp body mirrors in-app
- ✅ create_po_manual queues notification to site HOD
- ✅ create_po_manual queues notification to site SK
- ✅ create_po_manual notifications NEVER contain Vendor_Name
- ✅ create_po_manual with Site_ID=NULL queues NO notification

### Phase 7E — 16/16
- ✅ ix_form_drafts_expires index exists
- ✅ ix_form_drafts_user index exists
- ✅ UNIQUE(username, form_id) enforced
- ✅ upsert_form_draft writes a new row
- ✅ upsert_form_draft updates on duplicate (user, form)
- ✅ upsert_form_draft default TTL is 7 days
- ✅ upsert_form_draft honours custom ttl_days
- ✅ upsert_form_draft rejects non-JSON payload
- ✅ get_form_draft returns roundtripped payload
- ✅ get_form_draft returns None for missing entry
- ✅ get_form_draft hides expired entries
- ✅ delete_form_draft removes row + returns True
- ✅ delete_form_draft on missing entry returns False
- ✅ prune_expired_form_drafts deletes expired rows only
- ✅ list_user_drafts returns multi-form DataFrame
- ✅ requirements.txt declares streamlit-local-storage

### Phase 7F — 1/12
- ❌ ROLE_MANUAL_RECIPES covers all 6 production roles
- ❌ slice_markdown_for_role('store_keeper') keeps SK chapter
- ❌ slice_markdown_for_role('store_keeper') drops Logistics
- ❌ slice_markdown_for_role('supervisor') keeps Supervisor chapter
- ❌ slice_markdown_for_role('hod') keeps Reports chapter
- ❌ slice_markdown_for_role('admin') returns full markdown
- ❌ parse_markdown recognises image syntax
- ❌ render_image handles missing file (placeholder)
- ❌ build_role_manual_pdf returns valid PDF bytes
- ❌ build_role_manual_pdf('admin') == build_manual_pdf
- ❌ build_role_manual_pdf(unknown role) falls back to master
- ✅ docs/screenshots/ has the seed placeholder PNGs

### Phase 8A — 10/10
- ✅ app_settings seeds locate_anything_enabled=0
- ✅ app_settings seeds locate_anything_sidecar_url
- ✅ client.is_enabled() returns False when gate is off
- ✅ client.detect() short-circuits to [] when gate is off
- ✅ client.detect() parses mock 200 response into list
- ✅ client.detect() returns [] on 503 + trips breaker
- ✅ client circuit breaker opens after 3 failures
- ✅ client module imports without torch / transformers
- ✅ ai/locate_anything/requirements.txt exists
- ✅ scripts/download_model.sh exists and is executable

### Phase 8B — 5/5
- ✅ bundle_locate_anything_weights.sh exists + executable + bash-clean
- ✅ install_locate_anything_weights.sh exists + executable + bash-clean
- ✅ run_locate_anything.sh exists + executable + bash-clean
- ✅ com.gi.locate-anything.plist.tmpl parses as valid plist
- ✅ install.sh recognises --with-locate-anything flag

### Phase 8C — 11/11
- ✅ should_invoke_tier3([]) → True (empty)
- ✅ should_invoke_tier3([conf=0.25]) → True (manual band)
- ✅ should_invoke_tier3([conf=0.50]) → False (candidates band)
- ✅ should_invoke_tier3([conf=0.95]) → False (auto band)
- ✅ tier3_to_candidates reshapes LocateAnything output
- ✅ tier3_to_candidates filters items below noise floor
- ✅ tier3_to_candidates caps at MAX_CANDIDATES (3)
- ✅ tier3_to_candidates tags source='tier3_locate_anything'
- ✅ integration: YOLO empty + mock sidecar → tier3 candidates ready
- ✅ gate guard: toggle OFF + YOLO empty → sidecar HTTP NOT called
- ✅ gate guard: YOLO confident → sidecar HTTP NOT called

### Phase 8D — 1/2
- ❌ _render_locate_anything_panel doesn't crash with sidecar down
- ✅ panel toggle ON path stores '1' in app_settings

### Phase 8E — 8/8
- ✅ locate_anything_calls table exists with required columns
- ✅ ix_la_calls_called_at index present
- ✅ log_locate_anything_call writes a row + returns rowid
- ✅ mark_locate_anything_outcome updates accepted field
- ✅ client.detect happy path writes telemetry row
- ✅ client.detect failure path writes telemetry with error
- ✅ client.detect gate-off writes NO telemetry row
- ✅ get_locate_anything_summary computes rates safely

### Postgres — 2/2
- ✅ Phase 1 engine seam (SQLite default, no behavior change)
- ✅ Phase 2 portability helpers (dialect-correct SQL)

### Procurement — 10/10
- ✅ Email-path adoption metric + deprecation flag (#29)
- ✅ RL/BL strict-separation classifier
- ✅ Warehouses CRUD round-trip
- ✅ Vendors CRUD round-trip
- ✅ App notifications inbox (user + role broadcast)
- ✅ WhatsApp per-event gate honours config toggles
- ✅ users CHECK accepts logistics + warehouse_user
- ✅ po_items RL/BL strict-separation persists
- ✅ Auto-draft PRs from below-minimum (idempotent)
- ✅ PR factor qty + edit + rename + post-submit lock

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

### Receipts — 3/3
- ✅ Stage → commit_pending_receipts
- ✅ Bin_Location threads to ledger + lookup helper
- ✅ UoM pack conversions (CRUD + convert_to_base)

### Reminders — 2/2
- ✅ Delivery cadence configurable + normalized (#25)
- ✅ T-2 / T-1 / T-0 sweep is idempotent

### Reports — 4/4
- ✅ Scheduler due-ness + active/due selection (#13)
- ✅ Every report_* runs without raising
- ✅ Daily Receipts has Material_Code column
- ✅ Phase 5 procurement reports run cleanly

### Reservations — 1/1
- ✅ Approve transfer earmarks; fulfil/reject releases

### Resilience — 1/1
- ✅ global error boundary (friendly UI + dev log)

### Returnable — 1/1
- ✅ Tool loan → mark returned

### Returnable Reminders — 4/4
- ✅ sweep fires once per offset across all four windows
- ✅ sweep is idempotent within an hour
- ✅ phone resolution prefers CV → manual → audit
- ✅ T+24h escalates to supervisor (NOT HOD)

### Returns — 3/3
- ✅ Submit → approve → ledger row
- ✅ Reject removes from pending list
- ✅ Cleanup archives only old rejected rows (#20)

### Round 12 — 10/10
- ✅ Requested_By column on pending_issues + consumption
- ✅ line_status column on supervisor_material_request_items
- ✅ new SMR lines default to line_status='active'
- ✅ withdraw_smr_line_at_staging flips line_status
- ✅ commit_eod(hod_username=…) writes 'Approved By'
- ✅ commit_eod flips SMR line_status='committed'
- ✅ commit_eod carries Requested_By into consumption
- ✅ HIDDEN_FORM_COLS covers Technician + auto-fields
- ✅ list_smr_history honours filters + decided-only default
- ✅ E2E: sup → SK approve → SK submit → HOD commit

### Round 13 — 10/10
- ✅ commit_eod commits 'approved' rows
- ✅ commit_eod commits 'flagged' rows
- ✅ commit_eod skips 'rejected' rows
- ✅ get_pending_issues_for_site returns approved + flagged
- ✅ hod_reject_pending_issue moves to archive
- ✅ hod_unapprove_pending_issue flips approved → pending_hod
- ✅ bogus 'Approved' column dropped from consumption
- ✅ rejected_issues_archive schema present
- ✅ CONSUMPTION_EXPORT_COLS contains canonical set
- ✅ SMR reject at HOD flips line_status='rejected_at_hod'

### Round 14 — 5/5
- ✅ prep_image_for_vision caps long edge ≤ 1600 px
- ✅ prep_image_for_vision converts to RGB JPEG
- ✅ prep_image_for_vision shrinks byte size
- ✅ prep_image_for_vision honours EXIF orientation
- ✅ prep_image_for_vision raises ImagePrepError on bad bytes

### Round 15 — 13/15
- ✅ inventory_site_overrides schema + UNIQUE
- ✅ next_sap_code increments from max numeric tail
- ✅ next_temp_material_code persists + increments
- ✅ bulk_upsert_materials inserts + auto-codes blanks
- ✅ bulk_upsert_materials rejects duplicates
- ✅ bulk_upsert_materials overwrite path updates in place
- ✅ set/get_site_min_qty COALESCEs override over default
- ✅ inventory.Material_Code UNIQUE index enforced
- ❌ process_po_pdf extracts 3 items from sample PDF
- ✅ process_po_pdf synthetic two-line layout fixture
- ✅ list_pending_hod_dns falls back via PO/PR Site_ID
- ✅ request_reschedule routes to warehouse post-receive
- ✅ request_reschedule keeps logistics for PO-level
- ✅ CONSUMPTION_EXPORT_COLS unchanged
- ❌ _ALWAYS_KEEP includes UOM for PR report

### Round 16 — 4/5
- ✅ submit_dn_for_logistics writes status='pending_hod'
- ✅ submit_dn_for_logistics fans out HOD + Logistics notifications
- ✅ legacy pending_logistics DNs migrate to pending_hod
- ✅ get_pr_with_po_numbers comma-joins per PR line
- ❌ generate_pr_pdf renders new PO # + UoM columns

### Round 17 — 13/13
- ✅ sme_equipment table + key columns present
- ✅ sme_recipe table + key columns present
- ✅ sme_sqm_progress table + composite PK
- ✅ system_settings seeded with sme_location + sme_equipment_type for HQ
- ✅ init_db idempotent for SME tables (run twice, no errors)
- ✅ get_on_order_by_material: Qty=10 Delivered=3 Returned=1 → 6
- ✅ get_on_order_by_material: closed POs ignored
- ✅ get_on_order_by_material: site filter scopes correctly
- ✅ get_sme_inventory_view bridges ledger → engine schema
- ✅ add_sme_setting / delete_sme_setting round-trip
- ✅ upsert_sme_sqm_progress preserves Done_SQM on re-load
- ✅ Material Estimator RBAC: hod + admin only
- ✅ Material Estimator portal listed in PAGE_ACCESS

### Round 18 — 13/13
- ✅ sme_sqm_progress.Done_SQM_staged column present
- ✅ sme_consumption_log table + status FSM
- ✅ v_inventory_with_sme exposes is_sme flag
- ✅ is_sme_sap / is_sme_material dispatch fork
- ✅ get_sap_for_material resolves the 1:1 mapping
- ✅ stage_sme_consumption_batch aggregates per Material_Code
- ✅ stage_sme_consumption_batch rejects missing extras
- ✅ stage_sme_consumption_batch increments Done_SQM_staged
- ✅ commit_eod_with_sme_sync shifts staged→committed
- ✅ commit_eod itself is unchanged (regression)
- ✅ hod_reject_pending_issue_with_sme_sync decrements staged
- ✅ SME consumption form helper present in daily_issue_log
- ✅ hod_portal wires the SME-sync EOD + reject wrappers

### Round 20 — 11/11
- ✅ material_estimator_portal.py exists + exports page_material_estimator
- ✅ portal module loads cleanly (no module-level set_page_config)
- ✅ SME <style> CSS block preserved
- ✅ _apply_theme_attr preserved (dark/light mode toggle)
- ✅ Inventory tab body deleted (R18 owns consumption flow)
- ✅ tab unpacking has tab_scr, no tab_consume
- ✅ _show_login + auth gate deleted
- ✅ monkey-patch SCOPED inside page_material_estimator
- ✅ locations/types CRUD routes through add_sme_setting / delete_sme_setting
- ✅ compatibility VIEWS created in init_db (locations/types/consumption_log/equipment/recipe/sqm_progress)
- ✅ ERP ledger schemas unchanged (regression — R18 routing rule)

### Round 20.1 — 4/4
- ✅ no string-interior 8-space indent (markdown-as-code-block bug)
- ✅ cascade_allocate returns DataFrame with expected columns when empty
- ✅ Stock-Only Materials filter restricted to SME-tracked items
- ✅ load_all returns shape-preserving empty frames

### Round 20.5 — 11/11
- ✅ sme_equipment extended with 15 legacy Excel columns
- ✅ sme_recipe extended with 8 legacy Excel columns
- ✅ sme_inventory_seed table exists with correct schema
- ✅ sme_materials_view computes Available_Qty from seed + ledger
- ✅ equipment VIEW exposes Lining_System / Material Spec. / Lining_Area/location aliases
- ✅ recipe VIEW serves real Lining_Type (not empty literal)
- ✅ 9 SME CRUD helpers exist in database.py
- ✅ helpers translate UI form keys to PascalCase columns
- ✅ Tab 8 has no raw view-write SQL remaining
- ✅ TABLE_MAP points Materials_DetailsAvailable_Qty → sme_materials_view
- ✅ Equipment Smart Entry calls D.insert_sme_equipment + D.upsert_sme_sqm_progress

### Round 20.5.1 — 2/2
- ✅ Master Data read does not ORDER BY rowid on a VIEW
- ✅ get_sme_inventory_view is seed-sourced (not ERP live stock)

### Round 20.5.2 — 3/3
- ✅ no live import of the deleted material_estimator package
- ✅ days_of_continuation_block vendored into daily_issue_log
- ✅ Location Report reconciles stale loc_order vs eq_master

### Schema — 216/216
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
- ✅ table · supervisor_material_requests
- ✅ table · supervisor_material_request_items
- ✅ table · cross_site_views
- ✅ table · form_drafts
- ✅ column · inventory.SAP_Code
- ✅ column · inventory.Material_Code
- ✅ column · inventory.Equipment_Description
- ✅ column · inventory.UOM
- ✅ column · inventory.Minimum_Qty
- ✅ column · inventory.Unit_Cost
- ✅ column · inventory.Category
- ✅ column · inventory.Opening_Stock
- ✅ column · inventory.Site_ID
- ✅ column · consumption.Source_Ref
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
- ✅ column · employees.Site_ID
- ✅ column · supervisor_material_requests.request_no
- ✅ column · supervisor_material_requests.Site_ID
- ✅ column · supervisor_material_requests.Worker_ID
- ✅ column · supervisor_material_requests.Worker_Name
- ✅ column · supervisor_material_requests.Job_Tank_Place
- ✅ column · supervisor_material_requests.Old_PPE_Returned
- ✅ column · supervisor_material_requests.No_Return_Reason
- ✅ column · supervisor_material_requests.requested_by
- ✅ column · supervisor_material_requests.requested_at
- ✅ column · supervisor_material_requests.status
- ✅ column · supervisor_material_requests.sk_decided_by
- ✅ column · supervisor_material_requests.sk_decided_at
- ✅ column · supervisor_material_requests.sk_reject_reason
- ✅ column · supervisor_material_requests.posted_pending_ids
- ✅ column · supervisor_material_request_items.request_id
- ✅ column · supervisor_material_request_items.SAP_Code
- ✅ column · supervisor_material_request_items.Material_Code
- ✅ column · supervisor_material_request_items.Equipment_Description
- ✅ column · supervisor_material_request_items.UOM
- ✅ column · supervisor_material_request_items.Requested_Qty
- ✅ column · supervisor_material_request_items.Stock_At_Request
- ✅ column · supervisor_material_request_items.Available_Flag
- ✅ column · supervisor_material_request_items.SK_Adjusted_Qty
- ✅ column · supervisor_material_request_items.Notes
- ✅ column · pending_issues.Source_Ref
- ✅ column · cross_site_views.viewer_username
- ✅ column · cross_site_views.viewer_site_id
- ✅ column · cross_site_views.target_site_id
- ✅ column · cross_site_views.view_date
- ✅ column · cross_site_views.first_seen_at
- ✅ column · form_drafts.username
- ✅ column · form_drafts.form_id
- ✅ column · form_drafts.site_id
- ✅ column · form_drafts.payload_json
- ✅ column · form_drafts.created_at
- ✅ column · form_drafts.updated_at
- ✅ column · form_drafts.expires_at
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

### Uploads — 1/1
- ✅ Disk-mirror cleanup: old removed, recent kept (#19)

### Valuation — 1/1
- ✅ Per-site Unit_Cost override + fallback (#15)

### Vendors — 1/1
- ✅ Master add/update/status + bulk import dedupe (#24)

### Warehouse — 6/6
- ✅ Acknowledge + receive (partial + over-deliver guard)
- ✅ Warehouse view strictly hides prices (items + header)
- ✅ DN splitter enforces RL/BL strict separation
- ✅ Full DN flow → SK confirms → receipts row
- ✅ Internal return reopens PO line
- ✅ HOD rejection terminates the DN cleanly

### WhatsApp — 2/2
- ✅ queue_whatsapp_alert writes pending row
- ✅ failed sends auto-retry up to the cap, then fail

### Workstream C — 7/7
- ✅ webhook parses a text message (phone + body + name)
- ✅ webhook parses an interactive button_reply title
- ✅ status callbacks are separated from inbound messages
- ✅ stub router replies to greetings, stays silent otherwise
- ✅ GET handshake token + X-Hub-Signature-256 verification
- ✅ Meta provider — config read + routing + missing-config raise
- ✅ SME download_button forwards width= (no HOD TypeError)
