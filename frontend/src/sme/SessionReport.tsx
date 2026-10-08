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
import { Alert, App, Button, Card, Col, Collapse, Progress, Row, Skeleton, Space, Tag } from 'antd'
import { Table } from '../lib/smartTable'
import { FileExcelOutlined, FilePdfOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { Bar, BarChart, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { postDownloadDocument, useSmeSnapshot } from '../api/hooks'
import { buildModel, runPlan } from './engine'
import { fc } from './insights'
import KpiDrill from './KpiDrill'
import PriorityList, { FulfilPill, StatusDot } from './PriorityList'
import { useScenario } from './ScenarioContext'
import { scopeBottleneckCoverage, tagStats, weightedProcurement } from './session'
import type { WeightedProcurementRow } from './session'
import type { SqmCodeRow } from './engine'
import SuggestionPanel from './SuggestionPanel'
import TagDetail from './TagDetail'
import TierNote from './TierNote'
import { materialCodeCol, materialNameCol } from './materialCols'
import { status } from '../theme/tokens'

const mono: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }
const secHdr: React.CSSProperties = {
  ...mono, fontSize: '0.68rem', fontWeight: 700, letterSpacing: '.13em',
  textTransform: 'uppercase', opacity: 0.65,
}
const nf = (v: number, d = 3) =>
  v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: d })

/** The gold totals bar that closes each report card. */
function SummaryStrip({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      border: '1px solid rgba(212,175,55,.45)', background: 'rgba(212,175,55,.07)',
      borderRadius: 8, padding: '8px 12px', display: 'flex', gap: 20, flexWrap: 'wrap',
      alignItems: 'center', fontSize: '0.78rem', marginTop: 10,
    }}>{children}</div>
  )
}

/** Shared download driver — the server oracle renders every official document,
 *  so both button groups POST the same current priority order. */
function useSessionDownload(order: string[], siteId?: string) {
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
  return { busy, dl }
}

/** Combined-procurement downloads. Same `session-full` document as the header
 *  buttons — it now LEADS with the aggregated material demand (Excel: a
 *  "Total Material Demand" first sheet; PDF: a "Material-Wise Summary" on the
 *  first pages), which is exactly the table shown in this card. */
function ProcurementExportButtons({ order, siteId }: { order: string[]; siteId?: string }) {
  const { busy, dl } = useSessionDownload(order, siteId)
  return (
    <Space wrap>
      <Button size="small" icon={<FilePdfOutlined />} loading={busy === 'session-full.pdf'}
        onClick={() => dl('session-full', 'pdf')}>Download PDF</Button>
      <Button size="small" icon={<FileExcelOutlined />} loading={busy === 'session-full.xlsx'}
        onClick={() => dl('session-full', 'xlsx')}>Download Excel</Button>
    </Space>
  )
}

/** Material-Wise Segregated Report downloads — the SQM-by-system-code rollup,
 *  the blocking materials behind it, and the per-equipment detail. */
function SegregatedExportButtons({ order, siteId }: { order: string[]; siteId?: string }) {
  const { busy, dl } = useSessionDownload(order, siteId)
  return (
    <Space wrap>
      <Button size="small" icon={<FilePdfOutlined />} loading={busy === 'segregated.pdf'}
        onClick={() => dl('segregated', 'pdf')}>Download PDF</Button>
      <Button size="small" icon={<FileExcelOutlined />} loading={busy === 'segregated.xlsx'}
        onClick={() => dl('segregated', 'xlsx')}>Download Excel</Button>
    </Space>
  )
}

