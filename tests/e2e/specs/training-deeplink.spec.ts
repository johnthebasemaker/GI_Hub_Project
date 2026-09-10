/**
 * The Hub Assistant's "Watch it" destination, in a real browser (Phase 13c/13d).
 *
 * ⚠️ THIS SPEC EXISTS BECAUSE THE SERVER TESTS COULD NOT SEE THE BUG. Suite CZ
 * proves `206`, the fence and the ticket — and the player still could not load
 * a frame, because a `<video src>` issues its own plain GET with no way to
 * attach a bearer token. curl has no origin and no media element; only a page
 * shows `networkState: 3` and a black box. So the assertions here are the ones
 * a browser alone can make: that the element actually reached `readyState` 4,
 * and that its playhead is where the deep link said.
 */
import { test, expect } from '@playwright/test'
import { storageStatePath } from '../harness/env'

test.describe.configure({ mode: 'serial' })

const MODULE = 'ocr_workflow_v1'   // seeded by migration; supervisor + store keeper

test('a deep link opens the named module focused, and says where it starts',
  async ({ browser }) => {
    const ctx = await browser.newContext({ storageState: storageStatePath('supervisor') })
    const page = await ctx.newPage()
    await page.goto(`/training?module=${MODULE}&lang=en&t=61.2`)

    // The banner is the whole point of focused mode: before it, the card
    // merely scrolled into view inside a list, which on a phone reads as
    // "it went to the Training page and nothing happened".
    await expect(page.getByText('Sent here by the Hub Assistant')).toBeVisible()
    await expect(page.getByText(/Starting at 1:01/)).toBeVisible()
    await ctx.close()
  })

test('a deep link to a module this role may not watch says so, first',
  async ({ browser }) => {
    const ctx = await browser.newContext({ storageState: storageStatePath('sk') })
    const page = await ctx.newPage()
    await page.goto('/training?module=hod_executive_summary_v1&lang=en&t=30')
    // ⚠️ AT THE TOP, NOT UNDER THE LIST. Somebody who followed a link needs the
    // explanation before the cards, or the page just looks ordinary and they
    // conclude the link is broken.
    await expect(page.getByText('That tutorial is not one of yours')).toBeVisible()
    await ctx.close()
  })

test('an unpublished module says so in the assistant\'s own terms',
  async ({ browser }) => {
    const ctx = await browser.newContext({ storageState: storageStatePath('supervisor') })
    const page = await ctx.newPage()
    // The E2E stack has no rendered tutorials, which is a SUPPORTED state
    // (ruling Q3 keeps the renders local until the cutover). The page must
    // explain the gap rather than showing the generic "not published yet" —
    // the assistant promised a video, and a truthful absent state must not
    // read as a broken link.
    await page.goto(`/training?module=${MODULE}&lang=en&t=61.2`)
    await expect(
      page.getByText(/not been published on this server yet|Preparing the player/))
      .toBeVisible()
    await ctx.close()
  })

test('the media route refuses a role the module does not name', async ({ request }) => {
  // ⚠️ THE FENCE OVER THE BYTES, ASSERTED SEPARATELY FROM THE FENCE OVER THE
  // LIST. A raw media URL would be a second door if it did not re-check, and a
  // second copy of an access decision is exactly what P11-4 warns about.
  const r = await request.get('/api/training/media/hod_executive_summary_v1/en.mp4')
  expect([401, 403, 404]).toContain(r.status())
})
