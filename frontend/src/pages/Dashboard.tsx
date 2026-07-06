import { useState } from 'react'
import { App, Card, Col, Collapse, Input, Row, Table, Typography } from 'antd'
import { CloudServerOutlined, EnvironmentOutlined, InboxOutlined, SearchOutlined, WarningOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { api } from '../api/client'
import { useHealth, useInventorySummary, useList, useSites } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'
import BrowseTable from '../components/BrowseTable'
import KpiCard from '../components/KpiCard'
import { status } from '../theme/tokens'

interface CatRow {
  Category: string | null
  count: number
}

const catColumns: ColumnsType<CatRow> = [
  { title: 'Category', dataIndex: 'Category', key: 'Category', render: (v) => v ?? '—' },
  { title: 'Items', dataIndex: 'count', key: 'count', align: 'right', width: 100 },
]

// --- 🤖 NL→SQL search (Phase AI-5) — unscoped roles only (logistics/admin) -----
// Generated SQL runs on a TRUE read-only PG login behind the safety gate;
// scoped roles are excluded in V1 because generated SQL can't be site-pinned.
interface NlResult { ok: boolean; message: string; sql: string; columns: string[]; rows: unknown[][] }

function NlSearchCard() {
  const { message } = App.useApp()
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<NlResult | null>(null)

  const ask = async () => {
    setBusy(true)
    try {
      const r = await api.post<NlResult>('/ai/nl-search', { question: q })
      setRes(r.data)
      if (!r.data.ok) message.warning(r.data.message)
    } catch (e) {
      const x = e as { response?: { data?: { detail?: string } } }
      message.error(x?.response?.data?.detail ?? 'Search failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="🤖 Ask in plain English" size="small" style={{ marginTop: 16 }}>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        e.g. “items below minimum stock”, “top suppliers by quantity in the last
        90 days”. The AI writes a read-only SQL query — you can inspect exactly
        what ran.
      </Typography.Paragraph>
      <Input.Search placeholder="Ask about the inventory, receipts, consumption, PRs, POs…"
        value={q} onChange={(e) => setQ(e.target.value)} enterButton={<><SearchOutlined /> Search</>}
        loading={busy} onSearch={ask} allowClear />
      {res?.ok && (
        <>
          <Table size="small" style={{ marginTop: 12 }}
            dataSource={res.rows.map((r, i) => ({ __k: i, ...Object.fromEntries(res.columns.map((c, j) => [c, r[j]])) }))}
            columns={res.columns.map((c) => ({ title: c, dataIndex: c, key: c, ellipsis: true }))}
            rowKey="__k" scroll={{ x: 'max-content' }}
            pagination={{ pageSize: 10, showTotal: (t) => `${t} rows` }} />
          <Collapse ghost size="small" style={{ marginTop: 4 }}
            items={[{ key: 'sql', label: 'Show SQL', children:
              <pre style={{ margin: 0, fontSize: 12, whiteSpace: 'pre-wrap' }}>{res.sql}</pre> }]} />
        </>
      )}
      {res && !res.ok && res.sql && (
        <Collapse ghost size="small" style={{ marginTop: 8 }}
          items={[{ key: 'sql', label: 'Show rejected SQL', children:
            <pre style={{ margin: 0, fontSize: 12, whiteSpace: 'pre-wrap' }}>{res.sql}</pre> }]} />
      )}
    </Card>
  )
}

export default function Dashboard() {
  const { data: health } = useHealth()
  const { data: summary } = useInventorySummary()
  const { data: sites } = useSites()
  const { user } = useAuth()
  const expiring = useList('/stock/expiring', { limit: 1 })
  const expiringCount = expiring.data?.total ?? 0

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Dashboard
      </Typography.Title>

      <Row gutter={[16, 16]} className="gi-cascade">
        <Col xs={12} md={6}>
          <KpiCard
            title="Inventory items"
            value={summary?.total_items ?? 0}
            icon={<InboxOutlined />}
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            title="Sites"
            value={sites?.length ?? 0}
            icon={<EnvironmentOutlined />}
            tint={status.info}
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            title="Expiring / expired lots"
            value={expiringCount}
            icon={<WarningOutlined />}
            tint={expiringCount > 0 ? status.critical : status.ok}
            valueColor={expiringCount > 0 ? status.critical : undefined}
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            title="Database"
            value={health ? health.dialect : '—'}
            icon={<CloudServerOutlined />}
            tint={status.ok}
          />
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }} className="gi-cascade">
        <Col xs={24} lg={10}>
          <Card title="Inventory by category" size="small">
            <Table<CatRow>
              size="small"
              columns={catColumns}
              dataSource={summary?.by_category ?? []}
              rowKey={(r) => String(r.Category)}
              pagination={false}
              scroll={{ y: 320 }}
            />
          </Card>
        </Col>
        <Col xs={24} lg={14}>
          <Card title="Expiring & expired stock" size="small">
            <BrowseTable path="/stock/expiring" hasSite />
          </Card>
        </Col>
      </Row>

      {(user?.level ?? 0) >= 3 && <NlSearchCard />}
    </div>
  )
}
