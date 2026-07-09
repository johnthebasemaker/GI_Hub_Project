import { useState } from 'react'
import {
  Alert, App, Button, Card, Col, DatePicker, Form, Input, InputNumber, Modal, Popconfirm,
  Row, Select, Space, Table, Tabs, Tag, Typography, Upload,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { Dayjs } from 'dayjs'
import { DownloadOutlined, EditOutlined, InboxOutlined, MinusCircleOutlined, PlusOutlined } from '@ant-design/icons'
import { useAuth } from '../auth/AuthContext'
import { api } from '../api/client'
import {
  downloadPrPdf, useAutoDraftPr, useCreatePr, useEditPrLine, useHodPrLines, useHodPrs,
  useList, useRenamePr, useSites, useSubmitPr,
} from '../api/hooks'
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
  const autoDraft = useAutoDraftPr()
  const siteWatch = Form.useWatch('site_id', form)

  const itemOptions = (inventory.data?.items ?? []).map((r: ApiRow) => ({
    value: String(r.SAP_Code), label: `${r.SAP_Code} — ${r.Equipment_Description ?? ''}`,
  }))

  const doAutoDraft = async () => {
    if (!siteWatch) { message.warning('Pick a site first'); return }
    try {
      const res = await autoDraft.mutateAsync({ siteId: siteWatch })
      if (res.created === false) {
        message.info(res.reason ?? 'No items below minimum at this site')
      } else {
        message.success(`Auto-drafted PR ${res.pr_number} from ${res.lines} below-minimum item(s) — review it under "Submit to Logistics"`)
      }
    } catch (e) {
      message.error(errMsg(e))
    }
  }

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

        <Space>
          <Button type="primary" htmlType="submit" loading={create.isPending}>
            Create PR
          </Button>
          <Popconfirm
            title="Auto-draft a PR from below-minimum stock?"
            description="Creates one draft PR with a line for every item under its minimum at the selected site."
            onConfirm={doAutoDraft}
          >
            <Button loading={autoDraft.isPending} disabled={!siteWatch}>
              Auto-draft from low stock
            </Button>
          </Popconfirm>
        </Space>
      </Form>
    </Card>
  )
}

// ---- 📄 Import from PDF (Phase AI-2 preview-confirm) -------------------------
interface PrPreview {
  pr_number: string
  matched: { SAP_Code: string; Material_Code: string; Material_Name: string; UOM: string; Requested_Qty: number }[]
  unmatched: { material_code: string; qty: number; context: string }[]
}

