import type { ColumnsType } from 'antd/es/table'
import type { Row } from '../api/client'
import { fmtCell } from './format'

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
    render: (v: unknown) => renderCell(v),
  }))
}
