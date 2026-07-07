/**
 * frontend/src/sme/SmeDashboard.tsx — SME Dashboard rebuild (Phase S2).
 *
 * React port of the legacy Streamlit Tab 0 "Project Overview" — computed
 * entirely client-side from the /sme/model-snapshot via insights.ts, so every
 * filter change re-renders instantly with zero server round-trips:
 *   · 4-way cascading cross-filters (Location → Type → System Code → Substrate)
 *   · 7-KPI strip with single-click drill-down modals (legacy: double-click hack)
 *   · legacy SVG gauge + coverage hbars as native React components
 *   · Recharts stacked bars (demand-vs-available, per-location can-do/deficit)
 *   · Material Balance grid with the legacy 4-tier row tinting
 */
import { useMemo, useState } from 'react'
import { Alert, Button, Card, Col, Collapse, Row, Segmented, Select, Skeleton, Space, Table } from 'antd'
import { DownloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import {
  Bar, BarChart, Cell, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { useSmeSnapshot } from '../api/hooks'
import { buildModel, roundN } from './engine'
import CoverageGauge from './CoverageGauge'
import CoverageHBar from './CoverageHBar'
import KpiDrill from './KpiDrill'
import type { DrillRow } from './KpiDrill'
import {
  EMPTY_FILTERS, applyFilters, fc, fcBg, fcDot, filterOptions, locColor,
  locationRows, materialBalance, pairCoverage, stockOnlyRows, systemCodeRows,
} from './insights'
import type { BalanceRow, DashFilters } from './insights'
import ProcurementView from './ProcurementView'
import { RowsExportButtons } from './rowsExport'

// Legacy dashboard_material_balance export columns (same frame as the CSV).
const balanceExportCols = ['Code', 'Material Name', 'UOM', 'Available', 'On Order',
  'Total Demand', 'Shortfall', 'Net Shortfall', 'Coverage %']
const balanceExportRow = (r: BalanceRow) => [
  r.Material_Code, r.Material_Name, r.UOM, r.Available_Qty, r.Ordered_Qty,
  r.Demand_Qty, r.Shortfall, r.Net_Shortfall, r.Coverage_Pct,
]

const nf = (v: number, d = 1) =>
  v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })

