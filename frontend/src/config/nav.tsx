/**
 * frontend/src/config/nav.tsx — the ONE source of truth for navigation access.
 *
 * Ports the legacy visibility model (`_can_access` in main.py:230 =
 * PAGE_ACCESS + _EXACT_ROLE_PAGES + _PAGE_BLOCKED_ROLES) into a single
 * declarative manifest that drives:
 *   1. the sidebar (AppLayout),
 *   2. client-side route guards (AppLayout redirects on denied paths),
 *   3. (Phase 3) the ⌘K command palette.
 *
 * The API already enforces every endpoint's role gate; this makes the UI agree
 * with it instead of leaking pages a role can open but not use.
 *
 * ADMIN SHADOW: admin may reach ANY page (legacy "lands via shadow"), but its
 * DEFAULT sidebar is a curated set — operational groups are revealed via the
 * "All areas" toggle (see ADMIN_DEFAULT_GROUPS). This mirrors legacy hiding
 * exact-locked pages from the admin sidebar while still allowing navigation.
 */
import type { ReactNode } from 'react'
import {
  AimOutlined, AlertOutlined, AuditOutlined, BarChartOutlined, CameraOutlined, CarOutlined,
  DashboardOutlined, DatabaseOutlined, EnvironmentOutlined, ExperimentOutlined, FallOutlined,
  FieldTimeOutlined, FileExcelOutlined, FileProtectOutlined, FileSearchOutlined, FireOutlined,
  FormOutlined, FundProjectionScreenOutlined, InboxOutlined, MessageOutlined,
  PlayCircleOutlined, ProfileOutlined,
  SafetyCertificateOutlined, SafetyOutlined, SolutionOutlined, TagsOutlined, StockOutlined, TeamOutlined,
  ToolOutlined, ControlOutlined, UserAddOutlined,
} from '@ant-design/icons'
import type { User } from '../auth/AuthContext'
import { isReadOnly } from '../auth/readOnly'
import { READ_ENTITIES, WRITE_ENTITIES, type ReadEntity } from './entities'

// An access rule: either an exact set of roles (admin implicitly allowed), or a
// minimum hierarchy level. `minLevel` reproduces the legacy cascading checks;
// `anyRole` reproduces the exact-locks.
//
// `writes` marks a page whose PURPOSE is changing data — a form, an approval
// queue, an importer, a CRUD editor. A view-only role (Auditor) cannot open
// one at all, which is what removes the great majority of Edit/Upload/Sync/
// Delete controls from its UI without touching a single page component. Pages
// that merely CONTAIN an action (Documents has an upload button) are NOT
// marked; those controls are disabled individually via useReadOnly().
export type AccessRule = ({ anyRole: string[] } | { minLevel: number })
  & { writes?: boolean }

export interface NavNode {
  key: string          // route path (also the antd menu key)
  label: string
  icon?: ReactNode
  access: AccessRule
  badge?: string       // key into the work-queues map → gold count badge
  redBadge?: boolean   // SLA/urgency → red count badge (admin overdue)
}

export interface NavGroup {
  id: string           // stable group id (used by ADMIN_DEFAULT_GROUPS)
  label?: string       // undefined → ungrouped top items
  access?: AccessRule  // group-level gate; a node still needs its own rule
  children: NavNode[]
}

/** Convenience: an access rule that also forbids view-only roles. */
const w = <T extends object>(rule: T): T & { writes: true } =>
  ({ ...rule, writes: true })

