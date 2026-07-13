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
  admin: [
    { path: '/', marker: 'Dashboard' },
    { path: '/stock', marker: 'Stock (derived)' },
    { path: '/records/receipts', marker: 'Receipts' },
    { path: '/records/consumption', marker: 'Consumption' },
    { path: '/admin/users', marker: 'New user' },
    { path: '/admin/audit' },
    { path: '/admin/console' },
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
    // the P0 role manifest does NOT give store keepers the Stock page — the
    // app lands them on their Issue home instead; assert that lock holds
    { path: '/stock', marker: 'Issue Stock (Consumption)' },
    { path: '/entry/count' },
  ],
  supervisor: [
    { path: '/supervisor', marker: 'Material Requests' },
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
