/**
 * frontend/src/sme/ActualConsumption.tsx — the ACTUAL side of the SME portal.
 *
 * Two panels behind one tab, because they are two halves of one job:
 *
 *   Tank Aliases      resolve the workbook's `Tank No.` to real equipment
 *   Actual Draw       assign each logged consumption to equipment + SQM
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * ⚠️  THIS SCREEN NEVER REDUCES THE ESTIMATOR'S AVAILABILITY.
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Rule 1a keeps the estimator and the warehouse as two separately-calculated
 * pools: every SME quantity comes from `sme_inventory_seed` and a warehouse
 * event must not move one. So the physical draw recorded here is displayed as
 * a SIDE NOTE — "Actual Physical Balance" — beside the plan, and no arithmetic
 * is ever done between the two. The banner at the top of the tab says so, on
 * purpose: it is the thing that stops someone later "fixing" the estimator to
 * subtract these numbers.
 *
 * WHY ROWS ARRIVE UNASSIGNED. The Consumption Log states a quantity and a
 * `Tank No.`, but never a system code and never an area. `Tank No.` is also
 * frequently ambiguous — `TNK-091` matches BOTH `522-8J10-TNK-091` (TRAIN J)
 * and `522-8k10-TNK-091` (TRAIN K), and that is 39 of the 103 Surface-Shield
 * rows. The sync refuses to guess, so those rows land unassigned and this is
 * where a human resolves them.
 */
import { useMemo, useState } from 'react'
import {
  Alert, App, Badge, Button, Card, Empty, Form, InputNumber, Modal, Select,
  Space, Tag, Tooltip, Typography,
} from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { LinkOutlined, EditOutlined } from '@ant-design/icons'
import { Table } from '../lib/smartTable'
import { api } from '../api/client'
import type { Row } from '../api/client'
import { status } from '../theme/tokens'

const mono: React.CSSProperties = { fontVariantNumeric: 'tabular-nums' }
const nf = (n: unknown) => Number(n ?? 0).toLocaleString(undefined,
  { maximumFractionDigits: 3 })

interface Props { siteId?: string }

