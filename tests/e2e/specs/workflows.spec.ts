/**
 * Multi-role workflow state machines (automatic_test.md §11, W1–W3) — the
 * direct port of the QA-night-shift harness that ran 21/21. Runs against the
 * hermetic backend as each role in turn; WhatsApp/SMTP are disabled in this
 * env so notification assertions are the in-app bell rows.
 */
import { test, expect, APIRequestContext } from '@playwright/test'
import { apiAs } from '../harness/api'

const STAMP = `E2E${Date.now()}`

// some list endpoints return bare arrays, others {items:[...]} — accept both
const items = (x: unknown): Record<string, unknown>[] =>
  Array.isArray(x) ? x : ((x as { items?: Record<string, unknown>[] })?.items ?? [])

test.describe.configure({ mode: 'serial' })

let admin: APIRequestContext
let hod: APIRequestContext
let sk: APIRequestContext
let sup: APIRequestContext
let site: string
let sap: string

test.beforeAll(async () => {
  ;[admin, hod, sk, sup] = await Promise.all([
    apiAs('admin', '81'), apiAs('hod', '82'), apiAs('sk', '83'), apiAs('supervisor', '84'),
  ])
  site = ((await (await hod.get('/auth/me')).json()) as { site_id: string }).site_id
  const inv = (await (await admin.get('/inventory?limit=5')).json()) as { items: { SAP_Code: string }[] }
  sap = String(inv.items[0].SAP_Code)
})

test.afterAll(async () => {
  await Promise.all([admin, hod, sk, sup].filter(Boolean).map((c) => c.dispose()))
})

test('W1: SK stages receipt → HOD edits qty → approves → ledger + SK bell', async () => {
  const supplier = `${STAMP}-VENDOR`
  const staged = await sk.post('/entry/receipts', {
    data: {
      Date: '2026-07-13', SAP_Code: sap, Quantity: 7, Site_ID: site,
      Supplier: supplier, Lot_Number: `${STAMP}-LOT1`,
    },
  })
  expect(staged.status(), await staged.text()).toBe(201)

  const pend = items(await (await hod.get('/hod/pending/receipts')).json())
  const mine = pend.filter((x) => x.Supplier === supplier)
  expect(mine.length).toBeGreaterThanOrEqual(1)
  const pid = mine[0].id as number

  const edit = await hod.patch(`/hod/pending/receipts/${pid}`, { data: { fields: { Quantity: 9 } } })
  expect(edit.status(), await edit.text()).toBe(200)
  const appr = await hod.post(`/hod/pending/receipts/${pid}/approve`)
  expect(appr.status(), await appr.text()).toBe(200)

  // committed at the EDITED quantity
  const rec = (await (await admin.get(`/receipts?q=${supplier}&limit=10`)).json()) as { items: { Quantity: number }[] }
  expect(rec.items.some((x) => Math.abs(Number(x.Quantity) - 9) < 1e-6)).toBe(true)

  // NOTE: receipt approvals deliberately do NOT notify the submitter —
  // pending_receipts has no submitter column (_SUBMITTER_COL maps receipts →
  // None in backend/api/hod.py). The submitter-bell contract is covered by
  // the returns flow in W1c below.
})

test('W1c: SK stages return → HOD approves → ledger row + SK "approved" bell', async () => {
  const reason = `${STAMP}-APPR`
  const staged = await sk.post('/entry/returns', {
    data: { Date: '2026-07-13', SAP_Code: sap, Quantity: 1, Site_ID: site, Reason: reason },
  })
  expect(staged.status(), await staged.text()).toBe(201)

  const pend = items(await (await hod.get('/hod/pending/returns')).json())
  const mine = pend.filter((x) => x.Return_Reason === reason)
  expect(mine.length).toBe(1)
  expect(mine[0].submitted_by).toBe('worker')

  const appr = await hod.post(`/hod/pending/returns/${mine[0].id}/approve`)
  expect(appr.status(), await appr.text()).toBe(200)

  // the ledger table stores the pending row's Return_Reason as plain Reason
  const led = (await (await admin.get('/returns?limit=100')).json()) as { items: { Reason?: string }[] }
  expect(led.items.some((x) => x.Reason === reason)).toBe(true)

  await expect
    .poll(async () => {
      const notifs = items(await (await sk.get('/notifications?limit=50')).json())
      return notifs.some((n) => String(n.title ?? '').includes('return was approved'))
    }, { timeout: 10_000 })
    .toBe(true)
})

test('W1b: HOD rejects a staged return with a reason → not in ledger + SK "rejected" bell', async () => {
  const reason = `${STAMP}-REJ`
  const staged = await sk.post('/entry/returns', {
    data: { Date: '2026-07-13', SAP_Code: sap, Quantity: 1, Site_ID: site, Reason: reason },
  })
  expect(staged.status(), await staged.text()).toBe(201)

  const pend = items(await (await hod.get('/hod/pending/returns')).json())
  const mine = pend.filter((x) => x.Return_Reason === reason)
  expect(mine.length).toBe(1)

  const rej = await hod.post(`/hod/pending/returns/${mine[0].id}/reject`, {
    data: { reason: 'E2E rejection test' },
  })
  expect(rej.status(), await rej.text()).toBe(200)

  const led = (await (await admin.get('/returns?limit=100')).json()) as { items: { Reason?: string }[] }
  expect(led.items.some((x) => x.Reason === reason)).toBe(false)

  await expect
    .poll(async () => {
      const notifs = items(await (await sk.get('/notifications?limit=50')).json())
      return notifs.some((n) => String(n.title ?? '').includes('return was rejected'))
    }, { timeout: 10_000 })
    .toBe(true)
})

test('W2: supervisor SMR → SK sees + approves → mirrors into HOD issue queue', async () => {
  const created = await sup.post('/requests', {
    data: {
      site_id: site, worker_id: '30001', job_tank_place: `${STAMP}-JOB`,
      old_ppe_returned: true,
      items: [{ SAP_Code: sap, Requested_Qty: 2, Notes: 'e2e line' }],
    },
  })
  expect(created.status(), await created.text()).toBeLessThan(300)
  const body = (await created.json()) as { id?: number; request_id?: number }
  const smrId = body.id ?? body.request_id
  expect(smrId).toBeTruthy()

  const q = items(await (await sk.get('/requests?status=pending_sk')).json())
  expect(q.some((x) => String(x.id) === String(smrId))).toBe(true)

  const appr = await sk.post(`/requests/${smrId}/approve`, { data: { lines: [] } })
  expect(appr.status(), await appr.text()).toBe(200)

  const iss = items(await (await hod.get('/hod/pending/issues')).json())
  expect(iss.some((x) => String(x.SAP_Code) === sap)).toBe(true)
})

test('W3: HOD creates a PR and submits it to logistics', async () => {
  const created = await hod.post('/hod/prs', {
    data: {
      site_id: site, supplier: `${STAMP}-SUPPLIER`, notes: 'E2E PR',
      lines: [{ SAP_Code: sap, Requested_Qty: 5, Est_Cost_SAR: 100, Notes: 'e2e' }],
    },
  })
  expect(created.status(), await created.text()).toBeLessThan(300)
  const body = (await created.json()) as { PR_Number?: string; pr_number?: string }
  const prNumber = body.PR_Number ?? body.pr_number
  expect(prNumber).toBeTruthy()

  const sub = await hod.post(`/hod/prs/${prNumber}/submit?site_id=${site}`)
  expect(sub.status(), await sub.text()).toBe(200)
})
