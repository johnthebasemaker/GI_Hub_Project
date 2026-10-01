/**
 * SME strict tier segregation, in the browser.
 *
 * The locked rule: "Feasibility judges the physical tier only. A tank cannot
 * be built with a purchase order."
 *
 * The bug this pins, reported 2026-08-03: PHENACIN ACP POWDER held 0 available
 * and 56,350 on order, and the SME portal showed the systems that depend on it
 * as ready to build. The ENGINE was right all along (SQM_Achievable_Now = 0,
 * status 🔴 Blocked); every presentation layer above it re-derived coverage as
 * `Allocated_Qty / Demand_Qty` — and `Allocated_Qty` is physical stock PLUS
 * stock on order. Measured on the live snapshot: 18 of 85 (tag, code) units
 * showed a green 100% "Fully Ready" pill they had not earned, and buildable
 * area was overstated by 9,118 m² (21.5% of the remaining programme).
 *
 * The fixture (seeded in global-setup.ts) reproduces that shape exactly:
 * E2E-TIER-TANK, 100 m² remaining on system code 9101, blocked by
 * E2ETIER-ACP — 0 arrived of 5,000 procured, so the whole PO is still on the
 * water. Ready-now must read 0.0% and when-delivered 100.0%, as two separate
 * figures. (2026-08-05 SUBSET RULE: the 5,000 is the TOTAL procured, and the
 * pending delivery is 5,000 − 0; it is not 5,000 on top of the stock.)
 *
 * Suite BB in service_tests.py pins the same case at the engine, export and
 * executive-summary layers; this spec is the half only a browser can prove.
 *
 * NOTE: the master grid is antd `virtual`, so off-screen rows are not in the
 * DOM. These assertions deliberately target the per-code expander and the KPI
 * strip, which always render, rather than hunting a virtualized row.
 */
import { test, expect } from '@playwright/test'
import type { Locator, Page } from '@playwright/test'
import { storageStatePath } from '../harness/env'

const CODE = '9101'

// Total Overview is the most expensive page in the app: it pulls the whole
// model snapshot and then runs the FULL cascade over ~78 units on the
// browser's main thread. Loading it four times in parallel made these tests
// contend with each other (and with every other spec) badly enough to blow a
// 30s wait. SERIAL + one shared page: loaded once, asserted four times.
test.describe.configure({ mode: 'serial' })

