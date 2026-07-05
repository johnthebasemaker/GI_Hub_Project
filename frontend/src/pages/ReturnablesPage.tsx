import { useState } from 'react'
import { App, Button, DatePicker, Form, Input, InputNumber, Modal, Popconfirm, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { useCreateReturnable, useMarkReturned, useReturnables } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'
import type { Row } from '../api/client'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

// Tool loans: who borrowed what, when it's due back, and what's overdue.
// Overdue items fire a one-time in-app notification server-side.
export default function ReturnablesPage() {
  const { message } = App.useApp()
  const { user } = useAuth()
  const { data, isFetching } = useReturnables()
  const create = useCreateReturnable()
  const ret = useMarkReturned()
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()

  const now = data?.now ? dayjs(data.now) : dayjs()
  const isOverdue = (r: Row) =>
    r.status === 'borrowed' && !!r.expected_return_time && dayjs(String(r.expected_return_time)).isBefore(now)

  const submit = async () => {
    const v = await form.validateFields()
    try {
      await create.mutateAsync({
        material_name: v.material_name,
        borrower_name: v.borrower_name,
        borrower_phone: v.borrower_phone || undefined,
        qty: v.qty ?? 1,
        uom: v.uom || undefined,
        expected_return_time: (v.due as dayjs.Dayjs).toISOString(),
        site_id: user?.site_id || undefined,
      })
      message.success('Loan recorded')
      setOpen(false)
      form.resetFields()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const markReturned = async (id: number) => {
    try {
      await ret.mutateAsync(id)
      message.success('Marked returned')
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: 'Item', dataIndex: 'material_name', ellipsis: true },
    { title: 'Qty', dataIndex: 'qty', align: 'right', width: 70 },
    { title: 'UOM', dataIndex: 'uom', width: 70, render: (v) => v ?? '—' },
    { title: 'Borrower', dataIndex: 'borrower_name', width: 140 },
    { title: 'Given', dataIndex: 'given_time', width: 160,
      render: (v) => (v ? String(v).slice(0, 16).replace('T', ' ') : '—') },
    { title: 'Due back', dataIndex: 'expected_return_time', width: 160,
      render: (v) => (v ? String(v).slice(0, 16).replace('T', ' ') : '—') },
    {
      title: 'Status', key: '__s', width: 110,
      render: (_: unknown, r: Row) =>
        r.status === 'returned' ? (
          <Tag color="green">returned</Tag>
        ) : isOverdue(r) ? (
          <Tag color="red">OVERDUE</Tag>
        ) : (
          <Tag color="gold">on loan</Tag>
        ),
    },
    {
      title: 'Action', key: '__a', width: 130,
      render: (_: unknown, r: Row) =>
        r.status === 'borrowed' ? (
          <Popconfirm title="Tool physically back in the store?" onConfirm={() => markReturned(Number(r.id))}>
            <Button size="small" type="primary">Mark returned</Button>
          </Popconfirm>
        ) : null,
    },
  ]

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Returnable Items
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Tools and equipment on loan to employees. Overdue loans are flagged here and
        raise a one-time notification.
      </Typography.Paragraph>

      <Space style={{ marginBottom: 12 }}>
        <Button type="primary" onClick={() => setOpen(true)}>Loan a tool</Button>
      </Space>

      <Table
        size="small"
        loading={isFetching}
        columns={columns}
        dataSource={data?.items ?? []}
        rowKey={(r) => String(r.id)}
        rowClassName={(r) => (isOverdue(r) ? 'gi-row-overdue' : '')}
        scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 20, showTotal: (t) => `${t} loans` }}
      />

      <Modal title="Loan a tool to an employee" open={open} onOk={submit}
        onCancel={() => setOpen(false)} confirmLoading={create.isPending} okText="Record loan"
        destroyOnHidden>
        <Form form={form} layout="vertical" preserve={false} initialValues={{ qty: 1 }}>
          <Form.Item name="material_name" label="Tool / item" rules={[{ required: true }]}>
            <Input placeholder="e.g. Torque wrench" />
          </Form.Item>
          <Form.Item name="borrower_name" label="Borrower" rules={[{ required: true }]}>
            <Input placeholder="Employee name" />
          </Form.Item>
          <Form.Item name="borrower_phone" label="Phone (optional)"><Input /></Form.Item>
          <Space size="middle">
            <Form.Item name="qty" label="Qty"><InputNumber min={0.001} /></Form.Item>
            <Form.Item name="uom" label="UOM (optional)"><Input style={{ width: 100 }} /></Form.Item>
          </Space>
          <Form.Item name="due" label="Expected return" rules={[{ required: true }]}>
            <DatePicker showTime style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
