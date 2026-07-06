/**
 * frontend/src/sme/SessionReport.tsx — 📦 Session Order Report (Phase S3).
 * React rebuild of legacy Tab 2: KPI drill-downs, a second drag-priority list
 * over the SAME shared scenario, per-equipment expanders with per-code
 * detail, the shortage-only stacked bar, the SQM-weighted combined
 * procurement list, and the smart suggestion panel — all recomputed
 * client-side per reorder. Official exports POST the current priority order
 * to /sme/plan/export so the SERVER oracle renders the documents.
 */
import { useMemo, useState } from 'react'
import { Alert, App, Button, Card, Col, Collapse, Row, Skeleton, Space, Table } from 'antd'
import { FileExcelOutlined, FilePdfOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { Bar, BarChart, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { postDownloadDocument, useSmeSnapshot } from '../api/hooks'
import { buildModel, runPlan } from './engine'
import { fc } from './insights'
import KpiDrill from './KpiDrill'
import PriorityList, { FulfilPill, StatusDot } from './PriorityList'
import { useScenario } from './ScenarioContext'
import { tagStats, weightedProcurement } from './session'
import type { WeightedProcurementRow } from './session'
import SuggestionPanel from './SuggestionPanel'
import TagDetail from './TagDetail'

const mono: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }
const secHdr: React.CSSProperties = {
  ...mono, fontSize: '0.68rem', fontWeight: 700, letterSpacing: '.13em',
  textTransform: 'uppercase', opacity: 0.65,
}
const nf = (v: number, d = 3) =>
  v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: d })

function ExportButtons({ order, siteId }: { order: string[]; siteId?: string }) {
  const { message } = App.useApp()
  const [busy, setBusy] = useState<string | null>(null)
  const dl = async (key: string, format: string) => {
    setBusy(`${key}.${format}`)
    try {
      await postDownloadDocument('/sme/plan/export',
        { priority_order: order, key, format, ...(siteId ? { site_id: siteId } : {}) },
        `sme-${key}.${format}`)
    } catch {
      message.error('Export failed')
    } finally {
      setBusy(null)
    }
  }
  return (
    <Space wrap>
      <Button size="small" icon={<FileExcelOutlined />} loading={busy === 'session-full.xlsx'}
        onClick={() => dl('session-full', 'xlsx')}>Excel — Full Session</Button>
      <Button size="small" icon={<FilePdfOutlined />} loading={busy === 'session-full.pdf'}
        onClick={() => dl('session-full', 'pdf')}>PDF — Full Session</Button>
      <Button size="small" icon={<FileExcelOutlined />} loading={busy === 'order-list.xlsx'}
        onClick={() => dl('order-list', 'xlsx')}>Excel — Order List</Button>
      <Button size="small" icon={<FilePdfOutlined />} loading={busy === 'order-list.pdf'}
        onClick={() => dl('order-list', 'pdf')}>PDF — Order List</Button>
    </Space>
  )
}

