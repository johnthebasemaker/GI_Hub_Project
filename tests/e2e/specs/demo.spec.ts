/**
 * Phase 21f — the self-driving demos, run end to end in the PRACTICE leg
 * (PROPOSED_PHASE21_PLAN §3.2.4: a demo that would stall in front of
 * management fails a pull request first).
 *
 * Headless, muted, fast (localStorage gi-demo-fast / gi-demo-muted). Each
 * flow is started the way a person starts it — ▶ Auto demo → ▶ Start — and
 * must reach "Demo complete." on its own: the store keeper → HOD switch, the
 * approvals, all through the real UI. Then the Practice DATABASE is asked what
 * happened, and Reset demo data must put it back.
 *
 * SERIAL: both flows share the Practice database, and Reset removes every
 * DEMO- entry — run in parallel, one flow's reset would delete the other's.
 */
import { execFileSync } from 'node:child_process'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { PG_HOST, PG_PORT, PG_USER, PRACTICE_DB, PRACTICE_PASSWORD } from '../harness/env'

test.describe.configure({ mode: 'serial' })

function sql(q: string): string {
  return execFileSync('psql', ['-h', PG_HOST, '-p', PG_PORT, '-U', PG_USER, '-d', PRACTICE_DB, '-tAc', q],
    { encoding: 'utf-8' }).trim()
}

async function practiceAs(page: Page, user: string, fast = true) {
  await page.addInitScript((f) => {
    try {
      localStorage.setItem('gi-demo-fast', f ? '1' : '0')
      localStorage.setItem('gi-demo-muted', '1')
    } catch { /* private mode */ }
  }, fast)
  await page.goto('/')
  await page.locator('.gi-env-switch').getByText('Practice', { exact: true }).click()
  await page.waitForLoadState('load')
  await page.getByPlaceholder('Username').fill(user)
  await page.getByPlaceholder('Password').fill(PRACTICE_PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('button', { name: /sign out/i })).toBeVisible({ timeout: 20_000 })
}

async function runDemo(page: Page, id: string) {
  await page.getByTestId('demo-launch').click()
  await page.getByTestId(`demo-start-${id}`).click()
  const sub = page.getByTestId('demo-subtitle')
  await expect(sub).toBeVisible({ timeout: 15_000 })
  await expect(sub).toHaveText('Demo complete.', { timeout: 120_000 })
}

async function resetAsHod(page: Page) {
  await page.getByTestId('demo-close').click()
  await page.getByTestId('demo-launch').click()
  await page.getByTestId('demo-reset').click()
  const done = page.waitForResponse((r) => r.url().endsWith('/practice/demo/reset'))
  await page.locator('.ant-popconfirm').getByRole('button', { name: /ok|yes/i }).click()
  expect((await done).status()).toBe(200)
}

test('21f flow 1: the demo issues stock as the store keeper, switches to the HOD and approves it', async ({ page }) => {
  test.setTimeout(180_000)
  await practiceAs(page, 'practice.storekeeper')
  await runDemo(page, 'consumption-to-approval')
  // it ended signed in as the HOD — a real session, not a costume
  await expect(page.locator('.gi-user-label')).toContainText('practice.hod')
  // and the ledger really has it: an APPROVED consumption row tagged DEMO-
  expect(Number(sql(`SELECT count(*) FROM consumption WHERE "Remarks" LIKE 'DEMO-%'`))).toBeGreaterThanOrEqual(1)
  expect(Number(sql(`SELECT count(*) FROM system_audit_log WHERE action_type = 'PRACTICE_DEMO_SWITCH'`)))
    .toBeGreaterThanOrEqual(2)
  await page.screenshot({ path: test.info().outputPath('demo-flow1-done.png') })
  await resetAsHod(page)
  expect(Number(sql(`SELECT count(*) FROM consumption WHERE "Remarks" LIKE 'DEMO-%'`))).toBe(0)
  expect(Number(sql(`SELECT count(*) FROM pending_issues WHERE "Remarks" LIKE 'DEMO-%'`))).toBe(0)
})

test('21f flow 2: the supervisor bulk-submits the demo tank, the HOD bulk-approves; reset puts the tank back', async ({ page }) => {
  test.setTimeout(180_000)
  await practiceAs(page, 'practice.supervisor')
  await runDemo(page, 'ss-bulk')
  expect(sql(`SELECT count(*) FROM sme_attribution_group WHERE "Equipment_Tag_No" = 'DEMO-TANK-1' AND status = 'committed'`))
    .toBe('2')
  expect(Number(sql(`SELECT "Done_SQM" FROM sme_sqm_progress WHERE "Equipment_Tag_No" = 'DEMO-TANK-1'`))).toBe(10)
  await resetAsHod(page)
  expect(sql(`SELECT count(*) FROM sme_attribution_group WHERE "Equipment_Tag_No" = 'DEMO-TANK-1'`)).toBe('0')
  expect(Number(sql(`SELECT "Done_SQM" FROM sme_sqm_progress WHERE "Equipment_Tag_No" = 'DEMO-TANK-1'`))).toBe(0)
})

test('21f: touching the page pauses the demo ("You took over"), Resume carries on, Esc stops it', async ({ page }) => {
  test.setTimeout(120_000)
  // not fast: real reading time, so there is a moment to take over
  await practiceAs(page, 'practice.storekeeper', false)
  await page.getByTestId('demo-launch').click()
  await page.getByTestId('demo-start-consumption-to-approval').click()
  await expect(page.getByTestId('demo-overlay')).toBeVisible()
  await page.locator('.gi-header-title').click()
  await expect(page.getByTestId('demo-resume')).toContainText('You took over')
  await page.getByTestId('demo-resume').click()
  await expect(page.getByTestId('demo-pause')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('demo-subtitle')).toHaveText('Demo stopped.')
})
