/**
 * frontend/src/sme/KpiDrill.tsx — clickable KPI card with drill-down modal
 * (Phase S2). Replaces the legacy dbl_click_metric() hack: a single click
 * opens a real AntD modal with the underlying rows.
 */
import { useMemo, useState } from 'react'
import { Card, Modal, Table, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'

export type DrillRow = Record<string, string | number | null | undefined>

export default function KpiDrill({ title, value, delta, deltaColor, help, drillTitle, rows, accent }: {
  title: string
  value: string
  delta?: string
  deltaColor?: string
  help?: string
  drillTitle: string
  rows: DrillRow[]
  accent?: string
}) {
  const [open, setOpen] = useState(false)
  const columns: ColumnsType<DrillRow & { _k: number }> = useMemo(() => {
    const keys = rows.length ? Object.keys(rows[0]) : []
    return keys.map((k) => ({
      title: k, dataIndex: k, key: k, ellipsis: true,
      align: typeof rows[0]?.[k] === 'number' ? 'right' as const : 'left' as const,
    }))
  }, [rows])
  const data = useMemo(() => rows.map((r, i) => ({ ...r, _k: i })), [rows])

  return (
    <>
      <Card size="small" hoverable onClick={() => setOpen(true)}
        styles={{ body: { padding: '10px 14px', minHeight: 86 } }}>
        <div style={{
          fontSize: '0.72rem', fontWeight: 800, letterSpacing: '.04em',
          textTransform: 'uppercase', opacity: 0.75, whiteSpace: 'nowrap',
          overflow: 'hidden', textOverflow: 'ellipsis',
        }} title={help ?? title}>{title}</div>
        <div style={{
          fontFamily: 'JetBrains Mono, monospace', fontSize: '1.45rem', fontWeight: 800,
          color: accent, lineHeight: 1.3, whiteSpace: 'nowrap',
        }}>{value}</div>
        {delta !== undefined && (
          <div style={{ fontSize: '0.72rem', fontFamily: 'JetBrains Mono, monospace', color: deltaColor ?? '#94A3B8' }}>
            {delta}
          </div>
        )}
      </Card>
      <Modal open={open} onCancel={() => setOpen(false)} footer={null} width={820}
        title={drillTitle}>
        {help && <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>{help}</Typography.Paragraph>}
        <Table sticky={{ offsetHeader: 64 }} size="small" columns={columns} dataSource={data} rowKey="_k"
          scroll={{ x: 'max-content', y: 440 }}
          pagination={{ pageSize: 15, showTotal: (t) => `${t} rows` }} />
      </Modal>
    </>
  )
}
