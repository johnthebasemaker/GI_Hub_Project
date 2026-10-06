/**
 * `npm run test:design` (run by `npm run build`, so CI runs it) — Phase 21e,
 * the mechanical half of docs/DESIGN_SYSTEM.md (rulings Q21-21..24).
 *
 * HARD RULES (any occurrence fails):
 *   1. no `transition: all` anywhere in src — list the properties (Emil · Vercel)
 *   2. no `ease-in` (ease-in-out is fine) — UI motion never starts slow
 *   3. index.css keeps the GLOBAL prefers-reduced-motion switch-off
 *   4. index.css keeps tabular figures on every table and statistic
 *   5. index.css declares the motion tokens (--gi-ease-out, --gi-dur-*)
 *
 * RATCHET (may only go DOWN): raw hex colours in components (`*.tsx` outside
 * src/theme/). 195 existed when the contract was written; a new file must
 * have none and an existing one may not gain any — use the tokens. Fixing
 * some lowers the bar: re-record with `--update`, deliberately, and say so in
 * the commit (the same discipline as perf/critical-path.json).
 */
import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SRC = join(ROOT, 'src')
const BASELINE = join(ROOT, 'perf', 'design-baseline.json')
const update = process.argv.includes('--update')

function walk(d, out = []) {
  for (const f of readdirSync(d)) {
    const p = join(d, f)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.(tsx?|css)$/.test(f)) out.push(p)
  }
  return out
}
const files = walk(SRC)
const rel = (p) => relative(ROOT, p)
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')

const problems = []
const TRANSITION_ALL = /transition(?:Property)?\s*:\s*['"`]?\s*all\b/
const EASE_IN = /\bease-in(?!-out)\b/
for (const p of files) {
  const s = strip(readFileSync(p, 'utf8'))
  s.split('\n').forEach((line, i) => {
    if (TRANSITION_ALL.test(line)) problems.push(`${rel(p)}:${i + 1} transition: all — list the properties`)
    if (EASE_IN.test(line)) problems.push(`${rel(p)}:${i + 1} ease-in — use var(--gi-ease-out) (never start slow)`)
  })
}

const css = readFileSync(join(SRC, 'index.css'), 'utf8')
if (!/@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{\s*\*\s*,/.test(css)) {
  problems.push('src/index.css: the GLOBAL prefers-reduced-motion block (`* { animation-duration … }`) is missing')
}
if (!/\.ant-table[^{]*\{[^}]*font-variant-numeric:\s*tabular-nums/.test(css)) {
  problems.push('src/index.css: tables must set font-variant-numeric: tabular-nums')
}
for (const t of ['--gi-ease-out', '--gi-ease-in-out', '--gi-dur-press', '--gi-dur-fast', '--gi-dur-mid', '--gi-dur-slow']) {
  if (!css.includes(`${t}:`)) problems.push(`src/index.css: motion token ${t} is not declared`)
}

// ── the ratchet ──────────────────────────────────────────────────────────────
const HEX = /#[0-9a-fA-F]{3,8}\b/g
const counts = {}
for (const p of files) {
  if (!p.endsWith('.tsx') || p.startsWith(join(SRC, 'theme'))) continue
  const n = (strip(readFileSync(p, 'utf8')).match(HEX) || []).length
  if (n) counts[rel(p)] = n
}
const total = Object.values(counts).reduce((a, b) => a + b, 0)
if (update) {
  writeFileSync(BASELINE, JSON.stringify({
    _note: 'Raw hex colours per component (docs/DESIGN_SYSTEM.md §2). A RATCHET: may only go down. Re-record only with `node scripts/design_check.mjs --update`, deliberately.',
    total, files: counts }, null, 1) + '\n')
  console.log(`recorded design baseline: ${total} raw hex colour(s) in ${Object.keys(counts).length} file(s)`)
  process.exit(0)
}
const base = JSON.parse(readFileSync(BASELINE, 'utf8'))
for (const [f, n] of Object.entries(counts)) {
  const was = base.files[f] ?? 0
  if (n > was) problems.push(`${f}: ${n} raw hex colour(s), was ${was} — use the tokens (src/theme/tokens.ts, --gi-* in index.css)`)
}

if (problems.length) {
  console.error(`== DESIGN CONTRACT: ❌ FAIL (${problems.length}) — docs/DESIGN_SYSTEM.md ==`)
  for (const p of problems) console.error('   · ' + p)
  process.exit(1)
}
console.log(`== DESIGN CONTRACT: ✅ PASS — no transition: all, no ease-in, reduced motion global, tabular figures, motion tokens · raw hex ${total} (baseline ${base.total}, may only fall) ==`)
