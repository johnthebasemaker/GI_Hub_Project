import { useRef, useState } from 'react'
import { App, Button, Space, Tag, Typography, Upload } from 'antd'
import { CameraOutlined, PaperClipOutlined, ThunderboltOutlined, UploadOutlined } from '@ant-design/icons'
import { api } from '../api/client'

/**
 * Parity A1 (+C2) — supporting-document upload for the entry batch forms.
 * Files go straight to POST /entry/attachments; the parent form keeps the
 * returned ids and sends them as `attachment_ids` on submit — the backend
 * refuses the batch without one while `require_entry_documents` is on.
 * The 📷 button uses `capture="environment"` so the PWA opens the phone
 * camera directly (photograph the hand-written note on the warehouse floor).
 *
 * Parity C3 (doc assist): when `ocrKind` is set, every attached photo gets a
 * "Read (AI)" button — the SAME stored bytes go through the vision pipeline
 * (POST /ai/jobs/from-attachment → poll) and the parsed result is handed to
 * the parent so it can auto-fill form fields. Degrades gracefully: AI off /
 * Ollama down / non-photo attachments surface a friendly toast.
 */
export interface EntryDoc { id: number; file_name: string }

export interface OcrDocResult {
  header?: Record<string, string | null>
  rows?: Record<string, unknown>[]
  items?: Record<string, unknown>[]
}

export default function EntryDocsUpload({
  docType, siteId, docNumber, value, onChange, required = true,
  ocrKind, onOcrResult,
}: {
  // 'safety_approval' (QSEP slice 4) is the PPE distribution's mandatory
  // document. It reuses this component and the same /entry/attachments store
  // rather than getting its own: the BLOB handling, the size cap, the MIME
  // allowlist, the camera capture and the audit row are all already right here.
  // 'delivery_note' (2026-08-13) is the signed DN a warehouse must attach
  // before a shipment may leave, and it reuses this for the same reasons —
  // including the camera, which is the point at a loading bay.
  docType: 'consumption' | 'receipt' | 'return' | 'safety_approval' | 'delivery_note'
  siteId?: string
  docNumber?: string
  value: EntryDoc[]
  onChange: (docs: EntryDoc[]) => void
  required?: boolean
  ocrKind?: 'ocr_delivery_note' | 'ocr_consumption'
  onOcrResult?: (result: OcrDocResult) => void
}) {
  const { message } = App.useApp()
  const [busy, setBusy] = useState(false)
  const [readingId, setReadingId] = useState<number | null>(null)
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const readWithAi = async (doc: EntryDoc) => {
    setReadingId(doc.id)
    try {
      const { data } = await api.post<{ job_id: number }>('/ai/jobs/from-attachment',
        { attachment_id: doc.id, kind: ocrKind })
      const started = Date.now()
      const poll = async () => {
        try {
          const { data: j } = await api.get<{ status: string; error?: string; result?: OcrDocResult }>(
            `/ai/jobs/${data.job_id}`)
          if (j.status === 'done' && j.result) {
            setReadingId(null)
            onOcrResult?.(j.result)
            return
          }
          if (j.status === 'error') {
            setReadingId(null)
            message.warning(j.error ?? 'The AI could not read this document')
            return
          }
          if (Date.now() - started > 90_000) {
            setReadingId(null)
            message.warning('AI read timed out — fill the fields manually')
            return
          }
          pollTimer.current = setTimeout(poll, 2000)
        } catch {
          setReadingId(null)
          message.warning('AI read failed — fill the fields manually')
        }
      }
      pollTimer.current = setTimeout(poll, 1500)
    } catch (e) {
      setReadingId(null)
      const x = e as { response?: { data?: { detail?: string } } }
      message.warning(x?.response?.data?.detail ?? 'AI document reading is unavailable')
    }
  }

  const doUpload = async (file: File) => {
    if (!siteId) {
      message.warning('Pick the site first — documents are filed per site.')
      return
    }
    setBusy(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('doc_type', docType)
      fd.append('site_id', siteId)
      if (docNumber?.trim()) fd.append('doc_number', docNumber.trim())
      const r = await api.post<{ id: number; file_name: string }>('/entry/attachments', fd)
      onChange([...value, { id: r.data.id, file_name: r.data.file_name }])
      message.success(`Attached: ${r.data.file_name}`)
    } catch (e) {
      const x = e as { response?: { data?: { detail?: string } } }
      message.error(x?.response?.data?.detail ?? 'Upload failed')
    } finally {
      setBusy(false)
    }
  }

  const remove = async (doc: EntryDoc) => {
    try {
      await api.delete(`/entry/attachments/${doc.id}`)
    } catch { /* already linked or gone — still drop it locally */ }
    onChange(value.filter((d) => d.id !== doc.id))
  }

  const uploadProps = {
    multiple: false,
    showUploadList: false,
    accept: 'image/*,application/pdf,.xlsx',
    customRequest: ({ file, onSuccess }: { file: unknown; onSuccess?: (b: unknown) => void }) => {
      void doUpload(file as File).then(() => onSuccess?.({}))
    },
  }

  // The heading names the document the caller actually wants. It used to be
  // hardcoded to "hand-written note / delivery note", which is right for the
  // three entry types and wrong for a safety approval — the PPE panel showed
  // an SK the wrong instruction directly under a field labelled "Signed
  // Safety Approval".
  const heading = docType === 'safety_approval'
    ? { label: 'Signed safety approval', hint: 'the signed form authorising this issue' }
    : docType === 'delivery_note'
      ? { label: 'Signed delivery note', hint: 'a scan or photo of the document travelling with the goods' }
      : { label: 'Supporting documents', hint: 'hand-written note / delivery note' }

  return (
    <div style={{ marginBottom: 12 }} data-testid="entry-docs">
      <Typography.Text strong>
        <PaperClipOutlined /> {heading.label}
        {required ? ` (required — ${heading.hint})` : ' (optional)'}
      </Typography.Text>
      <div style={{ marginTop: 6 }}>
        <Space size={[6, 6]} wrap>
          <Upload {...uploadProps}>
            <Button size="small" icon={<UploadOutlined />} loading={busy}>Attach file</Button>
          </Upload>
          <Upload {...uploadProps} capture="environment">
            <Button size="small" icon={<CameraOutlined />} loading={busy}>Photograph note</Button>
          </Upload>
          {value.map((d) => (
            <Tag key={d.id} closable onClose={(e) => { e.preventDefault(); void remove(d) }}>
              {d.file_name}
              {ocrKind && (
                <Button
                  size="small" type="link" icon={<ThunderboltOutlined />}
                  loading={readingId === d.id}
                  style={{ padding: '0 2px', height: 18 }}
                  onClick={() => void readWithAi(d)}
                >
                  Read (AI)
                </Button>
              )}
            </Tag>
          ))}
          {required && !value.length && (
            <Typography.Text type="danger" style={{ fontSize: 12 }}>
              at least one document must be attached before submitting
            </Typography.Text>
          )}
        </Space>
      </div>
    </div>
  )
}