function ImportPrPdf() {
  const { message } = App.useApp()
  const { user } = useAuth()
  const { data: sites } = useSites()
  const [preview, setPreview] = useState<PrPreview | null>(null)
  const [site, setSite] = useState<string | undefined>(user?.site_id || undefined)
  const create = useCreatePr()

  const patchQty = (i: number, v: number | null) =>
    setPreview((p) => p && ({
      ...p,
      matched: p.matched.map((m, idx) => (idx === i ? { ...m, Requested_Qty: v ?? 0 } : m)),
    }))

  const confirm = async () => {
    if (!preview || !site) return
    try {
      const res = await create.mutateAsync({
        site_id: site,
        notes: `Imported from PR PDF ${preview.pr_number}`,
        lines: preview.matched
          .filter((m) => m.Requested_Qty > 0)
          .map((m) => ({
            SAP_Code: m.SAP_Code, Requested_Qty: m.Requested_Qty,
            Material_Code: m.Material_Code, Material_Name: m.Material_Name,
          })),
      })
      message.success(`PR ${res.pr_number} created from PDF ${preview.pr_number} (${res.lines} lines)`)
      setPreview(null)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  return (
    <Card style={{ maxWidth: 900 }}>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        Upload a SAP Purchase Request PDF — items are extracted and matched against
        the inventory master. Nothing is saved until you review and confirm; the PR
        is then created through the normal audited path.
      </Typography.Paragraph>
      <Upload.Dragger accept=".pdf" maxCount={1} showUploadList={false}
        customRequest={async ({ file, onSuccess, onError }) => {
          const fd = new FormData()
          fd.append('file', file as Blob)
          try {
            const r = await api.post<PrPreview>('/ai/extract/pr', fd)
            setPreview(r.data)
            message.success(`Parsed PR ${r.data.pr_number}: ${r.data.matched.length} matched, `
              + `${r.data.unmatched.length} unmatched`)
            onSuccess?.(r.data)
          } catch (e) {
            message.error(errMsg(e))
            onError?.(e as Error)
          }
        }}>
        <p className="ant-upload-drag-icon"><InboxOutlined /></p>
        <p className="ant-upload-text">Drop the PR PDF here</p>
        <p className="ant-upload-hint">Word-stream extraction · strict Material-Code matching</p>
      </Upload.Dragger>

      {preview && (
        <div style={{ marginTop: 16 }}>
          <Typography.Title level={5}>
            PDF PR {preview.pr_number} — {preview.matched.length} matched line(s)
          </Typography.Title>
          <Table size="small" dataSource={preview.matched} rowKey={(r) => r.SAP_Code}
            pagination={false}
            columns={[
              { title: 'Material Code', dataIndex: 'Material_Code', width: 130 },
              { title: 'SAP', dataIndex: 'SAP_Code', width: 80 },
              { title: 'Description', dataIndex: 'Material_Name', ellipsis: true },
              { title: 'UOM', dataIndex: 'UOM', width: 70 },
              {
                title: 'Qty (editable)', key: 'q', width: 130,
                render: (_: unknown, r, i) => (
                  <InputNumber size="small" min={0} value={r.Requested_Qty}
                    onChange={(v) => patchQty(i, v)} style={{ width: 100 }} />
                ),
              },
            ] as ColumnsType<PrPreview['matched'][number]>} />
          {preview.unmatched.length > 0 && (
            <Alert type="warning" showIcon style={{ marginTop: 12 }}
              title={`${preview.unmatched.length} item(s) not in the Master Inventory — add them via Admin → Inventory first`}
              description={
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {preview.unmatched.map((u) => (
                    <li key={u.material_code}>
                      <code>{u.material_code}</code> × {u.qty} — <em>{u.context}</em>
                    </li>
                  ))}
                </ul>
              } />
          )}
          <Space style={{ marginTop: 16 }}>
            <Select placeholder="Site" style={{ width: 160 }} value={site} onChange={setSite}
              options={(sites ?? []).map((s) => ({ value: s, label: s }))} />
            <Button type="primary" disabled={!site || preview.matched.length === 0}
              loading={create.isPending} onClick={confirm}>
              Create PR from {preview.matched.filter((m) => m.Requested_Qty > 0).length} line(s)
            </Button>
            <Button onClick={() => setPreview(null)}>Discard</Button>
          </Space>
        </div>
      )}
    </Card>
  )
}

// Draft-PR line editor (deferred MED): edit a line before submission.
const PR_LINE_FIELDS: { name: string; numeric?: boolean }[] = [
  { name: 'Requested_Qty', numeric: true }, { name: 'Supplier' },
  { name: 'Est_Cost_SAR', numeric: true }, { name: 'UOM' }, { name: 'Notes' },
]
function PrLinesEditor({ pr, site, editable }: { pr: string; site?: string; editable: boolean }) {
  const { message } = App.useApp()
  const { data: lines } = useHodPrLines(pr, site)
  const edit = useEditPrLine()
  const [editing, setEditing] = useState<ApiRow | null>(null)
  const [form] = Form.useForm()

  const save = async () => {
    const vals = await form.validateFields()
    const changed: Record<string, unknown> = {}
    for (const f of PR_LINE_FIELDS) {
      if (vals[f.name] !== undefined && vals[f.name] !== editing?.[f.name]) changed[f.name] = vals[f.name]
    }
    if (!Object.keys(changed).length) { setEditing(null); return }
    try {
      await edit.mutateAsync({ id: Number(editing!.id), fields: changed })
      message.success('Line updated')
      setEditing(null)
    } catch (e) { message.error(errMsg(e)) }
  }

  const cols: ColumnsType<ApiRow> = [
    { title: 'SAP', dataIndex: 'SAP_Code' },
    { title: 'Material', dataIndex: 'Material_Name', ellipsis: true, render: (v) => v ?? '—' },
    { title: 'Qty', dataIndex: 'Requested_Qty', align: 'right', render: (v) => Number(v) },
    { title: 'UOM', dataIndex: 'UOM', render: (v) => v ?? '—' },
    { title: 'Supplier', dataIndex: 'Supplier', render: (v) => v ?? '—' },
    { title: 'Est SAR', dataIndex: 'Est_Cost_SAR', align: 'right', render: (v) => (v == null ? '—' : Number(v)) },
    ...(editable ? [{
      title: '', key: '__e', width: 60,
      render: (_: unknown, r: ApiRow) => (
        <Button size="small" icon={<EditOutlined />} onClick={() => { setEditing(r); form.setFieldsValue(r) }} />
      ),
    }] : []),
  ]
  return (
    <>
      <Table size="small" columns={cols} dataSource={lines ?? []} rowKey={(r) => String(r.id)} pagination={false} />
      <Modal open={!!editing} title={`Edit PR line #${editing?.id ?? ''}`} onOk={save}
        onCancel={() => setEditing(null)} confirmLoading={edit.isPending} destroyOnHidden>
        <Form form={form} layout="vertical" preserve={false}>
          {PR_LINE_FIELDS.map((f) => (
            <Form.Item key={f.name} name={f.name} label={f.name.replace(/_/g, ' ')}>
              {f.numeric ? <InputNumber min={0} style={{ width: '100%' }} /> : <Input />}
            </Form.Item>
          ))}
        </Form>
      </Modal>
    </>
  )
}

function PrQueue() {
  const { message } = App.useApp()
  const { data: sites } = useSites()
  const [siteId, setSiteId] = useState<string | undefined>(undefined)
  const { data: rows, isFetching } = useHodPrs(siteId)
  const submit = useSubmitPr()
  const rename = useRenamePr()
  const [renaming, setRenaming] = useState<ApiRow | null>(null)
  const [newPr, setNewPr] = useState('')

  const doSubmit = async (r: ApiRow) => {
    try {
      const res = await submit.mutateAsync({ pr: String(r.PR_Number), site: String(r.Site_ID) })
      message.success(`Submitted ${res.lines} line(s) of PR ${r.PR_Number} to Logistics`)
    } catch (e) {
      message.error(errMsg(e))
    }
  }
  const doRename = async () => {
    if (!renaming || !newPr.trim()) return
    try {
      const res = await rename.mutateAsync({ pr: String(renaming.PR_Number), site_id: String(renaming.Site_ID), new_pr: newPr.trim() })
      message.success(`Renamed to ${res.new_pr} (${res.lines} line(s))`)
      setRenaming(null); setNewPr('')
    } catch (e) { message.error(errMsg(e)) }
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
          {r.logistics_status === 'site_draft' && (
            <Button size="small" onClick={() => { setRenaming(r); setNewPr(String(r.PR_Number)) }}>Rename</Button>
          )}
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
      <Typography.Paragraph type="secondary" style={{ marginTop: -4 }}>
        Expand a <b>draft</b> PR to edit its lines; use Rename to change a draft PR number before submitting.
      </Typography.Paragraph>
      <Table
        size="small"
        loading={isFetching}
        columns={columns}
        dataSource={rows ?? []}
        rowKey={(r) => `${r.PR_Number}-${r.Site_ID}`}
        expandable={{
          expandedRowRender: (r) => (
            <PrLinesEditor pr={String(r.PR_Number)} site={String(r.Site_ID)}
              editable={r.logistics_status === 'site_draft'} />
          ),
        }}
        pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `${t} PRs` }}
      />
      <Modal open={!!renaming} title={`Rename PR ${renaming?.PR_Number ?? ''}`} onOk={doRename}
        onCancel={() => { setRenaming(null); setNewPr('') }} okText="Rename"
        okButtonProps={{ disabled: !newPr.trim() }} confirmLoading={rename.isPending} destroyOnHidden>
        <Input placeholder="New PR number" value={newPr} onChange={(e) => setNewPr(e.target.value)} />
      </Modal>
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
          { key: 'import', label: '📄 Import from PDF', children: <ImportPrPdf /> },
          { key: 'submit', label: 'Submit to Logistics', children: <PrQueue /> },
        ]}
      />
    </div>
  )
}
