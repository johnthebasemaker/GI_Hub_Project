import { useState } from 'react'
import { App, Button, Card, Form, Input, Typography } from 'antd'
import { LockOutlined, SafetyOutlined, UserOutlined } from '@ant-design/icons'
import { useAuth } from '../auth/AuthContext'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Login failed'
}

export default function LoginPage() {
  const { message } = App.useApp()
  const { login, loginMfa } = useAuth()
  const [loading, setLoading] = useState(false)
  const [mfaToken, setMfaToken] = useState<string | null>(null)

  const onLogin = async (v: { username: string; password: string }) => {
    setLoading(true)
    try {
      const r = await login(v.username, v.password)
      if (r.mfa) {
        setMfaToken(r.mfaToken!)
        message.info('Enter your 6-digit authenticator code')
      }
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setLoading(false)
    }
  }

  const onMfa = async (v: { code: string }) => {
    setLoading(true)
    try {
      await loginMfa(mfaToken!, v.code)
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#f0f2f5',
      }}
    >
      <Card style={{ width: 360 }}>
        <div style={{ textAlign: 'center', marginBottom: 16 }}>
          <Typography.Title level={3} style={{ marginBottom: 0 }}>
            GI Hub
          </Typography.Title>
          <Typography.Text type="secondary">ERP Console — sign in</Typography.Text>
        </div>

        {!mfaToken ? (
          <Form layout="vertical" onFinish={onLogin}>
            <Form.Item name="username" rules={[{ required: true, message: 'Username' }]}>
              <Input prefix={<UserOutlined />} placeholder="Username" autoFocus />
            </Form.Item>
            <Form.Item name="password" rules={[{ required: true, message: 'Password' }]}>
              <Input.Password prefix={<LockOutlined />} placeholder="Password" />
            </Form.Item>
            <Button type="primary" htmlType="submit" block loading={loading}>
              Sign in
            </Button>
          </Form>
        ) : (
          <Form layout="vertical" onFinish={onMfa}>
            <Form.Item name="code" rules={[{ required: true, message: '6-digit code' }]}>
              <Input prefix={<SafetyOutlined />} placeholder="Authenticator code" autoFocus />
            </Form.Item>
            <Button type="primary" htmlType="submit" block loading={loading}>
              Verify
            </Button>
            <Button type="link" block onClick={() => setMfaToken(null)}>
              Back
            </Button>
          </Form>
        )}
      </Card>
    </div>
  )
}
