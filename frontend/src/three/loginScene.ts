/**
 * Phase 14e — the login's Tier 1: the GI mark extruded in gold over a glass
 * slab, in WebGL. Its own build entry, loaded by `boot.ts` after first paint
 * and only on a capable desktop, so none of it is on the critical path
 * (enforced by `scripts/critical_path_check.mjs`: ≤ 180 KB gz, never on the
 * path, never precached by the service worker).
 *
 * WHAT IT DOES. The mark rises and turns into place (an eased intro), a warm
 * key light sweeps across the gold, a soft halo glows behind it and a little
 * gold dust drifts through the glass. The pointer turns the whole rig — and
 * the sign-in card turns WITH it (CSS variables on `.gi-login`, two degrees at
 * most), so the page reads as one 3D space, not a video behind a form.
 *
 * THE PERFORMANCE RULES, carried from the launcher (plan §5.2):
 *   · renders only while the login is visible (tab hidden → stops);
 *   · caps at 30 fps, and drops to ZERO when nothing moves — the scene settles
 *     a few seconds after the pointer stops, then waits for it. Its clock only
 *     advances while it renders, so waking never jumps;
 *   · adaptive resolution: sustained heavy frames drop the pixel ratio to 1;
 *   · on sign-in the scene is DISPOSED (geometry, materials, textures, the GL
 *     context), not hidden — Ollama shares this GPU;
 *   · no WebGL, a SOFTWARE renderer (SwiftShader, llvmpipe…), frames far over
 *     budget, a lost context or the SVG not loading → it removes itself and
 *     Tier 0 (the CSS glass) stays exactly as it was.
 *
 * `.gi-login[data-gi3d]` says what happened, for people and for the E2E spec:
 * "loading" → "on" | "unavailable" (with `data-gi3d-reason` = "software" or
 * "slow" when the device has WebGL but could not afford the scene).
 */
import {
  ACESFilmicToneMapping, AdditiveBlending, AmbientLight, Box3, BufferAttribute, BufferGeometry,
  CanvasTexture, Color, DirectionalLight, ExtrudeGeometry, Group, Mesh, MeshBasicMaterial,
  MeshPhysicalMaterial, PerspectiveCamera, PMREMGenerator, Points, PointsMaterial, Scene, Shape,
  ShapeGeometry, Sprite, SpriteMaterial, SRGBColorSpace, Vector3, WebGLRenderer,
} from 'three'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import { markShapes } from './markShapes'

const MARK_URL = '/brand/gi-mark.svg'
const FRAME_MS = 1000 / 30
const SETTLE_MS = 3000
const INTRO_S = 1.6
const SLOW_FRAME_MS = 24        // average over the first frames → give up
const HEAVY_FRAME_MS = 12       // sustained → drop to pixel ratio 1
const DUST = 160

function isSoftwareRenderer(gl: WebGLRenderingContext | WebGL2RenderingContext): boolean {
  try {
    const dbg = gl.getExtension('WEBGL_debug_renderer_info')
    const name = String(dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER))
    return /swiftshader|llvmpipe|softpipe|software|basic render/i.test(name)
  } catch {
    return false
  }
}

