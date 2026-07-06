/**
 * frontend/src/sme/engine.ts — client-side SME allocation engine (Phase S1).
 *
 * Line-for-line mirror of backend/api/sme_engine.py (the Python parity
 * oracle). Both implementations must reproduce backend/api/
 * sme_parity_golden.json exactly — the TS side is checked by
 * frontend/scripts/sme_parity.mjs, the Python side by service_tests.py.
 * If you change ANY numeric behavior here, change the Python module in the
 * same commit and regenerate the golden.
 *
 * All functions are pure: the model comes from GET /sme/model-snapshot and
 * every recalculation (drag-reorder, what-if) runs entirely in the browser.
 */

// ─── Snapshot types (GET /sme/model-snapshot) ────────────────────────────────
export interface SnapshotEquipment {
  Equipment_Tag_No: string
  Name?: string | null
  Location?: string | null
  Sub_Location?: string | null
  Type?: string | null
  Substrate?: string | null
  Lining_System_Code: string | number
  Surface_Area_SQM?: number | string | null
}
export interface SnapshotRecipe {
  Lining_System_Code: string | number
  Lining_System_Name?: string | null
  Material_Code: string
  Material_Name?: string | null
  UOM?: string | null
  For_1_SQM?: number | string | null
}
export interface SnapshotMaterial {
  material_code: string
  material_name?: string | null
  uom?: string | null
  available_qty?: number | string | null
}
export interface SnapshotProgress {
  Equipment_Tag_No: string
  Lining_System_Code: string | number
  Original_SQM?: number | string | null
  Done_SQM?: number | string | null
  Done_SQM_staged?: number | string | null
}
export interface SmeSnapshot {
  site_id: string | null
  equipment: SnapshotEquipment[]
  recipes: SnapshotRecipe[]
  materials: SnapshotMaterial[]
  progress: SnapshotProgress[]
  default_order: string[]
}

// ─── Engine output types ─────────────────────────────────────────────────────
export interface AllocationLine {
  Equipment_Tag_No: string
  Lining_System_Code: string
  Lining_System_Short_Name: string
  Total_SQM: number
  Material_Code: string
  Material_Name: string
  UOM: string
  Demand_Qty: number
  Allocated_Qty: number
  Shortfall_Qty: number
  Pool_Before: number
  Pool_After: number
  Fulfillment_Pct: number
}
export interface FeasibilityRow {
  Priority_Rank: number
  Equipment_Tag_No: string
  Name: string
  Total_Demand_Qty: number
  Total_Allocated_Qty: number
  Total_Shortfall_Qty: number
  Completion_Pct: number
  Status: string
  Bottleneck_Material_Code: string
  Bottleneck_Material_Name: string
  Bottleneck_Shortfall: number
}
export interface SuggestionRow {
  Pause_Tag: string
  Pause_Name: string
  Newly_Completable_Count: number
  Newly_Completable_Tags: string
  Avg_Completion_Gain_Pct: number
  Net_Gain_Score: number
  Recommended: boolean
}
export interface ProcurementRow {
  Material_Code: string
  Material_Name: string
  UOM: string
  Available_Qty: number
  Shortage_Qty_To_Buy: number
}
export interface MaterialTotal {
  Material_Code: string
  Material_Name: string
  UOM: string
  Demand_Qty: number
  Allocated_Qty: number
  Shortfall_Qty: number
}
export interface PlanResult {
  order_used: string[]
  lines: AllocationLine[]
  feasibility: FeasibilityRow[]
  totals: MaterialTotal[]
  procurement: ProcurementRow[]
}
export interface SuggestionResult {
  suggestions: SuggestionRow[]
  best_detail: (FeasibilityRow & { Scenario: string })[]
}

interface Unit { total_original: number; remaining: number; done: number; short_name: string }
export interface SmeModel {
  units: Map<string, Unit> // key `${tag}\u0000${code}`
  codesByTag: Map<string, string[]>
  recipesByCode: Map<string, { Material_Code: string; Material_Name: string; UOM: string; For_1_SQM: number }[]>
  poolInit: Map<string, number>
  matMeta: Map<string, { Material_Name: string; UOM: string }>
  tagMeta: Map<string, { Name: string; Location: string; Type: string; Substrate: string }>
  defaultOrder: string[]
}

export const STATUS_FULL = '✅ 100% Fully Ready to Build'
export const STATUS_PARTIAL = '🟡 Partially Ready'
export const STATUS_BLOCKED = '🔴 Blocked by Shortages'

