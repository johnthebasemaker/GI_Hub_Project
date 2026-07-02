import { useState } from 'react'
import { App, Button, Popconfirm, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useHodPrs, useSites, useSubmitPr } from '../api/hooks'
import type { Row } from '../api/client'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

const STATUS_COLOR: Record<string, string> = {
  site_draft: 'default',
  submitted: 'blue',
  in_po: 'green',
}

export default function HodPrsPage() {
  const { message } = App.useApp()
  const { data: sites } = useSites()
  const [siteId, setSiteId] = useState<string | undefined>(undefined)
  const { data: rows, isFetching } = useHodPrs(siteId)
  const submit = useSubmitPr()

  const doSubmit = async (r: Row) => {
    try {
      const res = await submit.mutateAsync({ pr: String(r.PR_Number), site: String(r.Site_ID) })
      message.success(`Submitted ${res.lines} line(s) of PR ${r.PR_Number} to Logistics`)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = [
    { title: 'PR Number', dataIndex: 'PR_Number', key: 'PR_Number' },
    { title: 'Site', dataIndex: 'Site_ID', key: 'Site_ID' },
    { title: 'Lines', dataIndex: 'line_count', key: 'line_count', align: 'right' },
    { title: 'Total Qty', dataIndex: 'total_qty', key: 'total_qty', align: 'right', render: (v) => Number(v) },
    {
      title: 'Logistics status',
      dataIndex: 'logistics_status',
      key: 'logistics_status',
      render: (v: string) => <Tag color={STATUS_COLOR[v] ?? 'default'}>{v}</Tag>,
    },
    {
      title: 'Action',
      key: '__act',
      render: (_: unknown, r: Row) =>
        r.logistics_status === 'in_po' ? (
          <Typography.Text type="secondary">in PO</Typography.Text>
        ) : (
          <Popconfirm title="Submit this PR to Logistics?" onConfirm={() => doSubmit(r)}>
            <Button size="small" type="primary">Submit to Logistics</Button>
          </Popconfirm>
        ),
    },
  ]

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Purchase Requests
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Submit a site PR to Logistics for PO issuance.
      </Typography.Paragraph>
      <Space style={{ marginBottom: 12 }}>
        <Select
          allowClear
          placeholder="All sites"
          style={{ width: 180 }}
          value={siteId}
          onChange={setSiteId}
          options={(sites ?? []).map((s) => ({ value: s, label: s }))}
        />
      </Space>
      <Table
        size="small"
        loading={isFetching}
        columns={columns}
        dataSource={rows ?? []}
        rowKey={(r) => `${r.PR_Number}-${r.Site_ID}`}
        pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `${t} PRs` }}
      />
    </div>
  )
}
