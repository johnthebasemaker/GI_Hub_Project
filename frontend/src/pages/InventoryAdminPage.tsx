import { useState } from 'react'
import {
  App, Button, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Table, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { PlusOutlined } from '@ant-design/icons'
import {
  useCreateInventory, useDeleteInventory, useList, useSites, useUpdateInventory,
} from '../api/hooks'
import type { Row } from '../api/client'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

const PAGE = 20
type Mode = 'create' | 'edit' | null

export default function InventoryAdminPage() {
  const { message } = App.useApp()
  const [form] = Form.useForm()
  const [mode, setMode] = useState<Mode>(null)
  const [target, setTarget] = useState<Row | null>(null)
  const [site, setSite] = useState<string | undefined>()
  const [page, setPage] = useState(1)

  const { data: sites } = useSites()
  const { data, isFetching } = useList('/inventory', {
    limit: PAGE, offset: (page - 1) * PAGE, site_id: site,
  })
  const create = useCreateInventory()
  const update = useUpdateInventory()
  const del = useDeleteInventory()

  const openCreate = () => { setTarget(null); setMode('create'); form.resetFields() }
  const openEdit = (r: Row) => {
    setTarget(r); setMode('edit')
    form.setFieldsValue({
      SAP_Code: r.SAP_Code, Equipment_Description: r.Equipment_Description,
      Material_Code: r.Material_Code, Category: r.Category, UOM: r.UOM,
      Minimum_Qty: r.Minimum_Qty, Unit_Cost: r.Unit_Cost, Opening_Stock: r.Opening_Stock,
      Site_ID: r.Site_ID, Expiry_Date: r.Expiry_Date,
    })
  }
  const close = () => { setMode(null); setTarget(null); form.resetFields() }

  const onOk = async () => {
    try {
      const v = await form.validateFields()
      if (mode === 'create') {
        await create.mutateAsync(v)
        message.success(`Item ${v.SAP_Code} created`)
      } else if (mode === 'edit' && target) {
        const { SAP_Code: _omit, ...body } = v
        await update.mutateAsync({ sap: String(target.SAP_Code), body })
        message.success(`Item ${target.SAP_Code} updated`)
      }
      close()
    } catch (e) {
      if ((e as { errorFields?: unknown }).errorFields) return
      message.error(errMsg(e))
    }
  }

  const doDelete = async (r: Row) => {
    try { await del.mutateAsync(String(r.SAP_Code)); message.success(`Item ${r.SAP_Code} deleted`) }
    catch (e) { message.error(errMsg(e)) }
  }

  const columns: ColumnsType<Row> = [
    { title: 'SAP', dataIndex: 'SAP_Code', key: 'SAP_Code', fixed: 'left', width: 110 },
    { title: 'Description', dataIndex: 'Equipment_Description', key: 'd', ellipsis: true },
    { title: 'Category', dataIndex: 'Category', key: 'Category', width: 120 },
    { title: 'UoM', dataIndex: 'UOM', key: 'UOM', width: 70 },
    { title: 'Min', dataIndex: 'Minimum_Qty', key: 'Minimum_Qty', align: 'right', width: 70, render: (v) => Number(v ?? 0) },
    { title: 'Unit Cost', dataIndex: 'Unit_Cost', key: 'Unit_Cost', align: 'right', width: 90, render: (v) => Number(v ?? 0) },
    { title: 'Opening', dataIndex: 'Opening_Stock', key: 'Opening_Stock', align: 'right', width: 90, render: (v) => Number(v ?? 0) },
    { title: 'Site', dataIndex: 'Site_ID', key: 'Site_ID', width: 90 },
    {
      title: 'Actions', key: '__act', width: 150,
      render: (_: unknown, r: Row) => (
        <Space size="small">
          <Button size="small" onClick={() => openEdit(r)}>Edit</Button>
          <Popconfirm title={`Delete ${r.SAP_Code}? Blocked if it has ledger movements.`} onConfirm={() => doDelete(r)}>
            <Button size="small" danger>Delete</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Inventory Master</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Add / edit / delete inventory master items. Opening-stock changes are audited;
        an item with ledger movements cannot be deleted. Admin only.
      </Typography.Paragraph>
      <Space style={{ marginBottom: 12 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>New item</Button>
        <Select allowClear placeholder="All sites" style={{ width: 160 }} value={site}
          onChange={(v) => { setSite(v); setPage(1) }}
          options={(sites ?? []).map((s) => ({ value: s, label: s }))} />
      </Space>
      <Table sticky={{ offsetHeader: 64 }}
        size="small"
        loading={isFetching}
        columns={columns}
        dataSource={data?.items ?? []}
        rowKey={(r) => String(r.SAP_Code)}
        scroll={{ x: 900 }}
        pagination={{
          current: page, pageSize: PAGE, total: data?.total ?? 0,
          showSizeChanger: false, onChange: setPage, showTotal: (t) => `${t} items`,
        }}
      />

      <Modal open={mode !== null} onOk={onOk} onCancel={close} forceRender
        title={mode === 'create' ? 'New inventory item' : `Edit ${target?.SAP_Code}`}
        confirmLoading={create.isPending || update.isPending} okText="Save">
        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item name="SAP_Code" label="SAP Code" rules={[{ required: true }]}>
            <Input disabled={mode === 'edit'} placeholder="e.g. 5001" />
          </Form.Item>
          <Form.Item name="Equipment_Description" label="Description">
            <Input placeholder="Material description" />
          </Form.Item>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="Material_Code" label="Material Code"><Input /></Form.Item>
            <Form.Item name="Category" label="Category"><Input placeholder="e.g. Consumables" /></Form.Item>
            <Form.Item name="UOM" label="UoM"><Input placeholder="Each" /></Form.Item>
          </Space>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="Minimum_Qty" label="Minimum Qty"><InputNumber min={0} style={{ width: 130 }} /></Form.Item>
            <Form.Item name="Unit_Cost" label="Unit Cost"><InputNumber min={0} style={{ width: 130 }} /></Form.Item>
            <Form.Item name="Opening_Stock" label="Opening Stock"><InputNumber min={0} style={{ width: 130 }} /></Form.Item>
          </Space>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="Site_ID" label="Site"><Input placeholder="HQ" /></Form.Item>
            <Form.Item name="Expiry_Date" label="Expiry Date"><Input placeholder="YYYY-MM-DD" /></Form.Item>
          </Space>
        </Form>
      </Modal>
    </div>
  )
}
