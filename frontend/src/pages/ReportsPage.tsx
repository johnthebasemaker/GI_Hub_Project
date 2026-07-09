import { useRef, useState } from 'react'
import {
  App, Button, Card, Col, DatePicker, Form, Input, InputNumber, Modal, Popconfirm,
  Row as AntRow, Select, Space, Spin, Switch, Table, Tabs, Tag, Typography,
} from 'antd'
import { DownloadOutlined, FileExcelOutlined, FilePdfOutlined, FileTextOutlined, InboxOutlined, PlayCircleOutlined, RobotOutlined, ThunderboltOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs, { Dayjs } from 'dayjs'
import {
  downloadArchived, downloadReport, useArchiveReport, useDeleteArchived,
  useReportArchive, useReports, useScheduleMutation, useSchedules, useSites,
} from '../api/hooks'
import { streamSse } from '../api/sse'
import { api } from '../api/client'
import type { Row } from '../api/client'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Download failed'
}

const FORMATS: { key: string; label: string; icon: React.ReactNode }[] = [
  { key: 'xlsx', label: 'Excel', icon: <FileExcelOutlined /> },
  { key: 'pdf', label: 'PDF', icon: <FilePdfOutlined /> },
  { key: 'csv', label: 'CSV', icon: <FileTextOutlined /> },
]

