/**
 * frontend/src/sme/ProcurementView.tsx — 🛒 Material Requirement &
 * Procurement (Phase S4). The deferred second Dashboard sub-view: per-location
 * sections (dot · colored badge · equipment count · SQM · coverage) with
 * per-system-code expanders — 5 metric chips + the (location, code)-scoped
 * material balance table — and the grand-total strip. Same dashboard
 * semantics as insights.ts (per-material cap, no cascade), all client-side.
 */
import { Card, Collapse, Space, Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { SmeModel, SnapshotMaterial } from './engine'
import { syscodeCompare } from './engine'
import { fc, fcBg, fcDot, locColor, materialBalance, scopeCoverage, systemCodeRows } from './insights'
import type { BalanceRow, UnitRef } from './insights'
import { FulfilPill } from './PriorityList'
import { RowsExportButtons } from './rowsExport'

const procCols = ['Material', 'Name', 'UOM', 'Demand', 'Available', 'On Order',
  'Shortfall', 'Net Shortfall', 'Coverage %']
const procRow = (r: BalanceRow) => [
  r.Material_Code, r.Material_Name, r.UOM, r.Demand_Qty, r.Available_Qty,
  r.Ordered_Qty, r.Shortfall, r.Net_Shortfall, r.Coverage_Pct,
]

const mono: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }
const nf = (v: number, d = 3) =>
  v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: d })

const balanceCols: ColumnsType<BalanceRow> = [
  { title: 'Material', dataIndex: 'Material_Code', key: 'c', width: 120 },
  { title: 'Name', dataIndex: 'Material_Name', key: 'n', ellipsis: true },
  { title: 'UOM', dataIndex: 'UOM', key: 'u', width: 60 },
  { title: 'Demand', dataIndex: 'Demand_Qty', key: 'd', align: 'right', render: (v: number) => nf(v) },
  { title: 'Available', dataIndex: 'Available_Qty', key: 'a', align: 'right', render: (v: number) => nf(v) },
  { title: 'On Order', dataIndex: 'Ordered_Qty', key: 'o', align: 'right', render: (v: number) => nf(v) },
  {
    title: 'Shortfall', dataIndex: 'Shortfall', key: 's', align: 'right',
    render: (v: number) => <span style={{ color: v > 0 ? '#EF4444' : undefined, fontWeight: v > 0 ? 700 : 400 }}>{nf(v)}</span>,
  },
  { title: 'Net Shortfall', dataIndex: 'Net_Shortfall', key: 'x', align: 'right', render: (v: number) => nf(v) },
  {
    title: 'Coverage %', dataIndex: 'Coverage_Pct', key: 'p', align: 'right', width: 100,
    render: (v: number) => <span style={{ color: fc(v), fontWeight: 700 }}>{v.toFixed(1)}%</span>,
  },
]

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span style={{
      display: 'inline-block', border: '1px solid rgba(128,128,128,.3)', borderRadius: 8,
      padding: '2px 10px', marginRight: 8, marginBottom: 6, fontSize: '0.7rem',
    }}>
      <span style={{ opacity: 0.65 }}>{label} </span>
      <b style={mono}>{value}</b>
    </span>
  )
}

