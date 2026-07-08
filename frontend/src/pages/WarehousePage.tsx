import { useMemo, useState } from 'react'
import {
  App, Button, DatePicker, Form, Input, InputNumber, Modal, Select, Space, Table, Tabs, Tag, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { Dayjs } from 'dayjs'
import {
  useCreateDn, useDnItems, useList, useRaiseReschedule, useShipDn, useWhAck, useWhAssignmentItems,
  useWhAssignments, useWhCreateReturn, useWhDisposition, useWhDns, useWhHistory,
  useWhReceive, useWhReturns,
} from '../api/hooks'
import type { Row } from '../api/client'
import { useAuth } from '../auth/AuthContext'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

// ---- Receive modal ----------------------------------------------------------
function ReceiveModal({ assignment, onClose }: { assignment: Row | null; onClose: () => void }) {
  const { message } = App.useApp()
  const { data: items } = useWhAssignmentItems(assignment ? Number(assignment.assignment_id) : null)
  const receive = useWhReceive()
  const [qty, setQty] = useState<Record<string, number>>({})

  const submit = async () => {
    const received = Object.fromEntries(Object.entries(qty).filter(([, v]) => v > 0))
    if (!Object.keys(received).length) return message.warning('Enter at least one quantity')
    try {
      const res = await receive.mutateAsync({ id: Number(assignment!.assignment_id), received })
      message.success(`Received ${res.lines} line(s)`)
      setQty({})
      onClose()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = [
    { title: 'Material', dataIndex: 'Material_Code', key: 'Material_Code' },
    { title: 'Description', dataIndex: 'Description', key: 'Description', ellipsis: true },
    { title: 'Ordered', dataIndex: 'Qty', key: 'Qty', align: 'right', render: (v) => Number(v) },
    { title: 'Delivered', dataIndex: 'Delivered_Qty', key: 'Delivered_Qty', align: 'right', render: (v) => Number(v ?? 0) },
    {
      title: 'Receive now',
      key: '__rx',
      width: 130,
      render: (_: unknown, r: Row) => (
        <InputNumber
          min={0} style={{ width: 110 }}
          value={qty[String(r.id)]}
          onChange={(v) => setQty((q) => ({ ...q, [String(r.id)]: v ?? 0 }))}
        />
      ),
    },
  ]

  return (
    <Modal open={!!assignment} title={`Receive goods — PO ${assignment?.PO_Number ?? ''}`}
      width={760} onCancel={onClose} onOk={submit} confirmLoading={receive.isPending}>
      <Table size="small" columns={columns} dataSource={items ?? []} rowKey={(r) => String(r.id)} pagination={false} />
    </Modal>
  )
}

// ---- Prepare DN modal -------------------------------------------------------
function PrepareDNModal({ assignment, onClose }: { assignment: Row | null; onClose: () => void }) {
  const { message } = App.useApp()
  const { data: items } = useWhAssignmentItems(assignment ? Number(assignment.assignment_id) : null)
  const createDn = useCreateDn()
  const [qty, setQty] = useState<Record<string, number>>({})
  const [lot, setLot] = useState<Record<string, string>>({})

  const submit = async () => {
    const line_items = Object.entries(qty)
      .filter(([, v]) => v > 0)
      .map(([id, v]) => ({ po_item_id: Number(id), Qty: v, Lot_Number: lot[id] || null }))
    if (!line_items.length) return message.warning('Enter at least one quantity to ship')
    try {
      const res = await createDn.mutateAsync({
        po_number: String(assignment!.PO_Number),
        warehouse_id: String(assignment!.Warehouse_ID ?? ''),
        site_id: String(assignment!.Site_ID),
        line_items,
      })
      message.success(`DN ${res.dn_number} drafted (${res.lines} line(s))`)
      setQty({}); setLot({})
      onClose()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = [
    { title: 'Material', dataIndex: 'Material_Code', key: 'Material_Code' },
    { title: 'Delivered', dataIndex: 'Delivered_Qty', key: 'Delivered_Qty', align: 'right', render: (v) => Number(v ?? 0) },
    { title: 'Family', dataIndex: 'rl_bl_family', key: 'rl_bl_family', render: (v) => v ?? '—' },
    {
      title: 'Ship qty', key: '__q', width: 120,
      render: (_: unknown, r: Row) => (
        <InputNumber min={0} style={{ width: 100 }} value={qty[String(r.id)]}
          onChange={(v) => setQty((q) => ({ ...q, [String(r.id)]: v ?? 0 }))} />
      ),
    },
    {
      title: 'Lot', key: '__lot', width: 130,
      render: (_: unknown, r: Row) => (
        <Input style={{ width: 120 }} value={lot[String(r.id)]}
          onChange={(e) => setLot((l) => ({ ...l, [String(r.id)]: e.target.value }))} />
      ),
    },
  ]

  return (
    <Modal open={!!assignment} title={`Prepare DN — PO ${assignment?.PO_Number ?? ''} → ${assignment?.Site_ID ?? ''}`}
      width={820} onCancel={onClose} onOk={submit} confirmLoading={createDn.isPending}>
      <Typography.Paragraph type="secondary">
        Ship received goods to the site. RL and BL families must be on separate DNs.
      </Typography.Paragraph>
      <Table size="small" columns={columns} dataSource={items ?? []} rowKey={(r) => String(r.id)} pagination={false} />
    </Modal>
  )
}

// ---- Reschedule request modal (H7) -----------------------------------------
function RescheduleModal({ po, onClose }: { po: Row | null; onClose: () => void }) {
  const { message } = App.useApp()
  const raise = useRaiseReschedule()
  const [form] = Form.useForm<{ requested_date: Dayjs; reason: string }>()
  const submit = async () => {
    const v = await form.validateFields()
    try {
      await raise.mutateAsync({
        po_number: String(po!.PO_Number),
        requested_date: v.requested_date.format('YYYY-MM-DD'),
        reason: v.reason,
      })
      message.success('Reschedule requested — Logistics will review it')
      onClose()
    } catch (e) {
      message.error(errMsg(e))
    }
  }
  return (
    <Modal open={!!po} title={`Request reschedule — PO ${po?.PO_Number ?? ''}`}
      onOk={submit} onCancel={onClose} okText="Send to Logistics"
      confirmLoading={raise.isPending} destroyOnHidden>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        Current expected delivery: <b>{po?.Expected_Delivery ? String(po.Expected_Delivery) : '—'}</b>. Logistics
        approves the new date and it's pushed onto the PO.
      </Typography.Paragraph>
      <Form form={form} layout="vertical">
        <Form.Item name="requested_date" label="New delivery date" rules={[{ required: true }]}>
          <DatePicker style={{ width: '100%' }} format="YYYY-MM-DD" />
        </Form.Item>
        <Form.Item name="reason" label="Reason" rules={[{ required: true, message: 'a reason is required' }]}>
          <Input.TextArea rows={2} placeholder="e.g. vendor pushed the ship date" />
        </Form.Item>
      </Form>
    </Modal>
  )
}

// ---- Assignments tab --------------------------------------------------------
function Assignments({ warehouseId }: { warehouseId?: string }) {
  const { message } = App.useApp()
  const { data: rows, isFetching } = useWhAssignments(warehouseId)
  const ack = useWhAck()
  const [receiveFor, setReceiveFor] = useState<Row | null>(null)
  const [dnFor, setDnFor] = useState<Row | null>(null)
  const [rescheduleFor, setRescheduleFor] = useState<Row | null>(null)

  const doAck = async (r: Row) => {
    try {
      await ack.mutateAsync(Number(r.assignment_id))
      message.success('Acknowledged')
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = [
    { title: 'PO', dataIndex: 'PO_Number', key: 'PO_Number' },
    { title: 'PR', dataIndex: 'PR_Number', key: 'PR_Number' },
    { title: 'Site', dataIndex: 'Site_ID', key: 'Site_ID' },
    { title: 'Vendor', dataIndex: 'Vendor_Name', key: 'Vendor_Name', render: (v) => v ?? '—' },
    { title: 'Expected', dataIndex: 'Expected_Delivery', key: 'Expected_Delivery', render: (v) => v ?? '—' },
    { title: 'Status', dataIndex: 'status', key: 'status', render: (v: string) => <Tag>{v}</Tag> },
    {
      title: 'Action', key: '__act', width: 320,
      render: (_: unknown, r: Row) => (
        <Space>
          {r.status === 'assigned' && (
            <Button size="small" onClick={() => doAck(r)}>Acknowledge</Button>
          )}
          <Button size="small" onClick={() => setReceiveFor({ ...r, Warehouse_ID: warehouseId })}>Receive</Button>
          <Button size="small" type="primary" onClick={() => setDnFor({ ...r, Warehouse_ID: warehouseId })}>Prepare DN</Button>
          <Button size="small" onClick={() => setRescheduleFor(r)}>Reschedule</Button>
        </Space>
      ),
    },
  ]

  return (
    <>
      <Table size="small" loading={isFetching} columns={columns} dataSource={rows ?? []}
        rowKey={(r) => String(r.assignment_id)}
        pagination={{ pageSize: 20, showTotal: (t) => `${t} assignments` }} />
      <ReceiveModal assignment={receiveFor} onClose={() => setReceiveFor(null)} />
      <PrepareDNModal assignment={dnFor} onClose={() => setDnFor(null)} />
      <RescheduleModal po={rescheduleFor} onClose={() => setRescheduleFor(null)} />
    </>
  )
}

// ---- Delivery Notes tab -----------------------------------------------------
function DnItems({ dn }: { dn: string }) {
  const { data: items, isFetching } = useDnItems(dn)
  const columns: ColumnsType<Row> = [
    { title: 'Material', dataIndex: 'Material_Code', key: 'Material_Code' },
    { title: 'Description', dataIndex: 'Description', key: 'Description', ellipsis: true },
    { title: 'Qty', dataIndex: 'Qty', key: 'Qty', align: 'right', render: (v) => Number(v) },
    { title: 'Lot', dataIndex: 'Lot_Number', key: 'Lot_Number', render: (v) => v ?? '—' },
    { title: 'Status', dataIndex: 'status', key: 'status' },
  ]
  return <Table size="small" loading={isFetching} columns={columns} dataSource={items ?? []} rowKey={(r) => String(r.id)} pagination={false} />
}

function DeliveryNotes({ warehouseId }: { warehouseId?: string }) {
  const { message } = App.useApp()
  const { data: rows, isFetching } = useWhDns(warehouseId)
  const ship = useShipDn()

  const doShip = async (dn: string) => {
    try {
      await ship.mutateAsync(dn)
      message.success('DN marked in-transit')
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = [
    { title: 'DN Number', dataIndex: 'DN_Number', key: 'DN_Number' },
    { title: 'PO', dataIndex: 'PO_Number', key: 'PO_Number' },
    { title: 'Site', dataIndex: 'Site_ID', key: 'Site_ID' },
    { title: 'Family', dataIndex: 'rl_bl_family', key: 'rl_bl_family', render: (v) => v ?? '—' },
    { title: 'Driver', dataIndex: 'Driver_Name', key: 'Driver_Name', render: (v) => v ?? '—' },
    { title: 'Status', dataIndex: 'status', key: 'status', render: (v: string) => <Tag>{v}</Tag> },
    {
      title: 'Action', key: '__act',
      render: (_: unknown, r: Row) =>
        ['draft', 'prepared'].includes(String(r.status)) ? (
          <Button size="small" type="primary" onClick={() => doShip(String(r.DN_Number))}>Ship</Button>
        ) : null,
    },
  ]

  return (
    <Table size="small" loading={isFetching} columns={columns} dataSource={rows ?? []}
      rowKey={(r) => String(r.DN_Number)}
      expandable={{ expandedRowRender: (r) => <DnItems dn={String(r.DN_Number)} /> }}
      pagination={{ pageSize: 20, showTotal: (t) => `${t} DNs` }} />
  )
}

const DISPOSITIONS = ['hold', 'return_to_vendor', 'scrap', 'rework', 'closed']
const DISPO_COLOR: Record<string, string> = {
  open: 'gold', hold: 'orange', return_to_vendor: 'volcano',
  scrap: 'red', rework: 'blue', closed: 'green',
}

function ReturnsFromSite() {
  const { message } = App.useApp()
  const { data: items, isFetching } = useWhReturns()
  const create = useWhCreateReturn()
  const dispo = useWhDisposition()
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()

  const submit = async () => {
    const v = await form.validateFields()
    try {
      await create.mutateAsync(v)
      message.success('Return recorded — logistics notified')
      setOpen(false)
      form.resetFields()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const setStatus = async (id: number, status: string) => {
    try {
      await dispo.mutateAsync({ id, status })
      message.success(`Return #${id} → ${status}`)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: 'PO', dataIndex: 'PO_Number', width: 120 },
    { title: 'DN', dataIndex: 'DN_Number', width: 110, render: (v) => v ?? '—' },
    { title: 'Material', dataIndex: 'Material_Code', render: (v) => v ?? '—' },
    { title: 'Qty', dataIndex: 'Qty', align: 'right', width: 80 },
    { title: 'Reason', dataIndex: 'Reason', ellipsis: true },
    { title: 'Raised by', dataIndex: 'raised_by', width: 110 },
    { title: 'Status', dataIndex: 'status', width: 140,
      render: (v: string) => <Tag color={DISPO_COLOR[v] ?? 'default'}>{v}</Tag> },
    {
      title: 'Disposition', key: '__d', width: 190,
      render: (_: unknown, r: Row) =>
        r.status === 'closed' ? (
          <Typography.Text type="secondary">closed</Typography.Text>
        ) : (
          <Select
            size="small"
            style={{ width: 170 }}
            placeholder="Set disposition"
            options={DISPOSITIONS.map((d) => ({ value: d, label: d.replace(/_/g, ' ') }))}
            onChange={(v) => setStatus(Number(r.id), v)}
          />
        ),
    },
  ]

  return (
    <div>
      <Button type="primary" style={{ marginBottom: 12 }} onClick={() => setOpen(true)}>
        Record return from site
      </Button>
      <Table size="small" loading={isFetching} columns={columns} dataSource={items ?? []}
        rowKey={(r) => String(r.id)} scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 20, showTotal: (t) => `${t} returns` }} />
      <Modal title="Record a return received from a site" open={open} onOk={submit}
        onCancel={() => setOpen(false)} confirmLoading={create.isPending} okText="Record"
        destroyOnHidden>
        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item name="PO_Number" label="PO Number" rules={[{ required: true }]}>
            <Input placeholder="PO the material came from" />
          </Form.Item>
          <Form.Item name="DN_Number" label="DN Number (optional)"><Input /></Form.Item>
          <Form.Item name="Material_Code" label="Material code (optional)"><Input /></Form.Item>
          <Form.Item name="Qty" label="Quantity" rules={[{ required: true }]}>
            <InputNumber style={{ width: '100%' }} min={0.001} />
          </Form.Item>
          <Form.Item name="Reason" label="Reason" rules={[{ required: true }]}>
            <Select options={['damaged', 'over-receipt', 'quality issue', 'wrong item', 'other']
              .map((v) => ({ value: v, label: v }))} />
          </Form.Item>
          <Form.Item name="notes" label="Notes (optional)"><Input.TextArea rows={2} /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

function HistoryTab({ warehouseId }: { warehouseId?: string }) {
  const { data, isFetching } = useWhHistory(warehouseId)
  return (
    <div>
      <Space style={{ marginBottom: 12 }} wrap>
        {(data?.throughput.dn_by_status ?? []).map((r) => (
          <Tag key={String(r.status)} color={String(r.status) === 'in_transit' ? 'gold' : 'default'}>
            {String(r.status)}: {String(r.n)}
          </Tag>
        ))}
        {(data?.throughput.dn_by_family ?? []).map((r) => (
          <Tag key={`f-${r.family}`} color="blue">{String(r.family)}: {String(r.n)}</Tag>
        ))}
      </Space>
      <Typography.Title level={5}>Completed delivery notes</Typography.Title>
      <Table size="small" loading={isFetching} dataSource={data?.dns ?? []}
        rowKey={(r) => String(r.DN_Number)} scroll={{ x: 'max-content' }}
        columns={[
          { title: 'DN', dataIndex: 'DN_Number' }, { title: 'PO', dataIndex: 'PO_Number' },
          { title: 'Site', dataIndex: 'Site_ID' }, { title: 'Family', dataIndex: 'rl_bl_family' },
          { title: 'Date', dataIndex: 'DN_Date' },
          { title: 'Status', dataIndex: 'status', render: (v: string) => <Tag>{v}</Tag> },
        ] as ColumnsType<Row>}
        pagination={{ pageSize: 10 }} />
      <Typography.Title level={5}>Fulfilled assignments</Typography.Title>
      <Table size="small" loading={isFetching} dataSource={data?.assignments ?? []}
        rowKey={(r) => String(r.id)} scroll={{ x: 'max-content' }}
        columns={[
          { title: 'ID', dataIndex: 'id', width: 60 }, { title: 'PO', dataIndex: 'PO_Number' },
          { title: 'Warehouse', dataIndex: 'Warehouse_ID' },
          { title: 'Assigned', dataIndex: 'assigned_at' },
          { title: 'Status', dataIndex: 'status', render: (v: string) => <Tag color="green">{v}</Tag> },
        ] as ColumnsType<Row>}
        pagination={{ pageSize: 10 }} />
    </div>
  )
}

export default function WarehousePage() {
  const { user } = useAuth()
  // Warehouse users are server-pinned to their bound Warehouse_ID — no picker.
  const bound = user?.role === 'warehouse_user' ? (user.warehouse_id || undefined) : undefined
  const warehouses = useList('/warehouses', { limit: 200 })
  const options = useMemo(
    () => (warehouses.data?.items ?? []).map((w: Row) => ({ value: String(w.Warehouse_ID), label: `${w.Warehouse_ID} — ${w.Name ?? ''}` })),
    [warehouses.data],
  )
  const [wh, setWh] = useState<string | undefined>(undefined)
  const active = bound ?? wh ?? (options[0]?.value as string | undefined)

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Warehouse
      </Typography.Title>
      <Space style={{ marginBottom: 12 }}>
        <span>Warehouse:</span>
        {bound ? (
          <Tag color="gold">{bound}</Tag>
        ) : (
        <Select style={{ width: 240 }} placeholder="Select warehouse" value={active}
          onChange={setWh} options={options} loading={warehouses.isFetching} />
        )}
      </Space>
      <Tabs
        defaultActiveKey="assignments"
        items={[
          { key: 'assignments', label: 'Incoming Assignments', children: <Assignments warehouseId={active} /> },
          { key: 'dns', label: 'Delivery Notes', children: <DeliveryNotes warehouseId={active} /> },
          { key: 'returns', label: 'Returns from Site', children: <ReturnsFromSite /> },
          { key: 'history', label: 'History', children: <HistoryTab warehouseId={active} /> },
        ]}
      />
    </div>
  )
}
