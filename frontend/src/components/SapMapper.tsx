import { useMemo, useState } from 'react'
import type { Key } from 'react'
import { App, Button, Card, Popconfirm, Segmented, Select, Space, Tag, Tooltip, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table/interface'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Table } from '../lib/smartTable'
import { api } from '../api/client'
import { useList } from '../api/hooks'

/**
 * Phase 23c — "Needs a SAP code": the request lines the workbook gave no SAP,
 * decided IN GI HUB instead of in the workbook (rulings Q23-4/5).
 *
 *   Link to an item        → the line counts against that SAP at once
 *   Not stocked yet        → a GI code GI Hub does not stock: no SAP is
 *                            invented, and it links by itself the day the
 *                            workbook adds the item
 *   Not a stock item       → a service / one-off: out of pending and reorder
 *
 * A decision is learned per site and written name, so the same name never asks
 * twice; every decision is audited and can be undone. Admin, HOD (own site) and
 * Logistics decide; everybody else who sees the page can read it.
 */
interface Suggestion { SAP_Code: string; description: string; uom: string | null; score: number }
interface Group {
  site: string; key: string; written: string; Material_Code: string | null; uom: string | null
  status: 'needs_sap' | 'not_stocked' | 'mapped' | 'not_stock' | 'linked_by_code'
  mapping: { id: number; decision: string; by: string } | null; SAP_Code: string | null
  lines: number; requested: number; pending: number; files: string[]; suggestions?: Suggestion[]
}
interface Resp { groups: Group[]; counts: Record<string, number>; can_map: boolean }
type View = 'needs' | 'stocked' | 'decided'

const STATUS: Record<Group['status'], { label: string; color: string }> = {
  needs_sap: { label: 'needs a SAP code', color: 'orange' },
  not_stocked: { label: 'not stocked yet', color: 'blue' },
  mapped: { label: 'linked', color: 'green' },
  not_stock: { label: 'not a stock item', color: 'default' },
  linked_by_code: { label: 'linked by its GI code', color: 'green' },
}

export default function SapMapper({ site }: { site?: string }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [view, setView] = useState<View>('needs')
  const [picked, setPicked] = useState<Record<string, string | undefined>>({})
  const [selected, setSelected] = useState<Key[]>([])
  const [bulkSap, setBulkSap] = useState<string | undefined>()
  const { data, isFetching } = useQuery<Resp>({
    queryKey: ['/material-requests/needs-sap', site],
    queryFn: async () => (await api.get('/material-requests/needs-sap', { params: { site_id: site } })).data,
  })
  const inventory = useList('/inventory', { limit: 2000, ...(site ? { site_id: site } : {}) },
    !!data?.can_map)
  const itemOptions = useMemo(() => (inventory.data?.items ?? []).map((r) => ({
    value: String(r.SAP_Code),
    label: `${String(r.SAP_Code)} · ${String(r.Equipment_Description ?? '')}${r.Material_Code ? ` · ${String(r.Material_Code)}` : ''}`,
  })), [inventory.data])

  const groups = data?.groups ?? []
  const shown = groups.filter((g) => (view === 'needs' ? g.status === 'needs_sap'
    : view === 'stocked' ? g.status === 'not_stocked' && !g.mapping
      : !!g.mapping))
  const canMap = !!data?.can_map

  const decide = useMutation({
    mutationFn: async (v: { gs: Group[]; decision: 'item' | 'catalogue' | 'not_stock'; sap?: string }) => {
      const bySite = new Map<string, Group[]>()
      for (const g of v.gs) bySite.set(g.site, [...(bySite.get(g.site) ?? []), g])
      for (const [s, gs] of bySite) {
        await api.post('/material-requests/sap-map', {
          site_id: s, keys: gs.map((g) => g.key), decision: v.decision, SAP_Code: v.sap,
          examples: Object.fromEntries(gs.map((g) => [g.key, g.written])),
        })
      }
      return v
    },
    onSuccess: (v) => {
      message.success(`${v.gs.length} name(s) decided — ${v.decision === 'item' ? `linked to ${v.sap}`
        : v.decision === 'catalogue' ? 'not stocked yet (they link by themselves when the workbook adds the item)'
          : 'not a stock item'}`)
      setSelected([])
      setBulkSap(undefined)
      void qc.invalidateQueries({ queryKey: ['/material-requests/needs-sap'] })
      void qc.invalidateQueries({ queryKey: ['/material-requests'] })
    },
    onError: (e: unknown) => message.error((e as Error)?.message || 'Could not save'),
  })
  const undo = useMutation({
    mutationFn: async (id: number) => (await api.delete(`/material-requests/sap-map/${id}`)).data,
    onSuccess: () => {
      message.success('Undone — the line needs a decision again')
      void qc.invalidateQueries({ queryKey: ['/material-requests/needs-sap'] })
      void qc.invalidateQueries({ queryKey: ['/material-requests'] })
    },
    onError: (e: unknown) => message.error((e as Error)?.message || 'Could not undo'),
  })

  const cols: ColumnsType<Group> = [
    { title: 'Written in the request', key: 'w', width: 260,
      render: (_: unknown, g) => (
        <Space direction="vertical" size={0}>
          <span>{g.written || <i>(no description)</i>}</span>
          <Space size={4} wrap>
            {g.Material_Code && <Tag style={{ margin: 0 }}>{g.Material_Code}</Tag>}
            <Tag color={STATUS[g.status].color} style={{ margin: 0 }}>{STATUS[g.status].label}
              {g.status === 'mapped' && g.SAP_Code ? ` → ${g.SAP_Code}` : ''}</Tag>
            {g.mapping && <Typography.Text type="secondary" style={{ fontSize: 11 }}>by {g.mapping.by}</Typography.Text>}
          </Space>
        </Space>) },
    { title: 'Lines', key: 'n', width: 110,
      render: (_: unknown, g) => (
        <Tooltip title={g.files.join(' · ')}>
          <span>{g.lines} line(s) · {g.pending} {g.uom ?? ''} pending</span>
        </Tooltip>) },
    ...(view === 'needs' ? [{
      title: 'Best matches in the item master', key: 's', width: 300,
      render: (_: unknown, g: Group) => (
        <Space size={4} wrap>
          {(g.suggestions ?? []).map((s) => (
            <Tag key={s.SAP_Code} color="gold" style={{ cursor: canMap ? 'pointer' : 'default' }}
              data-testid="sap-suggestion"
              onClick={() => canMap && setPicked((p) => ({ ...p, [g.key]: s.SAP_Code }))}>
              {s.SAP_Code} · {s.description} · {Math.round(s.score * 100)}%</Tag>
          ))}
          {!g.suggestions?.length && <Typography.Text type="secondary" style={{ fontSize: 12 }}>no close match</Typography.Text>}
        </Space>),
    }] : []),
    ...(canMap ? [{
      title: '', key: 'a', width: 420,
      render: (_: unknown, g: Group) => (g.mapping ? (
        <Popconfirm title="Undo this decision?" onConfirm={() => undo.mutate(g.mapping!.id)}>
          <Button size="small" data-testid="sap-undo">Undo</Button>
        </Popconfirm>
      ) : (
        <Space wrap size={4}>
          <Select size="small" style={{ width: 230 }} showSearch optionFilterProp="label" placeholder="Choose the item"
            value={picked[g.key]} onChange={(v) => setPicked((p) => ({ ...p, [g.key]: v }))}
            options={itemOptions} data-testid="sap-pick" />
          <Button size="small" type="primary" disabled={!picked[g.key] || decide.isPending} data-testid="sap-link"
            onClick={() => decide.mutate({ gs: [g], decision: 'item', sap: picked[g.key] })}>Link</Button>
          {g.Material_Code && g.status === 'needs_sap' && (
            <Button size="small" disabled={decide.isPending} data-testid="sap-not-stocked"
              onClick={() => decide.mutate({ gs: [g], decision: 'catalogue' })}>Not stocked yet</Button>
          )}
          <Button size="small" type="text" disabled={decide.isPending} data-testid="sap-not-stock"
            onClick={() => decide.mutate({ gs: [g], decision: 'not_stock' })}>Not a stock item</Button>
        </Space>
      )),
    }] : []),
  ]

  const sel = shown.filter((g) => selected.includes(g.key))
  return (
    <Card size="small" style={{ marginBottom: 12 }} data-testid="req-needs-sap"
      title={`Needs a SAP code — ${data?.counts.needs_sap ?? 0} line(s)`}
      extra={<Segmented size="small" value={view} onChange={(v) => { setView(v as View); setSelected([]) }}
        options={[
          { value: 'needs', label: `Needs a SAP code (${groups.filter((g) => g.status === 'needs_sap').length})` },
          { value: 'stocked', label: `Not stocked yet (${groups.filter((g) => g.status === 'not_stocked' && !g.mapping).length})` },
          { value: 'decided', label: `Decided (${groups.filter((g) => !!g.mapping).length})` },
        ]} />}>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        {view === 'needs' && <>Request lines the workbook gave no SAP code. <b>Link</b> one to its item and it counts
          against that item at once; <b>Not stocked yet</b> keeps a GI-coded line waiting until the workbook adds
          the item (no SAP number is made up); <b>Not a stock item</b> takes it out of pending and reorder. GI Hub
          remembers the decision for this name — your workbook is never changed.</>}
        {view === 'stocked' && <>These carry a GI code from the material catalogue that GI Hub does not stock yet.
          They link by themselves the day the workbook adds an item with that code.</>}
        {view === 'decided' && <>Decisions made here, newest kept per name. <b>Undo</b> puts a line back on the list.</>}
        {!canMap && <> Admin, HOD and Logistics decide; you can see the list.</>}
      </Typography.Paragraph>
      {canMap && view === 'needs' && sel.length > 0 && (
        <Space wrap style={{ marginBottom: 8 }} data-testid="sap-bulk">
          <span>{sel.length} name(s) ticked —</span>
          <Select size="small" style={{ width: 260 }} showSearch optionFilterProp="label" placeholder="link them all to…"
            value={bulkSap} onChange={setBulkSap} options={itemOptions} data-testid="sap-bulk-pick" />
          <Button size="small" type="primary" disabled={!bulkSap || decide.isPending}
            onClick={() => decide.mutate({ gs: sel, decision: 'item', sap: bulkSap })}>Link {sel.length}</Button>
          {sel.every((g) => !!g.Material_Code) && (
            <Button size="small" disabled={decide.isPending}
              onClick={() => decide.mutate({ gs: sel, decision: 'catalogue' })}>Not stocked yet</Button>
          )}
          <Button size="small" disabled={decide.isPending}
            onClick={() => decide.mutate({ gs: sel, decision: 'not_stock' })}>Not a stock item</Button>
          <Button size="small" type="text" onClick={() => setSelected([])}>Clear</Button>
        </Space>
      )}
      <Table size="small" loading={isFetching} columns={cols} dataSource={shown} rowKey="key"
        rowSelection={canMap && view === 'needs' ? { selectedRowKeys: selected, onChange: setSelected } : undefined}
        pagination={{ pageSize: 10, showTotal: (t) => `${t} name(s)` }} scroll={{ x: 900 }} />
    </Card>
  )
}
