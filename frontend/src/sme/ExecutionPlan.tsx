/**
 * frontend/src/sme/ExecutionPlan.tsx — ⚙️ Execution Plan (Phase S5).
 * React rebuild of legacy Tab 4 with its three sub-views:
 *   ⚙️ Execution Plan — session-scoped critical-code analysis: pick an
 *     equipment + its critical system code, see the RED 1️⃣ critical shortage
 *     section then AMBER 2️⃣–N️⃣ per-code sections, and a narrative summary.
 *   📋 Progress List — plan-vs-done per (tag, code) from the snapshot, with
 *     date-wise production detail blocks from GET /sme/production-log.
 *   📊 Consumption Comparison — expected vs actual per (tag, code, material)
 *     with the legacy ±1% variance coloring (over amber · under blue · green).
 * All aggregation is client-side; the log endpoint is a pure Canon-safe read.
 */
import { useMemo, useState } from 'react'
import { Alert, Card, Col, Collapse, Empty, Radio, Row, Select, Skeleton, Space, Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useSmeProductionLog, useSmeSnapshot } from '../api/hooks'
import type { SmeLogRow } from '../api/hooks'
import { buildModel, runPlan, syscodeCompare, unitKey } from './engine'
import type { AllocationLine, SmeModel } from './engine'
import { allUnits, fc } from './insights'
import KpiDrill from './KpiDrill'
import { FulfilPill } from './PriorityList'
import { useScenario } from './ScenarioContext'
import { codeStats } from './session'

const mono: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }
const nf = (v: number, d = 3) =>
  v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: d })

const CodePill = ({ code }: { code: string }) => (
  <span style={{
    ...mono, border: '1px solid rgba(212,175,55,.5)', color: '#D4AF37',
    borderRadius: 6, padding: '0 6px', fontSize: '0.68rem', fontWeight: 700,
  }}>Code {code}</span>
)

const shortageCols: ColumnsType<AllocationLine> = [
  { title: 'Material', dataIndex: 'Material_Code', key: 'c', width: 120 },
  { title: 'Name', dataIndex: 'Material_Name', key: 'n', ellipsis: true },
  { title: 'UOM', dataIndex: 'UOM', key: 'u', width: 60 },
  { title: 'Demand', dataIndex: 'Demand_Qty', key: 'd', align: 'right', render: (v: number) => nf(v) },
  { title: 'Allocated', dataIndex: 'Allocated_Qty', key: 'a', align: 'right', render: (v: number) => nf(v) },
  {
    title: 'To Order', dataIndex: 'Shortfall_Qty', key: 's', align: 'right',
    render: (v: number) => <b style={{ color: '#EF4444' }}>{nf(v)}</b>,
  },
  {
    title: 'Fulfillment', dataIndex: 'Fulfillment_Pct', key: 'f', align: 'right', width: 100,
    render: (v: number) => <span style={{ color: fc(v), fontWeight: 700 }}>{v.toFixed(1)}%</span>,
  },
]

