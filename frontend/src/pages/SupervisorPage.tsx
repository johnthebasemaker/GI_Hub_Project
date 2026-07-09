import { App, Button, Card, Col, Form, Input, InputNumber, Popconfirm, Row, Select, Space, Switch, Table, Tabs, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { FormInstance } from 'antd'
import { MinusCircleOutlined, PlusOutlined } from '@ant-design/icons'
import { useAuth } from '../auth/AuthContext'
import {
  useCancelSmr, useCreateSmr, useIntentVsActual, useList, useSites, useSmrItems,
  useSmrList, useSmrStock,
} from '../api/hooks'
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

// Live-stock feedback for one cart line (Phase 6): shows current site stock and
// flags a shortage before the supervisor submits.
function LineStock({ form, name }: { form: FormInstance<FormValues>; name: number }) {
  const sap = Form.useWatch(['items', name, 'SAP_Code'], form) as string | undefined
  const qty = Form.useWatch(['items', name, 'Requested_Qty'], form) as number | undefined
  const { data } = useSmrStock(sap)
  if (!sap || !data) return null
  const short = qty != null && qty > data.current_stock
  return (
    <Typography.Text type={short ? 'danger' : 'secondary'} style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
      stock: {data.current_stock}{short ? ' ⚠ short' : ''}
    </Typography.Text>
  )
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
                    <Input placeholder="Notes" style={{ width: 180 }} />
                  </Form.Item>
                  <LineStock form={form} name={field.name} />
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

const STATUS_COLOR: Record<string, string> = { pending_sk: 'blue', approved: 'green', rejected: 'red', cancelled: 'default' }

function MyRequests() {
  const { message } = App.useApp()
  const { data: rows, isFetching } = useSmrList({ mine: true })
  const cancel = useCancelSmr()
  const doCancel = async (id: number) => {
    try {
      await cancel.mutateAsync(id)
      message.success('Request cancelled')
    } catch (e) {
      message.error(errMsg(e))
    }
  }
  const columns: ColumnsType<ApiRow> = [
    { title: 'Request', dataIndex: 'request_no', key: 'request_no' },
    { title: 'Site', dataIndex: 'Site_ID', key: 'Site_ID' },
    { title: 'Worker', dataIndex: 'Worker_Name', key: 'Worker_Name' },
    { title: 'Job/Tank', dataIndex: 'Job_Tank_Place', key: 'Job_Tank_Place', ellipsis: true },
    { title: 'Status', dataIndex: 'status', key: 'status', render: (v: string) => <Tag color={STATUS_COLOR[v] ?? 'default'}>{v}</Tag> },
    {
      title: 'Action', key: '__act', width: 110,
      render: (_: unknown, r: ApiRow) => (String(r.status) === 'pending_sk' ? (
        <Popconfirm title="Cancel this pending request?" onConfirm={() => doCancel(Number(r.id))}>
          <Button size="small" danger loading={cancel.isPending}>Cancel</Button>
        </Popconfirm>
      ) : null),
    },
  ]
  return (
    <Table size="small" loading={isFetching} columns={columns} dataSource={rows ?? []}
      rowKey={(r) => String(r.id)}
      expandable={{ expandedRowRender: (r) => <SmrItems id={Number(r.id)} /> }}
      pagination={{ pageSize: 20, showTotal: (t) => `${t} requests` }} />
  )
}

// Intent vs Actual — approved requests vs what was actually consumed (Phase 6).
function IntentVsActual() {
  const { data, isFetching } = useIntentVsActual(90)
  const columns: ColumnsType<Record<string, unknown>> = (data?.columns ?? []).map((c) => ({
    title: c.replace(/_/g, ' '), dataIndex: c, key: c,
    render: (v: unknown) => (v == null ? '—' : String(v)),
  }))
  return (
    <Card size="small">
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        Approved request quantities vs actual consumption over the last 90 days, with variance.
      </Typography.Paragraph>
      <Table size="small" loading={isFetching} columns={columns} dataSource={data?.rows ?? []}
        rowKey={(_, i) => String(i)} scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 20, showTotal: (t) => `${t} lines` }} />
    </Card>
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
          { key: 'iva', label: 'Intent vs Actual', children: <IntentVsActual /> },
        ]}
      />
    </div>
  )
}
