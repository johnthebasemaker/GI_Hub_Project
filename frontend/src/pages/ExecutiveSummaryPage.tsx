import { useMemo, useState } from 'react'
import {
  Alert, Button, Card, Col, DatePicker, Descriptions, Empty, Progress, Row,
  Select, Space, Spin, Statistic, Table, Tag, Typography,
} from 'antd'
import {
  ArrowDownOutlined, ArrowUpOutlined, DownloadOutlined, FileExcelOutlined,
  FilePdfOutlined, FundProjectionScreenOutlined, ReloadOutlined,
} from '@ant-design/icons'
import dayjs, { Dayjs } from 'dayjs'
import {
  downloadExecSummaryPdf, downloadExecSummaryXlsx, useExecutiveSummary, useSites,
  type ExecSummaryKpi,
} from '../api/hooks'
import { useAuth } from '../auth/AuthContext'

const { RangePicker } = DatePicker

/**
 * HOD Executive Summary — one professional page covering the period's full
 * picture: ledger movements, SQM done, manpower, PR/PO pipeline, the warehouse
 * delivery plan, actions taken vs pending, SME achievable-SQM capacity
 * (read-only engine run) and cross-site enquiries.
 * "Download PDF" and "Download Excel" both stream server-rendered files —
 * the PDF is a measured, paginated A4 report (exec_pdf.py), not a page print.
 */
function TrendTag({ pct }: { pct: number | null | undefined }) {
  if (pct === null || pct === undefined) return <Tag>—</Tag>
  const up = pct >= 0
  return (
    <Tag color={up ? 'green' : 'red'} icon={up ? <ArrowUpOutlined /> : <ArrowDownOutlined />}>
      {Math.abs(pct)}% vs prev
    </Tag>
  )
}

function LedgerKpiCard({ title, k }: { title: string; k: ExecSummaryKpi }) {
  return (
    <Card size="small">
      <Statistic title={title} value={k.qty} precision={2} suffix={`· ${k.count} entries`} />
      <Space size={4} wrap style={{ marginTop: 4 }}>
        <TrendTag pct={k.delta_pct} />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          7d avg {k.daily_avg_7d}/day
        </Typography.Text>
      </Space>
    </Card>
  )
}

