/**
 * Phase 18 Track 4 — Reorder signals (intelligent minimum stock), in a browser.
 *
 * Suite 18M pins the method with known answers; this proves the screens the
 * Logistics team reads: the Stock page's Reorder signals tab (deep-linked as
 * /stock?tab=reorder) lists items with a red / amber / green status, says WHY
 * for each, and filters by colour; the HOD's Dashboard — where "Stock vs
 * Minimum" used to say "No minimums set" — summarises them and links there.
 */
import { test, expect } from '@playwright/test'
import { storageStatePath } from '../harness/env'

test('18m: Logistics reads the reorder signals — a status, a minimum and a reason per item', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('logistics') })
  const page = await ctx.newPage()
  await page.goto('/stock?tab=reorder')
  const panel = page.getByTestId('reorder-signals')
  await expect(panel).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('tab', { name: 'Reorder signals', selected: true })).toBeVisible()
  const rows = panel.locator('.ant-table-row')
  await expect(rows.first()).toBeVisible({ timeout: 20_000 })
  // every row carries one of the four statuses and a basis tag + reason
  await expect(rows.first().locator('[data-testid^="rag-"]')).toHaveCount(1)
  await expect(rows.first()).toContainText(/Past use|SQM plan|Garnet|No recent use|Not in plan|No pack size/)
  await page.screenshot({ path: test.info().outputPath('reorder-signals.png'), fullPage: false })
  await panel.locator('.ant-table').screenshot({ path: test.info().outputPath('reorder-table.png') })

  // filter to "No signal" and back — every visible row then says so
  await page.getByTestId('rag-filter').getByText(/No signal/).click()
  const shown = await rows.count()
  if (shown > 0) {
    await expect(panel.locator('[data-testid="rag-none"]')).toHaveCount(shown)
  }
  await page.getByTestId('rag-filter').getByText('All', { exact: true }).click()
  await expect(rows.first()).toBeVisible()
  await ctx.close()
})

test('18m: the Dashboard summarises the reorder signals and links to them', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('hod') })
  const page = await ctx.newPage()
  await page.goto('/')
  const mini = page.getByTestId('reorder-mini')
  await expect(mini).toContainText('order now', { timeout: 20_000 })
  await mini.scrollIntoViewIfNeeded()
  await page.screenshot({ path: test.info().outputPath('dashboard-reorder.png') })
  await mini.getByRole('link', { name: 'Open Reorder signals →' }).click()
  await expect(page).toHaveURL(/\/stock\?tab=reorder/)
  await expect(page.getByTestId('reorder-signals')).toBeVisible({ timeout: 20_000 })
  await ctx.close()
})

test('18m: the HOD plans the site\'s SQM pace (ruling Q6 B) — set, seen in the note, cleared', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('hod') })
  const page = await ctx.newPage()
  await page.goto('/stock?tab=reorder')
  const note = page.getByTestId('pace-note-CNCEC')
  await expect(note).toBeVisible({ timeout: 20_000 })
  await page.screenshot({ path: test.info().outputPath('reorder-pace-before.png') })

  await page.getByTestId('pace-set-CNCEC').click()
  const dlg = page.locator('.ant-modal', { hasText: 'Planned SQM pace — CNCEC' })
  // a suggestion from the site's own approved work — or the plain "nothing to suggest"
  await expect(dlg.getByTestId('pace-suggestion')).toContainText(/Suggested: .* m²\/day|nothing to suggest/)
  await dlg.getByRole('spinbutton').fill('1000')
  await page.screenshot({ path: test.info().outputPath('reorder-pace-dialog.png') })
  await dlg.getByTestId('pace-save').click()
  await expect(page.getByText(/CNCEC: planned pace 1,000/)).toBeVisible()
  await expect(note).toContainText("this site's planned rate")
  await expect(note).toContainText('1,000 m²/day')

  // clear it again — back to the global rate / approved work
  await page.getByTestId('pace-set-CNCEC').click()
  await page.locator('.ant-modal', { hasText: 'Planned SQM pace — CNCEC' })
    .getByRole('button', { name: 'Clear' }).click()
  await expect(page.getByText('CNCEC: planned pace cleared')).toBeVisible()
  await expect(note).not.toContainText("this site's planned rate")
  await ctx.close()
})
