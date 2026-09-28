/**
 * Rule 17 — Live | Practice.
 *
 * Practice is a SECOND API process on its own database (global-setup step 2b/3b
 * builds it with the shipping tool). These specs prove the three things a
 * trainee and an operator actually depend on:
 *
 *   1. the login toggle routes to the sandbox, and what the page SAYS it is
 *      comes from the server, not from the toggle (vector V10);
 *   2. work done in Practice lands in Practice and nowhere else — asserted in
 *      BOTH databases, because "it is in Practice" alone does not prove "it is
 *      not in Live";
 *   3. an entry queued OFFLINE in Practice is never replayed into Live after
 *      the browser switches and signs in there (vector V4) — the one path no
 *      server-side wall can see, because the replay would arrive under a valid
 *      Live session.
 */
import { execFileSync } from 'node:child_process'
import { expect, request, test } from '@playwright/test'
import { tokenFor } from '../harness/api'
import {
  API_URL, E2E_DB, E2E_PASSWORD, PG_HOST, PG_PORT, PG_USER, PRACTICE_API_URL,
  PRACTICE_ADMIN_PASSWORD, PRACTICE_DB, PRACTICE_PASSWORD, USERS, apiHeaders,
} from '../harness/env'

function sql(db: string, q: string): string {
  return execFileSync('psql', ['-h', PG_HOST, '-p', PG_PORT, '-U', PG_USER, '-d', db, '-tAc', q],
    { encoding: 'utf-8' }).trim()
}

async function signIn(page: import('@playwright/test').Page, user: string, pw: string) {
  await page.getByPlaceholder('Username').fill(user)
  await page.getByPlaceholder('Password').fill(pw)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('button', { name: /sign out/i })).toBeVisible({ timeout: 20_000 })
}

async function choose(page: import('@playwright/test').Page, env: 'Live' | 'Practice') {
  await page.locator('.gi-env-switch').getByText(env, { exact: true }).click()
  await page.waitForLoadState('load')
  await expect(page.locator('.gi-env-switch .ant-segmented-item-selected')).toHaveText(env)
}

test('Live is the default, and no Practice banner is painted on it', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.gi-env-switch .ant-segmented-item-selected')).toHaveText('Live')
  await expect(page.locator('.gi-practice-banner')).toHaveCount(0)
  await expect(page.locator('.gi-practice-badge')).toHaveCount(0)
  await expect(page).not.toHaveTitle(/^PRACTICE/)
  // Live keeps its gold primary (Phase 15c changes Practice only)
  expect(await page.getByRole('button', { name: 'Sign in' }).evaluate(
    (el) => getComputedStyle(el).backgroundColor)).toBe('rgb(212, 175, 55)')
})

test('15c: Practice is violet, with a pulsing PRACTICE badge top-left on the login and beside the logo',
  async ({ page }) => {
    await page.goto('/')
    await choose(page, 'Practice')
    const floating = page.locator('.gi-practice-badge--float')
    await expect(floating).toBeVisible()
    const box = await floating.boundingBox()
    expect(box!.x).toBeLessThan(40)
    expect(box!.y).toBeLessThan(40)
    expect(await floating.evaluate((el) => getComputedStyle(el).animationName)).toBe('gi-practice-glow')
    expect(await page.getByRole('button', { name: 'Sign in' }).evaluate(
      (el) => getComputedStyle(el).backgroundColor)).toBe('rgb(142, 108, 239)')
    await signIn(page, 'practice.hod', PRACTICE_PASSWORD)
    const badge = page.locator('.gi-sider .gi-practice-badge')
    await expect(badge).toBeVisible()
    expect(await badge.evaluate((el) => getComputedStyle(el).animationName)).toBe('gi-practice-glow')
    expect(await page.locator('.gi-sider').evaluate((el) => getComputedStyle(el).backgroundImage))
      .toContain('rgb(42, 27, 61)')
  })

test('15c: under reduced motion the Practice badge stands still', async ({ browser }) => {
  const ctx = await browser.newContext({ reducedMotion: 'reduce' })
  const page = await ctx.newPage()
  await page.goto('/')
  await choose(page, 'Practice')
  const floating = page.locator('.gi-practice-badge--float')
  await expect(floating).toBeVisible()
  expect(await floating.evaluate((el) => getComputedStyle(el).animationName)).toBe('none')
  await ctx.close()
})

