/**
 * Phase 21g — narrated TOURS: every page in the sidebar, read-only (PROPOSED_
 * PHASE21_PLAN §3.4, tier 2). A tour opens the page, spotlights its regions
 * and says what each is for, who uses it and what to look at. It never clicks
 * anything that changes data.
 *
 * ⚠️ COVERAGE IS CHECKED: `npm run test:nav` (scripts/tour_check.mjs) fails
 * when a sidebar route has no tour here, so a new page without one is named
 * before it ships. Record and master-data pages share one template each (they
 * are generated from the same entity list).
 *
 * Regions are generic AntD selectors and every region beat is OPTIONAL: a page
 * whose table is empty for this role still tours. Sentences are written here
 * and reviewed in a diff (P12-3). No real site names or supplier prices
 * (Q20-17).
 */
import type { Beat, DemoScript } from './types'

const TITLE = 'h3.ant-typography, h2.ant-typography, h1.ant-typography'
const TABS = '.ant-tabs-nav'
const TABLE = '.ant-table-wrapper'
const FORM = '.ant-form'
const CARD = '.ant-card'
const KPI = '.ant-statistic, .gi-kpi, [data-testid="kpi-row"]'

type Region = [say: string, target: string, text?: string]

export interface Tour {
  path: string
  /** Also tours every route that starts with this (records, master data). */
  prefix?: boolean
  title: string
  intro: string
  regions: Region[]
  close: string
}

const t = (path: string, title: string, intro: string, regions: Region[], close: string): Tour =>
  ({ path, title, intro, regions, close })