// ── role sets (2026-08-12 strict-RBAC pass) ─────────────────────────────────
//
// WHY THESE EXIST, and why `minLevel` is gone from the contested pages.
//
// `minLevel` is a ladder: SK 0 · warehouse/supervisor/qc 1 · hod 2 ·
// logistics/auditor 3 · admin 4. It was the right tool for the legacy portal,
// where seniority really was one line. It is the wrong tool now, because the
// roles are not a line — they are four different JOBS plus two oversight
// roles. A store keeper is not "less senior" than a warehouse user; they do a
// different job in a different place.
//
// Encoding that as `0 < 1` meant every rule aimed at one role leaked to five
// others. `minLevel: 1` admits SIX of the eight roles — which is how seven
// roles ended up holding the staff roster with phone numbers, and how the
// store keeper ended up as the ONE role locked out of the Stock page.
//
// `minLevel` survives only where it still means seniority: /reports,
// /master/*, /admin/* and the oversight ledgers in entities.ts. Everywhere
// else a page now NAMES the jobs that need it.
const SK = 'store_keeper'
const WH = 'warehouse_user'
const SUP = 'supervisor'
const QC = 'qc'
const HOD = 'hod'
const LOG = 'logistics'
const AUD = 'auditor'

// Everyone who works a shift somewhere and needs the site's operational view.
// QC is deliberately absent: an inspector's job is a queue, and the dashboard
// was reaching them only because the role was created at level 1 (operator
// ruling, 2026-08-12).
const OPERATIONAL = [SK, WH, SUP, HOD, LOG, AUD]
// …plus QC. Stock is the one overview an inspector genuinely needs: it is how
// they see what is waiting for them and what their decision released.
const OPERATIONAL_AND_QC = [SK, WH, SUP, QC, HOD, LOG, AUD]
// People who physically walk to a shelf, plus the role that owns the racking.
const FLOOR = [SK, WH, LOG]

