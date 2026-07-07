/**
 * frontend/src/sme/TotalOverview.tsx — 📈 Total Overview (Phase S5).
 * React rebuild of legacy Tab 5: the master (Equipment × System Code) grid —
 * every pair cascaded in default order — with cascading Location/Type/Code
 * filters + the readiness-status filter, six KPI drill-downs, 4-tier row
 * tinting, VIRTUALIZED scrolling (antd Table `virtual`), per-code material
 * expanders, and an oracle-rendered export (POST /sme/plan/export overview).
 */
import { useMemo, useState } from 'react'
import { Alert, App, Button, Card, Col, Collapse, Row, Select, Skeleton, Space, Table } from 'antd'
import { FileExcelOutlined, FilePdfOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { postDownloadDocument, useSmeSnapshot } from '../api/hooks'
import { buildModel, runPlan, syscodeCompare, unitKey } from './engine'
import { allUnits, fc, fcBg } from './insights'
import KpiDrill from './KpiDrill'
import { ScopedExport } from './MatrixReports'
import { FulfilPill } from './PriorityList'

const mono: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }
const nf = (v: number, d = 1) =>
  v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: d })

interface OverviewRow {
  key: string
  sno: number
  tag: string
  name: string
  substrate: string
  type: string
  location: string
  code: string
  system: string
  totalSqm: number
  doneSqm: number
  remainingSqm: number
  demand: number
  allocated: number
  shortfall: number
  pct: number
}

const STATUS_OPTS = ['All', 'Fully Ready (100%)', 'Partial (50-99%)', 'Blocked (<50%)']

