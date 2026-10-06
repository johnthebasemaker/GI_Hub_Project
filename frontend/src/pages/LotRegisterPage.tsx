import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, Card, Empty, Input, Space, Switch, Tag, Typography } from 'antd'
import type { ColumnsType, SortOrder } from 'antd/es/table/interface'
import { Table } from '../lib/smartTable'
import { useLotRegister, useLotUnits } from '../api/lotHooks'
import type { LotRow } from '../api/lotHooks'
import type { Row } from '../api/client'
import { SiteFilter } from '../components/SiteField'
import { daysLabel, statusColor } from '../components/LotPicker'
import { status as tone } from '../theme/tokens'

/**
 * Phase 16 — the Lot Register: every lot, its expiry and what is left of it.
 *
 * A lot's quantities are computed from the ledger (received − consumed −
 * returned); its MFD and expiry come from the Lot Register workbook or the
 * Receive form, and a *derived* expiry is MFD + the item's shelf life. Expired
 * and soon-expiring lots are what this page exists to show (ruling Q16-16:
 * store keeper, HOD, QC, Head of Qualities).
 */
const STATUS_LABEL: Record<string, string> = {
  expired: 'Expired', expiring_30: '≤ 30 days', expiring_60: '≤ 60 days',
  expiring_90: '≤ 90 days', ok: 'OK', no_expiry: 'No expiry', exhausted: 'Used up',
  disposed: 'Disposed', quarantined: 'Quarantined',
}
const ORDER = ['expired', 'expiring_30', 'expiring_60', 'expiring_90', 'ok', 'no_expiry']

const n = (v: unknown) => (v == null ? '—' : Number(Number(v).toFixed(3)))

// Phase 21a — every header sorts. Status sorts by URGENCY, not alphabet.
const STATUS_RANK: Record<string, number> = {
  expired: 0, expiring_30: 1, expiring_60: 2, expiring_90: 3, ok: 4, no_expiry: 5,
  quarantined: 6, disposed: 7, exhausted: 8,
}
/** A date sorter that keeps EMPTY dates last in BOTH directions. antd negates
 *  the comparator for a descending sort, so a blank must answer the opposite
 *  way there — otherwise "newest expiry first" opens on a page of dashes. */
function datesEmptyLast(get: (r: LotRow) => unknown) {
  return (a: LotRow, b: LotRow, order?: SortOrder) => {
    const x = String(get(a) ?? '').slice(0, 10)
    const y = String(get(b) ?? '').slice(0, 10)
    if (!x || !y) {
      if (!x && !y) return 0
      const blankLast = order === 'descend' ? -1 : 1
      return !x ? blankLast : -blankLast
    }
    return x < y ? -1 : x > y ? 1 : 0
  }
}
const SORT_KEYS = ['m', 'lot', 'mfd', 'exp', 'st', 'rq', 'cq', 'tq', 'left', 'site'] as const

function Rolls({ r }: { r: LotRow }) {
  const { data, isLoading } = useLotUnits(r.SAP_Code, r.Lot_Number, String(r.Site_ID ?? ''))
  const cols: ColumnsType<Row> = [
    { title: 'Roll', dataIndex: 'Unit_No', key: 'u' },
    { title: 'Received', dataIndex: 'Received_Date', key: 'r', width: 110 },
    { title: 'Location', dataIndex: 'Location', key: 'l' },
    { title: 'Issued on', dataIndex: 'Used_On', key: 'd', width: 110, render: (v) => v ?? <Tag color="green">in stock</Tag> },
    { title: 'Tank', dataIndex: 'Tank_No', key: 't' },
  ]
  return (
    <>
      <Typography.Text type="secondary">
        {data ? `${data.in_stock} roll(s) in stock · ${data.used} issued` : ''}
      </Typography.Text>
      <Table size="small" loading={isLoading} columns={cols} dataSource={data?.items ?? []}
        rowKey={(x) => String(x.Unit_No)} pagination={{ pageSize: 10 }} />
    </>
  )
}

