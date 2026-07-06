import { useMemo, useState } from 'react'
import {
  Alert, App, Button, Card, DatePicker, Descriptions, Input, InputNumber, Popconfirm,
  Radio, Select, Space, Spin, Table, Tag, Typography, Upload,
} from 'antd'
import { CameraOutlined, DeleteOutlined, InboxOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import dayjs, { Dayjs } from 'dayjs'
import { api } from '../api/client'
import type { Row as ApiRow } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useList, useSites } from '../api/hooks'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

type Kind = 'ocr_consumption' | 'ocr_delivery_note'

interface OcrRow extends ApiRow {
  material_text: string
  quantity: number
  uom: string
  issued_to?: string
  work_type?: string
  SAP_Code: string
  match_state: 'auto' | 'pick' | 'unknown'
  candidates: { SAP_Code: string; Equipment_Description: string; score: number }[]
}

interface DnHeader { DN_No: string; Date: string; Mob_From: string; Driver_Name: string; Vehicle_No: string; Prepared_by: string; Mob_To: string }

const MATCH_COLOR = { auto: 'green', pick: 'gold', unknown: 'red' } as const

// 📷 OCR Import — the new-stack port of the legacy Daily Issue Log OCR lanes.
// Photo lane: POST /ai/jobs → poll → review. Paste lane: instant + offline.
// Both lanes land in the SAME review grid; staging goes through the existing
// exact-locked /entry/consumption and /entry/receipts services.
export default function OcrImportPage() {
  const { message } = App.useApp()
  const { user } = useAuth()
  const { data: sites } = useSites()
  const inventory = useList('/inventory', { limit: 1000 })

  const [kind, setKind] = useState<Kind>('ocr_consumption')
  const [jobId, setJobId] = useState<number | null>(null)
  const [rows, setRows] = useState<OcrRow[]>([])
  const [header, setHeader] = useState<DnHeader | null>(null)
  const [pasteText, setPasteText] = useState('')
  const [date, setDate] = useState<Dayjs>(dayjs())
  const [site, setSite] = useState<string | undefined>(user?.site_id || undefined)
  const [staging, setStaging] = useState(false)

  const isAdmin = user?.role === 'admin'
  const isConsumption = kind === 'ocr_consumption'

  const { data: aiHealth } = useQuery({
    queryKey: ['/ai/health'],
    queryFn: async () => (await api.get('/ai/health')).data as { ok: boolean; message: string },
  })

  // Poll the job while queued/running; load the result rows once done.
  const job = useQuery({
    queryKey: ['/ai/jobs', jobId],
    enabled: jobId != null,
    refetchInterval: (q) => {
      const s = (q.state.data as { status?: string } | undefined)?.status
      return s === 'queued' || s === 'running' ? 2000 : false
    },
    queryFn: async () => {
      const r = (await api.get(`/ai/jobs/${jobId}`)).data
      if (r.status === 'done' && r.result) {
        adopt(r.result)
        setJobId(null)
        message.success('Photo read — review the rows below')
      } else if (r.status === 'error') {
        setJobId(null)
        message.error(r.error ?? 'OCR failed')
      }
      return r
    },
  })

  const adopt = (result: { rows?: OcrRow[]; items?: OcrRow[]; header?: DnHeader }) => {
    // Stable per-row keys (rowKey by index is deprecated and reorders badly).
    setRows((result.rows ?? result.items ?? []).map((r, i) => ({ ...r, _key: `r${i}` })))
    setHeader(result.header ?? null)
  }

  const patch = (i: number, p: Partial<OcrRow>) =>
    setRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, ...p } : r)))

  const doPaste = async () => {
    try {
      const r = await api.post(`/ai/paste/${kind}`, { text: pasteText })
      adopt(r.data)
      message.success('Parsed — review the rows below')
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  // Stage every row that has a SAP code and a positive qty, through the
  // EXISTING staging services (audited; land as drafts for HOD approval).
  const stage = async () => {
    if (!site) return
    const ready = rows.filter((r) => r.SAP_Code && Number(r.quantity) > 0)
    setStaging(true)
    let ok = 0
    const failed: string[] = []
    for (const r of ready) {
      try {
        if (isConsumption) {
          await api.post('/entry/consumption', {
            Date: date.format('YYYY-MM-DD'), SAP_Code: r.SAP_Code,
            Quantity: Number(r.quantity), Site_ID: site,
            Issued_To: r.issued_to || null, Work_Type: r.work_type || null,
            Remarks: 'OCR import',
          })
        } else {
          await api.post('/entry/receipts', {
            Date: date.format('YYYY-MM-DD'), SAP_Code: r.SAP_Code,
            Quantity: Number(r.quantity), Site_ID: site,
            Supplier: header?.Mob_From || null,
            Remarks: `OCR DN ${header?.DN_No || ''}`.trim(),
          })
        }
        ok += 1
      } catch (e) {
        failed.push(`${r.material_text}: ${errMsg(e)}`)
      }
    }
    setStaging(false)
    if (ok) message.success(`${ok} row(s) staged for HOD approval`)
    if (failed.length) message.warning(`${failed.length} row(s) failed — ${failed[0]}`)
    setRows((rs) => rs.filter((r) => !(r.SAP_Code && Number(r.quantity) > 0) || failed.length > 0))
    if (!failed.length && ok) { setHeader(null) }
  }

  const invOptions = (inventory.data?.items ?? []).map((r: ApiRow) => ({
    value: String(r.SAP_Code), label: `${r.SAP_Code} — ${r.Equipment_Description ?? ''}`,
  }))

  const columns: ColumnsType<OcrRow> = [
    { title: 'Match', dataIndex: 'match_state', width: 90,
      render: (v: OcrRow['match_state']) => <Tag color={MATCH_COLOR[v]}>{v}</Tag> },
    { title: 'As written', dataIndex: 'material_text', ellipsis: true },
    {
      title: 'Material (SAP)', key: 'sap', width: 320,
      render: (_: unknown, r, i) => (
        <Select showSearch size="small" style={{ width: 300 }} optionFilterProp="label"
          value={r.SAP_Code || undefined} placeholder="Pick material"
          onChange={(v) => patch(i, { SAP_Code: v, match_state: r.match_state === 'unknown' ? 'pick' : r.match_state })}
          options={[
            ...r.candidates.map((c) => ({
              value: c.SAP_Code,
              label: `★ ${c.SAP_Code} — ${c.Equipment_Description} (${Math.round(c.score * 100)}%)`,
            })),
            ...invOptions.filter((o: { value: string }) => !r.candidates.some((c) => c.SAP_Code === o.value)),
          ]} />
      ),
    },
    {
      title: 'Qty', key: 'q', width: 110,
      render: (_: unknown, r, i) => (
        <InputNumber size="small" min={0} value={r.quantity}
          onChange={(v) => patch(i, { quantity: v ?? 0 })} style={{ width: 90 }} />
      ),
    },
    { title: 'UOM', dataIndex: 'uom', width: 70, render: (v) => v || '—' },
    ...(isConsumption
      ? [{
          title: 'Issued to', key: 'it', width: 150,
          render: (_: unknown, r: OcrRow, i: number) => (
            <Input size="small" value={r.issued_to}
              onChange={(e) => patch(i, { issued_to: e.target.value })} />
          ),
        }]
      : []),
    {
      title: '', key: 'x', width: 50,
      render: (_: unknown, __, i) => (
        <Button size="small" type="text" icon={<DeleteOutlined />}
          onClick={() => setRows((rs) => rs.filter((_r, idx) => idx !== i))} />
      ),
    },
  ]

  const readyCount = useMemo(
    () => rows.filter((r) => r.SAP_Code && Number(r.quantity) > 0).length, [rows])
  const polling = jobId != null && (job.data == null
    || job.data.status === 'queued' || job.data.status === 'running')

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>📷 OCR Import</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Photograph a handwritten consumption list or a printed delivery note — the
        local AI reads it into rows you review, then stage for HOD approval. The
        Paste tab works even when the AI is offline.
      </Typography.Paragraph>

      <Space style={{ marginBottom: 16 }} wrap>
        <Radio.Group value={kind} buttonStyle="solid"
          onChange={(e) => { setKind(e.target.value); setRows([]); setHeader(null) }}
          options={[
            { value: 'ocr_consumption', label: '📝 Consumption log' },
            { value: 'ocr_delivery_note', label: '🚚 Delivery note' },
          ]} optionType="button" />
      </Space>

      <Space align="start" wrap style={{ marginBottom: 16 }}>
        <Card size="small" title={<><CameraOutlined /> Photo</>} style={{ width: 420 }}>
          {aiHealth && !aiHealth.ok && (
            <Alert type="warning" showIcon style={{ marginBottom: 8 }}
              title="Local AI is offline — use the Paste lane meanwhile." />
          )}
          {polling ? (
            <div style={{ textAlign: 'center', padding: 24 }}>
              <Spin />
              <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>
                Reading the photo… ({job.data?.status ?? 'queued'}) — first scan can
                take a minute while the vision model warms up.
              </Typography.Paragraph>
            </div>
          ) : (
            <Upload.Dragger accept="image/*" maxCount={1} showUploadList={false}
              disabled={Boolean(aiHealth && !aiHealth.ok)}
              customRequest={async ({ file, onSuccess, onError }) => {
                const fd = new FormData()
                fd.append('file', file as Blob)
                try {
                  const r = await api.post('/ai/jobs', fd, { params: { kind } })
                  setJobId(r.data.job_id)
                  onSuccess?.(r.data)
                } catch (e) {
                  message.error(errMsg(e))
                  onError?.(e as Error)
                }
              }}>
              <p className="ant-upload-drag-icon"><InboxOutlined /></p>
              <p className="ant-upload-text">Drop / take a photo</p>
              <p className="ant-upload-hint">JPEG · PNG · WebP · HEIC (iPhone) — auto-rotated + downscaled</p>
            </Upload.Dragger>
          )}
        </Card>

        <Card size="small" title="📋 Paste (offline)" style={{ width: 420 }}>
          <Input.TextArea rows={6} value={pasteText} onChange={(e) => setPasteText(e.target.value)}
            placeholder={isConsumption
              ? 'Imran\t6m pipe\tNos\t45\tsite work\nAli, double clamp, PCS, 12'
              : 'DN_No: 15668\nMob_From: GI - ABU HADRIYAH\n6m pipe, Nos, 45'} />
          <Button style={{ marginTop: 8 }} onClick={doPaste} disabled={!pasteText.trim()}>
            Parse
          </Button>
        </Card>
      </Space>

      {header && (
        <Descriptions size="small" bordered column={4} style={{ marginBottom: 16 }}
          items={[
            { key: '1', label: 'DN No', children: header.DN_No || '—' },
            { key: '2', label: 'Date', children: header.Date || '—' },
            { key: '3', label: 'From', children: header.Mob_From || '—' },
            { key: '4', label: 'To', children: header.Mob_To || '—' },
            { key: '5', label: 'Driver', children: header.Driver_Name || '—' },
            { key: '6', label: 'Vehicle', children: header.Vehicle_No || '—' },
            { key: '7', label: 'Prepared by', children: header.Prepared_by || '—' },
          ]} />
      )}

      {rows.length > 0 && (
        <>
          <Table size="small" columns={columns} dataSource={rows}
            rowKey={(r) => String(r._key)} pagination={false} scroll={{ x: 'max-content' }} />
          <Space style={{ marginTop: 16 }} wrap>
            <DatePicker value={date} onChange={(d) => d && setDate(d)} allowClear={false} />
            {isAdmin ? (
              <Select placeholder="Site" style={{ width: 150 }} value={site} onChange={setSite}
                options={(sites ?? []).map((s) => ({ value: s, label: s }))} />
            ) : (
              <Tag>{site}</Tag>
            )}
            <Popconfirm title={`Stage ${readyCount} row(s) as ${isConsumption ? 'consumption' : 'receipt'} drafts?`}
              onConfirm={stage}>
              <Button type="primary" disabled={readyCount === 0 || !site} loading={staging}>
                Stage {readyCount} row(s) for HOD approval
              </Button>
            </Popconfirm>
            <Button onClick={() => { setRows([]); setHeader(null) }}>Discard</Button>
          </Space>
        </>
      )}
    </div>
  )
}
