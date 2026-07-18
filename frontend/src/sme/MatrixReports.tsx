/**
 * frontend/src/sme/MatrixReports.tsx — 📋 Equipment Report + 🔢 System Code
 * Report (Phase S4). Read-only matrix views of the (Equipment × System Code)
 * relation and its inverse — no demand math, original (planned) SQM:
 *   EquipmentMatrixReport: per-location expanders → per-equipment expanders →
 *     inline per-code rows (badge · short name · SQM), KPI strip on top.
 *   SystemCodeReport: summary grid (code · name · equipment count · SQM) +
 *     per-code expanders listing the carrying equipment.
 * Exports use the existing GET /sme/export renderers (equipment-report /
 * system-code-report) — server-side document authority.
 */
import { useMemo, useState } from 'react'
import { Alert, App, Button, Card, Col, Collapse, Row, Skeleton, Space, Table } from 'antd'
import { FileExcelOutlined, FilePdfOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { downloadDocument, useSmeSnapshot } from '../api/hooks'
import { buildModel, syscodeCompare } from './engine'
import { allUnits, locColor } from './insights'
import type { UnitRef } from './insights'
import KpiDrill from './KpiDrill'

const mono: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }
const nf = (v: number, d = 1) =>
  v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: d })

const CodePill = ({ code }: { code: string }) => (
  <span style={{
    ...mono, border: '1px solid rgba(212,175,55,.5)', color: '#D4AF37',
    borderRadius: 6, padding: '0 6px', fontSize: '0.68rem', fontWeight: 700, marginRight: 6,
  }}>Code {code}</span>
)

// Scoped legacy-parity downloads (per-tag / per-location / per-code files
// rendered by GET /sme/export with narrow params; server names the file).
export function ScopedExport({ exportKey, siteId, params, pdf = true }: {
  exportKey: string
  siteId?: string
  params?: Record<string, string>
  pdf?: boolean
}) {
  const { message } = App.useApp()
  const [busy, setBusy] = useState<string | null>(null)
  const dl = async (format: 'xlsx' | 'pdf') => {
    setBusy(format)
    try {
      await downloadDocument(`/sme/export/${exportKey}`,
        { format, ...(siteId ? { site_id: siteId } : {}), ...(params ?? {}) },
        `sme-${exportKey}.${format}`)
    } catch {
      message.error('Export failed')
    } finally {
      setBusy(null)
    }
  }
  return (
    <Space size={4} onClick={(e) => e.stopPropagation()}>
      <Button size="small" icon={<FileExcelOutlined />} loading={busy === 'xlsx'}
        onClick={() => dl('xlsx')}>Excel</Button>
      {pdf && (
        <Button size="small" icon={<FilePdfOutlined />} loading={busy === 'pdf'}
          onClick={() => dl('pdf')}>PDF</Button>
      )}
    </Space>
  )
}

function useUnits(siteId?: string) {
  const { data: snap, isLoading } = useSmeSnapshot(siteId)
  const units = useMemo(() => {
    if (!snap) return null
    return allUnits(buildModel(snap.equipment, snap.recipes, snap.materials, snap.progress))
  }, [snap])
  return { units, isLoading }
}

// ─── 📋 Equipment Report: Location → Equipment → codes ───────────────────────
export function EquipmentMatrixReport({ siteId }: { siteId?: string }) {
  const { units, isLoading } = useUnits(siteId)
  if (isLoading) return <Skeleton active paragraph={{ rows: 6 }} />
  if (!units) return <Alert type="warning" showIcon title="SME model unavailable" />

  const locations = [...new Set(units.map((u) => u.location || '—'))].sort()
  const tags = new Set(units.map((u) => u.tag))
  const codes = new Set(units.map((u) => u.code))
  const totalSqm = units.reduce((s, u) => s + u.original, 0)

  return (
    <div>
      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        <Col flex="1 1 150px"><KpiDrill title="Equipment" value={String(tags.size)}
          drillTitle="Equipment" rows={[...tags].sort().map((t) => {
            const u = units.find((x) => x.tag === t)!
            return { Tag: t, Name: u.name, Location: u.location, Type: u.type }
          })} /></Col>
        <Col flex="1 1 150px"><KpiDrill title="Locations" value={String(locations.length)}
          drillTitle="Locations" rows={locations.map((l) => ({
            Location: l, Equipment: new Set(units.filter((u) => (u.location || '—') === l).map((u) => u.tag)).size,
          }))} /></Col>
        <Col flex="1 1 150px"><KpiDrill title="System Codes" value={String(codes.size)}
          drillTitle="System Codes" rows={[...codes].sort(syscodeCompare).map((c) => ({
            Code: c, 'Short Name': units.find((u) => u.code === c)?.shortName ?? '',
          }))} /></Col>
        <Col flex="1 1 150px"><KpiDrill title="Total SQM" value={nf(totalSqm)}
          drillTitle="SQM by Equipment & Code" rows={[...units]
            .sort((a, b) => b.original - a.original)
            .map((u) => ({ Tag: u.tag, Code: u.code, SQM: u.original }))} /></Col>
      </Row>

      <Collapse size="small" items={locations.map((loc) => {
        const locUnits = units.filter((u) => (u.location || '—') === loc)
        const locTags = [...new Set(locUnits.map((u) => u.tag))].sort()
        const locSqm = locUnits.reduce((s, u) => s + u.original, 0)
        return {
          key: loc,
          label: (
            <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{
                ...mono, background: locColor(loc), color: '#fff', borderRadius: 6,
                padding: '1px 10px', fontSize: '0.72rem', fontWeight: 700,
              }}>{loc}</span>
              <span style={{ fontSize: '0.72rem', opacity: 0.75, flex: 1 }}>
                {locTags.length} equipment · {nf(locSqm)} SQM
              </span>
              {loc !== '—' && (
                <ScopedExport exportKey="equipment-report" siteId={siteId}
                  params={{ location: loc }} />
              )}
            </span>
          ),
          children: (
            <Collapse size="small" ghost items={locTags.map((tag) => {
              const tagUnits = locUnits.filter((u) => u.tag === tag)
                .sort((a, b) => syscodeCompare(a.code, b.code))
              const first = tagUnits[0]
              return {
                key: tag,
                label: (
                  <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <b style={{ ...mono, fontSize: '0.78rem' }}>{tag}</b>
                    <span style={{ fontSize: '0.72rem', opacity: 0.7, flex: 1 }}>
                      {first.name.slice(0, 34)} · {first.type || '—'} · {first.substrate || '—'}
                    </span>
                    <ScopedExport exportKey="equipment-report" siteId={siteId}
                      params={{ tag }} />
                  </span>
                ),
                children: (
                  <div>
                    {tagUnits.map((u) => (
                      <div key={u.code} style={{
                        display: 'flex', alignItems: 'center', gap: 8, padding: '3px 6px',
                        borderBottom: '1px solid rgba(128,128,128,.15)', fontSize: '0.75rem',
                      }}>
                        <CodePill code={u.code} />
                        <span style={{ opacity: 0.8, flex: 1 }}>{u.shortName || '—'}</span>
                        <span style={{ ...mono, opacity: 0.85 }}>{nf(u.original)} SQM</span>
                      </div>
                    ))}
                  </div>
                ),
              }
            })} />
          ),
        }
      })} />
    </div>
  )
}

