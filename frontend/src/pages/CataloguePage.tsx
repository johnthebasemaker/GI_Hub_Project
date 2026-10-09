import { useState } from 'react'
import type { Key } from 'react'
import {
  Alert, App, Button, Card, Drawer, Empty, Image, Input, Popconfirm, Segmented, Select, Space, Tabs, Tag,
  Tooltip, Typography, Upload,
} from 'antd'
import { CameraOutlined, DeleteOutlined, RollbackOutlined, StarFilled, StarOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table/interface'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Table } from '../lib/smartTable'
import { api } from '../api/client'
import { SiteFilter } from '../components/SiteField'
import { Thumb, imgSrc } from '../catalogue/thumbs'

/**
 * Phase 23d — Materials & equipment catalogue (rulings Q23-5..9).
 *
 * The 6,000 GI material codes from Drive's "All MATERIAL CODES" workbook and
 * the site's plant & tools list, each with up to four pictures. Everybody sees
 * them; Admin, HOD and Logistics add, replace, reorder and remove them — one
 * set per code, shared by every site, every change audited, a removed picture
 * restorable. Pictures come from uploads (or the phone's camera), from Drive's
 * `Material Images` folder (files named by GI code), or from a sibling of the
 * same family when somebody accepts it. Never from a web search.
 */
type Kind = 'material' | 'equipment'
interface Mat {
  code: string; description: string; uom: string | null; saps: string | null; stocked: boolean
  pictures: number; thumb: string | null
}
interface Eq {
  equipment_key: string; Site_ID: string; category: string | null; description: string; qty: number | null
  uom: string | null; serials: string | null; condition: string | null; thumb: string | null
}
interface Img {
  id: number; source: string; is_primary: boolean; uploaded_by: string; uploaded_at: string
  removed_at: string | null; thumb: string; display: string; original: string
}
interface Detail {
  kind: Kind; key: string; can_edit: boolean; max: number
  item: Record<string, unknown> & { description?: string; stocked?: { sap: string; site: string }[] }
  images: Img[]; removed: Img[]
  family: { code: string; description: string; image_id: number; thumb: string }[]
}
interface Report {
  catalogue: { file: string; codes: number; conflict_count: number; conflicts: { code: string; kept: string; other: string }[]
    item_master_not_in_catalogue: string[]; at: string } | null
  equipment: { file: string; items: number; site: string; at: string } | null
}

const SOURCE: Record<string, string> = { upload: 'uploaded', drive: 'from Drive', family: 'family', practice: 'Practice' }
const errText = (e: unknown) => (e as Error)?.message || 'Something went wrong'

function PictureEditor({ kind, itemKey, onClose }: { kind: Kind; itemKey: string | null; onClose: () => void }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [assignTo, setAssignTo] = useState<string[]>([])
  const { data, isFetching } = useQuery<Detail>({
    queryKey: ['/catalogue/item', kind, itemKey],
    enabled: !!itemKey,
    queryFn: async () => (await api.get(`/catalogue/${kind}/${encodeURIComponent(itemKey!)}`)).data,
  })
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['/catalogue/item'] })
    void qc.invalidateQueries({ queryKey: ['/catalogue/materials'] })
    void qc.invalidateQueries({ queryKey: ['/catalogue/equipment'] })
    void qc.invalidateQueries({ queryKey: ['/catalogue/thumbs'] })
  }
  const run = useMutation({
    mutationFn: async (fn: () => Promise<unknown>) => fn(),
    onSuccess: () => refresh(),
    onError: (e) => message.error(errText(e)),
  })
  const upload = async (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    run.mutate(async () => {
      await api.post(`/catalogue/${kind}/${encodeURIComponent(itemKey!)}/images`, fd)
      message.success('Picture added')
    })
    return false
  }
  const canEdit = !!data?.can_edit
  const full = (data?.images.length ?? 0) >= (data?.max ?? 4)
  return (
    <Drawer open={!!itemKey} onClose={onClose} size="large" data-testid="catalogue-editor"
      title={data ? `${kind === 'material' ? data.key : (data.item.category ?? 'Equipment')} — ${data.item.description ?? ''}` : 'Loading…'}>
      {data && (
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          {kind === 'material' && (
            <Typography.Text type="secondary">
              {data.item.stocked?.length
                ? <>Stocked as {data.item.stocked.map((s) => `SAP ${s.sap} (${s.site})`).join(', ')}.</>
                : <>Not stocked at GI Hub yet — a HOD can still raise a PR for it.</>}
              {data.item.uom ? <> Unit: {String(data.item.uom)}.</> : null}
            </Typography.Text>
          )}
          {!data.images.length && <Empty description="No picture yet" />}
          <Image.PreviewGroup>
            <Space wrap size={12}>
              {data.images.map((im) => (
                <Card key={im.id} size="small" style={{ width: 200 }} data-testid="catalogue-image"
                  cover={<Image src={imgSrc(im.display)} preview={{ src: imgSrc(im.original) }} alt=""
                    style={{ height: 160, objectFit: 'contain' }} />}
                  actions={canEdit ? [
                    im.is_primary
                      ? <Tooltip key="p" title="The main picture"><StarFilled /></Tooltip>
                      : <Tooltip key="p" title="Make it the main picture">
                          <StarOutlined data-testid="catalogue-primary"
                            onClick={() => run.mutate(() => api.post(`/catalogue/images/${im.id}/primary`))} />
                        </Tooltip>,
                    <Popconfirm key="d" title="Remove this picture? It is kept and can be restored."
                      onConfirm={() => run.mutate(() => api.delete(`/catalogue/images/${im.id}`))}>
                      <DeleteOutlined data-testid="catalogue-remove" />
                    </Popconfirm>,
                  ] : undefined}>
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    {im.is_primary && <Tag color="gold" style={{ marginInlineEnd: 4 }}>main</Tag>}
                    {SOURCE[im.source] ?? im.source} · {im.uploaded_by} · {im.uploaded_at.slice(0, 10)}
                  </Typography.Text>
                </Card>
              ))}
            </Space>
          </Image.PreviewGroup>
          {canEdit && (
            <Space wrap>
              <Upload accept="image/jpeg,image/png,image/webp,image/heic,image/heif,.heic" showUploadList={false}
                beforeUpload={(f) => upload(f)} disabled={full || run.isPending}>
                <Button type="primary" icon={<CameraOutlined />} disabled={full || run.isPending} data-testid="catalogue-upload">
                  Add a picture (or take one)</Button>
              </Upload>
              {full && <Typography.Text type="secondary">{data.max} pictures is the most — remove one first.</Typography.Text>}
            </Space>
          )}
          {kind === 'material' && canEdit && data.images.length > 0 && (
            <Card size="small" title="Use the main picture for other codes too (a family)">
              <Space wrap>
                <Select mode="tags" style={{ width: 380 }} value={assignTo} onChange={setAssignTo}
                  placeholder="GI codes, e.g. GI-6000002" tokenSeparators={[',', ' ']} data-testid="catalogue-assign-codes" />
                <Button disabled={!assignTo.length || run.isPending} data-testid="catalogue-assign"
                  onClick={() => {
                    const main = data.images.find((x) => x.is_primary) ?? data.images[0]
                    run.mutate(async () => {
                      const r = (await api.post(`/catalogue/images/${main.id}/assign`, { codes: assignTo })).data as
                        { assigned: string[]; skipped: { code: string; why: string }[] }
                      message.success(`Given to ${r.assigned.length} code(s)`
                        + (r.skipped.length ? ` · skipped ${r.skipped.map((x) => `${x.code} (${x.why})`).join(', ')}` : ''))
                      setAssignTo([])
                    })
                  }}>Assign</Button>
              </Space>
            </Card>
          )}
          {kind === 'material' && data.family.length > 0 && !data.images.length && (
            <Card size="small" title="Pictures of the same family — use one?">
              <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
                These codes share this item&apos;s name apart from the size. Nothing is attached until somebody chooses.
              </Typography.Paragraph>
              <Space wrap>
                {data.family.map((f) => (
                  <Space key={f.code} direction="vertical" size={2} align="center" data-testid="catalogue-family">
                    <Thumb src={f.thumb} size={72} />
                    <Typography.Text style={{ fontSize: 11 }}>{f.code}</Typography.Text>
                    {canEdit && (
                      <Button size="small" onClick={() => run.mutate(async () => {
                        await api.post(`/catalogue/images/${f.image_id}/assign`, { codes: [data.key] })
                        message.success(`Using ${f.code}'s picture`)
                      })}>Use this</Button>
                    )}
                  </Space>
                ))}
              </Space>
            </Card>
          )}
          {canEdit && data.removed.length > 0 && (
            <Card size="small" title="Removed pictures">
              <Space wrap>
                {data.removed.map((im) => (
                  <Space key={im.id} direction="vertical" size={2} align="center">
                    <Thumb src={im.thumb} size={64} />
                    <Button size="small" icon={<RollbackOutlined />} data-testid="catalogue-restore"
                      onClick={() => run.mutate(() => api.post(`/catalogue/images/${im.id}/restore`))}>Restore</Button>
                  </Space>
                ))}
              </Space>
            </Card>
          )}
          {isFetching && <Typography.Text type="secondary">Refreshing…</Typography.Text>}
        </Space>
      )}
    </Drawer>
  )
}

