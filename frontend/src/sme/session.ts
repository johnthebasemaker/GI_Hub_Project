/**
 * frontend/src/sme/session.ts — session-derived stats over cascade output
 * (Phase S3). Ports of the legacy per-tag/per-code helpers that decorate the
 * session builder and session report:
 *   tag_fulfillment()      → min(100, Σalloc/Σdemand×100) per tag
 *   syscode_fulfillment()  → same per (tag, code)
 *   sqm_can_do()           → code SQM × min(1, alloc/demand)
 *   combined procurement w/ SQM weighting (portal lines 4752–4777):
 *     per line SQM_Done = Total_SQM × Fulfillment_Pct/100, grouped by material.
 * UI-layer math like insights.ts — NOT part of the parity-locked engine.
 */
import { roundN, syscodeCompare, unitKey } from './engine'
import type { AllocationLine, SmeModel } from './engine'

export interface CodeStat {
  tag: string
  code: string
  shortName: string
  sqm: number
  canSqm: number
  shortSqm: number
  demand: number
  alloc: number
  shortfall: number
  fulfillPct: number
}

export interface TagStat {
  tag: string
  name: string
  location: string
  type: string
  substrate: string
  codes: string[]
  demand: number
  alloc: number
  shortfall: number
  fulfillPct: number
  sqm: number
  canSqm: number
}

/** Per-(tag, code) rollup of cascade lines (legacy syscode_fulfillment + sqm_can_do). */
export function codeStats(lines: AllocationLine[]): Map<string, CodeStat> {
  const out = new Map<string, CodeStat>()
  for (const ln of lines) {
    const k = unitKey(ln.Equipment_Tag_No, ln.Lining_System_Code)
    let s = out.get(k)
    if (!s) {
      s = {
        tag: ln.Equipment_Tag_No, code: ln.Lining_System_Code,
        shortName: ln.Lining_System_Short_Name, sqm: ln.Total_SQM,
        canSqm: 0, shortSqm: 0, demand: 0, alloc: 0, shortfall: 0, fulfillPct: 100,
      }
      out.set(k, s)
    }
    s.demand += ln.Demand_Qty
    s.alloc += ln.Allocated_Qty
    s.shortfall += ln.Shortfall_Qty
  }
  for (const s of out.values()) {
    const rate = s.demand > 0 ? Math.min(1, s.alloc / s.demand) : 1
    s.fulfillPct = roundN(rate * 100, 1)
    s.canSqm = roundN(s.sqm * rate, 2)
    s.shortSqm = roundN(s.sqm - s.canSqm, 2)
  }
  return out
}

/** Per-tag rollup (legacy tag_fulfillment + summed code SQM can-do). */
export function tagStats(model: SmeModel, lines: AllocationLine[]): Map<string, TagStat> {
  const perCode = codeStats(lines)
  const out = new Map<string, TagStat>()
  for (const s of perCode.values()) {
    let t = out.get(s.tag)
    if (!t) {
      const meta = model.tagMeta.get(s.tag)
      t = {
        tag: s.tag, name: meta?.Name ?? '', location: meta?.Location ?? '',
        type: meta?.Type ?? '', substrate: meta?.Substrate ?? '',
        codes: [], demand: 0, alloc: 0, shortfall: 0, fulfillPct: 100, sqm: 0, canSqm: 0,
      }
      out.set(s.tag, t)
    }
    t.codes.push(s.code)
    t.demand += s.demand
    t.alloc += s.alloc
    t.shortfall += s.shortfall
    t.sqm += s.sqm
    t.canSqm += s.canSqm
  }
  for (const t of out.values()) {
    t.codes.sort(syscodeCompare)
    t.fulfillPct = roundN(t.demand > 0 ? Math.min(100, (t.alloc / t.demand) * 100) : 100, 1)
    t.sqm = roundN(t.sqm, 2)
    t.canSqm = roundN(t.canSqm, 2)
  }
  return out
}

export interface WeightedProcurementRow {
  Material_Code: string
  Material_Name: string
  UOM: string
  Demand_Qty: number
  Allocated_Qty: number
  Shortfall_Qty: number
  Fulfillment_Pct: number
  SQM_Total: number
  SQM_Done: number
  SQM_Deficit: number
}

/** Combined procurement with legacy SQM weighting (per-cell fulfillment × SQM). */
export function weightedProcurement(lines: AllocationLine[]): WeightedProcurementRow[] {
  const acc = new Map<string, WeightedProcurementRow>()
  for (const ln of lines) {
    let r = acc.get(ln.Material_Code)
    if (!r) {
      r = {
        Material_Code: ln.Material_Code, Material_Name: ln.Material_Name, UOM: ln.UOM,
        Demand_Qty: 0, Allocated_Qty: 0, Shortfall_Qty: 0, Fulfillment_Pct: 100,
        SQM_Total: 0, SQM_Done: 0, SQM_Deficit: 0,
      }
      acc.set(ln.Material_Code, r)
    }
    r.Demand_Qty += ln.Demand_Qty
    r.Allocated_Qty += ln.Allocated_Qty
    r.Shortfall_Qty += ln.Shortfall_Qty
    r.SQM_Total += ln.Total_SQM
    r.SQM_Done += ln.Total_SQM * (ln.Fulfillment_Pct / 100)
  }
  const rows = [...acc.values()].map((r) => ({
    ...r,
    Demand_Qty: roundN(r.Demand_Qty, 3),
    Allocated_Qty: roundN(r.Allocated_Qty, 3),
    Shortfall_Qty: roundN(r.Shortfall_Qty, 3),
    Fulfillment_Pct: roundN(r.Demand_Qty > 0
      ? Math.min(100, (r.Allocated_Qty / r.Demand_Qty) * 100) : 100, 1),
    SQM_Total: roundN(r.SQM_Total, 2),
    SQM_Done: roundN(r.SQM_Done, 2),
    SQM_Deficit: roundN(r.SQM_Total - r.SQM_Done, 2),
  }))
  rows.sort((a, b) => a.Fulfillment_Pct - b.Fulfillment_Pct
    || (a.Material_Code < b.Material_Code ? -1 : a.Material_Code > b.Material_Code ? 1 : 0))
  return rows
}
