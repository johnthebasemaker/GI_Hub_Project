import { Card, Col, Row, Table, Typography } from 'antd'
import { CloudServerOutlined, EnvironmentOutlined, InboxOutlined, WarningOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useHealth, useInventorySummary, useList, useSites } from '../api/hooks'
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

export default function Dashboard() {
  const { data: health } = useHealth()
  const { data: summary } = useInventorySummary()
  const { data: sites } = useSites()
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
    </div>
  )
}
