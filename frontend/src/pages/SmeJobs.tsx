import { useEffect, useMemo, useState } from 'react'
import {
  Alert, App, Button, Card, Empty, Form, Input, InputNumber, Modal, Select,
  Space, Table, Tag, Tooltip, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import SystemCode from '../sme/SystemCode'
import { fmtPackBase } from '../lib/units'

/**
 * Phase 14c — the Surface Shield attribution queue, ONE JOB AT A TIME.
 *
 * ⚠️ A JOB IS ONE DAY ON ONE PIECE OF EQUIPMENT. The Phase 13 queue asked for a
 * system code, a tag and an area on every material; a four-component PU job
 * was four identical questions, and on approval credited its area four times
 * (defect D3). Here the day's draws against one vessel are one card, the
 * system code is SUGGESTED from the materials, and the area is asked ONCE.
 *
 * ⚠️ SPLIT IS UNTICKING. Two systems applied to one vessel on one day are two
 * submissions: tick the materials of the first, submit, and the rest stay on
 * the card for the second (ruling Q14-10).
 *
 * ⚠️ THE HOD DECIDES THE WHOLE JOB (Q14-11). Approving credits the job's area
 * once; rejecting sends every material back with the one reason.
 */

type Row = Record<string, unknown>
const s = (v: unknown) => (v == null ? '' : String(v))
const n = (v: unknown) => (v == null ? null : Number(v))
const r4 = (v: number) => Number(v.toFixed(4))

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } } }
  return x?.response?.data?.detail ?? 'Something went wrong'
}

interface Candidate {
  code: string; name?: string | null; coverage: number
  covered: string[]; missing: string[]; hinted: boolean
}
interface Job {
  key: string; site_id: string; work_date: string; tag: string
  rows: Row[]; rejected: { consumption_id: number; reason: string; by?: string }[]
  edited: number; candidates: Candidate[]
  suggested_code: string | null; sqm_hint: number | null
}
interface QueueResp {
  groups: Job[]; total_rows: number
  unmapped: { site_id: string; tank_no: string; rows: number }[]
  excluded_non_equipment: number
}

const sapKey = (v: unknown) => s(v).replace(/\s+/g, '')

/** "189 KG · 21 Can" for a Surface Shield container; the plain figure otherwise. */
function Drawn({ r }: { r: Row }) {
  const dual = fmtPackBase(r.sap_code, r.quantity, s(r.uom))
  if (dual) return <>{dual}</>
  const q = n(r.quantity)
  return <>{q == null ? '—' : r4(q)} {s(r.uom)}</>
}

