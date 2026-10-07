/**
 * Phase 22b — a receipt opens its delivery note from Drive; a delivery with no
 * DN carries its WD number (rulings Q22-6..9).
 *
 * The E2E stack has no Drive, so the spec does what a pull would: it places a
 * DN image in the Live API's cache folder and indexes it in `drive_files`, and
 * writes two receipts — one naming that DN, one "WD". Records → Receipts must
 * show the paper-clip that opens the image, and the WD number.
 */
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { test, expect } from '@playwright/test'
import { DRIVE_CACHE_DIR, E2E_DB, PG_HOST, PG_PORT, PG_USER, storageStatePath } from '../harness/env'

function sql(q: string): string {
  return execFileSync('psql', ['-h', PG_HOST, '-p', PG_PORT, '-U', PG_USER, '-d', E2E_DB, '-tAc', q],
    { encoding: 'utf-8' }).trim()
}

// a 1×1 PNG
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64')

test('22b: Records → Receipts opens the DN copy from Drive and shows a WD number for a delivery without one', async ({ browser }) => {
  const file = path.join(DRIVE_CACHE_DIR, 'dn', 'e2e22b.png')
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, PNG)
  sql(`DELETE FROM drive_files WHERE drive_id = 'e2e-22b'`)
  sql(`DELETE FROM receipts WHERE "Supplier" LIKE 'E2E22B%'`)
  sql(`INSERT INTO drive_files (drive_id, kind, name, mime, cache_path, parsed_key, link_status)
       VALUES ('e2e-22b', 'dn', 'DN# 77122.png', 'image/png', '${file}', 'dn:77122', 'linked')`)
  sql(`INSERT INTO receipts ("Date", "SAP_Code", "Quantity", "Site_ID", "DN_No", "Supplier")
       VALUES ('2099-01-02', (SELECT "SAP_Code" FROM inventory WHERE "Site_ID" = 'CNCEC' LIMIT 1), 1, 'CNCEC', '77122', 'E2E22B DN'),
              ('2099-01-03', (SELECT "SAP_Code" FROM inventory WHERE "Site_ID" = 'CNCEC' LIMIT 1), 1, 'CNCEC', 'WD', 'E2E22B WD')`)
  const ctx = await browser.newContext({ storageState: storageStatePath('hod') })
  const page = await ctx.newPage()
  try {
    await page.goto('/records/receipts')
    const search = page.getByPlaceholder(/search/i).first()
    await search.fill('E2E22B DN')
    await search.press('Enter')
    const clip = page.getByTestId('dn-open').first()
    await expect(clip).toBeVisible({ timeout: 20_000 })
    await clip.click()
    await expect(page.getByTestId('drive-doc-image')).toBeVisible({ timeout: 15_000 })
    await page.keyboard.press('Escape')
    await search.fill('E2E22B WD')
    await search.press('Enter')
    await expect(page.getByTestId('wd-no').first()).toHaveText(/^WD-CNCEC-\d{4}$/, { timeout: 20_000 })
    await page.screenshot({ path: test.info().outputPath('dn-wd.png') })
  } finally {
    await ctx.close()
    sql(`DELETE FROM receipt_wd WHERE receipt_id IN (SELECT id FROM receipts WHERE "Supplier" LIKE 'E2E22B%')`)
    sql(`DELETE FROM receipts WHERE "Supplier" LIKE 'E2E22B%'`)
    sql(`DELETE FROM drive_files WHERE drive_id = 'e2e-22b'`)
  }
})
