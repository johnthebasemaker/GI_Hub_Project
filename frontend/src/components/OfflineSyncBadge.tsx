import { useEffect, useState } from 'react'
import { App, Badge, Button, Tooltip } from 'antd'
import { CloudSyncOutlined } from '@ant-design/icons'
import { flushQueue, queueCount } from '../offline/queue'

/**
 * Header chip for the offline mutation queue (Phase B). Hidden while the
 * queue is empty; shows a count badge when entries are waiting, with a
 * click-to-sync. Also owns the user-facing toasts for queue events so the
 * (non-React) queue module never touches antd directly.
 */
export default function OfflineSyncBadge() {
  const { message } = App.useApp()
  const [count, setCount] = useState(0)

  useEffect(() => {
    void queueCount().then(setCount).catch(() => undefined)
    const onQueue = (e: Event) => setCount((e as CustomEvent<{ count: number }>).detail.count)
    const onQueued = () =>
      message.warning('You are offline — the entry was saved and will sync automatically.', 5)
    const onFlushed = (e: Event) => {
      const { sent, failed } = (e as CustomEvent<{ sent: number; failed: string[] }>).detail
      if (sent) message.success(`Back online — ${sent} queued entr${sent === 1 ? 'y' : 'ies'} synced.`, 5)
      for (const f of failed) message.error(`Offline entry rejected on sync: ${f}`, 8)
    }
    window.addEventListener('gi-offline-queue', onQueue)
    window.addEventListener('gi-offline-queued', onQueued)
    window.addEventListener('gi-offline-flushed', onFlushed)
    return () => {
      window.removeEventListener('gi-offline-queue', onQueue)
      window.removeEventListener('gi-offline-queued', onQueued)
      window.removeEventListener('gi-offline-flushed', onFlushed)
    }
  }, [message])

  if (!count) return null
  return (
    <Tooltip title={`${count} offline entr${count === 1 ? 'y' : 'ies'} waiting to sync — click to retry now`}>
      <Badge count={count} size="small">
        <Button type="text" aria-label="Sync offline queue" icon={<CloudSyncOutlined />}
          onClick={() => void flushQueue()} />
      </Badge>
    </Tooltip>
  )
}
