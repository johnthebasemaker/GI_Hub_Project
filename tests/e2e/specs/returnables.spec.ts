/**
 * Phase 18 Track 3 — the return desk, in a browser.
 *
 * Suite 18R pins the API (the resolver, the condition, the batch, the local
 * clock); this proves the screen a store keeper actually uses: the scan box
 * has focus the moment the page opens (a keyboard-wedge scanner types into
 * whatever is focused), a tool's code scanned on the Loan form is remembered,
 * and scanning that same code at the desk finds the loan and returns it with
 * a condition. No camera in headless Chromium: the scanner modal's manual
 * field (autofocused since Phase 18) stands in for the wedge.
 */
import { test, expect } from '@playwright/test'
import { storageStatePath } from '../harness/env'

test('18r: a scanned tool is loaned, then found by the same scan and returned damaged', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('sk') })
  const page = await ctx.newPage()
  const code = `E2E-TOOL-${Date.now().toString().slice(-6)}`
  const name = `E2E Torque Wrench ${code.slice(-6)}`

  await page.goto('/entry/returnables')
  await expect(page.getByRole('heading', { name: 'Returnable Items' })).toBeVisible({ timeout: 20_000 })
  const desk = page.getByTestId('return-scan-input')
  // ⚠️ the whole point for a wedge scanner: focus without a click
  await expect(desk).toBeFocused()

  // ── lend it, scanning the tool's code on the form ───────────────────────
  await page.getByRole('button', { name: 'Loan a tool' }).click()
  const loanModal = page.locator('.ant-modal', { hasText: 'Loan a tool to an employee' })
  await loanModal.getByRole('button', { name: 'Scan tool' }).click()
  const scanModal = page.locator('.ant-modal', { hasText: "Scan the tool's sticker" })
  const manual = scanModal.getByPlaceholder('…or type the code')
  await expect(manual).toBeFocused()
  await manual.fill(code)
  await manual.press('Enter')
  await expect(loanModal.getByText(`Scanned: ${code}`)).toBeVisible()
  await loanModal.getByPlaceholder('e.g. Torque wrench — or Scan tool ↑').fill(name)
  await loanModal.getByPlaceholder('Employee name — or Scan badge ↑').fill('E2E Borrower')
  await loanModal.getByRole('button', { name: '+3 days' }).click()
  await loanModal.getByRole('button', { name: 'Record loan' }).click()
  await expect(page.getByText('Loan recorded')).toBeVisible()
  await expect(page.locator('.ant-table-row', { hasText: name })).toContainText('on loan')

  // ── return it: the same code at the desk, lower-case as a sloppy scan ───
  await desk.click()
  await desk.fill(code.toLowerCase())
  await desk.press('Enter')
  const panel = page.getByTestId('return-panel')
  await expect(panel).toContainText(name)
  await expect(page.getByTestId('return-scan-msg')).toContainText('1 open loan')
  await page.screenshot({ path: test.info().outputPath('return-desk.png') })
  await panel.getByText('Damaged', { exact: true }).click()
  await panel.getByPlaceholder('Note (optional) — e.g. blade chipped').fill('handle cracked')
  await page.getByTestId('return-confirm').click()
  await expect(page.getByText(/Returned 1 item as DAMAGED/)).toBeVisible()
  await expect(panel).toHaveCount(0)

  // ── it moved from Open to Returned, with its condition ──────────────────
  await expect(page.locator('.ant-table-row', { hasText: name })).toHaveCount(0)
  await page.getByTestId('loan-view').getByText('Returned', { exact: true }).click()
  const row = page.locator('.ant-table-row', { hasText: name })
  await expect(row).toContainText('returned')
  await expect(row).toContainText('Damaged')
  await ctx.close()
})

test('18r: a code that names nothing says so at the desk — and the box keeps focus for the next scan', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('sk') })
  const page = await ctx.newPage()
  await page.goto('/entry/returnables')
  const desk = page.getByTestId('return-scan-input')
  await expect(desk).toBeFocused({ timeout: 20_000 })
  await desk.fill('NO-SUCH-CODE-E2E')
  await desk.press('Enter')
  await expect(page.getByTestId('return-scan-msg')).toContainText('Nothing matches')
  await expect(desk).toHaveAttribute('data-flash', 'error')
  await expect(desk).toHaveValue('')
  await expect(desk).toBeFocused()
  await ctx.close()
})