/** Half-up rounding shared verbatim with the Python oracle (never diverges). */
export function roundN(x: number, n: number): number {
  if (!Number.isFinite(x)) return 0
  const s = Math.pow(10, n)
  return x < 0 ? -Math.floor(-x * s + 0.5) / s : Math.floor(x * s + 0.5) / s
}

const clip = (x: number, lo: number, hi: number) => (x < lo ? lo : x > hi ? hi : x)

function num(v: unknown): number {
  const f = typeof v === 'number' ? v : parseFloat(String(v ?? ''))
  return Number.isNaN(f) ? 0 : f
}

const s = (v: unknown): string => (v === null || v === undefined ? '' : String(v).trim())

const ukey = (tag: string, code: string) => `${tag}\u0000${code}`

/** Numeric-first ordering for lining-system codes (mirrors syscode_sort_key). */
export function syscodeCompare(a: string, b: string): number {
  const ad = /^\d+$/.test(a)
  const bd = /^\d+$/.test(b)
  if (ad && bd) return Number(a) - Number(b)
  if (ad !== bd) return ad ? -1 : 1
  return a < b ? -1 : a > b ? 1 : 0
}

/** Code-point string compare (mirrors Python's str ordering, not locale). */
const strCompare = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)

// ─── Model ───────────────────────────────────────────────────────────────────
export function buildModel(
  equipment: SnapshotEquipment[], recipes: SnapshotRecipe[],
  materials: SnapshotMaterial[], progress: SnapshotProgress[],
): SmeModel {
  const recipesByCode: SmeModel['recipesByCode'] = new Map()
  const shortNameByCode = new Map<string, string>()
  for (const r of recipes) {
    const code = s(r.Lining_System_Code)
    const row = {
      Material_Code: s(r.Material_Code), Material_Name: s(r.Material_Name),
      UOM: s(r.UOM), For_1_SQM: num(r.For_1_SQM),
    }
    if (!recipesByCode.has(code)) recipesByCode.set(code, [])
    recipesByCode.get(code)!.push(row)
    if (!shortNameByCode.has(code)) shortNameByCode.set(code, s(r.Lining_System_Name))
  }

  const prog = new Map<string, { original: number; done: number; remaining: number }>()
  for (const p of progress) {
    const orig = num(p.Original_SQM)
    const done = num(p.Done_SQM) + num(p.Done_SQM_staged)
    prog.set(ukey(s(p.Equipment_Tag_No), s(p.Lining_System_Code)),
      { original: orig, done, remaining: Math.max(orig - done, 0) })
  }

  const units: SmeModel['units'] = new Map()
  const tagMeta: SmeModel['tagMeta'] = new Map()
  const codesByTag: SmeModel['codesByTag'] = new Map()
  for (const e of equipment) {
    const tag = s(e.Equipment_Tag_No)
    const code = s(e.Lining_System_Code)
    if (!tag) continue
    if (!tagMeta.has(tag)) {
      tagMeta.set(tag, {
        Name: s(e.Name), Location: s(e.Location), Type: s(e.Type), Substrate: s(e.Substrate),
      })
      codesByTag.set(tag, [])
    }
    const k = ukey(tag, code)
    const u = units.get(k)
    if (u === undefined) {
      units.set(k, { total_original: num(e.Surface_Area_SQM), remaining: 0, done: 0, short_name: '' })
      codesByTag.get(tag)!.push(code)
    } else {
      u.total_original += num(e.Surface_Area_SQM)
    }
  }
  for (const [k, u] of units) {
    const p = prog.get(k)
    u.remaining = p !== undefined ? p.remaining : u.total_original
    u.done = p !== undefined ? p.done : 0
    u.short_name = shortNameByCode.get(k.split('\u0000')[1]) ?? ''
  }
  for (const codes of codesByTag.values()) codes.sort(syscodeCompare)

  const poolInit = new Map<string, number>()
  const matMeta: SmeModel['matMeta'] = new Map()
  for (const m of materials) {
    const mat = s(m.material_code)
    poolInit.set(mat, num(m.available_qty))
    matMeta.set(mat, { Material_Name: s(m.material_name), UOM: s(m.uom) })
  }

  return {
    units, codesByTag, recipesByCode, poolInit, matMeta, tagMeta,
    defaultOrder: [...codesByTag.keys()].sort(strCompare),
  }
}

function dedupe(order: string[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const raw of order) {
    const t = s(raw)
    if (t && !seen.has(t)) { seen.add(t); out.push(t) }
  }
  return out
}

