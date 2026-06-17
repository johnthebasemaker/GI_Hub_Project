# General Industries Hub — Standard Operating Procedure

**Version 1.0** · Procurement Chain (Logistics + Warehouse + Site)
**Owners:** Logistics Manager · Warehouse Lead · Site HOD Council
**Applies to:** All sites + warehouses connected to the GI Hub ERP
**Companion docs:** `USER_MANUAL.md` (every page/tab/button) · `handoff.md` (technical architecture)
**Cadence:** Review quarterly; update on any role change, vendor onboarding, or DN-state-machine alteration.

---

## §1. Purpose & Scope

This SOP turns the procurement-chain features of GI Hub v3.0 into a daily / weekly operating rhythm. It answers *"what should I do at 8 AM Tuesday?"* — for that, USER_MANUAL is too granular and handoff.md too technical.

**In scope:**
- Daily / weekly / monthly cadences for Site HOD, Logistics, Warehouse User, Store Keeper, Admin
- Decision trees for the four most common judgment calls (reject vs reschedule vs force-close, return vs adjustment, override timing, when to close)
- Escalation matrix — who-pings-who-after-how-long, with WhatsApp / email / admin override
- Recovery procedures — DN rejected at HOD, warehouse short-receives, vendor return + resupply, HOD absent
- Quick reference cards per role

**Out of scope:**
- UI screenshots (use USER_MANUAL.md)
- Code / SQL details (use handoff.md)
- Hosting + backup procedures (use handoff.md §4 and USER_MANUAL §17)

---

## §2. Role Responsibility Matrix (RACI)

R = Responsible (does the work) · A = Accountable (signs off) · C = Consulted (input) · I = Informed (notified)

| Activity | Site HOD | Logistics | Warehouse User | Store Keeper | Admin |
|---|:---:|:---:|:---:|:---:|:---:|
| Create PR | R/A | I | — | — | I |
| Submit PR to Logistics | R/A | I | — | — | I |
| Negotiate vendor + price | I | R/A | — | — | C |
| Issue PO | I | R/A | — | — | I |
| Upload PO PDF | — | R/A | — | — | — |
| Assign PO to a warehouse | I | R/A | I | — | I |
| Acknowledge assignment | — | I | R/A | — | — |
| Receive goods from vendor | — | I | R/A | — | — |
| Prepare DN (RL/BL separated) | — | I | R/A | — | — |
| Approve DN delivery date | — | R/A | I | — | I |
| Approve DN content | R/A | I | I | — | I |
| Mark DN as received (site) | I | I | I | R/A | — |
| Request reschedule | C/R | A | C/R | — | I |
| Raise vendor return | C | A | C/R | C | I |
| Force-close PR / PO / line | I | R/A | — | — | I (critical) |
| Approve user registrations | — | — | — | — | R/A |
| Maintain vendor / warehouse master | — | C | C | — | R/A |
| Review audit logs | — | — | — | — | R/A |
| Resolve oversight escalations | C | C | C | C | R/A |

---

## §3. Cadence Checklists

### §3.1 Site HOD — Daily

**Morning (8:00 AM):**
- [ ] Open HOD Portal → check the 🔔 bell badge in the sidebar
- [ ] Open inbox → mark non-critical items read; act on critical (red border)
- [ ] 📤 EOD Commit tab — confirm yesterday's commits posted cleanly (no pending rows from yesterday)
- [ ] 📬 Pending Receipts — review and approve any inbound receipts (legacy path)
- [ ] 🚚 DN Approvals (NEW) — approve or reject any DNs the Warehouse has prepared
- [ ] 🚚 In-Transit → "Active in-transit" sub-tab — scan ETAs for the day

**Through the day:**
- [ ] When PRs are raised — submit to Logistics within 1 hour (🚚 Submit PR(s) to Logistics expander)
- [ ] Reschedules from team — verify before submitting, include real reason
- [ ] EOD Commit by 17:00 — typed COMMIT confirmation

