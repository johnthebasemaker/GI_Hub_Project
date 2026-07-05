import { useMemo, useState } from 'react'
import {
  App, Button, Card, Checkbox, Col, DatePicker, Form, Input, InputNumber, Modal,
  Popconfirm, Radio, Row, Select, Space, Statistic, Table, Tabs, Tag, Typography,
  Upload,
} from 'antd'
import { InboxOutlined, DownloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs, { Dayjs } from 'dayjs'
import { api } from '../api/client'
import type { Row as ApiRow } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useSites, downloadDocument } from '../api/hooks'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

// Every query/write on this page is site-parameterized: hods are pinned by the
// server; admins pick a site once (top-right) and it threads through all tabs.
function useMh<T = { items: ApiRow[] }>(path: string, params: Record<string, unknown>) {
  return useQuery({
    queryKey: [path, params],
    queryFn: async () => (await api.get<T>(path, { params })).data,
  })
}

interface TabProps {
  site?: string // undefined for hod (server-pinned); the picked site for admin
}

const sp = (site?: string) => (site ? { site_id: site } : {})

// --- 👥 Employees -----------------------------------------------------------
function EmployeesTab({ site }: TabProps) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [form] = Form.useForm()
  const { data, isFetching } = useMh('/mh/employees', sp(site))
  const invalidate = () => qc.invalidateQueries({ queryKey: ['/mh/employees'] })

  const upsert = useMutation({
    mutationFn: (b: Record<string, unknown>) =>
      api.post('/mh/employees', { ...b, ...sp(site) }).then((r) => r.data),
    onSuccess: () => { message.success('Employee saved'); form.resetFields(); invalidate() },
    onError: (e) => message.error(errMsg(e)),
  })
  const setStatus = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) =>
      api.patch(`/mh/employees/${id}/status`, null, { params: { status } }).then((r) => r.data),
    onSuccess: () => { message.success('Status updated'); invalidate() },
    onError: (e) => message.error(errMsg(e)),
  })

  const items = data?.items ?? []
  const supply = items.filter((r) => r.Worker_Type === 'Supply').length
  const columns: ColumnsType<ApiRow> = [
    { title: 'Code', dataIndex: 'Employee_Code', width: 90 },
    { title: 'Name', dataIndex: 'Name' },
    { title: 'Designation', dataIndex: 'Designation', render: (v) => v || '—' },
    { title: 'Type', dataIndex: 'Worker_Type', width: 90,
      render: (v: string) => <Tag color={v === 'Supply' ? 'blue' : 'default'}>{v}</Tag> },
    { title: 'Company', dataIndex: 'Company', render: (v) => v || '—' },
    { title: 'Status', dataIndex: 'status', width: 100,
      render: (v: string) => <Tag color={v === 'active' ? 'green' : 'red'}>{v}</Tag> },
    {
      title: 'Action', key: 'a', width: 130,
      render: (_: unknown, r: ApiRow) => (
        <Button size="small" onClick={() => setStatus.mutate({
          id: Number(r.id), status: r.status === 'active' ? 'inactive' : 'active',
        })}>
          {r.status === 'active' ? 'Deactivate' : 'Reactivate'}
        </Button>
      ),
    },
  ]

  return (
    <div>
      <Card size="small" title="Add / update a worker" style={{ marginBottom: 16 }}>
        <Form form={form} layout="inline" initialValues={{ worker_type: 'OWN' }}
          onFinish={(v) => upsert.mutate(v)}>
          <Form.Item name="employee_code" rules={[{ required: true, message: 'code' }]}>
            <Input placeholder="Employee code" style={{ width: 130 }} />
          </Form.Item>
          <Form.Item name="name" rules={[{ required: true, message: 'name' }]}>
            <Input placeholder="Name" style={{ width: 180 }} />
          </Form.Item>
          <Form.Item name="designation"><Input placeholder="Designation" style={{ width: 140 }} /></Form.Item>
          <Form.Item name="worker_type">
            <Radio.Group options={[{ value: 'OWN', label: 'OWN' }, { value: 'Supply', label: 'Supply' }]} />
          </Form.Item>
          <Form.Item name="company"><Input placeholder="Company" style={{ width: 120 }} /></Form.Item>
          <Button type="primary" htmlType="submit" loading={upsert.isPending}>Save</Button>
        </Form>
      </Card>
      <Typography.Paragraph type="secondary">
        {items.length} workers ({supply} Supply) — kept separate from the system users table.
      </Typography.Paragraph>
      <Table size="small" loading={isFetching} columns={columns} dataSource={items}
        rowKey={(r) => String(r.id)} scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 15, showTotal: (t) => `${t} workers` }} />
    </div>
  )
}