function ExportButtons({ order, siteId }: { order: string[]; siteId?: string }) {
  const { busy, dl } = useSessionDownload(order, siteId)
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
  const totAvail = combined.reduce((s, r) => s + r.Available_Qty, 0)
  const totOrdered = combined.reduce((s, r) => s + r.Pending_Delivery_Qty, 0)
  const totShort = combined.reduce((s, r) => s + r.Shortfall_Qty, 0)
  const shortOnly = combined.filter((r) => r.Shortfall_Qty > 0)
  const sessionTags = scenario.order.filter((t) => stats.has(t))

  // 2026-08-03 STRICT TIER SEGREGATION: this was Σ Allocated_Qty ÷ Σ Demand —
  // physical stock PLUS stock on order — so a session whose whole gap sat on an
  // open PO headlined 100%.
  //
  // 2026-08-04 AREA-WEIGHTED BOTTLENECK: it was then still a QUANTITY average
  // (Σ Available ÷ Σ Demand) across unlike materials, which reads 50% when you
  // hold all of Part A and none of Part B — and can therefore sit well above
  // the scarcest component, the very thing the strict-bottleneck ruling
  // forbids one level down. Coverage is now Σ buildable m² ÷ Σ remaining m²
  // over the session's tags, each already capped by its own bottleneck.
  const scope = scopeBottleneckCoverage(sessionTags.map((t) => stats.get(t)!))
  const cov = scope.coveragePct
  const covOrd = scope.coverageWithOrderedPct

  const combinedCols: ColumnsType<WeightedProcurementRow> = [
    materialCodeCol<WeightedProcurementRow>({ width: 150, fixed: 'left' }),
    materialNameCol<WeightedProcurementRow>({ title: 'Name', width: 240 }),
    { title: 'UOM', dataIndex: 'UOM', key: 'u', width: 60 },
    { title: 'Demand', dataIndex: 'Demand_Qty', key: 'd', align: 'right', render: (v: number) => nf(v) },
    {
      title: 'Available', dataIndex: 'Available_Qty', key: 'av', align: 'right',
      render: (v: number) => <span style={{ color: status.ok }}>{nf(v)}</span>,
    },
    {
      title: 'Pending Delivery', dataIndex: 'Pending_Delivery_Qty', key: 'or', align: 'right',
      render: (v: number) => (
        <span style={{ opacity: v > 0 ? 1 : 0.4, color: v > 0 ? status.low : undefined }}>{nf(v)}</span>
      ),
    },
    {
      title: 'To Order', dataIndex: 'Shortfall_Qty', key: 's', align: 'right',
      render: (v: number) => <span style={{ color: v > 0 ? status.critical : undefined, fontWeight: v > 0 ? 700 : 400 }}>{nf(v)}</span>,
    },
    // Three columns repeating "SQM" cost more width than the numbers do; one
    // group header says it once for all of them.
    {
      title: 'Area (m²)',
      children: [
        { title: 'Total', dataIndex: 'SQM_Total', key: 'st', align: 'right', render: (v: number) => nf(v, 1) },
        { title: 'Done', dataIndex: 'SQM_Done', key: 'sd', align: 'right', render: (v: number) => nf(v, 1) },
        {
          title: 'Deficit', dataIndex: 'SQM_Deficit', key: 'sx', align: 'right',
          render: (v: number) => <span style={{ color: v > 0 ? status.critical : undefined }}>{nf(v, 1)}</span>,
        },
      ],
    },
    {
      title: 'Ready now', dataIndex: 'Fulfillment_Pct', key: 'f', align: 'right', width: 100,
      render: (v: number) => <span style={{ color: fc(v), fontWeight: 700 }}>{v.toFixed(1)}%</span>,
    },
  ]

  const sqmTot = plan.sqm_by_code.reduce(
    (a, c) => ({
      rem: a.rem + c.Remaining_SQM, now: a.now + c.SQM_Achievable_Now,
      ord: a.ord + c.SQM_Achievable_With_Ordered, def: a.def + c.SQM_Deficit,
    }), { rem: 0, now: 0, ord: 0, def: 0 })

  const segregatedCols: ColumnsType<SqmCodeRow> = [
    {
      title: 'System', dataIndex: 'Lining_System_Code', key: 'c', width: 110, fixed: 'left',
      render: (v: string, r) => (
        <Space size={4}>
          <b style={mono}>{v}</b>
          {!r.Blocking_Materials.length && r.SQM_Deficit > 0 && <Tag color="default">no recipe</Tag>}
        </Space>
      ),
    },
    { title: 'Name', dataIndex: 'System_Name', key: 'n', ellipsis: true },
    { title: 'Equip.', dataIndex: 'Equipment_Count', key: 'e', width: 70, align: 'right' },
    { title: 'Remaining m²', dataIndex: 'Remaining_SQM', key: 'r', align: 'right', render: (v: number) => nf(v, 1) },
    {
      title: 'Achievable now', dataIndex: 'SQM_Achievable_Now', key: 'an', align: 'right',
      render: (v: number) => <span style={{ color: status.ok, fontWeight: 700 }}>{nf(v, 1)}</span>,
    },
    {
      title: 'When delivered', dataIndex: 'SQM_Achievable_With_Ordered', key: 'ao', align: 'right',
      render: (v: number, r) => (
        <span style={{ color: v > r.SQM_Achievable_Now ? status.low : undefined, opacity: v > r.SQM_Achievable_Now ? 1 : 0.5 }}>
          {nf(v, 1)}
        </span>
      ),
    },
    {
      title: 'Deficit m²', dataIndex: 'SQM_Deficit', key: 'd', align: 'right',
      render: (v: number) => (
        <span style={{ color: v > 0 ? status.critical : undefined, fontWeight: v > 0 ? 700 : 400 }}>{nf(v, 1)}</span>
      ),
    },
    {
      title: 'Coverage now', dataIndex: 'Coverage_Now_Pct', key: 'p', width: 130, align: 'right',
      render: (v: number) => (
        <Progress percent={v} size="small" strokeColor={fc(v)}
          format={(pc) => <span style={{ ...mono, fontSize: '0.7rem' }}>{(pc ?? 0).toFixed(1)}%</span>} />
      ),
    },
    {
      title: 'When delivered', dataIndex: 'Coverage_With_Ordered_Pct', key: 'pw',
      width: 110, align: 'right',
      render: (v: number, r) => (
        <span style={{ ...mono, fontSize: '0.72rem',
          color: v > r.Coverage_Now_Pct ? status.low : undefined,
          opacity: v > r.Coverage_Now_Pct ? 1 : 0.5 }}>{v.toFixed(1)}%</span>
      ),
    },
  ]

  return (
    <div>
      <TierNote style={{ marginBottom: 12 }} />
      {/* KPI strip */}
      <Row gutter={[12, 12]}>
        <Col flex="1 1 160px"><KpiDrill title="Equipment" value={String(sessionTags.length)}
          drillTitle="Session Feasibility" rows={plan.feasibility.map((f) => ({
            '#': f.Priority_Rank, Tag: f.Equipment_Tag_No, Name: f.Name,
            'Completion %': f.Completion_Pct, Status: f.Status,
            // The bottleneck is one PHYSICAL drum, so it needs its variant SAP:
            // "GI-8005765" alone names four components of a multi-part system.
            Bottleneck: f.Bottleneck_Material_Code, 'Bottleneck SAP': f.Bottleneck_SAP_Code,
          }))} help="Equipment in the session, in priority order." /></Col>
        <Col flex="1 1 160px"><KpiDrill title="Materials" value={String(combined.length)}
          drillTitle="Session Materials" rows={combined.map((r) => ({
            Material: r.Material_Code, SAP: r.SAP_Code, Name: r.Material_Name,
            Demand: r.Demand_Qty, Available: r.Available_Qty,
            'Pending Delivery': r.Pending_Delivery_Qty,
            // THE SUBSET RULE (1c): Total Procured is the CEILING —
            // max(available, ordered) — not a third bucket beside the other
            // two. It is taken from the engine, never re-derived here as
            // available + pending, because that is exactly the additive
            // reading that understated the buy list by 22,951 units.
            'Total Procured': r.Total_Procured_Qty,
            'Ready now %': r.Fulfillment_Pct,
          }))} help="Material components demanded by the session — one row per physical drum (Material_Code + variant SAP). Total Procured is the whole quantity bought for the project; Available is the part of it that has arrived." /></Col>
        <Col flex="1 1 160px"><KpiDrill title="Need to Order" value={String(shortOnly.length)}
          accent={shortOnly.length > 0 ? status.critical : status.ok}
          drillTitle="Order List (net shortfall > 0)" rows={shortOnly.map((r) => ({
            Material: r.Material_Code, SAP: r.SAP_Code, Name: r.Material_Name,
            Available: r.Available_Qty, 'Pending Delivery': r.Pending_Delivery_Qty,
            'Total Procured': r.Total_Procured_Qty,
            'To Order': r.Shortfall_Qty, 'Ready now %': r.Fulfillment_Pct,
          }))} help="Components still to procure once the ENTIRE purchase order has landed. To Order = demand − Total Procured, so nothing already bought is bought twice." /></Col>
        {/* Area-weighted bottleneck over the session's tags — drill down per
            EQUIPMENT, because that is the grain the number is computed at. */}
        <Col flex="1 1 160px"><KpiDrill title="Coverage now" value={`${cov.toFixed(1)}%`}
          accent={fc(cov)} drillTitle="Coverage by Equipment — physical vs when delivered"
          rows={sessionTags.map((t) => {
            const st = stats.get(t)!
            return {
              Tag: t, Name: st.name, 'Remaining m²': st.sqm,
              'Buildable now m²': st.canSqm, 'Ready now %': st.fulfillPct,
              'When delivered %': st.fulfillWithOrderedPct,
            }
          })} help={`Buildable m² ÷ remaining m² (${scope.canSqm} / ${scope.sqm}), each unit capped by its SCARCEST component — never a quantity average across materials. PHYSICAL stock only; against the total procured quantity it would be ${covOrd.toFixed(1)}%.`} /></Col>
        <Col flex="1 1 160px"><KpiDrill title="When delivered" value={`${covOrd.toFixed(1)}%`}
          accent={status.low} drillTitle="Components covered only by an open PO"
          rows={combined.filter((r) => r.Pending_Delivery_Qty > 0).map((r) => ({
            Material: r.Material_Code, SAP: r.SAP_Code, Name: r.Material_Name,
            Available: r.Available_Qty, 'Pending Delivery': r.Pending_Delivery_Qty,
            'Total Procured': r.Total_Procured_Qty,
            'Ready now %': r.Fulfillment_Pct,
          }))} help="Coverage against the TOTAL procured quantity, i.e. once the whole order has landed. Forecast only — never a readiness figure." /></Col>
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
                <span style={{
                  fontSize: '0.75rem', opacity: 0.7, maxWidth: 320,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }} title={st.name}>{st.name}</span>
                <FulfilPill pct={st.fulfillPct} />
              </Space>
            ),
            children: <TagDetail lines={plan.lines.filter((l) => l.Equipment_Tag_No === tag)} stat={st} />,
          }
        })} />
      </Card>

      {/* Shortage stacked bar + combined procurement */}
      <Card size="small" style={{ marginTop: 16 }}
        title={<span style={secHdr}>🛒 Combined procurement (SQM-weighted)</span>}
        extra={<ProcurementExportButtons order={scenario.order} siteId={siteId} />}>
        {shortOnly.length > 0 && (
          <ResponsiveContainer width="100%" height={Math.max(120, shortOnly.length * 30 + 60)}>
            <BarChart data={shortOnly} layout="vertical">
              <XAxis type="number" tick={{ fontSize: 10 }} />
              {/* The variant SAP, not the name, is what tells the four drums of a
                  multi-part system apart — and the truncated name used to make
                  them four identical bars. */}
              <YAxis type="category" width={170} tick={{ fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}
                dataKey={(r: WeightedProcurementRow) => (r.SAP_Code ? `${r.Material_Code} · ${r.SAP_Code}` : r.Material_Code)} />
              <Tooltip formatter={(v) => nf(Number(v))} />
              <Legend wrapperStyle={{ fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }} />
              {/* Split the allocated segment the way the table below splits it.
                  The amber bar is the PENDING part of the order (2026-08-05
                  subset rule) — stacking the RAW order on top of `Available`
                  would draw a fully-delivered material at twice its size. */}
              <Bar dataKey="Available_Qty" name="Available" stackId="p" fill={status.ok} fillOpacity={0.8} />
              <Bar dataKey="Pending_Delivery_Qty" name="Pending Delivery" stackId="p" fill={status.low} fillOpacity={0.8} />
              <Bar dataKey="Shortfall_Qty" name="To Order" stackId="p" fill={status.critical} fillOpacity={0.8} />
            </BarChart>
          </ResponsiveContainer>
        )}
        <Table<WeightedProcurementRow> sticky={{ offsetHeader: 64 }} size="small" rowKey="Material_Key" columns={combinedCols}
          dataSource={combined} pagination={{ pageSize: 15, showTotal: (t) => `${t} materials` }}
          scroll={{ x: 'max-content' }} style={{ marginTop: 8 }} />
        <SummaryStrip>
          <span>Equipment: <b style={mono}>{sessionTags.length}</b></span>
          <span>Components: <b style={mono}>{combined.length}</b></span>
          <span>Total demand: <b style={mono}>{nf(totDemand)}</b></span>
          <span>Available: <b style={{ ...mono, color: status.ok }}>{nf(totAvail)}</b></span>
          <span>On order: <b style={{ ...mono, color: totOrdered > 0 ? status.low : undefined }}>{nf(totOrdered)}</b></span>
          <span>To procure: <b style={{ ...mono, color: totShort > 0 ? status.critical : undefined }}>{nf(totShort)}</b></span>
          <span style={{ marginLeft: 'auto' }}>
            Ready now: <FulfilPill pct={cov} />
            {covOrd > cov && (
              <span style={{ ...mono, fontSize: '0.7rem', color: status.low, marginLeft: 6 }}>
                (when delivered {covOrd.toFixed(1)}%)
              </span>
            )}
          </span>
        </SummaryStrip>
      </Card>

      {/* Material-Wise Segregated Report — reverse SQM per system code */}
      <Card size="small" style={{ marginTop: 16 }}
        title={<span style={secHdr}>📐 Material-wise segregated report (by system code)</span>}
        extra={<SegregatedExportButtons order={scenario.order} siteId={siteId} />}>
        <Alert type="info" showIcon style={{ marginBottom: 10, fontSize: '0.76rem' }}
          message="Achievable SQM is limited by the scarcest material in each system's recipe — the bottleneck, not the average."
          description={'"Now" counts stock that has ARRIVED. "When delivered" measures against the TOTAL procured quantity — available plus the pending part of the order, which is the whole PO, not the order on top of the stock. Deficit is measured against arrived stock, because that is what procurement has to close.'} />
        <Table<SqmCodeRow> size="small" rowKey="Lining_System_Code"
          columns={segregatedCols} dataSource={plan.sqm_by_code}
          pagination={{ pageSize: 10, showTotal: (t) => `${t} system codes` }}
          scroll={{ x: 'max-content' }}
          expandable={{
            rowExpandable: (r) => r.Blocking_Materials.length > 0,
            expandedRowRender: (r) => (
              <Table size="small" rowKey={(r) => `${r.Material_Code}|${r.SAP_Code ?? ''}`}
                pagination={false}
                dataSource={r.Blocking_Materials}
                columns={[
                  materialCodeCol({ width: 150 }),
                  materialNameCol({ title: 'Name', width: 220 }),
                  { title: 'UOM', dataIndex: 'UOM', key: 'u', width: 60 },
                  { title: 'Demand', dataIndex: 'Demand_Qty', key: 'd', align: 'right', render: (v: number) => nf(v) },
                  {
                    title: 'Available', dataIndex: 'Alloc_Available', key: 'av', align: 'right',
                    render: (v: number) => <span style={{ color: status.ok }}>{nf(v)}</span>,
                  },
                  {
                    title: 'Pending Delivery', dataIndex: 'Alloc_Pending', key: 'or', align: 'right',
                    render: (v: number) => <span style={{ color: v > 0 ? status.low : undefined, opacity: v > 0 ? 1 : 0.4 }}>{nf(v)}</span>,
                  },
                  {
                    title: 'Still to buy', dataIndex: 'Shortfall_Qty', key: 's', align: 'right',
                    render: (v: number) => (
                      <span style={{ color: v > 0 ? status.critical : status.ok, fontWeight: v > 0 ? 700 : 400 }}>{nf(v)}</span>
                    ),
                  },
                ]} />
            ),
          }} />
        <SummaryStrip>
          <span>Remaining: <b style={mono}>{nf(sqmTot.rem, 1)}</b> m²</span>
          <span>Achievable now: <b style={{ ...mono, color: status.ok }}>{nf(sqmTot.now, 1)}</b> m²</span>
          <span>When delivered: <b style={{ ...mono, color: status.low }}>{nf(sqmTot.ord, 1)}</b> m²</span>
          <span>Deficit: <b style={{ ...mono, color: sqmTot.def > 0 ? status.critical : undefined }}>{nf(sqmTot.def, 1)}</b> m²</span>
        </SummaryStrip>
      </Card>

      {/* Smart suggestions (client-side simulation loop) */}
      <SuggestionPanel model={model} order={scenario.order} onPause={scenario.removeTag} />
    </div>
  )
}
