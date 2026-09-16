import { useState } from 'react'
import {
  Alert, App, Button, Card, Form, Input, InputNumber, Modal, Select, Space,
  Table, Tabs, Tag, Tooltip, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import SystemCode from '../sme/SystemCode'

/**
 * Surface Shield consumption — the Inventory ⇄ SME bridge (Phase 13, Track 3).
 *
 * ⚠️ WHAT THIS IS FOR. A Surface Shield material issued from the general
 * Inventory tells the ledger a drum left the shelf and NOTHING about what it
 * covered. This is where that gets said: which system, which vessel, how many
 * square metres — and how that compares to the recipe.
 *
 * ⚠️ IT IS AN ATTRIBUTION, NOT A DEDUCTION. The stock went when it was issued.
 * Nothing on this screen moves a quantity, and the Material Estimator's
 * readiness figures do not move either — ruling Q13-5 is Option B: the
 * estimator gains an observation column and its maths is untouched.
 *
 * ⚠️ AND THE QUEUE IS A LEDGER SWEEP, NOT AN INBOX. It holds every
 * unattributed Surface Shield consumption, oldest first, including rows from
 * long before this feature existed and rows brought in by an Excel import.
 * Expect it to be long the first time somebody opens it.
 */

type Row = Record<string, unknown>
const s = (v: unknown) => (v == null ? '' : String(v))
const n = (v: unknown) => (v == null ? null : Number(v))

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } } }
  return x?.response?.data?.detail ?? 'Something went wrong'
}

/** m² and quantities read better with a unit than without one. */
function Qty({ value, unit }: { value: unknown; unit?: string }) {
  const v = n(value)
  if (v == null) return <Typography.Text type="secondary">—</Typography.Text>
  return <>{Number(v.toFixed(4))}{unit ? ` ${unit}` : ''}</>
}

/**
 * ⚠️ THE VARIANCE TAG IS NEUTRAL WHEN IT CANNOT BE COMPUTED, AND THAT MATTERS.
 * A blank benchmark is not "on target" — it means the recipe has no line for
 * this component in this system, which is a thing an HOD must look at. It
 * renders as a question, never as a zero.
 */
function VarTag({ value, flag }: { value: unknown; flag?: unknown }) {
  const v = n(value)
  if (v == null) {
    return (
      <Tooltip title="No recipe line for this component in this system, so there is no benchmark to compare against. That is why it is High Priority.">
        <Tag color="purple">no benchmark</Tag>
      </Tooltip>
    )
  }
  const high = s(flag) === 'HIGH'
  return (
    <Tag color={high ? 'red' : 'green'}>
      {v > 0 ? '+' : ''}{v.toFixed(1)}%
    </Tag>
  )
}

function PriorityTag({ flag }: { flag: unknown }) {
  return s(flag) === 'HIGH'
    ? <Tag color="red">High Priority</Tag>
    : <Tag>Normal</Tag>
}

