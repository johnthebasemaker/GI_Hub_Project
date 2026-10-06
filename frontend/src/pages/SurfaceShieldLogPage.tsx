import { useMemo, useState } from 'react'
import {
  App, Button, Card, Col, Collapse, DatePicker, Empty, Input, Row as GridRow, Segmented, Space,
  Spin, Statistic, Tag, Tooltip, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { api } from '../api/client'
import { downloadDocument } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'
import { Table } from '../lib/smartTable'
import { brand, status as tone } from '../theme/tokens'

/**
 * Phase 20a — the Surface Shield daily log. ONE master view, linked from the
 * Execution page, the SME Estimator and the Executive Summary (ruling Q20-2).
 *
 * Built for someone who has never opened the SME portal: per DAY, every JOB
 * (one equipment, one system) with what was drawn, what the field WROTE in the
 * Excel consumption log (the Remarks, where the SQM comes from — shown exactly
 * as typed, ruling Q20-3), the SQM filed, and the HOD's decision in plain words.
 * Read-only; `services/sme_history.py` holds the method.
 */

type Status = 'approved' | 'pending' | 'rejected' | 'not_filed'

interface Material {
  sap: string; material_code?: string | null; description?: string | null
  pack_qty: number | null; pack_uom?: string | null
  base_qty: number | null; base_uom?: string | null
  variance_pct: number | null; high_priority: boolean
  lot?: string | null; remark?: string | null
}
interface Drawn { base: Record<string, number>; packs: Record<string, number> }
interface Job {
  key: string; group_id: number | null; kind: 'lining' | 'prep'; status: Status
  edited_in_excel: boolean; site_id: string; work_date: string; tag: string | null
  code: string | null; work_area: string | null; surface_state: string | null
  sqm: number | null; sqm_from_remark: number | null; sqm_differs: boolean
  original_sqm: number | null; hod_justification: string | null
  excel_remarks: string[]; job_note: string | null
  submitted_by: string | null; submitted_at: string | null
  decided_by: string | null; decided_at: string | null; rejected_reason: string | null
  high_priority: boolean; materials: Material[]; drawn: Drawn
}
interface Day { date: string; jobs: Job[]; job_count: number; sqm_approved: number; sqm_pending: number; drawn: Drawn }
interface Kpis {
  sqm_approved: number; sqm_pending: number; sqm_rejected: number; garnet_sqm_approved: number
  jobs: Record<Status, number>; edited_in_excel: number; high_priority_pending: number
  remark_differs: number; drawn: Drawn
}
interface LogResp { date_from: string; date_to: string; kpis: Kpis | null; days: Day[] }

export const STATUS_META: Record<Status, { color: string; label: string; help: string }> = {
  approved: { color: 'green', label: 'Approved', help: 'The HOD approved this job; its SQM counts.' },
  pending: { color: 'gold', label: 'Pending HOD', help: 'Filed, and waiting for the HOD.' },
  rejected: { color: 'red', label: 'Rejected', help: 'The HOD sent it back; it is in the field queue to correct.' },
  not_filed: { color: 'default', label: 'Not yet filed', help: 'Drawn, but nobody has stated the area yet.' },
}

const num = (v: number | null | undefined, dp = 2) =>
  v == null ? '—' : Number(v).toLocaleString(undefined, { maximumFractionDigits: dp })
const units = (d: Record<string, number>) =>
  Object.entries(d).map(([u, q]) => `${num(q)} ${u}`).join(' · ') || '—'

export default function SurfaceShieldLogPage() {
  const { message } = App.useApp()
  const { user } = useAuth()
  const global = (user?.level ?? 0) >= 3 || user?.role === 'admin'
  // ?date_from=&date_to= opens the log on a given period — the Executive
  // Summary links here with its own range. Default: the last 30 days (Q20-4).
  const [sp] = useSearchParams()
  const [range, setRange] = useState<[Dayjs, Dayjs]>(() => {
    const a = dayjs(sp.get('date_from') ?? '', 'YYYY-MM-DD', true)
    const b = dayjs(sp.get('date_to') ?? '', 'YYYY-MM-DD', true)
    return a.isValid() && b.isValid() && !a.isAfter(b) ? [a, b] : [dayjs().subtract(29, 'day'), dayjs()]
  })
  const [status, setStatus] = useState<Status | 'edited' | 'all'>('all')
  const [kind, setKind] = useState<'all' | 'lining' | 'prep'>('all')
  const [tag, setTag] = useState('')
  const [code, setCode] = useState('')
  const [site, setSite] = useState('')
  const [busy, setBusy] = useState<'xlsx' | 'pdf' | null>(null)

  const params = useMemo(() => {
    const p: Record<string, string> = {
      date_from: range[0].format('YYYY-MM-DD'), date_to: range[1].format('YYYY-MM-DD'),
    }
    if (status !== 'all') p.status = status
    if (kind !== 'all') p.kind = kind
    if (tag.trim()) p.tag = tag.trim()
    if (code.trim()) p.code = code.trim()
    if (global && site.trim()) p.site_id = site.trim()
    return p
  }, [range, status, kind, tag, code, site, global])

  const { data, isFetching, error } = useQuery({
    queryKey: ['/execution/sme-link/history', params],
    queryFn: async () => (await api.get<LogResp>('/execution/sme-link/history', { params })).data,
  })

  const exportAs = async (format: 'xlsx' | 'pdf') => {
    setBusy(format)
    try {
      await downloadDocument('/execution/sme-link/history/export', { ...params, format },
        `surface_shield_log.${format}`)
    } catch {
      message.error('Could not export the log')
    } finally {
      setBusy(null)
    }
  }

  const k = data?.kpis
  const columns: ColumnsType<Job> = [
    {
      title: 'Status', key: 'status', width: 150,
      render: (_: unknown, j: Job) => (
        <Space orientation="vertical" size={2}>
          <Tooltip title={STATUS_META[j.status].help}>
            <Tag color={STATUS_META[j.status].color} data-testid={`log-status-${j.status}`}
              style={{ fontWeight: 600 }}>{STATUS_META[j.status].label}</Tag>
          </Tooltip>
          {j.edited_in_excel && (
            <Tooltip title="The Excel line changed after approval. The approved figures still count until the HOD decides the edit.">
              <Tag color="blue">edited in Excel</Tag>
            </Tooltip>
          )}
          {j.high_priority && j.status !== 'approved' && (
            <Tooltip title="A material was drawn more than 10 % off the recipe for this area.">
              <Tag color="volcano">high variance</Tag>
            </Tooltip>
          )}
        </Space>
      ),
    },
    {
      title: 'Job', key: 'job', width: 220,
      render: (_: unknown, j: Job) => (
        <div>
          <Typography.Text strong>{j.tag ?? '—'}</Typography.Text>
          <div style={{ fontSize: 12, opacity: 0.8 }}>
            {j.code ?? 'no system yet'}{j.work_area ? ` · ${j.work_area}` : ''}
            {j.surface_state ? ` · ${j.surface_state} surface` : ''}
          </div>
        </div>
      ),
    },
    {
      // ⚠️ VERBATIM, Excel first (ruling Q20-3) — the SQM comes from these words.
      title: 'Remarks (Excel consumption log)', key: 'remarks', width: 340,
      render: (_: unknown, j: Job) => (
        <div data-testid="log-remarks">
          {j.excel_remarks.length ? j.excel_remarks.map((r) => (
            <div key={r} style={{ fontStyle: 'italic' }}>“{r}”</div>
          )) : <Typography.Text type="secondary">no remark in the log</Typography.Text>}
          {j.job_note && (
            <div style={{ fontSize: 12, marginTop: 2 }}>
              <Typography.Text type="secondary">Job note: </Typography.Text>{j.job_note}
            </div>
          )}
        </div>
      ),
    },
    {
      title: 'SQM done', key: 'sqm', width: 160, align: 'right',
      render: (_: unknown, j: Job) => (
        <div>
          <Typography.Text strong>{j.sqm == null ? '—' : `${num(j.sqm)} m²`}</Typography.Text>
          {j.sqm == null && j.sqm_from_remark != null && (
            <div style={{ fontSize: 12, opacity: 0.8 }}>remark says {num(j.sqm_from_remark)} m²</div>
          )}
          {j.sqm_differs && (
            <Tooltip title={j.hod_justification
              ? `HOD's reason: ${j.hod_justification}` : 'The filed SQM differs from the remark.'}>
              <div data-testid="log-sqm-differs" style={{ fontSize: 12, color: brand.goldDeep, cursor: 'help' }}>
                ⚠ remark says {num(j.sqm_from_remark)} m²
              </div>
            </Tooltip>
          )}
        </div>
      ),
    },
    {
      title: 'Drawn', key: 'drawn', width: 170, align: 'right',
      render: (_: unknown, j: Job) => (
        <div>
          <div>{units(j.drawn.packs)} <Typography.Text type="secondary" style={{ fontSize: 11 }}>packs</Typography.Text></div>
          <div style={{ fontSize: 12, opacity: 0.8 }}>{units(j.drawn.base)}</div>
        </div>
      ),
    },
    {
      title: 'Decision', key: 'decision', width: 230,
      render: (_: unknown, j: Job) => (
        <div style={{ fontSize: 12 }}>
          {j.decided_by && <div>{j.status === 'rejected' ? 'Rejected' : 'Approved'} by <b>{j.decided_by}</b>
            {j.decided_at ? `, ${dayjs(j.decided_at).format('DD MMM HH:mm')}` : ''}</div>}
          {j.status === 'pending' && j.submitted_by && <div>Filed by {j.submitted_by}
            {j.submitted_at ? `, ${dayjs(j.submitted_at).format('DD MMM HH:mm')}` : ''}</div>}
          {j.rejected_reason && <div style={{ color: tone.critical }}>“{j.rejected_reason}”</div>}
          {j.hod_justification && j.status === 'approved' && (
            <div>HOD changed {num(j.original_sqm)} → {num(j.sqm)} m²: “{j.hod_justification}”</div>
          )}
        </div>
      ),
    },
  ]

  const materialTable = (j: Job) => (
    <Table<Material> size="small" pagination={false} rowKey={(m, i) => `${m.sap}-${i}`}
      dataSource={j.materials}
      columns={[
        { title: 'SAP', dataIndex: 'sap', width: 90 },
        { title: 'Material', dataIndex: 'description', ellipsis: true },
        { title: 'Packs', key: 'p', align: 'right', width: 110,
          render: (_: unknown, m: Material) => `${num(m.pack_qty)} ${m.pack_uom ?? ''}` },
        { title: 'Base', key: 'b', align: 'right', width: 110,
          render: (_: unknown, m: Material) => `${num(m.base_qty)} ${m.base_uom ?? ''}` },
        { title: 'Variance', dataIndex: 'variance_pct', align: 'right', width: 90,
          render: (v: number | null, m: Material) => v == null ? '—'
            : <span style={{ color: m.high_priority ? tone.critical : undefined }}>{v > 0 ? '+' : ''}{num(v, 1)} %</span> },
        { title: 'Lot', dataIndex: 'lot', width: 110, render: (v) => v ?? '—' },
      ]} />
  )

  const dayItems = (data?.days ?? []).map((d) => {
    const lining = d.jobs.filter((j) => j.kind === 'lining')
    const prep = d.jobs.filter((j) => j.kind === 'prep')
    return {
      key: d.date,
      label: (
        <Space wrap size={12} data-testid={`log-day-${d.date}`}>
          <Typography.Text strong>{dayjs(d.date).format('ddd DD MMM YYYY')}</Typography.Text>
          <Typography.Text type="secondary">{d.job_count} job{d.job_count === 1 ? '' : 's'}</Typography.Text>
          <Tag color="green">{num(d.sqm_approved)} m² approved</Tag>
          {d.sqm_pending > 0 && <Tag color="gold">{num(d.sqm_pending)} m² pending</Tag>}
          <Typography.Text type="secondary">drawn {units(d.drawn.base)}</Typography.Text>
        </Space>
      ),
      children: (
        <div>
          {lining.length > 0 && (
            <Table<Job> size="small" rowKey="key" pagination={false} columns={columns}
              dataSource={lining} scroll={{ x: 'max-content' }}
              expandable={{ expandedRowRender: materialTable }} />
          )}
          {prep.length > 0 && (
            <div style={{ marginTop: 12 }}>
              {/* Garnet is surface PREPARATION (Phase 15d): its own section — its
                  blasted area is benchmark-only and is never lining SQM (Q20-6). */}
              <Typography.Text strong>Garnet — surface preparation</Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                blasted area, not counted as lining progress
              </Typography.Text>
              <Table<Job> size="small" rowKey="key" pagination={false} columns={columns}
                dataSource={prep} scroll={{ x: 'max-content' }}
                expandable={{ expandedRowRender: materialTable }} />
            </div>
          )}
        </div>
      ),
    }
  })

  return (
    <div data-testid="ss-log">
      <Typography.Title level={4} style={{ marginTop: 0 }}>Surface Shield daily log</Typography.Title>
      <Typography.Paragraph type="secondary">
        Every day&apos;s Surface Shield work: what was drawn, what the field wrote in the Excel
        consumption log (the remarks the SQM comes from, exactly as typed), the area done, and the
        HOD&apos;s decision. Packs and kilograms are shown together. Read-only.
      </Typography.Paragraph>

      <Card size="small" style={{ marginBottom: 12 }}>
        <Space wrap>
          <DatePicker.RangePicker value={range} allowClear={false}
            onChange={(v) => v && v[0] && v[1] && setRange([v[0], v[1]])}
            presets={[
              { label: 'Last 7 days', value: [dayjs().subtract(6, 'day'), dayjs()] },
              { label: 'Last 30 days', value: [dayjs().subtract(29, 'day'), dayjs()] },
              { label: 'This month', value: [dayjs().startOf('month'), dayjs()] },
              { label: 'Last month', value: [dayjs().subtract(1, 'month').startOf('month'),
                dayjs().subtract(1, 'month').endOf('month')] },
            ]} />
          <Segmented value={status} onChange={(v) => setStatus(v as typeof status)} data-testid="log-status-filter"
            options={[
              { value: 'all', label: 'All' },
              { value: 'approved', label: `Approved (${k?.jobs.approved ?? 0})` },
              { value: 'pending', label: `Pending (${k?.jobs.pending ?? 0})` },
              { value: 'rejected', label: `Rejected (${k?.jobs.rejected ?? 0})` },
              { value: 'not_filed', label: `Not filed (${k?.jobs.not_filed ?? 0})` },
              { value: 'edited', label: 'Edited in Excel' },
            ]} />
          <Segmented value={kind} onChange={(v) => setKind(v as typeof kind)}
            options={[{ value: 'all', label: 'Lining + Garnet' }, { value: 'lining', label: 'Lining' },
              { value: 'prep', label: 'Garnet' }]} />
          <Input.Search allowClear placeholder="Equipment tag" style={{ width: 170 }}
            onSearch={setTag} onChange={(e) => !e.target.value && setTag('')} />
          <Input.Search allowClear placeholder="System code" style={{ width: 140 }}
            onSearch={setCode} onChange={(e) => !e.target.value && setCode('')} />
          {global && (
            <Input.Search allowClear placeholder="Site (all)" style={{ width: 130 }}
              onSearch={setSite} onChange={(e) => !e.target.value && setSite('')} />
          )}
          <Button loading={busy === 'xlsx'} onClick={() => exportAs('xlsx')} data-testid="log-export-xlsx">Excel</Button>
          <Button loading={busy === 'pdf'} onClick={() => exportAs('pdf')} data-testid="log-export-pdf">PDF</Button>
        </Space>
      </Card>

      {k && (
        <GridRow gutter={[12, 12]} style={{ marginBottom: 12 }} data-testid="log-kpis">
          <Col xs={12} md={6}><Card size="small"><Statistic title="SQM approved (lining)" value={k.sqm_approved} precision={2} suffix="m²" /></Card></Col>
          <Col xs={12} md={6}><Card size="small"><Statistic title="SQM pending HOD" value={k.sqm_pending} precision={2} suffix="m²" /></Card></Col>
          <Col xs={12} md={6}><Card size="small"><Statistic title="Jobs not yet filed" value={k.jobs.not_filed} /></Card></Col>
          <Col xs={12} md={6}><Card size="small"><Statistic title="Surface Shield drawn" value={units(k.drawn.base)} /></Card></Col>
          {(k.high_priority_pending > 0 || k.remark_differs > 0 || k.sqm_rejected > 0 || k.garnet_sqm_approved > 0) && (
            <Col span={24}>
              <Space wrap>
                {k.sqm_rejected > 0 && <Tag color="red">{num(k.sqm_rejected)} m² rejected — back with the field</Tag>}
                {k.high_priority_pending > 0 && <Tag color="volcano">{k.high_priority_pending} pending job(s) with a high variance</Tag>}
                {k.remark_differs > 0 && <Tag color="gold">{k.remark_differs} job(s) where the filed SQM differs from the remark</Tag>}
                {k.garnet_sqm_approved > 0 && <Tag color="purple">{num(k.garnet_sqm_approved)} m² blasted (Garnet, not lining)</Tag>}
              </Space>
            </Col>
          )}
        </GridRow>
      )}

      {error ? <Empty description="Could not load the log" /> : isFetching && !data ? <Spin /> : dayItems.length === 0
        ? <Empty description="No Surface Shield work in this period" />
        : <Collapse items={dayItems} defaultActiveKey={dayItems.slice(0, 3).map((d) => d.key)} />}
    </div>
  )
}
