/**
 * `npm run check:critical` (run by `npm run build`) — Phase 14e: the sign-in
 * page's critical path must not grow, and the 3D scene must stay off it.
 *
 * THE CRITICAL PATH is what the browser must fetch before the login is on
 * screen: every script and stylesheet `dist/index.html` names (the entry
 * module and its `modulepreload`s). Measured gzipped, the way it travels.
 *
 * ⚠️ THE RULES, all enforced here:
 *   1. JS on the critical path may not exceed the recorded baseline
 *      (`perf/critical-path.json`, taken BEFORE Phase 14e) by more than
 *      JS_TOLERANCE — which is ZERO. The 3D login lives in two extra entries
 *      that the app bundle never imports (vite.config's gi-login-fx plugin).
 *   2. No critical-path chunk contains WebGL — Three.js is never pulled onto
 *      the path by a static import someone adds later.
 *   3. The async bootstrap (`<script type="module" async>`, src/three/boot.ts)
 *      is ≤ 1 KB gz and contains no WebGL; it never blocks the page.
 *   4. The WebGL scene is ≤ 180 KB gz, exists at all, and is NOT precached by
 *      the service worker (which would ship it to every installed phone).
 * CSS may grow by at most CSS_TOLERANCE (Tier 0 glass is CSS by design).
 *
 * `--update` re-records the baseline — a deliberate, reviewed act, the same
 * way a snapshot is re-approved. Never run it to make a red check green
 * without saying why in the commit.
 */
import { readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { gzipSync } from 'node:zlib'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const DIST = join(ROOT, 'dist')
const BASELINE = join(ROOT, 'perf', 'critical-path.json')
const JS_TOLERANCE = 0            // raw bytes — Phase 14e adds NOTHING to it
const ASYNC_BUDGET = 1024         // bytes gz — the async WebGL bootstrap (boot.ts)
const CSS_TOLERANCE = 2048        // bytes gz — Tier 0 glass is CSS, budgeted
const LAZY_3D_BUDGET = 180 * 1024 // bytes gz — ruling Q14-12 / plan §5.2
const WEBGL = /WebGLRenderer|WEBGL_lose_context|MeshPhysicalMaterial/

const html = readFileSync(join(DIST, 'index.html'), 'utf8')
const tags = [...html.matchAll(/<(?:script|link)\b[^>]*>/g)].map((m) => m[0])
const srcOf = (t) => /(?:src|href)="\/?(assets\/[^"]+\.(?:js|css))"/.exec(t)?.[1]
// An `async` module script never blocks the page — it is the optional WebGL
// bootstrap, measured and budgeted on its own below, never as critical path.
const asyncRefs = tags.filter((t) => /^<script\b/.test(t) && /\basync\b/.test(t)).map(srcOf).filter(Boolean)
const refs = tags.filter((t) => !(/^<script\b/.test(t) && /\basync\b/.test(t))).map(srcOf).filter(Boolean)
const uniq = [...new Set(refs)]
// Content hashes (`-B3fIvE5R.js`) change whenever ANY chunk changes, and the
// entry names lazy chunks by hashed file name — so the raw gzip size drifts by
// a few bytes with no code change at all. Every hash is replaced by a fixed
// token before measuring: this counts CODE, not hash entropy.
const HASH = /-[A-Za-z0-9_-]{8}(?=\.(?:js|css)\b)/g
const norm = (p) => readFileSync(join(DIST, p), 'utf8').replace(HASH, '-HASHHASH')
const gz = (p) => gzipSync(norm(p)).length
// ⚠️ The ZERO-growth rule is on RAW bytes: they are exact for a given lockfile,
// where gzip output can differ by a few bytes between zlib builds (a laptop's
// Node and CI's), which would make a zero tolerance flaky. gz is reported.
const raw = (p) => Buffer.byteLength(norm(p))
const js = uniq.filter((p) => p.endsWith('.js'))
const css = uniq.filter((p) => p.endsWith('.css'))
const jsBytes = js.reduce((a, p) => a + gz(p), 0)
const jsRaw = js.reduce((a, p) => a + raw(p), 0)
const cssBytes = css.reduce((a, p) => a + gz(p), 0)

const all = readdirSync(join(DIST, 'assets')).filter((f) => f.endsWith('.js')).map((f) => `assets/${f}`)
const webglOnPath = js.filter((p) => WEBGL.test(readFileSync(join(DIST, p), 'utf8')))
const lazy3d = all.filter((p) => !js.includes(p) && WEBGL.test(readFileSync(join(DIST, p), 'utf8')))
const lazy3dBytes = lazy3d.reduce((a, p) => a + gz(p), 0)

