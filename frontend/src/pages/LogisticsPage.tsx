import { useState } from 'react'
import {
  App, Button, Card, DatePicker, Descriptions, Form, Input, Modal, Popconfirm, Select, Space,
  Table, Tabs, Tag, Typography, Upload,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { Dayjs } from 'dayjs'
import { InboxOutlined } from '@ant-design/icons'
import {
  useAssignPo, useCreatePo, useDecideReschedule, useForceClose, useForceClosures,
  useList, useLogisticsPos, useLogisticsPrs, usePoItems, useReschedules,
  useSites, useUndoForceClose,
} from '../api/hooks'
import { api } from '../api/client'
import type { Row } from '../api/client'

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

function PurchaseOrders() {
  const { message } = App.useApp()
  const { data: rows, isFetching } = useLogisticsPos()
  const warehouses = useList('/warehouses', { limit: 200 })
  const assign = useAssignPo()
  const [po, setPo] = useState<Row | null>(null)
  const [form] = Form.useForm<{ warehouse_id: string; expected_delivery?: Dayjs; notes?: string }>()

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
      <Table
        size="small" loading={isFetching} columns={columns} dataSource={rows ?? []}
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
          { key: 'import', label: '📄 Import PO PDF', children: <ImportPoPdf /> },
          { key: 'pos', label: 'Purchase Orders', children: <PurchaseOrders /> },
          { key: 'reschedules', label: 'Reschedules', children: <Reschedules /> },
          { key: 'force', label: 'Force-Closures', children: <ForceClosures /> },
        ]}
      />
    </div>
  )
}
