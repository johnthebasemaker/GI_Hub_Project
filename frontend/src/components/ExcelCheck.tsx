import { useCallback, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import {
  Alert, App, Button, Collapse, Drawer, Empty, Select, Space, Tag, Tooltip, Typography, Upload,
} from 'antd'
import { DownloadOutlined, FileExcelOutlined, ReloadOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useSites } from '../api/hooks'

/**
 * GI Hub stock vs the Excel workbook's "Current Stock" — which materials differ
 * and WHY (backend/api/services/stock_excel.py).
 *
 * ⚠️ IT EXPLAINS, IT DOES NOT FIX. Every difference is broken into causes a
 * person can act on — "a receipt entered in GI Hub that the Receipt Log never
 * got", "a line the Consumption Log no longer has" — each with where to look
 * and how to fix it. Nothing here moves stock. The marked workbook is a COPY:
 * red rows, a note on each Current Stock cell, and a "GI Hub check" sheet.
 */
export interface Cause {
  code: string; effect: number; what: string; fix: string
  where: { sheet?: string; row?: number | null; ledger?: string; id?: number }
}
export interface Mismatch {
  sap: string; description: string; uom: string; excel_row: number
  workbook: number; app: number | null; difference: number | null
  unexplained: number; causes: Cause[]
}
interface Check {
  id: number; Site_ID: string; workbook: string; checked_by: string
  checked_at: string | null; total_saps: number; matched: number; items: Mismatch[]
}

const WRITERS = new Set(['admin', 'hod', 'logistics', 'store_keeper', 'warehouse_user'])
const KEY = ['/stock/excel-check']
const CODE: Record<string, { label: string; color: string }> = {
  app_only: { label: 'Only in GI Hub', color: 'orange' },
  vanished: { label: 'No longer in Excel', color: 'red' },
  not_synced: { label: 'Not synced yet', color: 'blue' },
  qty_edited: { label: 'Edited in Excel', color: 'gold' },
  sheet_totals: { label: 'Sheet totals stale', color: 'purple' },
  opening: { label: 'Opening stock', color: 'cyan' },
  missing: { label: 'Missing in GI Hub', color: 'red' },
  unexplained: { label: 'Unexplained', color: 'magenta' },
}

const n = (v: number | null | undefined) =>
  v == null ? '—' : String(Math.round(v * 10000) / 10000)

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } } }
  return x?.response?.data?.detail ?? 'Something went wrong'
}

export function useExcelCheck() {
  return useQuery({
    queryKey: KEY,
    queryFn: async () => (await api.get<{ check: Check | null }>('/stock/excel-check')).data.check,
    staleTime: 5 * 60_000,
  })
}

/** SAP → its mismatch, for highlighting rows elsewhere. */
export function useMismatchMap(): Map<string, Mismatch> {
  const { data } = useExcelCheck()
  return useMemo(() => new Map((data?.items ?? []).map((m) => [m.sap, m])), [data])
}

/** The red "≠ Excel" tag, with the causes on hover. */
export function MismatchTag({ m }: { m: Mismatch }) {
  return (
    <Tooltip title={<div>
      <div>Excel {n(m.workbook)} · GI Hub {n(m.app)}</div>
      {m.causes.slice(0, 4).map((c, i) => <div key={i}>• {c.what}</div>)}
    </div>}>
      <Tag color="red" style={{ marginInlineStart: 6 }} data-testid="excel-mismatch-tag">≠ Excel</Tag>
    </Tooltip>
  )
}

function whereText(w: Cause['where']): string | null {
  if (w.sheet && w.row) return `${w.sheet}, row ${w.row}`
  if (w.ledger && w.id) return `GI Hub ${w.ledger} #${w.id}`
  return null
}

function MismatchList({ items }: { items: Mismatch[] }) {
  if (!items.length) return <Empty description="Every material matches the workbook" />
  return (
    <Collapse
      size="small"
      items={items.map((m) => ({
        key: m.sap,
        label: (
          <Space wrap>
            <strong>{m.sap}</strong>
            <span>{m.description}</span>
            <Tag>Excel {n(m.workbook)}</Tag>
            <Tag>GI Hub {n(m.app)}</Tag>
            <Tag color={m.difference && m.difference > 0 ? 'orange' : 'red'}>
              {m.difference != null && m.difference > 0 ? '+' : ''}{n(m.difference)} {m.uom}
            </Tag>
          </Space>
        ),
        children: (
          <Space direction="vertical" style={{ width: '100%' }} size={10}>
            {m.causes.map((c, i) => (
              <div key={i}>
                <Space wrap size={4} style={{ marginBottom: 2 }}>
                  <Tag color={CODE[c.code]?.color}>{CODE[c.code]?.label ?? c.code}</Tag>
                  {whereText(c.where) && <Tag icon={<FileExcelOutlined />}>{whereText(c.where)}</Tag>}
                </Space>
                <Typography.Paragraph style={{ margin: 0 }}>{c.what}</Typography.Paragraph>
                <Typography.Text type="secondary">How to fix: {c.fix}</Typography.Text>
              </div>
            ))}
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Inventory sheet row {m.excel_row}.
            </Typography.Text>
          </Space>
        ),
      }))}
    />
  )
}

