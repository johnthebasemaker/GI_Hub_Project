/**
 * Phase 21f — the self-driving demo, on screen: the chooser, the gold ring and
 * spotlight, the subtitles and the controls (Pause · Skip · Voice · Stop, Esc).
 *
 * LAZY and PRACTICE-ONLY: AppLayout imports this only behind `isPractice()`,
 * on the first click of ▶ Auto demo (or the assistant's ▶ Run this demo), so
 * the Live app and the login page carry none of it (rule 17, zero growth).
 *
 * Touching the mouse or the keyboard while it runs PAUSES it ("You took over
 * — Resume?"): the demo is a guide, never something fighting the person at
 * the keyboard. Only trusted (human) events count — the runner's own clicks
 * are not.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { App, Button, Card, Modal, Popconfirm, Segmented, Space, Switch, Typography } from 'antd'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { DemoRunner } from './engine'
import type { RunState } from './engine'
import { SCRIPTS, scriptById } from './scripts'
import { Voice } from './voice'
import './demo.css'

export interface DemoRequest { n: number; id?: string; focus?: string }

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

function readFlag(key: string): boolean {
  try { return localStorage.getItem(key) === '1' } catch { return false }
}
function writeFlag(key: string, on: boolean) {
  try { localStorage.setItem(key, on ? '1' : '0') } catch { /* private mode */ }
}

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

