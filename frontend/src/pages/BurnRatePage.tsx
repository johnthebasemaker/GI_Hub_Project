import { useState } from 'react'
import { InputNumber, Space, Typography } from 'antd'
import { Table } from '../lib/smartTable'
import type { ColumnsType } from 'antd/es/table'
import { useBurnRate } from '../api/hooks'
import type { Row } from '../api/client'
import { SiteFilter } from '../components/SiteField'

const columns: ColumnsType<Row> = [
  { title: 'SAP', dataIndex: 'SAP_Code', key: 'SAP_Code', width: 110 },
  { title: 'Material', dataIndex: 'Material_Code', key: 'Material_Code', width: 130,
    render: (v) => v ?? '—' },
  { title: 'Description', dataIndex: 'Equipment_Description', key: 'd', ellipsis: true },
  { title: 'UOM', dataIndex: 'UOM', key: 'u', width: 70 },
  { title: 'Consumed', dataIndex: 'Consumed', key: 'Consumed', align: 'right', width: 110 },
  { title: 'Daily avg', dataIndex: 'Daily_Avg', key: 'Daily_Avg', align: 'right', width: 110 },
]

export default function BurnRatePage() {
  const [siteId, setSiteId] = useState<string | undefined>(undefined)
  const [days, setDays] = useState(30)
  const { data, isFetching } = useBurnRate(siteId, days)

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Burn Rate
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Consumption by material over a window, with a per-day average — for reorder planning.
      </Typography.Paragraph>

      <Space style={{ marginBottom: 12 }}>
        <SiteFilter style={{ width: 180 }} value={siteId} onChange={setSiteId} />
        <span>Days:</span>
        <InputNumber min={1} max={365} value={days} onChange={(v) => setDays(v ?? 30)} />
        {data?.since && <Typography.Text type="secondary">since {data.since}</Typography.Text>}
      </Space>

      <Table sticky={{ offsetHeader: 64 }}
        size="small"
        loading={isFetching}
        columns={columns}
        dataSource={data?.items ?? []}
        rowKey={(r) => String(r.SAP_Code)}
        pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `${t} materials` }}
      />
    </div>
  )
}
