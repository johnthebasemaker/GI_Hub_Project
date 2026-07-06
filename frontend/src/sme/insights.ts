/**
 * frontend/src/sme/insights.ts — SME Dashboard aggregation math (Phase S2).
 *
 * Faithful port of the legacy Streamlit dashboard (Tab 0 "Project Overview",
 * material_estimator_portal.py lines ~3633–4046). This module is UI-layer
 * math over the client model and is NOT part of the parity-locked engine —
 * engine.ts/sme_engine.py stay byte-equivalent; this file only consumes them.
 *
 * Legacy semantics preserved exactly:
 *   demand           = For_1_SQM × remaining SQM per filtered (tag, code)
 *   coverage (scope) = Σ_m min(demand_m, available_m) / Σ_m demand_m × 100
 *                      (per-material cap against the FULL pool — the dashboard
 *                      never cascades; every scope assumes full stock)
 *   coverable SQM    = scope SQM × min(1, coverage/100)
 *   4-tier colors    = ≥100 green · ≥90 orange · ≥80 yellow · <80 red (_fc)
 */
import { roundN, syscodeCompare, unitKey } from './engine'
import type { SmeModel, SnapshotMaterial } from './engine'

export interface DashFilters {
  locations: string[]
  types: string[]
  codes: string[]
  substrates: string[]
}
export const EMPTY_FILTERS: DashFilters = { locations: [], types: [], codes: [], substrates: [] }

export interface UnitRef {
  tag: string
  code: string
  shortName: string
  remaining: number
  /** Original (planned) SQM before progress deduction — matrix reports use this. */
  original: number
  name: string
  location: string
  type: string
  substrate: string
}

/** Legacy _fc(): coverage color tiers. */
export const fc = (p: number): string =>
  p >= 100 ? '#10B981' : p >= 90 ? '#F97316' : p >= 80 ? '#EAB308' : '#EF4444'
/** Row-tint variant of the same tiers (legacy _style_cov backgrounds). */
export const fcBg = (p: number): string =>
  p >= 100 ? 'rgba(16,185,129,.1)' : p >= 90 ? 'rgba(249,115,22,.1)'
    : p >= 80 ? 'rgba(234,179,8,.1)' : 'rgba(239,68,68,.1)'
/** Legacy status dot for location cards. */
export const fcDot = (p: number): string =>
  p >= 100 ? '🟢' : p >= 90 ? '🟠' : p >= 80 ? '🟡' : '🔴'

/** Legacy loc_colors_map for the per-location stacked bars. */
export const LOC_COLORS: Record<string, string> = {
  'Brown Field': '#3B82F6', 'TRAIN J': '#F59E0B', 'TRAIN K': '#10B981',
}
export const locColor = (loc: string) => LOC_COLORS[loc] ?? '#94A3B8'

export function allUnits(model: SmeModel): UnitRef[] {
  const out: UnitRef[] = []
  for (const [tag, codes] of model.codesByTag) {
    const meta = model.tagMeta.get(tag)
    for (const code of codes) {
      const u = model.units.get(unitKey(tag, code))
      if (!u) continue
      out.push({
        tag, code, shortName: u.short_name, remaining: u.remaining,
        original: u.total_original,
        name: meta?.Name ?? '', location: meta?.Location ?? '',
        type: meta?.Type ?? '', substrate: meta?.Substrate ?? '',
      })
    }
  }
  return out
}

const pass = (sel: string[], v: string) => sel.length === 0 || sel.includes(v)

/** Filtered (tag, code) pairs; tag-level attrs + code membership (legacy §4). */
export function applyFilters(model: SmeModel, f: DashFilters): UnitRef[] {
  return allUnits(model).filter((u) =>
    pass(f.locations, u.location) && pass(f.types, u.type)
    && pass(f.substrates, u.substrate) && pass(f.codes, u.code))
}