// --- 🕒 Daily Timesheet ------------------------------------------------------
interface GridRow { worked: boolean; in_time: string; out_time: string }

function TimesheetTab({ site }: TabProps) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [date, setDate] = useState<Dayjs>(dayjs())
  const [tag, setTag] = useState<string>()
  const [system, setSystem] = useState<string>()
  const [breakMins, setBreakMins] = useState(60)
  const [grid, setGrid] = useState<Record<string, GridRow>>({})
  const [sqm, setSqm] = useState<number>(0)
  const [method, setMethod] = useState('even')
  const [replace, setReplace] = useState(true)

  const workDate = date.format('YYYY-MM-DD')
  const { data: meta } = useMh<{ equipment_tags: string[]; tag_locations: Record<string, string>; system_codes: string[] }>('/mh/meta', sp(site))
  const roster = useMh('/mh/employees', { ...sp(site), status: 'active' })
  const existing = useMh('/mh/timesheets', { ...sp(site), work_date: workDate })
  const location = tag ? (meta?.tag_locations?.[tag] ?? '') : ''
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['/mh/timesheets'] })
    qc.invalidateQueries({ queryKey: ['/mh/variance'] })
    qc.invalidateQueries({ queryKey: ['/mh/employee-timeline'] })
  }

  const row = (code: string): GridRow =>
    grid[code] ?? { worked: false, in_time: '07:30', out_time: '16:30' }
  const patch = (code: string, p: Partial<GridRow>) =>
    setGrid((g) => ({ ...g, [code]: { ...row(code), ...p } }))

  const save = useMutation({
    mutationFn: () => {
      const rows = (roster.data?.items ?? [])
        .filter((r) => row(String(r.Employee_Code)).worked)
        .map((r) => {
          const g = row(String(r.Employee_Code))
          return { employee_code: String(r.Employee_Code), in_time: g.in_time, out_time: g.out_time }
        })
      return api.post('/mh/timesheets', {
        ...sp(site), work_date: workDate, equipment_tag: tag, system_code: system,
        location, break_mins: breakMins, rows,
      }).then((r) => r.data)
    },
    onSuccess: (r) => { message.success(`Saved ${r.saved} timesheet row(s) for ${workDate}`); invalidate() },
    onError: (e) => message.error(errMsg(e)),
  })

  const distribute = useMutation({
    mutationFn: () => api.post('/mh/production', {
      ...sp(site), work_date: workDate, equipment_tag: tag, system_code: system,
      sqm_done: sqm, distribution_method: method,
    }).then((r) => r.data),
    onSuccess: (r) => { message.success(`SQM saved — distributed across ${r.distributed_rows} row(s)`); invalidate() },
    onError: (e) => message.error(errMsg(e)),
  })

  const del = useMutation({
    mutationFn: (id: number) => api.delete(`/mh/timesheets/${id}`).then((r) => r.data),
    onSuccess: () => { message.success('Row deleted'); invalidate() },
    onError: (e) => message.error(errMsg(e)),
  })

  const workedCount = (roster.data?.items ?? [])
    .filter((r) => row(String(r.Employee_Code)).worked).length
  const ready = Boolean(tag && system)

  const gridColumns: ColumnsType<ApiRow> = [
    {
      title: 'Worked', key: 'w', width: 80,
      render: (_: unknown, r: ApiRow) => (
        <Checkbox checked={row(String(r.Employee_Code)).worked}
          onChange={(e) => patch(String(r.Employee_Code), { worked: e.target.checked })} />
      ),
    },
    { title: 'Code', dataIndex: 'Employee_Code', width: 90 },
    { title: 'Name', dataIndex: 'Name' },
    {
      title: 'In', key: 'in', width: 110,
      render: (_: unknown, r: ApiRow) => (
        <Input size="small" value={row(String(r.Employee_Code)).in_time}
          onChange={(e) => patch(String(r.Employee_Code), { in_time: e.target.value })} />
      ),
    },
    {
      title: 'Out', key: 'out', width: 110,
      render: (_: unknown, r: ApiRow) => (
        <Input size="small" value={row(String(r.Employee_Code)).out_time}
          onChange={(e) => patch(String(r.Employee_Code), { out_time: e.target.value })} />
      ),
    },
  ]

  const existingColumns: ColumnsType<ApiRow> = [
    { title: 'Code', dataIndex: 'Employee_Code', width: 90 },
    { title: 'Tag', dataIndex: 'Equipment_Tag', render: (v) => v ?? '—' },
    { title: 'System', dataIndex: 'System_Code', width: 80, render: (v) => v ?? '—' },
    { title: 'In', dataIndex: 'In_Time', width: 90 },
    { title: 'Out', dataIndex: 'Out_Time', width: 90 },
    { title: 'Total h', dataIndex: 'Total_Hours', align: 'right', width: 80 },
    { title: 'OT h', dataIndex: 'OT_Hours', align: 'right', width: 70 },
    { title: 'SQM', dataIndex: 'Allocated_SQM', align: 'right', width: 80 },
    {
      title: '', key: 'a', width: 70,
      render: (_: unknown, r: ApiRow) => (
        <Popconfirm title="Delete this row?" onConfirm={() => del.mutate(Number(r.id))}>
          <Button size="small" danger>Del</Button>
        </Popconfirm>
      ),
    },
  ]

  return (
    <div>
      <Card size="small" title="📤 Import attendance Excel (to_john_Attendance format)"
        style={{ marginBottom: 16 }}>
        <Space orientation="vertical" style={{ width: '100%' }}>
          <Radio.Group value={replace} onChange={(e) => setReplace(e.target.value)}
            options={[
              { value: true, label: 'Replace rows for the file’s dates (predictable re-import)' },
              { value: false, label: 'Append' },
            ]} />
          <Upload.Dragger accept=".xlsx" maxCount={1} showUploadList={false}
            customRequest={async ({ file, onSuccess, onError }) => {
              const fd = new FormData()
              fd.append('file', file as Blob)
              try {
                const r = await api.post('/mh/import', fd,
                  { params: { ...sp(site), replace } })
                message.success(`Imported ${r.data.employees} employees, `
                  + `${r.data.timesheets} timesheet rows (${r.data.dates.length} dates)`)
                qc.invalidateQueries({ queryKey: ['/mh/employees'] })
                invalidate()
                onSuccess?.(r.data)
              } catch (e) {
                message.error(errMsg(e))
                onError?.(e as Error)
              }
            }}>
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            <p className="ant-upload-text">Drop the attendance .xlsx here (sheets: ADD EMPLOYEE, SAR)</p>
            <p className="ant-upload-hint">Hours are recomputed from In/Out — the file's hour columns are ignored.</p>
          </Upload.Dragger>
        </Space>
      </Card>

      <Card size="small" title="🕒 Manual entry · per-day batch" style={{ marginBottom: 16 }}>
        <Space wrap style={{ marginBottom: 12 }}>
          <DatePicker value={date} onChange={(d) => d && setDate(d)} allowClear={false} />
          <Select showSearch placeholder="Equipment tag" style={{ width: 220 }} value={tag}
            onChange={setTag} options={(meta?.equipment_tags ?? []).map((t) => ({ value: t, label: t }))} />
          <Select showSearch placeholder="System code" style={{ width: 140 }} value={system}
            onChange={setSystem} options={(meta?.system_codes ?? []).map((c) => ({ value: c, label: c }))} />
          <Space size={4}>
            <Typography.Text type="secondary">Break (min)</Typography.Text>
            <InputNumber min={0} max={240} step={15} value={breakMins}
              onChange={(v) => setBreakMins(v ?? 60)} style={{ width: 90 }} />
          </Space>
        </Space>
        {location && (
          <Typography.Paragraph type="secondary" style={{ marginTop: -4 }}>
            📍 Location (from SME equipment): <strong>{location}</strong>
          </Typography.Paragraph>
        )}
        <Table size="small" loading={roster.isFetching} columns={gridColumns}
          dataSource={roster.data?.items ?? []} rowKey={(r) => String(r.Employee_Code)}
          pagination={{ pageSize: 12, showTotal: (t) => `${t} active workers` }} />
        <Space style={{ marginTop: 12 }} wrap>
          <Button type="primary" disabled={!ready || workedCount === 0}
            loading={save.isPending} onClick={() => save.mutate()}>
            💾 Save {workedCount} timesheet row(s)
          </Button>
          {!ready && <Typography.Text type="secondary">Pick an equipment tag and system code first.</Typography.Text>}
        </Space>
        <Card size="small" type="inner" title="📐 Record team SQM completed (auto-distribute)"
          style={{ marginTop: 16, maxWidth: 560 }}>
          <Space wrap>
            <Space size={4}>
              <Typography.Text type="secondary">SQM</Typography.Text>
              <InputNumber min={0} value={sqm} onChange={(v) => setSqm(v ?? 0)}
                style={{ width: 110 }} />
            </Space>
            <Select value={method} onChange={setMethod} style={{ width: 130 }}
              options={[{ value: 'even', label: 'even' }, { value: 'by_hours', label: 'by hours' }]} />
            <Button disabled={!ready} loading={distribute.isPending}
              onClick={() => distribute.mutate()}>Distribute</Button>
          </Space>
        </Card>
      </Card>

      <Typography.Title level={5}>Rows booked on {workDate} ({existing.data?.items?.length ?? 0})</Typography.Title>
      <Table size="small" loading={existing.isFetching} columns={existingColumns}
        dataSource={existing.data?.items ?? []} rowKey={(r) => String(r.id)}
        scroll={{ x: 'max-content' }} pagination={{ pageSize: 10 }} />
    </div>
  )
}

