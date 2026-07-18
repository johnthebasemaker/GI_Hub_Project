import { useState } from 'react'
import {
  Alert, App, Button, Card, Col, Form, Input, InputNumber, Modal, Popconfirm, Row, Select,
  Space, Switch, Table, Tabs, Tag, Typography,
} from 'antd'
import { CloudDownloadOutlined, DatabaseOutlined, DollarOutlined, SwapOutlined, TeamOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { Row as ApiRow } from '../api/client'
import { useSystemOverview } from '../api/hooks'
import KpiCard from '../components/KpiCard'
import { brand, status } from '../theme/tokens'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

function useConsole<T = ApiRow[]>(path: string, pick: (d: unknown) => T) {
  return useQuery({ queryKey: [path], queryFn: async () => pick((await api.get(path)).data) })
}

function SitesTab() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const { data: items, isFetching } = useConsole('/admin/sites', (d) => (d as { items: ApiRow[] }).items)
  const [name, setName] = useState('')
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['/admin/sites'] })
    qc.invalidateQueries({ queryKey: ['/meta/sites'] })
  }
  const add = useMutation({
    mutationFn: () => api.post('/admin/sites', { name }).then((r) => r.data),
    onSuccess: () => { message.success('Site added'); setName(''); invalidate() },
    onError: (e) => message.error(errMsg(e)),
  })
  const del = useMutation({
    mutationFn: (id: number) => api.delete(`/admin/sites/${id}`).then((r) => r.data),
    onSuccess: () => { message.success('Site removed'); invalidate() },
    onError: (e) => message.error(errMsg(e)),
  })
  return (
    <div>
      <Space style={{ marginBottom: 12 }}>
        <Input placeholder="New site name" value={name} onChange={(e) => setName(e.target.value)}
          style={{ width: 220 }} onPressEnter={() => name.trim() && add.mutate()} />
        <Button type="primary" loading={add.isPending} disabled={!name.trim()}
          onClick={() => add.mutate()}>Add site</Button>
      </Space>
      <Table sticky={{ offsetHeader: 64 }} size="small" loading={isFetching} dataSource={items ?? []} rowKey={(r) => String(r.id)}
        pagination={false}
        columns={[
          { title: 'ID', dataIndex: 'id', width: 70 },
          { title: 'Site', dataIndex: 'name' },
          {
            title: 'Action', key: 'a', width: 110,
            render: (_: unknown, r: ApiRow) => (
              <Popconfirm title="Remove this site? (blocked if users are bound to it)"
                onConfirm={() => del.mutate(Number(r.id))}>
                <Button size="small" danger>Delete</Button>
              </Popconfirm>
            ),
          },
        ] as ColumnsType<ApiRow>} />
    </div>
  )
}

function SettingsTab() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const { data } = useConsole('/admin/settings',
    (d) => d as unknown as { settings: Record<string, string>; editable: string[] })
  const put = useMutation({
    mutationFn: (b: { key: string; value: string }) => api.put('/admin/settings', b).then((r) => r.data),
    onSuccess: (_, b) => { message.success(`${b.key} updated`); qc.invalidateQueries({ queryKey: ['/admin/settings'] }) },
    onError: (e) => message.error(errMsg(e)),
  })
  const [backingUp, setBackingUp] = useState(false)
  const backup = async () => {
    setBackingUp(true)
    try {
      const r = (await api.post('/admin/backup')).data
      message.success(`Backup written: ${r.file} (${Math.ceil(r.size_bytes / 1024)} KB)`)
    } catch (e) { message.error(errMsg(e)) } finally { setBackingUp(false) }
  }
  const s = data?.settings ?? {}
  const numericKeys = ['low_stock_days', 'burn_alert_days', 'expiry_warn_days']
  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={12}>
        <Card size="small" title="Maintenance mode">
          <Typography.Paragraph type="secondary">
            When ON, non-admin sign-ins and session refreshes are refused (503); running
            sessions expire within their 15-minute token lifetime.
          </Typography.Paragraph>
          <Switch checked={s.maintenance_mode === '1'} checkedChildren="ON" unCheckedChildren="OFF"
            onChange={(v) => put.mutate({ key: 'maintenance_mode', value: v ? '1' : '0' })} />
        </Card>
        <Card size="small" title="Backup" style={{ marginTop: 16 }}>
          <Typography.Paragraph type="secondary">
            Runs pg_dump (custom format) into the backups directory now.
          </Typography.Paragraph>
          <Button icon={<CloudDownloadOutlined />} loading={backingUp} onClick={backup}>
            Back up database now
          </Button>
        </Card>
      </Col>
      <Col xs={24} lg={12}>
        <Card size="small" title="Alert thresholds (days)">
          {numericKeys.map((k) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
              <Typography.Text>{k.replace(/_/g, ' ')}</Typography.Text>
              <InputNumber min={1} max={365} defaultValue={Number(s[k] ?? 0) || undefined}
                key={`${k}-${s[k]}`}
                onBlur={(e) => {
                  const v = (e.target as HTMLInputElement).value
                  if (v && v !== s[k]) put.mutate({ key: k, value: v })
                }} />
            </div>
          ))}
        </Card>
      </Col>
    </Row>
  )
}

