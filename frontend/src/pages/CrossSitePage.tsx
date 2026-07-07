import {
  App, Button, Card, Form, Input, InputNumber, Popconfirm, Select, Space, Table, Tag, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { Row as ApiRow } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useList, useSites } from '../api/hooks'
import SubmissionInsight from '../components/SubmissionInsight'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

const STATUS_COLOR: Record<string, string> = {
  pending: 'gold', approved: 'green', rejected: 'red',
}

// Cross-site material requests: an HOD asks another site for stock; an admin
// arbitrates. (Legacy "My Requests" + admin "Pending Requests" in one page.)
export default function CrossSitePage() {
  const { message } = App.useApp()
  const { user } = useAuth()
  const qc = useQueryClient()
  const isAdmin = (user?.level ?? 0) >= 4
  const { data: sites } = useSites()
  const inventory = useList('/inventory', { limit: 500 })
  const [form] = Form.useForm()

  const { data, isFetching } = useQuery({
    queryKey: ['/xsite'],
    queryFn: async () => (await api.get<{ items: ApiRow[] }>('/xsite')).data.items,
  })
  const invalidate = () => qc.invalidateQueries({ queryKey: ['/xsite'] })

  const create = useMutation({
    mutationFn: (b: Record<string, unknown>) => api.post('/xsite', b).then((r) => r.data),
    onSuccess: (r) => {
      message.success(`Request raised — target site has ${r.available_at_target} on hand`)
      form.resetFields()
      invalidate()
    },
    onError: (e) => message.error(errMsg(e)),
  })
  const decide = useMutation({
    mutationFn: ({ id, action }: { id: number; action: string }) =>
      api.post(`/xsite/${id}/decide`, { action }).then((r) => r.data),
    onSuccess: (r) => { message.success(`Request ${r.status}`); invalidate() },
    onError: (e) => message.error(errMsg(e)),
  })
  const cancel = useMutation({
    mutationFn: (id: number) => api.delete(`/xsite/${id}`).then((r) => r.data),
    onSuccess: () => { message.success('Cancelled'); invalidate() },
    onError: (e) => message.error(errMsg(e)),
  })

  const columns: ColumnsType<ApiRow> = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: 'From', dataIndex: 'requesting_site', width: 100 },
    { title: 'To', dataIndex: 'target_site', width: 100 },
    { title: 'SAP', dataIndex: 'SAP_Code', width: 90 },
    { title: 'Qty', dataIndex: 'requested_qty', align: 'right', width: 80 },
    { title: 'Avail@target', dataIndex: 'available_qty', align: 'right', width: 110 },
    { title: 'Suggested', dataIndex: 'suggested_qty', align: 'right', width: 100,
      render: (v) => (v == null ? '—' : String(v)) },
    { title: 'By', dataIndex: 'requested_by', width: 110 },
    { title: 'Status', dataIndex: 'status', width: 100,
      render: (v: string) => <Tag color={STATUS_COLOR[v] ?? 'default'}>{v}</Tag> },
    { title: 'Notes', dataIndex: 'notes', ellipsis: true, render: (v) => v ?? '—' },
    {
      title: 'Action', key: 'a', width: 190,
      render: (_: unknown, r: ApiRow) => {
        if (r.status !== 'pending') return null
        if (isAdmin) {
          return (
            <Space>
              <Popconfirm title="Approve this transfer request?"
                onConfirm={() => decide.mutate({ id: Number(r.id), action: 'approve' })}>
                <Button size="small" type="primary">Approve</Button>
              </Popconfirm>
              <Popconfirm title="Reject it?"
                onConfirm={() => decide.mutate({ id: Number(r.id), action: 'reject' })}>
                <Button size="small" danger>Reject</Button>
              </Popconfirm>
            </Space>
          )
        }
        if (r.requested_by === user?.username) {
          return (
            <Popconfirm title="Cancel this pending request?"
              onConfirm={() => cancel.mutate(Number(r.id))}>
              <Button size="small">Cancel</Button>
            </Popconfirm>
          )
        }
        return null
      },
    },
  ]

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Cross-Site Requests</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Ask another site for material — an admin arbitrates. The availability snapshot
        at the target site is captured when you raise the request.
      </Typography.Paragraph>

      <Card size="small" title="Raise a request" style={{ marginBottom: 16, maxWidth: 860 }}>
        <Form form={form} layout="inline" onFinish={(v) => create.mutate(v)}>
          <Form.Item name="target_site" rules={[{ required: true, message: 'target site' }]}>
            <Select placeholder="Target site" style={{ width: 150 }}
              options={(sites ?? []).filter((s) => s !== user?.site_id)
                .map((s) => ({ value: s, label: s }))} />
          </Form.Item>
          <Form.Item name="SAP_Code" rules={[{ required: true, message: 'material' }]}>
            <Select showSearch placeholder="Material" style={{ width: 260 }} optionFilterProp="label"
              loading={inventory.isFetching}
              options={(inventory.data?.items ?? []).map((r: ApiRow) => ({
                value: String(r.SAP_Code), label: `${r.SAP_Code} — ${r.Equipment_Description ?? ''}`,
              }))} />
          </Form.Item>
          <Form.Item name="requested_qty" rules={[{ required: true, message: 'qty' }]}>
            <InputNumber placeholder="Qty" min={0.001} style={{ width: 100 }} />
          </Form.Item>
          <Form.Item name="notes"><Input placeholder="Notes (optional)" style={{ width: 180 }} /></Form.Item>
          <Button type="primary" htmlType="submit" loading={create.isPending}>Raise</Button>
        </Form>
      </Card>

      <Table size="small" loading={isFetching} columns={columns} dataSource={data ?? []}
        rowKey={(r) => String(r.id)} scroll={{ x: 'max-content' }}
        // T1 — Submission Intelligence for the granting side: expand a pending
        // request to see the depletion forecast ("if you give this, the site
        // is short in N days") computed from the target site's 30-day usage.
        expandable={{
          rowExpandable: (r: ApiRow) => r.status === 'pending',
          expandedRowRender: (r: ApiRow) => (
            <SubmissionInsight kind="xsite" refId={Number(r.id)} />
          ),
        }}
        pagination={{ pageSize: 20, showTotal: (t) => `${t} requests` }} />
    </div>
  )
}