/** Attribute one ledger row: system, tag, area. */
function AssignModal({ row, onClose }: { row: Row | null; onClose: () => void }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [form] = Form.useForm()
  const [code, setCode] = useState<string | undefined>()

  const sap = s(row?.sap_code)
  const site = s(row?.site_id)
  const systems = useQuery({
    queryKey: ['/execution/sme-link/system-codes', sap],
    enabled: !!row,
    queryFn: async () => (await api.get<{ items: Row[] }>(
      '/execution/sme-link/system-codes', { params: { sap } })).data.items,
  })
  const equipment = useQuery({
    queryKey: ['/execution/sme-link/equipment', code, site],
    enabled: !!code,
    queryFn: async () => (await api.get<{ items: Row[] }>(
      '/execution/sme-link/equipment',
      { params: { code, site_id: site } })).data.items,
  })

  const save = useMutation({
    mutationFn: async (v: Record<string, unknown>) =>
      (await api.post('/execution/sme-link/assign', {
        consumption_id: Number(row?.consumption_id),
        code: v.code, tag: v.tag, sqm: v.sqm,
        work_date: s(row?.work_date) || null,
        notes: v.notes ?? null, site_id: site || null,
      })).data,
    onSuccess: () => {
      message.success(rejected
        ? 'Resubmitted — it is back with the HOD'
        : edited
          ? 'Re-assigned — the HOD has been notified'
          : 'Recorded — it now goes to the HOD for approval')
      void qc.invalidateQueries({ queryKey: ['/execution/sme-link/queue'] })
      void qc.invalidateQueries({ queryKey: ['/execution/sme-link/assigned'] })
      onClose()
    },
    onError: (e) => message.error(errMsg(e)),
  })

  // The store keeper's `LS <code>` note, read back so the field does not
  // re-pick what somebody already picked. A hint, never an assignment.
  // ⚠️ An EDITED row starts from its previous answer instead: the field is
  // confirming or correcting work already done, not starting from nothing.
  // ⚠️ A REJECTED row starts from what was REJECTED, with the HOD's reason in
  // front of it: the field is correcting their own answer, so they need to see
  // it and what was wrong with it, not a blank form.
  const rejected = s(row?.reason) === 'rejected'
  const edited = s(row?.reason) === 'edited' || !!row?.edited_in_excel
  const hint = (rejected ? s(row?.rejected_code)
    : edited ? s(row?.prev_code) : s(row?.hinted_system_code)) || undefined

  return (
    <Modal open={!!row} onCancel={onClose}
      title={rejected ? 'Correct and resubmit' : 'What did this material cover?'}
      okText={rejected ? 'Resubmit to HOD' : 'Record'} confirmLoading={save.isPending}
      afterOpenChange={(o) => {
        if (o) {
          setCode(hint)
          form.setFieldsValue({
            code: hint,
            tag: rejected ? s(row?.rejected_tag) || undefined
              : edited ? s(row?.prev_tag) || undefined : undefined,
            sqm: rejected ? n(row?.rejected_sqm) ?? undefined
              : edited ? n(row?.prev_sqm) ?? undefined : undefined,
          })
        }
      }}
      onOk={() => form.validateFields().then((v) => save.mutate(v))}>
      {row && (
        <>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
            <strong>{s(row.material_name) || s(row.sap_code)}</strong>
            {' · '}<Qty value={row.quantity} unit={s(row.uom)} /> drawn on{' '}
            {s(row.work_date)}
            {s(row.tank_no) ? ` · noted against ${s(row.tank_no)}` : ''}
          </Typography.Paragraph>
          {/* ⚠️ THE HOD'S REASON, FIRST AND UNMISSABLE. A rejection the field
              cannot read is one they resubmit unchanged. */}
          {rejected && (
            <Alert type="error" showIcon style={{ marginBottom: 12 }}
              title="Rejected — needs correction"
              description={<>
                <Typography.Text strong style={{ display: 'block', marginBottom: 4 }}>
                  “{s(row.rejection_reason) || 'No reason recorded'}”
                </Typography.Text>
                {s(row.rejected_by) ? `— ${s(row.rejected_by)}. ` : ''}
                The answer that was rejected is filled in below. Correct it and
                resubmit; it goes straight back to the HOD.
              </>} />
          )}
          {/* ⚠️ THE EDIT, SHOWN AS AN EDIT. The operator asked for this to be
              highlighted, and the reason is practical: the field is being asked
              a question they already answered, and the only way that is not
              confusing is to show them what changed in the workbook. */}
          {edited && (
            <Alert type="warning" showIcon style={{ marginBottom: 12 }}
              title="Edited in Excel since this was assigned"
              description={<>
                The workbook now says <strong><Qty value={row.quantity} unit={s(row.uom)} /></strong>
                {' '}where it was assigned against <strong><Qty value={row.prev_qty} unit={s(row.uom)} /></strong>.
                {' '}The previous answer is filled in below — confirm it or correct it.
                {s(row.log_status) === 'committed'
                  ? ' The approved figures keep counting until the HOD approves these.'
                  : ''}
                {!rejected && s(row.last_revision_rejected_reason)
                  ? <> The HOD rejected the last re-assignment: <em>{s(row.last_revision_rejected_reason)}</em></>
                  : null}
              </>} />
          )}
          {/* ⚠️ STATED PLAINLY, because "recording consumption" sounds like it
              deducts something and this does not. */}
          <Alert type="info" showIcon style={{ marginBottom: 12 }}
            title="This records what the material was used for — it does not move any stock"
            description="The material left the store when it was issued. What is missing is the area it covered, so it can be compared against the recipe." />
          <Form form={form} layout="vertical">
            <Form.Item name="code" label="System Code"
              rules={[{ required: true, message: 'Pick the lining system this was drawn for' }]}
              extra={rejected ? 'This is the answer the HOD rejected — change it if the system was wrong.'
                : edited && hint ? 'Your previous answer — confirm it or change it.'
                  : hint ? 'Pre-selected from the store keeper’s note — change it if that was wrong.'
                    : 'Only the systems whose recipe contains this material are offered.'}>
              <Select showSearch optionFilterProp="label"
                loading={systems.isFetching}
                placeholder="Which system was this for?"
                onChange={(v) => { setCode(v); form.setFieldsValue({ tag: undefined }) }}
                options={(systems.data ?? []).map((x) => ({
                  value: s(x.code),
                  label: `${s(x.code)}${x.name ? ` — ${s(x.name)}` : ''}`,
                }))} />
            </Form.Item>
            <Form.Item name="tag" label="Equipment / tank"
              rules={[{ required: true, message: 'Pick the equipment this was applied to' }]}
              extra="Filtered to the equipment that system code applies to.">
              <Select showSearch optionFilterProp="label" disabled={!code}
                loading={equipment.isFetching}
                placeholder={code ? 'Which vessel?' : 'Pick a system code first'}
                options={(equipment.data ?? []).map((x) => ({
                  value: s(x.tag),
                  label: `${s(x.tag)}${x.name && x.name !== x.tag ? ` — ${s(x.name)}` : ''}`,
                }))} />
            </Form.Item>
            <Form.Item name="sqm" label="Area covered (m²)"
              rules={[{ required: true, message: 'How much area did this material cover?' }]}>
              <InputNumber min={0.01} step={1} style={{ width: 200 }} />
            </Form.Item>
          </Form>
        </>
      )}
    </Modal>
  )
}

