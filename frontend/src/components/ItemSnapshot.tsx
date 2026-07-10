/**
 * frontend/src/components/ItemSnapshot.tsx — Phase 1 entry-form snapshot.
 * Shows current stock + 30-day burn/daily-rate + a days-of-cover warning and a
 * sparkline for the selected material (legacy render_item_snapshot parity).
 * Advisory only — never blocks entry (FEFO/negative-stock stay allow-and-log).
 */
import { Skeleton, Tag, Tooltip, Typography } from 'antd'
import { useItemSnapshot } from '../api/hooks'
import Sparkline from './Sparkline'

function Metric({ label, value, suffix, tone }: {
  label: string; value: string; suffix?: string; tone?: 'default' | 'danger'
}) {
  return (
    <div style={{ minWidth: 92 }}>
      <div style={{ fontSize: 10, letterSpacing: 0.4, textTransform: 'uppercase', opacity: 0.6 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 600, color: tone === 'danger' ? '#EF4444' : undefined }}>
        {value}{suffix ? <span style={{ fontSize: 11, opacity: 0.6 }}> {suffix}</span> : null}
      </div>
    </div>
  )
}

export default function ItemSnapshot({ sap, site }: { sap?: string; site?: string }) {
  const { data, isFetching, isError } = useItemSnapshot(sap, site)
  if (!sap) return null
  if (isFetching && !data) return <Skeleton.Input active size="small" style={{ width: 320 }} />
  if (isError) {
    // Never fail silently (UAT: "stock/trend not rendering") — say why.
    return (
      <Typography.Text type="warning" style={{ display: 'block', marginBottom: 12, fontSize: 12 }}>
        Could not load the stock snapshot for {sap} — check the API is running the latest build.
      </Typography.Text>
    )
  }
  if (!data) return null
  const low = data.days_cover != null && data.days_cover < 14
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 20, flexWrap: 'wrap',
      padding: '10px 14px', borderRadius: 8,
      background: 'var(--gi-snapshot-bg, rgba(0,31,64,0.04))', marginBottom: 12,
    }}>
      <Metric label="Current stock" value={`${data.current_stock}`} suffix={data.uom ?? ''} />
      <Metric label="30-day used" value={`${data.total_30d}`} suffix={data.uom ?? ''} />
      <Metric label="Daily rate" value={`${data.mean_daily_qty}`} suffix={`${data.uom ?? ''}/day`} />
      <Metric label="Days cover" value={data.days_cover == null ? '—' : `${data.days_cover}`}
        tone={low ? 'danger' : 'default'} />
      {low && (
        <Tooltip title="Under 14 days of cover at the current burn rate — advisory only, issuing is not blocked.">
          <Tag color="warning">low cover</Tag>
        </Tooltip>
      )}
      <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
        <Sparkline data={data.trend.map((t) => t.consumed)} />
        <Typography.Text type="secondary" style={{ fontSize: 10 }}>30-day trend</Typography.Text>
      </div>
    </div>
  )
}