/** Upload the workbook: check it (and store), or get the marked copy back. */
function useUploads(onDone: () => void, siteId?: string) {
  const { message } = App.useApp()
  // Phase 23a — the check is STORED against a site. A site-bound user gets
  // their own; a global role (admin, logistics) must name one, or the server
  // answers 422 "site_id is required for a global role".
  const form = (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    if (siteId) fd.append('site_id', siteId)
    return fd
  }
  const check = useMutation({
    mutationFn: async (file: File) => {
      const fd = form(file)
      return (await api.post('/stock/excel-check', fd)).data
    },
    onSuccess: () => { message.success('Checked — the list is up to date'); onDone() },
    onError: (e) => message.error(errMsg(e)),
  })
  const marked = useMutation({
    mutationFn: async (file: File) => {
      const fd = form(file)
      const r = await api.post('/stock/excel-check/marked', fd, { responseType: 'blob' })
      return { blob: r.data as Blob, name: file.name.replace(/\.xlsx$/i, '') }
    },
    onSuccess: ({ blob, name }) => {
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${name} - GI Hub check.xlsx`
      a.click()
      URL.revokeObjectURL(url)
      message.success('Marked copy downloaded — make the fixes in your own workbook')
      onDone()
    },
    onError: (e) => message.error(errMsg(e)),
  })
  return { check, marked }
}

/** Banner + review drawer for the Stock page. */
export function ExcelCheckPanel({ extra }: { extra?: ReactNode }) {
  const { user } = useAuth()
  const qc = useQueryClient()
  const { data: check, isLoading } = useExcelCheck()
  const [open, setOpen] = useState(false)
  const refresh = useCallback(() => { void qc.invalidateQueries({ queryKey: KEY }) }, [qc])
  const isGlobal = (user?.level ?? 0) >= 3 || !user?.site_id
  const { data: sites } = useSites()
  const [pickedSite, setPickedSite] = useState<string | undefined>()
  const site = isGlobal ? (pickedSite ?? check?.Site_ID ?? sites?.[0]) : undefined
  const { check: runCheck, marked } = useUploads(refresh, site)
  const canUpload = WRITERS.has(user?.role ?? '')
  if (isLoading) return null
  const items = check?.items ?? []
  const when = check?.checked_at ? check.checked_at.slice(0, 16).replace('T', ' ') : ''

  const uploads = canUpload && (
    <Space wrap>
      {isGlobal && (
        <Select size="middle" style={{ width: 140 }} value={site} onChange={setPickedSite}
          aria-label="Site to check" data-testid="excel-check-site"
          options={(sites ?? []).map((x) => ({ value: x, label: x }))} />
      )}
      <Upload accept=".xlsx" showUploadList={false} disabled={isGlobal && !site}
        beforeUpload={(f) => { runCheck.mutate(f); return false }}>
        <Button icon={<ReloadOutlined />} disabled={runCheck.isPending || (isGlobal && !site)}>
          Check again (upload workbook)</Button>
      </Upload>
      <Upload accept=".xlsx" showUploadList={false} disabled={isGlobal && !site}
        beforeUpload={(f) => { marked.mutate(f); return false }}>
        <Button type="primary" icon={<DownloadOutlined />}
          disabled={marked.isPending || (isGlobal && !site)}>
          Get the marked workbook
        </Button>
      </Upload>
    </Space>
  )

  return (
    <>
      {!check ? (
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          title="GI Hub has not been checked against the Excel workbook yet"
          description={canUpload ? <Space direction="vertical">
            <span>Upload CNCEC_Inventory.xlsx to see which materials differ, and why.</span>{uploads}</Space> : undefined} />
      ) : items.length ? (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }} data-testid="excel-check-banner"
          title={`${items.length} of ${check.total_saps} materials don't match the Excel workbook`}
          description={<>
            Each one is marked <Tag color="red" style={{ marginInline: 2 }}>≠ Excel</Tag> below, with
            the reason. Checked {when} from {check.workbook || 'the workbook'}. Nothing is changed
            automatically — fix the workbook or the entry, then sync again.
          </>}
          action={<Space direction="vertical">
            <Button onClick={() => setOpen(true)}>Review the differences</Button>{extra}</Space>} />
      ) : (
        <Alert type="success" showIcon style={{ marginBottom: 12 }}
          title={`All ${check.total_saps} materials match the Excel workbook`}
          description={`Checked ${when} from ${check.workbook || 'the workbook'}.`}
          action={canUpload ? <Button size="small" onClick={() => setOpen(true)}>Check again</Button> : undefined} />
      )}
      <Drawer open={open} onClose={() => setOpen(false)} size="large"
        title={`GI Hub vs the Excel workbook — ${check?.matched ?? 0} of ${check?.total_saps ?? 0} match`}>
        <Typography.Paragraph type="secondary">
          Each difference is broken into what caused it and how to fix it. <b>Only in GI Hub</b>:
          entered here but never added to the workbook's log — add it to the log, or reverse it here
          if it was a test. <b>No longer in Excel</b>: a line GI Hub took from the workbook that the
          workbook has since lost. The marked workbook colours these rows red and adds a
          “GI Hub check” sheet; it is a copy — your file is never changed.
        </Typography.Paragraph>
        {uploads && <div style={{ marginBottom: 12 }}>{uploads}</div>}
        <MismatchList items={items} />
      </Drawer>
    </>
  )
}
