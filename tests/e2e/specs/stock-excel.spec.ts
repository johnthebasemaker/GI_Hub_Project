/**
 * Stock vs the Excel workbook — the screen. Suite SX proves the causes add up
 * and nothing changes; this proves the Stock page shows WHICH materials differ
 * (banner + red "≠ Excel" rows) and WHY (the drawer, the Material Card).
 * Fixture: global-setup step 1h (one stored check naming SAP 1001).
 */
import { test, expect } from '@playwright/test'
import { apiAs } from '../harness/api'
import { storageStatePath } from '../harness/env'

test.use({ storageState: storageStatePath('hod') })

test('the Stock page flags the materials that differ from Excel, with the reason', async ({ page }) => {
  await page.goto('/stock')
  const banner = page.getByTestId('excel-check-banner')
  await expect(banner).toBeVisible({ timeout: 20_000 })
  await expect(banner).toContainText("1 of 505 materials don't match the Excel workbook")
  await page.getByPlaceholder('Search SAP code / name…').fill('1001')
  await page.getByPlaceholder('Search SAP code / name…').press('Enter')
  await expect(page.getByTestId('excel-mismatch-tag').first()).toBeVisible()
  await page.getByRole('button', { name: 'Review the differences' }).click()
  await page.getByText('E2E fixture material').click()
  await expect(page.getByText(/entered in GI Hub by e2e, and the Receipt Log does not have it/)).toBeVisible()
  await expect(page.locator('.ant-drawer .ant-tag', { hasText: 'Only in GI Hub' })).toBeVisible()
})

test('the Material Card says the same for its one material', async ({ page }) => {
  await page.goto('/stock/material/1001')
  await expect(page.getByTestId('excel-mismatch-card')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId('excel-mismatch-card')).toContainText('the Excel workbook says 21')
})

test('a view-only auditor may read the check but not upload one', async () => {
  const aud = await apiAs('auditor', '63')
  expect((await aud.get('/stock/excel-check', { params: { site_id: 'CNCEC' } })).status()).toBe(200)
  const up = await aud.post('/stock/excel-check', {
    multipart: { file: { name: 'x.xlsx', mimeType: 'application/octet-stream', buffer: Buffer.from('x') } },
  })
  expect(up.status()).toBe(403)
  await aud.dispose()
})