// ─── 🔢 System Code Report: Code → equipments ────────────────────────────────
export function SystemCodeReport({ siteId }: { siteId?: string }) {
  const { units, isLoading } = useUnits(siteId)
  if (isLoading) return <Skeleton active paragraph={{ rows: 6 }} />
  if (!units) return <Alert type="warning" showIcon title="SME model unavailable" />

  const codes = [...new Set(units.map((u) => u.code))].sort(syscodeCompare)
  const summary = codes.map((code) => {
    const cu = units.filter((u) => u.code === code)
    return {
      code,
      shortName: cu[0]?.shortName ?? '',
      equipment: new Set(cu.map((u) => u.tag)).size,
      sqm: Math.round(cu.reduce((s, u) => s + u.original, 0) * 100) / 100,
    }
  })
  const summaryCols: ColumnsType<(typeof summary)[number]> = [
    { title: 'System Code', dataIndex: 'code', key: 'c', render: (v: string) => <CodePill code={v} /> },
    { title: 'Short Name', dataIndex: 'shortName', key: 'n', ellipsis: true },
    { title: 'Equipment', dataIndex: 'equipment', key: 'e', align: 'right' },
    { title: 'Total SQM', dataIndex: 'sqm', key: 's', align: 'right', render: (v: number) => nf(v) },
  ]
  const eqCols: ColumnsType<UnitRef> = [
    { title: 'Location', dataIndex: 'location', key: 'l', render: (v: string) => v || '—' },
    { title: 'Type', dataIndex: 'type', key: 't', render: (v: string) => v || '—' },
    { title: 'Equipment Tag', dataIndex: 'tag', key: 'g', render: (v: string) => <b style={mono}>{v}</b> },
    { title: 'Equipment Name', dataIndex: 'name', key: 'n', ellipsis: true },
    { title: 'Substrate', dataIndex: 'substrate', key: 's', render: (v: string) => v || '—' },
    { title: 'Total SQM', dataIndex: 'original', key: 'q', align: 'right', render: (v: number) => nf(v) },
  ]

  return (
    <div>
      <Card size="small" style={{ marginBottom: 12 }}>
        <Table sticky={{ offsetHeader: 64 }} size="small" rowKey="code" columns={summaryCols} dataSource={summary}
          pagination={false} scroll={{ x: 'max-content' }} />
      </Card>
      <Collapse size="small" items={summary.map((s) => ({
        key: s.code,
        label: (
          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <CodePill code={s.code} />
            <span style={{ fontSize: '0.75rem', opacity: 0.8 }}>{s.shortName || '—'}</span>
            <span style={{ ...mono, fontSize: '0.7rem', opacity: 0.65, flex: 1 }}>
              {s.equipment} equipment · {nf(s.sqm)} SQM
            </span>
            {/* legacy parity: one xlsx per system code */}
            <ScopedExport exportKey="system-code-report" siteId={siteId}
              params={{ code: s.code }} pdf={false} />
          </span>
        ),
        children: (
          <Table sticky={{ offsetHeader: 64 }} size="small" rowKey={(u) => `${u.tag}|${u.code}`} columns={eqCols}
            dataSource={units.filter((u) => u.code === s.code)
              .sort((a, b) => (a.tag < b.tag ? -1 : a.tag > b.tag ? 1 : 0))}
            pagination={false} scroll={{ x: 'max-content' }} />
        ),
      }))} />
    </div>
  )
}
