import { useEffect, useRef, useState } from 'react'
import {
  Alert, App, Button, Card, Empty, Progress, Segmented, Space, Table, Tabs, Tag, Typography,
} from 'antd'
import { CheckCircleTwoTone, ClockCircleOutlined } from '@ant-design/icons'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'

/**
 * Training & Onboarding (Phase 10 Track 5).
 *
 * Two views in one page: my own modules, and — for an HOD or admin — who else
 * has watched them. The second is the whole point of the SOFT gate: nothing is
 * refused, so the control has to be visibility.
 */

interface Asset { language: string; storage_uri: string; captions_uri?: string; duration_s?: number }
interface Module {
  module_key: string; title: string; description?: string; version: number
  acknowledged: boolean; completed: boolean; watched_seconds: number
  deferrals: number; required_roles: string[]; mandatory: boolean
  assets: Asset[]; published: boolean
}

const LANG_LABEL: Record<string, string> = {
  en: 'English', ta: 'தமிழ் (Tamil)', 'ta-Latn': 'Tanglish', ar: 'العربية (Arabic)',
}

function ModuleCard({ m, onChanged, seekTo, wantLang }:
  { m: Module; onChanged: () => void; seekTo?: number; wantLang?: string }) {
  const { message } = App.useApp()
  const [lang, setLang] = useState(
    m.assets.find((a) => a.language === wantLang)?.language
    ?? m.assets[0]?.language ?? 'en')
  const [busy, setBusy] = useState(false)
  const videoRef = useRef<HTMLVideoElement>(null)
  const seeked = useRef(false)
  const asset = m.assets.find((a) => a.language === lang)
  const dur = asset?.duration_s ?? 0
  const pct = dur ? Math.min(100, Math.round((m.watched_seconds / dur) * 100)) : 0

  const ack = async () => {
    setBusy(true)
    try {
      await api.post('/training/acknowledge', { module_key: m.module_key, language: lang })
      message.success('Recorded. Thank you.')
      onChanged()
    } catch (e) {
      const x = e as { response?: { data?: { detail?: string } } }
      message.error(x?.response?.data?.detail ?? 'Could not record that')
    } finally { setBusy(false) }
  }

  // ⚠️ PHASE 12f — SEEK ONCE, AND ONLY TOWARDS A FRAME THAT EXISTS. The Hub
  // Assistant links here with `?t=` when its answer matches a recorded step, so
  // the viewer lands on the second where that step is on screen rather than at
  // the start of a ninety-second video.
  //
  // `seeked` latches: without it every re-render (and there is one per progress
  // beacon) would drag the playhead back to the deep-link position while the
  // person is trying to watch. Clamped 1s inside the duration because seeking
  // to or past the end fires `ended` on some browsers, which would beacon a
  // completion the viewer never earned — and this module's whole purpose is a
  // compliance record somebody might produce as evidence.
  useEffect(() => {
    const el = videoRef.current
    if (!el || seeked.current || seekTo == null || !Number.isFinite(seekTo)) return
    const apply = () => {
      if (seeked.current) return
      const cap = el.duration && Number.isFinite(el.duration) ? el.duration - 1 : seekTo
      el.currentTime = Math.max(0, Math.min(seekTo, cap))
      seeked.current = true
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
    if (el.readyState >= 1) apply()
    else el.addEventListener('loadedmetadata', apply, { once: true })
  }, [seekTo, asset?.storage_uri])

  // Progress is reported by the player as it plays. Sent on pause/ended rather
  // than on a timer: a beacon every second is a write per second per viewer,
  // and the server takes the MAX so an out-of-order one cannot lose ground.
  const beacon = async (seconds: number) => {
    try {
      await api.post('/training/progress', {
        module_key: m.module_key, watched_seconds: Math.floor(seconds), language: lang,
      })
      onChanged()
    } catch { /* progress is best-effort; never interrupt playback */ }
  }

  return (
    <Card
      title={<Space>{m.title}<Tag>v{m.version}</Tag>
        {m.mandatory && <Tag color="red">Required for your role</Tag>}
        {m.acknowledged && <Tag icon={<CheckCircleTwoTone twoToneColor="#52c41a" />}>Completed</Tag>}</Space>}
      style={{ marginBottom: 16 }}
    >
      {m.description && <Typography.Paragraph type="secondary">{m.description}</Typography.Paragraph>}

      {!m.published ? (
        <Alert
          type="info" showIcon
          message="Not published yet"
          description="The videos for this module have not been uploaded. There is nothing to watch and nothing to acknowledge — your administrator will publish them."
        />
      ) : (
        <>
          {m.assets.length > 1 && (
            <Segmented
              style={{ marginBottom: 12 }}
              value={lang}
              onChange={(v) => setLang(String(v))}
              options={m.assets.map((a) => ({ label: LANG_LABEL[a.language] ?? a.language, value: a.language }))}
            />
          )}
          {asset && (
            <video
              ref={videoRef}
              key={asset.storage_uri}
              src={asset.storage_uri}
              controls
              width="100%"
              style={{ maxHeight: 420, background: '#000', borderRadius: 6 }}
              onPause={(e) => beacon((e.target as HTMLVideoElement).currentTime)}
              onEnded={(e) => beacon((e.target as HTMLVideoElement).currentTime)}
            >
              {asset.captions_uri && (
                <track kind="captions" src={asset.captions_uri} srcLang={asset.language} default />
              )}
            </video>
          )}
          <Space style={{ marginTop: 12, width: '100%', justifyContent: 'space-between' }}>
            <Space direction="vertical" size={0}>
              <Progress percent={pct} size="small" style={{ width: 220 }} />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {m.watched_seconds}s of {dur || '?'}s watched
                {m.deferrals > 0 && ` · deferred ${m.deferrals}×`}
              </Typography.Text>
            </Space>
            <Button type="primary" disabled={m.acknowledged || pct < 90} loading={busy} onClick={ack}>
              {m.acknowledged ? 'Acknowledged' : 'I have watched and understood this'}
            </Button>
          </Space>
          {!m.acknowledged && pct < 90 && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Watch at least 90% before acknowledging.
            </Typography.Text>
          )}
        </>
      )}
    </Card>
  )
}

function ComplianceTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['/training/compliance'],
    queryFn: async () => (await api.get('/training/compliance')).data,
  })
  const modules = data?.modules ?? []
  if (!isLoading && modules.length === 0) return <Empty description="No active training modules" />
  return (
    <>
      {modules.map((m: {
        module_key: string; title: string; version: number
        acknowledged: number; outstanding: number; deferrals: number
        people: { username: string; role: string; site_id?: string; acknowledged: boolean; deferrals: number; watched_seconds: number }[]
      }) => (
        <Card
          key={m.module_key}
          title={<Space>{m.title}<Tag>v{m.version}</Tag></Space>}
          extra={
            <Space>
              <Tag color="green">{m.acknowledged} done</Tag>
              <Tag color={m.outstanding ? 'red' : 'default'}>{m.outstanding} outstanding</Tag>
              {m.deferrals > 0 && <Tag icon={<ClockCircleOutlined />} color="orange">{m.deferrals} deferrals</Tag>}
            </Space>
          }
          style={{ marginBottom: 16 }}
        >
          {/* Driven from `users`, not from the compliance table — somebody who
              has never opened the module is the person most worth seeing, and
              listing only existing rows would hide exactly them. */}
          <Table
            size="small"
            rowKey="username"
            pagination={false}
            dataSource={m.people}
            columns={[
              { title: 'User', dataIndex: 'username' },
              { title: 'Role', dataIndex: 'role' },
              { title: 'Site', dataIndex: 'site_id', render: (v) => v || '—' },
              {
                title: 'Status', key: 's',
                render: (_: unknown, r) => (r.acknowledged
                  ? <Tag color="green">Acknowledged</Tag>
                  : r.watched_seconds > 0
                    ? <Tag color="blue">Started</Tag>
                    : <Tag>Not started</Tag>),
              },
              {
                title: 'Deferred', dataIndex: 'deferrals',
                render: (v: number) => (v > 0 ? <Tag color="orange">{v}×</Tag> : '—'),
              },
            ]}
          />
        </Card>
      ))}
    </>
  )
}

export default function TrainingPage() {
  const { user } = useAuth()
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['/training/modules'],
    queryFn: async () => (await api.get('/training/modules')).data,
  })
  const modules: Module[] = data?.modules ?? []
  const isHod = (user?.level ?? 0) >= 2
  // Deep link from the Hub Assistant: /training?module=…&lang=…&t=…
  // ⚠️ READ, NEVER TRUSTED. The URL selects which card scrolls into view and
  // where its playhead starts; it grants nothing. The module list is still the
  // role-filtered one the server returned, so a hand-edited `module=` for a
  // module this role has no business seeing simply matches no card.
  const [sp] = useSearchParams()
  const wantModule = sp.get('module') ?? undefined
  const wantLang = sp.get('lang') ?? undefined
  const tRaw = Number(sp.get('t'))
  const wantT = Number.isFinite(tRaw) && tRaw >= 0 ? tRaw : undefined
  const refresh = () => { void qc.invalidateQueries({ queryKey: ['/training/modules'] }) }

  const mine = (
    <>
      {!isLoading && modules.length === 0 && (
        <Empty description="No training modules apply to your role yet" />
      )}
      {modules.map((m) => (
        <ModuleCard key={m.module_key} m={m} onChanged={refresh}
          seekTo={m.module_key === wantModule ? wantT : undefined}
          wantLang={m.module_key === wantModule ? wantLang : undefined} />
      ))}
    </>
  )

  return (
    <div style={{ padding: 16, maxWidth: 900 }}>
      <Typography.Title level={4} style={{ marginTop: 0 }}>Training &amp; Onboarding</Typography.Title>
      {isHod
        ? (
          <Tabs
            items={[
              { key: 'mine', label: 'My training', children: mine },
              { key: 'team', label: 'Team compliance', children: <ComplianceTab /> },
            ]}
          />
        )
        : mine}
    </div>
  )
}
