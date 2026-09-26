/**
 * Phase 14a — pack and base units for Surface Shield materials, for DISPLAY.
 *
 * The ledger counts PACKS (Can, Bag, Roll) — what the store handles and the
 * workbook logs. The recipe and the estimator speak BASE units (KG, M2, EA).
 * Operator ruling Q14-5: show both, base first — "189 KG · 21 Can".
 *
 * ⚠️ THIS FILE NEVER DECIDES A FACTOR. The map comes from `GET
 * /meta/unit-sizes`, which reads `backend/api/services/units.py` — the one
 * home of the conversion. Here it is only multiplied and formatted, so the
 * screen cannot show a base figure the server would not compute.
 *
 * ⚠️ AN UNKNOWN FACTOR SHOWS AS UNKNOWN. A Surface Shield can with no Unit Size
 * reads "21 Can · — (no unit size)", never "21 KG": treating one can as one
 * kilogram is the exact defect (D1) Phase 14 exists to remove.
 */
import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export interface UnitInfo {
  sap: string
  is_surface_shield: boolean
  pack_uom: string | null
  unit_size: number | null
  factor: number | null
  base_uom: string | null
}

let _map: Record<string, UnitInfo> = {}

const norm = (sap: unknown) => String(sap ?? '').replace(/\s+/g, '').trim()

/** Test / bootstrap seam — the app shell calls `useUnitSizesLoader()`. */
export function setUnitMap(items: Record<string, UnitInfo>) { _map = items ?? {} }

export function unitFor(sap: unknown): UnitInfo | undefined {
  return _map[norm(sap)]
}

/** pack × factor, or null when the SAP is not a Surface Shield or the factor
 * is unknown. Rounded to 4 dp, the precision the ledger keeps. */
export function baseQty(sap: unknown, qty: unknown): number | null {
  const u = unitFor(sap)
  const q = Number(qty)
  if (!u || u.factor == null || qty === null || qty === undefined || qty === '' || !Number.isFinite(q)) return null
  return Math.round(q * u.factor * 10000) / 10000
}

function num(n: number): string {
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 100) / 100)
}

/**
 * "189 KG · 21 Can" for a Surface Shield whose pack is a container; null for
 * anything else (the caller renders the plain number). A measure pack (KG,
 * EA …) is its own base and needs no second figure.
 */
export function fmtPackBase(sap: unknown, qty: unknown, packUom?: string | null): string | null {
  const u = unitFor(sap)
  const q = Number(qty)
  if (!u || qty === null || qty === undefined || qty === '' || !Number.isFinite(q)) return null
  const pack = (packUom || u.pack_uom || '').trim()
  if (u.factor === 1 && (!u.base_uom || u.base_uom.toUpperCase() === pack.toUpperCase())) return null
  if (u.factor == null) return `${num(q)} ${pack} · — (no unit size)`.trim()
  const b = baseQty(sap, q)!
  return `${num(b)} ${u.base_uom ?? ''} · ${num(q)} ${pack}`.replace(/\s+/g, ' ').trim()
}

/** The live read-out under a quantity box: "= 40.5 KG". */
export function baseReadout(sap: unknown, qty: unknown): string | null {
  const u = unitFor(sap)
  if (!u) return null
  if (u.factor == null) return 'No Unit Size for this material yet — its KG figure is unknown.'
  if (u.factor === 1 && (!u.base_uom || u.base_uom.toUpperCase() === (u.pack_uom ?? '').toUpperCase())) return null
  const b = baseQty(sap, qty)
  const each = `1 ${u.pack_uom ?? 'pack'} = ${num(u.factor)} ${u.base_uom ?? ''}`.trim()
  return b == null ? each : `= ${num(b)} ${u.base_uom ?? ''}  (${each})`
}

/** Mounted once in the app shell: loads the factor map for every screen. */
export function useUnitSizesLoader() {
  const { data } = useQuery({
    queryKey: ['/meta/unit-sizes'],
    queryFn: async () => (await api.get<{ items: Record<string, UnitInfo> }>('/meta/unit-sizes')).data,
    staleTime: 10 * 60_000,
  })
  useEffect(() => { if (data?.items) setUnitMap(data.items) }, [data])
  return data?.items
}

/** Row keys that hold a PACK quantity of the row's `SAP_Code`. */
export const PACK_QTY_KEYS = new Set([
  'Quantity', 'Qty', 'Current_Stock', 'Opening_Stock', 'Received', 'Issued',
  'Returned', 'Balance', 'Lot_Balance', 'On_Hand', 'Available', 'Stock',
  'Total_Received', 'Total_Issued', 'Total_Returned',
])
