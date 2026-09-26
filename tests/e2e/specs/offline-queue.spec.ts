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
  // the badge lives in the app header — make sure the layout is mounted (and
  // its queue listeners attached) before we cut the network
  await expect(page.getByRole('button', { name: /sign out/i })).toBeVisible()

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

// 2026-09-26 — a replay is not a second entry. The queue removes an entry only
// when the server's ANSWER arrives, so a page that reloaded mid-replay used to
// commit the row, lose the answer, and send it again on the next load. This
// reproduces that deterministically: the queued request is delivered ONCE
// behind the queue's back (the answer that was "lost"), then the queue flushes.
test('a replay whose first answer was lost stages ONE row, not two', async ({ page, context }) => {
  const supplier = `REPLAY${Date.now()}`
  await page.goto('/entry/receive')
  await page.waitForFunction(() => '__giOffline' in window)
  await expect(page.getByRole('button', { name: /sign out/i })).toBeVisible()

  type Off = { __giOffline: {
    post: (p: string, b: unknown, h: Record<string, string>) => Promise<unknown>
    flush: () => Promise<{ sent: number; failed: string[] }>
    list: () => Promise<{ path: string; body: unknown; headers: Record<string, string> }[]>
  } }
  const body = { Date: '2026-07-13', SAP_Code: '1001', Quantity: 2, Site_ID: 'CNCEC', Supplier: supplier }
  await context.setOffline(true)
  await page.evaluate((b) => (window as unknown as Off).__giOffline.post('/entry/receipts', b, {}), body)
  const [entry] = await page.evaluate(() => (window as unknown as Off).__giOffline.list())
  expect(entry.headers['Idempotency-Key'], 'the key is minted BEFORE the first attempt and stored with the entry')
    .toBeTruthy()
  await context.setOffline(false)

  // The "lost answer": the same request reaches the server once, outside the
  // queue. ⚠️ Coming back online ALSO fires the app's own flush, so the two
  // deliveries of this key race — which is the point: whichever lands second
  // must be answered (201 replayed, or 409 while the first is still in
  // flight), never staged. So the assertions are about the OUTCOME.
  const first = await page.evaluate(async (e) => {
    const r = await fetch('/api/entry/receipts', {
      method: 'POST',
      headers: { ...e.headers, 'Content-Type': 'application/json',
        Authorization: `Bearer ${localStorage.getItem('gi_token')}`, 'X-GI-Instance': 'production' },
      body: JSON.stringify(e.body),
    })
    return r.status
  }, entry)
  expect([201, 409]).toContain(first)

  // Drain the queue: every remaining delivery of the key is answered, not staged.
  await expect.poll(async () => {
    await page.evaluate(() => (window as unknown as Off).__giOffline.flush())
    return (await page.evaluate(() => (window as unknown as Off).__giOffline.list())).length
  }, { timeout: 15_000 }).toBe(0)

  const hod = await apiAs('hod', '96')
  const pend = (await (await hod.get('/hod/pending/receipts')).json()) as
    | { items?: Record<string, unknown>[] } | Record<string, unknown>[]
  const rows = (Array.isArray(pend) ? pend : pend.items ?? []).filter((x) => x.Supplier === supplier)
  expect(rows).toHaveLength(1)   // …and stages nothing
  await hod.dispose()
})
