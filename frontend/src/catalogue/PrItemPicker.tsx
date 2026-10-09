import { useMemo, useState } from 'react'
import { Select, Space, Tag, Typography } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { Row } from '../api/client'
import { Thumb, useThumbs } from './thumbs'

/**
 * Phase 23d — the PR line's material, with its picture (rulings Q23-5/8).
 *
 * The stocked items come first, as before. Typing also searches the 6,000-code
 * catalogue, so a HOD can raise a PR for an item GI Hub does NOT stock yet
 * (ruling Q23-5): such a line carries its GI code and NO SAP — none is
 * invented — and links itself when the workbook adds the item.
 *
 * Value: `sap:<SAP>` or `cat:<GI code>`.
 */
interface CatItem { code: string; description: string; uom: string | null; stocked: boolean; thumb: string | null }

export function splitPick(v?: string): { SAP_Code: string; Material_Code?: string } {
  if (!v) return { SAP_Code: '' }
  if (v.startsWith('cat:')) return { SAP_Code: '', Material_Code: v.slice(4) }
  return { SAP_Code: v.startsWith('sap:') ? v.slice(4) : v }
}

export default function PrItemPicker({ value, onChange, inventory, loading }: {
  value?: string; onChange?: (v?: string) => void; inventory: Row[]; loading?: boolean
}) {
  const [q, setQ] = useState('')
  const { data: thumbs } = useThumbs({ saps: inventory.map((r) => String(r.SAP_Code)) })
  const cat = useQuery<{ items: CatItem[] }>({
    queryKey: ['/catalogue/materials', 'pr-picker', q],
    enabled: q.trim().length >= 3,
    staleTime: 5 * 60_000,
    queryFn: async () => (await api.get('/catalogue/materials',
      { params: { q, show: 'not_stocked', limit: 20 } })).data,
  })
  const stocked = useMemo(() => inventory.map((r) => {
    const sap = String(r.SAP_Code)
    return {
      value: `sap:${sap}`, search: `${sap} ${r.Equipment_Description ?? ''} ${r.Material_Code ?? ''}`,
      label: (
        <Space size={6}>
          <Thumb src={thumbs?.saps[sap]} size={22} />
          <span>{sap} — {String(r.Equipment_Description ?? '')}</span>
        </Space>
      ),
    }
  }), [inventory, thumbs])
  const notStocked = (cat.data?.items ?? []).map((c) => ({
    value: `cat:${c.code}`, search: `${c.code} ${c.description}`,
    label: (
      <Space size={6}>
        <Thumb src={c.thumb} size={22} />
        <span>{c.code} — {c.description}</span>
        <Tag color="blue" style={{ margin: 0 }}>not stocked yet</Tag>
      </Space>
    ),
  }))
  return (
    <Select showSearch value={value} onChange={onChange} placeholder="Material (SAP, or a catalogue code)"
      style={{ width: 380 }} loading={loading || cat.isFetching} onSearch={setQ}
      filterOption={(input, opt) => String((opt as { search?: string })?.search ?? '')
        .toLowerCase().includes(input.toLowerCase())}
      notFoundContent={q.trim().length < 3 ? 'Type 3 letters to search the catalogue too'
        : <Typography.Text type="secondary">Nothing in stock or in the catalogue</Typography.Text>}
      options={[
        { label: 'In stock at GI Hub', options: stocked },
        ...(notStocked.length ? [{ label: 'Catalogue — not stocked yet (no SAP; links itself later)', options: notStocked }] : []),
      ]} data-testid="pr-item" />
  )
}
