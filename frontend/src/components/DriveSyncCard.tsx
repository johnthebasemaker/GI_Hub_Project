import { App, Button, Card, Popconfirm, Space, Tag, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

/**
 * Phase 21c — Google Drive → workbooks → Excel sync (rulings Q21-7..11).
 *
 * "Fetch from Drive now" downloads the newest copy of each workbook the sync
 * reads (the .xlsm is converted structurally, every value verified), commits
 * the SME files by itself, and DRY-RUNS the ERP ledger. "Commit ERP ledger"
 * is the operator's click (Q21-8). The same run happens daily at 07:30.
 * Live only (rule 17).
 */
interface SyncRun { rc: number; summary: string[] }
interface Changed { dest: string; source: string; converted: boolean; side: string; modifiedTime?: string }
interface Fetched {
  changed: Changed[]; unchanged: string[]; missing: string[]
  refused: { dest: string; source: string; reason: string }[]; notes: string[]
  folder: { unused: string[]; folders: string[]; lock_files: string[] }
}
interface Report {
  kind?: string; trigger?: string; by?: string; started?: string; finished?: string; ok?: boolean
  error?: string; fetch?: Fetched; runs?: Record<string, SyncRun>; erp_pending?: boolean
  erp_commit?: Report
}
interface Status {
  live_only: boolean
  configured?: { client: boolean; token: boolean }
  folder_id?: string; daily_at?: string
  last?: Report | null
  running?: { since: string; by: string; what: string } | null
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
        <Line label="Runs by itself">every day at {data.daily_at} (SME files commit; the ERP ledger waits for you)</Line>
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
                ...f.refused.map((r) => `❌ ${r.dest} ← ${r.source}: ${r.reason} — the previous file was kept`),
                ...f.missing.map((m) => `— ${m}: nothing in Drive matches`),
                ...(f.unchanged.length ? [`unchanged: ${f.unchanged.join(', ')}`] : []),
                ...f.notes,
                ...(f.folder?.unused?.length ? [`not used (later slice): ${f.folder.unused.length} file(s)`] : []),
              ].map((t) => <li key={t} style={{ padding: '1px 0' }}>{t}</li>)}</ul>
          )}
          <Runs runs={last.runs} />
          {last.erp_commit && <Runs runs={last.erp_commit.runs} />}
        </div>
      )}
    </Card>
  )
}
