import { useState } from 'react'
import {
  Alert, App, Button, Card, DatePicker, Empty, Modal, Popconfirm, Space, Table, Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { Dayjs } from 'dayjs'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

/**
 * Phase 14d — the admin's side of "What's new", and the tutorial freshness
 * report (admins only — ruling Q14-14).
 *
 * ⚠️ THE TEXT IS NOT EDITABLE HERE. Announcements are authored as YAML in
 * `docs/announcements/` and reviewed in the PR that ships the feature (Q14-13);
 * this screen loads them, previews them and decides WHEN. Changing a sentence
 * is a pull request, which is the point.
 */
type Row = Record<string, unknown>
const s = (v: unknown) => (v == null ? '' : String(v))

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } } }
  return x?.response?.data?.detail ?? 'Something went wrong'
}

const STATUS: Record<string, string> = {
  draft: 'default', scheduled: 'blue', published: 'green', retracted: 'red',
}

export function AnnouncementsTab() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [preview, setPreview] = useState<Row | null>(null)
  const [schedule, setSchedule] = useState<{ key: string; at: Dayjs | null } | null>(null)
  const list = useQuery({
    queryKey: ['/announcements/admin'],
    queryFn: async () => (await api.get<{ items: Row[] }>('/announcements/admin')).data.items,
  })
  const refresh = () => { void qc.invalidateQueries({ queryKey: ['/announcements/admin'] }) }
  const sync = useMutation({
    mutationFn: async () => (await api.post('/announcements/admin/sync')).data as {
      added: number; updated: number; files: number; problems: { file: string; problem: string }[] },
    onSuccess: (d) => {
      if (d.problems.length) {
        Modal.warning({
          title: `${d.problems.length} file(s) refused`,
          content: <ul>{d.problems.map((p) => <li key={p.file}><b>{p.file}</b>: {p.problem}</li>)}</ul>,
        })
      }
      message.success(`Loaded ${d.files} file(s): ${d.added} new, ${d.updated} updated`)
      refresh()
    },
    onError: (e) => message.error(errMsg(e)),
  })
  const act = useMutation({
    mutationFn: async (v: { key: string; op: 'publish' | 'retract'; at?: string }) =>
      (await api.post(`/announcements/admin/${encodeURIComponent(v.key)}/${v.op}`,
        v.op === 'publish' ? { at: v.at ?? null } : undefined)).data,
    onSuccess: (d: Row) => {
      message.success(d.status === 'scheduled' ? `Scheduled for ${s(d.publish_at).slice(0, 16).replace('T', ' ')}`
        : d.status === 'published' ? 'Published — the bell has rung for its audience'
          : 'Retracted')
      setSchedule(null)
      refresh()
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const cols: ColumnsType<Row> = [
    { title: 'Announcement', key: 't',
      render: (_: unknown, r: Row) => (
        <Space direction="vertical" size={0}>
          <strong>{s(r.title)}</strong>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            {s(r.key)} · {s(r.source_path)}
          </Typography.Text>
        </Space>) },
    { title: 'Status', dataIndex: 'status', width: 120,
      render: (v: string, r: Row) => (
        <Space direction="vertical" size={0}>
          <Tag color={STATUS[v] ?? 'default'}>{v}</Tag>
          {v === 'scheduled' && <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            {s(r.publish_at).slice(0, 16).replace('T', ' ')}</Typography.Text>}
        </Space>) },
    { title: 'Audience (from the nav matrix)', key: 'aud',
      render: (_: unknown, r: Row) => (
        <Space size={4} wrap>
          {(r.audience_roles as string[]).map((x) => <Tag key={x}>{x}</Tag>)}
          {Array.isArray(r.sites) && (r.sites as string[]).map((x) => <Tag key={x} color="purple">{x}</Tag>)}
        </Space>) },
    { title: 'Seen by', dataIndex: 'reads', width: 80, align: 'right' },
    { title: '', key: 'a', width: 300,
      render: (_: unknown, r: Row) => (
        <Space wrap>
          <Button size="small" onClick={() => setPreview(r)}>Preview</Button>
          {r.status !== 'published' && (
            <Popconfirm title="Publish now?" description="The bell rings for everyone in the audience."
              onConfirm={() => act.mutate({ key: s(r.key), op: 'publish' })}>
              <Button size="small" type="primary">Publish</Button>
            </Popconfirm>
          )}
          {r.status !== 'published' && (
            <Button size="small" onClick={() => setSchedule({ key: s(r.key), at: null })}>Schedule</Button>
          )}
          {r.status !== 'retracted' && r.status !== 'draft' && (
            <Popconfirm title="Retract it?" description="It leaves every What's-new panel, and unread bell rows are removed."
              onConfirm={() => act.mutate({ key: s(r.key), op: 'retract' })}>
              <Button size="small" danger>Retract</Button>
            </Popconfirm>
          )}
        </Space>) },
  ]

  return (
    <>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        title="Announcements are written in the pull request, not here"
        description={<>Each one is a file in <code>docs/announcements/</code>. <b>Load from files</b> brings
          new and changed ones in as drafts; you decide when each is published. Who sees it is worked out
          from the navigation matrix: every role that can open the page it announces. Delivery is the bell
          and the What&apos;s-new panel only.</>} />
      <Space style={{ marginBottom: 12 }}>
        <Button type="primary" loading={sync.isPending} onClick={() => sync.mutate()}>Load from files</Button>
      </Space>
      <Table size="small" rowKey={(r) => s(r.key)} loading={list.isFetching} columns={cols}
        dataSource={list.data ?? []} pagination={false} scroll={{ x: 'max-content' }}
        locale={{ emptyText: <Empty description="Nothing loaded yet — press Load from files" /> }} />
      <Modal open={!!preview} onCancel={() => setPreview(null)} footer={null} width={620}
        className="gi-whats-new" closable={false} destroyOnHidden>
        {preview && (
          <div className="gi-glass" style={{ padding: 20 }}>
            <Typography.Text style={{ color: 'var(--gi-glass-text)', fontSize: 12 }}>
              Preview — as {(preview.audience_roles as string[]).join(', ')} will see it
            </Typography.Text>
            <div className="gi-glass-stage" style={{ marginTop: 10 }}>
              <div className="gi-glass gi-glass-tilt gi-whats-new-card">
                <h4>{s(preview.title)}</h4>
                <Typography.Paragraph style={{ color: 'var(--gi-glass-text)', whiteSpace: 'pre-line' }}>
                  {s(preview.body)}
                </Typography.Paragraph>
                <Space wrap size={6}>
                  <Tag color="gold">Open it → {(preview.routes as string[])[0]}</Tag>
                  {preview.tutorial_module ? <Tag color="blue">Watch the tutorial</Tag> : null}
                  {preview.manual_section ? <Tag>User Manual §{s(preview.manual_section)}</Tag> : null}
                </Space>
              </div>
            </div>
            <Button style={{ marginTop: 6 }} onClick={() => setPreview(null)}>Close</Button>
          </div>
        )}
      </Modal>
      <Modal open={!!schedule} title="Schedule publication" okText="Schedule"
        okButtonProps={{ disabled: !schedule?.at }} confirmLoading={act.isPending}
        onCancel={() => setSchedule(null)}
        onOk={() => schedule?.at && act.mutate({ key: schedule.key, op: 'publish', at: schedule.at.toISOString() })}>
        <DatePicker showTime style={{ width: '100%' }} value={schedule?.at ?? null}
          onChange={(v) => setSchedule((x) => (x ? { ...x, at: v } : x))} />
        <Typography.Paragraph type="secondary" style={{ marginTop: 8, fontSize: 12 }}>
          It is released by the first request after that time — nothing to start or keep running.
        </Typography.Paragraph>
      </Modal>
    </>
  )
}

interface StaleItem {
  tutorial: string; title?: string; status: 'current' | 'possibly_stale' | 'unknown'
  reason: string; changed?: string[]; rerecord?: string; recorded_at?: string
}

/** Admins only (Q14-14). Never a gate — a warning with a named reason. */
export function TutorialFreshness() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const q = useQuery({
    queryKey: ['/announcements/admin/tutorials'],
    queryFn: async () => (await api.get<{ result: { items: StaleItem[]; checked_at?: string; head?: string } | null
      git_here: boolean }>('/announcements/admin/tutorials')).data,
  })
  const run = useMutation({
    mutationFn: async () => (await api.post('/announcements/admin/tutorials/check')).data,
    onSuccess: (d: Row) => {
      if (!d.ran) message.warning(s(d.reason))
      else message.success(d.bell ? 'Checked — the admin bell has the summary' : 'Checked')
      void qc.invalidateQueries({ queryKey: ['/announcements/admin/tutorials'] })
    },
    onError: (e) => message.error(errMsg(e)),
  })
  const r = q.data?.result
  const color = { current: 'green', possibly_stale: 'orange', unknown: 'default' } as const
  return (
    <Card size="small" title="Tutorial freshness" data-testid="tutorial-freshness"
      extra={<Button size="small" loading={run.isPending} onClick={() => run.mutate()}>Check now</Button>}>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        A tutorial is <b>possibly stale</b> when a page it shows has changed since it was recorded.
        This never blocks anything; re-record with the command shown.
        {r?.checked_at ? ` Last checked ${r.checked_at.slice(0, 16).replace('T', ' ')} UTC.` : ' Not checked yet.'}
        {!q.data?.git_here && ' This server has no git, so the check runs on the deploy host (tools/tutorial_staleness.py --notify).'}
      </Typography.Paragraph>
      <Table size="small" rowKey="tutorial" pagination={false} dataSource={r?.items ?? []}
        columns={[
          { title: 'Tutorial', key: 't', render: (_: unknown, i: StaleItem) => i.title ?? i.tutorial },
          { title: 'Status', key: 's', width: 140,
            render: (_: unknown, i: StaleItem) => <Tag color={color[i.status]}>{i.status.replace('_', ' ')}</Tag> },
          { title: 'Why', key: 'w',
            render: (_: unknown, i: StaleItem) => (
              <Space direction="vertical" size={0}>
                <span>{i.reason}</span>
                {(i.changed ?? []).slice(0, 4).map((f) => (
                  <Typography.Text key={f} type="secondary" style={{ fontSize: 11 }}><code>{f}</code></Typography.Text>))}
                {i.status === 'possibly_stale' && i.rerecord && (
                  <Typography.Text copyable style={{ fontSize: 11 }}><code>{i.rerecord}</code></Typography.Text>)}
              </Space>) },
        ]} />
    </Card>
  )
}