**Evening (before sign-off):**
- [ ] 🚚 In-Transit → confirm tomorrow's expected DNs are still on schedule
- [ ] Force-closures sub-tab — anything new since last login?
- [ ] Sign out cleanly (audit log)

### §3.2 Site HOD — Weekly (Sunday or first workday)

- [ ] 📋 Purchase Requests — clean up any PR in `site_draft` longer than 7 days (either submit or delete)
- [ ] 🚚 In-Transit → My reschedule requests — chase any "pending" older than 48 hours
- [ ] Force-closures — review the past 7 days; raise an admin ticket on any without clear reason
- [ ] Cross-Site requests — fulfill outstanding incoming requests

### §3.3 Site HOD — Monthly

- [ ] PR backlog review — anything still in `submitted`/`in_po` from > 30 days ago needs a call to Logistics
- [ ] Generate **🧾 PO Status** report scoped to your site — review on-time delivery rate
- [ ] Audit your own bell inbox for missed criticals (filter to unread)

---

### §3.4 Logistics — Hourly (during work hours)

- [ ] 🔔 bell badge — clear unread within 30 minutes during business hours
- [ ] 📥 Incoming PRs queue — pick up new PRs within 4 hours; acknowledge to Site HOD via WhatsApp if delayed
- [ ] 🔁 Reschedules — decide within 2 hours during business hours; out-of-hours, EOD next business day

### §3.5 Logistics — Daily

**Morning (8:00 AM):**
- [ ] Run the day's PO PDF backlog (any vendor PDFs sitting in your email)
- [ ] 📋 Open POs → filter to "Expected" = today + tomorrow → confirm each has a warehouse assignment
- [ ] 📋 Open POs → filter to "Expected" = yesterday → if not delivered, chase warehouse + vendor
- [ ] ✈️ DN approval queue — approve same-day if delivery date is reasonable
- [ ] ↩️ Vendor Returns — chase open returns > 7 days old

**End of day:**
- [ ] Confirm every PO assigned today shows in the receiving Warehouse's notification log
- [ ] T-1 dashboard (Admin Portal → Logistics Oversight → DNs sub-tab) — pre-empt tomorrow's deliveries

### §3.6 Logistics — Weekly

- [ ] Review the WHATSAPP_TRIGGERS toggles in `config.py` — adjust if noise complaints came in
- [ ] Generate **🏭 Warehouse Throughput** report — flag underperforming warehouses to Warehouse Lead
- [ ] Generate **🛑 Force-Closures** report — review the week's closures with Admin
- [ ] Vendor performance review — open returns + delivery delays per vendor

### §3.7 Logistics — Monthly

- [ ] Vendor master cleanup — deactivate dormant vendors (>90 days no PO)
- [ ] PR-to-PO cycle-time analysis — is the chain getting faster or slower?
- [ ] Force-closure trend — too many means upstream PR quality is poor

---

### §3.8 Warehouse User — Per Shift

**Shift start:**
- [ ] 🔔 bell badge — clear any unread assignments / DN rejections
- [ ] 🔔 Incoming Assignments tab — ✅ Acknowledge new assignments within 1 hour
- [ ] Review today's Expected_Delivery list — physically prepare receiving area

**During shift (when goods arrive):**
- [ ] 📦 Receive Goods → record actual qty per line as physical inspection completes
- [ ] If short-receive vs PO qty — note the discrepancy in remarks; Logistics will be notified
- [ ] If quality issue at receive — DON'T add to stock; raise via ↩️ Returns from Site or a vendor-return note to Logistics

**Before DN preparation:**
- [ ] Confirm RL and BL items go on SEPARATE DNs (system enforces, but plan physically too)
- [ ] Verify destination site for each DN
- [ ] Driver + vehicle details ready

