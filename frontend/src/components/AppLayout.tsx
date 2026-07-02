import { Layout, Menu, Tag, Typography } from 'antd'
import type { MenuProps } from 'antd'
import { DashboardOutlined, FormOutlined, StockOutlined } from '@ant-design/icons'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useHealth } from '../api/hooks'
import { READ_ENTITIES, WRITE_ENTITIES } from '../config/entities'

const { Header, Sider, Content } = Layout

const menuItems: MenuProps['items'] = [
  { key: '/', icon: <DashboardOutlined />, label: 'Dashboard' },
  { key: '/stock', icon: <StockOutlined />, label: 'Stock' },
  {
    key: 'entry',
    label: 'Data Entry',
    type: 'group',
    children: [{ key: '/entry/receive', icon: <FormOutlined />, label: 'Receive Stock' }],
  },
  {
    key: 'records',
    label: 'Records',
    type: 'group',
    children: READ_ENTITIES.map((e) => ({ key: `/records/${e.key}`, label: e.label })),
  },
  {
    key: 'master',
    label: 'Master Data',
    type: 'group',
    children: WRITE_ENTITIES.map((e) => ({ key: `/master/${e.key}`, label: e.label })),
  },
]

export default function AppLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const { data: health } = useHealth()

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
          items={menuItems}
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
          <span>
            {health ? (
              <Tag color="green">
                {health.dialect} · {health.database}
              </Tag>
            ) : (
              <Tag color="red">API offline</Tag>
            )}
          </span>
        </Header>
        <Content style={{ margin: 24 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
