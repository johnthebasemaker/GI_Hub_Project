import { useState } from 'react'
import { App, Card, Collapse, Empty, Input, Space, Statistic, Table, Tag, Typography } from 'antd'
import { SearchOutlined, ThunderboltOutlined, RobotOutlined } from '@ant-design/icons'
import { api } from '../api/client'

/**
 * Phase C — "Chat with your data" (POST /ai/query). Two lanes server-side:
 * deterministic site-scoped SQL templates (instant, works for HODs and with
 * the AI switched off) and the NL→SQL model fallback for unscoped roles.
 * Renders a metric card for count/total questions, a data table otherwise,
 * with the executed SQL inspectable.
 */
interface QueryResult {
  ok: boolean
  mode: 'template' | 'nl'
  intent?: string
  message: string
  sql: string
  columns: string[]
  rows: unknown[][]
  metric?: { label: string; value: number; entries?: number }
  examples?: string[]
}

const DEFAULT_EXAMPLES = [
  'Show me all material returns from last week',
  'How many issues in the last 30 days?',
  'Items below minimum stock',
  'Top suppliers by received quantity last 90 days',
]

export default function AskDataCard() {
  const { message } = App.useApp()
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<QueryResult | null>(null)

  const ask = async (question?: string) => {
    const text = (question ?? q).trim()
    if (!text) return
    if (question) setQ(question)
    setBusy(true)
    try {
      const r = await api.post<QueryResult>('/ai/query', { question: text })
      setRes(r.data)
      if (!r.data.ok) message.info(r.data.message)
    } catch (e) {
      const x = e as { response?: { data?: { detail?: string } } }
      message.error(x?.response?.data?.detail ?? 'Query failed')
    } finally {
      setBusy(false)
    }
  }

  const examples = res?.examples ?? DEFAULT_EXAMPLES

  return (
    <Card size="small" style={{ marginTop: 16 }}
      title={<Space size={8}><RobotOutlined /> Ask your data</Space>}
      extra={res && (
        <Tag icon={res.mode === 'template' ? <ThunderboltOutlined /> : <RobotOutlined />}
          color={res.mode === 'template' ? 'gold' : 'blue'}>
          {res.mode === 'template' ? 'instant' : 'AI-generated SQL'}
        </Tag>
      )}>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        Plain-English questions about stock, receipts, issues, returns, PRs and
        POs — answered read-only from the live database. Site-scoped roles only
        ever see their own site.
      </Typography.Paragraph>
      <Input.Search
        placeholder="e.g. Show me all material returns from last week"
        value={q} onChange={(e) => setQ(e.target.value)}
        enterButton={<><SearchOutlined /> Ask</>}
        loading={busy} onSearch={() => ask()} allowClear />
      <Space size={[6, 6]} wrap style={{ marginTop: 8 }}>
        {examples.map((ex) => (
          <Tag key={ex} style={{ cursor: 'pointer' }} onClick={() => void ask(ex)}>{ex}</Tag>
        ))}
      </Space>

      {res?.ok && res.metric && (
        <Card size="small" style={{ marginTop: 12, maxWidth: 340 }}>
          <Statistic title={res.metric.label} value={res.metric.value}
            suffix={res.metric.entries != null ? `· ${res.metric.entries} entr${res.metric.entries === 1 ? 'y' : 'ies'}` : undefined} />
        </Card>
      )}
      {res?.ok && !res.metric && (
        res.rows.length ? (
          <Table size="small" style={{ marginTop: 12 }}
            dataSource={res.rows.map((r, i) => ({ __k: i, ...Object.fromEntries(res.columns.map((c, j) => [c, r[j]])) }))}
            columns={res.columns.map((c) => ({
              title: c.replace(/_/g, ' '), dataIndex: c, key: c, ellipsis: true,
              render: (v: unknown) => (v == null || v === '' ? '—' : String(v)),
            }))}
            rowKey="__k" scroll={{ x: 'max-content' }}
            pagination={{ pageSize: 10, showTotal: (t) => `${t} rows` }} />
        ) : (
          <Empty style={{ marginTop: 12 }} description={`No rows — ${res.message}`} />
        )
      )}
      {res?.ok && (
        <Collapse ghost size="small" style={{ marginTop: 4 }}
          items={[{ key: 'sql', label: `Show SQL · ${res.message}`, children:
            <pre style={{ margin: 0, fontSize: 12, whiteSpace: 'pre-wrap' }}>{res.sql}</pre> }]} />
      )}
      {res && !res.ok && res.sql && (
        <Collapse ghost size="small" style={{ marginTop: 8 }}
          items={[{ key: 'sql', label: 'Show rejected SQL', children:
            <pre style={{ margin: 0, fontSize: 12, whiteSpace: 'pre-wrap' }}>{res.sql}</pre> }]} />
      )}
    </Card>
  )
}
