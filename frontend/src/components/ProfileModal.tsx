import { useEffect, useState } from 'react'
import { App, Alert, Button, Form, Input, Modal, Space, Typography } from 'antd'
import { MobileOutlined, SafetyOutlined } from '@ant-design/icons'
import { useMyPhone, useRequestPhoneOtp, useVerifyPhoneOtp } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'

/**
 * Self-service profile modal — lets any user verify a new phone number via a
 * 6-digit WhatsApp OTP. Step 1: enter the new number → a code is sent to it.
 * Step 2: enter the code → the number is saved. The number only changes after
 * the code verifies (admins can set it directly from User Management, no OTP).
 */
export default function ProfileModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { message } = App.useApp()
  const { user } = useAuth()
  const { data: phone, isFetching, isError, refetch } = useMyPhone()
  const requestOtp = useRequestPhoneOtp()
  const verifyOtp = useVerifyPhoneOtp()

  const [step, setStep] = useState<'enter' | 'verify'>('enter')
  const [newNumber, setNewNumber] = useState('')
  const [code, setCode] = useState('')

  // Reset the flow + refresh the number every time the modal is (re)opened.
  useEffect(() => {
    if (open) { setStep('enter'); setNewNumber(''); setCode(''); void refetch() }
  }, [open, refetch])

  // Strict global format: +<country_code><number> (spaces/dashes tolerated).
  const validNumber = /^\+?[0-9][0-9\s()-]{7,18}$/.test(newNumber.trim())

  const send = async () => {
    try {
      const res = await requestOtp.mutateAsync(newNumber)
      if (res.sent) {
        message.success('Verification code sent via WhatsApp')
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
      message.success(`Phone number updated to ${res.phone_number}`)
      onClose()
    } catch (e: unknown) {
      message.error(errMsg(e) ?? 'Verification failed')
    }
  }

  return (
    <Modal open={open} onCancel={onClose} footer={null} title="My profile" destroyOnClose>
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
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
              message="Could not load your phone number — the API may be running an older build. Restart the backend and try again." />
          )}
        </div>

        {step === 'enter' ? (
          <Form layout="vertical" onFinish={send}>
            <Form.Item label="New phone number (international format)"
              validateStatus={newNumber && !validNumber ? 'error' : undefined}
              help={newNumber && !validNumber
                ? 'Use +<country code><number>, e.g. +966512345678'
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
            <Alert type="info" showIcon style={{ marginBottom: 12 }}
              message={`Enter the 6-digit code sent to ${newNumber} on WhatsApp.`} />
            <Form.Item label="Verification code">
              <Input prefix={<SafetyOutlined />} inputMode="numeric" maxLength={6}
                placeholder="000000" value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                onPressEnter={verify} />
            </Form.Item>
            <Space>
              <Button type="primary" onClick={verify} loading={verifyOtp.isPending}
                disabled={code.length !== 6}>
                Verify &amp; save
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
