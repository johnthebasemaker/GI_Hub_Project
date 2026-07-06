#!/usr/bin/env node
/**
 * frontend/scripts/sme_parity.mjs — SME engine client/server parity gate
 * (Phase S1).
 *
 * Imports frontend/src/sme/engine.ts directly (Node ≥23 strips erasable TS
 * types natively; the engine is dependency-free by design), runs it over the
 * shared fixture and asserts the output equals
 * backend/api/sme_parity_golden.json — the file the PYTHON engine generated
 * and service_tests.py re-verifies. Golden equality on both sides proves
 * TS engine ≡ Python oracle.
 *
 * Run from the repo root or frontend/:  node frontend/scripts/sme_parity.mjs
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const frontendDir = path.resolve(here, '..')
const repoRoot = path.resolve(frontendDir, '..')

const engine = await import(
  pathToFileURL(path.join(frontendDir, 'src', 'sme', 'engine.ts')))

const fixture = JSON.parse(readFileSync(
  path.join(repoRoot, 'backend', 'api', 'sme_parity_fixture.json'), 'utf8'))
const golden = JSON.parse(readFileSync(
  path.join(repoRoot, 'backend', 'api', 'sme_parity_golden.json'), 'utf8'))

const TOL = 1e-9
const failures = []
let checks = 0

function compare(a, b, where) {
  checks += 1
  if (typeof a === 'number' && typeof b === 'number') {
    if (Math.abs(a - b) > TOL) failures.push(`${where}: ${a} != ${b}`)
    return
  }
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) {
      failures.push(`${where}: length ${a.length} != ${b.length}`)
      return
    }
    a.forEach((v, i) => compare(v, b[i], `${where}[${i}]`))
    return
  }
  if (a && b && typeof a === 'object' && typeof b === 'object') {
    const ka = Object.keys(a).sort()
    const kb = Object.keys(b).sort()
    if (ka.join(',') !== kb.join(',')) {
      failures.push(`${where}: keys [${ka}] != [${kb}]`)
      return
    }
    for (const k of ka) compare(a[k], b[k], `${where}.${k}`)
    return
  }
  if (a !== b) failures.push(`${where}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`)
}

const m = fixture.model
const model = engine.buildModel(m.equipment, m.recipes, m.materials, m.progress)
compare(model.defaultOrder, golden._default_order, 'default_order')

for (const { name, order } of fixture.cases) {
  const got = { ...engine.runPlan(model, order), ...engine.runSuggestionEngine(model, order) }
  compare(got, golden[name], name)
}

if (failures.length) {
  console.error(`== SME TS↔PY PARITY: ❌ FAIL (${failures.length} mismatches over ${checks} comparisons)`)
  for (const f of failures.slice(0, 25)) console.error('  ' + f)
  process.exit(1)
}
console.log(`== SME TS↔PY PARITY: ✅ PASS (${checks} comparisons, ${fixture.cases.length} cases + default order)`)
