/**
 * frontend/src/pages/QcHodPage.tsx — the Head of Qualities' portal.
 *
 * Cross-site oversight of Surface Shield material, and nothing else. Every
 * figure on this page is already filtered to the controlled category by the
 * API (services/qc_oversight.py filters in SQL, per query) — the page never
 * has to remember, which is the point: a tab that forgot would leak silently
 * and look exactly like a working screen.
 *
 * ⚠️ THE ROLE READS AND SENDS MESSAGES. It cannot approve an inspection,
 * decide a delivery note, move stock or raise a PR. The one control that
 * writes is Escalate — asking somebody who CAN act to act — and its target is
 * a specific site or warehouse, never a broadcast (operator ruling Q12): a
 * message aimed at everywhere is one nobody owns.
 */
import {
  AlertOutlined, ClockCircleOutlined, ExportOutlined, FileProtectOutlined,
  SendOutlined,
} from '@ant-design/icons'
import {
  Alert, App, Button, Card, Descriptions, Empty, Form, Input, InputNumber,
  Modal, Radio, Row, Select, Skeleton, Space, Statistic, Table, Tabs, Tag,
  Tooltip, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import KpiRow from '../components/KpiRow'
import { useState } from 'react'

import { api } from '../api/client'
import { useSites } from '../api/hooks'
import { status } from '../theme/tokens'

type Row = Record<string, unknown>

const errMsg = (e: unknown): string => {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Request failed'
}
const n0 = (v: unknown) => (v == null ? '—' : Math.round(Number(v)).toLocaleString())
const n3 = (v: unknown) => (v == null ? '—' : Number(v).toFixed(3))

function useQc<T>(path: string) {
  return useQuery({
    queryKey: [path],
    queryFn: async () => (await api.get<T>(path)).data,
  })
}

// ── the escalation form, shared by every tab that can raise one ─────────────
interface EscalateSeed {
  sap_code?: string
  material_code?: string
  lot_number?: string
  po_number?: string
  kind?: string
  site?: string
  warehouse?: string
  message?: string
}

function EscalateModal({ seed, onClose }: { seed: EscalateSeed | null; onClose: () => void }) {
  const { message: msg } = App.useApp()
  const qc = useQueryClient()
  const [form] = Form.useForm()
  const { data: sites } = useSites()
  const warehouses = useQc<{ items: Row[] }>('/warehouses?limit=200')
  const [axis, setAxis] = useState<'site' | 'warehouse'>(
    seed?.warehouse ? 'warehouse' : 'site')

  const send = useMutation({
    mutationFn: (b: Row) => api.post('/qc-hod/escalations', b).then((r) => r.data),
    onSuccess: () => {
      msg.success('Escalation sent and logged')
      for (const k of ['/qc-hod/escalations', '/qc-hod/overview']) {
        qc.invalidateQueries({ queryKey: [k] })
      }
      onClose()
    },
    onError: (e) => msg.error(errMsg(e)),
  })

  const submit = async () => {
    const v = await form.validateFields()
    send.mutate({
      target_role: v.target_role,
      kind: v.kind,
      message: v.message,
      // EXACTLY ONE. The radio makes that structural rather than a rule the
      // user has to remember — and the API refuses neither-or-both anyway.
      target_site: axis === 'site' ? v.place : null,
      target_warehouse: axis === 'warehouse' ? v.place : null,
      sap_code: seed?.sap_code ?? null,
      material_code: seed?.material_code ?? null,
      lot_number: seed?.lot_number ?? null,
      po_number: seed?.po_number ?? null,
    })
  }

  return (
    <Modal open={!!seed} title="Escalate" onCancel={onClose} onOk={submit}
      okText="Send" confirmLoading={send.isPending} destroyOnHidden>
      <Form form={form} layout="vertical"
        initialValues={{
          kind: seed?.kind ?? 'mtc_demand',
          target_role: 'qc',
          place: seed?.site ?? seed?.warehouse,
          message: seed?.message ?? '',
        }}>
        <Form.Item name="kind" label="What are you asking for"
          rules={[{ required: true }]}>
          <Select options={[
            { value: 'mtc_demand', label: 'Material Test Certificate' },
            { value: 'inspection_request', label: 'An inspection' },
            { value: 'transfer_suggestion', label: 'Move this stock elsewhere' },
          ]} />
        </Form.Item>
        <Form.Item name="target_role" label="Who should act"
          rules={[{ required: true }]}>
          <Select options={[
            { value: 'qc', label: 'Quality Control (site or warehouse)' },
            { value: 'warehouse_user', label: 'Warehouse' },
            { value: 'logistics', label: 'Logistics' },
          ]} />
        </Form.Item>
        <Form.Item label="Where" required
          tooltip="A specific site or warehouse. A message aimed at everywhere
            is one nobody owns, so a broadcast is not offered.">
          <Space.Compact style={{ width: '100%' }}>
            <Radio.Group value={axis} optionType="button" buttonStyle="solid"
              onChange={(e) => { setAxis(e.target.value); form.setFieldValue('place', undefined) }}
              options={[{ label: 'Site', value: 'site' },
                        { label: 'Warehouse', value: 'warehouse' }]} />
            <Form.Item name="place" noStyle rules={[{ required: true, message: 'pick one' }]}>
              <Select style={{ minWidth: 220 }} showSearch
                placeholder={axis === 'site' ? 'Which site' : 'Which warehouse'}
                options={axis === 'site'
                  ? (sites ?? []).map((s) => ({ value: s, label: s }))
                  : (warehouses.data?.items ?? []).map((w) => ({
                      value: String(w.Warehouse_ID),
                      label: `${w.Warehouse_ID} — ${w.Name ?? ''}`,
                    }))} />
            </Form.Item>
          </Space.Compact>
        </Form.Item>
        <Form.Item name="message" label="Message" rules={[{ required: true }]}>
          <Input.TextArea rows={4}
            placeholder="What is needed, and by when" />
        </Form.Item>
        {(seed?.sap_code || seed?.lot_number || seed?.po_number) && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Attached: {[seed.sap_code, seed.lot_number, seed.po_number]
              .filter(Boolean).join(' · ')}
          </Typography.Text>
        )}
      </Form>
    </Modal>
  )
}

