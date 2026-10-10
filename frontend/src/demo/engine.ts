/**
 * Phase 21f — the demo RUNNER. Playwright cannot run inside a viewer's
 * browser, so this does what Playwright does, visibly: it finds an element,
 * moves a gold ring to it, says what is about to happen, then does it through
 * the same DOM the person would use (`element.click()`, React's native value
 * setter + an `input` event). Nothing here talks to the API except through the
 * page itself — the demo drives the real UI, and the real UI writes.
 *
 * Practice only: this module is in the lazy demo chunk that AppLayout loads
 * only when `isPractice()` (rule 17; zero bytes on the login critical path).
 */
import type { Beat, DemoScript } from './types'
import type { Voice } from './voice'

export type RunState = 'running' | 'paused' | 'done' | 'stopped' | 'error'

export interface RunnerHooks {
  navigate: (path: string) => void
  switchRole: (role: string) => Promise<string>
  onBeat: (index: number, total: number, sentence: string) => void
  /** The element the ring and spotlight sit on (null: none). */
  onTarget: (el: Element | null, pulse: boolean) => void
  onState: (state: RunState, detail?: string) => void
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

function visible(el: Element): boolean {
  const r = (el as HTMLElement).getBoundingClientRect?.()
  if (!r || (r.width === 0 && r.height === 0)) return false
  const cs = getComputedStyle(el as HTMLElement)
  return cs.visibility !== 'hidden' && cs.display !== 'none'
}

/** React tracks a controlled input's value itself; setting `.value` directly
 *  is invisible to it. The prototype's setter + an `input` event is what a
 *  keystroke looks like to React. */
function setNativeValue(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(proto, 'value')?.set?.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

function inputIn(el: Element): HTMLInputElement | HTMLTextAreaElement | null {
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return el
  return el.querySelector('input:not([type=hidden]):not([type=file]), textarea')
}

/** A small navy card that says what it is — the supporting document a demo
 *  entry attaches (Practice keeps the document gate on, as Live does). */
async function demoSlip(label: string): Promise<File> {
  const c = document.createElement('canvas')
  c.width = 480
  c.height = 240
  const g = c.getContext('2d')
  if (g) {
    const css = getComputedStyle(document.documentElement)
    g.fillStyle = css.getPropertyValue('--gi-navy').trim() || 'navy'
    g.fillRect(0, 0, c.width, c.height)
    g.fillStyle = css.getPropertyValue('--gi-gold').trim() || 'goldenrod'
    g.font = '600 26px sans-serif'
    g.fillText('PRACTICE DEMO SLIP', 32, 92)
    g.font = '20px sans-serif'
    g.fillText(label, 32, 140)
  }
  const blob = await new Promise<Blob>((r) => c.toBlob((b) => r(b ?? new Blob()), 'image/png'))
  return new File([blob], `${label}.png`, { type: 'image/png' })
}

export class DemoRunner {
  private i = 0
  private paused = false
  private stopped = false
  private wake: (() => void) | null = null
  readonly vars: Record<string, string>
  private readonly script: DemoScript
  private readonly voice: Voice
  private readonly hooks: RunnerHooks

  constructor(script: DemoScript, voice: Voice, hooks: RunnerHooks) {
    this.script = script
    this.voice = voice
    this.hooks = hooks
    this.vars = script.vars?.() ?? {}
  }

  get index() { return this.i }
  get isPaused() { return this.paused }

  fill(s: string | undefined): string {
    return (s ?? '').replace(/\{(\w+)\}/g, (_m, k: string) => this.vars[k] ?? '')
  }

  pause() {
    if (this.stopped || this.paused) return
    this.paused = true
    this.voice.cancel()
    this.hooks.onState('paused')
  }

  resume() {
    if (!this.paused) return
    this.paused = false
    this.hooks.onState('running')
    this.wake?.()
  }

  /** Cut the current sentence short; the beat's action still happens, so the
   *  flow never loses its place. */
  skip() { this.voice.cancel() }

  stop() {
    this.stopped = true
    this.voice.cancel()
    this.wake?.()
    this.hooks.onTarget(null, false)
    this.hooks.onState('stopped')
  }

  private async gate() {
    while (this.paused && !this.stopped) {
      await new Promise<void>((r) => { this.wake = r })
      this.wake = null
    }
  }

  private find(b: Beat): Element | null {
    let root: ParentNode = document
    if (b.within) {
      const want = this.fill(b.within.hasText)
      const box = Array.from(document.querySelectorAll(b.within.css))
        .find((e) => visible(e) && (e.textContent ?? '').includes(want))
      if (!box) return null
      root = box
      if (!b.target) return box
    }
    if (!b.target) return null
    const want = b.text != null ? this.fill(b.text) : null
    return Array.from(root.querySelectorAll(this.fill(b.target)))
      .find((e) => visible(e) && (want == null || (e.textContent ?? '').includes(want))) ?? null
  }

  private async waitFor(fn: () => Element | null | boolean, timeout: number): Promise<Element | null | boolean> {
    const end = Date.now() + timeout
    for (;;) {
      if (this.stopped) return null
      const hit = fn()
      if (hit) return hit
      if (Date.now() > end) return null
      await sleep(120)
    }
  }

  private async point(el: Element | null, pulse = false) {
    if (!el) { this.hooks.onTarget(null, false); return }
    el.scrollIntoView({ block: 'center', inline: 'nearest' })
    await sleep(this.voice.fast ? 20 : 160)
    this.hooks.onTarget(el, pulse)
  }

  async run(): Promise<void> {
    const beats = this.script.beats
    this.hooks.onState('running')
    try {
      for (this.i = 0; this.i < beats.length; this.i += 1) {
        await this.gate()
        if (this.stopped) return
        await this.play(beats[this.i])
        if (this.stopped) return
      }
      this.hooks.onTarget(null, false)
      this.hooks.onState('done')
    } catch (e) {
      this.hooks.onTarget(null, false)
      this.hooks.onState('error', e instanceof Error ? e.message : String(e))
    }
  }

  private async play(b: Beat) {
    const sentence = this.fill(b.say)
    const timeout = b.timeout ?? 15_000
    this.hooks.onBeat(this.i, this.script.beats.length, sentence)

    if (b.do === 'navigate') {
      this.hooks.onTarget(null, false)
      const said = this.voice.say(sentence)
      this.hooks.navigate(this.fill(b.value))
      await said
      await this.settle(b, timeout)
      return
    }
    if (b.do === 'switch') {
      this.hooks.onTarget(null, false)
      const said = this.voice.say(sentence)
      const who = await this.hooks.switchRole(this.fill(b.value))
      this.vars.user = who
      await said
      await this.settle(b, timeout)
      return
    }

    const needsTarget = !!(b.target || b.within)
    let el: Element | null = null
    if (needsTarget) {
      el = (await this.waitFor(() => this.find(b), timeout)) as Element | null
      if (!el) {
        if (b.optional) return
        if (this.stopped) return
        throw new Error(`The demo could not find "${this.fill(b.text ?? b.within?.hasText ?? b.target)}" on this page.`)
      }
      await this.point(el)
    } else {
      this.hooks.onTarget(null, false)
    }
    await this.voice.say(sentence)
    await this.gate()
    if (this.stopped) return
    if (el) await this.act(b, el)
    await this.settle(b, timeout)
  }

  private async settle(b: Beat, timeout: number) {
    if (b.until) {
      const ok = await this.waitFor(() => {
        const e = document.querySelector(this.fill(b.until))
        return !!e && visible(e)
      }, timeout)
      if (!ok && !this.stopped && !b.optional) throw new Error(`The page did not show what the demo expected (${b.until}).`)
    }
    if (b.untilText) {
      const want = this.fill(b.untilText)
      const ok = await this.waitFor(() => (document.body.textContent ?? '').includes(want), timeout)
      if (!ok && !this.stopped && !b.optional) throw new Error(`The page did not say “${want}”.`)
    }
  }

  private async act(b: Beat, el: Element) {
    const fast = this.voice.fast
    switch (b.do) {
      case undefined:
      case 'point':
      case 'wait':
        return
      case 'read': {
        const inp = inputIn(el)
        const got = (inp?.value ?? el.textContent ?? '').trim()
        if (b.pattern) {
          const m = new RegExp(b.pattern).exec(got)
          if (!m) throw new Error(`the page did not show what the demo needed to note (${b.as ?? 'value'})`)
          this.vars[b.as ?? 'value'] = (m[1] ?? m[0]).trim()
        } else {
          this.vars[b.as ?? 'value'] = got
        }
        return
      }
      case 'click':
        this.hooks.onTarget(el, true)
        ;(el as HTMLElement).click()
        await sleep(fast ? 50 : 350)
        return
      case 'type': {
        const inp = inputIn(el)
        if (!inp) throw new Error('nothing to type into')
        inp.focus()
        const text = this.fill(b.value)
        setNativeValue(inp, '')
        for (let k = 1; k <= text.length; k += 1) {
          setNativeValue(inp, text.slice(0, k))
          await sleep(fast ? 0 : 45 / this.voice.speed)
        }
        inp.dispatchEvent(new Event('change', { bubbles: true }))
        if (b.enter) {
          for (const kind of ['keydown', 'keypress', 'keyup']) {
            inp.dispatchEvent(new KeyboardEvent(kind, { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }))
          }
          await sleep(fast ? 50 : 200)
        } else {
          inp.blur()
        }
        return
      }
      case 'select': {
        const sel = el.closest('.ant-select') ?? el.querySelector('.ant-select') ?? el
        const inp = sel.querySelector('input') as HTMLInputElement | null
        inp?.focus()
        sel.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }))
        const want = this.fill(b.value)
        if (inp && want) {
          for (let k = 1; k <= want.length; k += 1) {
            setNativeValue(inp, want.slice(0, k))
            await sleep(fast ? 0 : 45 / this.voice.speed)
          }
        }
        const opt = (await this.waitFor(() => Array.from(document.querySelectorAll(
          '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option'))
          .find((o) => visible(o) && (o.textContent ?? '').toLowerCase().includes(want.toLowerCase())) ?? null,
        8000)) as HTMLElement | null
        if (!opt) throw new Error(`no option “${want}” to choose`)
        this.hooks.onTarget(opt, true)
        await sleep(fast ? 20 : 300)
        if (b.as) this.vars[b.as] = (opt.textContent ?? '').trim()
        opt.click()
        await sleep(fast ? 50 : 250)
        return
      }
      case 'attach': {
        const input = (el instanceof HTMLInputElement && el.type === 'file'
          ? el : el.querySelector('input[type=file]')) as HTMLInputElement | null
        if (!input) throw new Error('no file input to attach to')
        const dt = new DataTransfer()
        dt.items.add(await demoSlip(this.fill(b.value) || 'DEMO'))
        input.files = dt.files
        input.dispatchEvent(new Event('change', { bubbles: true }))
        await sleep(fast ? 100 : 400)
        return
      }
      case 'check': {
        const boxes = b.within
          ? Array.from(document.querySelectorAll(b.within.css))
            .filter((c) => visible(c) && (c.textContent ?? '').includes(this.fill(b.within!.hasText)))
            .flatMap((c) => Array.from(c.querySelectorAll(b.target ?? 'input[type=checkbox]')))
          : Array.from(document.querySelectorAll(b.target ?? 'input[type=checkbox]'))
        for (const x of boxes) {
          const cb = x as HTMLInputElement
          if (!cb.checked && !cb.disabled) {
            this.hooks.onTarget(cb, true)
            cb.click()
            await sleep(fast ? 20 : 250)
          }
        }
        return
      }
      default:
        return
    }
  }
}
