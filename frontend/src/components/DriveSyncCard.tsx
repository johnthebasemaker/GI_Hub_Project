import { useState } from 'react'
import { App, Button, Card, Input, Popconfirm, Space, Switch, Tag, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

/**
 * Phase 21c — Google Drive → workbooks → Excel sync (rulings Q21-7..11).
 *
 * "Fetch from Drive now" downloads the newest copy of each workbook the sync
 * reads (the .xlsm is converted structurally, every value verified), commits
 * the SME files by itself, and DRY-RUNS the ERP ledger. "Commit ERP ledger"
 * is the operator's click (Q21-8) — since Phase 22a an ERP dry run that only
 * ADDS rows is committed by itself (Q22-1), at the pull times set here (Q22-2).
 * Live only (rule 17).
 */
interface SyncRun { rc: number; summary: string[] }
interface Changed {
  dest: string; source: string; converted: boolean; side: string; modifiedTime?: string
  rows?: Record<string, number>; prev_rows?: Record<string, number>; md5_checked?: boolean
}
interface Fetched {
  changed: Changed[]; unchanged: string[]; missing: string[]
  refused: { dest: string; source: string; reason: string; shrink?: boolean }[]; notes: string[]
  folder: { unused: string[]; folders: string[]; lock_files: string[] }
}
interface Report {
  kind?: string; trigger?: string; by?: string; started?: string; finished?: string; ok?: boolean
  error?: string; fetch?: Fetched; runs?: Record<string, SyncRun>; erp_pending?: boolean
  erp_commit?: Report; auto_commit?: { done: boolean; rows?: number; reasons?: string[] }
  folders?: { error?: string; removed?: number }
}
interface Schedule { enabled: boolean; times: string[]; auto_commit_additions: boolean }
interface HistoryRow {
  id: number; started_at: string; trigger: string; by_user: string | null; kind: string
  ok: boolean | null; files_changed: number; erp_auto_committed: boolean
}
interface Status {
  live_only: boolean
  configured?: { client: boolean; token: boolean }
  token?: { saved_at: string; testing_expiry: string } | null
  folder_id?: string; daily_at?: string
  schedule?: Schedule; next_at?: string | null
  last?: Report | null
  running?: { since: string; by: string; what: string } | null
  history?: HistoryRow[]
  files?: { kind: string; link: string; n: number }[]
}
const KIND_LABEL: Record<string, string> = { dn: 'DN for CNCEC', mtc: 'MTC', pending: 'Pending Material Follow-up' }

/** Phase 22a — the pull times (Q22-2), on/off, and auto-commit of additions (Q22-1). */
function ScheduleEditor({ sch }: { sch: Schedule }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [times, setTimes] = useState(sch.times.join(', '))
  const [enabled, setEnabled] = useState(sch.enabled)
  const [auto, setAuto] = useState(sch.auto_commit_additions)
  const save = useMutation({
    mutationFn: async () => (await api.put('/admin/drive-sync/schedule', {
      times: times.split(/[,\s]+/).filter(Boolean), enabled, auto_commit_additions: auto })).data,
    onSuccess: (v: Schedule) => {
      setTimes(v.times.join(', '))
      message.success('Pull times saved')
      void qc.invalidateQueries({ queryKey: ['/admin/drive-sync'] })
      void qc.invalidateQueries({ queryKey: ['/drive/freshness'] })
    },
    onError: (e) => message.error(errMsg(e)),
  })
  return (
    <div data-testid="drive-schedule" style={{ marginTop: 6 }}>
      <Space wrap>
        <Switch checked={enabled} onChange={setEnabled} data-testid="drive-schedule-on"
          checkedChildren="on" unCheckedChildren="off" />
        <Input style={{ width: 220 }} value={times} onChange={(e) => setTimes(e.target.value)}
          data-testid="drive-schedule-times" placeholder="07:30, 19:30" aria-label="Pull times" />
        <Switch checked={auto} onChange={setAuto} data-testid="drive-schedule-auto" />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          commit the ERP side by itself when it only adds rows</Typography.Text>
        {/* `disabled`, not `loading`: an antd v6 Button whose `loading` flips back
            within the same frame (a fast 422) keeps ignoring clicks — measured in
            the 22a E2E, where the second Save never reached the handler */}
        <Button size="small" disabled={save.isPending} data-testid="drive-schedule-save"
          onClick={() => save.mutate()}>Save</Button>
      </Space>
    </div>
  )
}

const RUN_LABEL: Record<string, string> = {
  sme_commit: 'SME files — committed',
  erp_dry_run: 'ERP ledger — dry run (nothing written yet)',
  erp_commit: 'ERP ledger — committed',
}

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

function Runs({ runs }: { runs?: Record<string, SyncRun> }) {
  if (!runs || !Object.keys(runs).length) return null
  return (
    <>
      {Object.entries(runs).map(([k, r]) => (
        <div key={k} style={{ marginTop: 8 }} data-testid={`drive-run-${k}`}>
          <Space><Typography.Text strong>{RUN_LABEL[k] ?? k}</Typography.Text>
            <Tag color={r.rc === 0 ? 'green' : 'red'}>{r.rc === 0 ? 'ok' : `failed (exit ${r.rc})`}</Tag></Space>
          <pre style={{ fontSize: 11, whiteSpace: 'pre-wrap', margin: '4px 0 0', maxHeight: 220, overflow: 'auto' }}>
            {r.summary.join('\n') || '—'}</pre>
        </div>
      ))}
    </>
  )
}

// Plain rows rather than antd's Descriptions / List / Empty: each one this
// lazy page pulls in for the first time adds an entry to the sign-in page's
// preload map (+3 B on the critical path, which allows 0 — Phase 21c).
function Line({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: 8, padding: '2px 0' }}>
      <Typography.Text type="secondary" style={{ minWidth: 130 }}>{label}</Typography.Text>
      <div>{children}</div>
    </div>
  )
}

