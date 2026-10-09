/**
 * Phase 23b — "Tank for this whole page" (ruling Q23-2). Paste lane — no
 * vision model needed.
 *
 * Fixture (this spec): consumption lines of the 7 days before the paper's date
 * naming tank E2E-TNK-7, and a learned tank spelling E2EKEEP → E2E-KEEP-1.
 * The paper: a garbled first tank, a ditto under it, and a row whose own
 * written tank is matched. The page tank fills the first two and never the
 * third; Undo puts the first two back.
 */
import { execFileSync } from 'node:child_process'
import { test, expect } from '@playwright/test'
import { E2E_DB, PG_HOST, PG_PORT, PG_USER, storageStatePath } from '../harness/env'

function sql(q: string): string {
  return execFileSync('psql', ['-h', PG_HOST, '-p', PG_PORT, '-U', PG_USER, '-d', E2E_DB, '-tAc', q],
    { encoding: 'utf-8' }).trim()
}

const paperDay = new Date(Date.now() - 86_400_000)
const ISO = paperDay.toISOString().slice(0, 10)
const DMY = `${ISO.slice(8, 10)}/${ISO.slice(5, 7)}/${ISO.slice(2, 4)}`
const before = (n: number) => new Date(paperDay.getTime() - n * 86_400_000).toISOString().slice(0, 10)

function cleanup() {
  sql(`DELETE FROM consumption WHERE "Source_Ref" LIKE 'E2E23B%'`)
  sql(`DELETE FROM sme_tank_alias WHERE "Site_ID" = 'CNCEC' AND alias_raw = 'E2EKEEP'`)
}

test('23b: the page tank fills ditto / blank / unknown rows only, and Undo puts them back', async ({ browser }) => {
  cleanup()
  for (const [n, day] of [[1, before(2)], [2, before(3)], [3, before(5)]] as const) {
    sql(`INSERT INTO consumption ("Date", "SAP_Code", "Quantity", "Site_ID", "Tank_No", "Source_Ref")
         VALUES ('${day}', 'E2EOCR-1', 1, 'CNCEC', 'E2E-TNK-7', 'E2E23B:${n}')`)
  }
  sql(`INSERT INTO sme_tank_alias ("Site_ID", alias_raw, alias_norm, "Equipment_Tag_No", status)
       VALUES ('CNCEC', 'E2EKEEP', 'E2EKEEP', 'E2E-KEEP-1', 'mapped')`)
  const ctx = await browser.newContext({ storageState: storageStatePath('sk') })
  const page = await ctx.newPage()
  try {
    await page.goto('/entry/ocr')
    const paste = page.getByPlaceholder(/Imran/)
    await expect(paste).toBeVisible({ timeout: 20_000 })
    await paste.fill(`Date: ${DMY}\n`
      + 'Aria, E2EOCR DUST Mask, EA, 2, R/L, Zq9-Tnk-0o\n'
      + 'Aria, E2EOCR LEATHER GLOVES, Pair, 3, R/L, ″\n'
      + 'Aria, E2EOCR TAPE, EA, 1, R/L, E2EKEEP')
    await page.getByRole('button', { name: 'Parse' }).click()
    const tanks = page.getByTestId('ocr-tank')
    await expect(tanks.nth(2)).toContainText('E2E-KEEP-1', { timeout: 20_000 })

    const pick = page.getByTestId('ocr-page-tank-pick')
    await expect(pick).toBeVisible()
    await pick.click()
    await page.locator('.ant-select-item-option', { hasText: 'E2E-TNK-7' }).first().click()
    await expect(tanks.nth(0)).toContainText('E2E-TNK-7')
    await expect(tanks.nth(1)).toContainText('E2E-TNK-7')
    // a tank written AND matched on its own row is never changed
    await expect(tanks.nth(2)).toContainText('E2E-KEEP-1')
    await expect(tanks.nth(2)).not.toContainText('E2E-TNK-7')
    await page.screenshot({ path: test.info().outputPath('ocr-page-tank.png') })

    await page.getByTestId('ocr-page-tank-undo').click()
    await expect(tanks.nth(0)).not.toContainText('E2E-TNK-7')
    await expect(tanks.nth(1)).not.toContainText('E2E-TNK-7')
    await expect(tanks.nth(2)).toContainText('E2E-KEEP-1')
  } finally {
    await ctx.close()
    cleanup()
  }
})
