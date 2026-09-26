/**
 * The GI mark as Three.js shapes, read from /brand/gi-mark.svg.
 *
 * Ported from gi-launcher's `static/intro/logo.js` (plan §5.4). The SVG is
 * traced from the PNG logo and uses only absolute M, L, C and Z commands, so a
 * small parser covers it — no SVGLoader (a second parser, and more bytes in the
 * lazy chunk). No DOM here.
 *
 * ⚠️ LAZY ONLY. This file imports `three`; it may be reached from
 * `loginScene.ts` and nowhere on the critical path. `npm run build` fails if
 * WebGL code lands in a chunk dist/index.html loads.
 */
import { ShapePath, type Shape } from 'three'

const TOKEN = /[MLCZmlcz]|-?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?/g

/** Parse one path's `d` into a ShapePath (SVG y-down flipped to y-up). */
export function parsePath(d: string): ShapePath {
  const path = new ShapePath()
  const tokens = d.match(TOKEN) ?? []
  let i = 0
  let cmd: string | null = null
  const num = () => {
    const v = Number(tokens[i++])
    if (!Number.isFinite(v)) throw new Error(`bad number in path near token ${i}`)
    return v
  }
  const pt = (): [number, number] => { const x = num(); const y = -num(); return [x, y] }
  while (i < tokens.length) {
    if (/[A-Za-z]/.test(tokens[i])) cmd = tokens[i++]
    if (cmd === 'M') { path.moveTo(...pt()); cmd = 'L' }       // extra pairs after M are lines
    else if (cmd === 'L') path.lineTo(...pt())
    else if (cmd === 'C') { const a = pt(); const b = pt(); const c = pt(); path.bezierCurveTo(...a, ...b, ...c) }
    else if (cmd === 'Z' || cmd === 'z') {
      (path.currentPath as unknown as { closePath?: () => void } | null)?.closePath?.()
      cmd = null
    } else throw new Error(`unsupported path command ${cmd} (only absolute M, L, C, Z)`)
  }
  return path
}

/** Every <path d> of an SVG, as shapes, with the viewBox size. */
export function markShapes(svgText: string): { shapes: Shape[]; width: number; height: number } {
  const view = /viewBox="([^"]+)"/.exec(svgText)
  if (!view) throw new Error('SVG has no viewBox')
  const [, , width, height] = view[1].trim().split(/[\s,]+/).map(Number)
  const shapes: Shape[] = []
  for (const m of svgText.matchAll(/<path\b[^>]*\sd="([^"]+)"/g)) {
    // evenodd fill traced by OpenCV; since r18x toShapes() finds the holes by
    // even-odd containment itself (the launcher's r186 passed a winding flag
    // that is no longer read).
    shapes.push(...parsePath(m[1]).toShapes())
  }
  return { shapes, width, height }
}