export default function DriveSyncCard() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const { data } = useQuery<Status>({
    queryKey: ['/admin/drive-sync'],
    queryFn: async () => (await api.get('/admin/drive-sync')).data,
    refetchInterval: (q) => (q.state.data?.running ? 4000 : false),
  })
  const run = useMutation({
    mutationFn: async (path: string) => (await api.post(path)).data,
    onSuccess: () => { message.success('Started — this takes a minute or two'); qc.invalidateQueries({ queryKey: ['/admin/drive-sync'] }) },
    onError: (e) => message.error(errMsg(e)),
  })

  if (!data) return <Card size="small" loading data-testid="drive-sync-loading" />
  if (data.live_only) {
    return <Card size="small" title="Google Drive sync"><Typography.Text type="secondary">
      Live only — Practice has no Drive and no workbooks (rule 17).</Typography.Text></Card>
  }
  const ready = !!(data.configured?.client && data.configured?.token)
  const last = data.last
  const f = last?.fetch
  return (
    <Card size="small" title="Google Drive sync" data-testid="drive-sync"
      extra={<Space>
        <Button type="primary" disabled={!ready || !!data.running} loading={!!data.running && data.running.what === 'fetch'}
          data-testid="drive-fetch" onClick={() => run.mutate('/admin/drive-sync/run')}>Fetch from Drive now</Button>
        <Popconfirm title="Commit the ERP ledger?"
          description="This writes the Live warehouse ledger (receipts, consumption, returns, lots) from the fetched workbook — exactly what the dry run below showed."
          okText="Commit" onConfirm={() => run.mutate('/admin/drive-sync/commit-erp')}>
          <Button danger disabled={!ready || !!data.running || !last?.erp_pending}
            loading={!!data.running && data.running.what === 'commit_erp'} data-testid="drive-commit-erp">
            Commit ERP ledger</Button>
        </Popconfirm>
      </Space>}>
      <div>
        <Line label="Connection">
          {ready ? <Tag color="green">connected (read-only)</Tag>
            : <><Tag color="orange">not connected</Tag> follow <code>docs/GDRIVE_SETUP.md</code> once
              ({data.configured?.client ? 'client file ✓' : 'client file missing'}, {data.configured?.token ? 'token ✓' : 'no token yet'})</>}
        </Line>
        {data.token && (
          <Line label="Google sign-in">
            saved {data.token.saved_at.replace('T', ' ')} — if the Google app is still in
            <b> Testing</b>, Google ends it on {data.token.testing_expiry.replace('T', ' ')}.
            Publish it once (docs/GDRIVE_SETUP.md, step 6) and it stays.
          </Line>
        )}
        <Line label="Pulls by itself">
          {data.schedule?.enabled
            ? <>at {data.schedule.times.join(' and ')}{data.next_at && <> · next {data.next_at.replace('T', ' ')}</>}.
              SME files commit; the ERP ledger {data.schedule.auto_commit_additions
                ? 'commits by itself when it only ADDS rows — edits and removals wait for you'
                : 'always waits for you'}.</>
            : 'switched off — only the buttons pull'}
          {data.schedule && <ScheduleEditor key={data.schedule.times.join()} sch={data.schedule} />}
        </Line>
        {!!data.files?.length && (
          <Line label="Folders">
            <span data-testid="drive-folders">{Object.entries(data.files.reduce<Record<string, string[]>>((acc, f) => {
              (acc[f.kind] ??= []).push(`${f.n} ${f.link}`)
              return acc
            }, {})).map(([k, v]) => `${KIND_LABEL[k] ?? k}: ${v.join(', ')}`).join(' · ')}</span>
          </Line>
        )}
        {data.running && <Line label="Running">since {data.running.since.slice(11, 16)} ({data.running.by})</Line>}
      </div>
      {!last ? <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>No run yet.</Typography.Paragraph> : (
        <div style={{ marginTop: 8 }}>
          <Typography.Text type="secondary">
            Last run {last.started?.replace('T', ' ').slice(0, 16)} ({last.trigger}, {last.by}){' '}
            <Tag color={last.ok ? 'green' : 'red'}>{last.ok ? 'ok' : 'needs a look'}</Tag>
            {last.erp_pending && <Tag color="gold" data-testid="drive-erp-pending">ERP dry run waiting for Commit</Tag>}
          </Typography.Text>
          {last.error && <Typography.Paragraph type="danger">{last.error}</Typography.Paragraph>}
          {f && (
            <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>{[
                ...f.changed.map((c) => `✅ ${c.dest} ← ${c.source}${c.converted ? ' (converted from .xlsm, every value checked)' : ''}`),
                ...f.changed.filter((c) => c.rows && Object.keys(c.rows).length).map((c) =>
                  `   ${c.dest}: ${Object.entries(c.rows ?? {}).map(([k, v]) => {
                    const was = c.prev_rows?.[k]
                    return `${k} ${was != null && was !== v ? `${was.toLocaleString()} → ` : ''}${v.toLocaleString()}`
                  }).join(', ')}${c.md5_checked ? ' · checksum ✓' : ''}`),
                ...f.refused.map((r) => `❌ ${r.dest} ← ${r.source}: ${r.reason}`),
                ...f.missing.map((m) => `— ${m}: nothing in Drive matches`),
                ...(f.unchanged.length ? [`unchanged: ${f.unchanged.join(', ')}`] : []),
                ...f.notes,
                ...(f.folder?.unused?.length ? [`not used: ${f.folder.unused.length} file(s)`] : []),
              ].map((t) => <li key={t} style={{ padding: '1px 0' }}>{t}</li>)}</ul>
          )}
          {f?.refused?.some((r) => r.shrink) && (
            <Popconfirm title="Take the smaller workbook?"
              description="A sheet lost more than 2 % of its rows since the last copy. Only accept it if rows were deleted on purpose."
              okText="Accept" onConfirm={() => run.mutate('/admin/drive-sync/run?accept_shrink=true')}>
              <Button size="small" danger style={{ marginTop: 6 }} data-testid="drive-accept-shrink">
                Accept the smaller file</Button>
            </Popconfirm>
          )}
          {last.auto_commit && (
            <Typography.Paragraph style={{ margin: '6px 0 0' }} data-testid="drive-auto-commit">
              {last.auto_commit.done
                ? <Tag color="green">ERP committed by itself — {last.auto_commit.rows ?? 0} new row(s), nothing existing changed</Tag>
                : <Typography.Text type="secondary">Not committed by itself: {(last.auto_commit.reasons ?? []).join('; ')}</Typography.Text>}
            </Typography.Paragraph>
          )}
          {last.folders?.error && <Typography.Paragraph type="danger">Folders: {last.folders.error}</Typography.Paragraph>}
          <Runs runs={last.runs} />
          {last.erp_commit && <Runs runs={last.erp_commit.runs} />}
        </div>
      )}
      {!!data.history?.length && (
        <div style={{ marginTop: 10 }} data-testid="drive-history">
          <Typography.Text strong>Recent pulls</Typography.Text>
          <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 12 }}>
            {data.history.map((h) => (
              <li key={h.id}>
                {h.started_at.replace('T', ' ').slice(0, 16)} · {h.trigger}{h.by_user ? ` (${h.by_user})` : ''} ·{' '}
                {h.kind === 'commit_erp' ? 'ERP commit' : `${h.files_changed} workbook(s) changed`}
                {h.erp_auto_committed ? ' · ERP committed by itself' : ''}{' '}
                <Tag color={h.ok ? 'green' : 'red'} style={{ fontSize: 11 }}>{h.ok ? 'ok' : 'failed'}</Tag>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  )
}
