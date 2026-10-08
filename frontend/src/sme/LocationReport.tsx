/**
 * frontend/src/sme/LocationReport.tsx — 📍 Location Report (Phase S4).
 * React rebuild of legacy Tab 3 with its dual mode:
 *   🌐 All Equipment — ONE global drag-priority order over every tag, with a
 *     KPI strip, per-equipment expanders and a suggestion panel;
 *   📍 Location Based — an INDEPENDENT drag order per location (legacy
 *     loc_order[loc]), each location cascading against a fresh full pool,
 *     with per-location color badges, exports and suggestion panels.
 * Priority state persists per site in localStorage with the legacy
 * stale-tag reconciliation (drop tags gone from the model, append new ones,
 * preserve the user's ordering). Exports POST the relevant order to the
 * Python oracle (/sme/plan/export) with a per-scope document title.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, App, Button, Card, Col, Collapse, Radio, Row, Skeleton, Space } from 'antd'
import { FileExcelOutlined, FilePdfOutlined, PlusOutlined } from '@ant-design/icons'
import { postDownloadDocument, useSmeSnapshot } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'
import { buildModel, runPlan } from './engine'
import type { SmeModel } from './engine'
import { allUnits, fcDot, locColor } from './insights'
import KpiDrill from './KpiDrill'
import PriorityList, { FulfilPill, StatusDot } from './PriorityList'
import { useScenario } from './ScenarioContext'
import { scopeBottleneckCoverage, tagStats } from './session'
import SuggestionPanel from './SuggestionPanel'
import TagDetail from './TagDetail'
import TierNote from './TierNote'
import { light, status } from '../theme/tokens'

const mono: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }
const secHdr: React.CSSProperties = {
  ...mono, fontSize: '0.68rem', fontWeight: 700, letterSpacing: '.13em',
  textTransform: 'uppercase', opacity: 0.65,
}
const nf = (v: number, d = 1) =>
  v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: d })

/** Legacy reconciliation (portal 5162–5177): drop stale, append new, keep order. */
export function reconcileOrder(stored: string[], current: string[]): string[] {
  const cur = new Set(current)
  const kept = stored.filter((t) => cur.has(t))
  const keptSet = new Set(kept)
  return [...kept, ...current.filter((t) => !keptSet.has(t))]
}

function usePersistedOrders(storageKey: string, siteKey: string) {
  const read = useCallback((): Record<string, string[]> => {
    try {
      const all = JSON.parse(localStorage.getItem(storageKey) ?? '{}')
      const mine = all?.[siteKey]
      return mine && typeof mine === 'object' ? mine : {}
    } catch { return {} }
  }, [storageKey, siteKey])
  const [orders, setOrders] = useState<Record<string, string[]>>(read)
  useEffect(() => { setOrders(read()) }, [read])
  const save = useCallback((scope: string, order: string[]) => {
    setOrders((prev) => {
      const next = { ...prev, [scope]: order }
      try {
        const all = JSON.parse(localStorage.getItem(storageKey) ?? '{}')
        localStorage.setItem(storageKey, JSON.stringify({ ...all, [siteKey]: next }))
      } catch { /* non-fatal */ }
      return next
    })
  }, [storageKey, siteKey])
  return { orders, save }
}

function ScopeExports({ order, siteId, scopeTitle, slug, location }: {
  order: string[]
  siteId?: string
  scopeTitle: string
  slug: string
  location?: string
}) {
  const { message } = App.useApp()
  const [busy, setBusy] = useState<string | null>(null)
  const dl = async (format: string) => {
    setBusy(format)
    try {
      // T3: 'location-report' renders the LEGACY workbook layout server-side
      // (main alloc table + 3 summary blocks); the legacy
      // {stem}_{user}_{date} filename arrives via Content-Disposition.
      await postDownloadDocument('/sme/plan/export',
        {
          priority_order: order, key: 'location-report', format,
          title: scopeTitle, ...(location ? { location } : {}),
          ...(siteId ? { site_id: siteId } : {}),
        }, `sme-location-${slug}.${format}`)
    } catch { message.error('Export failed') } finally { setBusy(null) }
  }
  return (
    <Space onClick={(e) => e.stopPropagation()}>
      <Button size="small" icon={<FileExcelOutlined />} loading={busy === 'xlsx'}
        onClick={() => dl('xlsx')}>Excel</Button>
      <Button size="small" icon={<FilePdfOutlined />} loading={busy === 'pdf'}
        onClick={() => dl('pdf')}>PDF</Button>
    </Space>
  )
}

