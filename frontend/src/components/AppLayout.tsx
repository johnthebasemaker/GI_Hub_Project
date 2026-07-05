import { Suspense } from 'react'
import type { ReactNode } from 'react'
import { Badge, Button, ConfigProvider, Layout, Menu, Skeleton, Space, Tooltip, Typography } from 'antd'
import type { MenuProps } from 'antd'
import { AuditOutlined, BarChartOutlined, CarOutlined, DashboardOutlined, DatabaseOutlined, ExperimentOutlined, FallOutlined, FireOutlined, FileSearchOutlined, FormOutlined, InboxOutlined, LogoutOutlined, MoonOutlined, ProfileOutlined, SafetyCertificateOutlined, SolutionOutlined, StockOutlined, SunOutlined, TeamOutlined, ToolOutlined, UserAddOutlined } from '@ant-design/icons'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useHealth, useWorkQueues } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'
import { READ_ENTITIES, WRITE_ENTITIES } from '../config/entities'
import { useThemeMode } from '../theme/ThemeContext'
import { siderTheme } from '../theme/themes'
import NotificationBell from './NotificationBell'

const { Header, Sider, Content } = Layout

// Nav label + live work-queue count (gold badge = work waiting for you).
function withCount(label: string, count?: number): ReactNode {
  if (!count) return label
  return (
    <span className="gi-nav-flex">
      {label}
      <Badge
        count={count}
        size="small"
        overflowCount={99}
        style={{ backgroundColor: 'var(--gi-gold)', color: '#001F40', fontWeight: 600 }}
      />
    </span>
  )
}

