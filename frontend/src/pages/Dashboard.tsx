import { Card, Col, Row, Statistic, Table, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useHealth, useInventorySummary, useList, useSites } from '../api/hooks'
import BrowseTable from '../components/BrowseTable'

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

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Dashboard
      </Typography.Title>

      <Row gutter={[16, 16]}>
        <Col xs={12} md={6}>
          <Card>
            <Statistic title="Inventory items" value={summary?.total_items ?? 0} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card>
            <Statistic title="Sites" value={sites?.length ?? 0} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card>
            <Statistic
              title="Expiring / expired lots"
              value={expiring.data?.total ?? 0}
              styles={{ content: { color: (expiring.data?.total ?? 0) > 0 ? '#cf1322' : undefined } }}
            />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card>
            <Statistic
              title="Database"
              value={health ? health.dialect : '—'}
              styles={{ content: { fontSize: 20 } }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
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
