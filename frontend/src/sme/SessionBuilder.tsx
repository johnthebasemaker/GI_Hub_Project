/**
 * frontend/src/sme/SessionBuilder.tsx — 🔍 Selective Equipment Entry
 * (Phase S3). React rebuild of legacy Tab 1: filter the equipment pool, add
 * tags to the session, drag (or arrow) them into priority order — the TS
 * engine re-cascades the whole allocation instantly in the browser on every
 * change. The right panel shows the selected equipment's live per-code
 * detail; selecting a tag that is NOT in the session shows an added-last
 * what-if preview (impossible in the Streamlit version).
 */
import { useMemo, useState } from 'react'
import { App, Alert, Button, Card, Col, Empty, Row, Select, Skeleton, Space, Typography } from 'antd'
import { ClearOutlined, LinkOutlined, PlusOutlined } from '@ant-design/icons'
import { useSmeSnapshot } from '../api/hooks'
import { buildModel, runPlan } from './engine'
import { applyFilters, filterOptions } from './insights'
import type { DashFilters } from './insights'
import PriorityList from './PriorityList'
import { useScenario } from './ScenarioContext'
import { tagStats } from './session'
import TagDetail from './TagDetail'

const secHdr: React.CSSProperties = {
  fontFamily: 'JetBrains Mono, monospace', fontSize: '0.68rem', fontWeight: 700,
  letterSpacing: '.13em', textTransform: 'uppercase', opacity: 0.65,
}

export default function SessionBuilder({ siteId }: { siteId?: string }) {
  const { message } = App.useApp()
  const { data: snap, isLoading } = useSmeSnapshot(siteId)
  const scenario = useScenario()
  const [filters, setFilters] = useState<DashFilters>({ locations: [], types: [], codes: [], substrates: [] })
  const [picked, setPicked] = useState<string | undefined>()
  const [selected, setSelected] = useState<string | undefined>()

  const model = useMemo(
    () => (snap ? buildModel(snap.equipment, snap.recipes, snap.materials, snap.progress) : null),
    [snap])
  const options = useMemo(() => (model ? filterOptions(model, filters) : null), [model, filters])
  const pool = useMemo(() => {
    if (!model) return []
    const units = applyFilters(model, filters)
    const seen = new Set<string>()
    const out: { tag: string; name: string }[] = []
    for (const u of units) {
      if (!seen.has(u.tag)) { seen.add(u.tag); out.push({ tag: u.tag, name: u.name }) }
    }
    return out
  }, [model, filters])

  // THE live cascade: recomputed client-side on every order change.
  const plan = useMemo(
    () => (model ? runPlan(model, scenario.order) : null), [model, scenario.order])
  const stats = useMemo(
    () => (model && plan ? tagStats(model, plan.lines) : new Map()), [model, plan])

  // Right panel: in-session tags show live numbers; others an added-last preview.
  const detail = useMemo(() => {
    if (!model || !selected) return null
    const inSession = scenario.order.includes(selected)
    const p = inSession || !plan ? plan : runPlan(model, [...scenario.order, selected])
    if (!p) return null
    const lines = p.lines.filter((l) => l.Equipment_Tag_No === selected)
    const stat = tagStats(model, p.lines).get(selected)
    return stat ? { lines, stat, preview: !inSession } : null
  }, [model, plan, scenario.order, selected])

  if (isLoading) return <Skeleton active paragraph={{ rows: 8 }} />
  if (!snap || !model || !options) {
    return <Alert type="warning" showIcon title="SME model unavailable" />
  }

  const selectProps = {
    mode: 'multiple' as const, allowClear: true, maxTagCount: 'responsive' as const,
    style: { width: '100%' },
  }

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={11}>
        <Card size="small" title={<span style={secHdr}>🎛 Find equipment</span>}>
          <Row gutter={[8, 8]}>
            <Col span={8}><Select {...selectProps} placeholder="All locations" value={filters.locations}
              onChange={(v) => setFilters({ ...filters, locations: v })}
              options={options.locations.map((l) => ({ value: l, label: l }))} /></Col>
            <Col span={8}><Select {...selectProps} placeholder="All types" value={filters.types}
              onChange={(v) => setFilters({ ...filters, types: v })}
              options={options.types.map((t) => ({ value: t, label: t }))} /></Col>
            <Col span={8}><Select {...selectProps} placeholder="All codes" value={filters.codes}
              onChange={(v) => setFilters({ ...filters, codes: v })}
              options={options.codes.map((c) => ({ value: c.code, label: `Code ${c.code}` }))} /></Col>
          </Row>
          <Space.Compact style={{ width: '100%', marginTop: 10 }}>
            <Select showSearch allowClear placeholder="Pick an equipment tag…" value={picked}
              style={{ width: '100%' }} optionFilterProp="label"
              onChange={(v) => { setPicked(v); if (v) setSelected(v) }}
              options={pool.map((p) => ({
                value: p.tag,
                label: `${p.tag}${p.name ? ` — ${p.name.slice(0, 28)}` : ''}${scenario.order.includes(p.tag) ? '  ✓ in session' : ''}`,
              }))} />
            <Button type="primary" icon={<PlusOutlined />}
              disabled={!picked || scenario.order.includes(picked)}
              onClick={() => { if (picked) { scenario.addTag(picked); setSelected(picked) } }}>
              Add
            </Button>
          </Space.Compact>
        </Card>

        <Card size="small" style={{ marginTop: 16 }}
          title={<span style={secHdr}>📋 Session priority — drag to re-cascade ({scenario.order.length})</span>}
          extra={(
            <Space>
              <Button size="small" icon={<LinkOutlined />} onClick={async () => {
                try {
                  await navigator.clipboard.writeText(scenario.shareUrl())
                  message.success('Scenario link copied')
                } catch { message.info(scenario.shareUrl()) }
              }}>Share</Button>
              <Button size="small" danger icon={<ClearOutlined />}
                disabled={scenario.order.length === 0}
                onClick={() => { scenario.clear(); message.success('Session cleared') }}>
                Clear all
              </Button>
            </Space>
          )}>
          {scenario.order.length === 0 ? (
            <Alert type="info" showIcon title="No equipment in the session yet"
              description="Pick equipment on the left and press Add. Drag rows (or use the arrows) to set build priority — the allocation cascade recomputes instantly." />
          ) : (
            <PriorityList order={scenario.order} stats={stats}
              onReorder={scenario.setOrder} onMove={scenario.moveTag}
              onRemove={(t) => { scenario.removeTag(t); if (selected === t) setSelected(undefined) }}
              onSelect={setSelected} selected={selected} />
          )}
        </Card>
      </Col>

      <Col xs={24} lg={13}>
        <Card size="small" title={<span style={secHdr}>🔍 Equipment detail (live cascade)</span>}>
          {detail ? (
            <>
              <Typography.Title level={5} style={{ marginTop: 0, fontFamily: 'JetBrains Mono, monospace' }}>
                {selected} <span style={{ fontWeight: 400, fontSize: '0.8rem', opacity: 0.7 }}>{detail.stat.name}</span>
              </Typography.Title>
              <TagDetail lines={detail.lines} stat={detail.stat} preview={detail.preview} />
            </>
          ) : (
            <Empty description="Select an equipment tag to view its live per-code material detail" />
          )}
        </Card>
      </Col>
    </Row>
  )
}