export default function LotRegisterPage() {
  // The chosen sort lives in the URL (?sort=exp&dir=desc), so a shared link
  // opens the same view. Default: oldest expiry first, as the page promises.
  const [params, setParams] = useSearchParams()
  const sortKey = (SORT_KEYS as readonly string[]).includes(params.get('sort') ?? '')
    ? String(params.get('sort')) : 'exp'
  const sortDir: SortOrder = params.get('dir') === 'desc' ? 'descend' : 'ascend'
  const order = (key: string): SortOrder => (key === sortKey ? sortDir : null)
  const [site, setSite] = useState<string | undefined>()
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<string | undefined>()
  const [exhausted, setExhausted] = useState(false)
  const { data, isFetching } = useLotRegister({
    site_id: site, q: q || undefined, status, include_exhausted: exhausted,
  })
  const summary = data?.summary ?? {}

  const cols: ColumnsType<LotRow> = [
    { title: 'Material', key: 'm', fixed: 'left', width: 260, sortOrder: order('m'),
      sorter: (a, b) => String(a.Equipment_Description ?? a.SAP_Code)
        .localeCompare(String(b.Equipment_Description ?? b.SAP_Code)),
      render: (_: unknown, r) => (
        <Space direction="vertical" size={0}>
          <span>{String(r.Equipment_Description ?? r.SAP_Code)}</span>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>SAP {r.SAP_Code}</Typography.Text>
        </Space>) },
    { title: 'Lot', dataIndex: 'Lot_Number', key: 'lot', width: 150, sortOrder: order('lot'),
      render: (v, r) => <><strong>{v}</strong>{Number(r.Units) > 0 && <Tag style={{ marginInlineStart: 6 }}>{String(r.Units)} rolls</Tag>}</> },
    { title: 'MFD', dataIndex: 'MFD_Date', key: 'mfd', width: 105, sortOrder: order('mfd'),
      sorter: datesEmptyLast((r) => r.MFD_Date), render: (v) => v ?? '—' },
    { title: 'Expiry', dataIndex: 'Expiry_Date', key: 'exp', width: 140, sortOrder: order('exp'),
      sorter: datesEmptyLast((r) => r.Expiry_Date),
      render: (v, r) => (v ? <>{String(v).slice(0, 10)}{r.Expiry_Source === 'derived' &&
        <Tag style={{ marginInlineStart: 4 }}>derived</Tag>}</> : '—') },
    { title: 'Status', key: 'st', width: 150, sortOrder: order('st'),
      sorter: (a, b) => (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9)
        || (a.days_left ?? 1e9) - (b.days_left ?? 1e9),
      render: (_: unknown, r) => <Tag color={statusColor(r.status)}>
        {STATUS_LABEL[r.status] ?? r.status}{r.days_left != null ? ` · ${daysLabel(r)}` : ''}</Tag> },
    { title: 'Received', dataIndex: 'Received_Qty', key: 'rq', align: 'right', width: 90, render: n, sortOrder: order('rq') },
    { title: 'Consumed', dataIndex: 'Consumed_Qty', key: 'cq', align: 'right', width: 90, render: n, sortOrder: order('cq') },
    { title: 'Returned', dataIndex: 'Returned_Qty', key: 'tq', align: 'right', width: 90, render: n, sortOrder: order('tq') },
    { title: 'Remaining', dataIndex: 'Remaining_Qty', key: 'left', align: 'right', width: 100, sortOrder: order('left'),
      render: (v, r) => <strong>{n(v)} {String(r.UOM ?? '')}</strong> },
    { title: 'Site', dataIndex: 'Site_ID', key: 'site', width: 80, sortOrder: order('site') },
  ]

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Lots &amp; Expiry</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Every lot of a lot-tracked material, oldest expiry first. Quantities are worked out
        from the ledger (received − consumed − returned). An expiry marked <Tag>derived</Tag>
        is the manufacture date plus the item&apos;s shelf life.
      </Typography.Paragraph>
      <Space wrap style={{ marginBottom: 12 }} data-testid="lot-summary">
        {ORDER.filter((k) => summary[k]).map((k) => (
          <Tag.CheckableTag key={k} checked={status === k}
            onChange={(on) => setStatus(on ? k : undefined)}>
            <Tag color={statusColor(k)} style={{ margin: 0 }}>{STATUS_LABEL[k]}: {summary[k]}</Tag>
          </Tag.CheckableTag>
        ))}
      </Space>
      <Space wrap style={{ marginBottom: 12 }}>
        <SiteFilter style={{ width: 160 }} value={site} onChange={setSite} />
        <Input.Search allowClear placeholder="Material, SAP or lot" style={{ width: 260 }}
          aria-label="Find a lot" onSearch={setQ} />
        <span><Switch size="small" checked={exhausted} onChange={setExhausted} /> show used-up lots</span>
      </Space>
      {(summary.expired ?? 0) > 0 && !status && (
        <Alert type="error" showIcon style={{ marginBottom: 12 }}
          title={`${summary.expired} lot(s) are past their expiry and still have stock`}
          description="They are never the FEFO suggestion. Check them, then dispose of them or put them in quarantine (Admin Console → Lots)." />
      )}
      <div data-testid="lot-table">
      <Table size="small" loading={isFetching} columns={cols} dataSource={data?.items ?? []}
        rowKey={(r) => `${r.SAP_Code}|${r.Lot_Number}|${String(r.Site_ID)}`}
        scroll={{ x: 1300 }} pagination={{ pageSize: 25, showTotal: (t) => `${t} lot(s)` }}
        showSorterTooltip={false}
        onChange={(_p, _f, sorter) => {
          const one = Array.isArray(sorter) ? sorter[0] : sorter
          const next = new URLSearchParams(params)
          if (one?.order && one.columnKey) {
            next.set('sort', String(one.columnKey))
            next.set('dir', one.order === 'descend' ? 'desc' : 'asc')
          } else {
            next.delete('sort'); next.delete('dir')
          }
          setParams(next, { replace: true })
        }}
        locale={{ emptyText: <Empty description="No lots match" /> }}
        expandable={{
          rowExpandable: (r) => Number(r.Units) > 0,
          expandedRowRender: (r) => <Rolls r={r} />,
        }} />
      </div>
      {!!data?.problems?.length && (
        <Card size="small" style={{ marginTop: 16 }} data-testid="lot-problems"
          title={<span style={{ color: tone.critical }}>
            Lot problems from the workbook ({data.problems.length})</span>}>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
            These rows name a lot that no receipt brought in, or a lot that was already used up.
            The stock still counts — fix the row in the workbook, then sync again. <b>Row</b> is the
            Excel row at the last sync: inserting rows above it moves it.
          </Typography.Paragraph>
          <Table size="small" pagination={{ pageSize: 20, hideOnSinglePage: true }}
            dataSource={data.problems}
            rowKey={(r) => `${r.kind}|${r.id ?? ''}|${r.sheet ?? ''}|${r.row ?? ''}|${r.lot}`}
            columns={[
              { title: 'Sheet', dataIndex: 'sheet', key: 'sh', width: 140,
                render: (v) => v ?? <Typography.Text type="secondary">entered in the app</Typography.Text> },
              { title: 'Row', dataIndex: 'row', key: 'rw', width: 80, align: 'right',
                render: (v) => (v ? <strong data-testid="lot-problem-row">{Number(v).toLocaleString()}</strong> : '—') },
              { title: 'Date', dataIndex: 'date', key: 'd', width: 105 },
              { title: 'SAP', dataIndex: 'sap', key: 's', width: 90 },
              { title: 'Lot as written', dataIndex: 'lot', key: 'l', width: 140 },
              { title: 'Qty', dataIndex: 'qty', key: 'q', width: 70, align: 'right' },
              { title: 'Problem', dataIndex: 'problem', key: 'p', width: 170,
                render: (v) => <Tag color={v === 'unknown_lot' ? 'red' : 'orange'}>
                  {v === 'unknown_lot' ? 'Lot not received' : 'Lot already used up'}</Tag> },
              { title: 'Hint', dataIndex: 'hint', key: 'h' },
            ]} />
        </Card>
      )}
    </div>
  )
}
