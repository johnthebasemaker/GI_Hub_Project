/**
 * Phase 23f (ruling Q23-13) — Practice sign-in details.
 *
 * The Practice login page lists the 8 shared accounts with one-click sign-in
 * (never practice.admin); the Live login page shows nothing of the sort; the
 * Practice admin password is shown only inside a Live admin's console, hidden
 * until asked for. Values are never printed by this spec.
 */
import { expect, test } from '@playwright/test'
import { PRACTICE_PASSWORD, apiHeaders, storageStatePath } from '../harness/env'

let bucket = 400

test('23f: the Practice login page lists the 8 accounts and signs in with one click', async ({ browser }) => {
  const ctx = await browser.newContext()
  await ctx.setExtraHTTPHeaders(apiHeaders(String(bucket++)))
  const page = await ctx.newPage()
  try {
    await page.goto('/')
    await expect(page.getByTestId('practice-accounts')).toHaveCount(0)   // Live: no list
    await page.locator('.gi-env-switch').getByText('Practice', { exact: true }).click()
    await page.waitForLoadState('load')
    const card = page.getByTestId('practice-accounts')
    await expect(card).toBeVisible({ timeout: 20_000 })
    await expect(card.getByTestId('practice-password')).toHaveText(PRACTICE_PASSWORD)
    await expect(card.locator('[data-testid^="practice-signin-"]')).toHaveCount(8)
    await expect(card).not.toContainText('practice.admin')
    await page.screenshot({ path: test.info().outputPath('practice-accounts.png') })
    await card.getByTestId('practice-signin-qc').click()
    await expect(page.locator('.gi-user-label')).toContainText('practice.qc', { timeout: 20_000 })
  } finally {
    await ctx.close()
  }
})

test('23f: a Live admin finds the Practice admin password in the console, hidden until asked; an HOD has no such tab', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('admin') })
  const page = await ctx.newPage()
  try {
    await page.goto('/admin/console')
    await page.getByRole('tab', { name: 'Practice accounts' }).click()
    const card = page.getByTestId('practice-credentials')
    await expect(card).toBeVisible({ timeout: 20_000 })
    await expect(card).toContainText('tools/practice_db.py overlay')
    await expect(card).toContainText('practice.storekeeper')
    const pw = card.getByTestId('practice-admin-password')
    if (await pw.count()) {
      await expect(pw).toHaveText(/^•+$/)
      await card.getByTestId('practice-admin-show').click()
      await expect(pw).not.toHaveText(/^•+$/)
    } else {
      // no PRACTICE_ADMIN_PASSWORD in this stack's environment: the card says how to set one
      await expect(card).toContainText('PRACTICE_ADMIN_PASSWORD')
    }
  } finally {
    await ctx.close()
  }
  const hodCtx = await browser.newContext({ storageState: storageStatePath('hod') })
  const hod = await hodCtx.newPage()
  try {
    await hod.goto('/admin/console')
    await expect(hod.getByRole('tab', { name: 'Practice accounts' })).toHaveCount(0)
  } finally {
    await hodCtx.close()
  }
})
