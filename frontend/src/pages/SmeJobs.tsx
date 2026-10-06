import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import {
  Alert, App, AutoComplete, Button, Card, Checkbox, Empty, Form, Input, InputNumber, Modal, Radio,
  Select, Space, Table, Tag, Tooltip, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import SystemCode from '../sme/SystemCode'
import { fmtPackBase } from '../lib/units'
import { brand, status } from '../theme/tokens'

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

// ── Phase 20b — bulk submit: every MOUNTED card reports its live payload ─────
// A card's figures live in the card (the supervisor may have edited the code,
// the area, the ticks, the part or the remark), so the batch is built from what
// each card shows NOW — exactly what its own Submit button would send.
interface BulkPayload {
  key: string; work_date: string; tag: string; code: string; sqm: number
  consumption_ids: number[]; notes: string | null; work_area: string | null
  site_id: string; surface_state?: string
}
interface BulkEntry {
  key: string; label: string; notReady: string | null; sqm: number | null; n: number
  code: string | null; payload: BulkPayload | null
}
interface BulkCtxT {
  selected: Set<string>
  toggle: (key: string, on: boolean) => void
  report: (e: BulkEntry) => void
  drop: (key: string) => void
}
const BulkCtx = createContext<BulkCtxT | null>(null)

function useBulkSlot(entry: BulkEntry): BulkCtxT | null {
  const ctx = useContext(BulkCtx)
  const sig = JSON.stringify(entry)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { ctx?.report(entry) }, [sig])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => () => ctx?.drop(entry.key), [entry.key])
  return ctx
}

/** The card's box. Ruling Q20-8: a card that is not ready cannot be ticked,
 *  and says why. */
function BulkCheck({ ctx, entry }: { ctx: BulkCtxT | null; entry: BulkEntry }) {
  if (!ctx) return null
  const box = (
    <Checkbox aria-label={`Select ${entry.label} for bulk submit`} data-testid={`bulk-pick-${entry.key}`}
      checked={ctx.selected.has(entry.key)} disabled={!!entry.notReady}
      onChange={(e) => ctx.toggle(entry.key, e.target.checked)} />
  )
  return entry.notReady
    ? <Tooltip title={`Not ready for bulk submit: ${entry.notReady}`}>
        <span data-testid={`bulk-notready-${entry.key}`}>{box}
          <Typography.Text type="secondary" style={{ fontSize: 11, marginInlineStart: 4 }}>{entry.notReady}</Typography.Text>
        </span>
      </Tooltip>
    : box
}

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } } }
  return x?.response?.data?.detail ?? 'Something went wrong'
}

interface Candidate {
  code: string; name?: string | null; coverage: number
  covered: string[]; missing: string[]; hinted: boolean
}
/** Phase 15e — one distinct store-keeper remark on a job, parsed server-side
 * (`sme_groups.parse_note`): "Floor - 13.37 SQM Done" → Floor, 13.37. */
interface Note { text: string; sqm: number | null; area: string | null; ids: number[] }

