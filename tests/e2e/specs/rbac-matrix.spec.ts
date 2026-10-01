/**
 * THE RBAC MATRIX — every role against every page, asserted as data.
 *
 * This is the regression net for the 2026-08-12 strict-RBAC pass. The problem
 * it solves is not that any single rule was wrong; it is that the rules were
 * spread across a manifest, an entity list, a menu builder and a route guard,
 * and **nothing anywhere stated the intended answer**. So the answers drifted
 * — a store keeper locked out of the Stock page, seven roles holding the staff
 * roster with phone numbers, an inspector with the rack locator — and every
 * one of those drifts looked individually plausible in the diff that caused
 * it. A matrix that is only implied cannot be reviewed. This one can.
 *
 * ⚠️ IT DRIVES THE REAL FUNCTIONS. `window.__giNav` (AppLayout) hands out the
 * shipped `accessibleNodes` and `canAccessPath`, not a copy of the rules. A
 * matrix test that re-implemented `canAccess` would agree with itself forever
 * while the guard did something else — which is the exact class of failure
 * this pass exists to end.
 *
 * TWO THINGS ARE CHECKED PER CELL, and they are different questions:
 *   · `pages`  — is it in the sidebar / ⌘K palette? (group rule AND node rule)
 *   · `can()`  — can it be reached by TYPING THE URL? (the route guard)
 * They used to disagree: `buildMenu` checked the group, `canAccessPath` did
 * not, so a group-level gate could be walked around with the address bar.
 * Asserting both is what stops that returning.
 *
 * TO CHANGE THE MATRIX: edit MATRIX below **and** config/nav.tsx, in one
 * commit. If you find yourself editing only this file to make a test pass, the
 * access rule changed without anyone deciding to change it — which is the
 * thing being prevented.
 */
import { test, expect, type Page } from '@playwright/test'
import { storageStatePath, type Role } from '../harness/env'

type Matrix = Record<string, Role[]>

// Roles are named by their HARNESS key (see harness/env.ts USERS).
const ALL: Role[] = ['sk', 'warehouse', 'supervisor', 'qc', 'hod', 'logistics', 'auditor']

/**
 * Page → exactly the roles that may open it. Admin is excluded from every row
 * and asserted separately: it holds shadow access to everything, so listing it
 * on 44 rows would say nothing and hide the one property worth stating.
 *
 * Every entry here was ruled on by the operator on 2026-08-12 (see
 * PROPOSED_NAV_FIX.md §4.2/§4.3 for the reason behind each grant and each
 * revocation).
 */
