import { useEffect, useMemo, useState } from 'react'
import {
  Alert, App, Badge, Button, Form, Input, InputNumber, Modal, Popconfirm, Select,
  Space, Table, Tabs, Typography,
} from 'antd'
import { EditOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import {
  useHodBulkApprove, useHodCounts, useHodDecision, useHodEditPending,
  useHodPending, useHodPreflight, useSites,
} from '../api/hooks'
import type { Row } from '../api/client'
import { buildColumns } from '../lib/columns'
import SubmissionInsight from '../components/SubmissionInsight'
import DnApprovalQueue from '../components/DnApprovalQueue'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

const KINDS = [
  { key: 'receipts', label: 'Receipts' },
  { key: 'issues', label: 'Issues' },
  { key: 'returns', label: 'Returns' },
  { key: 'adjustments', label: 'Adjustments' },
]

// Editable staged fields per kind (mirrors the backend whitelist).
const EDIT_FIELDS: Record<string, { name: string; numeric?: boolean }[]> = {
  receipts: [{ name: 'Quantity', numeric: true }, { name: 'Lot_Number' }, { name: 'Expiry_Date' }, { name: 'Supplier' }, { name: 'Remarks' }],
  issues: [{ name: 'Quantity', numeric: true }, { name: 'Lot_Number' }, { name: 'Work_Type' }, { name: 'Issued_To' }, { name: 'Remarks' }],
  returns: [{ name: 'Quantity', numeric: true }, { name: 'Return_Reason' }, { name: 'Lot_Number' }],
  adjustments: [{ name: 'counted_qty', numeric: true }, { name: 'notes' }],
}

function EditModal({ kind, row, onClose }: { kind: string; row: Row | null; onClose: () => void }) {
  const { message } = App.useApp()
  const edit = useHodEditPending()
  const [form] = Form.useForm()
  const fields = EDIT_FIELDS[kind] ?? []

  const save = async () => {
    const values = await form.validateFields()
    // Only send fields the HOD actually changed.
    const changed: Record<string, unknown> = {}
    for (const f of fields) {
      if (values[f.name] !== undefined && values[f.name] !== row?.[f.name]) changed[f.name] = values[f.name]
    }
    if (!Object.keys(changed).length) return onClose()
    try {
      await edit.mutateAsync({ kind, id: Number(row!.id), fields: changed })
      message.success('Staged entry updated')
      onClose()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  return (
    <Modal
      title={`Edit staged ${kind.slice(0, -1)} #${row?.id ?? ''}`}
      open={!!row}
      onOk={save}
      onCancel={onClose}
      confirmLoading={edit.isPending}
      okText="Save changes"
      destroyOnHidden
    >
      <Form form={form} layout="vertical" initialValues={row ?? {}} preserve={false}>
        {fields.map((f) => (
          <Form.Item key={f.name} name={f.name} label={f.name.replace(/_/g, ' ')}>
            {f.numeric ? <InputNumber style={{ width: '100%' }} min={0} /> : <Input />}
          </Form.Item>
        ))}
      </Form>
    </Modal>
  )
}

function PendingKind({ kind, siteId }: { kind: string; siteId?: string }) {
  const { message, modal } = App.useApp()
  const { data: rows, isFetching } = useHodPending(kind, siteId)
  const decision = useHodDecision()
  const bulk = useHodBulkApprove()
  const [selected, setSelected] = useState<number[]>([])
  const [editing, setEditing] = useState<Row | null>(null)
  // T1 — Submission Intelligence is shown OPEN by default on the Issues tab so
  // the HOD sees the consumption analysis before approving (no click needed).
  // Controlled so the HOD can still collapse a row; re-opens as new rows load.
  const [expandedKeys, setExpandedKeys] = useState<string[]>([])
  const issueIds = useMemo(
    () => (kind === 'issues' ? (rows ?? []).map((r) => String(r.id)) : []),
    [kind, rows],
  )
  useEffect(() => { setExpandedKeys(issueIds) }, [issueIds])

  // Reject prompts for a mandatory reason (H5) — it's recorded on the audit row
  // and shown to the submitter in their rejection notification.
  const [rejectId, setRejectId] = useState<number | null>(null)
  const [rejectReason, setRejectReason] = useState('')

  const approve = async (id: number) => {
    try {
      const res = await decision.mutateAsync({ kind, id, action: 'approve' })
      const bits = [res.posted, res.pr_status, res.warning].filter(Boolean).join(' · ')
      message.success(`Approved${bits ? ` — ${bits}` : ''}`)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const doReject = async () => {
    if (rejectId == null || !rejectReason.trim()) return
    try {
      await decision.mutateAsync({ kind, id: rejectId, action: 'reject', reason: rejectReason.trim() })
      message.success('Rejected — the submitter has been notified with your reason')
      setRejectId(null)
      setRejectReason('')
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const bulkApprove = () => {
    modal.confirm({
      title: `Commit ${selected.length} staged ${kind} to the ledger?`,
      content: 'Each row commits independently — failures are reported per row.',
      okText: 'Commit all',
      onOk: async () => {
        try {
          const res = await bulk.mutateAsync({ kind, ids: selected })
          setSelected([])
          if (res.failed) {
            message.warning(`${res.committed} committed · ${res.failed} failed — see the remaining rows`)
          } else {
            message.success(`${res.committed} committed to the ledger`)
          }
        } catch (e) {
          message.error(errMsg(e))
        }
      },
    })
  }

  const columns: ColumnsType<Row> = [
    ...buildColumns(rows ?? []),
    {
      title: 'Action',
      key: '__act',
      fixed: 'right',
      width: 220,
      render: (_: unknown, r: Row) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => setEditing(r)} />
          <Popconfirm title="Approve → commit to ledger?" onConfirm={() => approve(Number(r.id))}>
            <Button size="small" type="primary">Approve</Button>
          </Popconfirm>
          <Button size="small" danger onClick={() => { setRejectId(Number(r.id)); setRejectReason('') }}>
            Reject
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <div>
      {selected.length > 0 && (
        <Space style={{ marginBottom: 12 }}>
          <Button type="primary" loading={bulk.isPending} onClick={bulkApprove}>
            Approve selected ({selected.length})
          </Button>
          <Button onClick={() => setSelected([])}>Clear selection</Button>
        </Space>
      )}
      {kind === 'issues' && (rows?.length ?? 0) > 0 && (
        <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 8, fontSize: 12 }}>
          🤖 AI consumption analysis is shown under each issue below — review it before approving.
        </Typography.Text>
      )}
      <Table
        size="small"
        loading={isFetching}
        columns={columns}
        dataSource={rows ?? []}
        rowKey={(r) => String(r.id)}
        rowSelection={{
          selectedRowKeys: selected.map(String),
          onChange: (keys) => setSelected(keys.map(Number)),
        }}
        // T1 — Submission Intelligence on staged issues, shown OPEN by default
        // (controlled expandedRowKeys) so the HOD reads the consumption analysis
        // before approving. Still collapsible per row.
        expandable={kind === 'issues' ? {
          expandedRowKeys: expandedKeys,
          onExpandedRowsChange: (keys) => setExpandedKeys(keys as string[]),
          expandedRowRender: (r: Row) => (
            <SubmissionInsight kind="staged-issue" refId={Number(r.id)} />
          ),
        } : undefined}
        scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `${t} pending` }}
      />
      <EditModal kind={kind} row={editing} onClose={() => setEditing(null)} />
      <Modal
        title={`Reject staged ${kind.slice(0, -1)} #${rejectId ?? ''}`}
        open={rejectId != null}
        onOk={doReject}
        onCancel={() => { setRejectId(null); setRejectReason('') }}
        okText="Reject"
        okButtonProps={{ danger: true, disabled: !rejectReason.trim() }}
        confirmLoading={decision.isPending}
        destroyOnHidden
      >
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          A reason is required — it's recorded on the audit trail and sent to the submitter.
        </Typography.Paragraph>
        <Input.TextArea
          rows={3}
          autoFocus
          placeholder="e.g. quantity exceeds the work order; wrong lot; duplicate entry"
          value={rejectReason}
          onChange={(e) => setRejectReason(e.target.value)}
        />
      </Modal>
    </div>
  )
}

export default function ApprovalsPage() {
  const { data: sites } = useSites()
  const [siteId, setSiteId] = useState<string | undefined>(undefined)
  const { data: counts } = useHodCounts(siteId)
  const { data: preflight } = useHodPreflight(siteId)

  const deficits = useMemo(() => preflight ?? [], [preflight])

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Approvals (EOD Commit)
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Review staged submissions. Approve commits to the ledger (FEFO, lot, audit);
        reject leaves stock untouched. Edit a row first if the store keeper's entry needs correcting.
      </Typography.Paragraph>

      {deficits.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={`Pre-flight: ${deficits.length} material(s) would go NEGATIVE if all pending issues were approved`}
          description={
            <Table
              size="small"
              pagination={false}
              rowKey={(r) => `${r.SAP_Code}·${r.Site_ID}`}
              dataSource={deficits}
              columns={[
                { title: 'SAP', dataIndex: 'SAP_Code' },
                { title: 'Description', dataIndex: 'Equipment_Description', ellipsis: true },
                { title: 'Site', dataIndex: 'Site_ID', width: 90 },
                { title: 'Pending qty', dataIndex: 'Pending_Qty', align: 'right' },
                { title: 'Current stock', dataIndex: 'Current_Stock', align: 'right' },
                { title: 'Deficit', dataIndex: 'Deficit', align: 'right',
                  render: (v) => <Typography.Text type="danger">{String(v)}</Typography.Text> },
              ] as ColumnsType<Row>}
            />
          }
        />
      )}

      <Select
        allowClear
        placeholder="All sites"
        style={{ width: 180, marginBottom: 12 }}
        value={siteId}
        onChange={setSiteId}
        options={(sites ?? []).map((s) => ({ value: s, label: s }))}
      />

      <Tabs
        items={[
          ...KINDS.map((k) => ({
            key: k.key,
            label: (
              <Badge count={counts?.[k.key] ?? 0} size="small" offset={[10, -2]}>
                <span style={{ paddingRight: 6 }}>{k.label}</span>
              </Badge>
            ),
            children: <PendingKind kind={k.key} siteId={siteId} />,
          })),
          // Phase 6 — HOD content stage of the DN two-stage approval.
          { key: 'dns', label: 'Delivery Notes', children: <DnApprovalQueue scope="hod" /> },
        ]}
      />
    </div>
  )
}
