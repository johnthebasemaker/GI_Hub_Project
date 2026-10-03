import { useCallback, useEffect, useRef, useState } from 'react'
import type { InputRef } from 'antd'
import { Button, Input, Space, Tooltip, Typography } from 'antd'
import { CameraOutlined, MutedOutlined, ScanOutlined, SoundOutlined } from '@ant-design/icons'
import QrScanner from './QrScanner'
import { BARCODE_FORMATS } from '../lib/barcode'
import { isMuted, scanFeedback, setMuted } from '../lib/scanFeedback'
import type { ScanTone } from '../lib/scanFeedback'
import { status as statusColors } from '../theme/tokens'

// ScanBox — one always-ready field for a scan (Phase 18 Track 3).
//
// ⚠️ A HANDHELD SCANNER IS A KEYBOARD. A USB/Bluetooth "keyboard-wedge" scanner
// types the code and presses Enter into whatever has focus — so the field that
// receives scans must HAVE focus, before the first scan and again after every
// one. QrScanner's manual field had no autofocus, so a wedge scan went nowhere
// unless somebody clicked first. This box focuses itself on mount, after every
// scan and when the window regains focus, and never steals focus from another
// text field the user is typing in.
//
// The caller resolves the code and answers with a tone; the box plays it
// (sound + vibration, lib/scanFeedback) and flashes its border, so a hit and a
// miss are told apart without reading anything.

export interface ScanResult {
  tone: ScanTone
  message?: string
}

interface Props {
  onScan: (code: string) => Promise<ScanResult | void> | ScanResult | void
  /** Enter on an EMPTY box — e.g. "confirm the return shown below". */
  onEmptyEnter?: () => void
  placeholder?: string
  cameraTitle?: string
  autoFocus?: boolean
  disabled?: boolean
  /** Shown under the box until the first scan. */
  hint?: string
  testId?: string
}

export default function ScanBox({
  onScan, onEmptyEnter, placeholder = 'Scan or type a code, then Enter',
  cameraTitle = 'Scan with the camera', autoFocus = true, disabled, hint, testId = 'scan-box',
}: Props) {
  const ref = useRef<InputRef>(null)
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [camera, setCamera] = useState(false)
  const [flash, setFlash] = useState<ScanTone | null>(null)
  const [last, setLast] = useState<string>('')
  const [muted, setMutedState] = useState(isMuted())
  const timer = useRef<number>(0)

  const focus = useCallback(() => {
    const active = document.activeElement as HTMLElement | null
    // Never pull focus out of another field the user is typing in.
    const typing = active && active !== document.body
      && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)
      && active !== ref.current?.input
    if (!typing) ref.current?.focus()
  }, [])

  useEffect(() => {
    if (!autoFocus) return
    focus()
    const onWin = () => window.setTimeout(focus, 0)
    window.addEventListener('focus', onWin)
    return () => window.removeEventListener('focus', onWin)
  }, [autoFocus, focus])

  useEffect(() => () => window.clearTimeout(timer.current), [])

  const run = async (raw: string) => {
    const code = raw.trim()
    if (!code || busy) return
    setBusy(true)
    let res: ScanResult | void
    try {
      res = await onScan(code)
    } catch (e) {
      const x = e as { response?: { data?: { detail?: string } }; message?: string }
      res = { tone: 'error', message: x?.response?.data?.detail ?? x?.message ?? 'Scan failed' }
    }
    const tone = res?.tone ?? 'ok'
    scanFeedback(tone)
    setFlash(tone)
    setLast(res?.message ?? '')
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setFlash(null), 1400)
    setValue('')
    setBusy(false)
    window.setTimeout(focus, 0)
  }

  const border = flash === 'ok' ? statusColors.ok
    : flash === 'error' ? statusColors.critical
      : flash === 'info' ? statusColors.info : undefined

  return (
    <div data-testid={testId}>
      <Space.Compact style={{ width: '100%' }}>
        <Input
          ref={ref}
          size="large"
          value={value}
          disabled={disabled}
          prefix={<ScanOutlined />}
          placeholder={placeholder}
          aria-label={placeholder}
          data-testid={`${testId}-input`}
          data-flash={flash ?? ''}
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => setValue(e.target.value)}
          onPressEnter={() => {
            if (value.trim()) void run(value)
            else onEmptyEnter?.()
          }}
          style={border ? { borderColor: border, boxShadow: `0 0 0 2px ${border}55` } : undefined}
        />
        <Tooltip title={cameraTitle}>
          <Button size="large" icon={<CameraOutlined />} disabled={disabled}
            aria-label={cameraTitle} onClick={() => setCamera(true)} />
        </Tooltip>
        <Tooltip title={muted ? 'Scan sounds off' : 'Scan sounds on'}>
          <Button size="large" aria-label="Toggle scan sounds"
            icon={muted ? <MutedOutlined /> : <SoundOutlined />}
            onClick={() => { setMuted(!muted); setMutedState(!muted); focus() }} />
        </Tooltip>
      </Space.Compact>
      <Typography.Text type={flash === 'error' ? 'danger' : 'secondary'}
        style={{ display: 'block', marginTop: 4, minHeight: 22 }} data-testid={`${testId}-msg`}>
        {last || hint || ''}
      </Typography.Text>
      <QrScanner open={camera} title={cameraTitle} formats={BARCODE_FORMATS}
        manualPlaceholder="…or type the code"
        onClose={() => { setCamera(false); window.setTimeout(focus, 0) }}
        onDecode={(text) => { setCamera(false); void run(text) }} />
    </div>
  )
}