const MATRIX: Matrix = {
  // ── overview ──────────────────────────────────────────────────────────────
  // QC is absent from the Dashboard by ruling: an inspector's job is a queue.
  '/': ['sk', 'warehouse', 'supervisor', 'hod', 'logistics', 'auditor'],
  // …but present on Stock: it is how they see what is waiting for them.
  '/stock': ALL,
  // Phase 16 (ruling Q16-16) — the roles that hold, inspect or answer for
  // controlled material. Read-only.
  '/lots': ['sk', 'qc', 'hod'],
  // Only the roles that physically walk to a shelf, plus the racking owner.
  '/locator': ['sk', 'warehouse', 'logistics'],
  // Everyone who might sign out a tool. A QC inspects material, not hammers.
  '/assets': ['sk', 'warehouse', 'supervisor', 'hod', 'logistics', 'auditor'],

  // ── data entry — exact-locked to the store keeper ─────────────────────────
  '/entry/receive': ['sk'],
  '/entry/issue': ['sk'],
  '/entry/return': ['sk'],
  '/entry/adjust': ['sk'],
  '/entry/count': ['sk'],
  '/entry/returnables': ['sk'],
  '/entry/ocr': ['sk'],
  '/site/incoming': ['sk'],
  '/sk/requests': ['sk'],

  // ── records ───────────────────────────────────────────────────────────────
  '/records/inventory': ALL,
  '/records/receipts': ['hod', 'logistics', 'auditor'],
  '/records/consumption': ['hod', 'logistics', 'auditor'],
  '/records/returns': ['hod', 'logistics', 'auditor'],
  '/records/lots': ['hod', 'logistics', 'auditor'],
  '/records/purchase-requests': ['hod', 'logistics', 'auditor'],
  // GRANTED to warehouse 2026-08-12: they receive goods AGAINST a PO and could
  // not look one up, so they were phoning Logistics to have quantities read out.
  '/records/purchase-orders': ['warehouse', 'logistics', 'auditor'],
  // GRANTED to auditor: the one read entity it had been missed from.
  '/records/equipment': ['hod', 'auditor'],

  // ── HOD ───────────────────────────────────────────────────────────────────
  '/hod/executive-summary': ['hod', 'auditor'],
  '/hod/burn-rate': ['hod', 'auditor'],
  '/hod/lining-coverage': ['hod', 'auditor'],
  '/hod/documents': ['hod', 'auditor'],
  '/hod/low-stock': ['hod', 'auditor'],
  '/hod/prs': ['hod', 'auditor'],
  '/hod/requests': ['hod', 'auditor'],
  // `writes` — a view-only role cannot open a page whose only purpose is
  // changing data, however senior it is.
  '/hod/approvals': ['hod'],
  '/bulk-import': ['hod'],

  // ── planning ──────────────────────────────────────────────────────────────
  '/sme': ['hod', 'auditor'],
  '/manhours': ['hod'],
  '/reports': ['hod', 'logistics', 'auditor'],

  // ── portals ───────────────────────────────────────────────────────────────
  '/logistics': ['logistics'],
  // REVOKED from auditor: a duplicate of /hod/lining-coverage whose data
  // endpoint 403s them anyway.
  '/logistics/lining-coverage': ['logistics'],
  '/supervisor': ['supervisor'],
  // GRANTED to logistics: /warehouse/* has always been
  // roles(warehouse_user, logistics) server-side.
  '/warehouse': ['warehouse', 'logistics'],

  // ── quality ───────────────────────────────────────────────────────────────
  // Reading the queue is open to everyone with a STAKE in an inspection —
  // including the SK, who needs to see WHY the issue form refused them.
  // Deciding is roles("qc"). The supervisor is deliberately absent: they
  // request material, they do not receive, inspect or issue it.
  '/qc/inspections': ['sk', 'warehouse', 'qc', 'hod', 'logistics', 'auditor'],
  '/qc/accounts': ['warehouse', 'hod', 'logistics'],

  // ── safety & people ───────────────────────────────────────────────────────
  // Carries names. Narrowed to the store that issues gear, the HOD who owns
  // the site, and Logistics, who place the order it exists to size.
  '/ppe/forecast': ['sk', 'hod', 'logistics'],
  '/ppe/rules': ['sk', 'hod'],
  // ⚠️ THE PII ROW. Revoked from warehouse, QC and Logistics; GRANTED to the
  // store keeper, who types an employee ID on every PPE issue and was the one
  // role denied the list to type it from.
  '/hr/employees': ['sk', 'supervisor', 'hod', 'auditor'],

  // ── master + admin ────────────────────────────────────────────────────────
  '/master/vendors': ['logistics'],
  '/master/warehouses': ['logistics'],
  // ⚠️ ADMIN ONLY — the one master-data entity that is not Logistics'.
  // Revoking the roster from Logistics for privacy while leaving them a full
  // create/update/delete editor over the same table would have made the
  // revocation cosmetic: reading every name and phone number from here is one
  // click. Empty array = nobody but admin (2026-08-12).
  '/master/employees': [],
  '/admin/users': [],
  '/admin/pending': [],
  '/admin/overdue': [],
  '/admin/inventory': [],
  '/admin/audit': [],
  '/admin/console': [],

  // ── everybody ─────────────────────────────────────────────────────────────
  '/documents': ALL,
  '/security': ALL,
  '/feedback': ALL,
}

interface NavProbe {
  role: string | null
  pages: () => string[]
  can: (path: string) => boolean
}

/**
 * `__giNav` is attached in an AppLayout effect, so it exists only once the
 * shell has mounted. Waiting on the function itself rather than on `load`
 * removes the race that would otherwise make this file flaky under a loaded
 * suite.
 */
async function probe(page: Page): Promise<{ pages: string[]; can: Record<string, boolean> }> {
  await page.goto('/')
  await page.waitForFunction(() => '__giNav' in window)
  return page.evaluate((paths) => {
    const nav = (window as unknown as { __giNav: NavProbe }).__giNav
    const can: Record<string, boolean> = {}
    for (const p of paths) can[p] = nav.can(p)
    return { pages: nav.pages(), can }
  }, Object.keys(MATRIX))
}

