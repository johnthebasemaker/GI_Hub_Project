/**
 * Phase 20a — the Surface Shield daily log, in a browser.
 *
 * Suite 20A pins the method (statuses, remarks, units, roles, exports) with
 * known answers. This proves the screen management reads: Logistics — who
 * cannot open /execution — opens the log, sees the days grouped with a plain
 * status on every job and the store keeper's Excel remark as typed, the Garnet
 * job in its own section, and exports it; and the Execution page links there.
 *
 * The E2E fixture's Surface Shield draws are dated 2026-09-20..22
 * (global-setup 1b-iii..v), so the log is opened on that period through its
 * URL. Their statuses depend on whether sme-jobs.spec filed them first, so
 * this asserts that EACH job has exactly one of the four statuses, not which.
 */
import { test, expect } from '@playwright/test'
import { storageStatePath } from '../harness/env'

const PERIOD = '/surface-shield/log?date_from=2026-09-19&date_to=2026-09-23'

test('20a: Logistics reads the Surface Shield daily log — days, statuses, Excel remarks, export', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('logistics') })
  const page = await ctx.newPage()
  await page.goto(PERIOD)
  const log = page.getByTestId('ss-log')
  await expect(log.getByRole('heading', { name: 'Surface Shield daily log' })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId('log-kpis')).toBeVisible()
  // the fixture days, newest first, expanded
  await expect(page.getByTestId('log-day-2026-09-22')).toBeVisible()
  await expect(page.getByTestId('log-day-2026-09-20')).toBeVisible()
  // the remark exactly as the store keeper typed it in the Excel log
  await expect(log.getByText('“Top of Brick Coving Applied - 4.82 SQM Done”')).toBeVisible()
  await expect(log.getByText('“Shell - 12.5 SQM Done”').first()).toBeVisible()
  // every job row carries exactly one plain status
  const rows = log.locator('.ant-table-row').filter({ has: page.getByTestId('log-remarks') })
  const n = await rows.count()
  expect(n).toBeGreaterThan(1)
  for (let i = 0; i < n; i++) {
    await expect(rows.nth(i).locator('[data-testid^="log-status-"]')).toHaveCount(1)
  }
  // Garnet is surface prep — its own section (Q20-6)
  await expect(log.getByText('Garnet — surface preparation').first()).toBeVisible()
  await page.screenshot({ path: test.info().outputPath('ss-log.png'), fullPage: false })

  const [dl] = await Promise.all([page.waitForEvent('download'), page.getByTestId('log-export-xlsx').click()])
  expect(dl.suggestedFilename()).toMatch(/surface_shield_log_2026-09-19_2026-09-23\.xlsx$/)
  await ctx.close()
})

test('20a: the Execution page links to the daily log', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('hod') })
  const page = await ctx.newPage()
  await page.goto('/execution')
  await page.getByTestId('link-ss-log').click()
  await expect(page).toHaveURL(/\/surface-shield\/log/)
  await expect(page.getByTestId('ss-log')).toBeVisible({ timeout: 20_000 })
  await ctx.close()
})
