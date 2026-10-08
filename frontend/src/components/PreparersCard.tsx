import { useEffect, useState } from 'react'
import { App, Button, Card, Input, Select, Space, Typography } from 'antd'
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
 */
export interface PreparerEntry { from: string; day: string; night: string }

export function pickPreparers(history: PreparerEntry[], dateIso?: string | null): PreparerEntry | null {
  if (!history.length) return null
  if (!dateIso) return history[history.length - 1]
  let cur: PreparerEntry | null = null
  for (const h of history) if ((h.from || '') <= dateIso) cur = h
  return cur
}

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
    onError: (e: unknown) => {
      const x = e as { response?: { data?: { detail?: unknown } } }
      message.error(typeof x?.response?.data?.detail === 'string' ? x.response.data.detail : 'Could not save')
    },
  })
  const set = (i: number, p: Partial<PreparerEntry>) =>
    setRows((rs) => rs.map((r, k) => (k === i ? { ...r, ...p } : r)))
  return (
    <Card size="small" title="Consumption papers — who prepares them" data-testid="preparers"
      style={{ marginTop: 16 }}
      extra={!fixedSite && <Select placeholder="Site" style={{ width: 150 }} value={site} onChange={setSite}
        options={(sites ?? []).map((s) => ({ value: s, label: s }))} data-testid="preparers-site" />}>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        A paper with <b>(Night)</b> written next to the date is the Night preparer&apos;s; a paper with
        nothing written is a <b>Day</b> paper. OCR Import fills <i>Prepared by</i> with the names in force on
        the paper&apos;s own date — add a line when the shift changes hands, and older papers keep their names.
      </Typography.Paragraph>
      {!site ? <Typography.Text type="secondary">Choose a site.</Typography.Text> : (
        <>
          {rows.map((r, i) => (
            <Space key={i} wrap style={{ marginBottom: 6 }}>
              <Input style={{ width: 130 }} placeholder="from YYYY-MM-DD" value={r.from}
                onChange={(e) => set(i, { from: e.target.value })} aria-label="From date" />
              <Input style={{ width: 160 }} placeholder="Day name" value={r.day}
                onChange={(e) => set(i, { day: e.target.value })} data-testid="preparers-day" aria-label="Day preparer" />
              <Input style={{ width: 160 }} placeholder="Night name" value={r.night}
                onChange={(e) => set(i, { night: e.target.value })} data-testid="preparers-night" aria-label="Night preparer" />
              <Button size="small" type="text" onClick={() => setRows((rs) => rs.filter((_x, k) => k !== i))}>Remove</Button>
            </Space>
          ))}
          <Space>
            <Button size="small" onClick={() => setRows((rs) => [...rs, { from: new Date().toISOString().slice(0, 10), day: '', night: '' }])}
              data-testid="preparers-add">Add a line</Button>
            <Button size="small" type="primary" disabled={save.isPending} onClick={() => save.mutate()}
              data-testid="preparers-save">Save</Button>
          </Space>
        </>
      )}
    </Card>
  )
}
