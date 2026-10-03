import { useMemo, useState } from 'react'
import { Empty, Input, Segmented, Select, Space, Tag, Tooltip, Typography } from 'antd'
import { Link } from 'react-router-dom'
import type { ColumnsType } from 'antd/es/table'
import { Table } from '../lib/smartTable'
import { useSmartMin } from '../api/smartMinHooks'
import type { RagStatus, SmartMinRow } from '../api/smartMinHooks'
import { status as statusColors } from '../theme/tokens'
const status = statusColors

// Reorder signals — Phase 18 Track 4. What to order, and why, per item and
// site: a recommended minimum (consumption for general items, the SQM plan for
// Surface Shields — services/smart_min.py), the stock against it as red /
// amber / green, and a suggested order quantity. Read-only: a manual minimum
// set on the item stays the operator's and wins.

const RAG: Record<RagStatus, { color: string; label: string }> = {
  red: { color: statusColors.critical, label: 'Order now' },
  amber: { color: statusColors.low, label: 'Order soon' },
  green: { color: statusColors.ok, label: 'OK' },
  none: { color: '#9CA3AF', label: 'No signal' },
}

const BASIS: Record<string, string> = {
  consumption: 'Past use',
  plan_pace: 'SQM plan',
  plan_all: 'SQM plan (whole)',
  plan_pace_garnet: 'Garnet (Old/New)',
  plan_all_garnet: 'Garnet (whole)',
  no_use: 'No recent use',
  no_plan: 'Not in plan',
  garnet_pool: 'Garnet pool',
  no_pack_size: 'No pack size',
}

function fmt(n: number | null | undefined, dp = 0): string {
  if (n == null) return '—'
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: dp })
}

// A plain note rather than antd's Alert: Alert's icon chunk is shared with
// the login page, and one more lazy importer made Rollup split it into its own
// chunk — +391 B on the sign-in critical path for a coloured box (Phase 18).
function Note({ tone, children }: { tone: 'info' | 'warning' | 'error'; children: React.ReactNode }) {
  const c = tone === 'error' ? statusColors.critical : tone === 'warning' ? statusColors.low : statusColors.info
  return (
    <div role={tone === 'error' ? 'alert' : 'note'} style={{
      borderLeft: `4px solid ${c}`, background: `${c}14`, padding: '6px 10px',
      borderRadius: 6, marginBottom: 8 }}>{children}</div>
  )
}