**Shift end:**
- [ ] ✈️ Outbound DNs → confirm every DN drafted today is submitted to Logistics (no orphan drafts)
- [ ] Sign off — audit-logged

### §3.9 Warehouse User — Weekly

- [ ] 📂 History tab — sanity-check completed DNs match physical movement records
- [ ] Open assignments older than 7 days — chase Logistics for closure or vendor follow-up
- [ ] RL/BL inventory walk — physically verify families are separated in the warehouse

### §3.10 Warehouse User — Monthly

- [ ] Throughput review with Warehouse Lead — DNs prepared, on-time rate, rejection rate
- [ ] Vehicle / driver master refresh — update Vehicle_No / Driver_Name defaults

---

### §3.11 Store Keeper — Each Delivery Day

- [ ] 📝 Entry Log → 📦 Receipt Staging — the 🚚 Incoming Delivery Notes expander shows pending DNs
- [ ] Physical truck arrives → inspect, count, match against DN
- [ ] If everything matches → ✅ Mark as Received on the DN card
- [ ] If discrepancy → tell HOD before marking; HOD escalates to Logistics

### §3.12 Store Keeper — Standard Daily (unchanged from v2.0)

See `USER_MANUAL.md` §4 for the Consumption Log / Receipt Staging / Return Items / Returnable Items / Stock Count / QR Label Request daily routines.

---

### §3.13 Admin — Weekly

- [ ] Admin Portal → 🚚 Logistics Oversight → review every sub-tab
- [ ] Force-closures sub-tab — any not flagged "agreed-by-stakeholders" → ask Logistics
- [ ] 👥 Users → review pending registrations (Logistics + Warehouse users always go through admin approval)
- [ ] 📜 Audit Logs → filter by FORCE_CLOSE_* actions

### §3.14 Admin — Monthly

- [ ] Generate all 3 procurement reports (PO Status, Warehouse Throughput, Force-Closures)
- [ ] Bell-noise audit — check `app_notifications` row count vs prior month; sudden spike means a process change is needed
- [ ] Vendor + Warehouse master review — deactivate stale entries

### §3.15 Admin — Quarterly

- [ ] WHATSAPP_TRIGGERS tuning meeting — review with Logistics, adjust per-event toggles
- [ ] SOP review — update this document with any process changes
- [ ] Database backup integrity test — restore to a sandbox + run bug_check.py

---

## §4. Decision Trees

### §4.1 DN approval — Approve vs Reject vs Reschedule

```
                     ┌─────────────────────────┐
                     │  DN in HOD approval     │
                     │  queue (or Logistics)   │
                     └────────────┬────────────┘
                                  │
                  ┌───────────────┴───────────────┐
                  │ Inspect: qty, family,         │
                  │ lot, expiry, vehicle, driver  │
                  └───────────────┬───────────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              ▼                   ▼                   ▼
       Everything OK    Date wrong but content    Content wrong
              │           is fine                     │
              │              │                        │
              ▼              ▼                        ▼
         ✅ Approve    🔁 Reschedule              ❌ Reject
                       (set new date)         (must include reason)
                              │                        │
                              ▼                        ▼
                       Logistics decides     Warehouse rebuilds DN;
                       within 2h business   loop continues
```

**Approve when:** Qty matches PR · Family correct · Lot has > 90d shelf life · Vehicle + driver populated · No HSE concern with this delivery date

