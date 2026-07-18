import { useMemo, useState } from 'react'
import { Alert, App, Button, Card, Descriptions, InputNumber, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { CalculatorOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

interface SystemInfo {
  code: string; short_name: string; substrate: string; lining_system: string
  sqm: { equipment_count: number; original_sqm: number; done_sqm: number; pending_sqm: number }
}

interface CalcLine {
  sap_code: string | null; material_code: string; component: string
  material_name: string; uom: string; for_1_sqm: number; required_qty: number
  package_size: number | null; packages_needed: number | null
  available_stock: number | null; shortfall_qty: number | null; explanation: string
}

interface CalcResult {
  code: string; short_name: string; lining_system: string; substrate: string
  thickness: string; target_sqm: number; lines: CalcLine[]
  totals: { line_count: number; shortfall_lines: number }
}

// Whole-number quantities render clean ("5", not "5.00"); real decimals keep
// their precision — the global smart-decimals convention.
const fmt = (v: number | null | undefined) =>
  v == null ? '—' : Number.isInteger(v) ? String(v) : String(Math.round(v * 10000) / 10000)

// 🧮 Smart Calculator — "system code + target SQM → segregated material list
// with explanations" (recipe demand model: For_1_SQM × SQM, live ERP stock
// through the sme_recipe.SAP_Code join).
export default function SmartCalculator({ siteId }: { siteId?: string }) {
  const { message } = App.useApp()
  const [code, setCode] = useState<string | undefined>()
  const [sqm, setSqm] = useState<number | null>(100)
  const [result, setResult] = useState<CalcResult | null>(null)
  const [busy, setBusy] = useState(false)

  const systems = useQuery({
    queryKey: ['/entry/lining-systems', siteId],
    queryFn: async () => (await api.get('/entry/lining-systems',
      { params: siteId ? { site_id: siteId } : {} })).data as { systems: SystemInfo[] },
  })

  const selected = useMemo(
    () => systems.data?.systems.find((s) => s.code === code),
    [systems.data, code])

  const run = async () => {
    if (!code || !sqm || sqm <= 0) return
    setBusy(true)
    try {
      const r = await api.get('/sme/calculator', { params: { code, sqm } })
      setResult(r.data as CalcResult)
    } catch (e) {
      const x = e as { response?: { data?: { detail?: string } } }
      message.error(x?.response?.data?.detail ?? 'Calculation failed')
    } finally {
      setBusy(false)
    }
  }

  const columns: ColumnsType<CalcLine> = [
    { title: 'Component', dataIndex: 'component', width: 160,
      render: (v, r) => v || r.material_name || '—' },
    { title: 'Material', dataIndex: 'material_name', ellipsis: true },
    { title: 'SAP', dataIndex: 'sap_code', width: 90, render: (v) => v ?? '—' },
    { title: 'Per SQM', dataIndex: 'for_1_sqm', width: 100, align: 'right',
      render: (v: number, r) => `${fmt(v)} ${r.uom}` },
    { title: 'Required', dataIndex: 'required_qty', width: 120, align: 'right',
      render: (v: number, r) => <b>{fmt(v)} {r.uom}</b> },
    { title: 'Packs', dataIndex: 'packages_needed', width: 90, align: 'right',
      render: (v: number | null, r) => v != null
        ? `${v} × ${fmt(r.package_size)}` : '—' },
    { title: 'In stock', dataIndex: 'available_stock', width: 110, align: 'right',
      render: (v: number | null, r) => v == null ? '—'
        : r.shortfall_qty
          ? <Tag color="red">{fmt(v)} (short {fmt(r.shortfall_qty)})</Tag>
          : <Tag color="green">{fmt(v)} ✓</Tag> },
  ]

  return (
    <div>
      <Typography.Paragraph type="secondary">
        Pick a lining system and a target surface area — the calculator returns every
        recipe component with the exact required quantity, pack counts and live stock
        coverage.
      </Typography.Paragraph>
      <Card size="small" style={{ marginBottom: 16, maxWidth: 720 }}>
        <Space wrap>
          <Select showSearch style={{ width: 340 }} placeholder="Lining system"
            loading={systems.isFetching} value={code} onChange={setCode}
            optionFilterProp="label"
            options={(systems.data?.systems ?? []).map((s) => ({
              value: s.code,
              label: `${s.code} — ${s.short_name} (${s.substrate || '?'} · ${s.lining_system || '?'})`,
            }))} />
          <InputNumber min={0.01} max={1000000} value={sqm} onChange={setSqm}
            addonAfter="SQM" style={{ width: 160 }} />
          <Button type="primary" icon={<CalculatorOutlined />} onClick={run}
            loading={busy} disabled={!code || !sqm}>
            Calculate
          </Button>
        </Space>
        {selected && (
          <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
            Site progress for this system: {fmt(selected.sqm.done_sqm)} SQM done ·{' '}
            <b>{fmt(selected.sqm.pending_sqm)} SQM pending</b> of {fmt(selected.sqm.original_sqm)}{' '}
            across {selected.sqm.equipment_count} unit(s).
          </Typography.Paragraph>
        )}
      </Card>

      {result && (
        <>
          <Descriptions size="small" bordered column={4} style={{ marginBottom: 12 }}
            items={[
              { key: 's', label: 'System', children: `${result.code} — ${result.short_name}` },
              { key: 'l', label: 'Lining', children: result.lining_system || '—' },
              { key: 't', label: 'Thickness', children: result.thickness || '—' },
              { key: 'q', label: 'Target', children: `${fmt(result.target_sqm)} SQM` },
            ]} />
          {result.totals.shortfall_lines > 0 ? (
            <Alert type="warning" showIcon style={{ marginBottom: 12 }}
              title={`${result.totals.shortfall_lines} of ${result.totals.line_count} `
                + 'component(s) are short on stock — see the red rows.'} />
          ) : (
            <Alert type="success" showIcon style={{ marginBottom: 12 }}
              title="Every component is covered by current stock." />
          )}
          <Table<CalcLine> size="small" rowKey={(r) => `${r.sap_code}-${r.component}`}
            columns={columns} dataSource={result.lines} pagination={false}
            sticky scroll={{ x: 'max-content' }}
            expandable={{
              expandedRowRender: (r) => (
                <Typography.Text type="secondary">{r.explanation}</Typography.Text>
              ),
            }} />
        </>
      )}
    </div>
  )
}