interface Job {
  key: string; site_id: string; work_date: string; tag: string
  notes?: Note[]
  rows: Row[]; rejected: { consumption_id: number; reason: string; by?: string }[]
  edited: number; candidates: Candidate[]
  suggested_code: string | null; sqm_hint: number | null
  // Phase 15d — a Garnet (surface-prep) card
  kind?: 'lining' | 'prep'
  prep_code?: string | null
  prep_options?: { code: string; name: string }[]
  surface_hint?: 'OLD' | 'NEW' | null
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

/** Phase 15e: the part of the equipment and the remark, both pre-filled from
 * the store keeper's note and both kept with the job exactly as submitted. */
function NoteFields({ area, setArea, remark, setRemark, areas }: {
  area: string; setArea: (v: string) => void
  remark: string; setRemark: (v: string) => void
  areas: string[]
}) {
  return (
    <>
      <div>
        <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
          Part of the equipment
        </Typography.Text>
        <AutoComplete style={{ width: 200 }} value={area} onChange={(v) => setArea(String(v ?? ''))}
          aria-label="Part of the equipment" placeholder="e.g. Floor, Shell" allowClear
          options={areas.map((a) => ({ value: a }))} />
      </div>
      <div>
        <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
          Remark (kept with the job)
        </Typography.Text>
        <Input style={{ width: 320 }} value={remark} maxLength={500} aria-label="Remark"
          placeholder="e.g. Floor - 13.37 SQM Done" onChange={(e) => setRemark(e.target.value)} />
      </div>
    </>
  )
}

function JobCard({ job }: { job: Job }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [code, setCode] = useState<string | undefined>(job.suggested_code ?? undefined)
  const [sqm, setSqm] = useState<number | null>(job.sqm_hint)
  const cand = job.candidates.find((c) => c.code === code)
  const inRecipe = useMemo(() => new Set(cand?.covered ?? []), [cand])
  // ⚠️ PHASE 15e — TWO NOTES ARE TWO JOBS. A day on one equipment can carry two
  // different remarks ("Floor - 9.25 SQM Done" and "Top of Brick Coving - 4.82
  // SQM Done"). The note picked here decides the materials ticked (its own,
  // plus the rows with no remark), the area, the part and the remark; the
  // other note's materials stay on the card for their own submission.
  const notes = useMemo(() => job.notes ?? [], [job.notes])
  const multi = notes.length > 1
  const [noteIx, setNoteIx] = useState(0)
  const [area, setArea] = useState(notes[0]?.area ?? '')
  const [remark, setRemark] = useState(notes[0]?.text ?? '')
  const otherNoteIds = (ix: number) => new Set(multi
    ? notes.flatMap((x, i) => (i === ix ? [] : x.ids.map(Number))) : [])
  // ⚠️ The default selection is what the chosen system's recipe contains: the
  // toluene drawn beside a PU job is not a PU component, and ticking it would
  // compare it against a benchmark that does not exist.
  const defaultSel = (c?: Candidate, ix = noteIx) => {
    const other = otherNoteIds(ix)
    return job.rows
      .filter((r) => !other.has(Number(r.consumption_id)))
      .filter((r) => !c || c.coverage === 0 || c.covered.includes(sapKey(r.sap_code)))
      .map((r) => Number(r.consumption_id))
  }
  const [sel, setSel] = useState<number[]>(() => defaultSel(cand))
  const pickNote = (ix: number) => {
    const nt = notes[ix]
    setNoteIx(ix)
    if (nt?.sqm != null) setSqm(nt.sqm)
    setArea(nt?.area ?? '')
    setRemark(nt?.text ?? '')
    setSel(defaultSel(cand, ix))
  }
  // One note submitted, the card refetches under the SAME key with the notes
  // that are left — start again from the first of those, or the figures of
  // the job just submitted would stay in the boxes.
  const noteSig = notes.map((x) => x.text).join('\u0000')
  const [seenSig, setSeenSig] = useState(noteSig)
  if (noteSig !== seenSig) {
    setSeenSig(noteSig)
    setNoteIx(0)
    if (notes[0]?.sqm != null) setSqm(notes[0].sqm)
    setArea(notes[0]?.area ?? '')
    setRemark(notes[0]?.text ?? '')
    setSel(defaultSel(cand, 0))
  }
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
      notes: remark.trim() || null, work_area: area.trim() || null,
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
  // Phase 20b — what this card would submit right now, or why it cannot.
  const entry: BulkEntry = (() => {
    const notReady = rejected ? 'rejected — correct it on the card'
      : !code ? 'no system code — pick one'
        : !sqm ? 'no area — the remark states none; type it'
          : sel.length === 0 ? 'no material ticked' : null
    return {
      key: job.key, label: `${job.work_date} · ${job.tag}`, notReady, sqm, n: sel.length,
      code: code ?? null,
      payload: notReady ? null : {
        key: job.key, work_date: job.work_date, tag: job.tag, code: code!, sqm: sqm!,
        consumption_ids: sel, notes: remark.trim() || null, work_area: area.trim() || null,
        site_id: job.site_id,
      },
    }
  })()
  const bulk = useBulkSlot(entry)

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
      style={{ marginBottom: 10, borderColor: rejected ? status.critical : undefined }}
      title={<Space wrap>
        <BulkCheck ctx={bulk} entry={entry} />
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
      {multi && (
        <div style={{ marginBottom: 8 }} data-testid="job-notes">
          <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>
            The store keeper wrote {notes.length} notes for this day — pick the one you are submitting;
            the other stays here for its own submission:
          </Typography.Text>
          <Radio.Group value={noteIx} onChange={(e) => pickNote(Number(e.target.value))}
            optionType="button" size="small" aria-label="Store keeper's note"
            options={notes.map((nt, i) => ({
              value: i,
              label: <Tooltip title={nt.text}>
                {nt.area ?? 'Note'}{nt.sqm != null ? ` · ${r4(nt.sqm)} m²` : ''}
              </Tooltip>,
            }))} />
        </div>
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
        <NoteFields area={area} setArea={setArea} remark={remark} setRemark={setRemark}
          areas={[...new Set(notes.map((x) => x.area).filter((x): x is string => !!x))]} />
        <Button type="primary" danger={rejected} loading={submit.isPending}
          disabled={!code || !sqm || sel.length === 0}
          onClick={() => submit.mutate()}>
          {rejected ? 'Correct & resubmit' : `Submit ${sel.length} to the HOD`}
        </Button>
      </Space>
      {notes[noteIx] && (
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: '6px 0 0' }}>
          Pre-filled from the store keeper’s note “{notes[noteIx].text}” — confirm or
          correct the area, the part and the remark. The remark is saved with the job.
        </Typography.Paragraph>
      )}
      {sel.length > 0 && sel.length < job.rows.length && (
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: '6px 0 0' }}>
          <strong>Split:</strong> the {job.rows.length - sel.length} unticked material(s) stay
          on this card {multi ? 'for their own note' : 'for another system code'}.
        </Typography.Paragraph>
      )}
    </Card>
  )
}

