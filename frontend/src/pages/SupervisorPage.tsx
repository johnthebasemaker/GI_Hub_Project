import { App, Button, Card, Col, Form, Input, InputNumber, Row, Select, Space, Switch, Table, Tabs, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { MinusCircleOutlined, PlusOutlined } from '@ant-design/icons'
import { useAuth } from '../auth/AuthContext'
import { useCreateSmr, useList, useSites, useSmrItems, useSmrList } from '../api/hooks'
import type { Row as ApiRow } from '../api/client'
import { useState } from 'react'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

interface FormValues {
  site_id: string
  worker_id: string
  job_tank_place: string
  old_ppe_returned: boolean
  no_return_reason?: string
  items: { SAP_Code: string; Requested_Qty: number; Notes?: string }[]
}

function NewRequest() {
  const { message } = App.useApp()
  const { user } = useAuth()
  const [form] = Form.useForm<FormValues>()
  const { data: sites } = useSites()
  const employees = useList('/employees', { limit: 500 })
  const inventory = useList('/inventory', { limit: 500 })
  const create = useCreateSmr()
  const ppe = Form.useWatch('old_ppe_returned', form)

  const workerOptions = (employees.data?.items ?? [])
    .filter((e: ApiRow) => e.status === 'active')
    .map((e: ApiRow) => ({ value: String(e.ID_Number), label: `${e.ID_Number} — ${e.Name ?? ''} (${e.Site_ID ?? '—'})` }))
  const itemOptions = (inventory.data?.items ?? []).map((r: ApiRow) => ({
    value: String(r.SAP_Code), label: `${r.SAP_Code} — ${r.Equipment_Description ?? ''}`,
  }))

  const onFinish = async (v: FormValues) => {
    try {
      const res = await create.mutateAsync({
        site_id: v.site_id || user?.site_id || null,
        worker_id: v.worker_id,
        job_tank_place: v.job_tank_place,
        old_ppe_returned: v.old_ppe_returned,
        no_return_reason: v.no_return_reason || null,
        items: v.items,
      })
      message.success(`Request ${res.request_no} created (${res.lines} line(s))`)
      form.resetFields()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  return (
    <Card style={{ maxWidth: 860 }}>
      <Form<FormValues>
        form={form}
        layout="vertical"
        initialValues={{ old_ppe_returned: true, site_id: user?.site_id || undefined, items: [{}] }}
        onFinish={onFinish}
      >
        <Row gutter={16}>
          <Col xs={24} md={8}>
            <Form.Item name="site_id" label="Site" rules={[{ required: true }]}>
              <Select placeholder="Site" options={(sites ?? []).map((s) => ({ value: s, label: s }))} />
            </Form.Item>
          </Col>
          <Col xs={24} md={10}>
            <Form.Item name="worker_id" label="Worker" rules={[{ required: true }]}>
              <Select showSearch optionFilterProp="label" placeholder="Worker" options={workerOptions} loading={employees.isFetching} />
            </Form.Item>
          </Col>
          <Col xs={24} md={6}>
            <Form.Item name="old_ppe_returned" label="Old PPE returned" valuePropName="checked">
              <Switch checkedChildren="Yes" unCheckedChildren="No" />
            </Form.Item>
          </Col>
        </Row>
        <Row gutter={16}>
          <Col xs={24} md={12}>
            <Form.Item name="job_tank_place" label="Job / Tank / Place" rules={[{ required: true }]}>
              <Input placeholder="e.g. Tank 5 blasting" />
            </Form.Item>
          </Col>
          {ppe === false && (
            <Col xs={24} md={12}>
              <Form.Item name="no_return_reason" label="Reason PPE not returned" rules={[{ required: true }]}>
                <Input placeholder="reason" />
              </Form.Item>
            </Col>
          )}
        </Row>

        <Typography.Text strong>Items</Typography.Text>
        <Form.List name="items">
          {(fields, { add, remove }) => (
            <>
              {fields.map((field) => (
                <Space key={field.key} align="baseline" style={{ display: 'flex', marginTop: 8 }}>
                  <Form.Item name={[field.name, 'SAP_Code']} rules={[{ required: true, message: 'Material' }]}>
                    <Select showSearch optionFilterProp="label" placeholder="Material" style={{ width: 320 }} options={itemOptions} />
                  </Form.Item>
                  <Form.Item name={[field.name, 'Requested_Qty']} rules={[{ required: true, message: 'Qty' }]}>
                    <InputNumber min={0.0001} placeholder="Qty" style={{ width: 110 }} />
                  </Form.Item>
                  <Form.Item name={[field.name, 'Notes']}>
                    <Input placeholder="Notes" style={{ width: 200 }} />
                  </Form.Item>
                  {fields.length > 1 && <MinusCircleOutlined onClick={() => remove(field.name)} />}
                </Space>
              ))}
              <Form.Item>
                <Button type="dashed" onClick={() => add()} icon={<PlusOutlined />} style={{ marginTop: 8 }}>
                  Add item
                </Button>
              </Form.Item>
            </>
          )}
        </Form.List>

        <Button type="primary" htmlType="submit" loading={create.isPending}>
          Submit request
        </Button>
      </Form>
    </Card>
  )
}

function SmrItems({ id }: { id: number }) {
  const { data: items } = useSmrItems(id)
  const columns: ColumnsType<ApiRow> = [
    { title: 'Material', dataIndex: 'SAP_Code', key: 'SAP_Code' },
    { title: 'Description', dataIndex: 'Equipment_Description', key: 'd', ellipsis: true },
    { title: 'Qty', dataIndex: 'Requested_Qty', key: 'q', align: 'right', render: (v) => Number(v) },
    { title: 'Stock@req', dataIndex: 'Stock_At_Request', key: 's', align: 'right', render: (v) => Number(v ?? 0) },
    { title: 'Available', dataIndex: 'Available_Flag', key: 'a', render: (v) => (v ? <Tag color="green">yes</Tag> : <Tag color="red">short</Tag>) },
  ]
  return <Table size="small" columns={columns} dataSource={items ?? []} rowKey={(r) => String(r.id)} pagination={false} />
}

const STATUS_COLOR: Record<string, string> = { pending_sk: 'blue', approved: 'green', rejected: 'red' }

function MyRequests() {
  const { data: rows, isFetching } = useSmrList({ mine: true })
  const columns: ColumnsType<ApiRow> = [
    { title: 'Request', dataIndex: 'request_no', key: 'request_no' },
    { title: 'Site', dataIndex: 'Site_ID', key: 'Site_ID' },
    { title: 'Worker', dataIndex: 'Worker_Name', key: 'Worker_Name' },
    { title: 'Job/Tank', dataIndex: 'Job_Tank_Place', key: 'Job_Tank_Place', ellipsis: true },
    { title: 'Status', dataIndex: 'status', key: 'status', render: (v: string) => <Tag color={STATUS_COLOR[v] ?? 'default'}>{v}</Tag> },
  ]
  return (
    <Table size="small" loading={isFetching} columns={columns} dataSource={rows ?? []}
      rowKey={(r) => String(r.id)}
      expandable={{ expandedRowRender: (r) => <SmrItems id={Number(r.id)} /> }}
      pagination={{ pageSize: 20, showTotal: (t) => `${t} requests` }} />
  )
}

export default function SupervisorPage() {
  const [tab, setTab] = useState('new')
  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Material Requests
      </Typography.Title>
      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          { key: 'new', label: 'New Request', children: <NewRequest /> },
          { key: 'mine', label: 'My Requests', children: <MyRequests /> },
        ]}
      />
    </div>
  )
}
