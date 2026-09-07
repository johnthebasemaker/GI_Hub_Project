/**
 * tests/video_gen/harness/record.ts — the recording harness for Phase 12.
 *
 * Everything here exists so that a tutorial recording is a REPRODUCIBLE
 * ARTEFACT rather than a screen capture somebody happened to take. Three jobs:
 *
 *   1. BEATS. A tutorial is a timeline, and the narration has to land on it.
 *      `Beats` stamps a label the moment a UI step completes and writes
 *      `beats.json` beside the video, so the compositor aligns audio to
 *      MEASURED times instead of guessed ones — the same discipline
 *      `docs/exec_video/project_v3/build.py` already applies to its narration
 *      (every scene cut to its measured WAV length, never to a guess).
 *
 *   2. CHROME. Playwright's video has no mouse pointer, so a viewer sees
 *      fields fill themselves. `installTutorialChrome` paints a synthetic
 *      cursor and a click pulse, and `glide` moves it along a path instead of
 *      teleporting. Without both, the recording is unusable as instruction.
 *
 *   3. THE REDACTION HOOK (rule 1 / P12-2). The E2E database is loaded from
 *      the REAL `gi_database.db` by `tools/migration/cutover_migrate.py`, so
 *      it holds real employee names and real stock. A recording of it is
 *      proprietary data in a file people forward. `installTutorialChrome`
 *      applies the shot list's `mask` and `replace` rules in the page before
 *      first paint and keeps applying them through a MutationObserver, because
 *      an SPA re-renders after every fetch and a one-shot pass would redact the
 *      login screen and nothing else.
 *
 * ⚠️ The redaction hook is a SECOND line, not the boundary. The boundary is the
 * dataset: a tutorial must be recorded against synthetic rows. See
 * `PROPOSED_PHASE12_PLAN.md` §3.
 */
import { expect, type Browser, type BrowserContext, type Page } from '@playwright/test'
import * as fs from 'node:fs'
import * as path from 'node:path'

// ── the shot list ──────────────────────────────────────────────────────────
// Python owns the YAML (tools/tutorials/*.yaml) and writes this JSON; the
// spec only ever reads it. One parser, in the language that already has one.
export interface ShotList {
  tutorial_id: string
  role: string
  language: string
  /** The UI steps to perform, in order. See harness/steps.ts. */
  steps: unknown[]
  /** What the assistant is scripted to answer. Rendered ON SCREEN, so it is
   *  reviewed in the diff like any other user-visible string. Absent on the
   *  tutorials that never open the assistant — most of them. */
  assistant_question?: string
  assistant_answer?: string
  /** ms the stub waits before answering, so "Thinking…" is actually visible. */
  assistant_think_ms: number
  /**
   * ⚠️ PER-BEAT HOLD TIMES, IN MILLISECONDS, MEASURED FROM THE RENDERED
   * NARRATION (pass A in `tools/generate_tutorial.py`). A Playwright-driven
   * UI is far faster than a person explaining it: recorded with guessed
   * pauses, this tutorial overran all six of its beats and fitted 40.2 s of
   * speech into 19.6 s of video. The recorder therefore does not choose how
   * long to dwell on a step — the audio does.
   */
  holds: Record<string, number>
  /** Routes the script DECLARES it visits. The rule-14 lint checks these
   *  against the role's access before a browser starts; the recorder then
   *  checks reality against them (see `trackNavigation`). */
  routes: string[]
  /** Redaction: CSS selectors blurred, and literal → replacement text. */
  mask: string[]
  replace: Record<string, string>
}

const DEFAULT_SHOTLIST: ShotList = {
  tutorial_id: 'store_keeper_hub_assistant',
  role: 'sk',
  language: 'en',
  steps: [],
  assistant_question: 'How do I stage a return?',
  assistant_answer:
    'Open Entry → Returnables and choose the material you are sending back.\n\n'
    + 'Enter the quantity and the reason, then Submit. The return is staged, '
    + 'not committed: it appears on your Head of Department’s approval '
    + 'queue and only reaches the permanent ledger once they sign the End of '
    + 'Day commit.',
  assistant_think_ms: 1400,
  holds: {},
  routes: ['/'],
  mask: [],
  replace: {},
}

export function loadShotList(): ShotList {
  const p = process.env.GI_TUTORIAL_SHOTLIST
  if (!p) return DEFAULT_SHOTLIST
  return { ...DEFAULT_SHOTLIST, ...JSON.parse(fs.readFileSync(p, 'utf-8')) }
}

export function outDir(): string {
  const d = process.env.GI_TUTORIAL_OUT
    ?? path.resolve(__dirname, '..', '.runtime')
  fs.mkdirSync(d, { recursive: true })
  return d
}

// ── beats ──────────────────────────────────────────────────────────────────
export interface Beat { id: string; note: string; t_ms: number }

export class Beats {
  private readonly t0 = Date.now()
  private readonly beats: Beat[] = []
  /** Every path the browser actually landed on, in order, de-duplicated. */
  readonly visited: string[] = []