// --- 📐 Estimator ------------------------------------------------------------
function EstimatorTab({ site }: TabProps) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [form] = Form.useForm()
  const { data: meta } = useMh<{ equipment_tags: string[]; tag_locations: Record<string, string>; system_codes: string[] }>('/mh/meta', sp(site))
  const { data, isFetching } = useMh('/mh/estimates', sp(site))
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['/mh/estimates'] })
    qc.invalidateQueries({ queryKey: ['/mh/variance'] })
  }
  const upsert = useMutation({
    mutationFn: (v: Record<string, unknown>) => api.post('/mh/estimates', {
      ...v, ...sp(site),
      location: meta?.tag_locations?.[String(v.equipment_tag)] ?? '',
    }).then((r) => r.data),
    onSuccess: () => { message.success('Estimate saved'); form.resetFields(); invalidate() },
    onError: (e) => message.error(errMsg(e)),
  })
  const del = useMutation({
    mutationFn: (id: number) => api.delete(`/mh/estimates/${id}`).then((r) => r.data),
    onSuccess: () => { message.success('Estimate removed'); invalidate() },
    onError: (e) => message.error(errMsg(e)),
  })

  const columns: ColumnsType<ApiRow> = [
    { title: 'Tag', dataIndex: 'Equipment_Tag' },
    { title: 'System', dataIndex: 'System_Code', width: 90 },
    { title: 'Location', dataIndex: 'Location', render: (v) => v || '—' },
    { title: 'Est. man-hours', dataIndex: 'Estimated_Manhours', align: 'right', width: 130 },
    { title: 'Est. SQM', dataIndex: 'Estimated_SQM', align: 'right', width: 100,
      render: (v) => v ?? '—' },
    { title: 'MH / SQM', key: 'norm', align: 'right', width: 100,
      render: (_: unknown, r: ApiRow) => {
        const mh = Number(r.Estimated_Manhours), sq = Number(r.Estimated_SQM)
        return sq > 0 ? (mh / sq).toFixed(2) : '—'
      } },
    { title: 'Basis', dataIndex: 'Basis', ellipsis: true, render: (v) => v || '—' },
    {
      title: '', key: 'a', width: 70,
      render: (_: unknown, r: ApiRow) => (
        <Popconfirm title="Remove this estimate?" onConfirm={() => del.mutate(Number(r.id))}>
          <Button size="small" danger>Del</Button>
        </Popconfirm>
      ),
    },
  ]

  return (
    <div>
      <Typography.Paragraph type="secondary">
        Define the REQUIRED man-hours for a scope. An optional Estimated SQM yields a
        man-hours-per-SQM norm.
      </Typography.Paragraph>
      <Card size="small" title="Save an estimate" style={{ marginBottom: 16 }}>
        <Form form={form} layout="inline" onFinish={(v) => upsert.mutate(v)}>
          <Form.Item name="equipment_tag" rules={[{ required: true, message: 'tag' }]}>
            <Select showSearch placeholder="Equipment tag" style={{ width: 210 }}
              options={(meta?.equipment_tags ?? []).map((t) => ({ value: t, label: t }))} />
          </Form.Item>
          <Form.Item name="system_code" rules={[{ required: true, message: 'system' }]}>
            <Select showSearch placeholder="System" style={{ width: 120 }}
              options={(meta?.system_codes ?? []).map((c) => ({ value: c, label: c }))} />
          </Form.Item>
          <Form.Item name="estimated_manhours" rules={[{ required: true, message: 'man-hours' }]}>
            <InputNumber placeholder="Man-hours" min={0} style={{ width: 120 }} />
          </Form.Item>
          <Form.Item name="estimated_sqm">
            <InputNumber placeholder="SQM (opt.)" min={0} style={{ width: 110 }} />
          </Form.Item>
          <Form.Item name="basis"><Input placeholder="Basis / notes" style={{ width: 180 }} /></Form.Item>
          <Button type="primary" htmlType="submit" loading={upsert.isPending}>💾 Save</Button>
        </Form>
      </Card>
      <Table size="small" loading={isFetching} columns={columns} dataSource={data?.items ?? []}
        rowKey={(r) => String(r.id)} scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 15, showTotal: (t) => `${t} estimates` }} />
    </div>
  )
}

