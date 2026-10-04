/**
 * Phase 20b — bulk submit and bulk approve, in a browser.
 *
 * Suite 20B pins the method (savepoints, skips, idempotence, roles). This
 * proves the clicks the operator asked to remove: the supervisor ticks ready
 * job cards (one that is not ready cannot be ticked, and says why), reviews
 * them and submits them in one go; the HOD ticks jobs and approves them in one
 * go — and the confirmation NAMES the job drawn more than 10 % off the recipe
 * before the click (ruling Q20-10).
 *
 * Its own tank (global-setup 1b-v-b), because sme-jobs.spec files and
 * approves E2E-JOB-TANK in parallel.
 */
import { test, expect } from '@playwright/test'
import { storageStatePath } from '../harness/env'

const TANK = 'E2E-BULK-TANK'

test('20b: the supervisor submits two ready jobs at once; the HOD approves them at once, warned of the high variance', async ({ browser }) => {
  // ── the field: select all ready → review → submit ─────────────────────────
  const sup = await browser.newContext({ storageState: storageStatePath('supervisor') })
  const page = await sup.newPage()
  await page.goto('/execution')
  await page.getByLabel('Find a job').fill(TANK)
  const cards = page.locator('.gi-job-card', { hasText: TANK })
  await expect(cards).toHaveCount(2, { timeout: 20_000 })
  const bar = page.getByTestId('bulk-bar')
  await expect(bar.getByTestId('bulk-select-ready')).toContainText('(2)')
  await bar.getByTestId('bulk-select-ready').click()
  await expect(bar.getByTestId('bulk-submit')).toContainText('(2)')

  // a card made NOT ready (its area cleared) leaves the batch and says why
  const wall = page.locator('[data-job="CNCEC|2026-09-25|E2E-BULK-TANK"]')
  await wall.getByLabel('Area covered').fill('')
  await expect(wall.getByTestId('bulk-notready-CNCEC|2026-09-25|E2E-BULK-TANK')).toContainText('no area')
  await expect(bar.getByTestId('bulk-submit')).toContainText('(1)')
  await wall.getByLabel('Area covered').fill('12.5')
  await wall.getByRole('checkbox', { name: /for bulk submit/ }).check()
  await expect(bar.getByTestId('bulk-submit')).toContainText('(2)')

  await bar.getByTestId('bulk-submit').click()
  const review = page.locator('.ant-modal', { hasText: 'Submit 2 job(s) to the HOD' })
  await expect(review).toContainText('17.5 m²')            // 12.5 + 5
  const posted = page.waitForRequest((r) => r.method() === 'POST'
    && r.url().endsWith('/execution/sme-link/groups/bulk-submit'))
  await page.getByTestId('bulk-submit-confirm').click()
  const body = (await posted).postDataJSON() as { jobs: { sqm: number; notes: string }[] }
  expect(body.jobs.map((j) => j.sqm).sort()).toEqual([12.5, 5])
  await expect(page.getByText('Submitted 2 job(s) to the HOD')).toBeVisible()
  await expect(cards).toHaveCount(0)
  await sup.close()

  // ── the HOD: tick both → the confirmation names the high variance → approve ─
  const hod = await browser.newContext({ storageState: storageStatePath('hod') })
  const hp = await hod.newPage()
  await hp.goto('/execution')
  await hp.getByText(/Awaiting the HOD/).click()
  const jobs = hp.locator('.gi-job-card', { hasText: TANK })
    .filter({ has: hp.getByRole('button', { name: 'Review job' }) })
  await expect(jobs).toHaveCount(2, { timeout: 20_000 })
  for (const i of [0, 1]) await jobs.nth(i).getByRole('checkbox').check()
  await hp.getByTestId('hod-approve-selected').click()
  const confirm = hp.locator('.ant-modal', { hasText: 'Approve 2 job(s)' })
  await expect(confirm).toContainText('17.5 m²')
  await expect(confirm.getByTestId('hod-risky')).toContainText('2026-09-26')
  await expect(confirm.getByTestId('hod-risky')).toContainText(TANK)
  await hp.screenshot({ path: test.info().outputPath('bulk-approve-confirm.png') })
  await hp.getByTestId('hod-approve-confirm').click()
  await expect(hp.getByText('Approved 2 job(s)')).toBeVisible()
  await expect(jobs).toHaveCount(0)
  await hod.close()
})
