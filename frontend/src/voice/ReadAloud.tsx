import { useEffect, useRef, useState } from 'react'
import { Button, Dropdown, Tooltip } from 'antd'
import { PauseCircleOutlined, SoundOutlined } from '@ant-design/icons'

/**
 * Phase 23e — 🔊 read aloud with the DEVICE's own voice (ruling Q23-11a).
 * On-device voices only (`localService`): a cloud voice would send the words
 * away. Stops when the component goes (the page changes) or is pressed again.
 */
const SPEEDS = [0.8, 1, 1.25]

export function speechSupported(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

function localVoice(): SpeechSynthesisVoice | null {
  const vs = window.speechSynthesis.getVoices().filter((v) => v.localService && /^en/i.test(v.lang))
  return vs.find((v) => /Samantha|Daniel|Karen|Moira|Serena/i.test(v.name)) ?? vs[0] ?? null
}

export function speak(text: string, rate = 1, onEnd?: () => void): boolean {
  if (!speechSupported() || !text.trim()) return false
  window.speechSynthesis.cancel()
  const v = localVoice()
  if (!v) return false
  const u = new SpeechSynthesisUtterance(text)
  u.voice = v
  u.lang = v.lang
  u.rate = rate
  u.onend = () => onEnd?.()
  u.onerror = () => onEnd?.()
  window.speechSynthesis.speak(u)
  return true
}

export default function ReadAloud({ text, size = 'small', testId = 'read-aloud', label }: {
  text: string | (() => string); size?: 'small' | 'middle'; testId?: string; label?: string
}) {
  const [on, setOn] = useState(false)
  const [rate, setRate] = useState(1)
  const mounted = useRef(true)
  useEffect(() => () => {
    mounted.current = false
    try { window.speechSynthesis?.cancel() } catch { /* nothing playing */ }
  }, [])
  if (!speechSupported()) return null
  const go = () => {
    if (on) { window.speechSynthesis.cancel(); setOn(false); return }
    const t = typeof text === 'function' ? text() : text
    if (speak(t, rate, () => { if (mounted.current) setOn(false) })) setOn(true)
  }
  return (
    <Dropdown trigger={['contextMenu']} menu={{
      selectable: true, selectedKeys: [String(rate)],
      items: SPEEDS.map((s) => ({ key: String(s), label: `${s}× speed` })),
      onClick: ({ key }) => setRate(Number(key)),
    }}>
      <Tooltip title={on ? 'Stop reading' : `Read aloud (${rate}×) — right-click for speed`}>
        <Button size={size} type="text" data-testid={testId} aria-pressed={on}
          aria-label={on ? 'Stop reading aloud' : 'Read aloud'}
          icon={on ? <PauseCircleOutlined /> : <SoundOutlined />} onClick={go}>{label}</Button>
      </Tooltip>
    </Dropdown>
  )
}
