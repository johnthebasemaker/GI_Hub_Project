import { useMemo, useState } from 'react'
import {
  App, Button, Card, Col, DatePicker, Form, Input, InputNumber, Popconfirm, Row,
  Select, Space, Table, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { useBulkEntry, useList, useSites } from '../api/hooks'
import type { Row as ApiRow } from '../api/client'
import ItemSnapshot from '../components/ItemSnapshot'

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

interface StagedRow extends ApiRow {
  _uid: string
  _label: string
}

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Request failed'
}

let _seq = 0

export default function ReceivePage() {
  const { message } = App.useApp()
  const [form] = Form.useForm<FormValues>()
  const { data: sites } = useSites()
  const inventory = useList('/inventory', { limit: 500 })
  const bulk = useBulkEntry('receipt', ['/receipts'])
  const [staged, setStaged] = useState<StagedRow[]>([])
  const [editingUid, setEditingUid] = useState<string | null>(null)

  const watchSap = Form.useWatch('SAP_Code', form)
  const watchSite = Form.useWatch('Site_ID', form)

  const itemOptions = useMemo(() => (inventory.data?.items ?? []).map((r: ApiRow) => ({
    value: String(r.SAP_Code),
    label: `${r.SAP_Code} — ${r.Equipment_Description ?? ''}`,
  })), [inventory.data])
  const labelFor = (sap: string) => itemOptions.find((o) => o.value === sap)?.label ?? sap

  const addToBatch = async () => {
    const v = await form.validateFields()
    const payload: StagedRow = {
      _uid: editingUid ?? `r${++_seq}`,
      _label: labelFor(v.SAP_Code),
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
    setStaged((prev) => editingUid
      ? prev.map((r) => (r._uid === editingUid ? payload : r))
      : [...prev, payload])
    setEditingUid(null)
    form.resetFields(['SAP_Code', 'Quantity', 'Supplier', 'Expiry_Date', 'PR_Number', 'Lot_Number', 'Remarks'])
  }

  const editLine = (r: StagedRow) => {
    setEditingUid(r._uid)
    form.setFieldsValue({
      Site_ID: r.Site_ID as string, SAP_Code: r.SAP_Code as string,
      Quantity: r.Quantity as number, Date: dayjs(r.Date as string),
      Supplier: (r.Supplier as string) ?? undefined,
      Expiry_Date: r.Expiry_Date ? dayjs(r.Expiry_Date as string) : undefined,
      PR_Number: (r.PR_Number as string) ?? undefined,
      Lot_Number: (r.Lot_Number as string) ?? undefined,
      Remarks: (r.Remarks as string) ?? undefined,
    })
  }

  const removeLine = (uid: string) => {
    setStaged((prev) => prev.filter((r) => r._uid !== uid))
    if (editingUid === uid) setEditingUid(null)
  }

  const submitBatch = async () => {
    if (!staged.length) return
    const rows = staged.map(({ _uid, _label, ...rest }) => { void _uid; void _label; return rest })
    try {
      const res = await bulk.mutateAsync(rows)
      message.success(`${res.staged} receipt line(s) submitted for HOD approval`)
      setStaged([])
      form.resetFields(['SAP_Code', 'Quantity', 'Supplier', 'Expiry_Date', 'PR_Number', 'Lot_Number', 'Remarks'])
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<StagedRow> = [
    { title: 'Material', dataIndex: '_label', ellipsis: true },
    { title: 'Qty', dataIndex: 'Quantity', align: 'right', width: 80 },
    { title: 'Supplier', dataIndex: 'Supplier', width: 130, render: (v) => v ?? '—' },
    { title: 'Expiry', dataIndex: 'Expiry_Date', width: 110, render: (v) => v ?? '—' },
    { title: 'PR', dataIndex: 'PR_Number', width: 100, render: (v) => v ?? '—' },
    {
      title: '', key: '_act', width: 90, align: 'right',
      render: (_: unknown, r: StagedRow) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => editLine(r)} />
          <Popconfirm title="Remove this line?" onConfirm={() => removeLine(r._uid)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Receive Stock</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Add each goods receipt to the batch, review the list, then submit them all at once for
        HOD approval — auto-creates a lot when an expiry is set, and closes the linked PR once
        fully received (same rules as the old app).
      </Typography.Paragraph>

      <Card style={{ maxWidth: 820, marginBottom: 16 }}>
        <Form<FormValues> form={form} layout="vertical" initialValues={{ Date: dayjs() }}>
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

          <ItemSnapshot sap={watchSap} site={watchSite} />

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
            <Col xs={24} md={8}><Form.Item name="Supplier" label="Supplier"><Input placeholder="Supplier name" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name="PR_Number" label="PR Number (optional)"><Input placeholder="links + auto-closes PR" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name="Lot_Number" label="Lot Number (optional)"><Input placeholder="auto if expiry set" /></Form.Item></Col>
          </Row>
          <Form.Item name="Remarks" label="Remarks"><Input.TextArea rows={2} placeholder="Notes (optional)" /></Form.Item>
          <Space>
            <Button type={editingUid ? 'primary' : 'default'} icon={<PlusOutlined />} onClick={addToBatch}>
              {editingUid ? 'Update line' : 'Add to batch'}
            </Button>
            {editingUid && <Button onClick={() => { setEditingUid(null); form.resetFields(['SAP_Code', 'Quantity', 'Supplier', 'Expiry_Date', 'PR_Number', 'Lot_Number', 'Remarks']) }}>Cancel edit</Button>}
          </Space>
        </Form>
      </Card>

      <Card
        title={`Batch (${staged.length} line${staged.length === 1 ? '' : 's'})`}
        extra={
          <Button type="primary" disabled={!staged.length} loading={bulk.isPending} onClick={submitBatch}>
            Submit batch to HOD
          </Button>
        }
      >
        <Table<StagedRow> size="small" rowKey="_uid" columns={columns} dataSource={staged}
          pagination={false}
          locale={{ emptyText: 'No lines yet — add materials above, then submit them all at once.' }} />
      </Card>
    </div>
  )
}