export default function ProcurementView({ model, units, materials }: {
  model: SmeModel
  units: UnitRef[] // already filtered by the dashboard's cross-filters
  materials: SnapshotMaterial[]
}) {
  const locations = [...new Set(units.map((u) => u.location))].sort()
  const overall = materialBalance(model, units, materials)

  return (
    <div>
      {locations.map((loc) => {
        const locUnits = units.filter((u) => u.location === loc)
        const cov = Math.min(scopeCoverage(model, locUnits, materials).coveragePct, 100)
        const sqm = locUnits.reduce((s, u) => s + u.remaining, 0)
        const tags = new Set(locUnits.map((u) => u.tag)).size
        const codes = systemCodeRows(model, locUnits, materials)
        return (
          <Card key={loc} size="small" style={{ marginTop: 12 }}
            title={(
              <span style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <span>{fcDot(cov)}</span>
                <span style={{
                  ...mono, background: locColor(loc), color: '#fff', borderRadius: 6,
                  padding: '1px 10px', fontSize: '0.72rem', fontWeight: 700,
                }}>{loc || '—'}</span>
                <span style={{ fontSize: '0.72rem', opacity: 0.75 }}>
                  {tags} equipment · {nf(sqm, 1)} SQM
                </span>
                <FulfilPill pct={cov} />
              </span>
            )}>
            <Collapse size="small" items={[...codes].sort((a, b) => syscodeCompare(a.key, b.key)).map((cs) => {
              const codeUnits = locUnits.filter((u) => u.code === cs.key)
              const bal = materialBalance(model, codeUnits, materials)
              return {
                key: cs.key,
                label: (
                  <span style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{
                      ...mono, border: '1px solid rgba(212,175,55,.5)', color: '#D4AF37',
                      borderRadius: 6, padding: '0 6px', fontSize: '0.68rem', fontWeight: 700,
                    }}>{cs.label}</span>
                    <span style={{ fontSize: '0.72rem', opacity: 0.75 }}>{cs.shortName}</span>
                    <FulfilPill pct={cs.coveragePct} />
                  </span>
                ),
                children: (
                  <div>
                    <div style={{ marginBottom: 6 }}>
                      <Chip label="System Code" value={cs.key} />
                      <Chip label="Short Name" value={cs.shortName || '—'} />
                      <Chip label="SQM Total" value={nf(cs.sqm, 1)} />
                      <Chip label="Coverage SQM" value={nf(cs.canSqm, 1)} />
                      <Chip label="SQM Deficit" value={nf(cs.shortSqm, 1)} />
                    </div>
                    <Table<BalanceRow> sticky={{ offsetHeader: 64 }} size="small" rowKey="Material_Code"
                      columns={balanceCols} pagination={false}
                      scroll={{ x: 'max-content' }}
                      dataSource={[...bal.rows].sort((a, b) => a.Coverage_Pct - b.Coverage_Pct)}
                      onRow={(r) => ({ style: { background: fcBg(r.Coverage_Pct) } })} />
                  </div>
                ),
              }
            })} />
          </Card>
        )
      })}

      {/* Grand total across all filtered materials */}
      <div style={{
        border: '1px solid rgba(212,175,55,.45)', background: 'rgba(212,175,55,.07)',
        borderRadius: 8, padding: '8px 12px', display: 'flex', gap: 20, flexWrap: 'wrap',
        alignItems: 'center', fontSize: '0.78rem', marginTop: 14,
      }}>
        <span>Materials: <b style={mono}>{overall.rows.length}</b></span>
        <span>Total demand: <b style={mono}>{nf(overall.totals.demand)}</b></span>
        <span>Shortfall: <b style={{ ...mono, color: overall.totals.shortfall > 0 ? '#EF4444' : undefined }}>{nf(overall.totals.shortfall)}</b></span>
        <span>Net shortfall (after on-order): <b style={mono}>{nf(overall.totals.netShortfall)}</b></span>
        <span style={{ marginLeft: 'auto' }}>
          Coverage: <FulfilPill pct={Math.min(overall.totals.coveragePct, 100)} />
        </span>
      </div>

      {/* legacy Tab-0 procurement downloads: grand total + net order list */}
      <Space wrap style={{ marginTop: 10 }}>
        <span style={{ fontSize: '0.72rem', opacity: 0.7 }}>Grand Procurement:</span>
        <RowsExportButtons doc={() => ({
          title: 'Procurement — Grand Total',
          filenameStem: 'procurement_grand_total',
          columns: procCols,
          rows: overall.rows.map(procRow),
        })} />
        {overall.rows.some((r) => r.Net_Shortfall > 0) && (
          <>
            <span style={{ fontSize: '0.72rem', opacity: 0.7, marginLeft: 12 }}>Net Order List:</span>
            <RowsExportButtons doc={() => ({
              title: 'Net Order List (shortages after on-order)',
              filenameStem: 'net_order_list',
              columns: procCols,
              rows: overall.rows.filter((r) => r.Net_Shortfall > 0).map(procRow),
            })} />
          </>
        )}
      </Space>
    </div>
  )
}