// ─── ⚙️ Sub-view 1: critical-code execution plan ─────────────────────────────
function ExecMain({ model }: { model: SmeModel }) {
  const scenario = useScenario()
  const plan = useMemo(() => runPlan(model, scenario.order), [model, scenario.order])
  const perCode = useMemo(() => codeStats(plan.lines), [plan])
  const [tag, setTag] = useState<string | undefined>()
  const [critical, setCritical] = useState<string | undefined>()

  if (scenario.order.length === 0) {
    return <Alert type="info" showIcon title="No session yet"
      description="The execution plan sequences the session's procurement — build one in the 🔍 Session Builder tab first." />
  }
  const sessionTags = scenario.order.filter((t) =>
    plan.lines.some((l) => l.Equipment_Tag_No === t))
  const selTag = tag && sessionTags.includes(tag) ? tag : sessionTags[0]
  const tagCodes = [...new Set(plan.lines.filter((l) => l.Equipment_Tag_No === selTag)
    .map((l) => l.Lining_System_Code))].sort(syscodeCompare)
  // Default critical code: the LOWEST-fulfillment code of the equipment.
  const worst = [...tagCodes].sort((a, b) =>
    (perCode.get(unitKey(selTag!, a))?.fulfillPct ?? 100)
    - (perCode.get(unitKey(selTag!, b))?.fulfillPct ?? 100))[0]
  const selCode = critical && tagCodes.includes(critical) ? critical : worst
  const cs = selCode ? perCode.get(unitKey(selTag!, selCode)) : undefined
  const critLines = plan.lines.filter((l) =>
    l.Equipment_Tag_No === selTag && l.Lining_System_Code === selCode && l.Shortfall_Qty > 0)
  const otherCodes = tagCodes.filter((c) => c !== selCode)
  const allShort = plan.lines.filter((l) => l.Equipment_Tag_No === selTag && l.Shortfall_Qty > 0)

  return (
    <div>
      <Space wrap style={{ marginBottom: 12 }}>
        <Select style={{ width: 300 }} value={selTag} onChange={setTag}
          options={sessionTags.map((t) => ({
            value: t, label: `${t} — ${model.tagMeta.get(t)?.Name?.slice(0, 24) ?? ''}`,
          }))} />
        <Select style={{ width: 220 }} value={selCode} onChange={setCritical}
          options={tagCodes.map((c) => ({
            value: c,
            label: `Code ${c} — ${(perCode.get(unitKey(selTag!, c))?.fulfillPct ?? 100).toFixed(1)}%`,
          }))} />
      </Space>

      {cs && (
        <Card size="small" style={{ borderColor: 'rgba(212,175,55,.55)', marginBottom: 12 }}>
          <Space wrap size="large">
            <span><CodePill code={cs.code} /> <b>{cs.shortName || '—'}</b></span>
            <span style={mono}>{nf(cs.sqm, 1)} SQM</span>
            <FulfilPill pct={cs.fulfillPct} />
            <span style={{ fontSize: '0.8rem', opacity: 0.8 }}>
              {critLines.length === 0
                ? 'This system code is fully covered by current stock.'
                : `${critLines.length} material${critLines.length > 1 ? 's' : ''} short — procure these FIRST to unblock the critical code.`}
            </span>
          </Space>
        </Card>
      )}

      {/* 1️⃣ critical code — RED */}
      <Card size="small" style={{ borderColor: 'rgba(239,68,68,.6)', marginBottom: 12 }}
        title={<span style={{ color: '#EF4444', fontWeight: 700 }}>1️⃣ Critical — Code {selCode}</span>}>
        {critLines.length === 0 ? (
          <Alert type="success" showIcon title="No shortages — fully covered ✅" />
        ) : (
          <Table size="small" rowKey={(r) => `${r.Lining_System_Code}|${r.Material_Code}`}
            columns={shortageCols} dataSource={critLines} pagination={false}
            scroll={{ x: 'max-content' }} />
        )}
      </Card>

      {/* 2️⃣–N️⃣ other codes — AMBER */}
      {otherCodes.map((c, i) => {
        const ls = plan.lines.filter((l) =>
          l.Equipment_Tag_No === selTag && l.Lining_System_Code === c && l.Shortfall_Qty > 0)
        const st = perCode.get(unitKey(selTag!, c))
        return (
          <Card key={c} size="small" style={{ borderColor: 'rgba(245,158,11,.5)', marginBottom: 12 }}
            title={(
              <Space>
                <span style={{ color: '#F59E0B', fontWeight: 700 }}>{i + 2}️⃣ Code {c}</span>
                <span style={{ fontSize: '0.75rem', opacity: 0.75 }}>{st?.shortName}</span>
                {st && <FulfilPill pct={st.fulfillPct} />}
              </Space>
            )}>
            {ls.length === 0 ? (
              <Alert type="success" showIcon title="No shortages — fully covered ✅" />
            ) : (
              <Table size="small" rowKey={(r) => `${r.Lining_System_Code}|${r.Material_Code}`}
                columns={shortageCols} dataSource={ls} pagination={false}
                scroll={{ x: 'max-content' }} />
            )}
          </Card>
        )
      })}

      {/* Narrative summary */}
      <div style={{
        border: '1px solid rgba(212,175,55,.45)', background: 'rgba(212,175,55,.07)',
        borderRadius: 8, padding: '10px 14px', fontSize: '0.8rem',
      }}>
        {allShort.length === 0 ? (
          <>✅ <b style={mono}>{selTag}</b> is fully buildable with current stock — no procurement needed.</>
        ) : (
          <>
            Procurement strategy for <b style={mono}>{selTag}</b>: order the{' '}
            <b style={{ color: '#EF4444' }}>{critLines.length} critical-code material{critLines.length === 1 ? '' : 's'}</b>{' '}
            (Code {selCode}) first — total{' '}
            <b style={mono}>{nf(critLines.reduce((s, l) => s + l.Shortfall_Qty, 0))}</b> units —
            then the remaining <b style={mono}>{allShort.length - critLines.length}</b> shortage
            line{allShort.length - critLines.length === 1 ? '' : 's'} across{' '}
            {otherCodes.length} other code{otherCodes.length === 1 ? '' : 's'}
            {' '}(session-wide order list exportable from the 📦 Session Report tab).
          </>
        )}
      </div>
    </div>
  )
}

