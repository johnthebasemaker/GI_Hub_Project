import { useState } from 'react'
import { Alert, App, Button, Card, Input, Space, Tag, Typography } from 'antd'
import { SafetyCertificateOutlined } from '@ant-design/icons'
import { use2faStatus, useDisable2fa, useEnroll2fa, useVerify2fa } from '../api/hooks'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

interface Enrollment { secret: string; otpauth_uri: string; qr: string }

export default function SecurityPage() {
  const { message } = App.useApp()
  const { data: enabled, isLoading } = use2faStatus()
  const enroll = useEnroll2fa()
  const verify = useVerify2fa()
  const disable = useDisable2fa()

  const [pending, setPending] = useState<Enrollment | null>(null)
  const [code, setCode] = useState('')
  const [disableCode, setDisableCode] = useState('')

  const startEnroll = async () => {
    try { setPending(await enroll.mutateAsync()) }
    catch (e) { message.error(errMsg(e)) }
  }
  const doVerify = async () => {
    try {
      await verify.mutateAsync(code.trim())
      message.success('Two-factor authentication enabled')
      setPending(null); setCode('')
    } catch (e) { message.error(errMsg(e)) }
  }
  const doDisable = async () => {
    try {
      await disable.mutateAsync(disableCode.trim())
      message.success('Two-factor authentication disabled')
      setDisableCode('')
    } catch (e) { message.error(errMsg(e)) }
  }

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        <SafetyCertificateOutlined /> Security
      </Typography.Title>
      <Card style={{ maxWidth: 560 }} loading={isLoading}
        title={
          <Space>
            Two-Factor Authentication
            {enabled ? <Tag color="green">ON</Tag> : <Tag>OFF</Tag>}
          </Space>
        }>
        {enabled ? (
          <>
            <Typography.Paragraph type="secondary">
              2FA is active on your account. To turn it off, enter a current code from your
              authenticator app.
            </Typography.Paragraph>
            <Space.Compact style={{ width: '100%', maxWidth: 320 }}>
              <Input placeholder="6-digit code" value={disableCode} maxLength={6}
                onChange={(e) => setDisableCode(e.target.value)} />
              <Button danger loading={disable.isPending} disabled={disableCode.length < 6} onClick={doDisable}>
                Disable
              </Button>
            </Space.Compact>
          </>
        ) : pending ? (
          <>
            <Typography.Paragraph type="secondary">
              Scan this QR in your authenticator app (or enter the key manually), then confirm
              a code to turn on 2FA.
            </Typography.Paragraph>
            <div style={{ textAlign: 'center', marginBottom: 12 }}>
              <img src={pending.qr} alt="2FA QR code" width={180} height={180}
                style={{ border: '1px solid #f0f0f0', borderRadius: 8 }} />
            </div>
            <Alert type="info" showIcon style={{ marginBottom: 12 }}
              title={<span>Manual key: <Typography.Text code copyable>{pending.secret}</Typography.Text></span>} />
            <Space.Compact style={{ width: '100%', maxWidth: 320 }}>
              <Input placeholder="6-digit code" value={code} maxLength={6}
                onChange={(e) => setCode(e.target.value)} onPressEnter={doVerify} />
              <Button type="primary" loading={verify.isPending} disabled={code.length < 6} onClick={doVerify}>
                Verify &amp; enable
              </Button>
            </Space.Compact>
          </>
        ) : (
          <>
            <Typography.Paragraph type="secondary">
              Add a second factor to your login. You'll need an authenticator app (Google
              Authenticator, Authy, 1Password, …).
            </Typography.Paragraph>
            <Button type="primary" loading={enroll.isPending} onClick={startEnroll}>
              Set up 2FA
            </Button>
          </>
        )}
      </Card>
    </div>
  )
}
