import { useState } from 'react'
import { App, Button, Form, Modal, Popconfirm, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useApprovePending, useList, usePendingUsers, useRejectPending } from '../api/hooks'
import type { Row } from '../api/client'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

const ROLE_OPTIONS = [
  { value: 'store_keeper', label: 'Store Keeper' },
  { value: 'supervisor', label: 'Supervisor' },
  { value: 'hod', label: 'Head of Department' },
  { value: 'warehouse_user', label: 'Warehouse' },
  { value: 'logistics', label: 'Logistics' },
  { value: 'admin', label: 'Admin' },
]

export default function PendingUsersPage() {
  const { message } = App.useApp()
  const [form] = Form.useForm()
  const [target, setTarget] = useState<Row | null>(null)
  const { data: pending, isFetching } = usePendingUsers()
  const warehouses = useList('/warehouses', { limit: 500 })
  const approve = useApprovePending()
  const reject = useRejectPending()

  const openApprove = (r: Row) => {
    setTarget(r)
    form.setFieldsValue({ role: r.role, warehouse_id: r.Warehouse_ID || undefined })
  }
  const doApprove = async () => {
    if (!target) return
    try {
      const v = await form.validateFields()
      const res = await approve.mutateAsync({ id: Number(target.id), body: v })
      message.success(`${res.username} approved as ${res.role}`)
      setTarget(null)
    } catch (e) {
      if ((e as { errorFields?: unknown }).errorFields) return
      message.error(errMsg(e))
    }
  }
  const doReject = async (r: Row) => {
    try { await reject.mutateAsync(Number(r.id)); message.success(`Request from ${r.username} rejected`) }
    catch (e) { message.error(errMsg(e)) }
  }

  const whOptions = (warehouses.data?.items ?? []).map((w: Row) => ({
    value: String(w.Warehouse_ID), label: `${w.Warehouse_ID}${w.Name ? ' — ' + w.Name : ''}`,
  }))

  const columns: ColumnsType<Row> = [
    { title: 'Username', dataIndex: 'username', key: 'username' },
    { title: 'Requested role', dataIndex: 'role', key: 'role', render: (v) => <Tag>{String(v)}</Tag> },
    { title: 'Site', dataIndex: 'Site_ID', key: 'Site_ID', render: (v) => v || <Typography.Text type="secondary">global</Typography.Text> },
    {
      // T4: unscoped (global) registrants give a free-text location instead of a site.
      title: 'Location', dataIndex: 'Location', key: 'Location',
      render: (v) => v || '—',
    },
    { title: 'Phone', dataIndex: 'Phone_Number', key: 'Phone_Number', render: (v) => v || '—' },
    { title: 'Requested', dataIndex: 'created_at', key: 'created_at', render: (v) => String(v ?? '') },
    {
      title: 'Actions', key: '__act', width: 200,
      render: (_: unknown, r: Row) => (
        <Space size="small">
          <Button size="small" type="primary" onClick={() => openApprove(r)}>Approve</Button>
          <Popconfirm title={`Reject the request from ${r.username}?`} onConfirm={() => doReject(r)}>
            <Button size="small" danger>Reject</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Access Requests</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Pending self-service registrations. Approving creates the login account (you can
        override the role and bind a warehouse); the applicant's password is carried over.
      </Typography.Paragraph>
      <Table
        size="small"
        loading={isFetching}
        columns={columns}
        dataSource={pending ?? []}
        rowKey={(r) => String(r.id)}
        pagination={{ pageSize: 20, showTotal: (t) => `${t} pending` }}
        locale={{ emptyText: 'No pending access requests' }}
      />

      <Modal open={target !== null} onOk={doApprove} onCancel={() => setTarget(null)} forceRender
        title={`Approve ${target?.username}`} okText="Approve & create user" confirmLoading={approve.isPending}>
        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item name="role" label="Role" rules={[{ required: true }]}>
            <Select options={ROLE_OPTIONS} />
          </Form.Item>
          <Form.Item name="warehouse_id" label="Warehouse (optional)">
            <Select allowClear showSearch optionFilterProp="label" options={whOptions}
              loading={warehouses.isFetching} placeholder="Warehouse binding" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