// ─── 📋 Sub-view 2: progress list + production details ───────────────────────
function ProgressList({ model, siteId }: { model: SmeModel; siteId?: string }) {
  const { data: log } = useSmeProductionLog(siteId)
  const [loc, setLoc] = useState<string | undefined>()
  const [status, setStatus] = useState('All')

  const rows = useMemo(() => allUnits(model).map((u) => {
    const pct = u.original > 0 ? Math.round((u.done / u.original) * 1000) / 10 : 0
    return {
      key: unitKey(u.tag, u.code),
      Location: u.location || '—', Tag: u.tag, Name: u.name,
      Code: u.code, System: u.shortName,
      Total_SQM: Math.round(u.original * 100) / 100,
      Completed_SQM: Math.round(u.done * 100) / 100,
      Remaining_SQM: Math.round(Math.max(u.original - u.done, 0) * 100) / 100,
      Completion_Pct: pct,
      Status: pct >= 100 ? '✅ Complete' : u.done > 0 ? '🔄 In Progress' : '⏳ Not Started',
    }
  }), [model])

  const locations = [...new Set(rows.map((r) => r.Location))].sort()
  const filtered = rows.filter((r) =>
    (!loc || r.Location === loc)
    && (status === 'All' || r.Status.includes(status)))

  const cols: ColumnsType<(typeof rows)[number]> = [
    { title: 'Location', dataIndex: 'Location', key: 'l' },
    { title: 'Equipment Tag', dataIndex: 'Tag', key: 't', render: (v: string) => <b style={mono}>{v}</b> },
    { title: 'Name', dataIndex: 'Name', key: 'n', ellipsis: true },
    { title: 'Code', dataIndex: 'Code', key: 'c', width: 70 },
    { title: 'System', dataIndex: 'System', key: 'sy', ellipsis: true },
    { title: 'Total SQM', dataIndex: 'Total_SQM', key: 'ts', align: 'right', render: (v: number) => nf(v, 1) },
    { title: 'Completed', dataIndex: 'Completed_SQM', key: 'cs', align: 'right', render: (v: number) => nf(v, 1) },
    { title: 'Remaining', dataIndex: 'Remaining_SQM', key: 'rs', align: 'right', render: (v: number) => nf(v, 1) },
    {
      title: 'Completion %', dataIndex: 'Completion_Pct', key: 'p', align: 'right', width: 110,
      render: (v: number) => (
        <b style={{ color: v >= 100 ? '#10B981' : v > 0 ? '#F59E0B' : '#EF4444' }}>{v.toFixed(1)}%</b>
      ),
    },
    { title: 'Status', dataIndex: 'Status', key: 'st', width: 130 },
  ]

  // Date-wise production detail blocks (committed log entries).
  const detail = useMemo(() => {
    const byUnit = new Map<string, Map<string, { sqm: number; mats: SmeLogRow[] }>>()
    for (const r of log ?? []) {
      const k = unitKey(r.Equipment_Tag_No, r.Lining_System_Code)
      if (!byUnit.has(k)) byUnit.set(k, new Map())
      const days = byUnit.get(k)!
      if (!days.has(r.entry_date)) days.set(r.entry_date, { sqm: Number(r.SQM_Completed ?? 0), mats: [] })
      days.get(r.entry_date)!.mats.push(r)
    }
    return byUnit
  }, [log])

  return (
    <div>
      <Space wrap style={{ marginBottom: 10 }}>
        <Select allowClear placeholder="All locations" style={{ width: 180 }} value={loc}
          onChange={setLoc} options={locations.map((l) => ({ value: l, label: l }))} />
        <Select style={{ width: 170 }} value={status} onChange={setStatus}
          options={['All', 'Complete', 'In Progress', 'Not Started']
            .map((s) => ({ value: s, label: s }))} />
      </Space>
      <Table size="small" rowKey="key" columns={cols} dataSource={filtered}
        pagination={{ pageSize: 15, showTotal: (t) => `${t} scopes` }}
        scroll={{ x: 'max-content' }} />

      <Card size="small" style={{ marginTop: 12 }} title="📆 Production details (committed entries)">
        {detail.size === 0 ? (
          <Empty description="No committed SME consumption entries yet — details appear once daily SQM entries are approved." />
        ) : (
          <Collapse size="small" items={[...detail.entries()].map(([k, days]) => {
            const [tag, code] = k.split('\u0000')
            return {
              key: k,
              label: <span><b style={mono}>{tag}</b> <CodePill code={code} /> — {days.size} day{days.size > 1 ? 's' : ''}</span>,
              children: [...days.entries()].sort().map(([date, d]) => (
                <div key={date} style={{ marginBottom: 10 }}>
                  <div style={{ ...mono, fontSize: '0.75rem', fontWeight: 700, marginBottom: 4 }}>
                    {date} — {nf(d.sqm, 2)} SQM done
                  </div>
                  <Table size="small" rowKey="Material_Code" pagination={false}
                    scroll={{ x: 'max-content' }}
                    columns={[
                      { title: 'Material', dataIndex: 'Material_Code', key: 'm' },
                      { title: 'Expected', dataIndex: 'Expected_Qty', key: 'e', align: 'right', render: (v: number) => nf(Number(v ?? 0)) },
                      { title: 'Actual', dataIndex: 'Actual_Qty', key: 'a', align: 'right', render: (v: number) => nf(Number(v ?? 0)) },
                    ]}
                    dataSource={d.mats} />
                </div>
              )),
            }
          })} />
        )}
      </Card>
    </div>
  )
}

