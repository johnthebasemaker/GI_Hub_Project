/**
 * Phase 14e — the 3D login, and above all its fallbacks (ruling Q14-12).
 *
 * Tier 0 (CSS glass + a static SVG mark) is for everyone. Tier 1 (WebGL,
 * lazy-loaded) only on a capable desktop: never under reduced motion, never on
 * a phone, never in a native shell, and a device without WebGL keeps Tier 0
 * untouched. The build check proves the chunk is off the critical path; this
 * proves the page decides correctly and that the sign-in form never waits.
 *
 * Every test opens `/`: signed out, the login renders at ANY path, and there is
 * no `/login` route to land on once signed in.
 */
import { test, expect, type Page } from '@playwright/test'
import { E2E_PASSWORD, USERS } from '../harness/env'

const SCENE = /loginScene/

function watchScene(page: Page) {
  const seen: string[] = []
  page.on('request', (r) => { if (SCENE.test(r.url())) seen.push(r.url()) })
  return seen
}

async function formReady(page: Page) {
  await expect(page.getByPlaceholder('Username')).toBeVisible()
  await expect(page.getByRole('button', { name: /sign in/i })).toBeEnabled()
}

test('reduced motion: Tier 0 only — no WebGL chunk is even fetched, and nothing moves',
  async ({ browser }) => {
    const ctx = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1440, height: 900 } })
    const page = await ctx.newPage()
    const seen = watchScene(page)
    await page.goto('/')
    await formReady(page)
    await page.waitForTimeout(3500)
    expect(seen, 'the WebGL chunk was requested under reduced motion').toHaveLength(0)
    await expect(page.locator('canvas.gi-login-fx')).toHaveCount(0)
    const anim = await page.locator('.gi-login-crest').evaluate(
      (el) => getComputedStyle(el, '::before').animationName)
    expect(anim).toBe('none')
    // …and no light sweep either (Phase 15c)
    expect(await page.locator('.gi-login-crest').evaluate(
      (el) => getComputedStyle(el, '::after').display)).toBe('none')
    await ctx.close()
  })

test('a phone gets Tier 0 only', async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })
  const page = await ctx.newPage()
  const seen = watchScene(page)
  await page.goto('/')
  await formReady(page)
  await page.waitForTimeout(3500)
  expect(seen).toHaveLength(0)
  await ctx.close()
})

test('a native shell gets Tier 0 only', async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  await ctx.addInitScript(() => { (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {} })
  const page = await ctx.newPage()
  const seen = watchScene(page)
  await page.goto('/')
  await formReady(page)
  await page.waitForTimeout(3500)
  expect(seen).toHaveLength(0)
  await ctx.close()
})

test('WebGL unavailable: the scene gives up cleanly and Tier 0 stays', async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  await ctx.addInitScript(() => {
    const orig = HTMLCanvasElement.prototype.getContext
    // @ts-expect-error — narrowing the overloads is beside the point here
    HTMLCanvasElement.prototype.getContext = function (type: string, ...rest: unknown[]) {
      if (/webgl/i.test(type)) return null
      // @ts-expect-error — forwarding the original overloads
      return orig.call(this, type, ...rest)
    }
  })
  const page = await ctx.newPage()
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(String(e)))
  await page.goto('/')
  await formReady(page)
  await expect(page.locator('.gi-login')).toHaveAttribute('data-gi3d', 'unavailable', { timeout: 15_000 })
  await expect(page.locator('canvas.gi-login-fx')).toHaveCount(0)
  // Tier 0's mark is still there, in its band above the card
  const bg = await page.locator('.gi-login-crest').evaluate((el) => getComputedStyle(el, '::before').backgroundImage)
  expect(bg).toContain('gi-mark.svg')
  expect(errors, errors.join(' | ')).toHaveLength(0)
  await ctx.close()
})

