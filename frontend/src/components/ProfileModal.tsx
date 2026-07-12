import { useEffect, useState } from 'react'
import { App, Alert, Button, Form, Input, Modal, Space, Steps, Typography } from 'antd'
import { MobileOutlined, SafetyOutlined } from '@ant-design/icons'
import { useMyPhone, useRequestPhoneOtp, useVerifyPhoneOtp } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'

/**
 * Self-service profile modal — dual-OTP phone change over WhatsApp.
 * With a number on file: code 1 goes to the OLD (current) number to authorize
 * the change, then code 2 goes to the NEW number to prove it can actually
 * receive WhatsApp; the database only updates after code 2 verifies (a typo
 * can never lock the user out). First-time setup skips code 1. Admins can set
 * numbers directly from User Management, no OTP. Profile/OTP requests always
 * send immediately — the WhatsApp delivery preference lives on the material
 * transaction forms (issue / receive / return), not here.
 */
export default function ProfileModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { message } = App.useApp()
  const { user } = useAuth()
  const { data: phone, isFetching, isError, refetch } = useMyPhone()
  const requestOtp = useRequestPhoneOtp()
  const verifyOtp = useVerifyPhoneOtp()

  const [step, setStep] = useState<'enter' | 'verify'>('enter')
  // Which device the pending code went to: 'old' = current number, 'new' = new number.
  const [stage, setStage] = useState<'old' | 'new'>('new')
  const [newNumber, setNewNumber] = useState('')
  const [code, setCode] = useState('')

  // Reset the flow + refresh the number every time the modal is (re)opened.
  useEffect(() => {
    if (open) { setStep('enter'); setStage('new'); setNewNumber(''); setCode(''); void refetch() }
  }, [open, refetch])

  // Strict global format: +<country_code><number> (spaces/dashes tolerated).
  const validNumber = /^\+?[0-9][0-9\s()-]{7,18}$/.test(newNumber.trim())

  const send = async () => {
    try {
      const res = await requestOtp.mutateAsync(newNumber)
      if (res.sent) {
        const st = res.stage === 'old' ? 'old' : 'new'
        setStage(st)
        message.success(st === 'old'
          ? 'Step 1 of 2 — authorization code sent to your CURRENT number on WhatsApp'
          : 'Verification code sent to the new number via WhatsApp')
        setCode('')
        setStep('verify')
      } else {
        const detail = (res as { error?: string }).error
        message.warning(detail ? `Could not send the code — ${detail}` : 'Could not send the code — check the number or try again')
      }
    } catch (e: unknown) {
      message.error(errMsg(e) ?? 'Could not send the code')
    }
  }

  const verify = async () => {
    try {
      const res = await verifyOtp.mutateAsync({ new_number: newNumber, code })
      if (res.updated) {
        message.success(`Phone number updated to ${res.phone_number}`)
        onClose()
        return
      }
      // Stage 1 (old number) passed — a second code is on its way to the NEW number.
      if (res.sent) {
        setStage('new')
        setCode('')
        message.success('Step 2 of 2 — a second code was sent to the NEW number on WhatsApp')
      } else {
        const detail = (res as { error?: string }).error
        message.warning(detail
          ? `Authorized, but the code to the new number failed — ${detail}`
          : 'Authorized, but the code to the new number could not be sent — check the number and restart')
        setStep('enter')
      }
    } catch (e: unknown) {
      message.error(errMsg(e) ?? 'Verification failed')
    }
  }

  const twoStep = Boolean(phone) // a number on file means the dual-OTP path

  return (
    <Modal open={open} onCancel={onClose} footer={null} title="My profile" destroyOnHidden>
      <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
        <div>
          <Typography.Text type="secondary">Signed in as</Typography.Text>
          <div style={{ fontWeight: 600 }}>{user?.label} · {user?.username}</div>
        </div>
        <div>
          <Typography.Text type="secondary">Phone number on file</Typography.Text>
          <div style={{ fontWeight: 600 }}>
            {isFetching ? '…' : isError ? '—' : (phone || 'none set')}
          </div>
          {isError && (
            <Alert type="error" showIcon style={{ marginTop: 6 }}
              title="Could not load your phone number — the API may be running an older build. Restart the backend and try again." />
          )}
        </div>

        {step === 'enter' ? (
          <Form layout="vertical" onFinish={send}>
            <Form.Item label="New phone number (international format)"
              validateStatus={newNumber && !validNumber ? 'error' : undefined}
              help={newNumber && !validNumber
                ? 'Use +<country code><number>, e.g. +966512345678'
                : phone
                  ? 'Two-step verification: a code to your CURRENT number authorizes the change, then a second code verifies the NEW number before it is saved.'
                  : 'Example: +966512345678. A 6-digit code will be sent to this number on WhatsApp.'}>
              <Input prefix={<MobileOutlined />} inputMode="tel" placeholder="+966512345678"
                value={newNumber} onChange={(e) => setNewNumber(e.target.value)}
                onPressEnter={send} allowClear />
            </Form.Item>
            <Button type="primary" onClick={send} loading={requestOtp.isPending}
              disabled={!validNumber || newNumber.replace(/\D/g, '').length < 8} block>
              Send verification code
            </Button>
          </Form>
        ) : (
          <Form layout="vertical" onFinish={verify}>
            {twoStep && (
              <Steps size="small" style={{ marginBottom: 12 }}
                current={stage === 'old' ? 0 : 1}
                items={[
                  { title: 'Authorize', description: 'code to current number' },
                  { title: 'Verify new', description: 'code to new number' },
                ]} />
            )}
            <Alert type="info" showIcon style={{ marginBottom: 12 }}
              title={stage === 'old'
                ? `Enter the 6-digit code sent to your CURRENT number ${phone || ''} on WhatsApp.`
                : `Enter the 6-digit code sent to the NEW number ${newNumber} on WhatsApp.`} />
            <Form.Item label="Verification code">
              <Input prefix={<SafetyOutlined />} inputMode="numeric" maxLength={6}
                placeholder="000000" value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                onPressEnter={verify} />
            </Form.Item>
            <Space>
              <Button type="primary" onClick={verify} loading={verifyOtp.isPending}
                disabled={code.length !== 6}>
                {stage === 'old' ? 'Authorize change' : 'Verify & save'}
              </Button>
              <Button type="link" onClick={() => setStep('enter')}>Use a different number</Button>
            </Space>
          </Form>
        )}
      </Space>
    </Modal>
  )
}

function errMsg(e: unknown): string | undefined {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof detail === 'string' ? detail : undefined
}
