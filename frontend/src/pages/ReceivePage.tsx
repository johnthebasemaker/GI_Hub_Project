import { App, Button, Card, Col, DatePicker, Form, Input, InputNumber, Row, Select, Typography } from 'antd'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { useList, useReceiptEntry, useSites } from '../api/hooks'
import type { Row as ApiRow } from '../api/client'

interface FormValues {
  Site_ID: string
  SAP_Code: string
  Quantity: number
  Date: Dayjs
  Supplier?: string
  Expiry_Date?: Dayjs
  PR_Number?: string
  Lot_Number?: string
  Remarks?: string
}

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Request failed'
}

export default function ReceivePage() {
  const { message } = App.useApp()
  const [form] = Form.useForm<FormValues>()
  const { data: sites } = useSites()
  const inventory = useList('/inventory', { limit: 500 })
  const receipt = useReceiptEntry()

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
      Supplier: v.Supplier || null,
      Remarks: v.Remarks || null,
      Expiry_Date: v.Expiry_Date ? v.Expiry_Date.format('YYYY-MM-DD') : null,
      PR_Number: v.PR_Number || null,
      Lot_Number: v.Lot_Number || null,
    }
    try {
      const res = await receipt.mutateAsync(payload)
      const extra = [res.lot_number ? `lot ${res.lot_number}` : null, res.pr_status]
        .filter(Boolean)
        .join(' · ')
      message.success(`Receipt posted${extra ? ` (${extra})` : ''}`)
      form.resetFields(['SAP_Code', 'Quantity', 'Supplier', 'Expiry_Date', 'PR_Number', 'Lot_Number', 'Remarks'])
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Receive Stock
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Post a goods receipt to the ledger — auto-creates a lot when an expiry is set,
        and closes the linked PR once fully received (same rules as the old app).
      </Typography.Paragraph>

      <Card style={{ maxWidth: 760 }}>
        <Form<FormValues>
          form={form}
          layout="vertical"
          initialValues={{ Date: dayjs() }}
          onFinish={onFinish}
        >
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item name="Site_ID" label="Site" rules={[{ required: true }]}>
                <Select
                  placeholder="Select site"
                  options={(sites ?? []).map((s) => ({ value: s, label: s }))}
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={16}>
              <Form.Item name="SAP_Code" label="Material (SAP Code)" rules={[{ required: true }]}>
                <Select
                  showSearch
                  placeholder="Search material"
                  loading={inventory.isFetching}
                  optionFilterProp="label"
                  options={itemOptions}
                />
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
              <Form.Item name="Date" label="Receipt date" rules={[{ required: true }]}>
                <DatePicker style={{ width: '100%' }} format="YYYY-MM-DD" />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="Expiry_Date" label="Expiry date (optional)">
                <DatePicker style={{ width: '100%' }} format="YYYY-MM-DD" />
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item name="Supplier" label="Supplier">
                <Input placeholder="Supplier name" />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="PR_Number" label="PR Number (optional)">
                <Input placeholder="links + auto-closes PR" />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="Lot_Number" label="Lot Number (optional)">
                <Input placeholder="auto if expiry set" />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item name="Remarks" label="Remarks">
            <Input.TextArea rows={2} placeholder="Notes (optional)" />
          </Form.Item>

          <Button type="primary" htmlType="submit" loading={receipt.isPending}>
            Post receipt
          </Button>
        </Form>
      </Card>
    </div>
  )
}