function Materials({ onOpen }: { onOpen: (k: string) => void }) {
  const [q, setQ] = useState('')
  const [show, setShow] = useState<'all' | 'no_picture' | 'has_picture' | 'stocked' | 'not_stocked'>('all')
  const [page, setPage] = useState({ current: 1, pageSize: 50 })
  const { data, isFetching } = useQuery<{ total: number; counts: { total: number; with_picture: number }; items: Mat[] }>({
    queryKey: ['/catalogue/materials', q, show, page.current, page.pageSize],
    queryFn: async () => (await api.get('/catalogue/materials', { params: {
      q: q || undefined, show, limit: page.pageSize, offset: (page.current - 1) * page.pageSize } })).data,
    placeholderData: (p) => p,
  })
  const cols: ColumnsType<Mat> = [
    { title: '', key: 'pic', width: 64, render: (_: unknown, r) => <Thumb src={r.thumb} size={44} alt={r.description} /> },
    { title: 'GI code', dataIndex: 'code', key: 'code', width: 130 },
    { title: 'Description', dataIndex: 'description', key: 'd' },
    { title: 'UoM', dataIndex: 'uom', key: 'u', width: 70 },
    { title: 'At GI Hub', key: 's', width: 150,
      render: (_: unknown, r) => (r.stocked ? <Tag color="green">SAP {r.saps}</Tag> : <Tag>not stocked</Tag>) },
    { title: 'Pictures', dataIndex: 'pictures', key: 'p', width: 90,
      render: (v: number) => (v ? `${v}` : <Tag color="orange">needs one</Tag>) },
  ]
  const missing = (data?.counts.total ?? 0) - (data?.counts.with_picture ?? 0)
  return (
    <>
      <Space wrap style={{ marginBottom: 12 }}>
        <Input.Search allowClear placeholder="GI code, description or SAP" style={{ width: 280 }}
          onSearch={(v) => { setQ(v); setPage((p) => ({ ...p, current: 1 })) }} data-testid="catalogue-search" />
        <Segmented value={show} onChange={(v) => { setShow(v as typeof show); setPage((p) => ({ ...p, current: 1 })) }}
          options={[{ value: 'all', label: 'All' }, { value: 'no_picture', label: `Needs a picture (${missing.toLocaleString()})` },
            { value: 'has_picture', label: 'Has a picture' }, { value: 'stocked', label: 'Stocked' },
            { value: 'not_stocked', label: 'Not stocked' }]} />
      </Space>
      <Table size="small" loading={isFetching} columns={cols} dataSource={data?.items ?? []} rowKey="code"
        onRow={(r) => ({ onClick: () => onOpen(r.code), style: { cursor: 'pointer' } })}
        pagination={{ ...page, total: data?.total ?? 0, showSizeChanger: true,
          showTotal: (t) => `${t.toLocaleString()} code(s)`, onChange: (current, pageSize) => setPage({ current, pageSize }) }}
        data-testid="catalogue-materials" />
    </>
  )
}

