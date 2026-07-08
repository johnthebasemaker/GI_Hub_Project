import { Suspense, useState } from 'react'
import type { ReactNode } from 'react'
import { Alert, Badge, Button, ConfigProvider, Layout, Menu, Skeleton, Space, Switch, Tooltip, Typography } from 'antd'
import type { MenuProps } from 'antd'
import { AppstoreOutlined, LogoutOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons'
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useHealth, useOverdueActions, useWorkQueues } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'
import type { User } from '../auth/AuthContext'
import { NAV, ADMIN_DEFAULT_GROUPS, canAccess, canAccessPath, roleHome } from '../config/nav'
import type { NavGroup, NavNode } from '../config/nav'
import { useThemeMode } from '../theme/ThemeContext'
import { siderTheme } from '../theme/themes'
import HubAssistant from './HubAssistant'
import NotificationBell from './NotificationBell'

const { Header, Sider, Content } = Layout

// Nav label + live work-queue count (gold badge = work waiting for you).
function withCount(label: string, count?: number): ReactNode {
  if (!count) return label
  return (
    <span className="gi-nav-flex">
      {label}
      <Badge count={count} size="small" overflowCount={99}
        style={{ backgroundColor: 'var(--gi-gold)', color: '#001F40', fontWeight: 600 }} />
    </span>
  )
}

// Red badge — SLA-breached items surfaced to the admin (urgency, not work).
function withRedCount(label: string, count?: number): ReactNode {
  if (!count) return label
  return (
    <span className="gi-nav-flex">
      {label}
      <Badge count={count} size="small" overflowCount={99}
        style={{ backgroundColor: '#EF4444', color: '#fff', fontWeight: 600 }} />
    </span>
  )
}

function nodeLabel(n: NavNode, q: Record<string, number>, overdue?: number): ReactNode {
  if (n.redBadge) return withRedCount(n.label, overdue)
  if (n.badge) return withCount(n.label, q[n.badge])
  return n.label
}

// Build the sidebar from the single-source-of-truth manifest (config/nav.tsx),
// filtered by the same access predicate the route guard uses. `allAreas` only
// affects admin: off → curated console groups; on → every group (shadow access).
function buildMenu(
  user: User | null,
  q: Record<string, number>,
  overdue: number | undefined,
  allAreas: boolean,
): MenuProps['items'] {
  const isAdmin = user?.role === 'admin'
  const groupVisible = (g: NavGroup): boolean => {
    if (g.access && !canAccess(user, g.access)) return false
    if (isAdmin && !allAreas && !ADMIN_DEFAULT_GROUPS.has(g.id)) return false
    return true
  }
  const items: MenuProps['items'] = []
  for (const g of NAV) {
    if (!groupVisible(g)) continue
    const children = g.children
      .filter((n) => canAccess(user, n.access))
      .map((n) => ({ key: n.key, icon: n.icon, label: nodeLabel(n, q, overdue) }))
    if (!children.length) continue
    if (g.label) {
      items.push({ key: g.id, label: g.label, type: 'group', children })
    } else {
      items.push(...children)   // ungrouped top items (Dashboard, Stock)
    }
  }
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
  const isAdmin = user?.role === 'admin'
  // Admin "All areas" toggle — reveals operational groups beyond the curated
  // console. Persisted so it survives reloads.
  const [allAreas, setAllAreas] = useState<boolean>(
    () => localStorage.getItem('gi-nav-all-areas') === '1')
  const setAll = (v: boolean) => {
    setAllAreas(v)
    localStorage.setItem('gi-nav-all-areas', v ? '1' : '0')
  }
  // Red SLA badge — polled only for admins (endpoint is level-4).
  const { data: overdue } = useOverdueActions(level >= 4)

  // Route guard: if the current path isn't allowed for this role, bounce to the
  // role's home. Keeps the UI honest with the API's per-endpoint role gates.
  const guardRedirect = user && !canAccessPath(user, location.pathname)
    ? roleHome(user)
    : null

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <ConfigProvider theme={siderTheme}>
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
              items={buildMenu(user, queues ?? {}, overdue?.count, allAreas)}
              onClick={({ key }) => navigate(key)}
            />
            {isAdmin && (
              <div className="gi-nav-allareas" style={{ padding: '12px 16px 20px', display: 'flex', alignItems: 'center', gap: 8 }}>
                <AppstoreOutlined style={{ opacity: 0.7 }} />
                <Typography.Text style={{ flex: 1, fontSize: 12, opacity: 0.85 }}>All areas</Typography.Text>
                <Switch size="small" checked={allAreas} onChange={setAll}
                  aria-label="Show all navigation areas" />
              </div>
            )}
          </div>
        </Sider>
      </ConfigProvider>
      <Layout>
        <Header
          className="gi-header"
          style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingInline: 24 }}
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
              <Button type="text" aria-label="Toggle color theme"
                icon={mode === 'dark' ? <SunOutlined /> : <MoonOutlined />} onClick={toggle} />
            </Tooltip>
            <NotificationBell />
            {user && (
              <Typography.Text type="secondary" className="gi-user-label">
                {user.label} · {user.username}
              </Typography.Text>
            )}
            <Button size="small" icon={<LogoutOutlined />} onClick={logout}>Sign out</Button>
          </Space>
        </Header>
        <Content style={{ margin: 24 }}>
          {Boolean((health as { maintenance?: boolean } | undefined)?.maintenance) && (
            <Alert type="warning" showIcon banner style={{ marginBottom: 16 }}
              message="Maintenance mode is ON — non-admin sign-ins are paused until it is switched off." />
          )}
          <Suspense
            fallback={
              <div className="gi-page">
                <Skeleton active title={{ width: 220 }} paragraph={{ rows: 5 }} />
              </div>
            }
          >
            <div key={location.pathname} className="gi-page">
              {guardRedirect ? <Navigate to={guardRedirect} replace /> : <Outlet />}
            </div>
          </Suspense>
          <HubAssistant />
        </Content>
      </Layout>
    </Layout>
  )
}