  /** Stamp the CURRENT moment. Call it when the step is visibly finished. */
  mark(id: string, note = ''): void {
    const t = Date.now() - this.t0
    this.beats.push({ id, note, t_ms: t })
    console.log(`[beat] ${String(t).padStart(6)} ms  ${id}`)
  }

  /**
   * Hold on the current frame for as long as this beat's narration takes.
   * `fallbackMs` is used only when the shot list has no measured time — a
   * standalone `npx playwright test` run with no orchestrator behind it.
   */
  async hold(page: Page, shot: ShotList, id: string, fallbackMs: number): Promise<void> {
    const ms = shot.holds[id] ?? fallbackMs
    await page.waitForTimeout(ms)
  }

  write(file: string, extra: Record<string, unknown>): void {
    fs.writeFileSync(file, JSON.stringify(
      { recorded_at: new Date(this.t0).toISOString(), beats: this.beats,
        visited: this.visited, ...extra },
      null, 2,
    ))
  }

  get elapsedMs(): number { return Date.now() - this.t0 }
}

// ── the recording context ──────────────────────────────────────────────────
/**
 * ⚠️ The context is created BY HAND rather than through `test.use({ video })`,
 * and that is the whole reason beat alignment works. Playwright starts the
 * recording when the CONTEXT is created; through the fixture that happens
 * before the test body runs and the spec never learns when. Creating it here
 * makes t0 a number we hold.
 *
 * ⚠️ MEASURED: `recordVideo.size` DOES NOT SCALE THE PAGE. It sets the video
 * canvas, and the viewport is drawn into it 1:1 at the top-left — a 1536×864
 * viewport in a 1920×1080 canvas produced a recording that filled exactly 80%
 * of the width and 80% of the height with black bands on two sides, which is
 * 1536/1920 to the pixel. `deviceScaleFactor` does not change this; the frames
 * are CSS-pixel sized whatever it says. So the two are pinned EQUAL here and
 * the upscale to the 1920×1080 delivery canvas happens once, in ffmpeg, with
 * lanczos.
 *
 * 1536 CSS px wide rather than 1920 is a legibility choice: this is a dense
 * ERP, and a 1.25× upscale of readable 12 px table text beats a native-1080p
 * capture of text nobody can read on a phone.
 */
export const RENDER = { css: { width: 1536, height: 864 }, dpr: 1 }
export const VIDEO = { width: 1536, height: 864 }

export async function newRecordingContext(
  browser: Browser, storageState: string, dir: string,
): Promise<BrowserContext> {
  return browser.newContext({
    storageState,
    viewport: RENDER.css,
    deviceScaleFactor: RENDER.dpr,
    recordVideo: { dir, size: VIDEO },
    colorScheme: 'dark',
    reducedMotion: 'no-preference',
  })
}

// ── synthetic cursor + redaction, injected before first paint ──────────────
export async function installTutorialChrome(
  context: BrowserContext, shot: ShotList,
): Promise<void> {
  await context.addInitScript(({ mask, replace }: { mask: string[]; replace: Record<string, string> }) => {
    const boot = () => {
      // -- cursor ------------------------------------------------------------
      const dot = document.createElement('div')
      dot.id = '__gi_cursor'
      dot.style.cssText = [
        'position:fixed', 'left:0', 'top:0', 'width:22px', 'height:22px',
        'margin:-11px 0 0 -11px', 'border-radius:50%', 'pointer-events:none',
        'z-index:2147483647', 'background:rgba(201,162,39,.35)',
        'border:2px solid #C9A227', 'box-shadow:0 0 12px rgba(201,162,39,.75)',
        'transition:transform .08s linear', 'opacity:0',
      ].join(';')
      document.body.appendChild(dot)
      addEventListener('mousemove', (e) => {
        dot.style.opacity = '1'
        dot.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`
      }, true)
      addEventListener('mousedown', (e) => {
        const r = document.createElement('div')
        r.style.cssText = [
          'position:fixed', `left:${e.clientX}px`, `top:${e.clientY}px`,
          'width:12px', 'height:12px', 'margin:-6px 0 0 -6px', 'border-radius:50%',
          'pointer-events:none', 'z-index:2147483646', 'border:2px solid #C9A227',
          'animation:__gi_pulse .45s ease-out forwards',
        ].join(';')
        document.body.appendChild(r)
        setTimeout(() => r.remove(), 500)
      }, true)
      const st = document.createElement('style')
      st.textContent = '@keyframes __gi_pulse{to{transform:scale(4.5);opacity:0}}'
        + '.__gi_masked{filter:blur(7px) !important}'
      document.head.appendChild(st)

      // -- redaction ---------------------------------------------------------
      // ⚠️ Runs on EVERY mutation, not once. An SPA paints its shell first and
      // fills it from fetch; a single pass at DOMContentLoaded would redact an
      // empty page and congratulate itself.
      const scrub = () => {
        for (const sel of mask) {
          for (const el of Array.from(document.querySelectorAll(sel))) {
            (el as HTMLElement).classList.add('__gi_masked')
          }
        }
        const pairs = Object.entries(replace)
        if (!pairs.length) return
        const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
        for (let n = walk.nextNode(); n; n = walk.nextNode()) {
          let v = n.nodeValue ?? ''
          for (const [from, to] of pairs) {
            // ⚠️ WHOLE WORDS ONLY. A raw substring replace turns `hod` inside
            // `method` into `metdemo.head`, and the tutorial then teaches a
            // page that has never existed. The key is escaped and bounded.
            // ⚠️ SKIP A NODE THAT ALREADY CARRIES THE REPLACEMENT. Writing a
            // text node is itself a mutation, so this observer re-enters on
            // its own output — and if the replacement CONTAINS the search
            // string the rewrite never reaches a fixed point. `hod` →
            // `demo.hod` produced `demo.demo.hod`, then `demo.demo.demo.hod`,
            // pegging the renderer until the recording timed out four minutes
            // later reporting only that a heading was not visible. The lint in
            // tools/generate_tutorial.py refuses that mapping outright; this is
            // the second line, because the loop is the expensive failure.
            if (v.includes(to)) continue
            const rx = new RegExp(`\\b${from.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'g')
            if (!rx.test(v)) continue
            v = v.replace(new RegExp(rx.source, 'g'), to)
          }
          if (v !== n.nodeValue) n.nodeValue = v
        }
      }
      new MutationObserver(scrub).observe(
        document.documentElement, { childList: true, subtree: true, characterData: true })
      scrub()
    }
    if (document.body) boot()
    else addEventListener('DOMContentLoaded', boot)
  }, { mask: shot.mask, replace: shot.replace })
}

