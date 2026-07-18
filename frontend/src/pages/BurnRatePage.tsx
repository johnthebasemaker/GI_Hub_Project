import { useState } from 'react'
import { InputNumber, Select, Space, Table, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useBurnRate, useSites } from '../api/hooks'
import type { Row } from '../api/client'

const columns: ColumnsType<Row> = [
  { title: 'SAP_Code', dataIndex: 'SAP_Code', key: 'SAP_Code' },
  { title: 'Consumed', dataIndex: 'Consumed', key: 'Consumed', align: 'right' },
  { title: 'Daily avg', dataIndex: 'Daily_Avg', key: 'Daily_Avg', align: 'right' },
]

export default function BurnRatePage() {
  const { data: sites } = useSites()
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
        <Select
          allowClear
          placeholder="All sites"
          style={{ width: 180 }}
          value={siteId}
          onChange={setSiteId}
          options={(sites ?? []).map((s) => ({ value: s, label: s }))}
        />
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
