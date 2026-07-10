import { useMemo, useState } from 'react'
import {
  App, Button, Card, Col, DatePicker, Form, Input, InputNumber, Popconfirm, Row,
  Select, Space, Table, Tag, Typography, Upload,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { BarcodeOutlined, DeleteOutlined, EditOutlined, PaperClipOutlined, PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { useBulkEntry, useCategories, useList, useReceiptMeta, useSites } from '../api/hooks'
import { api } from '../api/client'
import type { Row as ApiRow } from '../api/client'
import ItemSnapshot from '../components/ItemSnapshot'
import QrScanner from '../components/QrScanner'
import { BARCODE_FORMATS, matchScanToSap } from '../lib/barcode'
import { loadDefaults, saveDefaults } from '../lib/smartDefaults'

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
  entry_uom?: string
  mtc_document_id?: number
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
  const [scanOpen, setScanOpen] = useState(false)

  const watchSap = Form.useWatch('SAP_Code', form)
  const watchSite = Form.useWatch('Site_ID', form)
  const watchMtc = Form.useWatch('mtc_document_id', form)
  const { data: meta } = useReceiptMeta(watchSap)

  // Category narrows the material picker (search stays available inside it).
  const { data: categories } = useCategories()
  const [category, setCategory] = useState<string | undefined>(undefined)
  const itemOptions = useMemo(() => (inventory.data?.items ?? [])
    .filter((r: ApiRow) => !category || String(r.Category ?? '').trim() === category)
    .map((r: ApiRow) => ({
      value: String(r.SAP_Code),
      label: `${r.SAP_Code} — ${r.Equipment_Description ?? ''}`,
    })), [inventory.data, category])
  const labelFor = (sap: string) =>
    itemOptions.find((o) => o.value === sap)?.label ?? sap

  const RESET_FIELDS: (keyof FormValues)[] = ['SAP_Code', 'Quantity', 'Supplier', 'Expiry_Date', 'PR_Number', 'Lot_Number', 'Remarks', 'entry_uom', 'mtc_document_id']

  // Barcode/QR pick: decoded text → SAP code → select it in the form.
  const onScan = (decoded: string) => {
    setScanOpen(false)
    const sap = matchScanToSap(decoded, inventory.data?.items ?? [])
    if (sap) {
      setCategory(undefined) // the scanned item may sit outside the filter
      form.setFieldsValue({ SAP_Code: sap })
      message.success(`Scanned: ${sap}`)
    } else {
      message.warning(`No material matches the scanned code "${decoded.slice(0, 60)}"`)
    }
  }

  const addToBatch = async () => {
    const v = await form.validateFields()
    if (meta?.is_rubber && !v.mtc_document_id) {
      message.error('This is a Rubber material — attach an MTC document before adding it')
      return
    }
    // Smart defaults: remember the routine fields for the next session.
    saveDefaults('receive', { Site_ID: v.Site_ID, Supplier: v.Supplier ?? '' })
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
      entry_uom: v.entry_uom || null,
      mtc_document_id: v.mtc_document_id ?? null,
    }
    setStaged((prev) => editingUid
      ? prev.map((r) => (r._uid === editingUid ? payload : r))
      : [...prev, payload])
    setEditingUid(null)
    form.resetFields(RESET_FIELDS)
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
      entry_uom: (r.entry_uom as string) ?? undefined,
      mtc_document_id: (r.mtc_document_id as number) ?? undefined,
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
      form.resetFields(RESET_FIELDS)
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
        <Form<FormValues> form={form} layout="vertical"
          initialValues={{ Date: dayjs(), ...loadDefaults('receive') }}>
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
              <Form.Item label="Material (SAP Code)" required style={{ marginBottom: 0 }}>
                <Space.Compact style={{ width: '100%' }}>
                  <Form.Item name="SAP_Code" noStyle rules={[{ required: true, message: 'Pick a material' }]}>
                    <Select showSearch placeholder="Search material" loading={inventory.isFetching} optionFilterProp="label" options={itemOptions} />
                  </Form.Item>
                  <Button icon={<BarcodeOutlined />} onClick={() => setScanOpen(true)}
                    aria-label="Scan a material barcode" title="Scan barcode / QR" />
                </Space.Compact>
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
          {/* Phase 6 receipt guards — pack→base UoM + Rubber MTC gate. */}
          {(meta?.conversions?.length || meta?.is_rubber) && (
            <Row gutter={16}>
              {!!meta?.conversions?.length && (
                <Col xs={24} md={8}>
                  <Form.Item name="entry_uom" label="Receive in unit"
                    tooltip="Pick a pack unit to auto-convert the quantity to the base unit at submit.">
                    <Select allowClear placeholder={`base: ${meta.base_uom ?? '—'}`}
                      options={[
                        ...(meta.base_uom ? [{ value: meta.base_uom, label: `${meta.base_uom} (base)` }] : []),
                        ...meta.conversions.map((c) => ({ value: c.Pack_UOM, label: `${c.Pack_UOM} (× ${c.Factor})` })),
                      ]} />
                  </Form.Item>
                </Col>
              )}
              {meta?.is_rubber && (
                <Col xs={24} md={16}>
                  <Form.Item label="MTC (Material Test Certificate) — required for Rubber"
                    required validateStatus={watchMtc ? 'success' : 'warning'}
                    help={watchMtc ? 'Attached' : 'A Rubber material cannot be added without an MTC'}>
                    <Space>
                      <Upload showUploadList={false} accept=".pdf,.jpg,.jpeg,.png"
                        customRequest={async ({ file, onSuccess, onError }) => {
                          const fd = new FormData()
                          fd.append('file', file as Blob)
                          fd.append('sap_code', String(watchSap ?? ''))
                          fd.append('site_id', String(watchSite ?? ''))
                          try {
                            const r = await api.post<{ id: number; file_name: string }>('/entry/mtc', fd)
                            form.setFieldValue('mtc_document_id', r.data.id)
                            message.success(`MTC attached: ${r.data.file_name}`)
                            onSuccess?.(r.data)
                          } catch (e) { message.error(errMsg(e)); onError?.(e as Error) }
                        }}>
                        <Button icon={<PaperClipOutlined />} disabled={!watchSap || !watchSite}>Upload MTC</Button>
                      </Upload>
                      {watchMtc ? <Tag color="green">MTC #{watchMtc} attached</Tag> : <Tag color="warning">no MTC</Tag>}
                    </Space>
                  </Form.Item>
                  <Form.Item name="mtc_document_id" hidden><InputNumber /></Form.Item>
                </Col>
              )}
            </Row>
          )}
          <Form.Item name="Remarks" label="Remarks"><Input.TextArea rows={2} placeholder="Notes (optional)" /></Form.Item>
          <Space>
            <Button type={editingUid ? 'primary' : 'default'} icon={<PlusOutlined />} onClick={addToBatch}>
              {editingUid ? 'Update line' : 'Add to batch'}
            </Button>
            {editingUid && <Button onClick={() => { setEditingUid(null); form.resetFields(RESET_FIELDS) }}>Cancel edit</Button>}
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

      <QrScanner open={scanOpen} title="Scan material barcode / QR"
        formats={BARCODE_FORMATS} manualPlaceholder="…or type the SAP code"
        onClose={() => setScanOpen(false)} onDecode={onScan} />
    </div>
  )
}
