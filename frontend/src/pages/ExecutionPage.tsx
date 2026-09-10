/**
 * The consumption workflow. PAPER FIRST since Phase 9d:
 * Supervisor → Store Keeper → HOD.
 *
 * One page, three views, because the three roles look at the SAME entry and
 * each is allowed to change a different part of it:
 *
 *  · a SUPERVISOR fills a printed form in the field, photographs it, then
 *    reviews what the camera read and files the area, the crew, the
 *    quantities and the per-line lots;
 *  · a STORE KEEPER verifies those quantities against what actually left the
 *    shelf, and may correct them — every correction costs a reason and shows
 *    to the HOD in RED;
 *  · an HOD may correct either side, and the moment any number changes a
 *    justification becomes mandatory and the supervisor is notified.
 *
 * ⚠️ THE DIRECTION REVERSED, AND SO DID WHO MAY EDIT A MATERIAL LINE. Phase 5
 * showed the supervisor those lines read-only, because the store keeper had
 * counted them. The record starts in the field now, so the supervisor authors
 * them — and what replaces that control is FOUR LAYERS, each with an owner:
 *
 *     grey    what the camera read       never editable
 *     amber   what the supervisor filed  when it differs from grey
 *     red     what the store keeper set  when it differs from amber
 *     purple  what the HOD settled       when it differs from red
 *
 * One colour alone would have let a supervisor overwrite the machine's reading
 * of their own handwriting with nobody able to tell.
 *
 * ⚠️ The lining-system field disappears for a system-agnostic activity
 * (blasting, buffing). Surface prep belongs to no lining system, and forcing a
 * choice would trap the hours under whichever system was guessed — so the form
 * submits '' and the API stores '' (a real value; NULL would break both the
 * key and every GROUP BY over it).
 */
