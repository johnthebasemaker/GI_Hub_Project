/**
 * Phase 22e — a consumption paper that is ALREADY in the workbook is compared,
 * not staged (ruling Q22-19); "(Night)" by the date gives the site's Night
 * preparer (Q22-14/16). Paste lane — no vision model needed.
 *
 * Fixture (this spec): the site's preparers, and two workbook rows of the
 * paper's date prepared by the Night name — one that matches the paper, one
 * whose quantity differs.
 */
import { execFileSync } from 'node:child_process'
import { test, expect } from '@playwright/test'
import { E2E_DB, PG_HOST, PG_PORT, PG_USER, storageStatePath } from '../harness/env'

function sql(q: string): string {
  return execFileSync('psql', ['-h', PG_HOST, '-p', PG_PORT, '-U', PG_USER, '-d', E2E_DB, '-tAc', q],
    { encoding: 'utf-8' }).trim()
}

const d = new Date(Date.now() - 86_400_000)
const ISO = d.toISOString().slice(0, 10)
const DMY = `${ISO.slice(8, 10)}/${ISO.slice(5, 7)}/${ISO.slice(2, 4)}`

function cleanup() {
  sql(`DELETE FROM consumption WHERE "Source_Ref" LIKE 'XLSX:CNCEC:consumption:E2E22E%'`)
  sql(`DELETE FROM app_settings WHERE key = 'consumption_preparers:CNCEC'`)
}

test('22e: a paper already in the workbook is COMPARED line by line, never staged; Night → the Night preparer', async ({ browser }) => {
  cleanup()
  sql(`INSERT INTO app_settings (key, value) VALUES ('consumption_preparers:CNCEC',
       '[{"from": "", "day": "E2E Day", "night": "E2E Night"}]')`)
  for (const [n, sap, qty] of [[5, 'E2EOCR-1', 2], [6, 'E2EOCR-2', 1]] as const) {
    sql(`INSERT INTO consumption ("Date", "SAP_Code", "Quantity", "Site_ID", "Work_Type", "Issued_To",
         "Issued_By", "Prepared_By", "Source_Ref", "Source_Sheet", "Source_Row", "Item_Type")
         VALUES ('${ISO}', '${sap}', ${qty}, 'CNCEC', 'R/L', 'Aria', 'E2E Night', 'E2E Night',
                 'XLSX:CNCEC:consumption:E2E22E:${sap}:${n}', 'Consumption Log', ${n}, 'Safety')`)
  }
  const ctx = await browser.newContext({ storageState: storageStatePath('sk') })
  const page = await ctx.newPage()
  try {
    await page.goto('/entry/ocr')
    const paste = page.getByPlaceholder(/Imran/)
    await expect(paste).toBeVisible({ timeout: 20_000 })
    await paste.fill(`Date: ${DMY} (Night)\nAria, E2EOCR DUST Mask, EA, 2, R/L\nAria, E2EOCR LEATHER GLOVES, Pair, 3, R/L`)
    await page.getByRole('button', { name: 'Parse' }).click()
    await expect(page.getByTestId('ocr-shift-tag')).toContainText('Night shift', { timeout: 20_000 })
    await expect(page.getByTestId('ocr-prepared-by')).toHaveValue('E2E Night')
    const banner = page.getByTestId('ocr-compare')
    await expect(banner).toContainText('already in the workbook', { timeout: 20_000 })
    await expect(banner).toContainText('1 same')
    await expect(banner).toContainText('1 differ')
    await expect(page.getByTestId('ocr-wb').first()).toContainText('row 5')
    await expect(page.getByTestId('ocr-wb').nth(1)).toContainText('quantity: paper 3 · workbook 1')
    const stage = page.getByTestId('ocr-stage')
    await expect(stage).toBeDisabled()
    await expect(stage).toContainText('Already in the workbook')
    await page.screenshot({ path: test.info().outputPath('ocr-compare.png') })
    // flipping the shift to Day points at the Day preparer — no workbook block → staging again
    await page.getByTestId('ocr-shift-tag').click()
    await expect(page.getByTestId('ocr-prepared-by')).toHaveValue('E2E Day')
    await expect(page.getByTestId('ocr-compare')).toHaveCount(0, { timeout: 15_000 })
  } finally {
    await ctx.close()
    cleanup()
  }
})