function SessionsTab() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const { data: items, isFetching } = useConsole('/admin/sessions', (d) => (d as { items: ApiRow[] }).items)
  const act = useMutation({
    mutationFn: (path: string) => api.post(path).then((r) => r.data),
    onSuccess: () => { message.success('Revoked'); qc.invalidateQueries({ queryKey: ['/admin/sessions'] }) },
    onError: (e) => message.error(errMsg(e)),
  })
  return (
    <Table sticky={{ offsetHeader: 64 }} size="small" loading={isFetching} dataSource={items ?? []} rowKey={(r) => String(r.id)}
      pagination={{ pageSize: 20, showTotal: (t) => `${t} active sessions` }}
      columns={[
        { title: 'ID', dataIndex: 'id', width: 70 },
        { title: 'User', dataIndex: 'username' },
        { title: 'Started', dataIndex: 'created_at', render: (v) => String(v ?? '').slice(0, 16).replace('T', ' ') },
        { title: 'Expires', dataIndex: 'expires_at', render: (v) => String(v ?? '').slice(0, 16).replace('T', ' ') },
        {
          title: 'Action', key: 'a', width: 250,
          render: (_: unknown, r: ApiRow) => (
            <Space>
              <Popconfirm title="Revoke this session?"
                onConfirm={() => act.mutate(`/admin/sessions/${r.id}/revoke`)}>
                <Button size="small" danger>Revoke</Button>
              </Popconfirm>
              <Popconfirm title={`Log ${r.username} out everywhere?`}
                onConfirm={() => act.mutate(`/admin/sessions/revoke-user/${r.username}`)}>
                <Button size="small">Revoke all for user</Button>
              </Popconfirm>
            </Space>
          ),
        },
      ] as ColumnsType<ApiRow>} />
  )
}

function KpiBlock({ title, rows }: { title: string; rows?: ApiRow[] }) {
  const cols: ColumnsType<ApiRow> = rows?.length
    ? Object.keys(rows[0]).map((k) => ({ title: k, dataIndex: k, key: k }))
    : []
  return (
    <Card size="small" title={title} style={{ height: '100%' }}>
      <Table sticky={{ offsetHeader: 64 }} size="small" dataSource={(rows ?? []).map((r, i) => ({ ...r, __k: i }))} columns={cols}
        pagination={false} rowKey="__k" />
    </Card>
  )
}

const WA_COLOR: Record<string, string> = { pending: 'gold', sent: 'green', failed: 'red' }

function WhatsAppTab() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [status, setStatus] = useState<string | undefined>(undefined)
  const { data, isFetching } = useQuery({
    queryKey: ['/admin/whatsapp', status],
    queryFn: async () => (await api.get<{ items: ApiRow[]; counts: Record<string, number>; configured: boolean }>(
      '/admin/whatsapp', { params: status ? { status } : {} })).data,
  })
  const retry = useMutation({
    mutationFn: (id: number) => api.post(`/admin/whatsapp/${id}/retry`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['/admin/whatsapp'] }),
  })
  const doRetry = async (id: number) => {
    try { await retry.mutateAsync(id); message.success('Retried') }
    catch (e) { message.error(errMsg(e)) }
  }
  const cols: ColumnsType<ApiRow> = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 60 },
    { title: 'Event', dataIndex: 'event_key', key: 'ev', render: (v) => v ?? '—' },
    { title: 'To', dataIndex: 'to_number', key: 'to', render: (v) => v ?? '—' },
    { title: 'Type', dataIndex: 'message_type', key: 'ty', width: 80 },
    { title: 'Status', dataIndex: 'status', key: 'st', render: (v: string) => <Tag color={WA_COLOR[v] ?? 'default'}>{v}</Tag> },
    { title: 'Att.', dataIndex: 'attempts', key: 'at', width: 56, align: 'right' },
    { title: 'Error', dataIndex: 'error', key: 'er', ellipsis: true, render: (v) => v ?? '—' },
    { title: 'Created', dataIndex: 'created_at', key: 'cr', width: 150, render: (v) => (v ? String(v).slice(0, 16) : '—') },
    {
      title: 'Action', key: 'a', width: 90,
      render: (_: unknown, r: ApiRow) => (String(r.status) !== 'sent'
        ? <Button size="small" loading={retry.isPending} onClick={() => doRetry(Number(r.id))}>Retry</Button>
        : null),
    },
  ]
  return (
    <>
      {data && !data.configured && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          title="WhatsApp is not configured — set WHATSAPP_PHONE_NUMBER_ID / WHATSAPP_TOKEN in deploy/.env. Messages queue as 'failed' until then." />
      )}
      <Space style={{ marginBottom: 12 }}>
        <Select allowClear placeholder="All statuses" style={{ width: 200 }} value={status} onChange={setStatus}
          options={['pending', 'sent', 'failed'].map((s) => ({
            value: s, label: `${s}${data?.counts?.[s] != null ? ` (${data.counts[s]})` : ''}`,
          }))} />
      </Space>
      <Table sticky={{ offsetHeader: 64 }} size="small" loading={isFetching} columns={cols} dataSource={data?.items ?? []}
        rowKey={(r) => String(r.id)} pagination={{ pageSize: 20, showTotal: (t) => `${t} messages` }} />
    </>
  )
}