function JobCard({ job }: { job: Job }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [code, setCode] = useState<string | undefined>(job.suggested_code ?? undefined)
  const [sqm, setSqm] = useState<number | null>(job.sqm_hint)
  const cand = job.candidates.find((c) => c.code === code)
  const inRecipe = useMemo(() => new Set(cand?.covered ?? []), [cand])
  // ⚠️ The default selection is what the chosen system's recipe contains: the
  // toluene drawn beside a PU job is not a PU component, and ticking it would
  // compare it against a benchmark that does not exist.
  const defaultSel = (c?: Candidate) => job.rows
    .filter((r) => !c || c.coverage === 0 || c.covered.includes(sapKey(r.sap_code)))
    .map((r) => Number(r.consumption_id))
  const [sel, setSel] = useState<number[]>(() => defaultSel(cand))
  // A refetch can add or remove rows under an open card (a store keeper posts
  // another draw for the same tag and day). Keep the ticks that still exist;
  // a NEW row arrives unticked — the supervisor decides about it, not a default.
  const ids = useMemo(() => job.rows.map((r) => Number(r.consumption_id)), [job.rows])
  useEffect(() => {
    setSel((cur) => (cur.every((id) => ids.includes(id)) ? cur : cur.filter((id) => ids.includes(id))))
  }, [ids])
  const rejected = job.rejected.length > 0

  const submit = useMutation({
    mutationFn: async () => (await api.post('/execution/sme-link/groups', {
      work_date: job.work_date, tag: job.tag, code, sqm,
      consumption_ids: sel, site_id: job.site_id,
    })).data,
    onSuccess: () => {
      message.success(sel.length < job.rows.length
        ? `Submitted ${sel.length} material(s) — the rest stay here for another system code`
        : 'Submitted — the job now goes to the HOD as one approval')
      void qc.invalidateQueries({ queryKey: ['/execution/sme-link/groups'] })
      void qc.invalidateQueries({ queryKey: ['/execution/sme-link/groups/staged'] })
      void qc.invalidateQueries({ queryKey: ['/execution/sme-link/assigned'] })
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const cols: ColumnsType<Row> = [
    { title: 'Material', key: 'm',
      render: (_: unknown, r: Row) => (
        <Space direction="vertical" size={0}>
          <span>{s(r.material_name) || s(r.sap_code)}</span>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            SAP {s(r.sap_code)}{r.material_code ? ` · ${s(r.material_code)}` : ''}
          </Typography.Text>
        </Space>) },
    { title: 'Drawn', key: 'q', align: 'right', render: (_: unknown, r: Row) => <Drawn r={r} /> },
    { title: code ? `In ${code}?` : 'In recipe?', key: 'in', width: 120,
      render: (_: unknown, r: Row) => (!code ? null : inRecipe.has(sapKey(r.sap_code))
        ? <Tag color="green">in recipe</Tag>
        : <Tooltip title="This system's recipe does not list this material — untick it and submit it separately, or it will have no benchmark.">
            <Tag color="orange">not in recipe</Tag>
          </Tooltip>) },
    { title: '', key: 'st', width: 130,
      render: (_: unknown, r: Row) => (s(r.reason) === 'rejected'
        ? <Tag color="red">rejected</Tag>
        : s(r.reason) === 'edited' ? <Tag color="orange">edited in Excel</Tag> : null) },
  ]

  return (
    <Card size="small" className="gi-job-card" data-job={job.key}
      style={{ marginBottom: 10, borderColor: rejected ? '#ff4d4f' : undefined }}
      title={<Space wrap>
        <strong>{job.work_date}</strong>
        <span>·</span>
        <strong>{job.tag}</strong>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>{job.site_id}</Typography.Text>
        <Tag>{job.rows.length} material(s)</Tag>
        {rejected && <Tag color="red">Rejected - Needs Correction</Tag>}
        {job.edited > 0 && <Tag color="orange">Edited in Excel</Tag>}
      </Space>}>
      {rejected && (
        <Alert type="error" showIcon style={{ marginBottom: 8 }}
          title="The HOD sent this job back"
          description={<>
            <Typography.Text strong>“{job.rejected[0].reason || 'No reason recorded'}”</Typography.Text>
            {job.rejected[0].by ? ` — ${job.rejected[0].by}` : ''}. Correct the system
            code, the area or the materials and submit again.
          </>} />
      )}
      <Table size="small" pagination={false} columns={cols} dataSource={job.rows}
        rowKey={(r) => String(r.consumption_id)}
        // ⚠️ ONE key type end to end (Phase 15b). rowKey is a string, so the
        // selection handed back to antd must be strings too: antd compares
        // strictly, and 123 !== "123" left every box unticked and each click
        // replacing the whole selection with one row ("Submit 1").
        rowSelection={{ selectedRowKeys: sel.map(String), onChange: (k) => setSel(k.map(Number)) }} />
      <Space wrap align="end" style={{ marginTop: 10 }}>
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
            System code
          </Typography.Text>
          <Select style={{ minWidth: 280 }} value={code} placeholder="Which system?"
            aria-label="System code"
            onChange={(v) => {
              setCode(v)
              setSel(defaultSel(job.candidates.find((c) => c.code === v)))
            }}
            options={job.candidates.map((c) => ({
              value: c.code,
              label: `${c.code}${c.name ? ` — ${c.name}` : ''} · covers ${c.covered.length} of ${job.rows.length}${c.hinted ? ' · noted' : ''}`,
            }))} />
        </div>
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
            Area covered (m²) — once for the job
          </Typography.Text>
          <InputNumber min={0.01} step={1} value={sqm ?? undefined} aria-label="Area covered"
            onChange={(v) => setSqm(v == null ? null : Number(v))} style={{ width: 160 }} />
        </div>
        <Button type="primary" danger={rejected} loading={submit.isPending}
          disabled={!code || !sqm || sel.length === 0}
          onClick={() => submit.mutate()}>
          {rejected ? 'Correct & resubmit' : `Submit ${sel.length} to the HOD`}
        </Button>
      </Space>
      {job.sqm_hint != null && sqm === job.sqm_hint && (
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: '6px 0 0' }}>
          Area pre-filled from the store keeper’s note — confirm it or correct it.
        </Typography.Paragraph>
      )}
      {sel.length > 0 && sel.length < job.rows.length && (
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: '6px 0 0' }}>
          <strong>Split:</strong> the {job.rows.length - sel.length} unticked material(s) stay
          on this card for another system code.
        </Typography.Paragraph>
      )}
    </Card>
  )
}