// --- 📊 Estimate vs Actual ----------------------------------------------------
function VarianceTab({ site }: TabProps) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const { data, isFetching } = useMh<{ items: ApiRow[]; kpis: { scopes: number; over_consuming: number; total_actual: number } }>('/mh/variance', sp(site))
  const [reasonFor, setReasonFor] = useState<ApiRow | null>(null)
  const [reason, setReason] = useState('')

  const saveReason = useMutation({
    mutationFn: () => api.post('/mh/variance/reason', {
      ...sp(site), equipment_tag: reasonFor?.Equipment_Tag,
      system_code: reasonFor?.System_Code, reason,
    }).then((r) => r.data),
    onSuccess: () => {
      message.success('Reason saved')
      setReasonFor(null); setReason('')
      qc.invalidateQueries({ queryKey: ['/mh/variance'] })
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const items = data?.items ?? []
  const k = data?.kpis
  const columns: ColumnsType<ApiRow> = [
    { title: 'Tag', dataIndex: 'Equipment_Tag' },
    { title: 'System', dataIndex: 'System_Code', width: 80 },
    { title: 'Location', dataIndex: 'Location', render: (v) => v || '—' },
    { title: 'Estimated', dataIndex: 'Estimated_Manhours', align: 'right', width: 100 },
    { title: 'Actual', dataIndex: 'Actual_Manhours', align: 'right', width: 90 },
    { title: 'Variance', dataIndex: 'Variance_Manhours', align: 'right', width: 100,
      render: (v) => Number(v).toFixed(1) },
    { title: 'Var %', dataIndex: 'Variance_Pct', align: 'right', width: 90,
      render: (v) => {
        if (v == null) return '—'
        const n = Number(v)
        return <Tag color={n > 10 ? 'red' : n <= 0 ? 'green' : 'gold'}>{n > 0 ? '+' : ''}{n}%</Tag>
      } },
    { title: 'SQM done', dataIndex: 'SQM_Done', align: 'right', width: 100 },
    { title: 'Reason', dataIndex: 'Variance_Reason', ellipsis: true, render: (v) => v ?? '—' },
    {
      title: '', key: 'a', width: 90,
      render: (_: unknown, r: ApiRow) => (
        <Button size="small" onClick={() => {
          setReasonFor(r); setReason(String(r.Variance_Reason ?? ''))
        }}>📝 Reason</Button>
      ),
    },
  ]

  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 16, maxWidth: 640 }}>
        <Col span={8}><Card size="small"><Statistic title="Scopes tracked" value={k?.scopes ?? 0} /></Card></Col>
        <Col span={8}><Card size="small"><Statistic title="Over-consuming" value={k?.over_consuming ?? 0}
          styles={(k?.over_consuming ?? 0) > 0 ? { content: { color: '#dc3545' } } : undefined} /></Card></Col>
        <Col span={8}><Card size="small"><Statistic title="Total actual MH" value={k?.total_actual ?? 0} precision={1} /></Card></Col>
      </Row>
      <Table size="small" loading={isFetching} columns={columns} dataSource={items}
        rowKey={(r) => `${r.Equipment_Tag}·${r.System_Code}`} scroll={{ x: 'max-content' }}
        onRow={(r) => {
          const v = r.Variance_Pct == null ? null : Number(r.Variance_Pct)
          const bg = v == null ? undefined
            : v > 10 ? 'rgba(220,53,69,0.12)' : v <= 0 ? 'rgba(40,167,69,0.10)' : undefined
          return bg ? { style: { background: bg } } : {}
        }}
        pagination={{ pageSize: 15, showTotal: (t) => `${t} scopes` }} />
      <Modal open={!!reasonFor} title={`Over-consumption reason · ${reasonFor?.Equipment_Tag} / ${reasonFor?.System_Code}`}
        onCancel={() => setReasonFor(null)} onOk={() => saveReason.mutate()}
        okButtonProps={{ disabled: !reason.trim(), loading: saveReason.isPending }} okText="Save reason">
        <Input.TextArea rows={4} value={reason} onChange={(e) => setReason(e.target.value)}
          placeholder="Why did this scope consume more man-hours than estimated?" />
      </Modal>
    </div>
  )
}