function EmailTab() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [status, setStatus] = useState<string | undefined>(undefined)
  const { data, isFetching } = useQuery({
    queryKey: ['/admin/email', status],
    queryFn: async () => (await api.get<{ items: ApiRow[]; counts: Record<string, number>; configured: boolean }>(
      '/admin/email', { params: status ? { status } : {} })).data,
  })
  const retry = useMutation({
    mutationFn: (id: number) => api.post(`/admin/email/${id}/retry`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['/admin/email'] }),
  })
  const doRetry = async (id: number) => {
    try { await retry.mutateAsync(id); message.success('Retried') }
    catch (e) { message.error(errMsg(e)) }
  }
  const cols: ColumnsType<ApiRow> = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 60 },
    { title: 'Event', dataIndex: 'event_key', key: 'ev', render: (v) => v ?? '—' },
    { title: 'To', dataIndex: 'to_email', key: 'to', render: (v) => v ?? '—' },
    { title: 'Subject', dataIndex: 'subject', key: 'su', ellipsis: true },
    { title: 'Status', dataIndex: 'status', key: 'st', render: (v: string) => <Tag color={WA_COLOR[v] ?? 'default'}>{v}</Tag> },
    { title: 'Att.', dataIndex: 'attempts', key: 'at', width: 56, align: 'right' },
    { title: 'Error', dataIndex: 'error', key: 'er', ellipsis: true, render: (v) => v ?? '—' },
    { title: 'Created', dataIndex: 'created_at', key: 'cr', width: 150, render: (v) => (v ? String(v).slice(0, 16) : '—') },
    {
      title: 'Action', key: 'a', width: 90,
      render: (_: unknown, r: ApiRow) => (String(r.status) !== 'sent'
        ? <Button size="small" loading={retry.isPending} onClick={() => doRetry(Number(r.id))}>Retry</Button>
        : null),
    },
  ]
  return (
    <>
      {data && !data.configured && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          title="SMTP is not configured — set SMTP_HOST / SMTP_USER / SMTP_PASS in deploy/.env. Emails queue as 'failed' until then." />
      )}
      <Space style={{ marginBottom: 12 }}>
        <Select allowClear placeholder="All statuses" style={{ width: 200 }} value={status} onChange={setStatus}
          options={['pending', 'sent', 'failed'].map((s) => ({
            value: s, label: `${s}${data?.counts?.[s] != null ? ` (${data.counts[s]})` : ''}`,
          }))} />
      </Space>
      <Table sticky={{ offsetHeader: 64 }} size="small" loading={isFetching} columns={cols} dataSource={data?.items ?? []}
        rowKey={(r) => String(r.id)}
        expandable={{ expandedRowRender: (r) => (
          <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{String(r.body ?? '')}</Typography.Paragraph>
        ) }}
        pagination={{ pageSize: 20, showTotal: (t) => `${t} emails` }} />
    </>
  )
}

const LOT_COLOR: Record<string, string> = { open: 'green', quarantined: 'orange', disposed: 'red' }

