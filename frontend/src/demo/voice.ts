/**
 * Phase 21f — the demo's voice (ruling Q21-12: on-device speech, no paid
 * cloud voice; English, 1×, Q21-16).
 *
 * Order of preference for a sentence:
 *   1. a pre-recorded clip made on the office Mac with the macOS voice
 *      (`tools/demo_voice.py` → public/demo-audio/<hash>.m4a + manifest.json),
 *      so the projector laptop sounds the same as the office one — only for
 *      sentences WITHOUT placeholders, which never change;
 *   2. the browser's speech, ON-DEVICE voices only (`localService`): a cloud
 *      voice would send the sentence to Google, so those are filtered out;
 *   3. silence for the time it takes to read the subtitle — the subtitle is
 *      the source of truth, so a muted laptop or a missing voice still works.
 */

/** djb2 over UTF-16 code units — tools/demo_voice.py computes the same. */
export function sentenceHash(text: string): string {
  let h = 5381
  for (let i = 0; i < text.length; i += 1) h = ((h * 33) ^ text.charCodeAt(i)) >>> 0
  return h.toString(16).padStart(8, '0')
}

export function readingMs(text: string, speed: number, fast: boolean): number {
  if (fast) return 60
  return Math.max(1600, text.length * 58) / speed
}

const BASE = `${import.meta.env.BASE_URL || '/'}demo-audio/`

export class Voice {
  muted = false
  speed = 1
  /** E2E / rehearsal: no waiting for speech at all. */
  fast = false
  private clips: Set<string> | null = null
  private audio: HTMLAudioElement | null = null
  private cut: (() => void) | null = null

  async load(): Promise<void> {
    if (this.clips) return
    try {
      const r = await fetch(`${BASE}manifest.json`, { cache: 'no-cache' })
      const j = r.ok ? ((await r.json()) as { clips?: string[] }) : {}
      this.clips = new Set(j.clips ?? [])
    } catch {
      this.clips = new Set()
    }
  }

  private localVoice(): SpeechSynthesisVoice | null {
    if (typeof window === 'undefined' || !('speechSynthesis' in window)) return null
    const vs = window.speechSynthesis.getVoices().filter((v) => v.localService && /^en/i.test(v.lang))
    return vs.find((v) => /Samantha|Daniel|Karen|Moira|Serena/i.test(v.name)) ?? vs[0] ?? null
  }

  /** Resolves when the sentence has been said (or skipped with `cancel`). */
  say(text: string): Promise<void> {
    this.cancel()
    return new Promise<void>((resolve) => {
      let done = false
      const finish = () => {
        if (done) return
        done = true
        this.cut = null
        resolve()
      }
      this.cut = () => {
        try { window.speechSynthesis?.cancel() } catch { /* nothing to cancel */ }
        if (this.audio) { this.audio.pause(); this.audio = null }
        finish()
      }
      const quiet = () => setTimeout(finish, readingMs(text, this.speed, this.fast))
      if (this.muted || this.fast) { quiet(); return }
      const key = sentenceHash(text)
      if (this.clips?.has(key) && this.speed === 1) {
        const a = new Audio(`${BASE}${key}.m4a`)
        this.audio = a
        a.onended = finish
        a.onerror = () => { this.audio = null; quiet() }
        a.play().catch(() => { this.audio = null; quiet() })
        return
      }
      const v = this.localVoice()
      if (!v) { quiet(); return }
      const u = new SpeechSynthesisUtterance(text)
      u.voice = v
      u.lang = v.lang
      u.rate = this.speed
      u.onend = finish
      u.onerror = finish
      // a voice that never reports its end must not stall the demo
      setTimeout(finish, readingMs(text, this.speed, false) * 2.5 + 4000)
      window.speechSynthesis.speak(u)
    })
  }

  cancel(): void {
    this.cut?.()
  }
}