// --- 🧑‍🔧 Employee-wise --------------------------------------------------------
function EmployeeWiseTab({ site }: TabProps) {
  const [emp, setEmp] = useState<string>()
  const [range, setRange] = useState<[Dayjs, Dayjs]>([dayjs().subtract(30, 'day'), dayjs()])
  const roster = useMh('/mh/employees', sp(site))
  const params = useMemo(() => ({
    ...sp(site), ...(emp ? { employee_code: emp } : {}),
    date_from: range[0].format('YYYY-MM-DD'), date_to: range[1].format('YYYY-MM-DD'),
  }), [site, emp, range])
  const { data, isFetching } = useMh<{ items: ApiRow[]; total_hours: number }>('/mh/employee-timeline', params)

  const columns: ColumnsType<ApiRow> = [
    { title: 'Code', dataIndex: 'Employee_Code', width: 90 },
    { title: 'Name', dataIndex: 'Name' },
    { title: 'Date', dataIndex: 'Work_Date', width: 110 },
    { title: 'Location', dataIndex: 'Location', render: (v) => v || '—' },
    { title: 'Tag', dataIndex: 'Equipment_Tag', render: (v) => v ?? '—' },
    { title: 'System', dataIndex: 'System_Code', width: 80, render: (v) => v ?? '—' },
    { title: 'Total h', dataIndex: 'Total_Hours', align: 'right', width: 80 },
    { title: 'OT h', dataIndex: 'OT_Hours', align: 'right', width: 70 },
    { title: 'SQM', dataIndex: 'Allocated_SQM', align: 'right', width: 80 },
  ]

  return (
    <div>
      <Space wrap style={{ marginBottom: 12 }}>
        <Select allowClear showSearch placeholder="All employees" style={{ width: 260 }}
          value={emp} onChange={setEmp} optionFilterProp="label"
          options={(roster.data?.items ?? []).map((r) => ({
            value: String(r.Employee_Code), label: `${r.Employee_Code} — ${r.Name}`,
          }))} />
        <DatePicker.RangePicker value={range} allowClear={false}
          onChange={(v) => v && v[0] && v[1] && setRange([v[0], v[1]])} />
        <Button icon={<DownloadOutlined />} onClick={() =>
          downloadDocument('/mh/export/employee-timeline',
            { format: 'xlsx', ...params }, 'mh-employee-timeline.xlsx')}>
          Export XLSX
        </Button>
      </Space>
      <Typography.Paragraph type="secondary">
        {data?.items?.length ?? 0} rows · {data?.total_hours ?? 0} man-hours in this window
      </Typography.Paragraph>
      <Table size="small" loading={isFetching} columns={columns} dataSource={data?.items ?? []}
        rowKey={(r) => `${r.Employee_Code}-${r.Work_Date}-${r.Equipment_Tag}-${r.System_Code}`}
        scroll={{ x: 'max-content' }} pagination={{ pageSize: 20, showTotal: (t) => `${t} rows` }} />
    </div>
  )
}