function Equipment({ onOpen }: { onOpen: (k: string) => void }) {
  const [site, setSite] = useState<string | undefined>()
  const [q, setQ] = useState('')
  const { data, isFetching } = useQuery<{ items: Eq[] }>({
    queryKey: ['/catalogue/equipment', site, q],
    queryFn: async () => (await api.get('/catalogue/equipment', { params: { site_id: site, q: q || undefined } })).data,
  })
  const cols: ColumnsType<Eq> = [
    { title: '', key: 'pic', width: 64, render: (_: unknown, r) => <Thumb src={r.thumb} size={44} alt={r.description} /> },
    { title: 'Section', dataIndex: 'category', key: 'c', width: 170 },
    { title: 'Equipment', dataIndex: 'description', key: 'd' },
    { title: 'Qty', key: 'q', width: 90, render: (_: unknown, r) => `${r.qty ?? '—'} ${r.uom ?? ''}` },
    { title: 'Serials / tags', dataIndex: 'serials', key: 's', ellipsis: true },
    { title: 'Condition', dataIndex: 'condition', key: 'k', width: 100 },
  ]
  return (
    <>
      <Space wrap style={{ marginBottom: 12 }}>
        <SiteFilter style={{ width: 160 }} value={site} onChange={setSite} />
        <Input.Search allowClear placeholder="Equipment, section or tag" style={{ width: 260 }} onSearch={setQ} />
      </Space>
      <Table size="small" loading={isFetching} columns={cols} dataSource={data?.items ?? []} rowKey="equipment_key"
        onRow={(r) => ({ onClick: () => onOpen(r.equipment_key), style: { cursor: 'pointer' } })}
        pagination={{ pageSize: 50 }} data-testid="catalogue-equipment" />
    </>
  )
}

