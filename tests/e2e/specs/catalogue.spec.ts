/**
 * Phase 23d — the catalogue with pictures (rulings Q23-5..9).
 *
 * Fixture (this spec): two GI codes in the catalogue, neither stocked.
 */
import { execFileSync } from 'node:child_process'
import { deflateSync } from 'node:zlib'
import { test, expect } from '@playwright/test'
import { E2E_DB, PG_HOST, PG_PORT, PG_USER, storageStatePath } from '../harness/env'

function sql(q: string): string {
  return execFileSync('psql', ['-h', PG_HOST, '-p', PG_PORT, '-U', PG_USER, '-d', E2E_DB, '-tAc', q],
    { encoding: 'utf-8' }).trim()
}

/** A small solid-colour PNG, built here so the spec carries no binary file. */
function png(w = 64, h = 48): Buffer {
  const crcTable = Array.from({ length: 256 }, (_, n) => {
    let c = n
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    return c >>> 0
  })
  const crc = (b: Buffer) => { let c = 0xffffffff; for (const x of b) c = crcTable[(c ^ x) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0 }
  const chunk = (type: string, data: Buffer) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length)
    const td = Buffer.concat([Buffer.from(type), data])
    const c = Buffer.alloc(4); c.writeUInt32BE(crc(td))
    return Buffer.concat([len, td, c])
  }
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2
  const raw = Buffer.alloc((w * 3 + 1) * h)
  for (let y = 0; y < h; y += 1) for (let x = 0; x < w; x += 1) raw.set([30, 90, 160], y * (w * 3 + 1) + 1 + x * 3)
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))])
}

function cleanup() {
  sql(`DELETE FROM item_images WHERE item_key IN ('GI-7999401', 'GI-7999402')`)
  sql(`DELETE FROM material_catalog WHERE "Material_Code" IN ('GI-7999401', 'GI-7999402')`)
}

test.describe.configure({ mode: 'serial' })

test('23d: the HOD adds a picture to a catalogue code; it shows in the list', async ({ browser }) => {
  cleanup()
  sql(`INSERT INTO material_catalog ("Material_Code", description, uom, series)
       VALUES ('GI-7999401', 'E2E23D HAND TROWEL', 'EA', '7'), ('GI-7999402', 'E2E23D JUBILEE CLAMP', 'EA', '7')`)
  const ctx = await browser.newContext({ storageState: storageStatePath('hod') })
  const page = await ctx.newPage()
  try {
    await page.goto('/catalogue')
    await page.getByTestId('catalogue-search').locator('input').fill('E2E23D')
    await page.getByTestId('catalogue-search').locator('input').press('Enter')
    const row = page.getByTestId('catalogue-materials').locator('tr', { hasText: 'E2E23D HAND TROWEL' })
    await expect(row).toBeVisible({ timeout: 20_000 })
    await expect(row).toContainText('needs one')
    await row.click()
    const drawer = page.getByTestId('catalogue-editor')
    await expect(drawer).toContainText('Not stocked at GI Hub yet')
    await drawer.locator('input[type=file]').setInputFiles({ name: 'trowel.png', mimeType: 'image/png', buffer: png() })
    await expect(drawer.getByTestId('catalogue-image')).toHaveCount(1, { timeout: 20_000 })
    await expect(drawer.getByTestId('catalogue-image')).toContainText('main')
    await page.screenshot({ path: test.info().outputPath('catalogue-editor.png') })
    await page.keyboard.press('Escape')
    await expect(row.locator('img')).toBeVisible()
    await expect(row).not.toContainText('needs one')
  } finally {
    await ctx.close()
  }
})

test('23d: the store keeper sees the picture but cannot change it (Q23-8)', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('sk') })
  const page = await ctx.newPage()
  try {
    await page.goto('/catalogue')
    await page.getByTestId('catalogue-search').locator('input').fill('E2E23D HAND')
    await page.getByTestId('catalogue-search').locator('input').press('Enter')
    const row = page.getByTestId('catalogue-materials').locator('tr', { hasText: 'E2E23D HAND TROWEL' })
    await expect(row.locator('img')).toBeVisible({ timeout: 20_000 })
    await row.click()
    await expect(page.getByTestId('catalogue-image')).toHaveCount(1)
    await expect(page.getByTestId('catalogue-upload')).toHaveCount(0)
  } finally {
    await ctx.close()
  }
})

test('23d: a HOD can raise a PR for a catalogue item GI Hub does not stock (Q23-5)', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('hod') })
  const page = await ctx.newPage()
  try {
    await page.goto('/hod/prs')
    await page.getByRole('tab', { name: 'Create PR' }).click()
    const picker = page.getByTestId('pr-item').first()
    await picker.click()
    await page.keyboard.type('E2E23D JUBILEE')
    const opt = page.locator('.ant-select-item-option', { hasText: 'GI-7999402' })
    await expect(opt).toBeVisible({ timeout: 20_000 })
    await expect(opt).toContainText('not stocked yet')
    await opt.click()
    await page.getByPlaceholder('Qty').first().fill('2')
    await page.getByRole('button', { name: 'Create PR' }).click()
    await expect(page.getByText(/PR .* created \(1 line/)).toBeVisible({ timeout: 20_000 })
    expect(sql(`SELECT count(*) FROM pr_master WHERE "Material_Code" = 'GI-7999402' AND "SAP_Code" = ''`)).toBe('1')
  } finally {
    sql(`DELETE FROM pr_master WHERE "Material_Code" = 'GI-7999402'`)
    await ctx.close()
    cleanup()
  }
})
