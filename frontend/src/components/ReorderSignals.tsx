import { useMemo, useState } from 'react'
import { App, Button, Empty, Input, InputNumber, Modal, Segmented, Select, Space, Tag, Tooltip, Typography } from 'antd'
import { Link } from 'react-router-dom'
import type { ColumnsType } from 'antd/es/table'
import { Table } from '../lib/smartTable'
import { useAcceptMinimums, useSetSitePace, useSmartMin } from '../api/smartMinHooks'
import type { RagStatus, SmartMin, SmartMinRow, SmartMinSite } from '../api/smartMinHooks'
import { useAuth } from '../auth/AuthContext'
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

const PACE_SOURCE: Record<SmartMinSite['pace_source'], string> = {
  site_plan: "this site's planned rate",
  planned: 'global planned rate',
  approved_entries: 'approved work, last 30 days',
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

// Ruling Q6 option A: with no SQM pace the minimum IS the whole remaining
// plan — kept, but never left unexplained next to the number.
const WHOLE_PLAN_TIP = 'Whole remaining plan: this site has no SQM pace yet — no planned rate and '
  + 'no approved lining work in the last 30 days — so the minimum is everything the remaining plan '
  + 'still needs. Set the site\'s pace (the yellow note above) and it drops to the next 30 days of work.'

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
  const [kind, setKind] = useState<'all' | 'ss' | 'general' | 'changed'>('all')
  const [q, setQ] = useState('')

  const rows = useMemo(() => (data?.items ?? []).filter((r) =>
    (rag === 'all' || r.Status === rag)
    && (kind === 'all' || (kind === 'changed' ? r.Changed != null
      : kind === 'ss' ? r.Surface_Shield : !r.Surface_Shield))
    && (!q || `${r.SAP_Code} ${r.Description ?? ''} ${r.Material_Code ?? ''}`.toLowerCase().includes(q.toLowerCase()))),
  [data, rag, kind, q])

  const counts = data?.counts ?? { red: 0, amber: 0, green: 0, none: 0 }
  const p = data?.params ?? {}
  const { user, readOnly } = useAuth()
  const canSetPace = !readOnly && (user?.role === 'hod' || user?.role === 'admin')
  const [paceSite, setPaceSite] = useState<string | null>(null)
  // Phase 19a — the HOD's review: tick rows, edit the number, accept.
  const { message } = App.useApp()
  const accept = useAcceptMinimums()
  const [review, setReview] = useState(false)
  const [picked, setPicked] = useState<React.Key[]>([])
  const [edits, setEdits] = useState<Record<string, number | null>>({})
  const rowKey = (r: SmartMinRow) => `${r.Site_ID}|${r.SAP_Code}`
  const proposed = (r: SmartMinRow) => {
    const e = edits[rowKey(r)]
    return e !== undefined ? e : r.Recommended_Min
  }
  const submitAccept = async () => {
    const byKey = new Map((data?.items ?? []).map((r) => [rowKey(r), r]))
    const items = picked.map((k) => byKey.get(String(k))).filter((r): r is SmartMinRow => !!r)
      .filter((r) => proposed(r) != null)
      .map((r) => ({ site_id: r.Site_ID, sap_code: r.SAP_Code, minimum_qty: Number(proposed(r)) }))
    if (!items.length) return
    try {
      const res = await accept.mutateAsync(items)
      message.success(`Accepted ${res.accepted} minimum${res.accepted === 1 ? '' : 's'}`)
      setPicked([]); setEdits({})
    } catch (e) {
      const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      message.error(typeof d === 'string' ? d : 'Could not accept the minimums')
    }
  }

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
        <Tooltip title={r.Min_Source === 'accepted'
          ? `Accepted for ${r.Site_ID} by ${r.Accepted_By ?? '—'}${r.Accepted_At ? ` on ${r.Accepted_At.slice(0, 10)}` : ''}. `
            + `The system now recommends ${fmt(r.Recommended_Min)}.`
          : r.Min_Source === 'manual'
          ? `Set by hand on the item (${fmt(r.Manual_Min, 2)}). The system would recommend ${fmt(r.Recommended_Min)}.`
          : 'Recommended by the system — see Why.'}>
          <span>{fmt(v, 2)} {r.Min_Source === 'accepted'
            ? <Tag color="green" data-testid="min-accepted" style={{ marginLeft: 4 }}>accepted</Tag>
            : r.Min_Source === 'manual'
            ? <Tag style={{ marginLeft: 4 }}>manual</Tag>
            : r.Min_Source === 'smart' ? <Tag color="blue" style={{ marginLeft: 4 }}>smart</Tag> : null}
            {r.Changed != null ? (
              <Tooltip title={`The recommendation (${fmt(r.Recommended_Min)}) has moved more than `
                + `${fmt((p.changed_band ?? 0.2) * 100)} % from the accepted ${fmt(r.Accepted_Min)} — review it.`}>
                <Tag color="orange" data-testid="min-changed" style={{ marginLeft: 4, cursor: 'help' }}>changed</Tag>
              </Tooltip>
            ) : null}
            {r.Surface_Shield && r.Basis.startsWith('plan_all') ? (
              <Tooltip color="gold" title={<span style={{ color: '#111' }}>{WHOLE_PLAN_TIP}</span>}>
                <Tag color="gold" data-testid="whole-plan-tag" style={{ marginLeft: 4, cursor: 'help' }}>whole plan</Tag>
              </Tooltip>
            ) : null}</span>
        </Tooltip>
      ),
    },
    ...(review ? [{
      title: 'Accept as', key: 'accept_as', width: 140,
      render: (_: unknown, r: SmartMinRow) => (
        <InputNumber size="small" min={0} value={proposed(r)} style={{ width: 120 }}
          aria-label={`Accept minimum for ${r.SAP_Code}`}
          onChange={(v) => setEdits((m) => ({ ...m, [rowKey(r)]: v ?? null }))} />
      ),
    }] : []),
    { title: 'Days of cover', dataIndex: 'Days_Of_Cover', align: 'right', width: 110,
      render: (v) => (v == null ? '—' : fmt(v, 1)) },
    {
      title: 'On order', dataIndex: 'On_Order', align: 'right', width: 120,
      render: (v, r) => (
        <span>
          {v ? fmt(v, 2) : '—'}
          {r.Global_On_Order ? (
            <Tooltip title={`${fmt(r.Global_On_Order, 2)} more is on POs not raised from any site's PR. `
              + "It is not subtracted from this site's suggested order, so one PO is never counted twice."}>
              <div data-testid="global-on-order" style={{ fontSize: 11, opacity: 0.75, cursor: 'help' }}>
                + {fmt(r.Global_On_Order, 2)} global
              </div>
            </Tooltip>
          ) : null}
        </span>
      ),
    },
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
        twice the minimum, less what is already on open POs raised from this site's PRs (POs not
        raised from a PR are shown as <i>global</i> and subtracted from no site).
      </Typography.Paragraph>

      {error ? <Note tone="error">Could not load reorder signals.</Note> : null}

      {siteNotes.map(([s, info]) => (
        <Note key={s} tone={info.basis === 'plan_all' ? 'warning' : 'info'}>
          <span data-testid={`pace-note-${s}`}>
            <b>{s}</b>: {fmt(info.remaining_sqm)} m² still to line ·{' '}
            {info.sqm_per_day > 0
              ? <>pace {fmt(info.sqm_per_day, 1)} m²/day ({PACE_SOURCE[info.pace_source]})
                {' '}— Surface Shield minimums cover the next {fmt(p.cover_days)} days</>
              : <>no SQM pace yet — Surface Shield minimums cover the <b>whole remaining plan</b>.
                {' '}Set the site&apos;s pace to plan by rate instead</>}
            {canSetPace && info.remaining_sqm > 0 ? (
              <Button size="small" type="link" data-testid={`pace-set-${s}`}
                onClick={() => setPaceSite(s)}>
                {info.site_planned_sqm_per_day ? 'Change pace' : 'Set pace'}
              </Button>
            ) : null}
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
        <Segmented value={kind} onChange={(v) => setKind(v as typeof kind)} data-testid="kind-filter"
          options={[{ value: 'all', label: 'All items' }, { value: 'ss', label: 'Surface Shields' },
            { value: 'general', label: 'General' },
            { value: 'changed', label: `Changed (${counts.changed ?? 0})` }]} />
        {canPickSite && (
          <Select allowClear placeholder="All sites" style={{ width: 160 }} value={site}
            onChange={(v) => setSite(v || undefined)}
            options={Object.keys(data?.sites ?? {}).map((s) => ({ value: s, label: s }))} />
        )}
        <Input.Search allowClear placeholder="SAP, material code or name" style={{ width: 260 }}
          onSearch={setQ} onChange={(e) => !e.target.value && setQ('')} />
      </Space>

      {paceSite && data?.sites[paceSite] ? (
        <PaceDialog site={paceSite} info={data.sites[paceSite]} params={p}
          onClose={() => setPaceSite(null)} />
      ) : null}

      {canSetPace ? (
        <Space wrap style={{ marginBottom: 8 }}>
          <Button data-testid="min-review" type={review ? 'primary' : 'default'}
            onClick={() => { setReview(!review); setPicked([]); setEdits({}) }}>
            {review ? 'Done reviewing' : 'Review minimums'}
          </Button>
          {review ? (
            <>
              <Button type="primary" data-testid="min-accept" disabled={!picked.length}
                loading={accept.isPending} onClick={submitAccept}>
                Accept {picked.length || ''} minimum{picked.length === 1 ? '' : 's'}
              </Button>
              <Typography.Text type="secondary">
                Tick the rows to accept; edit <b>Accept as</b> to set your own number. An accepted
                minimum is your site&apos;s until you accept another.
              </Typography.Text>
            </>
          ) : null}
        </Space>
      ) : null}

      <Table<SmartMinRow>
        size="small"
        loading={isFetching}
        rowSelection={review ? { selectedRowKeys: picked, onChange: setPicked } : undefined}
        columns={columns}
        dataSource={rows}
        rowKey={(r) => `${r.Site_ID}|${r.SAP_Code}`}
        scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 50, showTotal: (t) => `${t} items` }}
      />
    </div>
  )
}

