import { App, Button, Card, Form, Input, Select, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation } from 'react-router-dom'
import { api } from '../api/client'
import type { Row as ApiRow } from '../api/client'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

const FB_COLOR: Record<string, string> = {
  open: 'gold', in_progress: 'blue', resolved: 'green', closed: 'default',
}

// Bug reports / feature requests — anyone can file one; admins respond from
// the Admin Console and the answer lands back here (plus a notification).
export default function FeedbackPage() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const location = useLocation()
  const [form] = Form.useForm()
  const { data: mine, isFetching } = useQuery({
    queryKey: ['/feedback/mine'],
    queryFn: async () => (await api.get<{ items: ApiRow[] }>('/feedback/mine')).data.items,
  })
  const submit = useMutation({
    mutationFn: (b: Record<string, unknown>) => api.post('/feedback', b).then((r) => r.data),
    onSuccess: () => {
      message.success('Thanks — the admins have been notified')
      form.resetFields()
      qc.invalidateQueries({ queryKey: ['/feedback/mine'] })
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const columns: ColumnsType<ApiRow> = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: 'Type', dataIndex: 'type', width: 90, render: (v: string) => <Tag>{v}</Tag> },
    { title: 'Description', dataIndex: 'description', ellipsis: true },
    { title: 'Status', dataIndex: 'status', width: 120,
      render: (v: string) => <Tag color={FB_COLOR[v] ?? 'default'}>{v}</Tag> },
    { title: 'Admin response', dataIndex: 'admin_response', ellipsis: true,
      render: (v) => v ?? '—' },
    { title: 'Filed', dataIndex: 'created_at', width: 160,
      render: (v) => String(v ?? '').slice(0, 16).replace('T', ' ') },
  ]

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Feedback &amp; Bug Reports</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Spotted a bug or want a feature? File it here — you'll be notified when an
        admin responds.
      </Typography.Paragraph>

      <Card size="small" title="New report" style={{ maxWidth: 640, marginBottom: 16 }}>
        <Form form={form} layout="vertical" initialValues={{ type: 'bug', page: location.pathname }}
          onFinish={(v) => submit.mutate(v)}>
          <Form.Item name="type" label="Type" rules={[{ required: true }]}>
            <Select options={[
              { value: 'bug', label: '🐞 Bug' },
              { value: 'feature', label: '✨ Feature request' },
              { value: 'other', label: '💬 Other' },
            ]} />
          </Form.Item>
          <Form.Item name="page" label="Page (optional)"><Input /></Form.Item>
          <Form.Item name="description" label="What happened / what do you need?"
            rules={[{ required: true, message: 'Describe it briefly' }]}>
            <Input.TextArea rows={4} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={submit.isPending}>Submit</Button>
        </Form>
      </Card>

      <Typography.Title level={5}>My reports</Typography.Title>
      <Table size="small" loading={isFetching} columns={columns} dataSource={mine ?? []}
        rowKey={(r) => String(r.id)} scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 10 }} />
    </div>
  )
}
