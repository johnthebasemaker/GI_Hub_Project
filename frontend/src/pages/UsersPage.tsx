import { useState } from 'react'
import {
  App, Button, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { PlusOutlined } from '@ant-design/icons'
import { useAuth } from '../auth/AuthContext'
import {
  useAdminRoles, useAdminUsers, useCreateUser, useDeleteUser, useList,
  useResetPassword, useResetUser2fa, useSites, useUpdateUser,
} from '../api/hooks'
import type { Row } from '../api/client'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

const ROLE_COLOR: Record<string, string> = {
  admin: 'gold', logistics: 'cyan', hod: 'geekblue',
  warehouse_user: 'green', supervisor: 'blue', store_keeper: 'default',
}

type ModalMode = 'create' | 'edit' | 'password' | null

export default function UsersPage() {
  const { message } = App.useApp()
  const { user: me } = useAuth()
  const [form] = Form.useForm()
  const [mode, setMode] = useState<ModalMode>(null)
  const [target, setTarget] = useState<Row | null>(null)

  const { data: users, isFetching } = useAdminUsers()
  const { data: roles } = useAdminRoles()
  const { data: sites } = useSites()
  const warehouses = useList('/warehouses', { limit: 500 })

  const create = useCreateUser()
  const update = useUpdateUser()
  const resetPw = useResetPassword()
  const reset2fa = useResetUser2fa()
  const del = useDeleteUser()

  const roleOptions = (roles ?? []).map((r: Row) => ({ value: String(r.value), label: String(r.label) }))
  const siteOptions = (sites ?? []).map((s) => ({ value: s, label: s }))
  const whOptions = (warehouses.data?.items ?? []).map((w: Row) => ({
    value: String(w.Warehouse_ID), label: `${w.Warehouse_ID}${w.Name ? ' — ' + w.Name : ''}`,
  }))

  const openCreate = () => {
    setTarget(null); setMode('create')
    form.resetFields()
  }
  const openEdit = (u: Row) => {
    setTarget(u); setMode('edit')
    form.setFieldsValue({
      role: u.role, site_id: u.Site_ID || undefined,
      warehouse_id: u.Warehouse_ID || undefined, phone_number: u.Phone_Number || undefined,
    })
  }
  const openPassword = (u: Row) => {
    setTarget(u); setMode('password')
    form.resetFields()
  }
  const close = () => { setMode(null); setTarget(null); form.resetFields() }

  const onOk = async () => {
    try {
      const v = await form.validateFields()
      if (mode === 'create') {
        await create.mutateAsync(v)
        message.success(`User ${v.username} created`)
      } else if (mode === 'edit' && target) {
        await update.mutateAsync({ username: String(target.username), body: {
          role: v.role, site_id: v.site_id ?? '', warehouse_id: v.warehouse_id ?? '',
          phone_number: v.phone_number ?? '',
        } })
        message.success(`User ${target.username} updated`)
      } else if (mode === 'password' && target) {
        await resetPw.mutateAsync({ username: String(target.username), password: v.password })
        message.success(`Password reset for ${target.username}`)
      }
      close()
    } catch (e) {
      if ((e as { errorFields?: unknown }).errorFields) return // form validation, keep open
      message.error(errMsg(e))
    }
  }

  const doReset2fa = async (u: Row) => {
    try { await reset2fa.mutateAsync(String(u.username)); message.success(`2FA cleared for ${u.username}`) }
    catch (e) { message.error(errMsg(e)) }
  }
  const doDelete = async (u: Row) => {
    try { await del.mutateAsync(String(u.username)); message.success(`User ${u.username} deleted`) }
    catch (e) { message.error(errMsg(e)) }
  }

  const columns: ColumnsType<Row> = [
    { title: 'Username', dataIndex: 'username', key: 'username' },
    {
      title: 'Role', dataIndex: 'role', key: 'role',
      render: (v: string, r) => <Tag color={ROLE_COLOR[v] ?? 'default'}>{String(r.label ?? v)}</Tag>,
    },
    { title: 'Site', dataIndex: 'Site_ID', key: 'Site_ID', render: (v) => v || <Typography.Text type="secondary">global</Typography.Text> },
    { title: 'Warehouse', dataIndex: 'Warehouse_ID', key: 'Warehouse_ID', render: (v) => v || '—' },
    { title: 'Phone', dataIndex: 'Phone_Number', key: 'Phone_Number', render: (v) => v || '—' },
    { title: '2FA', dataIndex: 'totp_enabled', key: 'totp_enabled', render: (v) => (v ? <Tag color="green">on</Tag> : <Tag>off</Tag>) },
    {
      title: 'Actions', key: '__act', width: 300,
      render: (_: unknown, u: Row) => {
        const isSelf = String(u.username) === me?.username
        return (
          <Space size="small" wrap>
            <Button size="small" onClick={() => openEdit(u)}>Edit</Button>
            <Button size="small" onClick={() => openPassword(u)}>Reset PW</Button>
            <Popconfirm title={`Clear 2FA for ${u.username}?`} onConfirm={() => doReset2fa(u)} disabled={!u.totp_enabled}>
              <Button size="small" disabled={!u.totp_enabled}>Reset 2FA</Button>
            </Popconfirm>
            <Popconfirm title={`Delete user ${u.username}? This cannot be undone.`} onConfirm={() => doDelete(u)} disabled={isSelf}>
              <Button size="small" danger disabled={isSelf}>Delete</Button>
            </Popconfirm>
          </Space>
        )
      },
    },
  ]

  const modalTitle = mode === 'create' ? 'Create user'
    : mode === 'edit' ? `Edit ${target?.username}`
    : mode === 'password' ? `Reset password — ${target?.username}` : ''
  const saving = create.isPending || update.isPending || resetPw.isPending

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Users</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Manage login accounts. Passwords are stored bcrypt-hashed and never shown. Every change is audited.
      </Typography.Paragraph>
      <Space style={{ marginBottom: 12 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>New user</Button>
      </Space>
      <Table
        size="small"
        loading={isFetching}
        columns={columns}
        dataSource={users ?? []}
        rowKey={(r) => String(r.username)}
        pagination={{ pageSize: 20, showTotal: (t) => `${t} users` }}
      />

      <Modal open={mode !== null} title={modalTitle} onOk={onOk} onCancel={close}
        okText={mode === 'password' ? 'Reset password' : 'Save'} confirmLoading={saving} forceRender>
        <Form form={form} layout="vertical" preserve={false}>
          {mode === 'create' && (
            <>
              <Form.Item name="username" label="Username" rules={[{ required: true }]}>
                <Input autoComplete="off" placeholder="e.g. jdoe" />
              </Form.Item>
              <Form.Item name="password" label="Password" rules={[{ required: true, min: 6, message: 'At least 6 characters' }]}>
                <Input.Password autoComplete="new-password" placeholder="min 6 characters" />
              </Form.Item>
            </>
          )}
          {(mode === 'create' || mode === 'edit') && (
            <>
              <Form.Item name="role" label="Role" rules={[{ required: true }]}>
                <Select options={roleOptions} placeholder="Role" />
              </Form.Item>
              <Form.Item name="site_id" label="Site (blank = global)">
                <Select allowClear options={siteOptions} placeholder="Site" />
              </Form.Item>
              <Form.Item name="warehouse_id" label="Warehouse (optional)">
                <Select allowClear showSearch optionFilterProp="label" options={whOptions}
                  loading={warehouses.isFetching} placeholder="Warehouse binding" />
              </Form.Item>
              <Form.Item name="phone_number" label="Phone (optional)">
                <Input placeholder="Phone number" />
              </Form.Item>
            </>
          )}
          {mode === 'password' && (
            <Form.Item name="password" label="New password" rules={[{ required: true, min: 6, message: 'At least 6 characters' }]}>
              <Input.Password autoComplete="new-password" placeholder="min 6 characters" />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  )
}
