/**
 * frontend/src/sme/TagDetail.tsx — per-equipment cascade detail (Phase S3).
 * Shared by the Session Builder right panel and the Session Report expanders:
 * meta strip → per-system-code header (dot · code badge · SQM · pill) +
 * material table → amber grand-total box. All values come straight from the
 * client cascade lines, so they update live as priorities shift.
 */
import { Typography } from 'antd'
import { Table } from '../lib/smartTable'
import type { ColumnsType } from 'antd/es/table'
import type { AllocationLine } from './engine'
import { syscodeCompare } from './engine'
import { fc } from './insights'
import { FulfilPill, StatusDot } from './PriorityList'
import { codeStats } from './session'
import type { TagStat } from './session'
import { materialCodeCol, materialNameCol } from './materialCols'
import { brand, status } from '../theme/tokens'

const mono: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }
const nf = (v: number, d = 3) =>
  v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: d })

// 2026-08-03 STRICT TIER SEGREGATION: the single "Allocated" column summed
// physical stock and stock on order, sitting next to a physical-only
// "Fulfillment" — two different meanings, one row, no way to tell them apart.
const matColumns: ColumnsType<AllocationLine> = [
  materialCodeCol<AllocationLine>({ width: 150 }),
  materialNameCol<AllocationLine>({ title: 'Name', width: 240 }),
  { title: 'UOM', dataIndex: 'UOM', key: 'u', width: 64 },
  { title: 'Demand', dataIndex: 'Demand_Qty', key: 'd', align: 'right', render: (v: number) => nf(v) },
  {
    title: 'Available', dataIndex: 'Alloc_Available', key: 'av', align: 'right',
    render: (v: number) => <span style={{ color: status.ok }}>{nf(v)}</span>,
  },
  {
    title: 'Pending Delivery', dataIndex: 'Alloc_Pending', key: 'or', align: 'right',
    render: (v: number) => (
      <span style={{ color: v > 0 ? status.low : undefined, opacity: v > 0 ? 1 : 0.4 }}>{nf(v)}</span>
    ),
  },
  {
    title: 'Short (physical)', dataIndex: 'Shortfall_Available_Qty', key: 'sp', align: 'right',
    render: (v: number) => <span style={{ color: v > 0 ? status.critical : undefined, fontWeight: v > 0 ? 700 : 400 }}>{nf(v)}</span>,
  },
  {
    title: 'To buy (net)', dataIndex: 'Shortfall_Qty', key: 's', align: 'right',
    render: (v: number) => <span style={{ color: v > 0 ? status.critical : status.ok }}>{nf(v)}</span>,
  },
  {
    title: 'Ready now', dataIndex: 'Fulfillment_Pct', key: 'f', align: 'right', width: 100,
    render: (v: number) => <span style={{ color: fc(v), fontWeight: 700 }}>{v.toFixed(1)}%</span>,
  },
  {
    title: 'When delivered', dataIndex: 'Fulfillment_With_Ordered_Pct', key: 'fo',
    align: 'right', width: 110,
    render: (v: number, r) => (
      <span style={{ color: v > r.Fulfillment_Pct ? status.low : undefined,
        opacity: v > r.Fulfillment_Pct ? 1 : 0.5 }}>{v.toFixed(1)}%</span>
    ),
  },
]

export default function TagDetail({ lines, stat, preview }: {
  lines: AllocationLine[]  // cascade lines for THIS tag only
  stat: TagStat
  preview?: boolean
}) {
  const perCode = codeStats(lines)
  const codes = [...perCode.values()].sort((a, b) => syscodeCompare(a.code, b.code))
  return (
    <div>
      {preview && (
        <Typography.Paragraph type="warning" style={{ fontSize: '0.75rem', marginTop: 0 }}>
          Preview — not in the session yet; numbers assume it is added at the LAST priority position.
        </Typography.Paragraph>
      )}
      <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', fontSize: '0.75rem', opacity: 0.8, marginBottom: 10 }}>
        <span>Type: <b>{stat.type || '—'}</b></span>
        <span>Substrate: <b>{stat.substrate || '—'}</b></span>
        <span>Location: <b>{stat.location || '—'}</b></span>
        <span>Total SQM: <b style={mono}>{nf(stat.sqm, 1)}</b></span>
      </div>
      {codes.map((cs) => (
        <div key={cs.code} style={{ marginBottom: 12 }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8, padding: '4px 8px',
            borderLeft: `3px solid ${fc(cs.fulfillPct)}`, background: 'rgba(128,128,128,.06)',
            borderRadius: 4, marginBottom: 6,
          }}>
            <StatusDot pct={cs.fulfillPct} />
            <span style={{
              ...mono, border: '1px solid rgba(212,175,55,.5)', color: brand.gold,
              borderRadius: 6, padding: '0 6px', fontSize: '0.7rem', fontWeight: 700,
            }}>Code {cs.code}</span>
            <span style={{ fontSize: '0.75rem', opacity: 0.8 }}>{cs.shortName}</span>
            <span style={{ ...mono, fontSize: '0.7rem', opacity: 0.7, marginLeft: 'auto' }}>
              {nf(cs.canSqm, 1)} / {nf(cs.sqm, 1)} SQM
            </span>
            <FulfilPill pct={cs.fulfillPct} />
            {cs.fulfillWithOrderedPct > cs.fulfillPct && (
              <span style={{ ...mono, fontSize: '0.64rem', color: status.low, whiteSpace: 'nowrap' }}
                title="Coverage once the open purchase orders land — not buildable today">
                → {cs.fulfillWithOrderedPct.toFixed(1)}% ordered
              </span>
            )}
          </div>
          <Table sticky={{ offsetHeader: 64 }} size="small" rowKey={(r) => `${r.Lining_System_Code}|${r.Material_Key}`}
            columns={matColumns} pagination={false} scroll={{ x: 'max-content' }}
            dataSource={lines.filter((l) => l.Lining_System_Code === cs.code)} />
        </div>
      ))}
      <div style={{
        border: '1px solid rgba(212,175,55,.45)', background: 'rgba(212,175,55,.07)',
        borderRadius: 8, padding: '8px 12px', display: 'flex', gap: 20, flexWrap: 'wrap',
        alignItems: 'center', fontSize: '0.78rem',
      }}>
        <span>System codes: <b style={mono}>{codes.length}</b></span>
        <span>Total demand: <b style={mono}>{nf(stat.demand)}</b></span>
        <span>Available: <b style={{ ...mono, color: status.ok }}>{nf(stat.allocAvailable)}</b></span>
        <span>On order: <b style={{ ...mono, color: stat.allocPending > 0 ? status.low : undefined }}>{nf(stat.allocPending)}</b></span>
        <span>To buy: <b style={{ ...mono, color: stat.shortfall > 0 ? status.critical : undefined }}>{nf(stat.shortfall)}</b></span>
        <span style={{ marginLeft: 'auto' }}>
          Ready now: <FulfilPill pct={stat.fulfillPct} />
          {stat.fulfillWithOrderedPct > stat.fulfillPct && (
            <span style={{ ...mono, fontSize: '0.7rem', color: status.low, marginLeft: 6 }}>
              (when delivered {stat.fulfillWithOrderedPct.toFixed(1)}%)
            </span>
          )}
        </span>
      </div>
    </div>
  )
}