// ── the manifest ────────────────────────────────────────────────────────────
// Access rules chosen to reproduce legacy exactly (see config.py PAGE_ACCESS /
// main.py _EXACT_ROLE_PAGES). Levels: SK 0 · warehouse/supervisor 1 · hod 2 ·
// logistics 3 · admin 4.
export const NAV: NavGroup[] = [
  {
    id: 'overview',
    children: [
      // ⚠️ The legacy Live Dashboard was supervisor+ and the SK "landed on its
      // role-home instead". That was a legacy-portal accident carried forward:
      // it left the person who physically holds the stock as the only role
      // that could not open the Dashboard OR the Stock page. Both endpoints
      // (`/dashboard`, `/stock/*`) have always accepted them — this is the
      // manifest catching up to the API, not a widening.
      { key: '/', label: 'Dashboard', icon: <DashboardOutlined />, access: { anyRole: OPERATIONAL } },
      { key: '/stock', label: 'Stock', icon: <StockOutlined />, access: { anyRole: OPERATIONAL_AND_QC } },
      // Phase 16 (ruling Q16-16): every lot, its expiry and what is left. The
      // roles that hold, inspect or answer for controlled material — the store
      // keeper, QC, the HOD and the Head of Qualities. Read-only.
      { key: '/lots', label: 'Lots & Expiry', icon: <ExperimentOutlined />, access: { anyRole: [SK, QC, HOD, 'qc_hod'] } },
      // The rack locator sits at the TOP LEVEL on purpose: the store keeper is
      // the person who has to walk to the shelf, and burying it inside a
      // supervisor group would hide it from its only real user. Narrowed
      // 2026-08-12 from "everyone" to the roles that walk to shelves — a
      // supervisor, an HOD, an inspector and an auditor never do.
      // NOT marked `writes` — finding a material is a read, and the
      // create/assign controls inside are guarded separately
      // (require_level(1) server-side, useReadOnly in the page).
      { key: '/locator', label: 'Locator', icon: <EnvironmentOutlined />, access: { anyRole: FLOOR } },
      // Serialised assets — hand tools, gauges, the things that get carried
      // off. Everyone who might sign one out, minus the inspector: a QC
      // inspects MATERIAL, not the hammer inventory.
      { key: '/assets', label: 'Assets', icon: <AimOutlined />, access: { anyRole: OPERATIONAL } },
    ],
  },
  {
    id: 'entry',
    label: 'Data Entry',
    access: w({ anyRole: [SK] }),   // Entry Log exact-locked to SK
    // Every node here is `w()` too, and not just for tidiness: the group rule
    // and the node rule are enforced by DIFFERENT functions, and until
    // 2026-08-12 the route guard checked only the node. A group that carries
    // `writes` over nodes that do not is a page reachable by URL that the
    // sidebar says is closed. Marked at both levels so the two agree whichever
    // one is consulted.
    children: [
      { key: '/entry/receive', label: 'Receive Stock', icon: <FormOutlined />, access: w({ anyRole: [SK] }) },
      { key: '/entry/issue', label: 'Issue Stock', icon: <FormOutlined />, access: w({ anyRole: [SK] }) },
      { key: '/entry/return', label: 'Return Stock', icon: <FormOutlined />, access: w({ anyRole: [SK] }) },
      { key: '/entry/adjust', label: 'Stock Adjustment', icon: <FormOutlined />, access: w({ anyRole: [SK] }) },
      { key: '/entry/count', label: 'Stock Count', icon: <FormOutlined />, access: w({ anyRole: [SK] }) },
      { key: '/entry/returnables', label: 'Returnable Items', icon: <ToolOutlined />, access: w({ anyRole: [SK] }), badge: 'returnables_overdue' },
      { key: '/entry/ocr', label: 'OCR Import', icon: <CameraOutlined />, access: w({ anyRole: [SK] }) },
      { key: '/site/incoming', label: 'Incoming Deliveries', icon: <InboxOutlined />, access: w({ anyRole: [SK] }), badge: 'incoming_dns' },
      { key: '/sk/requests', label: 'Supervisor Requests', icon: <SolutionOutlined />, access: w({ anyRole: [SK] }), badge: 'sk_requests' },
    ],
  },
  {
    id: 'records',
    label: 'Records',
    // Per-entity access (inventory is all-roles; ledger logs are hod+; POs are
    // logistics+; equipment is the SME read-lock). This is what stops "Issue &
    // Receipt Log" showing to every role.
    children: READ_ENTITIES.map((e) => ({
      key: `/records/${e.key}`,
      label: e.label,
      access: e.access,
    })),
  },
  {
    // 2026-08-04: `auditor` reads the HOD portal.
    //
    // The API was never the thing keeping it out — /hod/* is
    // `Depends(require_level(2))` and an auditor is level 3, so the backend
    // has always allowed these reads. Only this manifest hid them, which made
    // "the Auditor cannot see HOD" a UI accident rather than a policy.
    //
    // Added to the READ children only. Anything wrapped in `w()` stays
    // {hod, admin}: a `writes: true` page is unreachable for a view-only role
    // by design, and that is the mechanism that strips the Approve / Import
    // controls without touching a single page component. The write guard in
    // readonly.py is untouched and nothing is added to its allowlist — an
    // auditor that reached one of these pages still cannot POST from it.
    id: 'hod',
    label: 'HOD',
    access: { anyRole: ['hod', 'auditor'] },
    children: [
      { key: '/hod/executive-summary', label: 'Executive Summary', icon: <FundProjectionScreenOutlined />, access: { anyRole: ['hod', 'auditor'] } },
      { key: '/hod/approvals', label: 'Approvals', icon: <AuditOutlined />, access: w({ anyRole: ['hod'] }), badge: 'approvals' },
      { key: '/hod/burn-rate', label: 'Burn Rate', icon: <FireOutlined />, access: { anyRole: ['hod', 'auditor'] } },
      { key: '/hod/lining-coverage', label: 'Lining Coverage', icon: <ExperimentOutlined />, access: { anyRole: ['hod', 'logistics', 'auditor'] } },
      { key: '/hod/documents', label: 'Document Library', icon: <FileSearchOutlined />, access: { anyRole: ['hod', 'auditor'] } },
      // Phase 9a. The endpoints behind this have existed since the parity
      // build with no screen to reach them, which is exactly why every
      // live consumption row has a blank WBS.
      { key: '/hod/wbs', label: 'WBS & Work Types', icon: <TagsOutlined />, access: w({ anyRole: ['hod'] }) },
      { key: '/hod/low-stock', label: 'Low Stock', icon: <FallOutlined />, access: { anyRole: ['hod', 'auditor'] } },
      { key: '/hod/prs', label: 'Purchase Requests', icon: <ProfileOutlined />, access: { anyRole: ['hod', 'auditor'] } },
      { key: '/hod/requests', label: 'Cross-Site Requests', icon: <SolutionOutlined />, access: { anyRole: ['hod', 'auditor'] } },
      // Bulk Excel import — SME kinds for HOD; inventory/ledger cards appear
      // for admin only (server enforces both). NOT opened to the auditor: it
      // is a write surface end to end.
      { key: '/bulk-import', label: 'Bulk Excel Import', icon: <FileExcelOutlined />, access: w({ anyRole: ['hod'] }) },
    ],
  },
  {
    // The Estimator is a read-only analysis view and /sme/* is likewise
    // `require_level(2)`, so the auditor already reached the data. Its two
    // WRITE surfaces — Master Data and Actual Consumption — are
    // `require_roles("hod")` inside SmePage and on the server, so they simply
    // do not render for an auditor.
    id: 'sme',
    label: 'SME Estimator',
    access: { anyRole: ['hod', 'auditor'] },
    children: [
      { key: '/sme', label: 'Estimator', icon: <ExperimentOutlined />, access: { anyRole: ['hod', 'auditor'] } },
    ],
  },
  {
    id: 'mh',
    label: 'Man-Hours',
    access: { anyRole: ['hod'] },   // Man-Hours exact-locked {hod, admin}
    children: [
      { key: '/manhours', label: 'Manpower Tracking', icon: <FieldTimeOutlined />, access: { anyRole: ['hod'] } },
    ],
  },
  {
    id: 'reports',
    label: 'Reports',
    access: { minLevel: 2 },   // {hod, logistics, auditor, admin} — oversight (qc_hod) never satisfies a rank check
    children: [
      { key: '/reports', label: 'Reports', icon: <BarChartOutlined />, access: { minLevel: 2 } },
    ],
  },
  {
    id: 'logistics',
    label: 'Logistics',
    access: { minLevel: 3 },   // {logistics, admin} (hod level 2 < 3)
    children: [
      { key: '/logistics', label: 'Procurement', icon: <CarOutlined />, access: w({ minLevel: 3 }) },
      // Same PAGE as /hod/lining-coverage — a distinct route key so the two
      // nav groups never share an antd menu key (admin "All areas" shows
      // both). Narrowed from `minLevel: 3` to Logistics alone: an auditor is
      // also level 3 and was being offered this entry AND the identical
      // /hod/lining-coverage one, while its data endpoint
      // (`/analytics/lining`, roles hod+logistics) 403s them. A second menu
      // entry for one screen that then errors is worse than no entry.
      { key: '/logistics/lining-coverage', label: 'Lining Coverage', icon: <ExperimentOutlined />, access: { anyRole: [LOG] } },
    ],
  },
  {
    id: 'supervisor',
    label: 'Supervisor',
    access: w({ anyRole: ['supervisor'] }),
    children: [
      { key: '/supervisor', label: 'Material Requests', icon: <SolutionOutlined />, access: w({ anyRole: ['supervisor'] }) },
    ],
  },
  {
    // Phase 5. One page, three roles — each sees the same entries and may
    // change a different part of them, so the ACCESS is shared and the
    // controls live in the API (a supervisor's payload has no material field).
    id: 'execution',
    label: 'Execution',
    access: w({ anyRole: ['store_keeper', 'supervisor', 'hod'] }),
    children: [
      { key: '/execution', label: 'Execution Entries', icon: <SolutionOutlined />,
        access: w({ anyRole: ['store_keeper', 'supervisor', 'hod'] }) },
    ],
  },
  {
    // Phase 20a — the Surface Shield daily log: READ-ONLY, and wider than
    // /execution on purpose (ruling Q20-1): management (Logistics, Auditor,
    // Admin) reads it beside the three field roles. The endpoint is
    // `require_roles(store_keeper, supervisor, hod, logistics, auditor)`, the
    // same set; no write lives behind it.
    id: 'surface-shield',
    label: 'Surface Shield',
    access: { anyRole: ['store_keeper', 'supervisor', 'hod', 'logistics', 'auditor'] },
    children: [
      { key: '/surface-shield/log', label: 'Daily Log', icon: <SolutionOutlined />,
        access: { anyRole: ['store_keeper', 'supervisor', 'hod', 'logistics', 'auditor'] } },
    ],
  },
  {
    // GRANTED TO LOGISTICS 2026-08-12 (operator ruling). `/warehouse/*` has
    // always been `require_roles("warehouse_user", "logistics")` on the
    // server, so Logistics could already receive goods and cut delivery notes
    // by API while the sidebar told them the area was not theirs. Covering an
    // unstaffed shed is a real operational need; the honest fix was to make
    // the menu admit what the system already does, rather than narrow an API
    // that people depend on.
    id: 'warehouse',
    label: 'Warehouse',
    access: w({ anyRole: [WH, LOG] }),
    children: [
      { key: '/warehouse', label: 'Receiving & DN', icon: <InboxOutlined />, access: w({ anyRole: [WH, LOG] }), badge: 'warehouse' },
    ],
  },
  {
    // QSEP — Quality Control. The GROUP is open to everyone who has a stake in
    // an inspection, not only to the inspector: an HOD and a warehouse user
    // manage the accounts, and a Store Keeper needs to see WHY the issue form
    // just refused them. Only the two write pages carry `writes`.
    id: 'quality',
    label: 'Quality',
    access: { anyRole: ['qc', 'qc_hod', 'hod', 'logistics', 'warehouse_user', 'store_keeper', 'auditor'] },
    children: [
      // Not marked `writes`, deliberately: reading the inspection queue is a
      // read, and the Approve/Reject controls inside are gated separately
      // (require_roles("qc") server-side, useReadOnly in the page). Marking it
      // would hide the queue from the Auditor, who should be able to audit it.
      {
        key: '/qc/inspections', label: 'Inspections', icon: <ExperimentOutlined />,
        access: { anyRole: ['qc', 'hod', 'logistics', 'warehouse_user', 'store_keeper', 'auditor'] },
      },
      {
        key: '/qc/accounts', label: 'QC Accounts', icon: <TeamOutlined />,
        access: w({ anyRole: ['hod', 'logistics', 'warehouse_user'] }),
      },
      {
        // The Head of Qualities' whole portal (Phase 8 slice 8d). NOT marked
        // `writes`: the page is a set of read tabs, and its one mutating
        // control — raising an escalation — is what the role EXISTS to do.
        // Marking it would hide the page from the very account it is for.
        key: '/qc-hod', label: 'Quality Oversight',
        icon: <SafetyCertificateOutlined />,
        access: { anyRole: ['qc_hod'] },
      },
    ],
  },
  {
    // QSEP slices 4-5. "Safety & People" rather than two groups: the PPE
    // forecast and the employee roster are read by the same person doing the
    // same job (who has what, and who is where), and splitting them buries
    // one of them behind an extra click.
    //
    // Note the absence of an "Issue PPE" entry. PPE goes out through the
    // ordinary Issue form (Option A) — a link here would imply a second path
    // and there deliberately is not one.
    id: 'safety',
    label: 'Safety & People',
    access: { anyRole: [SK, SUP, HOD, LOG, AUD] },
    children: [
      {
        // A re-ordering worksheet that lists, BY NAME, who holds which piece
        // of safety gear. It was `minLevel: 0`, i.e. everybody. Narrowed to
        // the store that issues the gear, the HOD who owns the site, and
        // Logistics — who do the actual ordering and are the one role outside
        // the store with a use for it.
        key: '/ppe/forecast', label: 'PPE Forecast', icon: <FieldTimeOutlined />,
        access: { anyRole: [SK, HOD, LOG] },
      },
      {
        key: '/ppe/rules', label: 'PPE Usable Time', icon: <SafetyOutlined />,
        access: w({ anyRole: [SK, HOD] }),
      },
      {
        // ⚠️ THE BIGGEST PII NARROWING IN THIS PASS. The full staff roster —
        // names, phone numbers, departments — was `minLevel: 1`, which handed
        // it to warehouse users, QC inspectors and Logistics, none of whom
        // manage, move or equip people. Revoked from all three (operator
        // ruling, 2026-08-12).
        //
        // GRANTED to the Store Keeper in the same breath, and the inversion is
        // the point: since QSEP, PPE cannot be issued without a valid
        // employee ID, so the SK is the person typing one — and was the only
        // role denied the list to type it from. The read is site-scoped
        // server-side (`resolve_site_param`).
        //
        // NOT marked `writes`: an auditor should be able to read the roster
        // and somebody's PPE history. The Transfer button inside is gated on
        // the HOD role and on useReadOnly() separately.
        key: '/hr/employees', label: 'Employees', icon: <TeamOutlined />,
        access: { anyRole: [SK, SUP, HOD, AUD] },
      },
    ],
  },
  {
    id: 'master',
    label: 'Master Data',
    access: w({ minLevel: 3 }),
    // Per-entity, defaulting to the group's own rule. `employees` overrides it
    // to admin-only — see the note in entities.ts.
    children: WRITE_ENTITIES.map((e) => ({
      key: `/master/${e.key}`,
      label: e.label,
      access: w(e.access ?? { minLevel: 3 }) as AccessRule,
    })),
  },
  {
    id: 'admin',
    label: 'Admin',
    access: { minLevel: 4 },
    children: [
      { key: '/admin/users', label: 'Users', icon: <TeamOutlined />, access: w({ minLevel: 4 }) },
      { key: '/admin/pending', label: 'Access Requests', icon: <UserAddOutlined />, access: w({ minLevel: 4 }) },
      { key: '/admin/overdue', label: 'Overdue Actions', icon: <AlertOutlined />, access: { minLevel: 4 }, redBadge: true },
      { key: '/admin/inventory', label: 'Inventory', icon: <DatabaseOutlined />, access: { minLevel: 4 } },
      { key: '/admin/audit', label: 'Audit Log', icon: <FileSearchOutlined />, access: { minLevel: 4 } },
      { key: '/admin/console', label: 'Console', icon: <ControlOutlined />, access: w({ minLevel: 4 }) },
      // ⚠️ NAMED ROLES, NOT A LEVEL (rule 14). The Console above is minLevel 4,
      // so an Auditor — level 3, and the role whose whole job is reading what
      // happened — could never open a tab inside it. This route mounts the same
      // panel for both. It is read-only for the auditor by construction, not by
      // remembering: rule 7's method-level middleware refuses every mutating
      // verb from that role whatever a page offers.
      { key: '/admin/ai-traces', label: 'AI Traces', icon: <FileSearchOutlined />, access: { anyRole: ['admin', AUD] } },
    ],
  },
  {
    id: 'documents',
    label: 'Documents',
    children: [
      { key: '/documents', label: 'Documents', icon: <FileProtectOutlined />, access: { minLevel: 0 } },
    ],
  },
  {
    id: 'account',
    label: 'Account',
    children: [
      { key: '/security', label: 'Security', icon: <SafetyCertificateOutlined />, access: { minLevel: 0 } },
      // Everybody has training, including the roles that are not mandated for
      // any module — they see the list and their own (empty) standing rather
      // than a page that 404s for half the company.
      { key: '/training', label: 'Training', icon: <PlayCircleOutlined />, access: { minLevel: 0 } },
      { key: '/feedback', label: 'Feedback', icon: <MessageOutlined />, access: { minLevel: 0 } },
    ],
  },
]

