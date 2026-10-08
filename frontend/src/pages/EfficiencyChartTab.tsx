/**
 * Phase 9e — how much manpower went into Equipment A against Equipment B.
 *
 * ⚠️ THE LINE IS CUMULATIVE, NOT DAILY (ruling Q11). Mobilisation, scaffolding
 * and curing all book hours against zero square metres, so a per-day
 * `hours / m²` is a division by zero on exactly the days that need explaining
 * — and on the rest it swings hard enough to hide the trend it is supposed to
 * show. `cum_hours / cum_sqm` converges, and it is the number an HOD actually
 * argues about.
 *
 * ⚠️ AND A ZERO-AREA DAY IS DRAWN AS A GAP WITH ITS REASON (ruling Q12), not as
 * a zero. Zero would read as "this crew achieved nothing per metre", which is a
 * statement about efficiency; the truth is that there were no metres to divide
 * by. The reason comes from what the timekeeper wrote — where they wrote
 * nothing, the chart says THAT rather than guessing at "scaffolding".
 *
 * ⚠️ MH/m² IS THE WHOLE POINT OF NORMALISING. A 400 m² tank and a 40 m² vessel
 * cannot be compared on hours; they can be compared on hours per metre.
 */
import { useMemo, useState } from 'react'
import {
  Alert, Card, Col, Empty, Row, Segmented, Select, Space, Spin, Table, Tag,
  Tooltip as ATooltip, Typography,
} from 'antd'
import {
  Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer,
  Tooltip as RTooltip, XAxis, YAxis,
} from 'recharts'
import { ThunderboltOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import KpiCard from '../components/KpiCard'
import KpiRow from '../components/KpiRow'
import { brand, chartColors, dark, status } from '../theme/tokens'

interface Point {
  date: string
  hours: number
  sqm: number
  cum_hours: number
  cum_sqm: number
  daily_mh_per_sqm: number | null
  cum_mh_per_sqm: number | null
  gap: boolean
  idle: boolean
  reason: string | null
  entries: number
}

interface Series {
  key: string
  Equipment_Tag: string
  System_Code: string
  points: Point[]
  Total_Hours: number
  Total_SQM: number
  MH_per_SQM: number | null
  Days_Worked: number
  Days_Without_Area: number
}

interface Daily {
  days: string[]
  series: Series[]
  warnings: string[]
}

// Enough separation that two lines on one axis are never in doubt, and no
// reliance on red/green alone.
const PALETTE = [brand.gold, status.info, chartColors[7], status.ok, chartColors[6],
                 status.low, status.info, status.critical]

export default function EfficiencyChartTab({ site }: { site?: string }) {
  const [code, setCode] = useState<string | undefined>()
  const [tags, setTags] = useState<string[]>([])
  const [metric, setMetric] = useState<'cum' | 'hours'>('cum')

  const scope = useQuery({
    queryKey: ['/mh/analytics/scope', site],
    queryFn: async () => (await api.get<{ systems: Array<{
      System_Code: string; equipment: string[] }> }>(
      '/mh/analytics/scope', { params: site ? { site_id: site } : {} })).data,
  })

  const data = useQuery<Daily>({
    queryKey: ['/mh/analytics/daily', site, code, tags.join(',')],
    queryFn: async () => (await api.get<Daily>('/mh/analytics/daily', {
      params: {
        ...(site ? { site_id: site } : {}),
        ...(code ? { system_code: code } : {}),
        ...(tags.length ? { equipment: tags } : {}),
      },
    })).data,
  })

  const systems = scope.data?.systems ?? []
  const equipOptions = useMemo(() => {
    const src = code ? systems.filter((s) => s.System_Code === code) : systems
    return Array.from(new Set(src.flatMap((s) => s.equipment))).sort()
  }, [systems, code])

  const series = data.data?.series ?? []

  // recharts wants one row per x value with a column per series, so the shape
  // is pivoted here rather than in the API — the API's shape is the one that
  // answers "tell me about this job", which is what every other caller wants.
  const rows = useMemo(() => (data.data?.days ?? []).map((d, i) => {
    const row: Record<string, unknown> = { date: d }
    series.forEach((s) => {
      const p = s.points[i]
      if (!p) return
      row[`${s.key}__hours`] = p.hours || null
      // ⚠️ null, NOT 0. recharts draws a null as a break in the line, which is
      // exactly right: there is no ratio on a day with no metres, and a zero
      // would read as "achieved nothing per metre".
      row[`${s.key}__cum`] = p.cum_mh_per_sqm
      row[`${s.key}__gap`] = p.gap
      row[`${s.key}__reason`] = p.reason
    })
    return row
  }), [data.data, series])

  const gapDays = useMemo(() => series.flatMap((s) => s.points
    .filter((p) => p.gap)
    .map((p) => ({ key: `${s.key}@${p.date}`, series: s.key, date: p.date,
                   hours: p.hours, reason: p.reason }))),
  [series])

  const best = series.filter((s) => s.MH_per_SQM != null)
    .sort((a, b) => (a.MH_per_SQM ?? 0) - (b.MH_per_SQM ?? 0))

  return (
    <Space direction="vertical" size={14} style={{ width: '100%' }}>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
        Man-hours per square metre, so equipment of different sizes can be
        compared at all. The bars are the hours booked each day; the line is the
        job&rsquo;s <strong>running</strong> man-hours per m² — it settles, where
        a day-by-day figure swings too hard to read and does not exist at all on
        a day with no area.
      </Typography.Paragraph>

      <Space wrap>
        <Select style={{ width: 260 }} placeholder="Lining system"
          allowClear value={code} loading={scope.isLoading}
          onChange={(v) => { setCode(v); setTags([]) }}
          options={systems.map((s) => ({ value: s.System_Code,
                                         label: s.System_Code || '(none)' }))} />
        <Select style={{ width: 340 }} mode="multiple" allowClear
          placeholder="All equipment with hours" value={tags}
          maxTagCount="responsive" onChange={setTags}
          options={equipOptions.map((t) => ({ value: t, label: t }))} />
        <Segmented value={metric} onChange={(v) => setMetric(v as 'cum' | 'hours')}
          options={[{ label: 'Efficiency + hours', value: 'cum' },
                    { label: 'Hours only', value: 'hours' }]} />
      </Space>

      {(data.data?.warnings ?? []).map((w) => (
        <Alert key={w} type="warning" showIcon message={w} />
      ))}

      {data.isFetching && <Spin />}

      {!data.isFetching && series.length === 0 && (
        <Empty description="Nothing to chart yet — this fills in as the daily
          timesheet and the team SQM are recorded." />
      )}

      {series.length > 0 && (
        <>
          <KpiRow gap={12}>
            {/* Sorted best-first, so the leftmost card is the most efficient
                job — the comparison the operator asked for, before anybody
                reads a chart. */}
            {best.slice(0, 4).map((s, i) => (
              <KpiCard key={s.key}
                title={`${s.key} — MH/m²`}
                value={s.MH_per_SQM == null ? '—' : s.MH_per_SQM.toFixed(2)}
                icon={<ThunderboltOutlined />}
                tint={i === 0 ? status.ok : brand.gold} />
            ))}
          </KpiRow>

          <Card size="small" title="Day by day">
            <ResponsiveContainer width="100%" height={330}>
              <ComposedChart data={rows}
                margin={{ top: 8, right: 12, left: -8, bottom: 4 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} interval="preserveStartEnd"
                  angle={-35} textAnchor="end" height={58} />
                <YAxis yAxisId="h" tick={{ fontSize: 11 }}
                  label={{ value: 'hours', angle: -90, position: 'insideLeft',
                           style: { fontSize: 11 } }} />
                {metric === 'cum' && (
                  <YAxis yAxisId="r" orientation="right" tick={{ fontSize: 11 }}
                    label={{ value: 'MH/m² (running)', angle: 90,
                             position: 'insideRight', style: { fontSize: 11 } }} />
                )}
                <RTooltip content={<EffTooltip series={series} />} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                {series.map((s, i) => (
                  <Bar key={`b-${s.key}`} yAxisId="h" dataKey={`${s.key}__hours`}
                    name={`${s.key} — hours`} fill={PALETTE[i % PALETTE.length]}
                    fillOpacity={0.55} />
                ))}
                {metric === 'cum' && series.map((s, i) => (
                  <Line key={`l-${s.key}`} yAxisId="r" type="monotone"
                    dataKey={`${s.key}__cum`} name={`${s.key} — MH/m²`}
                    stroke={PALETTE[i % PALETTE.length]} strokeWidth={2}
                    dot={false}
                    // ⚠️ NOT `connectNulls`. A break in this line is a real
                    // statement — no area was produced, so there is no ratio.
                    // Bridging it would draw a number that does not exist.
                    connectNulls={false} />
                ))}
              </ComposedChart>
            </ResponsiveContainer>
          </Card>

          {gapDays.length > 0 && (
            <Card size="small"
              title={<Space>Days with hours but no area
                <ATooltip title="The line breaks on these days because there is
                  no area to divide by. The hours still count towards the
                  running figure — they are part of what the job cost.">
                  <Tag color="gold">{gapDays.length}</Tag>
                </ATooltip>
              </Space>}>
              <Table size="small" rowKey="key" pagination={{ pageSize: 8,
                hideOnSinglePage: true }}
                dataSource={gapDays}
                columns={[
                  { title: 'Date', dataIndex: 'date', width: 120 },
                  { title: 'Equipment', dataIndex: 'series' },
                  { title: 'Hours', dataIndex: 'hours', width: 90,
                    align: 'right' },
                  {
                    title: 'Reason recorded', dataIndex: 'reason',
                    render: (v: string | null) => (v
                      ? <span>{v}</span>
                      // ⚠️ SAY THAT NOTHING WAS WRITTEN. Filling this with
                      // "mobilisation" would put a word in somebody's mouth;
                      // "no reason recorded" is a thing a manager can act on.
                      : <Typography.Text type="secondary">
                          no reason recorded — only the timekeeper can say
                        </Typography.Text>),
                  },
                ]} />
            </Card>
          )}

          <Card size="small" title="Totals">
            <Row gutter={[12, 12]}>
              {series.map((s) => (
                <Col key={s.key} xs={24} md={12} lg={8}>
                  <Card size="small" type="inner" title={s.key}>
                    <Typography.Text>
                      <strong>{s.Total_Hours}</strong> hours over{' '}
                      <strong>{s.Total_SQM}</strong> m²
                      {s.MH_per_SQM != null && (
                        <> — <strong>{s.MH_per_SQM.toFixed(2)}</strong> MH/m²</>
                      )}
                    </Typography.Text>
                    <br />
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {s.Days_Worked} day(s) worked
                      {s.Days_Without_Area > 0
                        && `, ${s.Days_Without_Area} with no area`}
                    </Typography.Text>
                  </Card>
                </Col>
              ))}
            </Row>
          </Card>
        </>
      )}
    </Space>
  )
}

/** The tooltip carries the reason, because that is where somebody looks. */
function EffTooltip({ active, payload, label, series }: {
  active?: boolean
  payload?: Array<{ dataKey?: string | number }>
  label?: string
  series: Series[]
}) {
  if (!active || !payload?.length) return null
  const row = (payload[0] as unknown as { payload: Record<string, unknown> }).payload
  return (
    <div style={{ background: 'rgba(20,28,45,.96)', border: `1px solid ${dark.border}`,
                  borderRadius: 6, padding: '8px 10px', fontSize: 12,
                  color: dark.text, maxWidth: 320 }}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{label}</div>
      {series.map((s) => {
        const h = row[`${s.key}__hours`] as number | null
        const c = row[`${s.key}__cum`] as number | null
        const gap = row[`${s.key}__gap`] as boolean
        const reason = row[`${s.key}__reason`] as string | null
        if (!h && !gap) return null
        return (
          <div key={s.key} style={{ marginBottom: 3 }}>
            <strong>{s.key}</strong>: {h ?? 0} h
            {c != null ? ` · ${c} MH/m² running` : ' · no running figure yet'}
            {gap && (
              <div style={{ color: status.low }}>
                no area recorded — {reason || 'no reason written down'}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
