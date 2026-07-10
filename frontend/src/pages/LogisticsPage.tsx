import { useState } from 'react'
import {
  App, Button, Card, Col, DatePicker, Descriptions, Form, Input, InputNumber, Modal,
  Popconfirm, Row as ARow, Select, Space, Table, Tabs, Tag, Typography, Upload,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { InboxOutlined, MinusCircleOutlined, PlusOutlined } from '@ant-design/icons'
import {
  useAssignPo, useCloseVendorReturn, useCreate, useCreateManualPo, useCreatePo,
  useDecideReschedule, useForceClose, useForceClosures, useList, useLogisticsPos,
  useLogisticsPrs, usePoItems, useRaiseVendorReturn, useReschedules, useSites,
  useUndoForceClose, useVendorReturns,
} from '../api/hooks'
import { api } from '../api/client'
import type { Row } from '../api/client'
import DnApprovalQueue from '../components/DnApprovalQueue'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

// ---- Incoming PRs → Create PO ----------------------------------------------
function IncomingPRs() {
  const { message } = App.useApp()
  const { data: sites } = useSites()
  const [siteId, setSiteId] = useState<string | undefined>(undefined)
  const { data: rows, isFetching } = useLogisticsPrs(siteId)
  const createPo = useCreatePo()
  const [pr, setPr] = useState<Row | null>(null)
  const [form] = Form.useForm<{ po_number: string; vendor_name?: string; expected_delivery?: Dayjs }>()

  const submit = async () => {
    const v = await form.validateFields()
    try {
      const res = await createPo.mutateAsync({
        pr_number: String(pr!.PR_Number),
        site_id: String(pr!.Site_ID),
        po_number: v.po_number,
        vendor_name: v.vendor_name || null,
        expected_delivery: v.expected_delivery ? v.expected_delivery.format('YYYY-MM-DD') : null,
      })
      message.success(`PO ${res.po_number} created (${res.lines} lines)`)
      setPr(null)
      form.resetFields()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = [
    { title: 'PR Number', dataIndex: 'PR_Number', key: 'PR_Number' },
    { title: 'Site', dataIndex: 'Site_ID', key: 'Site_ID' },
    { title: 'Lines', dataIndex: 'line_count', key: 'line_count', align: 'right' },
    { title: 'Total Qty', dataIndex: 'total_qty', key: 'total_qty', align: 'right', render: (v) => Number(v) },
    {
      title: 'Action',
      key: '__act',
      width: 240,
      render: (_: unknown, r: Row) => (
        <Space>
          <Button size="small" type="primary" onClick={() => { setPr(r); form.resetFields() }}>
            Create PO
          </Button>
          <ForceCloseButton targetType="pr" targetRef={String(r.PR_Number)} />
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Space style={{ marginBottom: 12 }}>
        <Select
          allowClear placeholder="All sites" style={{ width: 180 }}
          value={siteId} onChange={setSiteId}
          options={(sites ?? []).map((s) => ({ value: s, label: s }))}
        />
      </Space>
      <Table
        size="small" loading={isFetching} columns={columns} dataSource={rows ?? []}
        rowKey={(r) => `${r.PR_Number}-${r.Site_ID}`}
        pagination={{ pageSize: 20, showTotal: (t) => `${t} in queue` }}
      />
      <Modal
        open={!!pr}
        title={`Create PO from PR ${pr?.PR_Number ?? ''}`}
        onCancel={() => setPr(null)}
        onOk={submit}
        confirmLoading={createPo.isPending}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="po_number" label="PO Number" rules={[{ required: true }]}>
            <Input placeholder="e.g. PO-2026-0001" />
          </Form.Item>
          <Form.Item name="vendor_name" label="Vendor">
            <Input placeholder="Vendor name" />
          </Form.Item>
          <Form.Item name="expected_delivery" label="Expected delivery">
            <DatePicker style={{ width: '100%' }} format="YYYY-MM-DD" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

// ---- 📄 Import PO PDF (Phase AI-2 preview-confirm) ---------------------------
interface PoPreview {
  ok: boolean
  message: string
  header: Record<string, string | number | undefined>
  items: Row[]
  shipment_schedule: { shipment_no: string; material_group: string; target_date: string }[]
}

function ImportPoPdf() {
  const { message } = App.useApp()
  const { data: sites } = useSites()
  const [preview, setPreview] = useState<PoPreview | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [form] = Form.useForm<{ pr_number: string; site_id: string; po_number: string; vendor_code?: string; vendor_name?: string }>()
  const createPo = useCreatePo()

  const openConfirm = () => {
    const h = preview?.header ?? {}
    form.setFieldsValue({
      pr_number: String(h.PR_Number ?? ''),
      po_number: String(h.PO_Number ?? ''),
      vendor_code: h.Vendor_Code ? String(h.Vendor_Code) : undefined,
      vendor_name: h.Vendor_Name ? String(h.Vendor_Name) : undefined,
    })
    setConfirmOpen(true)
  }

  const submit = async () => {
    const v = await form.validateFields()
    try {
      const res = await createPo.mutateAsync({
        pr_number: v.pr_number, site_id: v.site_id, po_number: v.po_number,
        vendor_code: v.vendor_code || null, vendor_name: v.vendor_name || null,
      })
      message.success(`PO ${res.po_number} created (${res.lines} lines from the submitted PR)`)
      setConfirmOpen(false)
      setPreview(null)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const h = preview?.header ?? {}
  return (
    <Card style={{ maxWidth: 980 }}>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        Upload a vendor PO PDF — header, line items and delivery schedule are extracted
        for review. On confirm, the PO is created from its <b>submitted PR</b> through the
        normal audited path (PO lines derive from the PR — simplified chain); the PDF's
        items below are for reconciliation against the PR.
      </Typography.Paragraph>
      <Upload.Dragger accept=".pdf" maxCount={1} showUploadList={false}
        customRequest={async ({ file, onSuccess, onError }) => {
          const fd = new FormData()
          fd.append('file', file as Blob)
          try {
            const r = await api.post<PoPreview>('/ai/extract/po', fd)
            setPreview(r.data)
            if (r.data.ok) message.success(r.data.message)
            else message.warning(r.data.message)
            onSuccess?.(r.data)
          } catch (e) {
            message.error(errMsg(e))
            onError?.(e as Error)
          }
        }}>
        <p className="ant-upload-drag-icon"><InboxOutlined /></p>
        <p className="ant-upload-text">Drop the PO PDF here</p>
        <p className="ant-upload-hint">All three GI layouts supported (7-col, inline, split-line)</p>
      </Upload.Dragger>

      {preview && (
        <div style={{ marginTop: 16 }}>
          <Descriptions size="small" bordered column={3}
            items={[
              { key: '1', label: 'PO Number', children: h.PO_Number ?? '—' },
              { key: '2', label: 'PO Date', children: h.PO_Date ?? '—' },
              { key: '3', label: 'PR Number', children: h.PR_Number ?? '—' },
              { key: '4', label: 'Vendor', children: `${h.Vendor_Code ?? '—'} · ${h.Vendor_Name ?? '—'}` },
              { key: '5', label: 'Payment terms', children: h.Payment_Terms ?? '—' },
              { key: '6', label: 'Total', children: h.Total_Amount ?? '—' },
            ]} />
          <Typography.Title level={5} style={{ marginTop: 16 }}>
            Extracted items ({preview.items.length}) — reconcile against the PR
          </Typography.Title>
          <Table size="small" dataSource={preview.items} rowKey={(r) => String(r.line_no)}
            pagination={false} scroll={{ x: 'max-content' }}
            columns={[
              { title: '#', dataIndex: 'line_no', width: 50 },
              { title: 'Code', dataIndex: 'Material_Code', width: 120 },
              { title: 'Description', dataIndex: 'Description', ellipsis: true },
              { title: 'Qty', dataIndex: 'Qty', align: 'right', width: 90 },
              { title: 'UOM', dataIndex: 'UOM', width: 70 },
              { title: 'Unit', dataIndex: 'Unit_Price', align: 'right', width: 90 },
              { title: 'Total', dataIndex: 'Total_Price', align: 'right', width: 110 },
              { title: 'Family', dataIndex: 'rl_bl_family', width: 80,
                render: (v: string | null) => (v ? <Tag>{v}</Tag> : '—') },
            ] as ColumnsType<Row>} />
          {preview.shipment_schedule.length > 0 && (
            <>
              <Typography.Title level={5} style={{ marginTop: 16 }}>Delivery schedule</Typography.Title>
              <Table size="small" dataSource={preview.shipment_schedule}
                rowKey={(r) => r.shipment_no} pagination={false}
                columns={[
                  { title: 'Shipment', dataIndex: 'shipment_no' },
                  { title: 'Material group', dataIndex: 'material_group' },
                  { title: 'Target date', dataIndex: 'target_date' },
                ]} />
            </>
          )}
          <Space style={{ marginTop: 16 }}>
            <Button type="primary" onClick={openConfirm} disabled={!preview.ok}>
              Create PO with these details
            </Button>
            <Button onClick={() => setPreview(null)}>Discard</Button>
          </Space>
        </div>
      )}

      <Modal open={confirmOpen} title="Create PO (from its submitted PR)"
        onCancel={() => setConfirmOpen(false)} onOk={submit}
        confirmLoading={createPo.isPending}>
        <Form form={form} layout="vertical">
          <Form.Item name="pr_number" label="PR Number" rules={[{ required: true }]}
            extra="The PR must already be submitted to Logistics — PO lines come from it.">
            <Input />
          </Form.Item>
          <Form.Item name="site_id" label="Site" rules={[{ required: true }]}>
            <Select options={(sites ?? []).map((s) => ({ value: s, label: s }))} />
          </Form.Item>
          <Form.Item name="po_number" label="PO Number" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="vendor_code" label="Vendor code"><Input /></Form.Item>
          <Form.Item name="vendor_name" label="Vendor name"><Input /></Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}

// ---- Force-close (H8): reusable reason modal for PR / PO / line ------------
function ForceCloseButton({ targetType, targetRef, disabled }: {
  targetType: 'pr' | 'po' | 'line'; targetRef: string; disabled?: boolean
}) {
  const { message } = App.useApp()
  const fc = useForceClose()
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const submit = async () => {
    if (!reason.trim()) return
    try {
      await fc.mutateAsync({ target_type: targetType, target_ref: targetRef, reason: reason.trim() })
      message.success(`Force-closed ${targetType} ${targetRef} — undo available for 24h`)
      setOpen(false); setReason('')
    } catch (e) { message.error(errMsg(e)) }
  }
  return (
    <>
      <Button size="small" danger disabled={disabled} onClick={() => { setOpen(true); setReason('') }}>
        Force-close
      </Button>
      <Modal open={open} title={`Force-close ${targetType} ${targetRef}`} onOk={submit}
        onCancel={() => setOpen(false)} okText="Force-close"
        okButtonProps={{ danger: true, disabled: !reason.trim() }}
        confirmLoading={fc.isPending} destroyOnHidden>
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          A reason is required and recorded on the audit trail. You can undo this within 24 hours.
        </Typography.Paragraph>
        <Input.TextArea rows={3} placeholder="Reason for force-closing"
          value={reason} onChange={(e) => setReason(e.target.value)} />
      </Modal>
    </>
  )
}

// ---- Purchase Orders (+ items, assign) -------------------------------------
function PoItems({ po }: { po: string }) {
  const { data: items, isFetching } = usePoItems(po)
  const columns: ColumnsType<Row> = [
    { title: 'Line', dataIndex: 'line_no', key: 'line_no', width: 60 },
    { title: 'Material', dataIndex: 'Material_Code', key: 'Material_Code' },
    { title: 'Description', dataIndex: 'Description', key: 'Description', ellipsis: true },
    { title: 'Qty', dataIndex: 'Qty', key: 'Qty', align: 'right', render: (v) => Number(v) },
    { title: 'UOM', dataIndex: 'UOM', key: 'UOM' },
    { title: 'Family', dataIndex: 'rl_bl_family', key: 'rl_bl_family', render: (v) => v ?? '—' },
    { title: 'Status', dataIndex: 'line_status', key: 'line_status' },
    {
      title: '', key: '__fc', width: 120, align: 'right',
      render: (_: unknown, r: Row) => (
        <ForceCloseButton targetType="line" targetRef={String(r.id)}
          disabled={['closed', 'force_closed'].includes(String(r.line_status))} />
      ),
    },
  ]
  return (
    <Table size="small" loading={isFetching} columns={columns} dataSource={items ?? []}
      rowKey={(r) => String(r.id)} pagination={false} />
  )
}

// ---- Force-closures tab (H8): log + 24h undo -------------------------------
function ForceClosures() {
  const { message } = App.useApp()
  const { data: rows, isFetching } = useForceClosures()
  const undo = useUndoForceClose()
  const doUndo = async (id: number) => {
    try { await undo.mutateAsync(id); message.success('Reverted') }
    catch (e) { message.error(errMsg(e)) }
  }
  const columns: ColumnsType<Row> = [
    { title: 'Type', dataIndex: 'target_type', width: 70 },
    { title: 'Ref', dataIndex: 'target_ref' },
    { title: 'Reason', dataIndex: 'reason', ellipsis: true },
    { title: 'By', dataIndex: 'closed_by', width: 110 },
    { title: 'When', dataIndex: 'closed_at', width: 150, render: (v) => (v ? String(v).slice(0, 16) : '—') },
    { title: 'State', key: 'state', width: 90,
      render: (_: unknown, r: Row) => (r.reverted_at ? <Tag>reverted</Tag> : <Tag color="red">closed</Tag>) },
    {
      title: 'Action', key: '__act', width: 140,
      render: (_: unknown, r: Row) => {
        if (r.reverted_at) return <Typography.Text type="secondary">{r.reverted_by ? String(r.reverted_by) : '—'}</Typography.Text>
        const ageOk = typeof r.age_hours === 'number' && (r.age_hours as number) <= 24
        return (
          <Popconfirm title={`Undo force-close of ${r.target_type} ${r.target_ref}?`}
            onConfirm={() => doUndo(Number(r.id))} disabled={!ageOk}>
            <Button size="small" disabled={!ageOk}>{ageOk ? 'Undo' : 'Undo expired'}</Button>
          </Popconfirm>
        )
      },
    },
  ]
  return (
    <Table size="small" loading={isFetching} columns={columns} dataSource={rows ?? []}
      rowKey={(r) => String(r.id)} pagination={{ pageSize: 20, showTotal: (t) => `${t} closures` }} />
  )
}

// KPI hero (UAT Phase 3): the procurement pulse at a glance. Cards double as
// filters — click one to narrow the table, click again to clear.
const _PO_TERMINAL = ['delivered', 'closed', 'force_closed', 'cancelled']

function PoKpiHero({ rows, active, onPick }: {
  rows: Row[]
  active: string | null
  onPick: (k: string | null) => void
}) {
  const today = dayjs().format('YYYY-MM-DD')
  const open = rows.filter((r) => !_PO_TERMINAL.includes(String(r.status)))
  const overdue = open.filter((r) => r.Expected_Delivery && String(r.Expected_Delivery) < today)
  const partial = rows.filter((r) => String(r.status) === 'partially_delivered')
  const done = rows.filter((r) => _PO_TERMINAL.includes(String(r.status)))
  const kpis = [
    { key: 'open', label: 'Open POs', value: open.length, color: 'var(--gi-gold, #B8860B)' },
    { key: 'overdue', label: 'Overdue delivery', value: overdue.length, color: '#EF4444' },
    { key: 'partial', label: 'Partially delivered', value: partial.length, color: '#3B82F6' },
    { key: 'done', label: 'Delivered / closed', value: done.length, color: '#22C55E' },
  ]
  return (
    <ARow gutter={12} style={{ marginBottom: 16 }}>
      {kpis.map((k) => (
        <Col xs={12} md={6} key={k.key}>
          <Card size="small" hoverable onClick={() => onPick(active === k.key ? null : k.key)}
            style={active === k.key ? { borderColor: k.color, boxShadow: `0 0 0 1px ${k.color}` } : undefined}>
            <div style={{ fontSize: 11, letterSpacing: 0.4, textTransform: 'uppercase', opacity: 0.65 }}>
              {k.label}{active === k.key ? ' · filtering' : ''}
            </div>
            <div style={{ fontSize: 26, fontWeight: 700, color: k.color }}>{k.value}</div>
          </Card>
        </Col>
      ))}
    </ARow>
  )
}

function PurchaseOrders() {
  const { message } = App.useApp()
  const { data: rows, isFetching } = useLogisticsPos()
  const warehouses = useList('/warehouses', { limit: 200 })
  const assign = useAssignPo()
  const [po, setPo] = useState<Row | null>(null)
  const [kpi, setKpi] = useState<string | null>(null)
  const [form] = Form.useForm<{ warehouse_id: string; expected_delivery?: Dayjs; notes?: string }>()

  const today = dayjs().format('YYYY-MM-DD')
  const visibleRows = (rows ?? []).filter((r) => {
    if (kpi === 'open') return !_PO_TERMINAL.includes(String(r.status))
    if (kpi === 'overdue') {
      return !_PO_TERMINAL.includes(String(r.status))
        && r.Expected_Delivery && String(r.Expected_Delivery) < today
    }
    if (kpi === 'partial') return String(r.status) === 'partially_delivered'
    if (kpi === 'done') return _PO_TERMINAL.includes(String(r.status))
    return true
  })

  const submit = async () => {
    const v = await form.validateFields()
    try {
      await assign.mutateAsync({
        po: String(po!.PO_Number),
        body: {
          warehouse_id: v.warehouse_id,
          expected_delivery: v.expected_delivery ? v.expected_delivery.format('YYYY-MM-DD') : null,
          notes: v.notes || null,
        },
      })
      message.success(`PO ${po!.PO_Number} assigned to ${v.warehouse_id}`)
      setPo(null)
      form.resetFields()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = [
    { title: 'PO Number', dataIndex: 'PO_Number', key: 'PO_Number' },
    { title: 'PR', dataIndex: 'PR_Number', key: 'PR_Number' },
    { title: 'Site', dataIndex: 'Site_ID', key: 'Site_ID' },
    { title: 'Vendor', dataIndex: 'Vendor_Name', key: 'Vendor_Name', render: (v) => v ?? '—' },
    { title: 'Expected', dataIndex: 'Expected_Delivery', key: 'Expected_Delivery', render: (v) => v ?? '—' },
    { title: 'Status', dataIndex: 'status', key: 'status', render: (v: string) => <Tag>{v}</Tag> },
    {
      title: 'Action',
      key: '__act',
      width: 220,
      render: (_: unknown, r: Row) => (
        <Space>
          <Button
            size="small"
            disabled={['closed', 'force_closed', 'cancelled'].includes(String(r.status))}
            onClick={() => { setPo(r); form.resetFields() }}
          >
            Assign
          </Button>
          <ForceCloseButton targetType="po" targetRef={String(r.PO_Number)}
            disabled={['closed', 'force_closed', 'cancelled'].includes(String(r.status))} />
        </Space>
      ),
    },
  ]

  const warehouseOptions = (warehouses.data?.items ?? [])
    .filter((w: Row) => w.status === 'active')
    .map((w: Row) => ({ value: String(w.Warehouse_ID), label: `${w.Warehouse_ID} — ${w.Name ?? ''}` }))

  return (
    <div>
      <PoKpiHero rows={rows ?? []} active={kpi} onPick={setKpi} />
      <Table
        size="small" loading={isFetching} columns={columns} dataSource={visibleRows}
        rowKey={(r) => String(r.PO_Number)}
        expandable={{ expandedRowRender: (r) => <PoItems po={String(r.PO_Number)} /> }}
        pagination={{ pageSize: 20, showTotal: (t) => `${t} POs` }}
      />
      <Modal
        open={!!po}
        title={`Assign PO ${po?.PO_Number ?? ''} to warehouse`}
        onCancel={() => setPo(null)}
        onOk={submit}
        confirmLoading={assign.isPending}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="warehouse_id" label="Warehouse" rules={[{ required: true }]}>
            <Select placeholder="Select warehouse" options={warehouseOptions} />
          </Form.Item>
          <Form.Item name="expected_delivery" label="Expected delivery">
            <DatePicker style={{ width: '100%' }} format="YYYY-MM-DD" />
          </Form.Item>
          <Form.Item name="notes" label="Notes">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

// ---- Reschedules tab (H7): review + decide ---------------------------------
function Reschedules() {
  const { message } = App.useApp()
  const { data: rows, isFetching } = useReschedules()
  const decide = useDecideReschedule()
  const [rejectId, setRejectId] = useState<number | null>(null)
  const [notes, setNotes] = useState('')

  const approve = async (id: number) => {
    try {
      const r = await decide.mutateAsync({ id, action: 'approve' })
      message.success(`Approved — PO ${r.po_number} now due ${r.new_date}`)
    } catch (e) { message.error(errMsg(e)) }
  }
  const doReject = async () => {
    if (rejectId == null) return
    try {
      await decide.mutateAsync({ id: rejectId, action: 'reject', decision_notes: notes.trim() || undefined })
      message.success('Rejected — requester notified')
      setRejectId(null); setNotes('')
    } catch (e) { message.error(errMsg(e)) }
  }

  const columns: ColumnsType<Row> = [
    { title: 'PO', dataIndex: 'PO_Number' },
    { title: 'Current', dataIndex: 'current_date', render: (v) => v ?? '—' },
    { title: 'Requested', dataIndex: 'requested_date' },
    { title: 'By', key: 'by', render: (_: unknown, r: Row) => `${r.requested_by_role} · ${r.requested_by}` },
    { title: 'Reason', dataIndex: 'reason', ellipsis: true },
    { title: 'Status', dataIndex: 'status', render: (v: string) =>
      <Tag color={v === 'pending' ? 'gold' : v === 'approved' ? 'green' : 'red'}>{v}</Tag> },
    {
      title: 'Action', key: '__act', width: 190,
      render: (_: unknown, r: Row) => (r.status !== 'pending'
        ? <Typography.Text type="secondary">{r.decided_by ? String(r.decided_by) : '—'}</Typography.Text>
        : (
          <Space>
            <Popconfirm title={`Approve → set PO ${r.PO_Number} to ${r.requested_date}?`}
              onConfirm={() => approve(Number(r.id))}>
              <Button size="small" type="primary">Approve</Button>
            </Popconfirm>
            <Button size="small" danger onClick={() => { setRejectId(Number(r.id)); setNotes('') }}>Reject</Button>
          </Space>
        )),
    },
  ]

  return (
    <>
      <Table size="small" loading={isFetching} columns={columns} dataSource={rows ?? []}
        rowKey={(r) => String(r.id)} pagination={{ pageSize: 20, showTotal: (t) => `${t} requests` }} />
      <Modal open={rejectId != null} title="Reject reschedule request" onOk={doReject}
        onCancel={() => { setRejectId(null); setNotes('') }} okText="Reject"
        okButtonProps={{ danger: true }} confirmLoading={decide.isPending} destroyOnHidden>
        <Input.TextArea rows={3} placeholder="Reason (optional — sent to the requester)"
          value={notes} onChange={(e) => setNotes(e.target.value)} />
      </Modal>
    </>
  )
}

// ---- Vendor picker with inline-add + default terms -------------------------
function VendorPicker({ value, onPick }: {
  value?: string
  onPick: (v: { code: string; name: string; inco?: string; pay?: string }) => void
}) {
  const { message } = App.useApp()
  const vendors = useList('/vendors', { limit: 500 })
  const createVendor = useCreate('/vendors')
  const [addOpen, setAddOpen] = useState(false)
  const [form] = Form.useForm<{ Vendor_Code: string; Vendor_Name: string; Default_Inco_Terms?: string; Default_Payment_Terms?: string }>()

  const raws = (vendors.data?.items ?? []) as Row[]
  const options = raws.map((v) => ({ value: String(v.Vendor_Code), label: `${v.Vendor_Code} — ${v.Vendor_Name ?? ''}` }))
  const pick = (code: string) => {
    const v = raws.find((x) => String(x.Vendor_Code) === code)
    if (v) onPick({
      code: String(v.Vendor_Code), name: String(v.Vendor_Name ?? ''),
      inco: v.Default_Inco_Terms ? String(v.Default_Inco_Terms) : undefined,
      pay: v.Default_Payment_Terms ? String(v.Default_Payment_Terms) : undefined,
    })
  }
  const addVendor = async () => {
    const v = await form.validateFields()
    try {
      await createVendor.mutateAsync({ ...v, status: 'active' } as Row)
      message.success('Vendor added')
      await vendors.refetch()
      setAddOpen(false)
      form.resetFields()
      onPick({ code: v.Vendor_Code, name: v.Vendor_Name, inco: v.Default_Inco_Terms, pay: v.Default_Payment_Terms })
    } catch (e) { message.error(errMsg(e)) }
  }

  return (
    <>
      <Space.Compact style={{ width: '100%' }}>
        <Select showSearch style={{ width: '100%' }} placeholder="Select vendor"
          optionFilterProp="label" value={value} options={options}
          onChange={pick} loading={vendors.isFetching} />
        <Button onClick={() => setAddOpen(true)}>+ Add</Button>
      </Space.Compact>
      <Modal open={addOpen} title="Add vendor" onOk={addVendor}
        onCancel={() => setAddOpen(false)} confirmLoading={createVendor.isPending} destroyOnHidden>
        <Form form={form} layout="vertical">
          <Form.Item name="Vendor_Code" label="Vendor Code" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="Vendor_Name" label="Vendor Name" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="Default_Inco_Terms" label="Default Inco Terms"><Input placeholder="e.g. FOB" /></Form.Item>
          <Form.Item name="Default_Payment_Terms" label="Default Payment Terms"><Input placeholder="e.g. Net 30" /></Form.Item>
        </Form>
      </Modal>
    </>
  )
}

// ---- Manual PO creation (free-text lines/prices, unlisted PR) ---------------
interface ManualLine {
  Material_Code?: string; Description?: string; Qty: number; UOM?: string
  Unit_Price?: number; WBS_Number?: string; Network?: string; Plant?: string; PR_Number?: string
}
interface ManualPoForm {
  po_number: string; site_id?: string; pr_number?: string; vendor_code?: string
  vendor_name?: string; inco_terms?: string; payment_terms?: string
  expected_delivery?: Dayjs; lines: ManualLine[]
}

function CreatePoManual() {
  const { message } = App.useApp()
  const { data: sites } = useSites()
  const [form] = Form.useForm<ManualPoForm>()
  const create = useCreateManualPo()

  const onFinish = async (v: ManualPoForm) => {
    try {
      const res = await create.mutateAsync({
        po_number: v.po_number,
        site_id: v.site_id || null,
        pr_number: v.pr_number || null,
        vendor_code: v.vendor_code || null,
        vendor_name: v.vendor_name || null,
        inco_terms: v.inco_terms || null,
        payment_terms: v.payment_terms || null,
        expected_delivery: v.expected_delivery ? v.expected_delivery.format('YYYY-MM-DD') : null,
        lines: (v.lines ?? []).map((l) => ({
          Material_Code: l.Material_Code || null, Description: l.Description || null,
          Qty: l.Qty, UOM: l.UOM || null, Unit_Price: l.Unit_Price || 0,
          WBS_Number: l.WBS_Number || null, Network: l.Network || null,
          Plant: l.Plant || null, PR_Number: l.PR_Number || null,
        })),
      } as unknown as Row)
      message.success(`PO ${res.po_number} created — ${res.lines} line(s), total ${res.total}`)
      form.resetFields()
    } catch (e) { message.error(errMsg(e)) }
  }

  return (
    <Card style={{ maxWidth: 1000 }}>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        Create a PO by hand — free-text lines, custom prices, and an optional (even
        unlisted) PR reference. Pick a vendor to auto-fill its default Inco/Payment terms.
      </Typography.Paragraph>
      <Form<ManualPoForm> form={form} layout="vertical" initialValues={{ lines: [{} as ManualLine] }} onFinish={onFinish}>
        <ARow gutter={16}>
          <Col xs={24} md={6}><Form.Item name="po_number" label="PO Number" rules={[{ required: true }]}><Input placeholder="PO-2026-0001" /></Form.Item></Col>
          <Col xs={24} md={6}><Form.Item name="site_id" label="Site"><Select allowClear placeholder="Site" options={(sites ?? []).map((s) => ({ value: s, label: s }))} /></Form.Item></Col>
          <Col xs={24} md={6}><Form.Item name="pr_number" label="PR Number (optional / unlisted)"><Input placeholder="free text" /></Form.Item></Col>
          <Col xs={24} md={6}><Form.Item name="expected_delivery" label="Expected delivery"><DatePicker style={{ width: '100%' }} format="YYYY-MM-DD" /></Form.Item></Col>
        </ARow>
        <ARow gutter={16}>
          <Col xs={24} md={10}>
            <Form.Item name="vendor_code" label="Vendor">
              <VendorPicker onPick={(v) => form.setFieldsValue({
                vendor_code: v.code, vendor_name: v.name,
                inco_terms: form.getFieldValue('inco_terms') || v.inco,
                payment_terms: form.getFieldValue('payment_terms') || v.pay,
              })} />
            </Form.Item>
          </Col>
          <Col xs={24} md={7}><Form.Item name="inco_terms" label="Inco Terms"><Input placeholder="e.g. FOB" /></Form.Item></Col>
          <Col xs={24} md={7}><Form.Item name="payment_terms" label="Payment Terms"><Input placeholder="e.g. Net 30" /></Form.Item></Col>
        </ARow>
        <Form.Item name="vendor_name" hidden><Input /></Form.Item>

        <Typography.Text strong>Lines</Typography.Text>
        <Form.List name="lines">
          {(fields, { add, remove }) => (
            <>
              {fields.map((field) => (
                <Space key={field.key} align="baseline" wrap style={{ display: 'flex', marginTop: 8 }}>
                  <Form.Item name={[field.name, 'Material_Code']}><Input placeholder="Material code" style={{ width: 150 }} /></Form.Item>
                  <Form.Item name={[field.name, 'Description']}><Input placeholder="Description" style={{ width: 220 }} /></Form.Item>
                  <Form.Item name={[field.name, 'Qty']} rules={[{ required: true, message: 'Qty' }]}><InputNumber min={0.0001} placeholder="Qty" style={{ width: 90 }} /></Form.Item>
                  <Form.Item name={[field.name, 'UOM']}><Input placeholder="UOM" style={{ width: 80 }} /></Form.Item>
                  <Form.Item name={[field.name, 'Unit_Price']}><InputNumber min={0} placeholder="Unit SAR" style={{ width: 110 }} /></Form.Item>
                  <Form.Item name={[field.name, 'WBS_Number']}><Input placeholder="WBS" style={{ width: 110 }} /></Form.Item>
                  {fields.length > 1 && <MinusCircleOutlined onClick={() => remove(field.name)} />}
                </Space>
              ))}
              <Form.Item>
                <Button type="dashed" onClick={() => add()} icon={<PlusOutlined />} style={{ marginTop: 8 }}>Add line</Button>
              </Form.Item>
            </>
          )}
        </Form.List>
        <Button type="primary" htmlType="submit" loading={create.isPending}>Create PO</Button>
      </Form>
    </Card>
  )
}

// ---- Vendor Returns (deferred MED): raise-to-vendor + reopen PO line -------
function VendorReturns() {
  const { message } = App.useApp()
  const { data: pos } = useLogisticsPos()
  const { data: rows, isFetching } = useVendorReturns()
  const raise = useRaiseVendorReturn()
  const close = useCloseVendorReturn()
  const [form] = Form.useForm<{ po_number: string; po_item_id: number; qty: number; reason: string; expected_resupply?: Dayjs }>()
  const poWatch = Form.useWatch('po_number', form)
  const { data: lines } = usePoItems(poWatch ?? null)

  const onRaise = async () => {
    const v = await form.validateFields()
    try {
      const res = await raise.mutateAsync({
        po_number: v.po_number, po_item_id: v.po_item_id, qty: v.qty, reason: v.reason,
        expected_resupply: v.expected_resupply ? v.expected_resupply.format('YYYY-MM-DD') : null,
      } as unknown as Row)
      message.success(`Return raised${res.reopened_line ? ' — PO line reopened' : ''}`)
      form.resetFields(['po_item_id', 'qty', 'reason', 'expected_resupply'])
    } catch (e) { message.error(errMsg(e)) }
  }
  const doClose = async (id: number) => {
    try { await close.mutateAsync({ id }); message.success('Closed') }
    catch (e) { message.error(errMsg(e)) }
  }

  const poOptions = (pos ?? []).map((p: Row) => ({ value: String(p.PO_Number), label: `${p.PO_Number} (${p.status})` }))
  const lineOptions = (lines ?? []).map((l: Row) => ({
    value: Number(l.id),
    label: `#${l.line_no} ${l.Material_Code ?? ''} — delivered ${Number(l.Delivered_Qty ?? 0)}, returned ${Number(l.Returned_Qty ?? 0)}`,
  }))

  const columns: ColumnsType<Row> = [
    { title: 'PO', dataIndex: 'PO_Number' },
    { title: 'Material', dataIndex: 'Material_Code', render: (v) => v ?? '—' },
    { title: 'Qty', dataIndex: 'Qty', align: 'right', render: (v) => Number(v) },
    { title: 'Reason', dataIndex: 'Reason', ellipsis: true },
    { title: 'Resupply', dataIndex: 'Expected_Resupply', render: (v) => (v ? String(v) : '—') },
    { title: 'By', dataIndex: 'raised_by', width: 110 },
    { title: 'Status', dataIndex: 'status', render: (v: string) => <Tag color={v === 'open' ? 'gold' : 'green'}>{v}</Tag> },
    {
      title: 'Action', key: '__act', width: 120,
      render: (_: unknown, r: Row) => (String(r.status) === 'open'
        ? <Popconfirm title="Mark this return as resupplied / closed?" onConfirm={() => doClose(Number(r.id))}>
            <Button size="small">Close</Button>
          </Popconfirm>
        : <Typography.Text type="secondary">{r.closed_by ? String(r.closed_by) : '—'}</Typography.Text>),
    },
  ]

  return (
    <div>
      <Card size="small" title="Raise a return to vendor" style={{ marginBottom: 16, maxWidth: 940 }}>
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          Returning delivered goods to the vendor reopens the PO line so a re-delivery is expected again.
        </Typography.Paragraph>
        <Form form={form} layout="inline">
          <Form.Item name="po_number" rules={[{ required: true, message: 'PO' }]}>
            <Select showSearch optionFilterProp="label" placeholder="PO" style={{ width: 180 }}
              options={poOptions} onChange={() => form.setFieldValue('po_item_id', undefined)} />
          </Form.Item>
          <Form.Item name="po_item_id" rules={[{ required: true, message: 'line' }]}>
            <Select placeholder="PO line" style={{ width: 340 }} options={lineOptions} disabled={!poWatch} />
          </Form.Item>
          <Form.Item name="qty" rules={[{ required: true, message: 'qty' }]}>
            <InputNumber min={0.0001} placeholder="Qty" style={{ width: 100 }} />
          </Form.Item>
          <Form.Item name="reason" rules={[{ required: true, message: 'reason' }]}>
            <Input placeholder="Reason" style={{ width: 200 }} />
          </Form.Item>
          <Form.Item name="expected_resupply"><DatePicker placeholder="Resupply date" format="YYYY-MM-DD" /></Form.Item>
          <Button type="primary" loading={raise.isPending} onClick={onRaise}>Raise return</Button>
        </Form>
      </Card>
      <Table size="small" loading={isFetching} columns={columns} dataSource={rows ?? []}
        rowKey={(r) => String(r.id)} pagination={{ pageSize: 20, showTotal: (t) => `${t} returns` }} />
    </div>
  )
}

export default function LogisticsPage() {
  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Logistics — Procurement
      </Typography.Title>
      <Tabs
        defaultActiveKey="prs"
        items={[
          { key: 'prs', label: 'Incoming PRs', children: <IncomingPRs /> },
          { key: 'create', label: 'Create PO', children: <CreatePoManual /> },
          { key: 'import', label: '📄 Import PO PDF', children: <ImportPoPdf /> },
          { key: 'pos', label: 'Purchase Orders', children: <PurchaseOrders /> },
          { key: 'dns', label: 'DN Approvals', children: <DnApprovalQueue scope="logistics" /> },
          { key: 'vreturns', label: 'Vendor Returns', children: <VendorReturns /> },
          { key: 'reschedules', label: 'Reschedules', children: <Reschedules /> },
          { key: 'force', label: 'Force-Closures', children: <ForceClosures /> },
        ]}
      />
    </div>
  )
}