/** Cross-filtered option lists (each scoped by the OTHER three selections). */
export function filterOptions(model: SmeModel, f: DashFilters) {
  const units = allUnits(model)
  const uniq = (vals: string[]) => [...new Set(vals.filter(Boolean))].sort()
  const locations = uniq(units.map((u) => u.location))
  const types = uniq(units.filter((u) => pass(f.locations, u.location)).map((u) => u.type))
  const codePool = units.filter((u) =>
    pass(f.locations, u.location) && pass(f.types, u.type) && pass(f.substrates, u.substrate))
  const codeMap = new Map<string, string>()
  for (const u of codePool) if (!codeMap.has(u.code)) codeMap.set(u.code, u.shortName)
  const codes = [...codeMap.keys()].sort(syscodeCompare)
    .map((code) => ({ code, shortName: codeMap.get(code) ?? '' }))
  const substrates = uniq(units.filter((u) =>
    pass(f.locations, u.location) && pass(f.types, u.type) && pass(f.codes, u.code))
    .map((u) => u.substrate))
  return { locations, types, codes, substrates }
}

// ─── Material balance (legacy f_demand) ──────────────────────────────────────
export interface BalanceRow {
  Material_Code: string
  Material_Name: string
  UOM: string
  Available_Qty: number
  Ordered_Qty: number
  Demand_Qty: number
  Shortfall: number
  Net_Shortfall: number
  Coverage_Pct: number
}
export interface ScopeTotals {
  demand: number
  availCapped: number
  shortfall: number
  netShortfall: number
  coveragePct: number // 0–100, uncapped-at-0 demand → 100
}

function materialMaps(materials: SnapshotMaterial[]) {
  const avail = new Map<string, number>()
  const ordered = new Map<string, number>()
  const meta = new Map<string, { name: string; uom: string }>()
  for (const m of materials) {
    const code = String(m.material_code ?? '').trim()
    avail.set(code, Number(m.available_qty ?? 0) || 0)
    ordered.set(code, Number((m as { ordered_qty?: number | null }).ordered_qty ?? 0) || 0)
    meta.set(code, { name: String(m.material_name ?? ''), uom: String(m.uom ?? '') })
  }
  return { avail, ordered, meta }
}

/** Aggregate demand per material over the given units (raw, unrounded). */
export function demandByMaterial(model: SmeModel, units: UnitRef[]) {
  const demand = new Map<string, number>()
  const names = new Map<string, { name: string; uom: string }>()
  for (const u of units) {
    for (const r of model.recipesByCode.get(u.code) ?? []) {
      demand.set(r.Material_Code, (demand.get(r.Material_Code) ?? 0) + r.For_1_SQM * u.remaining)
      if (!names.has(r.Material_Code)) {
        names.set(r.Material_Code, { name: r.Material_Name, uom: r.UOM })
      }
    }
  }
  return { demand, names }
}

export function materialBalance(
  model: SmeModel, units: UnitRef[], materials: SnapshotMaterial[],
): { rows: BalanceRow[]; totals: ScopeTotals } {
  const { demand, names } = demandByMaterial(model, units)
  const { avail, ordered, meta } = materialMaps(materials)
  const rows: BalanceRow[] = []
  let tDemand = 0, tAvail = 0, tShort = 0, tNet = 0
  for (const mat of [...demand.keys()].sort()) {
    const d = demand.get(mat)!
    const a = avail.get(mat) ?? 0
    const o = ordered.get(mat) ?? 0
    const short = Math.max(d - a, 0)
    const net = Math.max(d - a - o, 0)
    tDemand += d
    tAvail += Math.min(a, d)
    tShort += short
    tNet += net
    rows.push({
      Material_Code: mat,
      Material_Name: names.get(mat)?.name || meta.get(mat)?.name || '',
      UOM: names.get(mat)?.uom || meta.get(mat)?.uom || '',
      Available_Qty: a,
      Ordered_Qty: o,
      Demand_Qty: roundN(d, 3),
      Shortfall: roundN(short, 3),
      Net_Shortfall: roundN(net, 3),
      Coverage_Pct: d > 0 ? roundN(Math.min((Math.min(a, d) / d) * 100, 100), 1) : 100,
    })
  }
  return {
    rows,
    totals: {
      demand: tDemand, availCapped: tAvail, shortfall: tShort, netShortfall: tNet,
      coveragePct: tDemand > 0 ? (tAvail / tDemand) * 100 : 100,
    },
  }
}

/** Coverage of an arbitrary unit scope (per-material cap; legacy §7.2). */
export function scopeCoverage(
  model: SmeModel, units: UnitRef[], materials: SnapshotMaterial[],
): ScopeTotals {
  return materialBalance(model, units, materials).totals
}

// ─── Per-(tag, code) coverage (legacy _dd_sqm_pair drill-downs) ──────────────
export interface PairCoverage {
  tag: string
  code: string
  shortName: string
  sqm: number
  coveragePct: number
  coverableSqm: number
  deficitSqm: number
}

