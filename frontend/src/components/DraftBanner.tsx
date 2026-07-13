import { Alert, Button, Space } from 'antd'
import { HistoryOutlined } from '@ant-design/icons'

/** Parity C4 — "you have an unsaved form" restore banner. */
export default function DraftBanner({ hasDraft, onRestore, onDiscard }: {
  hasDraft: boolean
  onRestore: () => void
  onDiscard: () => void
}) {
  if (!hasDraft) return null
  return (
    <Alert type="info" showIcon icon={<HistoryOutlined />} style={{ marginBottom: 12 }}
      title="An unsaved form draft from your last visit was found."
      action={
        <Space>
          <Button size="small" type="primary" onClick={onRestore}>Restore</Button>
          <Button size="small" onClick={onDiscard}>Discard</Button>
        </Space>
      } />
  )
}
