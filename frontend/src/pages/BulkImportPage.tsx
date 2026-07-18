/**
 * Bulk Excel Import — the operator's "update from Excel" surface.
 *
 * Flow per kind: pick workbook → DRY-RUN (server parses + plans, nothing
 * written) → review counts/warnings/rejects → COMMIT the same plan.
 * SME kinds are {hod, admin}; the inventory master and ledger backfill are
 * admin-only (cards hidden here, enforced server-side too).
 */
import { useState } from 'react'
import {
  Alert, App, Button, Card, Descriptions, Popconfirm, Select,
  Space, Table, Typography, Upload,
} from 'antd'
import { FileExcelOutlined, InboxOutlined } from '@ant-design/icons'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useSites } from '../api/hooks'

interface ImportResult {
  kind: string
  committed: boolean
  summary: Record<string, unknown>
  warnings: string[]
  rejects: { row?: number; sheet?: string; sap?: string; reason: string }[]
  preview?: Record<string, unknown>
}

const KINDS = [
  { value: 'sme-equipment', label: 'SME Equipment (Equipment.xlsx)', admin: false, scoped: true },
  { value: 'sme-recipes', label: 'SME Recipes / BOM (For_1_SQM.xlsx)', admin: false, scoped: false },
  { value: 'sme-materials', label: 'SME Materials seed (Materials_DetailsAvailable_Qty.xlsx)', admin: false, scoped: false },
  { value: 'inventory', label: 'Inventory master (CNCEC_Inventory.xlsx — Inventory sheet)', admin: true, scoped: true },
  { value: 'ledger', label: 'Ledger backfill (Receipt/Consumption/Return Log sheets)', admin: true, scoped: true },
]

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Request failed'
}

export default function BulkImportPage() {
  const { message } = App.useApp()
  const { user } = useAuth()
  const { data: sites } = useSites()
  const isAdmin = user?.role === 'admin'
  const kinds = KINDS.filter((k) => isAdmin || !k.admin)

  const [kind, setKind] = useState('sme-equipment')
  const [siteId, setSiteId] = useState<string | undefined>(undefined)
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState<'dry' | 'commit' | null>(null)
  const [result, setResult] = useState<ImportResult | null>(null)

  const spec = KINDS.find((k) => k.value === kind)
  const needsSite = !!spec?.scoped && user?.role !== 'hod'

  const run = async (commit: boolean) => {
    if (!file) {
      message.warning('Pick a .xlsx workbook first')
      return
    }
    if (needsSite && !siteId) {
      message.warning('Pick the target site first')
      return
    }
    setBusy(commit ? 'commit' : 'dry')
    try {
      const fd = new FormData()
      fd.append('file', file)
      const params: Record<string, string | boolean> = { commit }
      if (siteId) params.site_id = siteId
      const { data } = await api.post<ImportResult>(`/import/${kind}`, fd, { params })
      setResult(data)
      if (commit) message.success('Import committed')
      else message.info('Dry-run complete — nothing was written')
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setBusy(null)
    }
  }

  const summaryItems = result
    ? Object.entries(result.summary).map(([k, v]) => ({
        key: k,
        label: k,
        children: typeof v === 'object' ? JSON.stringify(v) : String(v),
      }))
    : []

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        <FileExcelOutlined /> Bulk Excel Import
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        Upload a structured workbook, dry-run to preview the exact plan
        (inserts / updates / rejects), then commit. Imports only upsert —
        they never delete existing rows; ledger history gets an append-only
        reconcile with quantity corrections.
      </Typography.Paragraph>

      <Card size="small" style={{ maxWidth: 720 }}>
        <Space orientation="vertical" style={{ width: '100%' }} size="middle">
          <Select
            style={{ width: '100%' }}
            value={kind}
            onChange={(v) => { setKind(v); setResult(null) }}
            options={kinds.map((k) => ({ value: k.value, label: k.label }))}
          />
          {needsSite && (
            <Select
              style={{ width: 240 }}
              placeholder="Target site"
              value={siteId}
              onChange={setSiteId}
              options={(sites ?? []).map((s) => ({ value: s, label: s }))}
            />
          )}
          <Upload.Dragger
            accept=".xlsx"
            maxCount={1}
            beforeUpload={(f) => { setFile(f); setResult(null); return false }}
            onRemove={() => { setFile(null); setResult(null) }}
          >
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            <p className="ant-upload-text">
              {file ? file.name : 'Click or drag the .xlsx workbook here'}
            </p>
          </Upload.Dragger>
          <Space>
            <Button type="primary" loading={busy === 'dry'} disabled={!file}
              onClick={() => run(false)}>
              Dry-run (preview)
            </Button>
            <Popconfirm
              title="Apply this import to the live database?"
              description="The committed plan is exactly what the dry-run showed."
              onConfirm={() => run(true)}
              okText="Commit"
            >
              <Button danger loading={busy === 'commit'}
                disabled={!file || !result || result.committed}>
                Commit import
              </Button>
            </Popconfirm>
          </Space>
        </Space>
      </Card>

      {result && (
        <Card size="small" style={{ marginTop: 16 }}
          title={result.committed ? '✅ Committed' : '🔎 Dry-run preview'}>
          <Descriptions size="small" bordered column={1} items={summaryItems} />
          {result.warnings.length > 0 && (
            <Alert type="warning" showIcon style={{ marginTop: 12 }}
              title="Warnings"
              description={<ul style={{ margin: 0, paddingLeft: 18 }}>
                {result.warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>} />
          )}
          {result.rejects.length > 0 && (
            <>
              <Typography.Title level={5} style={{ marginTop: 16 }}>
                Rejected rows ({result.rejects.length})
              </Typography.Title>
              <Table sticky={{ offsetHeader: 64 }}
                size="small"
                rowKey={(_, i) => String(i)}
                dataSource={result.rejects}
                pagination={{ pageSize: 10 }}
                columns={[
                  { title: 'Sheet', dataIndex: 'sheet', width: 140 },
                  { title: 'Row', dataIndex: 'row', width: 70 },
                  { title: 'SAP', dataIndex: 'sap', width: 100 },
                  { title: 'Reason', dataIndex: 'reason' },
                ]}
              />
            </>
          )}
        </Card>
      )}
    </div>
  )
}