test.describe('sme-tiers:hod', () => {
  test.use({ storageState: storageStatePath('hod') })

  let page: Page
  let panel: Locator

  test.beforeAll(async ({ browser }) => {
    // ⚠️ INSIDE the hook, and that placement is the whole point. A
    // `test.setTimeout()` at describe scope sets the budget for the TESTS and
    // leaves hooks on the config default — so the previous attempt to raise
    // this read as though it had worked and the hook still died at 60s. Called
    // here, it raises THIS hook's own budget, which is the thing that was
    // expiring.
    //
    // What it is buying time for is CONTENTION, not a slow page: in isolation
    // this renders in under 8s. The SME cascade is a CPU-bound computation
    // inside a single-process test API, so while it runs it starves the event
    // loop and every other spec's request queues behind it — and it queues
    // behind theirs. A cold Vite dep-optimize on the first run after a
    // frontend edit is enough to tip it. Production runs multiple workers and
    // does not have this shape.
    test.setTimeout(180_000)
    const ctx = await browser.newContext({ storageState: storageStatePath('hod') })
    page = await ctx.newPage()
    await page.goto('/sme')
    // Under the same contention a click can land while the page is still
    // mounting its tabs and be lost — the 2026-09-30/10-01 failures show the
    // Dashboard tab still selected after the click. Click until the tab IS
    // selected, then wait for its content.
    const tab = page.getByRole('tab', { name: /Total Overview/ })
    await expect(async () => {
      await tab.click()
      await expect(tab).toHaveAttribute('aria-selected', 'true', { timeout: 2_000 })
    }).toPass({ timeout: 60_000 })
    // antd keeps every visited tab mounted, so page-wide text locators would
    // also hit the Dashboard's copy — scope everything to THIS panel.
    panel = page.getByRole('tabpanel', { name: /Total Overview/ })
    await expect(panel.getByText('MASTER TABLE — EQUIPMENT × SYSTEM CODE'))
      .toBeVisible({ timeout: 150_000 })
  })

  test.afterAll(async () => { await page?.context().close() })

  test('the grid gives each tier its own column', async () => {
    // The legend that makes the distinction readable at all.
    await expect(panel.getByText('Arrived stock and pending deliveries are counted separately'))
      .toBeVisible()

    // One merged "Allocated" + "Fulfil %" pair was the shape that hid the bug.
    // Header cells also contain a filter button, so match on their TEXT.
    const head = panel.locator('.ant-table-thead').first()
    const titles = (await head.locator('th').allInnerTexts()).map((t) => t.trim())
    for (const name of ['Available', 'Pending Delivery', 'Short (physical)',
      'To buy (net)', 'Ready now %', 'When delivered %']) {
      expect(titles, `the grid must have a "${name}" column — got ${titles.join(' · ')}`)
        .toContain(name)
    }
    // …and must NOT still carry the merged one.
    expect(titles, 'the merged "Allocated" column must be gone').not.toContain('Allocated')
  })

  test('the KPI strip reports buildable-now apart from when-delivered',
    async () => {
      await expect(panel.getByText('Buildable now SQM').first()).toBeVisible()
      await expect(panel.getByText('When delivered SQM').first()).toBeVisible()
      await expect(panel.getByText('Avg Coverage (now)')).toBeVisible()
      // The old single "Available Coverage SQM" KPI showed the with-ordered
      // number under a physical-sounding name.
      await expect(page.getByText('Available Coverage SQM')).toHaveCount(0)
    })

  test('a unit covered only by a purchase order reads 0% ready, 100% when delivered',
    async () => {
      const header = panel.locator('.ant-collapse-header')
        .filter({ hasText: `Code ${CODE}` })
      await expect(header).toBeVisible()

      // THE assertion. The pill is the physical figure; the amber hint is the
      // forecast. Before the fix the pill itself read 100.0%.
      await expect(header, 'the readiness pill must show the PHYSICAL 0.0%')
        .toContainText('0.0%')
      await expect(header, 'the forecast must be labelled, not merged into the pill')
        .toContainText('100.0% when delivered')
    })

  test('the per-code material table separates arrived stock from pending delivery',
    async () => {
      const header = panel.locator('.ant-collapse-header')
        .filter({ hasText: `Code ${CODE}` })
      await header.click()

      // `expanded` shadows nothing: the shared `panel` is the tab, this is
      // the open collapse item inside it.
      const expanded = panel.locator('.ant-collapse-item-active')
      await expect(expanded.getByRole('columnheader', { name: 'Stock available' })).toBeVisible()
      await expect(expanded.getByRole('columnheader', { name: 'Pending delivery' })).toBeVisible()

      // The blocked component still REPORTS its 5,000 pending — the quantity is
      // never hidden, it just never counts as readiness. Under the 2026-08-05
      // subset rule that 5,000 is the UNRECEIVED part of a 5,000 order against
      // 0 arrived, so the whole PO is still on the water.
      const acp = expanded.locator('.ant-table-row').filter({ hasText: 'E2ETIER-ACP' })
      await expect(acp).toBeVisible()
      const cells = (await acp.locator('td').allInnerTexts()).join(' | ')
      expect(cells, `ACP row read: ${cells}`).toContain('5,000')
      expect(cells, 'ready-now must be 0.0% with nothing on the shelf').toContain('0.0%')
      expect(cells, 'with-ordered must be 100.0%').toContain('100.0%')

      // The fully-stocked sibling is the control: it IS ready today.
      const ok = expanded.locator('.ant-table-row').filter({ hasText: 'E2ETIER-OK' })
      await expect(ok).toBeVisible()
      const okCells = (await ok.locator('td').allInnerTexts()).join(' | ')
      expect(okCells, `OK row read: ${okCells}`).toContain('100.0%')
    })
})
