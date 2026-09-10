/**
 * Surface Shield consumption — the Inventory ⇄ SME bridge, in a real browser
 * (Phase 13, Track 3).
 *
 * ⚠️ SUITES DA AND DB PROVE THE MATHS; THIS PROVES THE SCREEN EXISTS AND
 * RENDERS. A React component that throws on mount passes every server test
 * ever written and shows the user a blank page, so the assertions here are
 * deliberately shallow and deliberately about the DOM.
 *
 * ⚠️ AND ONE OF THEM IS A ROLE CHECK. The person who knows how many square
 * metres a drum covered is the SUPERVISOR, and `/sme` is exact-locked to
 * {hod, auditor} — mounting this there would have handed the feature to
 * everybody except its user.
 */
import { test, expect } from '@playwright/test'
import { storageStatePath } from '../harness/env'

test.describe.configure({ mode: 'serial' })

for (const role of ['supervisor', 'sk', 'hod'] as const) {
  test(`a ${role} sees the Surface Shield consumption card on /execution`,
    async ({ browser }) => {
      const ctx = await browser.newContext({ storageState: storageStatePath(role) })
      const page = await ctx.newPage()
      const errors: string[] = []
      page.on('pageerror', (e) => errors.push(String(e)))

      await page.goto('/execution')
      await expect(
        page.getByText('Surface Shield consumption — what it was used for'))
        .toBeVisible()
      // Both halves of the workflow are on screen: what still needs an area,
      // and what is waiting on the HOD.
      await expect(page.getByText(/Needs an area/)).toBeVisible()
      await expect(page.getByText(/Awaiting the HOD/)).toBeVisible()

      // ⚠️ THE SENTENCE THAT STOPS THE WHOLE SCREEN BEING MISREAD. "Recording
      // consumption" sounds like it deducts something, and this does not —
      // the material left the store when it was issued.
      await expect(page.getByText(/Recording an area moves no stock/)).toBeVisible()

      expect(errors, `uncaught page errors: ${errors.join(' | ')}`).toHaveLength(0)
      await ctx.close()
    })
}

test('the HOD tab explains that every row is reviewed, not just the outliers',
  async ({ browser }) => {
    const ctx = await browser.newContext({ storageState: storageStatePath('hod') })
    const page = await ctx.newPage()
    await page.goto('/execution')
    await page.getByText(/Awaiting the HOD/).click()
    // ⚠️ Ruling Q13-8, on the screen: the band sets PRIORITY, never approval.
    // A reader who thinks the tolerance gates approval will not understand why
    // a 0% row is in their queue.
    await expect(page.getByText(/Every.{0,3} Surface Shield consumption is reviewed/))
      .toBeVisible()
    await expect(page.getByText(/never whether a decision is needed/)).toBeVisible()
    await ctx.close()
  })

test('a role outside the execution workflow cannot reach the queue API',
  async ({ request }) => {
    // The card is inside a nav-guarded page; this is the endpoint behind it.
    const r = await request.get('/api/execution/sme-link/queue')
    expect([401, 403]).toContain(r.status())
  })
