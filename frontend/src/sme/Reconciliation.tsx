/**
 * Phase 14b — QR ⇄ Excel reconciliation, per bucket (day · equipment · SAP).
 *
 * The ledger holds ONE quantity per bucket — max(QR, Excel), never the sum
 * (backend/api/services/reconcile.py). This tab is the HOD's view of how the
 * two sources compared, and the two statuses that need a person:
 *
 *   qr_extra            the paper claims more than the store's book — corrected
 *                       through the execution entry, never by editing its row
 *   possible_duplicate  an Excel draw one day away from a QR entry — NOT merged
 *                       (ruling Q14-2); check whether it is the same drum
 *
 * Acknowledging records that somebody looked. It changes no quantity.
 */
import { useState } from 'react'
import { Alert, App, Button, Input, Modal, Segmented, Space, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { Row } from '../api/client'
import { Table } from '../lib/smartTable'
import { fmtPackBase } from '../lib/units'

const STATUS: Record<string, { color: string; label: string }> = {
  matched: { color: 'green', label: 'Matched' },
  excel_extra: { color: 'blue', label: 'Excel shows more' },
  qr_extra: { color: 'red', label: 'Conflict — paper claims more' },
  awaiting_excel: { color: 'default', label: 'Awaiting Excel' },
  possible_duplicate: { color: 'orange', label: 'Possible duplicate (±1 day)' },
  over_ledger: { color: 'red', label: 'Counted twice — fix at source' },
}

export default function Reconciliation({ siteId }: { siteId?: string }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [filter, setFilter] = useState<string>('attention')
  const [ack, setAck] = useState<Row | null>(null)
  const [note, setNote] = useState('')
  const { data, isFetching } = useQuery({
    queryKey: ['/sme/actuals/reconciliation', siteId],
    queryFn: async () => (await api.get('/sme/actuals/reconciliation',
      { params: siteId ? { site_id: siteId } : {} })).data as { items: Row[]; needs_attention: number },
  })
  const save = useMutation({
    mutationFn: async () => (await api.post(`/sme/actuals/reconciliation/${ack!.id}/acknowledge`, { note })).data,
    onSuccess: () => {
      message.success('Acknowledged')
      setAck(null); setNote('')
      qc.invalidateQueries({ queryKey: ['/sme/actuals/reconciliation'] })
    },
    onError: (e: unknown) => message.error(
      (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Failed'),
  })
  const items = (data?.items ?? []).filter((r) => filter === 'all'
    || (filter === 'attention' && ['qr_extra', 'over_ledger', 'possible_duplicate'].includes(String(r.status))
        && !r.acknowledged_by))
  const cols: ColumnsType<Row> = [
    { title: 'Date', dataIndex: 'Work_Date', width: 110 },
    { title: 'Equipment', dataIndex: 'Equipment_Tag_No', width: 170 },
    { title: 'SAP', dataIndex: 'SAP_Code', width: 90 },
    { title: 'QR (paper)', dataIndex: 'QR_Qty', align: 'right', width: 150,
      render: (v, r) => fmtPackBase(r.SAP_Code, v) ?? String(v) },
    { title: 'Excel (book)', dataIndex: 'Excel_Qty', align: 'right', width: 150,
      render: (v, r) => fmtPackBase(r.SAP_Code, v) ?? String(v) },
    { title: 'Ledger holds', dataIndex: 'Ledger_Qty', align: 'right', width: 150,
      render: (v, r) => fmtPackBase(r.SAP_Code, v) ?? String(v) },
    { title: 'Status', dataIndex: 'status', width: 230,
      render: (v) => <Tag color={STATUS[String(v)]?.color}>{STATUS[String(v)]?.label ?? String(v)}</Tag> },
    { title: 'Entries', dataIndex: 'entry_ids', width: 100 },
    { title: '', key: 'ack', width: 150,
      render: (_, r) => (['qr_extra', 'over_ledger', 'possible_duplicate'].includes(String(r.status))
        ? (r.acknowledged_by
          ? <Typography.Text type="secondary">✓ {String(r.acknowledged_by)}</Typography.Text>
          : <Button size="small" onClick={() => setAck(r)}>Acknowledge…</Button>)
        : null) },
  ]
  return (
    <div>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        title="The ledger holds ONE quantity per day · equipment · material — never the paper plus the book."
        description="When a QR form and the Excel log both record the same drums, the larger figure stands. 'Excel shows more' is posted once as a difference row attached to the entry. A conflict is fixed on the execution entry; a possible duplicate is checked against the workbook." />
      <Space style={{ marginBottom: 12 }}>
        <Segmented value={filter} onChange={(v) => setFilter(String(v))} options={[
          { value: 'attention', label: `Needs attention (${data?.needs_attention ?? 0})` },
          { value: 'all', label: 'All buckets' },
        ]} />
      </Space>
      <Table rowKey="id" size="small" loading={isFetching} dataSource={items} columns={cols}
        pagination={{ pageSize: 50 }} scroll={{ x: 1300 }} />
      <Modal open={!!ack} title="Acknowledge" onCancel={() => setAck(null)}
        okText="Acknowledge" okButtonProps={{ disabled: note.trim().length < 3, loading: save.isPending }}
        onOk={() => save.mutate()} destroyOnHidden>
        <Typography.Paragraph>
          This records that you looked. It changes <strong>no quantity</strong> — fix a conflict on
          the execution entry, and a duplicate in the workbook.
        </Typography.Paragraph>
        <Input.TextArea rows={3} value={note} onChange={(e) => setNote(e.target.value)}
          placeholder="What you checked and why you are content" />
      </Modal>
    </div>
  )
}
