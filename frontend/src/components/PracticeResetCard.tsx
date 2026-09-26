import { useState } from 'react'
import { Alert, App, Button, Card, Descriptions, Input, Modal, Space, Typography } from 'antd'
import { ExperimentOutlined, ReloadOutlined } from '@ant-design/icons'
import { api, setAuthToken } from '../api/client'
import { useInstance } from './PracticeBanner'

const PHRASE = 'RESET PRACTICE DATA'

/**
 * The Practice admin's reset (operator ruling Q6: this button, no schedule).
 *
 * Rendered only when the SERVER says it is Practice — and on Live the endpoint
 * does not exist at all (it is not mounted; backend/api/practice.py), so this
 * card is never the thing standing between a click and Live's data.
 *
 * A reset replaces the whole sandbox, including every session in it, so the
 * admin is signed out afterwards and signs in again against the fresh copy.
 */
export default function PracticeResetCard() {
  const { message } = App.useApp()
  const { data, refetch } = useInstance()
  const [open, setOpen] = useState(false)
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  if (!data?.practice) return null

  const run = async () => {
    setBusy(true)
    try {
      await api.post('/practice/reset', { confirm: typed })
      message.success('Practice data reset. Signing you out — sign in again to continue.', 4)
      setOpen(false)
      await refetch()
      setAuthToken(null)
      setTimeout(() => window.location.assign('/'), 1200)
    } catch (e) {
      const x = e as { response?: { data?: { detail?: string } } }
      message.error(x?.response?.data?.detail ?? 'Reset failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title={<Space><ExperimentOutlined />Practice sandbox</Space>} style={{ maxWidth: 720 }}>
      <Descriptions size="small" column={1} style={{ marginBottom: 16 }} items={[
        { key: 'v', label: 'Dataset version', children: data.dataset_version ?? '—' },
        { key: 's', label: 'Seed built', children: data.seeded_at ? new Date(data.seeded_at).toLocaleString() : '—' },
        { key: 'r', label: 'Last reset', children: data.reset_at
            ? `${new Date(data.reset_at).toLocaleString()}${data.reset_by ? ` by ${data.reset_by}` : ''}` : '—' },
      ]} />
      <Alert type="warning" showIcon style={{ marginBottom: 16 }}
        title="Resetting wipes everything every trainee has done here"
        description="Receipts, issues, approvals, new users and settings changes all go, and the sandbox returns to its starting data (with the approval queues refilled). Everybody signed in to Practice is signed out. Live is not touched — it is a different database the Practice server cannot even open." />
      <Button danger icon={<ReloadOutlined />} onClick={() => { setTyped(''); setOpen(true) }}>
        Reset Practice data…
      </Button>
      <Modal open={open} title="Reset Practice data" onCancel={() => setOpen(false)}
        okText="Reset now" okButtonProps={{ danger: true, disabled: typed.trim() !== PHRASE, loading: busy }}
        onOk={run} destroyOnHidden>
        <Typography.Paragraph>
          Type <Typography.Text code>{PHRASE}</Typography.Text> to confirm.
        </Typography.Paragraph>
        <Input autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={PHRASE}
          onPressEnter={() => typed.trim() === PHRASE && void run()} />
      </Modal>
    </Card>
  )
}