// Nav is role-gated by the signed-in user's hierarchy level (admin 4 … store_keeper 0)
// plus exact-role gates for the parallel-ladder portals (warehouse).
function buildMenu(level: number, role: string, q: Record<string, number>): MenuProps['items'] {
  const items: MenuProps['items'] = [
    { key: '/', icon: <DashboardOutlined />, label: 'Dashboard' },
    { key: '/stock', icon: <StockOutlined />, label: 'Stock' },
    // Data entry is the store keeper's job — exact-locked like the legacy
    // Entry Log page (the API 403s staging writes for other roles anyway).
    ...(['store_keeper', 'admin'].includes(role)
      ? [{
          key: 'entry',
          label: 'Data Entry',
          type: 'group' as const,
          children: [
            { key: '/entry/receive', icon: <FormOutlined />, label: 'Receive Stock' },
            { key: '/entry/issue', icon: <FormOutlined />, label: 'Issue Stock' },
            { key: '/entry/return', icon: <FormOutlined />, label: 'Return Stock' },
            { key: '/entry/adjust', icon: <FormOutlined />, label: 'Stock Adjustment' },
            { key: '/entry/count', icon: <FormOutlined />, label: 'Stock Count' },
            { key: '/entry/returnables', icon: <ToolOutlined />, label: withCount('Returnable Items', q.returnables_overdue) },
            { key: '/site/incoming', icon: <InboxOutlined />, label: withCount('Incoming Deliveries', q.incoming_dns) },
            { key: '/sk/requests', icon: <SolutionOutlined />, label: withCount('Supervisor Requests', q.sk_requests) },
          ],
        }]
      : []),
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
        { key: '/hod/approvals', icon: <AuditOutlined />, label: withCount('Approvals', q.approvals) },
        { key: '/hod/burn-rate', icon: <FireOutlined />, label: 'Burn Rate' },
        { key: '/hod/low-stock', icon: <FallOutlined />, label: 'Low Stock' },
        { key: '/hod/prs', icon: <ProfileOutlined />, label: 'Purchase Requests' },
      ],
    })
    items.push({
      key: 'sme',
      label: 'SME Estimator',
      type: 'group',
      children: [{ key: '/sme', icon: <ExperimentOutlined />, label: 'Estimator' }],
    })
    items.push({
      key: 'reports-grp',
      label: 'Reports',
      type: 'group',
      children: [{ key: '/reports', icon: <BarChartOutlined />, label: 'Reports' }],
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
  // Supervisor portal — supervisor / admin (exact roles).
  if (['supervisor', 'admin'].includes(role)) {
    items.push({
      key: 'supervisor',
      label: 'Supervisor',
      type: 'group',
      children: [{ key: '/supervisor', icon: <SolutionOutlined />, label: 'Material Requests' }],
    })
  }
  // Warehouse portal — warehouse_user / logistics / admin (exact roles).
  if (['warehouse_user', 'logistics', 'admin'].includes(role)) {
    items.push({
      key: 'warehouse',
      label: 'Warehouse',
      type: 'group',
      children: [{ key: '/warehouse', icon: <InboxOutlined />, label: withCount('Receiving & DN', q.warehouse) }],
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
  // Admin console — user management + audit-log viewer — admin only (level 4).
  if (level >= 4) {
    items.push({
      key: 'admin',
      label: 'Admin',
      type: 'group',
      children: [
        { key: '/admin/users', icon: <TeamOutlined />, label: 'Users' },
        { key: '/admin/pending', icon: <UserAddOutlined />, label: 'Access Requests' },
        { key: '/admin/inventory', icon: <DatabaseOutlined />, label: 'Inventory' },
        { key: '/admin/audit', icon: <FileSearchOutlined />, label: 'Audit Log' },
      ],
    })
  }
  // Security (2FA self-enrollment) — every authenticated user.
  items.push({
    key: 'account', label: 'Account', type: 'group',
    children: [{ key: '/security', icon: <SafetyCertificateOutlined />, label: 'Security' }],
  })
  return items
}

export default function AppLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const { data: health } = useHealth()
  const { data: queues } = useWorkQueues()
  const { user, logout } = useAuth()
  const { mode, toggle } = useThemeMode()
  const level = user?.level ?? 0

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {/* The brand rail is always navy, whatever the app mode */}
      <ConfigProvider theme={siderTheme}>
        {/* Collapse only below md (true mobile) — and never set overflow on the
            Sider itself: it would clip the zero-width reopen trigger that hangs
            outside the collapsed rail. The inner wrapper scrolls instead. */}
        <Sider
          width={232}
          className="gi-sider"
          breakpoint="md"
          collapsedWidth={0}
          style={{ height: '100vh', position: 'sticky', top: 0 }}
        >
          <div className="gi-sider-scroll">
            <div className="gi-brand">
              <div className="gi-wordmark">GI&nbsp;Hub</div>
              <div className="gi-brand-sub">ERP CONSOLE</div>
            </div>
            <Menu
              mode="inline"
              selectedKeys={[location.pathname]}
              items={buildMenu(level, user?.role ?? '', queues ?? {})}
              onClick={({ key }) => navigate(key)}
            />
          </div>
        </Sider>
      </ConfigProvider>
      <Layout>
        <Header
          className="gi-header"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            paddingInline: 24,
          }}
        >
          <Typography.Text strong className="gi-header-title">Warehouse &amp; Inventory</Typography.Text>
          <Space size="middle">
            <span className="gi-health">
              <span className={`gi-health-dot ${health ? 'ok' : 'err'}`} />
              <Typography.Text type="secondary" className="gi-health-label" style={{ fontSize: 12 }}>
                {health ? `${health.dialect} · ${health.database}` : 'API offline'}
              </Typography.Text>
            </span>
            <Tooltip title={mode === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}>
              <Button
                type="text"
                aria-label="Toggle color theme"
                icon={mode === 'dark' ? <SunOutlined /> : <MoonOutlined />}
                onClick={toggle}
              />
            </Tooltip>
            <NotificationBell />
            {user && (
              <Typography.Text type="secondary" className="gi-user-label">
                {user.label} · {user.username}
              </Typography.Text>
            )}
            <Button size="small" icon={<LogoutOutlined />} onClick={logout}>
              Sign out
            </Button>
          </Space>
        </Header>
        <Content style={{ margin: 24 }}>
          {/* Keyed wrapper = fade+rise route transition on every navigation;
              the Skeleton covers lazy page-chunk loads. */}
          <Suspense
            fallback={
              <div className="gi-page">
                <Skeleton active title={{ width: 220 }} paragraph={{ rows: 5 }} />
              </div>
            }
          >
            <div key={location.pathname} className="gi-page">
              <Outlet />
            </div>
          </Suspense>
        </Content>
      </Layout>
    </Layout>
  )
}
