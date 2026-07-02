import { App, Button, Card, Col, DatePicker, Form, Input, InputNumber, Row, Select, Typography } from 'antd'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { useConsumptionEntry, useList, useSites } from '../api/hooks'
import type { Row as ApiRow } from '../api/client'

interface FormValues {
  Site_ID: string
  SAP_Code: string
  Quantity: number
  Date: Dayjs
  Work_Type?: string
  Issued_To?: string
  Issued_By?: string
  PR_Number?: string
  Tank_No?: string
  Serial_No?: string
  Lot_Number?: string
  Remarks?: string
}

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Request failed'
}

export default function IssuePage() {
  const { message } = App.useApp()
  const [form] = Form.useForm<FormValues>()
  const { data: sites } = useSites()
  const inventory = useList('/inventory', { limit: 500 })
  const consume = useConsumptionEntry()

  const itemOptions = (inventory.data?.items ?? []).map((r: ApiRow) => ({
    value: String(r.SAP_Code),
    label: `${r.SAP_Code} — ${r.Equipment_Description ?? ''}`,
  }))

  const onFinish = async (v: FormValues) => {
    const payload: ApiRow = {
      Date: v.Date.format('YYYY-MM-DD'),
      SAP_Code: v.SAP_Code,
      Quantity: v.Quantity,
      Site_ID: v.Site_ID,
      Work_Type: v.Work_Type || null,
      Issued_To: v.Issued_To || null,
      Issued_By: v.Issued_By || null,
      PR_Number: v.PR_Number || null,
      Tank_No: v.Tank_No || null,
      Serial_No: v.Serial_No || null,
      Lot_Number: v.Lot_Number || null,
      Remarks: v.Remarks || null,
    }
    try {
      const res = await consume.mutateAsync(payload)
      message.success(res.message ?? 'Submitted for HOD approval')
      form.resetFields(['SAP_Code', 'Quantity', 'Issued_To', 'PR_Number', 'Tank_No', 'Serial_No', 'Lot_Number', 'Remarks'])
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Issue Stock (Consumption)
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Records a material issue. Leave Lot blank to auto-tag the earliest-expiry open
        lot (FEFO). Over-issue is allowed and logged (not blocked) — same as the old app.
      </Typography.Paragraph>

      <Card style={{ maxWidth: 820 }}>
        <Form<FormValues> form={form} layout="vertical" initialValues={{ Date: dayjs() }} onFinish={onFinish}>
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item name="Site_ID" label="Site" rules={[{ required: true }]}>
                <Select placeholder="Select site" options={(sites ?? []).map((s) => ({ value: s, label: s }))} />
              </Form.Item>
            </Col>
            <Col xs={24} md={16}>
              <Form.Item name="SAP_Code" label="Material (SAP Code)" rules={[{ required: true }]}>
                <Select showSearch placeholder="Search material" loading={inventory.isFetching} optionFilterProp="label" options={itemOptions} />
              </Form.Item>
            </Col>
          </Row>
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
              <Form.Item name="Lot_Number" label="Lot (optional)">
                <Input placeholder="blank → FEFO auto-pick" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item name="Work_Type" label="Work Type"><Input placeholder="e.g. Maintenance" /></Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="Issued_To" label="Issued To"><Input placeholder="recipient / crew" /></Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="Issued_By" label="Issued By"><Input placeholder="issuer" /></Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col xs={24} md={8}><Form.Item name="PR_Number" label="PR Number"><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name="Tank_No" label="Tank No"><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name="Serial_No" label="Serial No"><Input /></Form.Item></Col>
          </Row>
          <Form.Item name="Remarks" label="Remarks"><Input.TextArea rows={2} /></Form.Item>
          <Button type="primary" htmlType="submit" loading={consume.isPending}>Post issue</Button>
        </Form>
      </Card>
    </div>
  )
}
