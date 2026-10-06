import { Suspense, lazy, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Alert, App, Badge, Button, ConfigProvider, Drawer, Grid, Layout, Menu, Modal, Skeleton, Space, Switch, Tag, Tooltip, Typography } from 'antd'
import type { MenuProps } from 'antd'
import { AppstoreOutlined, EyeOutlined, LogoutOutlined, MenuOutlined, MoonOutlined, QrcodeOutlined, SearchOutlined, SunOutlined, UserOutlined } from '@ant-design/icons'
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useHealth, useOverdueActions, useWorkQueues } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'
import { READ_ONLY_REASON } from '../auth/useReadOnly'
import { useIdleLogout } from '../auth/useIdleLogout'
import type { User } from '../auth/AuthContext'
import { NAV, ADMIN_DEFAULT_GROUPS, PRIMARY_GROUP, accessibleNodes, canAccess, canAccessPath, groupOfPath, roleHome } from '../config/nav'
import type { NavGroup, NavNode } from '../config/nav'
import { useThemeMode } from '../theme/ThemeContext'
import { practiceTheme, siderTheme } from '../theme/themes'
import { isPractice } from '../api/environment'
import CommandPalette from './CommandPalette'
import HubAssistant from './HubAssistant'
// ⚠️ LAZY (Phase 18): the header scanner (and jsQR inside it) was on the login
// critical path although nothing renders it until somebody presses the scan
// button. Loaded on first open instead — the critical-path check re-baselined
// DOWN, deliberately.
const QrScanner = lazy(() => import('./QrScanner'))
// Phase 21f — the self-driving demo. PRACTICE ONLY and lazy: nothing of it is
// fetched until ▶ Auto demo (or the assistant's ▶ Run this demo) is pressed.
const DemoHost = lazy(() => import('../demo/DemoHost'))
import { BARCODE_FORMATS, parseScanPayload } from '../lib/barcode'
import NotificationBell from './NotificationBell'
import WhatsNew from './WhatsNew'
import OfflineSyncBadge from './OfflineSyncBadge'
import SyncControls from './SyncControls'
import ProfileModal from './ProfileModal'
import PracticeBanner, { PracticeBadge, PracticeTag } from './PracticeBanner'
import { useUnitSizesLoader } from '../lib/units'

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
      // Collapsible SubMenu (progressive disclosure) — see openKeys below.
      items.push({ key: g.id, label: g.label, children })
    } else {
      items.push(...children)   // ungrouped top items (Dashboard, Stock)
    }
  }
  return items
}

// The group ids visible to this user (to bound the persisted openKeys).
function visibleGroupIds(user: User | null, allAreas: boolean): string[] {
  const isAdmin = user?.role === 'admin'
  return NAV.filter((g) => {
    if (!g.label) return false
    if (g.access && !canAccess(user, g.access)) return false
    if (isAdmin && !allAreas && !ADMIN_DEFAULT_GROUPS.has(g.id)) return false
    return g.children.some((n) => canAccess(user, n.access))
  }).map((g) => g.id)
}

