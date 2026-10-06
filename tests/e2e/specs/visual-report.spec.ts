/**
 * Phase 21e — the SCREENSHOT REPORT (ruling Q21-24: a report, never a gate).
 *
 * Twelve key pages, dark and light, saved under
 * `.results/visual-report/<theme>/<page>.png` for a before/after comparison in
 * a UI pull request (docs/DESIGN_SYSTEM.md §7). It asserts only that each page
 * rendered — pixels change for good reasons, so nothing here compares them.
 *
 * Registered ONLY when GI_VISUAL_REPORT=1, so the normal E2E run neither runs
 * nor SKIPS it (rule 16: a skip is not a pass — there is simply no test):
 *
 *   cd tests/e2e && GI_VISUAL_REPORT=1 npx playwright test specs/visual-report.spec.ts
 */
import { test, expect } from '@playwright/test'
import { storageStatePath } from '../harness/env'

const PAGES: { name: string; path: string; role: 'hod' | 'sk' | 'admin'; ready: RegExp }[] = [
  { name: '01-dashboard', path: '/', role: 'hod', ready: /Dashboard|Top 5 expiring/ },
  { name: '02-stock', path: '/stock', role: 'hod', ready: /Stock/ },
  { name: '03-reorder', path: '/stock?tab=reorder', role: 'hod', ready: /Reorder signals/ },
  { name: '04-lots', path: '/lots', role: 'hod', ready: /Lots & Expiry/ },
  { name: '05-execution', path: '/execution', role: 'hod', ready: /Execution|Surface Shield/ },
  { name: '06-approvals', path: '/approvals', role: 'hod', ready: /Approvals/ },
  { name: '07-daily-log', path: '/surface-shield/log', role: 'hod', ready: /daily log|Surface Shield/i },
  { name: '08-issue', path: '/entry/issue', role: 'sk', ready: /Issue/ },
  { name: '09-receive', path: '/entry/receive', role: 'sk', ready: /Receive/ },
  { name: '10-ocr', path: '/entry/ocr', role: 'sk', ready: /OCR Import/ },
  { name: '11-admin-console', path: '/admin/console', role: 'admin', ready: /Admin Console/ },
  { name: '12-login', path: '/login', role: 'hod', ready: /GI Hub|Sign in/i },
]

if (process.env.GI_VISUAL_REPORT === '1') {
  for (const theme of ['dark', 'light'] as const) {
    test.describe(`visual report — ${theme}`, () => {
      for (const p of PAGES) {
        test(`${theme} · ${p.name}`, async ({ browser }) => {
          const ctx = await browser.newContext(p.name === '12-login' ? {} : { storageState: storageStatePath(p.role) })
          await ctx.addInitScript((t) => { try { localStorage.setItem('gi-hub-theme', t) } catch { /* private mode */ } }, theme)
          const page = await ctx.newPage()
          await page.goto(p.path)
          await expect(page.locator('body')).toContainText(p.ready, { timeout: 30_000 })
          await page.waitForLoadState('networkidle')
          // the sidebar can satisfy `ready` while the page body is still a skeleton
          await expect(page.locator('.ant-layout-content .ant-skeleton')).toHaveCount(0, { timeout: 30_000 })
          await page.evaluate(() => document.fonts.ready)
          await page.screenshot({ path: `.results/visual-report/${theme}/${p.name}.png` })
          await ctx.close()
        })
      }
    })
  }
}
