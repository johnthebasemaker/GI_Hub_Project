/**
 * Phase 15d — the Garnet baseline: KG of Garnet per m² for an OLD or a NEW
 * surface, per substrate (ESC1 concrete, ESC2 steel / vessel). A one-time setup
 * that stays editable; every change is audited server-side.
 *
 * Until a figure is saved, NEW falls back to the workbook's For_1_SQM (ruling
 * Q15-9) and OLD has no benchmark — a Garnet job on an old surface then reaches
 * the HOD as "no benchmark", High Priority, like any missing benchmark.
 *
 * ⚠️ A JOB KEEPS THE FIGURE IT WAS MEASURED AGAINST. Saving here moves every
 * FUTURE variance and no past one (the benchmark is snapshotted on each row).
 */
import { useEffect, useState } from 'react'
import { Alert, App, Button, Card, InputNumber, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

interface Cell {
  code: string; state: 'OLD' | 'NEW'; state_label: string; substrate: string
  kg_per_sqm: number | null; source: 'set' | 'workbook' | null; workbook: number | null
  updated_by: string | null; updated_at: string | null
}
interface Resp { items: Cell[]; complete: boolean; garnet_saps: string[] }
interface Line { code: string; substrate: string; OLD?: Cell; NEW?: Cell }

const KEY = ['/sme/master/prep-baseline']

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Request failed'
}

function Source({ c }: { c?: Cell }) {
  if (!c) return null
  if (c.source === 'set') {
    return (
      <Typography.Text type="secondary" style={{ fontSize: 11 }}>
        saved{c.updated_by ? ` by ${c.updated_by}` : ''}
        {c.updated_at ? ` · ${new Date(c.updated_at).toLocaleDateString()}` : ''}
      </Typography.Text>
    )
  }
  if (c.source === 'workbook') return <Tag color="blue">from the workbook — not saved yet</Tag>
  return <Tag color="purple">not set — no benchmark</Tag>
}

export default function GarnetBaseline() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const q = useQuery({
    queryKey: KEY,
    queryFn: async () => (await api.get<Resp>('/sme/master/prep-baseline')).data,
  })
  const [draft, setDraft] = useState<Record<string, number | null>>({})
  useEffect(() => {
    const d: Record<string, number | null> = {}
    for (const c of q.data?.items ?? []) d[`${c.code}|${c.state}`] = c.source === 'set' ? c.kg_per_sqm : null
    setDraft(d)
  }, [q.data])

  const save = useMutation({
    mutationFn: async (changes: { code: string; state: string; kg_per_sqm: number | null }[]) => {
      for (const ch of changes) await api.put('/sme/master/prep-baseline', ch)
      return changes.length
    },
    onSuccess: (n) => {
      message.success(`Saved ${n} benchmark(s) — they apply to Garnet jobs filed from now on`)
      void qc.invalidateQueries({ queryKey: KEY })
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const items = q.data?.items ?? []
  const lines: Line[] = []
  for (const c of items) {
    let l = lines.find((x) => x.code === c.code)
    if (!l) { l = { code: c.code, substrate: c.substrate }; lines.push(l) }
    l[c.state] = c
  }
  const changes = items
    .filter((c) => {
      const saved = c.source === 'set' ? c.kg_per_sqm : null
      return (draft[`${c.code}|${c.state}`] ?? null) !== saved
    })
    .map((c) => ({ code: c.code, state: c.state, kg_per_sqm: draft[`${c.code}|${c.state}`] ?? null }))

  const cell = (state: 'OLD' | 'NEW') => (_: unknown, l: Line) => {
    const c = l[state]
    if (!c) return null
    const k = `${c.code}|${c.state}`
    return (
      <Space direction="vertical" size={2}>
        <Space>
          <InputNumber min={0.01} max={1000} step={0.5} style={{ width: 130 }}
            aria-label={`${l.substrate} — ${c.state_label} (KG per m²)`}
            value={draft[k] ?? undefined}
            placeholder={c.state === 'NEW' && c.workbook != null ? `${c.workbook} (workbook)` : 'not set'}
            onChange={(v) => setDraft((d) => ({ ...d, [k]: v == null ? null : Number(v) }))} />
          <Typography.Text type="secondary">KG/m²</Typography.Text>
        </Space>
        <Source c={c} />
      </Space>
    )
  }
  const cols: ColumnsType<Line> = [
    { title: 'Substrate', key: 's',
      render: (_: unknown, l: Line) => <><strong>{l.substrate}</strong>
        <Typography.Text type="secondary"> · {l.code}</Typography.Text></> },
    { title: 'Old surface', key: 'old', render: cell('OLD') },
    { title: 'New surface', key: 'new', render: cell('NEW') },
  ]
  const missing = items.filter((c) => c.kg_per_sqm == null)

  return (
    <Card size="small" className="gi-garnet-baseline">
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        How much Garnet a square metre of blasting should take — for an <strong>old</strong>{' '}
        surface (previously lined or coated) and a <strong>new</strong> one, on each substrate.
        When a supervisor files a Garnet job they say Old or New; the draw (in KG — a TON
        counts as 1000 KG) is compared with this figure × the area blasted, and the HOD
        sees the variance. Garnet credits no lining progress and is not in the estimator.
      </Typography.Paragraph>
      {missing.length > 0 && (
        <Alert type="warning" showIcon style={{ marginBottom: 10 }}
          title={`${missing.length} benchmark(s) not set`}
          description={`${missing.map((c) => `${c.substrate} · ${c.state_label}`).join(', ')}: Garnet
            jobs of that kind reach the HOD as "no benchmark" until a figure is saved here.`} />
      )}
      <Table size="small" pagination={false} loading={q.isLoading} columns={cols}
        dataSource={lines} rowKey={(l) => l.code} />
      <Space style={{ marginTop: 12 }} wrap>
        <Button type="primary" disabled={!changes.length} loading={save.isPending}
          onClick={() => save.mutate(changes)}>
          Save {changes.length ? `${changes.length} change(s)` : ''}
        </Button>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          A job already filed keeps the benchmark it was measured against.
          {q.data?.garnet_saps?.length ? ` Garnet materials: SAP ${q.data.garnet_saps.join(', ')}.` : ''}
        </Typography.Text>
      </Space>
    </Card>
  )
}