function roundedRect(w: number, h: number, r: number): Shape {
  const s = new Shape()
  const x = -w / 2
  const y = -h / 2
  s.moveTo(x + r, y)
  s.lineTo(x + w - r, y); s.quadraticCurveTo(x + w, y, x + w, y + r)
  s.lineTo(x + w, y + h - r); s.quadraticCurveTo(x + w, y + h, x + w - r, y + h)
  s.lineTo(x + r, y + h); s.quadraticCurveTo(x, y + h, x, y + h - r)
  s.lineTo(x, y + r); s.quadraticCurveTo(x, y, x + r, y)
  return s
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
  const key = new DirectionalLight(0xffe2a8, 2.4)      // the sweep
  const rim = new DirectionalLight(0x6fa8ff, 1.5)
  rim.position.set(4, -2, -3)
  scene.add(key, rim)

  const rig = new Group()          // pointer parallax turns this
  scene.add(rig)
  const geos: BufferGeometry[] = []
  const textures: CanvasTexture[] = []

  // ── the glass slab, with a thin gold rim ────────────────────────────────
  const glass = new MeshPhysicalMaterial({
    color: new Color(0x7fa6e6), metalness: 0, roughness: 0.12, transparent: true,
    opacity: 0.13, depthWrite: false, clearcoat: 1, clearcoatRoughness: 0.1, envMapIntensity: 1.1,
  })
  const slabGeo = new ExtrudeGeometry(roundedRect(1.75, 1.2, 0.12), {
    depth: 0.06, bevelEnabled: true, bevelThickness: 0.02, bevelSize: 0.02,
    bevelSegments: 3, curveSegments: 8,
  })
  slabGeo.translate(0, 0, -0.4)
  geos.push(slabGeo)
  rig.add(new Mesh(slabGeo, glass))
  const rimShape = roundedRect(1.8, 1.25, 0.14)
  rimShape.holes.push(roundedRect(1.77, 1.22, 0.13))
  const rimGeo = new ShapeGeometry(rimShape, 12)
  rimGeo.translate(0, 0, -0.3)
  geos.push(rimGeo)
  const rimMat = new MeshBasicMaterial({ color: 0xd4af37, transparent: true, opacity: 0.55, toneMapped: false })
  rig.add(new Mesh(rimGeo, rimMat))

  // ── the halo behind the mark ────────────────────────────────────────────
  const haloTex = glowTexture('rgba(255, 200, 90, 0.55)', 'rgba(255, 200, 90, 0)')
  textures.push(haloTex)
  const haloMat = new SpriteMaterial({ map: haloTex, transparent: true, depthWrite: false,
    blending: AdditiveBlending, opacity: 0 })
  const halo = new Sprite(haloMat)
  halo.scale.set(2.6, 1.9, 1)
  halo.position.z = -0.35
  rig.add(halo)

  // ── gold dust drifting through the glass ────────────────────────────────
  const dustPos = new Float32Array(DUST * 3)
  const dustSeed = new Float32Array(DUST)
  for (let i = 0; i < DUST; i++) {
    dustPos[i * 3] = (Math.random() - 0.5) * 2.6
    dustPos[i * 3 + 1] = (Math.random() - 0.5) * 1.8
    dustPos[i * 3 + 2] = (Math.random() - 0.5) * 1.2
    dustSeed[i] = Math.random() * Math.PI * 2
  }
  const dustGeo = new BufferGeometry()
  dustGeo.setAttribute('position', new BufferAttribute(dustPos, 3))
  geos.push(dustGeo)
  const dotTex = glowTexture('rgba(255, 225, 150, 1)', 'rgba(255, 225, 150, 0)')
  textures.push(dotTex)
  const dustMat = new PointsMaterial({ map: dotTex, size: 0.035, transparent: true, opacity: 0,
    depthWrite: false, blending: AdditiveBlending, color: 0xffd98a })
  rig.add(new Points(dustGeo, dustMat))

  // ── the mark (filled in when the SVG arrives) ───────────────────────────
  const gold = new MeshPhysicalMaterial({
    color: new Color(0xd59e0e), metalness: 1, roughness: 0.28, clearcoat: 0.7,
    clearcoatRoughness: 0.16, envMapIntensity: 1.35,
  })
  const mark = new Group()
  rig.add(mark)

  // ── the loop ────────────────────────────────────────────────────────────
  let raf = 0
  let last = 0
  let settledAt = 0
  let alive = true
  let clock = 0                   // seconds; advances only while rendering
  let introAt = -1                // clock time the mark arrived
  let compiling = false
  const target = { x: 0, y: 0 }
  const cur = { x: 0, y: 0 }
  const probe: number[] = []     // frame costs after the warm-up frames
  let warm = 2                   // frames skipped before measuring
  let heavy = 0

  const place = () => {
    const w = el.clientWidth || window.innerWidth
    const h = el.clientHeight || window.innerHeight
    renderer.setSize(w, h, false)
    camera.aspect = w / h
    camera.updateProjectionMatrix()
    // centred in the margin LEFT of the centred 400 px card, whatever the width
    const halfW = Math.tan((camera.fov * Math.PI) / 360) * camera.position.z * camera.aspect
    const cardLeft = Math.max(0, (w - 400) / 2) / w
    rig.position.set(-halfW * (1 - cardLeft), 0.2, 0)
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

    cur.x += (target.x - cur.x) * 0.08
    cur.y += (target.y - cur.y) * 0.08
    const intro = introAt < 0 ? 0 : easeOutCubic((t - introAt) / INTRO_S)
    rig.rotation.y = -0.35 + cur.x * 0.45 + Math.sin(t * 0.5) * 0.06
    rig.rotation.x = 0.12 + cur.y * 0.3 + Math.cos(t * 0.4) * 0.03
    mark.position.y = (1 - intro) * -0.5 + Math.sin(t * 0.8) * 0.04
    mark.rotation.y = (1 - intro) * -1.4
    mark.scale.setScalar(0.6 + 0.4 * intro)
    haloMat.opacity = 0.38 * intro * (0.85 + 0.15 * Math.sin(t * 1.3))
    dustMat.opacity = 0.7 * intro
    // the key light sweeps across the gold, then keeps a slow drift
    key.position.set(-4 + 6 * intro + Math.sin(t * 0.35) * 1.2, 4, 5)
    for (let i = 0; i < DUST; i++) {
      dustPos[i * 3 + 1] += 0.0016 + Math.sin(t + dustSeed[i]) * 0.0008
      if (dustPos[i * 3 + 1] > 0.9) dustPos[i * 3 + 1] = -0.9
    }
    dustGeo.attributes.position.needsUpdate = true
    // the sign-in card turns with the scene — two degrees, never more
    el.style.setProperty('--gi-ry', `${(cur.x * 2).toFixed(2)}deg`)
    el.style.setProperty('--gi-rx', `${(-cur.y * 2).toFixed(2)}deg`)

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
    const moving = Math.abs(target.x - cur.x) + Math.abs(target.y - cur.y) > 0.002
      || (introAt >= 0 && t - introAt < INTRO_S)
    if (moving) settledAt = now
    // drop to 0 fps once settled; the pointer wakes it
    if (now - settledAt < SETTLE_MS || introAt < 0) raf = requestAnimationFrame(frame)
  }
  const wake = () => {
    if (!alive || raf || document.hidden) return
    settledAt = performance.now()
    last = 0
    raf = requestAnimationFrame(frame)
  }
  const onPointer = (e: PointerEvent) => {
    target.x = (e.clientX / window.innerWidth) * 2 - 1
    target.y = (e.clientY / window.innerHeight) * 2 - 1
    wake()
  }
  const onVisibility = () => { if (!document.hidden) wake() }
  const onResize = () => { place(); wake() }
  const onLost = (e: Event) => { e.preventDefault(); teardown(); el.dataset.gi3d = 'unavailable' }

  window.addEventListener('pointermove', onPointer, { passive: true })
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
      geo.scale(1.4 / width, 1.4 / width, 1.4 / width)
      geos.push(geo)
      mark.add(new Mesh(geo, gold))
    }
    const box = new Box3().setFromObject(mark)
    const c = box.getCenter(new Vector3())
    mark.children.forEach((m) => m.position.sub(c))
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
    window.removeEventListener('pointermove', onPointer)
    window.removeEventListener('resize', onResize)
    document.removeEventListener('visibilitychange', onVisibility)
    canvas.removeEventListener('webglcontextlost', onLost)
    geos.forEach((g) => g.dispose())
    textures.forEach((x) => x.dispose())
    ;[gold, glass, rimMat, haloMat, dustMat].forEach((m) => m.dispose())
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
    el.style.removeProperty('--gi-rx')
    el.style.removeProperty('--gi-ry')
    if (el.dataset.gi3d === 'on' || el.dataset.gi3d === 'loading') delete el.dataset.gi3d
  }
  return teardown
}

// Loaded as its own module <script> by boot.ts (never import()ed — see there).
;(window as Window & { __giLoginScene?: typeof mountLoginScene }).__giLoginScene = mountLoginScene
window.dispatchEvent(new Event('gi-login-scene'))
