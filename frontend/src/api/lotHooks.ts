/**
 * Phase 16c — lots, expiry and FEFO. Kept OUT of `hooks.ts` on purpose: that
 * module is on the critical path (zero-growth build check), and only the lazy
 * Issue / Lots pages need these.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from './client'
import type { Row } from './client'

export interface LotOption {
  lot: string
  expiry: string | null
  expiry_source: string | null
  mfd: string | null
  remaining: number
  status: string
  days_left: number | null
  fefo: boolean
}
export interface LotOptions {
  mode: 'lot' | 'roll' | null
  items: LotOption[]
  units: { unit: string; lot: string; location: string | null }[]
  fefo: string | null
}
/** The open lots of ONE material in FEFO order — the Issue form's picker. */
export function useLotOptions(sap?: string, site?: string) {
  return useQuery<LotOptions>({
    queryKey: ['/lot-register/options', sap, site],
    enabled: !!sap && !!site,
    staleTime: 30_000,
    queryFn: async () => (await api.get<LotOptions>('/lot-register/options',
      { params: { sap_code: sap, site_id: site } })).data,
  })
}
export interface LotRow extends Row {
  Lot_Number: string
  SAP_Code: string
  status: string
  days_left: number | null
  /** Phase 22c — a certificate is on file for this lot (from Drive or uploaded) */
  Has_MTC?: boolean
  MTC_Drive_File?: number | null
}
/** Phase 21a — one workbook row naming a bad lot, with where to fix it. */
export interface LotProblem extends Row {
  sheet: string | null; row: number | null; date: string; sap: string; lot: string
  qty: number; kind: string; site: string; id: number | null
  problem: 'unknown_lot' | 'lot_used_up'; problem_text: string; hint: string
}
/** Phase 22a — a problem the last sync no longer found: shown ✅ for a day. */
export interface FixedLotProblem extends Row {
  sheet: string | null; row: number | null; date: string; sap: string; lot: string
  qty: number; problem: string; fixed_at: string
}
/** Phase 22a — every lot problem as Excel, with what to change. */
export async function downloadLotProblems(siteId?: string) {
  const res = await api.get('/lot-register/problems.xlsx',
    { params: siteId ? { site_id: siteId } : {}, responseType: 'blob' })
  const cd = (res.headers['content-disposition'] as string | undefined) ?? ''
  const name = cd.match(/filename="?([^"]+)"?/)?.[1] ?? 'lot-problems.xlsx'
  const url = URL.createObjectURL(res.data as Blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
export function useLotRegister(params: { site_id?: string; q?: string; status?: string;
                                          include_exhausted?: boolean }) {
  return useQuery<{ items: LotRow[]; summary: Record<string, number>; exceptions: Row[];
                    problems?: LotProblem[]; problems_fixed?: FixedLotProblem[];
                    buckets: number[] }>({
    queryKey: ['/lot-register', params],
    queryFn: async () => (await api.get('/lot-register', { params })).data,
  })
}
export function useLotUnits(sap?: string, lot?: string, site?: string) {
  return useQuery<{ items: Row[]; used: number; in_stock: number }>({
    queryKey: ['/lot-register/units', sap, lot, site],
    enabled: !!sap && !!lot,
    queryFn: async () => (await api.get('/lot-register/units',
      { params: { sap_code: sap, lot, site_id: site } })).data,
  })
}