export default function AppLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const { data: health } = useHealth()
  const { data: queues } = useWorkQueues()
  const { user, logout, readOnly } = useAuth()
  // Phase 14a: the pack → base factors every quantity display reads.
  useUnitSizesLoader()
  const { mode, toggle } = useThemeMode()
  const { message } = App.useApp()
  const [demoReq, setDemoReq] = useState<{ n: number; id?: string; focus?: string } | null>(null)
  useEffect(() => {
    if (!isPractice()) return
    const run = (e: Event) => setDemoReq({ n: Date.now(), id: (e as CustomEvent<{ id?: string }>).detail?.id })
    window.addEventListener('gi-demo-run', run)
    // ?demo=<id> opens the chooser on that demo; a click still starts it,
    // because browsers only let a page speak after a person's gesture
    const want = new URLSearchParams(window.location.search).get('demo')
    if (want) setDemoReq({ n: Date.now(), focus: want })
    return () => window.removeEventListener('gi-demo-run', run)
  }, [])
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
  const [profileOpen, setProfileOpen] = useState(false)
  // Global QR scan → Material Intelligence page (QR ecosystem). The sticker
  // payload is parsed client-side (parseScanPayload) AND resolved server-side,
  // because labels are not all bare SAP codes — the operator's older stickers
  // read "1163|Cable Tie Wire ( Nylon)".
  const [scanOpen, setScanOpen] = useState(false)

  // Collapsible sidebar groups — the role's primary group opens by default
  // (progressive disclosure); the choice persists, and the active group is
  // always kept open so the current page's highlight is visible.
  const [openKeys, setOpenKeys] = useState<string[]>(() => {
    const saved = localStorage.getItem('gi-nav-open')
    if (saved) { try { return JSON.parse(saved) } catch { /* ignore */ } }
    const primary = user ? PRIMARY_GROUP[user.role] : undefined
    return primary ? [primary] : []
  })
  const onOpenChange = (keys: string[]) => {
    setOpenKeys(keys)
    localStorage.setItem('gi-nav-open', JSON.stringify(keys))
  }
  useEffect(() => {
    const g = groupOfPath(location.pathname)
    if (g) setOpenKeys((prev) => (prev.includes(g) ? prev : [...prev, g]))
  }, [location.pathname])

  // Exposed for the Playwright RBAC-matrix spec + console debugging, the same
  // way the offline queue exposes `__giOffline`.
  //
  // ⚠️ It hands out the REAL functions, not a copy of the rules. A matrix test
  // that re-implemented `canAccess` would pass happily while the shipped guard
  // did something else — which is the exact failure this whole pass exists to
  // stop. Read-only, and it exposes nothing a signed-in user cannot already
  // determine by clicking around their own sidebar.
  useEffect(() => {
    ;(window as unknown as Record<string, unknown>).__giNav = {
      role: user?.role ?? null,
      pages: () => accessibleNodes(user).map((n) => n.key),
      can: (path: string) => canAccessPath(user, path),
    }
  }, [user])

  // Route guard: if the current path isn't allowed for this role, bounce to the
  // role's home. Keeps the UI honest with the API's per-endpoint role gates.
  const guardRedirect = user && !canAccessPath(user, location.pathname)
    ? roleHome(user)
    : null

  // Mobile shell: below `md` the nav is an OVERLAY drawer, not a rail in the
  // flex row. antd's zero-width Sider trigger re-opens the rail IN FLOW, which
  // is what squeezed the content column on a phone — every industry-standard
  // app slides the nav over the page instead.
  const screens = Grid.useBreakpoint()
  const isMobile = !screens.md
  const [navOpen, setNavOpen] = useState(false)
  useEffect(() => { setNavOpen(false) }, [location.pathname])

  // Inactivity sign-out. The logout it calls is the ordinary one, so the
  // refresh-token family is REVOKED server-side — the session is genuinely
  // over, not just visually reset. See auth/useIdleLogout.ts.
  const idle = useIdleLogout(!!user, () => {
    logout()
    message.warning('Signed out after 30 minutes of inactivity.', 6)
  })

  // A view-only account tried to change something. The API client rejects the
  // request before it is sent (auth/readOnly.ts) and fires this; without a
  // toast the click would look like it simply did nothing.
  useEffect(() => {
    const onBlocked = () => message.warning(READ_ONLY_REASON, 5)
    window.addEventListener('gi-read-only-blocked', onBlocked)
    return () => window.removeEventListener('gi-read-only-blocked', onBlocked)
  }, [message])

  const navBody = (
    <div className="gi-sider-scroll">
      <div className="gi-brand">
        <div className="gi-wordmark">GI&nbsp;Hub</div>
        <div className="gi-brand-sub">ERP CONSOLE</div>
        <PracticeBadge />
      </div>
      <Menu
        mode="inline"
        selectedKeys={[location.pathname]}
        openKeys={openKeys.filter((k) => visibleGroupIds(user, allAreas).includes(k))}
        onOpenChange={(keys) => onOpenChange(keys as string[])}
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
  )

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <ConfigProvider theme={isPractice() ? practiceTheme(siderTheme) : siderTheme}>
        {isMobile ? (
          <Drawer
            open={navOpen}
            onClose={() => setNavOpen(false)}
            placement="left"
            width={264}
            closable={false}
            rootClassName="gi-nav-drawer"
            styles={{ body: { padding: 0 } }}
          >
            {navBody}
          </Drawer>
        ) : (
          <Sider
            width={232}
            className="gi-sider"
            trigger={null}
            style={{ height: '100vh', position: 'sticky', top: 0 }}
          >
            {navBody}
          </Sider>
        )}
      </ConfigProvider>
      <Layout>
        {/* Rule 17: Live | Practice, as the SERVER reports it (GET /instance). */}
        <PracticeBanner compact={isMobile} />
        <Header
          className="gi-header"
          style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingInline: 24 }}
        >
          <Space size="small">
            {isMobile && (
              <Button type="text" aria-label="Open navigation" icon={<MenuOutlined />}
                onClick={() => setNavOpen(true)} />
            )}
            {isMobile && <PracticeTag />}
            <Typography.Text strong className="gi-header-title">Warehouse &amp; Inventory</Typography.Text>
            {!isMobile && <PracticeTag />}
          </Space>
          <Space size="middle" className="gi-header-actions">
            {isPractice() && (
              <Tooltip title="Watch the app run itself on Practice data">
                <Button size="small" data-testid="demo-launch"
                  onClick={() => setDemoReq({ n: Date.now() })}>▶ Auto demo</Button>
              </Tooltip>
            )}
            <Tooltip title="Jump to any page (⌘K / Ctrl-K)">
              <Button type="text" aria-label="Open command palette" icon={<SearchOutlined />}
                onClick={() => window.dispatchEvent(new Event('gi-open-command-palette'))} />
            </Tooltip>
            <Tooltip title="Scan a material QR / barcode — opens its stock dashboard">
              <Button type="text" aria-label="Scan material QR" icon={<QrcodeOutlined />}
                onClick={() => setScanOpen(true)} />
            </Tooltip>
            <span className="gi-health">
              <span className={`gi-health-dot ${health ? 'ok' : 'err'}`} />
              <Typography.Text type="secondary" className="gi-health-label" style={{ fontSize: 12 }}>
                {health ? 'API online' : 'API offline'}
              </Typography.Text>
            </span>
            <Tooltip title={mode === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}>
              <Button type="text" aria-label="Toggle color theme"
                icon={mode === 'dark' ? <SunOutlined /> : <MoonOutlined />} onClick={toggle} />
            </Tooltip>
            <SyncControls />
            <OfflineSyncBadge />
            <WhatsNew />
            <NotificationBell />
            {readOnly && (
              <Tooltip title="Your account is view-only — you can read everything your role reaches, but nothing can be changed.">
                <Tag color="blue" icon={<EyeOutlined />} style={{ marginInlineEnd: 0 }}>View only</Tag>
              </Tooltip>
            )}
            {user && (
              <Tooltip title="My profile — update phone number">
                <Button type="text" className="gi-user-label" icon={<UserOutlined />}
                  onClick={() => setProfileOpen(true)}>
                  {user.label} · {user.username}
                </Button>
              </Tooltip>
            )}
            <Button size="small" icon={<LogoutOutlined />} onClick={logout}>Sign out</Button>
          </Space>
        </Header>
        <Content className="gi-content">
          {Boolean((health as { maintenance?: boolean } | undefined)?.maintenance) && (
            <Alert type="warning" showIcon banner style={{ marginBottom: 16 }}
              title="Maintenance mode is ON — non-admin sign-ins are paused until it is switched off." />
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
      <CommandPalette />
      {demoReq && (
        <Suspense fallback={null}>
          <DemoHost request={demoReq} onClose={() => setDemoReq(null)} />
        </Suspense>
      )}
      <ProfileModal open={profileOpen} onClose={() => setProfileOpen(false)} />
      {scanOpen && (
        <Suspense fallback={null}>
          <QrScanner open={scanOpen} title="Scan a material QR / barcode"
            formats={BARCODE_FORMATS} manualPlaceholder="…or type the SAP code"
            onClose={() => setScanOpen(false)}
            onDecode={(text) => {
              setScanOpen(false)
              navigate(`/stock/material/${encodeURIComponent(parseScanPayload(text))}`)
            }} />
        </Suspense>
      )}
      <Modal
        open={idle.warning}
        title="Still there?"
        onOk={idle.staySignedIn}
        onCancel={idle.staySignedIn}
        okText="Stay signed in"
        cancelButtonProps={{ style: { display: 'none' } }}
        closable={false}
        maskClosable={false}
        keyboard={false}
      >
        <Typography.Paragraph style={{ marginBottom: 0 }}>
          You have been inactive for a while. For security you will be signed out in{' '}
          <b>{idle.secondsLeft}s</b>.
        </Typography.Paragraph>
        <Typography.Paragraph type="secondary" style={{ fontSize: '0.8rem', marginBottom: 0 }}>
          Any unsaved form entries on this page will be lost.
        </Typography.Paragraph>
      </Modal>
    </Layout>
  )
}
