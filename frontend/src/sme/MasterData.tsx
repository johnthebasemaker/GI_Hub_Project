/**
 * SME Phase S6 (cutover day) — Master Data tab: CRUD over the sme_* masters.
 *
 * Port of the legacy Tab 8: Equipment / Recipes (LINING SYSTEM MATERIAL
 * CONSM) / Materials seed / SQM progress / Location & Type dropdowns.
 * Site-scoped entities (equipment, progress, dropdowns) need a site: HODs are
 * pinned server-side; admins must pick one in the page header first.
 * Materials edit the SME-owned seed only — derived availability columns are
 * read-only by design (Canon Rule 2).
 */
import { useMemo, useState } from 'react'
import {
  Alert, App, Button, Card, Col, Form, Input, InputNumber, List, Modal,
  Popconfirm, Row as GridRow, Space, Table, Tabs, Typography,
} from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import type { Row } from '../api/client'
import {
  useSmeMasterCreate, useSmeMasterDelete, useSmeMasterList, useSmeMasterPatch,
  useSmeMasterSettings, useSmeProgressUpsert, useSmeSettingAdd, useSmeSettingDelete,
} from '../api/hooks'
import { useAuth } from '../auth/AuthContext'
import { buildColumns } from '../lib/columns'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Request failed'
}

interface FieldDef {
  name: string       // API field name (PascalCase, as the router expects)
  label: string
  required?: boolean
  number?: boolean
  from?: string      // row key when the list payload uses a different casing
}

const EQUIPMENT_FIELDS: FieldDef[] = [
  { name: 'Equipment_Tag_No', label: 'Equipment Tag No.', required: true },
  { name: 'Lining_System_Code', label: 'Lining System Code', required: true },
  { name: 'Surface_Area_SQM', label: 'Surface Area SQM', required: true, number: true },
  { name: 'Name', label: 'Name' },
  { name: 'Location', label: 'Location' },
  { name: 'Type', label: 'Type' },
  { name: 'Substrate', label: 'Substrate' },
  { name: 'Lining_System_Short_Name', label: 'System Short Name' },
  { name: 'Lining_Type', label: 'Lining Type' },
  { name: 'Lining_System', label: 'Lining System' },
  { name: 'Material_Spec', label: 'Material Spec.' },
  { name: 'Design', label: 'Design' },
  { name: 'Lining_Area_Location', label: 'Lining Area / Location' },
  { name: 'Sub_Location', label: 'Sub Location' },
  { name: 'Project', label: 'Project' },
  { name: 'WBS_No', label: 'WBS #' },
  { name: 'IO_No', label: 'IO #' },
  { name: 'Drawing_No', label: 'Drawing #' },
  { name: 'Dia_L', label: 'Dia / L' },
  { name: 'Ht_W', label: 'Ht. / W' },
  { name: 'Equipment_Total_SQM', label: 'Equipment Total SQM', number: true },
  { name: 'Sl_No', label: 'Sl. #' },
  { name: 'Remaraks', label: 'Remarks' },
]

const RECIPE_FIELDS: FieldDef[] = [
  { name: 'Lining_System_Code', label: 'Lining System Code', required: true },
  { name: 'Material_Code', label: 'Material Code', required: true },
  { name: 'For_1_SQM', label: 'For 1 SQM', required: true, number: true },
  { name: 'Lining_System_Name', label: 'System Short Name' },
  { name: 'Material_Name', label: 'Material Name' },
  { name: 'Material_Description', label: 'Material Description' },
  { name: 'UOM', label: 'UOM' },
  { name: 'Nature', label: 'Nature' },
  { name: 'Substrate', label: 'Substrate' },
  { name: 'System_Keys', label: 'System Keys' },
  { name: 'Lining_Thickness', label: 'Lining Thickness' },
  { name: 'Lining_System', label: 'Lining System' },
  { name: 'Lining_Type', label: 'Lining Type' },
  { name: 'Package_Size', label: 'Package Size' },
  { name: 'Sl_No', label: 'Sl. #' },
]

