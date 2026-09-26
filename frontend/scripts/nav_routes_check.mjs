/**
 * `npm run test:nav` — every routable page must declare who may open it.
 *
 * WHY THIS EXISTS.
 *
 * `canAccessPath()` used to `return true` for any path it did not recognise.
 * Nothing exploited it, because every <Route> in App.tsx happened to have a
 * NAV entry — but the failure mode was "let them in", so the next page added
 * without a manifest entry would have been reachable by every signed-in user
 * in the company, silently, with nothing to notice it.
 *
 * That guard now fails CLOSED. Which converts the old silent leak into a new
 * silent lockout: forget the manifest entry and the page becomes unreachable
 * instead of universal. Better, but still silent. This check is the part that
 * makes it LOUD — a route with no rule fails the build, and the author is told
 * which file to edit before either failure can ship.
 *
 * HOW IT READS THE SOURCE. Through the TypeScript compiler's own parser, not
 * a regex. Both files are hand-edited manifests full of string literals that a
 * regex would match inside comments and doc examples; a parser only ever sees
 * the real thing. TypeScript is already a devDependency, so this adds nothing
 * to install and never touches the network.
 *
 * Run by `npm test` and in CI.
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

// ── 1. every path declared in App.tsx ───────────────────────────────────────
// `<Route path="x">` → '/x'; `<Route index>` → '/'. Nested routes inherit the
// layout route's empty path, so a plain join with '/' is correct here.
function routePaths() {
  const out = new Set()
  walk(read('src/App.tsx'), (n) => {
    if (!ts.isJsxSelfClosingElement(n) && !ts.isJsxOpeningElement(n)) return
    if (n.tagName.getText() !== 'Route') return
    let path
    let isIndex = false
    let element
    for (const a of n.attributes.properties) {
      if (!ts.isJsxAttribute(a)) continue
      const name = a.name.getText()
      if (name === 'index') isIndex = true
      if (name === 'path' && a.initializer) path = str(a.initializer)
      if (name === 'element' && a.initializer) element = a.initializer.getText()
    }
    // The catch-all is allowed ONLY if it renders nothing: it exists so the
    // layout (and its guard's redirect) renders for an unknown path. A `*`
    // route that rendered a page would be a page with no access rule.
    if (path === '*' && element === '{null}') return
    if (isIndex) out.add('/')
    else if (path) out.add(path.startsWith('/') ? path : `/${path}`)
  })
  return out
}

// ── 2. every key the manifest claims ────────────────────────────────────────
// Any object literal carrying a `key:` string that looks like a route. That
// deliberately over-collects (it also sees group ids and menu items), which is
// the safe direction: a stray extra key can only make this check more lenient
// about a page that IS declared, never hide one that is not.
function manifestKeys() {
  const out = new Set()
  walk(read('src/config/nav.tsx'), (n) => {
    if (!ts.isPropertyAssignment(n)) return
    if (n.name.getText() !== 'key') return
    const v = str(n.initializer)
    if (v && v.startsWith('/')) out.add(v)
  })
  // The two `.map()` groups: /records/<entity> and /master/<entity>.
  walk(read('src/config/entities.ts'), (n) => {
    if (!ts.isPropertyAssignment(n)) return
    if (n.name.getText() !== 'key') return
    const v = str(n.initializer)
    if (v && !v.startsWith('/')) { out.add(`/records/${v}`); out.add(`/master/${v}`) }
  })
  return out
}

// ── 3. the exemptions, read from nav.tsx rather than restated ───────────────
// PUBLIC_PATH_PREFIXES is the guard's own allowlist. Reading it here means the
// two can never disagree: adding a prefix there is the single edit that makes
// a path exempt, and forgetting to update this file is not possible.
function publicPrefixes() {
  const out = []
  walk(read('src/config/nav.tsx'), (n) => {
    if (!ts.isVariableDeclaration(n)) return
    if (n.name.getText() !== 'PUBLIC_PATH_PREFIXES') return
    walk(n, (c) => { const v = str(c); if (v) out.push(v) })
  })
  return out
}

const routes = routePaths()
const keys = manifestKeys()
const publics = publicPrefixes()

// A `:param` route matches the manifest's concrete keys by prefix.
const covered = (r) => {
  if (keys.has(r)) return true
  if (publics.some((p) => r.startsWith(p) || p.startsWith(r.split(':')[0]))) return true
  if (r.includes(':')) {
    const prefix = r.slice(0, r.indexOf(':'))
    return [...keys].some((k) => k.startsWith(prefix))
  }
  return false
}

const orphans = [...routes].filter((r) => !covered(r)).sort()

// Sanity: if the parser silently returned nothing, every check above passes
// for the wrong reason. This is the check that catches a refactor that moves
// the route table or the manifest somewhere else.
const sane = routes.size >= 40 && keys.size >= 40 && publics.length >= 1

if (!sane) {
  console.error(`\n== NAV ROUTE COVERAGE: ❌ FAIL — the parser found almost nothing `
    + `(${routes.size} routes, ${keys.size} manifest keys, ${publics.length} public `
    + `prefixes). The route table or the manifest has moved; this check was `
    + `passing for the wrong reason.\n`)
  process.exit(1)
}

if (orphans.length) {
  console.error('\n== NAV ROUTE COVERAGE: ❌ FAIL ==\n')
  console.error(`${orphans.length} route(s) in src/App.tsx have no access rule:\n`)
  for (const o of orphans) console.error(`  ${o}`)
  console.error(`\nEvery routable page must say who may open it. Add a node to NAV in`
    + `\nsrc/config/nav.tsx, or — if the page is genuinely open to every signed-in`
    + `\nuser — add its prefix to PUBLIC_PATH_PREFIXES in the same file.`
    + `\n\nUntil you do, canAccessPath() refuses it and the page is unreachable.\n`)
  process.exit(1)
}

// ── Phase 14d: the announcement audience snapshot must match the manifest ──
// Announcements are addressed by ROUTE and their audience is derived from
// this manifest (rule 14). The API image has no Node, so the matrix travels as
// `backend/api/data/nav_access.json`; a manifest edit without a refresh would
// announce a page to people who can no longer open it. Role levels come from
// `auth.ROLE_META`, the one place they are defined — read, never restated.
{
  const { execFileSync } = await import('node:child_process')
  const repo = join(ROOT, '..')
  const auth = readFileSync(join(repo, 'backend', 'api', 'auth.py'), 'utf8')
  const block = auth.split('ROLE_META = {')[1]?.split('}\n')[0] ?? ''
  const levels = Object.fromEntries([...block.matchAll(
    /"(\w+)":\s*\{"label":[^,]+,\s*"level":\s*(\d+)\}/g)].map((m) => [m[1], Number(m[2])]))
  const fresh = JSON.parse(execFileSync('node',
    [join(ROOT, 'scripts', 'nav_access_dump.mjs'), JSON.stringify(levels)], { encoding: 'utf8' }))
  let snap = null
  try {
    snap = JSON.parse(readFileSync(join(repo, 'backend', 'api', 'data', 'nav_access.json'), 'utf8'))
  } catch { /* reported below */ }
  const norm = (d) => JSON.stringify({ sane: d.sane, roleLevels: d.roleLevels, publics: d.publics,
    routes: Object.fromEntries(Object.keys(d.routes).sort().map((k) => [k, d.routes[k]])) })
  if (!snap || norm(snap) !== norm(fresh) || Object.keys(levels).length < 5) {
    console.error('\n== NAV ROUTE COVERAGE: ❌ FAIL — backend/api/data/nav_access.json is STALE ==\n'
      + 'The navigation matrix changed; the announcement audiences are derived from it.\n'
      + 'Run `.venv/bin/python tools/announcements.py nav` and commit the result.\n')
    process.exit(1)
  }
}

console.log(`== NAV ROUTE COVERAGE: ✅ PASS (${routes.size} routes, all claimed; `
  + `${keys.size} manifest keys, ${publics.length} public prefix; announcement `
  + `audience snapshot current) ==`)
