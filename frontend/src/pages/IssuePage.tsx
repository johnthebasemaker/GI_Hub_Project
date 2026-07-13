import { useMemo, useState } from 'react'
import {
  App, Button, Card, Col, DatePicker, Form, Input, InputNumber, Popconfirm, Row,
  Select, Space, Table, Tag, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { BarcodeOutlined, DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { useBins, useBulkEntry, useCategories, useDocsRequired, useList, useSites, useWbsOptions } from '../api/hooks'
import type { Row as ApiRow } from '../api/client'
import DeliveryPrefRadio from '../components/DeliveryPrefRadio'
import DraftBanner from '../components/DraftBanner'
import EntryDocsUpload from '../components/EntryDocsUpload'
import type { EntryDoc } from '../components/EntryDocsUpload'
import { useFormDraft } from '../lib/formDraft'
import ItemSnapshot from '../components/ItemSnapshot'
import QrScanner from '../components/QrScanner'
import { BARCODE_FORMATS, matchScanToSap } from '../lib/barcode'
import { loadDefaults, saveDefaults } from '../lib/smartDefaults'

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
  wbs?: string
  FEFO_Override?: string
}

// A batched line: API-shaped payload + a local uid + a display label.
interface StagedRow extends ApiRow {
  _uid: string
  _label: string
}

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Request failed'
}

let _seq = 0

