/**
 * `node scripts/nav_access_dump.mjs '<roleLevelsJson>'` — dump, as JSON, which
 * roles may open each route in the nav manifest.
 *
 * WHY THIS EXISTS. Phase 12 records role tutorials, and a tutorial that walks
 * into a page its role cannot open is a tutorial that teaches a lie — the app
 * fails closed (rule 14) and the viewer is shown a redirect. That has to be
 * caught before a browser is started, so the batch runner can refuse a bad
 * script in `--dry-run`.
 *
 * ⚠️ THIS IS A MODEL OF `canAccessPath`, NOT `canAccessPath`. It re-evaluates
 * the same two rules (`anyRole`, `minLevel`) plus the admin shadow, the
 * `writes` gate and the enclosing group's rule. A second implementation of an
 * access decision is exactly the thing this repository distrusts — so it is
 * used only as a FAST PRE-FLIGHT, and `tools/generate_tutorial.py` checks it
 * against the ground truth at record time: the recorder logs every path the
 * browser actually landed on, and a redirect contradicts a PASS here loudly.
 * Same shape as the SME dual-engine parity: two implementations, one oracle.
 *
 * ⚠️ AND IT REPORTS `unresolved` RATHER THAN GUESSING. `/records/*` and
 * `/master/*` are built by `.map()` over `entities.ts` and are listed as
 * unresolved unless that file parses cleanly. An unresolved route is reported
 * as UNKNOWN, never as allowed — a SKIP is not a PASS (rule 16).
 *
 * Role levels are NOT restated here: they are passed in from
 * `backend/api/auth.py`'s ROLE_META, which is the one place they are defined.
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import ts from 'typescript'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (rel) => {
  const path = join(ROOT, rel)
  return ts.createSourceFile(path, readFileSync(path, 'utf8'),
                             ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
}
const walk = (node, fn) => { fn(node); node.forEachChild((c) => walk(c, fn)) }
const str = (n) => (n && ts.isStringLiteral(n) ? n.text : undefined)

const roleLevels = JSON.parse(process.argv[2] ?? '{}')
const ROLES = Object.keys(roleLevels)
const nav = read('src/config/nav.tsx')

// ── 1. resolve the module's string and string-array constants ───────────────
const scope = new Map()
walk(nav, (n) => {
  if (!ts.isVariableDeclaration(n) || !n.initializer) return
  const name = n.name.getText()
  const init = n.initializer
  if (ts.isStringLiteral(init)) { scope.set(name, init.text); return }
  if (!ts.isArrayLiteralExpression(init)) return
  const out = []
  let ok = true
  for (const el of init.elements) {
    if (ts.isStringLiteral(el)) out.push(el.text)
    else if (ts.isIdentifier(el) && typeof scope.get(el.text) === 'string') out.push(scope.get(el.text))
    else if (ts.isSpreadElement(el) && ts.isIdentifier(el.expression)
             && Array.isArray(scope.get(el.expression.text))) out.push(...scope.get(el.expression.text))
    else ok = false
  }
  if (ok) scope.set(name, out)
})

const unresolved = []

/** `{ anyRole: X }` · `{ minLevel: n }` · `w({...})` → a plain rule object. */
function ruleOf(node) {
  if (!node) return null
  let obj = node
  let writes = false
  if (ts.isCallExpression(obj)) {                      // w({...}) adds writes
    if (obj.expression.getText() === 'w') writes = true
    obj = obj.arguments[0]
  }
  if (!obj || !ts.isObjectLiteralExpression(obj)) return { unresolved: obj?.getText() }
  const rule = { writes: writes || undefined }
  for (const p of obj.properties) {
    if (!ts.isPropertyAssignment(p)) continue
    const k = p.name.getText()
    const v = p.initializer
    if (k === 'writes') rule.writes = v.getText() === 'true'
    else if (k === 'minLevel' && ts.isNumericLiteral(v)) rule.minLevel = Number(v.text)
    else if (k === 'anyRole') {
      if (ts.isArrayLiteralExpression(v)) {
        const arr = []
        let ok = true
        for (const el of v.elements) {
          if (ts.isStringLiteral(el)) arr.push(el.text)
          else if (ts.isIdentifier(el) && typeof scope.get(el.text) === 'string') arr.push(scope.get(el.text))
          else ok = false
        }
        if (ok) rule.anyRole = arr; else return { unresolved: v.getText() }
      } else if (ts.isIdentifier(v) && Array.isArray(scope.get(v.text))) {
        rule.anyRole = scope.get(v.text)
      } else return { unresolved: v.getText() }
    }
  }
  return rule
}