/**
 * Record every path the main frame lands on — the GROUND TRUTH half of the
 * rule-14 lint.
 *
 * WARNING: the static lint in `frontend/scripts/nav_access_dump.mjs` is a MODEL
 * of `canAccessPath`, and a second implementation of an access decision is
 * exactly what this repository distrusts. This is the oracle it is checked
 * against: `canAccessPath` fails closed by REDIRECTING, so a role walking into
 * a page it may not open lands somewhere it never declared, and the
 * orchestrator refuses the render. No model can drift past that.
 */
export function trackNavigation(page: Page, beats: Beats): void {
  page.on('framenavigated', (frame) => {
    if (frame !== page.mainFrame()) return
    try {
      const p = new URL(frame.url()).pathname.replace(/\/+$/, '') || '/'
      if (beats.visited[beats.visited.length - 1] !== p) beats.visited.push(p)
    } catch { /* about:blank and friends */ }
  })
}

/** Move the pointer along a path instead of teleporting to the target. */
export async function glide(page: Page, x: number, y: number, steps = 22): Promise<void> {
  await page.mouse.move(x, y, { steps })
  await page.waitForTimeout(120)
}

/** Glide to an element's centre, then click it. */
export async function glideClick(page: Page, selector: string): Promise<void> {
  const el = page.locator(selector).first()
  await expect(el).toBeVisible()
  const box = await el.boundingBox()
  if (!box) throw new Error(`no bounding box for ${selector}`)
  await glide(page, box.x + box.width / 2, box.y + box.height / 2)
  await el.click()
}

// ── the AI lane, scripted ───────────────────────────────────────────────────
/**
 * ⚠️ THE ASSISTANT IS SCRIPTED FOR A RECORDING, AND THAT IS A DECISION (P12-3).
 *
 * Three reasons, in order of weight:
 *   · A tutorial is re-rendered whenever the UI moves. An 8B model answers the
 *     same question differently each time, so a re-render silently changes what
 *     the video TEACHES — and `docs/PROJECT_STATUS.md` §1a already records what
 *     that model does when it is unsure: it invents UI no chapter describes.
 *     A tutorial is the one place that cannot be allowed to happen.
 *   · The answer is on screen, so it is user-visible copy. Scripted, it is
 *     reviewed in a diff. Sampled, it is reviewed by nobody.
 *   · Ollama holds ONE warm model by standing decision, and the batch job would
 *     otherwise queue behind whatever the operator is doing.
 *
 * This does NOT bypass rule 9's fence — it never reaches the fence. The stub
 * replaces the transport, and the answer text comes from the tracked YAML.
 */
export async function scriptAssistant(page: Page, shot: ShotList): Promise<void> {
  if (!shot.assistant_answer) return
  await page.route('**/ai/health', (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ ok: true, enabled: true, model: 'llama3.1:8b',
      message: 'Local AI ready.' }),
  }))

  await page.route('**/ai/assistant', async (route) => {
    // The pause is the point: it is what makes "Thinking…" appear on camera.
    await new Promise((r) => setTimeout(r, shot.assistant_think_ms))
    const frames = chunk(shot.assistant_answer ?? '')
      .map((tok) => `data: ${JSON.stringify({ token: tok })}\n\n`)
      .join('')
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body: frames + 'data: {"done":true}\n\n',
    })
  })
}

/** Split into token-ish chunks so the SSE shape matches the real stream. */
function chunk(text: string): string[] {
  return text.match(/\S+\s*|\s+/g) ?? [text]
}
