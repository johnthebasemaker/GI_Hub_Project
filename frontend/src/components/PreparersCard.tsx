import { useEffect, useState } from 'react'
import { App, Button, Card, DatePicker, Input, Select, Space, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useSites } from '../api/hooks'

/**
 * Phase 22e (rulings Q22-14..16) — who prepares the site's consumption papers.
 *
 * A paper with "(Night)" next to the date was prepared by the Night name, an
 * unmarked paper by the Day name. The names change hands, so this is a HISTORY:
 * each line says from which date a pair applies, and OCR Import uses the pair
 * in force on the paper's own date. Admin (any site) and the site's HOD.
 *
 * Phase 23c (ruling Q23-1) — a ONE-DAY COVER: somebody who prepared papers on a
 * single date (`{on, cover, shift}`). It never changes the pair in force; it is
 * one more name OCR Import offers for that date. Dates are picked, not typed.
 */
export interface PreparerEntry { from?: string; day?: string; night?: string; on?: string; cover?: string; shift?: string }

export function pickPreparers(history: PreparerEntry[], dateIso?: string | null): PreparerEntry | null {
  const regular = history.filter((h) => !h.on)
  if (!regular.length) return null
  if (!dateIso) return regular[regular.length - 1]
  let cur: PreparerEntry | null = null
  for (const h of regular) if ((h.from || '') <= dateIso) cur = h
  return cur
}

export function coversOn(history: PreparerEntry[], dateIso?: string | null, shift?: string | null): string[] {
  if (!dateIso) return []
  return history.filter((h) => h.on === dateIso && h.cover && (!shift || !h.shift || h.shift === shift))
    .map((h) => h.cover as string)
}

const D = 'YYYY-MM-DD'

export default function PreparersCard({ fixedSite }: { fixedSite?: string }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const { data: sites } = useSites()
  const [site, setSite] = useState<string | undefined>(fixedSite)
  const [rows, setRows] = useState<PreparerEntry[]>([])
  const { data } = useQuery<{ history: PreparerEntry[] }>({
    queryKey: ['/ai/ocr/preparers', site],
    enabled: !!site,
    queryFn: async () => (await api.get('/ai/ocr/preparers', { params: { site_id: site } })).data,
  })
  useEffect(() => { setRows(data?.history ?? []) }, [data])
  const save = useMutation({
    mutationFn: async () => (await api.put('/ai/ocr/preparers', { site_id: site, history: rows })).data,
    onSuccess: () => {
      message.success('Saved — OCR Import uses these names from now on')
      void qc.invalidateQueries({ queryKey: ['/ai/ocr/preparers'] })
    },
    onError: (e: unknown) => message.error((e as Error)?.message || 'Could not save'),
  })
  const set = (i: number, p: Partial<PreparerEntry>) =>
    setRows((rs) => rs.map((r, k) => (k === i ? { ...r, ...p } : r)))
  const remove = (i: number) => setRows((rs) => rs.filter((_x, k) => k !== i))
  const pairs = rows.map((r, i) => ({ r, i })).filter(({ r }) => !r.on)
  const covers = rows.map((r, i) => ({ r, i })).filter(({ r }) => !!r.on)
  return (
    <Card size="small" title="Consumption papers — who prepares them" data-testid="preparers"
      style={{ marginTop: 16 }}
      extra={!fixedSite && <Select placeholder="Site" style={{ width: 150 }} value={site} onChange={setSite}
        options={(sites ?? []).map((s) => ({ value: s, label: s }))} data-testid="preparers-site" />}>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        A paper with <b>(Night)</b> written next to the date is the Night preparer&apos;s; a paper with
        nothing written is a <b>Day</b> paper. OCR Import fills <i>Prepared by</i> with the names in force on
        the paper&apos;s own date — add a line when the shift changes hands, and older papers keep their names.
        A <b>one-day cover</b> is somebody who prepared papers on a single date; the regular names stay in force.
      </Typography.Paragraph>
      {!site ? <Typography.Text type="secondary">Choose a site.</Typography.Text> : (
        <>
          {pairs.map(({ r, i }) => (
            <Space key={`p${i}`} wrap style={{ marginBottom: 6 }}>
              <DatePicker style={{ width: 140 }} placeholder="from" allowClear={false} aria-label="From date"
                value={r.from ? dayjs(r.from) : null} format="DD MMM YYYY"
                onChange={(d) => set(i, { from: d ? d.format(D) : '' })} data-testid="preparers-from" />
              <Input style={{ width: 160 }} placeholder="Day name" value={r.day}
                onChange={(e) => set(i, { day: e.target.value })} data-testid="preparers-day" aria-label="Day preparer" />
              <Input style={{ width: 160 }} placeholder="Night name (none yet = leave blank)" value={r.night}
                onChange={(e) => set(i, { night: e.target.value })} data-testid="preparers-night" aria-label="Night preparer" />
              <Button size="small" type="text" onClick={() => remove(i)}>Remove</Button>
            </Space>
          ))}
          {covers.length > 0 && (
            <Typography.Text strong style={{ display: 'block', margin: '8px 0 4px' }}>One-day covers</Typography.Text>
          )}
          {covers.map(({ r, i }) => (
            <Space key={`c${i}`} wrap style={{ marginBottom: 6 }} data-testid="preparers-cover">
              <Tag color="purple" style={{ margin: 0 }}>only this day</Tag>
              <DatePicker style={{ width: 140 }} allowClear={false} aria-label="Cover date"
                value={r.on ? dayjs(r.on) : null} format="DD MMM YYYY"
                onChange={(d) => set(i, { on: d ? d.format(D) : '' })} />
              <Input style={{ width: 160 }} placeholder="Name" value={r.cover} aria-label="Cover name"
                onChange={(e) => set(i, { cover: e.target.value })} data-testid="preparers-cover-name" />
              <Select style={{ width: 130 }} value={r.shift ?? ''} aria-label="Cover shift"
                onChange={(v) => set(i, { shift: v })}
                options={[{ value: '', label: 'either shift' }, { value: 'Day', label: 'Day' }, { value: 'Night', label: 'Night' }]} />
              <Button size="small" type="text" onClick={() => remove(i)}>Remove</Button>
            </Space>
          ))}
          <Space wrap>
            <Button size="small" onClick={() => setRows((rs) => [...rs, { from: dayjs().format(D), day: '', night: '' }])}
              data-testid="preparers-add">Add a line</Button>
            <Button size="small" onClick={() => setRows((rs) => [...rs, { on: dayjs().format(D), cover: '', shift: '' }])}
              data-testid="preparers-add-cover">Add a one-day cover</Button>
            <Button size="small" type="primary" disabled={save.isPending} onClick={() => save.mutate()}
              data-testid="preparers-save">Save</Button>
          </Space>
        </>
      )}
    </Card>
  )
}
