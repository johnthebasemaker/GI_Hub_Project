import { useMemo, useState } from 'react'
import { Alert, App, Button, Card, Collapse, Descriptions, InputNumber, Radio, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { CalculatorOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

interface SystemInfo {
  code: string; short_name: string; substrate: string; lining_system: string
  sqm: { equipment_count: number; original_sqm: number; done_sqm: number; pending_sqm: number }
}

interface CalcLine {
  sap_code: string | null; material_code: string | null; component: string
  material_name: string; uom: string; for_1_sqm: number; required_qty: number
  package_size: number | null; packages_needed: number | null
  available_stock: number | null; pooled_saps: number
  shortfall_qty: number | null; explanation: string
}

interface SystemBlock {
  code: string; short_name: string; lining_system: string; substrate: string
  thickness: string; target_sqm: number; lines: CalcLine[]
  totals: { line_count: number; shortfall_lines: number }
}

interface AggLine extends Omit<CalcLine, 'for_1_sqm'> { systems: string[] }

interface CalcResult {
  codes: string[]; mode: 'global' | 'per_system'; target_total_sqm: number
  systems: SystemBlock[]
  aggregate: { lines: AggLine[]; totals: { line_count: number; shortfall_lines: number } }
}

// Whole-number quantities render clean ("5", not "5.00"); real decimals keep
// their precision — the global smart-decimals convention.
const fmt = (v: number | null | undefined) =>
  v == null ? '—' : Number.isInteger(v) ? String(v) : String(Math.round(v * 10000) / 10000)

// Header cells must never truncate (Phase-4 polish) — reserve the full title.
const noWrapHeader = () => ({ style: { whiteSpace: 'nowrap' as const } })

// Availability is pooled per Material Code across every variant SAP (the SAP
// code is an internal id and is deliberately NOT displayed).
const stockCell = (v: number | null, r: { shortfall_qty: number | null; pooled_saps: number }) =>
  v == null ? '—'
    : r.shortfall_qty
      ? <Tag color="red">{fmt(v)} (short {fmt(r.shortfall_qty)})</Tag>
      : <Tag color="green">{fmt(v)} ✓</Tag>

// 🧮 Smart Calculator — "system codes + target SQM(s) → segregated material
// list with explanations" (recipe demand model: For_1_SQM × SQM; live ERP
// stock pooled per Material_Code over all variant SAPs).
export default function SmartCalculator({ siteId, stickyTop }:
  { siteId?: string; stickyTop?: number }) {
  const { message } = App.useApp()
  const [selCodes, setSelCodes] = useState<string[]>([])
  const [sqmMode, setSqmMode] = useState<'global' | 'per'>('global')
  const [sqm, setSqm] = useState<number | null>(100)
  const [perSqm, setPerSqm] = useState<Record<string, number | null>>({})
  const [result, setResult] = useState<CalcResult | null>(null)
  const [busy, setBusy] = useState(false)

  const systems = useQuery({
    queryKey: ['/entry/lining-systems', siteId],
    queryFn: async () => (await api.get('/entry/lining-systems',
      { params: siteId ? { site_id: siteId } : {} })).data as { systems: SystemInfo[] },
  })

  const selected = useMemo(
    () => (systems.data?.systems ?? []).filter((s) => selCodes.includes(s.code)),
    [systems.data, selCodes])

  const perValues = selCodes.map((c) => perSqm[c])
  const ready = selCodes.length > 0 && (sqmMode === 'global'
    ? !!sqm && sqm > 0
    : perValues.every((v) => !!v && v > 0))

  const run = async () => {
    if (!ready) return
    setBusy(true)
    try {
      const params: Record<string, string | number> = { codes: selCodes.join(',') }
      if (sqmMode === 'global') params.sqm = sqm as number
      else params.sqms = selCodes.map((c) => perSqm[c]).join(',')
      const r = await api.get('/sme/calculator', { params })
      setResult(r.data as CalcResult)
    } catch (e) {
      const x = e as { response?: { data?: { detail?: string } } }
      message.error(x?.response?.data?.detail ?? 'Calculation failed')
    } finally {
      setBusy(false)
    }
  }

  // Shared display columns — Material Code instead of SAP (internal id).
  const aggColumns: ColumnsType<AggLine> = [
    { title: 'Material Code', dataIndex: 'material_code', width: 130,
      onHeaderCell: noWrapHeader, render: (v) => v ?? '—' },
    { title: 'Component', dataIndex: 'component', width: 160,
      onHeaderCell: noWrapHeader, render: (v, r) => v || r.material_name || '—' },
    { title: 'Material', dataIndex: 'material_name', ellipsis: true,
      onHeaderCell: noWrapHeader },
    ...(result && result.systems.length > 1 ? [{
      title: 'Systems', dataIndex: 'systems', width: 140,
      onHeaderCell: noWrapHeader,
      render: (v: string[]) => v.map((c) => <Tag key={c}>{c}</Tag>),
    } as ColumnsType<AggLine>[number]] : []),
    { title: 'Required', dataIndex: 'required_qty', width: 120, align: 'right',
      onHeaderCell: noWrapHeader,
      render: (v: number, r) => <b>{fmt(v)} {r.uom}</b> },
    { title: 'Packs', dataIndex: 'packages_needed', width: 100, align: 'right',
      onHeaderCell: noWrapHeader,
      render: (v: number | null, r) => v != null
        ? `${v} × ${fmt(r.package_size)}` : '—' },
    { title: 'In Stock (all variants)', dataIndex: 'available_stock', width: 170,
      align: 'right', onHeaderCell: noWrapHeader, render: stockCell },
  ]

  const sysColumns: ColumnsType<CalcLine> = [
    { title: 'Material Code', dataIndex: 'material_code', width: 130,
      onHeaderCell: noWrapHeader, render: (v) => v ?? '—' },
    { title: 'Component', dataIndex: 'component', width: 160,
      onHeaderCell: noWrapHeader, render: (v, r) => v || r.material_name || '—' },
    { title: 'Material', dataIndex: 'material_name', ellipsis: true,
      onHeaderCell: noWrapHeader },
    { title: 'Per SQM', dataIndex: 'for_1_sqm', width: 100, align: 'right',
      onHeaderCell: noWrapHeader, render: (v: number, r) => `${fmt(v)} ${r.uom}` },
    { title: 'Required', dataIndex: 'required_qty', width: 120, align: 'right',
      onHeaderCell: noWrapHeader,
      render: (v: number, r) => <b>{fmt(v)} {r.uom}</b> },
    { title: 'Packs', dataIndex: 'packages_needed', width: 100, align: 'right',
      onHeaderCell: noWrapHeader,
      render: (v: number | null, r) => v != null
        ? `${v} × ${fmt(r.package_size)}` : '—' },
    { title: 'In Stock (all variants)', dataIndex: 'available_stock', width: 170,
      align: 'right', onHeaderCell: noWrapHeader, render: stockCell },
  ]

  return (
    <div>
      <Typography.Paragraph type="secondary">
        Pick one or more lining systems and target surface areas — the calculator
        returns every recipe component with the exact required quantity, pack counts
        and live stock coverage. Stock is pooled per Material Code across all its
        SAP variants, and quantities are aggregated across the selected systems.
      </Typography.Paragraph>
      <Card size="small" style={{ marginBottom: 16, maxWidth: 760 }}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Select mode="multiple" showSearch style={{ width: '100%' }}
            placeholder="Lining system(s)" maxTagCount="responsive"
            loading={systems.isFetching} value={selCodes} onChange={setSelCodes}
            optionFilterProp="label"
            options={(systems.data?.systems ?? []).map((s) => ({
              value: s.code,
              label: `${s.code} — ${s.short_name} (${s.substrate || '?'} · ${s.lining_system || '?'})`,
            }))} />
          <Space wrap>
            <Radio.Group value={sqmMode} onChange={(e) => setSqmMode(e.target.value)}
              options={[
                { label: 'One SQM for all', value: 'global' },
                { label: 'Per-system SQM', value: 'per' },
              ]} optionType="button" size="small" />
            {sqmMode === 'global' && (
              <InputNumber min={0.01} max={1000000} value={sqm} onChange={setSqm}
                addonAfter="SQM" style={{ width: 160 }} />
            )}
            <Button type="primary" icon={<CalculatorOutlined />} onClick={run}
              loading={busy} disabled={!ready}>
              Calculate
            </Button>
          </Space>
          {sqmMode === 'per' && selCodes.length > 0 && (
            <Space wrap>
              {selCodes.map((c) => (
                <InputNumber key={c} min={0.01} max={1000000}
                  value={perSqm[c] ?? null}
                  onChange={(v) => setPerSqm((p) => ({ ...p, [c]: v }))}
                  addonBefore={c} addonAfter="SQM" style={{ width: 220 }} />
              ))}
            </Space>
          )}
        </Space>
        {selected.length > 0 && (
          <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
            {selected.map((s) => (
              <span key={s.code} style={{ display: 'block' }}>
                {s.code}: {fmt(s.sqm.done_sqm)} SQM done ·{' '}
                <b>{fmt(s.sqm.pending_sqm)} SQM pending</b> of {fmt(s.sqm.original_sqm)}{' '}
                across {s.sqm.equipment_count} unit(s).
              </span>
            ))}
          </Typography.Paragraph>
        )}
      </Card>

      {result && (
        <>
          <Descriptions size="small" bordered column={4} style={{ marginBottom: 12 }}
            items={[
              { key: 's', label: result.systems.length > 1 ? 'Systems' : 'System',
                children: result.systems.map((s) => `${s.code} — ${s.short_name}`).join(' · ') },
              { key: 'm', label: 'SQM mode',
                children: result.mode === 'global' ? 'one figure for all' : 'per system' },
              { key: 'q', label: 'Total target',
                children: `${fmt(result.target_total_sqm)} SQM` },
              { key: 'n', label: 'Materials',
                children: String(result.aggregate.totals.line_count) },
            ]} />
          {result.aggregate.totals.shortfall_lines > 0 ? (
            <Alert type="warning" showIcon style={{ marginBottom: 12 }}
              title={`${result.aggregate.totals.shortfall_lines} of ${result.aggregate.totals.line_count} `
                + 'material(s) are short on pooled stock — see the red rows.'} />
          ) : (
            <Alert type="success" showIcon style={{ marginBottom: 12 }}
              title="Every material is covered by current pooled stock." />
          )}
          <Table<AggLine> size="small"
            rowKey={(r) => `${r.material_code}-${r.sap_code}-${r.component}`}
            columns={aggColumns} dataSource={result.aggregate.lines} pagination={false}
            sticky={{ offsetHeader: stickyTop ?? 64 }} scroll={{ x: 'max-content' }}
            expandable={{
              expandedRowRender: (r) => (
                <Typography.Text type="secondary">{r.explanation}</Typography.Text>
              ),
            }} />
          {result.systems.length > 1 && (
            <Collapse style={{ marginTop: 16 }} items={result.systems.map((s) => ({
              key: s.code,
              label: `${s.code} — ${s.short_name} · ${fmt(s.target_sqm)} SQM`
                + (s.totals.shortfall_lines
                  ? ` · ${s.totals.shortfall_lines} short` : ' · covered'),
              children: (
                <Table<CalcLine> size="small"
                  rowKey={(r) => `${r.material_code}-${r.sap_code}-${r.component}`}
                  columns={sysColumns} dataSource={s.lines} pagination={false}
                  scroll={{ x: 'max-content' }}
                  expandable={{
                    expandedRowRender: (r) => (
                      <Typography.Text type="secondary">{r.explanation}</Typography.Text>
                    ),
                  }} />
              ),
            }))} />
          )}
        </>
      )}
    </div>
  )
}
