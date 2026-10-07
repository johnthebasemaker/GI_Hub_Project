/**
 * Phase 21c — the Google Drive sync card in the Admin Console (rulings Q21-7..11).
 *
 * Suite 21C pins the fetch, the structural .xlsm conversion and the commands.
 * This proves the screen the admin uses: the Drive sync tab says whether Drive
 * is connected, names the setup guide when it is not, says when it runs by
 * itself, and keeps both buttons disabled until there is something to do.
 * The E2E stack has no Drive token, so the "not connected" state is the one
 * that can be asserted here.
 */
import { test, expect } from '@playwright/test'
import { storageStatePath } from '../harness/env'

test('21c: the admin sees the Drive sync card — not connected yet, the guide named, buttons disabled', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('admin') })
  const page = await ctx.newPage()
  await page.goto('/admin/console')
  // the console renders its first tab's data on arrival; a click during that
  // render can be swallowed, so click until the tab is actually selected
  const tab = page.getByRole('tab', { name: 'Drive sync' })
  await expect(async () => {
    await tab.click()
    await expect(tab).toHaveAttribute('aria-selected', 'true', { timeout: 2_000 })
  }).toPass({ timeout: 20_000 })
  const card = page.getByTestId('drive-sync')
  await expect(card).toBeVisible({ timeout: 20_000 })
  await expect(card).toContainText('not connected')
  await expect(card).toContainText('docs/GDRIVE_SETUP.md')
  // Phase 22a: the pull times (Q22-2) and the auto-commit rule (Q22-1)
  await expect(card).toContainText('at 07:30 and 19:30')
  await expect(card).toContainText('only ADDS rows')
  await expect(card.getByTestId('drive-schedule-times')).toHaveValue('07:30, 19:30')
  await expect(card.getByTestId('drive-fetch')).toBeDisabled()
  await expect(card.getByTestId('drive-commit-erp')).toBeDisabled()
  await page.screenshot({ path: test.info().outputPath('drive-sync-card.png') })
  await ctx.close()
})

test('22a: every role sees "Last updated from Drive" in the top bar; Pull only when connected, for admin / HOD', async ({ browser }) => {
  for (const role of ['sk', 'hod', 'admin'] as const) {
    const ctx = await browser.newContext({ storageState: storageStatePath(role) })
    const page = await ctx.newPage()
    await page.goto('/')
    const chip = page.getByTestId('drive-freshness')
    // the E2E stack has no Drive token: "not connected", amber, and no Pull for anyone
    await expect(chip).toHaveAttribute('data-status', 'not_connected', { timeout: 20_000 })
    await expect(chip).toContainText('not connected')
    await expect(page.getByTestId('drive-pull')).toHaveCount(0)
    await chip.hover()
    await expect(page.getByTestId('drive-freshness-tip')).toContainText('Drive is not connected')
    // the chip must not push the top bar past the window (the HOD's long name, 1280 px)
    expect(await page.locator('.gi-header').evaluate((e) => e.scrollWidth - e.clientWidth),
      `${role}: the top bar overflows`).toBeLessThanOrEqual(1)
    if (role === 'sk') await page.screenshot({ path: test.info().outputPath('drive-chip.png') })
    await ctx.close()
  }
})

test('22a: the admin sets the pull times — validated, saved, shown back, then restored', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('admin') })
  const page = await ctx.newPage()
  await page.goto('/admin/console')
  const tab = page.getByRole('tab', { name: 'Drive sync' })
  await expect(async () => {
    await tab.click()
    await expect(tab).toHaveAttribute('aria-selected', 'true', { timeout: 2_000 })
  }).toPass({ timeout: 20_000 })
  const card = page.getByTestId('drive-sync')
  const times = card.getByTestId('drive-schedule-times')
  await times.fill('7:3x')
  await card.getByTestId('drive-schedule-save').click()
  await expect(page.locator('.ant-message')).toContainText('is not a time')
  const save = async (value: string) => {
    await card.getByTestId('drive-schedule-times').fill(value)
    const [res] = await Promise.all([
      page.waitForResponse((r) => r.url().includes('/admin/drive-sync/schedule') && r.request().method() === 'PUT'),
      card.getByTestId('drive-schedule-save').click(),
    ])
    expect(res.status(), await res.text()).toBe(200)
  }
  await save('19:30, 6:15')
  await expect(card).toContainText('at 06:15 and 19:30', { timeout: 10_000 })
  await save('07:30, 19:30')
  await expect(card).toContainText('at 07:30 and 19:30', { timeout: 10_000 })
  await ctx.close()
})
