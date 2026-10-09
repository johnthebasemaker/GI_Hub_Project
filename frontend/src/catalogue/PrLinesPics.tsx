import { Space, Table, Tag, Typography } from 'antd'
import type { Row } from '../api/client'
import { useHodPrLines } from '../api/hooks'
import { Thumb, useThumbs } from './thumbs'

/**
 * Phase 23d — a PR's lines with their pictures, for Logistics' review (the
 * people who buy the item see what the site means by it). A line for an item
 * not stocked yet carries its GI code and no SAP (ruling Q23-5).
 */
export default function PrLinesPics({ pr, site }: { pr: string; site?: string }) {
  const { data, isLoading } = useHodPrLines(pr, site)
  const lines = (data ?? []) as Row[]
  const { data: thumbs } = useThumbs({
    saps: lines.map((l) => String(l.SAP_Code ?? '')),
    codes: lines.map((l) => String(l.Material_Code ?? '')),
  })
  return (
    <Table size="small" loading={isLoading} dataSource={lines} rowKey={(r) => String(r.id)} pagination={false}
      data-testid="pr-lines-pics"
      columns={[
        { title: '', key: 'pic', width: 64, render: (_: unknown, r: Row) => (
          <Thumb size={48} src={thumbs?.saps[String(r.SAP_Code ?? '')] ?? thumbs?.codes[String(r.Material_Code ?? '').toUpperCase()]} />) },
        { title: 'Item', key: 'i', render: (_: unknown, r: Row) => (
          <Space direction="vertical" size={0}>
            <span>{String(r.Material_Name ?? '')}</span>
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              {r.SAP_Code ? `SAP ${String(r.SAP_Code)}` : <Tag color="blue" style={{ margin: 0 }}>not stocked yet</Tag>}
              {r.Material_Code ? ` · ${String(r.Material_Code)}` : ''}
            </Typography.Text>
          </Space>) },
        { title: 'Qty', key: 'q', width: 110, align: 'right' as const,
          render: (_: unknown, r: Row) => `${Number(r.Requested_Qty)} ${String(r.UOM ?? '')}` },
        { title: 'Note', dataIndex: 'Notes', key: 'n', ellipsis: true },
      ]} />
  )
}
