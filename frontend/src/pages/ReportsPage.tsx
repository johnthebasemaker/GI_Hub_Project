import { useState } from 'react'
import {
  App, Button, Card, Col, Form, Input, InputNumber, Modal, Popconfirm,
  Row as AntRow, Select, Space, Switch, Table, Tabs, Tag, Typography,
} from 'antd'
import { DownloadOutlined, FileExcelOutlined, FilePdfOutlined, FileTextOutlined, InboxOutlined, PlayCircleOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import {
  downloadArchived, downloadReport, useArchiveReport, useDeleteArchived,
  useReportArchive, useReports, useScheduleMutation, useSchedules, useSites,
} from '../api/hooks'
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
        </Space>
      </div>
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
        ]}
      />
    </div>
  )
}