test('a capable desktop: the scene arrives after the form, and is disposed on sign-in',
  async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
    const page = await ctx.newPage()
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(String(e)))
    await page.goto('/')
    await formReady(page)
    // What does this browser really have? A headless run usually has WebGL on
    // SwiftShader — the CPU — which the scene must REFUSE (it would take the
    // main thread from the form). Both branches are asserted, and which one
    // ran is recorded; a half-built scene is never an acceptable third answer.
    const gpu = await page.evaluate(() => {
      const gl = document.createElement('canvas').getContext('webgl2')
      if (!gl) return 'none'
      const dbg = gl.getExtension('WEBGL_debug_renderer_info')
      const name = String(dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER))
      return /swiftshader|llvmpipe|softpipe|software|basic render/i.test(name) ? 'software' : 'hardware'
    })
    test.info().annotations.push({ type: 'webgl', description: gpu })
    const fx = page.locator('.gi-login')
    await expect(fx).toHaveAttribute('data-gi3d', /^(on|unavailable)$/, { timeout: 20_000 })
    const state = await fx.getAttribute('data-gi3d')
    console.log(`[login-3d] this browser's WebGL: ${gpu} → data-gi3d=${state}`)
    if (gpu === 'hardware') {
      // on, unless the frame budget refused it — then it must say so
      if (state === 'unavailable') await expect(fx).toHaveAttribute('data-gi3d-reason', 'slow')
      else await expect(page.locator('canvas.gi-login-fx')).toHaveCount(1)
    } else {
      expect(state).toBe('unavailable')
      if (gpu === 'software') await expect(fx).toHaveAttribute('data-gi3d-reason', 'software')
      await expect(page.locator('canvas.gi-login-fx')).toHaveCount(0)
    }

    await page.getByPlaceholder('Username').fill(USERS.admin)
    await page.getByPlaceholder('Password').fill(E2E_PASSWORD)
    const cons: string[] = []
    page.on('console', (m) => { if (m.type() === 'error') cons.push(m.text().slice(0, 300)) })
    await page.getByRole('button', { name: /sign in/i }).click()
    await expect(page.getByText('Dashboard').first(), [...errors, ...cons].join(' | ')).toBeVisible()
    // disposed, not hidden
    await expect(page.locator('canvas.gi-login-fx')).toHaveCount(0)
    expect(errors, errors.join(' | ')).toHaveLength(0)
    await ctx.close()
  })

test('the Training Hub landing carries the glass header', async ({ browser }) => {
  const { storageStatePath } = await import('../harness/env')
  const ctx = await browser.newContext({ storageState: storageStatePath('hod') })
  const page = await ctx.newPage()
  await page.goto('/training')
  await expect(page.locator('.gi-training-hero img[src="/brand/gi-mark.svg"]')).toBeVisible()
  await ctx.close()
})

// Phase 15c (rulings Q15-1/Q15-2). The 14e mark floated in the margin BESIDE an
// assumed 400 px card and turned with the pointer; on narrower windows it sat on
// the card, behind a translucent glass slab. Now it has its own band above the
// card, and nothing on the page follows the pointer.
for (const [w, h] of [[1280, 720], [1024, 640], [1440, 900], [390, 844]] as const) {
  test(`15c: the mark's band sits above the card, never on it (${w}×${h})`, async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: w, height: h } })
    const page = await ctx.newPage()
    await page.goto('/')
    await formReady(page)
    const crest = await page.locator('.gi-login-crest').boundingBox()
    const card = await page.locator('.gi-login-card').boundingBox()
    expect(crest && card).toBeTruthy()
    expect(crest!.height).toBeGreaterThanOrEqual(100)
    expect(crest!.y + crest!.height).toBeLessThanOrEqual(card!.y + 0.5)
    // the Tier 0 background marks are gone
    for (const pseudo of ['::before', '::after']) {
      expect(await page.locator('.gi-login').evaluate(
        (el, p) => getComputedStyle(el, p).content, pseudo)).toMatch(/^(none|normal)$/)
    }
    // static: moving the pointer leaves the card untransformed
    await page.mouse.move(10, 10)
    await page.mouse.move(w - 10, h - 10, { steps: 5 })
    expect(await page.locator('.gi-login-card').evaluate((el) => getComputedStyle(el).transform)).toBe('none')
    await ctx.close()
  })
}

test('15c: a window too short for the band shows the small static mark inside the card instead',
  async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 500 } })
    const page = await ctx.newPage()
    await page.goto('/')
    await formReady(page)
    await expect(page.locator('.gi-login-crest')).toBeHidden()
    const small = await page.locator('.gi-login-head .gi-wordmark').evaluate((el) => {
      const cs = getComputedStyle(el, '::before')
      return { display: cs.display, anim: cs.animationName, bg: cs.backgroundImage }
    })
    expect(small.display).toBe('block')
    expect(small.anim).toBe('none')
    expect(small.bg).toContain('gi-mark.svg')
    await ctx.close()
  })