export const TOURS: Tour[] = [
  t('/', 'Dashboard', 'The Dashboard: the site at a glance, the first page after signing in.', [
    ['The figures across the top are today’s numbers: stock value, what is waiting for approval, what is low.', KPI],
    ['Top 5 expiring lists only lots that still have stock, soonest first.', CARD, 'expiring'],
    ['Reorder signals summarise what is below its minimum, and link to the full list.', '[data-testid="reorder-mini"]'],
  ], 'Every card links to the page that holds the detail.'),
  t('/stock', 'Stock', 'Stock: what is on hand, per material and per site.', [
    ['The tabs switch between the stock list, reorder signals and the other stock views.', TABS],
    ['Every column sorts and filters; the quantity is in the item’s own unit.', TABLE],
  ], 'Click a material to open its card: movements, lots and trend.'),
  t('/requests-pending', 'Requests & Pending', 'Requests and Pending: every line of the requests the site mails, from the Drive folder Pending Material Follow-up.', [
    ['The tags count the lines, how many are still pending, and how many were asked for without a PR.', '[data-testid="req-totals"]'],
    ['Received is worked out from the Receipt Log, oldest request first. An orange tag means the workbook says something else.', '[data-testid="req-table"]'],
    ['Lines with no SAP code are listed here so they can be fixed in the workbook.', '[data-testid="req-needs-sap"]'],
  ], 'Requests without a PR also count as on order in Smart Reorder, so nothing is ordered twice.'),
  t('/lots', 'Lots & Expiry', 'Lots and Expiry: every batch, its expiry, and what is left of it.', [
    ['The summary counts expired lots, lots expiring soon and lots in good date.', '[data-testid="lot-summary"]'],
    ['Each row is one lot. Sort by expiry, status or what is left.', '[data-testid="lot-table"]'],
    ['Lot problems from the workbook name the sheet and row to fix.', '[data-testid="lot-problems"]'],
  ], 'Issuing picks the earliest expiry first, and an expired lot is never suggested.'),
  t('/locator', 'Locator', 'Locator: where a material is kept — bin, shelf and yard.', [
    ['Search by name, code or bin.', FORM],
    ['The results show each place and the quantity held there.', TABLE],
  ], 'Store keepers keep bins up to date when they receive.'),
  t('/assets', 'Assets', 'Assets: tracked equipment with a serial number, and its condition.', [
    ['Each asset shows where it is and whether it is working.', TABLE],
  ], 'The app owns an asset’s status; the workbook sync never overwrites it.'),
  t('/entry/receive', 'Receive Stock', 'Receive Stock: goods arriving at the store are recorded here, with their paper.', [
    ['The material, the quantity in packs, and the date.', FORM],
    ['A lot-tracked item asks for its batch, manufacture date and expiry from the label.', FORM, 'Batch'],
    ['Lines go into a batch and are submitted to the HOD together.', CARD, 'Batch'],
  ], 'Nothing reaches the ledger until the HOD approves it.'),
  t('/entry/issue', 'Issue Stock', 'Issue Stock: everything that leaves the store is written here first.', [
    ['Pick the material; the line under it shows stock on hand and days of cover.', FORM],
    ['Leave the lot blank and the earliest-expiry lot is used.', FORM, 'Lot'],
    ['The batch, with its supporting paper, goes to the HOD in one submission.', CARD, 'Batch'],
  ], 'Over-issue is allowed and logged, never blocked.'),
  t('/entry/return', 'Return Stock', 'Return Stock: unused material coming back from the field.', [
    ['The material, the quantity and the reason it came back.', FORM],
  ], 'Like every entry, a return waits for the HOD before it changes stock.'),
  t('/entry/adjust', 'Stock Adjustment', 'Stock Adjustment: corrections to the count, each with a reason.', [
    ['Choose the material, the quantity up or down, and why.', FORM],
  ], 'Adjustments are audited and approved like any other entry.'),
  t('/entry/count', 'Stock Count', 'Stock Count: the physical count, compared with what the system expects.', [
    ['Count what is on the shelf; the difference is shown before anything is posted.', FORM],
  ], 'A count produces adjustments for the HOD to approve.'),
  t('/entry/returnables', 'Returnable Items', 'Returnable Items: tools lent to the field, and coming back.', [
    ['The return desk takes a scan or a code and finds the open loan.', '[data-testid="return-scan-input"]'],
    ['Every open loan, who has it and when it is due. A loan can come back in parts.', TABLE],
  ], 'Overdue loans are chased automatically, and the HOD is told when they go long overdue.'),
  t('/entry/ocr', 'OCR Import', 'OCR Import: a photographed consumption paper becomes rows to review.', [
    ['A photo goes to the local AI; the paste box works even when it is offline.', CARD],
    ['Green rows matched exactly, gold ones need your Accept, red ones need an item chosen.', TABLE],
  ], 'The paper’s date is checked, and an unlikely one must be confirmed before staging.'),
  t('/site/incoming', 'Incoming Deliveries', 'Incoming Deliveries: delivery notes on their way to this site.', [
    ['Each note shows what was sent; confirm what actually arrived.', TABLE],
  ], 'Confirming a delivery note prepares the receipt for you.'),
  t('/sk/requests', 'Supervisor Requests', 'Supervisor Requests: material the site supervisors have asked the store for.', [
    ['Each request shows who asked, what for, and how much.', TABLE],
  ], 'Fulfilling a request issues the stock against it.'),
  {
    path: '/records/', prefix: true, title: 'Records',
    intro: 'This is a records page: the ledger rows of one kind, as approved.',
    regions: [['Every column sorts and filters, and the list exports to Excel.', TABLE]],
    close: 'Records are read here; they change only through an entry and its approval.',
  },
  t('/hod/executive-summary', 'Executive Summary', 'Executive Summary: the HOD’s one-page view for management.', [
    ['Lining capacity, stock health and the period’s movements, ready to print.', CARD],
  ], 'It downloads as Excel or prints as a PDF.'),
  t('/hod/approvals', 'Approvals', 'Approvals: everything the site has submitted, waiting for the HOD.', [
    ['One tab per kind: receipts, issues, returns, adjustments, delivery notes.', TABS],
    ['Approve commits a line to the ledger; reject sends it back with your reason.', TABLE],
  ], 'Issues show the AI’s consumption analysis under each line before you approve.'),
  t('/hod/burn-rate', 'Burn Rate', 'Burn Rate: how fast each material is being used.', [
    ['Usage per day and per week, against the stock on hand.', TABLE],
  ], 'A rising burn rate is the earliest sign of a reorder.'),
  t('/hod/lining-coverage', 'Lining Coverage', 'Lining Coverage: how much of each tank is lined.', [
    ['Done against total area, per piece of equipment.', TABLE],
  ], 'Approved Surface Shield jobs move these numbers.'),
  t('/hod/documents', 'Document Library', 'Document Library: every paper attached to an entry, searchable.', [
    ['Find a document by entry, date or type, and open it.', TABLE],
  ], 'Nothing is lost: every supporting document is stored with its entry.'),
  t('/hod/wbs', 'WBS & Work Types', 'WBS and Work Types: the work packages a site charges material to.', [
    ['Once a site has WBS numbers, every issue must name one.', TABLE],
  ], 'The HOD manages the list for their own site.'),
  t('/hod/low-stock', 'Low Stock', 'Low Stock: materials below their minimum.', [
    ['How far below, and what is already on order.', TABLE],
  ], 'Raise a purchase request straight from here.'),
  t('/hod/prs', 'Purchase Requests', 'Purchase Requests: what the site has asked Procurement to buy.', [
    ['Each request, its lines and where it stands.', TABLE],
  ], 'A receipt that names the PR closes it automatically.'),
  t('/hod/requests', 'Cross-Site Requests', 'Cross-Site Requests: material borrowed from, or lent to, another site.', [
    ['Each request and its status.', TABLE],
  ], 'Both HODs see the same request.'),
  t('/bulk-import', 'Bulk Excel Import', 'Bulk Excel Import: the workbooks, synced into the system.', [
    ['A dry run first shows exactly what would change; only Commit writes.', CARD],
  ], 'Workbook problems are named with their sheet and row.'),
  t('/sme', 'Estimator', 'The Estimator: lining material needed against what is in stock and on order.', [
    ['The tabs move from the overview to each tank, the plan and procurement.', TABS],
  ], 'The same numbers are computed twice, by two engines, and must agree.'),
  t('/manhours', 'Manpower Tracking', 'Manpower Tracking: people, hours and the work they produced.', [
    ['Hours against output, per crew and per day.', TABS],
  ], 'Variance shows where the plan and the work disagree.'),
  t('/reports', 'Reports', 'Reports: every standard report, ready to download.', [
    ['Pick a report and a period; schedules send them by themselves.', CARD],
  ], 'The archive keeps every report that was sent.'),
  t('/logistics', 'Procurement', 'Procurement: purchase requests in, purchase orders out.', [
    ['The tabs follow a request from arrival to order to delivery.', TABS],
  ], 'Logistics sees every site’s requests in one place.'),
  t('/logistics/lining-coverage', 'Lining Coverage', 'Lining Coverage for Logistics: what each tank still needs.', [
    ['Remaining area per tank drives what lining material to buy.', TABLE],
  ], 'It is the same figure the HOD sees.'),
  t('/supervisor', 'Material Requests', 'Material Requests: the supervisor asks the store for material.', [
    ['What, how much, and for which job.', FORM],
  ], 'The store keeper sees the request and issues against it.'),
  t('/execution', 'Execution Entries', 'Execution: the work done on site, filed against the material it used.', [
    ['Surface Shield jobs: one card per day per tank. A card is ready when its remark gives the area.', CARD, 'Surface Shield'],
    ['Ready cards can be submitted together; the HOD approves them together.', '[data-testid="bulk-bar"]'],
  ], 'Approval credits the area to the tank once.'),
  t('/surface-shield/log', 'Daily Log', 'The Surface Shield daily log: every day of lining, and its status.', [
    ['Each row is one job: filed, pending, approved or rejected, with its area.', TABLE],
  ], 'A rejected job says why, so it can be filed again.'),
  t('/warehouse', 'Receiving & DN', 'Receiving and delivery notes for the central warehouse.', [
    ['Goods in, delivery notes out, each with its signed paper.', TABS],
  ], 'A delivery note cannot leave without its signed copy attached.'),
  t('/qc/inspections', 'Inspections', 'Quality inspections: Surface Shield material checked before it may be issued.', [
    ['Each inspection, its result and its certificate.', TABLE],
  ], 'Material without a certificate on file cannot reach the field.'),
  t('/qc/accounts', 'QC Accounts', 'QC Accounts: the quality inspectors and where they work.', [
    ['Each inspector and their site.', TABLE],
  ], 'Managed by the quality head.'),
  t('/qc-hod', 'Quality Oversight', 'Quality Oversight: the quality head’s view across every site.', [
    ['Open inspections, stagnant lots and what needs attention.', CARD],
  ], 'It reads across sites; it changes nothing on them.'),
  t('/ppe/forecast', 'PPE Forecast', 'PPE Forecast: protective equipment needed for the crew on site.', [
    ['Need per item against stock, from the crew size and usable time.', TABLE],
  ], 'Short items are flagged before they run out.'),
  t('/ppe/rules', 'PPE Usable Time', 'PPE Usable Time: how long each protective item lasts.', [
    ['One rule per item, used by the forecast.', TABLE],
  ], 'Change a rule and the forecast follows.'),
  t('/hr/employees', 'Employees', 'Employees: the people on site, with their badge and role.', [
    ['Each employee, active or not.', TABLE],
  ], 'Badges are scanned at the return desk and on PPE issues.'),
  {
    path: '/master/', prefix: true, title: 'Master data',
    intro: 'This is a master-data page: a list the rest of the system relies on.',
    regions: [['Add, edit or retire entries; every change is audited.', TABLE]],
    close: 'Change master data carefully: other pages read it.',
  },
  t('/admin/users', 'Users', 'Users: every account, its role and its site.', [
    ['Create accounts, reset passwords, change roles.', TABLE],
  ], 'A role decides what a person sees; the sidebar is built from it.'),
  t('/admin/pending', 'Access Requests', 'Access Requests: people asking for an account.', [
    ['Approve with a role and a site, or decline.', TABLE],
  ], 'Nobody gets in without an administrator approving them.'),
  t('/admin/overdue', 'Overdue Actions', 'Overdue Actions: anything waiting more than a day for someone.', [
    ['What is waiting, for whom, and since when.', TABLE],
  ], 'A nudge reminds the person by message.'),
  t('/admin/inventory', 'Inventory', 'Inventory administration: the material master.', [
    ['Codes, descriptions, units and pack sizes.', TABLE],
  ], 'The pack size turns packs into base units everywhere.'),
  t('/admin/audit', 'Audit Log', 'The Audit Log: who did what, and when.', [
    ['Every approval, change and sign-in, searchable.', TABLE],
  ], 'The log is append-only.'),
  t('/admin/console', 'Console', 'The admin Console: settings, backups, sessions and syncs.', [
    ['One tab per area, including the Drive sync and Feedback.', TABS],
  ], 'Demo requests from the assistant arrive under Feedback.'),
  t('/admin/ai-traces', 'AI Traces', 'AI Traces: every assistant request, what it retrieved and how long it took.', [
    ['Each request and its route through the assistant.', TABLE],
  ], 'Questions are kept; answers are not.'),
  t('/documents', 'Documents', 'Documents: the manuals, procedures and printable labels.', [
    ['Open a document or print labels and badges.', CARD],
  ], 'The user manual here is the same one the assistant reads.'),
  t('/security', 'Security', 'Security: your password, two-step sign-in and signed-in devices.', [
    ['Set up an authenticator, or sign out a device you no longer use.', CARD],
  ], 'Some roles must use two-step sign-in.'),
  t('/training', 'Training', 'Training: short videos for each part of the system.', [
    ['Pick a module; the assistant can jump straight to the right moment.', CARD],
  ], 'Videos are recorded on Practice data, never live.'),
  t('/feedback', 'Feedback', 'Feedback: report a problem or ask for a feature.', [
    ['Say what happened and where; the administrator answers here.', FORM],
  ], 'Your report goes straight to the admin.'),
]

/** The tour for a sidebar route (exact, or a prefix template). */
export function tourFor(path: string): Tour | undefined {
  return TOURS.find((x) => (x.prefix ? path.startsWith(x.path) : x.path === path))
}

/** A tour as a runnable script: open the page, spotlight its regions, close. */
export function tourScript(path: string, label: string): DemoScript | undefined {
  const tour = tourFor(path)
  if (!tour) return undefined
  const beats: Beat[] = [
    { say: tour.intro, do: 'navigate', value: path, until: TITLE, optional: true, timeout: 8000 },
    ...tour.regions.map(([say, target, text]): Beat =>
      ({ say, do: 'point', target, text, optional: true, timeout: 4000 })),
    { say: tour.close },
  ]
  return { id: `tour:${path}`, title: `Tour: ${label}`, blurb: tour.intro, roles: [], beats }
}
