/**
 * frontend/src/components/DnApprovalQueue.tsx — Phase 6 DN two-stage approval.
 * One shared queue for both stages: `scope="logistics"` vets the delivery /
 * logistics aspect (pending_logistics), `scope="hod"` vets the DN content
 * (pending_hod). Approve advances the DN; reject (with a reason) sends it back.
 */
import { useState } from 'react'
import { App, Button, Input, Modal, Popconfirm, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useDnDecide, useDnQueue, useScopedDnItems } from '../api/hooks'
import type { Row } from '../api/client'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

function DnLines({ scope, dn }: { scope: 'logistics' | 'hod'; dn: string }) {
  const { data, isFetching } = useScopedDnItems(scope, dn)
  const cols: ColumnsType<Row> = [
    { title: 'Material', dataIndex: 'Material_Code', render: (v) => v ?? '—' },
    { title: 'Description', dataIndex: 'Description', ellipsis: true, render: (v) => v ?? '—' },
    { title: 'Qty', dataIndex: 'Qty', align: 'right', render: (v) => Number(v) },
    { title: 'Lot', dataIndex: 'Lot_Number', render: (v) => v ?? '—' },
  ]
  return <Table sticky={{ offsetHeader: 64 }} size="small" loading={isFetching} columns={cols} dataSource={data ?? []}
    rowKey={(r) => String(r.id)} pagination={false} />
}

export default function DnApprovalQueue({ scope }: { scope: 'logistics' | 'hod' }) {
  const { message } = App.useApp()
  const { data: rows, isFetching } = useDnQueue(scope)
  const decide = useDnDecide(scope)
  const [rejectDn, setRejectDn] = useState<string | null>(null)
  const [reason, setReason] = useState('')

  const approve = async (dn: string) => {
    try {
      await decide.mutateAsync({ dn, action: 'approve' })
      message.success(scope === 'logistics' ? 'Approved — sent to HOD' : 'Approved — ready to ship')
    } catch (e) { message.error(errMsg(e)) }
  }
  const doReject = async () => {
    if (!rejectDn) return
    try {
      await decide.mutateAsync({ dn: rejectDn, action: 'reject', reason: reason.trim() || undefined })
      message.success('Rejected — warehouse notified')
      setRejectDn(null); setReason('')
    } catch (e) { message.error(errMsg(e)) }
  }

  const columns: ColumnsType<Row> = [
    { title: 'DN Number', dataIndex: 'DN_Number' },
    { title: 'PO', dataIndex: 'PO_Number', render: (v) => v ?? '—' },
    { title: 'Site', dataIndex: 'Site_ID', render: (v) => v ?? '—' },
    { title: 'Family', dataIndex: 'rl_bl_family', render: (v) => v ?? '—' },
    { title: 'Date', dataIndex: 'DN_Date', render: (v) => v ?? '—' },
    { title: 'Status', dataIndex: 'status', render: (v: string) => <Tag color="gold">{v}</Tag> },
    {
      title: 'Action', key: '__act', width: 190,
      render: (_: unknown, r: Row) => (
        <Space>
          <Popconfirm title={`Approve DN ${r.DN_Number}?`} onConfirm={() => approve(String(r.DN_Number))}>
            <Button size="small" type="primary">Approve</Button>
          </Popconfirm>
          <Button size="small" danger onClick={() => { setRejectDn(String(r.DN_Number)); setReason('') }}>
            Reject
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        {scope === 'logistics'
          ? 'Vet the delivery date / logistics details. Approving forwards the DN to the HOD.'
          : 'Vet the DN content. Approving lets the warehouse ship it.'}
      </Typography.Paragraph>
      <Table sticky={{ offsetHeader: 64 }} size="small" loading={isFetching} columns={columns} dataSource={rows ?? []}
        rowKey={(r) => String(r.DN_Number)}
        expandable={{ expandedRowRender: (r) => <DnLines scope={scope} dn={String(r.DN_Number)} /> }}
        pagination={{ pageSize: 20, showTotal: (t) => `${t} awaiting` }} />
      <Modal open={rejectDn != null} title={`Reject DN ${rejectDn ?? ''}`} onOk={doReject}
        onCancel={() => { setRejectDn(null); setReason('') }} okText="Reject"
        okButtonProps={{ danger: true }} confirmLoading={decide.isPending} destroyOnHidden>
        <Input.TextArea rows={3} placeholder="Reason (sent to the warehouse)"
          value={reason} onChange={(e) => setReason(e.target.value)} />
      </Modal>
    </>
  )
}