/** One cascaded scope: priority list + per-equipment expanders + suggestions. */
function ScopeSection({ model, order, onOrder, siteId, scopeTitle, slug, showSuggestions, location }: {
  model: SmeModel
  order: string[]
  onOrder: (next: string[]) => void
  siteId?: string
  scopeTitle: string
  slug: string
  showSuggestions: boolean
  location?: string
}) {
  const { message } = App.useApp()
  const scenario = useScenario()
  const plan = useMemo(() => runPlan(model, order), [model, order])
  const stats = useMemo(() => tagStats(model, plan.lines), [model, plan])
  const move = (from: number, to: number) => {
    if (to < 0 || to >= order.length) return
    const next = [...order]
    const [x] = next.splice(from, 1)
    next.splice(to, 0, x)
    onOrder(next)
  }
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
        <ScopeExports order={order} siteId={siteId} scopeTitle={scopeTitle} slug={slug}
          location={location} />
      </div>
      <PriorityList order={order} stats={stats}
        onReorder={onOrder} onMove={move}
        onRemove={(t) => onOrder(order.filter((x) => x !== t))} />
      <Collapse size="small" style={{ marginTop: 8 }} items={order.filter((t) => stats.has(t)).map((tag) => {
        const st = stats.get(tag)!
        const inSession = scenario.order.includes(tag)
        return {
          key: tag,
          label: (
            <Space>
              <StatusDot pct={st.fulfillPct} />
              <b style={{ ...mono, fontSize: '0.78rem' }}>{tag}</b>
              <span style={{ fontSize: '0.72rem', opacity: 0.7 }}>{st.name.slice(0, 26)}</span>
              <FulfilPill pct={st.fulfillPct} />
              {st.fulfillWithOrderedPct > st.fulfillPct && (
                <span style={{ ...mono, fontSize: '0.64rem', color: status.low }}
                  title="Coverage once the open purchase orders land">
                  → {st.fulfillWithOrderedPct.toFixed(1)}% ordered
                </span>
              )}
            </Space>
          ),
          extra: (
            <Button size="small" type="text" icon={<PlusOutlined />} disabled={inSession}
              onClick={(e) => {
                e.stopPropagation()
                scenario.addTag(tag)
                message.success(`${tag} added to session`)
              }}>{inSession ? 'In session' : 'Session'}</Button>
          ),
          children: <TagDetail lines={plan.lines.filter((l) => l.Equipment_Tag_No === tag)} stat={st} />,
        }
      })} />
      {showSuggestions && order.length >= 2 && (
        <SuggestionPanel model={model} order={order}
          onPause={(t) => onOrder(order.filter((x) => x !== t))} />
      )}
    </div>
  )
}

export default function LocationReport({ siteId }: { siteId?: string }) {
  const { data: snap, isLoading } = useSmeSnapshot(siteId)
  const { user } = useAuth()
  const [mode, setMode] = useState<'loc' | 'all'>('loc')
  // Scoped by USER as well as site — same reason as ScenarioContext v2: keyed
  // by site alone, an admin's drag order was inherited by the next person to
  // sign in at that site. auth/sessionState.ts additionally clears these keys
  // on a user change; the scoping is the belt to that braces.
  const siteKey = `${user?.username ?? 'anon'}::${siteId ?? 'all'}`
  const { orders: locOrders, save: saveLoc } = usePersistedOrders('gi.sme.locorder.v1', siteKey)
  const { orders: allOrders, save: saveAll } = usePersistedOrders('gi.sme.alleqorder.v1', siteKey)

  const model = useMemo(
    () => (snap ? buildModel(snap.equipment, snap.recipes, snap.materials, snap.progress) : null),
    [snap])

  const byLocation = useMemo(() => {
    if (!model) return new Map<string, string[]>()
    const m = new Map<string, string[]>()
    const seen = new Set<string>()
    for (const u of allUnits(model)) {
      if (seen.has(u.tag)) continue
      seen.add(u.tag)
      const loc = u.location || '—'
      if (!m.has(loc)) m.set(loc, [])
      m.get(loc)!.push(u.tag)
    }
    return m
  }, [model])

  if (isLoading) return <Skeleton active paragraph={{ rows: 8 }} />
  if (!snap || !model) return <Alert type="warning" showIcon title="SME model unavailable" />

  const allTags = model.defaultOrder
  const allOrder = reconcileOrder(allOrders['__all__'] ?? [], allTags)

  return (
    <div>
      <TierNote style={{ marginBottom: 12 }} />
      <Radio.Group value={mode} onChange={(e) => setMode(e.target.value)}
        optionType="button" buttonStyle="solid" style={{ marginBottom: 14 }}
        options={[{ label: '📍 Location Based', value: 'loc' },
          { label: '🌐 All Equipment', value: 'all' }]} />

      {mode === 'all' ? (
        <AllEquipmentMode model={model} order={allOrder}
          onOrder={(next) => saveAll('__all__', next)} siteId={siteId} />
      ) : (
        <>
          {[...byLocation.keys()].sort().map((loc) => {
            const tags = byLocation.get(loc)!
            const order = reconcileOrder(locOrders[loc] ?? [], tags)
            const slug = loc.toLowerCase().replace(/[^a-z0-9]+/g, '-')
            return (
              <Card key={loc} size="small" style={{ marginBottom: 14 }}
                title={(
                  <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span style={{
                      ...mono, background: locColor(loc), color: light.surface, borderRadius: 6,
                      padding: '1px 10px', fontSize: '0.72rem', fontWeight: 700,
                    }}>{loc}</span>
                    <span style={{ fontSize: '0.72rem', opacity: 0.75 }}>{tags.length} equipment</span>
                  </span>
                )}>
                <ScopeSection model={model} order={order}
                  onOrder={(next) => saveLoc(loc, next)} siteId={siteId}
                  scopeTitle={`SME Location Report — ${loc}`} slug={slug}
                  showSuggestions location={loc} />
              </Card>
            )
          })}
          {byLocation.size === 0 && <Alert type="info" title="No equipment in this site." />}
        </>
      )}
    </div>
  )
}