// The materials list is the derived view (lowercase keys); the writes take the
// seed's PascalCase columns. `from` maps grid row → form value on edit.
const MATERIAL_FIELDS: FieldDef[] = [
  { name: 'Material_Code', label: 'Material Code', required: true, from: 'material_code' },
  { name: 'Material_Name', label: 'Material Name', from: 'material_name' },
  { name: 'Item', label: 'Item', from: 'item' },
  { name: 'Vendor', label: 'Vendor', from: 'vendor' },
  { name: 'Purchasing_Document', label: 'Purchasing Document', from: 'purchasing_document' },
  { name: 'Document_Date', label: 'Document Date', from: 'document_date' },
  { name: 'Nature', label: 'Nature', from: 'nature' },
  { name: 'UOM', label: 'UOM', from: 'uom' },
  { name: 'Initial_Available_Qty', label: 'Initial Available Qty', number: true, from: 'initial_available_qty' },
  { name: 'Initial_Ordered_Qty', label: 'Initial Ordered Qty', number: true, from: 'initial_ordered_qty' },
]

function FieldInputs({ fields, lockNames }: { fields: FieldDef[]; lockNames?: string[] }) {
  return (
    <GridRow gutter={12}>
      {fields.map((f) => (
        <Col span={f.number ? 12 : 12} key={f.name}>
          <Form.Item
            name={f.name}
            label={f.label}
            rules={f.required ? [{ required: true, message: `${f.label} is required` }] : []}
          >
            {f.number
              ? <InputNumber style={{ width: '100%' }} min={0} disabled={lockNames?.includes(f.name)} />
              : <Input disabled={lockNames?.includes(f.name)} />}
          </Form.Item>
        </Col>
      ))}
    </GridRow>
  )
}

interface CrudTabProps {
  kind: 'equipment' | 'recipes' | 'materials'
  fields: FieldDef[]
  idKey: string
  siteId?: string
  needsSite: boolean
  siteMissing: boolean
  deleteWarning?: string
  // materials: PK is immutable on edit; create is an upsert on the PK.
  lockOnEdit?: string[]
}