/**
 * Phase 15d — a GARNET job: surface preparation, not lining. Its own card, its
 * own area (the m² blasted), a prep code chosen by the equipment's substrate
 * (concrete → ESC1, steel / vessel → ESC2) and the one question the benchmark
 * needs: was it an OLD surface or a NEW one? Pre-filled with this equipment's
 * last answer (ruling Q15-7), never guessed when there is none. Approving it
 * records the variance only — Garnet credits no lining progress.
 */
function PrepJobCard({ job }: { job: Job }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [code, setCode] = useState<string | undefined>(job.prep_code ?? undefined)
  const [state, setState] = useState<'OLD' | 'NEW' | undefined>(job.surface_hint ?? undefined)
  const [sqm, setSqm] = useState<number | null>(job.sqm_hint)
  const notes = job.notes ?? []
  const [area, setArea] = useState(notes[0]?.area ?? '')
  const [remark, setRemark] = useState(notes.map((x) => x.text).join(' | '))
  const rejected = job.rejected.length > 0
  const submit = useMutation({
    mutationFn: async () => (await api.post('/execution/sme-link/groups', {
      work_date: job.work_date, tag: job.tag, code, sqm, surface_state: state,
      consumption_ids: job.rows.map((r) => Number(r.consumption_id)), site_id: job.site_id,
      notes: remark.trim() || null, work_area: area.trim() || null,
    })).data,
    onSuccess: () => {
      message.success('Garnet job submitted — the HOD sees it against the ' +
        `${state === 'OLD' ? 'old' : 'new'}-surface benchmark`)
      void qc.invalidateQueries({ queryKey: ['/execution/sme-link/groups'] })
      void qc.invalidateQueries({ queryKey: ['/execution/sme-link/groups/staged'] })
    },
    onError: (e) => message.error(errMsg(e)),
  })
  // Phase 20b — what this Garnet card would submit right now, or why it cannot.
  const entry: BulkEntry = (() => {
    const notReady = rejected ? 'rejected — correct it on the card'
      : !code ? 'no substrate code'
        : !state ? 'say Old or New surface'
          : !sqm ? 'no area — the remark states none; type it' : null
    return {
      key: job.key, label: `${job.work_date} · ${job.tag} (Garnet)`, notReady, sqm,
      n: job.rows.length, code: code ?? null,
      payload: notReady ? null : {
        key: job.key, work_date: job.work_date, tag: job.tag, code: code!, sqm: sqm!,
        consumption_ids: job.rows.map((r) => Number(r.consumption_id)),
        notes: remark.trim() || null, work_area: area.trim() || null,
        site_id: job.site_id, surface_state: state,
      },
    }
  })()
  const bulk = useBulkSlot(entry)
  const cols: ColumnsType<Row> = [
    { title: 'Material', key: 'm',
      render: (_: unknown, r: Row) => (
        <Space direction="vertical" size={0}>
          <span>{s(r.material_name) || s(r.sap_code)}</span>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>SAP {s(r.sap_code)}</Typography.Text>
        </Space>) },
    { title: 'Drawn', key: 'q', align: 'right', render: (_: unknown, r: Row) => <Drawn r={r} /> },
  ]
  return (
    <Card size="small" className="gi-job-card gi-prep-card" data-job={job.key}
      style={{ marginBottom: 10, borderColor: rejected ? status.critical : brand.gold }}
      title={<Space wrap>
        <BulkCheck ctx={bulk} entry={entry} />
        <strong>{job.work_date}</strong>
        <span>·</span>
        <strong>{job.tag}</strong>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>{job.site_id}</Typography.Text>
        <Tag color="gold">Surface prep — Garnet</Tag>
        {rejected && <Tag color="red">Rejected - Needs Correction</Tag>}
      </Space>}>
      {rejected && (
        <Alert type="error" showIcon style={{ marginBottom: 8 }}
          title="The HOD sent this job back"
          description={<Typography.Text strong>“{job.rejected[0].reason || 'No reason recorded'}”</Typography.Text>} />
      )}
      <Table size="small" pagination={false} columns={cols} dataSource={job.rows}
        rowKey={(r) => String(r.consumption_id)} />
      <Space wrap align="end" style={{ marginTop: 10 }}>
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
            Old surface or new surface?
          </Typography.Text>
          <Radio.Group value={state} onChange={(e) => setState(e.target.value)}
            optionType="button" buttonStyle="solid" aria-label="Old surface or new surface"
            options={[{ value: 'OLD', label: 'Old surface' }, { value: 'NEW', label: 'New surface' }]} />
        </div>
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
            Substrate (from the equipment)
          </Typography.Text>
          <Select style={{ minWidth: 240 }} value={code} placeholder="Concrete or steel?"
            aria-label="Prep code" disabled={!!job.prep_code} onChange={setCode}
            options={(job.prep_options ?? []).map((o) => ({ value: o.code, label: `${o.code} — ${o.name}` }))} />
        </div>
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
            Area blasted (m²)
          </Typography.Text>
          <InputNumber min={0.01} step={1} value={sqm ?? undefined} aria-label="Area blasted"
            onChange={(v) => setSqm(v == null ? null : Number(v))} style={{ width: 160 }} />
        </div>
        <NoteFields area={area} setArea={setArea} remark={remark} setRemark={setRemark}
          areas={[...new Set(notes.map((x) => x.area).filter((x): x is string => !!x))]} />
        <Button type="primary" danger={rejected} loading={submit.isPending}
          disabled={!code || !sqm || !state} onClick={() => submit.mutate()}>
          {rejected ? 'Correct & resubmit' : 'Submit Garnet to the HOD'}
        </Button>
      </Space>
      {job.surface_hint && state === job.surface_hint && (
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: '6px 0 0' }}>
          Pre-filled with this equipment’s last answer — change it if this surface is different.
        </Typography.Paragraph>
      )}
      {!job.prep_code && (
        <Typography.Paragraph type="warning" style={{ fontSize: 12, margin: '6px 0 0' }}>
          The equipment master does not say whether {job.tag} is concrete or steel — pick it here.
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
  // ── Phase 20b — bulk submit ──────────────────────────────────────────────
  const { message, modal } = App.useApp()
  const qc = useQueryClient()
  const [entries, setEntries] = useState<Record<string, BulkEntry>>({})
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [review, setReview] = useState(false)
  const bulkCtx = useMemo<BulkCtxT>(() => ({
    selected,
    toggle: (k, on) => setSelected((p) => { const n = new Set(p); if (on) n.add(k); else n.delete(k); return n }),
    report: (e) => setEntries((p) => ({ ...p, [e.key]: e })),
    drop: (k) => {
      setEntries((p) => { const n = { ...p }; delete n[k]; return n })
      setSelected((p) => { if (!p.has(k)) return p; const n = new Set(p); n.delete(k); return n })
    },
  }), [selected])
  // a ticked card that stops being ready (its area cleared) leaves the batch
  useEffect(() => {
    setSelected((p) => {
      const keep = [...p].filter((k) => entries[k] && !entries[k].notReady)
      return keep.length === p.size ? p : new Set(keep)
    })
  }, [entries])
  const ready = Object.values(entries).filter((e) => !e.notReady)
  const picked = [...selected].map((k) => entries[k]).filter((e): e is BulkEntry => !!e?.payload)
  const bulk = useMutation({
    mutationFn: async () => (await api.post<{
      submitted: { key: string; group_id: number }[]
      skipped: { key: string; status: number; reason: string }[]
    }>('/execution/sme-link/groups/bulk-submit', { jobs: picked.map((e) => e.payload) })).data,
    onSuccess: (r) => {
      setReview(false)
      setSelected(new Set())
      void qc.invalidateQueries({ queryKey: ['/execution/sme-link/groups'] })
      void qc.invalidateQueries({ queryKey: ['/execution/sme-link/groups/staged'] })
      void qc.invalidateQueries({ queryKey: ['/execution/sme-link/assigned'] })
      if (r.submitted.length) message.success(`Submitted ${r.submitted.length} job(s) to the HOD`)
      if (r.skipped.length) {
        modal.warning({
          title: `${r.skipped.length} job(s) were not submitted`,
          content: <ul style={{ paddingLeft: 18 }}>{r.skipped.map((x) => (
            <li key={x.key}><b>{entries[x.key]?.label ?? x.key}</b>: {x.reason}</li>))}</ul>,
        })
      }
    },
    onError: (e) => message.error(errMsg(e)),
  })
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
      {jobs.length > 0 && (
        <Space wrap style={{ marginBottom: 10 }} data-testid="bulk-bar">
          <Button data-testid="bulk-select-ready" disabled={!ready.length}
            onClick={() => setSelected(new Set(ready.map((e) => e.key)))}>
            Select all ready ({ready.length})
          </Button>
          {selected.size > 0 && <Button onClick={() => setSelected(new Set())}>Clear</Button>}
          <Button type="primary" data-testid="bulk-submit" disabled={!picked.length}
            onClick={() => setReview(true)}>
            Submit selected to HOD ({picked.length})
          </Button>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Ready = a system code, an area and at least one material. Of the jobs shown.
          </Typography.Text>
        </Space>
      )}
      <BulkCtx.Provider value={bulkCtx}>
        {q.isLoading ? <Card loading size="small" /> : jobs.length === 0
          ? <Empty description="Nothing waiting for an area" />
          : <>
              {jobs.slice(0, shown).map((j) => (j.kind === 'prep'
                ? <PrepJobCard key={j.key} job={j} />
                : <JobCard key={j.key} job={j} />))}
              {jobs.length > shown && (
                <Button block onClick={() => setShown((x) => x + 10)}>
                  Show more ({jobs.length - shown} more job(s))
                </Button>
              )}
            </>}
      </BulkCtx.Provider>
      <Modal open={review} title={`Submit ${picked.length} job(s) to the HOD`} width={720}
        onCancel={() => setReview(false)} destroyOnHidden
        okText={`Submit ${picked.length} job(s)`} okButtonProps={{ 'data-testid': 'bulk-submit-confirm' } as never}
        confirmLoading={bulk.isPending} onOk={() => bulk.mutate()}>
        <Typography.Paragraph type="secondary">
          Each job goes to the HOD exactly as its card shows it — system code, area, ticked
          materials, part and remark. Check them: a suggested figure is a pre-fill, not the record.
        </Typography.Paragraph>
        <Table size="small" pagination={false} rowKey="key" dataSource={picked}
          columns={[
            { title: 'Job', dataIndex: 'label' },
            { title: 'System', dataIndex: 'code', width: 110 },
            { title: 'Area', dataIndex: 'sqm', align: 'right', width: 90, render: (v) => `${v} m²` },
            { title: 'Materials', dataIndex: 'n', align: 'right', width: 90 },
          ]} />
        <Typography.Paragraph style={{ marginTop: 8 }}>
          Total: <b>{r4(picked.reduce((a, e) => a + Number(e.sqm ?? 0), 0))} m²</b>
        </Typography.Paragraph>
      </Modal>
    </>
  )
}

