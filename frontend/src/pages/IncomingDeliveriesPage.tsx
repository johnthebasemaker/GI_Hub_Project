import { App, Button, Popconfirm, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useIncomingDns, useReceiveDn, useSiteDnItems } from '../api/hooks'
import type { Row } from '../api/client'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

function DnItems({ dn }: { dn: string }) {
  const { data: items, isFetching } = useSiteDnItems(dn)
  const columns: ColumnsType<Row> = [
    { title: 'Material', dataIndex: 'Material_Code', key: 'Material_Code' },
    { title: 'Description', dataIndex: 'Description', key: 'Description', ellipsis: true },
    { title: 'Qty', dataIndex: 'Qty', key: 'Qty', align: 'right', render: (v) => Number(v) },
    { title: 'Lot', dataIndex: 'Lot_Number', key: 'Lot_Number', render: (v) => v ?? '—' },
  ]
  return <Table sticky={{ offsetHeader: 64 }} size="small" loading={isFetching} columns={columns} dataSource={items ?? []} rowKey={(r) => String(r.id)} pagination={false} />
}

export default function IncomingDeliveriesPage() {
  const { message } = App.useApp()
  const { data: rows, isFetching } = useIncomingDns()
  const receive = useReceiveDn()

  const doReceive = async (dn: string) => {
    try {
      const res = await receive.mutateAsync(dn)
      message.success(res.message ?? `Received ${res.staged} line(s) — staged for HOD approval`)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = [
    { title: 'DN Number', dataIndex: 'DN_Number', key: 'DN_Number' },
    { title: 'PO', dataIndex: 'PO_Number', key: 'PO_Number' },
    { title: 'From WH', dataIndex: 'Warehouse_ID', key: 'Warehouse_ID' },
    { title: 'Site', dataIndex: 'Site_ID', key: 'Site_ID' },
    { title: 'Driver', dataIndex: 'Driver_Name', key: 'Driver_Name', render: (v) => v ?? '—' },
    { title: 'Status', dataIndex: 'status', key: 'status', render: (v: string) => <Tag color="blue">{v}</Tag> },
    {
      title: 'Action', key: '__act',
      render: (_: unknown, r: Row) => (
        <Popconfirm title="Receive this delivery? It will be staged for HOD approval." onConfirm={() => doReceive(String(r.DN_Number))}>
          <Button size="small" type="primary">Receive</Button>
        </Popconfirm>
      ),
    },
  ]

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Incoming Deliveries
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        In-transit Delivery Notes headed to your site. Receiving stages each line as a
        pending receipt for HOD approval (which commits it to the ledger).
      </Typography.Paragraph>
      <Table sticky={{ offsetHeader: 64 }}
        size="small" loading={isFetching} columns={columns} dataSource={rows ?? []}
        rowKey={(r) => String(r.DN_Number)}
        expandable={{ expandedRowRender: (r) => <DnItems dn={String(r.DN_Number)} /> }}
        pagination={{ pageSize: 20, showTotal: (t) => `${t} incoming` }}
      />
    </div>
  )
}