function CrudTab({ kind, fields, idKey, siteId, needsSite, siteMissing,
                   deleteWarning, lockOnEdit }: CrudTabProps) {
  const { message } = App.useApp()
  const list = useSmeMasterList(kind, siteId)
  const create = useSmeMasterCreate(kind)
  const patch = useSmeMasterPatch(kind)
  const del = useSmeMasterDelete(kind)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Row | null>(null)
  const [form] = Form.useForm()

  const rows = list.data ?? []
  const blocked = needsSite && siteMissing

  const openAdd = () => { setEditing(null); form.resetFields(); setOpen(true) }
  const openEdit = (r: Row) => {
    setEditing(r)
    form.resetFields()
    form.setFieldsValue(Object.fromEntries(
      fields.map((f) => [f.name, r[f.from ?? f.name]])))
    setOpen(true)
  }

  const submit = async () => {
    const values = (await form.validateFields()) as Row
    const body: Row = {}
    for (const f of fields) {
      const v = values[f.name]
      if (v !== undefined && v !== null && v !== '') body[f.name] = v
    }
    if (needsSite && siteId) body.site_id = siteId
    try {
      if (editing) {
        for (const locked of lockOnEdit ?? []) delete body[locked]
        await patch.mutateAsync({ id: editing[idKey] as string | number, body })
        message.success('Updated')
      } else {
        await create.mutateAsync(body)
        message.success('Saved')
      }
      setOpen(false)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const remove = async (r: Row) => {
    try {
      await del.mutateAsync(r[idKey] as string | number)
      message.success('Deleted')
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = useMemo(() => [
    ...buildColumns(rows),
    {
      title: 'Actions', key: '__actions', fixed: 'right', width: 150,
      render: (_: unknown, r: Row) => (
        <Space>
          <Button size="small" onClick={() => openEdit(r)}>Edit</Button>
          <Popconfirm title={deleteWarning ?? 'Delete this row?'} onConfirm={() => remove(r)}>
            <Button size="small" danger>Delete</Button>
          </Popconfirm>
        </Space>
      ),
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [rows, deleteWarning])

  return (
    <div>
      {blocked && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          title="Pick a site in the page header to edit site-scoped master data." />
      )}
      <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}
        disabled={blocked} style={{ marginBottom: 12 }}>
        Add
      </Button>
      <Table sticky={{ offsetHeader: 64 }}
        size="small"
        loading={list.isFetching}
        columns={columns}
        dataSource={rows.map((r, i) => ({ ...r, __rk: r[idKey] ?? i }))}
        rowKey="__rk"
        scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `${t} rows` }}
      />
      <Modal
        open={open}
        forceRender
        width={720}
        title={editing ? `Edit ${kind}` : `Add ${kind}`}
        onCancel={() => setOpen(false)}
        onOk={submit}
        confirmLoading={create.isPending || patch.isPending}
      >
        <Form form={form} layout="vertical">
          <FieldInputs fields={fields} lockNames={editing ? lockOnEdit : undefined} />
        </Form>
      </Modal>
    </div>
  )
}

function ProgressTab({ siteId, siteMissing }: { siteId?: string; siteMissing: boolean }) {
  const { message } = App.useApp()
  const list = useSmeMasterList('progress', siteId)
  const upsert = useSmeProgressUpsert()
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()
  const rows = list.data ?? []

  const openFor = (r?: Row) => {
    form.resetFields()
    if (r) {
      form.setFieldsValue({
        Equipment_Tag_No: r.Equipment_Tag_No,
        Lining_System_Code: r.Lining_System_Code,
        Original_SQM: r.Original_SQM,
        Done_SQM: r.Done_SQM,
      })
    }
    setOpen(true)
  }

  const submit = async () => {
    const v = (await form.validateFields()) as Row
    try {
      await upsert.mutateAsync({ ...v, ...(siteId ? { site_id: siteId } : {}) })
      message.success('Progress saved')
      setOpen(false)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = [
    ...buildColumns(rows),
    {
      title: 'Actions', key: '__actions', fixed: 'right', width: 90,
      render: (_: unknown, r: Row) => (
        <Button size="small" onClick={() => openFor(r)}>Edit</Button>
      ),
    },
  ]

  return (
    <div>
      {siteMissing && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          title="Pick a site in the page header to edit SQM progress." />
      )}
      <Button type="primary" icon={<PlusOutlined />} onClick={() => openFor()}
        disabled={siteMissing} style={{ marginBottom: 12 }}>
        Add / update entry
      </Button>
      <Table sticky={{ offsetHeader: 64 }}
        size="small" loading={list.isFetching} columns={columns}
        dataSource={rows.map((r, i) => ({ ...r, __rk: i }))} rowKey="__rk"
        scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `${t} rows` }}
      />
      <Modal open={open} forceRender title="Upsert SQM progress"
        onCancel={() => setOpen(false)} onOk={submit} confirmLoading={upsert.isPending}>
        <Form form={form} layout="vertical">
          <Form.Item name="Equipment_Tag_No" label="Equipment Tag No."
            rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="Lining_System_Code" label="Lining System Code"
            rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="Original_SQM" label="Original SQM (blank keeps current)">
            <InputNumber style={{ width: '100%' }} min={0} />
          </Form.Item>
          <Form.Item name="Done_SQM" label="Done SQM (blank keeps current)">
            <InputNumber style={{ width: '100%' }} min={0} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

function DropdownsTab({ siteId, siteMissing, isHod }: {
  siteId?: string; siteMissing: boolean; isHod: boolean
}) {
  const { message } = App.useApp()
  // HODs are pinned server-side, so the query works without an explicit site.
  const settings = useSmeMasterSettings(siteId, isHod || !siteMissing)
  const add = useSmeSettingAdd()
  const del = useSmeSettingDelete()
  const [newLoc, setNewLoc] = useState('')
  const [newType, setNewType] = useState('')

  if (!isHod && siteMissing) {
    return <Alert type="info" showIcon
      title="Pick a site in the page header to edit locations and types." />
  }

  const doAdd = async (kind: 'locations' | 'types', value: string, clear: () => void) => {
    if (!value.trim()) return
    try {
      await add.mutateAsync({ kind, value: value.trim(), site_id: siteId })
      message.success('Added')
      clear()
    } catch (e) {
      message.error(errMsg(e))
    }
  }
  const doDel = async (kind: 'locations' | 'types', value: string) => {
    try {
      await del.mutateAsync({ kind, value, site_id: siteId })
      message.success('Removed')
    } catch (e) {
      message.error(errMsg(e))  // in-use guard surfaces here (409)
    }
  }

  const listCard = (kind: 'locations' | 'types', title: string, items: string[],
                    value: string, setValue: (v: string) => void) => (
    <Card title={title} size="small">
      <Space.Compact style={{ width: '100%', marginBottom: 12 }}>
        <Input placeholder={`New ${title.toLowerCase().replace(/s$/, '')}`}
          value={value} onChange={(e) => setValue(e.target.value)}
          onPressEnter={() => doAdd(kind, value, () => setValue(''))} />
        <Button type="primary" icon={<PlusOutlined />} loading={add.isPending}
          onClick={() => doAdd(kind, value, () => setValue(''))}>Add</Button>
      </Space.Compact>
      <List
        size="small"
        dataSource={items}
        loading={settings.isFetching}
        renderItem={(v) => (
          <List.Item actions={[
            <Popconfirm key="del" title={`Remove "${v}"?`} onConfirm={() => doDel(kind, v)}>
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>,
          ]}>
            {v}
          </List.Item>
        )}
      />
    </Card>
  )

  return (
    <div>
      <Typography.Paragraph type="secondary">
        Dropdown values offered on the equipment forms. A value still used by
        equipment at the site cannot be removed.
      </Typography.Paragraph>
      <GridRow gutter={16}>
        <Col xs={24} md={12}>
          {listCard('locations', 'Locations', settings.data?.locations ?? [], newLoc, setNewLoc)}
        </Col>
        <Col xs={24} md={12}>
          {listCard('types', 'Types', settings.data?.types ?? [], newType, setNewType)}
        </Col>
      </GridRow>
    </div>
  )
}

export default function MasterData({ siteId }: { siteId?: string }) {
  const { user } = useAuth()
  const isHod = user?.role === 'hod'
  // HOD writes are pinned server-side; admin must pick a site for the
  // site-scoped entities (equipment / progress / dropdowns).
  const siteMissing = !isHod && !siteId

  return (
    <div>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        Phase S6 — edits the SME masters directly (the estimator recomputes
        instantly). Equipment adds also seed the SQM-progress baseline;
        deleting equipment removes its progress entry. Materials edit the
        SME-owned seed only — live availability stays derived from ERP
        movements.
      </Typography.Paragraph>
      <Tabs
        defaultActiveKey="equipment"
        items={[
          {
            key: 'equipment', label: 'Equipment',
            children: <CrudTab kind="equipment" fields={EQUIPMENT_FIELDS} idKey="id"
              siteId={siteId} needsSite siteMissing={siteMissing}
              deleteWarning="Delete this equipment row? Its SQM-progress entry is removed too." />,
          },
          {
            key: 'recipes', label: 'Recipes / BOM',
            children: <CrudTab kind="recipes" fields={RECIPE_FIELDS} idKey="id"
              siteId={siteId} needsSite={false} siteMissing={false} />,
          },
          {
            key: 'materials', label: 'Materials seed',
            children: <CrudTab kind="materials" fields={MATERIAL_FIELDS}
              idKey="material_code" siteId={siteId} needsSite={false} siteMissing={false}
              deleteWarning="Delete this seed row? ERP receipts/consumption history is preserved."
              lockOnEdit={['Material_Code']} />,
          },
          {
            key: 'progress', label: 'SQM Progress',
            children: <ProgressTab siteId={siteId} siteMissing={siteMissing} />,
          },
          {
            key: 'dropdowns', label: 'Locations & Types',
            children: <DropdownsTab siteId={siteId} siteMissing={siteMissing} isHod={isHod} />,
          },
        ]}
      />
    </div>
  )
}
