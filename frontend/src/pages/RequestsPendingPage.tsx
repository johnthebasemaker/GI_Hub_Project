import { useMemo, useState } from 'react'
import { Card, Input, Segmented, Space, Switch, Tag, Tooltip, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table/interface'
import { useQuery } from '@tanstack/react-query'
import { Table } from '../lib/smartTable'
import { api } from '../api/client'
import { SiteFilter } from '../components/SiteField'
import SapMapper from '../components/SapMapper'
import { SapThumb } from '../catalogue/thumbs'

/**
 * Phase 22d — Requests & Pending (rulings Q22-12/13).
 *
 * Every line of the request workbooks the site keeps in the Drive folder
 * *Pending Material Follow-up*: what was asked for, what has arrived (worked
 * out by GI Hub from the Receipt Log — oldest request first — beside what the
 * workbook says), and what is still pending. Requests made WITHOUT a PR count
 * as "on order" in Smart Reorder. General items only: Surface Shields are not
 * here. The roll-up workbook is a check, not a source.
 */
interface Item {
  id: number; file: string; sheet: string; row: number; date: string | null
  SAP_Code: string | null; Material_Code: string | null; description: string; uom: string | null
  requested: number; received: number; wb_received: number | null; pending: number
  wb_pending: number | null; differs: boolean; pr: string | null; without_pr: boolean
  type: string | null; remarks: string | null; site: string | null; age_days: number | null
  // Phase 23c — what the mapper decided for a line the workbook gave no SAP
  status?: 'sap' | 'needs_sap' | 'not_stocked' | 'mapped' | 'not_stock' | 'linked_by_code'
}
interface Resp {
  items: Item[]; totals: { lines: number; open: number; without_pr_open: number; differs: number }
  needs_sap: { file: string; sheet: string; row: number; Material_Code: string | null; description: string }[]
  summary_check: { file: string; sheet: string; row: number; description: string
                   rollup_pending: number | null; gi_pending: number | null; why: string }[]
}

const n = (v: number | null | undefined) => (v == null ? '—' : Number(v.toFixed(3)).toLocaleString())

export default function RequestsPendingPage() {
  const [site, setSite] = useState<string | undefined>()
  const [openOnly, setOpenOnly] = useState(true)
  const [pr, setPr] = useState<'all' | 'without' | 'with'>('all')
  const [q, setQ] = useState('')
  const { data, isFetching } = useQuery<Resp>({
    queryKey: ['/material-requests', site, openOnly, pr],
    queryFn: async () => (await api.get('/material-requests', {
      params: { site_id: site, open_only: openOnly, pr: pr === 'all' ? undefined : pr } })).data,
  })
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const all = data?.items ?? []
    return needle ? all.filter((i) => `${i.description} ${i.SAP_Code ?? ''} ${i.Material_Code ?? ''} ${i.file}`
      .toLowerCase().includes(needle)) : all
  }, [data, q])

  const cols: ColumnsType<Item> = [
    { title: 'Requested', dataIndex: 'date', key: 'date', width: 105, sorter: (a, b) => String(a.date).localeCompare(String(b.date)),
      render: (v, r) => <Space direction="vertical" size={0}>
        <span>{v ?? '—'}</span>
        {r.age_days != null && <Typography.Text type="secondary" style={{ fontSize: 11 }}>{r.age_days} days ago</Typography.Text>}
      </Space> },
    { title: 'Item', key: 'item', width: 280, sorter: (a, b) => a.description.localeCompare(b.description),
      render: (_: unknown, r) => <Space size={8} align="start">{r.SAP_Code && <SapThumb sap={r.SAP_Code} size={36} />}
        <Space direction="vertical" size={0}>
        <span>{r.description}</span>
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
          {r.SAP_Code ? `SAP ${r.SAP_Code}` : r.status === 'not_stocked'
            ? <Tag color="blue" style={{ margin: 0 }}>not stocked yet</Tag>
            : r.status === 'not_stock' ? <Tag style={{ margin: 0 }}>not a stock item</Tag>
              : <Tag color="orange" style={{ margin: 0 }}>no SAP code</Tag>}
          {r.status === 'mapped' && <Tag color="green" style={{ margin: '0 0 0 4px' }}>linked here</Tag>}
          {r.Material_Code ? ` · ${r.Material_Code}` : ''}</Typography.Text>
      </Space></Space> },
    { title: 'PR', key: 'pr', width: 130,
      render: (_: unknown, r) => (r.without_pr ? <Tag color="gold">no PR</Tag> : <Typography.Text style={{ fontSize: 12 }}>{r.pr}</Typography.Text>) },
    { title: 'Asked', dataIndex: 'requested', key: 'req', align: 'right', width: 80, render: n },
    { title: 'Received', key: 'rec', align: 'right', width: 130, sorter: (a, b) => a.received - b.received,
      render: (_: unknown, r) => (
        <Tooltip title={`GI Hub (Receipt Log, oldest request first): ${n(r.received)} · the workbook says ${n(r.wb_received)}`}>
          <span data-testid="req-received">{n(r.received)}{r.differs && <Tag color="orange" style={{ marginInlineStart: 4 }}>
            workbook {n(r.wb_received)}</Tag>}</span>
        </Tooltip>) },
    { title: 'Pending', dataIndex: 'pending', key: 'pend', align: 'right', width: 90, sorter: (a, b) => a.pending - b.pending,
      render: (v: number, r) => <strong style={{ opacity: v > 0 ? 1 : 0.5 }}>{n(v)} {r.uom ?? ''}</strong> },
    { title: 'Type', dataIndex: 'type', key: 'type', width: 140 },
    { title: 'From', key: 'from', width: 220,
      render: (_: unknown, r) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.file} · row {r.row}</Typography.Text> },
  ]

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Requests &amp; Pending</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        The requests in Drive → <i>Pending Material Follow-up</i>, line by line. <b>Received</b> is worked out
        from the Receipt Log (the oldest request takes a delivery first); an orange tag shows where the
        workbook says otherwise. Requests without a PR count as <b>on order</b> in Smart Reorder.
        Surface Shields are not listed here.
      </Typography.Paragraph>
      <Space wrap style={{ marginBottom: 12 }} data-testid="req-totals">
        <Tag>{data?.totals.lines ?? 0} line(s)</Tag>
        <Tag color="blue">{data?.totals.open ?? 0} still pending</Tag>
        <Tag color="gold">{data?.totals.without_pr_open ?? 0} pending without a PR</Tag>
        {!!data?.totals.differs && <Tag color="orange">{data.totals.differs} differ from the workbook</Tag>}
      </Space>
      <Space wrap style={{ marginBottom: 12 }}>
        <SiteFilter style={{ width: 160 }} value={site} onChange={setSite} />
        <Segmented value={pr} onChange={(v) => setPr(v as typeof pr)}
          options={[{ label: 'All', value: 'all' }, { label: 'Without PR', value: 'without' }, { label: 'With PR', value: 'with' }]} />
        <span><Switch size="small" checked={openOnly} onChange={setOpenOnly} data-testid="req-open-only" /> pending only</span>
        <Input.Search allowClear placeholder="Item, SAP, code or file" style={{ width: 240 }} onSearch={setQ} onChange={(e) => !e.target.value && setQ('')} />
      </Space>
      <SapMapper site={site} />
      <div data-testid="req-table">
        <Table size="small" loading={isFetching} columns={cols} dataSource={rows} rowKey="id"
          scroll={{ x: 1200 }} pagination={{ pageSize: 25, showTotal: (t) => `${t} line(s)` }} />
      </div>
      {!!data?.summary_check.length && (
        <Card size="small" style={{ marginTop: 16 }} data-testid="req-summary-check"
          title={`The roll-up workbook disagrees on ${data.summary_check.length} line(s)`}>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
            <i>CNCEC_Indents Over all Supply and Pending Details</i> is a check, not a source: where its pending
            figure differs from GI Hub&apos;s, the row is listed here to look at. Nothing is changed.
          </Typography.Paragraph>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>{data.summary_check.slice(0, 200).map((x) => (
            <li key={`${x.sheet}|${x.row}`}>{x.sheet} row {x.row}: {x.description} — roll-up pending {n(x.rollup_pending)},
              GI Hub {x.gi_pending == null ? '—' : n(x.gi_pending)} ({x.why})</li>
          ))}</ul>
        </Card>
      )}
    </div>
  )
}