// ── 1. Overview ────────────────────────────────────────────────────────────
function Overview({ onEscalate }: { onEscalate: (s: EscalateSeed) => void }) {
  const { data, isLoading } = useQc<Row>('/qc-hod/overview')
  if (isLoading) return <Skeleton active />
  if (!data) return <Empty />
  const th = data.thresholds as Row | undefined
  const uncertified = Number(data.uncertified_materials ?? 0)
  return (
    <>
      <KpiRow min={180} gap={12} style={{ marginBottom: 16 }}>
        <Card size="small">
          <Statistic title="Uncertified materials" value={n0(uncertified)}
            valueStyle={{ color: uncertified > 0 ? status.critical : status.ok }} />
        </Card>
        <Card size="small">
          <Statistic title="Sites affected" value={n0(data.sites_affected)} />
        </Card>
        <Card size="small">
          <Statistic title="Warehouses affected"
            value={n0(data.warehouses_affected)} />
        </Card>
        <Card size="small">
          <Tooltip title={`No movement for ${th?.stagnant_days ?? 90} days`}>
            <Statistic title="Stagnant lots" value={n0(data.stagnant_lots)} />
          </Tooltip>
        </Card>
        <Card size="small">
          <Tooltip title={`Within ${th?.expiry_warn_days ?? 60} days of expiry`}>
            <Statistic title="Expiring soon" value={n0(data.expiring_lots)}
              valueStyle={{ color: Number(data.expiring_lots ?? 0) > 0 ? status.low : undefined }} />
          </Tooltip>
        </Card>
        <Card size="small">
          <Statistic title="Expired" value={n0(data.expired_lots)}
            valueStyle={{ color: Number(data.expired_lots ?? 0) > 0 ? status.critical : status.ok }} />
        </Card>
      </KpiRow>

      <Descriptions size="small" bordered column={{ xs: 1, md: 3 }}
        style={{ marginBottom: 16 }}>
        <Descriptions.Item label="Category in scope">
          <Tag color="gold">{String(data.category ?? '—')}</Tag>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            every figure on this page is filtered to it
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="Controlled lots held">
          {n0(data.controlled_lots_held)}
        </Descriptions.Item>
        <Descriptions.Item label="Open escalations">
          {n0(data.open_escalations)}
        </Descriptions.Item>
      </Descriptions>

      {uncertified > 0 && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          message={`${uncertified} material(s) are on hand with no certificate`}
          description={
            <>
              <div style={{ marginBottom: 8 }}>
                At: {(data.places_affected as string[] ?? []).join(', ') || '—'}.
                None of it can be issued to the field until the certificates are
                uploaded.
              </div>
              <Button size="small" icon={<SendOutlined />}
                onClick={() => onEscalate({ kind: 'mtc_demand' })}>
                Chase a certificate
              </Button>
            </>
          } />
      )}
    </>
  )
}

