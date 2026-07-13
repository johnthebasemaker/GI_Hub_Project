/**
 * Parity A1 — the mandatory supporting-document gate, end to end: with
 * require_entry_documents ON, a bulk submit without documents is refused;
 * after uploading a document the same batch stages, the attachment links to
 * it, and the HOD sees it in the Document Library. Serial — it flips a
 * global setting and restores it.
 */
import { test, expect } from '@playwright/test'
import { apiAs } from '../harness/api'

test.describe.configure({ mode: 'serial' })

test('document gate blocks, upload unblocks, HOD library lists it', async () => {
  const admin = await apiAs('admin', '96')
  const sk = await apiAs('sk', '97')
  const hod = await apiAs('hod', '98')
  const stamp = `EDOC${Date.now()}`

  await admin.put('/admin/settings', { data: { key: 'require_entry_documents', value: '1' } })
  try {
    // no document → the whole batch is refused
    const blocked = await sk.post('/entry/bulk', {
      data: {
        kind: 'receipt',
        rows: [{ Date: '2026-07-13', SAP_Code: '1001', Quantity: 1, Site_ID: 'CNCEC', Supplier: stamp }],
      },
    })
    expect(blocked.status(), await blocked.text()).toBe(422)
    expect(await blocked.text()).toContain('document')

    // upload a note, resubmit with it → staged + linked
    const up = await sk.post('/entry/attachments', {
      multipart: {
        file: { name: 'note.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 e2e note') },
        doc_type: 'receipt',
        site_id: 'CNCEC',
        doc_number: stamp,
      },
    })
    expect(up.status(), await up.text()).toBe(201)
    const aid = ((await up.json()) as { id: number }).id

    const staged = await sk.post('/entry/bulk', {
      data: {
        kind: 'receipt', attachment_ids: [aid],
        rows: [{ Date: '2026-07-13', SAP_Code: '1001', Quantity: 1, Site_ID: 'CNCEC', Supplier: stamp }],
      },
    })
    expect(staged.status(), await staged.text()).toBe(201)

    // the HOD Document Library lists it, linked to the batch
    const lib = await hod.get(`/entry/attachments?doc_type=receipt&doc_number=${stamp}`)
    const items = ((await lib.json()) as { items: { id: number; entry_table: string | null }[] }).items
    expect(items.some((d) => d.id === aid && d.entry_table === 'pending_receipts')).toBe(true)

    // and the bytes stream back
    const dl = await hod.get(`/entry/attachments/${aid}/download`)
    expect(dl.status()).toBe(200)
    expect((await dl.body()).toString()).toContain('%PDF-1.4 e2e note')
  } finally {
    await admin.put('/admin/settings', { data: { key: 'require_entry_documents', value: '0' } })
    await Promise.all([admin, sk, hod].map((c) => c.dispose()))
  }
})