**Reschedule (don't reject) when:** Content is fine but YOU can't receive on the scheduled date (site shutdown, manpower, weather). Reschedule is faster than reject + rebuild.

**Reject when:** Qty doesn't match PR · Wrong material · Lot expiry inadequate · Wrong destination site (misroute) · Driver/vehicle missing critical info

### §4.2 Vendor return vs Stock adjustment

```
                ┌─────────────────────────────┐
                │  Discrepancy discovered     │
                └──────────────┬──────────────┘
                               │
            ┌──────────────────┴──────────────────┐
            │  Was material RECEIVED already      │
            │  via a DN this site approved?       │
            └──────────────────┬──────────────────┘
                               │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
      YES (in receipts)               NO (still at warehouse)
              │                                 │
              ▼                                 ▼
   ┌──────────────────────┐         ┌──────────────────────┐
   │ Is the issue with    │         │  Warehouse handles   │
   │ THIS DN (defective,  │         │  internally — Return │
   │ wrong qty), or       │         │  to vendor flow      │
   │ general stock        │         │  (Tab 8 Warehouse)   │
   │ count drift?         │         └──────────────────────┘
   └──────────┬───────────┘
              │
    ┌─────────┴─────────┐
    ▼                   ▼
This DN              General drift
    │                   │
    ▼                   ▼
↩️ Vendor          🧮 Stock Adjustment
   Return            (SK Entry Log →
   (any role)        Stock Count tab)
```

**Use Vendor Return when:** A specific DN brought defective / wrong qty / quality-issue material. Reopens the originating PO line so Logistics can chase vendor.

**Use Stock Adjustment when:** Cycle-count discovers shelf qty ≠ system qty for a general reason (damage in storage, miscount, expired-disposal, etc.). Doesn't touch the PO chain.

### §4.3 30-day return window — Override or not?

```
                ┌─────────────────────────────┐
                │  SK / HOD wants to return   │
                │  material to logistics      │
                └──────────────┬──────────────┘
                               │
                  ┌────────────┴────────────┐
                  │ Received in last 30d?   │
                  └────────────┬────────────┘
                               │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
            YES                                 NO
              │                                 │
              ▼                                 ▼
   Standard return flow         Override required:
   (SK Entry Log → Return)      tick "Override 30-day window"
                                + write justification
                                       │
                                       ▼
                               HOD reviews — override
                               rows highlighted RED
                                       │
                               ┌───────┴───────┐
                               ▼               ▼
                       Justified            Not justified
                           │                    │
                           ▼                    ▼
                      ✓ Approve            ✗ Reject
                                     (use Stock Adjustment
                                      with reason `damaged`
                                      or `expired_disposal`)
```

### §4.4 Force-close decision

```
                ┌─────────────────────────────┐
                │  Logistics considering      │
                │  force-closing PR/PO/line   │
                └──────────────┬──────────────┘
                               │
                  ┌────────────┴────────────┐
                  │ Has stakeholder been    │
                  │ consulted (Site HOD)?   │
                  └────────────┬────────────┘
                               │
                  NO ─────────┐│┌──────── YES
                               │ │
                               ▼ ▼
               STOP — call HOD first.       Proceed to next gate.
               Force-close is permanent.
                                         │
                            ┌────────────┴────────────┐
                            │  Is the reason in       │
                            │  scope of force-close?  │
                            └────────────┬────────────┘
                                         │
                       In scope ───────┐│┌────── Out of scope
                                       │ │
                                       ▼ ▼
                 ✓ Force-close      Use alternative:
                 with full reason   - Vendor cancellation → close PR
                                    - Wrong qty on PO → vendor return + new PO
                                    - Wrong material → vendor return
                                    - Project cancelled → close PR
```

**In scope for force-close:** Project cancellation · Vendor permanently unable to fulfill · PR raised in error and beyond email-recall · Item discontinued · Compliance violation

**Out of scope (use other tool):** Quantity / quality issues (use vendor return) · Date slippage (use reschedule) · Wrong destination site (rebuild DN, no force-close needed)

---

## §5. Escalation Matrix

When a process step doesn't happen on time, here's who pings whom, when, and on which channel.

### §5.1 PR → PO escalation

| Trigger | Wait time | Action | Channel |
|---|---|---|---|
| PR submitted to Logistics, no PO after 4 business hours | 4h | Site HOD → Logistics lead | WhatsApp |
| ↑ Still nothing after another 4h | 8h | Site HOD → Admin | App notification + email |
| ↑ Still nothing after 24h | 24h | Admin overrides — assigns alternate Logistics user | Admin Portal → 👥 Users |

### §5.2 PO → Assignment escalation

| Trigger | Wait time | Action | Channel |
|---|---|---|---|
| PO issued, no warehouse assignment after 8 business hours | 8h | Site HOD → Logistics (with PO number) | WhatsApp |
| Assignment created, Warehouse hasn't acknowledged in 24h | 24h | Logistics → Warehouse Lead | WhatsApp + email |
| ↑ Still no ack after 48h | 48h | Warehouse Lead → Admin | Admin escalation |

### §5.3 DN approval escalation

| Trigger | Wait time | Action | Channel |
|---|---|---|---|
| DN sitting at `pending_logistics` for > 4 business hours | 4h | Warehouse → Logistics | WhatsApp |
| DN sitting at `pending_hod` for > 8 business hours | 8h | Logistics → Site HOD | WhatsApp |
| DN sitting at `pending_sk` for > 24 hours after expected arrival | 24h | HOD → SK + Warehouse | WhatsApp |

### §5.4 Reschedule escalation

| Trigger | Wait time | Action | Channel |
|---|---|---|---|
| Reschedule pending > 2 business hours during work day | 2h | Requester → Logistics | WhatsApp |
| ↑ Pending > 24h | 24h | Site HOD → Admin | App + email |

### §5.5 Vendor return escalation

| Trigger | Wait time | Action | Channel |
|---|---|---|---|
| Vendor return raised, no acknowledgement from vendor in 7 days | 7d | Logistics → Vendor directly (email) | Email |
| ↑ Still unresolved after 14d | 14d | Logistics → Site HOD + Admin | App + email |
| Resupply date passed without delivery | T+1d | Logistics → Admin | Critical |

### §5.6 Force-closure escalation

Force-closures fire `critical` notifications to Admin + originating Site HOD instantly. There's no escalation needed — the action is already at maximum severity. Admin reviews weekly per §3.13.

---

## §6. Recovery Procedures

### §6.1 DN rejected at HOD — how Warehouse recovers

1. Bell shows rejection notification with HOD's reason
2. Warehouse user reads the reason from the notification card (full text in the related DN row in `delivery_notes.rejection_reason`)
3. Determine what changed:
   - If qty wrong: prepare a new DN with corrected qty (the rejected DN's qty frees back into the available calculation immediately)
   - If material wrong: file an internal Stock Adjustment if you over-counted at receive, OR raise a vendor return if the wrong material was delivered to you
   - If date wrong: prepare new DN with the corrected date; HOD will approve in same cycle
4. Submit new DN → Logistics → HOD; loop continues

### §6.2 Warehouse short-receives from vendor

1. Receive Goods tab — type the actual received qty (less than ordered)
2. System auto-flips `po_items.line_status` to `partially_delivered`
3. Add a note in the assignment notes describing the shortfall
4. Notify Logistics via WhatsApp with the PO number + shortfall qty
5. Logistics raises a Vendor Return with reason "Vendor under-shipped" (uses the same return flow, reopens the line for resupply)
6. New shipment from vendor → Warehouse receives the remainder → line flips to `delivered`

### §6.3 Vendor return + resupply cycle

1. Issue surfaces (SK at site OR Warehouse at receive)
2. Determine raiser:
   - Issue at site after SK confirmed → SK raises to HOD → HOD raises to Warehouse via internal return → Warehouse raises Vendor Return
   - Issue at warehouse before DN ship → Warehouse raises Vendor Return directly
3. Logistics tracks return in `↩️ Vendor Returns` tab
4. Expected_Resupply date set (default today + 14)
5. T-2 / T-1 / T-0 reminders fire on Expected_Resupply
6. When resupply arrives: Warehouse re-receives → line `Delivered_Qty` recalculates → status moves forward

### §6.4 HOD absent for > 24h with pending DN

1. Admin shadows HOD Portal (admin can access via the existing override)
2. Admin reviews the pending DN(s) in HOD Portal → 🚚 DN Approvals
3. Admin approves on behalf, leaves a note in the DN attesting to the cover
4. Audit log records `DN_HOD_APPROVE` with admin's username
5. HOD-on-return is notified to review post-fact

### §6.5 Logistics absent — Admin override

1. Admin enters Logistics Portal via shadow access
2. Admin acts as Logistics for the day (approve PRs, issue POs if pre-arranged, approve reschedules)
3. Audit log records each action with admin's username (not the absent Logistics user)
4. Admin notifies the absent Logistics user via WhatsApp before signing off

### §6.6 Force-closure made in error

There's no "undo force-close" button (yet — on the v3.0 backlog). Workaround:

1. Admin → Master DB Editor → relevant table (`purchase_orders` / `pr_master`)
2. Edit `status` from `force_closed` back to `open` (PO) or remove the `logistics_status='force_closed'` flag (PR)
3. Add a corresponding `po_force_closures` row with reason "Reversed in error — see audit"
4. The Site HOD's "Force-closures affecting me" tab will show both the original close + the reversal

Use sparingly — this is invasive. Better: communicate the reversal verbally and update via DB Editor with a paper trail.

### §6.7 RL/BL DN was prepared as combined (system rejected, user confused)

1. UI shows: *"Strict separation violated: this DN spans multiple RL/BL families. Prepare one DN per family."*
2. Warehouse user reduces Ship Qty to 0 on either the RL lines OR the BL lines (not both)
3. Save DN draft — succeeds with only one family
4. Prepare second DN with the other family — same source PO, different family
5. Submit both DNs separately; both flow through approval independently

---

## §7. Quick Reference Cards

### §7.1 Site HOD — One-page summary

**Daily must-dos:** Bell badge clear · Pending Receipts approved · DN Approvals queue empty · EOD commit by 17:00 · Sign out clean.
**Submit PR to Logistics:** HOD Portal → 📋 PRs → 🚚 Submit PR(s) to Logistics expander → multi-select → 📨 Submit.
**See what's incoming:** 🚚 In-Transit → Active in-transit (sorted by closest-to-SK).
**Request reschedule:** 🚚 In-Transit → click DN card → 🔁 Request reschedule (date defaults to ETA + 3 days).
**Escalate stuck PR:** WhatsApp Logistics if no PO after 4h business hours.
**Force-closure questions:** Check 🚚 In-Transit → Force-closures sub-tab; escalate to Admin if reason looks weak.

### §7.2 Logistics — One-page summary

**Hourly must-dos:** Bell badge ≤ 30min · 📥 Incoming PRs queue ≤ 4h · 🔁 Reschedules ≤ 2h.
**Issue PO from PR:** 📥 Incoming PRs → drill in → 🧾 Use this PR to create a PO → fill header → save.
**Issue PO from vendor PDF:** 🧾 Create PO → 📄 PDF upload → Extract → Review → Save.
**Assign to warehouse:** 🏭 Assign → pick PO + WH → confirm items → Expected Delivery → 📨 Assign.
**Approve DN date:** ✈️ Outbound DNs queue (Tab 4 visible on Warehouse Portal — Logistics actions through ✈️ DN approval queue) → ✅ Approve.
**Force-close:** Always call Site HOD first. Then 🛑 Force-Close with detailed reason.

### §7.3 Warehouse User — One-page summary

**Per shift:** Bell badge clear · 🔔 Incoming Assignments acked within 1h · Outbound DNs all submitted (no orphan drafts).
**Receive goods:** 📦 Receive Goods → assignment → type Receive Now per line → 📥 Record receipt.
**Prepare DN:** 📝 Prepare DN → pick PO + site → Ship Qty per line → header → 📝 Save → ✈️ Submit.
**Strict rule:** ONE family (RL or BL) per DN. System rejects mixed.
**Returns from site:** ↩️ Returns from Site → DN → return qty + reason → ↩️ Raise to vendor.

### §7.4 Store Keeper — One-page summary

**On delivery day:** 📝 Entry Log → 📦 Receipt Staging → 🚚 Incoming DN expander → inspect → ✅ Mark as Received.
**On every day:** Consumption Log → record issues; Receipt Staging for direct deliveries; Stock Count for cycle counts; QR Label Request for new labels.
**Don't:** Mark received without physical inspection. Mix Returnable (tool loans) with Return Items (real returns).
**📷 Smart Scan — when CV detection is wrong:** the green "Auto-filled" card can be edited before submit. If a yellow "Top candidates" radio appears, pick the right one or override the tool name in the manual form. If no candidates show (or no active model), just type it manually — borrower fields stay pre-filled from the badge scan. The submitted loan records which path (CV vs manual) was used so adoption telemetry stays honest. Full workflow: `USER_MANUAL.md` §4.5.0. Trainer guide: `docs/cv_training_guide.md`.

### §7.5 Admin — One-page summary

**Weekly:** 🚚 Logistics Oversight scan; force-closures review; user registrations approve.
**Monthly:** 3 procurement reports (PO Status / Warehouse Throughput / Force-Closures); bell-noise audit.
**Quarterly:** SOP review; backup integrity test.
**Override:** Use Master DB Editor with a paper trail; document every override in `system_audit_log` via the affected helper.

---

## §8. Glossary

For complete vocabulary (FEFO, EOD, PR, DN, OCR, WAL, etc.) see `USER_MANUAL.md` §11.5. Procurement-chain-specific terms only here:

| Term | Meaning |
|---|---|
| **Procurement chain** | The in-app SQL-driven workflow from Site PR → Logistics PO → Warehouse DN → Site SK receipt. Replaces (optionally) the legacy email/PDF flow. |
| **DN state machine** | The seven-step lifecycle of a Delivery Note (`draft` → `pending_logistics` → `logistics_approved` → `pending_hod` → `hod_approved` → `pending_sk` → `received`), with `rejected` as terminal from any pending state. |
| **RL/BL strict separation** | Hard rule that Rubber Lining and Brick Lining never share a PO group, DN, or warehouse aggregation. Enforced at three layers. |
| **Warehouse-blind pricing** | Hard rule that warehouse_user role never sees Unit_Price, Total_Price, or monetary header fields on any PO. Three enforcement layers. |
| **Logistics shadow** | Admin's read+write access to the Logistics Portal as if they were a Logistics user. Used for absence cover. |
| **Warehouse shadow** | Admin's access to the Warehouse Portal with a sidebar warehouse picker. Used for absence cover or audit. |
| **Force-closure** | Logistics' one-way close of a PR / PO / line with mandatory reason. Notifies Admin + Site HOD with `critical` severity. |
| **T-2 / T-1 / T-0 reminders** | Daily sweep job firing notifications 2 days before, 1 day before, and on the expected delivery date. Idempotent. |
| **App notification** | A row in `app_notifications`, surfaced via the sidebar bell. Always fires; in-app only. |
| **WhatsApp trigger toggle** | `config.WHATSAPP_TRIGGERS[event_key] = True/False` to per-event control WhatsApp emission. In-app is unaffected. |

---

## §9. Change Log

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06 | Initial release | First publication alongside Hub v3.0 procurement chain. Covers Site HOD, Logistics, Warehouse User, SK, Admin. Includes RACI, daily/weekly/monthly cadences, 4 decision trees, 6-tier escalation matrix, 7 recovery procedures, 5 quick-reference cards. |

---

**End of SOP. Review quarterly. Update on any role change, vendor onboarding, escalation-time tweak, or DN-state-machine alteration. Companion: `USER_MANUAL.md` (UI reference) · `handoff.md` (technical architecture).**