// ── 2. Surface Shield POs ──────────────────────────────────────────────────
function SsPos({ onEscalate }: { onEscalate: (s: EscalateSeed) => void }) {
  const { data, isFetching } = useQc<{ items: Row[] }>('/qc-hod/surface-shield/pos')
  const cols: ColumnsType<Row> = [
    { title: 'PO', dataIndex: 'PO_Number', width: 150 },
    { title: 'Site', dataIndex: 'Site_ID', width: 110 },
    { title: 'Vendor', dataIndex: 'Vendor_Name', width: 170, ellipsis: true,
      render: (v) => v ?? '—' },
    { title: 'Material', dataIndex: 'Material_Code', width: 140 },
    { title: 'Description', dataIndex: 'Description', ellipsis: true },
    { title: 'Qty', dataIndex: 'Qty', width: 90, align: 'right', render: n3 },
    { title: 'Expected', dataIndex: 'Expected_Delivery', width: 120,
      render: (v) => v ?? '—' },
    { title: 'MTC', dataIndex: 'Has_MTC', width: 110,
      render: (v: boolean) => v
        ? <Tag color="green">on file</Tag>
        : <Tag color="red">missing</Tag> },
    { title: '', key: 'act', width: 110,
      render: (_: unknown, r: Row) => r.Has_MTC ? null : (
        <Button size="small" icon={<SendOutlined />}
          onClick={() => onEscalate({
            kind: 'mtc_demand', po_number: String(r.PO_Number),
            material_code: String(r.Material_Code ?? ''),
            sap_code: String(r.SAP_Code ?? ''),
            site: String(r.Site_ID ?? ''),
            message: `PO ${r.PO_Number} (${r.Material_Code}) has no Material `
              + 'Test Certificate on file. Please obtain it from the supplier.',
          })}>Chase</Button>
      ) },
  ]
  return (
    <Table size="small" loading={isFetching} columns={cols}
      dataSource={data?.items ?? []}
      rowKey={(r) => `${r.PO_Number}-${r.po_item_id}`}
      scroll={{ x: 'max-content' }}
      pagination={{ pageSize: 25, showTotal: (t) => `${t} Surface Shield line(s)` }} />
  )
}

// ── 3. MTC register ────────────────────────────────────────────────────────
function MtcRegister() {
  const { data, isFetching } = useQc<{ items: Row[] }>('/qc-hod/mtc')
  const cols: ColumnsType<Row> = [
    { title: 'MTC', dataIndex: 'mtc_number', width: 150, render: (v) => v ?? '—' },
    { title: 'SAP', dataIndex: 'SAP_Code', width: 110 },
    { title: 'Material', dataIndex: 'Equipment_Description', ellipsis: true,
      render: (v) => v ?? '—' },
    { title: 'Lot', dataIndex: 'Lot_Number', width: 120, render: (v) => v ?? '—' },
    { title: 'Site', dataIndex: 'Site_ID', width: 100, render: (v) => v ?? '—' },
    { title: 'Warehouse', dataIndex: 'Warehouse_ID', width: 110,
      render: (v) => v ?? '—' },
    { title: 'Qty', dataIndex: 'Quantity', width: 90, align: 'right', render: n3 },
    { title: 'Status', dataIndex: 'status', width: 110,
      render: (v: string) => <Tag>{v ?? '—'}</Tag> },
    { title: 'Uploaded', dataIndex: 'submitted_at', width: 160,
      render: (v) => (v ? String(v).slice(0, 16).replace('T', ' ') : '—') },
    { title: 'By', dataIndex: 'submitted_by', width: 120 },
  ]
  return (
    <Table size="small" loading={isFetching} columns={cols}
      dataSource={data?.items ?? []} rowKey={(r) => String(r.id)}
      scroll={{ x: 'max-content' }}
      pagination={{ pageSize: 25, showTotal: (t) => `${t} certificate(s)` }} />
  )
}

