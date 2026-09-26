/**
 * frontend/src/pages/MaterialCardPage.tsx — Material Intelligence.
 *
 * Where a QR scan lands (header scanner → /stock/material/:sap) and where any
 * SAP code can link. Data is one role-scoped call to GET /stock/material-card:
 * SK / supervisor / warehouse / HOD see ONLY their own site, admin & logistics
 * see the global picture — the scope chip says which, and the per-site split
 * only appears for the unscoped roles because it is meaningless otherwise.
 *
 * The identifier in the URL may be a SAP code, a Material_Code, or a raw label
 * payload ("1163|Cable Tie Wire ( Nylon)"); the server resolves all three, so
 * this page never has to guess what a sticker encodes.
 */
import { fmtPackBase } from '../lib/units'
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Alert, Button, Card, Col, Descriptions, Empty, Radio, Row, Segmented, Skeleton,
  Space, Statistic, Tag, Typography,
} from 'antd'
import { ArrowLeftOutlined, WarningOutlined } from '@ant-design/icons'
import KpiRow from '../components/KpiRow'
import { Table } from '../lib/smartTable'
import { useQuery } from '@tanstack/react-query'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, ComposedChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api } from '../api/client'

interface Lot {
  Lot_Number: string; Expiry_Date: string | null; Status: string | null
  Site_ID: string | null; Remaining_Qty: number
}
interface Move { d: string; kind: string; qty: number; party: string; site: string }
interface CardData {
  sap_code: string; description: string; material_code: string | null
  category: string; uom: string; scope: string | null
  current_stock: number; minimum_qty: number; unit_cost: number
  stock_value: number; below_minimum: boolean
  window_days: number; avg_daily_consumption: number; days_of_cover: number | null
  series: { date: string; received: number; consumed: number; balance: number }[]
  lots: Lot[]; movements: Move[]; by_site: { site: string; stock: number }[]
  totals: { received_30d: number; consumed_30d: number }
}

// Two fixed categorical hues (received/consumed) — a CVD-separated pair; the
// balance line is ink, not a third category, because it is a different QUANTITY
// (a level, not a flow) and must not read as another series of the same kind.
const C_RECEIVED = '#4C78DB'
const C_CONSUMED = '#E8894A'
const C_BALANCE = '#8C8C8C'
const C_LOW = '#EF4444'

const nf = (v: number) =>
  v.toLocaleString('en-US', { maximumFractionDigits: 2 })

/** Days of cover → the one sentence an operator actually needs. */
function coverTone(d: CardData): { text: string; color: string } {
  if (d.avg_daily_consumption <= 0) {
    return { text: 'No consumption in this window — burn rate unknown', color: '#8C8C8C' }
  }
  const c = d.days_of_cover ?? 0
  if (c < 7) return { text: `About ${c} days of cover left`, color: C_LOW }
  if (c < 30) return { text: `About ${c} days of cover left`, color: '#F59E0B' }
  return { text: `About ${c} days of cover left`, color: '#10B981' }
}

