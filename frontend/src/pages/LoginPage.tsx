import { Suspense, lazy, useState } from 'react'
import { App, Button, ConfigProvider, Form, Input, Segmented, Select, Tooltip, Typography } from 'antd'
import { EnvironmentOutlined, ExperimentOutlined, LockOutlined, SafetyOutlined, SettingOutlined, ThunderboltOutlined, UserOutlined } from '@ant-design/icons'
import { useAuth } from '../auth/AuthContext'
import { useRegister, useRegisterSites, useRegisterWarehouses } from '../api/hooks'
import { passwordProblems } from '../lib/password'
import { darkTheme, practiceTheme } from '../theme/themes'
import ServerConfigModal from '../components/ServerConfigModal'
import MandatoryEnrollPanel from '../components/MandatoryEnrollPanel'
import { apiBase, isApiOverridden, switchEnvironment } from '../api/client'
import { CURRENT_ENV, type GiEnv } from '../api/environment'
import PracticeBanner, { PracticeBadge } from '../components/PracticeBanner'

// Phase 23f (Q23-13) — Practice only, and off the Live sign-in critical path
const PracticeAccounts = lazy(() => import('../components/PracticeAccounts'))

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Something went wrong'
}

// Self-registrants may request any role except admin. This list is
// hand-maintained rather than fetched, because /auth/register is
// unauthenticated and the role catalogue is not something to publish to
// anyone who loads the login page — but that means ADDING A ROLE TO
// ROLE_META REQUIRES EDITING HERE TOO. `qc` shipped without this line and
// was simply unrequestable: the backend accepted it, the form never offered
// it.
const REGISTER_ROLES = [
  { value: 'store_keeper', label: 'Store Keeper' },
  { value: 'supervisor', label: 'Supervisor' },
  { value: 'hod', label: 'Head of Department' },
  { value: 'warehouse_user', label: 'Warehouse' },
  { value: 'logistics', label: 'Logistics' },
  { value: 'qc', label: 'Quality Control' },
  { value: 'auditor', label: 'Auditor (view-only)' },
]

// T4 — scoped roles MUST pick an admin-created site; unscoped (global) roles
// carry no site and may give a free-text location instead. Mirrors auth.py.
const SCOPED_ROLES = new Set(['store_keeper', 'supervisor', 'hod'])
// QSEP — `qc` is the first DUAL-scope role: a quality inspector belongs to a
// site OR to a warehouse, exactly one. The form therefore cannot be a binary
// scoped/unscoped switch for it; it asks which, then shows that one field.
// auth.py rejects both-or-neither with a 422, so this is the UI agreeing
// with the boundary rather than being it.
const DUAL_SCOPE_ROLES = new Set(['qc'])