export default function ReorderSignals({ canPickSite }: { canPickSite: boolean }) {
  const [site, setSite] = useState<string | undefined>(undefined)
  const { data, isFetching, error } = useSmartMin(site)
  const [rag, setRag] = useState<RagStatus | 'all'>('all')
  const [kind, setKind] = useState<'all' | 'ss' | 'general'>('all')
  const [q, setQ] = useState('')

  const rows = useMemo(() => (data?.items ?? []).filter((r) =>
    (rag === 'all' || r.Status === rag)
    && (kind === 'all' || (kind === 'ss' ? r.Surface_Shield : !r.Surface_Shield))
    && (!q || `${r.SAP_Code} ${r.Description ?? ''} ${r.Material_Code ?? ''}`.toLowerCase().includes(q.toLowerCase()))),
  [data, rag, kind, q])

  const counts = data?.counts ?? { red: 0, amber: 0, green: 0, none: 0 }
  const p = data?.params ?? {}

  const columns: ColumnsType<SmartMinRow> = [
    {
      title: 'Status', dataIndex: 'Status', width: 120, fixed: 'left',
      render: (v: RagStatus) => (
        <Tag bordered={false} data-testid={`rag-${v}`}
          style={{ background: RAG[v].color, color: v === 'amber' || v === 'none' ? '#111' : '#fff', fontWeight: 600 }}>
          {RAG[v].label}
        </Tag>
      ),
    },
    { title: 'SAP', dataIndex: 'SAP_Code', width: 90 },
    { title: 'Item', dataIndex: 'Description', ellipsis: true, width: 260 },
    ...(canPickSite ? [{ title: 'Site', dataIndex: 'Site_ID', width: 90 }] : []),
    { title: 'Stock', dataIndex: 'Current_Stock', align: 'right', width: 90, render: (v) => fmt(v, 2) },
    {
      title: 'Minimum', dataIndex: 'Effective_Min', align: 'right', width: 130,
      render: (v, r) => (
        <Tooltip title={r.Min_Source === 'manual'
          ? `Set by hand on the item (${fmt(r.Manual_Min, 2)}). The system would recommend ${fmt(r.Recommended_Min)}.`
          : 'Recommended by the system — see Why.'}>
          <span>{fmt(v, 2)} {r.Min_Source === 'manual'
            ? <Tag style={{ marginLeft: 4 }}>manual</Tag>
            : r.Min_Source === 'smart' ? <Tag color="blue" style={{ marginLeft: 4 }}>smart</Tag> : null}</span>
        </Tooltip>
      ),
    },
    { title: 'Days of cover', dataIndex: 'Days_Of_Cover', align: 'right', width: 110,
      render: (v) => (v == null ? '—' : fmt(v, 1)) },
    { title: 'On order', dataIndex: 'On_Order', align: 'right', width: 90,
      render: (v) => (v ? fmt(v, 2) : '—') },
    {
      title: 'Suggested order', dataIndex: 'Suggested_Order', align: 'right', width: 130,
      render: (v, r) => (v ? <Typography.Text strong>{fmt(v)} {r.UOM ?? ''}</Typography.Text> : '—'),
    },
    {
      title: 'Why', key: 'why', width: 380,
      render: (_: unknown, r: SmartMinRow) => (
        <Space size={4} wrap>
          <Tag>{BASIS[r.Basis] ?? r.Basis}</Tag>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.Why}</Typography.Text>
        </Space>
      ),
    },
  ]

  const siteNotes = Object.entries(data?.sites ?? {})

  return (
    <div data-testid="reorder-signals">
      <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
        A minimum for every item, worked out by the system unless one was set by hand.
        <b> General items</b>: the daily use (the higher of the last 30 and {fmt(p.window_days)} days) ×{' '}
        {fmt(p.cover_days)} days of cover. <b>Surface Shields</b>: the kilograms / packs the remaining
        SQM plan needs for the next {fmt(p.cover_days)} days of work (Garnet at the equipment's Old /
        New surface rate) — never past use. <span style={{ color: statusColors.critical }}>Red</span>{' '}
        = below the minimum, <span style={{ color: statusColors.low }}>amber</span> = within{' '}
        {fmt(((p.amber_factor ?? 1.5) - 1) * 100)} % of it. The suggested order brings stock back to
        twice the minimum, less what is already on open POs.
      </Typography.Paragraph>

      {error ? <Note tone="error">Could not load reorder signals.</Note> : null}

      {siteNotes.map(([s, info]) => (
        <Note key={s} tone={info.basis === 'plan_all' ? 'warning' : 'info'}>
          <span>
            <b>{s}</b>: {fmt(info.remaining_sqm)} m² still to line ·{' '}
            {info.sqm_per_day > 0
              ? <>pace {fmt(info.sqm_per_day, 1)} m²/day ({info.pace_source === 'planned' ? 'planned rate' : 'approved work, last 30 days'})
                {' '}— Surface Shield minimums cover the next {fmt(p.cover_days)} days</>
              : <>no SQM pace yet — Surface Shield minimums cover the <b>whole remaining plan</b> (set{' '}
                <code>ss_planned_sqm_per_day</code> under Admin → Settings to plan by rate)</>}
          </span>
          {info.flags.length ? <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{info.flags.map((f) => <li key={f}>{f}</li>)}</ul> : null}
        </Note>
      ))}

      <Space wrap style={{ marginBottom: 8 }}>
        <Segmented value={rag} onChange={(v) => setRag(v as typeof rag)} data-testid="rag-filter"
          options={[
            { value: 'all', label: 'All' },
            ...(['red', 'amber', 'green', 'none'] as RagStatus[]).map((k) => ({
              value: k,
              label: (
                <span>
                  <span aria-hidden style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 4,
                    background: RAG[k].color, marginRight: 6 }} />
                  {RAG[k].label} ({counts[k]})
                </span>
              ),
            })),
          ]} />
        <Segmented value={kind} onChange={(v) => setKind(v as typeof kind)}
          options={[{ value: 'all', label: 'All items' }, { value: 'ss', label: 'Surface Shields' },
            { value: 'general', label: 'General' }]} />
        {canPickSite && (
          <Select allowClear placeholder="All sites" style={{ width: 160 }} value={site}
            onChange={(v) => setSite(v || undefined)}
            options={Object.keys(data?.sites ?? {}).map((s) => ({ value: s, label: s }))} />
        )}
        <Input.Search allowClear placeholder="SAP, material code or name" style={{ width: 260 }}
          onSearch={setQ} onChange={(e) => !e.target.value && setQ('')} />
      </Space>

      <Table<SmartMinRow>
        size="small"
        loading={isFetching}
        columns={columns}
        dataSource={rows}
        rowKey={(r) => `${r.Site_ID}|${r.SAP_Code}`}
        scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 50, showTotal: (t) => `${t} items` }}
      />
    </div>
  )
}

/** Phase 18 Track 4: with no minimum set by hand anywhere, the chart below had
 *  nothing to draw ("No minimums set") on every site. The system's own
 *  recommended minimums (services/smart_min.py) fill the gap: how many items
 *  are red / amber / green, and the most urgent ones, one click from the full
 *  Reorder signals tab. */
export function ReorderMini() {
  const { data, isLoading } = useSmartMin()
  if (isLoading) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Working out minimums…" />
  const c = data?.counts ?? { red: 0, amber: 0, green: 0, none: 0 }
  const urgent = (data?.items ?? []).filter((r) => r.Status === 'red').slice(0, 5)
  return (
    <div data-testid="reorder-mini">
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
        <Tag color={status.critical}>{c.red} order now</Tag>
        <Tag color={status.low} style={{ color: '#111' }}>{c.amber} order soon</Tag>
        <Tag color={status.ok}>{c.green} OK</Tag>
      </div>
      {urgent.length ? (
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {urgent.map((r) => (
            <li key={`${r.Site_ID}|${r.SAP_Code}`}>
              <Typography.Text>{r.SAP_Code} · {r.Description}</Typography.Text>{' '}
              <Typography.Text type="secondary">
                — {fmt(r.Current_Stock)} of {fmt(r.Effective_Min)}{r.Suggested_Order ? `, order ${fmt(r.Suggested_Order)}` : ''}
              </Typography.Text>
            </li>
          ))}
        </ul>
      ) : <Typography.Text type="secondary">Nothing below its minimum.</Typography.Text>}
      <div style={{ marginTop: 8 }}><Link to="/stock?tab=reorder">Open Reorder signals →</Link></div>
    </div>
  )
}
