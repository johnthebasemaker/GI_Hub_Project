/**
 * Phase 16c — which lot to issue, and what is about to expire, in a browser.
 *
 * Suite 16C pins the order and the buckets; this proves the screens: the
 * Issue form's Lot field lists the material's open lots in FEFO order with
 * the EXPIRED one last and marked, suggests the FEFO lot, and asks for a
 * reason only when another is chosen; the Lot Register shows each lot's status
 * and warns about the expired one. Fixture: global-setup 1b-vi (E2ELOT-1).
 */
import { test, expect } from '@playwright/test'
import { storageStatePath } from '../harness/env'

test('16c: the Issue form lists lots in FEFO order — the expired one last, never the suggestion', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('sk') })
  const page = await ctx.newPage()
  await page.goto('/entry/issue')
  const material = page.locator('.ant-form-item', { hasText: 'Material (SAP Code)' }).getByRole('combobox')
  await material.click()
  await page.keyboard.type('E2ELOT-1')
  await page.locator('.ant-select-item-option', { hasText: 'E2ELOT-1' }).first().click()
  const picker = page.getByTestId('lot-picker')
  await expect(picker).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('Blank = FEFO: E2ESOON')).toBeVisible()
  await picker.click()
  const opts = page.locator('.ant-select-dropdown:visible .ant-select-item-option')
  await expect(opts).toHaveCount(3)
  await expect(opts.nth(0)).toContainText('E2ESOON')
  await expect(opts.nth(0)).toContainText('FEFO')
  await expect(opts.nth(1)).toContainText('E2ELATE')
  await expect(opts.nth(2)).toContainText('E2EOLD')
  await expect(opts.nth(2)).toContainText('expired')
  // the FEFO lot itself is not an override — no reason asked
  await opts.nth(0).click()
  await expect(page.getByText('Reason for manual lot (FEFO override)')).toHaveCount(0)
  // another lot is — the reason field appears (allow-and-log: asked, never blocked)
  await picker.click()
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option', { hasText: 'E2ELATE' }).click()
  await expect(page.getByText('Reason for manual lot (FEFO override)')).toBeVisible()
  await ctx.close()
})

test('16c: the Lot Register shows each lot’s status and warns about expired stock', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('hod') })
  const page = await ctx.newPage()
  await page.goto('/lots')
  await expect(page.getByRole('heading', { name: 'Lots & Expiry' })).toBeVisible({ timeout: 20_000 })
  await page.getByLabel('Find a lot').fill('E2ELOT')
  await page.getByLabel('Find a lot').press('Enter')
  const rows = page.locator('.ant-table-row')
  await expect(rows).toHaveCount(3)
  await expect(page.locator('.ant-table-row', { hasText: 'E2EOLD' })).toContainText('Expired')
  await expect(page.locator('.ant-table-row', { hasText: 'E2ESOON' })).toContainText('≤ 30 days')
  await expect(page.locator('.ant-table-row', { hasText: 'E2ELATE' })).toContainText('OK')
  await expect(page.getByText(/lot\(s\) are past their expiry and still have stock/)).toBeVisible()
  await ctx.close()
})
