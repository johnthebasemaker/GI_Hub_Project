/**
 * Part of `npm run test:nav` — Phase 21g: every sidebar page has a narrated
 * TOUR (frontend/src/demo/tours.ts, PROPOSED_PHASE21_PLAN §3.4).
 *
 * A page added to the sidebar without a tour fails here, by name, so the
 * self-driving demo never meets a page it cannot explain. Record and master
 * pages share one template each (`prefix: true`), because they are generated
 * from the same entity list.
 *
 * Reads the sources with the TypeScript parser, like nav_routes_check.mjs.
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import ts from 'typescript'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (rel) => ts.createSourceFile(rel, readFileSync(join(ROOT, rel), 'utf8'),
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
const walk = (node, fn) => { fn(node); node.forEachChild((c) => walk(c, fn)) }

// the sidebar: every `key: '/…'` in the manifest, and the two generated families
const pages = new Set()
let templates = 0
walk(read('src/config/nav.tsx'), (n) => {
  if (!ts.isPropertyAssignment(n) || n.name.getText() !== 'key') return
  if (ts.isStringLiteral(n.initializer) && n.initializer.text.startsWith('/')) pages.add(n.initializer.text)
  if (ts.isTemplateExpression(n.initializer)) {
    const head = n.initializer.head.text
    if (head.startsWith('/')) { pages.add(`${head}*`); templates += 1 }
  }
})

// the tours: every `t('/path', …)` call and every `{ path: '/x/', prefix: true }`
const exact = new Set()
const prefixes = []
walk(read('src/demo/tours.ts'), (n) => {
  if (ts.isCallExpression(n) && n.expression.getText() === 't' && ts.isStringLiteral(n.arguments[0])) {
    exact.add(n.arguments[0].text)
  }
  if (ts.isObjectLiteralExpression(n)) {
    const prop = (k) => n.properties.find((p) => ts.isPropertyAssignment(p) && p.name.getText() === k)
    const path = prop('path')
    const pre = prop('prefix')
    if (path && ts.isStringLiteral(path.initializer)) {
      if (pre && pre.initializer.kind === ts.SyntaxKind.TrueKeyword) prefixes.push(path.initializer.text)
      else exact.add(path.initializer.text)
    }
  }
})

const missing = [...pages].filter((p) => (p.endsWith('*')
  ? !prefixes.includes(p.slice(0, -1))
  : !exact.has(p) && !prefixes.some((x) => p.startsWith(x))))
const stale = [...exact].filter((p) => !pages.has(p))

if (missing.length || stale.length) {
  console.error(`== PAGE TOURS: ❌ FAIL — ${missing.length} sidebar page(s) without a tour, ${stale.length} tour(s) for no page ==`)
  for (const p of missing) console.error(`   · no tour: ${p}  → add one to frontend/src/demo/tours.ts`)
  for (const p of stale) console.error(`   · tour for a page the sidebar no longer has: ${p}`)
  process.exit(1)
}
console.log(`== PAGE TOURS: ✅ PASS — ${pages.size} sidebar page(s) (${templates} generated families), every one toured ==`)