interface StagedJob extends Row {
  id: number; Site_ID: string; Work_Date: string; Equipment_Tag_No: string
  Lining_System_Code: string; SQM_Completed: number; submitted_by?: string
  rows: Row[]; high_priority: boolean
  prep?: boolean; Surface_State?: string | null
  Work_Area?: string | null; notes?: string | null
  /** Phase 20a — the store keeper's words from the Excel log, verbatim */
  excel_remarks?: string[]
}

/** Phase 15e: the part and the remark the job was filed with. Phase 20a: the
 *  Excel remark FIRST, as typed (ruling Q20-3), then the job note when it
 *  differs from it. */
function JobNote({ job }: { job: StagedJob }) {
  const excel = job.excel_remarks ?? []
  const noteDiffers = !!job.notes && !excel.some((x) => x.toLowerCase() === s(job.notes).trim().toLowerCase())
  if (!job.Work_Area && !excel.length && !job.notes) return null
  return (
    <Typography.Paragraph style={{ margin: '0 0 8px' }} data-testid="job-remark">
      {job.Work_Area && <Tag color="blue">{job.Work_Area}</Tag>}
      {excel.map((x) => (
        <Typography.Text key={x} style={{ fontStyle: 'italic', marginRight: 8 }}>Excel: “{x}”</Typography.Text>
      ))}
      {(noteDiffers || (!excel.length && job.notes)) && (
        <Typography.Text type="secondary">Job note: “{job.notes}”</Typography.Text>)}
    </Typography.Paragraph>
  )
}