// ─── 📊 Sub-view 3: consumption comparison (expected vs actual) ──────────────
function ConsumptionComparison({ model, siteId }: { model: SmeModel; siteId?: string }) {
  const { data: log, isLoading } = useSmeProductionLog(siteId)
  const [locs, setLocs] = useState<string[]>([])
  const [tags, setTags] = useState<string[]>([])
  const [codes, setCodes] = useState<string[]>([])

  const matName = useMemo(() => {
    const m = new Map<string, { name: string; uom: string }>()
    for (const rows of model.recipesByCode.values()) {
      for (const r of rows) if (!m.has(r.Material_Code)) m.set(r.Material_Code, { name: r.Material_Name, uom: r.UOM })
    }
    return m
  }, [model])

  const agg = useMemo(() => {
    // SQM dedup per (date, tag, code) — legacy lines 5909–5935.
    const sqmSeen = new Map<string, number>()
    const totals = new Map<string, { tag: string; code: string; mat: string; expected: number; actual: number }>()
    for (const r of log ?? []) {
      const dk = `${r.entry_date}|${r.Equipment_Tag_No}|${r.Lining_System_Code}`
      if (!sqmSeen.has(dk)) sqmSeen.set(dk, Number(r.SQM_Completed ?? 0))
      const mk = `${r.Equipment_Tag_No}|${r.Lining_System_Code}|${r.Material_Code}`
      const t = totals.get(mk) ?? {
        tag: r.Equipment_Tag_No, code: r.Lining_System_Code,
        mat: r.Material_Code, expected: 0, actual: 0,
      }
      t.expected += Number(r.Expected_Qty ?? 0)
      t.actual += Number(r.Actual_Qty ?? 0)
      totals.set(mk, t)
    }
    const sqmByUnit = new Map<string, number>()
    for (const [dk, sqm] of sqmSeen) {
      const [, tag, code] = dk.split('|')
      const k = `${tag}|${code}`
      sqmByUnit.set(k, (sqmByUnit.get(k) ?? 0) + sqm)
    }
    return [...totals.values()].map((t) => {
      const meta = model.tagMeta.get(t.tag)
      const variance = t.actual - t.expected
      return {
        key: `${t.tag}|${t.code}|${t.mat}`,
        Location: meta?.Location ?? '—', Tag: t.tag, Code: t.code,
        SQM_Done: Math.round((sqmByUnit.get(`${t.tag}|${t.code}`) ?? 0) * 100) / 100,
        Material: t.mat,
        Name: matName.get(t.mat)?.name ?? '', UOM: matName.get(t.mat)?.uom ?? '',
        Expected: Math.round(t.expected * 1000) / 1000,
        Actual: Math.round(t.actual * 1000) / 1000,
        Variance: Math.round(variance * 1000) / 1000,
        Variance_Pct: t.expected > 0 ? Math.round((variance / t.expected) * 1000) / 10 : null,
      }
    })
  }, [log, model, matName])

  if (isLoading) return <Skeleton active paragraph={{ rows: 5 }} />
  if (agg.length === 0) {
    return <Alert type="info" showIcon title="No committed consumption yet"
      description="Expected-vs-actual variance appears here once SME daily entries are committed by the HOD." />
  }

  const filtered = agg.filter((r) =>
    (locs.length === 0 || locs.includes(r.Location))
    && (tags.length === 0 || tags.includes(r.Tag))
    && (codes.length === 0 || codes.includes(r.Code)))
  const tagPool = [...new Set(agg.filter((r) => locs.length === 0 || locs.includes(r.Location)).map((r) => r.Tag))].sort()
  const codePool = [...new Set(agg.filter((r) =>
    (locs.length === 0 || locs.includes(r.Location)) && (tags.length === 0 || tags.includes(r.Tag)))
    .map((r) => r.Code))].sort(syscodeCompare)

  const totE = filtered.reduce((s, r) => s + r.Expected, 0)
  const totA = filtered.reduce((s, r) => s + r.Actual, 0)

  // Legacy ±1% tinting: over amber · under blue · on-target green.
  const varBg = (p: number | null) =>
    p === null ? undefined
      : p > 1 ? 'rgba(245,158,11,.10)' : p < -1 ? 'rgba(59,130,246,.10)' : 'rgba(16,185,129,.08)'

  const cols: ColumnsType<(typeof agg)[number]> = [
    { title: 'Location', dataIndex: 'Location', key: 'l' },
    { title: 'Tag', dataIndex: 'Tag', key: 't', render: (v: string) => <b style={mono}>{v}</b> },
    { title: 'Code', dataIndex: 'Code', key: 'c', width: 70 },
    { title: 'SQM Done', dataIndex: 'SQM_Done', key: 'sq', align: 'right', render: (v: number) => nf(v, 2) },
    { title: 'Material', dataIndex: 'Material', key: 'm' },
    { title: 'Name', dataIndex: 'Name', key: 'n', ellipsis: true },
    { title: 'Expected', dataIndex: 'Expected', key: 'e', align: 'right', render: (v: number) => nf(v) },
    { title: 'Actual', dataIndex: 'Actual', key: 'a', align: 'right', render: (v: number) => nf(v) },
    { title: 'Variance', dataIndex: 'Variance', key: 'v', align: 'right', render: (v: number) => nf(v) },
    {
      title: 'Variance %', dataIndex: 'Variance_Pct', key: 'p', align: 'right', width: 100,
      render: (v: number | null) => v === null ? '—' : (
        <b style={{ color: v > 1 ? '#F59E0B' : v < -1 ? '#3B82F6' : '#10B981' }}>
          {v > 0 ? '+' : ''}{v.toFixed(1)}%
        </b>
      ),
    },
  ]

  return (
    <div>
      <Space wrap style={{ marginBottom: 10 }}>
        <Select mode="multiple" allowClear placeholder="All locations" style={{ minWidth: 170 }}
          value={locs} onChange={setLocs} maxTagCount="responsive"
          options={[...new Set(agg.map((r) => r.Location))].sort().map((l) => ({ value: l, label: l }))} />
        <Select mode="multiple" allowClear placeholder="All equipment" style={{ minWidth: 190 }}
          value={tags} onChange={setTags} maxTagCount="responsive"
          options={tagPool.map((t) => ({ value: t, label: t }))} />
        <Select mode="multiple" allowClear placeholder="All codes" style={{ minWidth: 140 }}
          value={codes} onChange={setCodes} maxTagCount="responsive"
          options={codePool.map((c) => ({ value: c, label: `Code ${c}` }))} />
      </Space>
      <Row gutter={[12, 12]} style={{ marginBottom: 10 }}>
        <Col flex="1 1 140px"><KpiDrill title="Rows" value={String(filtered.length)}
          drillTitle="Comparison rows" rows={filtered.map((r) => ({
            Tag: r.Tag, Code: r.Code, Material: r.Material, 'Variance %': r.Variance_Pct ?? '—',
          }))} /></Col>
        <Col flex="1 1 140px"><KpiDrill title="Total Expected" value={nf(totE)}
          drillTitle="Expected by material" rows={filtered.map((r) => ({ Material: r.Material, Expected: r.Expected }))} /></Col>
        <Col flex="1 1 140px"><KpiDrill title="Total Actual" value={nf(totA)}
          drillTitle="Actual by material" rows={filtered.map((r) => ({ Material: r.Material, Actual: r.Actual }))} /></Col>
        <Col flex="1 1 140px"><KpiDrill title="Variance" value={nf(totA - totE)}
          accent={Math.abs(totA - totE) > 0.001 ? (totA > totE ? '#F59E0B' : '#3B82F6') : '#10B981'}
          drillTitle="Variance by row" rows={filtered.map((r) => ({
            Tag: r.Tag, Material: r.Material, Variance: r.Variance,
          }))} /></Col>
      </Row>
      <Table size="small" rowKey="key" columns={cols} dataSource={filtered}
        pagination={{ pageSize: 15, showTotal: (t) => `${t} rows` }}
        scroll={{ x: 'max-content' }}
        onRow={(r) => ({ style: { background: varBg(r.Variance_Pct) } })} />
    </div>
  )
}

// ─── Tab shell ────────────────────────────────────────────────────────────────
export default function ExecutionPlan({ siteId }: { siteId?: string }) {
  const { data: snap, isLoading } = useSmeSnapshot(siteId)
  const [sub, setSub] = useState('plan')
  const model = useMemo(
    () => (snap ? buildModel(snap.equipment, snap.recipes, snap.materials, snap.progress) : null),
    [snap])

  if (isLoading) return <Skeleton active paragraph={{ rows: 8 }} />
  if (!snap || !model) return <Alert type="warning" showIcon title="SME model unavailable" />

  return (
    <div>
      <Radio.Group value={sub} onChange={(e) => setSub(e.target.value)}
        optionType="button" buttonStyle="solid" style={{ marginBottom: 14 }}
        options={[{ label: '⚙️ Execution Plan', value: 'plan' },
          { label: '📋 Progress List', value: 'progress' },
          { label: '📊 Consumption Comparison', value: 'comparison' }]} />
      {sub === 'plan' && <ExecMain model={model} />}
      {sub === 'progress' && <ProgressList model={model} siteId={siteId} />}
      {sub === 'comparison' && <ConsumptionComparison model={model} siteId={siteId} />}
    </div>
  )
}
