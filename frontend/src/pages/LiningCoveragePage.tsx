import { Alert, Card, Col, Empty, Progress, Row, Select, Space, Statistic, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ExperimentOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'
import { useSites } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'

/**
 * Phase 8-1 — Lining Coverage (predictive material analytics).
 * GET /analytics/lining-coverage runs the read-only SME planning engine with
 * the LIVE ledger stock as the availability pool: how many SQM of rubber /
 * brick lining the site can actually execute right now, which material is
 * the bottleneck, and when each material runs out at the 90-day burn rate.
 */
interface Family {
  family: string; label: string; remaining_sqm: number; achievable_sqm: number
  coverage_pct: number; systems: string[]; bottlenecks: string[]
}
interface SystemRow {
  System_Code: string; System_Name: string; families: string[]
  Remaining_SQM: number; Achievable_SQM: number; Coverage_Pct: number
}
interface MaterialRow {
  material_code: string; material_name: string; uom: string; family: string
  systems: string[]; demand_qty: number; allocated_qty: number; shortfall_qty: number
  live_stock: number | null; stock_source: string
  burn_per_day_90d: number; days_of_cover: number | null; depletion_date: string | null
}
interface Coverage {
  site: string | null; generated_at: string
  source: { live: number; seed_only: number }
  families: Family[]; per_system: SystemRow[]; materials: MaterialRow[]
  message?: string
}

const FAMILY_COLOR: Record<string, string> = { RL: 'geekblue', BL: 'volcano', OTHER: 'default' }

function useLiningCoverage(site?: string) {
  return useQuery<Coverage>({
    queryKey: ['/analytics/lining-coverage', site],
    queryFn: async () =>
      (await api.get<Coverage>('/analytics/lining-coverage',
        { params: site ? { site_id: site } : {} })).data,
  })
}

export default function LiningCoveragePage() {
  const { user } = useAuth()
  const unscoped = (user?.level ?? 0) >= 3
  const { data: sites } = useSites()
  const [site, setSite] = useState<string | undefined>(undefined)
  const { data, isFetching } = useLiningCoverage(site)

  const sysColumns: ColumnsType<SystemRow> = [
    { title: 'System', dataIndex: 'System_Code', key: 'c', width: 90 },
    { title: 'Name', dataIndex: 'System_Name', key: 'n', ellipsis: true },
    {
      title: 'Family', dataIndex: 'families', key: 'f', width: 120,
      render: (fams: string[]) => fams.map((f) => <Tag key={f} color={FAMILY_COLOR[f]}>{f}</Tag>),
    },
    { title: 'Remaining SQM', dataIndex: 'Remaining_SQM', key: 'r', align: 'right' },
    { title: 'Achievable SQM', dataIndex: 'Achievable_SQM', key: 'a', align: 'right' },
    {
      title: 'Coverage', dataIndex: 'Coverage_Pct', key: 'p', width: 170,
      render: (v: number) => (
        <Progress percent={v} size="small"
          status={v >= 100 ? 'success' : v > 25 ? 'active' : 'exception'} />
      ),
    },
  ]

  const matColumns: ColumnsType<MaterialRow> = [
    { title: 'Material', dataIndex: 'material_code', key: 'c', width: 120 },
    { title: 'Description', dataIndex: 'material_name', key: 'n', ellipsis: true },
    {
      title: 'Family', dataIndex: 'family', key: 'f', width: 80,
      render: (f: string) => <Tag color={FAMILY_COLOR[f]}>{f}</Tag>,
    },
    { title: 'UOM', dataIndex: 'uom', key: 'u', width: 70 },
    { title: 'Required', dataIndex: 'demand_qty', key: 'd', align: 'right' },
    {
      title: 'Live stock', dataIndex: 'live_stock', key: 's', align: 'right',
      render: (v: number | null, r) => (
        <span>{v ?? '—'}{r.stock_source === 'seed' && <Tag style={{ marginLeft: 6 }}>seed</Tag>}</span>
      ),
    },
    {
      title: 'Shortfall', dataIndex: 'shortfall_qty', key: 'x', align: 'right',
      render: (v: number) => (v > 0
        ? <Typography.Text type="danger">{v}</Typography.Text>
        : <Typography.Text type="success">0</Typography.Text>),
    },
    { title: 'Burn/day (90d)', dataIndex: 'burn_per_day_90d', key: 'b', align: 'right' },
    {
      title: 'Days of cover', dataIndex: 'days_of_cover', key: 'dc', align: 'right',
      render: (v: number | null) => (v == null ? '—'
        : <Typography.Text type={v < 30 ? 'danger' : v < 90 ? 'warning' : undefined}>{v}</Typography.Text>),
    },
    { title: 'Depletion', dataIndex: 'depletion_date', key: 'dd', render: (v) => v ?? '—' },
  ]

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        <ExperimentOutlined /> Lining Coverage — predictive material analytics
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        The SME planning engine run against the <strong>live ledger stock</strong>:
        how many SQM of rubber / brick lining the current materials can execute,
        the bottleneck material per family, and when each material depletes at
        the 90-day burn rate.
      </Typography.Paragraph>

      {unscoped && (
        <Space style={{ marginBottom: 16 }}>
          <Typography.Text type="secondary">Site</Typography.Text>
          <Select style={{ width: 180 }} placeholder="CNCEC (default)" allowClear
            value={site} onChange={setSite}
            options={(sites ?? []).map((s) => ({ value: s, label: s }))} />
        </Space>
      )}

      {data?.message && <Alert type="info" showIcon title={data.message} style={{ marginBottom: 16 }} />}

      <Row gutter={[16, 16]}>
        {(data?.families ?? []).map((f) => (
          <Col xs={24} md={8} key={f.family}>
            <Card size="small" loading={isFetching}>
              <Statistic title={`${f.label} — achievable / remaining SQM`}
                value={f.achievable_sqm} suffix={`/ ${f.remaining_sqm}`} />
              <Progress percent={f.coverage_pct} size="small"
                status={f.coverage_pct >= 100 ? 'success' : f.coverage_pct > 25 ? 'active' : 'exception'} />
              {f.bottlenecks.length > 0 && (
                <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0, marginTop: 8 }}>
                  Bottlenecks: {f.bottlenecks.slice(0, 3).join(' · ')}
                </Typography.Paragraph>
              )}
            </Card>
          </Col>
        ))}
      </Row>

      <Card size="small" title="Coverage per lining system (worst first)" style={{ marginTop: 16 }}>
        <Table size="small" loading={isFetching} columns={sysColumns}
          dataSource={data?.per_system ?? []} rowKey="System_Code"
          pagination={false} scroll={{ x: 'max-content' }}
          locale={{ emptyText: <Empty description="No SME systems for this site" /> }} />
      </Card>

      <Card size="small" style={{ marginTop: 16 }}
        title="Materials — engineering demand vs live stock (biggest shortfall first)"
        extra={data && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {data.source.live} live · {data.source.seed_only} seed-only · generated {data.generated_at}
          </Typography.Text>
        )}>
        <Table size="small" loading={isFetching} columns={matColumns}
          dataSource={data?.materials ?? []} rowKey="material_code"
          pagination={{ pageSize: 20, showTotal: (t) => `${t} materials` }}
          scroll={{ x: 'max-content' }} />
      </Card>
    </div>
  )
}