import {
  CameraOutlined, DownloadOutlined, PlusOutlined, PrinterOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import SystemCode from '../sme/SystemCode'
import {
  Alert, App, Button, Card, Col, Descriptions, Divider, Form, Input, InputNumber,
  Modal, Popconfirm, Row, Select, Space, Statistic, Table, Tag, Tooltip, Typography,
  Upload,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import OcrJobProgress from '../components/OcrJobProgress'
import type { OcrJobStatus } from '../components/OcrJobProgress'
import TrainingGate from '../components/TrainingGate'
import { downloadConsumptionForm, useFormSystems } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'

type Row = Record<string, unknown>

const errMsg = (e: unknown): string => {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Request failed'
}

const STATUS_META: Record<string, { color: string; label: string }> = {
  DRAFT_SUPERVISOR: { color: 'gold', label: 'Draft (supervisor)' },
  PENDING_SK: { color: 'orange', label: 'With store keeper' },
  PENDING_HOD: { color: 'blue', label: 'With HOD' },
  APPROVED: { color: 'green', label: 'Approved' },
  REJECTED: { color: 'red', label: 'Rejected' },
  // Drained in 9d. Kept so a historical row renders with a label rather than a
  // raw string — the states were retired, not deleted.
  DRAFT_SK: { color: 'default', label: 'Draft (retired flow)' },
  PENDING_SUPERVISOR: { color: 'default', label: 'Retired flow' },
}

/**
 * ⚠️ THE FOUR LAYERS, AND WHY EACH COLOUR IS EARNED.
 *
 * A colour that appears when nothing changed teaches people to ignore it, so
 * each layer renders ONLY when it differs from the one before. A row everybody
 * agreed on shows one plain number — which is what makes a red one worth
 * looking at.
 */
function QtyTrail({ line }: { line: Row }) {
  const n = (v: unknown) => (v == null ? null : Number(v))
  const ocr = n(line.OCR_Qty)
  const sup = n(line.Supervisor_Qty)
  const sk = n(line.SK_Qty)
  const act = n(line.Actual_Qty) ?? 0
  const differs = (a: number | null, b: number | null) =>
    a != null && b != null && Math.abs(a - b) > 1e-9

  return (
    <Space size={4} wrap>
      {ocr != null && (
        <Tooltip title="What the camera read. Never editable — it is the record
          of what was on the paper.">
          <Tag color="default">{ocr}</Tag>
        </Tooltip>
      )}
      {differs(sup, ocr) && (
        <Tooltip title="The supervisor changed the machine's reading.">
          <Tag color="orange">→ {sup}</Tag>
        </Tooltip>
      )}
      {differs(sk, sup) && (
        <Tooltip title="The store keeper changed the supervisor's figure —
          this is what actually left the shelf.">
          <Tag color="red">→ {sk}</Tag>
        </Tooltip>
      )}
      {differs(act, sk ?? sup ?? ocr) && (
        <Tooltip title="The HOD settled on this figure.">
          <Tag color="purple">→ {act}</Tag>
        </Tooltip>
      )}
      {ocr == null && sup == null && <strong>{act}</strong>}
      {ocr != null && !differs(sup, ocr) && !differs(sk, sup)
        && !differs(act, sk ?? ocr) && (
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>agreed</Typography.Text>
      )}
      {line.OCR_Qty == null && line.OCR_Qty_Text ? (
        <Tooltip title={`The camera read "${String(line.OCR_Qty_Text)}" but could
          not be sure of the number, so it did not guess.`}>
          <Tag color="gold">unread: {String(line.OCR_Qty_Text)}</Tag>
        </Tooltip>
      ) : null}
      {line.Plausibility_Flag ? (
        <Tooltip title={`This is ${String(line.Plausibility_Flag)}. A misread
          digit lands an order of magnitude out; an unusual day is also
          possible, which is why this only flags and never blocks.`}>
          <Tag color="volcano">check</Tag>
        </Tooltip>
      ) : null}
    </Space>
  )
}

const num = (v: unknown) => (v == null ? null : Number(v))
const pct = (v: unknown) => (v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v)}%`)

/** A variance reads green only when it is genuinely comparable and small. */
function VarianceTag({ value }: { value: unknown }) {
  if (value == null) {
    return (
      <Tooltip title="No benchmark to compare against — that is not the same as
        a perfect match, so it is deliberately not shown as 0%.">
        <Tag>not comparable</Tag>
      </Tooltip>)
  }
  const n = Number(value)
  const color = Math.abs(n) <= 5 ? 'green' : Math.abs(n) <= 15 ? 'gold' : 'red'
  return <Tag color={color}>{pct(n)}</Tag>
}

function useEntries(status?: string) {
  return useQuery({
    queryKey: ['/execution/entries', status ?? ''],
    queryFn: async () => (await api.get<{ items: Row[] }>('/execution/entries',
      { params: status ? { status } : {} })).data.items,
  })
}

function useActivities() {
  return useQuery({
    queryKey: ['/execution/activities'],
    queryFn: async () =>
      (await api.get<{ items: Row[] }>('/execution/activities')).data.items,
  })
}

// ─── SK: open an entry ───────────────────────────────────────────────────────
function OpenEntryModal({ open, onClose, asSupervisor }: {
  open: boolean; onClose: () => void; asSupervisor: boolean
}) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [form] = Form.useForm()
  const acts = useActivities()
  const [activity, setActivity] = useState<Row | null>(null)
  const [materials, setMaterials] = useState<Row[]>([])

  // A supervisor may only open what needs no store keeper.
  const options = useMemo(() => ((acts.data ?? []) as Row[])
    .filter((a: Row) => !asSupervisor || a.manpower_only)
    .map((a: Row) => ({
      value: `${a.Lining_System_Code}|${a.Execution_Sub_Activity_Code}|${a.Variant_Key ?? ''}`,
      label: `${a.Execution_Sub_Activity_Code} — ${a.Sub_Activity || a.Activity}`
        + (a.Variant_Key ? ` (${a.Variant_Key})` : ''),
      row: a,
    })), [acts.data, asSupervisor])

  const create = useMutation({
    mutationFn: (b: Row) => api.post('/execution/entries', b).then((r) => r.data),
    onSuccess: () => {
      message.success('Entry opened')
      qc.invalidateQueries({ queryKey: ['/execution/entries'] })
      form.resetFields(); setMaterials([]); setActivity(null); onClose()
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const agnostic = !!activity?.system_agnostic

  const submit = async () => {
    const v = await form.validateFields()
    create.mutate({
      work_date: v.work_date,
      equipment_tag: v.equipment_tag,
      // '' for surface prep — see the file header.
      lining_system_code: agnostic ? '' : String(activity?.Lining_System_Code ?? ''),
      execution_sub_activity_code: String(activity?.Execution_Sub_Activity_Code ?? ''),
      variant_key: String(activity?.Variant_Key ?? ''),
      materials: asSupervisor ? [] : materials,
    })
  }

  return (
    <Modal open={open} onCancel={onClose} onOk={submit} width={720}
      confirmLoading={create.isPending} destroyOnHidden
      title={asSupervisor ? 'Open a manpower-only entry' : 'Open an execution entry'}>
      {asSupervisor && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="Manpower-only activities skip the store keeper"
          description="Blasting and buffing consume no Surface Shield, so there
            is nothing for a store keeper to count and no draft for them to
            raise. Only those activities are listed here." />
      )}
      <Form form={form} layout="vertical">
        <Row gutter={12}>
          <Col span={12}>
            <Form.Item name="work_date" label="Work date" rules={[{ required: true }]}>
              <Input type="date" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="equipment_tag" label="Equipment tag"
              rules={[{ required: true }]}><Input placeholder="522-8J80-TNK-031" /></Form.Item>
          </Col>
        </Row>
        <Form.Item label="Activity" required>
          <Select showSearch options={options} loading={acts.isFetching}
            placeholder="Sub-activity"
            onChange={(v) => setActivity(options.find((o: { value: string; row: Row }) => o.value === v)?.row ?? null)}
            optionFilterProp="label" />
        </Form.Item>
        {activity && (
          agnostic
            ? <Alert type="warning" showIcon style={{ marginBottom: 12 }}
                message="No lining system for this activity"
                description="Surface prep belongs to no lining system. Tying
                  these hours to one would trap them there if the lining plan
                  changes, so the entry is recorded against the equipment only." />
            : <Descriptions size="small" column={2} bordered style={{ marginBottom: 12 }}>
                <Descriptions.Item label="Lining system">
                  {String(activity.Lining_System_Code)}
                </Descriptions.Item>
                <Descriptions.Item label="Benchmark">
                  {String(activity.Crew_Size)} crew @{' '}
                  {String(activity.Standard_Productivity_Per_Shift)} m²/shift
                </Descriptions.Item>
              </Descriptions>
        )}
        {!asSupervisor && (
          <>
            <Divider plain>Material consumed</Divider>
            {materials.map((m, i) => (
              <Space key={i} style={{ display: 'flex', marginBottom: 8 }}>
                <Input placeholder="Material code" value={String(m.Material_Code ?? '')}
                  onChange={(e) => setMaterials((r) => r.map((x, j) =>
                    j === i ? { ...x, Material_Code: e.target.value } : x))} />
                <Input placeholder="SAP" value={String(m.SAP_Code ?? '')}
                  style={{ width: 110 }}
                  onChange={(e) => setMaterials((r) => r.map((x, j) =>
                    j === i ? { ...x, SAP_Code: e.target.value } : x))} />
                <InputNumber placeholder="Qty" min={0} value={num(m.Actual_Qty)}
                  onChange={(v) => setMaterials((r) => r.map((x, j) =>
                    j === i ? { ...x, Actual_Qty: v ?? 0 } : x))} />
                <Input placeholder="UOM" value={String(m.UOM ?? '')} style={{ width: 80 }}
                  onChange={(e) => setMaterials((r) => r.map((x, j) =>
                    j === i ? { ...x, UOM: e.target.value } : x))} />
                <Button danger onClick={() =>
                  setMaterials((r) => r.filter((_, j) => j !== i))}>Remove</Button>
              </Space>
            ))}
            <Button icon={<PlusOutlined />} onClick={() =>
              setMaterials((r) => [...r, { Material_Code: '', SAP_Code: '', Actual_Qty: 0, UOM: '' }])}>
              Add material line
            </Button>
          </>
        )}
      </Form>
    </Modal>
  )
}

/**
 * The crop of the photograph a figure came from.
 *
 * ⚠️ AUTHENTICATED, SO IT CANNOT BE A BARE <img src>. The token lives in
 * localStorage and never in a URL — putting it in a query string would write
 * it into every proxy log between here and the server. The blob is fetched
 * through the same axios instance as everything else and handed to the tag as
 * an object URL, which is revoked on unmount.
 */
function RowCrop({ entryId, row }: { entryId: number; row: number }) {
  const [url, setUrl] = useState<string>('')
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let live = true
    let made = ''
    api.get(`/execution/entries/${entryId}/crop`, {
      params: { row }, responseType: 'blob',
    }).then((r) => {
      if (!live) return
      made = URL.createObjectURL(r.data as Blob)
      setUrl(made)
      // ⚠️ THE SERVER SAYS WHETHER THE CROP IS TRUSTWORTHY. When the page could
      // not be rectified it returns the WHOLE photo rather than cropping by
      // guesswork — a strip captioned "row 3" that is actually row 4 invites
      // somebody to confirm a quantity against the wrong material.
      setFailed(String(r.headers['x-crop'] ?? '') !== 'row')
    }).catch(() => { if (live) setFailed(true) })
    return () => { live = false; if (made) URL.revokeObjectURL(made) }
  }, [entryId, row])

  if (!url) {
    return <Typography.Text type="secondary" style={{ fontSize: 11 }}>—</Typography.Text>
  }
  return (
    <Tooltip title={failed
      ? 'The page could not be squared up, so this is the whole photo rather '
        + 'than this row. Check the figure against the paper.'
      : `Row ${row + 1} of the form, as photographed`}>
      <span>
        <img src={url} alt={`row ${row + 1} of the form`}
          style={{ width: 120, borderRadius: 3,
                   border: failed ? '1px solid #d4b106'
                                  : '1px solid rgba(128,128,128,.35)' }} />
        {failed && <Tag color="warning" style={{ marginTop: 2 }}>whole page</Tag>}
      </span>
    </Tooltip>
  )
}


/**
 * Store keeper: verify the supervisor's figures against what left the shelf.
 *
 * ⚠️ AN EDIT HERE IS THE POINT OF THE STEP, NOT AN EXCEPTION — and it costs a
 * reason, because the HOD is about to approve a number the supervisor did not
 * write. "The store keeper changed it" with no why is not something an
 * approver can weigh.
 */
function SkVerifyModal({ entry, onClose }: { entry: Row | null; onClose: () => void }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [mats, setMats] = useState<Record<string, Row>>({})
  const [reason, setReason] = useState('')

  useEffect(() => { setMats({}); setReason('') }, [entry?.id])

  const lines = (entry?.materials as Row[]) ?? []
  const changed = lines.filter((r) => {
    const e = mats[String(r.id)]
    if (!e) return false
    const q = e.Actual_Qty != null
      && Math.abs(Number(e.Actual_Qty) - Number(r.Actual_Qty ?? 0)) > 1e-9
    const l = e.Lot_No != null && String(e.Lot_No) !== String(r.Lot_No ?? '')
    return q || l
  })

  const save = useMutation({
    mutationFn: (b: Row) =>
      api.post(`/execution/entries/${entry?.id}/sk-verify`, b).then((r) => r.data),
    onSuccess: () => {
      message.success('Sent to the HOD')
      qc.invalidateQueries({ queryKey: ['/execution/entries'] })
      onClose()
    },
    onError: (e) => message.error(errMsg(e)),
  })

  return (
    <Modal open={!!entry} onCancel={onClose} width={860} destroyOnHidden
      title={entry ? `Verify what left the store — ${entry.Entry_No}` : ''}
      okText={changed.length ? `Confirm ${changed.length} change(s)` : 'Verify as correct'}
      confirmLoading={save.isPending}
      onOk={() => save.mutate({
        materials: lines.map((r) => ({
          id: Number(r.id),
          Actual_Qty: Number(mats[String(r.id)]?.Actual_Qty ?? r.Actual_Qty ?? 0),
          Lot_No: String(mats[String(r.id)]?.Lot_No ?? r.Lot_No ?? ''),
        })),
        reason,
      })}>
      {entry && (
        <>
          <Alert type="info" showIcon style={{ marginBottom: 10 }}
            message="You are checking the supervisor's figures against the shelf"
            description="Where they disagree, yours is the number with a stock
              movement behind it — change it and say why. Anything you leave
              alone is recorded as verified, not as ignored." />
          <Table size="small" pagination={false} rowKey={(r) => String(r.id)}
            dataSource={lines}
            columns={[
              { title: '#', width: 44,
                render: (_: unknown, r: Row) =>
                  (r.Row_Index == null ? '—' : Number(r.Row_Index) + 1) },
              { title: 'Material', dataIndex: 'Material_Code' },
              { title: 'SAP', dataIndex: 'SAP_Code', width: 92 },
              { title: 'Trail', width: 160,
                render: (_: unknown, r: Row) => <QtyTrail line={r} /> },
              {
                title: 'Left the store', width: 130, align: 'right',
                render: (_: unknown, r: Row) => (
                  <InputNumber min={0} style={{ width: 110 }}
                    value={num(mats[String(r.id)]?.Actual_Qty ?? r.Actual_Qty)}
                    onChange={(v) => setMats((x) => ({
                      ...x, [String(r.id)]: { ...x[String(r.id)],
                                             Actual_Qty: v ?? 0 } }))} />
                ),
              },
              {
                title: 'Lot / batch', width: 150,
                render: (_: unknown, r: Row) => (
                  <Input style={{ width: 135 }}
                    value={String(mats[String(r.id)]?.Lot_No ?? r.Lot_No ?? '')}
                    onChange={(e) => setMats((x) => ({
                      ...x, [String(r.id)]: { ...x[String(r.id)],
                                             Lot_No: e.target.value } }))} />
                ),
              },
              ...(String(entry.Entry_Origin) === 'ocr' ? [{
                title: 'Photo', width: 132,
                render: (_: unknown, r: Row) => (r.Row_Index == null ? '—' : (
                  <RowCrop entryId={Number(entry.id)} row={Number(r.Row_Index)} />
                )),
              }] : []),
            ]} />
          {changed.length > 0 && (
            <>
              <Alert type="warning" showIcon style={{ margin: '10px 0 8px' }}
                message={`You changed ${changed.length} figure(s) — the HOD sees
                  these in red, and the supervisor is told`}
                description={changed.map((r) => String(r.Material_Code)).join(', ')} />
              <Input.TextArea rows={2} value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Why did the figure change? e.g. only 18 KG left the store" />
            </>
          )}
        </>
      )}
    </Modal>
  )
}


// ─── Supervisor: the area, the crew, and the two mandatory reasons ───────────
function SupervisorModal({ entry, onClose }: { entry: Row | null; onClose: () => void }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [form] = Form.useForm()
  const [crew, setCrew] = useState<Row[]>([])
  const roles = useQuery({
    queryKey: ['/sme/master/roles'],
    queryFn: async () => (await api.get<{ items: Row[] }>('/sme/master/roles')).data.items,
  })

  const save = useMutation({
    mutationFn: (b: Row) =>
      api.post(`/execution/entries/${entry?.id}/supervisor`, b).then((r) => r.data),
    onSuccess: (d: Row) => {
      message.success(String(d?.status) === 'PENDING_SK'
        ? 'Sent to the store keeper to verify'
        : 'Sent to the HOD')
      qc.invalidateQueries({ queryKey: ['/execution/entries'] })
      form.resetFields(); setCrew([]); setMats({}); onClose()
    },
    onError: (e) => message.error(errMsg(e)),
  })

  // Only the lines the supervisor actually touched are sent; the API keeps the
  // rest at whatever the camera read.
  const [mats, setMats] = useState<Record<string, Row>>({})
  const isOcr = String(entry?.Entry_Origin ?? '') === 'ocr'

  const submit = async () => {
    const v = await form.validateFields()
    save.mutate({
      actual_sqm: v.actual_sqm,
      manpower: crew.filter((c) => c.Role_Code),
      material_variance_reason: v.material_variance_reason,
      manpower_variance_reason: v.manpower_variance_reason,
      materials: (entry?.materials as Row[] ?? []).map((r) => ({
        id: Number(r.id),
        Actual_Qty: Number(mats[String(r.id)]?.Actual_Qty ?? r.Actual_Qty ?? 0),
        Lot_No: String(mats[String(r.id)]?.Lot_No ?? r.Lot_No ?? ''),
      })),
    })
  }

  return (
    <Modal open={!!entry} onCancel={onClose} onOk={submit} width={760}
      confirmLoading={save.isPending} destroyOnHidden
      title={entry ? `Report execution — ${entry.Entry_No}` : ''}>
      {entry && (
        <>
          <Descriptions size="small" column={3} bordered style={{ marginBottom: 12 }}>
            <Descriptions.Item label="Equipment">{String(entry.Equipment_Tag_No)}</Descriptions.Item>
            <Descriptions.Item label="System">
              {String(entry.Lining_System_Code) || <Tag>surface prep</Tag>}
            </Descriptions.Item>
            <Descriptions.Item label="Sub-activity">
              {String(entry.Execution_Sub_Activity_Code)}
            </Descriptions.Item>
          </Descriptions>

          {(entry.materials as Row[])?.length > 0 && (
            <>
              <Alert
                type={isOcr ? 'warning' : 'info'} showIcon
                style={{ marginBottom: 8 }}
                message={isOcr
                  ? 'Check every figure against your form before you send it'
                  : 'Enter what you actually used, per material'}
                description={isOcr
                  ? 'The grey number is what the camera read. Where it could not '
                    + 'be sure it left the box empty rather than guessing — those '
                    + 'rows are marked, and the photo of each line is beside it.'
                  : 'Write 0 for anything you did not use. The store keeper '
                    + 'checks these against what left the shelf next.'} />
              <Table size="small" pagination={false} rowKey={(r) => String(r.id)}
                style={{ marginBottom: 12 }}
                dataSource={entry.materials as Row[]}
                columns={[
                  { title: '#', width: 46,
                    render: (_: unknown, r: Row) =>
                      (r.Row_Index == null ? '—' : Number(r.Row_Index) + 1) },
                  { title: 'Material', dataIndex: 'Material_Code' },
                  { title: 'SAP', dataIndex: 'SAP_Code', width: 96 },
                  { title: 'Read', width: 150,
                    render: (_: unknown, r: Row) => <QtyTrail line={r} /> },
                  {
                    title: 'Qty used', width: 130, align: 'right',
                    render: (_: unknown, r: Row) => (
                      <InputNumber min={0} style={{ width: 110 }}
                        value={num(mats[String(r.id)]?.Actual_Qty
                                   ?? r.Actual_Qty)}
                        onChange={(v) => setMats((x) => ({
                          ...x, [String(r.id)]: { ...x[String(r.id)],
                                                  Actual_Qty: v ?? 0 } }))} />
                    ),
                  },
                  { title: 'UOM', dataIndex: 'UOM', width: 70 },
                  {
                    // ⚠️ PER LINE, NOT PER FORM. Each material arrives from its
                    // own batch, and the certificate gate at approval checks
                    // the lot for THAT material.
                    title: 'Lot / batch', width: 160,
                    render: (_: unknown, r: Row) => (
                      <Input style={{ width: 145 }} placeholder="batch no."
                        value={String(mats[String(r.id)]?.Lot_No
                                      ?? r.Lot_No ?? '')}
                        onChange={(e) => setMats((x) => ({
                          ...x, [String(r.id)]: { ...x[String(r.id)],
                                                 Lot_No: e.target.value } }))} />
                    ),
                  },
                  ...(isOcr ? [{
                    title: 'Photo', width: 132,
                    render: (_: unknown, r: Row) => (r.Row_Index == null ? '—' : (
                      <RowCrop entryId={Number(entry.id)}
                        row={Number(r.Row_Index)} />
                    )),
                  }] : []),
                ]} />
            </>
          )}

          <Form form={form} layout="vertical">
            <Form.Item name="actual_sqm" label="Actual area done (m²)"
              rules={[{ required: true, message: 'the area is required' }]}>
              <InputNumber min={0.01} step={1} style={{ width: 220 }} />
            </Form.Item>

            <Divider plain>Crew that did the work</Divider>
            {crew.map((c, i) => (
              <Space key={i} style={{ display: 'flex', marginBottom: 8 }}>
                <Select style={{ width: 220 }} placeholder="Role"
                  value={c.Role_Code ? String(c.Role_Code) : undefined}
                  options={(roles.data ?? []).map((r: Row) => ({
                    value: String(r.Role_Code), label: String(r.Name) }))}
                  onChange={(v) => setCrew((x) => x.map((y, j) =>
                    j === i ? { ...y, Role_Code: v } : y))} />
                <InputNumber placeholder="Head" min={0} value={num(c.Headcount)}
                  onChange={(v) => setCrew((x) => x.map((y, j) =>
                    j === i ? { ...y, Headcount: v ?? 0 } : y))} />
                <InputNumber placeholder="Hours each" min={0} step={0.5}
                  value={num(c.Hours)}
                  onChange={(v) => setCrew((x) => x.map((y, j) =>
                    j === i ? { ...y, Hours: v ?? 0 } : y))} />
                <Button danger onClick={() =>
                  setCrew((x) => x.filter((_, j) => j !== i))}>Remove</Button>
              </Space>
            ))}
            <Button icon={<PlusOutlined />} style={{ marginBottom: 12 }}
              onClick={() => setCrew((x) => [...x, { Role_Code: '', Headcount: 0, Hours: 11 }])}>
              Add crew line
            </Button>

            <Alert type="warning" showIcon style={{ marginBottom: 12 }}
              message="Both reasons are required, even at zero variance"
              description="A reason demanded only past a threshold teaches
                people to aim just under it. A zero-variance entry with a stated
                reason is evidence you looked at the comparison." />
            <Form.Item name="material_variance_reason" label="Reason — material variance"
              rules={[{ required: true, message: 'required on every entry' }]}>
              <Input.TextArea rows={2} />
            </Form.Item>
            <Form.Item name="manpower_variance_reason" label="Reason — manpower variance"
              rules={[{ required: true, message: 'required on every entry' }]}>
              <Input.TextArea rows={2} />
            </Form.Item>
          </Form>
        </>
      )}
    </Modal>
  )
}

// ─── HOD: review, correct, decide ────────────────────────────────────────────
function HodModal({ entry, onClose }: { entry: Row | null; onClose: () => void }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [sqm, setSqm] = useState<number | null>(null)
  const [mats, setMats] = useState<Record<number, number>>({})
  const [crew, setCrew] = useState<Record<number, { Headcount?: number; Hours?: number }>>({})
  const [why, setWhy] = useState('')
  const [reject, setReject] = useState('')
  const [qsepReason, setQsepReason] = useState('')

  // ⚠️ ASKED BEFORE THE BUTTON IS PRESSED, not after. A blocked entry is
  // something an HOD can see coming and chase Logistics about; a refusal that
  // only arrives on submit teaches them to press again with the override on.
  const qsep = useQuery({
    queryKey: ['/execution/qsep', entry?.id],
    enabled: !!entry?.id && String(entry?.status) === 'PENDING_HOD',
    queryFn: async () => (await api.get<{ blocked: Row[]; clear: boolean }>(
      `/execution/entries/${entry?.id}/qsep`)).data,
  })
  const blocked = qsep.data?.blocked ?? []

  const v = entry?.variance as Row | undefined
  const mv = v?.material_total as Row | undefined
  const pv = v?.manpower as Row | undefined

  const decide = useMutation({
    mutationFn: (b: Row) =>
      api.post(`/execution/entries/${entry?.id}/decision`, b).then((r) => r.data),
    onSuccess: (_d, b) => {
      message.success((b as Row).approve ? 'Approved' : 'Rejected')
      qc.invalidateQueries({ queryKey: ['/execution/entries'] })
      setSqm(null); setMats({}); setCrew({}); setWhy('')
      setReject(''); setQsepReason(''); onClose()
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const edited = sqm != null || Object.keys(mats).length > 0
    || Object.keys(crew).length > 0

  return (
    <Modal open={!!entry} onCancel={onClose} destroyOnHidden width={860}
      title={entry ? `Approve execution — ${entry.Entry_No}` : ''}
      footer={entry && [
        <Popconfirm key="r" title="Reject this entry?"
          description={<Input.TextArea rows={2} value={reject} placeholder="Reason"
            onChange={(e) => setReject(e.target.value)} />}
          onConfirm={() => decide.mutate({ approve: false, reject_reason: reject })}>
          <Button danger>Reject</Button>
        </Popconfirm>,
        <Button key="a" type="primary" loading={decide.isPending}
          danger={blocked.length > 0}
          disabled={blocked.length > 0 && !qsepReason.trim()}
          onClick={() => decide.mutate({
            approve: true,
            justification: why,
            actual_sqm: sqm ?? undefined,
            materials: Object.entries(mats).map(([id, q]) =>
              ({ id: Number(id), Actual_Qty: q })),
            manpower: Object.entries(crew).map(([id, c]) => ({ id: Number(id), ...c })),
            ...(blocked.length > 0
              ? { qsep_override: true, qsep_reason: qsepReason } : {}),
          })}>
          {blocked.length > 0
            ? 'Approve WITHOUT clearance'
            : edited ? 'Approve with corrections' : 'Approve'}
        </Button>,
      ]}>
      {entry && (
        <>
          {blocked.length > 0 && (
            <Alert type="error" showIcon style={{ marginBottom: 12 }}
              message={`${blocked.length} material line(s) are not cleared for issue`}
              description={(
                <>
                  <ul style={{ margin: '4px 0 8px 18px', padding: 0 }}>
                    {blocked.map((b) => (
                      <li key={String(b.line_id)}>
                        <strong>{String(b.Material_Code)}</strong>{' '}
                        <Tag color="red">{String(b.gate)}</Tag>{' '}
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {String(b.detail).slice(0, 160)}
                        </Typography.Text>
                      </li>
                    ))}
                  </ul>
                  {/* ⚠️ THE HONEST FRAMING. The drum was emptied days ago, so
                      refusing outright would only strand the record and let
                      stock overstate. The override exists — and costs a written
                      reason plus a notification to the Head of Qualities. */}
                  <Typography.Paragraph style={{ marginBottom: 6 }}>
                    The material has already been applied, so this is a
                    paperwork gap rather than something you can prevent. Chase
                    the certificate, or approve anyway and say why — the Head of
                    Qualities is told either way.
                  </Typography.Paragraph>
                  <Input.TextArea rows={2} value={qsepReason}
                    onChange={(e) => setQsepReason(e.target.value)}
                    placeholder="Why is this being approved without clearance?" />
                </>
              )} />
          )}
          <Row gutter={12} style={{ marginBottom: 12 }}>
            <Col span={8}><Card size="small">
              <Statistic title="Area reported" value={String(entry.Actual_SQM ?? '—')} suffix="m²" />
            </Card></Col>
            <Col span={8}><Card size="small">
              <Statistic title="Material vs benchmark"
                value={String(mv?.Actual ?? '—')}
                suffix={<span style={{ fontSize: 13 }}>
                  / {String(mv?.Benchmark ?? '—')} <VarianceTag value={mv?.Variance_Pct} />
                </span>} />
            </Card></Col>
            <Col span={8}><Card size="small">
              <Statistic title="Man-hours vs benchmark"
                value={String(pv?.Actual_Manhours ?? '—')}
                suffix={<span style={{ fontSize: 13 }}>
                  / {String(pv?.Benchmark_Manhours ?? '—')} <VarianceTag value={pv?.Variance_Pct} />
                </span>} />
            </Card></Col>
          </Row>

          <Descriptions size="small" column={1} bordered style={{ marginBottom: 12 }}>
            <Descriptions.Item label="Supervisor — material reason">
              {String(entry.Material_Variance_Reason ?? '—')}
            </Descriptions.Item>
            <Descriptions.Item label="Supervisor — manpower reason">
              {String(entry.Manpower_Variance_Reason ?? '—')}
            </Descriptions.Item>
            {entry.sk_edited ? (
              <Descriptions.Item label={
                <span>Store keeper <Tag color="red">changed figures</Tag></span>}>
                {String(entry.SK_Edit_Reason ?? '—')}
              </Descriptions.Item>
            ) : null}
            {String(entry.Entry_Origin) === 'ocr' ? (
              <Descriptions.Item label="Read from">
                <Space size={4}>
                  <Tag>form {String(entry.Form_UUID ?? '—')}</Tag>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    by {String(entry.OCR_Model ?? 'the vision model')}
                  </Typography.Text>
                </Space>
              </Descriptions.Item>
            ) : null}
          </Descriptions>

          <Divider plain>Material — editable</Divider>
          <Table size="small" pagination={false} rowKey={(r) => String(r.id)}
            dataSource={(v?.materials as Row[]) ?? []}
            columns={[
              { title: 'Material', dataIndex: 'Material_Code' },
              { title: 'Benchmark', dataIndex: 'Benchmark_Qty', align: 'right',
                render: (x) => x ?? '—' },
              { title: 'Actual', dataIndex: 'Actual_Qty', align: 'right' },
              {
                // ⚠️ WHO TOUCHED THIS NUMBER, IN ORDER. Grey what the camera
                // read, amber the supervisor, red the store keeper, purple the
                // HOD. The whole reason four layers exist rather than one is
                // that the approver can see the chain, not just the answer.
                title: 'Who changed it', key: 'trail', width: 175,
                render: (_: unknown, r: Row) => {
                  const line = (entry.materials as Row[])
                    ?.find((m: Row) => m.Material_Code === r.Material_Code)
                  return line ? <QtyTrail line={line} /> : '—'
                },
              },
              { title: 'Lot / batch', key: 'lot', width: 130,
                render: (_: unknown, r: Row) => {
                  const line = (entry.materials as Row[])
                    ?.find((m: Row) => m.Material_Code === r.Material_Code)
                  return String(line?.Lot_No ?? '—')
                } },
              { title: 'Variance', dataIndex: 'Variance_Pct', align: 'right',
                render: (x) => <VarianceTag value={x} /> },
              { title: 'Correct to', key: 'e', width: 140,
                render: (_: unknown, r: Row) => {
                  const line = (entry.materials as Row[])
                    ?.find((m: Row) => m.Material_Code === r.Material_Code)
                  return (
                    <InputNumber min={0} placeholder={String(r.Actual_Qty)}
                      value={line ? mats[Number(line.id)] : undefined}
                      onChange={(x) => line && setMats((s) => {
                        const n = { ...s }
                        if (x == null) delete n[Number(line.id)]
                        else n[Number(line.id)] = Number(x)
                        return n
                      })} />)
                } },
            ]} />

          <Divider plain>Crew (supervisor) — editable</Divider>
          <Table size="small" pagination={false} rowKey={(r) => String(r.id)}
            dataSource={(entry.manpower as Row[]) ?? []}
            columns={[
              { title: 'Role', dataIndex: 'Role_Code' },
              { title: 'Benchmark head', dataIndex: 'Bench_Headcount',
                align: 'right', render: (x) => x ?? '—' },
              { title: 'Head', dataIndex: 'Headcount', align: 'right' },
              { title: 'Hours', dataIndex: 'Hours', align: 'right' },
              { title: 'Correct head', key: 'h', width: 130,
                render: (_: unknown, r: Row) => (
                  <InputNumber min={0} placeholder={String(r.Headcount)}
                    value={crew[Number(r.id)]?.Headcount}
                    onChange={(x) => setCrew((s) => ({ ...s,
                      [Number(r.id)]: { ...s[Number(r.id)], Headcount: x ?? undefined } }))} />) },
              { title: 'Correct hours', key: 'hr', width: 130,
                render: (_: unknown, r: Row) => (
                  <InputNumber min={0} step={0.5} placeholder={String(r.Hours)}
                    value={crew[Number(r.id)]?.Hours}
                    onChange={(x) => setCrew((s) => ({ ...s,
                      [Number(r.id)]: { ...s[Number(r.id)], Hours: x ?? undefined } }))} />) },
            ]} />

          <Form layout="vertical" style={{ marginTop: 12 }}>
            <Form.Item label="Correct the area (m²)">
              <InputNumber min={0} value={sqm} onChange={setSqm}
                placeholder={String(entry.Actual_SQM ?? '')} style={{ width: 200 }} />
            </Form.Item>
            {edited && (
              <Alert type="warning" showIcon style={{ marginBottom: 8 }}
                message="You are changing somebody else's figures"
                description="A justification is required, and the supervisor is
                  notified of exactly what changed. Without it they would be
                  answering for numbers they never entered." />
            )}
            <Form.Item label="Justification for your corrections"
              required={edited} validateStatus={edited && !why.trim() ? 'error' : undefined}
              help={edited && !why.trim() ? 'required once you change a number' : undefined}>
              <Input.TextArea rows={2} value={why}
                onChange={(e) => setWhy(e.target.value)} />
            </Form.Item>
          </Form>
        </>
      )}
    </Modal>
  )
}

/**
 * The HOD's approval queue, as a standalone tab.
 *
 * Exported so the Man-Hours portal can host it directly rather than growing a
 * second review modal — one implementation of "correct a figure, justify it,
 * notify the supervisor" is the whole point.
 */
export function HodApprovalQueueTab() {
  const { data, isFetching } = useEntries('PENDING_HOD')
  const [row, setRow] = useState<Row | null>(null)
  const rows = data ?? []
  const columns: ColumnsType<Row> = [
    { title: 'Entry', dataIndex: 'Entry_No', width: 165,
      render: (v: string, r: Row) => (
        <Space size={4}>
          {String(v)}
          {String(r.Entry_Origin) === 'ocr' && (
            <Tooltip title="Read from a photographed form">
              <Tag color="geekblue" style={{ marginInlineEnd: 0 }}>OCR</Tag>
            </Tooltip>
          )}
        </Space>) },
    { title: 'Date', dataIndex: 'Work_Date', width: 105 },
    { title: 'Equipment', dataIndex: 'Equipment_Tag_No', width: 165 },
    { title: 'System', dataIndex: 'Lining_System_Code', width: 145,
      render: (v: string, r: Row) => v
        ? <SystemCode code={v} type={String(r.Type ?? '')} plain />
        : <Tag color="gold">surface prep{r.Type ? ` [${r.Type}]` : ''}</Tag> },
    { title: 'Sub-activity', dataIndex: 'Execution_Sub_Activity_Code', width: 120 },
    { title: 'Area', dataIndex: 'Actual_SQM', width: 90, align: 'right',
      render: (v) => (v == null ? '—' : `${Number(v)} m²`) },
    { title: 'Material', key: 'mv', width: 105, align: 'right',
      render: (_: unknown, r: Row) =>
        <VarianceTag value={((r.variance as Row)?.material_total as Row)?.Variance_Pct} /> },
    { title: 'Manpower', key: 'pv', width: 105, align: 'right',
      render: (_: unknown, r: Row) =>
        <VarianceTag value={((r.variance as Row)?.manpower as Row)?.Variance_Pct} /> },
    { title: 'Supervisor', dataIndex: 'supervisor_username', width: 120 },
    { title: '', key: 'a', fixed: 'right', width: 110,
      render: (_: unknown, r: Row) => (
        <Button size="small" type="primary" onClick={() => setRow(r)}>Review</Button>) },
  ]
  return (
    <div>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        Entries waiting on you. Approval is what deducts stock — nothing before
        it moves a quantity, which is what makes correcting a figure here safe.
      </Typography.Paragraph>
      <Table sticky={{ offsetHeader: 64 }} size="small" loading={isFetching}
        columns={columns} dataSource={rows} rowKey={(r) => String(r.id)}
        scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 20, showTotal: (t) => `${t} awaiting approval` }} />
      <HodModal entry={row} onClose={() => setRow(null)} />
    </div>
  )
}

/**
 * Print blank consumption forms (Phase 9c; bulk added in Phase 13a).
 *
 * ⚠️ EVERY DOWNLOAD IS A NEW SHEET. Two prints are two physical pieces of
 * paper, each with its own QR, because the upload side has to tell a RE-PRINT
 * from a RE-PHOTOGRAPH — one identity for both would make duplicate detection
 * impossible to get right. The card says so, because "download it again" is
 * otherwise the obvious thing to do with a form you have mislaid.
 *
 * ⚠️ AND THAT IS WHY BULK PRINTING EXISTS. Needing fifty sheets, people
 * printed one and PHOTOCOPIED it — which duplicates the QR, and slice 9d maps
 * handwriting to materials positionally off exactly that identity. Fifty
 * identical QRs means the intake cannot tell one tank's page from another's.
 * Asking for fifty forms here mints fifty identities instead.
 *
 * ⚠️ THE NUMBER IS FORMS, NOT SHEETS OF A4, and the difference is visible
 * rather than explained: a form of more than 18 materials spans several pages
 * under one QR, so the helper line under the box multiplies it out live.
 * Somebody who wanted 50 pieces of paper and typed 50 needs to see "= 150 A4
 * sheets" BEFORE they press print, not after the printer has started.
 */
const ROWS_PER_FORM_PAGE = 18   // mirrors consumption_form.ROWS_PER_PAGE

function FormPrintCard() {
  const { message } = App.useApp()
  const { data: systems, isLoading } = useFormSystems()
  const [code, setCode] = useState<string | undefined>()
  const [esc, setEsc] = useState<string | undefined>()
  const [copies, setCopies] = useState<number>(1)
  const [busy, setBusy] = useState(false)

  const picked = (systems ?? []).find((x) => x.Lining_System_Code === code)
  const rows = picked
    ? (esc ? undefined : picked.materials)
    : undefined

  // Pages per form, from the same 18-rows-per-page the renderer uses. Unknown
  // when a sub-activity is selected — the server filters the recipe down and
  // the picker does not know by how much — so the helper says "forms" alone
  // rather than guessing a sheet count that would be wrong.
  const pagesPerForm = rows ? Math.max(1, Math.ceil(rows / ROWS_PER_FORM_PAGE)) : null
  const totalSheets = pagesPerForm == null ? null : pagesPerForm * copies

  const go = async () => {
    if (!code) return
    setBusy(true)
    try {
      await downloadConsumptionForm(code, esc, copies)
      message.success(copies > 1
        ? `${copies} forms downloaded — every one is a separate numbered sheet `
          + 'with its own QR code'
        : 'Form downloaded — each download is a new numbered sheet')
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card size="small" style={{ marginBottom: 12 }}
      title={<Space><PrinterOutlined />Print a consumption form</Space>}>
      <Space wrap align="start">
        <Select
          style={{ width: 300 }} placeholder="Lining system" loading={isLoading}
          value={code} showSearch optionFilterProp="label"
          onChange={(v) => { setCode(v); setEsc(undefined) }}
          options={(systems ?? []).map((x) => ({
            value: x.Lining_System_Code,
            label: `${x.Lining_System_Code} — ${x.Lining_System_Name}`,
          }))} />
        <Select
          style={{ width: 220 }} placeholder="All sub-activities" allowClear
          value={esc} disabled={!picked?.sub_activities.length}
          onChange={(v) => setEsc(v)}
          options={(picked?.sub_activities ?? []).map((x) => ({
            value: x, label: x }))} />
        {/* ⚠️ FORMS, NOT PAGES. The label is the whole guard against somebody
            typing the number of pieces of paper they want. The live line
            underneath multiplies it out so the two numbers are never confused
            silently. */}
        <Space direction="vertical" size={0}>
          <InputNumber
            style={{ width: 150 }} min={1} max={200} precision={0}
            value={copies} onChange={(v) => setCopies(Math.max(1, Math.min(200, Number(v) || 1)))}
            addonBefore="Forms" aria-label="How many forms?" />
        </Space>
        <Button type="primary" icon={<DownloadOutlined />} loading={busy}
          disabled={!code} onClick={go}>Download</Button>
        {rows != null && (
          <Typography.Text type="secondary" style={{ lineHeight: '32px' }}>
            {rows} material line(s)
          </Typography.Text>
        )}
      </Space>
      {code && (
        <Typography.Paragraph style={{ marginBottom: 0, marginTop: 8, fontSize: 12 }}>
          {totalSheets == null
            ? <Typography.Text type="secondary">
                {copies} form{copies === 1 ? '' : 's'}, each a separate numbered
                sheet with its own QR code.
              </Typography.Text>
            : <Typography.Text type={copies > 1 ? undefined : 'secondary'}>
                <strong>{copies} form{copies === 1 ? '' : 's'} × {pagesPerForm} page
                {pagesPerForm === 1 ? '' : 's'} = {totalSheets} A4 sheet
                {totalSheets === 1 ? '' : 's'}</strong>
                {pagesPerForm! > 1 && (
                  <Typography.Text type="secondary">
                    {' '}— this recipe has more than {ROWS_PER_FORM_PAGE} materials,
                    so one form spans {pagesPerForm} pages under a single QR code.
                  </Typography.Text>
                )}
              </Typography.Text>}
        </Typography.Paragraph>
      )}
      <Typography.Paragraph type="secondary"
        style={{ marginBottom: 0, marginTop: 10, fontSize: 12 }}>
        The form prints every material for the system, so nobody writes a
        material name by hand, and carries a QR code holding the site, system,
        sub-activity and the sheet&rsquo;s own number. Fill in the date, equipment and area at the top, then a
        quantity and a lot number on each row, and photograph the whole
        page including the QR.
        {' '}<strong>Every form is a separate numbered sheet</strong> —
        printing again gives you new paper, not another copy of the same form.
        {' '}<strong>Never photocopy one.</strong> A copy repeats the QR code,
        and the reader identifies a sheet by it — two sheets with one code
        cannot be told apart on the way back in. Print the number you need here
        instead.
      </Typography.Paragraph>
    </Card>
  )
}


/**
 * Upload a photographed form.
 *
 * ⚠️ A JOB, NOT A REQUEST. Reading a form takes 5–120 s on a 7B vision model
 * with a cold start — longer than proxy timeouts and far longer than somebody
 * standing in a plant on mobile data will hold a page open. The upload returns
 * an id, this polls, and the state lives in Postgres so a locked phone loses
 * nothing.
 *
 * ⚠️ AND IT LANDS ON A DRAFT, NEVER A SUBMISSION. Extraction is the machine's
 * opinion. Every figure is reviewed and both mandatory reasons supplied before
 * anything moves.
 */
function OcrUploadCard({ onDraft }: { onDraft: (id: number) => void }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [jobId, setJobId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [retrying, setRetrying] = useState(false)
  // ⚠️ THE FILE IS KEPT SO "UPLOAD IT AGAIN" IS ONE CLICK, NOT A WALK BACK TO
  // THE PLANT. When a worker dies the orphan sweep clears the stored image, so
  // the server cannot re-queue the row — but the browser still has the exact
  // bytes it sent, and a supervisor who has already photographed the sheet
  // should never be asked to photograph it twice.
  const [lastFile, setLastFile] = useState<File | null>(null)

  const job = useQuery({
    queryKey: ['/execution/ocr/jobs', jobId],
    enabled: jobId != null,
    refetchInterval: (q) => {
      const st = (q.state.data as Row | undefined)?.status
      return st === 'done' || st === 'error' ? false : 2000
    },
    queryFn: async () => (await api.get<Row>(
      `/execution/ocr/jobs/${jobId}`)).data,
  })

  useEffect(() => {
    if (job.data?.status !== 'done') return
    const res = job.data.result as Row | undefined
    if (!res) return
    qc.invalidateQueries({ queryKey: ['/execution/entries'] })
    message.success(`Read ${String(res.lines)} line(s) — check them before filing`)
    onDraft(Number(res.entry_id))
    setJobId(null)
  }, [job.data?.status])

  const upload = async (file: File) => {
    setBusy(true)
    setLastFile(file)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await api.post<Row>('/execution/ocr/upload', fd)
      setJobId(Number(r.data.job_id))
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setBusy(false)
    }
    return false
  }

  /**
   * Re-run an interrupted read. Prefer the server's re-queue — it still holds
   * the prepared image, so nothing is re-uploaded over site mobile data — and
   * fall back to re-sending the file the browser kept when it does not.
   */
  const retry = async () => {
    setRetrying(true)
    try {
      if (job.data?.can_requeue && jobId != null) {
        await api.post(`/execution/ocr/jobs/${jobId}/requeue`)
        await job.refetch()
      } else if (lastFile) {
        setJobId(null)
        await upload(lastFile)
      } else {
        message.info('Photograph the form again — the original is no longer held.')
      }
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setRetrying(false)
    }
  }

  const running = jobId != null && job.data?.status !== 'error'
  const problems = (job.data?.result as Row | undefined)?.problems as string[] | undefined

  return (
    <Card size="small" style={{ marginBottom: 12 }}
      title={<Space><CameraOutlined />Upload a filled form</Space>}>
      <Space wrap align="start">
        {/* ⚠️ THE GATE WRAPS THE UPLOAD, NOT THE PAGE. Wrapping the card
            blocked the "Print a consumption form" control beside it, so
            somebody wanting a BLANK sheet was stopped by a video about
            filling one in — caught by the Playwright suite as a modal
            intercepting an unrelated click. See components/TrainingGate.tsx. */}
        <TrainingGate feature="ocr_upload">
          {(guard) => (
            <Upload
              beforeUpload={(file) => { guard(() => { void upload(file) }); return false }}
              showUploadList={false}
              accept=".jpg,.jpeg,.png,.heic,.heif,.pdf">
              <Button type="primary" icon={<UploadOutlined />}
                loading={busy || running} disabled={busy || running}>
                {running ? 'Reading the form…' : 'Photograph or upload'}
              </Button>
            </Upload>
          )}
        </TrainingGate>
      </Space>

      {running && (
        <OcrJobProgress job={job.data as OcrJobStatus | undefined}
          what="a printed consumption form" onRetry={retry} retrying={retrying} />
      )}

      {job.data?.status === 'error' && (
        <Alert type="error" showIcon style={{ marginTop: 10 }}
          message="That form could not be read"
          description={
            <>
              {String(job.data.error ?? '')}
              {/* ⚠️ A REFUSAL SHOULD NAME THE NEXT STEP, NOT JUST THE PROBLEM.
                  "No QR code was found" is the correct answer for this lane —
                  the QR is what makes positional row-mapping safe — but it is a
                  dead end for somebody holding a handwritten sheet or a
                  supplier's delivery note, neither of which has ever had one.
                  Those belong to a different lane, and the person is two clicks
                  from it rather than stuck. */}
              {/no QR code/i.test(String(job.data.error ?? '')) && (
                <Typography.Paragraph style={{ marginTop: 8, marginBottom: 0 }}>
                  If this is a <strong>handwritten consumption sheet</strong> or a{' '}
                  <strong>supplier delivery note</strong>, it has no QR code and
                  never will — read it in{' '}
                  <Link to="/entry/ocr">Entry → OCR Import</Link> instead. Only
                  forms printed from <em>Print a consumption form</em> can be
                  filed here.
                </Typography.Paragraph>
              )}
            </>
          }
          action={<Button size="small" onClick={() => setJobId(null)}>Dismiss</Button>} />
      )}
      {problems && problems.length > 0 && (
        <Alert type="warning" showIcon style={{ marginTop: 10 }}
          message="Read, but check these before filing"
          description={<ul style={{ margin: '4px 0 0 18px', padding: 0 }}>
            {problems.map((x) => <li key={x}>{x}</li>)}
          </ul>} />
      )}

      <Typography.Paragraph type="secondary"
        style={{ marginBottom: 0, marginTop: 10, fontSize: 12 }}>
        Photograph the <strong>whole page including the QR code</strong> — it is
        what tells us which form this is, and a photo without it cannot be
        matched to anything. JPG, PNG, HEIC or PDF.
        {' '}A <strong>handwritten consumption sheet or a supplier delivery
        note</strong> has no QR and belongs in{' '}
        <Link to="/entry/ocr">Entry → OCR Import</Link> instead.
        {' '}Where the handwriting is not certain the number is left
        <strong> blank rather than guessed</strong>, and those rows are marked
        for you to fill in.
      </Typography.Paragraph>
    </Card>
  )
}


// ─── the page ────────────────────────────────────────────────────────────────
export default function ExecutionPage() {
  const { user } = useAuth()
  const role = user?.role ?? ''
  const isSk = role === 'store_keeper'
  const isSup = role === 'supervisor'
  const isHod = role === 'hod' || role === 'admin'

  const { data, isFetching } = useEntries()
  const [openNew, setOpenNew] = useState(false)
  const [supRow, setSupRow] = useState<Row | null>(null)
  const [skRow, setSkRow] = useState<Row | null>(null)
  const [hodRow, setHodRow] = useState<Row | null>(null)

  const rows = data ?? []
  const columns: ColumnsType<Row> = [
    { title: 'Entry', dataIndex: 'Entry_No', width: 165,
      render: (v: string, r: Row) => (
        <Space size={4}>
          {String(v)}
          {String(r.Entry_Origin) === 'ocr' && (
            <Tooltip title="Read from a photographed form">
              <Tag color="geekblue" style={{ marginInlineEnd: 0 }}>OCR</Tag>
            </Tooltip>
          )}
        </Space>) },
    { title: 'Date', dataIndex: 'Work_Date', width: 110 },
    { title: 'Equipment', dataIndex: 'Equipment_Tag_No', width: 170 },
    { title: 'System', dataIndex: 'Lining_System_Code', width: 145,
      render: (v: string, r: Row) => v
        ? <SystemCode code={v} type={String(r.Type ?? '')} plain />
        : <Tag>surface prep{r.Type ? ` [${r.Type}]` : ''}</Tag> },
    { title: 'Sub-activity', dataIndex: 'Execution_Sub_Activity_Code', width: 130 },
    { title: 'Area', dataIndex: 'Actual_SQM', width: 90, align: 'right',
      render: (v) => (v == null ? '—' : `${Number(v)} m²`) },
    { title: 'Material', key: 'mv', width: 110, align: 'right',
      render: (_: unknown, r: Row) =>
        <VarianceTag value={((r.variance as Row)?.material_total as Row)?.Variance_Pct} /> },
    { title: 'Manpower', key: 'pv', width: 110, align: 'right',
      render: (_: unknown, r: Row) =>
        <VarianceTag value={((r.variance as Row)?.manpower as Row)?.Variance_Pct} /> },
    { title: 'Status', dataIndex: 'status', width: 160,
      render: (v: string, r: Row) => (
        <Space size={4}>
          <Tag color={STATUS_META[v]?.color}>{STATUS_META[v]?.label ?? v}</Tag>
          {r.hod_edited ? (
            <Tooltip title={String(r.HOD_Edit_Justification ?? '')}>
              <Tag color="purple">HOD edited</Tag>
            </Tooltip>) : null}
        </Space>) },
    {
      title: 'Action', key: 'a', fixed: 'right', width: 170,
      render: (_: unknown, r: Row) => {
        const st = String(r.status)
        if ((isSup || isHod) && st === 'DRAFT_SUPERVISOR') {
          return <Button size="small" type="primary"
            onClick={() => setSupRow(r)}>
            {String(r.Entry_Origin) === 'ocr' ? 'Check & file' : 'Report'}
          </Button>
        }
        if ((isSk || isHod) && st === 'PENDING_SK') {
          return <Button size="small" type="primary"
            onClick={() => setSkRow(r)}>Verify</Button>
        }
        if (isHod && st === 'PENDING_HOD') {
          return <Button size="small" type="primary"
            onClick={() => setHodRow(r)}>Review</Button>
        }
        return <Button size="small" onClick={() => setHodRow(r)}>View</Button>
      },
    },
  ]

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>Execution entries</Typography.Title>
      <Typography.Paragraph type="secondary">
        The supervisor fills a printed form in the field and photographs it, the
        store keeper verifies the quantities against what left the shelf, and
        the HOD approves — approval is what posts the area <em>and</em> deducts
        the material, so nothing before it moves a figure or a quantity.
      </Typography.Paragraph>
      <FormPrintCard />
      {(isSup || isHod || isSk) && (
        <OcrUploadCard onDraft={(id) => {
          // Land the supervisor straight on the draft they just created —
          // otherwise the next step is hunting for it in a queue.
          const found = (data ?? []).find((r) => Number(r.id) === id)
          if (found) setSupRow(found)
        }} />
      )}
      {(isSk || isSup) && (
        <Button type="primary" icon={<PlusOutlined />} style={{ marginBottom: 12 }}
          onClick={() => setOpenNew(true)}>
          {isSup ? 'Open a manpower-only entry' : 'Open an entry'}
        </Button>
      )}
      <Table sticky={{ offsetHeader: 64 }} size="small" loading={isFetching}
        columns={columns} dataSource={rows} rowKey={(r) => String(r.id)}
        scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 20, showSizeChanger: true,
                      showTotal: (t) => `${t} entries` }} />
      <OpenEntryModal open={openNew} onClose={() => setOpenNew(false)}
        asSupervisor={isSup} />
      <SupervisorModal entry={supRow} onClose={() => setSupRow(null)} />
      <SkVerifyModal entry={skRow} onClose={() => setSkRow(null)} />
      <HodModal entry={hodRow} onClose={() => setHodRow(null)} />
    </div>
  )
}