// --- Page --------------------------------------------------------------------
// Exact-locked to {hod, admin} (nav + server require_roles). Admin picks a site;
// hods are pinned to their own site by the server.
export default function ManHoursPage() {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const { data: sites } = useSites()
  const [site, setSite] = useState<string>()
  const effSite = isAdmin ? (site ?? sites?.[0]) : undefined

  return (
    <div>
      <Space align="center" style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={3} style={{ marginTop: 0 }}>🕒 Man-Hours &amp; Labor Tracking</Typography.Title>
        {isAdmin && (
          <Select style={{ width: 160 }} value={effSite} onChange={setSite}
            options={(sites ?? []).map((s) => ({ value: s, label: s }))} placeholder="Site" />
        )}
      </Space>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Track LABOR the way the SME tracks material — roster, daily timesheets,
        required-MH estimates and the variance between them.
      </Typography.Paragraph>
      <Tabs
        defaultActiveKey="employees"
        items={[
          { key: 'employees', label: '👥 Employees', children: <EmployeesTab site={effSite} /> },
          { key: 'timesheet', label: '🕒 Daily Timesheet', children: <TimesheetTab site={effSite} /> },
          { key: 'estimator', label: '📐 Estimator', children: <EstimatorTab site={effSite} /> },
          { key: 'variance', label: '📊 Estimate vs Actual', children: <VarianceTab site={effSite} /> },
          { key: 'employee-wise', label: '🧑‍🔧 Employee-wise', children: <EmployeeWiseTab site={effSite} /> },
        ]}
      />
    </div>
  )
}
