import { useEffect, useState } from 'react'
import { Badge, Button, Modal, Space, Tag, Tooltip, Typography } from 'antd'
import { GiftOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'

/**
 * Phase 14d — "What's new", shown once on the next sign-in to the people a
 * feature concerns, and reopened any time from the gift icon in the header.
 *
 * ⚠️ THE SERVER CHOOSES WHO SEES WHAT. The list arrives already filtered to
 * this user's role and site — the audience was derived from the navigation
 * matrix when the announcement was synced (rule 14). Nothing here filters.
 *
 * ⚠️ SHOWN ONCE, THEN QUIET. Closing the panel marks what it showed as read;
 * it does not come back by itself. The gift icon lists recent ones again.
 */
interface Ann {
  id: number; key: string; title: string; body: string; routes: string[]
  tutorial_module?: string | null; manual_section?: string | null
  published_at?: string | null; read: boolean
}

const KEY = ['/announcements/whats-new']

export default function WhatsNew() {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [history, setHistory] = useState(false)
  const unread = useQuery({
    queryKey: KEY,
    queryFn: async () => (await api.get<{ items: Ann[] }>('/announcements/whats-new')).data.items,
    staleTime: 5 * 60_000,
  })
  const recent = useQuery({
    queryKey: [...KEY, 'all'],
    enabled: history,
    queryFn: async () => (await api.get<{ items: Ann[] }>('/announcements/whats-new',
      { params: { include_read: true } })).data.items,
  })
  const markRead = useMutation({
    mutationFn: async (ids: number[]) => (await api.post('/announcements/read', { ids })).data,
    onSettled: () => { void qc.invalidateQueries({ queryKey: KEY }) },
  })
  const items = (history ? recent.data : unread.data) ?? []
  const count = unread.data?.length ?? 0

  // Open by itself once per load when something unread is waiting.
  useEffect(() => { if (count > 0) setOpen(true) }, [count])

  const close = () => {
    const ids = (unread.data ?? []).map((a) => a.id)
    if (ids.length) markRead.mutate(ids)
    setOpen(false)
    setHistory(false)
  }

  return (
    <>
      <Tooltip title="What's new">
        <Badge count={count} size="small" offset={[-4, 4]}>
          <Button type="text" aria-label="What's new" icon={<GiftOutlined />}
            onClick={() => { setHistory(count === 0); setOpen(true) }} />
        </Badge>
      </Tooltip>
      <Modal open={open} onCancel={close} footer={null} width={620}
        className="gi-whats-new" title={null} closable={false} destroyOnHidden>
        <div className="gi-glass" style={{ padding: 20 }} data-testid="whats-new">
          <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }}>
            <Typography.Title level={4} style={{ margin: 0, color: 'var(--gi-glass-text)' }}>
              <GiftOutlined style={{ color: 'var(--gi-gold)', marginInlineEnd: 8 }} />
              What&apos;s new
            </Typography.Title>
            <Button type="primary" onClick={close}>Got it</Button>
          </Space>
          {items.length === 0 && (
            <Typography.Text style={{ color: 'var(--gi-glass-text)' }}>
              Nothing new for your role right now.
            </Typography.Text>
          )}
          <div className="gi-glass-stage">
            {items.map((a, i) => (
              <div key={a.id} className="gi-glass gi-glass-tilt gi-rise-3d gi-whats-new-card"
                style={{ animationDelay: `${i * 70}ms` }}>
                <h4>{a.title}</h4>
                <Typography.Paragraph style={{ color: 'var(--gi-glass-text)', whiteSpace: 'pre-line', marginBottom: 8 }}>
                  {a.body}
                </Typography.Paragraph>
                <Space wrap size={6}>
                  {a.routes.slice(0, 1).map((r) => (
                    <Link key={r} to={r} onClick={close}><Tag color="gold">Open it</Tag></Link>
                  ))}
                  {a.tutorial_module && (
                    <Link to={`/training?module=${encodeURIComponent(a.tutorial_module)}`} onClick={close}>
                      <Tag color="blue">Watch the tutorial</Tag>
                    </Link>
                  )}
                  {a.manual_section && <Tag>User Manual §{a.manual_section}</Tag>}
                  {a.published_at && (
                    <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                      {a.published_at.slice(0, 10)}
                    </Typography.Text>
                  )}
                </Space>
              </div>
            ))}
          </div>
        </div>
      </Modal>
    </>
  )
}
