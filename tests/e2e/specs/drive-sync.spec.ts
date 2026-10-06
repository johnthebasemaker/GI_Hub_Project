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
  await expect(card).toContainText('every day at 07:30')
  await expect(card.getByTestId('drive-fetch')).toBeDisabled()
  await expect(card.getByTestId('drive-commit-erp')).toBeDisabled()
  await page.screenshot({ path: test.info().outputPath('drive-sync-card.png') })
  await ctx.close()
})
