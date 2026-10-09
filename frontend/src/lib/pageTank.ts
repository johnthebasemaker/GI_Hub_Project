/**
 * Phase 23b — the OCR page's two row-level helpers, kept pure so the rules
 * are testable without a browser.
 *
 * 1. "Tank for this whole page" (ruling Q23-2). The reader garbles the FIRST
 *    tank cell of a page and every ditto below inherits it (tank right 0.42 on
 *    the 8 photos). The store keeper sets the page's tank once. It fills ONLY
 *    rows whose tank is a ditto mark, blank, or unknown — a gold "check" is
 *    unknown too, it is a question and not an answer — and NEVER a row where a
 *    tank was written and matched, or one the store keeper set by hand.
 *    Rows it filled can be filled again (a second choice) or undone exactly.
 *
 * 2. The second read (ruling Q23-3) returns rows the first read skipped; each
 *    carries its printed S.No and goes in at its place on the page.
 */
export interface TankRow {
  tank_no?: string
  tank_tag?: string | null
  tank_state?: string
  tank_source?: string | null
  tank_group?: string
  sno?: string | number | null
  _key?: string
}

/** Undo is keyed by the row's own key, so rows the second read inserts later
 * cannot shift it onto the wrong line. */
const keyOf = (r: TankRow, i: number) => String(r._key ?? i)

const HAS_TEXT = /[A-Za-z0-9]/

/** May the page tank fill this row? */
export function pageTankFillable(r: TankRow): boolean {
  if (r.tank_source === 'accepted') return false          // the store keeper's own choice
  if (r.tank_source === 'page') return true                // filled by the page tank before
  const written = String(r.tank_no ?? '').trim()
  if (!HAS_TEXT.test(written)) return true                 // blank, or a ditto mark
  if (!r.tank_tag) return true                             // written but not matched
  return r.tank_state === 'unknown' || r.tank_state === 'suggested'
}

export type TankSnapshot = Pick<TankRow, 'tank_tag' | 'tank_state' | 'tank_source'>

export function fillPageTank<T extends TankRow>(rows: T[], tag: string):
  { rows: T[]; filled: number[]; kept: number; undo: Record<string, TankSnapshot> } {
  const filled: number[] = []
  const undo: Record<string, TankSnapshot> = {}
  const out = rows.map((r, i) => {
    if (!pageTankFillable(r)) return r
    filled.push(i)
    undo[keyOf(r, i)] = { tank_tag: r.tank_tag ?? null, tank_state: r.tank_state, tank_source: r.tank_source ?? null }
    return { ...r, tank_tag: tag, tank_state: 'auto', tank_source: 'page' }
  })
  return { rows: out, filled, kept: rows.length - filled.length, undo }
}

/** Put back exactly what the page tank replaced, on rows it still owns. */
export function undoPageTank<T extends TankRow>(rows: T[], undo: Record<string, TankSnapshot>): T[] {
  return rows.map((r, i) => {
    const u = undo[keyOf(r, i)]
    return u && r.tank_source === 'page' ? { ...r, ...u } : r
  })
}

function snoOf(r: { sno?: string | number | null }): number | null {
  const n = Number(String(r.sno ?? '').trim())
  return Number.isInteger(n) && n >= 1 && n <= 30 ? n : null
}

/** Insert each added row before the first row with a larger S.No. Rows the
 * page already has keep their order (and their keys); nothing is replaced. */
export function insertBySno<T extends { sno?: string | number | null }>(rows: T[], added: T[]): T[] {
  const out = [...rows]
  for (const a of [...added].sort((x, y) => (snoOf(x) ?? 99) - (snoOf(y) ?? 99))) {
    const s = snoOf(a)
    if (s == null) { out.push(a); continue }
    if (out.some((r) => snoOf(r) === s)) continue           // already on the page
    const at = out.findIndex((r) => (snoOf(r) ?? -1) > s)
    if (at < 0) out.push(a)
    else out.splice(at, 0, a)
  }
  return out
}

/** A ditto / blank tank on an added row takes the tank of the row above. */
export function inheritTankFromAbove<T extends TankRow>(rows: T[], idxs: number[]): T[] {
  const set = new Set(idxs)
  const out = [...rows]
  for (let i = 0; i < out.length; i += 1) {
    if (!set.has(i)) continue
    const r = out[i]
    if (HAS_TEXT.test(String(r.tank_no ?? '')) || i === 0) continue
    const up = out[i - 1]
    out[i] = { ...r, tank_tag: up.tank_tag ?? null, tank_state: up.tank_tag ? up.tank_state : 'blank',
               tank_source: up.tank_source === 'accepted' ? null : (up.tank_source ?? null),
               tank_group: up.tank_group ?? up.tank_no }
  }
  return out
}