/**
 * The HOD's decision.
 *
 * ⚠️ THREE THINGS ARE EDITABLE AND THE QUANTITY IS NOT ONE OF THEM (Q13-7).
 * The drum left the shelf when it was issued, so this settles the attribution
 * and the explanation, never the deduction. The quantity is rendered as
 * evidence — and the API REFUSES it rather than ignoring it, so a field that
 * somehow reached the request would produce an error and not a silent loss.
 */
function DecideModal({ row, onClose }: { row: Row | null; onClose: () => void }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [form] = Form.useForm()

  const site = s(row?.Site_ID)
  const code = s(row?.Lining_System_Code)
  const equipment = useQuery({
    queryKey: ['/execution/sme-link/equipment', code, site],
    enabled: !!row,
    queryFn: async () => (await api.get<{ items: Row[] }>(
      '/execution/sme-link/equipment',
      { params: { code, site_id: site } })).data.items,
  })

  const decide = useMutation({
    mutationFn: async (payload: Record<string, unknown>) =>
      (await api.post(`/execution/sme-link/${Number(row?.id)}/decide`, payload)).data,
    onSuccess: (_d, vars) => {
      message.success((vars as Record<string, unknown>).approve
        ? 'Approved — the area is credited to that equipment'
        : 'Rejected')
      void qc.invalidateQueries({ queryKey: ['/execution/sme-link/assigned'] })
      onClose()
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const approve = async () => {
    const v = await form.validateFields()
    const edits: Record<string, unknown> = {}
    if (Number(v.sqm) !== Number(row?.SQM_Completed)) edits.SQM_Completed = Number(v.sqm)
    if (s(v.tag) && s(v.tag) !== s(row?.Equipment_Tag_No)) edits.Equipment_Tag_No = v.tag
    if (Object.keys(edits).length && !s(v.justification).trim()) {
      message.error('Changing a filed figure needs a written reason — the person '
        + 'who reported it will be answering for what is recorded.')
      return
    }
    decide.mutate({
      approve: true, edits, justification: v.justification ?? '',
      site_id: site || null,
    })
  }

  const reject = async () => {
    const v = form.getFieldsValue()
    if (!s(v.justification).trim()) {
      message.error('A rejection needs a reason — the person who filed this has '
        + 'to know what to do differently.')
      return
    }
    decide.mutate({ approve: false, reject_reason: v.justification, site_id: site || null })
  }

  return (
    <Modal open={!!row} onCancel={onClose} title="Approve this attribution"
      footer={[
        <Button key="c" onClick={onClose}>Cancel</Button>,
        <Button key="r" danger loading={decide.isPending} onClick={reject}>Reject</Button>,
        <Button key="a" type="primary" loading={decide.isPending} onClick={approve}>
          Approve
        </Button>,
      ]}
      afterOpenChange={(o) => {
        if (o && row) {
          form.setFieldsValue({
            sqm: n(row.SQM_Completed), tag: s(row.Equipment_Tag_No), justification: '',
          })
        }
      }}>
      {row && (
        <>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
            <strong>{s(row.Material_Code)} · {s(row.SAP_Code)}</strong>
            {' · '}<SystemCode code={code} plain />
            {' · '}{s(row.entry_date)}
          </Typography.Paragraph>
          <Space wrap style={{ marginBottom: 12 }}>
            <Tag>drawn <Qty value={row.Actual_Qty} /></Tag>
            <Tag>expected <Qty value={row.Expected_Qty} /></Tag>
            <VarTag value={row.Variance_Pct} flag={row.Priority_Flag} />
            <PriorityTag flag={row.Priority_Flag} />
          </Space>
          {row.resubmitted_after_rejection ? (
            <Alert type="info" showIcon style={{ marginBottom: 12 }}
              title="Resubmitted after you rejected it"
              description={<>You sent this back: <em>“{s(row.rejected_reason)}”</em>.
                The field has corrected it — these are the new figures.</>} />
          ) : null}
          {/* ⚠️ THE ONE SENTENCE AN HOD MOST NEEDS ON THIS SCREEN. */}
          <Alert type="warning" showIcon style={{ marginBottom: 12 }}
            title="The quantity cannot be changed here"
            description="The material left the shelf when it was issued, so this approval settles the area and the explanation — not the amount. If the physical figure is wrong, raise a stock adjustment: that is a ledger event with its own audit line." />
          <Form form={form} layout="vertical">
            <Form.Item name="sqm" label="Area covered (m²)">
              <InputNumber min={0.01} step={1} style={{ width: 200 }} />
            </Form.Item>
            <Form.Item name="tag" label="Equipment / tank">
              <Select showSearch optionFilterProp="label" loading={equipment.isFetching}
                options={(equipment.data ?? []).map((x) => ({
                  value: s(x.tag), label: s(x.tag) }))} />
            </Form.Item>
            <Form.Item name="justification"
              label="Reason — required if you change anything, and to reject"
              extra="The person who reported this will be answering for what is recorded.">
              <Input.TextArea rows={2} maxLength={500}
                placeholder="Why you changed it, or why you are rejecting it" />
            </Form.Item>
          </Form>
        </>
      )}
    </Modal>
  )
}

/**
 * The HOD's decision on a re-assignment of APPROVED consumption.
 *
 * ⚠️ APPROVAL UPDATES THE ORIGINAL ENTRY — the operator's requirement — and
 * moves its area credit by the difference. Rejection leaves the approved
 * figures standing and sends the row back to the field with the reason.
 */
function RevisionModal({ row, onClose }: { row: Row | null; onClose: () => void }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [reason, setReason] = useState('')

  const decide = useMutation({
    mutationFn: async (approve: boolean) =>
      (await api.post(`/execution/sme-link/revisions/${Number(row?.id)}/decide`, {
        approve, reject_reason: approve ? '' : reason,
        site_id: s(row?.Site_ID) || null,
      })).data,
    onSuccess: (_d, approve) => {
      message.success(approve
        ? 'Approved — the original entry now carries the new figures'
        : 'Rejected — it is back with the field')
      void qc.invalidateQueries({ queryKey: ['/execution/sme-link/assigned'] })
      void qc.invalidateQueries({ queryKey: ['/execution/sme-link/queue'] })
      onClose()
    },
    onError: (e) => message.error(errMsg(e)),
  })

  return (
    <Modal open={!!row} onCancel={onClose} title="Approve a re-assignment"
      afterOpenChange={(o) => { if (o) setReason('') }}
      footer={[
        <Button key="c" onClick={onClose}>Cancel</Button>,
        <Button key="r" danger loading={decide.isPending}
          onClick={() => {
            if (!reason.trim()) {
              message.error('A rejection needs a reason — the field has to know what to do differently.')
              return
            }
            decide.mutate(false)
          }}>Reject</Button>,
        <Button key="a" type="primary" loading={decide.isPending}
          disabled={!!row?.stale} onClick={() => decide.mutate(true)}>
          Approve
        </Button>,
      ]}>
      {row && (
        <>
          {row.stale ? (
            <Alert type="error" showIcon style={{ marginBottom: 12 }}
              title="The workbook changed again after this was re-assigned"
              description="These figures are already out of date, so they cannot be approved. The row is back in the field's queue." />
          ) : null}
          <Space direction="vertical" style={{ width: '100%' }}>
            <Typography.Text type="secondary">Approved — still counting</Typography.Text>
            <Typography.Text>
              {s(row.Prev_Equipment_Tag_No)} · {s(row.Prev_Lining_System_Code)} ·{' '}
              <Qty value={row.Prev_SQM_Completed} unit="m²" /> · drawn <Qty value={row.Prev_Actual_Qty} />
            </Typography.Text>
            <Typography.Text type="secondary">Re-assigned against the edited workbook</Typography.Text>
            <Typography.Text strong>
              {s(row.Equipment_Tag_No)} · {s(row.Lining_System_Code)} ·{' '}
              <Qty value={row.SQM_Completed} unit="m²" /> · drawn <Qty value={row.Actual_Qty} />
              {' '}<VarTag value={row.Variance_Pct} flag={row.Priority_Flag} />
            </Typography.Text>
          </Space>
          <Alert type="info" showIcon style={{ marginTop: 12, marginBottom: 12 }}
            title="Approving updates the original entry"
            description="The approved area is taken back off the equipment it was credited to and the new area credited, so progress moves by the difference only." />
          <Input.TextArea rows={2} maxLength={500} value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Reason — required to reject" />
        </>
      )}
    </Modal>
  )
}

export default function SmeLinkCard() {
  const { user } = useAuth()
  const isHod = user?.role === 'hod' || user?.role === 'admin'
  const [assignRow, setAssignRow] = useState<Row | null>(null)
  const [decideRow, setDecideRow] = useState<Row | null>(null)
  const [revisionRow, setRevisionRow] = useState<Row | null>(null)

  const queue = useQuery({
    queryKey: ['/execution/sme-link/queue'],
    queryFn: async () => (await api.get<{ items: Row[]; total: number; edited: number
      rejected: number }>(
      '/execution/sme-link/queue', { params: { limit: 200 } })).data,
  })
  const assigned = useQuery({
    queryKey: ['/execution/sme-link/assigned'],
    queryFn: async () => (await api.get<{ items: Row[]; high_priority: number
      tolerance_pct: number; revisions: Row[] }>(
      '/execution/sme-link/assigned', { params: { status: 'staged' } })).data,
  })

  const queueCols: ColumnsType<Row> = [
    // ⚠️ 2026-09-17: WHY THE ROW IS HERE, IN WORDS. A rejected row carries the
    // HOD's reason in the table itself, not only behind a click — the field
    // should be able to scan the top of the queue and know what to fix.
    { title: 'Status', key: 'st', width: 260,
      render: (_: unknown, r: Row) => (
        <Space direction="vertical" size={2} style={{ maxWidth: 250 }}>
          {s(r.reason) === 'rejected' && (<>
            <Tag color="red" style={{ marginInlineEnd: 0, fontWeight: 600 }}>
              Rejected - Needs Correction
            </Tag>
            <Typography.Text style={{ fontSize: 12, whiteSpace: 'normal' }}>
              “{s(r.rejection_reason) || 'No reason recorded'}”
              {s(r.rejected_by)
                ? <Typography.Text type="secondary" style={{ fontSize: 11 }}> — {s(r.rejected_by)}</Typography.Text>
                : null}
            </Typography.Text>
          </>)}
          {(s(r.reason) === 'edited' || !!r.edited_in_excel) && (
            <Tooltip title="This consumption was already assigned, then its figures were changed in the Excel workbook. It needs assigning again against the new figures.">
              <Tag color="orange" style={{ marginInlineEnd: 0 }}>Edited in Excel</Tag>
            </Tooltip>
          )}
          {s(r.reason) === 'unattributed' && <Tag style={{ marginInlineEnd: 0 }}>Needs an area</Tag>}
        </Space>) },
    { title: 'Date', dataIndex: 'work_date', width: 110,
      render: (v) => s(v).slice(0, 10) },
    { title: 'Material', key: 'm', width: 260,
      render: (_: unknown, r: Row) => (
        <Space direction="vertical" size={0}>
          <span>{s(r.material_name) || s(r.sap_code)}</span>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            SAP {s(r.sap_code)}{r.material_code ? ` · ${s(r.material_code)}` : ''}
          </Typography.Text>
        </Space>) },
    { title: 'Drawn', key: 'q', width: 140, align: 'right',
      render: (_: unknown, r: Row) => ((s(r.reason) === 'edited' || !!r.edited_in_excel)
        && n(r.prev_qty) !== n(r.quantity)
        ? <Space direction="vertical" size={0} style={{ alignItems: 'flex-end' }}>
            <strong><Qty value={r.quantity} unit={s(r.uom)} /></strong>
            <Typography.Text type="secondary" delete style={{ fontSize: 11 }}>
              <Qty value={r.prev_qty} unit={s(r.uom)} />
            </Typography.Text>
          </Space>
        : <Qty value={r.quantity} unit={s(r.uom)} />) },
    { title: 'Noted against', dataIndex: 'tank_no', width: 160,
      render: (v) => s(v) || <Typography.Text type="secondary">—</Typography.Text> },
    { title: 'System', key: 'ls', width: 150,
      render: (_: unknown, r: Row) => (s(r.hinted_system_code)
        ? <SystemCode code={s(r.hinted_system_code)} plain />
        : <Tooltip title="No system was noted at issue, so the app will ask rather than guess — a guessed code compares the draw against the wrong benchmark.">
            <Tag>not stated</Tag>
          </Tooltip>) },
    { title: '', key: 'a', fixed: 'right', width: 120,
      render: (_: unknown, r: Row) => (
        <Button size="small" type="primary" danger={s(r.reason) === 'rejected'}
          onClick={() => setAssignRow(r)}>
          {s(r.reason) === 'rejected' ? 'Correct & resubmit'
            : s(r.reason) === 'edited' ? 'Re-assign' : 'Record area'}
        </Button>) },
  ]

  // ⚠️ BEFORE AND AFTER, SIDE BY SIDE. A revision replaces figures that already
  // count; an HOD approving one needs to see what it replaces, not just what it
  // says.
  const revisionCols: ColumnsType<Row> = [
    { title: 'Priority', key: 'p', width: 130,
      render: (_: unknown, r: Row) => <PriorityTag flag={r.Priority_Flag} /> },
    { title: 'Date', dataIndex: 'entry_date', width: 110 },
    { title: 'Approved (still counting)', key: 'prev', width: 250,
      render: (_: unknown, r: Row) => (
        <Typography.Text type="secondary">
          {s(r.Prev_Equipment_Tag_No)} · {s(r.Prev_Lining_System_Code)} ·{' '}
          <Qty value={r.Prev_SQM_Completed} unit="m²" /> · drawn <Qty value={r.Prev_Actual_Qty} />
        </Typography.Text>) },
    { title: 'Re-assigned against the edit', key: 'now', width: 270,
      render: (_: unknown, r: Row) => (
        <span>
          {s(r.Equipment_Tag_No)} · {s(r.Lining_System_Code)} ·{' '}
          <Qty value={r.SQM_Completed} unit="m²" /> · drawn <strong><Qty value={r.Actual_Qty} /></strong>
          {r.stale ? <Tag color="orange" style={{ marginInlineStart: 6 }}>edited again</Tag> : null}
        </span>) },
    { title: 'Variance', key: 'v', width: 120, align: 'right',
      render: (_: unknown, r: Row) =>
        <VarTag value={r.Variance_Pct} flag={r.Priority_Flag} /> },
    { title: 'Filed by', dataIndex: 'submitted_by', width: 120 },
    { title: '', key: 'a', fixed: 'right', width: 110,
      render: (_: unknown, r: Row) => (isHod
        ? <Button size="small" type="primary" onClick={() => setRevisionRow(r)}>Review</Button>
        : <Typography.Text type="secondary">with the HOD</Typography.Text>) },
  ]

  const assignedCols: ColumnsType<Row> = [
    { title: 'Priority', key: 'p', width: 180,
      render: (_: unknown, r: Row) => (
        <Space direction="vertical" size={2}>
          <PriorityTag flag={r.Priority_Flag} />
          {r.resubmitted_after_rejection ? (
            <Tooltip title={`You rejected it before: “${s(r.rejected_reason)}”`}>
              <Tag color="blue" style={{ marginInlineEnd: 0 }}>Resubmitted after rejection</Tag>
            </Tooltip>
          ) : null}
        </Space>) },
    { title: 'Date', dataIndex: 'entry_date', width: 110 },
    { title: 'Equipment', dataIndex: 'Equipment_Tag_No', width: 170 },
    { title: 'System', dataIndex: 'Lining_System_Code', width: 140,
      render: (v) => <SystemCode code={s(v)} plain /> },
    { title: 'Area', key: 'sqm', width: 100, align: 'right',
      render: (_: unknown, r: Row) => <Qty value={r.SQM_Completed} unit="m²" /> },
    { title: 'Drawn', key: 'aq', width: 100, align: 'right',
      render: (_: unknown, r: Row) => <Qty value={r.Actual_Qty} /> },
    { title: 'Expected', key: 'eq', width: 110, align: 'right',
      render: (_: unknown, r: Row) => <Qty value={r.Expected_Qty} /> },
    { title: 'Variance', key: 'v', width: 130, align: 'right',
      render: (_: unknown, r: Row) =>
        <VarTag value={r.Variance_Pct} flag={r.Priority_Flag} /> },
    { title: 'Filed by', dataIndex: 'entered_by', width: 130 },
    { title: '', key: 'a', fixed: 'right', width: 110,
      render: (_: unknown, r: Row) => (isHod
        ? <Button size="small" type="primary" onClick={() => setDecideRow(r)}>Review</Button>
        : <Typography.Text type="secondary">with the HOD</Typography.Text>) },
  ]

  const tol = assigned.data?.tolerance_pct ?? 10

  return (
    <Card size="small" style={{ marginBottom: 12 }}
      title="Surface Shield consumption — what it was used for">
      <Tabs
        size="small"
        items={[
          {
            key: 'queue',
            label: `Needs an area (${queue.data?.total ?? 0})`,
            children: (
              <>
                <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
                  Surface Shield material issued from the store, with no area
                  recorded against it yet — <strong>rejected rows first</strong>,
                  then <strong>oldest first</strong>. This
                  list reaches back over the whole ledger, so it includes issues
                  from long before this screen existed.
                  {' '}Recording an area moves no stock: the material left the
                  store when it was issued.
                </Typography.Paragraph>
                {(queue.data?.rejected ?? 0) > 0 && (
                  <Alert type="error" showIcon style={{ marginBottom: 8 }}
                    title={`${queue.data?.rejected} row(s) rejected by the HOD — at the top, needs correction`}
                    description="Each one shows the HOD's reason. Correct the system code, equipment or area and resubmit; it goes straight back to the HOD." />
                )}
                {(queue.data?.edited ?? 0) > 0 && (
                  <Alert type="warning" showIcon style={{ marginBottom: 8 }}
                    title={`${queue.data?.edited} row(s) marked Edited in Excel`}
                    description="These were already assigned, then their quantity, date or tank changed in the workbook. Re-assign them against the new figures — the previous answer is filled in for you. A workbook push that changes nothing does not send a row back." />
                )}
                <Table size="small" loading={queue.isFetching} columns={queueCols}
                  dataSource={queue.data?.items ?? []}
                  rowKey={(r) => String(r.consumption_id)}
                  onRow={(r) => (s(r.reason) === 'rejected'
                    ? { style: { background: 'rgba(255, 77, 79, 0.08)' } } : {})}
                  scroll={{ x: 'max-content' }}
                  pagination={{ pageSize: 10, showTotal: (t) => `${t} outstanding` }} />
              </>
            ),
          },
          {
            key: 'staged',
            label: `Awaiting the HOD (${(assigned.data?.items?.length ?? 0)
              + (assigned.data?.revisions?.length ?? 0)})`,
            children: (
              <>
                <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
                  <strong>Every</strong> Surface Shield consumption is reviewed,
                  whatever the variance. A draw more than {tol}% off the recipe
                  is flagged <Tag color="red" style={{ marginInline: 4 }}>High Priority</Tag>
                  and sorted to the top — the band decides what an HOD sees
                  first, never whether a decision is needed.
                  {' '}Approval credits the area to that equipment.
                </Typography.Paragraph>
                <Table size="small" loading={assigned.isFetching}
                  columns={assignedCols} dataSource={assigned.data?.items ?? []}
                  rowKey={(r) => String(r.id)} scroll={{ x: 'max-content' }}
                  pagination={{ pageSize: 10, showTotal: (t) => `${t} awaiting` }} />
                {(assigned.data?.revisions?.length ?? 0) > 0 && (
                  <>
                    <Typography.Title level={5} style={{ marginTop: 16 }}>
                      Approved, then edited in Excel
                    </Typography.Title>
                    <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
                      These were already approved when the workbook changed. The
                      approved figures keep counting until you approve the new
                      ones; approving <strong>updates the original entry</strong>
                      {' '}and moves its area credit by the difference.
                    </Typography.Paragraph>
                    <Table size="small" loading={assigned.isFetching}
                      columns={revisionCols} dataSource={assigned.data?.revisions ?? []}
                      rowKey={(r) => `rev-${String(r.id)}`} scroll={{ x: 'max-content' }}
                      pagination={{ pageSize: 10 }} />
                  </>
                )}
              </>
            ),
          },
        ]} />
      <AssignModal row={assignRow} onClose={() => setAssignRow(null)} />
      <DecideModal row={decideRow} onClose={() => setDecideRow(null)} />
      <RevisionModal row={revisionRow} onClose={() => setRevisionRow(null)} />
    </Card>
  )
}