// ─── Cascade allocation (legacy cascade_allocate port) ───────────────────────
export function cascadeAllocate(model: SmeModel, order: string[]): AllocationLine[] {
  const pool = new Map(model.poolInit)
  const lines: AllocationLine[] = []
  for (const tag of dedupe(order)) {
    for (const code of model.codesByTag.get(tag) ?? []) {
      const unit = model.units.get(ukey(tag, code))!
      const remaining = unit.remaining
      for (const r of model.recipesByCode.get(code) ?? []) {
        const mat = r.Material_Code
        const demand = r.For_1_SQM * remaining
        const before = pool.get(mat) ?? 0
        const alloc = Math.min(demand, before)
        const after = Math.max(0, before - alloc)
        pool.set(mat, after)
        const d4 = roundN(demand, 4)
        const a4 = roundN(alloc, 4)
        lines.push({
          Equipment_Tag_No: tag,
          Lining_System_Code: code,
          Lining_System_Short_Name: unit.short_name,
          Total_SQM: roundN(remaining, 2),
          Material_Code: mat,
          Material_Name: r.Material_Name || (model.matMeta.get(mat)?.Material_Name ?? ''),
          UOM: r.UOM,
          Demand_Qty: d4,
          Allocated_Qty: a4,
          Shortfall_Qty: roundN(demand - alloc, 4),
          Pool_Before: roundN(before, 4),
          Pool_After: roundN(after, 4),
          Fulfillment_Pct: d4 > 0 ? roundN(clip((a4 / d4) * 100, 0, 100), 2) : 100,
        })
      }
    }
  }
  return lines
}

// ─── Feasibility (legacy compute_feasibility port, cascade granularity) ──────
export function computeFeasibility(
  model: SmeModel, lines: AllocationLine[], order: string[],
): FeasibilityRow[] {
  const byTag = new Map<string, AllocationLine[]>()
  for (const ln of lines) {
    if (!byTag.has(ln.Equipment_Tag_No)) byTag.set(ln.Equipment_Tag_No, [])
    byTag.get(ln.Equipment_Tag_No)!.push(ln)
  }

  const out: FeasibilityRow[] = []
  let rank = 0
  for (const tag of dedupe(order)) {
    rank += 1
    const rows = byTag.get(tag)
    if (!rows || rows.length === 0) continue
    let demand = 0, alloc = 0, short = 0
    for (const r of rows) { demand += r.Demand_Qty; alloc += r.Allocated_Qty; short += r.Shortfall_Qty }
    const completion = demand > 0 ? roundN(clip((alloc / demand) * 100, 0, 100), 2) : 100
    let minRate = 2
    let bottleneck: AllocationLine | null = null
    for (const r of rows) {
      const rate = r.Demand_Qty > 0 ? clip(r.Allocated_Qty / r.Demand_Qty, 0, 1) : 1
      if (rate < minRate) { minRate = rate; bottleneck = r } // strict: first min wins ties
    }
    const status = short <= 0 ? STATUS_FULL
      : minRate === 0 ? STATUS_BLOCKED
        : `${STATUS_PARTIAL} (${completion.toFixed(1)}%)`
    const hasBn = bottleneck !== null && bottleneck.Shortfall_Qty > 0
    out.push({
      Priority_Rank: rank,
      Equipment_Tag_No: tag,
      Name: model.tagMeta.get(tag)?.Name ?? '',
      Total_Demand_Qty: roundN(demand, 4),
      Total_Allocated_Qty: roundN(alloc, 4),
      Total_Shortfall_Qty: roundN(short, 4),
      Completion_Pct: completion,
      Status: status,
      Bottleneck_Material_Code: hasBn ? bottleneck!.Material_Code : '—',
      Bottleneck_Material_Name: hasBn ? bottleneck!.Material_Name : '—',
      Bottleneck_Shortfall: hasBn ? bottleneck!.Shortfall_Qty : 0,
    })
  }
  return out
}

