/**
 * Phase 8 slice 8e — 🔗 Session Report For MP&H.
 *
 * The SME Session Builder answers "what can we build with the material on
 * site?". The Manpower Planner answers "how many people does this work need?".
 * Neither could answer the question people actually ask at the morning
 * meeting, which is both at once:
 *
 *   WE CAN DO   the labour for the area the PHYSICAL stock supports — the
 *               only column you can act on today
 *   OVERALL     the whole remaining job, materials no object
 *   BLOCKED     the difference: the size of the delay, and what is causing it
 *
 * ⚠️ THE BLOCKED COLUMN SHOWS NO HEADCOUNT, HERE OR IN THE EXPORT. Labour you
 * cannot deploy because the material has not landed is not a hiring
 * requirement; a number in that cell is a number somebody hires against, and
 * they would be idle when the drums arrive. The column shows man-hours and
 * crew-shifts — how big the delay is — plus the materials responsible. The
 * per-role "to assign" figure is likewise measured against CAN-DO only.
 *
 * ⚠️ THE SESSION ARRIVES IN THE URL, and nothing global is touched.
 * `ScenarioProvider` lives inside SmePage; this page is a different route and
 * reads `?scenario=` / `?codes=` with the SAME encoder the SME page writes
 * with (`sme/ScenarioContext`). Changing the target days here cannot disturb
 * the planning session someone left open in the other tab.
 */
import { useEffect, useMemo, useState } from 'react'
import {
  Alert, Button, Card, Col, Collapse, Descriptions, Empty, InputNumber, Radio,
  Row, Segmented, Space, Statistic, Switch, Table, Tag, Tooltip, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  DownloadOutlined, ReloadOutlined, ThunderboltOutlined,
} from '@ant-design/icons'
import { useMutation } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'

import { api } from '../api/client'
import { postDownloadDocument } from '../api/hooks'
import KpiRow from '../components/KpiRow'
import MultiSelectAll from '../sme/MultiSelectAll'
import { decodeTags } from '../sme/ScenarioContext'
import SystemCode from '../sme/SystemCode'
import { status } from '../theme/tokens'

type Row = Record<string, unknown>

const errMsg = (e: unknown): string => {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Request failed'
}
const n2 = (v: unknown) => (v == null ? '—' : Number(v).toFixed(2))
const n0 = (v: unknown) => (v == null ? '—' : Math.round(Number(v)).toLocaleString())

/** The three columns, in the order the question is asked. */
const COLS = [
  { key: 'can_do', title: 'We can do now', hint: 'Costed from the area the material physically on site supports. This is the part you can start today.', tone: status.ok },
  { key: 'overall', title: 'Overall total', hint: 'The whole remaining job, materials no object.', tone: undefined },
  { key: 'blocked', title: 'Blocked by material', hint: 'The difference — how big the delay is. Deliberately shows no headcount.', tone: status.critical },
] as const

