import { useState } from 'react'
import { App, Button, InputNumber, Modal, Popconfirm, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useSmrDecision, useSmrItems, useSmrList } from '../api/hooks'
import type { Row } from '../api/client'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

function Items({ id }: { id: number }) {
  const { data: items } = useSmrItems(id)
  const columns: ColumnsType<Row> = [
    { title: 'Material', dataIndex: 'SAP_Code', key: 'SAP_Code' },
    { title: 'Description', dataIndex: 'Equipment_Description', key: 'd', ellipsis: true },
    { title: 'Qty', dataIndex: 'Requested_Qty', key: 'q', align: 'right', render: (v) => Number(v) },
    { title: 'Stock@req', dataIndex: 'Stock_At_Request', key: 's', align: 'right', render: (v) => Number(v ?? 0) },
    { title: 'Available', dataIndex: 'Available_Flag', key: 'a', render: (v) => (v ? <Tag color="green">yes</Tag> : <Tag color="red">short</Tag>) },
  ]
  return <Table size="small" columns={columns} dataSource={items ?? []} rowKey={(r) => String(r.id)} pagination={false} />
}

// Approve modal: the SK can trim (or zero-out = withdraw) each line before it
// stages issues for the HOD — legacy qty-adjust-at-approval parity.
function ApproveModal({ id, onClose }: { id: number | null; onClose: () => void }) {
  const { message } = App.useApp()
  const { data: items } = useSmrItems(id ?? 0)
  const decide = useSmrDecision()
  const [qty, setQty] = useState<Record<string, number>>({})

  const ok = async () => {
    // Only send lines the SK actually changed.
    const adjustments: Record<string, number> = {}
    for (const it of items ?? []) {
      const v = qty[String(it.id)]
      if (v !== undefined && Math.abs(v - Number(it.Requested_Qty)) > 1e-9) {
        adjustments[String(it.id)] = v
      }
    }
    try {
      const res = await decide.mutateAsync({
        id: id!, action: 'approve',
        adjustments: Object.keys(adjustments).length ? adjustments : undefined,
      })
      message.success(`Approved — ${res.staged_issues} issue(s) staged for HOD`)
      setQty({})
      onClose()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = [
    { title: 'Material', dataIndex: 'SAP_Code' },
    { title: 'Description', dataIndex: 'Equipment_Description', ellipsis: true },
    { title: 'Requested', dataIndex: 'Requested_Qty', align: 'right', width: 90, render: (v) => Number(v) },
    {
      title: 'Approve qty (0 withdraws)', key: '__q', width: 180,
      render: (_: unknown, it: Row) => (
        <InputNumber
          size="small" min={0} style={{ width: 150 }}
          value={qty[String(it.id)] ?? Number(it.Requested_Qty)}
          onChange={(v) => setQty((m) => ({ ...m, [String(it.id)]: v ?? 0 }))}
        />
      ),
    },
  ]

  return (
    <Modal title="Approve request — adjust quantities if needed" open={id != null}
      onOk={ok} onCancel={onClose} confirmLoading={decide.isPending}
      okText="Approve & stage" width={640} destroyOnHidden>
      <Table size="small" columns={columns} dataSource={items ?? []}
        rowKey={(r) => String(r.id)} pagination={false} />
    </Modal>
  )
}

export default function SkRequestsPage() {
  const { message } = App.useApp()
  const { data: rows, isFetching } = useSmrList({ status: 'pending_sk' })
  const decide = useSmrDecision()
  const [approving, setApproving] = useState<number | null>(null)

  const rejectIt = async (id: number) => {
    try {
      await decide.mutateAsync({ id, action: 'reject', reason: 'rejected by SK' })
      message.success('Rejected')
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = [
    { title: 'Request', dataIndex: 'request_no', key: 'request_no' },
    { title: 'Site', dataIndex: 'Site_ID', key: 'Site_ID' },
    { title: 'Worker', dataIndex: 'Worker_Name', key: 'Worker_Name' },
    { title: 'Job/Tank', dataIndex: 'Job_Tank_Place', key: 'Job_Tank_Place', ellipsis: true },
    { title: 'By', dataIndex: 'requested_by', key: 'requested_by' },
    {
      title: 'Action', key: '__act', width: 200,
      render: (_: unknown, r: Row) => (
        <>
          <Button size="small" type="primary" style={{ marginRight: 8 }}
            onClick={() => setApproving(Number(r.id))}>
            Review &amp; approve
          </Button>
          <Popconfirm title="Reject this request?" onConfirm={() => rejectIt(Number(r.id))}>
            <Button size="small" danger>Reject</Button>
          </Popconfirm>
        </>
      ),
    },
  ]

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Supervisor Requests
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Review a supervisor's material request — adjust or withdraw lines if stock
        demands it — then approve to stage the issues for HOD approval.
      </Typography.Paragraph>
      <Table
        size="small" loading={isFetching} columns={columns} dataSource={rows ?? []}
        rowKey={(r) => String(r.id)}
        expandable={{ expandedRowRender: (r) => <Items id={Number(r.id)} /> }}
        pagination={{ pageSize: 20, showTotal: (t) => `${t} pending` }}
      />
      <ApproveModal id={approving} onClose={() => setApproving(null)} />
    </div>
  )
}
