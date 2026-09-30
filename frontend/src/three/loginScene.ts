/**
 * Phase 14e/15c — the login's Tier 1: the GI mark extruded in gold, in WebGL,
 * standing in the band ABOVE the sign-in card (`.gi-login-crest`). Its own
 * build entry, loaded by `boot.ts` after first paint and only on a capable
 * desktop, so none of it is on the critical path (enforced by
 * `scripts/critical_path_check.mjs`: ≤ 180 KB gz, never on the path, never
 * precached by the service worker).
 *
 * WHAT IT DOES (Phase 15c, rulings Q15-1/Q15-2). The mark rises into place
 * ONCE (an eased intro while a warm key light crosses it), then stands still.
 * Every few seconds a slow band of light sweeps across the gold — the only
 * motion after the intro. There is no glass slab behind it any more (it read
 * as an empty pane behind the card), no drifting dust, and nothing follows the
 * pointer: the mark and the card are static.
 *
 * WHERE IT IS. The canvas covers only the crest band (CSS gives both the same
 * `--gi-crest-h`), and the camera distance is computed from the band's size so
 * the whole mark always fits inside it. The card is laid out BELOW the band, so
 * the two cannot overlap at any window size — the 14e version centred the mark
 * in the margin beside an assumed 400 px card and collided on narrow windows.
 *
 * THE PERFORMANCE RULES, carried from the launcher (plan §5.2):
 *   · renders only while the login is visible (tab hidden → stops);
 *   · caps at 30 fps, and renders NOTHING between sweeps — its clock only
 *     advances while it renders, so waking never jumps;
 *   · adaptive resolution: sustained heavy frames drop the pixel ratio to 1;
 *   · on sign-in the scene is DISPOSED (geometry, materials, textures, the GL
 *     context), not hidden — Ollama shares this GPU;
 *   · no WebGL, a SOFTWARE renderer (SwiftShader, llvmpipe…), frames far over
 *     budget, a lost context, no room for the band, or the SVG not loading →
 *     it removes itself and Tier 0 (the CSS mark in the same band) stays.
 *
 * `.gi-login[data-gi3d]` says what happened, for people and for the E2E spec:
 * "loading" → "on" | "unavailable" (with `data-gi3d-reason` = "software",
 * "slow" or "space" when the device has WebGL but the scene was not drawn).
 */
import {
  ACESFilmicToneMapping, AdditiveBlending, AmbientLight, Box3, BufferGeometry, CanvasTexture,
  Color, DirectionalLight, ExtrudeGeometry, Group, Mesh, MeshPhysicalMaterial, PerspectiveCamera,
  PMREMGenerator, Scene, Sprite, SpriteMaterial, SRGBColorSpace, Vector3, WebGLRenderer,
} from 'three'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import { markShapes } from './markShapes'

const MARK_URL = '/brand/gi-mark.svg'
const FRAME_MS = 1000 / 30
const INTRO_S = 1.6
const SWEEP_S = 2.6             // one pass of light across the gold
const SWEEP_EVERY_MS = 9000     // …then stillness until the next
const SLOW_FRAME_MS = 24        // median over the first frames → give up
const HEAVY_FRAME_MS = 12       // sustained → drop to pixel ratio 1
const MIN_BAND_PX = 60          // a band this short has no room for the mark
const MARK_W = 1.4              // scene units the mark is scaled to

function isSoftwareRenderer(gl: WebGLRenderingContext | WebGL2RenderingContext): boolean {
  try {
    const dbg = gl.getExtension('WEBGL_debug_renderer_info')
    const name = String(dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER))
    return /swiftshader|llvmpipe|softpipe|software|basic render/i.test(name)
  } catch {
    return false
  }
}

/** A soft round sprite, drawn once on a 2D canvas (no image to fetch). */
function glowTexture(inner: string, outer: string): CanvasTexture {
  const c = document.createElement('canvas')
  c.width = c.height = 128
  const g = c.getContext('2d')!
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64)
  grad.addColorStop(0, inner)
  grad.addColorStop(1, outer)
  g.fillStyle = grad
  g.fillRect(0, 0, 128, 128)
  const t = new CanvasTexture(c)
  t.colorSpace = SRGBColorSpace
  return t
}