for (const role of ALL) {
  test.describe(`rbac:${role}`, () => {
    test.use({ storageState: storageStatePath(role) })

    test(`sees exactly its own pages, and can open exactly those`, async ({ page }) => {
      const { pages, can } = await probe(page)
      const visible = new Set(pages)

      const wrongMenu: string[] = []
      const wrongGuard: string[] = []
      for (const [path, allowed] of Object.entries(MATRIX)) {
        const should = allowed.includes(role)
        if (visible.has(path) !== should) {
          wrongMenu.push(`${should ? 'MISSING' : 'LEAKED '} ${path}`)
        }
        // The menu and the route guard must agree. A page hidden from the
        // sidebar but reachable by typing its URL is the shape that let a
        // group-level gate be walked around before 2026-08-12.
        if (can[path] !== should) {
          wrongGuard.push(`${should ? 'BLOCKED' : 'REACHABLE'} ${path}`)
        }
      }

      expect(wrongMenu, `${role}: sidebar/palette disagrees with the matrix`).toEqual([])
      expect(wrongGuard, `${role}: route guard disagrees with the matrix`).toEqual([])
    })
  })
}

/**
 * Admin is the shadow role: it may reach ANY page, while its DEFAULT sidebar
 * stays a curated console (ADMIN_DEFAULT_GROUPS) with an "All areas" switch.
 * Both halves matter — an admin that could not reach a page would be locked
 * out of its own system, and an admin whose sidebar listed all 44 would have
 * no console at all.
 */
test.describe('rbac:admin', () => {
  test.use({ storageState: storageStatePath('admin') })

  test('reaches every page, and still gets a curated sidebar', async ({ page }) => {
    const { pages, can } = await probe(page)
    const unreachable = Object.keys(MATRIX).filter((p) => !can[p])
    expect(unreachable, 'admin shadow access must open every page').toEqual([])
    // `accessibleNodes` deliberately IGNORES the curated-default filter — it
    // feeds ⌘K, where an admin should be able to jump anywhere by name. So the
    // palette listing everything is correct, and the curated console is a
    // property of the SIDEBAR, asserted against the DOM below.
    expect(pages, 'the palette must let an admin jump anywhere').toContain('/entry/issue')

    // The rendered sidebar is the curated console: operational groups stay
    // behind "All areas". An admin whose sidebar listed all 44 pages by
    // default would have no console at all.
    const sider = page.locator('.gi-sider-scroll')
    await expect(sider.getByRole('menuitem', { name: 'Admin' })).toBeVisible()
    await expect(sider.getByRole('menuitem', { name: 'Data Entry' })).toHaveCount(0)
    await expect(sider.getByRole('menuitem', { name: 'Warehouse', exact: true })).toHaveCount(0)
    await expect(page.getByLabel('Show all navigation areas')).toBeVisible()
  })
})

/**
 * The guard's DEFAULT, tested directly. It used to `return true` for anything
 * it did not recognise, so a page added without a manifest entry was open to
 * everyone. `npm run test:nav` stops such a page being added; this proves what
 * happens if one ever is.
 */
test.describe('rbac:fail-closed', () => {
  test.use({ storageState: storageStatePath('hod') })

  test('an unrecognised path is REFUSED, not allowed', async ({ page }) => {
    await page.goto('/')
    await page.waitForFunction(() => '__giNav' in window)
    const verdicts = await page.evaluate(() => {
      const nav = (window as unknown as { __giNav: NavProbe }).__giNav
      return {
        unknown: nav.can('/not-a-real-page'),
        unknownDeep: nav.can('/admin/secret/backdoor'),
        unknownEntity: nav.can('/records/does-not-exist'),
        // …while a genuinely public prefix still works.
        material: nav.can('/stock/material/1001'),
      }
    })
    expect(verdicts.unknown, 'unknown path must fail CLOSED').toBe(false)
    expect(verdicts.unknownDeep, 'an unknown path under a real prefix too').toBe(false)
    // `/records/<unknown>` used to fall back to the generic hod+ rule, so any
    // string at all resolved to a real access decision.
    expect(verdicts.unknownEntity, 'an unknown record entity must fail CLOSED').toBe(false)
    expect(verdicts.material, 'PUBLIC_PATH_PREFIXES must still be honoured').toBe(true)
  })
})
