import { useState } from 'react'
import { InputNumber, Space, Tabs, Typography } from 'antd'
import BrowseTable from '../components/BrowseTable'

function ExpiringTable() {
  const [withinDays, setWithinDays] = useState<number | null>(null)
  return (
    <BrowseTable
      path="/stock/expiring"
      hasSite
      extraParams={withinDays != null ? { within_days: withinDays } : undefined}
      toolbarExtra={
        <Space>
          <span>Within days:</span>
          <InputNumber
            style={{ width: 120 }}
            placeholder="any"
            value={withinDays}
            onChange={(v) => setWithinDays(v)}
          />
        </Space>
      }
    />
  )
}

export default function StockPage() {
  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Stock (derived)
      </Typography.Title>
      <Tabs
        defaultActiveKey="live"
        items={[
          { key: 'live', label: 'Live (global)', children: <BrowseTable path="/stock/live" /> },
          { key: 'by-site', label: 'By site', children: <BrowseTable path="/stock/by-site" hasSite /> },
          { key: 'lots', label: 'Lot balances', children: <BrowseTable path="/stock/lots" hasSite /> },
          { key: 'expiring', label: 'Expiring', children: <ExpiringTable /> },
        ]}
      />
    </div>
  )
}
