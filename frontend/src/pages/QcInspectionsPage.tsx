/**
 * QcInspectionsPage — the quality inspector's queue, and everyone else's
 * window into why a Store Keeper's issue was refused.
 *
 * The page is readable by six roles and decidable by one. That asymmetry is
 * deliberate: an SK who is blocked at the issue form needs to see the row
 * that blocked them (and who has it), an HOD needs to chase it, and an
 * Auditor needs to audit it — but only a `qc` may approve. The Decide button
 * is therefore gated on the role rather than the page being hidden, which is
 * also why the nav entry is NOT marked `writes`.
 *
 * Scoping is entirely server-side. This page never sends a site or a
 * warehouse: `auth.qc_scope()` decides what the caller may read, so a site
 * inspector cannot reach another site's queue by editing a query string.
 */
import { useState } from 'react'
import {
  Alert, App, Button, Card, Descriptions, Empty, InputNumber, Input, Modal,
  Segmented, Space, Tag, Typography,
} from 'antd'
import { ExperimentOutlined, FileSearchOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { Table } from '../lib/smartTable'
import { useQcDecide, useQcInspections } from '../api/hooks'
import type { Row } from '../api/client'
import { openAuthed } from '../api/authedFile'
import { useAuth } from '../auth/AuthContext'
import { useReadOnly } from '../auth/useReadOnly'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

const STATUS_TAG: Record<string, { color: string; label: string }> = {
  pending: { color: 'gold', label: 'Awaiting inspection' },
  approved: { color: 'green', label: 'Approved' },
  partially_approved: { color: 'orange', label: 'Partly approved' },
  rejected: { color: 'red', label: 'Rejected' },
}

const num = (v: unknown) => Number(v ?? 0)

/**
 * The decision. One modal for approve / partly approve / reject, because they
 * differ only in the quantity — and modelling them as three buttons is how
 * two of the three end up forgetting to ask for a reason.
 */
function DecideModal({ row, onClose }: { row: Row | null; onClose: () => void }) {
  const { message } = App.useApp()
  const decide = useQcDecide()
  const submitted = num(row?.submitted_qty)
  const [qty, setQty] = useState<number>(submitted)
  const [reason, setReason] = useState('')

  const rejecting = submitted - qty > 1e-9
  const outcome = qty <= 1e-9 ? 'rejected'
    : qty >= submitted - 1e-9 ? 'approved' : 'partially_approved'

  const submit = async () => {
    try {
      const res = await decide.mutateAsync({
        id: Number(row!.id), approved_qty: qty, reason: reason.trim() || undefined,
      })
      // The Return No is the whole handoff, so it is announced rather than
      // left to be discovered in the grid — the inspector usually reads it
      // straight out to the store keeper standing next to them.
      if (res.return_no) {
        message.warning({
          content: `Return ${String(res.return_no)} raised for ${num(res.rejected_qty)} `
            + 'rejected — give this number to the store keeper.',
          duration: 8,
        })
      } else {
        message.success(
          `${STATUS_TAG[String(res.status)]?.label ?? res.status} — `
          + `${num(res.approved_qty)} cleared for issue`)
      }
      onClose()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  return (
    <Modal
      open={!!row}
      title={`Inspect ${row?.SAP_Code ?? ''}`}
      onCancel={onClose}
      onOk={submit}
      okText="Record decision"
      confirmLoading={decide.isPending}
      okButtonProps={{ disabled: rejecting && !reason.trim() }}
      afterOpenChange={(open) => { if (open) { setQty(submitted); setReason('') } }}
    >
      <Descriptions size="small" column={1} style={{ marginBottom: 16 }}>
        {/* The NAME leads. An inspector was previously shown '1032' and asked
            to approve it — the SAP code is our identifier, not the thing on
            the drum in front of them, so deciding quality from it meant a walk
            back to the shelf. The codes stay, underneath, because they are
            what everything else in the system is keyed on. */}
        <Descriptions.Item label="Material">
          <Typography.Text strong>
            {String(row?.Material_Name ?? '(name not in the material master)')}
          </Typography.Text>
          <br />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            SAP {String(row?.SAP_Code ?? '')}
            {row?.Material_Code ? ` · ${row.Material_Code}` : ''}
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="Lot">{String(row?.Lot_Number ?? '—')}</Descriptions.Item>
        <Descriptions.Item label="Where">
          {String(row?.Site_ID || row?.Warehouse_ID || '—')}
        </Descriptions.Item>
        <Descriptions.Item label="Submitted">{submitted}</Descriptions.Item>
        {/* Openable, not merely acknowledged. This used to render
            "certificate #41" — which tells an inspector a certificate exists
            and gives them no way to read it, so the approval was made against
            a document nobody had opened. */}
        <Descriptions.Item label="Certificate">
          {row?.mtc_document_id
            ? (
              <Space size={4} wrap>
                <Tag color="green">{String(row.mtc_number ?? `#${row.mtc_document_id}`)}</Tag>
                <Button
                  size="small" type="link" icon={<FileSearchOutlined />}
                  style={{ padding: '0 4px' }}
                  onClick={() => void openAuthed(`/qc/inspections/${row.id}/certificate`, { inline: 1 })
                    .catch((e: Error) => message.error(e.message))}
                >
                  {String(row.mtc_file_name ?? 'Open certificate')}
                </Button>
              </Space>
            )
            : <Tag>not linked — inspect the physical paperwork</Tag>}
        </Descriptions.Item>
      </Descriptions>

      <Space orientation="vertical" style={{ width: '100%' }} size="middle">
        <div>
          <Typography.Text strong>Quantity you are approving</Typography.Text>
          <InputNumber
            style={{ width: '100%', marginTop: 4 }}
            min={0} max={submitted} value={qty}
            onChange={(v) => setQty(Number(v ?? 0))}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            The rest stays in stock as unusable — it is never auto-returned to
            the vendor, and it simply cannot be issued.
          </Typography.Text>
        </div>
        {rejecting && (
          <div>
            <Typography.Text strong>
              Reason for rejecting {Number((submitted - qty).toFixed(6))}
            </Typography.Text>
            <Input.TextArea
              rows={3} value={reason} style={{ marginTop: 4 }}
              onChange={(e) => setReason(e.target.value)}
              placeholder="What is wrong with the material or its certificate?"
            />
          </div>
        )}
        <Alert
          type={outcome === 'rejected' ? 'error' : outcome === 'approved' ? 'success' : 'warning'}
          showIcon
          title={`This records: ${STATUS_TAG[outcome].label}`}
          description={
            outcome === 'rejected'
              ? 'Nothing from this lot may be issued to the field.'
              : `${qty} unit(s) become issuable; the Store Keeper is notified.`}
        />
      </Space>
    </Modal>
  )
}

export default function QcInspectionsPage() {
  const { message } = App.useApp()
  const { user } = useAuth()
  const { readOnly } = useReadOnly()
  const [status, setStatus] = useState<string>('pending')
  const [decide, setDecide] = useState<Row | null>(null)
  const { data, isLoading } = useQcInspections(
    status === 'all' ? {} : { status })

  const isInspector = user?.role === 'qc'
  const rows = data ?? []
  // Not memoised: `rows` is a fresh array on every render, so a useMemo keyed
  // on it recomputes every time anyway — it would only add the appearance of
  // caching. This is a filter over at most 200 rows.
  const pendingCount = rows.filter((r) => r.status === 'pending').length

  const columns: ColumnsType<Row> = [
    {
      title: 'Status', dataIndex: 'status', key: 'status', width: 170,
      render: (v: string) => {
        const t = STATUS_TAG[v] ?? { color: 'default', label: v }
        return <Tag color={t.color}>{t.label}</Tag>
      },
    },
    // Name first, codes underneath — the queue is read by someone deciding
    // what to walk over and look at, and '1032' does not tell them.
    {
      title: 'Material', key: 'material', width: 260, ellipsis: true,
      render: (_, r) => (
        <div>
          <div>{String(r.Material_Name ?? '—')}</div>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            {String(r.SAP_Code ?? '')}
            {r.Material_Code ? ` · ${r.Material_Code}` : ''}
          </Typography.Text>
        </div>
      ),
    },
    { title: 'Lot', dataIndex: 'Lot_Number', key: 'lot', width: 120,
      render: (v) => String(v ?? '—') },
    {
      title: 'Where', key: 'place', width: 120,
      render: (_, r) => String(r.Site_ID || r.Warehouse_ID || '—'),
    },
    { title: 'Submitted', dataIndex: 'submitted_qty', key: 'sub', align: 'right',
      width: 100, render: num },
    { title: 'Approved', dataIndex: 'approved_qty', key: 'app', align: 'right',
      width: 100, render: num },
    { title: 'Rejected', dataIndex: 'rejected_qty', key: 'rej', align: 'right',
      width: 100, render: num },
    // Visible to every role that reads this page, which is the point: the SK
    // needs it to raise the return, and the HOD needs it to recognise the
    // return when it arrives for approval.
    {
      title: 'Return No', dataIndex: 'return_no', key: 'ret', width: 170,
      render: (v, r) => (v
        ? (
          <Tag color={r.return_posted_id ? 'default' : 'volcano'}>
            {String(v)}{r.return_posted_id ? ' · posted' : ''}
          </Tag>
        )
        : <Typography.Text type="secondary" style={{ fontSize: 12 }}>—</Typography.Text>),
    },
    { title: 'Reason', dataIndex: 'decision_reason', key: 'why', ellipsis: true,
      render: (v) => String(v ?? '') },
    { title: 'Inspector', dataIndex: 'inspected_by', key: 'by', width: 130,
      render: (v) => String(v ?? '—') },
    // Openable from the queue as well as from the modal: an inspector often
    // wants to read the certificate BEFORE deciding which row to open.
    {
      title: 'Certificate', key: '__mtc', width: 150,
      render: (_, r) => (r.mtc_document_id
        ? (
          <Button size="small" type="link" icon={<FileSearchOutlined />}
            style={{ padding: 0 }}
            onClick={() => void openAuthed(`/qc/inspections/${r.id}/certificate`, { inline: 1 })
              .catch((e: Error) => message.error(e.message))}>
            {String(r.mtc_number ?? 'open')}
          </Button>
        )
        : <Typography.Text type="secondary" style={{ fontSize: 12 }}>none</Typography.Text>),
    },
    {
      title: 'Action', key: '__act', width: 120, fixed: 'right',
      render: (_, r) => (
        <Button
          type="primary" size="small"
          // Visible to everyone, usable by the inspector. Six roles read this
          // page — an SK who was just refused at the issue form needs to see
          // the row and who holds it, not a 404.
          disabled={!isInspector || readOnly || r.status !== 'pending'}
          onClick={() => setDecide(r)}
        >
          Inspect
        </Button>
      ),
    },
  ]

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <div>
        <Typography.Title level={3} style={{ marginBottom: 4 }}>
          <ExperimentOutlined /> Quality Inspections
        </Typography.Title>
        <Typography.Text type="secondary">
          Surface-Shield material is inspected where it lands — at the warehouse
          when it is booked in, and at the site when a delivery note is
          received. Material may travel uninspected; it may not be{' '}
          <strong>issued</strong> until a quantity here is approved.
        </Typography.Text>
      </div>

      {!isInspector && (
        <Alert
          type="info" showIcon
          title="You are viewing this queue, not deciding it"
          description="Approving and rejecting is restricted to Quality Control accounts."
        />
      )}
      {isInspector && pendingCount > 0 && (
        <Alert
          type="warning" showIcon
          title={`${pendingCount} lot(s) waiting on you`}
          description="Nothing in this list can be issued to the field until it is inspected."
        />
      )}

      <Card
        size="small"
        title={
          <Segmented
            value={status}
            onChange={(v) => setStatus(String(v))}
            options={[
              { label: 'Awaiting inspection', value: 'pending' },
              { label: 'Approved', value: 'approved' },
              { label: 'Partly approved', value: 'partially_approved' },
              { label: 'Rejected', value: 'rejected' },
              { label: 'All', value: 'all' },
            ]}
          />
        }
      >
        <Table
          sticky={{ offsetHeader: 64 }}
          size="small"
          loading={isLoading}
          columns={columns}
          dataSource={rows}
          rowKey={(r) => String(r.id)}
          scroll={{ x: 'max-content' }}
          locale={{
            emptyText: (
              <Empty description={
                status === 'pending'
                  ? 'Nothing is waiting for inspection.'
                  : 'No inspections in this state.'} />
            ),
          }}
        />
      </Card>

      <DecideModal row={decide} onClose={() => setDecide(null)} />
    </Space>
  )
}
