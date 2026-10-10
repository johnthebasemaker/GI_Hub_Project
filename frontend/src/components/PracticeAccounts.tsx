import { App, Button, Card, Space, Table, Tooltip, Typography } from 'antd'
import { CopyOutlined, LoginOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

/**
 * Phase 23f (ruling Q23-13) — the Practice accounts, on the Practice login
 * page, with one-click sign-in. The list comes from `GET /practice/accounts`,
 * which exists ONLY in the Practice process (a 404 on Live). The data behind
 * these accounts is invented, and the password is the one a trainer hands out
 * anyway. `practice.admin` is never listed: its password is shown only to a
 * signed-in Live admin (Admin Console → Practice accounts).
 */
interface Acc { username: string; role: string; label: string; what: string }

export default function PracticeAccounts({ onSignIn, busy }: {
  onSignIn: (username: string, password: string) => void; busy?: boolean
}) {
  const { message } = App.useApp()
  const { data } = useQuery<{ password: string; accounts: Acc[] }>({
    queryKey: ['/practice/accounts'],
    retry: false,
    staleTime: 10 * 60_000,
    queryFn: async () => (await api.get('/practice/accounts')).data,
  })
  if (!data) return null
  const copy = (t: string) => {
    navigator.clipboard?.writeText(t).then(() => message.success('Copied'), () => undefined)
  }
  return (
    <Card size="small" title="Practice accounts" style={{ marginBottom: 16 }} data-testid="practice-accounts">
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 0 }}>
        Every account shares one password:{' '}
        <Typography.Text code data-testid="practice-password">{data.password}</Typography.Text>
        <Tooltip title="Copy the password">
          <Button size="small" type="text" icon={<CopyOutlined />} onClick={() => copy(data.password)}
            aria-label="Copy the password" />
        </Tooltip>
        {' '}Pick a role to sign in as it. The admin account is given out by your administrator.
      </Typography.Paragraph>
      <Table size="small" pagination={false} rowKey="username" dataSource={data.accounts} showHeader={false}
        columns={[
          { key: 'r', render: (_: unknown, a: Acc) => (
            <Space direction="vertical" size={0}>
              <Typography.Text strong>{a.label}</Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                <code>{a.username}</code> · {a.what}</Typography.Text>
            </Space>) },
          { key: 'go', width: 110, align: 'right' as const, render: (_: unknown, a: Acc) => (
            <Button size="small" type="primary" icon={<LoginOutlined />} disabled={busy}
              data-testid={`practice-signin-${a.role}`} aria-label={`Sign in as ${a.label}`}
              onClick={() => onSignIn(a.username, data.password)}>
              Sign in</Button>) },
        ]} />
    </Card>
  )
}
