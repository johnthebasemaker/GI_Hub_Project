import { useState } from 'react'
import { App, Button, Card, Col, DatePicker, Form, Input, InputNumber, Row, Select, Typography } from 'antd'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { useCategories, useList, useReturnEntry, useSites } from '../api/hooks'
import type { Row as ApiRow } from '../api/client'
import ItemSnapshot from '../components/ItemSnapshot'

interface FormValues {
  Site_ID: string
  SAP_Code: string
  Quantity: number
  Date: Dayjs
  Reason?: string
  Remarks?: string
}

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Request failed'
}

const REASONS = ['defect', 'damage', 'overstock', 'unused', 'return_to_supplier', 'other']

export default function ReturnPage() {
  const { message } = App.useApp()
  const [form] = Form.useForm<FormValues>()
  const { data: sites } = useSites()
  const inventory = useList('/inventory', { limit: 500 })
  const ret = useReturnEntry()
  const watchSap = Form.useWatch('SAP_Code', form)
  const watchSite = Form.useWatch('Site_ID', form)

  const { data: categories } = useCategories()
  const [category, setCategory] = useState<string | undefined>(undefined)
  const itemOptions = (inventory.data?.items ?? [])
    .filter((r: ApiRow) => !category || String(r.Category ?? '').trim() === category)
    .map((r: ApiRow) => ({
      value: String(r.SAP_Code),
      label: `${r.SAP_Code} — ${r.Equipment_Description ?? ''}`,
    }))

  const onFinish = async (v: FormValues) => {
    const payload: ApiRow = {
      Date: v.Date.format('YYYY-MM-DD'),
      SAP_Code: v.SAP_Code,
      Quantity: v.Quantity,
      Site_ID: v.Site_ID,
      Reason: v.Reason || null,
      Remarks: v.Remarks || null,
    }
    try {
      const res = await ret.mutateAsync(payload)
      message.success(res.message ?? 'Submitted for HOD approval')
      form.resetFields(['SAP_Code', 'Quantity', 'Reason', 'Remarks'])
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Return Stock
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Records a return to the returns ledger — reduces current stock (same identity as the old app).
      </Typography.Paragraph>

      <Card style={{ maxWidth: 760 }}>
        <Form<FormValues> form={form} layout="vertical" initialValues={{ Date: dayjs() }} onFinish={onFinish}>
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item name="Site_ID" label="Site" rules={[{ required: true }]}>
                <Select placeholder="Select site" options={(sites ?? []).map((s) => ({ value: s, label: s }))} />
              </Form.Item>
            </Col>
            <Col xs={24} md={5}>
              <Form.Item label="Category">
                <Select allowClear showSearch placeholder="All" value={category}
                  onChange={(v) => setCategory(v)}
                  options={(categories ?? []).map((c) => ({ value: c, label: c }))} />
              </Form.Item>
            </Col>
            <Col xs={24} md={11}>
              <Form.Item name="SAP_Code" label="Material (SAP Code)" rules={[{ required: true }]}>
                <Select showSearch placeholder="Search material" loading={inventory.isFetching} optionFilterProp="label" options={itemOptions} />
              </Form.Item>
            </Col>
          </Row>

          {/* Current stock + 30-day trend for the picked material (advisory). */}
          <ItemSnapshot sap={watchSap} site={watchSite} />

          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item name="Quantity" label="Quantity" rules={[{ required: true }]}>
                <InputNumber min={0.0001} style={{ width: '100%' }} placeholder="0" />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="Date" label="Date" rules={[{ required: true }]}>
                <DatePicker style={{ width: '100%' }} format="YYYY-MM-DD" />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="Reason" label="Reason">
                <Select allowClear placeholder="reason" options={REASONS.map((r) => ({ value: r, label: r }))} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="Remarks" label="Remarks"><Input.TextArea rows={2} /></Form.Item>
          <Button type="primary" htmlType="submit" loading={ret.isPending}>Post return</Button>
        </Form>
      </Card>
    </div>
  )
}