export function pairCoverage(
  model: SmeModel, units: UnitRef[], materials: SnapshotMaterial[],
): PairCoverage[] {
  const { avail } = materialMaps(materials)
  return units.map((u) => {
    let d = 0, a = 0
    for (const r of model.recipesByCode.get(u.code) ?? []) {
      const rowDemand = r.For_1_SQM * u.remaining
      d += rowDemand
      a += Math.min(rowDemand, avail.get(r.Material_Code) ?? 0)
    }
    const cov = d > 0 ? Math.min((a / d) * 100, 100) : 100
    const coverable = roundN(u.remaining * (Math.min(cov, 100) / 100), 2)
    return {
      tag: u.tag, code: u.code, shortName: u.shortName,
      sqm: roundN(u.remaining, 2),
      coveragePct: roundN(cov, 1),
      coverableSqm: coverable,
      deficitSqm: roundN(u.remaining - coverable, 2),
    }
  })
}

// ─── Per-location / per-system-code rollups ──────────────────────────────────
export interface GroupCoverage {
  key: string
  label: string
  shortName?: string
  equipment: number
  sqm: number
  canSqm: number
  shortSqm: number
  coveragePct: number
}

function groupCoverage(
  units: UnitRef[], keyOf: (u: UnitRef) => string,
): Map<string, UnitRef[]> {
  const groups = new Map<string, UnitRef[]>()
  for (const u of units) {
    const k = keyOf(u)
    if (!groups.has(k)) groups.set(k, [])
    groups.get(k)!.push(u)
  }
  return groups
}

export function locationRows(
  model: SmeModel, units: UnitRef[], materials: SnapshotMaterial[],
): GroupCoverage[] {
  const groups = groupCoverage(units, (u) => u.location)
  return [...groups.keys()].sort().map((loc) => {
    const gu = groups.get(loc)!
    const cov = scopeCoverage(model, gu, materials).coveragePct
    const sqm = gu.reduce((s, u) => s + u.remaining, 0)
    const can = roundN(sqm * Math.min(1, cov / 100), 2)
    return {
      key: loc, label: loc || '—',
      equipment: new Set(gu.map((u) => u.tag)).size,
      sqm: roundN(sqm, 2), canSqm: can, shortSqm: roundN(sqm - can, 2),
      coveragePct: roundN(cov, 1),
    }
  })
}

export function systemCodeRows(
  model: SmeModel, units: UnitRef[], materials: SnapshotMaterial[],
): GroupCoverage[] {
  const groups = groupCoverage(units, (u) => u.code)
  return [...groups.keys()].sort(syscodeCompare).map((code) => {
    const gu = groups.get(code)!
    const cov = scopeCoverage(model, gu, materials).coveragePct
    const sqm = gu.reduce((s, u) => s + u.remaining, 0)
    const can = roundN(sqm * Math.min(1, cov / 100), 2)
    return {
      key: code, label: `Code ${code}`, shortName: gu[0]?.shortName ?? '',
      equipment: new Set(gu.map((u) => u.tag)).size,
      sqm: roundN(sqm, 2), canSqm: can, shortSqm: roundN(sqm - can, 2),
      coveragePct: roundN(cov, 1),
    }
  })
}

// ─── Stock-only materials (legacy R20.1: recipe-member ∧ no current demand) ──
export function stockOnlyRows(
  model: SmeModel, units: UnitRef[], materials: SnapshotMaterial[],
): { Material_Code: string; Material_Name: string; UOM: string; Available_Qty: number; Ordered_Qty: number }[] {
  const { demand } = demandByMaterial(model, units)
  const recipeMats = new Set<string>()
  for (const rows of model.recipesByCode.values()) {
    for (const r of rows) recipeMats.add(r.Material_Code)
  }
  return materials
    .filter((m) => {
      const code = String(m.material_code ?? '').trim()
      return recipeMats.has(code) && !demand.has(code)
    })
    .map((m) => ({
      Material_Code: String(m.material_code ?? ''),
      Material_Name: String(m.material_name ?? ''),
      UOM: String(m.uom ?? ''),
      Available_Qty: Number(m.available_qty ?? 0) || 0,
      Ordered_Qty: Number((m as { ordered_qty?: number | null }).ordered_qty ?? 0) || 0,
    }))
}
