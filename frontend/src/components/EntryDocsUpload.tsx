import { useState } from 'react'
import { App, Button, Space, Tag, Typography, Upload } from 'antd'
import { CameraOutlined, PaperClipOutlined, UploadOutlined } from '@ant-design/icons'
import { api } from '../api/client'

/**
 * Parity A1 (+C2) — supporting-document upload for the entry batch forms.
 * Files go straight to POST /entry/attachments; the parent form keeps the
 * returned ids and sends them as `attachment_ids` on submit — the backend
 * refuses the batch without one while `require_entry_documents` is on.
 * The 📷 button uses `capture="environment"` so the PWA opens the phone
 * camera directly (photograph the hand-written note on the warehouse floor).
 */
export interface EntryDoc { id: number; file_name: string }

export default function EntryDocsUpload({
  docType, siteId, docNumber, value, onChange, required = true,
}: {
  docType: 'consumption' | 'receipt' | 'return'
  siteId?: string
  docNumber?: string
  value: EntryDoc[]
  onChange: (docs: EntryDoc[]) => void
  required?: boolean
}) {
  const { message } = App.useApp()
  const [busy, setBusy] = useState(false)

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

  return (
    <div style={{ marginBottom: 12 }}>
      <Typography.Text strong>
        <PaperClipOutlined /> Supporting documents
        {required ? ' (required — hand-written note / delivery note)' : ' (optional)'}
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