// Groups an admin sees by DEFAULT (lean console). Operational groups are hidden
// until the admin flips "All areas" — legacy admin-shadow behavior.
export const ADMIN_DEFAULT_GROUPS = new Set([
  'overview', 'records', 'reports', 'master', 'admin', 'documents', 'account',
])

// The group each role works in most — opened by default in the sidebar
// (progressive disclosure: your workspace first, everything else collapsed).
export const PRIMARY_GROUP: Record<string, string> = {
  auditor: 'records',
  store_keeper: 'entry',
  warehouse_user: 'warehouse',
  supervisor: 'supervisor',
  hod: 'hod',
  logistics: 'logistics',
  qc: 'quality',
  qc_hod: 'quality',
  admin: 'admin',
}

// Where each role lands when it hits a page it cannot see (and the "/" index).
export const ROLE_HOME: Record<string, string> = {
  // An auditor lands on the Dashboard: it is the broadest read-only view, and
  // every write-purpose portal the other roles land on is closed to them.
  auditor: '/',
  store_keeper: '/entry/issue',
  warehouse_user: '/warehouse',
  supervisor: '/supervisor',
  hod: '/hod/approvals',
  logistics: '/logistics',
  // A quality inspector's whole job is the queue, so that is the landing page.
  qc: '/qc/inspections',
  // The Head of Qualities lands on their own dashboard — the ordinary
  // Dashboard is site-shaped and shows them nothing they are responsible for.
  qc_hod: '/qc-hod',
  admin: '/admin/console',
}

