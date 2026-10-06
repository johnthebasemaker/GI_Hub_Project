import { lazy, Suspense } from 'react'
import { Card, Col, Empty, Row, Tag, Tooltip, Typography } from 'antd'
import { Table } from '../lib/smartTable'
import { DollarOutlined, EnvironmentOutlined, InboxOutlined, WarningOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from 'recharts'
import { useDashboardMetrics, useInventorySummary, useList, useSites } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'
import AskDataCard from '../components/AskDataCard'
import BrowseTable from '../components/BrowseTable'
import KpiCard from '../components/KpiCard'
import KpiRow from '../components/KpiRow'
import { brand, status } from '../theme/tokens'

interface CatRow {
  Category: string | null
  count: number
}

const nf = (v: number) => v.toLocaleString('en-US')

/** Share of the master this category holds — the counts alone don't say whether
 *  "Chemicals: 84" is most of the inventory or a rounding error. */
function catColumnsFor(total: number): ColumnsType<CatRow> {
  return [
    { title: 'Category', dataIndex: 'Category', key: 'Category', render: (v) => v ?? '—' },
    { title: 'Items', dataIndex: 'count', key: 'count', align: 'right', width: 90 },
    {
      title: 'Share', dataIndex: 'count', key: 'share', align: 'right', width: 90,
      render: (v: number) => (
        <Typography.Text type="secondary" style={{ fontVariantNumeric: 'tabular-nums' }}>
          {total > 0 ? `${((v / total) * 100).toFixed(1)}%` : '—'}
        </Typography.Text>
      ),
    },
  ]
}

// Phase 18 Track 4 — lazy, so the reorder module never reaches the entry
// chunk's preload map (the login critical path may not grow by a byte).
const ReorderMini = lazy(() => import('../components/ReorderSignals').then((m) => ({ default: m.ReorderMini })))

export default function Dashboard() {
  const { data: summary } = useInventorySummary()
  const { data: sites } = useSites()
  const { user } = useAuth()
  const expiring = useList('/stock/expiring', { limit: 1 })
  const expiringCount = expiring.data?.total ?? 0
  const { data: metrics } = useDashboardMetrics()

  const byCategory = summary?.by_category ?? []
  const catTotal = byCategory.reduce((s, r) => s + (r.count ?? 0), 0)
  const catColumns = catColumnsFor(catTotal)

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Dashboard
      </Typography.Title>

      {/* Four cards, one clean row. The fifth used to be a "Database: online"
          card stranded on a row of its own — it read the same `useHealth()` as
          the header's "API online" chip, which is on every page, not just this
          one. A KPI slot is worth more than a duplicated status light. */}
      <KpiRow className="gi-cascade">
        <KpiCard
          title="Inventory items"
          value={summary?.total_items ?? 0}
          icon={<InboxOutlined />}
        />
        <KpiCard
          title="Stock value (SAR)"
          value={metrics ? Math.round(metrics.valuation_total).toLocaleString() : '—'}
          icon={<DollarOutlined />}
          tint={brand.gold}
        />
        <KpiCard
          title="Sites"
          value={sites?.length ?? 0}
          icon={<EnvironmentOutlined />}
          tint={status.info}
        />
        <KpiCard
          title="Expiring / expired lots"
          value={expiringCount}
          icon={<WarningOutlined />}
          tint={expiringCount > 0 ? status.critical : status.ok}
          valueColor={expiringCount > 0 ? status.critical : undefined}
        />
      </KpiRow>

      {/* Phase 5 — legacy visual parity: stock-vs-min, burn forecast, top-consumed. */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }} className="gi-cascade">
        <Col xs={24} lg={8}>
          <Card title="Stock vs Minimum · reorder signals" size="small">
            {metrics?.stock_vs_min?.length ? (
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={metrics.stock_vs_min} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                  <XAxis dataKey="sap" tick={{ fontSize: 10 }} interval={0} angle={-35} textAnchor="end" height={52} />
                  <YAxis tick={{ fontSize: 10 }} />
                  <RTooltip formatter={(v) => nf(Number(v))} />
                  <Legend />
                  <Bar dataKey="current" name="Current" fill={status.info} />
                  <Bar dataKey="minimum" name="Minimum" fill={status.critical} />
                </BarChart>
              </ResponsiveContainer>
            ) : null}
            {/* Phase 18: the system's own minimums, always — the chart above
                only knows the handful set by hand. */}
            <div style={{ marginTop: metrics?.stock_vs_min?.length ? 12 : 0 }}><Suspense fallback={null}><ReorderMini /></Suspense></div>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="Burn forecast — days of cover" size="small">
            {metrics?.burn_forecast?.length ? (
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={metrics.burn_forecast} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                  <XAxis dataKey="sap" tick={{ fontSize: 10 }} interval={0} angle={-35} textAnchor="end" height={52} />
                  <YAxis tick={{ fontSize: 10 }} />
                  <RTooltip formatter={(v) => nf(Number(v))} />
                  <Bar dataKey="days_remaining" name="Days of cover" fill={brand.gold} />
                </BarChart>
              </ResponsiveContainer>
            ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No consumption in 30 days" />}
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="Top consumed (30 days)" size="small">
            {metrics?.top_consumed?.length ? (
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={metrics.top_consumed} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                  <XAxis dataKey="sap" tick={{ fontSize: 10 }} interval={0} angle={-35} textAnchor="end" height={52} />
                  <YAxis tick={{ fontSize: 10 }} />
                  <RTooltip formatter={(v) => nf(Number(v))} />
                  <Bar dataKey="consumed" name="Consumed" fill={status.ok} />
                </BarChart>
              </ResponsiveContainer>
            ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No consumption in 30 days" />}
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }} className="gi-cascade">
        <Col xs={24} lg={12}>
          {/* A WARNING widget, never a block: FEFO stays allow-and-log by
              standing ruling, so already-expired lots are shown (negative
              days) rather than hidden — those are the ones needing a decision
              today. Phase 21a: only lots with stock LEFT — an issued-out lot is
              not on a shelf, whatever its date. */}
          <Card title="Top 5 expiring lots" size="small">
            {metrics?.top_expiring?.length ? (
              <Table size="small" pagination={false} rowKey={(r) => `${r.lot}|${r.sap}|${r.site}`}
                dataSource={metrics.top_expiring}
                columns={[
                  { title: 'Lot', dataIndex: 'lot', width: 120 },
                  { title: 'SAP', dataIndex: 'sap', width: 90 },
                  { title: 'Material', dataIndex: 'name', ellipsis: true },
                  { title: 'Expires', dataIndex: 'expiry_date', width: 110 },
                  { title: 'Left', dataIndex: 'remaining', width: 90, align: 'right',
                    render: (v: number, r) => <span data-testid="expiring-left">{v} {r.uom}</span> },
                  { title: 'Days', dataIndex: 'days_left', width: 90, align: 'right',
                    render: (v: number) => (
                      <Tag color={v < 0 ? 'red' : v <= 30 ? 'orange' : 'default'}>
                        {v < 0 ? `${Math.abs(v)} overdue` : v}
                      </Tag>) },
                ]} />
            ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No dated lots with stock left" />}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="Highest value on hand"
            extra={metrics?.value_coverage && (
              <Tooltip title="Unit cost is optional on the inventory master, so
                this ranking only covers the items that have one. A total stated
                without that caveat is a wrong number stated confidently.">
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {metrics.value_coverage.priced} of {metrics.value_coverage.total} priced
                </Typography.Text>
              </Tooltip>)}>
            {metrics?.highest_value?.length ? (
              <Table size="small" pagination={false} rowKey={(r) => String(r.sap)}
                dataSource={metrics.highest_value}
                columns={[
                  { title: 'SAP', dataIndex: 'sap', width: 90 },
                  { title: 'Material', dataIndex: 'name', ellipsis: true },
                  { title: 'Qty', dataIndex: 'qty', width: 90, align: 'right',
                    render: (v: number) => nf(Number(v)) },
                  { title: 'Value', dataIndex: 'value', width: 120, align: 'right',
                    render: (v: number) => <b>{nf(Number(v))}</b> },
                ]} />
            ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="No item carries a unit cost yet" />}
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }} className="gi-cascade">
        <Col xs={24} lg={10}>
          <Card title="Inventory by category" size="small">
            <Table<CatRow> sticky={{ offsetHeader: 64 }}
              size="small"
              columns={catColumns}
              dataSource={byCategory}
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

      {/* Phase C — chat-with-your-data: template lane serves HODs (site-pinned
          server-side); the NL→SQL lane still backs unscoped roles. */}
      {(user?.level ?? 0) >= 2 && <AskDataCard />}
    </div>
  )
}
