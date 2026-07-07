/**
 * frontend/src/pages/OverdueActionsPage.tsx — T2 Admin SLA tracker.
 *
 * Every pending submission older than the 24h SLA, age-sorted (server-side),
 * with the responsible users resolved. Two actions per row:
 *   Notify — dispatches the URGENT nudge notification to every responsible
 *            user (exact T2 template, server-side + audited).
 *   Clear  — dismisses the row from the tracker (sla_dismissals, audited).
 */
import { App, Alert, Button, Popconfirm, Space, Table, Tag, Tooltip, Typography } from 'antd'
import { BellOutlined, CheckOutlined, ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useClearOverdue, useNotifyOverdue, useOverdueActions } from '../api/hooks'
import type { OverdueItem } from '../api/hooks'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

const KIND_COLOR: Record<string, string> = {
  'hod-receipt': 'geekblue', 'hod-issue': 'geekblue', 'hod-return': 'geekblue',
  'hod-adjustment': 'geekblue', 'sk-request': 'gold',
  'wh-assignment': 'purple', 'log-reschedule': 'cyan', 'log-pr': 'cyan',
}

export default function OverdueActionsPage() {
  const { message } = App.useApp()
  const { data, isFetching, refetch } = useOverdueActions(true)
  const clear = useClearOverdue()
  const notifyM = useNotifyOverdue()

  const doNotify = async (r: OverdueItem) => {
    try {
      const res = await notifyM.mutateAsync({ kind: r.kind, refId: r.ref_id })
      message.success(`URGENT nudge sent to ${res.recipients.join(', ')}`)
    } catch (e) { message.error(errMsg(e)) }
  }
  const doClear = async (r: OverdueItem) => {
    try {
      await clear.mutateAsync({ kind: r.kind, refId: r.ref_id })
      message.success('Cleared from the tracker')
    } catch (e) { message.error(errMsg(e)) }
  }

  const columns: ColumnsType<OverdueItem> = [
    {
      title: 'Age', dataIndex: 'age_hours', key: 'age', width: 90,
      sorter: (a, b) => a.age_hours - b.age_hours, defaultSortOrder: 'descend',
      render: (v: number) => (
        <b style={{ color: v >= 72 ? '#DC2626' : v >= 48 ? '#EF4444' : '#F59E0B' }}>
          {v >= 48 ? `${Math.floor(v / 24)}d ${Math.round(v % 24)}h` : `${v.toFixed(1)}h`}
        </b>
      ),
    },
    {
      title: 'Queue', dataIndex: 'label', key: 'label',
      render: (v: string, r) => <Tag color={KIND_COLOR[r.kind] ?? 'default'}>{v}</Tag>,
    },
    { title: 'Submission', dataIndex: 'summary', key: 'summary', ellipsis: true },
    {
      title: 'Scope', key: 'scope', width: 130,
      render: (_, r) => r.site || r.warehouse
        || <Typography.Text type="secondary">global</Typography.Text>,
    },
    {
      title: 'Responsible', dataIndex: 'responsible', key: 'resp',
      render: (v: string[], r) => v.length
        ? v.map((u) => <Tag key={u}>{u}</Tag>)
        : <Tooltip title={`No ${r.role} user matches this scope`}>
            <Tag color="red">unassigned {r.role}</Tag>
          </Tooltip>,
    },
    { title: 'Pending since', dataIndex: 'pending_since', key: 'since', width: 150 },
    {
      title: 'Actions', key: 'act', width: 190,
      render: (_, r) => (
        <Space size="small">
          <Button size="small" type="primary" danger icon={<BellOutlined />}
            loading={notifyM.isPending} disabled={!r.responsible.length}
            onClick={() => doNotify(r)}>Notify</Button>
          <Popconfirm title="Clear this item from the tracker?"
            description="It will not resurface even if it stays pending."
            onConfirm={() => doClear(r)}>
            <Button size="small" icon={<CheckOutlined />}>Clear</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Overdue Actions</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Submissions sitting without action for more than {data?.hours ?? 24} hours,
        with the users responsible for the next step. <b>Notify</b> sends the URGENT
        nudge to their portal bell; <b>Clear</b> dismisses the row (audited).
      </Typography.Paragraph>
      <Space style={{ marginBottom: 12 }}>
        <Button icon={<ReloadOutlined />} onClick={() => refetch()} loading={isFetching}>
          Refresh
        </Button>
        <Tag color={data?.count ? 'red' : 'green'}>
          {data?.count ?? 0} overdue
        </Tag>
      </Space>
      {data && data.count === 0 ? (
        <Alert type="success" showIcon title="No overdue actions"
          description="Every pending submission is inside the 24-hour SLA. 🎉" />
      ) : (
        <Table
          size="small"
          loading={isFetching}
          columns={columns}
          dataSource={data?.items ?? []}
          rowKey={(r) => `${r.kind}|${r.ref_id}`}
          pagination={{ pageSize: 20, showTotal: (t) => `${t} overdue` }}
          scroll={{ x: 'max-content' }}
        />
      )}
    </div>
  )
}
