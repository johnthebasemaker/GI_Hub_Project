/**
 * Phase 14e — the login's Tier 1 (WebGL), bootstrapped from OUTSIDE the app.
 *
 * ⚠️ WHY A SEPARATE ENTRY. `index.html` loads this with `<script type="module"
 * async>`, beside the app's own entry — so the app bundle, LoginPage included,
 * carries NO 3D code at all: the sign-in page's critical path is byte-for-byte
 * what it was before Phase 14e (`scripts/critical_path_check.mjs` holds it to
 * zero growth). This file is small, async (it never blocks the page), and does
 * three things:
 *
 *   1. decides whether this device may have the WebGL mark at all (below);
 *   2. watches for the login card to appear, and when the browser is idle,
 *      loads the scene entry (Three.js, ~140 KB gz, fetched only here) from
 *      the URL vite.config writes onto this script tag;
 *   3. watches for the card to go (sign-in) and DISPOSES the scene — and
 *      mounts it again if the user signs out.
 *
 * Tier 1 only when ALL hold (ruling Q14-12):
 *   · not a native shell (Tauri / Capacitor) — native apps get Tier 0 only;
 *   · no `prefers-reduced-motion` (re-checked live: turning it on mid-session
 *     tears the scene down);
 *   · a desktop: a fine pointer and ≥ 1024 px wide;
 *   · not a low-memory device: `navigator.deviceMemory` ≥ 4 where reported
 *     (Chromium); Safari/Firefox do not report it, and a desktop is then taken
 *     as capable. WebGL itself, a software renderer and the frame budget are
 *     tested inside the scene — any failure there leaves Tier 0 untouched.
 */
const RM = '(prefers-reduced-motion: reduce)'

/** The same test as `api/client.detectClientType()`, restated rather than
 * imported: importing ANY app module from this entry makes Rollup re-split the
 * app's shared chunks, and the app bundle must not change by a byte. The E2E
 * spec login-3d pins the behaviour (a Tauri shell gets no WebGL). */
function isNativeShell(): boolean {
  const w = window as unknown as Record<string, unknown>
  if ('__TAURI_INTERNALS__' in w || '__TAURI__' in w) return true
  const cap = w.Capacitor as { isNativePlatform?: () => boolean } | undefined
  return !!cap?.isNativePlatform?.()
}

export function tier1Allowed(): boolean {
  if (isNativeShell()) return false
  const mm = (q: string) => window.matchMedia?.(q).matches ?? false
  if (mm(RM)) return false
  if (!mm('(pointer: fine) and (min-width: 1024px)')) return false
  const mem = (navigator as { deviceMemory?: number }).deviceMemory
  return mem === undefined || mem >= 4
}

type Stop = () => void
type SceneHost = Window & { __giLoginScene?: (host: HTMLElement) => Stop }
let stop: Stop | null = null        // the mounted scene's teardown
let pending: Stop | null = null     // a scheduled-but-not-mounted start
const logins = document.getElementsByClassName('gi-login')   // live collection

function start(host: HTMLElement) {
  let dead = false
  const go = () => {
    // The scene is its own entry; vite.config's gi-login-fx plugin writes its
    // URL onto this script tag (the dev server serves the source path). It is
    // added as a plain module <script>, NOT import()ed: Vite wraps every
    // import() in a preload helper it would then share with the app entry,
    // re-splitting the app's chunks. The scene registers itself on
    // `window.__giLoginScene` and announces it with a `gi-login-scene` event.
    const url = document.querySelector<HTMLScriptElement>('script[data-gi-scene]')?.dataset.giScene
    if (!url) { pending = null; return }
    const mount = () => {
      pending = null
      const fn = (window as SceneHost).__giLoginScene
      if (!dead && fn && host.isConnected) stop = fn(host)
    }
    if ((window as SceneHost).__giLoginScene) { mount(); return }
    window.addEventListener('gi-login-scene', mount, { once: true })
    if (!document.querySelector(`script[src="${url}"]`)) {
      const tag = document.createElement('script')
      tag.type = 'module'
      tag.src = url
      tag.onerror = () => { pending = null }   // a failed load leaves Tier 0
      document.head.appendChild(tag)
    }
  }
  // Safari has no requestIdleCallback; a short timeout stands in for it.
  const idle = 'requestIdleCallback' in window
  const id = idle ? window.requestIdleCallback(go, { timeout: 2500 }) : window.setTimeout(go, 1200)
  pending = () => {
    dead = true
    if (idle) window.cancelIdleCallback(id)
    else window.clearTimeout(id)
  }
}

function halt() {
  pending?.(); pending = null
  stop?.(); stop = null
}

function sync() {
  const host = logins[0] as HTMLElement | undefined
  if (host && !stop && !pending && tier1Allowed()) start(host)
  else if (!host && (stop || pending)) halt()          // signed in: dispose
}

// O(1) per DOM batch: the collection is live, so this is a length check.
new MutationObserver(sync).observe(document.body, { childList: true, subtree: true })
window.matchMedia?.(RM).addEventListener?.('change', (e) => { if (e.matches) halt() })
window.addEventListener('pagehide', halt)
sync()