/** Shared by the queue and the tab label — one request, one cache entry. */
export function useJobQueue() {
  return useQuery({
    queryKey: ['/execution/sme-link/groups'],
    queryFn: async () => (await api.get<QueueResp>('/execution/sme-link/groups')).data,
  })
}

export function useStagedJobs() {
  return useQuery({
    queryKey: ['/execution/sme-link/groups/staged'],
    queryFn: async () => (await api.get<{ items: StagedJob[] }>('/execution/sme-link/groups/staged')).data.items,
  })
}

/** The field's side: one card per job still needing an area. */
export function JobQueue() {
  const [shown, setShown] = useState(10)
  const [find, setFind] = useState('')
  const q = useJobQueue()
  const all = q.data?.groups ?? []
  const rejected = all.filter((j) => j.rejected.length).length
  const needle = find.trim().toLowerCase()
  const jobs = needle
    ? all.filter((j) => `${j.tag} ${j.work_date} ${j.site_id}`.toLowerCase().includes(needle))
    : all
  return (
    <>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        Surface Shield material issued from the store with no area recorded
        against it, <strong>grouped by day and equipment</strong> — one card per
        job, the system code suggested from the materials, the area asked
        once. Rejected jobs first, then oldest first. This list reaches back over
        the whole ledger.
        {' '}Recording an area moves no stock: the material left the store
        when it was issued.
      </Typography.Paragraph>
      {rejected > 0 && (
        <Alert type="error" showIcon style={{ marginBottom: 8 }}
          title={`${rejected} job(s) rejected by the HOD — at the top, needs correction`} />
      )}
      {(q.data?.unmapped?.length ?? 0) > 0 && (
        <Alert type="warning" showIcon style={{ marginBottom: 8 }}
          title="Some draws name a tank no equipment answers to"
          description={<>
            {q.data!.unmapped.slice(0, 8).map((u) => (
              <Tag key={`${u.site_id}|${u.tank_no}`}>{u.tank_no || '(blank)'} · {u.rows}</Tag>
            ))}
            {q.data!.unmapped.length > 8 ? ` +${q.data!.unmapped.length - 8} more. ` : ' '}
            Map them to equipment in SME → Tank Aliases and they join a job here.
          </>} />
      )}
      {(q.data?.excluded_non_equipment ?? 0) > 0 && (
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          {q.data!.excluded_non_equipment} draw(s) marked Others / To Site / Scaffolding
          and the like are left out: they stay in stock figures and credit no area.
        </Typography.Paragraph>
      )}
      <Input.Search allowClear placeholder="Find a job by equipment tag or date (YYYY-MM-DD)"
        aria-label="Find a job" style={{ maxWidth: 420, marginBottom: 10 }}
        onChange={(e) => { setFind(e.target.value); setShown(10) }} />
      {needle && (
        <Typography.Text type="secondary" style={{ fontSize: 12, marginInlineStart: 8 }}>
          {jobs.length} of {all.length} job(s)
        </Typography.Text>
      )}
      {q.isLoading ? <Card loading size="small" /> : jobs.length === 0
        ? <Empty description="Nothing waiting for an area" />
        : <>
            {jobs.slice(0, shown).map((j) => <JobCard key={j.key} job={j} />)}
            {jobs.length > shown && (
              <Button block onClick={() => setShown((x) => x + 10)}>
                Show more ({jobs.length - shown} more job(s))
              </Button>
            )}
          </>}
    </>
  )
}

