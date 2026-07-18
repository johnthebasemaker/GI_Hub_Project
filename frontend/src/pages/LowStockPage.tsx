import { useState } from 'react'
import { App, Button, Select, Space, Table, Typography } from 'antd'
import { FileAddOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useNavigate } from 'react-router-dom'
import { useAutoDraftPr, useLowStock, useSites } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'
import type { Row } from '../api/client'
import { status } from '../theme/tokens'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

const columns: ColumnsType<Row> = [
  { title: 'SAP', dataIndex: 'SAP_Code', width: 90 },
  { title: 'Description', dataIndex: 'Equipment_Description', ellipsis: true },
  { title: 'Site', dataIndex: 'Site_ID', width: 90 },
  { title: 'UOM', dataIndex: 'UOM', width: 70 },
  { title: 'Min', dataIndex: 'Minimum_Qty', align: 'right', width: 80 },
  { title: 'Current', dataIndex: 'Current_Stock', align: 'right', width: 90,
    render: (v) => <Typography.Text type="danger" strong>{String(v)}</Typography.Text> },
  { title: 'Shortage', dataIndex: 'Shortage', align: 'right', width: 90 },
  { title: 'Daily burn (30d)', dataIndex: 'Daily_Burn', align: 'right', width: 120 },
  { title: 'Days of supply', dataIndex: 'Days_Of_Supply', align: 'right', width: 120,
    render: (v) => (v == null ? '—' : String(v)) },
  { title: 'Suggested reorder', dataIndex: 'Suggested_Reorder', align: 'right', width: 130,
    render: (v) => <span style={{ color: status.low, fontWeight: 600 }}>{String(v)}</span> },
]

export default function LowStockPage() {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const { user } = useAuth()
  const { data: sites } = useSites()
  const scoped = (user?.level ?? 0) < 3
  const [siteId, setSiteId] = useState<string | undefined>(undefined)
  const effectiveSite = scoped ? user?.site_id : siteId
  const { data: items, isFetching } = useLowStock(siteId)
  const draft = useAutoDraftPr()

  const autoDraft = async () => {
    if (!effectiveSite) {
      message.warning('Pick a site first — a PR belongs to one site.')
      return
    }
    try {
      const res = await draft.mutateAsync({ siteId: effectiveSite })
      if (res.created === false) {
        message.info(res.reason ?? 'Nothing below minimum.')
        return
      }
      message.success(`Drafted ${res.pr_number} with ${res.lines} line(s) — review it under Purchase Requests`)
      navigate('/hod/prs')
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Low Stock &amp; Reorder
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Items below their minimum quantity, with 30-day burn rate and a suggested
        reorder quantity (shortage + 30 days of burn).
      </Typography.Paragraph>

      <Space style={{ marginBottom: 12 }} wrap>
        {!scoped && (
          <Select
            allowClear
            placeholder="All sites"
            style={{ width: 180 }}
            value={siteId}
            onChange={setSiteId}
            options={(sites ?? []).map((s) => ({ value: s, label: s }))}
          />
        )}
        <Button
          type="primary"
          icon={<FileAddOutlined />}
          loading={draft.isPending}
          disabled={!items?.length}
          onClick={autoDraft}
        >
          Auto-draft PR from this list
        </Button>
      </Space>

      <Table sticky={{ offsetHeader: 64 }}
        size="small"
        loading={isFetching}
        columns={columns}
        dataSource={items ?? []}
        rowKey={(r) => `${r.SAP_Code}·${r.Site_ID}`}
        scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 20, showTotal: (t) => `${t} below minimum` }}
      />
    </div>
  )
}
