import { useMemo, useState } from 'react'
import {
  Alert, App, Button, Card, Checkbox, Col, DatePicker, Form, Input, InputNumber, Modal, Row as GridRow,
  Segmented, Select, Space, Spin, Statistic, Switch, Tag, Tooltip, Typography, Upload,
} from 'antd'
import { Table } from '../lib/smartTable'
import { CameraOutlined, QrcodeOutlined, ScanOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { useCreateReturnable, useReturnables } from '../api/hooks'
import { printLoanSlip, resolveReturnScan, useReturnBatch, useReturnOne } from '../api/returnablesHooks'
import type { ReturnCondition, ReturnScan } from '../api/returnablesHooks'
import { useAuth } from '../auth/AuthContext'
import { api } from '../api/client'
import type { Row } from '../api/client'
import QrScanner from '../components/QrScanner'
import ScanBox from '../components/ScanBox'
import type { ScanResult } from '../components/ScanBox'
import { BARCODE_FORMATS } from '../lib/barcode'
import { scanFeedback } from '../lib/scanFeedback'
import { status as statusColors } from '../theme/tokens'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

const QUICK_KEY = 'gi.returns.quick'
function readQuick(): boolean {
  try { return window.localStorage.getItem(QUICK_KEY) === '1' } catch { return false }
}
function writeQuick(v: boolean) {
  try { window.localStorage.setItem(QUICK_KEY, v ? '1' : '0') } catch { /* optional */ }
}

const CONDITIONS: { value: ReturnCondition; label: string; color: string }[] = [
  { value: 'ok', label: 'Good order', color: 'green' },
  { value: 'damaged', label: 'Damaged', color: 'red' },
  { value: 'incomplete', label: 'Parts missing', color: 'orange' },
]

/** "in 3 h" / "2 d overdue" — what a store keeper reads at a glance. */
function dueText(due: dayjs.Dayjs, now: dayjs.Dayjs): string {
  const mins = due.diff(now, 'minute')
  const abs = Math.abs(mins)
  const span = abs < 60 ? `${abs} min` : abs < 48 * 60 ? `${Math.round(abs / 60)} h` : `${Math.round(abs / 1440)} d`
  return mins >= 0 ? `in ${span}` : `${span} overdue`
}

/** Sensible due-back presets: the end of today's shift, or tomorrow's. */
function shiftEnd(daysAhead = 0): dayjs.Dayjs {
  return dayjs().add(daysAhead, 'day').hour(17).minute(0).second(0)
}
function defaultDue(): dayjs.Dayjs {
  return dayjs().hour() < 16 ? shiftEnd(0) : shiftEnd(1)
}

// Tool loans: who borrowed what, when it's due back, and what's overdue.
// Overdue items fire a one-time in-app notification server-side.
//
// Phase 18 Track 3 — the RETURN DESK. A return used to be: find the row in a
// 500-line table, press "Mark returned", confirm a popup. Now the page opens
// with the cursor in one scan box: scan the borrower's badge (everything they
// have out), the tool's sticker / serial / asset tag (that loan), or type
// "#<loan id>"; pick the condition if it is not in good order; press Enter.
export default function ReturnablesPage() {
  const { message } = App.useApp()
  const { user } = useAuth()
  const { data, isFetching } = useReturnables()
  const create = useCreateReturnable()
  const ret = useReturnOne()
  const batch = useReturnBatch()
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()
  const [view, setView] = useState<'open' | 'overdue' | 'returned' | 'all'>('open')

  const now = data?.now ? dayjs(data.now) : dayjs()
  const isOverdue = (r: Row) =>
    r.status === 'borrowed' && !!r.expected_return_time && dayjs(String(r.expected_return_time)).isBefore(now)

  const items = useMemo(() => data?.items ?? [], [data])
  const kpi = useMemo(() => {
    const today = now.format('YYYY-MM-DD')
    let onLoan = 0, overdue = 0, dueToday = 0, backToday = 0
    for (const r of items) {
      if (r.status === 'borrowed') {
        onLoan += 1
        const due = r.expected_return_time ? dayjs(String(r.expected_return_time)) : null
        if (due && due.isBefore(now)) overdue += 1
        else if (due && due.format('YYYY-MM-DD') === today) dueToday += 1
      } else if (r.returned_time && dayjs(String(r.returned_time)).format('YYYY-MM-DD') === today) {
        backToday += 1
      }
    }
    return { onLoan, overdue, dueToday, backToday }
  }, [items, data?.now])

  // --- the return desk ---------------------------------------------------------
  const [scan, setScan] = useState<ReturnScan | null>(null)
  const [picked, setPicked] = useState<number[]>([])
  const [condition, setCondition] = useState<ReturnCondition>('ok')
  const [note, setNote] = useState('')
  const [quick, setQuick] = useState(readQuick())

  const clearDesk = () => { setScan(null); setPicked([]); setCondition('ok'); setNote('') }

  const doReturn = async (ids: number[], cond: ReturnCondition, n: string): Promise<ScanResult> => {
    if (!ids.length) return { tone: 'error', message: 'Tick at least one loan to return.' }
    const r = await batch.mutateAsync({ ids, condition: cond, note: n.trim() || undefined })
    const okN = r.returned.length
    const bad = r.skipped.map((s) => `#${s.id}: ${s.reason}`).join(' · ')
    clearDesk()
    if (!okN) return { tone: 'error', message: bad || 'Nothing was returned.' }
    const words = cond === 'ok' ? 'in good order' : cond === 'damaged' ? 'as DAMAGED — the HOD is told' : 'with parts missing — the HOD is told'
    return { tone: bad ? 'info' : 'ok', message: `Returned ${okN} item${okN === 1 ? '' : 's'} ${words}.${bad ? ` Skipped ${bad}` : ''}` }
  }

  const confirmDesk = async () => {
    if (!scan || !picked.length) return
    try {
      const res = await doReturn(picked, condition, note)
      scanFeedback(res.tone)
      if (res.tone === 'error') message.error(res.message)
      else message.success(res.message)
    } catch (e) {
      scanFeedback('error')
      message.error(errMsg(e))
    }
  }

  const onDeskScan = async (code: string): Promise<ScanResult> => {
    const r = await resolveReturnScan(code)
    if (r.kind === 'material') {
      setScan(r); setPicked([])
      return { tone: 'info', message: r.message }
    }
    if (!r.loans.length) {
      setScan(r.kind === 'employee' ? r : null); setPicked([])
      return { tone: 'error', message: r.message || 'No open loan matches that code.' }
    }
    const ids = r.loans.map((l) => Number(l.id))
    // One-scan return: an exact single match, in good order, no further press.
    if (quick && r.loans.length === 1 && (r.kind === 'item' || r.kind === 'loan')) {
      return doReturn(ids, 'ok', '')
    }
    setScan(r); setPicked(ids); setCondition('ok'); setNote('')
    const who = r.employee?.name ?? String(r.loans[0].borrower_name ?? '')
    return {
      tone: 'ok',
      message: `${r.loans.length} open loan${r.loans.length === 1 ? '' : 's'}${who ? ` — ${who}` : ''}. Enter to return.`,
    }
  }

  // --- Smart Scan when LENDING (Phase AI-4 + Phase 18) ------------------------
  // Badge QR → GET /ai/badge/{id} prefills the borrower. A tool's sticker /
  // serial → /entry/returnables/resolve prefills the item and REMEMBERS the
  // code, so the return scan finds this loan. A photo → tool_identify job.
  const [scanOpen, setScanOpen] = useState(false)
  const [toolScanOpen, setToolScanOpen] = useState(false)
  const [badge, setBadge] = useState<{ id: string; name: string; active: boolean } | null>(null)
  const [toolJobId, setToolJobId] = useState<number | null>(null)
  const [toolAlts, setToolAlts] = useState<string[]>([])
  const [toolCv, setToolCv] = useState<{ name: string; confidence?: number } | null>(null)

  const openLoan = (prefill?: ReturnScan['material']) => {
    setBadge(null); setToolAlts([]); setToolJobId(null); setToolCv(null)
    form.resetFields()
    form.setFieldsValue({
      qty: 1, due: defaultDue(),
      ...(prefill ? {
        material_name: prefill.description || prefill.SAP_Code, uom: prefill.uom || undefined,
        sap_code: prefill.SAP_Code, item_ref: prefill.item_ref,
      } : {}),
    })
    setOpen(true)
  }

  const onBadgeDecoded = async (id: string) => {
    setScanOpen(false)
    try {
      const r = (await api.get(`/ai/badge/${encodeURIComponent(id)}`)).data
      if (!r.found) {
        setBadge(null)
        scanFeedback('error')
        message.warning(r.message)
        return
      }
      setBadge({ id, name: r.name, active: r.active })
      form.setFieldsValue({ borrower_name: r.name, borrower_phone: r.phone || undefined })
      scanFeedback(r.active ? 'ok' : 'error')
      if (r.active) message.success(`Badge verified: ${r.name} (${r.department})`)
      else message.warning(r.message)
    } catch (e) {
      scanFeedback('error')
      message.error(errMsg(e))
    }
  }

  const onToolDecoded = async (code: string) => {
    setToolScanOpen(false)
    try {
      const r = await resolveReturnScan(code)
      if (r.kind === 'item' || r.kind === 'loan') {
        const l = r.loans[0]
        scanFeedback('error')
        message.warning(`Already on loan to ${l.borrower_name} (#${l.id}) — return it first.`)
        return
      }
      if (r.kind === 'material' && r.material) {
        form.setFieldsValue({
          material_name: r.material.description || r.material.SAP_Code,
          uom: r.material.uom || undefined, sap_code: r.material.SAP_Code, item_ref: code,
        })
        scanFeedback('ok')
        message.success(`Tool: ${r.material.description || r.material.SAP_Code}`)
        return
      }
      // Unknown code: keep it so the return scan still finds this loan.
      form.setFieldsValue({ item_ref: code })
      scanFeedback('info')
      message.info('Code not in the item master — kept on the loan; type the tool name.')
    } catch (e) {
      scanFeedback('error')
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
      const made = await create.mutateAsync({
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
        // Phase 18: what was lent — the return scan matches on these.
        sap_code: v.sap_code || undefined,
        item_ref: v.item_ref || undefined,
      })
      scanFeedback('ok')
      message.success({
        key: 'loan-recorded', duration: 8,
        content: (
          <span>
            Loan recorded{made?.id ? ` — #${made.id}` : ''}
            {made?.id ? (
              <Button size="small" type="link" data-testid="loan-slip-after"
                onClick={() => { message.destroy('loan-recorded'); slip(made.id) }}>
                🖨 Print slip
              </Button>
            ) : null}
          </span>
        ),
      })
      setOpen(false)
      form.resetFields()
    } catch (e) {
      scanFeedback('error')
      message.error(errMsg(e))
    }
  }

  const slip = (id: unknown) =>
    printLoanSlip(String(id)).catch((e) => message.error(errMsg(e)))

  const returnOne = async (r: Row) => {
    try {
      await ret.mutateAsync({ id: Number(r.id), condition: 'ok' })
      scanFeedback('ok')
      message.success(`#${r.id} returned in good order`)
    } catch (e) {
      scanFeedback('error')
      message.error(errMsg(e))
    }
  }

  const condTag = (c: unknown) => {
    const x = CONDITIONS.find((k) => k.value === c)
    return x ? <Tag color={x.color}>{x.label}</Tag> : null
  }

  const columns: ColumnsType<Row> = [
    { title: 'ID', dataIndex: 'id', width: 70, render: (v) => `#${v}` },
    {
      title: 'Item', dataIndex: 'material_name', ellipsis: true,
      render: (v, r) => (
        <span>
          {v}
          {r.SAP_Code || r.Item_Ref ? (
            <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 6 }}>
              {[r.SAP_Code, r.Item_Ref && r.Item_Ref !== r.SAP_Code ? r.Item_Ref : null].filter(Boolean).join(' · ')}
            </Typography.Text>
          ) : null}
        </span>
      ),
    },
    { title: 'Qty', dataIndex: 'qty', align: 'right', width: 90, render: (v, r) => `${v ?? ''} ${r.uom ?? ''}`.trim() },
    { title: 'Borrower', dataIndex: 'borrower_name', width: 150 },
    // dayjs parses naive DB timestamps as local and tz-suffixed ones as UTC →
    // local, so both render in the user's local time (UTC+3 on site).
    { title: 'Given', dataIndex: 'given_time', width: 150,
      render: (v) => (v ? dayjs(String(v)).format('YYYY-MM-DD HH:mm') : '—') },
    {
      title: 'Due back', dataIndex: 'expected_return_time', width: 190,
      render: (v, r) => {
        if (!v) return '—'
        const d = dayjs(String(v))
        return (
          <span>
            {d.format('YYYY-MM-DD HH:mm')}
            {r.status === 'borrowed' && (
              <Typography.Text type={isOverdue(r) ? 'danger' : 'secondary'} style={{ fontSize: 12, marginLeft: 6 }}>
                {dueText(d, now)}
              </Typography.Text>
            )}
          </span>
        )
      },
    },
    {
      title: 'Status', key: '__s', width: 210,
      render: (_: unknown, r: Row) =>
        r.status === 'returned' ? (
          <Space size={4} wrap>
            <Tag color="green">returned</Tag>
            {condTag(r.return_condition)}
            {r.returned_time ? (
              <Tooltip title={[r.returned_by ? `received by ${r.returned_by}` : '', r.return_note ?? ''].filter(Boolean).join(' — ')}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {dayjs(String(r.returned_time)).format('MM-DD HH:mm')}
                </Typography.Text>
              </Tooltip>
            ) : null}
          </Space>
        ) : isOverdue(r) ? (
          <Tag color="red">OVERDUE</Tag>
        ) : (
          <Tag color="gold">on loan</Tag>
        ),
    },
    {
      title: 'Action', key: '__a', width: 250,
      render: (_: unknown, r: Row) =>
        r.status === 'borrowed' ? (
          <Space size={4}>
            {/* ⚠️ a glyph, not PrinterOutlined: a second importer made Rollup
                split that icon into a shared chunk named in the entry's
                preload map — +47 B on the sign-in critical path (Phase 18) */}
            <Tooltip title="Print the borrower's loan slip — its QR returns this loan in one scan">
              <Button size="small" aria-label={`Print slip #${r.id}`}
                data-testid={`loan-slip-${r.id}`} onClick={() => slip(r.id)}>🖨 Slip</Button>
            </Tooltip>
            <Button size="small" type="primary" loading={ret.isPending}
              data-testid={`return-row-${r.id}`} onClick={() => returnOne(r)}>
              Returned OK
            </Button>
            <Tooltip title="Return with a condition or a note">
              <Button size="small" onClick={() => {
                setScan({ code: `#${r.id}`, kind: 'loan', loans: [r], employee: null, material: null, message: '' })
                setPicked([Number(r.id)]); setCondition('ok'); setNote('')
                window.scrollTo({ top: 0, behavior: 'smooth' })
              }}>…</Button>
            </Tooltip>
          </Space>
        ) : null,
    },
  ]

  const visible = useMemo(() => {
    const rows = items.filter((r) =>
      view === 'all' ? true
        : view === 'returned' ? r.status === 'returned'
          : view === 'overdue' ? isOverdue(r)
            : r.status === 'borrowed')
    if (view === 'open' || view === 'overdue') {
      // Most urgent first: the earliest due date leads.
      return [...rows].sort((a, b) =>
        String(a.expected_return_time ?? '9999').localeCompare(String(b.expected_return_time ?? '9999')))
    }
    return rows
  }, [items, view, data?.now])

  const deskLoans = scan?.loans ?? []

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Returnable Items
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Tools and equipment on loan to employees. Overdue loans are flagged here and
        raise a one-time notification.
      </Typography.Paragraph>

      <GridRow gutter={[12, 12]} style={{ marginBottom: 12 }}>
        <Col xs={12} md={6}><Card size="small"><Statistic title="On loan" value={kpi.onLoan} /></Card></Col>
        <Col xs={12} md={6}>
          <Card size="small" hoverable onClick={() => setView('overdue')}>
            <Statistic title="Overdue" value={kpi.overdue}
              valueStyle={{ color: kpi.overdue ? statusColors.critical : undefined }} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="Due back today" value={kpi.dueToday}
              valueStyle={{ color: kpi.dueToday ? statusColors.low : undefined }} />
          </Card>
        </Col>
        <Col xs={12} md={6}><Card size="small"><Statistic title="Returned today" value={kpi.backToday} /></Card></Col>
      </GridRow>

      <Card size="small" title={<span><ScanOutlined /> Return desk</span>} style={{ marginBottom: 12 }}
        extra={
          <Space>
            <Tooltip title="When a scan matches exactly one loan, return it in good order straight away">
              <Space size={6}>
                <Switch size="small" checked={quick} data-testid="quick-return"
                  onChange={(v) => { setQuick(v); writeQuick(v) }} />
                <Typography.Text type="secondary">One-scan return</Typography.Text>
              </Space>
            </Tooltip>
            <Button type="primary" onClick={() => openLoan()}>Loan a tool</Button>
          </Space>
        }>
        <ScanBox testId="return-scan" onScan={onDeskScan} onEmptyEnter={confirmDesk}
          placeholder="Scan a badge or a tool — or type #loan id — then Enter"
          cameraTitle="Scan a badge or a tool with the camera"
          hint="A badge shows everything that person has out. Enter on the empty box confirms the return below." />

        {scan && scan.kind === 'material' && scan.material && (
          <Alert type="info" showIcon style={{ marginTop: 8 }}
            title={scan.message}
            action={<Button size="small" onClick={() => { openLoan(scan.material); clearDesk() }}>Loan it</Button>} />
        )}
        {scan && scan.kind === 'employee' && !deskLoans.length && (
          <Alert type="success" showIcon style={{ marginTop: 8 }} title={scan.message} />
        )}
        {deskLoans.length > 0 && (
          <div data-testid="return-panel" style={{ marginTop: 8 }}>
            <Typography.Text strong>
              {scan?.employee ? `${scan.employee.name} has ${deskLoans.length} item${deskLoans.length === 1 ? '' : 's'} out` : 'Matching loan'}
            </Typography.Text>
            <Checkbox.Group style={{ display: 'block', marginTop: 6 }} value={picked}
              onChange={(v) => setPicked(v as number[])}>
              <Space orientation="vertical" size={4} style={{ width: '100%' }}>
                {deskLoans.map((l) => {
                  const due = l.expected_return_time ? dayjs(String(l.expected_return_time)) : null
                  const late = !!due && due.isBefore(now)
                  return (
                    <Checkbox key={String(l.id)} value={Number(l.id)}>
                      <Space size={6} wrap>
                        <Typography.Text>#{String(l.id)} {String(l.material_name)}</Typography.Text>
                        <Typography.Text type="secondary">× {String(l.qty ?? 1)} {String(l.uom ?? '')}</Typography.Text>
                        {!scan?.employee && <Typography.Text type="secondary">— {String(l.borrower_name ?? '')}</Typography.Text>}
                        {due && <Tag color={late ? 'red' : 'default'}>{dueText(due, now)}</Tag>}
                      </Space>
                    </Checkbox>
                  )
                })}
              </Space>
            </Checkbox.Group>
            <Space wrap style={{ marginTop: 10 }}>
              <Segmented value={condition} onChange={(v) => setCondition(v as ReturnCondition)}
                options={CONDITIONS.map((c) => ({ value: c.value, label: c.label }))} />
              <Input style={{ width: 260 }} placeholder="Note (optional) — e.g. blade chipped"
                value={note} onChange={(e) => setNote(e.target.value)} onPressEnter={confirmDesk} />
              <Button type="primary" size="large" data-testid="return-confirm" loading={batch.isPending}
                disabled={!picked.length} onClick={confirmDesk}
                danger={condition !== 'ok'}>
                Return {picked.length} item{picked.length === 1 ? '' : 's'}
              </Button>
              <Button onClick={clearDesk}>Clear</Button>
            </Space>
          </div>
        )}
      </Card>

      <Space style={{ marginBottom: 8 }} wrap>
        <Segmented value={view} onChange={(v) => setView(v as typeof view)} data-testid="loan-view"
          options={[
            { value: 'open', label: `Open (${kpi.onLoan})` },
            { value: 'overdue', label: `Overdue (${kpi.overdue})` },
            { value: 'returned', label: 'Returned' },
            { value: 'all', label: 'All' },
          ]} />
      </Space>

      <Table sticky={{ offsetHeader: 64 }}
        size="small"
        loading={isFetching}
        columns={columns}
        dataSource={visible}
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
            <Button icon={<ScanOutlined />} onClick={() => setToolScanOpen(true)}>
              Scan tool
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
          <Form.Item name="sap_code" hidden><Input /></Form.Item>
          <Form.Item name="item_ref" hidden><Input /></Form.Item>
          <Form.Item name="material_name" label="Tool / item" rules={[{ required: true }]}
            extra={<Form.Item noStyle shouldUpdate>{() => {
              const s = form.getFieldValue('sap_code'), ref = form.getFieldValue('item_ref')
              return s || ref ? `Scanned: ${[s, ref && ref !== s ? ref : null].filter(Boolean).join(' · ')} — a return scan of this code finds the loan` : null
            }}</Form.Item>}>
            {toolAlts.length > 1 ? (
              <Select options={toolAlts.map((a) => ({ value: a, label: a }))}
                popupMatchSelectWidth={false} showSearch
                onChange={(v) => form.setFieldsValue({ material_name: v })} />
            ) : (
              <Input placeholder="e.g. Torque wrench — or Scan tool ↑" />
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
          <Form.Item label="Expected return" required style={{ marginBottom: 4 }}>
            <Space wrap style={{ marginBottom: 6 }}>
              {[
                { label: 'End of shift', at: () => shiftEnd(0) },
                { label: 'Tomorrow 17:00', at: () => shiftEnd(1) },
                { label: '+3 days', at: () => shiftEnd(3) },
                { label: '+1 week', at: () => shiftEnd(7) },
              ].map((p) => (
                <Button key={p.label} size="small" onClick={() => form.setFieldsValue({ due: p.at() })}>{p.label}</Button>
              ))}
            </Space>
            <Form.Item name="due" noStyle rules={[{ required: true, message: 'When is it due back?' }]}>
              <DatePicker showTime style={{ width: '100%' }} />
            </Form.Item>
          </Form.Item>
        </Form>
      </Modal>

      <QrScanner open={scanOpen} title="Scan employee badge"
        onClose={() => setScanOpen(false)} onDecode={onBadgeDecoded} />
      <QrScanner open={toolScanOpen} title="Scan the tool's sticker, serial or asset tag"
        formats={BARCODE_FORMATS} manualPlaceholder="…or type the code"
        onClose={() => setToolScanOpen(false)} onDecode={onToolDecoded} />
    </div>
  )
}