test('Practice: the server declares itself, a shared account signs in, and an approval lands only in the sandbox',
  async ({ page }) => {
    await page.goto('/')
    await choose(page, 'Practice')
    // The banner is driven by GET /instance on the Practice API — the SERVER.
    await expect(page.locator('.gi-practice-banner')).toContainText('PRACTICE')
    await signIn(page, 'practice.hod', PRACTICE_PASSWORD)
    await expect(page.locator('.gi-practice-tag')).toBeVisible()
    await expect(page).toHaveTitle(/^PRACTICE · /)
    const stored = await page.evaluate(() => ({
      env: localStorage.getItem('gi_env'),
      live: localStorage.getItem('gi_token'),
      practice: localStorage.getItem('gi_token@training'),
    }))
    expect(stored.env).toBe('training')
    expect(stored.practice).toBeTruthy()
    expect(stored.live).toBeNull()   // a Practice session never writes Live's key

    // Approve one of the overlay's seeded issues through the Practice API.
    const p = await request.newContext({ baseURL: PRACTICE_API_URL, extraHTTPHeaders: apiHeaders('61') })
    const tok = (await (await p.post('/auth/login',
      { data: { username: 'practice.hod', password: PRACTICE_PASSWORD } })).json()).access_token as string
    const H = { Authorization: `Bearer ${tok}`, 'X-GI-Instance': 'training' }
    const listed = await (await p.get('/hod/pending/issues', { headers: H })).json() as
      { items?: Record<string, unknown>[] } | Record<string, unknown>[]
    const rows = Array.isArray(listed) ? listed : listed.items ?? []
    const seeded = rows.find((r) => String(r.Remarks ?? '').startsWith('Practice seed')
      && r.Issued_To === 'Aria Bellweather')
    expect(seeded, 'the overlay seeded an issue for the HOD to practise on').toBeTruthy()

    const q = `SELECT count(*) FROM consumption WHERE "Issued_To" = 'Aria Bellweather'`
    const practiceBefore = Number(sql(PRACTICE_DB, q))
    const liveBefore = Number(sql(E2E_DB, q))
    const ok = await p.post(`/hod/pending/issues/${seeded!.id}/approve`, { headers: H, data: {} })
    expect(ok.status(), await ok.text()).toBeLessThan(300)
    expect(Number(sql(PRACTICE_DB, q))).toBe(practiceBefore + 1)
    // …and NOT in Live. A synthetic name that the collision check proved is
    // absent from the real register, so any row here would be contamination.
    expect(liveBefore).toBe(0)
    expect(Number(sql(E2E_DB, q))).toBe(0)

    // Tokens do not cross, in either direction, and a mis-declared request is refused.
    const live = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: apiHeaders('62') })
    expect((await live.get('/auth/me', { headers: { Authorization: `Bearer ${tok}` } })).status()).toBe(401)
    expect((await p.get('/auth/me', { headers: { Authorization: `Bearer ${tokenFor('hod')}` } })).status()).toBe(401)
    expect((await live.get('/auth/me', {
      headers: { Authorization: `Bearer ${tokenFor('hod')}`, 'X-GI-Instance': 'training' },
    })).status()).toBe(409)
    expect((await live.post('/practice/reset', {
      headers: { Authorization: `Bearer ${tokenFor('admin')}` }, data: { confirm: 'RESET PRACTICE DATA' },
    })).status()).toBe(404)
    await p.dispose()
    await live.dispose()
  })

