/**
 * Phase 15e — the operator's 2026-09-30 follow-ups, in a real browser.
 *
 *   · a store keeper is assigned ONE site: the entry forms show it as a fact
 *     and post to it, instead of asking with a dropdown;
 *   · the WBS field says who sets WBS numbers up when a site has none;
 *   · the HOD adds (and edits) inventory items from the Inventory page, pinned
 *     to their own site; the admin still has Admin → Inventory.
 * Suite 15E pins the server side (scope, refusals, audit).
 */
import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { storageStatePath } from '../harness/env'

/** GET the API as the signed-in user (the token lives in localStorage). */
async function api<T>(page: Page, path: string): Promise<T> {
  return page.evaluate(async (p) => {
    const r = await fetch(`/api${p}`, {
      headers: { Authorization: `Bearer ${localStorage.getItem('gi_token')}` } })
    return r.json()
  }, path) as Promise<T>
}

test('15e: the store keeper’s Issue and Receive forms show their site, not a dropdown', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('sk') })
  const page = await ctx.newPage()
  await page.goto('/entry/issue')
  const me = await api<{ site_id: string }>(page, '/auth/me')
  for (const path of ['/entry/issue', '/entry/receive']) {
    await page.goto(path)
    const tag = page.getByTestId('site-locked')
    await expect(tag).toBeVisible({ timeout: 20_000 })
    await expect(tag).toHaveText(me.site_id)
    await expect(page.getByText('Select site')).toHaveCount(0)
  }
  await ctx.close()
})

test('15e: the HOD adds an inventory item from the Inventory page, for their own site', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('hod') })
  const page = await ctx.newPage()
  const sap = `E2E15E-${Date.now().toString(36).toUpperCase()}`
  await page.goto('/records/inventory')
  await page.getByRole('button', { name: 'New item' }).click()
  const dlg = page.getByRole('dialog', { name: 'New inventory item' })
  await expect(dlg).toBeVisible()
  await dlg.getByLabel('SAP Code').fill(sap)
  await dlg.getByLabel('Description').fill('E2E 15e HOD item')
  await dlg.getByLabel('Category').fill('Consumables')
  const saved = page.waitForResponse((r) => r.request().method() === 'POST'
    && r.url().endsWith('/inventory-items'))
  await dlg.getByRole('button', { name: 'Save' }).click()
  const r = await saved
  if (r.status() === 422) {
    // a fresh E2E database may have no 'Consumables' yet — confirm it as new
    await page.getByRole('button', { name: /Yes, new category/ }).click()
  }
  await expect(page.getByText(`Item ${sap} created`)).toBeVisible({ timeout: 15_000 })
  const me = await api<{ site_id: string }>(page, '/auth/me')
  const { items } = await api<{ items: { SAP_Code: string; Site_ID: string }[] }>(
    page, `/inventory?q=${encodeURIComponent(sap)}`)
  expect(items.find((i) => i.SAP_Code === sap)?.Site_ID).toBe(me.site_id)
  await ctx.close()
})
