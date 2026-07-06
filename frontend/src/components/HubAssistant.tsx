import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Input, Space, Tooltip, Typography } from 'antd'
import { CloseOutlined, RobotOutlined, SendOutlined } from '@ant-design/icons'
import { api, getAuthToken } from '../api/client'

interface Msg { who: 'user' | 'ai'; text: string }
interface AiHealth { ok: boolean; enabled: boolean; message: string }

// Floating Hub Assistant — role-gated Q&A over the user manual, streamed over
// SSE. axios buffers responses, so the stream uses fetch + ReadableStream with
// the bearer token attached by hand; the panel checks /ai/health on open and
// degrades to a friendly notice when the local AI is offline or disabled.
export default function HubAssistant() {
  const [open, setOpen] = useState(false)
  const [health, setHealth] = useState<AiHealth | null>(null)
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [queued, setQueued] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    api.get<AiHealth>('/ai/health')
      .then((r) => setHealth(r.data))
      .catch(() => setHealth({ ok: false, enabled: true, message: 'AI status unavailable.' }))
  }, [open])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [msgs])

  useEffect(() => () => abortRef.current?.abort(), [])

  const appendToLast = (text: string) =>
    setMsgs((m) => m.map((x, i) => (i === m.length - 1 ? { ...x, text: x.text + text } : x)))

  async function ask() {
    const question = q.trim()
    if (!question || busy) return
    setQ('')
    setBusy(true)
    setQueued(false)
    setMsgs((m) => [...m, { who: 'user', text: question }, { who: 'ai', text: '' }])
    const ctrl = new AbortController()
    abortRef.current = ctrl
    try {
      const res = await fetch('/api/ai/assistant', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${getAuthToken() ?? ''}`,
        },
        body: JSON.stringify({ question }),
        signal: ctrl.signal,
      })
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        // SSE frames are separated by a blank line; keep the tail partial.
        const frames = buf.split('\n\n')
        buf = frames.pop() ?? ''
        for (const f of frames) {
          const line = f.split('\n').find((l) => l.startsWith('data: '))
          if (!line) continue
          try {
            const ev = JSON.parse(line.slice(6))
            if (ev.status === 'queued') setQueued(true)
            if (ev.token) { setQueued(false); appendToLast(ev.token) }
            if (ev.error) appendToLast(ev.error)
          } catch { /* ignore malformed frame */ }
        }
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        appendToLast('⚠️ The assistant is unreachable right now — please try again shortly.')
      }
    } finally {
      setBusy(false)
      setQueued(false)
      abortRef.current = null
    }
  }

  if (!open) {
    return (
      <Tooltip title="Ask Hub Assistant" placement="left">
        <Button
          type="primary" shape="circle" size="large" icon={<RobotOutlined />}
          aria-label="Open Hub Assistant"
          style={{ position: 'fixed', right: 24, bottom: 24, zIndex: 900,
                   width: 52, height: 52, boxShadow: '0 4px 16px rgba(0,0,0,0.35)' }}
          onClick={() => setOpen(true)}
        />
      </Tooltip>
    )
  }

  return (
    <Card
      size="small"
      title={<Space><RobotOutlined /> Hub Assistant</Space>}
      extra={<Button type="text" size="small" icon={<CloseOutlined />}
        aria-label="Close" onClick={() => { abortRef.current?.abort(); setOpen(false) }} />}
      style={{ position: 'fixed', right: 24, bottom: 24, zIndex: 900, width: 380,
               boxShadow: '0 8px 32px rgba(0,0,0,0.45)' }}
      styles={{ body: { padding: 12 } }}
    >
      {health && !health.ok && (
        <Alert type="warning" showIcon style={{ marginBottom: 8 }}
          title={health.enabled ? 'Local AI is offline' : 'AI is switched off'}
          description={health.message} />
      )}
      <div ref={scrollRef} style={{ height: 300, overflowY: 'auto', marginBottom: 8,
                                    display: 'flex', flexDirection: 'column', gap: 8 }}>
        {msgs.length === 0 && (
          <Typography.Text type="secondary" style={{ fontSize: 12.5 }}>
            Ask anything about the part of the system you can use — answers come
            from the user manual via the local AI. e.g. “How do I stage a return?”
          </Typography.Text>
        )}
        {msgs.map((m, i) => (
          <div key={i} style={{
            alignSelf: m.who === 'user' ? 'flex-end' : 'flex-start',
            maxWidth: '85%', padding: '6px 10px', borderRadius: 8,
            fontSize: 12.5, whiteSpace: 'pre-wrap',
            background: m.who === 'user' ? 'var(--gi-gold, #C9A227)' : 'rgba(128,128,128,0.15)',
            color: m.who === 'user' ? '#001F40' : undefined,
          }}>
            {m.text || (busy && i === msgs.length - 1
              ? (queued ? 'Waiting for a free AI slot…' : 'Thinking…') : '')}
          </div>
        ))}
      </div>
      <Space.Compact style={{ width: '100%' }}>
        <Input
          placeholder="Ask the manual…" value={q} disabled={busy || (health ? !health.ok : false)}
          onChange={(e) => setQ(e.target.value)} onPressEnter={ask} maxLength={500}
        />
        <Button type="primary" icon={<SendOutlined />} onClick={ask}
          loading={busy} disabled={health ? !health.ok : false} aria-label="Send" />
      </Space.Compact>
    </Card>
  )
}
