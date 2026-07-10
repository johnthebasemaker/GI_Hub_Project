import { useState } from 'react'
import { InputNumber, Space, Tabs, Typography } from 'antd'
import { useAuth } from '../auth/AuthContext'
import BrowseTable from '../components/BrowseTable'

function ExpiringTable() {
  const [withinDays, setWithinDays] = useState<number | null>(null)
  return (
    <BrowseTable
      path="/stock/expiring"
      hasSite
      searchable
      hasCategory
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
  const { user } = useAuth()
  // The global (cross-site) view is restricted to logistics/admin — the server
  // 403s it for site-scoped users, so don't offer the tab at all.
  const global = (user?.level ?? 0) >= 3
  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Stock (derived)
      </Typography.Title>
      <Tabs
        defaultActiveKey={global ? 'live' : 'by-site'}
        items={[
          ...(global
            ? [{ key: 'live', label: 'Live (global)', children: <BrowseTable path="/stock/live" searchable hasCategory /> }]
            : []),
          { key: 'by-site', label: 'By site', children: <BrowseTable path="/stock/by-site" hasSite searchable hasCategory /> },
          { key: 'lots', label: 'Lot balances', children: <BrowseTable path="/stock/lots" hasSite searchable hasCategory /> },
          { key: 'expiring', label: 'Expiring', children: <ExpiringTable /> },
        ]}
      />
    </div>
  )
}