// ── 4. Where it is being used ──────────────────────────────────────────────
function Usage() {
  const { data, isFetching } = useQc<{ items: Row[] }>('/qc-hod/usage')
  const cols: ColumnsType<Row> = [
    { title: 'Site', dataIndex: 'Site_ID', width: 130 },
    { title: 'SAP', dataIndex: 'SAP_Code', width: 110 },
    { title: 'Material', dataIndex: 'Equipment_Description', ellipsis: true },
    { title: 'Consumed', dataIndex: 'consumed_qty', width: 120, align: 'right',
      render: n3 },
    { title: 'UOM', dataIndex: 'UOM', width: 80 },
    { title: 'Draws', dataIndex: 'draws', width: 80, align: 'right' },
    { title: 'Last used', dataIndex: 'last_used', width: 120,
      render: (v) => v ?? '—' },
  ]
  return (
    <Table size="small" loading={isFetching} columns={cols}
      dataSource={data?.items ?? []}
      rowKey={(r) => `${r.Site_ID}-${r.SAP_Code}`}
      scroll={{ x: 'max-content' }}
      pagination={{ pageSize: 25, showTotal: (t) => `${t} site/material pair(s)` }} />
  )
}

// ── 5. Stagnation and expiry ───────────────────────────────────────────────
function Stagnation({ onEscalate }: { onEscalate: (s: EscalateSeed) => void }) {
  const { data, isFetching } = useQc<Row>('/qc-hod/stagnation')
  const th = data?.thresholds as Row | undefined

  const cols = (kind: string): ColumnsType<Row> => [
    { title: 'Lot', dataIndex: 'Lot_Number', width: 130 },
    { title: 'SAP', dataIndex: 'SAP_Code', width: 110 },
    { title: 'Material', dataIndex: 'Equipment_Description', ellipsis: true },
    { title: 'Site', dataIndex: 'Site_ID', width: 110 },
    { title: 'Remaining', dataIndex: 'remaining_qty', width: 110, align: 'right',
      render: n3 },
    { title: 'Idle', dataIndex: 'idle_days', width: 150,
      render: (v: number, r: Row) => (
        // "Received and never touched" and "used until March then abandoned"
        // are the same number of idle days and completely different problems.
        <Tooltip title={String(r.basis)}>
          <span>{v == null ? '—' : `${v} d`}{' '}
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              {r.basis === 'never used since receipt' ? '(never used)' : ''}
            </Typography.Text>
          </span>
        </Tooltip>) },
    { title: 'Expiry', dataIndex: 'Expiry_Date', width: 120,
      render: (v: string, r: Row) => {
        const d = r.days_to_expiry as number | null
        if (!v) return '—'
        const colour = d == null ? undefined : d < 0 ? 'red' : d <= 30 ? 'orange' : undefined
        return <Tag color={colour}>{v}{d != null && ` (${d} d)`}</Tag>
      } },
    { title: 'Could move to', dataIndex: 'could_move_to', width: 200,
      render: (v: Row[]) => (v ?? []).length
        ? (v ?? []).map((c) => String(c.Site_ID)).join(', ')
        : <Typography.Text type="secondary">nowhere is drawing it</Typography.Text> },
    { title: '', key: 'act', width: 110,
      render: (_: unknown, r: Row) => (
        <Button size="small" icon={<ExportOutlined />}
          onClick={() => onEscalate({
            kind: kind === 'expired' ? 'inspection_request' : 'transfer_suggestion',
            sap_code: String(r.SAP_Code ?? ''),
            lot_number: String(r.Lot_Number ?? ''),
            site: String(r.Site_ID ?? ''),
            message: kind === 'expired'
              ? `Lot ${r.Lot_Number} of ${r.SAP_Code} at ${r.Site_ID} has `
                + 'passed its expiry date. Please quarantine and inspect it.'
              : `Lot ${r.Lot_Number} of ${r.SAP_Code} has sat at ${r.Site_ID} `
                + `for ${r.idle_days} days. Consider moving it to a site that `
                + 'is drawing this material before it expires.',
          })}>Escalate</Button>
      ) },
  ]

  if (isFetching && !data) return <Skeleton active />
  return (
    <>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message={`Stagnant after ${th?.stagnant_days ?? 90} days without `
          + `movement; expiry warning at ${th?.expiry_warn_days ?? 60} days`}
        description="Change these under Settings — they are your policy, not a
          system constant." />
      <Tabs size="small" items={[
        { key: 'expired', label: `⛔ Expired (${(data?.expired as Row[] ?? []).length})`,
          children: <Table size="small" columns={cols('expired')}
            dataSource={(data?.expired as Row[]) ?? []}
            rowKey={(r) => `${r.Lot_Number}-${r.SAP_Code}-${r.Site_ID}`}
            scroll={{ x: 'max-content' }} pagination={{ pageSize: 15 }} /> },
        { key: 'expiring', label: `⏳ Expiring (${(data?.expiring as Row[] ?? []).length})`,
          children: <Table size="small" columns={cols('expiring')}
            dataSource={(data?.expiring as Row[]) ?? []}
            rowKey={(r) => `${r.Lot_Number}-${r.SAP_Code}-${r.Site_ID}`}
            scroll={{ x: 'max-content' }} pagination={{ pageSize: 15 }} /> },
        { key: 'stagnant', label: `🐌 Stagnant (${(data?.stagnant as Row[] ?? []).length})`,
          children: <Table size="small" columns={cols('stagnant')}
            dataSource={(data?.stagnant as Row[]) ?? []}
            rowKey={(r) => `${r.Lot_Number}-${r.SAP_Code}-${r.Site_ID}`}
            scroll={{ x: 'max-content' }} pagination={{ pageSize: 15 }} /> },
      ]} />
    </>
  )
}