function ReportCard({ report }: { report: Row }) {
  const { message } = App.useApp()
  const { data: sites } = useSites()
  const filters = (report.filters as string[]) ?? []
  const [site, setSite] = useState<string | undefined>()
  const [days, setDays] = useState(30)
  const [withinDays, setWithinDays] = useState(30)
  const [status, setStatus] = useState<string | undefined>()
  const [busy, setBusy] = useState<string | null>(null)

  const params: Record<string, unknown> = {}
  if (filters.includes('site_id') && site) params.site_id = site
  if (filters.includes('days')) params.days = days
  if (filters.includes('within_days')) params.within_days = withinDays
  if (filters.includes('status') && status) params.status = status

  const archive = useArchiveReport()

  const doDownload = async (fmt: string) => {
    setBusy(fmt)
    try {
      await downloadReport(String(report.key), fmt, params)
      message.success(`${report.label} (${fmt.toUpperCase()}) downloaded`)
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setBusy(null)
    }
  }

  const doArchive = async () => {
    try {
      const res = await archive.mutateAsync({ key: report.key, format: 'xlsx', ...params })
      message.success(`Archived as ${res.name}`)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  // Phase 7 — deliver this report to a WhatsApp number (as a PDF document).
  const [waOpen, setWaOpen] = useState(false)
  const [waTo, setWaTo] = useState('')
  const [waBusy, setWaBusy] = useState(false)
  const doWhatsApp = async () => {
    if (!waTo.trim()) return
    setWaBusy(true)
    try {
      const res = await api.post(`/reports/${report.key}/whatsapp`,
        { to: waTo.trim(), format: 'pdf', site_id: params.site_id, days: params.days, status: params.status })
      if (res.data.status === 'sent') message.success('Report sent to WhatsApp')
      else message.warning(`Queued but not delivered: ${res.data.error ?? 'see WhatsApp Console'}`)
      setWaOpen(false); setWaTo('')
    } catch (e) { message.error(errMsg(e)) } finally { setWaBusy(false) }
  }

  return (
    <Card title={String(report.label)} size="small" style={{ height: '100%' }}>
      <Typography.Paragraph type="secondary" style={{ minHeight: 44 }}>
        {String(report.description ?? '')}
      </Typography.Paragraph>
      <Space wrap style={{ marginBottom: 12 }}>
        {filters.includes('site_id') && (
          <Select allowClear placeholder="All sites" style={{ width: 150 }} value={site} onChange={setSite}
            options={(sites ?? []).map((s) => ({ value: s, label: s }))} />
        )}
        {filters.includes('days') && (
          <Space size={4}>
            <Typography.Text type="secondary">Last</Typography.Text>
            <InputNumber min={1} max={3650} value={days} onChange={(v) => setDays(v ?? 30)} style={{ width: 80 }} />
            <Typography.Text type="secondary">days</Typography.Text>
          </Space>
        )}
        {filters.includes('within_days') && (
          <Space size={4}>
            <Typography.Text type="secondary">Within</Typography.Text>
            <InputNumber min={0} max={3650} value={withinDays} onChange={(v) => setWithinDays(v ?? 30)} style={{ width: 80 }} />
            <Typography.Text type="secondary">days</Typography.Text>
          </Space>
        )}
        {filters.includes('status') && (
          <Select allowClear placeholder="Any status" style={{ width: 150 }} value={status} onChange={setStatus}
            options={['open', 'closed', 'force_closed', 'cancelled'].map((s) => ({ value: s, label: s }))} />
        )}
      </Space>
      <div>
        <Space wrap>
          {FORMATS.map((f) => (
            <Button key={f.key} icon={f.icon} loading={busy === f.key} onClick={() => doDownload(f.key)}>
              {f.label}
            </Button>
          ))}
          <Button icon={<InboxOutlined />} loading={archive.isPending} onClick={doArchive}>
            Archive
          </Button>
          <Button onClick={() => { setWaOpen(true); setWaTo('') }}>WhatsApp</Button>
        </Space>
      </div>
      <Modal open={waOpen} title={`Send "${report.label}" via WhatsApp`} onOk={doWhatsApp}
        onCancel={() => setWaOpen(false)} okText="Send" okButtonProps={{ disabled: !waTo.trim() }}
        confirmLoading={waBusy} destroyOnHidden>
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          Sends the report as a PDF document. Enter the recipient number in E.164 (no “+”), e.g. 9665XXXXXXXX.
        </Typography.Paragraph>
        <Input placeholder="Recipient WhatsApp number" value={waTo} onChange={(e) => setWaTo(e.target.value)} />
      </Modal>
    </Card>
  )
}

function ArchiveTab() {
  const { message } = App.useApp()
  const { data: items, isFetching } = useReportArchive()
  const del = useDeleteArchived()

  const columns: ColumnsType<Row> = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: 'Name', dataIndex: 'name', ellipsis: true },
    { title: 'Report', dataIndex: 'report_type', width: 150 },
    { title: 'Format', dataIndex: 'format', width: 80, render: (v: string) => <Tag>{v}</Tag> },
    { title: 'Site', dataIndex: 'site_id', width: 90, render: (v) => v ?? '—' },
    { title: 'By', dataIndex: 'generated_by', width: 150, ellipsis: true },
    { title: 'At', dataIndex: 'generated_at', width: 160,
      render: (v) => (v ? String(v).slice(0, 16).replace('T', ' ') : '—') },
    { title: 'Size', dataIndex: 'size_bytes', width: 90, align: 'right',
      render: (v) => (v ? `${Math.ceil(Number(v) / 1024)} KB` : '—') },
    {
      title: 'Action', key: '__a', width: 140,
      render: (_: unknown, r: Row) => (
        <Space>
          <Button size="small" icon={<DownloadOutlined />}
            onClick={() => downloadArchived(Number(r.id), String(r.name)).catch(() => message.error('Download failed'))} />
          <Popconfirm title="Delete this archived file?" onConfirm={async () => {
            try { await del.mutateAsync(Number(r.id)); message.success('Deleted') }
            catch (e) { message.error(errMsg(e)) }
          }}>
            <Button size="small" danger>Delete</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return <Table size="small" loading={isFetching} columns={columns} dataSource={items ?? []}
    rowKey={(r) => String(r.id)} scroll={{ x: 'max-content' }}
    pagination={{ pageSize: 20, showTotal: (t) => `${t} archived` }} />
}

function SchedulesTab() {
  const { message } = App.useApp()
  const { data: reports } = useReports()
  const { data: items, isFetching } = useSchedules()
  const { create, toggle, remove, run } = useScheduleMutation()
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()

  const submit = async () => {
    const v = await form.validateFields()
    try {
      await create.mutateAsync(v)
      message.success('Schedule created — the daemon will run it on its due date')
      setOpen(false)
      form.resetFields()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = [
    { title: 'Label', dataIndex: 'label', ellipsis: true },
    { title: 'Report', dataIndex: 'report_type', width: 150 },
    { title: 'Frequency', dataIndex: 'frequency', width: 160 },
    { title: 'Format', dataIndex: 'format', width: 80, render: (v: string) => <Tag>{v}</Tag> },
    { title: 'Site', dataIndex: 'site_id', width: 90, render: (v) => v ?? '—' },
    { title: 'Last run', dataIndex: 'last_run', width: 160,
      render: (v) => (v ? String(v).slice(0, 16).replace('T', ' ') : 'never') },
    {
      title: 'Active', dataIndex: 'active', width: 80,
      render: (v, r) => (
        <Switch size="small" checked={!!v}
          onChange={() => toggle.mutateAsync(Number(r.id)).catch((e) => message.error(errMsg(e)))} />
      ),
    },
    {
      title: 'Action', key: '__a', width: 160,
      render: (_: unknown, r: Row) => (
        <Space>
          <Button size="small" icon={<PlayCircleOutlined />} loading={run.isPending}
            onClick={async () => {
              try { const res = await run.mutateAsync(Number(r.id)); message.success(`Ran — archived #${res.archive?.id}`) }
              catch (e) { message.error(errMsg(e)) }
            }}>
            Run now
          </Button>
          <Popconfirm title="Delete this schedule?" onConfirm={async () => {
            try { await remove.mutateAsync(Number(r.id)); message.success('Deleted') }
            catch (e) { message.error(errMsg(e)) }
          }}>
            <Button size="small" danger>Delete</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Button type="primary" style={{ marginBottom: 12 }} onClick={() => setOpen(true)}>
        New schedule
      </Button>
      <Table size="small" loading={isFetching} columns={columns} dataSource={items ?? []}
        rowKey={(r) => String(r.id)} scroll={{ x: 'max-content' }} pagination={false} />
      <Modal title="Schedule a report" open={open} onOk={submit} onCancel={() => setOpen(false)}
        confirmLoading={create.isPending} okText="Create" destroyOnHidden>
        <Form form={form} layout="vertical" preserve={false}
          initialValues={{ format: 'xlsx', frequency: 'daily 06:00' }}>
          <Form.Item name="label" label="Label" rules={[{ required: true }]}>
            <Input placeholder="e.g. Morning stock report" />
          </Form.Item>
          <Form.Item name="report_type" label="Report" rules={[{ required: true }]}>
            <Select options={(reports ?? []).map((r) => ({ value: String(r.key), label: String(r.label) }))} />
          </Form.Item>
          <Form.Item name="frequency" label="Frequency" rules={[{ required: true }]}
            extra="daily HH:MM · weekly mon..sun HH:MM · monthly DD HH:MM (server time)">
            <Input placeholder="daily 06:00" />
          </Form.Item>
          <Form.Item name="format" label="Format">
            <Select options={['xlsx', 'pdf', 'csv'].map((f) => ({ value: f, label: f.toUpperCase() }))} />
          </Form.Item>
          <Form.Item name="recipients" label="Notify (usernames, comma-separated — blank = you)">
            <Input placeholder="hod, admin" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

// --- 🤖 AI tab (Phase AI-5): streaming EOD summary + progressive insights -------
function EodSummaryCard() {
  const { message } = App.useApp()
  const [date, setDate] = useState<Dayjs>(dayjs())
  const [running, setRunning] = useState(false)
  const [textOut, setTextOut] = useState('')
  const abortRef = useRef<AbortController | null>(null)

  const generate = async () => {
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl
    setRunning(true)
    setTextOut('')
    try {
      await streamSse('/ai/eod-summary', { date: date.format('YYYY-MM-DD') }, (ev) => {
        if (ev.token) setTextOut((t) => t + ev.token)
        if (ev.error) message.warning(String(ev.error))
      }, ctrl.signal)
    } catch (e) {
      if ((e as Error).name !== 'AbortError') message.error(String(e))
    } finally {
      setRunning(false)
    }
  }

  return (
    <Card size="small" title="✨ AI Executive Summary" style={{ marginBottom: 16 }}>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        A 3–6 sentence narration of the day's ledger activity — totals, site
        differences, and the most critical low-stock items. Streams live from
        the local AI; numbers come straight from the database.
      </Typography.Paragraph>
      <Space style={{ marginBottom: 12 }}>
        <DatePicker value={date} onChange={(d) => d && setDate(d)} allowClear={false} />
        <Button type="primary" icon={<ThunderboltOutlined />} loading={running}
          onClick={generate}>
          Generate
        </Button>
      </Space>
      {(textOut || running) && (
        <Typography.Paragraph style={{
          background: 'rgba(255,255,255,0.04)', padding: 12, borderRadius: 8,
        }}>
          {textOut}{running && <Spin size="small" style={{ marginLeft: 8 }} />}
        </Typography.Paragraph>
      )}
    </Card>
  )
}

interface Insight {
  id: string
  icon: string
  metric: string
  metric_label: string
  severity: 'crit' | 'low' | 'ok'
  confidence: number
  title?: string
  body?: string
  recs?: string[]
}

const SEV_COLOR = { crit: 'red', low: 'gold', ok: 'green' } as const

function InsightsCard() {
  const { message } = App.useApp()
  const [running, setRunning] = useState(false)
  const [insights, setInsights] = useState<Insight[]>([])

  const generate = async () => {
    setRunning(true)
    setInsights([])
    try {
      // Probe events land first (instant, deterministic SQL); commentary
      // events upgrade each card as the model narrates.
      await streamSse('/ai/insights', {}, (ev) => {
        const probe = ev.probe as Insight | undefined
        const comm = ev.commentary as (Partial<Insight> & { id: string }) | undefined
        if (probe) setInsights((xs) => [...xs, probe])
        if (comm) setInsights((xs) => xs.map((x) => (x.id === comm.id ? { ...x, ...comm } : x)))
        if (ev.error) message.warning(String(ev.error))
      })
    } catch (e) {
      message.error(String(e))
    } finally {
      setRunning(false)
    }
  }

  return (
    <Card size="small" title="🤖 AI Insights">
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        Five deterministic SQL probes — consumption spikes, projected stockouts,
        expired lots, supplier consolidation, inventory health — with AI-written
        narration. SQL owns the numbers; the model only explains them.
      </Typography.Paragraph>
      <Button type="primary" icon={<RobotOutlined />} loading={running}
        onClick={generate} style={{ marginBottom: 12 }}>
        Run insights
      </Button>
      <AntRow gutter={[12, 12]}>
        {insights.map((ins) => (
          <Col key={ins.id} xs={24} md={12} lg={8}>
            <Card size="small">
              <Space style={{ justifyContent: 'space-between', width: '100%' }}>
                <Typography.Text strong>
                  {ins.icon} {ins.title ?? ins.id.replace(/_/g, ' ')}
                </Typography.Text>
                <Tag color={SEV_COLOR[ins.severity]}>{ins.severity}</Tag>
              </Space>
              <div style={{ margin: '8px 0' }}>
                <Typography.Title level={4} style={{ margin: 0 }}>{ins.metric}</Typography.Title>
                <Typography.Text type="secondary">{ins.metric_label}</Typography.Text>
              </div>
              {ins.body ? (
                <>
                  <Typography.Paragraph style={{ fontSize: 13 }}>{ins.body}</Typography.Paragraph>
                  <ul style={{ paddingLeft: 18, margin: 0, fontSize: 13 }}>
                    {(ins.recs ?? []).map((r, i) => <li key={i}>{r}</li>)}
                  </ul>
                </>
              ) : (
                <Space><Spin size="small" /><Typography.Text type="secondary">writing commentary…</Typography.Text></Space>
              )}
              <div style={{ marginTop: 8 }}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  confidence {ins.confidence}%
                </Typography.Text>
              </div>
            </Card>
          </Col>
        ))}
        {!running && insights.length === 0 && (
          <Col span={24}>
            <Typography.Text type="secondary">
              Run to scan the database for anything worth your attention.
            </Typography.Text>
          </Col>
        )}
      </AntRow>
    </Card>
  )
}

function AiTab() {
  return (
    <div>
      <EodSummaryCard />
      <InsightsCard />
    </div>
  )
}

export default function ReportsPage() {
  const { data: reports, isFetching } = useReports()
  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Reports</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Export the ERP's live data as Excel, PDF, or CSV — download directly, keep a copy
        in the archive, or schedule automatic generation.
      </Typography.Paragraph>
      <Tabs
        items={[
          {
            key: 'generate', label: 'Generate',
            children: (
              <AntRow gutter={[16, 16]}>
                {(reports ?? []).map((r) => (
                  <Col key={String(r.key)} xs={24} md={12} lg={8}>
                    <ReportCard report={r} />
                  </Col>
                ))}
                {!isFetching && (reports ?? []).length === 0 && (
                  <Col span={24}><Typography.Text type="secondary">No reports available.</Typography.Text></Col>
                )}
              </AntRow>
            ),
          },
          { key: 'archive', label: 'Archive', children: <ArchiveTab /> },
          { key: 'schedules', label: 'Schedules', children: <SchedulesTab /> },
          { key: 'ai', label: '🤖 AI', children: <AiTab /> },
        ]}
      />
    </div>
  )
}
