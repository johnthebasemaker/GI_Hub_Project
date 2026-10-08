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
import {
  App, Alert, Badge, Button, Card, Col, Collapse, Empty, Row, Select, Skeleton,
  Space, Tooltip, Typography,
} from 'antd'
import { BarChartOutlined, ClearOutlined, LinkOutlined, PlusOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useSmeSnapshot } from '../api/hooks'
import { buildModel, runPlan } from './engine'
import { applyFilters, filterOptions, locColor } from './insights'
import type { DashFilters } from './insights'
import MultiSelectAll from './MultiSelectAll'
import PriorityList from './PriorityList'
import { encodeTags, useScenario } from './ScenarioContext'
import { tagStats } from './session'
import TagDetail from './TagDetail'
import { light } from '../theme/tokens'

const secHdr: React.CSSProperties = {
  fontFamily: 'JetBrains Mono, monospace', fontSize: '0.68rem', fontWeight: 700,
  letterSpacing: '.13em', textTransform: 'uppercase', opacity: 0.65,
}

export default function SessionBuilder({ siteId }: { siteId?: string }) {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const { data: snap, isLoading } = useSmeSnapshot(siteId)
  const scenario = useScenario()
  const [filters, setFilters] = useState<DashFilters>({ locations: [], types: [], codes: [], substrates: [] })
  // Bulk pick: an array now, not one tag. "Select all" resolves to the real
  // values (MultiSelectAll), so nothing downstream has a sentinel to unwrap.
  const [picked, setPicked] = useState<string[]>([])
  const [selected, setSelected] = useState<string | undefined>()

  const model = useMemo(
    () => (snap ? buildModel(snap.equipment, snap.recipes, snap.materials, snap.progress) : null),
    [snap])
  const options = useMemo(() => (model ? filterOptions(model, filters) : null), [model, filters])
  const pool = useMemo(() => {
    if (!model) return []
    const units = applyFilters(model, filters)
    const seen = new Set<string>()
    const out: { tag: string; name: string; location: string }[] = []
    for (const u of units) {
      if (!seen.has(u.tag)) {
        seen.add(u.tag)
        out.push({ tag: u.tag, name: u.name, location: u.location || '—' })
      }
    }
    return out
  }, [model, filters])

  // The same pool, grouped by location — the source for the per-location
  // "+ Add all" buttons. Sorted so the panel order is stable across renders.
  const byLocation = useMemo(() => {
    const m = new Map<string, { tag: string; name: string }[]>()
    for (const p of pool) {
      if (!m.has(p.location)) m.set(p.location, [])
      m.get(p.location)!.push({ tag: p.tag, name: p.name })
    }
    return [...m.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [pool])

  // What the Add button would actually do. Picking a tag that is already in the
  // session is not an error — it just is not an addition, so the button counts
  // and adds only the remainder rather than refusing the whole selection.
  const freshPicked = useMemo(
    () => picked.filter((t) => !scenario.order.includes(t)), [picked, scenario.order])
  const alreadyIn = picked.length - freshPicked.length

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
            {/* Bulk pick. MultiSelectAll is the same component the seven other
                SME filter selects use, so "Select all" behaves identically
                everywhere — it resolves to the CURRENT option list, never a
                magic token. `pool` is already filter-narrowed, so "Select all"
                means "everything I can currently see", which is what someone
                who has just set three filters expects. */}
            <MultiSelectAll showSearch placeholder="Pick equipment tags…" value={picked}
              style={{ width: '100%' }} optionFilterProp="label"
              onChange={(v) => { setPicked(v); if (v.length) setSelected(v[v.length - 1]) }}
              options={pool.map((p) => ({
                value: p.tag,
                label: `${p.tag}${p.name ? ` — ${p.name.slice(0, 28)}` : ''}${scenario.order.includes(p.tag) ? '  ✓ in session' : ''}`,
              }))} />
            <Button type="primary" icon={<PlusOutlined />}
              disabled={!freshPicked.length}
              onClick={() => {
                scenario.addTags(freshPicked)
                setSelected(freshPicked[freshPicked.length - 1])
                message.success(freshPicked.length === 1
                  ? `${freshPicked[0]} added to session`
                  : `${freshPicked.length} equipment added to session`)
                setPicked([])   // the picker is a staging area, not a filter
              }}>
              Add{freshPicked.length > 1 ? ` ${freshPicked.length}` : ''}
            </Button>
          </Space.Compact>
          {picked.length > 0 && alreadyIn > 0 && (
            <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 6 }}>
              {freshPicked.length === 0
                ? `All ${alreadyIn} selected are already in the session.`
                : `${alreadyIn} of ${picked.length} already in the session — `
                  + `${freshPicked.length} will be added.`}
            </Typography.Text>
          )}
        </Card>

        {/* ── Location-based quick add ──────────────────────────────────────
            Adapted from the Location Report's per-equipment "+ Session"
            button, but lifted a level: there you add ONE tag out of a
            location's cascade, here the point is to fill a session by area, so
            the location gets a bulk button and the individual tags sit under
            it. Same scenario.addTag/addTags actions, same in-session guard, so
            both tabs stay in step through one shared context. */}
        <Card size="small" style={{ marginTop: 16 }}
          title={<span style={secHdr}>📍 Add by location</span>}
          extra={(
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {pool.length} equipment in {byLocation.length} location{byLocation.length === 1 ? '' : 's'}
            </Typography.Text>
          )}>
          {byLocation.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="No equipment matches the current filters" />
          ) : (
            <Collapse size="small" accordion items={byLocation.map(([loc, tags]) => {
              const missing = tags.filter((t) => !scenario.order.includes(t.tag))
              return {
                key: loc,
                label: (
                  <Space size={8}>
                    <span style={{
                      fontFamily: 'JetBrains Mono, monospace', background: locColor(loc),
                      color: light.surface, borderRadius: 6, padding: '1px 10px',
                      fontSize: '0.72rem', fontWeight: 700,
                    }}>{loc}</span>
                    <span style={{ fontSize: '0.72rem', opacity: 0.75 }}>
                      {tags.length} equipment
                    </span>
                    {missing.length === 0 && (
                      <Badge status="success" text={<span style={{ fontSize: '0.7rem' }}>all in session</span>} />
                    )}
                  </Space>
                ),
                extra: (
                  <Tooltip title={missing.length
                    ? `Add ${missing.length} equipment from ${loc} to the session`
                    : `Every ${loc} equipment is already in the session`}>
                    {/* span wrapper: antd Tooltip cannot anchor a disabled button */}
                    <span onClick={(e) => e.stopPropagation()}>
                      <Button size="small" type="primary" ghost icon={<PlusOutlined />}
                        disabled={!missing.length}
                        onClick={() => {
                          scenario.addTags(missing.map((t) => t.tag))
                          setSelected(missing[missing.length - 1].tag)
                          message.success(`${missing.length} equipment from ${loc} added`)
                        }}>
                        Add all{missing.length ? ` (${missing.length})` : ''}
                      </Button>
                    </span>
                  </Tooltip>
                ),
                children: (
                  <Space direction="vertical" size={4} style={{ width: '100%' }}>
                    {tags.map((t) => {
                      const inSession = scenario.order.includes(t.tag)
                      return (
                        <div key={t.tag} style={{
                          display: 'flex', alignItems: 'center', gap: 8,
                          justifyContent: 'space-between',
                        }}>
                          <span style={{ minWidth: 0, cursor: 'pointer' }}
                            onClick={() => setSelected(t.tag)}>
                            <b style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: '0.76rem' }}>
                              {t.tag}
                            </b>
                            {t.name && (
                              <span style={{ fontSize: '0.72rem', opacity: 0.7, marginLeft: 8 }}>
                                {t.name.slice(0, 30)}
                              </span>
                            )}
                          </span>
                          <Button size="small" type="text" icon={<PlusOutlined />}
                            disabled={inSession}
                            onClick={() => {
                              scenario.addTag(t.tag)
                              setSelected(t.tag)
                              message.success(`${t.tag} added to session`)
                            }}>
                            {inSession ? 'In session' : 'Session'}
                          </Button>
                        </div>
                      )
                    })}
                  </Space>
                ),
              }
            })} />
          )}
        </Card>

        <Card size="small" style={{ marginTop: 16 }}
          title={<span style={secHdr}>📋 Session priority — drag to re-cascade ({scenario.order.length})</span>}
          extra={(
            <Space>
              {/* ── The bridge to the manpower module (Phase 8 slice 8e) ────
                  The session is handed over IN THE URL, not through shared
                  state. `ScenarioProvider` is mounted inside SmePage and
                  Manpower Tracking is a different route, so lifting the provider
                  to App.tsx was the alternative — a much wider blast radius
                  for one button, and it would have disturbed the provider's
                  per-user/per-site key logic. The encoding is the one already
                  written and tested for `?scenario=`, imported rather than
                  respelt, and nothing global is touched on the way. */}
              <Tooltip title={scenario.order.length
                ? 'Cost this session in manpower: the manpower for what the '
                  + 'material on site can actually start, the overall total, '
                  + 'and what is waiting on a delivery'
                : 'Add equipment to the session first — there is nothing to cost'}>
                <span>
                  <Button size="small" type="primary" ghost
                    icon={<BarChartOutlined />}
                    disabled={scenario.order.length === 0}
                    onClick={() => {
                      const p = new URLSearchParams({
                        tab: 'session',
                        scenario: encodeTags(scenario.order),
                      })
                      // Carried only if the operator actually set one. An
                      // empty filter means "every system on this equipment",
                      // and sending '' would read as "no systems".
                      if (filters.codes.length) {
                        p.set('codes', encodeTags(filters.codes))
                      }
                      navigate(`/manhours?${p.toString()}`)
                    }}>
                    📊 Session Report For MP&amp;H
                  </Button>
                </span>
              </Tooltip>
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
