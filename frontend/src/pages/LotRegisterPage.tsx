import { useState } from 'react'
import { Alert, Card, Empty, Input, Space, Switch, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useLotRegister, useLotUnits } from '../api/lotHooks'
import type { LotRow } from '../api/lotHooks'
import type { Row } from '../api/client'
import { SiteFilter } from '../components/SiteField'
import { daysLabel, statusColor } from '../components/LotPicker'

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
  disposed: 'Disposed', quarantine: 'Quarantine',
}
const ORDER = ['expired', 'expiring_30', 'expiring_60', 'expiring_90', 'ok', 'no_expiry']

const n = (v: unknown) => (v == null ? '—' : Number(Number(v).toFixed(3)))

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
  const [site, setSite] = useState<string | undefined>()
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<string | undefined>()
  const [exhausted, setExhausted] = useState(false)
  const { data, isFetching } = useLotRegister({
    site_id: site, q: q || undefined, status, include_exhausted: exhausted,
  })
  const summary = data?.summary ?? {}

  const cols: ColumnsType<LotRow> = [
    { title: 'Material', key: 'm', fixed: 'left', width: 260,
      render: (_: unknown, r) => (
        <Space direction="vertical" size={0}>
          <span>{String(r.Equipment_Description ?? r.SAP_Code)}</span>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>SAP {r.SAP_Code}</Typography.Text>
        </Space>) },
    { title: 'Lot', dataIndex: 'Lot_Number', key: 'lot', width: 150,
      render: (v, r) => <><strong>{v}</strong>{Number(r.Units) > 0 && <Tag style={{ marginInlineStart: 6 }}>{String(r.Units)} rolls</Tag>}</> },
    { title: 'MFD', dataIndex: 'MFD_Date', key: 'mfd', width: 105, render: (v) => v ?? '—' },
    { title: 'Expiry', dataIndex: 'Expiry_Date', key: 'exp', width: 140,
      render: (v, r) => (v ? <>{String(v).slice(0, 10)}{r.Expiry_Source === 'derived' &&
        <Tag style={{ marginInlineStart: 4 }}>derived</Tag>}</> : '—') },
    { title: 'Status', key: 'st', width: 150,
      render: (_: unknown, r) => <Tag color={statusColor(r.status)}>
        {STATUS_LABEL[r.status] ?? r.status}{r.days_left != null ? ` · ${daysLabel(r)}` : ''}</Tag> },
    { title: 'Received', dataIndex: 'Received_Qty', key: 'rq', align: 'right', width: 90, render: n },
    { title: 'Consumed', dataIndex: 'Consumed_Qty', key: 'cq', align: 'right', width: 90, render: n },
    { title: 'Returned', dataIndex: 'Returned_Qty', key: 'tq', align: 'right', width: 90, render: n },
    { title: 'Remaining', dataIndex: 'Remaining_Qty', key: 'left', align: 'right', width: 100,
      render: (v, r) => <strong>{n(v)} {String(r.UOM ?? '')}</strong> },
    { title: 'Site', dataIndex: 'Site_ID', key: 'site', width: 80 },
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
      <Table size="small" loading={isFetching} columns={cols} dataSource={data?.items ?? []}
        rowKey={(r) => `${r.SAP_Code}|${r.Lot_Number}|${String(r.Site_ID)}`}
        scroll={{ x: 1300 }} pagination={{ pageSize: 25, showTotal: (t) => `${t} lot(s)` }}
        locale={{ emptyText: <Empty description="No lots match" /> }}
        expandable={{
          rowExpandable: (r) => Number(r.Units) > 0,
          expandedRowRender: (r) => <Rolls r={r} />,
        }} />
      {!!data?.exceptions?.length && (
        <Card size="small" style={{ marginTop: 16 }}
          title={`Lots used but never received (${data.exceptions.length})`}>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
            The Consumption or Return Log names these lots, but no receipt of that material
            brought them in — usually a typing slip. The stock still counts; correct the
            workbook and sync again.
          </Typography.Paragraph>
          <Table size="small" pagination={false} dataSource={data.exceptions}
            rowKey={(r) => `${String(r.kind)}|${String(r.sap)}|${String(r.lot)}`}
            columns={[
              { title: 'Log', dataIndex: 'kind', key: 'k', width: 110 },
              { title: 'SAP', dataIndex: 'sap', key: 's', width: 100 },
              { title: 'Lot as typed', dataIndex: 'lot', key: 'l' },
              { title: 'Rows', dataIndex: 'n', key: 'n', width: 70 },
              { title: 'Qty', dataIndex: 'qty', key: 'q', width: 80 },
              { title: 'Received under SAP', dataIndex: 'received_under', key: 'r',
                render: (v) => v ?? '—' },
            ]} />
        </Card>
      )}
    </div>
  )
}