function LotsTab() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [status, setStatus] = useState<string | undefined>(undefined)
  const { data, isFetching } = useQuery({
    queryKey: ['/admin/lots', status],
    queryFn: async () => (await api.get<{ items: ApiRow[] }>('/admin/lots', { params: status ? { status } : {} })).data.items,
  })
  const setLot = useMutation({
    mutationFn: ({ id, st, reason }: { id: number; st: string; reason?: string }) =>
      api.post(`/admin/lots/${id}/status`, { status: st, reason }).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['/admin/lots'] }),
  })
  const act = async (id: number, st: string) => {
    try { await setLot.mutateAsync({ id, st }); message.success(`Lot ${st}`) }
    catch (e) { message.error(errMsg(e)) }
  }
  const cols: ColumnsType<ApiRow> = [
    { title: 'Lot', dataIndex: 'Lot_Number', key: 'Lot_Number' },
    { title: 'SAP', dataIndex: 'SAP_Code', key: 'SAP_Code' },
    { title: 'Site', dataIndex: 'Site_ID', key: 'Site_ID' },
    { title: 'Received', dataIndex: 'Received_Date', key: 'r', render: (v) => v ?? '—' },
    { title: 'Expiry', dataIndex: 'Expiry_Date', key: 'e', render: (v) => v ?? '—' },
    { title: 'Status', dataIndex: 'Status', key: 's', render: (v: string) => <Tag color={LOT_COLOR[v] ?? 'default'}>{v}</Tag> },
    {
      title: 'Action', key: 'a', width: 280,
      render: (_: unknown, r: ApiRow) => {
        const st = String(r.Status)
        if (st === 'disposed') return <Typography.Text type="secondary">disposed</Typography.Text>
        return (
          <Space>
            {st !== 'quarantined' && (
              <Popconfirm title="Quarantine this lot?" onConfirm={() => act(Number(r.id), 'quarantined')}>
                <Button size="small">Quarantine</Button>
              </Popconfirm>
            )}
            {st === 'quarantined' && <Button size="small" onClick={() => act(Number(r.id), 'open')}>Release</Button>}
            <Popconfirm title="Dispose this lot? It's removed from FEFO picking." onConfirm={() => act(Number(r.id), 'disposed')}>
              <Button size="small" danger>Dispose</Button>
            </Popconfirm>
          </Space>
        )
      },
    },
  ]
  return (
    <>
      <Space style={{ marginBottom: 12 }}>
        <Select allowClear placeholder="All statuses" style={{ width: 180 }} value={status} onChange={setStatus}
          options={['open', 'quarantined', 'disposed'].map((s) => ({ value: s, label: s }))} />
      </Space>
      <Table sticky={{ offsetHeader: 64 }} size="small" loading={isFetching} columns={cols} dataSource={data ?? []}
        rowKey={(r) => String(r.id)} pagination={{ pageSize: 20, showTotal: (t) => `${t} lots` }} />
    </>
  )
}

function OverviewTab() {
  const { data } = useSystemOverview()
  const txns = data?.transactions
  return (
    <>
      <Row gutter={[16, 16]} className="gi-cascade">
        <Col xs={12} md={6}><KpiCard title="Database size" value={data?.db_size ?? '—'} icon={<DatabaseOutlined />} tint={status.info} /></Col>
        <Col xs={12} md={6}><KpiCard title="Total transactions" value={txns ? txns.total.toLocaleString() : '—'} icon={<SwapOutlined />} /></Col>
        <Col xs={12} md={6}><KpiCard title="Stock value (SAR)" value={data ? Math.round(data.valuation_total).toLocaleString() : '—'} icon={<DollarOutlined />} tint={brand.gold} /></Col>
        <Col xs={12} md={6}><KpiCard title="Users" value={data?.users ?? 0} icon={<TeamOutlined />} /></Col>
      </Row>
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} md={12}>
          <Card size="small" title="Transactions by type">
            <Table<ApiRow> sticky={{ offsetHeader: 64 }} size="small" pagination={false} rowKey="k"
              dataSource={txns ? [
                { k: 'Receipts', n: txns.receipts }, { k: 'Consumption', n: txns.consumption },
                { k: 'Returns', n: txns.returns }, { k: 'Adjustments', n: txns.adjustments },
                { k: 'Audit log', n: txns.audit_log },
              ] : []}
              columns={[
                { title: 'Type', dataIndex: 'k' },
                { title: 'Count', dataIndex: 'n', align: 'right', render: (v) => Number(v).toLocaleString() },
              ]} />
          </Card>
        </Col>
        <Col xs={24} md={12}>
          <Card size="small" title="Valuation by site (SAR)">
            <Table<ApiRow> sticky={{ offsetHeader: 64 }} size="small" pagination={false} rowKey={(r) => String(r.Site_ID)}
              dataSource={data?.valuation_by_site ?? []}
              columns={[
                { title: 'Site', dataIndex: 'Site_ID' },
                { title: 'Value', dataIndex: 'value', align: 'right', render: (v) => Number(v).toLocaleString() },
              ]} />
          </Card>
        </Col>
      </Row>
    </>
  )
}

