import { useState } from 'react'
import { Button, Card, DatePicker, Drawer, Input, Space, Table, Tabs, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { DownloadOutlined, EyeOutlined, FolderOpenOutlined } from '@ant-design/icons'
import type { Dayjs } from 'dayjs'
import { api } from '../api/client'
import { useEntryDocs, useSites } from '../api/hooks'
import type { EntryDocRow } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'
import { Select } from 'antd'

/**
 * Parity A1 — the legacy HOD "DOC" tab (Document Library): every supporting
 * document Store Keepers attached to consumption / receipt / return batches,
 * filterable by type, date range and doc number, downloadable, with inline
 * preview for images and PDFs (C1). Includes the 35 documents migrated from
 * the legacy SQLite.
 */
const TYPE_COLORS: Record<string, string> = { consumption: 'blue', receipt: 'green', return: 'volcano' }

export function docPreviewUrl(id: number) {
  return `/api/entry/attachments/${id}/download?inline=1`
}

export function DocPreviewDrawer({ doc, onClose }: { doc: EntryDocRow | null; onClose: () => void }) {
  const isImage = doc?.mime_type?.startsWith('image/')
  const isPdf = doc?.mime_type === 'application/pdf'
  return (
    <Drawer open={!!doc} onClose={onClose} width={720}
      title={doc ? `${doc.file_name} · ${doc.doc_type} · ${doc.doc_number}` : ''}>
      {doc && (
        <>
          {isImage && <img src={docPreviewUrl(doc.id)} alt={doc.file_name} style={{ maxWidth: '100%' }} />}
          {isPdf && <iframe src={docPreviewUrl(doc.id)} title={doc.file_name}
            style={{ width: '100%', height: '75vh', border: 'none' }} />}
          {!isImage && !isPdf && (
            <Typography.Paragraph>No inline preview for this file type.</Typography.Paragraph>
          )}
          <Button icon={<DownloadOutlined />} style={{ marginTop: 12 }}
            onClick={() => void download(doc)}>
            Download
          </Button>
        </>
      )}
    </Drawer>
  )
}

async function download(doc: EntryDocRow) {
  const r = await api.get(`/entry/attachments/${doc.id}/download`, { responseType: 'blob' })
  const url = URL.createObjectURL(r.data as Blob)
  const a = document.createElement('a')
  a.href = url
  a.download = doc.file_name
  a.click()
  URL.revokeObjectURL(url)
}

export default function DocumentLibraryPage() {
  const { user } = useAuth()
  const unscoped = (user?.level ?? 0) >= 3
  const { data: sites } = useSites()
  const [docType, setDocType] = useState('receipt')
  const [site, setSite] = useState<string | undefined>(undefined)
  const [docNo, setDocNo] = useState('')
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null)
  const [preview, setPreview] = useState<EntryDocRow | null>(null)

  const { data: items, isFetching } = useEntryDocs({
    doc_type: docType,
    site_id: site,
    doc_number: docNo || undefined,
    date_from: range?.[0] ? range[0].format('YYYY-MM-DD') : undefined,
    date_to: range?.[1] ? range[1].format('YYYY-MM-DD') : undefined,
  })

  const columns: ColumnsType<EntryDocRow> = [
    { title: 'Doc No.', dataIndex: 'doc_number', width: 110 },
    { title: 'File', dataIndex: 'file_name', ellipsis: true },
    {
      title: 'Type', dataIndex: 'doc_type', width: 120,
      render: (v: string) => <Tag color={TYPE_COLORS[v]}>{v}</Tag>,
    },
    { title: 'Site', dataIndex: 'Site_ID', width: 90 },
    { title: 'Entry date', dataIndex: 'entry_date', width: 110, render: (v) => v ?? '—' },
    {
      title: 'Linked', dataIndex: 'entry_table', width: 110,
      render: (v) => (v ? <Tag color="green">submitted</Tag> : <Tag>unlinked</Tag>),
    },
    { title: 'Uploaded by', dataIndex: 'uploaded_by', width: 120 },
    { title: 'At', dataIndex: 'uploaded_at', width: 160, render: (v) => String(v).slice(0, 16) },
    {
      title: '', key: 'act', width: 100, align: 'right',
      render: (_: unknown, r: EntryDocRow) => (
        <Space>
          <Button size="small" icon={<EyeOutlined />} onClick={() => setPreview(r)} />
          <Button size="small" icon={<DownloadOutlined />} onClick={() => void download(r)} />
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        <FolderOpenOutlined /> Document Library
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        Supporting documents Store Keepers attached to entry batches — hand-written notes,
        delivery notes, photos. Filter by type, date and doc number.
      </Typography.Paragraph>

      <Card size="small">
        <Space wrap style={{ marginBottom: 12 }}>
          {unscoped && (
            <Select allowClear placeholder="All sites" style={{ width: 140 }}
              value={site} onChange={setSite}
              options={(sites ?? []).map((s) => ({ value: s, label: s }))} />
          )}
          <Input.Search allowClear placeholder="Doc / DN number" style={{ width: 200 }}
            value={docNo} onChange={(e) => setDocNo(e.target.value)} />
          <DatePicker.RangePicker value={range} onChange={(v) => setRange(v)} />
        </Space>
        <Tabs activeKey={docType} onChange={setDocType} items={[
          { key: 'receipt', label: 'Receipts' },
          { key: 'consumption', label: 'Consumption / Issues' },
          { key: 'return', label: 'Returns' },
        ]} />
        <Table<EntryDocRow> sticky={{ offsetHeader: 64 }} size="small" rowKey="id" loading={isFetching}
          columns={columns} dataSource={items ?? []}
          pagination={{ pageSize: 20, showTotal: (t) => `${t} document(s)` }}
          scroll={{ x: 'max-content' }} />
      </Card>

      <DocPreviewDrawer doc={preview} onClose={() => setPreview(null)} />
    </div>
  )
}