interface StagedJob extends Row {
  id: number; Site_ID: string; Work_Date: string; Equipment_Tag_No: string
  Lining_System_Code: string; SQM_Completed: number; submitted_by?: string
  rows: Row[]; high_priority: boolean
}

function VarTag({ value, flag }: { value: unknown; flag?: unknown }) {
  const v = n(value)
  if (v == null) return <Tag color="purple">no benchmark</Tag>
  return <Tag color={s(flag) === 'HIGH' ? 'red' : 'green'}>{v > 0 ? '+' : ''}{v.toFixed(1)}%</Tag>
}

function JobDecideModal({ job, onClose }: { job: StagedJob | null; onClose: () => void }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [form] = Form.useForm()
  const equipment = useQuery({
    queryKey: ['/execution/sme-link/equipment', job?.Lining_System_Code, job?.Site_ID],
    enabled: !!job,
    queryFn: async () => (await api.get<{ items: Row[] }>('/execution/sme-link/equipment',
      { params: { code: job?.Lining_System_Code, site_id: job?.Site_ID } })).data.items,
  })
  const decide = useMutation({
    mutationFn: async (payload: Record<string, unknown>) =>
      (await api.post(`/execution/sme-link/groups/${job?.id}/decide`, payload)).data,
    onSuccess: (_d, v) => {
      message.success((v as Row).approve
        ? 'Approved — the job’s area is credited once to that equipment'
        : 'Rejected — the whole job is back with the field')
      void qc.invalidateQueries({ queryKey: ['/execution/sme-link/groups/staged'] })
      void qc.invalidateQueries({ queryKey: ['/execution/sme-link/assigned'] })
      void qc.invalidateQueries({ queryKey: ['/execution/sme-link/groups'] })
      onClose()
    },
    onError: (e) => message.error(errMsg(e)),
  })
  const approve = async () => {
    const v = await form.validateFields()
    const edits: Row = {}
    if (Number(v.sqm) !== Number(job?.SQM_Completed)) edits.SQM_Completed = Number(v.sqm)
    if (s(v.tag) && s(v.tag) !== s(job?.Equipment_Tag_No)) edits.Equipment_Tag_No = v.tag
    if (Object.keys(edits).length && !s(v.justification).trim()) {
      message.error('Changing a filed figure needs a written reason.')
      return
    }
    decide.mutate({ approve: true, edits, justification: v.justification ?? '',
      site_id: job?.Site_ID ?? null })
  }
  const reject = () => {
    const v = form.getFieldsValue()
    if (!s(v.justification).trim()) {
      message.error('A rejection needs a reason — the field has to know what to do differently.')
      return
    }
    decide.mutate({ approve: false, reject_reason: v.justification, site_id: job?.Site_ID ?? null })
  }
  return (
    <Modal open={!!job} onCancel={onClose} title="Approve this job" width={720} destroyOnHidden
      footer={[
        <Button key="c" onClick={onClose}>Cancel</Button>,
        <Button key="r" danger loading={decide.isPending} onClick={reject}>Reject the job</Button>,
        <Button key="a" type="primary" loading={decide.isPending} onClick={approve}>Approve the job</Button>,
      ]}>
      {job && (
        <>
          <Typography.Paragraph type="secondary">
            <strong>{job.Work_Date} · {job.Equipment_Tag_No}</strong>
            {' · '}<SystemCode code={job.Lining_System_Code} plain />
            {' · '}{job.rows.length} material(s){job.submitted_by ? ` · filed by ${job.submitted_by}` : ''}
          </Typography.Paragraph>
          <StagedRows rows={job.rows} />
          <Alert type="info" showIcon style={{ margin: '12px 0' }}
            title="One decision for the whole job"
            description="Approving credits the job’s area ONCE, whatever the number of materials. Rejecting sends every material back with your reason. The quantities cannot be changed here — the material left the shelf when it was issued." />
          {/* ⚠️ Filled from the job AS IT RENDERS, not after the opening
              animation: an Approve clicked in that window used to read an
              empty area, take it for a change and refuse it for lack of a
              reason. */}
          <Form form={form} layout="vertical" key={job.id}
            initialValues={{ sqm: job.SQM_Completed, tag: job.Equipment_Tag_No, justification: '' }}>
            <Space wrap>
              <Form.Item name="sqm" label="Area covered (m²)">
                <InputNumber min={0.01} step={1} style={{ width: 180 }} />
              </Form.Item>
              <Form.Item name="tag" label="Equipment / tank">
                <Select showSearch style={{ minWidth: 220 }} loading={equipment.isFetching}
                  options={(equipment.data ?? []).map((x) => ({ value: s(x.tag), label: s(x.tag) }))} />
              </Form.Item>
            </Space>
            <Form.Item name="justification" label="Reason — required if you change anything, and to reject">
              <Input.TextArea rows={2} maxLength={500} />
            </Form.Item>
          </Form>
        </>
      )}
    </Modal>
  )
}

