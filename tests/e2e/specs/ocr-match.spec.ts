/**
 * Phase 21d — the OCR review grid's three colours, in a browser (Q21-3..5).
 *
 * Suite 21D pins the matcher and the learned names; this proves the screen the
 * store keeper works in: a near-miss name is a GOLD suggestion with Accept and
 * NO material filled in (never auto, Q21-5), a name nothing resembles is RED,
 * Stage waits until both are resolved, and an accepted name comes back GREEN
 * ("learned") on the next paste (Q21-3). Paste lane — no vision model needed.
 * Fixture: global-setup (E2EOCR-1 / -2).
 */
import { test, expect } from '@playwright/test'
import { storageStatePath } from '../harness/env'

const PASTE = 'Aria, E2EOCR dust mash, Nos, 2\nAria, qxzv wbrp, Nos, 1'

test('21d: gold suggestion with Accept, red when nothing is close, Stage waits, an accepted name is learned', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('sk') })
  const page = await ctx.newPage()
  await page.goto('/entry/ocr')
  const paste = page.getByPlaceholder(/Imran/)
  await expect(paste).toBeVisible({ timeout: 20_000 })
  await paste.fill(PASTE)
  await page.getByRole('button', { name: 'Parse' }).click()

  const gold = page.getByTestId('ocr-suggestion')
  await expect(gold).toHaveCount(1, { timeout: 20_000 })
  await expect(gold).toContainText('did you mean')
  await expect(page.getByTestId('ocr-notfound')).toHaveCount(1)
  const stage = page.getByTestId('ocr-stage')
  await expect(stage).toBeDisabled()
  await expect(stage).toContainText('Resolve 2 row(s) first')
  await page.screenshot({ path: test.info().outputPath('ocr-three-states.png') })

  // Accept the gold one (this teaches it), remove the red one → Stage enables
  const learned = page.waitForResponse((r) => r.url().endsWith('/ai/ocr/aliases') && r.request().method() === 'POST')
  await page.getByTestId('ocr-accept').click()
  expect((await learned).status()).toBe(200)
  await page.locator('.ant-table-row', { hasText: 'qxzv wbrp' }).getByRole('button').last().click()
  await expect(stage).toBeEnabled()
  await expect(stage).toContainText('Stage 1 row(s)')

  // the same written name, pasted again: GREEN, learned
  await page.getByRole('button', { name: 'Discard' }).click()
  await paste.fill('Aria, E2EOCR Dust  Mash, Nos, 1')
  await page.getByRole('button', { name: 'Parse' }).click()
  await expect(page.getByTestId('ocr-state-auto')).toContainText('learned', { timeout: 20_000 })
  await expect(page.getByTestId('ocr-suggestion')).toHaveCount(0)
  await ctx.close()
})

/**
 * Phase 21d follow-up — the paper's date box. 3 of the operator's 11 photos
 * were read with the wrong month or day; such a sheet now waits for the store
 * keeper to confirm the date, and the first suggestion is the likely one. The
 * date is built from TODAY with the month misread, so the right answer is
 * always today (day intact, month wrong — the 01/07 → 01/10 case).
 */
test('21d: an implausible paper date holds Stage until the store keeper confirms one', async ({ browser }) => {
  const now = new Date()
  const p2 = (n: number) => String(n).padStart(2, '0')
  const yy = p2(now.getFullYear() % 100)
  const wrongMonth = ((now.getMonth() + 6) % 12) + 1
  const todayLabel = `${p2(now.getDate())}/${p2(now.getMonth() + 1)}/${yy}`
  const ctx = await browser.newContext({ storageState: storageStatePath('sk') })
  const page = await ctx.newPage()
  await page.goto('/entry/ocr')
  const paste = page.getByPlaceholder(/Imran/)
  await expect(paste).toBeVisible({ timeout: 20_000 })
  await paste.fill(`Date: ${p2(now.getDate())}/${p2(wrongMonth)}/${yy} (Night)\nAria, E2EOCR LEATHER GLOVES, Pair, 1, RIL`)
  await page.getByRole('button', { name: 'Parse' }).click()

  const check = page.getByTestId('ocr-date-check')
  await expect(check).toBeVisible({ timeout: 20_000 })
  const stage = page.getByTestId('ocr-stage')
  await expect(stage).toBeDisabled()
  await expect(stage).toContainText("Confirm the paper's date first")
  await expect(page.getByTestId('ocr-date-candidate').first()).toHaveText(todayLabel)
  // the work type comes back in the workbook's spelling (RIL → R/L)
  await expect(page.getByTestId('ocr-work-type').first()).toHaveValue('R/L')
  await page.screenshot({ path: test.info().outputPath('ocr-date-check.png') })

  await page.getByTestId('ocr-date-candidate').first().click()
  await expect(check).toHaveCount(0)
  await expect(stage).toBeEnabled()
  await expect(stage).toContainText('Stage 1 row(s)')

  // a plausible date is simply taken, and says so
  await page.getByRole('button', { name: 'Discard' }).click()
  await paste.fill(`Date: ${todayLabel}\nAria, E2EOCR LEATHER GLOVES, Pair, 1`)
  await page.getByRole('button', { name: 'Parse' }).click()
  await expect(page.getByTestId('ocr-paper-date-ok')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId('ocr-date-check')).toHaveCount(0)
  await expect(stage).toBeEnabled()
  await ctx.close()
})