function OversightTab() {
  const { data } = useConsole('/admin/oversight', (d) => d as unknown as Record<string, ApiRow[]>)
  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} md={8}><KpiBlock title="PRs by state" rows={data?.prs_by_state} /></Col>
      <Col xs={24} md={8}><KpiBlock title="POs by status" rows={data?.pos_by_status} /></Col>
      <Col xs={24} md={8}><KpiBlock title="DNs by status" rows={data?.dns_by_status} /></Col>
      <Col xs={24} md={8}><KpiBlock title="Top vendors (by POs)" rows={data?.top_vendors} /></Col>
      <Col xs={24} md={8}><KpiBlock title="Warehouse load" rows={data?.warehouse_load} /></Col>
      <Col xs={24} md={8}><KpiBlock title="Force-closures by reason" rows={data?.force_closures_by_reason} /></Col>
    </Row>
  )
}

const FB_COLOR: Record<string, string> = {
  open: 'gold', in_progress: 'blue', resolved: 'green', closed: 'default',
}

function FeedbackTab() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const { data: items, isFetching } = useConsole('/admin/feedback', (d) => (d as { items: ApiRow[] }).items)
  const [responding, setResponding] = useState<ApiRow | null>(null)
  const [form] = Form.useForm()
  const patch = useMutation({
    mutationFn: (b: { id: number; status: string; admin_response?: string }) =>
      api.patch(`/admin/feedback/${b.id}`, b).then((r) => r.data),
    onSuccess: () => {
      message.success('Updated — the submitter was notified')
      setResponding(null)
      qc.invalidateQueries({ queryKey: ['/admin/feedback'] })
    },
    onError: (e) => message.error(errMsg(e)),
  })
  return (
    <div>
      <Table sticky={{ offsetHeader: 64 }} size="small" loading={isFetching} dataSource={items ?? []} rowKey={(r) => String(r.id)}
        scroll={{ x: 'max-content' }} pagination={{ pageSize: 20 }}
        columns={[
          { title: 'ID', dataIndex: 'id', width: 60 },
          { title: 'Type', dataIndex: 'type', width: 90, render: (v: string) => <Tag>{v}</Tag> },
          { title: 'From', dataIndex: 'username', width: 110 },
          { title: 'Page', dataIndex: 'page', width: 130, render: (v) => v ?? '—' },
          { title: 'Description', dataIndex: 'description', ellipsis: true },
          { title: 'Status', dataIndex: 'status', width: 120,
            render: (v: string) => <Tag color={FB_COLOR[v] ?? 'default'}>{v}</Tag> },
          {
            title: 'Action', key: 'a', width: 110,
            render: (_: unknown, r: ApiRow) => (
              <Button size="small" onClick={() => { setResponding(r); form.setFieldsValue({ status: r.status, admin_response: r.admin_response }) }}>
                Respond
              </Button>
            ),
          },
        ] as ColumnsType<ApiRow>} />
      <Modal title={`Report #${responding?.id ?? ''}`} open={!!responding}
        onCancel={() => setResponding(null)} confirmLoading={patch.isPending}
        onOk={async () => {
          const v = await form.validateFields()
          patch.mutate({ id: Number(responding!.id), ...v })
        }} okText="Save & notify" destroyOnHidden>
        <Typography.Paragraph type="secondary">{String(responding?.description ?? '')}</Typography.Paragraph>
        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item name="status" label="Status" rules={[{ required: true }]}>
            <Select options={['open', 'in_progress', 'resolved', 'closed'].map((s) => ({ value: s, label: s }))} />
          </Form.Item>
          <Form.Item name="admin_response" label="Response to the submitter">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default function AdminConsolePage() {
  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Admin Console</Typography.Title>
      <Tabs
        items={[
          { key: 'overview', label: 'Overview', children: <OverviewTab /> },
          { key: 'whatsapp', label: 'WhatsApp', children: <WhatsAppTab /> },
          { key: 'email', label: 'Email', children: <EmailTab /> },
          { key: 'lots', label: 'Lots', children: <LotsTab /> },
          { key: 'sites', label: 'Sites', children: <SitesTab /> },
          { key: 'settings', label: 'Settings', children: <SettingsTab /> },
          { key: 'sessions', label: 'Sessions', children: <SessionsTab /> },
          { key: 'oversight', label: 'Oversight', children: <OversightTab /> },
          { key: 'feedback', label: 'Feedback', children: <FeedbackTab /> },
        ]}
      />
    </div>
  )
}
