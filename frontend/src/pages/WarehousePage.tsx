import { useMemo, useState } from 'react'
import {
  App, Button, Input, InputNumber, Modal, Select, Space, Table, Tabs, Tag, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  useCreateDn, useDnItems, useList, useShipDn, useWhAck, useWhAssignmentItems,
  useWhAssignments, useWhDns, useWhReceive,
} from '../api/hooks'
import type { Row } from '../api/client'

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

// ---- Assignments tab --------------------------------------------------------
function Assignments({ warehouseId }: { warehouseId?: string }) {
  const { message } = App.useApp()
  const { data: rows, isFetching } = useWhAssignments(warehouseId)
  const ack = useWhAck()
  const [receiveFor, setReceiveFor] = useState<Row | null>(null)
  const [dnFor, setDnFor] = useState<Row | null>(null)

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

export default function WarehousePage() {
  const warehouses = useList('/warehouses', { limit: 200 })
  const options = useMemo(
    () => (warehouses.data?.items ?? []).map((w: Row) => ({ value: String(w.Warehouse_ID), label: `${w.Warehouse_ID} — ${w.Name ?? ''}` })),
    [warehouses.data],
  )
  const [wh, setWh] = useState<string | undefined>(undefined)
  const active = wh ?? (options[0]?.value as string | undefined)

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Warehouse
      </Typography.Title>
      <Space style={{ marginBottom: 12 }}>
        <span>Warehouse:</span>
        <Select style={{ width: 240 }} placeholder="Select warehouse" value={active}
          onChange={setWh} options={options} loading={warehouses.isFetching} />
      </Space>
      <Tabs
        defaultActiveKey="assignments"
        items={[
          { key: 'assignments', label: 'Incoming Assignments', children: <Assignments warehouseId={active} /> },
          { key: 'dns', label: 'Delivery Notes', children: <DeliveryNotes warehouseId={active} /> },
        ]}
      />
    </div>
  )
}
