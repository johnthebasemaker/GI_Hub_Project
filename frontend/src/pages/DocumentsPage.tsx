import { useMemo, useState } from 'react'
import { App, Button, Card, Col, InputNumber, Row, Select, Space, Typography } from 'antd'
import {
  FileExcelOutlined, FilePdfOutlined, FileTextOutlined, IdcardOutlined, QrcodeOutlined, TagsOutlined,
} from '@ant-design/icons'
import { downloadDocument, useList, useSites } from '../api/hooks'
import type { Row as ApiRow } from '../api/client'
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

  // Label sheet scoping: pick specific materials (blank = every item at the
  // site) and how many copies of each label to print (legacy per-item qty).
  const inventory = useList('/inventory', { limit: 600 })
  const [labelSaps, setLabelSaps] = useState<string[]>([])
  const [labelCopies, setLabelCopies] = useState(1)
  const materialOptions = useMemo(() => (inventory.data?.items ?? [])
    .map((r: ApiRow) => ({
      value: String(r.SAP_Code),
      label: `${r.SAP_Code} — ${r.Equipment_Description ?? ''}`,
    })), [inventory.data])

  // Single employee badge PNG (legacy admin Roster+Badges parity).
  const employees = useList('/employees', { limit: 600 })
  const [badgeEmp, setBadgeEmp] = useState<string | undefined>()
  const employeeOptions = useMemo(() => (employees.data?.items ?? [])
    .filter((r: ApiRow) => String(r.status ?? 'active') === 'active')
    .map((r: ApiRow) => ({
      value: String(r.ID_Number),
      label: `${r.Name} (${r.ID_Number})`,
    })), [employees.data])

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
              <Select mode="multiple" allowClear showSearch optionFilterProp="label"
                placeholder="Specific materials (blank = all items)"
                style={{ width: '100%', marginBottom: 8 }}
                value={labelSaps} onChange={setLabelSaps}
                options={materialOptions} maxTagCount={3} />
              <Space wrap style={{ marginBottom: 8 }}>
                <span>Copies per label:</span>
                <InputNumber min={1} max={20} value={labelCopies}
                  onChange={(v) => setLabelCopies(v ?? 1)} />
              </Space>
              <Space wrap>
                <Button icon={<QrcodeOutlined />} loading={busy === 'qr'}
                  onClick={() => go('qr', '/documents/qr-labels', {
                    ...siteParam,
                    ...(labelSaps.length ? {
                      sap_codes: labelSaps
                        .flatMap((s) => Array(labelCopies).fill(s)).join(','),
                    } : {}),
                  }, 'qr-bin-labels.pdf')}>
                  Bin labels
                </Button>
                <Button icon={<IdcardOutlined />} loading={busy === 'badges'}
                  onClick={() => go('badges', '/documents/employee-badges', siteParam, 'employee-badges.pdf')}>
                  Employee badges
                </Button>
                {(user?.role === 'hod' || user?.role === 'admin') && (
                  <Button icon={<TagsOutlined />} loading={busy === 'stickers'}
                    onClick={() => go('stickers', '/documents/material-stickers', {
                      ...siteParam,
                      ...(labelSaps.length ? {
                        sap_codes: labelSaps
                          .flatMap((s) => Array(labelCopies).fill(s)).join(','),
                      } : {}),
                    }, 'material-stickers.pdf')}>
                    Material stickers
                  </Button>
                )}
              </Space>
              <div style={{ marginTop: 12 }}>
                <Typography.Text type="secondary">Single badge (PNG):</Typography.Text>
                <Space.Compact style={{ width: '100%', marginTop: 4 }}>
                  <Select allowClear showSearch optionFilterProp="label"
                    placeholder="Pick an employee" style={{ width: '100%' }}
                    value={badgeEmp} onChange={setBadgeEmp}
                    options={employeeOptions} />
                  <Button icon={<IdcardOutlined />} disabled={!badgeEmp}
                    loading={busy === 'badge1'}
                    onClick={() => badgeEmp && go('badge1',
                      `/documents/employee-badge/${encodeURIComponent(badgeEmp)}`,
                      {}, `badge_${badgeEmp}.png`)}>
                    PNG
                  </Button>
                </Space.Compact>
              </div>
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