// ── 6. Escalations ─────────────────────────────────────────────────────────
function Escalations() {
  const { message: msg } = App.useApp()
  const qc = useQueryClient()
  const { data, isFetching } = useQc<{ items: Row[] }>('/qc-hod/escalations')
  const [resolving, setResolving] = useState<Row | null>(null)
  const [note, setNote] = useState('')

  const resolve = useMutation({
    mutationFn: ({ id, n }: { id: number; n: string }) =>
      api.post(`/qc-hod/escalations/${id}/resolve`, { note: n }).then((r) => r.data),
    onSuccess: () => {
      msg.success('Closed')
      for (const k of ['/qc-hod/escalations', '/qc-hod/overview']) {
        qc.invalidateQueries({ queryKey: [k] })
      }
      setResolving(null); setNote('')
    },
    onError: (e) => msg.error(errMsg(e)),
  })

  const cols: ColumnsType<Row> = [
    { title: '#', dataIndex: 'id', width: 70 },
    { title: 'Raised', dataIndex: 'created_at', width: 160,
      render: (v) => (v ? String(v).slice(0, 16).replace('T', ' ') : '—') },
    { title: 'Kind', dataIndex: 'kind', width: 160,
      render: (v: string) => <Tag>{v.replace(/_/g, ' ')}</Tag> },
    { title: 'To', key: 'to', width: 200,
      render: (_: unknown, r: Row) =>
        `${r.target_role} @ ${r.target_site ?? r.target_warehouse ?? '—'}` },
    { title: 'About', key: 'about', width: 170,
      render: (_: unknown, r: Row) => [r.SAP_Code, r.Lot_Number, r.PO_Number]
        .filter(Boolean).join(' · ') || '—' },
    { title: 'Message', dataIndex: 'message', ellipsis: true },
    { title: 'Status', dataIndex: 'status', width: 110,
      render: (v: string) => <Tag color={v === 'open' ? 'red' : 'green'}>{v}</Tag> },
    { title: '', key: 'act', width: 100,
      render: (_: unknown, r: Row) => r.status === 'open' ? (
        <Button size="small" onClick={() => setResolving(r)}>Close</Button>
      ) : (
        <Tooltip title={String(r.resolution_note ?? '')}>
          <Typography.Text type="secondary">{String(r.resolved_by ?? '')}</Typography.Text>
        </Tooltip>
      ) },
  ]
  return (
    <>
      <Table size="small" loading={isFetching} columns={cols}
        dataSource={data?.items ?? []} rowKey={(r) => String(r.id)}
        scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 25, showTotal: (t) => `${t} escalation(s)` }} />
      <Modal open={!!resolving} title={`Close escalation #${resolving?.id ?? ''}`}
        onCancel={() => setResolving(null)}
        onOk={() => resolve.mutate({ id: Number(resolving!.id), n: note })}
        confirmLoading={resolve.isPending} okText="Close">
        <Typography.Paragraph type="secondary">
          Say what happened. The note is the record of what actually fixed it,
          and it is not overwritten later.
        </Typography.Paragraph>
        <Input.TextArea rows={3} value={note} onChange={(e) => setNote(e.target.value)}
          placeholder="e.g. certificate uploaded by the warehouse on 23/08" />
      </Modal>
    </>
  )
}

