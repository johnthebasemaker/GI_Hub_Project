import { useEffect, useRef, useState } from 'react'
import { App, Button, Tooltip } from 'antd'
import { AudioOutlined, LoadingOutlined } from '@ant-design/icons'
import { status as tone } from '../theme/tokens'
import { MAX_SECONDS, Recording, micSupported } from './recorder'
import { transcribe, useSttStatus } from './useStt'

/**
 * Phase 23e — dictate with Whistle, on this machine (rulings Q23-10..12).
 * Tap to start, tap to stop (it stops by itself at 30 s). The words come back
 * to `onText` — they are put in the box for the person to read; nothing is
 * ever SENT by speaking. Hidden when the server has no voice model or the
 * browser has no microphone. English only.
 */
export default function MicButton({ onText, size = 'middle', testId = 'mic' }: {
  onText: (text: string) => void; size?: 'small' | 'middle'; testId?: string
}) {
  const { message } = App.useApp()
  const { data } = useSttStatus()
  const [state, setState] = useState<'idle' | 'rec' | 'work'>('idle')
  const [secs, setSecs] = useState(0)
  const [level, setLevel] = useState(0)
  const rec = useRef<Recording | null>(null)
  const timer = useRef<number | null>(null)

  useEffect(() => () => { rec.current?.release(); if (timer.current) window.clearInterval(timer.current) }, [])

  if (!data?.available || !micSupported()) return null

  const stop = async () => {
    if (!rec.current) return
    if (timer.current) { window.clearInterval(timer.current); timer.current = null }
    setState('work')
    try {
      const wav = await rec.current.stop()
      const text = await transcribe(wav)
      if (text) onText(text)
      else message.info('Nothing was heard — try again a little closer to the microphone')
    } catch (e) {
      message.error((e as Error)?.message || 'Could not understand the recording')
    } finally {
      rec.current = null
      setState('idle')
      setLevel(0)
    }
  }

  const start = async () => {
    try {
      rec.current = new Recording()
      await rec.current.start(setLevel)
      setSecs(0)
      setState('rec')
      const t0 = Date.now()
      timer.current = window.setInterval(() => {
        const s = Math.floor((Date.now() - t0) / 1000)
        setSecs(s)
        if (s >= MAX_SECONDS) void stop()
      }, 250)
    } catch {
      rec.current?.release()
      rec.current = null
      message.error('The microphone is not available — allow it in the browser and try again')
    }
  }

  const recording = state === 'rec'
  return (
    <Tooltip title={recording ? `Listening… ${secs}s — tap to stop` : state === 'work' ? 'Writing it down…'
      : 'Speak instead of typing (English)'}>
      <Button size={size} data-testid={testId} aria-pressed={recording}
        aria-label={recording ? 'Stop dictating' : 'Dictate'}
        icon={state === 'work' ? <LoadingOutlined /> : <AudioOutlined />}
        disabled={state === 'work'}
        onClick={() => (recording ? void stop() : void start())}
        style={recording ? { color: tone.critical, borderColor: tone.critical,
          boxShadow: `0 0 0 ${Math.round(2 + level * 6)}px color-mix(in srgb, ${tone.critical} 25%, transparent)` } : undefined}>
        {recording ? `${secs}s` : null}
      </Button>
    </Tooltip>
  )
}