test('V4: an entry queued offline in Practice is never replayed into Live', async ({ page, context }) => {
  const marker = `PRACTICE-OFFLINE-${Date.now()}`
  await page.goto('/')
  await choose(page, 'Practice')
  await signIn(page, 'practice.storekeeper', PRACTICE_PASSWORD)
  await page.goto('/entry/receive')
  await page.waitForFunction(() => '__giOffline' in window)

  type Off = {
    __giOffline: {
      post: (p: string, b: unknown, h: Record<string, string>) => Promise<unknown>
      flush: () => Promise<{ sent: number; failed: string[] }>
      count: () => Promise<number>
      list: () => Promise<{ env?: string }[]>
    }
  }
  await context.setOffline(true)
  const queued = await page.evaluate(([m]) => (window as unknown as Off).__giOffline.post(
    '/entry/receipts',
    { Date: new Date().toISOString().slice(0, 10), SAP_Code: '899001', Quantity: 3,
      Site_ID: 'CNCEC', Supplier: m, wbs: 'WBS-9001' },
    {}), [marker])
  expect(queued).toEqual({ queued: true })
  const stamped = await page.evaluate(() => (window as unknown as Off).__giOffline.list())
  expect(stamped.map((e) => e.env)).toEqual(['training'])
  // Leave Practice WITHOUT coming back online in it (the 'online' event would
  // legitimately flush it into Practice and prove nothing).
  await page.evaluate(() => localStorage.removeItem('gi_env'))
  await page.close()
  await context.setOffline(false)

  // A new page in the SAME browser: same localStorage, same IndexedDB.
  const live = await context.newPage()
  await live.goto('/')
  await expect(live.locator('.gi-env-switch .ant-segmented-item-selected')).toHaveText('Live')
  await signIn(live, USERS.sk, E2E_PASSWORD)
  await live.goto('/entry/receive')
  await live.waitForFunction(() => '__giOffline' in window)
  const liveCount = await live.evaluate(() => (window as unknown as Off).__giOffline.count())
  const flushed = await live.evaluate(() => (window as unknown as Off).__giOffline.flush())
  expect(liveCount).toBe(0)               // Live cannot even SEE Practice's queue
  expect(flushed.sent).toBe(0)
  expect(Number(sql(E2E_DB,
    `SELECT count(*) FROM pending_receipts WHERE "Supplier" = '${marker}'`))).toBe(0)

  // …and the Practice entry was not dropped: back in Practice it is still
  // queued, and it lands where it was made.
  // ONE page load: the app flushes its queue at boot. (A second navigation
  // mid-flush is now harmless — the replay carries the entry's Idempotency-Key
  // and is answered, not re-staged; offline-queue.spec.ts proves that — but a
  // single load keeps this test about environments.) The Practice session is
  // still in `gi_token@training`: this browser left Practice without signing out.
  await live.evaluate(() => localStorage.setItem('gi_env', 'training'))
  await live.goto('/entry/receive')
  await live.waitForFunction(() => '__giOffline' in window)
  await expect.poll(async () => Number(sql(PRACTICE_DB,
    `SELECT count(*) FROM pending_receipts WHERE "Supplier" = '${marker}'`)), { timeout: 15_000 })
    .toBe(1)
  expect(Number(sql(E2E_DB,
    `SELECT count(*) FROM pending_receipts WHERE "Supplier" = '${marker}'`))).toBe(0)
})

test('15a: an item the Practice admin adds reaches the store keeper and the HOD — and never Live',
  async ({ page }) => {
    // Found 2026-09-27: both Practice databases were six migrations behind, so
    // /inventory 500'd and a new item looked "not saved"; and a site typed as
    // `cncec` hid it from every CNCEC store keeper even once the list loaded.
    const sap = `P15A${Date.now() % 1_000_000}`
    const p = await request.newContext({ baseURL: PRACTICE_API_URL, extraHTTPHeaders: apiHeaders('63') })
    const login = async (u: string, pw: string) => (await (await p.post('/auth/login',
      { data: { username: u, password: pw } })).json()).access_token as string
    const H = (t: string) => ({ Authorization: `Bearer ${t}`, 'X-GI-Instance': 'training' })
    const admin = await login('practice.admin', PRACTICE_ADMIN_PASSWORD)
    const made = await p.post('/admin/inventory', {
      headers: H(admin),
      data: { SAP_Code: sap, Equipment_Description: 'Phase 15a practice item', UOM: 'Each',
              Site_ID: 'cncec', Category: 'safety' },
    })
    expect(made.status(), await made.text()).toBe(201)
    expect(sql(PRACTICE_DB, `SELECT "Site_ID" || '|' || "Category" FROM inventory WHERE "SAP_Code" = '${sap}'`))
      .toBe('CNCEC|Safety')
    expect(Number(sql(E2E_DB, `SELECT count(*) FROM inventory WHERE "SAP_Code" = '${sap}'`))).toBe(0)

    // Every read that 500'd on the stale schema answers for the roles that use it.
    const hod = await login('practice.hod', PRACTICE_PASSWORD)
    for (const path of ['/inventory?limit=5', '/meta/unit-sizes', '/announcements/whats-new',
      '/execution/sme-link/groups/staged', '/sme/actuals/reconciliation', '/stock/excel-check']) {
      expect((await p.get(path, { headers: H(hod) })).status(), path).toBe(200)
    }
    expect((await p.get('/health')).status()).toBe(200)
    expect(((await (await p.get('/health')).json()) as { schema?: string }).schema).toBe('ok')
    await p.dispose()

    // …and the store keeper can pick it on the Issue form.
    await page.goto('/')
    await choose(page, 'Practice')
    await signIn(page, 'practice.storekeeper', PRACTICE_PASSWORD)
    await page.goto('/entry/issue')
    await page.locator('.ant-select').filter({ hasText: 'Search material' }).first().click()
    await page.keyboard.type(sap)
    await expect(page.locator('.ant-select-item-option').filter({ hasText: sap }).first()).toBeVisible()
  })
