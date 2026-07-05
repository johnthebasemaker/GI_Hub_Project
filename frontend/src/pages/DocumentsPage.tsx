import { useState } from 'react'
import { App, Button, Card, Col, Row, Select, Space, Typography } from 'antd'
import {
  FileExcelOutlined, FilePdfOutlined, FileTextOutlined, IdcardOutlined, QrcodeOutlined,
} from '@ant-design/icons'
import { downloadDocument, useSites } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Download failed'
}

const MASTER_ENTITIES = [
  { value: 'vendors', label: 'Vendors' },
  { value: 'warehouses', label: 'Warehouses' },
  { value: 'employees', label: 'Employees' },
  { value: 'inventory', label: 'Inventory' },
]

export default function DocumentsPage() {
  const { message } = App.useApp()
  const { user } = useAuth()
  const { data: sites } = useSites()
  const canManage = (user?.level ?? 0) >= 2
  const scoped = (user?.level ?? 0) < 3
  const [site, setSite] = useState<string | undefined>()
  const [entity, setEntity] = useState('vendors')
  const [busy, setBusy] = useState<string | null>(null)

  const go = async (tag: string, path: string, params: Record<string, unknown>, fallback: string) => {
    setBusy(tag)
    try {
      await downloadDocument(path, params, fallback)
      message.success('Downloaded')
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setBusy(null)
    }
  }

  const siteParam = !scoped && site ? { site_id: site } : {}

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Documents</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Reference material, printable QR label sheets, and master-data exports.
      </Typography.Paragraph>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={8}>
          <Card title="Reference" size="small" style={{ height: '100%' }}>
            <Typography.Paragraph type="secondary" style={{ minHeight: 40 }}>
              The Standard Operating Procedure and User Manual.
            </Typography.Paragraph>
            <Space wrap>
              <Button icon={<FilePdfOutlined />} loading={busy === 'sop'}
                onClick={() => go('sop', '/documents/reference/sop', {}, 'GI-Hub-SOP.pdf')}>
                SOP
              </Button>
              <Button icon={<FileTextOutlined />} loading={busy === 'manual'}
                onClick={() => go('manual', '/documents/reference/manual', {}, 'GI-Hub-User-Manual.pdf')}>
                User Manual
              </Button>
            </Space>
          </Card>
        </Col>

        {canManage && (
          <Col xs={24} lg={8}>
            <Card title="Label sheets (PDF)" size="small" style={{ height: '100%' }}>
              <Typography.Paragraph type="secondary" style={{ minHeight: 40 }}>
                Printable A4 grids of scannable QR codes.
              </Typography.Paragraph>
              {!scoped && (
                <Select allowClear placeholder="All sites" style={{ width: '100%', marginBottom: 12 }}
                  value={site} onChange={setSite}
                  options={(sites ?? []).map((s) => ({ value: s, label: s }))} />
              )}
              <Space wrap>
                <Button icon={<QrcodeOutlined />} loading={busy === 'qr'}
                  onClick={() => go('qr', '/documents/qr-labels', siteParam, 'qr-bin-labels.pdf')}>
                  Bin labels
                </Button>
                <Button icon={<IdcardOutlined />} loading={busy === 'badges'}
                  onClick={() => go('badges', '/documents/employee-badges', siteParam, 'employee-badges.pdf')}>
                  Employee badges
                </Button>
              </Space>
            </Card>
          </Col>
        )}

        {canManage && (
          <Col xs={24} lg={8}>
            <Card title="Master-data export" size="small" style={{ height: '100%' }}>
              <Typography.Paragraph type="secondary" style={{ minHeight: 40 }}>
                Download a master table as a spreadsheet, CSV, or PDF.
              </Typography.Paragraph>
              <Select style={{ width: '100%', marginBottom: 12 }} value={entity} onChange={setEntity}
                options={MASTER_ENTITIES} />
              <Space wrap>
                {(['xlsx', 'csv', 'pdf'] as const).map((fmt) => (
                  <Button key={fmt}
                    icon={fmt === 'xlsx' ? <FileExcelOutlined /> : fmt === 'pdf' ? <FilePdfOutlined /> : <FileTextOutlined />}
                    loading={busy === `m-${fmt}`}
                    onClick={() => go(`m-${fmt}`, `/documents/master/${entity}`,
                      { format: fmt, ...siteParam }, `${entity}-master.${fmt}`)}>
                    {fmt.toUpperCase()}
                  </Button>
                ))}
              </Space>
            </Card>
          </Col>
        )}
      </Row>
    </div>
  )
}
