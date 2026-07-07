import { useState } from 'react'
import { App, Button, ConfigProvider, Form, Input, Select } from 'antd'
import { EnvironmentOutlined, LockOutlined, SafetyOutlined, UserOutlined } from '@ant-design/icons'
import { useAuth } from '../auth/AuthContext'
import { useRegister, useRegisterSites } from '../api/hooks'
import { darkTheme } from '../theme/themes'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Something went wrong'
}

// Self-registrants may request any role except admin.
const REGISTER_ROLES = [
  { value: 'store_keeper', label: 'Store Keeper' },
  { value: 'supervisor', label: 'Supervisor' },
  { value: 'hod', label: 'Head of Department' },
  { value: 'warehouse_user', label: 'Warehouse' },
  { value: 'logistics', label: 'Logistics' },
]

// T4 — scoped roles MUST pick an admin-created site; unscoped (global) roles
// carry no site and may give a free-text location instead. Mirrors auth.py.
const SCOPED_ROLES = new Set(['store_keeper', 'supervisor', 'hod'])

export default function LoginPage() {
  const { message } = App.useApp()
  const { login, loginMfa } = useAuth()
  const [loading, setLoading] = useState(false)
  const [mfaToken, setMfaToken] = useState<string | null>(null)
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const register = useRegister()
  const [regForm] = Form.useForm()
  const regRole: string = Form.useWatch('role', regForm) ?? 'store_keeper'
  const isScoped = SCOPED_ROLES.has(regRole)
  const { data: regSites, isLoading: sitesLoading } = useRegisterSites(mode === 'register')

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

  const onRegister = async (v: Record<string, unknown>) => {
    try {
      await register.mutateAsync(v)
      message.success('Request submitted — an admin will review it before you can sign in.')
      setMode('login')
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  // The login screen is always navy (the flagship first impression),
  // independent of the in-app light/dark toggle.
  return (
    <ConfigProvider theme={darkTheme}>
      <div className="gi-login">
        <div className="gi-login-card gi-stagger">
          <div className="gi-login-head">
            <div className="gi-wordmark">GI&nbsp;Hub</div>
            <div className="gi-brand-sub">
              {mode === 'register' ? 'ERP CONSOLE — REQUEST ACCESS' : 'ERP CONSOLE — SIGN IN'}
            </div>
          </div>

          {mode === 'register' ? (
            <Form key="register" form={regForm} layout="vertical" onFinish={onRegister}
              initialValues={{ role: 'store_keeper' }}>
              <Form.Item name="username" rules={[{ required: true, message: 'Username' }]}>
                <Input prefix={<UserOutlined />} placeholder="Username" autoFocus />
              </Form.Item>
              <Form.Item name="password" rules={[{ required: true, min: 6, message: 'At least 6 characters' }]}>
                <Input.Password prefix={<LockOutlined />} placeholder="Password (min 6)" />
              </Form.Item>
              <Form.Item name="role" label="Requested role" rules={[{ required: true }]}>
                <Select options={REGISTER_ROLES}
                  onChange={() => regForm.setFieldsValue({ site_id: undefined, location: undefined })} />
              </Form.Item>
              {isScoped ? (
                // Scoped roles work AT a site — mandatory, admin-created list only.
                <Form.Item name="site_id" label="Site"
                  rules={[{ required: true, message: 'Site is required for this role' }]}>
                  <Select
                    placeholder={sitesLoading ? 'Loading sites…' : 'Select your site'}
                    loading={sitesLoading}
                    options={(regSites ?? []).map((s) => ({ value: s, label: s }))}
                    notFoundContent="No sites yet — ask an admin to create one"
                  />
                </Form.Item>
              ) : (
                // Global roles (warehouse / logistics) carry no site — optional
                // free-text location instead.
                <Form.Item name="location" label="Location (optional)">
                  <Input prefix={<EnvironmentOutlined />} placeholder="e.g. Central Warehouse, Dammam" />
                </Form.Item>
              )}
              <Form.Item name="phone_number" label="Phone (optional)">
                <Input placeholder="Phone number" />
              </Form.Item>
              <Button type="primary" htmlType="submit" block loading={register.isPending}>
                Request access
              </Button>
              <Button type="link" block onClick={() => setMode('login')}>
                Back to sign in
              </Button>
            </Form>
          ) : !mfaToken ? (
            <Form key="login" layout="vertical" onFinish={onLogin}>
              <Form.Item name="username" rules={[{ required: true, message: 'Username' }]}>
                <Input prefix={<UserOutlined />} placeholder="Username" autoFocus />
              </Form.Item>
              <Form.Item name="password" rules={[{ required: true, message: 'Password' }]}>
                <Input.Password prefix={<LockOutlined />} placeholder="Password" />
              </Form.Item>
              <Button type="primary" htmlType="submit" block loading={loading}>
                Sign in
              </Button>
              <Button type="link" block onClick={() => setMode('register')}>
                Request access
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
        </div>
      </div>
    </ConfigProvider>
  )
}