function downloadCsv(filename: string, rows: Record<string, unknown>[]) {
  if (!rows.length) return
  const cols = Object.keys(rows[0])
  const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
  const csv = [cols.map(esc).join(','),
    ...rows.map((r) => cols.map((c) => esc(r[c])).join(','))].join('\n')
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

const secHdr: React.CSSProperties = {
  fontFamily: 'JetBrains Mono, monospace', fontSize: '0.68rem', fontWeight: 700,
  letterSpacing: '.13em', textTransform: 'uppercase', opacity: 0.65,
}

export default function SmeDashboard({ siteId }: { siteId?: string }) {
  const { data: snap, isLoading, isError } = useSmeSnapshot(siteId)
  const [filters, setFilters] = useState<DashFilters>(EMPTY_FILTERS)
  const [view, setView] = useState<string>('📈 Project Overview')

  const model = useMemo(
    () => (snap ? buildModel(snap.equipment, snap.recipes, snap.materials, snap.progress) : null),
    [snap])
  const options = useMemo(
    () => (model ? filterOptions(model, filters) : null), [model, filters])

  const computed = useMemo(() => {
    if (!model || !snap) return null
    const units = applyFilters(model, filters)
    const tags = [...new Set(units.map((u) => u.tag))]
    const balance = materialBalance(model, units, snap.materials)
    const pairs = pairCoverage(model, units, snap.materials)
    const projSqm = roundN(units.reduce((s, u) => s + u.remaining, 0), 2)
    const fCov = Math.min(balance.totals.coveragePct, 100)
    const canSqm = roundN(projSqm * Math.min(1, fCov / 100), 2)
    return {
      units, tags, balance, pairs, projSqm, fCov, canSqm,
      shortSqm: roundN(projSqm - canSqm, 2),
      locs: locationRows(model, units, snap.materials),
      codes: systemCodeRows(model, units, snap.materials),
      stockOnly: stockOnlyRows(model, units, snap.materials),
    }
  }, [model, snap, filters])

  if (isLoading) return <Skeleton active paragraph={{ rows: 8 }} />
  if (isError || !snap || !model || !options || !computed) {
    return <Alert type="warning" showIcon title="SME model unavailable"
      description="Could not load the estimator model snapshot — check the API connection." />
  }

  const { tags, balance, pairs, projSqm, fCov, canSqm, shortSqm, locs, codes, stockOnly } = computed

  // ── Drill-down frames (legacy _dd_* ports) ─────────────────────────────────
  const tagMeta = new Map(computed.units.map((u) => [u.tag, u]))
  const dEquip: DrillRow[] = tags.map((t) => {
    const u = tagMeta.get(t)!
    return { 'Equipment Tag': t, Name: u.name, Location: u.location, Type: u.type, Substrate: u.substrate }
  })
  const dSqm: DrillRow[] = [...pairs].sort((a, b) => b.sqm - a.sqm)
    .map((p) => ({ 'Equipment Tag': p.tag, 'System Code': p.code, 'Total SQM': p.sqm }))
  const dCovSqm: DrillRow[] = [...pairs].sort((a, b) => a.coveragePct - b.coveragePct)
    .map((p) => ({ 'Equipment Tag': p.tag, 'System Code': p.code, 'Total SQM': p.sqm, 'Coverage %': p.coveragePct, 'Coverable SQM': p.coverableSqm }))
  const dDefSqm: DrillRow[] = [...pairs].filter((p) => p.deficitSqm > 0)
    .sort((a, b) => b.deficitSqm - a.deficitSqm)
    .map((p) => ({ 'Equipment Tag': p.tag, 'System Code': p.code, 'Total SQM': p.sqm, 'Coverable SQM': p.coverableSqm, 'SQM Deficit': p.deficitSqm }))
  const dCrit: DrillRow[] = balance.rows.filter((r) => r.Coverage_Pct < 50)
    .sort((a, b) => a.Coverage_Pct - b.Coverage_Pct)
    .map((r) => ({ Code: r.Material_Code, Material: r.Material_Name, Demand: r.Demand_Qty, Available: r.Available_Qty, 'Coverage %': r.Coverage_Pct }))
  const critCount = dCrit.length

  // ── Material balance table ─────────────────────────────────────────────────
  const balanceSorted = [...balance.rows].sort((a, b) => a.Coverage_Pct - b.Coverage_Pct)
  const balanceCols: ColumnsType<BalanceRow> = [
    { title: 'Code', dataIndex: 'Material_Code', key: 'code', width: 120, fixed: 'left' },
    { title: 'Material Name', dataIndex: 'Material_Name', key: 'name', ellipsis: true },
    { title: 'UOM', dataIndex: 'UOM', key: 'uom', width: 70 },
    { title: 'Available', dataIndex: 'Available_Qty', key: 'avail', align: 'right', render: (v: number) => nf(v, 3) },
    { title: 'On Order', dataIndex: 'Ordered_Qty', key: 'ord', align: 'right', render: (v: number) => nf(v, 3) },
    { title: 'Total Demand', dataIndex: 'Demand_Qty', key: 'dem', align: 'right', render: (v: number) => nf(v, 3) },
    { title: 'Shortfall', dataIndex: 'Shortfall', key: 'short', align: 'right', render: (v: number) => nf(v, 3) },
    { title: 'Net Shortfall', dataIndex: 'Net_Shortfall', key: 'net', align: 'right', render: (v: number) => nf(v, 3) },
    {
      title: 'Coverage %', dataIndex: 'Coverage_Pct', key: 'cov', align: 'right', width: 110,
      render: (v: number) => <span style={{ color: fc(v), fontWeight: 700 }}>{v.toFixed(1)}%</span>,
    },
  ]

  const scTableCols: ColumnsType<(typeof codes)[number]> = [
    { title: 'Code', dataIndex: 'label', key: 'c' },
    { title: 'Short Name', dataIndex: 'shortName', key: 's', ellipsis: true },
    { title: 'SQM Total', dataIndex: 'sqm', key: 't', align: 'right', render: (v: number) => nf(v, 1) },
    { title: 'Coverage SQM', dataIndex: 'canSqm', key: 'a', align: 'right', render: (v: number) => nf(v, 1) },
    { title: 'SQM Deficit', dataIndex: 'shortSqm', key: 'd', align: 'right', render: (v: number) => nf(v, 1) },
    {
      title: 'Coverage %', dataIndex: 'coveragePct', key: 'p', align: 'right',
      render: (v: number) => <span style={{ color: fc(v), fontWeight: 700 }}>{v.toFixed(1)}%</span>,
    },
  ]

  const selectProps = {
    mode: 'multiple' as const, allowClear: true, maxTagCount: 'responsive' as const,
    style: { width: '100%' },
  }

  return (
    <div>
      {/* ── 🎛 Cascading cross-filters ─────────────────────────────────────── */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <div style={{ ...secHdr, marginBottom: 8 }}>🎛 Filter</div>
        <Row gutter={[12, 12]}>
          <Col xs={24} sm={12} lg={6}>
            <Select {...selectProps} placeholder="All locations" value={filters.locations}
              onChange={(v) => setFilters({ ...filters, locations: v })}
              options={options.locations.map((l) => ({ value: l, label: l }))} />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Select {...selectProps} placeholder="All types" value={filters.types}
              onChange={(v) => setFilters({ ...filters, types: v })}
              options={options.types.map((t) => ({ value: t, label: t }))} />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Select {...selectProps} placeholder="All system codes" value={filters.codes}
              onChange={(v) => setFilters({ ...filters, codes: v })}
              options={options.codes.map((c) => ({
                value: c.code, label: `Code ${c.code}${c.shortName ? ` – ${c.shortName}` : ''}`,
              }))} />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Select {...selectProps} placeholder="All substrates" value={filters.substrates}
              onChange={(v) => setFilters({ ...filters, substrates: v })}
              options={options.substrates.map((s) => ({ value: s, label: s }))} />
          </Col>
        </Row>
      </Card>

      {/* ── Sub-view toggle (legacy dash_view radio) ──────────────────────── */}
      <Segmented options={['📈 Project Overview', '🛒 Material Requirement & Procurement']}
        value={view} onChange={(v) => setView(String(v))} style={{ marginBottom: 16 }} />

      {view !== '📈 Project Overview' ? (
        <>
          {/* 4-KPI strip (legacy procurement view) + per-location/per-code drill */}
          <Row gutter={[12, 12]}>
            <Col flex="1 1 160px"><KpiDrill title="Equipment" value={String(tags.length)}
              drillTitle="Equipment List" rows={dEquip}
              help="Equipment tags matching current filter selection." /></Col>
            <Col flex="1 1 160px"><KpiDrill title="Total SQM" value={nf(projSqm)}
              drillTitle="SQM by Equipment & System Code" rows={dSqm}
              help="Remaining surface area (m²) after deducting daily consumption entries." /></Col>
            <Col flex="1 1 160px"><KpiDrill title="Available Coverage SQM" value={nf(canSqm, 2)}
              drillTitle="Coverable SQM by Equipment & System Code" rows={dCovSqm}
              help="Area (m²) coverable with currently available stock." /></Col>
            <Col flex="1 1 160px"><KpiDrill title="SQM Deficit" value={nf(shortSqm, 2)}
              accent={shortSqm > 0 ? '#EF4444' : undefined}
              drillTitle="SQM Deficit by Equipment & System Code" rows={dDefSqm}
              help="Area (m²) that cannot be completed with current stock." /></Col>
          </Row>
          <ProcurementView model={model} units={computed.units} materials={snap.materials} />
        </>
      ) : (
        <>
      {/* ── 7-KPI strip with click drill-downs ────────────────────────────── */}
      <Row gutter={[12, 12]}>
        <Col flex="1 1 145px"><KpiDrill title="Equipment" value={String(tags.length)}
          drillTitle="Equipment List" rows={dEquip}
          help="Equipment tags matching current filter selection." /></Col>
        <Col flex="1 1 145px"><KpiDrill title="Total SQM" value={nf(projSqm)}
          drillTitle="SQM by Equipment & System Code" rows={dSqm}
          help="Remaining surface area (m²) after deducting daily consumption entries." /></Col>
        <Col flex="1 1 145px"><KpiDrill title="Available Coverage SQM" value={nf(canSqm, 2)}
          drillTitle="Coverable SQM by Equipment & System Code" rows={dCovSqm}
          help="Area (m²) coverable with currently available stock = Total SQM × Coverage %." /></Col>
        <Col flex="1 1 145px"><KpiDrill title="SQM Deficit" value={nf(shortSqm, 2)}
          accent={shortSqm > 0 ? '#EF4444' : undefined}
          drillTitle="SQM Deficit by Equipment & System Code" rows={dDefSqm}
          help="Area (m²) that cannot be completed = Total SQM − Coverable SQM." /></Col>
        <Col flex="1 1 145px"><KpiDrill title="Overall Coverage" value={`${fCov.toFixed(1)}%`}
          accent={fc(fCov)} delta={`${(fCov - 100).toFixed(1)}%`} deltaColor={fc(fCov)}
          drillTitle="Coverable SQM by Equipment & System Code" rows={dCovSqm}
          help="Allocated Qty ÷ Demand Qty × 100 across all filtered materials." /></Col>
        <Col flex="1 1 145px"><KpiDrill title="Shortfall SQM" value={nf(shortSqm, 2)}
          accent={shortSqm > 0 ? '#EF4444' : undefined}
          drillTitle="SQM Deficit by Equipment & System Code" rows={dDefSqm}
          help="Area (m²) shortfall = Total SQM − Available Coverage SQM." /></Col>
        <Col flex="1 1 145px"><KpiDrill title="Critical (<50%)" value={String(critCount)}
          accent={critCount > 0 ? '#EF4444' : '#10B981'}
          drillTitle="Critical Materials (Coverage < 50%)" rows={dCrit}
          help="Materials where Available Qty covers less than 50% of total demand." /></Col>
      </Row>

      {/* ── Gauge + demand-vs-available | per-location bars ───────────────── */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={9}>
          <Card size="small" title={<span style={secHdr}>🎯 Overall Coverage</span>}>
            <CoverageGauge pct={fCov} canSqm={canSqm} totalSqm={projSqm} />
            <ResponsiveContainer width="100%" height={120}>
              <BarChart data={[{
                name: 'Inventory',
                Available: roundN(balance.totals.availCapped, 1),
                Shortfall: roundN(balance.totals.shortfall, 1),
              }]}>
                <XAxis dataKey="name" hide />
                <YAxis hide />
                <Tooltip formatter={(v) => nf(Number(v), 1)} />
                <Legend wrapperStyle={{ fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }} />
                <Bar dataKey="Available" stackId="inv" fill="#10B981" fillOpacity={0.8} />
                <Bar dataKey="Shortfall" stackId="inv" fill="#EF4444" fillOpacity={0.8} />
              </BarChart>
            </ResponsiveContainer>
          </Card>
        </Col>
        <Col xs={24} lg={15}>
          <Card size="small" title={<span style={secHdr}>📍 Coverage by Location (SQM)</span>}>
            {locs.length === 0 ? <Alert type="info" title="No equipment matches the filters." /> : (
              <>
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={locs}>
                    <XAxis dataKey="label" tick={{ fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }} />
                    <YAxis tick={{ fontSize: 10 }} width={54}
                      label={{ value: 'SQM', angle: -90, position: 'insideLeft', fontSize: 10 }} />
                    <Tooltip formatter={(v) => `${nf(Number(v), 1)} SQM`} />
                    <Bar dataKey="canSqm" name="Can Do" stackId="loc">
                      {locs.map((l) => <Cell key={l.key} fill={locColor(l.label)} fillOpacity={0.8} />)}
                    </Bar>
                    <Bar dataKey="shortSqm" name="Deficit" stackId="loc" fill="#EF4444" fillOpacity={0.6} />
                  </BarChart>
                </ResponsiveContainer>
                <Row gutter={[8, 8]} style={{ marginTop: 8 }}>
                  {locs.map((l) => (
                    <Col key={l.key} flex="1 1 120px">
                      <Card size="small" styles={{ body: { padding: 8, textAlign: 'center' } }}>
                        <div style={{ fontSize: '1.05rem' }}>{fcDot(l.coveragePct)}</div>
                        <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: '0.7rem', fontWeight: 700, color: '#D4AF37' }}>{l.label}</div>
                        <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: '1.05rem', fontWeight: 700 }}>{l.coveragePct.toFixed(1)}%</div>
                        <div style={{ fontSize: '0.66rem', opacity: 0.65 }}>{nf(l.canSqm, 0)} / {nf(l.sqm, 0)} SQM</div>
                        <div style={{ fontSize: '0.64rem', opacity: 0.65 }}>{l.equipment} equipment</div>
                      </Card>
                    </Col>
                  ))}
                </Row>
              </>
            )}
          </Card>
        </Col>
      </Row>

      {/* ── Coverage hbars: by system code | by material ──────────────────── */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={12}>
          <Card size="small" title={<span style={secHdr}>⚙️ Coverage by System Code (SQM)</span>}>
            <CoverageHBar data={[...codes].sort((a, b) => a.coveragePct - b.coveragePct)
              .map((c) => ({
                label: `${c.label}${c.shortName ? ` – ${c.shortName.slice(0, 14)}` : ''}`,
                val: c.coveragePct,
              }))} />
            <Table size="small" style={{ marginTop: 8 }} columns={scTableCols}
              dataSource={codes} rowKey="key" pagination={false} scroll={{ x: 'max-content', y: 260 }} />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title={<span style={secHdr}>🧪 Coverage by Material</span>}>
            <CoverageHBar data={balanceSorted.map((r) => ({
              label: (r.Material_Name || r.Material_Code).slice(0, 18),
              val: r.Coverage_Pct,
            }))} />
          </Card>
        </Col>
      </Row>

      {/* ── 📋 Full material balance (4-tier tinting) ─────────────────────── */}
      <Card size="small" style={{ marginTop: 16 }}
        title={<span style={secHdr}>📋 Full Material Balance</span>}
        extra={(
          <Space size={4}>
            <Button size="small" icon={<DownloadOutlined />}
              onClick={() => downloadCsv('sme-material-balance.csv',
                balanceSorted.map((r) => ({
                  Code: r.Material_Code, 'Material Name': r.Material_Name, UOM: r.UOM,
                  Available: r.Available_Qty, 'On Order': r.Ordered_Qty,
                  'Total Demand': r.Demand_Qty, Shortfall: r.Shortfall,
                  'Net Shortfall': r.Net_Shortfall, 'Coverage %': r.Coverage_Pct,
                })))}>CSV</Button>
            {/* legacy dashboard_material_balance xlsx / pdf */}
            <RowsExportButtons doc={() => ({
              title: 'Material Balance Report',
              filenameStem: 'dashboard_material_balance',
              columns: balanceExportCols,
              rows: balanceSorted.map(balanceExportRow),
            })} />
          </Space>
        )}>
        <Table<BalanceRow> size="small" columns={balanceCols} dataSource={balanceSorted}
          rowKey="Material_Code" pagination={{ pageSize: 20, showTotal: (t) => `${t} materials` }}
          scroll={{ x: 'max-content' }}
          onRow={(r) => ({ style: { background: fcBg(r.Coverage_Pct) } })} />
      </Card>

      {/* ── 📦 Stock-only materials ───────────────────────────────────────── */}
      {stockOnly.length > 0 && (
        <Collapse ghost size="small" style={{ marginTop: 8 }} items={[{
          key: 'stock-only',
          label: `📦 Stock-Only Materials (No Demand in Any System Code) — ${stockOnly.length}`,
          children: (
            <Table size="small" rowKey="Material_Code" pagination={false}
              scroll={{ x: 'max-content' }}
              columns={[
                { title: 'Code', dataIndex: 'Material_Code', key: 'c' },
                { title: 'Material Name', dataIndex: 'Material_Name', key: 'n', ellipsis: true },
                { title: 'UOM', dataIndex: 'UOM', key: 'u', width: 70 },
                { title: 'Available', dataIndex: 'Available_Qty', key: 'a', align: 'right', render: (v: number) => nf(v, 3) },
                { title: 'On Order', dataIndex: 'Ordered_Qty', key: 'o', align: 'right', render: (v: number) => nf(v, 3) },
              ]}
              dataSource={stockOnly} />
          ),
        }]} />
      )}
        </>
      )}
    </div>
  )
}