/** Phase 15d — what a Garnet job is measured against, for the HOD. */
function PrepTags({ job }: { job: StagedJob }) {
  if (!job.prep) return null
  const bench = n(job.rows[0]?.Bench_For_1_SQM)
  return <>
    <Tag color="gold">Garnet · {s(job.Surface_State) === 'OLD' ? 'Old surface' : 'New surface'}</Tag>
    {bench == null
      ? <Tooltip title="Set it in SME → Master Data → Garnet baseline"><Tag color="purple">no benchmark</Tag></Tooltip>
      : <Tag>benchmark {r4(bench)} KG/m²</Tag>}
  </>
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
            {' '}<PrepTags job={job} />
          </Typography.Paragraph>
          <JobNote job={job} />
          <StagedRows rows={job.rows} />
          {job.prep
            ? <Alert type="info" showIcon style={{ margin: '12px 0' }}
                title="Garnet is surface preparation"
                description="Approving records this job’s variance against the Garnet benchmark for its surface. It credits no lining progress — blasted area is recorded by the blasting execution entries. Rejecting sends it back with your reason." />
            : <Alert type="info" showIcon style={{ margin: '12px 0' }}
                title="One decision for the whole job"
                description="Approving credits the job’s area ONCE, whatever the number of materials. Rejecting sends every material back with your reason. The quantities cannot be changed here — the material left the shelf when it was issued." />}
          {/* ⚠️ Filled from the job AS IT RENDERS, not after the opening
              animation: an Approve clicked in that window used to read an
              empty area, take it for a change and refuse it for lack of a
              reason. */}
          <Form form={form} layout="vertical" key={job.id}
            initialValues={{ sqm: job.SQM_Completed, tag: job.Equipment_Tag_No, justification: '' }}>
            <Space wrap>
              <Form.Item name="sqm" label={job.prep ? 'Area blasted (m²)' : 'Area covered (m²)'}>
                <InputNumber min={0.01} step={1} style={{ width: 180 }} />
              </Form.Item>
              {!job.prep && (
                <Form.Item name="tag" label="Equipment / tank">
                  <Select showSearch style={{ minWidth: 220 }} loading={equipment.isFetching}
                    options={(equipment.data ?? []).map((x) => ({ value: s(x.tag), label: s(x.tag) }))} />
                </Form.Item>
              )}
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
  // ── Phase 20b — select by date / system code, approve in one click ────────
  const { message, modal } = App.useApp()
  const qc = useQueryClient()
  const [dateF, setDateF] = useState<string | undefined>()
  const [codeF, setCodeF] = useState<string | undefined>()
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [confirm, setConfirm] = useState(false)
  const all = [...(q.data ?? [])].sort((a, b) =>
    Number(b.high_priority) - Number(a.high_priority) || s(a.Work_Date).localeCompare(s(b.Work_Date)))
  const jobs = all.filter((j) => (!dateF || j.Work_Date === dateF) && (!codeF || j.Lining_System_Code === codeF))
  const chosen = all.filter((j) => picked.has(j.id))
  const risky = chosen.filter((j) => j.high_priority)
  const sqmTotal = r4(chosen.filter((j) => !j.prep).reduce((a, j) => a + Number(j.SQM_Completed || 0), 0))
  useEffect(() => {        // a job decided elsewhere leaves the selection
    const ids = new Set(all.map((j) => j.id))
    setPicked((p) => { const k = [...p].filter((i) => ids.has(i)); return k.length === p.size ? p : new Set(k) })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.data])
  const approve = useMutation({
    mutationFn: async () => (await api.post<{
      approved: { id: number; sqm_credited: number | null }[]
      skipped: { id: number; status: number; reason: string }[]
    }>('/execution/sme-link/groups/bulk-approve', { ids: chosen.map((j) => j.id) })).data,
    onSuccess: (r) => {
      setConfirm(false)
      setPicked(new Set())
      void qc.invalidateQueries({ queryKey: ['/execution/sme-link/groups/staged'] })
      void qc.invalidateQueries({ queryKey: ['/execution/sme-link/assigned'] })
      if (r.approved.length) message.success(`Approved ${r.approved.length} job(s)`)
      if (r.skipped.length) {
        modal.warning({
          title: `${r.skipped.length} job(s) were not approved`,
          content: <ul style={{ paddingLeft: 18 }}>{r.skipped.map((x) => {
            const j = all.find((y) => y.id === x.id)
            return <li key={x.id}><b>{j ? `${j.Work_Date} · ${j.Equipment_Tag_No}` : `#${x.id}`}</b>: {x.reason}</li>
          })}</ul>,
        })
      }
    },
    onError: (e) => message.error(errMsg(e)),
  })
  if (!q.isLoading && all.length === 0) return null
  const toggle = (id: number, on: boolean) =>
    setPicked((p) => { const n = new Set(p); if (on) n.add(id); else n.delete(id); return n })
  return (
    <>
      {isHod && (
        <Space wrap style={{ marginBottom: 10 }} data-testid="hod-bulk-bar">
          <Select allowClear placeholder="Date" style={{ width: 140 }} value={dateF} onChange={setDateF}
            aria-label="Filter by date"
            options={[...new Set(all.map((j) => j.Work_Date))].sort().map((d) => ({ value: d, label: d }))} />
          <Select allowClear placeholder="System code" style={{ width: 150 }} value={codeF} onChange={setCodeF}
            aria-label="Filter by system code"
            options={[...new Set(all.map((j) => j.Lining_System_Code))].sort().map((c) => ({ value: c, label: c }))} />
          <Button data-testid="hod-select-all" onClick={() => setPicked(new Set([...picked, ...jobs.map((j) => j.id)]))}>
            Select all{dateF || codeF ? ' shown' : ''} ({jobs.length})
          </Button>
          {picked.size > 0 && <Button onClick={() => setPicked(new Set())}>Clear</Button>}
          <Button type="primary" data-testid="hod-approve-selected" disabled={!picked.size}
            onClick={() => setConfirm(true)}>
            Approve selected ({picked.size})
          </Button>
        </Space>
      )}
      {jobs.map((j) => (
        <Card key={j.id} size="small" className="gi-job-card" style={{ marginBottom: 10 }}
          title={<Space wrap>
            {isHod && <Checkbox aria-label={`Select ${j.Work_Date} ${j.Equipment_Tag_No}`}
              data-testid={`hod-pick-${j.id}`} checked={picked.has(j.id)}
              onChange={(e) => toggle(j.id, e.target.checked)} />}
            {j.high_priority ? <Tag color="red">High Priority</Tag> : <Tag>Normal</Tag>}
            <strong>{j.Work_Date} · {j.Equipment_Tag_No}</strong>
            <SystemCode code={j.Lining_System_Code} plain />
            <Tag>{r4(Number(j.SQM_Completed))} m²</Tag>
            <Tag>{j.rows.length} material(s)</Tag>
            <PrepTags job={j} />
          </Space>}
          extra={isHod
            ? <Button size="small" type="primary" onClick={() => setOpen(j)}>Review job</Button>
            : <Typography.Text type="secondary">with the HOD</Typography.Text>}>
          <JobNote job={j} />
          <StagedRows rows={j.rows} />
        </Card>
      ))}
      <JobDecideModal job={open} onClose={() => setOpen(null)} />
      <Modal open={confirm} title={`Approve ${chosen.length} job(s)`} width={720} destroyOnHidden
        onCancel={() => setConfirm(false)} okText={`Approve ${chosen.length} job(s)`}
        okButtonProps={{ 'data-testid': 'hod-approve-confirm' } as never}
        confirmLoading={approve.isPending} onOk={() => approve.mutate()}>
        <Typography.Paragraph>
          Each job is approved as a whole and its area credited <b>once</b>: <b>{sqmTotal} m²</b> of
          lining in all{chosen.some((j) => j.prep) ? ' (Garnet jobs credit no lining area)' : ''}.
          The quantities cannot change here — the material left the store when it was issued.
          To reject a job, open it: a rejection needs its own reason.
        </Typography.Paragraph>
        {/* ⚠️ RULING Q20-10 — a variance above 10 % may be approved in bulk, but
            never silently: each such job is named here before the click. */}
        {risky.length > 0 && (
          <Alert type="warning" showIcon style={{ marginBottom: 8 }} data-testid="hod-risky"
            title={`${risky.length} of these used materials more than 10 % off the recipe`}
            description={<ul style={{ paddingLeft: 18, margin: 0 }}>{risky.map((j) => (
              <li key={j.id}>{j.Work_Date} · {j.Equipment_Tag_No} · {j.Lining_System_Code} — {
                j.rows.filter((r) => r.Priority_Flag === 'HIGH').map((r) =>
                  `${s(r.Material_Code)} ${Number(r.Variance_Pct) > 0 ? '+' : ''}${r4(Number(r.Variance_Pct))} %`).join(', ')}</li>
            ))}</ul>} />
        )}
        <Table size="small" pagination={false} rowKey="id" dataSource={chosen}
          columns={[
            { title: 'Date', dataIndex: 'Work_Date', width: 110 },
            { title: 'Equipment', dataIndex: 'Equipment_Tag_No' },
            { title: 'System', dataIndex: 'Lining_System_Code', width: 110 },
            { title: 'Area', dataIndex: 'SQM_Completed', align: 'right', width: 90,
              render: (v: number) => `${r4(Number(v))} m²` },
            { title: '', key: 'p', width: 110,
              render: (_: unknown, j: StagedJob) => (j.high_priority ? <Tag color="red">High Priority</Tag> : null) },
          ]} />
      </Modal>
    </>
  )
}