export default function CataloguePage() {
  const [tab, setTab] = useState<Kind>('material')
  const [open, setOpen] = useState<Key | null>(null)
  const { data: rep } = useQuery<Report>({
    queryKey: ['/catalogue/report'],
    queryFn: async () => (await api.get('/catalogue/report')).data,
    staleTime: 5 * 60_000,
  })
  const c = rep?.catalogue
  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Materials &amp; equipment catalogue</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Every GI material code and the site&apos;s plant &amp; tools, with pictures. Click a row to see or change its
        pictures. Pictures named by GI code in Drive&apos;s <i>Material Images</i> folder arrive with the next pull.
      </Typography.Paragraph>
      {c ? (
        <Alert type="info" showIcon style={{ marginBottom: 12 }} data-testid="catalogue-report"
          title={`From ${c.file} · ${c.codes.toLocaleString()} codes · read ${c.at.slice(0, 16).replace('T', ' ')}`}
          description={(c.conflict_count || c.item_master_not_in_catalogue.length) ? (
            <Space direction="vertical" size={2}>
              {c.conflict_count > 0 && <span>{c.conflict_count} code(s) carry two different descriptions in the file
                — the first is kept: {c.conflicts.slice(0, 5).map((x) => `${x.code} (“${x.kept}” / “${x.other}”)`).join('; ')}</span>}
              {c.item_master_not_in_catalogue.length > 0 && <span>{c.item_master_not_in_catalogue.length} GI code(s) in the
                item master are not in this file: {c.item_master_not_in_catalogue.slice(0, 20).join(', ')}</span>}
            </Space>) : undefined} />
      ) : (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          title="The catalogue has not been read from Drive yet — the next pull reads “All MATERIAL CODES-….xlsx”." />
      )}
      <Tabs activeKey={tab} onChange={(k) => { setTab(k as Kind); setOpen(null) }} items={[
        { key: 'material', label: 'Materials', children: <Materials onOpen={(k) => setOpen(k)} /> },
        { key: 'equipment', label: `Plant & tools${rep?.equipment ? ` (${rep.equipment.items})` : ''}`,
          children: <Equipment onOpen={(k) => setOpen(k)} /> },
      ]} />
      <PictureEditor kind={tab} itemKey={open ? String(open) : null} onClose={() => setOpen(null)} />
    </div>
  )
}
