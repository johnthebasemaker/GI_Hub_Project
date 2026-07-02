import { App, Button, Card, Col, Form, Input, InputNumber, Row, Select, Typography } from 'antd'
import { useAdjustmentEntry, useAdjustmentReasons, useList, useSites } from '../api/hooks'
import type { Row as ApiRow } from '../api/client'

interface FormValues {
  Site_ID: string
  SAP_Code: string
  system_qty: number
  counted_qty: number
  reason_code: string
  Lot_Number?: string
  notes?: string
}

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Request failed'
}

export default function AdjustPage() {
  const { message } = App.useApp()
  const [form] = Form.useForm<FormValues>()
  const { data: sites } = useSites()
  const inventory = useList('/inventory', { limit: 500 })
  const { data: reasons } = useAdjustmentReasons()
  const adjust = useAdjustmentEntry()

  const itemOptions = (inventory.data?.items ?? []).map((r: ApiRow) => ({
    value: String(r.SAP_Code),
    label: `${r.SAP_Code} — ${r.Equipment_Description ?? ''}`,
  }))
  const reasonOptions = Object.entries(reasons ?? {}).map(([value, label]) => ({ value, label }))

  const onFinish = async (v: FormValues) => {
    const payload: ApiRow = {
      SAP_Code: v.SAP_Code,
      Site_ID: v.Site_ID,
      system_qty: v.system_qty,
      counted_qty: v.counted_qty,
      reason_code: v.reason_code,
      Lot_Number: v.Lot_Number || null,
      notes: v.notes || null,
    }
    try {
      const res = await adjust.mutateAsync(payload)
      const dir = res.variance >= 0 ? `surplus +${res.variance}` : `shortage ${res.variance}`
      message.success(`Adjustment posted (${dir}, ${res.posted})`)
      form.resetFields(['SAP_Code', 'system_qty', 'counted_qty', 'reason_code', 'Lot_Number', 'notes'])
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Stock Adjustment
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Posts a physical-count correction: a surplus becomes a receipt, a shortage a
        consumption (tagged STOCK_ADJUSTMENT). Set a lot to write it off (disposed).
      </Typography.Paragraph>

      <Card style={{ maxWidth: 820 }}>
        <Form<FormValues> form={form} layout="vertical" onFinish={onFinish}>
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
              <Form.Item name="system_qty" label="System qty" rules={[{ required: true }]}>
                <InputNumber style={{ width: '100%' }} placeholder="on-system" />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="counted_qty" label="Counted qty" rules={[{ required: true }]}>
                <InputNumber style={{ width: '100%' }} placeholder="physically counted" />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="reason_code" label="Reason" rules={[{ required: true }]}>
                <Select placeholder="reason" options={reasonOptions} />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item name="Lot_Number" label="Lot (optional — disposes lot)">
                <Input placeholder="write off a lot" />
              </Form.Item>
            </Col>
            <Col xs={24} md={16}>
              <Form.Item name="notes" label="Notes"><Input placeholder="context" /></Form.Item>
            </Col>
          </Row>
          <Button type="primary" htmlType="submit" loading={adjust.isPending}>Post adjustment</Button>
        </Form>
      </Card>
    </div>
  )
}
