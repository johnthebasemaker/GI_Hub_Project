import { useEffect, useMemo, useState } from 'react'
import type { Key } from 'react'
import PracticeNotice from '../components/PracticeNotice'
import {
  Alert, App, Button, Card, DatePicker, Descriptions, Input, InputNumber, Popconfirm, Radio,
  Select, Space, Tag, Typography, Upload,
} from 'antd'
import { Table } from '../lib/smartTable'
import { CameraOutlined, DeleteOutlined, DownloadOutlined, InboxOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import dayjs, { Dayjs } from 'dayjs'
import { api } from '../api/client'
import type { Row as ApiRow } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { fillPageTank, inheritTankFromAbove, insertBySno, undoPageTank } from '../lib/pageTank'
import type { TankSnapshot } from '../lib/pageTank'
import { useDocsRequired, useList, useSites, useWbsOptions } from '../api/hooks'
import EntryDocsUpload from '../components/EntryDocsUpload'
import type { EntryDoc } from '../components/EntryDocsUpload'
import OcrJobProgress from '../components/OcrJobProgress'
import type { OcrJobStatus } from '../components/OcrJobProgress'
import { status } from '../theme/tokens'
import { pickPreparers } from '../components/PreparersCard'
import type { PreparerEntry } from '../components/PreparersCard'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

type Kind = 'ocr_consumption' | 'ocr_delivery_note'

interface OcrRow extends ApiRow {
  material_text: string
  // null when the model could not read the box. The vision lane stops
  // inventing 0 for an unreadable quantity (backend `clean_consumption_row`)
  // — a blank cell is a question the store keeper answers, a 0 is a number
  // nobody re-reads.
  quantity: number | null
  uom: string
  issued_to?: string
  work_type?: string
  tank_no?: string
  qty_text?: string
  struck_through?: boolean
  SAP_Code: string
  // Phase 21d (Q21-5): 'suggested' is a GOLD match the store keeper must
  // accept — only an exact or a learned match is 'auto' (green). 'pick' is the
  // delivery-note lane's older state.
  match_state: 'auto' | 'pick' | 'unknown' | 'suggested'
  candidates: { SAP_Code: string; Equipment_Description: string; score: number }[]
  markers?: string[]
  blocked?: boolean
  match_source?: string | null
  learned_count?: number
  suggestion?: { SAP_Code: string; description: string; score: number; in_stock: boolean } | null
  // Phase 22e — the tank, matched like a name (Q22-17)
  tank_state?: 'auto' | 'suggested' | 'unknown' | 'ditto' | 'blank'
  tank_tag?: string | null
  tank_source?: string | null
  tank_group?: string
  tank_candidates?: { tag: string; cost: number }[]
  // Phase 22e — Compare with the workbook (Q22-19)
  wb_status?: 'same' | 'differs' | 'missing'
  wb_row?: number | null
  wb_diffs?: Record<string, [unknown, unknown]>
  // Phase 23b — the printed row number, and a row the second read found
  sno?: string | number | null
  second_read?: boolean
}

interface SecondReadState {
  status: 'running' | 'done' | 'error'
  rows: number[]
  expected_s?: number
  added?: OcrRow[]
  seconds?: number
}

interface PageTanks {
  from: string; to: string
  recent: { tag: string; lines: number }[]
  learned: string[]
}

interface TankMatch {
  written: string; state: OcrRow['tank_state']; tag: string | null; source: string | null
  group?: string; candidates: { tag: string; cost: number }[]
}
interface CompareRes {
  in_workbook: number
  lines: { index: number; status: 'same' | 'differs' | 'missing'; row: number | null; diffs: Record<string, [unknown, unknown]> }[]
  extra: { row: number | null; SAP_Code: string; quantity: number | null; tank: string | null }[]
  counts: Record<string, number>
}

interface NameMatch {
  written: string; state: 'auto' | 'suggested' | 'unknown'; sap: string | null
  description: string | null; source: string | null; score: number; learned_count: number
  candidates: { SAP_Code: string; Equipment_Description: string; score: number; in_stock: boolean }[]
}

interface HwRow {
  product_name_raw?: string; received_by?: string; tank_no?: string
  work_type?: string; qty?: number; sap_code?: string; date_iso?: string
  resolved_description?: string; substitution_reason?: string
  candidates?: { SAP_Code: string; Equipment_Description: string; confidence: number }[]
  flags?: string[]; markers?: string[]; blocked?: boolean
}

interface HwSummary {
  total_rows: number; auto_matched: number; needs_review: number
  blocked: number; substituted: number; struck_through_excluded: number
}

// Phase 21d follow-up — the paper's date box, checked (POST /ai/ocr/paper-check).
interface PaperDate {
  read: string; date_iso: string | null; shift: string | null; plausible: boolean
  days_from_today: number | null
  candidates: { date_iso: string; label: string; cost: number }[]
}

interface DnHeader { DN_No: string; Date: string; Mob_From: string; Driver_Name: string; Vehicle_No: string; Prepared_by: string; Mob_To: string }

const MATCH_COLOR = { auto: 'green', pick: 'gold', unknown: 'red', suggested: 'gold' } as const
const MATCH_LABEL = { auto: 'matched', pick: 'pick', unknown: 'not found', suggested: 'check' } as const

/** [2,3,4,9] → "2–4, 9" */
function snoRuns(nums: number[]): string {
  const out: [number, number][] = []
  for (const n of [...new Set(nums.filter((x) => Number.isFinite(x)))].sort((a, b) => a - b)) {
    const last = out[out.length - 1]
    if (last && n === last[1] + 1) last[1] = n
    else out.push([n, n])
  }
  return out.map(([a, b]) => (a === b ? `${a}` : `${a}–${b}`)).join(', ')
}

// 📷 OCR Import — the new-stack port of the legacy Daily Issue Log OCR lanes.
// Photo lane: POST /ai/jobs → poll → review. Paste lane: instant + offline.
// Both lanes land in the SAME review grid; staging goes through the existing
// exact-locked /entry/consumption and /entry/receipts services.
export default function OcrImportPage() {
  const { message } = App.useApp()
  const { user } = useAuth()
  const { data: sites } = useSites()
  const inventory = useList('/inventory', { limit: 1000 })

  const [kind, setKind] = useState<Kind>('ocr_consumption')
  const [jobId, setJobId] = useState<number | null>(null)
  const [rows, setRows] = useState<OcrRow[]>([])
  const [header, setHeader] = useState<DnHeader | null>(null)
  const [pasteText, setPasteText] = useState('')
  const [dateText, setDateText] = useState<string | null>(null)
  const [tsv, setTsv] = useState<string | null>(null)
  const [hwSummary, setHwSummary] = useState<HwSummary | null>(null)
  const [date, setDate] = useState<Dayjs>(dayjs())
  // The paper's date as read, and whether the store keeper has settled it. A
  // date outside the last two weeks holds the sheet back until one is chosen:
  // 3 of the operator's 11 test pages were read with the wrong month or day.
  const [paperDate, setPaperDate] = useState<PaperDate | null>(null)
  const [dateConfirmed, setDateConfirmed] = useState(true)
  const [site, setSite] = useState<string | undefined>(user?.site_id || undefined)
  const [staging, setStaging] = useState(false)
  const [retrying, setRetrying] = useState(false)
  // Kept so an interrupted read can be re-sent in one click — the orphan
  // sweep clears the server's copy of the image, and a store keeper should
  // not have to fetch the paper back to the desk to try again.
  const [lastFile, setLastFile] = useState<File | null>(null)
  // ⚠️ Phase 21g fix: staging went through /entry/consumption WITHOUT a
  // supporting document, so with `require_entry_documents` on (the default,
  // and Live's setting) every OCR stage was refused. The paper IS the
  // document: a photographed page is attached automatically; a pasted one is
  // attached by hand, like any entry. A site with a WBS list needs its WBS.
  const [docs, setDocs] = useState<EntryDoc[]>([])
  const [wbs, setWbs] = useState<string | undefined>(undefined)
  const { data: docsRequired } = useDocsRequired()
  const { data: wbsOptions } = useWbsOptions(site)

  const isAdmin = user?.role === 'admin'
  const isConsumption = kind === 'ocr_consumption'

  // Phase 22e — the shift written by the date ("(Night)"; nothing = Day, Q22-14)
  // and the site's preparer for it on the paper's date (Q22-16)
  const [shift, setShift] = useState<{ shift: 'Day' | 'Night'; marked: boolean } | null>(null)
  const [preparedOverride, setPreparedOverride] = useState<string | null>(null)
  const [tankTags, setTankTags] = useState<string[]>([])
  const [selected, setSelected] = useState<Key[]>([])
  const [bulkTank, setBulkTank] = useState<string | undefined>()
  // Phase 23b — the tank for the whole page (Q23-2) and the background second
  // read of the rows the first read skipped (Q23-3)
  const [pageTank, setPageTank] = useState<string | undefined>()
  const [pageTankUndo, setPageTankUndo] = useState<Record<string, TankSnapshot> | null>(null)
  const [second, setSecond] = useState<{ jobId: number; rows: number[]; since: number; expected: number } | null>(null)
  const [secondNote, setSecondNote] = useState<{ type: 'info' | 'success' | 'warning'; text: string } | null>(null)
  const [cmp, setCmp] = useState<CompareRes | null>(null)
  const { data: prepData } = useQuery<{ history: PreparerEntry[] }>({
    queryKey: ['/ai/ocr/preparers', site],
    enabled: !!site && isConsumption,
    queryFn: async () => (await api.get('/ai/ocr/preparers', { params: { site_id: site } })).data,
  })
  const preparedBy = useMemo(() => {
    if (preparedOverride != null) return preparedOverride
    const p = pickPreparers(prepData?.history ?? [], date.format('YYYY-MM-DD'))
    if (!p || !shift) return ''
    return (shift.shift === 'Night' ? p.night : p.day) || ''
  }, [preparedOverride, prepData, date, shift])

  const { data: aiHealth } = useQuery({
    queryKey: ['/ai/health'],
    queryFn: async () => (await api.get('/ai/health')).data as { ok: boolean; message: string },
  })

  // Poll the job while queued/running; load the result rows once done.
  const job = useQuery({
    queryKey: ['/ai/jobs', jobId],
    enabled: jobId != null,
    refetchInterval: (q) => {
      const s = (q.state.data as { status?: string } | undefined)?.status
      return s === 'queued' || s === 'running' ? 2000 : false
    },
    queryFn: async () => {
      const r = (await api.get(`/ai/jobs/${jobId}`)).data
      if (r.status === 'done' && r.result) {
        adopt(r.result)
        if (lastFile) void attachPhoto(lastFile)
        const sr = r.result.second_read as SecondReadState | undefined
        if (sr?.status === 'running' && jobId != null) {
          setSecond({ jobId, rows: sr.rows, since: Date.now(), expected: sr.expected_s ?? 90 })
          setSecondNote({ type: 'info', text: `Reading again the printed rows the first read skipped (S.No ${snoRuns(sr.rows)}). `
            + 'Keep working — any rows found are added here, marked “2nd read”.' })
        }
        setJobId(null)
        message.success('Photo read — review the rows below')
      } else if (r.status === 'error') {
        setJobId(null)
        message.error(r.error ?? 'OCR failed')
      }
      return r
    },
  })

  // Phase 23b — the second read finishes in the background; the page keeps
  // working and its rows slot in at their printed S.No when they arrive.
  useQuery({
    queryKey: ['/ai/jobs/second', second?.jobId],
    enabled: second != null,
    refetchInterval: 4000,
    queryFn: async () => {
      if (!second) return null
      const r = (await api.get(`/ai/jobs/${second.jobId}`)).data
      const sr = r.result?.second_read as SecondReadState | undefined
      const late = Date.now() - second.since > Math.max(300, second.expected * 3) * 1000
      if (sr?.status === 'done') {
        setSecond(null)
        const added = sr.added ?? []
        if (added.length) {
          await addSecondRows(added)
          setSecondNote({ type: 'success', text: `The second read found ${added.length} more row(s): S.No `
            + `${snoRuns(added.map((x) => Number(x.sno)))}. They are marked “2nd read” — check them like any row.` })
        } else {
          setSecondNote({ type: 'success', text: 'The second read found nothing more — the skipped rows are blank.' })
        }
      } else if (sr?.status === 'error' || late) {
        setSecond(null)
        setSecondNote({ type: 'warning', text: 'The second read did not finish. The rows above are the first read; '
          + 'add any missing line by hand.' })
      }
      return r
    },
  })

  // Phase 21d — the layered matcher (exact → learned → fuzzy) decides each
  // consumption row's colour. Only exact / learned arrive with a SAP filled
  // in; a suggestion waits for the store keeper's Accept (ruling Q21-5).
  const rematch = async (rs: OcrRow[]) => {
    if (!isConsumption || !rs.length || !site) return rs
    try {
      const r = await api.post('/ai/ocr/consumption-match', {
        site_id: site, names: rs.map((x) => x.material_text ?? ''),
      })
      const ms = (r.data?.matches ?? []) as NameMatch[]
      return rs.map((x, i) => {
        const m = ms[i]
        if (!m) return x
        return {
          ...x,
          SAP_Code: m.state === 'auto' ? String(m.sap) : '',
          match_state: m.state,
          match_source: m.source,
          learned_count: m.learned_count,
          suggestion: m.state === 'suggested' && m.sap
            ? { SAP_Code: m.sap, description: m.description ?? '', score: m.score,
                in_stock: m.candidates[0]?.in_stock ?? true }
            : null,
          candidates: m.candidates.map((c) => ({
            SAP_Code: c.SAP_Code, score: c.score,
            Equipment_Description: `${c.Equipment_Description}${c.in_stock ? '' : ' — no stock here'}`,
          })),
        }
      })
    } catch {
      return rs
    }
  }

  // Accepting a suggestion, or picking an item by hand, TEACHES the matcher
  // for this site (ruling Q21-3) — the next paper's same word is green.
  const learn = (written: string, sap: string) => {
    if (!isConsumption || !written.trim() || !sap || !site) return
    api.post('/ai/ocr/aliases', { written, SAP_Code: sap, site_id: site }).catch(() => undefined)
  }

  // The paper's date (plausible, or which dates it most likely says) and its
  // work types in the workbook's spelling (PV → PU, RIL → R/L, Blaster → Blast).
  const checkPaper = async (rs: OcrRow[], dt: string | null | undefined, withDate = true) => {
    try {
      const r = await api.post('/ai/ocr/paper-check', {
        date_text: dt || null, work_types: rs.map((x) => x.work_type ?? ''),
        site_id: site, tanks: rs.map((x) => x.tank_no ?? ''),
      })
      const d = r.data.date as PaperDate
      const wts = (r.data.work_types ?? []) as string[]
      const tks = (r.data.tanks ?? []) as TankMatch[]
      if (r.data.shift && withDate) setShift(r.data.shift)
      if (r.data.tank_tags) setTankTags(r.data.tank_tags as string[])
      if (withDate && dt && dt.trim()) {
        setPaperDate(d)
        if (d.plausible && d.date_iso) {
          setDate(dayjs(d.date_iso))
          setDateConfirmed(true)
        } else {
          setDateConfirmed(false)
        }
      }
      return rs.map((x, i) => {
        const t = tks[i]
        return {
          ...x,
          ...(wts[i] != null ? { work_type: wts[i] } : {}),
          ...(t ? { tank_state: t.state, tank_tag: t.state === 'auto' ? t.tag : null,
                    tank_source: t.source, tank_group: t.group ?? t.written,
                    tank_candidates: t.candidates?.length ? t.candidates
                      : (t.tag ? [{ tag: t.tag, cost: 0 }] : []) } : {}),
        }
      })
    } catch {
      return rs
    }
  }

  // the photographed paper becomes the entry's supporting document
  const attachPhoto = async (file: File) => {
    if (!site) return
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('doc_type', isConsumption ? 'consumption' : 'receipt')
      fd.append('site_id', site)
      const r = await api.post<{ id: number; file_name: string }>('/entry/attachments', fd)
      setDocs((d) => [...d, { id: r.data.id, file_name: r.data.file_name }])
    } catch {
      // the store keeper attaches it by hand below
    }
  }

  // Phase 22e (Q22-19): a paper the workbook already holds is COMPARED, never
  // staged again. Runs once the date and the preparer are settled, and again
  // when a row's item, quantity or tank changes.
  const cmpKey = JSON.stringify(rows.map((r) => [r.SAP_Code, r.quantity, r.tank_tag, r.work_type, r.issued_to]))
  useEffect(() => {
    if (!isConsumption || !rows.length || !site || !dateConfirmed) { setCmp(null); return }
    const t = setTimeout(() => {
      api.post('/ai/ocr/compare', {
        site_id: site, date: date.format('YYYY-MM-DD'), prepared_by: preparedBy || null,
        rows: rows.map((r) => ({ SAP_Code: r.SAP_Code || null, quantity: r.quantity,
                                 tank: r.tank_tag || r.tank_no || null, work_type: r.work_type || null,
                                 issued_to: r.issued_to || null })),
      }).then((r) => {
        const c = r.data as CompareRes
        setCmp(c.in_workbook > 0 ? c : null)
      }).catch(() => setCmp(null))
    }, 400)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cmpKey, site, date, dateConfirmed, preparedBy, isConsumption])

  // Accepting a tank teaches its spelling(s) for this site (Q22-17) — for every
  // ticked row at once, ditto marks included (they teach nothing themselves)
  const setTank = (idxs: number[], tag: string) => {
    const written = idxs.map((i) => rows[i]?.tank_group || rows[i]?.tank_no || '').filter(Boolean)
    setRows((rs) => rs.map((r, k) => (idxs.includes(k)
      ? { ...r, tank_tag: tag, tank_state: 'auto', tank_source: 'accepted' } : r)))
    if (site && written.length) {
      api.post('/ai/ocr/tank-alias', { site_id: site, tag, written }).catch(() => undefined)
    }
  }
  // Second-read rows: matched like the first read's (names, tank, work type),
  // then put in at their S.No; a ditto tank or remark takes the row above.
  const addSecondRows = async (added: OcrRow[]) => {
    const fresh = added.map((r) => ({ ...r, _key: `s${String(r.sno)}`, second_read: true }))
    const rs = await checkPaper(await rematch(fresh), null, false)
    setRows((cur) => {
      const merged = insertBySno(cur, rs)
      const idxs = merged.map((r, k) => (r.second_read ? k : -1)).filter((k) => k >= 0)
      const withTank = inheritTankFromAbove(merged, idxs)
      return withTank.map((r, k) => (idxs.includes(k) && k > 0 && !String(r.work_type ?? '').trim()
        ? { ...r, work_type: withTank[k - 1].work_type } : r))
    })
  }

  const { data: pageTanks } = useQuery<PageTanks>({
    queryKey: ['/ai/ocr/page-tanks', site, date.format('YYYY-MM-DD')],
    enabled: isConsumption && !!site && dateConfirmed && rows.length > 0,
    queryFn: async () => (await api.get('/ai/ocr/page-tanks',
      { params: { site_id: site, date: date.format('YYYY-MM-DD') } })).data,
  })
  const choosePageTank = (tag: string) => {
    const base = pageTankUndo ? undoPageTank(rows, pageTankUndo) : rows
    const r = fillPageTank(base, tag)
    setRows(r.rows)
    setPageTank(tag)
    setPageTankUndo(r.undo)
    message.success(`${r.filled.length} row(s) set to ${tag} — ditto, blank or unknown only; `
      + `${r.kept} row(s) with their own tank kept`)
  }
  const clearPageTank = () => {
    if (pageTankUndo) setRows((rs) => undoPageTank(rs, pageTankUndo))
    setPageTank(undefined)
    setPageTankUndo(null)
  }

  const sameTank = (i: number) => {
    const g = rows[i]?.tank_group
    return rows.map((r, k) => (r.tank_group === g ? k : -1)).filter((k) => k >= 0)
  }

  const confirmDate = (iso: string) => {
    setDate(dayjs(iso))
    setDateConfirmed(true)
  }

  const adopt = (result: { rows?: OcrRow[]; items?: OcrRow[]; header?: DnHeader; date_text?: string }) => {
    // Stable per-row keys (rowKey by index is deprecated and reorders badly).
    const fresh = (result.rows ?? result.items ?? []).map((r, i) => ({ ...r, _key: `r${i}` }))
    setRows(fresh)
    setPaperDate(null)
    setDateConfirmed(true)
    setShift(null)
    setPreparedOverride(null)
    setSelected([])
    setCmp(null)
    setPageTank(undefined)
    setPageTankUndo(null)
    setSecond(null)
    setSecondNote(null)
    void (async () => {
      let rs = await rematch(fresh)
      if (isConsumption) rs = await checkPaper(rs, result.date_text)
      setRows(rs)
    })()
    setHeader(result.header ?? null)
    setDateText(result.date_text ?? null)
    setTsv(null)
    setHwSummary(null)
  }

  // Handwritten-form spec pass (docs/features/handwritten-ocr): corrections,
  // ditto marks, qty rules, spec fuzzy match, substitutions and the whole-
  // batch stock simulation — returns flagged rows + the legacy TSV export.
  const [processing, setProcessing] = useState(false)
  const specProcess = async () => {
    setProcessing(true)
    try {
      const r = await api.post('/ai/ocr/handwritten-process', {
        forms: [{ form_id: `form_${Date.now()}`, date_text: dateText, rows }],
      })
      const d = r.data as { rows: HwRow[]; tsv: string; summary: HwSummary }
      const specRows = d.rows.map((p, i) => ({
        _key: `h${i}`,
        material_text: p.product_name_raw ?? '',
        quantity: p.qty ?? 0,
        uom: '',
        issued_to: p.received_by ?? '',
        work_type: p.work_type ?? '',
        tank_no: p.tank_no ?? '',
        SAP_Code: p.sap_code ?? '',
        match_state: p.sap_code ? 'auto' : (p.candidates?.length ? 'pick' : 'unknown'),
        candidates: (p.candidates ?? []).map((c) => ({
          SAP_Code: c.SAP_Code, Equipment_Description: c.Equipment_Description,
          score: (c.confidence ?? 0) / 100,
        })),
        markers: p.markers, blocked: p.blocked,
      } as OcrRow))
      // the spec pass keeps the remark as written (its TSV is the spec); the
      // grid shows and stages it in the workbook's spelling
      setRows(await checkPaper(await rematch(specRows), null, false))
      // a checked paper date (or the one the store keeper chose) wins
      if (d.rows[0]?.date_iso && !paperDate) setDate(dayjs(d.rows[0].date_iso))
      setTsv(d.tsv ?? '')
      setHwSummary(d.summary)
      message.success('Validated against the handwritten-form spec')
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setProcessing(false)
    }
  }

  const downloadTsv = () => {
    const blob = new Blob([tsv ?? ''], { type: 'text/tab-separated-values' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `consumption_${date.format('YYYY-MM-DD')}.tsv`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  const patch = (i: number, p: Partial<OcrRow>) =>
    setRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, ...p } : r)))

  const doPaste = async () => {
    try {
      const r = await api.post(`/ai/paste/${kind}`, { text: pasteText })
      adopt(r.data)
      message.success('Parsed — review the rows below')
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  // Stage every row that has a SAP code and a positive qty, through the
  // EXISTING staging services (audited; land as drafts for HOD approval).
  const stage = async () => {
    if (!site) return
    const ready = rows.filter((r) => r.SAP_Code && Number(r.quantity) > 0)
    setStaging(true)
    let ok = 0
    const failed: string[] = []
    for (const r of ready) {
      try {
        if (isConsumption) {
          await api.post('/entry/consumption', {
            Date: date.format('YYYY-MM-DD'), SAP_Code: r.SAP_Code,
            Quantity: Number(r.quantity), Site_ID: site,
            Issued_To: r.issued_to || null, Work_Type: r.work_type || null,
            Tank_No: r.tank_tag || null, Prepared_By: preparedBy || null,
            Remarks: 'OCR import', wbs: wbs || null,
            attachment_ids: docs.map((d) => d.id),
          })
        } else {
          await api.post('/entry/receipts', {
            Date: date.format('YYYY-MM-DD'), SAP_Code: r.SAP_Code,
            Quantity: Number(r.quantity), Site_ID: site,
            Supplier: header?.Mob_From || null,
            Remarks: `OCR DN ${header?.DN_No || ''}`.trim(), wbs: wbs || null,
            attachment_ids: docs.map((d) => d.id),
          })
        }
        ok += 1
      } catch (e) {
        failed.push(`${r.material_text}: ${errMsg(e)}`)
      }
    }
    setStaging(false)
    if (ok) message.success(`${ok} row(s) staged for HOD approval`)
    if (failed.length) message.warning(`${failed.length} row(s) failed — ${failed[0]}`)
    setRows((rs) => rs.filter((r) => !(r.SAP_Code && Number(r.quantity) > 0) || failed.length > 0))
    if (!failed.length && ok) { setHeader(null); setDocs([]) }
  }

  const invOptions = (inventory.data?.items ?? []).map((r: ApiRow) => ({
    value: String(r.SAP_Code), label: `${r.SAP_Code} — ${r.Equipment_Description ?? ''}`,
  }))

  const columns: ColumnsType<OcrRow> = [
    ...(cmp ? [{
      // Phase 22e — this paper is already in the workbook: line by line (Q22-19)
      title: 'Workbook', key: 'wb', width: 170,
      render: (_: unknown, r: OcrRow) => (
        <Space direction="vertical" size={0} data-testid="ocr-wb">
          <Tag color={r.wb_status === 'same' ? 'green' : r.wb_status === 'differs' ? 'gold' : 'red'} style={{ margin: 0 }}>
            {r.wb_status === 'same' ? `✓ row ${r.wb_row ?? ''}` : r.wb_status === 'differs' ? `≠ row ${r.wb_row ?? ''}` : 'not in the workbook'}</Tag>
          {Object.entries(r.wb_diffs ?? {}).map(([k, [paper, book]]) => (
            <Typography.Text key={k} type="secondary" style={{ fontSize: 11 }}>
              {k.replace('_', ' ')}: paper {String(paper ?? '—')} · workbook {String(book ?? '—')}</Typography.Text>
          ))}
        </Space>
      ),
    }] : []),
    { title: 'Match', dataIndex: 'match_state', width: 90,
      render: (v: OcrRow['match_state'], r) => (
        <Space size={4}>
          <Tag color={r.blocked ? 'red' : MATCH_COLOR[v]} data-testid={`ocr-state-${v}`}>
            {r.blocked ? 'blocked' : r.match_source === 'learned'
              ? `learned${r.learned_count ? ` ×${r.learned_count}` : ''}` : MATCH_LABEL[v]}</Tag>
          {(r.markers ?? []).map((m) => <span key={m}>{m}</span>)}
          {r.second_read && <Tag color="purple" data-testid="ocr-second-read">2nd read</Tag>}
        </Space>
      ) },
    { title: 'As written', dataIndex: 'material_text', ellipsis: true },
    {
      title: 'Material (SAP)', key: 'sap', width: 320,
      render: (_: unknown, r, i) => (
        <Space direction="vertical" size={2} data-ocr-unresolved={!r.SAP_Code ? 'yes' : undefined}>
          {!r.SAP_Code && r.match_state === 'suggested' && r.suggestion && (
            <span data-testid="ocr-suggestion" style={{ fontSize: 12 }}>
              Paper says “{r.material_text}” — did you mean <b>{r.suggestion.description}</b>{' '}
              ({Math.round(r.suggestion.score * 100)}%{r.suggestion.in_stock ? '' : ', no stock here'})?{' '}
              <Button size="small" type="primary" data-testid="ocr-accept"
                onClick={() => {
                  patch(i, { SAP_Code: r.suggestion!.SAP_Code, match_state: 'auto', match_source: 'accepted' })
                  learn(r.material_text, r.suggestion!.SAP_Code)
                }}>Accept</Button>
            </span>
          )}
          {!r.SAP_Code && r.match_state === 'unknown' && isConsumption && (
            <span data-testid="ocr-notfound" style={{ fontSize: 12, color: status.critical }}>
              “{r.material_text || '(blank)'}” is not in stock — choose the item, or type its SAP.
            </span>
          )}
        <Select showSearch size="small" style={{ width: 300 }} optionFilterProp="label"
          value={r.SAP_Code || undefined} placeholder={r.match_state === 'unknown' ? 'Choose the item' : 'Pick material'}
          status={!r.SAP_Code && isConsumption ? (r.match_state === 'unknown' ? 'error' : 'warning') : undefined}
          onChange={(v) => {
            patch(i, { SAP_Code: v, match_state: isConsumption ? 'auto' : (r.match_state === 'unknown' ? 'pick' : r.match_state),
                       match_source: isConsumption ? 'chosen' : r.match_source })
            learn(r.material_text, v)
          }}
          options={[
            ...r.candidates.map((c) => ({
              value: c.SAP_Code,
              label: `★ ${c.SAP_Code} — ${c.Equipment_Description} (${Math.round(c.score * 100)}%)`,
            })),
            ...invOptions.filter((o: { value: string }) => !r.candidates.some((c) => c.SAP_Code === o.value)),
          ]} />
        </Space>
      ),
    },
    {
      title: 'Qty', key: 'q', width: 110,
      render: (_: unknown, r, i) => (
        <InputNumber size="small" min={0} value={r.quantity}
          placeholder="—" status={r.quantity == null ? 'warning' : undefined}
          onChange={(v) => patch(i, { quantity: v })} style={{ width: 90 }} />
      ),
    },
    { title: 'UOM', dataIndex: 'uom', width: 70, render: (v) => v || '—' },
    ...(isConsumption
      ? [{
          // the paper's Name column — the workbook's "Received by" (Q21-6)
          title: 'Received by (name)', key: 'it', width: 160,
          render: (_: unknown, r: OcrRow, i: number) => (
            <Input size="small" value={r.issued_to}
              onChange={(e) => patch(i, { issued_to: e.target.value })} />
          ),
        }, {
          // the paper's Remarks — the workbook's Work Type, in its spelling
          // (PV → PU, RIL → R/L, Blaster → Blast); staged with the row
          title: 'Work type', key: 'wt', width: 110,
          render: (_: unknown, r: OcrRow, i: number) => (
            <Input size="small" value={r.work_type} data-testid="ocr-work-type"
              onChange={(e) => patch(i, { work_type: e.target.value })} />
          ),
        }, {
          // Phase 22e — the tank, green / gold / red like the names (Q22-17)
          title: 'Tank', key: 'tank', width: 260,
          render: (_: unknown, r: OcrRow, i: number) => (
            <Space direction="vertical" size={2} data-testid="ocr-tank">
              <Space size={4} wrap>
                <Tag color={r.tank_tag ? 'green' : r.tank_state === 'suggested' ? 'gold' : r.tank_no || r.tank_group ? 'red' : 'default'}
                  style={{ margin: 0 }}>{r.tank_tag ? (r.tank_source === 'learned' ? 'learned' : 'matched')
                    : r.tank_state === 'suggested' ? 'check' : r.tank_no || r.tank_group ? 'not found' : 'none'}</Tag>
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  {r.tank_group && r.tank_group !== r.tank_no ? `as above (“${r.tank_group}”)` : r.tank_no ? `“${r.tank_no}”` : ''}</Typography.Text>
                {/* a tie (a bare TNK-091 is a tank in BOTH trains) is never accepted
                    in one click — the store keeper chooses below */}
                {!r.tank_tag && r.tank_state === 'suggested' && r.tank_candidates?.[0]
                  && !(r.tank_candidates.length > 1 && r.tank_candidates[0].cost === r.tank_candidates[1].cost) && (
                  <Button size="small" type="primary" data-testid="ocr-tank-accept"
                    onClick={() => setTank(sameTank(i), r.tank_candidates![0].tag)}>
                    Accept {r.tank_candidates[0].tag}</Button>
                )}
                <Button size="small" type="link" style={{ padding: 0 }} data-testid="ocr-tank-same"
                  onClick={() => setSelected(sameTank(i).map((k) => String(rows[k]._key)))}>tick all like this</Button>
              </Space>
              <Select size="small" style={{ width: 240 }} showSearch value={r.tank_tag || undefined}
                placeholder="Choose the tank" onChange={(v) => setTank([i], v)}
                options={[...(r.tank_candidates ?? []).map((c) => ({ value: c.tag, label: `★ ${c.tag}` })),
                  ...tankTags.filter((t) => !(r.tank_candidates ?? []).some((c) => c.tag === t))
                    .map((t) => ({ value: t, label: t }))]} />
            </Space>
          ),
        }]
      : []),
    {
      title: '', key: 'x', width: 50,
      render: (_: unknown, __, i) => (
        <Button size="small" type="text" icon={<DeleteOutlined />}
          onClick={() => setRows((rs) => rs.filter((_r, idx) => idx !== i))} />
      ),
    },
  ]

  const rowsView = useMemo(() => (cmp
    ? rows.map((r, i) => {
        const ln = cmp.lines.find((x) => x.index === i)
        return ln ? { ...r, wb_status: ln.status, wb_row: ln.row, wb_diffs: ln.diffs } : r
      })
    : rows), [rows, cmp])
  const inWorkbook = !!cmp && cmp.in_workbook > 0

  const readyCount = useMemo(
    () => rows.filter((r) => r.SAP_Code && Number(r.quantity) > 0).length, [rows])
  // Phase 21d: a consumption row with no material yet (gold to accept, red to
  // choose) holds the whole sheet back — or remove it. Nothing is skipped
  // silently.
  const unresolved = useMemo(
    () => (isConsumption ? rows.filter((r) => !r.SAP_Code).length : 0), [rows, isConsumption])
  const needDocs = docsRequired !== false && docs.length === 0
  const needWbs = !!wbsOptions?.length && !wbs
  const nextUnresolved = () => {
    const el = document.querySelector('[data-ocr-unresolved="yes"]')
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    ;(el?.querySelector('input, button') as HTMLElement | null)?.focus()
  }
  const polling = jobId != null && (job.data == null
    || job.data.status === 'queued' || job.data.status === 'running')

  /**
   * Re-run an interrupted read. The server re-queues in place while it still
   * holds the prepared image; otherwise the browser re-sends the file it kept,
   * so a store keeper never has to fetch the paper back to the desk.
   */
  const retry = async () => {
    setRetrying(true)
    try {
      if (job.data?.can_requeue && jobId != null) {
        await api.post(`/ai/jobs/${jobId}/requeue`)
        await job.refetch()
      } else if (lastFile) {
        const fd = new FormData()
        fd.append('file', lastFile)
        setJobId(null)
        const r = await api.post('/ai/jobs', fd, { params: { kind } })
        setJobId(r.data.job_id)
      } else {
        message.info('Photograph the page again — the original is no longer held.')
      }
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setRetrying(false)
    }
  }

  return (
    <div>
      <PracticeNotice kind="ocr" />
      <Typography.Title level={3} style={{ marginTop: 0 }}>📷 OCR Import</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Photograph a handwritten consumption list or a printed delivery note — the
        local AI reads it into rows you review, then stage for HOD approval. The
        Paste tab works even when the AI is offline.
      </Typography.Paragraph>

      <Space style={{ marginBottom: 16 }} wrap>
        <Radio.Group value={kind} buttonStyle="solid"
          onChange={(e) => {
            setKind(e.target.value); setRows([]); setHeader(null); setPaperDate(null); setDateConfirmed(true)
          }}
          options={[
            { value: 'ocr_consumption', label: '📝 Consumption log' },
            { value: 'ocr_delivery_note', label: '🚚 Delivery note' },
          ]} optionType="button" />
      </Space>

      <Space align="start" wrap style={{ marginBottom: 16 }}>
        <Card size="small" title={<><CameraOutlined /> Photo</>} style={{ width: 420 }}>
          {aiHealth && !aiHealth.ok && (
            <Alert type="warning" showIcon style={{ marginBottom: 8 }}
              title="Local AI is offline — use the Paste lane meanwhile." />
          )}
          {polling ? (
            <OcrJobProgress job={job.data as OcrJobStatus | undefined}
              what={isConsumption ? 'a handwritten consumption sheet' : 'a delivery note'}
              onRetry={retry} retrying={retrying} />
          ) : (
            <Upload.Dragger accept="image/*" maxCount={1} showUploadList={false}
              disabled={Boolean(aiHealth && !aiHealth.ok)}
              customRequest={async ({ file, onSuccess, onError }) => {
                const fd = new FormData()
                fd.append('file', file as Blob)
                setLastFile(file as File)
                try {
                  const r = await api.post('/ai/jobs', fd, { params: { kind } })
                  setJobId(r.data.job_id)
                  onSuccess?.(r.data)
                } catch (e) {
                  message.error(errMsg(e))
                  onError?.(e as Error)
                }
              }}>
              <p className="ant-upload-drag-icon"><InboxOutlined /></p>
              <p className="ant-upload-text">Drop / take a photo</p>
              <p className="ant-upload-hint">JPEG · PNG · WebP · HEIC (iPhone) — auto-rotated + downscaled</p>
            </Upload.Dragger>
          )}
        </Card>

        <Card size="small" title="📋 Paste (offline)" style={{ width: 420 }}>
          <Input.TextArea rows={6} value={pasteText} onChange={(e) => setPasteText(e.target.value)}
            placeholder={isConsumption
              ? 'Date: 01/10/26\nImran\t6m pipe\tNos\t45\tsite work\nAli, double clamp, PCS, 12'
              : 'DN_No: 15668\nMob_From: GI - ABU HADRIYAH\n6m pipe, Nos, 45'} />
          <Button style={{ marginTop: 8 }} onClick={doPaste} disabled={!pasteText.trim()}>
            Parse
          </Button>
        </Card>
      </Space>

      {header && (
        <Descriptions size="small" bordered column={4} style={{ marginBottom: 16 }}
          items={[
            { key: '1', label: 'DN No', children: header.DN_No || '—' },
            { key: '2', label: 'Date', children: header.Date || '—' },
            { key: '3', label: 'From', children: header.Mob_From || '—' },
            { key: '4', label: 'To', children: header.Mob_To || '—' },
            { key: '5', label: 'Driver', children: header.Driver_Name || '—' },
            { key: '6', label: 'Vehicle', children: header.Vehicle_No || '—' },
            { key: '7', label: 'Prepared by', children: header.Prepared_by || '—' },
          ]} />
      )}

      {hwSummary && (
        <Alert type={hwSummary.blocked ? 'warning' : 'success'} showIcon
          style={{ marginBottom: 12 }}
          title={`Spec check: ${hwSummary.auto_matched}/${hwSummary.total_rows} auto-matched · `
            + `${hwSummary.needs_review} need review · ${hwSummary.substituted} substituted · `
            + `${hwSummary.blocked} blocked · ${hwSummary.struck_through_excluded} struck-through excluded`} />
      )}

      {rows.length > 0 && isConsumption && paperDate && !paperDate.plausible && !dateConfirmed && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }} data-testid="ocr-date-check"
          title={`The paper's date reads “${paperDate.read}”${paperDate.days_from_today != null
            ? ` — ${Math.abs(paperDate.days_from_today)} days ${paperDate.days_from_today < 0 ? 'ago' : 'ahead'}`
            : ' — not a real date'}. Which day is this sheet?`}
          description={
            <Space wrap size={8}>
              {paperDate.candidates.length > 0 && <span>Did you mean</span>}
              {paperDate.candidates.map((c, k) => (
                // gold for the likeliest only (docs/DESIGN_SYSTEM.md: gold once per view)
                <Button key={c.date_iso} size="small" type={k === 0 ? 'primary' : 'default'}
                  data-testid="ocr-date-candidate"
                  onClick={() => confirmDate(c.date_iso)}>
                  {c.label}
                </Button>
              ))}
              {paperDate.date_iso && (
                <Button size="small" data-testid="ocr-date-keep" onClick={() => confirmDate(paperDate.date_iso!)}>
                  Keep {paperDate.read.replace(/\s*\(.*\)\s*$/, '')}
                </Button>
              )}
              <span>or pick the date below.</span>
            </Space>
          } />
      )}
      {rows.length > 0 && isConsumption && paperDate?.plausible && (
        <Typography.Paragraph type="secondary" data-testid="ocr-paper-date-ok" style={{ marginBottom: 8 }}>
          Paper date “{paperDate.read}” → {date.format('DD MMM YYYY')}
        </Typography.Paragraph>
      )}
      {rows.length > 0 && (
        <>
          {isConsumption && (
            <Space wrap style={{ marginBottom: 8 }} data-testid="ocr-shift">
              {shift && (
                <Tag color={shift.shift === 'Night' ? 'blue' : 'gold'} style={{ cursor: 'pointer' }}
                  data-testid="ocr-shift-tag"
                  onClick={() => { setShift({ shift: shift.shift === 'Night' ? 'Day' : 'Night', marked: false }); setPreparedOverride(null) }}>
                  {shift.shift === 'Night' ? 'Night shift' : shift.marked ? 'Day shift' : 'Day shift (no mark)'} · click to switch
                </Tag>
              )}
              <span>Prepared by</span>
              <Input size="small" style={{ width: 180 }} value={preparedBy} data-testid="ocr-prepared-by"
                placeholder="set the site's names (Admin → Sites)"
                onChange={(e) => setPreparedOverride(e.target.value)} />
            </Space>
          )}
          {inWorkbook && cmp && (
            <Alert type="info" showIcon style={{ marginBottom: 12 }} data-testid="ocr-compare"
              title={`This paper is already in the workbook — ${cmp.in_workbook} row(s) for ${date.format('DD MMM YYYY')}`
                + `${preparedBy ? `, prepared by ${preparedBy}` : ''}. It is COMPARED, not staged (staging it too would count the stock twice).`}
              description={<Space wrap size={6}>
                <Tag color="green">{cmp.counts.same ?? 0} same</Tag>
                <Tag color="gold">{cmp.counts.differs ?? 0} differ</Tag>
                <Tag color="red">{cmp.counts.missing ?? 0} not in the workbook</Tag>
                <Tag>{cmp.counts.extra ?? 0} workbook row(s) not on this page</Tag>
                <span>Fix a difference in the workbook; the next pull brings it in.</span>
              </Space>} />
          )}
          {secondNote && (
            <Alert type={secondNote.type} showIcon style={{ marginBottom: 8 }} data-testid="ocr-second-note"
              title={secondNote.text} closable onClose={() => setSecondNote(null)} />
          )}
          {isConsumption && rows.length > 0 && (
            <Space wrap style={{ marginBottom: 8 }} data-testid="ocr-page-tank">
              <span>Tank for this whole page</span>
              <Select size="small" style={{ width: 260 }} showSearch allowClear value={pageTank}
                placeholder={pageTanks ? 'Choose — fills ditto, blank and unknown rows' : 'Settle the date first'}
                disabled={!pageTanks} onChange={(v) => (v ? choosePageTank(v) : clearPageTank())}
                data-testid="ocr-page-tank-pick"
                options={pageTanks ? [
                  { label: `Used ${dayjs(pageTanks.from).format('D MMM')} – ${dayjs(pageTanks.to).format('D MMM')}`,
                    options: pageTanks.recent.map((t) => ({ value: t.tag, label: `${t.tag} · ${t.lines} line(s)` })) },
                  ...(pageTanks.learned.length ? [{ label: 'Other tanks at this site',
                    options: pageTanks.learned.map((t) => ({ value: t, label: t })) }] : []),
                ] : []} />
              {pageTank && (
                <Button size="small" onClick={clearPageTank} data-testid="ocr-page-tank-undo">Undo</Button>
              )}
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                A tank written and matched on its own row, or one you chose, is never changed.
              </Typography.Text>
            </Space>
          )}
          {isConsumption && selected.length > 0 && (
            <Space wrap style={{ marginBottom: 8 }} data-testid="ocr-bulk-tank">
              <span>{selected.length} row(s) ticked — set their tank:</span>
              <Select size="small" style={{ width: 240 }} showSearch value={bulkTank} onChange={setBulkTank}
                placeholder="Tank" options={tankTags.map((t) => ({ value: t, label: t }))} data-testid="ocr-bulk-tank-pick" />
              <Button size="small" type="primary" disabled={!bulkTank} data-testid="ocr-bulk-tank-apply"
                onClick={() => {
                  const idxs = rows.map((r, k) => (selected.includes(String(r._key)) ? k : -1)).filter((k) => k >= 0)
                  setTank(idxs, bulkTank!)
                  setSelected([])
                }}>Apply to {selected.length}</Button>
              <Button size="small" onClick={() => setSelected([])}>Clear</Button>
            </Space>
          )}
          <Table sticky={{ offsetHeader: 64 }} size="small" columns={columns} dataSource={rowsView}
            rowKey={(r) => String(r._key)} pagination={false} scroll={{ x: 'max-content' }}
            rowSelection={isConsumption ? { selectedRowKeys: selected, onChange: setSelected, columnWidth: 36 } : undefined} />
          {inWorkbook && !!cmp?.extra.length && (
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8 }} data-testid="ocr-compare-extra">
              In the workbook for this date and preparer but not on this page (often another page of the same shift):{' '}
              {cmp.extra.slice(0, 40).map((x) => `row ${x.row ?? '—'} SAP ${x.SAP_Code} × ${x.quantity ?? '—'}`).join(' · ')}
            </Typography.Paragraph>
          )}
          <div style={{ marginTop: 16 }}>
            <EntryDocsUpload docType={isConsumption ? 'consumption' : 'receipt'} siteId={site}
              value={docs} onChange={setDocs} required={docsRequired !== false} />
          </div>
          <Space style={{ marginTop: 4 }} wrap>
            {isConsumption && (
              <Button icon={<SafetyCertificateOutlined />} onClick={specProcess}
                loading={processing}>
                Validate (handwritten spec)
              </Button>
            )}
            {tsv != null && (
              <Button icon={<DownloadOutlined />} onClick={downloadTsv}>
                TSV export
              </Button>
            )}
            <DatePicker value={date} allowClear={false} data-testid="ocr-date"
              status={isConsumption && !dateConfirmed ? 'warning' : undefined}
              onChange={(d) => { if (d) { setDate(d); setDateConfirmed(true) } }} />
            {!!wbsOptions?.length && (
              <Select placeholder="WBS Number" style={{ width: 160 }} value={wbs} onChange={setWbs}
                data-testid="ocr-wbs" status={needWbs ? 'warning' : undefined}
                options={wbsOptions.map((w) => ({ value: w, label: w }))} />
            )}
            {isAdmin ? (
              <Select placeholder="Site" style={{ width: 150 }} value={site} onChange={setSite}
                options={(sites ?? []).map((s) => ({ value: s, label: s }))} />
            ) : (
              <Tag>{site}</Tag>
            )}
            {unresolved > 0 && (
              <Button data-testid="ocr-next-unresolved" onClick={nextUnresolved}>
                Next to check ({unresolved})
              </Button>
            )}
            <Popconfirm title={`Stage ${readyCount} row(s) as ${isConsumption ? 'consumption' : 'receipt'} drafts?`}
              onConfirm={stage}>
              <Button type="primary" loading={staging} data-testid="ocr-stage"
                disabled={readyCount === 0 || !site || unresolved > 0 || (isConsumption && !dateConfirmed)
                  || needDocs || needWbs || inWorkbook}>
                {inWorkbook ? 'Already in the workbook — compared, not staged'
                  : unresolved > 0 ? `Resolve ${unresolved} row(s) first`
                  : isConsumption && !dateConfirmed ? "Confirm the paper's date first"
                    : needDocs ? 'Attach the paper first'
                      : needWbs ? 'Choose the WBS first'
                        : `Stage ${readyCount} row(s) for HOD approval`}
              </Button>
            </Popconfirm>
            <Button onClick={() => { setRows([]); setHeader(null); setPaperDate(null); setDateConfirmed(true); setCmp(null); setShift(null); setSelected([]); setPageTank(undefined); setPageTankUndo(null); setSecond(null); setSecondNote(null) }}>
              Discard
            </Button>
          </Space>
        </>
      )}
    </div>
  )
}
