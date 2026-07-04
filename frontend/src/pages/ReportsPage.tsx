import { useState } from 'react'
import { App, Button, Card, Col, InputNumber, Row as AntRow, Select, Space, Typography } from 'antd'
import { FileExcelOutlined, FilePdfOutlined, FileTextOutlined } from '@ant-design/icons'
import { downloadReport, useReports, useSites } from '../api/hooks'
import type { Row } from '../api/client'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Download failed'
}

const FORMATS: { key: string; label: string; icon: React.ReactNode }[] = [
  { key: 'xlsx', label: 'Excel', icon: <FileExcelOutlined /> },
  { key: 'pdf', label: 'PDF', icon: <FilePdfOutlined /> },
  { key: 'csv', label: 'CSV', icon: <FileTextOutlined /> },
]

function ReportCard({ report }: { report: Row }) {
  const { message } = App.useApp()
  const { data: sites } = useSites()
  const filters = (report.filters as string[]) ?? []
  const [site, setSite] = useState<string | undefined>()
  const [days, setDays] = useState(30)
  const [withinDays, setWithinDays] = useState(30)
  const [status, setStatus] = useState<string | undefined>()
  const [busy, setBusy] = useState<string | null>(null)

  const params: Record<string, unknown> = {}
  if (filters.includes('site_id') && site) params.site_id = site
  if (filters.includes('days')) params.days = days
  if (filters.includes('within_days')) params.within_days = withinDays
  if (filters.includes('status') && status) params.status = status

  const doDownload = async (fmt: string) => {
    setBusy(fmt)
    try {
      await downloadReport(String(report.key), fmt, params)
      message.success(`${report.label} (${fmt.toUpperCase()}) downloaded`)
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <Card title={String(report.label)} size="small" style={{ height: '100%' }}>
      <Typography.Paragraph type="secondary" style={{ minHeight: 44 }}>
        {String(report.description ?? '')}
      </Typography.Paragraph>
      <Space wrap style={{ marginBottom: 12 }}>
        {filters.includes('site_id') && (
          <Select allowClear placeholder="All sites" style={{ width: 150 }} value={site} onChange={setSite}
            options={(sites ?? []).map((s) => ({ value: s, label: s }))} />
        )}
        {filters.includes('days') && (
          <Space size={4}>
            <Typography.Text type="secondary">Last</Typography.Text>
            <InputNumber min={1} max={3650} value={days} onChange={(v) => setDays(v ?? 30)} style={{ width: 80 }} />
            <Typography.Text type="secondary">days</Typography.Text>
          </Space>
        )}
        {filters.includes('within_days') && (
          <Space size={4}>
            <Typography.Text type="secondary">Within</Typography.Text>
            <InputNumber min={0} max={3650} value={withinDays} onChange={(v) => setWithinDays(v ?? 30)} style={{ width: 80 }} />
            <Typography.Text type="secondary">days</Typography.Text>
          </Space>
        )}
        {filters.includes('status') && (
          <Select allowClear placeholder="Any status" style={{ width: 150 }} value={status} onChange={setStatus}
            options={['open', 'closed', 'force_closed', 'cancelled'].map((s) => ({ value: s, label: s }))} />
        )}
      </Space>
      <div>
        <Space>
          {FORMATS.map((f) => (
            <Button key={f.key} icon={f.icon} loading={busy === f.key} onClick={() => doDownload(f.key)}>
              {f.label}
            </Button>
          ))}
        </Space>
      </div>
    </Card>
  )
}

export default function ReportsPage() {
  const { data: reports, isFetching } = useReports()
  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Reports</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Export the ERP's live data as Excel, PDF, or CSV.
      </Typography.Paragraph>
      <AntRow gutter={[16, 16]}>
        {(reports ?? []).map((r) => (
          <Col key={String(r.key)} xs={24} md={12} lg={8}>
            <ReportCard report={r} />
          </Col>
        ))}
        {!isFetching && (reports ?? []).length === 0 && (
          <Col span={24}><Typography.Text type="secondary">No reports available.</Typography.Text></Col>
        )}
      </AntRow>
    </div>
  )
}