function SectionTable({ rows, columns, empty }: {
  rows: Record<string, unknown>[]
  columns: { title: string; dataIndex: string; render?: (v: unknown) => React.ReactNode }[]
  empty: string
}) {
  if (!rows.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={empty} />
  return (
    <Table sticky={{ offsetHeader: 64 }}
      size="small"
      rowKey="__k"
      dataSource={rows.map((r, i) => ({ ...r, __k: i }))}
      columns={columns}
      pagination={rows.length > 8 ? { pageSize: 8, size: 'small' } : false}
      scroll={{ x: true }}
    />
  )
}

const col = (title: string, dataIndex: string) => ({ title, dataIndex })

export default function ExecutiveSummaryPage() {
  const { user } = useAuth()
  const { data: sites } = useSites()
  const isAdmin = user?.role === 'admin'
  const [range, setRange] = useState<[Dayjs, Dayjs]>([dayjs(), dayjs()])
  const [site, setSite] = useState<string | undefined>(undefined)
  const [pdfBusy, setPdfBusy] = useState(false)

  const params = useMemo(() => ({
    date_from: range[0].format('YYYY-MM-DD'),
    date_to: range[1].format('YYYY-MM-DD'),
    ...(isAdmin && site ? { site_id: site } : {}),
  }), [range, site, isAdmin])

  const { data: d, isFetching, isError, refetch } = useExecutiveSummary(params)

  const presets = [
    { label: 'Today', value: [dayjs(), dayjs()] as [Dayjs, Dayjs] },
    { label: 'Yesterday', value: [dayjs().subtract(1, 'day'), dayjs().subtract(1, 'day')] as [Dayjs, Dayjs] },
    { label: 'Last 7 days', value: [dayjs().subtract(6, 'day'), dayjs()] as [Dayjs, Dayjs] },
    { label: 'This month', value: [dayjs().startOf('month'), dayjs()] as [Dayjs, Dayjs] },
  ]

  return (
    <div className="exec-report">
      <Space wrap style={{ marginBottom: 16, justifyContent: 'space-between', width: '100%' }}>
        <Space wrap>
          <FundProjectionScreenOutlined style={{ fontSize: 20 }} />
          <Typography.Title level={4} style={{ margin: 0 }}>Executive Summary</Typography.Title>
          <RangePicker
            value={range}
            allowClear={false}
            presets={presets}
            onChange={(v) => v && v[0] && v[1] && setRange([v[0], v[1]])}
          />
          {isAdmin && (
            <Select
              allowClear
              placeholder="All sites"
              style={{ minWidth: 140 }}
              value={site}
              onChange={setSite}
              options={(sites ?? []).map((s) => ({ value: s, label: s }))}
            />
          )}
          <Button icon={<ReloadOutlined />} onClick={() => refetch()} loading={isFetching} />
        </Space>
        <Space>
          <Button icon={<FilePdfOutlined />} disabled={!d} loading={pdfBusy}
            onClick={() => {
              setPdfBusy(true)
              downloadExecSummaryPdf(params).finally(() => setPdfBusy(false))
            }}>
            Download PDF
          </Button>
          <Button type="primary" icon={<FileExcelOutlined />} disabled={!d}
            onClick={() => downloadExecSummaryXlsx(params)}>
            Download Excel
          </Button>
        </Space>
      </Space>

      {isError && (
        <Alert type="error" showIcon style={{ marginBottom: 16 }}
          title="Could not load the executive summary — check your connection or restart the backend." />
      )}

      {!d ? (
        <div style={{ textAlign: 'center', padding: 48 }}><Spin /></div>
      ) : (
        <Spin spinning={isFetching}>
          {/* ── KPI hero ─────────────────────────────────────────────── */}
          <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
            <Col xs={12} md={8} xl={4}><LedgerKpiCard title="Receipts (qty)" k={d.kpis.receipts} /></Col>
            <Col xs={12} md={8} xl={4}><LedgerKpiCard title="Consumption (qty)" k={d.kpis.consumption} /></Col>
            <Col xs={12} md={8} xl={4}><LedgerKpiCard title="Returns (qty)" k={d.kpis.returns} /></Col>
            <Col xs={12} md={8} xl={4}>
              <Card size="small">
                <Statistic title="SQM done" value={d.kpis.sqm_done.total} precision={2} />
                <TrendTag pct={d.kpis.sqm_done.delta_pct} />
              </Card>
            </Col>
            <Col xs={12} md={8} xl={4}>
              <Card size="small">
                <Statistic title="Man-hours" value={d.kpis.man_hours.total} precision={1} />
                <TrendTag pct={d.kpis.man_hours.delta_pct} />
              </Card>
            </Col>
            <Col xs={12} md={8} xl={4}>
              <Card size="small">
                <Statistic title="Manpower present" value={d.kpis.manpower.present}
                  suffix={`/ ${d.kpis.manpower.active_total}`} />
                <Tag color={d.kpis.manpower.absent ? 'orange' : 'green'}>
                  {d.kpis.manpower.absent} absent
                </Tag>
              </Card>
            </Col>
          </Row>

          {/* ── Movements ───────────────────────────────────────────── */}
          <Row gutter={[12, 12]}>
            <Col xs={24} xl={12}>
              <Card size="small" title="Material receipts">
                <SectionTable rows={d.receipts_detail} empty="No receipts in this period"
                  columns={[col('Date', 'Date'), col('SAP', 'SAP_Code'),
                    col('Description', 'Equipment_Description'), col('Qty', 'Quantity'),
                    col('UOM', 'UOM'), col('Supplier', 'Supplier'), col('PR', 'PR_Number')]} />
              </Card>
            </Col>
            <Col xs={24} xl={12}>
              <Card size="small" title="Material consumption (issues)">
                <SectionTable rows={d.consumption_detail} empty="No consumption in this period"
                  columns={[col('Date', 'Date'), col('SAP', 'SAP_Code'),
                    col('Description', 'Equipment_Description'), col('Qty', 'Quantity'),
                    col('UOM', 'UOM'), col('Issued to', 'Issued_To'), col('WBS', 'WBS')]} />
              </Card>
            </Col>
            <Col xs={24} xl={12}>
              <Card size="small" title="Returned material">
                <SectionTable rows={d.returns_detail} empty="No returns in this period"
                  columns={[col('Date', 'Date'), col('SAP', 'SAP_Code'),
                    col('Description', 'Equipment_Description'), col('Qty', 'Quantity'),
                    col('UOM', 'UOM'), col('Site', 'Site_ID')]} />
              </Card>
            </Col>
            <Col xs={24} xl={12}>
              <Card size="small" title="SQM done (per equipment × system)">
                <SectionTable rows={d.sqm_detail} empty="No production logged in this period"
                  columns={[col('Date', 'Work_Date'), col('Equipment', 'Equipment_Tag'),
                    col('System', 'System_Code'), col('SQM', 'SQM_Done')]} />
              </Card>
            </Col>

            {/* ── Manpower ──────────────────────────────────────────── */}
            <Col xs={24} xl={12}>
              <Card size="small" title={`Manpower present (${d.manpower.present.length})`}>
                <SectionTable rows={d.manpower.present} empty="No timesheets in this period"
                  columns={[col('Code', 'Employee_Code'), col('Name', 'Name'),
                    col('Designation', 'Designation'), col('Hours', 'Hours'),
                    col('OT', 'OT_Hours'), col('SQM alloc', 'Allocated_SQM')]} />
              </Card>
            </Col>
            <Col xs={24} xl={12}>
              <Card size="small" title={`Absent (${d.manpower.absent.length} of ${d.kpis.manpower.active_total} active)`}>
                <SectionTable rows={d.manpower.absent} empty="Full attendance 🎉"
                  columns={[col('Code', 'Employee_Code'), col('Name', 'Name'),
                    col('Designation', 'Designation'), col('Type', 'Worker_Type')]} />
              </Card>
            </Col>

            {/* ── Procurement pipeline ──────────────────────────────── */}
            <Col xs={24} xl={12}>
              <Card size="small" title="Purchase requests"
                extra={<Tag color="blue">{d.pr_status.raised_in_period} raised this period</Tag>}>
                <SectionTable
                  rows={d.pr_status.open_by_state as unknown as Record<string, unknown>[]}
                  empty="No PRs on file"
                  columns={[col('Workflow state', 'state'), col('PRs', 'n')]} />
              </Card>
            </Col>
            <Col xs={24} xl={12}>
              <Card size="small" title="Purchase orders"
                extra={
                  <Space size={4}>
                    <Tag color="blue">{d.po_status.open} open</Tag>
                    <Tag color={d.po_status.overdue ? 'red' : 'green'}>{d.po_status.overdue} overdue</Tag>
                  </Space>
                }>
                <SectionTable
                  rows={d.po_status.by_status as unknown as Record<string, unknown>[]}
                  empty="No POs on file"
                  columns={[col('Status', 'status'), col('POs', 'n')]} />
              </Card>
            </Col>

            {/* ── Delivery plan ─────────────────────────────────────── */}
            <Col xs={24} xl={12}>
              <Card size="small" title="Delivery notes in flight">
                <SectionTable rows={d.delivery_plan.dn_in_flight} empty="No DNs in flight"
                  columns={[col('DN', 'DN_Number'), col('PO', 'PO_Number'),
                    col('Warehouse', 'Warehouse_ID'), col('Status', 'status'),
                    col('Date', 'DN_Date'), col('Vehicle', 'Vehicle_No')]} />
              </Card>
            </Col>
            <Col xs={24} xl={12}>
              <Card size="small" title={`Upcoming deliveries (to ${d.delivery_plan.window_to})`}>
                <SectionTable rows={d.delivery_plan.upcoming_deliveries} empty="Nothing scheduled in the window"
                  columns={[col('PO', 'PO_Number'), col('Warehouse', 'Warehouse_ID'),
                    col('Expected', 'Expected_Delivery'), col('Status', 'status'),
                    col('Vendor', 'Vendor_Name')]} />
              </Card>
            </Col>

            {/* ── Actions taken vs pending ──────────────────────────── */}
            <Col xs={24} xl={12}>
              <Card size="small" title="Actions taken (this period)">
                <Descriptions size="small" column={1} bordered>
                  <Descriptions.Item label="Receipts posted">{d.actions.taken.receipts_posted}</Descriptions.Item>
                  <Descriptions.Item label="Issues approved">{d.actions.taken.issues_approved}</Descriptions.Item>
                  <Descriptions.Item label="Returns processed">{d.actions.taken.returns_processed}</Descriptions.Item>
                  <Descriptions.Item label="Entries rejected">{d.actions.taken.entries_rejected}</Descriptions.Item>
                  <Descriptions.Item label="DN decisions">{d.actions.taken.dn_decisions}</Descriptions.Item>
                </Descriptions>
              </Card>
            </Col>
            <Col xs={24} xl={12}>
              <Card size="small" title="Pending actions">
                <Descriptions size="small" column={1} bordered>
                  <Descriptions.Item label="Entry approvals waiting">
                    {d.actions.pending.entry_approvals}
                  </Descriptions.Item>
                  <Descriptions.Item label="DN queue">
                    {Object.entries(d.actions.pending.dn_queue).length
                      ? Object.entries(d.actions.pending.dn_queue)
                          .map(([k, v]) => `${k}: ${v}`).join(' · ')
                      : '0'}
                  </Descriptions.Item>
                  <Descriptions.Item label="Supervisor requests pending">
                    {d.actions.pending.smr_pending}
                  </Descriptions.Item>
                  <Descriptions.Item label="Draft PRs">{d.actions.pending.draft_prs}</Descriptions.Item>
                </Descriptions>
              </Card>
            </Col>

            {/* ── SME capacity ──────────────────────────────────────── */}
            <Col xs={24} xl={12}>
              <Card size="small" title="Achievable SQM with available material — per equipment">
                <SectionTable rows={d.sqm_capacity.per_equipment} empty="No SME model for this site"
                  columns={[col('Equipment', 'Equipment_Tag'), col('Name', 'Name'),
                    col('Remaining SQM', 'Remaining_SQM'), col('Achievable SQM', 'Achievable_SQM'),
                    { title: 'Coverage', dataIndex: 'Coverage_Pct',
                      render: (v) => <Progress percent={Number(v) || 0} size="small"
                        status={Number(v) >= 100 ? 'success' : Number(v) > 0 ? 'active' : 'exception'} /> },
                    col('Bottleneck material', 'Bottleneck')]} />
              </Card>
            </Col>
            <Col xs={24} xl={12}>
              <Card size="small" title="Achievable SQM — per system code">
                <SectionTable rows={d.sqm_capacity.per_system} empty="No SME model for this site"
                  columns={[col('System', 'System_Code'), col('Name', 'System_Name'),
                    col('Remaining SQM', 'Remaining_SQM'), col('Achievable SQM', 'Achievable_SQM'),
                    { title: 'Coverage', dataIndex: 'Coverage_Pct',
                      render: (v) => <Progress percent={Number(v) || 0} size="small"
                        status={Number(v) >= 100 ? 'success' : Number(v) > 0 ? 'active' : 'exception'} /> }]} />
              </Card>
            </Col>

            {/* ── Cross-site ────────────────────────────────────────── */}
            <Col xs={24}>
              <Card size="small" title="Cross-site enquiries (open + decided this period)">
                <SectionTable rows={d.cross_site} empty="No cross-site activity"
                  columns={[col('#', 'id'), col('From', 'requesting_site'), col('To', 'target_site'),
                    col('SAP', 'SAP_Code'), col('Requested', 'requested_qty'),
                    col('Available', 'available_qty'),
                    { title: 'Status', dataIndex: 'status',
                      render: (v) => <Tag color={v === 'pending' ? 'orange' : v === 'approved' ? 'green' : 'default'}>{String(v)}</Tag> },
                    col('By', 'requested_by'), col('Created', 'created')]} />
              </Card>
            </Col>
          </Row>

          <Typography.Text type="secondary" style={{ display: 'block', marginTop: 12, fontSize: 12 }}>
            Generated {d.generated_at} · Site {d.site_id ?? 'ALL'} · {d.date_from} → {d.date_to}
            {' '}· Trends compare the preceding {d.days}-day window.
            <span> <DownloadOutlined /> Download PDF streams the full paginated report.</span>
          </Typography.Text>
        </Spin>
      )}
    </div>
  )
}