// ─── Suggestion engine (legacy run_suggestion_engine port) ───────────────────
export function runSuggestionEngine(model: SmeModel, orderIn: string[]): SuggestionResult {
  const order = dedupe(orderIn)
  const baseFeas = computeFeasibility(model, cascadeAllocate(model, order), order)
  const baseFull = new Set(baseFeas.filter((f) => f.Status === STATUS_FULL).map((f) => f.Equipment_Tag_No))
  const candidates = baseFeas.filter((f) => f.Status !== STATUS_FULL).map((f) => f.Equipment_Tag_No)

  const rows: SuggestionRow[] = []
  let bestScore: [number, number] = [-1, -999]
  let bestDetail: SuggestionResult['best_detail'] = []
  for (const pause of candidates) {
    const simOrder = order.filter((t) => t !== pause)
    const simFeas = computeFeasibility(model, cascadeAllocate(model, simOrder), simOrder)
    const simFull = new Set(simFeas.filter((f) => f.Status === STATUS_FULL).map((f) => f.Equipment_Tag_No))
    const simCompletion = new Map(simFeas.map((f) => [f.Equipment_Tag_No, f.Completion_Pct]))
    const newly = [...simFull].filter((t) => !baseFull.has(t)).sort(strCompare)
    const gains: number[] = []
    for (const f of baseFeas) {
      if (f.Equipment_Tag_No !== pause && simCompletion.has(f.Equipment_Tag_No)) {
        gains.push(simCompletion.get(f.Equipment_Tag_No)! - f.Completion_Pct)
      }
    }
    const avgGain = gains.length ? gains.reduce((a, b) => a + b, 0) / gains.length : 0
    rows.push({
      Pause_Tag: pause,
      Pause_Name: model.tagMeta.get(pause)?.Name || pause,
      Newly_Completable_Count: newly.length,
      Newly_Completable_Tags: newly.length ? newly.join(', ') : '—',
      Avg_Completion_Gain_Pct: roundN(avgGain, 2),
      Net_Gain_Score: newly.length - 1,
      Recommended: false,
    })
    if (newly.length > bestScore[0] || (newly.length === bestScore[0] && avgGain > bestScore[1])) {
      bestScore = [newly.length, avgGain]
      bestDetail = simFeas.map((f) => ({ ...f, Scenario: `If '${pause}' is paused` }))
    }
  }

  rows.sort((a, b) => b.Newly_Completable_Count - a.Newly_Completable_Count
    || b.Avg_Completion_Gain_Pct - a.Avg_Completion_Gain_Pct) // stable on ties
  if (rows.length) rows[0].Recommended = true
  return { suggestions: rows, best_detail: bestDetail }
}

// ─── Procurement list + per-material totals ──────────────────────────────────
export function buildProcurementList(model: SmeModel, lines: AllocationLine[]): ProcurementRow[] {
  const shortage = new Map<string, number>()
  for (const ln of lines) shortage.set(ln.Material_Code, (shortage.get(ln.Material_Code) ?? 0) + ln.Shortfall_Qty)
  const out: ProcurementRow[] = []
  for (const mat of [...shortage.keys()].sort(strCompare)) {
    const v = shortage.get(mat)!
    if (v <= 0) continue
    const meta = model.matMeta.get(mat)
    out.push({
      Material_Code: mat,
      Material_Name: meta?.Material_Name ?? '',
      UOM: meta?.UOM ?? '',
      Available_Qty: model.poolInit.get(mat) ?? 0,
      Shortage_Qty_To_Buy: roundN(v, 3),
    })
  }
  out.sort((a, b) => b.Shortage_Qty_To_Buy - a.Shortage_Qty_To_Buy
    || strCompare(a.Material_Code, b.Material_Code))
  return out
}

export function buildTotals(lines: AllocationLine[]): MaterialTotal[] {
  const totals = new Map<string, MaterialTotal>()
  for (const ln of lines) {
    let t = totals.get(ln.Material_Code)
    if (t === undefined) {
      t = {
        Material_Code: ln.Material_Code, Material_Name: ln.Material_Name, UOM: ln.UOM,
        Demand_Qty: 0, Allocated_Qty: 0, Shortfall_Qty: 0,
      }
      totals.set(ln.Material_Code, t)
    }
    t.Demand_Qty += ln.Demand_Qty
    t.Allocated_Qty += ln.Allocated_Qty
    t.Shortfall_Qty += ln.Shortfall_Qty
  }
  return [...totals.keys()].sort(strCompare).map((mat) => {
    const t = totals.get(mat)!
    return {
      ...t,
      Demand_Qty: roundN(t.Demand_Qty, 3),
      Allocated_Qty: roundN(t.Allocated_Qty, 3),
      Shortfall_Qty: roundN(t.Shortfall_Qty, 3),
    }
  })
}

/** One-shot plan: cascade + feasibility + totals + procurement. */
export function runPlan(model: SmeModel, orderIn: string[]): PlanResult {
  const order = dedupe(orderIn)
  const lines = cascadeAllocate(model, order)
  return {
    order_used: order,
    lines,
    feasibility: computeFeasibility(model, lines, order),
    totals: buildTotals(lines),
    procurement: buildProcurementList(model, lines),
  }
}