export default function MaterialCardPage() {
  const { sap = '' } = useParams()
  const navigate = useNavigate()
  const [days, setDays] = useState(30)
  const [flowMode, setFlowMode] = useState<'flows' | 'balance'>('flows')

  const q = useQuery({
    queryKey: ['/stock/material-card', sap, days],
    enabled: !!sap,
    retry: false,
    queryFn: async () =>
      (await api.get('/stock/material-card', { params: { sap, days } })).data as CardData,
  })
  const d = q.data

  if (q.isError) {
    return (
      <>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)} style={{ marginBottom: 12 }}>
          Back
        </Button>
        <Alert type="warning" showIcon
          title={(q.error as { response?: { data?: { detail?: string } } })
            ?.response?.data?.detail ?? 'No inventory item matches this code.'}
          description={`Scanned identifier: ${sap}`} />
      </>
    )
  }
  if (!d) return <Skeleton active paragraph={{ rows: 8 }} />

  const cover = coverTone(d)
  const hasFlow = d.series.some((p) => p.received > 0 || p.consumed > 0)

  return (
    <>
      <Space wrap style={{ marginBottom: 12, rowGap: 8 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)}>Back</Button>
        {d.scope
          ? <Tag color="blue">Your site: {d.scope}</Tag>
          : <Tag color="gold">All sites (global)</Tag>}
        {d.below_minimum && (
          <Tag icon={<WarningOutlined />} color="error">Below minimum ({nf(d.minimum_qty)} {d.uom})</Tag>
        )}
      </Space>

      <Typography.Title level={3} style={{ margin: 0 }}>
        {d.description || d.sap_code}
      </Typography.Title>
      <Typography.Text type="secondary">
        SAP {d.sap_code}{d.material_code ? ` · MAT ${d.material_code}` : ''}
        {d.category ? ` · ${d.category}` : ''}
      </Typography.Text>

      {/* KPI strip */}
      <KpiRow style={{ marginTop: 16 }}>
        <Card size="small">
          <Statistic title={`Current stock${d.scope ? ` @ ${d.scope}` : ' (all sites)'}`}
            value={d.current_stock} suffix={d.uom}
            valueStyle={d.below_minimum ? { color: C_LOW } : undefined}
            precision={Number.isInteger(d.current_stock) ? 0 : 2} />
          {/* Phase 14a: the base figure (KG / M2) beside the pack count. */}
          {fmtPackBase(d.sap_code, d.current_stock, d.uom) && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {fmtPackBase(d.sap_code, d.current_stock, d.uom)}
            </Typography.Text>
          )}
        </Card>
        <Card size="small">
          <Statistic title="Days of cover"
            value={d.days_of_cover ?? '—'} valueStyle={{ color: cover.color }} />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {d.avg_daily_consumption > 0
              ? `${nf(d.avg_daily_consumption)} ${d.uom}/day burn`
              : 'no issues in window'}
          </Typography.Text>
        </Card>
        <Card size="small">
          <Statistic title={`Received (${d.window_days}d)`} value={d.totals.received_30d}
            suffix={d.uom} valueStyle={{ color: C_RECEIVED }} precision={2} />
        </Card>
        <Card size="small">
          <Statistic title={`Consumed (${d.window_days}d)`} value={d.totals.consumed_30d}
            suffix={d.uom} valueStyle={{ color: C_CONSUMED }} precision={2} />
        </Card>
      </KpiRow>

      <Alert type="info" showIcon style={{ marginTop: 12 }}
        title={cover.text}
        description={d.unit_cost > 0
          ? `Stock value ≈ SAR ${nf(d.stock_value)} at SAR ${nf(d.unit_cost)}/${d.uom}.`
          : undefined} />

      {/* Trend */}
      <Card size="small" style={{ marginTop: 16 }}
        title="Movement trend"
        extra={
          <Space wrap>
            <Radio.Group size="small" value={flowMode} onChange={(e) => setFlowMode(e.target.value)}
              optionType="button" buttonStyle="solid"
              options={[{ label: 'Flows', value: 'flows' }, { label: 'Balance', value: 'balance' }]} />
            <Segmented size="small" value={days} onChange={(v) => setDays(v as number)}
              options={[{ label: '30d', value: 30 }, { label: '90d', value: 90 }, { label: '180d', value: 180 }]} />
          </Space>
        }>
        {!hasFlow ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={`No receipts or issues in the last ${d.window_days} days`} />
        ) : (
          <div style={{ width: '100%', height: 300 }}>
            <ResponsiveContainer>
              {flowMode === 'flows' ? (
                <ComposedChart data={d.series} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeOpacity={0.15} vertical={false} />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} tickLine={false}
                    tickFormatter={(v: string) => v.slice(5)} minTickGap={24} />
                  <YAxis tick={{ fontSize: 11 }} tickLine={false} axisLine={false} width={46} />
                  <Tooltip cursor={{ fillOpacity: 0.06 }} contentStyle={{ borderRadius: 8 }}
                    labelStyle={{ fontWeight: 600 }} formatter={(v) => nf(Number(v))} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Bar dataKey="received" name="Received" fill={C_RECEIVED}
                    radius={[3, 3, 0, 0]} maxBarSize={14} />
                  <Bar dataKey="consumed" name="Consumed" fill={C_CONSUMED}
                    radius={[3, 3, 0, 0]} maxBarSize={14} />
                  {/* The level behind the flows — same axis, deliberately quiet. */}
                  <Line type="monotone" dataKey="balance" name="Stock balance"
                    stroke={C_BALANCE} strokeWidth={1.5} dot={false} />
                </ComposedChart>
              ) : (
                <AreaChart data={d.series} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeOpacity={0.15} vertical={false} />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} tickLine={false}
                    tickFormatter={(v: string) => v.slice(5)} minTickGap={24} />
                  <YAxis tick={{ fontSize: 11 }} tickLine={false} axisLine={false} width={46} />
                  <Tooltip contentStyle={{ borderRadius: 8 }} labelStyle={{ fontWeight: 600 }}
                    formatter={(v) => nf(Number(v))} />
                  <Area type="monotone" dataKey="balance" name="Stock balance"
                    stroke={C_RECEIVED} fill={C_RECEIVED} fillOpacity={0.18} strokeWidth={2} />
                </AreaChart>
              )}
            </ResponsiveContainer>
          </div>
        )}
      </Card>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={12}>
          <Card size="small" title="Recent movements">
            <Table<Move> size="small" rowKey={(r, i) => `${r.d}-${r.kind}-${i}`}
              dataSource={d.movements} pagination={false}
              scroll={{ x: 'max-content' }}
              locale={{ emptyText: 'No ledger movements yet' }}
              columns={[
                { title: 'Date', dataIndex: 'd', width: 110,
                  render: (v: string) => String(v).slice(0, 10) },
                { title: 'Type', dataIndex: 'kind', width: 100,
                  render: (v: string) => (
                    <Tag color={v === 'Received' ? 'blue' : v === 'Issued' ? 'orange' : 'default'}>{v}</Tag>
                  ) },
                { title: 'Qty', dataIndex: 'qty', align: 'right', width: 90,
                  render: (v: number) => nf(v) },
                { title: 'Party / reason', dataIndex: 'party', ellipsis: true,
                  render: (v: string) => v || '—' },
              ]} />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="Lots (earliest expiry first — FEFO order)">
            <Table<Lot> size="small" rowKey="Lot_Number" dataSource={d.lots}
              pagination={false} scroll={{ x: 'max-content' }}
              locale={{ emptyText: 'No lots recorded for this material' }}
              columns={[
                { title: 'Lot', dataIndex: 'Lot_Number', ellipsis: true },
                { title: 'Expiry', dataIndex: 'Expiry_Date', width: 110,
                  render: (v: string | null) => v || '—' },
                { title: 'Remaining', dataIndex: 'Remaining_Qty', align: 'right', width: 110,
                  render: (v: number) => nf(v) },
                { title: 'Status', dataIndex: 'Status', width: 100,
                  render: (v: string | null) => <Tag>{v || 'open'}</Tag> },
              ]} />
          </Card>
        </Col>
      </Row>

      {d.by_site.length > 0 && (
        <Card size="small" style={{ marginTop: 16 }} title="Stock by site">
          <div style={{ width: '100%', height: Math.max(120, d.by_site.length * 38 + 40) }}>
            <ResponsiveContainer>
              <BarChart data={d.by_site} layout="vertical"
                margin={{ top: 4, right: 16, left: 8, bottom: 0 }}>
                <CartesianGrid strokeOpacity={0.15} horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 11 }} />
                <YAxis type="category" dataKey="site" width={110} tick={{ fontSize: 11 }} />
                <Tooltip cursor={{ fillOpacity: 0.06 }} formatter={(v) => nf(Number(v))} />
                <Bar dataKey="stock" name="Stock" fill={C_RECEIVED}
                  radius={[0, 3, 3, 0]} maxBarSize={22} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}

      <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 4 }} style={{ marginTop: 16 }}
        items={[
          { key: 'u', label: 'UOM', children: d.uom || '—' },
          { key: 'm', label: 'Minimum', children: d.minimum_qty ? `${nf(d.minimum_qty)} ${d.uom}` : '—' },
          { key: 'c', label: 'Unit cost', children: d.unit_cost ? `SAR ${nf(d.unit_cost)}` : '—' },
          { key: 'v', label: 'Stock value', children: d.stock_value ? `SAR ${nf(d.stock_value)}` : '—' },
        ]} />
    </>
  )
}
