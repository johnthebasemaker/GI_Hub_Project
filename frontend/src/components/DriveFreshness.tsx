import { App, Badge, Button, Tag, Tooltip } from 'antd'
import { CloudSyncOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { brand, status as tone } from '../theme/tokens'

/**
 * Phase 22a — "Last updated from Drive", on every page (rulings Q22-3/4).
 *
 * The chip says how fresh the workbook data is; its tooltip says, per
 * workbook, which Drive copy GI Hub holds and when the ERP side was last
 * committed. Admin and HOD also get the Pull button (the same run as the
 * schedule: an ERP dry run that only ADDS rows is committed by itself, anything
 * else waits for the Admin's Commit — ruling Q22-1).
 *
 * The chip is short — a cloud and the time ("07:30 today") — with a red count
 * badge for workbook rows naming a bad lot; the tooltip says it in words.
 *
 *   navy   ok              last pull succeeded within 26 hours
 *   amber  stale / waiting older than 26 h, or ERP changes wait for Commit
 *   red    failed / token  the last run failed, or Google's sign-in ended
 *   grey   practice / not connected / never
 */
interface Freshness {
  status: string
  last_ok_at?: string | null; erp_commit_at?: string | null; sme_commit_at?: string | null
  files?: Record<string, { source: string; modifiedTime?: string; fetched_at?: string }>
  last_run_at?: string | null; erp_pending?: boolean; pending_reasons?: string[]
  error?: string | null; running?: boolean; next_at?: string | null
  lot_problems?: number; can_pull?: boolean
}

function when(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso.length === 16 ? `${iso}:00` : iso)
  if (Number.isNaN(d.getTime())) return iso
  const today = new Date()
  const y = new Date(today)
  y.setDate(today.getDate() - 1)
  const hm = d.toTimeString().slice(0, 5)
  if (d.toDateString() === today.toDateString()) return `${hm} today`
  if (d.toDateString() === y.toDateString()) return `${hm} yesterday`
  return `${d.toLocaleDateString(undefined, { day: '2-digit', month: 'short' })} ${hm}`
}

// Short on purpose: the top bar also carries the role's name, the PRACTICE tag
// and the demo launcher, and a long chip pushed the HOD's header past 1280 px
// (22a E2E). The tooltip carries the sentence.
const LOOK: Record<string, { color: string; label: (f: Freshness) => string }> = {
  ok: { color: brand.navy, label: (f) => when(f.last_ok_at) },
  pending_commit: { color: tone.low, label: (f) => `${when(f.last_ok_at)} · Commit` },
  stale: { color: tone.low, label: (f) => `${when(f.last_ok_at)} (old)` },
  running: { color: tone.info, label: () => 'pulling…' },
  failed: { color: tone.critical, label: () => 'pull failed' },
  token: { color: tone.critical, label: () => 'sign-in ended' },
  never: { color: tone.low, label: () => 'not pulled' },
  not_connected: { color: tone.low, label: () => 'not connected' },
}
const HEAD: Record<string, string> = {
  ok: 'Last updated from Drive', pending_commit: 'Updated from Drive — ERP changes wait for Commit',
  stale: 'Last updated from Drive — over 26 hours ago', running: 'Pulling from Drive now',
  failed: 'The last pull from Drive failed', token: "Google's sign-in ended — run --auth again",
  never: 'Not pulled from Drive yet', not_connected: 'Drive is not connected (docs/GDRIVE_SETUP.md)',
}

export default function DriveFreshness() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { data } = useQuery<Freshness>({
    queryKey: ['/drive/freshness'],
    queryFn: async () => (await api.get('/drive/freshness')).data,
    refetchInterval: (q) => (q.state.data?.running ? 5000 : 5 * 60_000),
    staleTime: 30_000,
  })
  const pull = useMutation({
    mutationFn: async () => (await api.post('/drive/pull')).data,
    onSuccess: () => {
      message.success('Pulling from Drive — this takes a minute or two')
      void qc.invalidateQueries({ queryKey: ['/drive/freshness'] })
    },
    onError: (e: unknown) => {
      const x = e as { response?: { data?: { detail?: string } } }
      message.error(x?.response?.data?.detail ?? 'Pull failed')
    },
  })
  if (!data) return null
  if (data.status === 'practice') {
    return (
      <Tooltip title="Practice data — Practice never pulls from Google Drive (rule 17).">
        <Tag data-testid="drive-freshness" data-status="practice" aria-label="Practice data"
          icon={<CloudSyncOutlined />} style={{ marginInlineEnd: 0 }} />
      </Tooltip>
    )
  }
  const look = LOOK[data.status] ?? LOOK.never
  const files = Object.entries(data.files ?? {})
  const tip = (
    <div style={{ fontSize: 12, lineHeight: 1.6 }} data-testid="drive-freshness-tip">
      <div><b>{HEAD[data.status] ?? HEAD.never}</b></div>
      <div>Last successful pull: {when(data.last_ok_at)}</div>
      <div>ERP ledger committed: {when(data.erp_commit_at)}</div>
      {files.map(([dest, f]) => (
        <div key={dest}>· {dest}: Drive copy of {when(f.modifiedTime)}</div>
      ))}
      {data.erp_pending && (
        <div>⚠ ERP changes wait for the Admin's Commit
          {data.pending_reasons?.length ? ` (${data.pending_reasons.slice(0, 2).join('; ')})` : ''}</div>
      )}
      {data.error && <div>⚠ {data.error}</div>}
      {data.next_at && <div>Next pull: {when(data.next_at)}</div>}
      {!!data.lot_problems && (
        <div>⚠ {data.lot_problems} workbook row(s) name a bad lot — click to see them</div>
      )}
    </div>
  )
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <Tooltip title={tip}>
        <Badge count={data.lot_problems ?? 0} size="small" overflowCount={999}
          title={`${data.lot_problems ?? 0} workbook rows to fix`}>
          <Tag data-testid="drive-freshness" data-status={data.status}
            aria-label={`${HEAD[data.status] ?? HEAD.never}: ${look.label(data)}`}
            icon={<CloudSyncOutlined spin={data.status === 'running'} />}
            color={look.color} style={{ marginInlineEnd: 0, cursor: data.lot_problems ? 'pointer' : 'default' }}
            onClick={() => { if (data.lot_problems) navigate('/lots#lot-problems') }}>
            {look.label(data)}
          </Tag>
        </Badge>
      </Tooltip>
      {data.can_pull && (
        <Tooltip title="Pull the newest workbooks and DN / MTC / request files from Google Drive now">
          <Button size="small" data-testid="drive-pull" disabled={pull.isPending || !!data.running}
            onClick={() => pull.mutate()}>{data.running ? 'Pulling…' : 'Pull'}</Button>
        </Tooltip>
      )}
    </span>
  )
}
