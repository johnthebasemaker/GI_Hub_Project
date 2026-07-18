import { useState } from 'react'
import { Button, Input, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ReloadOutlined } from '@ant-design/icons'
import { useAuditLog, useAuditMeta } from '../api/hooks'
import type { Row } from '../api/client'

const PAGE = 50

export default function AuditLogPage() {
  const [username, setUsername] = useState('')
  const [actionType, setActionType] = useState<string | undefined>()
  const [targetTable, setTargetTable] = useState<string | undefined>()
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)

  const { data: meta } = useAuditMeta()
  const params = {
    username: username || undefined,
    action_type: actionType,
    target_table: targetTable,
    q: q || undefined,
    limit: PAGE,
    offset: (page - 1) * PAGE,
  }
  const { data, isFetching, refetch } = useAuditLog(params)

  const resetFilters = () => {
    setUsername(''); setActionType(undefined); setTargetTable(undefined); setQ(''); setPage(1)
  }

  const columns: ColumnsType<Row> = [
    { title: 'When', dataIndex: 'timestamp', key: 'timestamp', width: 190, render: (v) => String(v ?? '') },
    { title: 'User', dataIndex: 'username', key: 'username', width: 130, render: (v) => v || '—' },
    {
      title: 'Action', dataIndex: 'action_type', key: 'action_type', width: 200,
      render: (v: string) => <Tag color={/FAIL|REJECT|DELETE/.test(v) ? 'red' : 'blue'}>{v}</Tag>,
    },
    { title: 'Table', dataIndex: 'target_table', key: 'target_table', width: 150, render: (v) => v || '—' },
    { title: 'Details', dataIndex: 'details', key: 'details', ellipsis: true, render: (v) => v || '—' },
  ]

  const onFilterChange = (fn: () => void) => { fn(); setPage(1) }

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Audit Log</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Immutable system activity — logins, approvals, ledger commits, procurement, and admin actions.
      </Typography.Paragraph>
      <Space style={{ marginBottom: 12 }} wrap>
        <Input.Search allowClear placeholder="Username" style={{ width: 170 }}
          value={username} onChange={(e) => onFilterChange(() => setUsername(e.target.value))} />
        <Select allowClear placeholder="Action" style={{ width: 220 }} value={actionType} showSearch
          optionFilterProp="label"
          onChange={(v) => onFilterChange(() => setActionType(v))}
          options={(meta?.action_types ?? []).map((a) => ({ value: a, label: a }))} />
        <Select allowClear placeholder="Table" style={{ width: 190 }} value={targetTable} showSearch
          optionFilterProp="label"
          onChange={(v) => onFilterChange(() => setTargetTable(v))}
          options={(meta?.target_tables ?? []).map((t) => ({ value: t, label: t }))} />
        <Input.Search allowClear placeholder="Search details" style={{ width: 200 }}
          value={q} onChange={(e) => onFilterChange(() => setQ(e.target.value))} />
        <Button onClick={resetFilters}>Clear</Button>
        <Button icon={<ReloadOutlined />} onClick={() => refetch()}>Refresh</Button>
      </Space>
      <Table sticky={{ offsetHeader: 64 }}
        size="small"
        loading={isFetching}
        columns={columns}
        dataSource={data?.items ?? []}
        rowKey={(r) => String(r.id)}
        pagination={{
          current: page,
          pageSize: PAGE,
          total: data?.total ?? 0,
          showSizeChanger: false,
          onChange: setPage,
          showTotal: (t) => `${t} events`,
        }}
      />
    </div>
  )
}
