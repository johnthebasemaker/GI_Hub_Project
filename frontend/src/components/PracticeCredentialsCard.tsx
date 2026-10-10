import { useState } from 'react'
import { Alert, App, Button, Card, Descriptions, Popconfirm, Space, Typography } from 'antd'
import { CopyOutlined, EyeInvisibleOutlined, EyeOutlined, UndoOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

/**
 * Phase 23f (ruling Q23-13) — Practice sign-in details for the LIVE admin.
 *
 * The 8 shared accounts are on the Practice login page for anybody; the
 * Practice ADMIN password is not — it is here, behind a Live admin's sign-in,
 * hidden until asked for. "Reset Practice passwords" runs
 * `tools/practice_db.py passwords` on the server — a Practice process per
 * database, so Live itself never opens a Practice database (rule 17); the same
 * command is shown for a shell.
 */
interface Creds {
  shared_password: string; admin_username: string; admin_password: string | null
  admin_password_note: string | null; accounts: { username: string; label: string }[]
  reset_command: string
}

export default function PracticeCredentialsCard() {
  const { message } = App.useApp()
  const [show, setShow] = useState(false)
  const { data, error } = useQuery<Creds>({
    queryKey: ['/admin/practice/credentials'],
    queryFn: async () => (await api.get('/admin/practice/credentials')).data,
  })
  const reset = useMutation({
    mutationFn: async () => (await api.post('/admin/practice/reset-passwords')).data as { databases: number },
    onSuccess: (r) => message.success(`Every Practice password is back (${r.databases} database(s))`),
    onError: (e: unknown) => message.error((e as Error)?.message || 'The reset did not finish'),
  })
  const copy = (t: string) => navigator.clipboard?.writeText(t).then(() => message.success('Copied'), () => undefined)
  if (error) return <Alert type="error" showIcon title={(error as Error).message} />
  if (!data) return null
  return (
    <Card size="small" title="Practice accounts — sign-in details" data-testid="practice-credentials">
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        Practice is the training copy (invented data). The eight role accounts and their shared password are
        listed on the Practice login page with one-click sign-in. The <b>admin</b> account can wipe everyone&apos;s
        practice work, so its password is shown only here.
      </Typography.Paragraph>
      <Descriptions size="small" column={1} bordered items={[
        { key: 'a', label: <code>{data.admin_username}</code>, children: data.admin_password ? (
          <Space>
            <Typography.Text code data-testid="practice-admin-password">{show ? data.admin_password : '••••••••••'}</Typography.Text>
            <Button size="small" type="text" icon={show ? <EyeInvisibleOutlined /> : <EyeOutlined />}
              onClick={() => setShow((v) => !v)} aria-label={show ? 'Hide' : 'Show'} data-testid="practice-admin-show" />
            <Button size="small" type="text" icon={<CopyOutlined />} onClick={() => copy(data.admin_password!)} aria-label="Copy" />
          </Space>
        ) : <Typography.Text type="warning">{data.admin_password_note}</Typography.Text> },
        { key: 's', label: 'The other 8 accounts', children: (
          <Space direction="vertical" size={2}>
            <span>Password <Typography.Text code>{data.shared_password}</Typography.Text></span>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {data.accounts.map((a) => a.username).join(' · ')}</Typography.Text>
          </Space>
        ) },
        { key: 'r', label: 'A password stopped working?', children: (
          <Space direction="vertical" size={2}>
            <span>On the office computer, run this — it puts every Practice password back:</span>
            <Space>
              <Typography.Text code>{data.reset_command}</Typography.Text>
              <Button size="small" type="text" icon={<CopyOutlined />} onClick={() => copy(data.reset_command)} aria-label="Copy the command" />
            </Space>
            <Popconfirm title="Put every Practice password back?"
              description="All nine Practice accounts get their published passwords again. No other Practice data changes."
              disabled={!data.admin_password} onConfirm={() => reset.mutate()}>
              <Button size="small" icon={<UndoOutlined />} loading={reset.isPending} disabled={!data.admin_password}
                data-testid="practice-reset-passwords">…or do it now: Reset Practice passwords</Button>
            </Popconfirm>
          </Space>
        ) },
      ]} />
    </Card>
  )
}