// ── 7. Settings ────────────────────────────────────────────────────────────
function Settings() {
  const { message: msg } = App.useApp()
  const qc = useQueryClient()
  const { data, isLoading } = useQc<Row>('/qc-hod/settings')
  const [form] = Form.useForm()

  const save = useMutation({
    mutationFn: (b: Row) => api.put('/qc-hod/settings', b).then((r) => r.data),
    onSuccess: () => {
      msg.success('Thresholds saved')
      for (const k of ['/qc-hod/settings', '/qc-hod/stagnation', '/qc-hod/overview']) {
        qc.invalidateQueries({ queryKey: [k] })
      }
    },
    onError: (e) => msg.error(errMsg(e)),
  })

  if (isLoading) return <Skeleton active />
  return (
    <Card size="small" style={{ maxWidth: 620 }}>
      <Form form={form} layout="vertical"
        initialValues={{
          stagnant_days: data?.stagnant_days ?? 90,
          expiry_warn_days: data?.expiry_warn_days ?? 60,
        }}
        onFinish={(v) => save.mutate(v)}>
        <Form.Item name="stagnant_days" label="Stagnant after (days without movement)"
          rules={[{ required: true }]}
          tooltip="Measured from the last draw, or from the receipt date for a
            lot that has never been touched.">
          <InputNumber min={1} max={3650} style={{ width: 160 }} />
        </Form.Item>
        <Form.Item name="expiry_warn_days" label="Warn this many days before expiry"
          rules={[{ required: true }]}>
          <InputNumber min={1} max={3650} style={{ width: 160 }} />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={save.isPending}>
          Save
        </Button>
      </Form>
      <Typography.Paragraph type="secondary"
        style={{ marginTop: 12, marginBottom: 0, fontSize: 12 }}>
        Applies to <b>{String(data?.Category ?? '—')}</b>. These are your policy,
        not a system constant — changing them must not need a release.
        {data?.source === 'default (no rule row for this category)' && (
          <> Currently running on built-in defaults; saving writes a real rule.</>
        )}
      </Typography.Paragraph>
    </Card>
  )
}

export default function QcHodPage() {
  const [seed, setSeed] = useState<EscalateSeed | null>(null)
  const onEscalate = (s: EscalateSeed) => setSeed(s)

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        <FileProtectOutlined /> Quality Oversight
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        Surface Shield material across every site — what is uncertified, where
        it is being used, and what is sitting still. You can read everything and
        change nothing; the one action here is asking somebody who can act, to
        act.
      </Typography.Paragraph>

      <Tabs
        defaultActiveKey="overview"
        items={[
          { key: 'overview', label: <><AlertOutlined /> Overview</>,
            children: <Overview onEscalate={onEscalate} /> },
          { key: 'pos', label: '📦 Surface Shield POs',
            children: <SsPos onEscalate={onEscalate} /> },
          { key: 'mtc', label: '📄 MTC Register', children: <MtcRegister /> },
          { key: 'usage', label: '📍 Where It Is Used', children: <Usage /> },
          { key: 'stagnation',
            label: <><ClockCircleOutlined /> Stagnation & Expiry</>,
            children: <Stagnation onEscalate={onEscalate} /> },
          { key: 'escalations', label: '📣 Escalations',
            children: <Escalations /> },
          { key: 'settings', label: '⚙️ Settings', children: <Settings /> },
        ]} />

      <EscalateModal seed={seed} onClose={() => setSeed(null)} />
    </div>
  )
}