export default function DemoHost({ request, onClose }: { request: DemoRequest; onClose: () => void }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { user } = useAuth()
  const { message } = App.useApp()
  const voice = useMemo(() => {
    const v = new Voice()
    v.fast = readFlag('gi-demo-fast')       // E2E / rehearsal
    v.muted = readFlag('gi-demo-muted')
    return v
  }, [])

  const [chooser, setChooser] = useState(false)
  const [runner, setRunner] = useState<DemoRunner | null>(null)
  const [title, setTitle] = useState('')
  const [state, setState] = useState<RunState | null>(null)
  const [detail, setDetail] = useState<string | undefined>()
  const [sentence, setSentence] = useState('')
  const [step, setStep] = useState<[number, number]>([0, 0])
  const [target, setTarget] = useState<Element | null>(null)
  const [pulse, setPulse] = useState(0)
  const [rect, setRect] = useState<DOMRect | null>(null)
  const [muted, setMuted] = useState(voice.muted)
  const [speed, setSpeed] = useState(1)
  const [tookOver, setTookOver] = useState(false)
  const [resetting, setResetting] = useState(false)
  const panel = useRef<HTMLDivElement>(null)
  const ticket = useRef<string | null>(null)

  const start = useCallback(async (id: string) => {
    const script = scriptById(id)
    if (!script) { message.warning('That demo is not available.'); return }
    setChooser(false)
    try {
      await voice.load()
      ticket.current = (await api.post<{ ticket: string }>('/practice/demo/start')).data.ticket
    } catch (e) {
      message.error(errMsg(e))
      return
    }
    const r = new DemoRunner(script, voice, {
      navigate: (p) => navigate(p),
      switchRole: async (role) => {
        const { data } = await api.post('/practice/demo/switch', { ticket: ticket.current, role })
        window.dispatchEvent(new CustomEvent('gi-session-adopt', { detail: data }))
        qc.clear()                     // the previous role's cached pages are not this role's
        await sleep(voice.fast ? 150 : 600)
        return String(data?.user?.username ?? '')
      },
      onBeat: (i, total, s) => { setStep([i + 1, total]); setSentence(s) },
      onTarget: (el, p) => { setTarget(el); if (p) setPulse((n) => n + 1) },
      onState: (s, d) => { setState(s); setDetail(d); if (s === 'running') setTookOver(false) },
    })
    setTitle(script.title)
    setRunner(r)
    void r.run()
  }, [message, navigate, qc, voice])

  // a new request (▶ Auto demo, or the assistant's ▶ Run this demo)
  useEffect(() => {
    if (runner && (state === 'running' || state === 'paused')) return
    if (request.id) void start(request.id)
    else setChooser(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on a NEW request
  }, [request.n])

  // the spotlight follows its element through scrolling and resizing
  useEffect(() => {
    if (!target) { setRect(null); return }
    let raf = 0
    const measure = () => { raf = 0; setRect(target.getBoundingClientRect()) }
    const queue = () => { if (!raf) raf = requestAnimationFrame(measure) }
    measure()
    window.addEventListener('scroll', queue, true)
    window.addEventListener('resize', queue)
    const iv = setInterval(queue, 500)          // late layout (a table loading)
    return () => {
      window.removeEventListener('scroll', queue, true)
      window.removeEventListener('resize', queue)
      clearInterval(iv)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [target])

  // you took over: a human click or key pauses it; Esc stops it
  useEffect(() => {
    if (!runner || state !== 'running') return
    const onInput = (e: Event) => {
      if (!e.isTrusted) return
      if (panel.current?.contains(e.target as Node)) return
      if (e instanceof KeyboardEvent && e.key === 'Escape') { runner.stop(); return }
      runner.pause()
      setTookOver(true)
    }
    window.addEventListener('pointerdown', onInput, true)
    window.addEventListener('keydown', onInput, true)
    return () => {
      window.removeEventListener('pointerdown', onInput, true)
      window.removeEventListener('keydown', onInput, true)
    }
  }, [runner, state])

  useEffect(() => () => { runner?.stop() }, [runner])

  const close = () => {
    runner?.stop()
    setRunner(null)
    setState(null)
    setTarget(null)
    onClose()
  }

  const resetData = async () => {
    setResetting(true)
    try {
      const { data } = await api.post<{ removed: Record<string, number> }>('/practice/demo/reset')
      const n = Object.entries(data.removed ?? {}).filter(([k]) => k !== 'sqm_reversed')
        .reduce((a, [, v]) => a + (v || 0), 0)
      message.success(n ? `Demo data cleared — ${n} demo entr${n === 1 ? 'y' : 'ies'} removed` : 'There was no demo data to clear')
      qc.invalidateQueries()
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setResetting(false)
    }
  }

  const canReset = user?.role === 'hod' || user?.role === 'admin'
  const mine = SCRIPTS.filter((s) => user && (s.roles.includes(user.role) || user.role === 'admin'))
  const live = runner && state != null
  const pad = 6

  return (
    <>
      <Modal open={chooser} title="Auto demo — Practice" footer={null} onCancel={close} width={560}
        destroyOnHidden>
        <Typography.Paragraph type="secondary">
          The app runs itself on Practice data, switching roles as the work moves (never the
          admin account), and says what it is doing. Touch the mouse or keyboard to pause it; Esc stops it.
        </Typography.Paragraph>
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          {mine.map((s) => (
            <Card key={s.id} size="small" title={s.title}
              style={request.focus === s.id ? { borderColor: 'var(--gi-gold)' } : undefined}
              extra={<Button type="primary" data-testid={`demo-start-${s.id}`}
                onClick={() => void start(s.id)}>▶ Start</Button>}>
              <Typography.Text type="secondary">{s.blurb}</Typography.Text>
            </Card>
          ))}
          {!mine.length && <Typography.Text>No demo starts from this role yet.</Typography.Text>}
          <Space wrap size={16}>
            <span>Voice <Switch size="small" checked={!muted} data-testid="demo-voice"
              onChange={(on) => { setMuted(!on); voice.muted = !on; writeFlag('gi-demo-muted', !on) }} /></span>
            <Segmented size="small" value={speed} options={[{ label: '1×', value: 1 }, { label: '1.5×', value: 1.5 }]}
              onChange={(v) => { setSpeed(Number(v)); voice.speed = Number(v) }} />
            {canReset && (
              <Popconfirm title="Remove every DEMO- entry and put the demo tank back?" onConfirm={resetData}>
                <Button size="small" loading={resetting} data-testid="demo-reset">Reset demo data</Button>
              </Popconfirm>
            )}
          </Space>
        </Space>
      </Modal>

      {live && rect && (
        <div className="gi-demo-spot" data-testid="demo-spotlight"
          style={{
            width: rect.width + pad * 2, height: rect.height + pad * 2,
            transform: `translate(${rect.left - pad}px, ${rect.top - pad}px)`,
          }} />
      )}
      {live && rect && (
        <div key={pulse} className={`gi-demo-ring${pulse ? ' gi-demo-ring--pulse' : ''}`}
          style={{ transform: `translate(${rect.left + Math.min(rect.width - 10, 24)}px, ${rect.top + rect.height / 2}px)` }} />
      )}
      {live && (
        <div ref={panel} data-testid="demo-overlay" role="status" aria-live="polite"
          className={`gi-demo-panel${rect && rect.bottom > window.innerHeight - 200 ? ' gi-demo-panel--top' : ''}`}>
          <div className="gi-demo-meta">
            ▶ {title} · step {step[0]} of {step[1]}{state === 'paused' ? ' · paused' : ''}
          </div>
          <div className="gi-demo-sub" data-testid="demo-subtitle">
            {state === 'done' ? 'Demo complete.'
              : state === 'error' ? `The demo stopped: ${detail ?? 'something on the page was not as expected'}`
                : state === 'stopped' ? 'Demo stopped.' : sentence}
          </div>
          <Space wrap size={8} style={{ marginTop: 8 }}>
            {state === 'running' && <Button size="small" data-testid="demo-pause" onClick={() => runner.pause()}>Pause</Button>}
            {state === 'paused' && (
              <Button size="small" type="primary" data-testid="demo-resume" onClick={() => runner.resume()}>
                {tookOver ? 'You took over — Resume' : 'Resume'}
              </Button>
            )}
            {(state === 'running' || state === 'paused') && (
              <>
                <Button size="small" data-testid="demo-skip" onClick={() => runner.skip()}>Skip</Button>
                <Button size="small" onClick={() => { const m = !muted; setMuted(m); voice.muted = m; writeFlag('gi-demo-muted', m) }}>
                  {muted ? 'Voice on' : 'Mute'}
                </Button>
                <Button size="small" danger data-testid="demo-stop" onClick={() => runner.stop()}>Stop (Esc)</Button>
              </>
            )}
            {(state === 'done' || state === 'stopped' || state === 'error') && (
              <>
                <Button size="small" type="primary" data-testid="demo-close" onClick={close}>Close</Button>
                <Button size="small" onClick={() => { setRunner(null); setState(null); setTarget(null); setChooser(true) }}>
                  Another demo
                </Button>
              </>
            )}
          </Space>
        </div>
      )}
    </>
  )
}
