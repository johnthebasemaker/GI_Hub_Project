import { App, Button, Popconfirm, Tag, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Table } from '../lib/smartTable'
import { api } from '../api/client'

/**
 * Phase 21d (ruling Q21-3) — the handwritten names the store keepers taught
 * the OCR matcher, per site. A learned name is matched GREEN on the next
 * paper, so a wrong one would keep posting the wrong item: the HOD (or Admin)
 * removes it here. Every removal is audited.
 */
interface Learned {
  id: number; Site_ID: string; written_key: string; written_example: string | null
  SAP_Code: string; description: string; confirmations: number
  updated_by: string | null; updated_at: string
}

export default function LearnedNamesCard({ siteId }: { siteId?: string }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const { data, isFetching } = useQuery<{ items: Learned[] }>({
    queryKey: ['/ai/ocr/aliases', siteId],
    queryFn: async () => (await api.get('/ai/ocr/aliases', { params: { site_id: siteId } })).data,
  })
  const del = useMutation({
    mutationFn: async (id: number) => (await api.delete(`/ai/ocr/aliases/${id}`)).data,
    onSuccess: () => { message.success('Removed — that name will be checked again next time'); qc.invalidateQueries({ queryKey: ['/ai/ocr/aliases'] }) },
    onError: () => message.error('Could not remove it'),
  })
  return (
    <div data-testid="learned-names">
      <Typography.Paragraph type="secondary">
        When a store keeper accepts or picks the item a handwritten name means, the OCR import
        remembers it for that site and matches it <Tag color="green">learned</Tag> next time.
        Remove a name that was taught wrongly.
      </Typography.Paragraph>
      <Table size="small" loading={isFetching} dataSource={data?.items ?? []} rowKey={(r) => r.id}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        columns={[
          { title: 'Written on the paper', dataIndex: 'written_example', key: 'w',
            render: (v, r) => v || r.written_key },
          { title: 'Means', key: 'm', render: (_: unknown, r) => <>{r.SAP_Code} — {r.description}</> },
          { title: 'Site', dataIndex: 'Site_ID', key: 's', width: 90 },
          { title: 'Confirmed', dataIndex: 'confirmations', key: 'c', width: 100, align: 'right',
            render: (v) => `${v}×` },
          { title: 'Last by', key: 'b', width: 180, render: (_: unknown, r) => `${r.updated_by ?? '—'} · ${r.updated_at}` },
          { title: '', key: 'x', width: 90, render: (_: unknown, r) => (
            <Popconfirm title={`Forget “${r.written_example || r.written_key}”?`}
              okText="Remove" onConfirm={() => del.mutate(r.id)}>
              <Button size="small" danger data-testid={`learned-remove-${r.id}`}>Remove</Button>
            </Popconfirm>) },
        ]} />
    </div>
  )
}
