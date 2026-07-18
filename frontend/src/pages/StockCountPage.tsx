import { useMemo, useState } from 'react'
import { App, Button, InputNumber, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useCountSheet, useSubmitCount } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'
import { api } from '../api/client'
import { useQuery } from '@tanstack/react-query'
import type { Row } from '../api/client'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

function useAdjustmentReasons() {
  return useQuery({
    queryKey: ['/entry/adjustment-reasons'],
    queryFn: async () => (await api.get<Record<string, string>>('/entry/adjustment-reasons')).data,
  })
}

// Physical count sheet: enter what you actually counted; rows with a variance
// become staged adjustments for the HOD to approve (legacy Stock Count tab).
export default function StockCountPage() {
  const { message } = App.useApp()
  const { user } = useAuth()
  const site = user?.site_id || undefined
  const { data: items, isFetching, refetch } = useCountSheet()
  const { data: reasons } = useAdjustmentReasons()
  const submit = useSubmitCount()
  const [counted, setCounted] = useState<Record<string, number>>({})
  const [reason, setReason] = useState('cycle_count')

  const changed = useMemo(
    () => (items ?? []).filter((r) => {
      const c = counted[String(r.SAP_Code)]
      return c !== undefined && Math.abs(c - Number(r.System_Qty ?? 0)) > 1e-9
    }),
    [items, counted],
  )

  const doSubmit = async () => {
    if (!site) {
      message.warning('Your account has no site — a count belongs to one site.')
      return
    }
    try {
      const res = await submit.mutateAsync({
        site_id: site,
        reason_code: reason,
        rows: changed.map((r) => ({
          SAP_Code: String(r.SAP_Code),
          counted_qty: counted[String(r.SAP_Code)],
        })),
      })
      message.success(`${res.staged} adjustment(s) staged for HOD approval`)
      setCounted({})
      refetch()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<Row> = [
    { title: 'SAP', dataIndex: 'SAP_Code', width: 90 },
    { title: 'Description', dataIndex: 'Equipment_Description', ellipsis: true },
    { title: 'UOM', dataIndex: 'UOM', width: 70 },
    { title: 'System qty', dataIndex: 'System_Qty', align: 'right', width: 100 },
    {
      title: 'Counted qty', key: '__c', width: 140,
      render: (_: unknown, r: Row) => (
        <InputNumber
          size="small"
          min={0}
          style={{ width: 120 }}
          placeholder={String(r.System_Qty ?? 0)}
          value={counted[String(r.SAP_Code)]}
          onChange={(v) =>
            setCounted((m) => {
              const next = { ...m }
              if (v == null) delete next[String(r.SAP_Code)]
              else next[String(r.SAP_Code)] = v
              return next
            })}
        />
      ),
    },
    {
      title: 'Variance', key: '__v', align: 'right', width: 100,
      render: (_: unknown, r: Row) => {
        const c = counted[String(r.SAP_Code)]
        if (c === undefined) return '—'
        const v = c - Number(r.System_Qty ?? 0)
        if (Math.abs(v) < 1e-9) return <Tag>match</Tag>
        return (
          <Typography.Text type={v < 0 ? 'danger' : 'warning'} strong>
            {v > 0 ? `+${v}` : v}
          </Typography.Text>
        )
      },
    },
  ]

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Stock Count
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Enter the physically counted quantity for anything that differs — matching rows
        can be left blank. Variances stage adjustments for HOD approval; nothing hits the
        ledger until then.
      </Typography.Paragraph>

      <Space style={{ marginBottom: 12 }} wrap>
        <span>Reason:</span>
        <Select
          style={{ width: 240 }}
          value={reason}
          onChange={setReason}
          options={Object.entries(reasons ?? { cycle_count: 'Cycle count correction' })
            .map(([value, label]) => ({ value, label: String(label) }))}
        />
        <Button
          type="primary"
          disabled={!changed.length}
          loading={submit.isPending}
          onClick={doSubmit}
        >
          Stage {changed.length || ''} adjustment{changed.length === 1 ? '' : 's'}
        </Button>
      </Space>

      <Table sticky={{ offsetHeader: 64 }}
        size="small"
        loading={isFetching}
        columns={columns}
        dataSource={items ?? []}
        rowKey={(r) => String(r.SAP_Code)}
        scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 50, showSizeChanger: true, showTotal: (t) => `${t} materials` }}
      />
    </div>
  )
}
