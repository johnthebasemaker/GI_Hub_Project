import { Button, Layout, Menu, Space, Tag, Typography } from 'antd'
import type { MenuProps } from 'antd'
import { AuditOutlined, CarOutlined, DashboardOutlined, FireOutlined, FormOutlined, InboxOutlined, LogoutOutlined, ProfileOutlined, StockOutlined } from '@ant-design/icons'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useHealth } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'
import { READ_ENTITIES, WRITE_ENTITIES } from '../config/entities'

const { Header, Sider, Content } = Layout

// Nav is role-gated by the signed-in user's hierarchy level (admin 4 … store_keeper 0)
// plus exact-role gates for the parallel-ladder portals (warehouse).
function buildMenu(level: number, role: string): MenuProps['items'] {
  const items: MenuProps['items'] = [
    { key: '/', icon: <DashboardOutlined />, label: 'Dashboard' },
    { key: '/stock', icon: <StockOutlined />, label: 'Stock' },
    {
      key: 'entry',
      label: 'Data Entry',
      type: 'group',
      children: [
        { key: '/entry/receive', icon: <FormOutlined />, label: 'Receive Stock' },
        { key: '/entry/issue', icon: <FormOutlined />, label: 'Issue Stock' },
        { key: '/entry/return', icon: <FormOutlined />, label: 'Return Stock' },
        { key: '/entry/adjust', icon: <FormOutlined />, label: 'Stock Adjustment' },
        { key: '/site/incoming', icon: <InboxOutlined />, label: 'Incoming Deliveries' },
      ],
    },
    {
      key: 'records',
      label: 'Records',
      type: 'group',
      children: READ_ENTITIES.map((e) => ({ key: `/records/${e.key}`, label: e.label })),
    },
  ]
  // HOD portal — approvals + burn-rate — hod & admin (level ≥ 2).
  if (level >= 2) {
    items.push({
      key: 'hod',
      label: 'HOD',
      type: 'group',
      children: [
        { key: '/hod/approvals', icon: <AuditOutlined />, label: 'Approvals' },
        { key: '/hod/burn-rate', icon: <FireOutlined />, label: 'Burn Rate' },
        { key: '/hod/prs', icon: <ProfileOutlined />, label: 'Purchase Requests' },
      ],
    })
  }
  // Logistics portal — PR queue → PO → assign — logistics & admin (level ≥ 3).
  if (level >= 3) {
    items.push({
      key: 'logistics',
      label: 'Logistics',
      type: 'group',
      children: [{ key: '/logistics', icon: <CarOutlined />, label: 'Procurement' }],
    })
  }
  // Warehouse portal — warehouse_user / logistics / admin (exact roles).
  if (['warehouse_user', 'logistics', 'admin'].includes(role)) {
    items.push({
      key: 'warehouse',
      label: 'Warehouse',
      type: 'group',
      children: [{ key: '/warehouse', icon: <InboxOutlined />, label: 'Receiving & DN' }],
    })
  }
  // Master data (vendors/warehouses/employees) — admin & logistics only.
  if (level >= 3) {
    items.push({
      key: 'master',
      label: 'Master Data',
      type: 'group',
      children: WRITE_ENTITIES.map((e) => ({ key: `/master/${e.key}`, label: e.label })),
    })
  }
  return items
}

export default function AppLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const { data: health } = useHealth()
  const { user, logout } = useAuth()
  const level = user?.level ?? 0

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={220} theme="light" style={{ borderRight: '1px solid #f0f0f0' }}>
        <div style={{ padding: '18px 16px 8px' }}>
          <Typography.Title level={4} style={{ margin: 0 }}>
            GI&nbsp;Hub
          </Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            ERP Console
          </Typography.Text>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={buildMenu(level, user?.role ?? '')}
          onClick={({ key }) => navigate(key)}
          style={{ borderInlineEnd: 'none' }}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            background: '#fff',
            borderBottom: '1px solid #f0f0f0',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            paddingInline: 24,
          }}
        >
          <Typography.Text strong>Warehouse & Inventory</Typography.Text>
          <Space size="middle">
            {health ? (
              <Tag color="green">
                {health.dialect} · {health.database}
              </Tag>
            ) : (
              <Tag color="red">API offline</Tag>
            )}
            {user && (
              <Typography.Text type="secondary">
                {user.label} · {user.username}
              </Typography.Text>
            )}
            <Button size="small" icon={<LogoutOutlined />} onClick={logout}>
              Sign out
            </Button>
          </Space>
        </Header>
        <Content style={{ margin: 24 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
