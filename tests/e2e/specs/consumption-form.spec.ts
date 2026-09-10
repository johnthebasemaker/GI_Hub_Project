/**
 * The printed consumption form, in a real browser (Phase 9c).
 *
 * Suite CM proves the PDF, the QR and the registry. What it cannot prove is
 * that a supervisor — the person who actually carries this paper into the
 * plant — can reach it. The endpoint deliberately does NOT live under `/mh`,
 * which is exact-locked to {hod, admin}; putting the download there would have
 * handed the form to everybody except its user.
 */
import { test, expect } from '@playwright/test'
import { storageStatePath } from '../harness/env'

test.describe.configure({ mode: 'serial' })

for (const role of ['supervisor', 'hod', 'sk'] as const) {
  test(`a ${role} can reach the print card on the execution page`,
    async ({ browser }) => {
      const ctx = await browser.newContext({ storageState: storageStatePath(role) })
      const page = await ctx.newPage()
      await page.goto('/execution')
      await expect(page.getByText('Print a consumption form')).toBeVisible()
      // The rule that is least obvious and most likely to be "tidied away".
      await expect(page.getByText(/Every form is a separate numbered sheet/))
        .toBeVisible()
      // ⚠️ Phase 13a. The instruction that stops the workaround this slice
      // exists to remove: a photocopy repeats the QR, and the reader
      // identifies a sheet by exactly that code.
      await expect(page.getByText(/Never photocopy one/)).toBeVisible()
      await ctx.close()
    })
}

test('picking a system downloads a PDF, and a second download is a different sheet',
  async ({ browser }) => {
    const ctx = await browser.newContext({ storageState: storageStatePath('supervisor') })
    const page = await ctx.newPage()
    await page.goto('/execution')

    // antd renders a Select's placeholder as a span, not an input attribute, so
    // scope to the card and take its first combobox.
    const card = page.locator('.ant-card', { hasText: 'Print a consumption form' })
    await card.getByRole('combobox').first().click()
    const first = page.locator('.ant-select-item-option').first()
    await expect(first).toBeVisible()
    await first.click()

    const uuids: string[] = []
    for (let i = 0; i < 2; i += 1) {
      const [dl] = await Promise.all([
        page.waitForEvent('download'),
        card.getByRole('button', { name: 'Download' }).click(),
      ])
      const name = dl.suggestedFilename()
      expect(name).toMatch(/^consumption-.*\.pdf$/)
      uuids.push(name)
    }
    // ⚠️ TWO PRINTS ARE TWO SHEETS. The filename carries the Form_UUID, so two
    // identical names would mean the upload side cannot tell a re-print from a
    // re-photograph — which is the whole basis of duplicate detection in 9d.
    expect(uuids[0]).not.toEqual(uuids[1])
    await ctx.close()
  })

/**
 * Bulk printing, in the browser (Phase 13a).
 *
 * ⚠️ THE NUMBER IS FORMS, NOT SHEETS OF A4, and the helper line is the only
 * place a user ever finds that out. Somebody who wants fifty pieces of paper
 * and types 50 against a 22-material recipe gets a hundred, and they have to
 * see that BEFORE the printer starts rather than after. Suite CY proves the
 * PDF and the registry; what only a browser can prove is that the arithmetic
 * is on screen and moves when the box does.
 */
test('the forms box multiplies out to A4 sheets, live, before printing',
  async ({ browser }) => {
    const ctx = await browser.newContext({ storageState: storageStatePath('supervisor') })
    const page = await ctx.newPage()
    await page.goto('/execution')

    const card = page.locator('.ant-card', { hasText: 'Print a consumption form' })
    await card.getByRole('combobox').first().click()
    const first = page.locator('.ant-select-item-option').first()
    await expect(first).toBeVisible()
    await first.click()

    // One form is the default, and the line already states the arithmetic.
    await expect(card.getByText(/1 form × \d+ page/)).toBeVisible()

    const box = card.getByLabel('How many forms?')
    await box.fill('50')
    await box.blur()
    // ⚠️ The assertion is the MULTIPLICATION, not the number typed. A card that
    // echoed "50" back would look identical and teach nothing.
    await expect(card.getByText(/50 forms × \d+ pages? = \d+ A4 sheets/))
      .toBeVisible()

    const [dl] = await Promise.all([
      page.waitForEvent('download'),
      card.getByRole('button', { name: 'Download' }).click(),
    ])
    // The filename carries the BATCH id and the count for a run, so a
    // supervisor with two runs in a downloads folder can tell them apart.
    expect(dl.suggestedFilename()).toMatch(/^consumption-.*-x50-[0-9A-F]{16}\.pdf$/)
    await ctx.close()
  })

test('the forms box refuses a number the server would refuse anyway',
  async ({ browser }) => {
    const ctx = await browser.newContext({ storageState: storageStatePath('supervisor') })
    const page = await ctx.newPage()
    await page.goto('/execution')

    const card = page.locator('.ant-card', { hasText: 'Print a consumption form' })
    const box = card.getByLabel('How many forms?')
    // ⚠️ CLAMPED IN THE UI, AND STILL VALIDATED ON THE SERVER (suite CY-19).
    // This is the friendly half; the endpoint is the boundary. A UI that were
    // the only guard would be bypassed by anyone who edited the query string,
    // and 500 registry rows is not a cosmetic outcome.
    await box.fill('9999')
    await box.blur()
    await expect(box).toHaveValue('200')
    await box.fill('0')
    await box.blur()
    await expect(box).toHaveValue('1')
    await ctx.close()
  })
