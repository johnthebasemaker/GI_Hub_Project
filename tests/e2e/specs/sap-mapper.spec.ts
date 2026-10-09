/**
 * Phase 23c — "Needs a SAP code" on Requests & Pending (rulings Q23-4/5).
 *
 * Fixture (this spec): one request with two lines the workbook gave no SAP —
 * a trowel written only by description, and a GI-coded clamp GI Hub does not
 * stock — and an item-master trowel for the matcher to find.
 */
import { execFileSync } from 'node:child_process'
import { test, expect } from '@playwright/test'
import { E2E_DB, PG_HOST, PG_PORT, PG_USER, storageStatePath } from '../harness/env'

function sql(q: string): string {
  return execFileSync('psql', ['-h', PG_HOST, '-p', PG_PORT, '-U', PG_USER, '-d', E2E_DB, '-tAc', q],
    { encoding: 'utf-8' }).trim()
}

function cleanup() {
  sql(`DELETE FROM material_request_lines WHERE request_id IN (SELECT id FROM material_requests WHERE drive_file_id = -2323)`)
  sql(`DELETE FROM material_requests WHERE drive_file_id = -2323`)
  sql(`DELETE FROM request_sap_map WHERE "Site_ID" = 'CNCEC' AND written_example LIKE 'E2E23C%'`)
  sql(`DELETE FROM request_sap_map WHERE "Site_ID" = 'CNCEC' AND written_key IN ('desc:e2e23c garden trowel hand showel', 'code:GI-7999023')`)
  sql(`DELETE FROM inventory WHERE "SAP_Code" = 'E2E23C-TRW'`)
}

test.describe.configure({ mode: 'serial' })

test('23c: the HOD links a request name to an item, parks a GI code as "not stocked yet", and undoes', async ({ browser }) => {
  cleanup()
  sql(`INSERT INTO inventory ("SAP_Code", "Material_Code", "Equipment_Description", "Category", "Site_ID", "Minimum_Qty", "Opening_Stock")
       VALUES ('E2E23C-TRW', NULL, 'E2E23C TROWEL GARDEN HAND', 'Tools', 'CNCEC', 0, 0)`)
  const rid = sql(`INSERT INTO material_requests (drive_file_id, file_name, request_date, layout, is_summary, "Site_ID")
       VALUES (-2323, 'Request E2E23C.xlsx', CURRENT_DATE - 3, 'x', false, 'CNCEC') RETURNING id`).split('\n')[0]
  for (const [row, code, desc] of [[1, 'N/A', 'E2E23C Garden Trowel (Hand Showel)'], [2, 'GI-7999023', 'E2E23C Jublee Clamp']] as const) {
    sql(`INSERT INTO material_request_lines (request_id, sheet, row_no, "SAP_Code", "Material_Code", description, uom,
         requested_qty, without_pr, request_date) VALUES (${rid}, 'S', ${row}, NULL, '${code}', '${desc}', 'EA', 3, true, CURRENT_DATE - 3)`)
  }
  const ctx = await browser.newContext({ storageState: storageStatePath('hod') })
  const page = await ctx.newPage()
  try {
    await page.goto('/requests-pending')
    const mapper = page.getByTestId('req-needs-sap')
    const trowel = mapper.locator('tr', { hasText: 'E2E23C Garden Trowel' })
    await expect(trowel).toBeVisible({ timeout: 20_000 })
    // the matcher's best guess is offered; one click fills the box
    await trowel.getByTestId('sap-suggestion').filter({ hasText: 'E2E23C-TRW' }).click()
    await trowel.getByTestId('sap-link').click()
    await expect(page.getByText(/linked to E2E23C-TRW/)).toBeVisible()
    await expect(mapper.locator('tr', { hasText: 'E2E23C Garden Trowel' })).toHaveCount(0)

    const clamp = mapper.locator('tr', { hasText: 'E2E23C Jublee Clamp' })
    await clamp.getByTestId('sap-not-stocked').click()
    await expect(page.getByText(/not stocked yet/).first()).toBeVisible()

    await mapper.locator('.ant-segmented-item', { hasText: 'Decided' }).click()
    const decided = mapper.locator('tr', { hasText: 'E2E23C Garden Trowel' })
    await expect(decided).toContainText('linked')
    await page.screenshot({ path: test.info().outputPath('sap-mapper.png') })
    await decided.getByTestId('sap-undo').click()
    await page.getByRole('button', { name: 'OK' }).click()
    await expect(page.getByText(/Undone/)).toBeVisible()
    await mapper.locator('.ant-segmented-item', { hasText: 'Needs a SAP code' }).click()
    await expect(mapper.locator('tr', { hasText: 'E2E23C Garden Trowel' })).toBeVisible()
  } finally {
    await ctx.close()
  }
})

test('23c: the store keeper sees the list but cannot decide (Q23-4)', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('sk') })
  const page = await ctx.newPage()
  try {
    await page.goto('/requests-pending')
    const mapper = page.getByTestId('req-needs-sap')
    await expect(mapper.locator('tr', { hasText: 'E2E23C Garden Trowel' })).toBeVisible({ timeout: 20_000 })
    await expect(mapper.getByTestId('sap-link')).toHaveCount(0)
    await expect(mapper.getByText(/Admin, HOD and Logistics decide/)).toBeVisible()
  } finally {
    await ctx.close()
    cleanup()
  }
})
