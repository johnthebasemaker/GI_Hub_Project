import { useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  App,
  Button,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { PlusOutlined } from '@ant-design/icons'
import { useCreate, useDelete, useList, useUpdate } from '../api/hooks'
import type { Row } from '../api/client'
import { WRITE_ENTITIES } from '../config/entities'
import type { WriteEntity } from '../config/entities'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Request failed'
}

function Crud({ entity }: { entity: WriteEntity }) {
  const { message } = App.useApp()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const { data, isFetching } = useList(entity.path, {
    limit: pageSize,
    offset: (page - 1) * pageSize,
  })
  const create = useCreate(entity.path)
  const update = useUpdate(entity.path)
  const del = useDelete(entity.path)

  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Row | null>(null)
  const [form] = Form.useForm()

  const openAdd = () => {
    setEditing(null)
    form.resetFields()
    setOpen(true)
  }
  const openEdit = (r: Row) => {
    setEditing(r)
    form.setFieldsValue(r)
    setOpen(true)
  }

  const submit = async () => {
    const values = (await form.validateFields()) as Row
    try {
      if (editing) {
        await update.mutateAsync({ id: editing[entity.idKey] as number, body: values })
        message.success('Updated')
      } else {
        await create.mutateAsync(values)
        message.success('Created')
      }
      setOpen(false)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const remove = async (r: Row) => {
    try {
      await del.mutateAsync(r[entity.idKey] as number)
      message.success('Deleted')
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = [
    { title: 'ID', dataIndex: entity.idKey, key: entity.idKey, width: 70 },
    ...entity.fields.map((f) => ({
      title: f.label,
      dataIndex: f.name,
      key: f.name,
      render: (v: unknown) => (v == null ? '—' : String(v)),
    })),
    {
      title: 'Actions',
      key: 'actions',
      fixed: 'right',
      width: 150,
      render: (_: unknown, r: Row) => (
        <Space>
          <Button size="small" onClick={() => openEdit(r)}>
            Edit
          </Button>
          <Popconfirm title="Delete this row?" onConfirm={() => remove(r)}>
            <Button size="small" danger>
              Delete
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 12,
        }}
      >
        <Typography.Title level={3} style={{ margin: 0 }}>
          {entity.label}
        </Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}>
          Add
        </Button>
      </div>

      <Table
        size="small"
        loading={isFetching}
        columns={columns}
        dataSource={data?.items ?? []}
        rowKey={(r) => String(r[entity.idKey])}
        scroll={{ x: 'max-content' }}
        pagination={{
          current: page,
          pageSize,
          total: data?.total ?? 0,
          showSizeChanger: true,
          showTotal: (t) => `${t} rows`,
          onChange: (p, ps) => {
            setPage(p)
            setPageSize(ps)
          },
        }}
      />

      <Modal
        open={open}
        forceRender
        title={editing ? `Edit ${entity.label}` : `Add ${entity.label}`}
        onCancel={() => setOpen(false)}
        onOk={submit}
        confirmLoading={create.isPending || update.isPending}
      >
        <Form form={form} layout="vertical">
          {entity.fields.map((f) => (
            <Form.Item
              key={f.name}
              name={f.name}
              label={f.label}
              rules={f.required ? [{ required: true, message: `${f.label} is required` }] : []}
            >
              {f.type === 'select' ? (
                <Select
                  allowClear
                  options={(f.options ?? []).map((o) => ({ value: o, label: o }))}
                />
              ) : (
                <Input />
              )}
            </Form.Item>
          ))}
        </Form>
      </Modal>
    </div>
  )
}

export default function MasterDataPage() {
  const { key } = useParams<{ key: string }>()
  const entity = WRITE_ENTITIES.find((e) => e.key === key)
  if (!entity) return <Empty description={`Unknown master data: ${key}`} />
  return <Crud key={entity.key} entity={entity} />
}
