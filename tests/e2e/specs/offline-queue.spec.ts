/**
 * Phase B — offline mutation queue: a transaction POSTed while the browser is
 * offline is saved to IndexedDB (header badge appears), then auto-synced when
 * the network returns, landing in the HOD pending queue like a normal submit.
 * Drives the SAME postWithOfflineFallback the entry-form hooks use (exposed
 * as window.__giOffline by initOfflineQueue).
 */
import { test, expect } from '@playwright/test'
import { apiAs } from '../harness/api'
import { storageStatePath } from '../harness/env'

test.use({ storageState: storageStatePath('sk') })

test('offline entry queues, badges, and syncs on reconnect', async ({ page, context }) => {
  const supplier = `OFFL${Date.now()}`
  await page.goto('/entry/receive')
  await page.waitForFunction(() => '__giOffline' in window)

  await context.setOffline(true)
  const queued = await page.evaluate(
    ([supp]) =>
      (window as unknown as {
        __giOffline: { post: (p: string, b: unknown, h: Record<string, string>) => Promise<unknown> }
      }).__giOffline.post(
        '/entry/receipts',
        { Date: '2026-07-13', SAP_Code: '1001', Quantity: 2, Site_ID: 'CNCEC', Supplier: supp },
        {},
      ),
    [supplier],
  )
  expect(queued).toEqual({ queued: true })

  // header badge shows 1 queued entry
  await expect(page.getByRole('button', { name: /sync offline queue/i })).toBeVisible()

  await context.setOffline(false)
  await page.evaluate(() =>
    (window as unknown as { __giOffline: { flush: () => Promise<unknown> } }).__giOffline.flush(),
  )
  await expect(page.getByRole('button', { name: /sync offline queue/i })).toHaveCount(0)

  // the replayed POST really landed: HOD sees the staged receipt
  const hod = await apiAs('hod', '95')
  const pend = (await (await hod.get('/hod/pending/receipts')).json()) as
    | { items?: Record<string, unknown>[] }
    | Record<string, unknown>[]
  const rows = Array.isArray(pend) ? pend : pend.items ?? []
  expect(rows.some((x) => x.Supplier === supplier)).toBe(true)
  await hod.dispose()
})