export default function ActualConsumption({ siteId }: Props) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const params = siteId ? { site_id: siteId } : {}

  const aliases = useQuery({
    queryKey: ['/sme/actuals/aliases', siteId],
    queryFn: () => api.get('/sme/actuals/aliases', { params }).then((r) => r.data),
  })
  const draw = useQuery({
    queryKey: ['/sme/actuals/consumption', siteId],
    queryFn: () => api.get('/sme/actuals/consumption', { params }).then((r) => r.data),
  })
  const equipment = useQuery({
    queryKey: ['/sme/master/equipment', siteId],
    queryFn: () => api.get('/sme/master/equipment', { params }).then((r) => r.data),
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['/sme/actuals/aliases'] })
    qc.invalidateQueries({ queryKey: ['/sme/actuals/consumption'] })
  }

  const resolveAlias = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Row }) =>
      api.patch(`/sme/actuals/aliases/${id}`, body).then((r) => r.data),
    onSuccess: (d) => {
      message.success(`Alias resolved — ${d.rows_tagged ?? 0} logged row(s) tagged`)
      invalidate()
    },
    onError: () => message.error('Could not resolve that alias'),
  })
  const assign = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Row }) =>
      api.patch(`/sme/actuals/consumption/${id}`, body).then((r) => r.data),
    onSuccess: () => { message.success('Assigned'); invalidate() },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      message.error(e?.response?.data?.detail ?? 'Could not assign that row'),
  })

  // tag → the system codes that tag actually carries. Assigning to a pair the
  // equipment master does not have is a 422 server-side, so the picker is
  // narrowed to real pairs rather than letting the user find that out.
  const tags = useMemo(() => {
    const m = new Map<string, string[]>()
    for (const e of (equipment.data?.items ?? []) as Row[]) {
      const t = String(e.Equipment_Tag_No ?? '')
      const c = String(e.Lining_System_Code ?? '')
      if (!t) continue
      m.set(t, [...(m.get(t) ?? []), c].filter(Boolean))
    }
    return m
  }, [equipment.data])

  const [aliasEdit, setAliasEdit] = useState<Row | null>(null)
  const [rowEdit, setRowEdit] = useState<Row | null>(null)

  const unresolved = aliases.data?.unresolved ?? 0
  const unassigned = draw.data?.unassigned ?? 0

  const aliasCols = [
    { title: 'Tank No. (as typed)', dataIndex: 'alias_raw', width: 180,
      render: (v: string) => <b style={mono}>{v}</b> },
    { title: 'Matching form', dataIndex: 'alias_norm', width: 140,
      render: (v: string) => <span style={{ ...mono, opacity: 0.65 }}>{v}</span> },
    { title: 'Rows held', dataIndex: 'row_count', width: 110, align: 'right' as const,
      render: (v: number) => <span style={mono}>{nf(v)}</span> },
    { title: 'Resolves to', dataIndex: 'Equipment_Tag_No', width: 220,
      render: (v: string | null, r: Row) => v
        ? <Tag color="green" style={mono}>{v}</Tag>
        : r.status === 'ignored'
          ? <Tag>not an equipment</Tag>
          : <Tag color="red">unresolved</Tag> },
    { title: 'Why', dataIndex: 'match_count', width: 260,
      render: (n: number, r: Row) => r.status !== 'unresolved'
        ? <span style={{ opacity: 0.6 }}>resolved by {String(r.resolved_by ?? 'sync')}</span>
        : n === 0
          ? <span>no equipment tag matches</span>
          : <span><b>{n}</b> equipment tags match — ambiguous</span> },
    { title: '', key: 'act', width: 110,
      render: (_: unknown, r: Row) => (
        <Button size="small" icon={<LinkOutlined />} onClick={() => setAliasEdit(r)}>
          Resolve
        </Button>) },
  ]

  const drawCols = [
    { title: 'Date', dataIndex: 'entry_date', width: 110 },
    // Code AND name. An operator working down this queue is deciding which
    // tank a drum went on; a column of bare GI-600001x asks them to know 400
    // codes by heart. The name wraps rather than ellipses (rule 5).
    { title: 'Material', dataIndex: 'Material_Code', width: 230,
      render: (v: string, r: Row) => (
        <div style={{ lineHeight: 1.25 }}>
          <span style={mono}>{v}</span>
          {r.Material_Name ? (
            <div style={{ fontSize: 11, opacity: 0.7, whiteSpace: 'normal',
                          wordBreak: 'break-word' }}>
              {String(r.Material_Name)}
            </div>) : null}
        </div>) },
    { title: 'Drawn', dataIndex: 'Actual_Qty', width: 100, align: 'right' as const,
      render: (v: number) => <b style={mono}>{nf(v)}</b> },
    { title: 'Equipment', dataIndex: 'Equipment_Tag_No', width: 200,
      render: (v: string) => v
        ? <span style={mono}>{v}</span>
        : <Tag color="red">unassigned</Tag> },
    { title: 'System', dataIndex: 'Lining_System_Code', width: 100,
      render: (v: string) => v || <span style={{ opacity: 0.4 }}>—</span> },
    { title: 'SQM done', dataIndex: 'SQM_Completed', width: 110, align: 'right' as const,
      render: (v: number) => v ? <span style={mono}>{nf(v)}</span>
        : <span style={{ opacity: 0.4 }}>—</span> },
    { title: 'Expected', dataIndex: 'Expected_Qty', width: 110, align: 'right' as const,
      render: (v: number, r: Row) => r.status === 'unassigned'
        ? <Tooltip title="Needs a system code and an area before an expectation exists">
            <span style={{ opacity: 0.4 }}>—</span></Tooltip>
        : <span style={mono}>{nf(v)}</span> },
    { title: 'Variance', dataIndex: 'Variance_Pct', width: 110, align: 'right' as const,
      render: (v: number | null) => v == null
        ? <span style={{ opacity: 0.4 }}>—</span>
        : <span style={{ ...mono, color: v > 0 ? status.critical : status.ok }}>
            {v > 0 ? '+' : ''}{nf(v)}%</span> },
    { title: 'Source', dataIndex: 'notes', ellipsis: true },
    { title: '', key: 'act', width: 110,
      render: (_: unknown, r: Row) => (
        <Button size="small" icon={<EditOutlined />} onClick={() => setRowEdit(r)}>
          Assign
        </Button>) },
  ]

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Alert
        type="info" showIcon
        message="Actual Physical Balance — a side note, not part of the plan"
        description={
          <span>
            These are quantities physically drawn, recorded from the workbook's
            Surface-Shield rows. The estimator's availability is deliberately{' '}
            <b>not</b> reduced by them: the plan comes from the material seed and
            the warehouse is tracked separately, so the two sit side by side and
            nothing is netted between them.
          </span>}
      />

      <Card size="small" title={
        <Space>
          <span>Tank aliases</span>
          {unresolved > 0 && <Badge count={unresolved} />}
        </Space>}
        extra={<span style={{ opacity: 0.65, fontSize: 12 }}>
          the workbook's Tank No. → real equipment
        </span>}>
        {unresolved > 0 && (
          <Alert type="warning" showIcon style={{ marginBottom: 12 }}
            message={`${unresolved} alias(es) are holding rows`}
            description="An alias that matches two pieces of equipment — TNK-091 is
              on both TRAIN J and TRAIN K — is never resolved automatically.
              Either answer would render plausibly in every report, so the rows
              wait here instead." />
        )}
        <Table rowKey="id" size="small" columns={aliasCols}
          dataSource={(aliases.data?.items ?? []) as Row[]}
          loading={aliases.isFetching} pagination={false}
          locale={{ emptyText: <Empty description="No tank aliases yet — run the Excel sync" /> }} />
      </Card>

      <Card size="small" title={
        <Space>
          <span>Actual draw</span>
          {unassigned > 0 && <Badge count={unassigned} />}
        </Space>}
        extra={<span style={{ opacity: 0.65, fontSize: 12 }}>
          assign equipment + the area it covered
        </span>}>
        <Table rowKey="id" size="small" columns={drawCols}
          dataSource={(draw.data?.items ?? []) as Row[]}
          loading={draw.isFetching}
          pagination={{ pageSize: 25, showSizeChanger: true }}
          locale={{ emptyText: <Empty description="Nothing logged yet" /> }} />
      </Card>

      {/* ── resolve one alias ── */}
      <Modal open={!!aliasEdit} onCancel={() => setAliasEdit(null)} footer={null}
        title={`Resolve ${aliasEdit?.alias_raw ?? ''}`} destroyOnHidden>
        {aliasEdit && (
          <Form layout="vertical"
            initialValues={{ Equipment_Tag_No: aliasEdit.Equipment_Tag_No ?? undefined,
                             status: aliasEdit.status === 'ignored' ? 'ignored' : 'mapped' }}
            onFinish={(v) => resolveAlias.mutate(
              { id: Number(aliasEdit.id), body: v },
              { onSuccess: () => setAliasEdit(null) })}>
            <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
              {Number(aliasEdit.row_count ?? 0)} logged row(s) are waiting on this.
              Mapping it tags all of them that are still unassigned.
            </Typography.Paragraph>
            <Form.Item name="status" label="Decision">
              <Select options={[
                { value: 'mapped', label: 'It is this equipment →' },
                { value: 'ignored', label: 'Not an equipment (a place or an activity)' },
              ]} />
            </Form.Item>
            <Form.Item noStyle shouldUpdate={(a, b) => a.status !== b.status}>
              {({ getFieldValue }) => getFieldValue('status') === 'mapped' && (
                <Form.Item name="Equipment_Tag_No" label="Equipment"
                  rules={[{ required: true, message: 'Pick the equipment' }]}>
                  <Select showSearch placeholder="Search equipment tags"
                    options={[...tags.keys()].sort().map((t) => ({ value: t, label: t }))} />
                </Form.Item>)}
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={resolveAlias.isPending}>
              Save
            </Button>
          </Form>)}
      </Modal>

      {/* ── assign one logged draw ── */}
      <Modal open={!!rowEdit} onCancel={() => setRowEdit(null)} footer={null}
        title="Assign this draw" destroyOnHidden>
        {rowEdit && (
          <Form layout="vertical"
            initialValues={{
              Equipment_Tag_No: rowEdit.Equipment_Tag_No || undefined,
              Lining_System_Code: rowEdit.Lining_System_Code || undefined,
              SQM_Completed: Number(rowEdit.SQM_Completed ?? 0) || undefined,
            }}
            onFinish={(v) => assign.mutate(
              { id: Number(rowEdit.id), body: { ...v, SQM_Completed: v.SQM_Completed ?? 0 } },
              { onSuccess: () => setRowEdit(null) })}>
            <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
              <b style={mono}>{nf(rowEdit.Actual_Qty)}</b> of{' '}
              <b style={mono}>{String(rowEdit.Material_Code)}</b>
              {rowEdit.Material_Name ? ` — ${String(rowEdit.Material_Name)}` : ''}
              {' '}drawn on {String(rowEdit.entry_date)}.<br />
              <span style={{ fontSize: 12 }}>{String(rowEdit.notes ?? '')}</span>
            </Typography.Paragraph>
            <Form.Item name="Equipment_Tag_No" label="Equipment"
              rules={[{ required: true, message: 'Pick the equipment' }]}>
              <Select showSearch placeholder="Search equipment tags"
                options={[...tags.keys()].sort().map((t) => ({ value: t, label: t }))} />
            </Form.Item>
            <Form.Item noStyle shouldUpdate>
              {({ getFieldValue }) => (
                <Form.Item name="Lining_System_Code" label="Lining system code"
                  rules={[{ required: true, message: 'Pick the system code' }]}
                  extra="Only the codes this equipment actually carries are listed.">
                  <Select placeholder="System code"
                    options={(tags.get(String(getFieldValue('Equipment_Tag_No') ?? '')) ?? [])
                      .map((c) => ({ value: c, label: c }))} />
                </Form.Item>)}
            </Form.Item>
            <Form.Item name="SQM_Completed" label="SQM actually covered"
              extra="The workbook records a quantity issued, never an area — so this
                     is typed by hand, and it is what makes the variance mean anything.">
              <InputNumber min={0} step={0.1} style={{ width: '100%' }} />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={assign.isPending}>
              Assign
            </Button>
          </Form>)}
      </Modal>
    </Space>
  )
}
