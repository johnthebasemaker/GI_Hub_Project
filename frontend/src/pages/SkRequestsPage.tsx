import { App, Button, Popconfirm, Table, Tag, Typography } from 'antd'
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

export default function SkRequestsPage() {
  const { message } = App.useApp()
  const { data: rows, isFetching } = useSmrList({ status: 'pending_sk' })
  const decide = useSmrDecision()

  const act = async (id: number, action: 'approve' | 'reject') => {
    try {
      const res = await decide.mutateAsync({ id, action, reason: 'rejected by SK' })
      message.success(action === 'approve' ? `Approved — ${res.staged_issues} issue(s) staged for HOD` : 'Rejected')
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
          <Popconfirm title="Approve → stage issues for HOD?" onConfirm={() => act(Number(r.id), 'approve')}>
            <Button size="small" type="primary" style={{ marginRight: 8 }}>Approve</Button>
          </Popconfirm>
          <Popconfirm title="Reject this request?" onConfirm={() => act(Number(r.id), 'reject')}>
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
        Approve a supervisor's material request to stage it as an issue for HOD approval.
      </Typography.Paragraph>
      <Table
        size="small" loading={isFetching} columns={columns} dataSource={rows ?? []}
        rowKey={(r) => String(r.id)}
        expandable={{ expandedRowRender: (r) => <Items id={Number(r.id)} /> }}
        pagination={{ pageSize: 20, showTotal: (t) => `${t} pending` }}
      />
    </div>
  )
}
