import type { ColumnsType } from 'antd/es/table'
import type { Row } from '../api/client'
import { fmtCell } from './format'
import { PACK_QTY_KEYS, fmtPackBase } from './units'

function renderCell(v: unknown) {
  if (v === null || v === undefined) return <span style={{ opacity: 0.35 }}>—</span>
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(fmtCell(v))
}

// Derive antd columns from the first row's keys — generic across every entity.
// Every column reserves enough width for its full header title (headers must
// never truncate — the derived key IS the label); body cells keep their
// ellipsis and the table scrolls horizontally instead.
export function buildColumns(rows: Row[]): ColumnsType<Row> {
  if (!rows.length) return []
  return Object.keys(rows[0]).map((key) => ({
    title: key,
    dataIndex: key,
    key,
    ellipsis: true,
    width: Math.max(96, Math.round(key.length * 8.5) + 40),
    onHeaderCell: () => ({ style: { whiteSpace: 'nowrap' as const } }),
    // Phase 14a: a Surface Shield pack quantity also shows its base figure,
    // base first — "189 KG · 21 Can" (ruling Q14-5). Anything else is untouched.
    render: (v: unknown, row: Row) => {
      if (PACK_QTY_KEYS.has(key) && row && 'SAP_Code' in row) {
        const dual = fmtPackBase(row.SAP_Code, v, (row.UOM as string) ?? null)
        if (dual) return dual
      }
      return renderCell(v)
    },
  }))
}
