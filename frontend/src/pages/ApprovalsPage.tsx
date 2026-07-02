import { useState } from 'react'
import { App, Badge, Button, Popconfirm, Select, Space, Table, Tabs, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useHodCounts, useHodDecision, useHodPending, useSites } from '../api/hooks'
import type { Row } from '../api/client'
import { buildColumns } from '../lib/columns'

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

function PendingKind({ kind, siteId }: { kind: string; siteId?: string }) {
  const { message } = App.useApp()
  const { data: rows, isFetching } = useHodPending(kind, siteId)
  const decision = useHodDecision()

  const act = async (id: number, action: 'approve' | 'reject') => {
    try {
      const res = await decision.mutateAsync({ kind, id, action, reason: 'rejected by HOD' })
      if (action === 'approve') {
        const bits = [res.posted, res.pr_status, res.warning].filter(Boolean).join(' · ')
        message.success(`Approved${bits ? ` — ${bits}` : ''}`)
      } else {
        message.success('Rejected')
      }
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = [
    ...buildColumns(rows ?? []),
    {
      title: 'Action',
      key: '__act',
      fixed: 'right',
      width: 180,
      render: (_: unknown, r: Row) => (
        <Space>
          <Popconfirm title="Approve → commit to ledger?" onConfirm={() => act(Number(r.id), 'approve')}>
            <Button size="small" type="primary">Approve</Button>
          </Popconfirm>
          <Popconfirm title="Reject this item?" onConfirm={() => act(Number(r.id), 'reject')}>
            <Button size="small" danger>Reject</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <Table
      size="small"
      loading={isFetching}
      columns={columns}
      dataSource={rows ?? []}
      rowKey={(r) => String(r.id)}
      scroll={{ x: 'max-content' }}
      pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `${t} pending` }}
    />
  )
}

export default function ApprovalsPage() {
  const { data: sites } = useSites()
  const [siteId, setSiteId] = useState<string | undefined>(undefined)
  const { data: counts } = useHodCounts(siteId)

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Approvals (EOD Commit)
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Review staged submissions. Approve commits to the ledger (FEFO, lot, audit);
        reject leaves stock untouched.
      </Typography.Paragraph>

      <Select
        allowClear
        placeholder="All sites"
        style={{ width: 180, marginBottom: 12 }}
        value={siteId}
        onChange={setSiteId}
        options={(sites ?? []).map((s) => ({ value: s, label: s }))}
      />

      <Tabs
        items={KINDS.map((k) => ({
          key: k.key,
          label: (
            <Badge count={counts?.[k.key] ?? 0} size="small" offset={[10, -2]}>
              <span style={{ paddingRight: 6 }}>{k.label}</span>
            </Badge>
          ),
          children: <PendingKind kind={k.key} siteId={siteId} />,
        }))}
      />
    </div>
  )
}