export function roleHome(user: User | null): string {
  if (!user) return '/'
  return ROLE_HOME[user.role] ?? '/'
}

// ⚠️ OVERSIGHT ROLES ARE NOT ON THE SENIORITY LADDER — the frontend twin of
// `auth.QC_OVERSIGHT_ROLES` (service_tests suite 17Q pins the two equal).
// `qc_hod` carries level 2 so that it inherits nothing, and the backend's
// `require_level()` refuses it outright. This guard did not, so `minLevel: 2`
// put Reports and five Records ledgers in the Head of Qualities' menu, each
// opening onto a page whose every call 403s (Phase 17a). `minLevel: 0` still
// admits them: in this file it means "every signed-in user" (Documents,
// Security, Training, Feedback, Records → Inventory), which the backend serves
// to anyone authenticated.
// An array literal first so `scripts/nav_access_dump.mjs` (the snapshot's
// model of this guard) can read the same list rather than restating it.
const OVERSIGHT_ROLE_LIST = ['qc_hod']
export const OVERSIGHT_ROLES: ReadonlySet<string> = new Set(OVERSIGHT_ROLE_LIST)

// Route-guard permission: may this user OPEN this page? Admin → always (shadow).
export function canAccess(user: User | null, rule: AccessRule): boolean {
  if (!user) return false
  // Checked BEFORE the admin shadow: a view-only account is never an admin,
  // but putting the test first makes the precedence explicit — `writes` is a
  // capability gate, not another rung on the role ladder, so no amount of
  // seniority opens a page whose only purpose is changing data.
  if (rule.writes && isReadOnly(user)) return false
  if (user.role === 'admin') return true
  if ('anyRole' in rule) return rule.anyRole.includes(user.role)
  // A rank check above 0 is never satisfied by an oversight role: its grants
  // are enumerated with `anyRole`, exactly as the API uses `require_roles`.
  if (OVERSIGHT_ROLES.has(user.role) && rule.minLevel > 0) return false
  return (user.level ?? 0) >= rule.minLevel
}