export default function LoginPage() {
  const { message } = App.useApp()
  const { login, loginMfa } = useAuth()
  const [loading, setLoading] = useState(false)
  const [mfaToken, setMfaToken] = useState<string | null>(null)
  const [enroll, setEnroll] = useState<{ token: string; from: string | null } | null>(null)
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const register = useRegister()
  const [regForm] = Form.useForm()
  const regRole: string = Form.useWatch('role', regForm) ?? 'store_keeper'
  const isDual = DUAL_SCOPE_ROLES.has(regRole)
  // A dual-scope role picks its own axis; everything else is decided by role.
  const bindTo: 'site' | 'warehouse' = Form.useWatch('bind_to', regForm) ?? 'site'
  const isScoped = SCOPED_ROLES.has(regRole) || (isDual && bindTo === 'site')
  const needsWarehouse = isDual && bindTo === 'warehouse'
  const { data: regSites, isLoading: sitesLoading } = useRegisterSites(mode === 'register')
  const { data: regWarehouses, isLoading: whLoading } =
    useRegisterWarehouses(mode === 'register' && isDual)
  const [serverOpen, setServerOpen] = useState(false)
  const [switching, setSwitching] = useState(false)

  const onLogin = async (v: { username: string; password: string }) => {
    setLoading(true)
    try {
      const r = await login(v.username, v.password)
      if (r.mfa) {
        setMfaToken(r.mfaToken!)
        message.info('Enter your 6-digit authenticator code')
      } else if (r.enroll) {
        // ⚠️ NOT A SESSION. `enrollToken` is scoped to /auth/2fa/* by the
        // server; it is handed to the enrolment panel and never to
        // setAuthToken. See AuthContext.LoginOutcome.
        setEnroll({ token: r.enrollToken!, from: r.enforcedFrom ?? null })
      } else if (r.mfaDue) {
        message.warning(
          `Two-factor authentication becomes mandatory for your role on ${r.mfaDue}. ` +
          'Set it up in Security before then.', 8)
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
    // `bind_to` is a UI-only radio for the dual-scope role — the API takes
    // the binding itself, and sending both site_id and warehouse_id is a 422
    // by design, so the unused one is stripped rather than sent empty.
    const { bind_to: _bindTo, ...payload } = v
    void _bindTo
    if (isDual) {
      if (bindTo === 'site') delete payload.warehouse_id
      else delete payload.site_id
    }
    try {
      await register.mutateAsync(payload)
      message.success('Request submitted — an admin will review it before you can sign in.')
      setMode('login')
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  // The login screen is always navy (the flagship first impression),
  // independent of the in-app light/dark toggle.
  return (
    <ConfigProvider theme={CURRENT_ENV === 'training' ? practiceTheme(darkTheme) : darkTheme}>
      <div className="gi-login">
        {/* Phase 15c: the mark's own band above the card — static, never
            overlapping it. Tier 1's canvas draws into the same band. */}
        <div className="gi-login-crest" aria-hidden="true" />
        {CURRENT_ENV === 'training' && <PracticeBadge floating />}
        <div className="gi-login-card gi-stagger">
          <div className="gi-login-head">
            {/* Server picker: an installed APK/EXE/DMG is built once against
                production, so the only way to test it against the local tunnel
                is a runtime override. Deliberately on the LOGIN screen — the
                server has to be chosen before there is a session. */}
            <Tooltip title="Server configuration">
              <Button type="text" aria-label="Server configuration" className="gi-login-gear"
                icon={<SettingOutlined />} onClick={() => setServerOpen(true)} />
            </Tooltip>
            <div className="gi-wordmark">GI&nbsp;Hub</div>
            <div className="gi-brand-sub">
              {mode === 'register' ? 'ERP CONSOLE — REQUEST ACCESS' : 'ERP CONSOLE — SIGN IN'}
            </div>
          </div>

          {/* Rule 17 — Live | Practice. Chooses which API process this browser
              talks to; switching signs out of the current one and reloads
              (ruling Q3). What the page then SHOWS comes from the server
              (PracticeBanner → GET /instance), not from this control. */}
          {!mfaToken && !enroll && (
            <div className="gi-env-switch">
              <Segmented
                aria-label="Environment"
                value={CURRENT_ENV}
                disabled={switching}
                onChange={(v) => {
                  setSwitching(true)
                  void switchEnvironment(v as GiEnv)
                }}
                options={[
                  { value: 'production', label: 'Live', icon: <ThunderboltOutlined /> },
                  { value: 'training', label: 'Practice', icon: <ExperimentOutlined /> },
                ]}
              />
            </div>
          )}
          {CURRENT_ENV === 'training' && !mfaToken && !enroll && (
            <>
              <PracticeBanner compact />
              <Typography.Paragraph type="secondary" className="gi-practice-hint">
                Sign in with a practice account below (e.g. <code>practice.storekeeper</code>,
                {' '}<code>practice.hod</code>). Your Live password does not work here.
              </Typography.Paragraph>
              <Suspense fallback={null}>
                <PracticeAccounts busy={loading}
                  onSignIn={(username, password) => void onLogin({ username, password })} />
              </Suspense>
            </>
          )}

          {mode === 'register' ? (
            <Form key="register" form={regForm} layout="vertical" onFinish={onRegister}
              initialValues={{ role: 'store_keeper' }}>
              <Form.Item name="username" rules={[{ required: true, message: 'Username' }]}>
                <Input prefix={<UserOutlined />} placeholder="Username" autoFocus />
              </Form.Item>
              {/* The policy IS enforced here — this form SETS a credential.
                  (The sign-in form deliberately does not: existing shorter
                  passwords must still authenticate, and a rule there would
                  lock those users out rather than protect anyone.) The
                  message names the whole requirement at once; the server
                  repeats every failure in its 422 if this is bypassed. */}
              <Form.Item name="password" rules={[
                { required: true, message: 'Password is required' },
                {
                  validator: (_, value: string) => {
                    const problems = passwordProblems(value ?? '')
                    return problems.length
                      ? Promise.reject(new Error('Password must ' + problems.join('; ') + '.'))
                      : Promise.resolve()
                  },
                },
              ]}>
                <Input.Password prefix={<LockOutlined />}
                  placeholder="Password (8+, with A-Z, 0-9 and a symbol)" />
              </Form.Item>
              <Form.Item name="role" label="Requested role" rules={[{ required: true }]}>
                <Select options={REGISTER_ROLES}
                  onChange={() => regForm.setFieldsValue({
                    site_id: undefined, location: undefined,
                    warehouse_id: undefined, bind_to: 'site',
                  })} />
              </Form.Item>
              {isDual && (
                // A quality inspector belongs to a site OR a warehouse —
                // exactly one, and which one decides everything they can see.
                <Form.Item name="bind_to" label="Where do you work?"
                  initialValue="site" tooltip="A quality inspector is based at one site or at one warehouse — not both.">
                  <Segmented block options={[
                    { label: 'At a site', value: 'site' },
                    { label: 'At a warehouse', value: 'warehouse' },
                  ]} />
                </Form.Item>
              )}
              {needsWarehouse ? (
                <Form.Item name="warehouse_id" label="Warehouse"
                  rules={[{ required: true, message: 'Pick the warehouse you work at' }]}>
                  <Select
                    placeholder={whLoading ? 'Loading warehouses…' : 'Select your warehouse'}
                    loading={whLoading}
                    options={(regWarehouses ?? []).map((w) => ({
                      value: w.id, label: w.name ? `${w.id} — ${w.name}` : w.id }))}
                    notFoundContent="No warehouses yet — ask an admin to create one"
                  />
                </Form.Item>
              ) : isScoped ? (
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
              <Form.Item name="phone_number" label="Phone (optional)"
                rules={[{ pattern: /^\+[0-9][0-9\s()-]{7,18}$/, message: 'Use +<country code><number>, e.g. +966512345678' }]}>
                <Input placeholder="+966512345678" inputMode="tel" />
              </Form.Item>
              <Button type="primary" htmlType="submit" block loading={register.isPending}>
                Request access
              </Button>
              <Button type="link" block onClick={() => setMode('login')}>
                Back to sign in
              </Button>
            </Form>
          ) : enroll ? (
            <MandatoryEnrollPanel
              token={enroll.token}
              enforcedFrom={enroll.from}
              onDone={() => {
                setEnroll(null)
                message.success('Two-factor authentication is on. Sign in again with your code.')
              }}
            />
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
          {isApiOverridden() && (
            <Typography.Paragraph type="warning" style={{ fontSize: 12, textAlign: 'center', marginTop: 12, marginBottom: 0 }}>
              Connected to {apiBase()}
            </Typography.Paragraph>
          )}
        </div>
        <ServerConfigModal open={serverOpen} onClose={() => setServerOpen(false)} />
      </div>
    </ConfigProvider>
  )
}
