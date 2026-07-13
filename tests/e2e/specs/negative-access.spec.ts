/**
 * Role-lock lattice (automatic_test.md §12): the API refuses cross-role and
 * cross-site access with 403, and the UI hides admin affordances from
 * non-admin roles.
 */
import { test, expect } from '@playwright/test'
import { apiAs } from '../harness/api'
import { storageStatePath } from '../harness/env'

test('SK cannot reach HOD or logistics surfaces', async () => {
  const sk = await apiAs('sk', '91')
  expect((await sk.get('/hod/pending')).status()).toBe(403)
  expect((await sk.get('/logistics/prs')).status()).toBe(403)
  await sk.dispose()
})

test('HOD cannot reach admin surfaces or other sites', async () => {
  const hod = await apiAs('hod', '92')
  expect((await hod.get('/admin/users')).status()).toBe(403)
  const site = ((await (await hod.get('/auth/me')).json()) as { site_id: string }).site_id
  const other = site === 'HQ' ? 'CNCEC' : 'HQ'
  expect((await hod.get(`/hod/low-stock?site_id=${other}`)).status()).toBe(403)
  await hod.dispose()
})

test('supervisor cannot post ledger entries', async () => {
  const sup = await apiAs('supervisor', '93')
  const r = await sup.post('/entry/receipts', {
    data: { Date: '2026-07-13', SAP_Code: '1001', Quantity: 1, Site_ID: 'CNCEC' },
  })
  expect(r.status()).toBe(403)
  await sup.dispose()
})

test.describe('UI hides admin affordances from SK', () => {
  test.use({ storageState: storageStatePath('sk') })
  test('no Users management for a store keeper', async ({ page }) => {
    await page.goto('/admin/users')
    await expect(page.getByRole('button', { name: /new user/i })).toHaveCount(0)
  })
})
