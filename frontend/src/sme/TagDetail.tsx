/**
 * frontend/src/sme/TagDetail.tsx — per-equipment cascade detail (Phase S3).
 * Shared by the Session Builder right panel and the Session Report expanders:
 * meta strip → per-system-code header (dot · code badge · SQM · pill) +
 * material table → amber grand-total box. All values come straight from the
 * client cascade lines, so they update live as priorities shift.
 */
import { Table, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { AllocationLine } from './engine'
import { syscodeCompare } from './engine'
import { fc } from './insights'
import { FulfilPill, StatusDot } from './PriorityList'
import { codeStats } from './session'
import type { TagStat } from './session'

const mono: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }
const nf = (v: number, d = 3) =>
  v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: d })

const matColumns: ColumnsType<AllocationLine> = [
  { title: 'Material', dataIndex: 'Material_Code', key: 'c', width: 120 },
  { title: 'Name', dataIndex: 'Material_Name', key: 'n', ellipsis: true },
  { title: 'UOM', dataIndex: 'UOM', key: 'u', width: 64 },
  { title: 'Demand', dataIndex: 'Demand_Qty', key: 'd', align: 'right', render: (v: number) => nf(v) },
  { title: 'Allocated', dataIndex: 'Allocated_Qty', key: 'a', align: 'right', render: (v: number) => nf(v) },
  {
    title: 'Shortfall', dataIndex: 'Shortfall_Qty', key: 's', align: 'right',
    render: (v: number) => <span style={{ color: v > 0 ? '#EF4444' : undefined, fontWeight: v > 0 ? 700 : 400 }}>{nf(v)}</span>,
  },
  {
    title: 'Fulfillment', dataIndex: 'Fulfillment_Pct', key: 'f', align: 'right', width: 110,
    render: (v: number) => <span style={{ color: fc(v), fontWeight: 700 }}>{v.toFixed(1)}%</span>,
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
              ...mono, border: '1px solid rgba(212,175,55,.5)', color: '#D4AF37',
              borderRadius: 6, padding: '0 6px', fontSize: '0.7rem', fontWeight: 700,
            }}>Code {cs.code}</span>
            <span style={{ fontSize: '0.75rem', opacity: 0.8 }}>{cs.shortName}</span>
            <span style={{ ...mono, fontSize: '0.7rem', opacity: 0.7, marginLeft: 'auto' }}>
              {nf(cs.canSqm, 1)} / {nf(cs.sqm, 1)} SQM
            </span>
            <FulfilPill pct={cs.fulfillPct} />
          </div>
          <Table sticky={{ offsetHeader: 64 }} size="small" rowKey={(r) => `${r.Lining_System_Code}|${r.Material_Code}`}
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
        <span>Allocated: <b style={mono}>{nf(stat.alloc)}</b></span>
        <span style={{ marginLeft: 'auto' }}>Coverage: <FulfilPill pct={stat.fulfillPct} /></span>
      </div>
    </div>
  )
}
