import { useEffect, useRef, useState } from 'react'
import { Badge, Button, Empty, Popover, Spin, Typography } from 'antd'
import { BellOutlined, CheckOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useMarkAllNotifRead, useMarkNotifRead, useNotifications, useUnreadCount } from '../api/hooks'
import type { Row } from '../api/client'
import { status } from '../theme/tokens'

const SEV_COLOR: Record<string, string> = {
  info: status.info, success: status.ok, warning: status.low, critical: status.critical,
}

function NotifRow({ n, onClick }: { n: Row; onClick: (n: Row) => void }) {
  const linked = !!n.link_page && String(n.link_page).startsWith('/')
  return (
    <div
      onClick={() => onClick(n)}
      className={`gi-notif-row${n.read_at ? '' : ' gi-notif-unread'}`}
      style={{
        display: 'flex', gap: 8, padding: '8px 8px', borderRadius: 6,
        cursor: linked ? 'pointer' : 'default',
      }}
    >
      <span style={{
        width: 8, height: 8, borderRadius: '50%', marginTop: 5, flex: '0 0 auto',
        background: SEV_COLOR[String(n.severity)] ?? SEV_COLOR.info,
      }} />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontWeight: n.read_at ? 400 : 600, fontSize: 13 }}>{String(n.title)}</div>
        {n.body ? (
          <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
            {String(n.body)}
          </Typography.Text>
        ) : null}
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
          {String(n.created_at ?? '')}{linked ? '  ·  open →' : ''}
        </Typography.Text>
      </div>
    </div>
  )
}

export default function NotificationBell() {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const { data: unread = 0 } = useUnreadCount()
  const { data: items, isFetching } = useNotifications(open)
  const markRead = useMarkNotifRead()
  const markAll = useMarkAllNotifRead()

  // One gentle ring when the unread count goes UP (new notification).
  const [ringing, setRinging] = useState(false)
  const prevUnread = useRef(unread)
  useEffect(() => {
    const increased = unread > prevUnread.current
    prevUnread.current = unread
    if (increased) {
      setRinging(true)
      const t = setTimeout(() => setRinging(false), 600)
      return () => clearTimeout(t)
    }
  }, [unread])

  const onItemClick = (n: Row) => {
    if (!n.read_at) markRead.mutate(Number(n.id))
    const link = n.link_page ? String(n.link_page) : ''
    if (link.startsWith('/')) {
      setOpen(false)
      navigate(link)
    }
  }

  const content = (
    <div style={{ width: 340 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
        <Typography.Text strong>Notifications</Typography.Text>
        <Button type="link" size="small" icon={<CheckOutlined />} disabled={!unread}
          loading={markAll.isPending} onClick={() => markAll.mutate()}>
          Mark all read
        </Button>
      </div>
      <div style={{ maxHeight: 400, overflowY: 'auto' }}>
        {isFetching && !items ? (
          <div style={{ textAlign: 'center', padding: 24 }}><Spin size="small" /></div>
        ) : items && items.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No notifications" />
        ) : (
          (items ?? []).map((n) => <NotifRow key={String(n.id)} n={n} onClick={onItemClick} />)
        )}
      </div>
    </div>
  )

  return (
    <Popover content={content} trigger="click" open={open} onOpenChange={setOpen} placement="bottomRight">
      <Badge count={unread} size="small" overflowCount={99}>
        <Button
          type="text"
          className={ringing ? 'gi-bell-ring' : undefined}
          icon={<BellOutlined style={{ fontSize: 18 }} />}
          aria-label="Notifications"
        />
      </Badge>
    </Popover>
  )
}
