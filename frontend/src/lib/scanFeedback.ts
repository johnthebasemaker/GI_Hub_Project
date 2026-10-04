// Scan feedback — a sound and a buzz the store keeper can trust without
// looking at the screen (Phase 18 Track 3).
//
// ⚠️ A WAREHOUSE COUNTER IS LOUD AND THE EYES ARE ON THE TOOL, NOT THE SCREEN.
// Before this, a scan's only feedback was a toast in the corner: a miss looked
// exactly like a hit until somebody read it. Three distinct signals instead:
//
//   ok     one short high tone + one 40 ms buzz   — "found it / done"
//   info   one soft mid tone                       — "found, needs a choice"
//   error  two low tones + a long buzz             — "nothing matched / refused"
//
// Generated with WebAudio (no audio files to ship or cache), so nothing is
// added to the login critical path — this module is only imported by pages
// that scan. Every browser API here is optional: a desktop without vibration,
// a browser that blocks audio until a gesture, or a private window with no
// storage all degrade to silence, never to an exception.

export type ScanTone = 'ok' | 'info' | 'error'

const MUTE_KEY = 'gi.scan.muted'
let ctx: AudioContext | null = null

export function isMuted(): boolean {
  try {
    return window.localStorage.getItem(MUTE_KEY) === '1'
  } catch {
    return false
  }
}

export function setMuted(muted: boolean): void {
  try {
    window.localStorage.setItem(MUTE_KEY, muted ? '1' : '0')
  } catch {
    /* per-viewer convenience only — fine to lose */
  }
}

function audio(): AudioContext | null {
  try {
    if (!ctx) {
      const AC = window.AudioContext
        ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (!AC) return null
      ctx = new AC()
    }
    if (ctx.state === 'suspended') void ctx.resume().catch(() => undefined)
    return ctx
  } catch {
    return null
  }
}

function tone(ac: AudioContext, freq: number, startS: number, durS: number, gain = 0.12) {
  const osc = ac.createOscillator()
  const g = ac.createGain()
  osc.type = 'square'
  osc.frequency.value = freq
  const t0 = ac.currentTime + startS
  g.gain.setValueAtTime(0, t0)
  g.gain.linearRampToValueAtTime(gain, t0 + 0.005)
  g.gain.setValueAtTime(gain, t0 + durS - 0.01)
  g.gain.linearRampToValueAtTime(0, t0 + durS)
  osc.connect(g).connect(ac.destination)
  osc.start(t0)
  osc.stop(t0 + durS + 0.02)
}

function buzz(pattern: number | number[]) {
  try {
    navigator.vibrate?.(pattern)
  } catch {
    /* not every device can */
  }
}

/** Play the feedback for one scan outcome. Never throws. */
export function scanFeedback(kind: ScanTone): void {
  if (kind === 'ok') buzz(40)
  else if (kind === 'error') buzz([120, 60, 120])
  if (isMuted()) return
  const ac = audio()
  if (!ac) return
  try {
    if (kind === 'ok') tone(ac, 1320, 0, 0.09)
    else if (kind === 'info') tone(ac, 880, 0, 0.08, 0.08)
    else {
      tone(ac, 220, 0, 0.12)
      tone(ac, 196, 0.16, 0.16)
    }
  } catch {
    /* audio is a courtesy */
  }
}