const easeOutCubic = (x: number) => 1 - Math.pow(1 - Math.min(1, Math.max(0, x)), 3)

/** Mount into `.gi-login`. Returns the teardown; safe to call more than once. */
export function mountLoginScene(host: HTMLElement | null =
  document.querySelector<HTMLElement>('.gi-login')): () => void {
  if (!host) return () => {}
  const el: HTMLElement = host
  el.dataset.gi3d = 'loading'
  const canvas = document.createElement('canvas')
  canvas.className = 'gi-login-fx'
  canvas.setAttribute('aria-hidden', 'true')

  let renderer: WebGLRenderer
  try {
    renderer = new WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'low-power' })
  } catch {
    el.dataset.gi3d = 'unavailable'
    return () => {}
  }
  // ⚠️ A SOFTWARE RENDERER IS NOT A CAPABLE DEVICE. SwiftShader / llvmpipe /
  // Microsoft Basic Render draw WebGL on the CPU; this scene would then take
  // the main thread from the sign-in form. Tier 0 is the right answer there.
  if (isSoftwareRenderer(renderer.getContext())) {
    renderer.dispose()
    renderer.forceContextLoss()
    el.dataset.gi3d = 'unavailable'
    el.dataset.gi3dReason = 'software'
    return () => {}
  }
  el.prepend(canvas)
  // A window too short for the crest band (CSS hides it) gets Tier 0's small
  // mark inside the card instead — there is nowhere to draw this one.
  if (canvas.clientHeight < MIN_BAND_PX) {
    canvas.remove()
    renderer.dispose()
    renderer.forceContextLoss()
    el.dataset.gi3d = 'unavailable'
    el.dataset.gi3dReason = 'space'
    return () => {}
  }
  let dpr = Math.min(window.devicePixelRatio || 1, 2)
  renderer.setPixelRatio(dpr)
  renderer.outputColorSpace = SRGBColorSpace
  renderer.toneMapping = ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.05

  // ── scene, lights, environment ──────────────────────────────────────────
  const scene = new Scene()
  const pmrem = new PMREMGenerator(renderer)
  const room = new RoomEnvironment()
  const env = pmrem.fromScene(room, 0.04).texture
  scene.environment = env
  const camera = new PerspectiveCamera(32, 1, 0.1, 50)
  camera.position.set(0, 0, 6)
  scene.add(new AmbientLight(0xffffff, 0.3))
  const key = new DirectionalLight(0xffe2a8, 2.4)      // crosses the mark during the intro
  const rim = new DirectionalLight(0x6fa8ff, 1.5)
  rim.position.set(4, -2, -3)
  const sweep = new DirectionalLight(0xfff1c8, 0)      // the slow band of light, between intros
  scene.add(key, rim, sweep)

  // A fixed three-quarter pose: enough angle for the bevel to catch the
  // light, never moving (ruling Q15-1).
  const rig = new Group()
  rig.rotation.set(0.06, -0.16, 0)
  scene.add(rig)
  const geos: BufferGeometry[] = []
  const textures: CanvasTexture[] = []

  // ── the halo behind the mark ────────────────────────────────────────────
  const haloTex = glowTexture('rgba(255, 200, 90, 0.55)', 'rgba(255, 200, 90, 0)')
  textures.push(haloTex)
  const haloMat = new SpriteMaterial({ map: haloTex, transparent: true, depthWrite: false,
    blending: AdditiveBlending, opacity: 0 })
  const halo = new Sprite(haloMat)
  halo.position.z = -0.35
  rig.add(halo)

  // ── the mark (filled in when the SVG arrives) ───────────────────────────
  const gold = new MeshPhysicalMaterial({
    color: new Color(0xd59e0e), metalness: 1, roughness: 0.28, clearcoat: 0.7,
    clearcoatRoughness: 0.16, envMapIntensity: 1.35,
  })
  const mark = new Group()
  rig.add(mark)
  let markH = MARK_W * 246 / 412   // the SVG's own aspect, until it arrives

  // ── the loop ────────────────────────────────────────────────────────────
  let raf = 0
  let last = 0
  let alive = true
  let clock = 0                   // seconds; advances only while rendering
  let introAt = -1                // clock time the mark arrived
  let sweepAt = -1                // clock time the current sweep began
  let sweepTimer = 0
  let compiling = false
  const probe: number[] = []     // frame costs after the warm-up frames
  let warm = 2                   // frames skipped before measuring
  let heavy = 0

  const place = () => {
    // The band is not at the top of the page (the crest + card column is
    // centred), so the canvas follows it rather than assuming top: 0.
    const band = el.querySelector<HTMLElement>('.gi-login-crest')
    if (band) canvas.style.top = `${band.offsetTop}px`
    const w = canvas.clientWidth
    const h = canvas.clientHeight
    if (!w || !h) return
    renderer.setSize(w, h, false)
    camera.aspect = w / h
    // Stand back far enough that the whole mark — with room for the rise —
    // fits the band: 80 % of its height, 70 % of its width, whichever binds.
    const need = Math.max(markH / 0.8, MARK_W / 0.7 / camera.aspect)
    camera.position.z = need / 2 / Math.tan((camera.fov * Math.PI) / 360)
    camera.updateProjectionMatrix()
    halo.scale.set(MARK_W * 1.7, Math.min(markH * 1.35, need), 1)
  }

  const frame = (now: number) => {
    raf = 0
    if (!alive || document.hidden) return
    // while the mark's shaders compile, draw nothing — a render now would
    // compile them synchronously and stall the page
    if (compiling || (last && now - last < FRAME_MS)) { raf = requestAnimationFrame(frame); return }
    clock += last ? Math.min(now - last, 50) / 1000 : 0
    last = now
    const t = clock

    const intro = introAt < 0 ? 0 : easeOutCubic((t - introAt) / INTRO_S)
    mark.position.y = (1 - intro) * -0.5
    mark.rotation.y = (1 - intro) * -1.4
    mark.scale.setScalar(0.6 + 0.4 * intro)
    haloMat.opacity = 0.34 * intro
    key.position.set(-4 + 6 * intro, 4, 5)
    // the sweep: a band of light crossing left to right, fading in and out,
    // so it starts and ends at nothing and never jumps
    const p = sweepAt < 0 ? 1 : Math.min(1, (t - sweepAt) / SWEEP_S)
    sweep.intensity = sweepAt < 0 ? 0 : 3.2 * Math.sin(Math.PI * p)
    sweep.position.set(-6 + 12 * p, 2, 5)

    const f0 = performance.now()
    renderer.render(scene, camera)
    const cost = performance.now() - f0
    if (introAt >= 0) {        // measured only once the shaders are compiled
      // ⚠️ THE FRAME BUDGET. A device that cannot draw this well inside a
      // frame is not a capable one, whatever it reports: give the main thread
      // back to the form and keep Tier 0.
      // Judged on the MEDIAN of six frames AFTER two warm-up frames: the first
      // frames can carry one-off costs (texture upload, a late shader) that
      // say nothing about the device, and a mean would let one spike decide.
      if (warm > 0) warm -= 1
      else if (probe.length < 6) {
        probe.push(cost)
        if (probe.length === 6) {
          const median = [...probe].sort((a, b) => a - b)[3]
          if (median > SLOW_FRAME_MS) {
            teardown()
            el.dataset.gi3d = 'unavailable'
            el.dataset.gi3dReason = 'slow'
            return
          }
        }
      }
      heavy = cost > HEAVY_FRAME_MS ? heavy + 1 : 0
      if (heavy > 10 && dpr > 1) { dpr = 1; renderer.setPixelRatio(1); place(); heavy = 0 }
      if (el.dataset.gi3d !== 'on') el.dataset.gi3d = 'on'
    }
    const introRunning = introAt < 0 || t - introAt < INTRO_S
    const sweepRunning = sweepAt >= 0 && p < 1
    if (introRunning || sweepRunning) { raf = requestAnimationFrame(frame); return }
    // Still: draw nothing until the next sweep.
    if (sweepAt >= 0) sweepAt = -1
    if (!sweepTimer) sweepTimer = window.setTimeout(startSweep, SWEEP_EVERY_MS)
  }
  const wake = () => {
    if (!alive || raf || document.hidden) return
    last = 0
    raf = requestAnimationFrame(frame)
  }
  function startSweep() {
    sweepTimer = 0
    if (!alive) return
    sweepAt = clock
    wake()
  }
  const onVisibility = () => { if (!document.hidden) wake() }
  const onResize = () => { place(); if (introAt >= 0) { last = 0; raf = raf || requestAnimationFrame(frame) } }
  const onLost = (e: Event) => { e.preventDefault(); teardown(); el.dataset.gi3d = 'unavailable' }

  window.addEventListener('resize', onResize)
  document.addEventListener('visibilitychange', onVisibility)
  canvas.addEventListener('webglcontextlost', onLost)
  place()

  fetch(MARK_URL).then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    return r.text()
  }).then((svg) => {
    if (!alive) return
    const { shapes, width, height } = markShapes(svg)
    const depth = 30
    for (const shape of shapes) {
      const geo = new ExtrudeGeometry(shape, {
        depth, bevelEnabled: true, bevelThickness: 5, bevelSize: 3, bevelSegments: 3, curveSegments: 5,
      })
      geo.translate(-width / 2, height / 2, -depth / 2)
      geo.scale(MARK_W / width, MARK_W / width, MARK_W / width)
      geos.push(geo)
      mark.add(new Mesh(geo, gold))
    }
    markH = MARK_W * height / width
    // ⚠️ CENTRE IN THE MARK'S OWN SPACE. `setFromObject(mark)` measures in
    // WORLD space, i.e. through the intro pose the first frames have already
    // applied (dropped 0.5, scaled 0.6, turned) — subtracting that centre left
    // the finished mark half a band too high, cut off at the top of the window
    // (2026-09-30). Headless runs never saw it: a software renderer takes Tier 0.
    const box = new Box3()
    for (const g of geos) { g.computeBoundingBox(); box.union(g.boundingBox!) }
    const c = box.getCenter(new Vector3())
    for (const g of geos) g.translate(-c.x, -c.y, -c.z)
    place()
    // Compile every shader BEFORE the intro starts (in parallel where the GPU
    // driver allows): the physical materials can take 100 ms+ to compile, and
    // doing it inside the first frame would stutter the intro and trip the
    // frame budget on a perfectly capable machine.
    compiling = true
    return renderer.compileAsync(scene, camera)
  }).then(() => {
    compiling = false
    if (!alive) return
    introAt = clock
    wake()
  }).catch(() => {
    // no mark, no scene: Tier 0 stays exactly as it was
    teardown()
    el.dataset.gi3d = 'unavailable'
  })
  wake()

  function teardown() {
    if (!alive) return
    alive = false
    if (raf) cancelAnimationFrame(raf)
    if (sweepTimer) window.clearTimeout(sweepTimer)
    window.removeEventListener('resize', onResize)
    document.removeEventListener('visibilitychange', onVisibility)
    canvas.removeEventListener('webglcontextlost', onLost)
    geos.forEach((g) => g.dispose())
    textures.forEach((x) => x.dispose())
    ;[gold, haloMat].forEach((m) => m.dispose())
    env.dispose()
    pmrem.dispose()
    room.traverse((o) => {
      const m = o as Mesh
      m.geometry?.dispose?.()
      const mat = m.material as { dispose?: () => void } | undefined
      mat?.dispose?.()
    })
    renderer.dispose()
    renderer.forceContextLoss()
    canvas.remove()
    if (el.dataset.gi3d === 'on' || el.dataset.gi3d === 'loading') delete el.dataset.gi3d
  }
  return teardown
}

// Loaded as its own module <script> by boot.ts (never import()ed — see there).
;(window as Window & { __giLoginScene?: typeof mountLoginScene }).__giLoginScene = mountLoginScene
window.dispatchEvent(new Event('gi-login-scene'))