// Which sidebar group a path belongs to (for keeping the active group open).
export function groupOfPath(pathname: string): string | undefined {
  const path = pathname.replace(/\/+$/, '') || '/'
  if (path.startsWith('/records/')) return 'records'
  if (path.startsWith('/master/')) return 'master'
  for (const g of NAV) {
    if (g.children.some((n) => n.key === path)) return g.id
  }
  return undefined
}

// Flat list of every page this user can OPEN (admin shadow included, ignoring
// the curated-default filter) — powers the ⌘K command palette.
export interface FlatNav { key: string; label: string; group: string }
export function accessibleNodes(user: User | null): FlatNav[] {
  const out: FlatNav[] = []
  for (const g of NAV) {
    if (g.access && !canAccess(user, g.access)) continue
    for (const n of g.children) {
      if (canAccess(user, n.access)) out.push({ key: n.key, label: n.label, group: g.label ?? '' })
    }
  }
  return out
}

// Paths that are deliberately open to every signed-in user and carry no menu
// entry of their own. EXHAUSTIVE and exported, because `canAccessPath` below
// now refuses everything it does not recognise — this list is the entire set
// of exceptions, and `npm run test:nav` asserts that App.tsx declares nothing
// outside it that the manifest has not claimed.
export const PUBLIC_PATH_PREFIXES = [
  // The QR-scan Material Intelligence page. Store keepers (level 0) are the
  // ones holding the scanner, and its endpoint is get_current_user +
  // site_scope — not the level-1 gate the /stock LIST carries. Inheriting
  // /stock's rule would bounce an SK off their own scan.
  '/stock/material/',
]