export default function SessionReport({ siteId }: { siteId?: string }) {
  const { data: snap, isLoading } = useSmeSnapshot(siteId)
  const scenario = useScenario()

  const model = useMemo(
    () => (snap ? buildModel(snap.equipment, snap.recipes, snap.materials, snap.progress) : null),
    [snap])
  const plan = useMemo(
    () => (model ? runPlan(model, scenario.order) : null), [model, scenario.order])
  const stats = useMemo(
    () => (model && plan ? tagStats(model, plan.lines) : new Map()), [model, plan])
  const combined = useMemo(
    () => (plan ? weightedProcurement(plan.lines) : []), [plan])

  if (isLoading) return <Skeleton active paragraph={{ rows: 8 }} />
  if (!snap || !model || !plan) return <Alert type="warning" showIcon title="SME model unavailable" />
  if (scenario.order.length === 0) {
    return <Alert type="info" showIcon title="No session yet"
      description="Build a session in the 🔍 Session Builder tab first — this report renders the session's priority-cascaded demand, procurement list and smart suggestions." />
  }

  const totDemand = combined.reduce((s, r) => s + r.Demand_Qty, 0)
  const totAlloc = combined.reduce((s, r) => s + r.Allocated_Qty, 0)
  const totShort = combined.reduce((s, r) => s + r.Shortfall_Qty, 0)
  const cov = totDemand > 0 ? Math.min(100, (totAlloc / totDemand) * 100) : 100
  const shortOnly = combined.filter((r) => r.Shortfall_Qty > 0)
  const sessionTags = scenario.order.filter((t) => stats.has(t))

  const combinedCols: ColumnsType<WeightedProcurementRow> = [
    { title: 'Material', dataIndex: 'Material_Code', key: 'c', width: 120, fixed: 'left' },
    { title: 'Name', dataIndex: 'Material_Name', key: 'n', ellipsis: true },
    { title: 'UOM', dataIndex: 'UOM', key: 'u', width: 60 },
    { title: 'Demand', dataIndex: 'Demand_Qty', key: 'd', align: 'right', render: (v: number) => nf(v) },
    { title: 'Allocated', dataIndex: 'Allocated_Qty', key: 'a', align: 'right', render: (v: number) => nf(v) },
    {
      title: 'To Order', dataIndex: 'Shortfall_Qty', key: 's', align: 'right',
      render: (v: number) => <span style={{ color: v > 0 ? '#EF4444' : undefined, fontWeight: v > 0 ? 700 : 400 }}>{nf(v)}</span>,
    },
    { title: 'SQM Total', dataIndex: 'SQM_Total', key: 'st', align: 'right', render: (v: number) => nf(v, 1) },
    { title: 'SQM Done', dataIndex: 'SQM_Done', key: 'sd', align: 'right', render: (v: number) => nf(v, 1) },
    { title: 'SQM Deficit', dataIndex: 'SQM_Deficit', key: 'sx', align: 'right', render: (v: number) => nf(v, 1) },
    {
      title: 'Coverage', dataIndex: 'Fulfillment_Pct', key: 'f', align: 'right', width: 100,
      render: (v: number) => <span style={{ color: fc(v), fontWeight: 700 }}>{v.toFixed(1)}%</span>,
    },
  ]

  return (
    <div>
      {/* KPI strip */}
      <Row gutter={[12, 12]}>
        <Col flex="1 1 160px"><KpiDrill title="Equipment" value={String(sessionTags.length)}
          drillTitle="Session Feasibility" rows={plan.feasibility.map((f) => ({
            '#': f.Priority_Rank, Tag: f.Equipment_Tag_No, Name: f.Name,
            'Completion %': f.Completion_Pct, Status: f.Status,
            Bottleneck: f.Bottleneck_Material_Code,
          }))} help="Equipment in the session, in priority order." /></Col>
        <Col flex="1 1 160px"><KpiDrill title="Materials" value={String(combined.length)}
          drillTitle="Session Materials" rows={combined.map((r) => ({
            Material: r.Material_Code, Name: r.Material_Name, Demand: r.Demand_Qty,
            Allocated: r.Allocated_Qty, 'Coverage %': r.Fulfillment_Pct,
          }))} help="Distinct materials demanded by the session." /></Col>
        <Col flex="1 1 160px"><KpiDrill title="Need to Order" value={String(shortOnly.length)}
          accent={shortOnly.length > 0 ? '#EF4444' : '#10B981'}
          drillTitle="Order List (shortfall > 0)" rows={shortOnly.map((r) => ({
            Material: r.Material_Code, Name: r.Material_Name, 'To Order': r.Shortfall_Qty,
            'Coverage %': r.Fulfillment_Pct,
          }))} help="Materials that must be procured to fully build the session." /></Col>
        <Col flex="1 1 160px"><KpiDrill title="Overall Coverage" value={`${cov.toFixed(1)}%`}
          accent={fc(cov)} drillTitle="Coverage by Material"
          rows={combined.map((r) => ({
            Material: r.Material_Code, Name: r.Material_Name, 'Coverage %': r.Fulfillment_Pct,
          }))} help="Allocated ÷ Demand across the whole session." /></Col>
      </Row>

      {/* Priority reorder (same shared scenario as the builder) */}
      <Card size="small" style={{ marginTop: 16 }}
        title={<span style={secHdr}>📋 Priority — drag to re-cascade</span>}
        extra={<ExportButtons order={scenario.order} siteId={siteId} />}>
        <PriorityList order={scenario.order} stats={stats}
          onReorder={scenario.setOrder} onMove={scenario.moveTag}
          onRemove={scenario.removeTag} />
      </Card>

      {/* Per-equipment expanders */}
      <Card size="small" style={{ marginTop: 16 }} title={<span style={secHdr}>🏗 Per-equipment breakdown</span>}>
        <Collapse size="small" items={sessionTags.map((tag) => {
          const st = stats.get(tag)!
          return {
            key: tag,
            label: (
              <Space>
                <StatusDot pct={st.fulfillPct} />
                <b style={{ ...mono, fontSize: '0.8rem' }}>{tag}</b>
                <span style={{ fontSize: '0.75rem', opacity: 0.7 }}>{st.name.slice(0, 30)}</span>
                <FulfilPill pct={st.fulfillPct} />
              </Space>
            ),
            children: <TagDetail lines={plan.lines.filter((l) => l.Equipment_Tag_No === tag)} stat={st} />,
          }
        })} />
      </Card>

      {/* Shortage stacked bar + combined procurement */}
      <Card size="small" style={{ marginTop: 16 }}
        title={<span style={secHdr}>🛒 Combined procurement (SQM-weighted)</span>}>
        {shortOnly.length > 0 && (
          <ResponsiveContainer width="100%" height={Math.max(120, shortOnly.length * 30 + 60)}>
            <BarChart data={shortOnly} layout="vertical">
              <XAxis type="number" tick={{ fontSize: 10 }} />
              <YAxis type="category" width={170} tick={{ fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}
                dataKey={(r: WeightedProcurementRow) => `${r.Material_Code} ${r.Material_Name}`.slice(0, 26)} />
              <Tooltip formatter={(v) => nf(Number(v))} />
              <Legend wrapperStyle={{ fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }} />
              <Bar dataKey="Allocated_Qty" name="Available" stackId="p" fill="#10B981" fillOpacity={0.8} />
              <Bar dataKey="Shortfall_Qty" name="To Order" stackId="p" fill="#EF4444" fillOpacity={0.8} />
            </BarChart>
          </ResponsiveContainer>
        )}
        <Table<WeightedProcurementRow> size="small" rowKey="Material_Code" columns={combinedCols}
          dataSource={combined} pagination={{ pageSize: 15, showTotal: (t) => `${t} materials` }}
          scroll={{ x: 'max-content' }} style={{ marginTop: 8 }} />
        <div style={{
          border: '1px solid rgba(212,175,55,.45)', background: 'rgba(212,175,55,.07)',
          borderRadius: 8, padding: '8px 12px', display: 'flex', gap: 20, flexWrap: 'wrap',
          alignItems: 'center', fontSize: '0.78rem', marginTop: 10,
        }}>
          <span>Equipment: <b style={mono}>{sessionTags.length}</b></span>
          <span>Materials: <b style={mono}>{combined.length}</b></span>
          <span>Total demand: <b style={mono}>{nf(totDemand)}</b></span>
          <span>To procure: <b style={{ ...mono, color: totShort > 0 ? '#EF4444' : undefined }}>{nf(totShort)}</b></span>
          <span style={{ marginLeft: 'auto' }}>Coverage: <FulfilPill pct={cov} /></span>
        </div>
      </Card>

      {/* Smart suggestions (client-side simulation loop) */}
      <SuggestionPanel model={model} order={scenario.order} onPause={scenario.removeTag} />
    </div>
  )
}
