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
import type { Browser, Page } from '@playwright/test'
import { PG_HOST, PG_PORT, PG_USER, PRACTICE_DB, PRACTICE_PASSWORD, apiHeaders } from '../harness/env'

test.describe.configure({ mode: 'serial' })

function sql(q: string): string {
  return execFileSync('psql', ['-h', PG_HOST, '-p', PG_PORT, '-U', PG_USER, '-d', PRACTICE_DB, '-tAc', q],
    { encoding: 'utf-8' }).trim()
}

// Logins are limited to 10 a minute per client IP; this file signs in about a
// dozen times, so every sign-in gets its own bucket (as the API specs do).
let bucket = 200
async function practiceAs(page: Page, user: string, fast = true) {
  await page.context().setExtraHTTPHeaders(apiHeaders(String(bucket++)))
  await page.addInitScript((f) => {
    try {
      localStorage.setItem('gi-demo-fast', f ? '1' : '0')
      localStorage.setItem('gi-demo-muted', '1')
    } catch { /* private mode */ }
  }, fast)
  await page.goto('/')
  await page.locator('.gi-env-switch').getByText('Practice', { exact: true }).click()
  await page.waitForLoadState('load')
  // switching environment reloads the page — fill only once it has settled
  await expect(page.locator('.gi-env-switch .ant-segmented-item-selected')).toHaveText('Practice')
  await expect(async () => {
    await page.getByPlaceholder('Username').fill(user)
    await page.getByPlaceholder('Password').fill(PRACTICE_PASSWORD)
    await expect(page.getByPlaceholder('Username')).toHaveValue(user, { timeout: 1000 })
  }).toPass({ timeout: 15_000 })
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByRole('button', { name: /sign out/i })).toBeVisible({ timeout: 20_000 })
}

async function runDemo(page: Page, id: string) {
  await page.getByTestId('demo-launch').click()
  await page.getByTestId(`demo-start-${id}`).click()
  const sub = page.getByTestId('demo-subtitle')
  await expect(sub).toBeVisible({ timeout: 15_000 })
  await expect(sub).toHaveText('Demo complete.', { timeout: 240_000 })
}

async function resetAsHod(page: Page) {
  await page.getByTestId('demo-close').click()
  await page.getByTestId('demo-launch').click()
  await page.getByTestId('demo-reset').click()
  const done = page.waitForResponse((r) => r.url().endsWith('/practice/demo/reset'))
  await page.locator('.ant-popconfirm').getByRole('button', { name: /ok|yes/i }).click()
  expect((await done).status()).toBe(200)
}

/** Reset as practice.hod in a fresh browser (a store keeper has no Reset). */
async function resetFresh(browser: Browser) {
  const ctx = await browser.newContext()
  const page = await ctx.newPage()
  await practiceAs(page, 'practice.hod')
  await page.getByTestId('demo-launch').click()
  await page.getByTestId('demo-reset').click()
  const done = page.waitForResponse((r) => r.url().endsWith('/practice/demo/reset'))
  await page.locator('.ant-popconfirm').getByRole('button', { name: /ok|yes/i }).click()
  expect((await done).status()).toBe(200)
  await ctx.close()
}

