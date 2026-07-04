import type { ReactNode } from 'react'
import { Card, Typography } from 'antd'
import { useCountUp } from '../lib/useCountUp'
import { brand } from '../theme/tokens'

interface Props {
  title: string
  value: number | string
  icon: ReactNode
  /** Accent for the icon chip — a 6-digit hex (gets an alpha suffix). */
  tint?: string
  valueColor?: string
}

// Dashboard stat card: tinted icon chip + count-up number. The gold hairline
// and hover lift come from .gi-kpi in index.css.
export default function KpiCard({ title, value, icon, tint = brand.gold, valueColor }: Props) {
  const numeric = typeof value === 'number'
  const counted = useCountUp(numeric ? value : 0)
  return (
    <Card className="gi-kpi">
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <div
          style={{
            width: 42,
            height: 42,
            borderRadius: 12,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 19,
            flex: '0 0 auto',
            color: tint,
            background: `${tint}22`,
          }}
        >
          {icon}
        </div>
        <div style={{ minWidth: 0 }}>
          <Typography.Text
            type="secondary"
            style={{
              fontSize: 13,
              display: 'block',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {title}
          </Typography.Text>
          <div style={{ fontSize: 26, fontWeight: 650, lineHeight: 1.3, color: valueColor }}>
            {numeric ? counted.toLocaleString() : value}
          </div>
        </div>
      </div>
    </Card>
  )
}
