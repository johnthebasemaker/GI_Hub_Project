/**
 * Phase 14c — the Surface Shield queue, one JOB at a time, in a real browser.
 *
 * Suite 14C proves the arithmetic (the area credited ONCE per job, the split,
 * the whole-job rejection); this proves the screen: the day's draws on one
 * vessel are one card, the system code arrives suggested and the area
 * pre-filled, one submission reaches the HOD as one job, and the HOD decides
 * it whole. Fixture: global-setup step 1b-iii (E2E-JOB-TANK, system 9102).
 */
import { test, expect } from '@playwright/test'
import { storageStatePath } from '../harness/env'

test.describe.configure({ mode: 'serial' })

const JOB = '[data-job="CNCEC|2026-09-20|E2E-JOB-TANK"]'

test('15b: the job card boxes tick, untick and multi-select', async ({ browser }) => {
  // Phase 15b. rowKey was a string and the selection numbers, so antd never
  // showed a box ticked and each click replaced the selection with one row —
  // the operator's "Submit 1 to the HOD" with every box empty. The 14c spec
  // only ever submitted the default, so it never clicked a box.
  const ctx = await browser.newContext({ storageState: storageStatePath('supervisor') })
  const page = await ctx.newPage()
  await page.goto('/execution')
  await page.getByLabel('Find a job').fill('E2E-JOB-TANK')
  const card = page.locator(JOB)
  await expect(card).toBeVisible({ timeout: 20_000 })
  const rows = card.locator('tbody .ant-checkbox-input')
  const all = card.locator('thead .ant-checkbox-input')
  const submit = card.getByRole('button', { name: /^Submit \d+ to the HOD$/ })
  await expect(rows).toHaveCount(2)
  // The recipe's default selection is SHOWN, not just counted.
  await expect(rows.nth(0)).toBeChecked()
  await expect(rows.nth(1)).toBeChecked()
  await expect(submit).toHaveText('Submit 2 to the HOD')
  await rows.nth(1).click()
  await expect(rows.nth(1)).not.toBeChecked()
  await expect(rows.nth(0)).toBeChecked()
  await expect(submit).toHaveText('Submit 1 to the HOD')
  await rows.nth(1).click()
  await expect(rows.nth(1)).toBeChecked()
  await expect(rows.nth(0)).toBeChecked()
  await expect(submit).toHaveText('Submit 2 to the HOD')
  await all.click()                       // everything ticked → header clears all
  await expect(rows.nth(0)).not.toBeChecked()
  await expect(rows.nth(1)).not.toBeChecked()
  await expect(submit).toBeDisabled()
  await all.click()
  await expect(rows.nth(0)).toBeChecked()
  await expect(rows.nth(1)).toBeChecked()
  await expect(submit).toHaveText('Submit 2 to the HOD')
  await ctx.close()
})

test('a supervisor attributes a two-material job with ONE code and ONE area', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('supervisor') })
  const page = await ctx.newPage()
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(String(e)))
  await page.goto('/execution')
  // The E2E database carries the real legacy ledger, so the queue is long:
  // find the fixture job the way a supervisor finds theirs.
  await page.getByLabel('Find a job').fill('E2E-JOB-TANK')
  const card = page.locator(JOB)
  await expect(card).toBeVisible({ timeout: 20_000 })
  await expect(card.getByText('2 material(s)')).toBeVisible()
  // Suggested from the materials: 9102's recipe lists both.
  await expect(card.getByText(/9102.*covers 2 of 2/)).toBeVisible()
  // Pre-filled from the store keeper's "Shell - 12.5 SQM Done".
  await expect(card.getByLabel('Area covered')).toHaveValue('12.5')
  // Base first, then packs (Q14-5): 2.5 cans × 4 kg.
  await expect(card.getByText('10 KG · 2.5 Can')).toBeVisible()
  const posted = page.waitForRequest((r) => r.method() === 'POST'
    && r.url().endsWith('/execution/sme-link/groups'))
  await card.getByRole('button', { name: /Submit 2 to the HOD/ }).click()
  // Exactly the two ticked rows travel — the ids, not a count.
  const body = (await posted).postDataJSON() as { consumption_ids: number[] }
  expect(body.consumption_ids).toHaveLength(2)
  expect(body.consumption_ids.every((n) => typeof n === 'number')).toBe(true)
  await expect(page.getByText(/goes to the HOD as one approval/)).toBeVisible()
  await expect(page.locator(JOB)).toHaveCount(0)
  expect(errors, errors.join(' | ')).toHaveLength(0)
  await ctx.close()
})

test('the HOD approves the job as a whole', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('hod') })
  const page = await ctx.newPage()
  await page.goto('/execution')
  await page.getByText(/Awaiting the HOD/).click()
  const card = page.locator('.gi-job-card', { hasText: 'E2E-JOB-TANK' })
  await expect(card).toBeVisible({ timeout: 20_000 })
  await expect(card.getByText('12.5 m²')).toBeVisible()
  await card.getByRole('button', { name: 'Review job' }).click()
  await expect(page.getByText('One decision for the whole job')).toBeVisible()
  await page.getByRole('button', { name: 'Approve the job' }).click()
  await expect(page.getByText(/credited once to that equipment/)).toBeVisible()
  await ctx.close()
})