const asyncBytes = asyncRefs.reduce((a, p) => a + gz(p), 0)
let sw = ''
try { sw = readFileSync(join(DIST, 'sw.js'), 'utf8') } catch { /* no PWA build */ }
const precached3d = lazy3d.filter((p) => sw.includes(p.replace('assets/', '')))
const kb = (n) => `${(n / 1024).toFixed(2)} KB`
const now = { js_raw: jsRaw, js_gz: jsBytes, css_gz: cssBytes, files: uniq.length }

if (process.argv.includes('--update')) {
  writeFileSync(BASELINE, JSON.stringify({
    _note: 'Critical path of the sign-in page (raw + gzipped bytes, hashes normalised). Re-recorded only by '
      + '`node scripts/critical_path_check.mjs --update`, deliberately.',
    ...now,
  }, null, 1) + '\n')
  console.log(`recorded baseline: JS ${kb(jsBytes)} · CSS ${kb(cssBytes)} (${uniq.length} files)`)
  process.exit(0)
}

const base = JSON.parse(readFileSync(BASELINE, 'utf8'))
const dJs = jsRaw - base.js_raw
const dJsGz = jsBytes - base.js_gz
const dCss = cssBytes - base.css_gz
const problems = []
if (uniq.length < 2 || jsBytes < 50 * 1024 || base.js_raw === undefined) problems.push('found almost nothing in dist/index.html — this check would pass for the wrong reason')
if (dJs > JS_TOLERANCE) problems.push(`critical-path JS grew by ${dJs} B (≈${dJsGz} B gz; allowed ${JS_TOLERANCE} B)`)
if (dCss > CSS_TOLERANCE) problems.push(`critical-path CSS grew by ${kb(dCss)} gz (allowed ${kb(CSS_TOLERANCE)})`)
if (webglOnPath.length) problems.push(`WebGL is on the critical path: ${webglOnPath.join(', ')} — Three.js must only be import()ed`)
if (!lazy3d.length) problems.push('no WebGL chunk was built at all — the login scene is missing, so this check proves nothing about it')
if (asyncBytes > ASYNC_BUDGET) problems.push(`the async login bootstrap is ${asyncBytes} B gz (budget ${ASYNC_BUDGET} B)`)
if (asyncRefs.some((p) => WEBGL.test(readFileSync(join(DIST, p), 'utf8')))) problems.push('the async bootstrap contains WebGL — it must only load the scene')
if (precached3d.length) problems.push(`the service worker precaches ${precached3d.join(', ')} — every installed phone would download it`)
if (lazy3dBytes > LAZY_3D_BUDGET) problems.push(`the lazy 3D chunk is ${kb(lazy3dBytes)} gz (budget ${kb(LAZY_3D_BUDGET)})`)
// Phase 15c: two `@keyframes` with one name is not an error to a browser — the
// LATER one silently wins everywhere the name is used. A login animation named
// `gi-rise` once replaced the dashboard's card entrance on Live.
const srcCss = readFileSync(join(ROOT, 'src', 'index.css'), 'utf8')
const frames = [...srcCss.matchAll(/@keyframes\s+([\w-]+)/g)].map((m) => m[1])
const dupFrames = [...new Set(frames.filter((n, i) => frames.indexOf(n) !== i))]
if (dupFrames.length) problems.push(`@keyframes defined twice in src/index.css (the later one wins everywhere): ${dupFrames.join(', ')}`)

const line = `JS ${kb(jsBytes)} gz (${dJs >= 0 ? '+' : ''}${dJs} B raw vs baseline) · `
  + `CSS ${kb(cssBytes)} (${dCss >= 0 ? '+' : ''}${dCss} B) · lazy 3D ${kb(lazy3dBytes)}`
  + `${lazy3d.length ? ` in ${lazy3d.length} chunk(s)` : ' (none built)'}`
  + ` · async bootstrap ${asyncBytes} B · not precached`
if (problems.length) {
  console.error(`\n== CRITICAL PATH: ❌ FAIL — ${line}`)
  for (const p of problems) console.error(`   · ${p}`)
  process.exit(1)
}
console.log(`== CRITICAL PATH: ✅ PASS — ${line} ==`)
