/**
 * Phase 14d — "What's new", in a real browser. Suite 14D proves who is in an
 * audience; this proves the panel: it opens by itself for its audience, once,
 * and closing it marks it read. Plus the admin's publish controls and the
 * admin-only tutorial-freshness card. Fixture: global-setup step 1g.
 */
import { test, expect } from '@playwright/test'
import { apiAs } from '../harness/api'
import { storageStatePath } from '../harness/env'

test.describe.configure({ mode: 'serial' })

test('an admin publishes; nobody else may', async () => {
  const hod = await apiAs('hod', '61')
  expect((await hod.get('/announcements/admin')).status()).toBe(403)
  expect((await hod.post('/announcements/admin/e2e-news/publish', { data: {} })).status()).toBe(403)
  await hod.dispose()
  const admin = await apiAs('admin', '62')
  const list = await admin.get('/announcements/admin')
  expect(list.status()).toBe(200)
  expect((await list.json()).items.map((a: { key: string }) => a.key)).toContain('e2e-news')
  const pub = await admin.post('/announcements/admin/e2e-news/publish', { data: {} })
  expect(pub.status(), await pub.text()).toBe(200)
  await admin.dispose()
})

test('the panel opens by itself for its audience, once', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('news') })
  const page = await ctx.newPage()
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(String(e)))
  await page.goto('/stock')
  const panel = page.getByTestId('whats-new')
  await expect(panel).toBeVisible({ timeout: 20_000 })
  await expect(panel.getByText('E2E: glass announcement')).toBeVisible()
  await expect(panel.getByText('User Manual §4.11')).toBeVisible()
  await panel.getByRole('button', { name: 'Got it' }).click()
  await expect(panel).toBeHidden()
  // Read now: a reload does not bring it back by itself …
  await page.reload()
  await expect(page.getByRole('button', { name: "What's new" })).toBeVisible({ timeout: 20_000 })
  await page.waitForTimeout(1500)
  await expect(page.getByTestId('whats-new')).toHaveCount(0)
  // … and the gift icon still lists it.
  await page.getByRole('button', { name: "What's new" }).click()
  await expect(page.getByTestId('whats-new').getByText('E2E: glass announcement')).toBeVisible()
  expect(errors, errors.join(' | ')).toHaveLength(0)
  await ctx.close()
})

test('the admin sees the Announcements tab and tutorial freshness', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('admin') })
  const page = await ctx.newPage()
  await page.goto('/admin/console')
  await page.getByRole('tab', { name: 'Announcements' }).evaluate((el) => (el as HTMLElement).click())
  await expect(page.getByText('Announcements are written in the pull request, not here')).toBeVisible()
  await expect(page.getByText('e2e-news', { exact: false }).first()).toBeVisible()
  await expect(page.getByTestId('tutorial-freshness')).toBeVisible()
  await ctx.close()
})
