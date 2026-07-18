// Smart decimals (2026-07-18 polish): a material quantity that is a whole
// number renders clean ("5", never "5.00"); real fractions keep up to 4 dp
// with trailing zeros trimmed. Percentages keep their fixed style — this is
// for quantities/stock columns only.
export function fmtQty(v: unknown): string {
  if (v === null || v === undefined || v === '') return ''
  const n = typeof v === 'number' ? v : Number(v)
  if (!Number.isFinite(n)) return String(v)
  if (Number.isInteger(n)) return String(n)
  return String(Math.round(n * 10000) / 10000)
}

// Numeric-looking cell values (raw SQL rows serialise PG numerics as
// "5.00"-style strings) — used by the generic column builder.
const NUMERIC_RX = /^-?\d+(\.\d+)?$/

export function fmtCell(v: unknown): unknown {
  if (typeof v === 'number') return fmtQty(v)
  if (typeof v === 'string' && NUMERIC_RX.test(v.trim())) return fmtQty(v.trim())
  return v
}