/** Ruling Q6 option B: a site plans its own lining rate. The suggestion is the
 *  site's own approved SQM per day over the pace window; the HOD keeps it or
 *  types another, and Clear goes back to the global rate / approved work. */
function PaceDialog({ site, info, params, onClose }: {
  site: string; info: SmartMinSite; params: SmartMin['params']; onClose: () => void
}) {
  const { message } = App.useApp()
  const save = useSetSitePace()
  const suggested = info.suggested_sqm_per_day
  const [rate, setRate] = useState<number | null>(
    info.site_planned_sqm_per_day ?? (suggested > 0 ? suggested : null))
  const submit = async (value: number | null) => {
    try {
      await save.mutateAsync({ site_id: site, sqm_per_day: value })
      message.success(value ? `${site}: planned pace ${fmt(value, 1)} m²/day` : `${site}: planned pace cleared`)
      onClose()
    } catch (e) {
      const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      message.error(typeof d === 'string' ? d : 'Could not save the pace')
    }
  }
  return (
    <Modal open title={`Planned SQM pace — ${site}`} onCancel={onClose} destroyOnHidden
      footer={[
        info.site_planned_sqm_per_day ? (
          <Button key="clear" danger onClick={() => submit(null)} loading={save.isPending}>Clear</Button>
        ) : null,
        <Button key="cancel" onClick={onClose}>Cancel</Button>,
        <Button key="save" type="primary" data-testid="pace-save" disabled={!rate}
          loading={save.isPending} onClick={() => submit(rate)}>Save</Button>,
      ]}>
      <Typography.Paragraph>
        How many m² of lining does <b>{site}</b> plan to finish per day? Surface Shield minimums then
        cover the next {fmt(params.cover_days)} days of that work instead of the whole remaining plan
        ({fmt(info.remaining_sqm)} m²).
      </Typography.Paragraph>
      <Typography.Paragraph type="secondary" data-testid="pace-suggestion">
        {suggested > 0
          ? <>Suggested: <b>{fmt(suggested, 1)} m²/day</b> — {site}&apos;s approved lining work over the
            last {fmt(params.pace_window_days)} days.{' '}
            <Button size="small" type="link" onClick={() => setRate(suggested)}>Use it</Button></>
          : <>No approved lining work at {site} in the last {fmt(params.pace_window_days)} days, so there
            is nothing to suggest — enter the rate you plan.</>}
      </Typography.Paragraph>
      <InputNumber min={0.1} max={100000} step={1} value={rate} suffix="m²/day"
        data-testid="pace-input" style={{ width: 220 }} onChange={(v) => setRate(v ?? null)} />
    </Modal>
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
