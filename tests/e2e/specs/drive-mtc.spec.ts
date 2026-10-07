/**
 * Phase 22c — a certificate from Drive that the pull could not be sure of is
 * confirmed by QC on the Lots page, and only then is it the lot's certificate
 * (rulings Q22-10/11).
 *
 * The E2E stack has no Drive: the spec places the file in the Live API's
 * cache, indexes it, and writes the proposal a pull (or a HOD) would have made.
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

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64')

function cleanup() {
  sql(`DELETE FROM mtc_documents WHERE "SAP_Code" = 'E2E22C-AR'`)
  sql(`DELETE FROM mtc_assignments WHERE "SAP_Code" = 'E2E22C-AR'`)
  sql(`DELETE FROM lots WHERE "SAP_Code" = 'E2E22C-AR'`)
  sql(`DELETE FROM inventory WHERE "SAP_Code" = 'E2E22C-AR'`)
  sql(`DELETE FROM drive_files WHERE drive_id = 'e2e-22c'`)
}

test('22c: QC confirms a proposed Drive certificate on Lots & Expiry; the HOD cannot', async ({ browser }) => {
  const file = path.join(DRIVE_CACHE_DIR, 'mtc', 'e2e22c.png')
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, PNG)
  cleanup()
  sql(`INSERT INTO inventory ("SAP_Code", "Equipment_Description", "Category", "Site_ID")
       VALUES ('E2E22C-AR', 'AR BRICKS 40MM (E2E)', 'Surface Shields', 'CNCEC')`)
  sql(`INSERT INTO lots ("SAP_Code", "Lot_Number", "Site_ID", "Received_Date")
       VALUES ('E2E22C-AR', 'E2E-AR-1', 'CNCEC', '2026-07-02')`)
  sql(`INSERT INTO drive_files (drive_id, kind, name, mime, cache_path, link_status)
       VALUES ('e2e-22c', 'mtc', 'AR BRICK MTC - E2E container.png', 'image/png', '${file}', 'suggested')`)
  sql(`INSERT INTO mtc_assignments (drive_file_id, "SAP_Code", "Lot_Number", "Site_ID", source, status, proposed_by, note)
       VALUES ((SELECT id FROM drive_files WHERE drive_id = 'e2e-22c'), 'E2E22C-AR', 'E2E-AR-1', 'CNCEC', 'manual', 'proposed', 'hod', 'E2E container')`)
  try {
    const hodCtx = await browser.newContext({ storageState: storageStatePath('hod') })
    const hod = await hodCtx.newPage()
    await hod.goto('/lots')
    const hp = hod.getByTestId('mtc-proposed')
    await expect(hp).toContainText('AR BRICK MTC - E2E container.png', { timeout: 20_000 })
    await expect(hp.getByTestId('mtc-confirm')).toHaveCount(0)
    await hodCtx.close()

    const ctx = await browser.newContext({ storageState: storageStatePath('qc') })
    const page = await ctx.newPage()
    await page.goto('/lots')
    const box = page.getByTestId('mtc-proposed')
    await expect(box).toContainText('E2E-AR-1', { timeout: 20_000 })
    await page.screenshot({ path: test.info().outputPath('mtc-proposed.png') })
    await box.getByTestId('mtc-confirm').first().click()
    await expect(page.locator('.ant-message')).toContainText('Certificate filed', { timeout: 10_000 })
    expect(sql(`SELECT count(*) FROM mtc_documents WHERE "SAP_Code" = 'E2E22C-AR' AND "Lot_Number" = 'E2E-AR-1'`)).toBe('1')
    expect(sql(`SELECT status FROM mtc_assignments WHERE "SAP_Code" = 'E2E22C-AR'`)).toBe('confirmed')
    await ctx.close()
  } finally {
    cleanup()
  }
})