test('21f flow 1: the demo issues stock as the store keeper, switches to the HOD and approves it', async ({ page }) => {
  test.setTimeout(300_000)
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
  test.setTimeout(300_000)
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

test('21g flow 3: a batch received with its MFD is approved and shows in Lots & Expiry; reset removes the lot', async ({ page }) => {
  test.setTimeout(300_000)
  await practiceAs(page, 'practice.storekeeper')
  await runDemo(page, 'receive-lot')
  expect(Number(sql(`SELECT count(*) FROM receipts WHERE "Lot_Number" LIKE 'DEMO-%'`))).toBe(1)
  // the expiry was WORKED OUT from the MFD + the material's 9-month shelf life
  expect(sql(`SELECT count(*) FROM lots WHERE "Lot_Number" LIKE 'DEMO-%' AND "Expiry_Date" IS NOT NULL`)).toBe('1')
  await resetAsHod(page)
  expect(sql(`SELECT count(*) FROM lots WHERE "Lot_Number" LIKE 'DEMO-%'`)).toBe('0')
  expect(sql(`SELECT count(*) FROM receipts WHERE "Lot_Number" LIKE 'DEMO-%'`)).toBe('0')
})

test('21g flow 4: three tools lent, one back — the loan stays open with two out; reset removes it', async ({ page, browser }) => {
  test.setTimeout(300_000)
  await practiceAs(page, 'practice.storekeeper')
  await runDemo(page, 'loan-partial')
  expect(sql(`SELECT qty || '/' || qty_returned || '/' || status FROM returnable_items WHERE borrower_name LIKE 'DEMO-%'`))
    .toMatch(/^3(\.0+)?\/1(\.0+)?\//)
  await resetFresh(browser)
  expect(sql(`SELECT count(*) FROM returnable_items WHERE borrower_name LIKE 'DEMO-%'`)).toBe('0')
})

test('21g flow 5: the HOD sets the pace and accepts a minimum; reset puts BOTH back as they were', async ({ page }) => {
  test.setTimeout(300_000)
  const before = sql(`SELECT coalesce(string_agg(key || '=' || value, ',' ORDER BY key), '') FROM app_settings WHERE key LIKE 'ss_planned_sqm_per_day%'`)
  const minBefore = sql(`SELECT coalesce(string_agg("Site_ID" || '=' || "Minimum_Qty", ','), '') FROM inventory_site_overrides WHERE "SAP_Code" = '899971'`)
  await practiceAs(page, 'practice.hod')
  await runDemo(page, 'reorder-pace')
  expect(sql(`SELECT value FROM app_settings WHERE key = 'ss_planned_sqm_per_day@CNCEC'`)).toBe('6')
  expect(Number(sql(`SELECT count(*) FROM inventory_site_overrides WHERE "SAP_Code" = '899971' AND "Site_ID" = 'CNCEC'`))).toBe(1)
  await resetAsHod(page)
  expect(sql(`SELECT coalesce(string_agg(key || '=' || value, ',' ORDER BY key), '') FROM app_settings WHERE key LIKE 'ss_planned_sqm_per_day%'`)).toBe(before)
  expect(sql(`SELECT coalesce(string_agg("Site_ID" || '=' || "Minimum_Qty", ','), '') FROM inventory_site_overrides WHERE "SAP_Code" = '899971'`)).toBe(minBefore)
})

test('21g flow 6: a paper with a misread date — confirmed, a gold name accepted, staged with its paper; reset clears it', async ({ page }) => {
  test.setTimeout(300_000)
  await practiceAs(page, 'practice.storekeeper')
  await runDemo(page, 'ocr-paste')
  // staged with TODAY's date (the confirmed suggestion), the paper attached, PV → PU
  expect(sql(`SELECT count(*) FROM pending_issues WHERE "Issued_To" LIKE 'DEMO-%' AND "Date" LIKE to_char(current_date, 'YYYY-MM-DD') || '%' AND "Work_Type" IN ('PU', 'R/L')`)).toBe('2')
  expect(Number(sql(`SELECT count(*) FROM entry_attachments WHERE file_name LIKE 'DEMO-%' AND entry_table = 'pending_issues'`))).toBeGreaterThanOrEqual(1)
  expect(sql(`SELECT count(*) FROM ocr_aliases WHERE written_key = 'safty goggls'`)).toBe('1')
  await resetAsHod(page)
  expect(sql(`SELECT count(*) FROM pending_issues WHERE "Issued_To" LIKE 'DEMO-%'`)).toBe('0')
  expect(sql(`SELECT count(*) FROM ocr_aliases WHERE written_key = 'safty goggls'`)).toBe('0')
})

test('21g: a page tour walks the page it is on, read-only', async ({ page }) => {
  test.setTimeout(120_000)
  await practiceAs(page, 'practice.hod')
  await page.goto('/lots')
  await expect(page.getByTestId('lot-table')).toBeVisible({ timeout: 20_000 })
  await page.getByTestId('demo-launch').click()
  await expect(page.getByTestId('demo-tours')).toBeVisible()
  await page.getByTestId('demo-tour-here').click()
  await expect(page.getByTestId('demo-subtitle')).toHaveText('Demo complete.', { timeout: 60_000 })
  await expect(page).toHaveURL(/\/lots$/)
})

// ── Phase 23f (ruling Q23-14): the twelve new demos, each driven to its end ──

async function again(page: Page, id: string) {
  await page.getByTestId('demo-close').click()
  await runDemo(page, id)
}

test('23f: the look-only demos — Drive chip, Smart Reorder, the management tour, form print, voice', async ({ page }) => {
  test.setTimeout(600_000)
  await practiceAs(page, 'practice.hod')
  await runDemo(page, 'drive-freshness')
  await again(page, 'reorder-requests')
  await again(page, 'management-tour')
  await expect(page).toHaveURL(/\/hod\/prs$/)
  // the form demo stops BEFORE Download — no form number is used up
  const forms = sql(`SELECT count(*) FROM sme_consumption_form`)
  await again(page, 'form-print')
  await expect(page.locator('.gi-user-label')).toContainText('practice.supervisor')
  expect(sql(`SELECT count(*) FROM sme_consumption_form`)).toBe(forms)
  await again(page, 'assistant-voice')
})

test('23f: a receipt opens its delivery note, a delivery without one shows its WD number', async ({ page }) => {
  test.setTimeout(300_000)
  await practiceAs(page, 'practice.hod')
  await runDemo(page, 'dn-wd')
})

test('23f: QC confirms the proposed certificate; reset puts it back in the queue', async ({ page }) => {
  test.setTimeout(300_000)
  const q = `SELECT a.status || '/' || (SELECT count(*) FROM mtc_documents m WHERE m.drive_file_id = f.id)
             FROM mtc_assignments a JOIN drive_files f ON f.id = a.drive_file_id WHERE f.drive_id = 'practice-mtc-2'`
  expect(sql(q)).toBe('proposed/0')
  await practiceAs(page, 'practice.hod')
  await runDemo(page, 'mtc-confirm')
  await expect(page.locator('.gi-user-label')).toContainText('practice.qc')
  expect(sql(q)).toBe('confirmed/1')
  await resetFresh(page.context().browser()!)
  expect(sql(q)).toBe('proposed/0')
})

test('23f: the SAP mapper demo links the trowel and undoes it — nothing is left decided', async ({ page }) => {
  test.setTimeout(300_000)
  const q = `SELECT count(*) FROM request_sap_map WHERE written_key = 'desc:practice garden trowel hand'`
  const unmaps = () => Number(sql(`SELECT count(*) FROM system_audit_log WHERE action_type = 'REQUEST_SAP_UNMAP'
                                     AND details LIKE '%practice garden trowel hand%'`))
  const before = unmaps()
  await practiceAs(page, 'practice.hod')
  await runDemo(page, 'requests-sap')
  // it linked (and the link was audited), then undid it — the line needs a decision again
  expect(sql(q)).toBe('0')
  expect(unmaps()).toBe(before + 1)
})

test('23f: a Night paper already in the workbook is compared; the page tank fills it and Undo puts it back; nothing is staged', async ({ page }) => {
  test.setTimeout(300_000)
  const staged = () => sql(`SELECT count(*) FROM pending_issues`)
  const before = staged()
  await practiceAs(page, 'practice.storekeeper')
  await runDemo(page, 'ocr-page-tank')
  expect(staged()).toBe(before)
})

test('23f: a PR for a catalogue item reaches Logistics with its picture; reset removes it', async ({ page }) => {
  test.setTimeout(480_000)
  page.on('console', (m) => { if (process.env.DEMO_DEBUG) console.log('[console]', m.type(), m.text()) })
  const q = `SELECT count(*) FROM pr_master WHERE "Notes" LIKE 'DEMO-%' AND "Material_Code" = 'MAT-990001' AND "SAP_Code" = ''`
  await practiceAs(page, 'practice.hod')
  await runDemo(page, 'pr-pictures')
  await expect(page.locator('.gi-user-label')).toContainText('practice.logistics')
  expect(sql(q)).toBe('1')
  await resetFresh(page.context().browser()!)
  expect(sql(`SELECT count(*) FROM pr_master WHERE "Notes" LIKE 'DEMO-%'`)).toBe('0')
})

test('23f: the 6 mm sheet uses its family picture, then removes it; reset clears the family row', async ({ page }) => {
  test.setTimeout(300_000)
  const q = `SELECT count(*) || '/' || count(removed_at) FROM item_images WHERE item_key = 'MAT-990002' AND source = 'family'`
  // the drawn pictures must LOAD: the overlay writes them where the Practice API serves them
  const pics: number[] = []
  page.on('response', (r) => { if (r.url().includes('/catalogue/img/')) pics.push(r.status()) })
  await practiceAs(page, 'practice.hod')
  await runDemo(page, 'catalogue-family')
  expect(sql(q)).toBe('1/1')
  expect(pics.length).toBeGreaterThan(0)
  expect(pics.filter((st) => st !== 200)).toEqual([])
  await resetFresh(page.context().browser()!)
  expect(sql(q)).toBe('0/0')
})

test('23f: a return against its receipt, with its return DN, approved by the HOD; reset removes it', async ({ page }) => {
  test.setTimeout(300_000)
  await practiceAs(page, 'practice.storekeeper')
  await runDemo(page, 'return-dn')
  expect(sql(`SELECT status FROM pending_returns WHERE "Return_DN_No" LIKE 'DEMO-%'`)).toBe('approved')
  expect(sql(`SELECT count(*) FROM returns WHERE "Remarks" LIKE 'Return DN: DEMO-%'`)).toBe('1')
  await resetAsHod(page)
  expect(sql(`SELECT count(*) FROM pending_returns WHERE "Return_DN_No" LIKE 'DEMO-%'`)).toBe('0')
  expect(sql(`SELECT count(*) FROM returns WHERE "Remarks" LIKE 'Return DN: DEMO-%'`)).toBe('0')
})