function StagedRows({ rows }: { rows: Row[] }) {
  const cols: ColumnsType<Row> = [
    { title: 'Material', key: 'm',
      render: (_: unknown, r: Row) => <>{s(r.Material_Code)} · SAP {s(r.SAP_Code)}</> },
    { title: 'Drawn', key: 'a', align: 'right',
      render: (_: unknown, r: Row) => (n(r.Pack_Qty) != null && n(r.Unit_Size_Used) != null
        ? <>{r4(Number(r.Actual_Qty))} · <Typography.Text type="secondary">{r4(Number(r.Pack_Qty))} pack(s)</Typography.Text></>
        : <>{n(r.Actual_Qty) == null ? '—' : r4(Number(r.Actual_Qty))}</>) },
    { title: 'Expected', key: 'e', align: 'right',
      render: (_: unknown, r: Row) => (n(r.Expected_Qty) == null ? '—' : r4(Number(r.Expected_Qty))) },
    { title: 'Variance', key: 'v', align: 'right',
      render: (_: unknown, r: Row) => <VarTag value={r.Variance_Pct} flag={r.Priority_Flag} /> },
  ]
  return <Table size="small" pagination={false} columns={cols} dataSource={rows}
    rowKey={(r) => String(r.id)} />
}

/** The HOD's side: one card per job, decided whole. */
export function HodJobs({ isHod }: { isHod: boolean }) {
  const [open, setOpen] = useState<StagedJob | null>(null)
  const q = useStagedJobs()
  const jobs = [...(q.data ?? [])].sort((a, b) =>
    Number(b.high_priority) - Number(a.high_priority) || s(a.Work_Date).localeCompare(s(b.Work_Date)))
  if (!q.isLoading && jobs.length === 0) return null
  return (
    <>
      {jobs.map((j) => (
        <Card key={j.id} size="small" className="gi-job-card" style={{ marginBottom: 10 }}
          title={<Space wrap>
            {j.high_priority ? <Tag color="red">High Priority</Tag> : <Tag>Normal</Tag>}
            <strong>{j.Work_Date} · {j.Equipment_Tag_No}</strong>
            <SystemCode code={j.Lining_System_Code} plain />
            <Tag>{r4(Number(j.SQM_Completed))} m²</Tag>
            <Tag>{j.rows.length} material(s)</Tag>
          </Space>}
          extra={isHod
            ? <Button size="small" type="primary" onClick={() => setOpen(j)}>Review job</Button>
            : <Typography.Text type="secondary">with the HOD</Typography.Text>}>
          <StagedRows rows={j.rows} />
        </Card>
      ))}
      <JobDecideModal job={open} onClose={() => setOpen(null)} />
    </>
  )
}