function AllEquipmentMode({ model, order, onOrder, siteId }: {
  model: SmeModel
  order: string[]
  onOrder: (next: string[]) => void
  siteId?: string
}) {
  const plan = useMemo(() => runPlan(model, order), [model, order])
  const stats = useMemo(() => tagStats(model, plan.lines), [model, plan])
  const all = [...stats.values()]
  // 2026-08-03 STRICT TIER SEGREGATION. `canSqm` is the TIER-1 bottleneck — it
  // used to include stock on order, so a KPI literally titled "Available SQM"
  // counted material that was still on a truck.
  //
  // 2026-08-04 AREA-WEIGHTED BOTTLENECK. `cov` was `Σ Available ÷ Σ Demand`, a
  // quantity average across unlike materials: 100% of Part A and 0% of Part B
  // read as 50% coverage when nothing could be built. It is now
  // Σ buildable m² ÷ Σ remaining m², so it can never exceed the scarcest
  // component's rate. Shared with the Session Report via one helper.
  const scope = scopeBottleneckCoverage(all)
  const { sqm, canSqm: can, canSqmWithOrdered: canOrd } = scope
  const cov = scope.coveragePct
  const covOrd = scope.coverageWithOrderedPct

  return (
    <div>
      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        <Col flex="1 1 150px"><KpiDrill title="Equipment" value={String(all.length)}
          drillTitle="All Equipment (cascade order)"
          rows={order.filter((t) => stats.has(t)).map((t, i) => ({
            '#': i + 1, Tag: t, Name: stats.get(t)!.name, Location: stats.get(t)!.location,
            'Ready now %': stats.get(t)!.fulfillPct,
            'When delivered %': stats.get(t)!.fulfillWithOrderedPct,
          }))} help="Every equipment tag, cascaded in the order below." /></Col>
        <Col flex="1 1 150px"><KpiDrill title="Total SQM" value={nf(sqm)}
          drillTitle="SQM by Equipment" rows={all.map((t) => ({ Tag: t.tag, SQM: t.sqm }))} /></Col>
        <Col flex="1 1 150px"><KpiDrill title="Buildable now SQM" value={nf(can)}
          accent={status.ok} drillTitle="Buildable today (physical stock only)"
          help="Bottlenecked by stock ON THE SHELF. Material on an open purchase order is excluded — it cannot line a tank today."
          rows={all.map((t) => ({
            Tag: t.tag, 'Buildable now': t.canSqm, 'Ready now %': t.fulfillPct,
          }))} /></Col>
        <Col flex="1 1 150px"><KpiDrill title="When delivered SQM" value={nf(canOrd)}
          accent={status.low} drillTitle="Buildable once purchase orders arrive"
          help="Coverage against the TOTAL procured quantity — arrived stock plus the pending part of the order. Forecast only."
          rows={all.filter((t) => t.canSqmWithOrdered > t.canSqm).map((t) => ({
            Tag: t.tag, 'Buildable now': t.canSqm, 'When delivered': t.canSqmWithOrdered,
          }))} /></Col>
        <Col flex="1 1 150px"><KpiDrill title="Deficit SQM" value={nf(sqm - can)}
          accent={sqm - can > 0 ? status.critical : undefined}
          drillTitle="SQM Deficit by Equipment (against physical stock)"
          rows={all.filter((t) => t.sqm - t.canSqm > 0.005)
            .map((t) => ({ Tag: t.tag, 'Deficit SQM': Math.round((t.sqm - t.canSqm) * 100) / 100 }))} /></Col>
        <Col flex="1 1 150px"><KpiDrill title="Coverage now" value={`${cov.toFixed(1)}%`}
          drillTitle="Coverage by Equipment — physical vs when delivered"
          help={`Buildable m² ÷ remaining m², where each unit is capped by its `
            + `SCARCEST component — never a quantity average across materials. `
            + `Arrived stock only; against the total procured quantity it would read `
            + `${covOrd.toFixed(1)}%.`}
          rows={all.map((t) => ({
            Tag: t.tag, 'Ready now %': t.fulfillPct,
            'When delivered %': t.fulfillWithOrderedPct,
          }))} /></Col>
      </Row>
      <Card size="small" title={<span style={secHdr}>{fcDot(cov)} All equipment — drag to re-cascade</span>}>
        <ScopeSection model={model} order={order} onOrder={onOrder} siteId={siteId}
          scopeTitle="SME Location Report — All Equipment" slug="all-equipment"
          showSuggestions />
      </Card>
    </div>
  )
}
