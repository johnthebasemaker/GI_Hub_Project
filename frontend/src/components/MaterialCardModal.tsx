import { Alert, Descriptions, Modal, Skeleton, Space, Statistic, Tag, Typography } from 'antd'
import { useQuery } from '@tanstack/react-query'
import {
  Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api } from '../api/client'

interface CardData {
  sap_code: string; description: string; material_code: string | null
  category: string; uom: string; scope: string | null; current_stock: number
  series: { date: string; received: number; consumed: number }[]
  totals: { received_30d: number; consumed_30d: number }
}

// Two fixed categorical hues (received/consumed) — CVD-separated pair, text
// stays in ink tokens; the colored mark alone carries series identity.
const C_RECEIVED = '#4C78DB'
const C_CONSUMED = '#E8894A'

/**
 * Scan-to-dashboard Material Card (QR ecosystem). Opened by the header QR
 * scanner (or anywhere a SAP code is clicked). Data comes role-scoped from
 * GET /stock/material-card: SK / supervisor / warehouse / HOD see ONLY their
 * site; admin & logistics see the global picture — the scope chip says which.
 */
export default function MaterialCardModal({ sap, open, onClose }:
  { sap: string | null; open: boolean; onClose: () => void }) {
  const q = useQuery({
    queryKey: ['/stock/material-card', sap],
    enabled: open && !!sap,
    retry: false,
    queryFn: async () =>
      (await api.get('/stock/material-card', { params: { sap } })).data as CardData,
  })
  const d = q.data

  return (
    <Modal open={open} onCancel={onClose} footer={null} width={720}
      title={<Space>
        <span>Material dashboard</span>
        {d && (d.scope
          ? <Tag color="blue">Your site: {d.scope}</Tag>
          : <Tag color="gold">All sites (global)</Tag>)}
      </Space>}>
      {q.isFetching && <Skeleton active paragraph={{ rows: 6 }} />}
      {q.isError && (
        <Alert type="warning" showIcon
          title={(q.error as { response?: { data?: { detail?: string } } })
            ?.response?.data?.detail ?? 'No inventory item matches this code.'} />
      )}
      {d && !q.isFetching && (
        <>
          <Space align="start" style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
            <div>
              <Typography.Title level={4} style={{ margin: 0 }}>{d.description || d.sap_code}</Typography.Title>
              <Typography.Text type="secondary">
                SAP {d.sap_code}{d.material_code ? ` · MAT ${d.material_code}` : ''}
                {d.category ? ` · ${d.category}` : ''}
              </Typography.Text>
            </div>
            <Statistic title={`Current stock${d.scope ? ` @ ${d.scope}` : ' (all sites)'}`}
              value={d.current_stock} suffix={d.uom} precision={Number.isInteger(d.current_stock) ? 0 : 2} />
          </Space>
          <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4 }}>
            Last 30 days — received vs consumed
          </Typography.Text>
          <div style={{ width: '100%', height: 260 }}>
            <ResponsiveContainer>
              <BarChart data={d.series} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeOpacity={0.15} vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} tickLine={false}
                  tickFormatter={(v: string) => v.slice(5)} minTickGap={24} />
                <YAxis tick={{ fontSize: 11 }} tickLine={false} axisLine={false} width={42} />
                <Tooltip cursor={{ fillOpacity: 0.06 }}
                  contentStyle={{ borderRadius: 8 }} labelStyle={{ fontWeight: 600 }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="received" name="Received" fill={C_RECEIVED}
                  radius={[3, 3, 0, 0]} maxBarSize={14} />
                <Bar dataKey="consumed" name="Consumed" fill={C_CONSUMED}
                  radius={[3, 3, 0, 0]} maxBarSize={14} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <Descriptions size="small" column={2} style={{ marginTop: 12 }}
            items={[
              { key: 'r', label: 'Received (30d)', children: `${d.totals.received_30d} ${d.uom}` },
              { key: 'c', label: 'Consumed (30d)', children: `${d.totals.consumed_30d} ${d.uom}` },
            ]} />
        </>
      )}
    </Modal>
  )
}
