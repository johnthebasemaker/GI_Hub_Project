/**
 * Per-role page smoke — every core route renders real content with zero
 * uncaught page errors (the headless version of automatic_test.md §2–§10).
 * Known headings are asserted exactly; the rest assert "alive": app chrome
 * present, main region non-trivial, no pageerror events.
 */
import { test, expect, Page } from '@playwright/test'
import { Role, storageStatePath } from '../harness/env'

type Check = { path: string; marker?: string | RegExp }
const ROUTES: Record<Role, Check[]> = {
  // Exists only for announcements.spec.ts (a What's-new audience of one).
  news: [],
  admin: [
    { path: '/', marker: 'Dashboard' },
    { path: '/stock', marker: 'Stock (derived)' },
    { path: '/records/receipts', marker: 'Receipts' },
    { path: '/records/consumption', marker: 'Consumption' },
    { path: '/admin/users', marker: 'New user' },
    { path: '/admin/audit' },
    { path: '/admin/console' },
    // Slice 11c. The panel is mounted twice — here as its own route (the one an
    // AUDITOR can reach, since the Console is minLevel 4) and as a Console tab.
    { path: '/admin/ai-traces', marker: /AI Traces/i },
    { path: '/reports' },
  ],
  hod: [
    { path: '/hod/approvals' },
    { path: '/hod/executive-summary', marker: /download pdf/i },
    { path: '/hod/burn-rate' },
    { path: '/hod/low-stock' },
    { path: '/hod/prs' },
  ],
  sk: [
    { path: '/entry/issue', marker: 'Issue Stock (Consumption)' },
    { path: '/entry/receive' },
    { path: '/entry/return' },
    { path: '/sk/requests' },
    // ⚠️ INVERTED 2026-08-12. The P0 role manifest used to bounce a store
    // keeper off /stock to their Issue home, and this line asserted that
    // lock. It was never a decision — `minLevel: 1` on the Dashboard and the
    // Stock page made the person who physically holds the stock the ONE role
    // that could not open the screen named after it, while `/stock/*` on the
    // server (`get_current_user`) had always allowed them. The manifest now
    // names roles instead of a level, and the SK lands on Stock.
    { path: '/stock', marker: 'Stock (derived)' },
    { path: '/entry/count' },
    // ⚠️ ADDED 2026-09-02. The two OCR pages were NOT in this sweep, and the
    // Phase 11 work edited both — so a broken import or a hook crash in either
    // upload card would have shipped past a 125/125 green run. A render sweep
    // that omits the pages people are actively changing is the coverage most
    // worth having and the easiest to forget.
    { path: '/entry/ocr', marker: /OCR Import/i },
  ],
  supervisor: [
    { path: '/supervisor', marker: 'Material Requests' },
    { path: '/execution', marker: /execution/i },
  ],
  logistics: [
    { path: '/logistics' },
    { path: '/warehouse' },
    { path: '/records/purchase_orders' },
  ],
}

async function expectAlive(page: Page, errors: Error[]) {
  // app chrome rendered (sidebar brand) and the page body is not blank
  await expect(page.getByText('GI Hub').first()).toBeVisible()
  await expect
    .poll(async () => (await page.locator('#root').innerText()).length, {
      message: 'page body should not be blank',
    })
    .toBeGreaterThan(80)
  expect(errors, `uncaught page errors: ${errors.map((e) => e.message).join(' | ')}`).toEqual([])
}

for (const [role, checks] of Object.entries(ROUTES) as [Role, Check[]][]) {
  test.describe(`smoke:${role}`, () => {
    test.use({ storageState: storageStatePath(role) })
    for (const { path, marker } of checks) {
      test(`${path} renders`, async ({ page }) => {
        const errors: Error[] = []
        page.on('pageerror', (e) => errors.push(e))
        await page.goto(path)
        if (marker) {
          await expect(
            typeof marker === 'string'
              ? page.getByText(marker, { exact: false }).first()
              : page.getByText(marker).first(),
          ).toBeVisible()
        }
        await expectAlive(page, errors)
      })
    }
  })
}