export default function SessionManpowerReport({ site }: { site?: string }) {
  const [params] = useSearchParams()
  // Read ONCE, into local state. Re-reading on every render would fight the
  // controls below the moment somebody edited the URL, and writing back would
  // be the "polluting the global state" this handoff exists to avoid.
  const [order, setOrder] = useState<string[]>(() => decodeTags(params.get('scenario')))
  const [codes, setCodes] = useState<string[]>(() => decodeTags(params.get('codes')))
  const [days, setDays] = useState<number>(5)
  const [mode, setMode] = useState<'days' | 'hours'>('days')
  const [hours, setHours] = useState<number>(55)
  const [autoShifts, setAutoShifts] = useState(true)
  const [shifts, setShifts] = useState<1 | 2>(2)
  const [result, setResult] = useState<Row | null>(null)

  const body = useMemo(() => ({
    priority_order: order,
    lining_system_codes: codes,
    ...(mode === 'days' ? { target_days: days } : { deadline_hours: hours }),
    ...(autoShifts ? {} : { shifts_per_day: shifts }),
    ...(site ? { site_id: site } : {}),
  }), [order, codes, mode, days, hours, autoShifts, shifts, site])

  const run = useMutation({
    mutationFn: (b: Row) =>
      api.post('/mh/planner/session', b).then((r) => r.data),
    onSuccess: (d) => setResult(d as Row),
  })

  // Arriving from the SME page with a session in the URL runs the report at
  // once: the operator has already said what they want by pressing the button.
  useEffect(() => {
    if (order.length) run.mutate(body as Row)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const [exporting, setExporting] = useState<string | null>(null)
  const download = async (format: string) => {
    setExporting(format)
    try {
      await postDownloadDocument('/mh/planner/session/export',
        { ...body, format }, `session-mph.${format}`)
    } finally { setExporting(null) }
  }

  const cols = (result?.columns as Record<string, Row>) ?? {}
  const inputs = result?.inputs as Row | undefined
  const cascade = result?.cascade as Row | undefined
  const warnings = (result?.warnings as string[]) ?? []
  const jobs = (result?.jobs as Row[]) ?? []
  const byRole = (result?.by_role as Row[]) ?? []
  const materials = (result?.materials_blocking as Row[]) ?? []
  const shiftsPerDay = Number(inputs?.shifts_per_day ?? 1)

  const jobCols: ColumnsType<Row> = [
    { title: '#', dataIndex: 'Priority_Rank', width: 50, align: 'right' },
    { title: 'Equipment', dataIndex: 'Equipment_Tag_No', width: 160 },
    { title: 'System', key: 'sys', width: 210,
      render: (_: unknown, r: Row) => {
        const j = r.Job as Row | undefined
        return <SystemCode code={String(r.Lining_System_Code ?? '')}
          type={String(j?.Type ?? '')} name={String(r.System_Name ?? '')} />
      } },
    { title: 'Can-do m²', dataIndex: 'Can_Do_SQM', width: 110, align: 'right',
      render: n2 },
    { title: 'Overall m²', dataIndex: 'Overall_SQM', width: 110, align: 'right',
      render: n2 },
    { title: 'Blocked m²', dataIndex: 'Blocked_SQM', width: 110, align: 'right',
      render: (v: unknown) => (
        <span style={{ color: Number(v ?? 0) > 0 ? status.critical : undefined }}>
          {n2(v)}</span>) },
    { title: 'Can-do man-hrs', dataIndex: 'Can_Do_Manhours', width: 130,
      align: 'right', render: n0 },
    { title: 'Overall man-hrs', dataIndex: 'Overall_Manhours', width: 135,
      align: 'right', render: n0 },
    { title: 'Blocked man-hrs', dataIndex: 'Blocked_Manhours', width: 135,
      align: 'right', render: n0 },
    { title: 'What is stopping it', key: 'bn', width: 230,
      render: (_: unknown, r: Row) => {
        const b = r.Bottleneck as Row | null
        if (!b) return r.Has_Recipe === false
          ? <Tag color="orange">no recipe — unmodelled</Tag>
          : <Tag color="green">nothing</Tag>
        return (
          <Tooltip title={`${n2(b.Shortfall_Available_Qty)} ${String(b.UOM ?? '')} short of physical stock`}>
            <span><Tag color="red">{String(b.Material_Code)}</Tag>
              <span style={{ fontSize: 12, opacity: 0.75 }}>
                {String(b.Material_Name ?? '').slice(0, 28)}</span></span>
          </Tooltip>)
      } },
  ]

  const matCols: ColumnsType<Row> = [
    { title: 'Material', dataIndex: 'Material_Code', width: 150 },
    { title: 'SAP', dataIndex: 'SAP_Code', width: 130 },
    { title: 'Name', dataIndex: 'Material_Name', ellipsis: true },
    { title: 'UOM', dataIndex: 'UOM', width: 70 },
    { title: 'Short now', dataIndex: 'Short_Now_Qty', width: 120,
      align: 'right', render: n2 },
    { title: 'Short after open POs', dataIndex: 'Short_Net_Qty', width: 160,
      align: 'right',
      render: (v: unknown) => (
        <Tooltip title="What is still missing once everything already on order
          has landed. Zero here means the purchase exists and has not arrived.">
          <span>{n2(v)}</span></Tooltip>) },
    { title: 'Jobs held up', dataIndex: 'Blocks_Job_Count', width: 110,
      align: 'right' },
    { title: 'Of which it is the bottleneck', dataIndex: 'Bottleneck_Job_Count',
      width: 200, align: 'right',
      render: (v: unknown) => (
        <Tooltip title="A unit can be short of several materials while only the
          scarcest decides how much of it can be built. Those are the ones to
          buy first.">
          <span>{Number(v ?? 0) > 0
            ? <Tag color="red">{String(v)}</Tag> : String(v ?? 0)}</span>
        </Tooltip>) },
  ]

  return (
    <div>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        The SME planning session, costed in labour. <b>We can do</b> is what the
        material on site actually supports; <b>blocked</b> is the same work
        waiting on a delivery. Surface prep is not included — blasting consumes
        no recipe line, so the material model has no opinion on it; use the
        🧠 Manpower Planner for that.
      </Typography.Paragraph>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Row gutter={[12, 12]} align="bottom">
          <Col xs={24} md={9}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Session — priority order (top gets the material first)
            </Typography.Text>
            <MultiSelectAll value={order} onChange={(v) => setOrder(v)}
              placeholder="Arrives from the SME Session Builder"
              style={{ width: '100%' }} id="session_order"
              options={order.map((t) => ({ value: t, label: t }))} />
          </Col>
          <Col xs={24} md={6}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              System codes {codes.length ? '(from your SME filter)' : ''}
            </Typography.Text>
            <MultiSelectAll value={codes} onChange={(v) => setCodes(v)}
              placeholder="All systems in the session" id="session_codes"
              style={{ width: '100%' }}
              options={codes.map((c) => ({ value: c, label: c }))} />
          </Col>
          <Col xs={24} md={4}>
            <Segmented value={mode} onChange={(v) => setMode(v as 'days' | 'hours')}
              options={[{ label: 'Target days', value: 'days' },
                        { label: 'Hours/person', value: 'hours' }]}
              style={{ marginBottom: 6 }} />
            {mode === 'days'
              ? <InputNumber min={0.5} step={1} value={days} id="session_days"
                  onChange={(v) => setDays(Number(v ?? 1))} style={{ width: '100%' }} />
              : <InputNumber min={0.5} step={1} value={hours} id="session_hours"
                  onChange={(v) => setHours(Number(v ?? 1))} style={{ width: '100%' }} />}
          </Col>
          <Col xs={24} md={5}>
            <Tooltip title="Auto reads the roster: two if anyone in a required
              role is on nights. You can force two anyway.">
              <Space style={{ marginBottom: 6 }}>
                <Switch checked={autoShifts} onChange={setAutoShifts}
                  checkedChildren="auto" unCheckedChildren="manual" />
                {!autoShifts && (
                  <Radio.Group optionType="button" buttonStyle="solid" size="small"
                    value={shifts} onChange={(e) => setShifts(e.target.value)}
                    options={[{ label: 'Day', value: 1 },
                              { label: 'Day + Night', value: 2 }]} />
                )}
              </Space>
            </Tooltip>
            <Space.Compact style={{ width: '100%' }}>
              <Button type="primary" icon={<ThunderboltOutlined />} block
                loading={run.isPending} disabled={!order.length}
                onClick={() => run.mutate(body as Row)}>
                Cost it
              </Button>
              <Tooltip title="The material picture is cached for about a minute
                so changing the target days is instant. This re-reads stock.">
                <Button icon={<ReloadOutlined />} loading={run.isPending}
                  disabled={!order.length}
                  onClick={() => run.mutate({ ...body, refresh: true } as Row)} />
              </Tooltip>
            </Space.Compact>
          </Col>
        </Row>
      </Card>

      {run.isError && (
        <Alert type="error" showIcon style={{ marginBottom: 12 }}
          message={errMsg(run.error)} />
      )}

      {!result && !run.isPending && (
        <Empty description={order.length
          ? 'Press "Cost it" to price this session in labour'
          : 'Open the SME Session Builder, add equipment, and press 📊 Session Report For MP&H'} />
      )}

      {result && (
        <>
          {warnings.length > 0 && (
            <Alert type="warning" showIcon style={{ marginBottom: 16 }}
              message="Read these before trusting the numbers"
              description={<ul style={{ margin: 0, paddingLeft: 18 }}>
                {warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>} />
          )}

          {/* ── the three columns ──────────────────────────────────────── */}
          <KpiRow min={280} style={{ marginBottom: 16 }}>
            {COLS.map((c) => {
              const d = cols[c.key] ?? {}
              const blocked = c.key === 'blocked'
              return (
                <Card size="small" key={c.key}
                    title={<Tooltip title={c.hint}><span>{c.title}</span></Tooltip>}
                    extra={<Typography.Text type="secondary" style={{ fontSize: 11 }}>
                      {String(d.Basis ?? '')}</Typography.Text>}>
                    <Statistic title="Man-hours" value={n0(d.Manhours)}
                      valueStyle={{ color: c.tone }} />
                    <Descriptions size="small" column={1} style={{ marginTop: 10 }}>
                      <Descriptions.Item label="Area">
                        {n2(d.SQM)} m²</Descriptions.Item>
                      <Descriptions.Item label="Crew-shifts of work">
                        {n2(d.Crew_Shifts)}</Descriptions.Item>
                      <Descriptions.Item label="Headcount for the target">
                        {blocked
                          ? <Tag color="default">not applicable</Tag>
                          : <b>{String(d.Required_Headcount_Rounded ?? '—')}</b>}
                      </Descriptions.Item>
                      <Descriptions.Item label={shiftsPerDay === 2
                        ? 'Day / night crew' : 'Per shift'}>
                        {blocked ? '—' : shiftsPerDay === 2
                          ? `${String(d.Required_Day_Headcount ?? '—')} / ${String(d.Required_Night_Headcount ?? '—')}`
                          : String(d.Headcount_Per_Shift ?? '—')}
                      </Descriptions.Item>
                      <Descriptions.Item label="Days at current roster">
                        {blocked ? '—' : n2(d.Days_With_Current_Roster)}
                      </Descriptions.Item>
                    </Descriptions>
                    {blocked && (
                      <Typography.Paragraph type="secondary"
                        style={{ marginTop: 4, marginBottom: 0, fontSize: 12 }}>
                        No headcount is shown here on purpose: you cannot deploy
                        labour against material that has not arrived, and a
                        number in this cell is a number somebody hires against.
                      </Typography.Paragraph>
                    )}
                </Card>
              )
            })}
          </KpiRow>

          <Descriptions size="small" column={{ xs: 1, md: 4 }} bordered
            style={{ marginBottom: 16 }}>
            <Descriptions.Item label="Session">
              {String((inputs?.priority_order as string[])?.length ?? 0)} equipment
              {' · '}{String(cascade?.jobs_costed ?? 0)} job(s) costed
            </Descriptions.Item>
            <Descriptions.Item label="Deadline">
              {n2(inputs?.target_days)} days = {n2(inputs?.deadline_hours)} h per person
            </Descriptions.Item>
            <Descriptions.Item label="Shifts per day">
              <Tag color={shiftsPerDay === 2 ? 'blue' : undefined}>
                {shiftsPerDay === 2 ? 'Day + Night' : 'Day only'}</Tag>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {String(inputs?.shifts_per_day_source) === 'operator'
                  ? 'set by you' : 'read from the roster'}
              </Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="Material picture">
              <Tooltip title="The cascade is the heaviest read in the system and
                nothing about it depends on the deadline, so it is reused for
                about a minute. Press the reload button to re-read stock.">
                <span>{cascade?.cached
                  ? `cached ${n0(cascade?.age_seconds)}s ago`
                  : 'read just now'}</span>
              </Tooltip>
            </Descriptions.Item>
          </Descriptions>

          {shiftsPerDay === 2 && (
            <Alert type="info" showIcon style={{ marginBottom: 16 }}
              message="Nights buy time, not a smaller payroll"
              description={(
                <>
                  {String(cols.can_do?.Required_Headcount_Rounded ?? 0)} people
                  are still needed in total for the startable work — nobody works
                  both shifts, so running nights does not reduce the hiring. The
                  crew splits{' '}
                  <strong>
                    {String(cols.can_do?.Required_Day_Headcount ?? 0)} day
                    {' / '}
                    {String(cols.can_do?.Required_Night_Headcount ?? 0)} night
                  </strong>
                  {cols.can_do?.Shift_Split_Basis === 'roster'
                    ? ', in the proportion your roster actually runs.'
                    : ' — an assumed split; there is no night crew on the roster to derive a real proportion from.'}
                </>
              )} />
          )}

          <Space style={{ marginBottom: 12 }} wrap>
            <Button icon={<DownloadOutlined />} loading={exporting === 'xlsx'}
              onClick={() => download('xlsx')}>Excel</Button>
            <Button icon={<DownloadOutlined />} loading={exporting === 'csv'}
              onClick={() => download('csv')}>CSV</Button>
            <Button icon={<DownloadOutlined />} loading={exporting === 'pdf'}
              onClick={() => download('pdf')}>PDF</Button>
          </Space>

          {/* ── per role, collapsible ──────────────────────────────────── */}
          <Typography.Title level={5}>Per role</Typography.Title>
          <Collapse style={{ marginBottom: 20 }}
            items={byRole.map((g) => ({
              key: String(g.Role_Code),
              label: (
                <Row gutter={8} align="middle" style={{ width: '100%' }}>
                  <Col flex="190px"><strong>{String(g.Role_Code)}</strong></Col>
                  <Col flex="auto">
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      can do <strong>{n0(g.Can_Do_Manhours)}</strong> of{' '}
                      <strong>{n0(g.Overall_Manhours)}</strong> man-hours
                      {' · '}need <strong>{String(g.Can_Do_Headcount_Rounded)}</strong>
                      {' · '}have <strong>{String(g.Available_Headcount)}</strong>
                      {' · '}
                      {Number(g.To_Assign ?? 0) > 0
                        ? <Tag color="red">assign {String(g.To_Assign)}</Tag>
                        : <Tag color="green">covered</Tag>}
                      {Number(g.Blocked_Manhours ?? 0) > 0 && (
                        <Tag color="orange" style={{ marginLeft: 6 }}>
                          {n0(g.Blocked_Manhours)} blocked</Tag>)}
                    </Typography.Text>
                  </Col>
                </Row>
              ),
              children: (
                <>
                  <Descriptions size="small" column={{ xs: 2, md: 4 }} bordered>
                    <Descriptions.Item label="Can-do man-hours">
                      {n0(g.Can_Do_Manhours)}</Descriptions.Item>
                    <Descriptions.Item label="Overall man-hours">
                      {n0(g.Overall_Manhours)}</Descriptions.Item>
                    <Descriptions.Item label="Blocked man-hours">
                      {n0(g.Blocked_Manhours)}</Descriptions.Item>
                    <Descriptions.Item label="Blocked headcount">
                      <Tooltip title="Absent on purpose — see the Blocked card.">
                        <Tag>not applicable</Tag></Tooltip>
                    </Descriptions.Item>
                    <Descriptions.Item label="Can-do headcount">
                      {n2(g.Can_Do_Headcount)}</Descriptions.Item>
                    <Descriptions.Item label="Per shift">
                      {String(g.Can_Do_Per_Shift)}</Descriptions.Item>
                    <Descriptions.Item label="Overall headcount">
                      {n2(g.Overall_Headcount)}</Descriptions.Item>
                    <Descriptions.Item label="To assign">
                      <Tooltip title="Measured against CAN-DO, never the overall
                        — hiring for work whose material has not landed puts
                        people on site with nothing to do.">
                        <span>{String(g.To_Assign)}</span></Tooltip>
                    </Descriptions.Item>
                  </Descriptions>
                  {((g.Jobs as Row[]) ?? []).length > 0 && (
                    <Table size="small" pagination={false} style={{ marginTop: 8 }}
                      dataSource={(g.Jobs as Row[]) ?? []}
                      rowKey={(r) => String(r.Job)}
                      columns={[
                        { title: 'Which job asked for this role', dataIndex: 'Job' },
                        { title: 'Can do', dataIndex: 'Can_Do_Manhours',
                          width: 110, align: 'right', render: n0 },
                        { title: 'Overall', dataIndex: 'Overall_Manhours',
                          width: 110, align: 'right', render: n0 },
                        { title: 'Blocked', dataIndex: 'Blocked_Manhours',
                          width: 110, align: 'right', render: n0 },
                      ]} />
                  )}
                </>
              ),
            }))} />

          {/* ── per job ────────────────────────────────────────────────── */}
          <Typography.Title level={5}>Per job, in session priority order</Typography.Title>
          <Table size="small" pagination={false} columns={jobCols} dataSource={jobs}
            rowKey={(r) => `${r.Equipment_Tag_No}|${r.Lining_System_Code}`}
            scroll={{ x: 'max-content' }} style={{ marginBottom: 20 }} />

          {/* ── what is in the way ─────────────────────────────────────── */}
          <Typography.Title level={5}>What is in the way</Typography.Title>
          {materials.length === 0 ? (
            <Alert type="success" showIcon
              message="Nothing is short — the whole session can start on the material already on site" />
          ) : (
            <Table size="small" columns={matCols} dataSource={materials}
              rowKey={(r) => `${r.Material_Code}|${r.SAP_Code}`}
              scroll={{ x: 'max-content' }}
              pagination={{ pageSize: 10, showTotal: (t) => `${t} materials` }} />
          )}
        </>
      )}
    </div>
  )
}
