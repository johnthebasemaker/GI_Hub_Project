import { useState } from 'react'
import { App, Button, Card, Col, Row, Select, Space, Statistic, Table, Tabs, Typography } from 'antd'
import { FileExcelOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import {
  downloadDocument, useSites, useSmeComparison, useSmeDemandMatrix, useSmeEquipment,
  useSmeEquipmentReport, useSmeMaterials, useSmeRecipes, useSmeSqm, useSmeSummary,
} from '../api/hooks'
import type { Row as ApiRow } from '../api/client'
import { buildColumns } from '../lib/columns'

// One-click XLSX export of an SME view (read-only server render).
function ExportButton({ exportKey, siteId }: { exportKey: string; siteId?: string }) {
  const { message } = App.useApp()
  const [busy, setBusy] = useState(false)
  return (
    <Button
      icon={<FileExcelOutlined />}
      size="small"
      loading={busy}
      style={{ marginBottom: 12 }}
      onClick={async () => {
        setBusy(true)
        try {
          await downloadDocument(`/sme/export/${exportKey}`,
            { format: 'xlsx', ...(siteId ? { site_id: siteId } : {}) },
            `sme-${exportKey}.xlsx`)
        } catch {
          message.error('Export failed')
        } finally {
          setBusy(false)
        }
      }}
    >
      Export XLSX
    </Button>
  )
}

function num(v: unknown) {
  return v == null ? 0 : Number(v)
}

function SmeTable({ rows, loading }: { rows?: ApiRow[]; loading?: boolean }) {
  return (
    <Table
      size="small"
      loading={loading}
      columns={buildColumns(rows ?? [])}
      dataSource={(rows ?? []).map((r, i) => ({ ...r, __rk: i }))}
      rowKey="__rk"
      scroll={{ x: 'max-content' }}
      pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `${t} rows` }}
    />
  )
}

function Dashboard({ siteId }: { siteId?: string }) {
  const { data: s } = useSmeSummary(siteId)
  const pct = s && s.original_sqm ? Math.round((s.done_sqm / s.original_sqm) * 1000) / 10 : 0
  const lsColumns: ColumnsType<ApiRow> = [
    { title: 'Lining System', dataIndex: 'Lining_System_Code', key: 'l', render: (v) => v ?? '—' },
    { title: 'Equipment', dataIndex: 'count', key: 'c', align: 'right' },
  ]
  return (
    <div>
      <Row gutter={[16, 16]}>
        <Col xs={12} md={6}><Card><Statistic title="Equipment" value={num(s?.equipment)} /></Card></Col>
        <Col xs={12} md={6}><Card><Statistic title="Total SQM" value={num(s?.total_sqm)} precision={1} /></Card></Col>
        <Col xs={12} md={6}><Card><Statistic title="Recipes (BOM lines)" value={num(s?.recipes)} /></Card></Col>
        <Col xs={12} md={6}><Card><Statistic title="Materials" value={num(s?.materials)} /></Card></Col>
      </Row>
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} md={10}>
          <Card size="small" title="SQM progress">
            <Statistic title="Planned SQM" value={num(s?.original_sqm)} precision={1} />
            <Statistic title="Done SQM" value={num(s?.done_sqm)} precision={1} style={{ marginTop: 8 }} />
            <Statistic title="Completion" value={pct} suffix="%" style={{ marginTop: 8 }} />
          </Card>
        </Col>
        <Col xs={24} md={14}>
          <Card size="small" title="Equipment by lining system">
            <Table size="small" columns={lsColumns} dataSource={s?.by_lining_system ?? []}
              rowKey={(r: ApiRow) => String(r.Lining_System_Code)} pagination={false} />
          </Card>
        </Col>
      </Row>
    </div>
  )
}

function DemandMatrix({ siteId }: { siteId?: string }) {
  const { data, isFetching } = useSmeDemandMatrix(siteId)
  return (
    <div>
      <Space wrap style={{ marginBottom: 4 }}>
        <ExportButton exportKey="demand-matrix" siteId={siteId} />
        <ExportButton exportKey="demand-totals" siteId={siteId} />
      </Space>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        {data?.allocation_order ?? ''}
      </Typography.Paragraph>
      <Typography.Title level={5} style={{ marginTop: 0 }}>Per-material totals (net order list)</Typography.Title>
      <SmeTable rows={data?.totals} loading={isFetching} />
      <Typography.Title level={5}>Allocation detail (per equipment × material)</Typography.Title>
      <SmeTable rows={data?.lines} loading={isFetching} />
    </div>
  )
}

export default function SmePage() {
  const { data: sites } = useSites()
  const [siteId, setSiteId] = useState<string | undefined>(undefined)
  const equipment = useSmeEquipment(siteId)
  const recipes = useSmeRecipes()
  const sqm = useSmeSqm(siteId)
  const materials = useSmeMaterials()
  const eqReport = useSmeEquipmentReport(siteId)
  const comparison = useSmeComparison(siteId)

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        SME Material Estimator
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Read-only view of the estimator data (equipment, recipes/BOM, SQM progress,
        materials with derived available quantity).
      </Typography.Paragraph>
      <Space style={{ marginBottom: 12 }}>
        <Select allowClear placeholder="All sites" style={{ width: 180 }} value={siteId}
          onChange={setSiteId} options={(sites ?? []).map((s) => ({ value: s, label: s }))} />
      </Space>
      <Tabs
        defaultActiveKey="dash"
        items={[
          { key: 'dash', label: 'Dashboard', children: <Dashboard siteId={siteId} /> },
          { key: 'equip', label: 'Equipment', children: <SmeTable rows={equipment.data} loading={equipment.isFetching} /> },
          { key: 'recipes', label: 'Recipes / BOM', children: <SmeTable rows={recipes.data} loading={recipes.isFetching} /> },
          { key: 'sqm', label: 'SQM Progress', children: <SmeTable rows={sqm.data} loading={sqm.isFetching} /> },
          {
            key: 'materials', label: 'Materials',
            children: (
              <div>
                <ExportButton exportKey="materials" siteId={siteId} />
                <SmeTable rows={materials.data} loading={materials.isFetching} />
              </div>
            ),
          },
          {
            key: 'eq-report', label: 'Equipment Report',
            children: (
              <div>
                <ExportButton exportKey="equipment-report" siteId={siteId} />
                <SmeTable rows={eqReport.data} loading={eqReport.isFetching} />
              </div>
            ),
          },
          {
            key: 'comparison', label: 'Consumption Comparison',
            children: (
              <div>
                <ExportButton exportKey="consumption-comparison" siteId={siteId} />
                <SmeTable rows={comparison.data} loading={comparison.isFetching} />
              </div>
            ),
          },
          { key: 'demand', label: 'Demand Matrix', children: <DemandMatrix siteId={siteId} /> },
        ]}
      />
    </div>
  )
}