export default function IssuePage() {
  const { message } = App.useApp()
  const [form] = Form.useForm<FormValues>()
  const { data: sites } = useSites()
  const inventory = useList('/inventory', { limit: 500 })
  const bulk = useBulkEntry('consumption', ['/consumption'])
  const [staged, setStaged] = useState<StagedRow[]>([])
  const [editingUid, setEditingUid] = useState<string | null>(null)
  const [scanOpen, setScanOpen] = useState(false)
  const [docs, setDocs] = useState<EntryDoc[]>([])
  const draft = useFormDraft(form, 'issue')

  const watchSap = Form.useWatch('SAP_Code', form)
  const watchSite = Form.useWatch('Site_ID', form)
  const watchLot = Form.useWatch('Lot_Number', form)
  const { data: bins } = useBins(watchSap, watchSite)
  const { data: wbsOptions } = useWbsOptions(watchSite)
  const { data: docsRequired } = useDocsRequired()

  // Category narrows the material picker (search stays available inside it).
  const { data: categories } = useCategories()
  const [category, setCategory] = useState<string | undefined>(undefined)
  const itemOptions = useMemo(() => (inventory.data?.items ?? [])
    .filter((r: ApiRow) => !category || String(r.Category ?? '').trim() === category)
    .map((r: ApiRow) => ({
      value: String(r.SAP_Code),
      label: `${r.SAP_Code} — ${r.Equipment_Description ?? ''}`,
    })), [inventory.data, category])
  const labelFor = (sap: string) => itemOptions.find((o) => o.value === sap)?.label ?? sap

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

  // Add the current form to the batch (or update the line being edited).
  const addToBatch = async () => {
    const v = await form.validateFields()
    // Smart defaults: remember the routine fields for the next session.
    saveDefaults('issue', { Site_ID: v.Site_ID, Work_Type: v.Work_Type ?? '', Issued_By: v.Issued_By ?? '' })
    const payload: StagedRow = {
      _uid: editingUid ?? `r${++_seq}`,
      _label: labelFor(v.SAP_Code),
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
      wbs: v.wbs || null,
      // Parity B1 — a manual lot pick is a FEFO override; the reason travels
      // to the HOD (allow-and-log ruling: never blocks).
      FEFO_Override: v.Lot_Number ? (v.FEFO_Override || 'manual lot (no reason given)') : null,
    }
    setStaged((prev) => editingUid
      ? prev.map((r) => (r._uid === editingUid ? payload : r))
      : [...prev, payload])
    setEditingUid(null)
    // Keep Site + Date for the next line; clear the item-specific fields.
    form.resetFields(['SAP_Code', 'Quantity', 'Issued_To', 'PR_Number', 'Tank_No', 'Serial_No', 'Lot_Number', 'Remarks'])
  }

  const editLine = (r: StagedRow) => {
    setEditingUid(r._uid)
    form.setFieldsValue({
      Site_ID: r.Site_ID as string, SAP_Code: r.SAP_Code as string,
      Quantity: r.Quantity as number, Date: dayjs(r.Date as string),
      Work_Type: (r.Work_Type as string) ?? undefined, Issued_To: (r.Issued_To as string) ?? undefined,
      Issued_By: (r.Issued_By as string) ?? undefined, PR_Number: (r.PR_Number as string) ?? undefined,
      Tank_No: (r.Tank_No as string) ?? undefined, Serial_No: (r.Serial_No as string) ?? undefined,
      Lot_Number: (r.Lot_Number as string) ?? undefined, Remarks: (r.Remarks as string) ?? undefined,
    })
  }

  const removeLine = (uid: string) => {
    setStaged((prev) => prev.filter((r) => r._uid !== uid))
    if (editingUid === uid) setEditingUid(null)
  }

  const submitBatch = async () => {
    if (!staged.length) return
    if (docsRequired !== false && !docs.length) {
      message.error('Attach a supporting document (hand-written note / delivery note) before submitting')
      return
    }
    const rows = staged.map(({ _uid, _label, ...rest }) => { void _uid; void _label; return rest })
    try {
      const res = await bulk.mutateAsync({ rows, attachment_ids: docs.map((d) => d.id) })
      if (res.queued) message.warning(`Offline — ${res.staged} issue line(s) saved to the sync queue`)
      else message.success(`${res.staged} issue line(s) submitted for HOD approval`)
      setStaged([])
      setDocs([])
      draft.clear()
      form.resetFields(['SAP_Code', 'Quantity', 'Issued_To', 'PR_Number', 'Tank_No', 'Serial_No', 'Lot_Number', 'Remarks'])
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<StagedRow> = [
    { title: 'Material', dataIndex: '_label', ellipsis: true },
    { title: 'Qty', dataIndex: 'Quantity', align: 'right', width: 80 },
    { title: 'Work Type', dataIndex: 'Work_Type', width: 120, render: (v) => v ?? '—' },
    { title: 'Issued To', dataIndex: 'Issued_To', width: 120, render: (v) => v ?? '—' },
    { title: 'Lot', dataIndex: 'Lot_Number', width: 110, render: (v) => v ?? 'FEFO' },
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
      <Typography.Title level={3} style={{ marginTop: 0 }}>Issue Stock (Consumption)</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Add each material to the batch, review the list, then submit them all at once for HOD
        approval. Leave Lot blank to auto-tag the earliest-expiry open lot (FEFO). Over-issue is
        allowed and logged (not blocked) — same as the old app.
      </Typography.Paragraph>

      <DraftBanner hasDraft={draft.hasDraft} onRestore={draft.restore} onDiscard={draft.discard} />
      <Card style={{ maxWidth: 860, marginBottom: 16 }}>
        <Form<FormValues> form={form} layout="vertical"
          onValuesChange={draft.onValuesChange}
          initialValues={{ Date: dayjs(), ...loadDefaults('issue') }}>
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

          {/* Current stock + 30-day trend for the picked material (advisory). */}
          <ItemSnapshot sap={watchSap} site={watchSite} />

          {!!bins?.length && (
            <div style={{ marginTop: -4, marginBottom: 12 }}>
              <Typography.Text type="secondary" style={{ marginRight: 6 }}>Pull from bin:</Typography.Text>
              {bins.map((b) => <Tag key={b} color="blue">{b}</Tag>)}
            </div>
          )}

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
            {!!wbsOptions?.length && (
              <Col xs={24} md={8}>
                <Form.Item name="wbs" label="WBS Number"
                  rules={[{ required: true, message: 'This site requires a WBS' }]}>
                  <Select showSearch placeholder="Pick WBS"
                    options={wbsOptions.map((w) => ({ value: w, label: w }))} />
                </Form.Item>
              </Col>
            )}
            {!!watchLot && (
              <Col xs={24} md={8}>
                <Form.Item name="FEFO_Override" label="Reason for manual lot (FEFO override)"
                  rules={[{ min: 5, message: 'Give at least 5 characters' }]}>
                  <Input placeholder="why not the FEFO lot?" />
                </Form.Item>
              </Col>
            )}
          </Row>
          <Row gutter={16}>
            <Col xs={24} md={8}><Form.Item name="Work_Type" label="Work Type"><Input placeholder="e.g. Maintenance" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name="Issued_To" label="Issued To"><Input placeholder="recipient / crew" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name="Issued_By" label="Issued By"><Input placeholder="issuer" /></Form.Item></Col>
          </Row>
          <Row gutter={16}>
            <Col xs={24} md={8}><Form.Item name="PR_Number" label="PR Number"><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name="Tank_No" label="Tank No"><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name="Serial_No" label="Serial No"><Input /></Form.Item></Col>
          </Row>
          <Form.Item name="Remarks" label="Remarks"><Input.TextArea rows={2} /></Form.Item>
          <Space>
            <Button type={editingUid ? 'primary' : 'default'} icon={<PlusOutlined />} onClick={addToBatch}>
              {editingUid ? 'Update line' : 'Add to batch'}
            </Button>
            {editingUid && <Button onClick={() => { setEditingUid(null); form.resetFields(['SAP_Code', 'Quantity', 'Issued_To', 'PR_Number', 'Tank_No', 'Serial_No', 'Lot_Number', 'Remarks']) }}>Cancel edit</Button>}
          </Space>
        </Form>
      </Card>

      <Card
        title={`Batch (${staged.length} line${staged.length === 1 ? '' : 's'})`}
        extra={
          <Space size={16} wrap>
            <DeliveryPrefRadio />
            <Button type="primary" disabled={!staged.length} loading={bulk.isPending} onClick={submitBatch}>
              Submit batch to HOD
            </Button>
          </Space>
        }
      >
        <EntryDocsUpload docType="consumption" siteId={watchSite}
          value={docs} onChange={setDocs} required={docsRequired !== false} />
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
