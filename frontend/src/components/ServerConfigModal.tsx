/**
 * frontend/src/components/ServerConfigModal.tsx — which server this app talks to.
 *
 * The installed binaries (APK / EXE / DMG) are built ONCE against production,
 * so without this a tester needs a rebuild to point at the local tunnel. The
 * build-time VITE_API_URL stays the DEFAULT; this writes a localStorage
 * override that the axios client reads at request time (api/client.ts).
 *
 * Switching servers ends the session on purpose: an access token minted by one
 * server means nothing to another, and silently carrying it over produces a
 * confusing 401 loop instead of a clean sign-in.
 */
import { useState } from 'react'
import { App, Alert, Button, Form, Input, Modal, Space, Tag, Typography } from 'antd'
import { CloudServerOutlined } from '@ant-design/icons'
import {
  apiBase, getApiBaseDefault, isApiOverridden, normalizeApiBase, setApiBase, TOKEN_KEY,
} from '../api/client'

// The two servers this project actually has. `local` is the dev tunnel served
// from a developer's Mac; `gi` is production once Hetzner is live.
const PRESETS = [
  { label: 'Production — gi.giinventory.com', value: 'https://gi.giinventory.com/api' },
  { label: 'Local tunnel — local.giinventory.com', value: 'https://local.giinventory.com/api' },
]

export default function ServerConfigModal({ open, onClose }:
  { open: boolean; onClose: () => void }) {
  const { message } = App.useApp()
  const [value, setValue] = useState(isApiOverridden() ? apiBase() : '')

  const apply = (raw: string) => {
    const next = setApiBase(raw)
    message.success(`Server set to ${next} — signing out to reconnect.`)
    // Drop the old server's token, then hard-reload so every cached query and
    // in-flight request starts again against the new base.
    try { localStorage.removeItem(TOKEN_KEY) } catch { /* private mode */ }
    setTimeout(() => window.location.reload(), 600)
  }

  return (
    <Modal open={open} onCancel={onClose} title={<Space><CloudServerOutlined />Server configuration</Space>}
      footer={null} destroyOnHidden>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        Which backend this app signs in to. Installed apps default to production;
        override it to test against a development tunnel.
      </Typography.Paragraph>

      <Space wrap style={{ marginBottom: 12 }}>
        <Typography.Text type="secondary">Currently:</Typography.Text>
        <Tag color={isApiOverridden() ? 'gold' : 'blue'}>{apiBase()}</Tag>
        {isApiOverridden() && <Tag>overridden</Tag>}
      </Space>

      <Form layout="vertical" onFinish={() => apply(value)}>
        <Form.Item label="API base URL"
          help="Host or full URL. A bare host becomes https://<host>/api.">
          <Input placeholder={getApiBaseDefault()} value={value} allowClear
            onChange={(e) => setValue(e.target.value)} />
        </Form.Item>
        <Space wrap style={{ marginBottom: 12 }}>
          {PRESETS.map((p) => (
            <Button key={p.value} size="small" onClick={() => setValue(p.value)}>{p.label}</Button>
          ))}
        </Space>
        {value.trim() !== '' && (
          <Alert type="info" showIcon style={{ marginBottom: 12 }}
            title={`Will connect to ${normalizeApiBase(value)}`} />
        )}
        <Space wrap>
          <Button type="primary" htmlType="submit">Save &amp; reconnect</Button>
          <Button disabled={!isApiOverridden()} onClick={() => apply('')}>
            Reset to default
          </Button>
          <Button type="text" onClick={onClose}>Cancel</Button>
        </Space>
      </Form>
    </Modal>
  )
}
