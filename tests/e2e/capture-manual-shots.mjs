// One-off: capture user-manual screenshots from the RUNNING dev stack
// (Vite :5173 → API :8000). Usage: node capture-manual-shots.mjs
// Output: ../../docs/screenshots/v2/*.png
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const BASE = 'http://localhost:5173'
const OUT = path.resolve(import.meta.dirname, '../../docs/screenshots/v2')
fs.mkdirSync(OUT, { recursive: true })

async function tokenFor(username, password) {
  const r = await fetch(`${BASE}/api/auth/login`, {
    method: 'POST', headers: { 'content-type': 'application/json', 'x-real-ip': '203.0.113.90' },
    body: JSON.stringify({ username, password }),
  })
  const j = await r.json()
  if (!j.access_token) throw new Error(`login failed for ${username}: ${JSON.stringify(j)}`)
  return j.access_token
}

const SHOTS = [
  { user: ['hod', 'hod2026'], path: '/', name: 'dashboard-hod' },
  { user: ['hod', 'hod2026'], path: '/hod/approvals', name: 'hod-approvals' },
  { user: ['hod', 'hod2026'], path: '/sme', name: 'sme-estimator' },
  { user: ['hod', 'hod2026'], path: '/bulk-import', name: 'bulk-import' },
  { user: ['hod', 'hod2026'], path: '/hod/lining-coverage', name: 'lining-coverage' },
  { user: ['worker', 'floor2026'], path: '/entry/receive', name: 'sk-receive' },
  { user: ['worker', 'floor2026'], path: '/entry/returnables', name: 'sk-returnables' },
  { user: ['admin', 'admin2026'], path: '/documents', name: 'documents-qr' },
]

const browser = await chromium.launch()
const tokens = {}
for (const s of SHOTS) {
  const key = s.user[0]
  tokens[key] ??= await tokenFor(...s.user)
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'dark' })
  await ctx.addInitScript((t) => localStorage.setItem('gi_token', t), tokens[key])
  const page = await ctx.newPage()
  await page.goto(BASE + s.path, { waitUntil: 'networkidle', timeout: 30000 }).catch(() => {})
  await page.waitForTimeout(1500)
  await page.screenshot({ path: path.join(OUT, `${s.name}.png`) })
  console.log('captured', s.name)
  await ctx.close()
}
await browser.close()