export default function TotalOverview({ siteId }: { siteId?: string }) {
  const { message } = App.useApp()
  const { data: snap, isLoading } = useSmeSnapshot(siteId)
  const [locs, setLocs] = useState<string[]>([])
  const [types, setTypes] = useState<string[]>([])
  const [codes, setCodes] = useState<string[]>([])
  const [status, setStatus] = useState('All')
  const [busy, setBusy] = useState<string | null>(null)

  const model = useMemo(
    () => (snap ? buildModel(snap.equipment, snap.recipes, snap.materials, snap.progress) : null),
    [snap])

  const { rows, lines } = useMemo(() => {
    if (!model) return { rows: [] as OverviewRow[], lines: [] }
    const plan = runPlan(model, model.defaultOrder)
    const acc = new Map<string, { demand: number; alloc: number; short: number }>()
    for (const ln of plan.lines) {
      const k = unitKey(ln.Equipment_Tag_No, ln.Lining_System_Code)
      const a = acc.get(k) ?? { demand: 0, alloc: 0, short: 0 }
      a.demand += ln.Demand_Qty
      a.alloc += ln.Allocated_Qty
      a.short += ln.Shortfall_Qty
      acc.set(k, a)
    }
    const out: OverviewRow[] = allUnits(model).map((u) => {
      const a = acc.get(unitKey(u.tag, u.code)) ?? { demand: 0, alloc: 0, short: 0 }
      const pct = a.demand > 0 ? Math.min(100, (a.alloc / a.demand) * 100) : 100
      return {
        key: unitKey(u.tag, u.code), sno: 0,
        tag: u.tag, name: u.name, substrate: u.substrate, type: u.type,
        location: u.location || '—', code: u.code, system: u.shortName,
        totalSqm: Math.round(u.original * 100) / 100,
        doneSqm: Math.round(u.done * 100) / 100,
        remainingSqm: Math.round(u.remaining * 100) / 100,
        demand: Math.round(a.demand * 1000) / 1000,
        allocated: Math.round(a.alloc * 1000) / 1000,
        shortfall: Math.round(a.short * 1000) / 1000,
        pct: Math.round(pct * 10) / 10,
      }
    })
    return { rows: out, lines: plan.lines }
  }, [model])

  if (isLoading) return <Skeleton active paragraph={{ rows: 8 }} />
  if (!snap || !model) return <Alert type="warning" showIcon title="SME model unavailable" />

  // Cascading filter option pools.
  const locPool = [...new Set(rows.map((r) => r.location))].sort()
  const typePool = [...new Set(rows.filter((r) => !locs.length || locs.includes(r.location))
    .map((r) => r.type).filter(Boolean))].sort()
  const codePool = [...new Set(rows.filter((r) =>
    (!locs.length || locs.includes(r.location)) && (!types.length || types.includes(r.type)))
    .map((r) => r.code))].sort(syscodeCompare)

  const statusPass = (p: number) =>
    status === 'All' ? true
      : status.startsWith('Fully') ? p >= 100
        : status.startsWith('Partial') ? p >= 50 && p < 100
          : p < 50

  const filtered = rows.filter((r) =>
    (!locs.length || locs.includes(r.location))
    && (!types.length || types.includes(r.type))
    && (!codes.length || codes.includes(r.code))
    && statusPass(r.pct))
    .map((r, i) => ({ ...r, sno: i + 1 }))

  const totSqm = filtered.reduce((s, r) => s + r.totalSqm, 0)
  const doneSqm = filtered.reduce((s, r) => s + r.doneSqm, 0)
  const remSqm = filtered.reduce((s, r) => s + r.remainingSqm, 0)
  const shortSqm = filtered.reduce((s, r) => s + r.remainingSqm * (1 - r.pct / 100), 0)
  const avgCov = filtered.length ? filtered.reduce((s, r) => s + r.pct, 0) / filtered.length : 100

  const cols: ColumnsType<OverviewRow> = [
    { title: 'S.No', dataIndex: 'sno', key: 'sn', width: 60, fixed: 'left' },
    { title: 'Equipment No', dataIndex: 'tag', key: 't', width: 170, fixed: 'left', render: (v: string) => <b style={mono}>{v}</b> },
    { title: 'Name', dataIndex: 'name', key: 'n', width: 200, ellipsis: true },
    { title: 'Substrate', dataIndex: 'substrate', key: 'sub', width: 100, render: (v: string) => v || '—' },
    { title: 'Type', dataIndex: 'type', key: 'ty', width: 100, render: (v: string) => v || '—' },
    { title: 'Location', dataIndex: 'location', key: 'l', width: 110 },
    { title: 'Code', dataIndex: 'code', key: 'c', width: 70 },
    { title: 'System', dataIndex: 'system', key: 'sy', width: 110, ellipsis: true },
    { title: 'Total SQM', dataIndex: 'totalSqm', key: 'ts', width: 100, align: 'right', render: (v: number) => nf(v) },
    { title: 'Done SQM', dataIndex: 'doneSqm', key: 'ds', width: 100, align: 'right', render: (v: number) => nf(v) },
    { title: 'Remaining', dataIndex: 'remainingSqm', key: 'rs', width: 100, align: 'right', render: (v: number) => nf(v) },
    { title: 'Demand', dataIndex: 'demand', key: 'd', width: 110, align: 'right', render: (v: number) => nf(v, 2) },
    { title: 'Allocated', dataIndex: 'allocated', key: 'a', width: 110, align: 'right', render: (v: number) => nf(v, 2) },
    {
      title: 'Shortfall', dataIndex: 'shortfall', key: 'sh', width: 110, align: 'right',
      render: (v: number) => <span style={{ color: v > 0 ? '#EF4444' : undefined, fontWeight: v > 0 ? 700 : 400 }}>{nf(v, 2)}</span>,
    },
    {
      title: 'Fulfil %', dataIndex: 'pct', key: 'p', width: 90, align: 'right',
      render: (v: number) => <b style={{ color: fc(v) }}>{v.toFixed(1)}%</b>,
    },
  ]

  const exportPlan = async (format: string) => {
    setBusy(format)
    try {
      await postDownloadDocument('/sme/plan/export',
        {
          priority_order: model.defaultOrder, key: 'overview', format,
          ...(siteId ? { site_id: siteId } : {}),
        }, `sme-total-overview.${format}`)
    } catch { message.error('Export failed') } finally { setBusy(null) }
  }

  // Per-code material expanders over the FILTERED tag set.
  const filteredTagCodes = new Set(filtered.map((r) => r.key))
  const codesInView = [...new Set(filtered.map((r) => r.code))].sort(syscodeCompare)
  const availOf = new Map(snap.materials.map((m) => [String(m.material_code).trim(), Number(m.available_qty ?? 0) || 0]))

  return (
    <div>
      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        <Col xs={24} sm={12} lg={6}>
          <Select mode="multiple" allowClear placeholder="All locations" style={{ width: '100%' }}
            maxTagCount="responsive" value={locs} onChange={setLocs}
            options={locPool.map((l) => ({ value: l, label: l }))} />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Select mode="multiple" allowClear placeholder="All types" style={{ width: '100%' }}
            maxTagCount="responsive" value={types} onChange={setTypes}
            options={typePool.map((t) => ({ value: t, label: t }))} />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Select mode="multiple" allowClear placeholder="All system codes" style={{ width: '100%' }}
            maxTagCount="responsive" value={codes} onChange={setCodes}
            options={codePool.map((c) => ({ value: c, label: `Code ${c}` }))} />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Select style={{ width: '100%' }} value={status} onChange={setStatus}
            options={STATUS_OPTS.map((s) => ({ value: s, label: s }))} />
        </Col>
      </Row>

      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        <Col flex="1 1 140px"><KpiDrill title="No. of Items" value={String(filtered.length)}
          drillTitle="Filtered items" rows={filtered.map((r) => ({
            '#': r.sno, Tag: r.tag, Code: r.code, 'Fulfil %': r.pct,
          }))} /></Col>
        <Col flex="1 1 140px"><KpiDrill title="Total SQM" value={nf(totSqm)}
          drillTitle="SQM (desc)" rows={[...filtered].sort((a, b) => b.totalSqm - a.totalSqm)
            .map((r) => ({ Tag: r.tag, Code: r.code, 'Total SQM': r.totalSqm }))} /></Col>
        <Col flex="1 1 140px"><KpiDrill title="Already Done SQM" value={nf(doneSqm)}
          drillTitle="Done (desc)" rows={[...filtered].sort((a, b) => b.doneSqm - a.doneSqm)
            .map((r) => ({ Tag: r.tag, Code: r.code, 'Done SQM': r.doneSqm }))} /></Col>
        <Col flex="1 1 140px"><KpiDrill title="Remaining SQM" value={nf(remSqm)}
          drillTitle="Remaining (desc)" rows={[...filtered].sort((a, b) => b.remainingSqm - a.remainingSqm)
            .map((r) => ({ Tag: r.tag, Code: r.code, Remaining: r.remainingSqm }))} /></Col>
        <Col flex="1 1 140px"><KpiDrill title="Shortfall SQM" value={nf(shortSqm)}
          accent={shortSqm > 0.005 ? '#EF4444' : undefined}
          drillTitle="Shortfall SQM (fulfillment-weighted)"
          rows={filtered.filter((r) => r.pct < 100)
            .map((r) => ({
              Tag: r.tag, Code: r.code,
              'Shortfall SQM': Math.round(r.remainingSqm * (1 - r.pct / 100) * 100) / 100,
            }))} /></Col>
        <Col flex="1 1 140px"><KpiDrill title="Avg Coverage" value={`${avgCov.toFixed(1)}%`}
          accent={fc(avgCov)} drillTitle="Coverage (asc)"
          rows={[...filtered].sort((a, b) => a.pct - b.pct)
            .map((r) => ({ Tag: r.tag, Code: r.code, 'Fulfil %': r.pct }))} /></Col>
      </Row>

      <Card size="small"
        title={<span style={{ ...mono, fontSize: '0.7rem', fontWeight: 700, letterSpacing: '.1em', opacity: 0.7 }}>MASTER TABLE — EQUIPMENT × SYSTEM CODE</span>}
        extra={(
          <Space>
            <Button size="small" icon={<FileExcelOutlined />} loading={busy === 'xlsx'}
              onClick={() => exportPlan('xlsx')}>Excel</Button>
            <Button size="small" icon={<FilePdfOutlined />} loading={busy === 'pdf'}
              onClick={() => exportPlan('pdf')}>PDF</Button>
            {/* legacy consumption_log_full download (committed entries) */}
            <span style={{ fontSize: '0.7rem', opacity: 0.7, marginLeft: 8 }}>Consumption Log:</span>
            <ScopedExport exportKey="production-log" siteId={siteId} />
          </Space>
        )}>
        <Table<OverviewRow> virtual size="small" rowKey="key" columns={cols}
          dataSource={filtered} pagination={false}
          scroll={{ x: 1600, y: 520 }}
          onRow={(r) => ({ style: { background: fcBg(r.pct) } })} />
      </Card>

      {/* Per-code material detail over the filtered set (cascade-based) */}
      <Collapse size="small" style={{ marginTop: 12 }} items={codesInView.map((code) => {
        const codeRows = filtered.filter((r) => r.code === code)
        const codeLines = lines.filter((l) =>
          l.Lining_System_Code === code && filteredTagCodes.has(unitKey(l.Equipment_Tag_No, l.Lining_System_Code)))
        const mats = new Map<string, { name: string; uom: string; demand: number; alloc: number; short: number }>()
        for (const l of codeLines) {
          const m = mats.get(l.Material_Code) ?? { name: l.Material_Name, uom: l.UOM, demand: 0, alloc: 0, short: 0 }
          m.demand += l.Demand_Qty
          m.alloc += l.Allocated_Qty
          m.short += l.Shortfall_Qty
          mats.set(l.Material_Code, m)
        }
        const sqm = codeRows.reduce((s, r) => s + r.totalSqm, 0)
        const done = codeRows.reduce((s, r) => s + r.doneSqm, 0)
        const demand = codeLines.reduce((s, l) => s + l.Demand_Qty, 0)
        const alloc = codeLines.reduce((s, l) => s + l.Allocated_Qty, 0)
        const cov = demand > 0 ? Math.min(100, (alloc / demand) * 100) : 100
        return {
          key: code,
          label: (
            <Space>
              <span style={{
                ...mono, border: '1px solid rgba(212,175,55,.5)', color: '#D4AF37',
                borderRadius: 6, padding: '0 6px', fontSize: '0.68rem', fontWeight: 700,
              }}>Code {code}</span>
              <span style={{ fontSize: '0.74rem', opacity: 0.75 }}>{codeRows[0]?.system}</span>
              <span style={{ ...mono, fontSize: '0.7rem', opacity: 0.65 }}>
                {nf(sqm)} SQM · done {nf(done)}
              </span>
              <FulfilPill pct={Math.round(cov * 10) / 10} />
            </Space>
          ),
          children: (
            <Table size="small" rowKey="mat" pagination={false} scroll={{ x: 'max-content' }}
              columns={[
                { title: 'Material', dataIndex: 'mat', key: 'm', width: 130 },
                { title: 'Name', dataIndex: 'name', key: 'n', ellipsis: true },
                { title: 'UOM', dataIndex: 'uom', key: 'u', width: 60 },
                { title: 'Available', dataIndex: 'avail', key: 'av', align: 'right' as const, render: (v: number) => nf(v, 3) },
                { title: 'Total Demand', dataIndex: 'demand', key: 'd', align: 'right' as const, render: (v: number) => nf(v, 3) },
                {
                  title: 'Shortfall', dataIndex: 'short', key: 's', align: 'right' as const,
                  render: (v: number) => <span style={{ color: v > 0 ? '#EF4444' : undefined, fontWeight: v > 0 ? 700 : 400 }}>{nf(v, 3)}</span>,
                },
                {
                  title: 'Coverage %', dataIndex: 'pct', key: 'p', align: 'right' as const, width: 100,
                  render: (v: number) => <b style={{ color: fc(v) }}>{v.toFixed(1)}%</b>,
                },
              ]}
              dataSource={[...mats.entries()].map(([mat, m]) => ({
                mat, name: m.name, uom: m.uom,
                avail: availOf.get(mat) ?? 0,
                demand: Math.round(m.demand * 1000) / 1000,
                short: Math.round(m.short * 1000) / 1000,
                pct: m.demand > 0 ? Math.round(Math.min(100, (m.alloc / m.demand) * 100) * 10) / 10 : 100,
              }))}
              onRow={(r) => ({ style: { background: fcBg(r.pct) } })} />
          ),
        }
      })} />
    </div>
  )
}