// Resolve the access rule for an arbitrary pathname (handles dynamic
// /records/:key and /master/:key).
//
// ⚠️ THIS FUNCTION FAILS CLOSED, and both halves of that are deliberate.
//
// It used to `return true` for any path it did not recognise. Nothing
// exploited it — every route declared in App.tsx happened to have a manifest
// entry — but the failure mode was "let them in": the next <Route> added
// without a NAV node would have been reachable by every signed-in user in the
// company, silently, with nothing to notice it. A guard whose default is
// ALLOW is not a guard, it is a lookup table with optimistic edges.
//
// It also used to check only the NODE's rule while `buildMenu` checked the
// GROUP's rule as well, so the sidebar and the route guard enforced two
// different policies and a group-level gate could be walked around by typing
// the URL. Both are checked here now, group first, in the same order the menu
// checks them.
export function canAccessPath(user: User | null, pathname: string): boolean {
  if (!user) return false
  const path = pathname.replace(/\/+$/, '') || '/'
  if (path.startsWith('/records/')) {
    const key = path.slice('/records/'.length)
    const ent = READ_ENTITIES.find((e: ReadEntity) => e.key === key)
    // An UNKNOWN entity key is refused rather than handed the generic hod+
    // rule. `/records/anything-at-all` used to resolve to `minLevel: 2`.
    return ent ? canAccess(user, ent.access) : false
  }
  if (path.startsWith('/master/')) {
    const key = path.slice('/master/'.length)
    const ent = WRITE_ENTITIES.find((e) => e.key === key)
    // Same fail-closed treatment as `/records/`: an unknown entity key is
    // refused rather than handed the group's generic rule.
    return ent ? canAccess(user, w(ent.access ?? { minLevel: 3 })) : false
  }
  if (PUBLIC_PATH_PREFIXES.some((p) => path.startsWith(p))) return true
  for (const g of NAV) {
    const node = g.children.find((n) => n.key === path)
    if (!node) continue
    if (g.access && !canAccess(user, g.access)) return false
    return canAccess(user, node.access)
  }
  return false // unlisted → refused, and `test:nav` stops that being a surprise
}
