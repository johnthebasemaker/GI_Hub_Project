/**
 * frontend/src/sme/SuggestionPanel.tsx — predictive suggestion engine panel
 * (Phase S3). Runs the parity-locked simulation loop (engine.ts
 * runSuggestionEngine — pause each incomplete equipment, re-cascade the rest)
 * ENTIRELY client-side, so what took a Streamlit rerun now updates the moment
 * the priority order changes. "Apply" removes the tag from the session — a
 * reversible React-native upgrade over the legacy display-only panel.
 */
import { useMemo } from 'react'
import { Button, Card, Collapse, Table, Tag, Typography } from 'antd'
import { BulbOutlined, PauseCircleOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { runSuggestionEngine } from './engine'
import type { SmeModel, SuggestionRow } from './engine'

export default function SuggestionPanel({ model, order, onPause }: {
  model: SmeModel
  order: string[]
  onPause: (tag: string) => void
}) {
  const result = useMemo(
    () => (order.length >= 2 ? runSuggestionEngine(model, order) : null),
    [model, order])
  if (!result || result.suggestions.length === 0) return null

  const best = result.suggestions[0]
  const columns: ColumnsType<SuggestionRow> = [
    {
      title: 'Pause', dataIndex: 'Pause_Tag', key: 't',
      render: (v: string, r) => (
        <span>
          <Typography.Text strong style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: '0.78rem' }}>{v}</Typography.Text>
          {r.Recommended && <Tag color="gold" style={{ marginLeft: 6 }}>⭐ recommended</Tag>}
        </span>
      ),
    },
    { title: 'Newly completable', dataIndex: 'Newly_Completable_Count', key: 'n', align: 'right' },
    { title: 'Unlocks', dataIndex: 'Newly_Completable_Tags', key: 'u', ellipsis: true },
    {
      title: 'Avg gain', dataIndex: 'Avg_Completion_Gain_Pct', key: 'g', align: 'right',
      render: (v: number) => <span style={{ color: v > 0 ? '#10B981' : undefined }}>{v > 0 ? '+' : ''}{v.toFixed(2)}%</span>,
    },
    {
      title: '', key: 'a', width: 90,
      render: (_, r) => (
        <Button size="small" icon={<PauseCircleOutlined />} onClick={() => onPause(r.Pause_Tag)}>
          Apply
        </Button>
      ),
    },
  ]

  return (
    <Card size="small" style={{ marginTop: 16 }}
      title={<span><BulbOutlined style={{ color: '#D4AF37' }} /> Smart Suggestions — what if you paused one?</span>}>
      <Typography.Paragraph style={{ marginTop: 0 }}>
        {best.Newly_Completable_Count > 0 ? (
          <>⭐ Pausing <Typography.Text strong code>{best.Pause_Tag}</Typography.Text> makes{' '}
            <Typography.Text strong>{best.Newly_Completable_Count}</Typography.Text> other
            equipment fully completable ({best.Newly_Completable_Tags}) with an average
            completion gain of +{best.Avg_Completion_Gain_Pct.toFixed(1)}%.</>
        ) : (
          <>No pause makes another equipment fully completable with current stock —
            the best option, pausing <Typography.Text strong code>{best.Pause_Tag}</Typography.Text>,
            still yields the highest average completion gain
            (+{best.Avg_Completion_Gain_Pct.toFixed(1)}%).</>
        )}
      </Typography.Paragraph>
      <Collapse ghost size="small" items={[{
        key: 'all',
        label: `All ${result.suggestions.length} simulated scenarios`,
        children: (
          <Table size="small" rowKey="Pause_Tag" columns={columns}
            dataSource={result.suggestions} pagination={{ pageSize: 8 }}
            scroll={{ x: 'max-content' }} />
        ),
      }]} />
    </Card>
  )
}