// Oversight roles never satisfy a rank check above 0 (nav.tsx, Phase 17a).
// Read from nav.tsx's own literal; an absent list is unresolved, not empty.
const OVERSIGHT = scope.get('OVERSIGHT_ROLE_LIST')
if (!Array.isArray(OVERSIGHT)) unresolved.push('OVERSIGHT_ROLE_LIST')
// The view-only roles (src/auth/readOnly.ts READ_ONLY_ROLES).
const READ_ONLY = new Set(['auditor', 'qc_hod'])

/** The same decision `canAccess` makes, in the same order. */
function allows(role, rule) {
  if (!rule || rule.unresolved) return null
  // `writes` is a capability gate checked BEFORE the admin shadow, exactly as
  // in nav.tsx.
  if (rule.writes && READ_ONLY.has(role)) return false
  if (role === 'admin') return true
  if (rule.anyRole) return rule.anyRole.includes(role)
  if (rule.minLevel !== undefined) {
    if (!Array.isArray(OVERSIGHT)) return null
    if (OVERSIGHT.includes(role) && rule.minLevel > 0) return false
    return (roleLevels[role] ?? 0) >= rule.minLevel
  }
  return true
}

// ── 2. walk NAV: groups carry a gate their children inherit ─────────────────
const routes = {}
// The menu label of each route — read by the assistant's navigation lane
// (backend/api/ai/system_one.py, Phase 17) so "open lots and expiry" resolves
// against the words the user actually sees, never a second hand-kept list.
const labels = {}
// …and the label of the group it sits in ("Warehouse" for /warehouse, whose
// own label is "Receiving & DN") — what people call a portal out loud.
const groupLabels = {}
const navDecl = (() => {
  let found
  walk(nav, (n) => {
    if (ts.isVariableDeclaration(n) && n.name.getText() === 'NAV') found = n.initializer
  })
  return found
})()

if (navDecl && ts.isArrayLiteralExpression(navDecl)) {
  for (const group of navDecl.elements) {
    if (!ts.isObjectLiteralExpression(group)) { unresolved.push('a NAV group'); continue }
    let groupRule = null
    let children = null
    let groupLabel
    for (const p of group.properties) {
      if (!ts.isPropertyAssignment(p)) continue
      if (p.name.getText() === 'label') groupLabel = str(p.initializer)
      if (p.name.getText() === 'access') groupRule = ruleOf(p.initializer)
      if (p.name.getText() === 'children') children = p.initializer
    }
    if (!children || !ts.isArrayLiteralExpression(children)) {
      // A `.map()`-built group (/records, /master). Named, never guessed.
      unresolved.push(children ? children.getText().slice(0, 60) : 'group with no children')
      continue
    }
    for (const child of children.elements) {
      if (!ts.isObjectLiteralExpression(child)) { unresolved.push('a NAV child'); continue }
      let key, label, rule = null
      for (const p of child.properties) {
        if (!ts.isPropertyAssignment(p)) continue
        if (p.name.getText() === 'key') key = str(p.initializer)
        if (p.name.getText() === 'label') label = str(p.initializer)
        if (p.name.getText() === 'access') rule = ruleOf(p.initializer)
      }
      if (!key) continue
      if (label) labels[key] = label
      if (groupLabel) groupLabels[key] = groupLabel
      const allowed = []
      let unknown = false
      for (const role of ROLES) {
        const g = groupRule ? allows(role, groupRule) : true
        const c = allows(role, rule)
        if (g === null || c === null) { unknown = true; break }
        if (g && c) allowed.push(role)
      }
      if (unknown) { unresolved.push(key); continue }
      routes[key] = allowed
    }
  }
}

// ── 3. the guard's own public allowlist, read rather than restated ──────────
const publics = []
walk(nav, (n) => {
  if (!ts.isVariableDeclaration(n) || n.name.getText() !== 'PUBLIC_PATH_PREFIXES') return
  walk(n, (c) => { const v = str(c); if (v) publics.push(v) })
})

// Sanity, same instinct as nav_routes_check.mjs: if the parser found almost
// nothing, every downstream check would pass for the wrong reason.
const sane = Object.keys(routes).length >= 30 && publics.length >= 1
process.stdout.write(JSON.stringify(
  { sane, routes, labels, groupLabels, publics, unresolved: [...new Set(unresolved)], roleLevels }, null, 2))
