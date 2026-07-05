import { useState } from 'react'
import {
  App, Button, Card, Col, DatePicker, Form, Input, InputNumber, Popconfirm,
  Row, Select, Space, Table, Tabs, Tag, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { Dayjs } from 'dayjs'
import { DownloadOutlined, MinusCircleOutlined, PlusOutlined } from '@ant-design/icons'
import { useAuth } from '../auth/AuthContext'
import { downloadPrPdf, useCreatePr, useHodPrs, useList, useSites, useSubmitPr } from '../api/hooks'
import type { Row as ApiRow } from '../api/client'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

const STATUS_COLOR: Record<string, string> = {
  site_draft: 'default',
  submitted: 'blue',
  in_po: 'green',
}

interface PrFormValues {
  site_id: string
  supplier?: string
  delivery_date?: Dayjs
  notes?: string
  lines: { SAP_Code: string; Requested_Qty: number; Est_Cost_SAR?: number; Notes?: string }[]
}

function NewPr() {
  const { message } = App.useApp()
  const { user } = useAuth()
  const [form] = Form.useForm<PrFormValues>()
  const { data: sites } = useSites()
  const inventory = useList('/inventory', { limit: 500 })
  const create = useCreatePr()

  const itemOptions = (inventory.data?.items ?? []).map((r: ApiRow) => ({
    value: String(r.SAP_Code), label: `${r.SAP_Code} — ${r.Equipment_Description ?? ''}`,
  }))

  const onFinish = async (v: PrFormValues) => {
    try {
      const res = await create.mutateAsync({
        site_id: v.site_id,
        supplier: v.supplier || null,
        notes: v.notes || null,
        delivery_date: v.delivery_date ? v.delivery_date.format('YYYY-MM-DD') : null,
        lines: v.lines,
      })
      message.success(`PR ${res.pr_number} created (${res.lines} line(s)) — now submit it to Logistics`)
      form.resetFields()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  return (
    <Card style={{ maxWidth: 900 }}>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        Create a draft purchase request. Lines are validated against the inventory
        master (Material / UoM auto-filled). The PR number is assigned automatically;
        the draft then appears under <b>Submit to Logistics</b>.
      </Typography.Paragraph>
      <Form<PrFormValues>
        form={form}
        layout="vertical"
        initialValues={{ site_id: user?.site_id || undefined, lines: [{}] }}
        onFinish={onFinish}
      >
        <Row gutter={16}>
          <Col xs={24} md={7}>
            <Form.Item name="site_id" label="Site" rules={[{ required: true }]}>
              <Select placeholder="Site" options={(sites ?? []).map((s) => ({ value: s, label: s }))} />
            </Form.Item>
          </Col>
          <Col xs={24} md={9}>
            <Form.Item name="supplier" label="Supplier (optional)">
              <Input placeholder="Preferred supplier" />
            </Form.Item>
          </Col>
          <Col xs={24} md={8}>
            <Form.Item name="delivery_date" label="Required by (optional)">
              <DatePicker style={{ width: '100%' }} format="YYYY-MM-DD" />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="notes" label="Notes (optional)">
          <Input.TextArea rows={2} placeholder="Applies to lines without their own note" />
        </Form.Item>

        <Typography.Text strong>Lines</Typography.Text>
        <Form.List name="lines">
          {(fields, { add, remove }) => (
            <>
              {fields.map((field) => (
                <Space key={field.key} align="baseline" style={{ display: 'flex', marginTop: 8 }} wrap>
                  <Form.Item name={[field.name, 'SAP_Code']} rules={[{ required: true, message: 'Material' }]}>
                    <Select showSearch optionFilterProp="label" placeholder="Material (SAP)"
                      style={{ width: 320 }} options={itemOptions} loading={inventory.isFetching} />
                  </Form.Item>
                  <Form.Item name={[field.name, 'Requested_Qty']} rules={[{ required: true, message: 'Qty' }]}>
                    <InputNumber min={0.0001} placeholder="Qty" style={{ width: 100 }} />
                  </Form.Item>
                  <Form.Item name={[field.name, 'Est_Cost_SAR']}>
                    <InputNumber min={0} placeholder="Est SAR/unit" style={{ width: 130 }} />
                  </Form.Item>
                  <Form.Item name={[field.name, 'Notes']}>
                    <Input placeholder="Line note" style={{ width: 180 }} />
                  </Form.Item>
                  {fields.length > 1 && <MinusCircleOutlined onClick={() => remove(field.name)} />}
                </Space>
              ))}
              <Form.Item>
                <Button type="dashed" onClick={() => add()} icon={<PlusOutlined />} style={{ marginTop: 8 }}>
                  Add line
                </Button>
              </Form.Item>
            </>
          )}
        </Form.List>

        <Button type="primary" htmlType="submit" loading={create.isPending}>
          Create PR
        </Button>
      </Form>
    </Card>
  )
}

function PrQueue() {
  const { message } = App.useApp()
  const { data: sites } = useSites()
  const [siteId, setSiteId] = useState<string | undefined>(undefined)
  const { data: rows, isFetching } = useHodPrs(siteId)
  const submit = useSubmitPr()

  const doSubmit = async (r: ApiRow) => {
    try {
      const res = await submit.mutateAsync({ pr: String(r.PR_Number), site: String(r.Site_ID) })
      message.success(`Submitted ${res.lines} line(s) of PR ${r.PR_Number} to Logistics`)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<ApiRow> = [
    { title: 'PR Number', dataIndex: 'PR_Number', key: 'PR_Number' },
    { title: 'Site', dataIndex: 'Site_ID', key: 'Site_ID' },
    { title: 'Lines', dataIndex: 'line_count', key: 'line_count', align: 'right' },
    { title: 'Total Qty', dataIndex: 'total_qty', key: 'total_qty', align: 'right', render: (v) => Number(v) },
    {
      title: 'Logistics status',
      dataIndex: 'logistics_status',
      key: 'logistics_status',
      render: (v: string) => <Tag color={STATUS_COLOR[v] ?? 'default'}>{v}</Tag>,
    },
    {
      title: 'Action',
      key: '__act',
      render: (_: unknown, r: ApiRow) => (
        <Space>
          {r.logistics_status === 'in_po' ? (
            <Typography.Text type="secondary">in PO</Typography.Text>
          ) : (
            <Popconfirm title="Submit this PR to Logistics?" onConfirm={() => doSubmit(r)}>
              <Button size="small" type="primary">Submit to Logistics</Button>
            </Popconfirm>
          )}
          <Button
            size="small"
            icon={<DownloadOutlined />}
            onClick={async () => {
              try {
                await downloadPrPdf(String(r.PR_Number), String(r.Site_ID))
              } catch {
                message.error('PDF download failed')
              }
            }}
          >
            PDF
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <>
      <Space style={{ marginBottom: 12 }}>
        <Select
          allowClear
          placeholder="All sites"
          style={{ width: 180 }}
          value={siteId}
          onChange={setSiteId}
          options={(sites ?? []).map((s) => ({ value: s, label: s }))}
        />
      </Space>
      <Table
        size="small"
        loading={isFetching}
        columns={columns}
        dataSource={rows ?? []}
        rowKey={(r) => `${r.PR_Number}-${r.Site_ID}`}
        pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `${t} PRs` }}
      />
    </>
  )
}

export default function HodPrsPage() {
  const [tab, setTab] = useState('create')
  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Purchase Requests
      </Typography.Title>
      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          { key: 'create', label: 'Create PR', children: <NewPr /> },
          { key: 'submit', label: 'Submit to Logistics', children: <PrQueue /> },
        ]}
      />
    </div>
  )
}
