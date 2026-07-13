import { useState } from 'react'
import { App, Button, DatePicker, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Spin, Table, Tag, Typography, Upload } from 'antd'
import { CameraOutlined, QrcodeOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { useCreateReturnable, useMarkReturned, useReturnables } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'
import { api } from '../api/client'
import type { Row } from '../api/client'
import QrScanner from '../components/QrScanner'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

// Tool loans: who borrowed what, when it's due back, and what's overdue.
// Overdue items fire a one-time in-app notification server-side.
export default function ReturnablesPage() {
  const { message } = App.useApp()
  const { user } = useAuth()
  const { data, isFetching } = useReturnables()
  const create = useCreateReturnable()
  const ret = useMarkReturned()
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()

  const now = data?.now ? dayjs(data.now) : dayjs()
  const isOverdue = (r: Row) =>
    r.status === 'borrowed' && !!r.expected_return_time && dayjs(String(r.expected_return_time)).isBefore(now)

  // --- Smart Scan (Phase AI-4) -------------------------------------------------
  // Tier 1: badge QR decoded CLIENT-SIDE (QrScanner) → GET /ai/badge/{id}
  // verifies the active employee and prefills the borrower. Tier 2: a tool
  // photo → tool_identify vision job → prefills the item name.
  const [scanOpen, setScanOpen] = useState(false)
  const [badge, setBadge] = useState<{ id: string; name: string; active: boolean } | null>(null)
  const [toolJobId, setToolJobId] = useState<number | null>(null)
  const [toolAlts, setToolAlts] = useState<string[]>([])
  const [toolCv, setToolCv] = useState<{ name: string; confidence?: number } | null>(null)

  const onBadgeDecoded = async (id: string) => {
    setScanOpen(false)
    try {
      const r = (await api.get(`/ai/badge/${encodeURIComponent(id)}`)).data
      if (!r.found) {
        setBadge(null)
        message.warning(r.message)
        return
      }
      setBadge({ id, name: r.name, active: r.active })
      form.setFieldsValue({ borrower_name: r.name, borrower_phone: r.phone || undefined })
      if (r.active) message.success(`Badge verified: ${r.name} (${r.department})`)
      else message.warning(r.message)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  // Return-side "Employee QR check" (legacy parity): scan a returning
  // borrower's badge to show ONLY their open loans.
  const [filterScanOpen, setFilterScanOpen] = useState(false)
  const [loanFilter, setLoanFilter] = useState<{ id: string; name: string } | null>(null)
  const onFilterBadge = async (id: string) => {
    setFilterScanOpen(false)
    try {
      const r = (await api.get(`/ai/badge/${encodeURIComponent(id)}`)).data
      if (!r.found) {
        message.warning(r.message)
        return
      }
      setLoanFilter({ id, name: r.name })
      message.success(`Showing loans for ${r.name}`)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  useQuery({
    queryKey: ['/ai/jobs', toolJobId],
    enabled: toolJobId != null,
    refetchInterval: (q) => {
      const s = (q.state.data as { status?: string } | undefined)?.status
      return s === 'queued' || s === 'running' ? 2000 : false
    },
    queryFn: async () => {
      const r = (await api.get(`/ai/jobs/${toolJobId}`)).data
      if (r.status === 'done' && r.result?.tool) {
        setToolJobId(null)
        const t = r.result.tool
        form.setFieldsValue({ material_name: t.name })
        setToolAlts([t.name, ...t.alternatives.map((a: { name: string }) => a.name)])
        setToolCv({ name: t.name, confidence: t.confidence })
        message.success(`Identified: ${t.name}${t.description ? ` — ${t.description}` : ''}`)
      } else if (r.status === 'error') {
        setToolJobId(null)
        message.warning(r.error ?? 'Could not identify the tool — type it manually.')
      }
      return r
    },
  })

  const submit = async () => {
    const v = await form.validateFields()
    try {
      await create.mutateAsync({
        material_name: v.material_name,
        borrower_name: v.borrower_name,
        borrower_phone: v.borrower_phone || undefined,
        qty: v.qty ?? 1,
        uom: v.uom || undefined,
        // LOCAL wall-clock time, no timezone conversion — the ledger stores
        // naive local timestamps (toISOString() shifted every due time to UTC,
        // showing 3 h early next to given_time; UAT timezone bug).
        expected_return_time: (v.due as dayjs.Dayjs).format('YYYY-MM-DDTHH:mm:ss'),
        site_id: user?.site_id || undefined,
        // Smart-Scan adoption audit: how this loan was identified.
        cv_employee_id: badge?.id || undefined,
        cv_tool_class: toolCv?.name || undefined,
        cv_confidence: toolCv?.confidence ?? undefined,
      })
      message.success('Loan recorded')
      setOpen(false)
      form.resetFields()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const markReturned = async (id: number) => {
    try {
      await ret.mutateAsync(id)
      message.success('Marked returned')
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: 'Item', dataIndex: 'material_name', ellipsis: true },
    { title: 'Qty', dataIndex: 'qty', align: 'right', width: 70 },
    { title: 'UOM', dataIndex: 'uom', width: 70, render: (v) => v ?? '—' },
    { title: 'Borrower', dataIndex: 'borrower_name', width: 140 },
    // dayjs parses naive DB timestamps as local and tz-suffixed ones as UTC →
    // local, so both render in the user's local time (UTC+3 on site).
    { title: 'Given', dataIndex: 'given_time', width: 160,
      render: (v) => (v ? dayjs(String(v)).format('YYYY-MM-DD HH:mm') : '—') },
    { title: 'Due back', dataIndex: 'expected_return_time', width: 160,
      render: (v) => (v ? dayjs(String(v)).format('YYYY-MM-DD HH:mm') : '—') },
    {
      title: 'Status', key: '__s', width: 110,
      render: (_: unknown, r: Row) =>
        r.status === 'returned' ? (
          <Tag color="green">returned</Tag>
        ) : isOverdue(r) ? (
          <Tag color="red">OVERDUE</Tag>
        ) : (
          <Tag color="gold">on loan</Tag>
        ),
    },
    {
      title: 'Action', key: '__a', width: 130,
      render: (_: unknown, r: Row) =>
        r.status === 'borrowed' ? (
          <Popconfirm title="Tool physically back in the store?" onConfirm={() => markReturned(Number(r.id))}>
            <Button size="small" type="primary">Mark returned</Button>
          </Popconfirm>
        ) : null,
    },
  ]

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Returnable Items
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Tools and equipment on loan to employees. Overdue loans are flagged here and
        raise a one-time notification.
      </Typography.Paragraph>

      <Space style={{ marginBottom: 12 }} wrap>
        <Button type="primary" onClick={() => {
          setBadge(null); setToolAlts([]); setToolJobId(null); setToolCv(null); setOpen(true)
        }}>Loan a tool</Button>
        <Button icon={<QrcodeOutlined />} onClick={() => setFilterScanOpen(true)}>
          Scan badge — show this employee's loans
        </Button>
        {loanFilter && (
          <Tag closable color="blue" onClose={() => setLoanFilter(null)}>
            loans for {loanFilter.name}
          </Tag>
        )}
      </Space>

      <Table
        size="small"
        loading={isFetching}
        columns={columns}
        dataSource={(data?.items ?? []).filter((r) => !loanFilter
          || String(r.cv_employee_id ?? '') === loanFilter.id
          || String(r.borrower_name ?? '').trim().toLowerCase()
             === loanFilter.name.trim().toLowerCase())}
        rowKey={(r) => String(r.id)}
        rowClassName={(r) => (isOverdue(r) ? 'gi-row-overdue' : '')}
        scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 20, showTotal: (t) => `${t} loans` }}
      />

      <Modal title="Loan a tool to an employee" open={open} onOk={submit}
        onCancel={() => setOpen(false)} confirmLoading={create.isPending} okText="Record loan"
        destroyOnHidden>
        <Form form={form} layout="vertical" preserve={false} initialValues={{ qty: 1 }}>
          <Space style={{ marginBottom: 12 }} wrap>
            <Button icon={<QrcodeOutlined />} onClick={() => setScanOpen(true)}>
              Scan badge
            </Button>
            <Upload accept="image/*" maxCount={1} showUploadList={false}
              customRequest={async ({ file, onSuccess, onError }) => {
                const fd = new FormData()
                fd.append('file', file as Blob)
                try {
                  const r = await api.post('/ai/jobs', fd, { params: { kind: 'tool_identify' } })
                  setToolJobId(r.data.job_id)
                  onSuccess?.(r.data)
                } catch (e) {
                  message.error(errMsg(e))
                  onError?.(e as Error)
                }
              }}>
              <Button icon={<CameraOutlined />} loading={toolJobId != null}>
                {toolJobId != null ? 'Identifying…' : 'Identify tool (photo)'}
              </Button>
            </Upload>
            {toolJobId != null && <Spin size="small" />}
            {badge && (
              <Tag color={badge.active ? 'green' : 'red'}>
                badge: {badge.name}{badge.active ? '' : ' (inactive)'}
              </Tag>
            )}
          </Space>
          <Form.Item name="material_name" label="Tool / item" rules={[{ required: true }]}>
            {toolAlts.length > 1 ? (
              <Select options={toolAlts.map((a) => ({ value: a, label: a }))}
                popupMatchSelectWidth={false} showSearch
                onChange={(v) => form.setFieldsValue({ material_name: v })} />
            ) : (
              <Input placeholder="e.g. Torque wrench — or use Identify tool ↑" />
            )}
          </Form.Item>
          <Form.Item name="borrower_name" label="Borrower" rules={[{ required: true }]}>
            <Input placeholder="Employee name — or Scan badge ↑" />
          </Form.Item>
          <Form.Item name="borrower_phone" label="Phone (optional — gets WhatsApp updates)"
            rules={[{ pattern: /^\+[0-9][0-9\s()-]{7,18}$/, message: 'Use +<country code><number>, e.g. +966512345678' }]}>
            <Input placeholder="+966512345678" inputMode="tel" />
          </Form.Item>
          <Space size="middle">
            <Form.Item name="qty" label="Qty"><InputNumber min={0.001} /></Form.Item>
            <Form.Item name="uom" label="UOM (optional)"><Input style={{ width: 100 }} /></Form.Item>
          </Space>
          <Form.Item name="due" label="Expected return" rules={[{ required: true }]}>
            <DatePicker showTime style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>

      <QrScanner open={scanOpen} title="Scan employee badge"
        onClose={() => setScanOpen(false)} onDecode={onBadgeDecoded} />
      <QrScanner open={filterScanOpen} title="Scan the returning employee's badge"
        onClose={() => setFilterScanOpen(false)} onDecode={onFilterBadge} />
    </div>
  )
}
