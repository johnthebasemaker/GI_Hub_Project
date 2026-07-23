import { useState } from 'react'
import { App, Button, InputNumber, Popover, Space, Tooltip, Typography } from 'antd'
import { SettingOutlined, SyncOutlined } from '@ant-design/icons'
import { useQueryClient } from '@tanstack/react-query'
import { flushQueue, getSyncIntervalMin, setSyncIntervalMin } from '../offline/queue'

/**
 * "Outlook-style" Send / Receive (header, always visible).
 *
 * SEND    — force-flush the IndexedDB offline queue to the server now.
 * RECEIVE — invalidate every TanStack Query cache entry so each open screen
 *           refetches the latest database state.
 *
 * The gear popover is the Sync Settings UI: the auto-sync cap in minutes
 * (persisted in localStorage, applied instantly by the queue's boot timer —
 * see offline/queue.ts getSyncIntervalMin/setSyncIntervalMin).
 */
export default function SyncControls() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [cap, setCap] = useState<number>(getSyncIntervalMin())

  const sendReceive = async () => {
    setBusy(true)
    try {
      const { sent, failed } = await flushQueue()
      await queryClient.invalidateQueries()
      if (failed.length) {
        message.warning(`Send / Receive finished — ${sent} sent, ${failed.length} rejected (see toasts), data refreshed.`, 6)
      } else {
        message.success(sent
          ? `Send / Receive complete — ${sent} offline entr${sent === 1 ? 'y' : 'ies'} sent, data refreshed.`
          : 'Send / Receive complete — nothing queued, data refreshed.', 4)
      }
    } catch {
      message.error('Send / Receive failed — check your connection and try again.')
    } finally {
      setBusy(false)
    }
  }

  const settings = (
    <Space direction="vertical" size={4} style={{ width: 230 }}>
      <Typography.Text strong>Sync settings</Typography.Text>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Auto-sync the offline queue every…
      </Typography.Text>
      <InputNumber min={1} max={120} value={cap} addonAfter="minutes" style={{ width: '100%' }}
        onChange={(v) => {
          if (!v) return
          setCap(v)
          setSyncIntervalMin(v)
        }} />
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Applies immediately. Send / Receive always syncs on demand.
      </Typography.Text>
    </Space>
  )

  return (
    <Space.Compact>
      <Tooltip title="Send / Receive — push queued offline entries and refresh all data now">
        <Button type="text" aria-label="Send and receive" icon={<SyncOutlined spin={busy} />}
          onClick={() => void sendReceive()} disabled={busy} />
      </Tooltip>
      <Popover content={settings} trigger="click" placement="bottomRight">
        <Button type="text" aria-label="Sync settings" icon={<SettingOutlined />}
          style={{ width: 22, paddingInline: 2 }} />
      </Popover>
    </Space.Compact>
  )
}
