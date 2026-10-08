import { useEffect, useState } from 'react'
import { Button, Modal, Space, Tag, Tooltip, Typography } from 'antd'
import { PaperClipOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

/**
 * Phase 22b — the DN copies from Google Drive, opened from a receipt or a
 * return (rulings Q22-6..9).
 *
 * The files are read-only copies GI Hub keeps of the Drive folder *DN for
 * CNCEC* (staff have no Drive access). A receipt with no delivery note shows
 * its automatic WD number instead (Q22-8: one per delivery, never renumbered).
 */
export interface DriveDoc { id: number; name: string; mime: string | null }
export interface LedgerDoc { files: DriveDoc[]; wd: string | null }

export function useLedgerDocs(kind: 'receipts' | 'returns', enabled = true) {
  return useQuery<{ rows: Record<string, LedgerDoc> }>({
    queryKey: ['/drive/ledger-docs', kind],
    enabled,
    staleTime: 60_000,
    queryFn: async () => (await api.get('/drive/ledger-docs', { params: { kind } })).data,
  })
}

// the downloaded file's own type first — a certificate can be a .jpeg
function isImage(d: DriveDoc, type: string) {
  return type.startsWith('image/') || (!type && /\.(jpe?g|png|gif|webp)$/i.test(d.name))
    || (d.mime ?? '').startsWith('image/')
}
function isPdf(d: DriveDoc, type: string) {
  return type === 'application/pdf' || (!type && /\.pdf$/i.test(d.name))
}

/** One file, fetched with the session (an <img src> cannot carry it). */
function Preview({ doc }: { doc: DriveDoc }) {
  const [url, setUrl] = useState<string | null>(null)
  const [type, setType] = useState('')
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    let made: string | null = null
    api.get(`/drive/files/${doc.id}`, { responseType: 'blob' })
      .then((r) => {
        const blob = r.data as Blob
        made = URL.createObjectURL(blob)
        if (alive) { setType(blob.type || ''); setUrl(made) }
      })
      .catch(() => { if (alive) setErr('This copy is not on the server yet — the next pull fetches it.') })
    return () => { alive = false; if (made) URL.revokeObjectURL(made) }
  }, [doc.id])
  if (err) return <Typography.Text type="secondary">{err}</Typography.Text>
  if (!url) return <Typography.Text type="secondary">Loading…</Typography.Text>
  if (isImage(doc, type)) {
    return <img src={url} alt={doc.name} data-testid="drive-doc-image"
      style={{ maxWidth: '100%', maxHeight: '70vh', display: 'block', margin: '0 auto' }} />
  }
  if (isPdf(doc, type)) {
    return <iframe src={url} title={doc.name} data-testid="drive-doc-pdf"
      style={{ width: '100%', height: '70vh', border: 0 }} />
  }
  return <a href={url} download={doc.name} data-testid="drive-doc-download">Download {doc.name}</a>
}

export function DriveDocsModal({ title, docs, onClose }:
  { title: string; docs: DriveDoc[]; onClose: () => void }) {
  const [i, setI] = useState(0)
  const doc = docs[Math.min(i, docs.length - 1)]
  return (
    <Modal open title={title} onCancel={onClose} footer={null} width={900} destroyOnHidden
      data-testid="drive-docs-modal">
      {docs.length > 1 && (
        <Space wrap style={{ marginBottom: 8 }}>
          {docs.map((d, k) => (
            <Button key={d.id} size="small" type={k === i ? 'primary' : 'default'}
              onClick={() => setI(k)}>{d.name}</Button>
          ))}
        </Space>
      )}
      {doc && <Preview key={doc.id} doc={doc} />}
      {doc && <Typography.Paragraph type="secondary" style={{ marginTop: 8, fontSize: 12 }}>
        {doc.name} — a read-only copy of the file in Google Drive.</Typography.Paragraph>}
    </Modal>
  )
}

/** The DN column of Records → Receipts / Returns. */
export function DnCell({ doc, dn }: { doc?: LedgerDoc; dn: unknown }) {
  const [open, setOpen] = useState(false)
  const label = dn != null && String(dn).trim() ? String(dn) : null
  return (
    <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
      {doc?.wd
        ? <Tooltip title={`No delivery note${label ? ` (written “${label}”)` : ''} — GI Hub numbered this delivery`}>
            <Tag data-testid="wd-no" style={{ marginInlineEnd: 0 }}>{doc.wd}</Tag></Tooltip>
        : label}
      {!!doc?.files?.length && (
        <>
          <Button size="small" type="link" icon={<PaperClipOutlined />} data-testid="dn-open"
            aria-label={`Open the DN copy${doc.files.length > 1 ? 'ies' : ''}`}
            onClick={() => setOpen(true)}>{doc.files.length > 1 ? doc.files.length : ''}</Button>
          {open && <DriveDocsModal title={`DN ${label ?? ''}`.trim()} docs={doc.files}
            onClose={() => setOpen(false)} />}
        </>
      )}
    </span>
  )
}
