/**
 * Phase 14b — the HOD's QR ⇄ Excel reconciliation view. The arithmetic is
 * proven by service suite 14B; this proves the HOD can reach it and the API
 * is exact-locked like the rest of /sme/actuals.
 */
import { test, expect } from '@playwright/test'
import { apiAs } from '../harness/api'
import { storageStatePath } from '../harness/env'

test.use({ storageState: storageStatePath('hod') })

test('an HOD sees the reconciliation tab; a store keeper is refused the API', async ({ page }) => {
  const hod = await apiAs('hod', '41')
  expect((await hod.get('/sme/actuals/reconciliation')).status()).toBe(200)
  await hod.dispose()
  const sk = await apiAs('sk', '42')
  expect((await sk.get('/sme/actuals/reconciliation')).status()).toBe(403)
  await sk.dispose()

  await page.goto('/sme')
  // The SME tab bar overflows (antd scrolls it with a transform, so the last
  // tabs can sit clipped at the edge); a DOM click fires the same handler a
  // user's click — or the "…" overflow menu — does.
  await page.getByRole('tab', { name: /QR ⇄ Excel/ }).evaluate((el) => (el as HTMLElement).click())
  await expect(page.getByText(/never the paper plus the book/i)).toBeVisible({ timeout: 20_000 })
})
