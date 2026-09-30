import { useState } from 'react'
import { App, Button, Popconfirm, Select, Space, Typography } from 'antd'
import { Table } from '../lib/smartTable'
import type { ColumnsType } from 'antd/es/table'
import { PlusOutlined } from '@ant-design/icons'
import { useDeleteInventory, useList, useSites } from '../api/hooks'
import type { Row } from '../api/client'
import InventoryItemModal, { errMsg } from '../components/InventoryItemModal'
import type { ItemMode } from '../components/InventoryItemModal'

const PAGE = 20

export default function InventoryAdminPage() {
  const { message } = App.useApp()
  const [mode, setMode] = useState<ItemMode>(null)
  const [target, setTarget] = useState<Row | null>(null)
  const [site, setSite] = useState<string | undefined>()
  const [page, setPage] = useState(1)

  const { data: sites } = useSites()
  const { data, isFetching } = useList('/inventory', {
    limit: PAGE, offset: (page - 1) * PAGE, site_id: site,
  })
  const del = useDeleteInventory()

  const openCreate = () => { setTarget(null); setMode('create') }
  const openEdit = (r: Row) => { setTarget(r); setMode('edit') }
  const close = () => { setMode(null); setTarget(null) }

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

      <InventoryItemModal mode={mode} target={target} onClose={close} base="/admin/inventory" />
    </div>
  )
}
