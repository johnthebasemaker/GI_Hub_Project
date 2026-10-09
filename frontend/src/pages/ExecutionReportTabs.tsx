/**
 * Phase 6 — the analytical half of the Man-Hours portal.
 *
 * Four views over the SAME execution entries, because one table cannot answer
 * the four different questions people bring to this data:
 *
 *  · APPROVAL QUEUE      — what is waiting on me right now
 *  · ACTUAL vs BENCHMARK — where are we losing material or hours, and by how much
 *  · REASON LOG          — what did people SAY, and what did the HOD change
 *  · SURFACE PREP        — prep area, kept deliberately apart from lining progress
 *
 * ⚠️ THE TOTALS ROW SUMS ABSOLUTES and derives one percentage from the sums.
 * It never averages the per-entry percentages: that weights a 2 m² entry the
 * same as a 2,000 m² one, which is how a programme that is 8% over reports
 * itself as on target.
 */
import { DownloadOutlined } from '@ant-design/icons'
import SystemCode from '../sme/SystemCode'
import {
  Alert, App, Button, Card, DatePicker, Row, Space, Statistic, Table, Tag,
  Tooltip, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import type { Dayjs } from 'dayjs'
import { useState } from 'react'

import { api } from '../api/client'
import { downloadDocument } from '../api/hooks'
import KpiRow from '../components/KpiRow'

type Row = Record<string, unknown>

const n2 = (v: unknown) => (v == null ? '—' : Number(v).toFixed(2))
const pctText = (v: unknown) =>
  v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v)}%`

/**
 * A variance reads green only when it is genuinely comparable AND small.
 * `null` is its own state: "no benchmark to compare against" must never look
 * like "matched perfectly".
 */
export function VarianceTag({ value }: { value: unknown }) {
  if (value == null) {
    return (
      <Tooltip title="No benchmark to compare against. That is not the same as a
        perfect match, so it is deliberately not shown as 0%.">
        <Tag>n/a</Tag>
      </Tooltip>)
  }
  const x = Number(value)
  const color = Math.abs(x) <= 5 ? 'green' : Math.abs(x) <= 15 ? 'gold' : 'red'
  return <Tag color={color}>{pctText(x)}</Tag>
}

function ExportButtons({ path, params }: { path: string; params?: Record<string, string> }) {
  const { message } = App.useApp()
  const [busy, setBusy] = useState(false)
  // ⚠️ Phase 23a: these used `window.open(url)`. A new tab carries NO bearer
  // token (the access token lives in memory, never in a cookie), so every
  // press came back 401. The authenticated helper fetches with the token, and
  // the server still applies rule 12 (formula defusing) on the way out.
  const go = async (format: 'csv' | 'xlsx') => {
    setBusy(true)
    try {
      await downloadDocument(path, { ...(params ?? {}), format },
        `${path.split('/').pop()}.${format}`)
    } catch (e) {
      message.error((e as Error)?.message || 'Download failed')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Space>
      <Button size="small" icon={<DownloadOutlined />} disabled={busy} onClick={() => go('xlsx')}>Excel</Button>
      <Button size="small" icon={<DownloadOutlined />} disabled={busy} onClick={() => go('csv')}>CSV</Button>
    </Space>
  )
}

// ─── Actual vs benchmark ─────────────────────────────────────────────────────
export function ExecVarianceTab() {
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null)
  const params: Record<string, string> = range
    ? { date_from: range[0].format('YYYY-MM-DD'), date_to: range[1].format('YYYY-MM-DD') }
    : {}
  const { data, isFetching } = useQuery({
    queryKey: ['/execution/report/variance', JSON.stringify(params)],
    queryFn: async () => (await api.get<{ items: Row[]; totals: Row }>(
      '/execution/report/variance', { params })).data,
  })
  const items = data?.items ?? []
  const t = data?.totals ?? {}

  const columns: ColumnsType<Row> = [
    { title: 'Entry', dataIndex: 'Entry_No', width: 150 },
    { title: 'Date', dataIndex: 'Work_Date', width: 105 },
    { title: 'Equipment', dataIndex: 'Equipment_Tag_No', width: 160 },
    { title: 'System', dataIndex: 'Lining_System_Code', width: 150,
      render: (v: string, r: Row) => v === '(surface prep)'
        ? <Tag color="gold">surface prep{r.Type ? ` [${r.Type}]` : ''}</Tag>
        : <SystemCode code={v} type={String(r.Type ?? '')} plain /> },
    { title: 'Sub-activity', dataIndex: 'Execution_Sub_Activity_Code', width: 120 },
    { title: 'Area m²', dataIndex: 'Actual_SQM', width: 90, align: 'right', render: n2 },
    { title: 'Material actual', dataIndex: 'Material_Actual', width: 120, align: 'right', render: n2 },
    { title: 'Material benchmark', dataIndex: 'Material_Benchmark', width: 140, align: 'right', render: n2 },
    { title: 'Material var.', dataIndex: 'Material_Variance_Pct', width: 110, align: 'right',
      sorter: (a, b) => Number(a.Material_Variance_Pct ?? 0) - Number(b.Material_Variance_Pct ?? 0),
      render: (v) => <VarianceTag value={v} /> },
    { title: 'Man-hrs actual', dataIndex: 'Manpower_Actual_Manhours', width: 120, align: 'right', render: n2 },
    { title: 'Man-hrs benchmark', dataIndex: 'Manpower_Benchmark_Manhours', width: 140, align: 'right', render: n2 },
    { title: 'Manpower var.', dataIndex: 'Manpower_Variance_Pct', width: 115, align: 'right',
      sorter: (a, b) => Number(a.Manpower_Variance_Pct ?? 0) - Number(b.Manpower_Variance_Pct ?? 0),
      render: (v) => <VarianceTag value={v} /> },
    { title: 'Status', dataIndex: 'status', width: 140 },
  ]

  return (
    <div>
      <Space style={{ marginBottom: 12 }} wrap>
        <DatePicker.RangePicker onChange={(v) => setRange(v as [Dayjs, Dayjs] | null)} />
        <ExportButtons path="/execution/report/variance" params={params} />
      </Space>
      <KpiRow gap={12} style={{ marginBottom: 12 }}>
        <Card size="small">
          <Statistic title="Entries" value={String(t.Entries ?? 0)} /></Card>
        <Card size="small">
          <Statistic title="Area reported" value={n2(t.Actual_SQM)} suffix="m²" /></Card>
        <Card size="small">
          <Statistic title="Material vs benchmark" value={n2(t.Material_Actual)}
            suffix={<span style={{ fontSize: 13 }}>/ {n2(t.Material_Benchmark)}{' '}
              <VarianceTag value={t.Material_Variance_Pct} /></span>} /></Card>
        <Card size="small">
          <Statistic title="Man-hours vs benchmark" value={n2(t.Manpower_Actual_Manhours)}
            suffix={<span style={{ fontSize: 13 }}>/ {n2(t.Manpower_Benchmark_Manhours)}{' '}
              <VarianceTag value={t.Manpower_Variance_Pct} /></span>} /></Card>
      </KpiRow>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        Totals sum the absolute figures and derive one percentage from the sums —
        they are not the average of the per-entry percentages, which would weight
        a 2 m² entry the same as a 2,000 m² one.
      </Typography.Paragraph>
      <Table sticky={{ offsetHeader: 64 }} size="small" loading={isFetching}
        columns={columns} dataSource={items} rowKey={(r) => String(r.Entry_No)}
        scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (x) => `${x} entries` }} />
    </div>
  )
}

// ─── Reason audit log ────────────────────────────────────────────────────────
export function ReasonLogTab() {
  const { data, isFetching } = useQuery({
    queryKey: ['/execution/report/reasons'],
    queryFn: async () =>
      (await api.get<{ items: Row[] }>('/execution/report/reasons')).data.items,
  })
  const columns: ColumnsType<Row> = [
    { title: 'Entry', dataIndex: 'Entry_No', width: 150 },
    { title: 'Date', dataIndex: 'Work_Date', width: 105 },
    { title: 'Equipment', dataIndex: 'Equipment_Tag_No', width: 160 },
    { title: 'Supervisor', dataIndex: 'supervisor_username', width: 120 },
    { title: 'Material reason', dataIndex: 'Material_Variance_Reason', width: 220 },
    { title: 'Manpower reason', dataIndex: 'Manpower_Variance_Reason', width: 220 },
    { title: 'HOD changed', dataIndex: 'Changed', width: 240,
      render: (v: string) => v
        ? <Tooltip title={v}><Tag color="purple">{v.slice(0, 40)}{v.length > 40 ? '…' : ''}</Tag></Tooltip>
        : <span style={{ opacity: 0.45 }}>—</span> },
    { title: 'HOD justification', dataIndex: 'HOD_Edit_Justification', width: 240 },
    { title: 'Rejected because', dataIndex: 'Reject_Reason', width: 200 },
    { title: 'Status', dataIndex: 'status', width: 140 },
  ]
  return (
    <div>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message="Every entry carries a stated reason, at any variance"
        description="A reason demanded only past a threshold teaches people to
          aim just under it. Where the HOD corrected a figure, the before→after
          is shown beside their justification — an audit line saying a number
          changed without saying from what is not an audit trail." />
      <Space style={{ marginBottom: 12 }}>
        <ExportButtons path="/execution/report/reasons" />
      </Space>
      <Table sticky={{ offsetHeader: 64 }} size="small" loading={isFetching}
        columns={columns} dataSource={data ?? []} rowKey={(r) => String(r.Entry_No)}
        scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 20, showSizeChanger: true }} />
    </div>
  )
}

// ─── Surface-prep progress ───────────────────────────────────────────────────
export function SurfacePrepTab() {
  const { data, isFetching } = useQuery({
    queryKey: ['/execution/report/surface-prep'],
    queryFn: async () => (await api.get<{ items: Row[]; totals: Row }>(
      '/execution/report/surface-prep')).data,
  })
  const t = data?.totals ?? {}
  const columns: ColumnsType<Row> = [
    { title: 'Equipment', dataIndex: 'Equipment_Tag_No', width: 180 },
    { title: 'Sub-activity', dataIndex: 'Execution_Sub_Activity_Code', width: 130 },
    { title: 'Activity', dataIndex: 'Activity', width: 220 },
    { title: 'Variant', dataIndex: 'Variant_Key', width: 130,
      render: (v: string) => v ? <Tag color="purple">{v}</Tag> : '—' },
    { title: 'Prep done m²', dataIndex: 'Done_SQM', width: 120, align: 'right', render: n2 },
    { title: 'Equipment area m²', dataIndex: 'Equipment_Area_SQM', width: 140, align: 'right', render: n2 },
    { title: 'Coverage', dataIndex: 'Coverage_Pct', width: 110, align: 'right',
      sorter: (a, b) => Number(a.Coverage_Pct ?? 0) - Number(b.Coverage_Pct ?? 0),
      render: (v: unknown) => {
        if (v == null) return <Tag>n/a</Tag>
        const x = Number(v)
        return <Tag color={x >= 100 ? 'green' : x >= 50 ? 'gold' : 'default'}>{x}%</Tag>
      } },
    { title: 'Entries', dataIndex: 'Entry_Count', width: 90, align: 'right' },
    { title: 'Last entry', dataIndex: 'Last_Entry_No', width: 150 },
  ]
  return (
    <div>
      <Alert type="warning" showIcon style={{ marginBottom: 12 }}
        message="Surface prep is tracked apart from lining progress — on purpose"
        description="Blasting 100 m² of a tank is not 100 m² of lining done; the
          surface is only ready to be lined. Folding this into lining progress
          would report a vessel as part-lined the moment it was cleaned, and
          that figure drives completion %, buildable area and the buy list.
          Coverage can exceed 100% — a surface can be re-blasted, and clamping
          it would hide rework instead of showing it." />
      <KpiRow gap={12} style={{ marginBottom: 12 }}>
        <Card size="small">
          <Statistic title="Prep area recorded" value={n2(t.Prep_SQM)} suffix="m²" /></Card>
        <Card size="small">
          <Statistic title="Activities tracked" value={String(t.Activities ?? 0)} /></Card>
        <Card size="small">
          <Statistic title="Approved entries" value={String(t.Entries ?? 0)} /></Card>
      </KpiRow>
      <Space style={{ marginBottom: 12 }}>
        <ExportButtons path="/execution/report/surface-prep" />
      </Space>
      <Table sticky={{ offsetHeader: 64 }} size="small" loading={isFetching}
        columns={columns} dataSource={data?.items ?? []}
        rowKey={(r) => `${r.Equipment_Tag_No}|${r.Execution_Sub_Activity_Code}|${r.Variant_Key}`}
        scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 20, showSizeChanger: true }} />
    </div>
  )
}
