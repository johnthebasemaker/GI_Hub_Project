import type { ColumnsType } from 'antd/es/table'
import type { Row } from '../api/client'

function renderCell(v: unknown) {
  if (v === null || v === undefined) return <span style={{ color: '#bbb' }}>—</span>
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

// Derive antd columns from the first row's keys — generic across every entity.
export function buildColumns(rows: Row[]): ColumnsType<Row> {
  if (!rows.length) return []
  return Object.keys(rows[0]).map((key) => ({
    title: key,
    dataIndex: key,
    key,
    ellipsis: true,
    render: (v: unknown) => renderCell(v),
  }))
}
