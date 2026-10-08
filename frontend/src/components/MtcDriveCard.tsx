import { useState } from 'react'
import { App, Button, Card, Collapse, Input, Modal, Select, Space, Tag, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { DriveDocsModal } from './DriveDocs'
import type { LotRow } from '../api/lotHooks'

/**
 * Phase 22c — the suppliers' certificates from the Drive folder *MTC*, on
 * their lots (rulings Q22-10/11).
 *
 * A certificate whose batch AND product match a lot is filed by the pull by
 * itself (it shows as MTC ✓ in the table). Everything else needs a person:
 *   · Admin / HOD / QC assign a file to the lot(s) it covers — AR bricks by
 *     container, CHEMOLINE rolls by order (Q22-10);
 *   · QC confirms or rejects every proposal. Only a confirmed one becomes the
 *     lot's certificate, because a certificate on file clears the issue gate.
 */
interface MtcFile { id: number; name: string; link_status: string | null; parsed_key: string | null
                    lots: { SAP_Code: string; Lot_Number: string }[] }
interface Proposal { id: number; drive_file_id: number; file: string; SAP_Code: string
                     Lot_Number: string; source: string; proposed_by: string; batch_text: string | null }
interface Overview { files: MtcFile[]; proposed: Proposal[]
                     lots_without_mtc: { SAP_Code: string; Lot_Number: string; site: string; description: string }[] }

const PROPOSERS = new Set(['admin', 'hod', 'qc', 'qc_hod'])
const QC = new Set(['qc', 'qc_hod'])

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

export default function MtcDriveCard({ lots, site }: { lots: LotRow[]; site?: string }) {
  const { message } = App.useApp()
  const { user } = useAuth()
  const qc = useQueryClient()
  const role = user?.role ?? ''
  const [view, setView] = useState<{ id: number; name: string } | null>(null)
  const [assign, setAssign] = useState<MtcFile | null>(null)
  const [pick, setPick] = useState<string[]>([])
  const [note, setNote] = useState('')
  const { data } = useQuery<Overview>({
    queryKey: ['/drive/mtc', site],
    queryFn: async () => (await api.get('/drive/mtc', { params: site ? { site_id: site } : {} })).data,
  })
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['/drive/mtc'] })
    void qc.invalidateQueries({ queryKey: ['/lot-register'] })
  }
  const doAssign = useMutation({
    mutationFn: async () => (await api.post(`/drive/mtc/${assign!.id}/assign`, {
      lots: pick.map((k) => { const [SAP_Code, Lot_Number] = k.split('|'); return { SAP_Code, Lot_Number } }),
      note: note || null })).data,
    onSuccess: () => { message.success('Sent to QC to confirm'); setAssign(null); setPick([]); setNote(''); refresh() },
    onError: (e) => message.error(errMsg(e)),
  })
  const decide = useMutation({
    mutationFn: async ({ id, ok }: { id: number; ok: boolean }) =>
      (await api.post(`/drive/mtc/assignments/${id}/${ok ? 'confirm' : 'reject'}`)).data,
    onSuccess: (_r, v) => { message.success(v.ok ? 'Certificate filed on the lot' : 'Proposal turned down'); refresh() },
    onError: (e) => message.error(errMsg(e)),
  })
  if (!data || (!data.files.length && !data.lots_without_mtc.length)) return null
  const linked = data.files.filter((f) => f.link_status === 'linked')
  const open = data.files.filter((f) => f.link_status !== 'linked')
  const lotOptions = Array.from(new Map(lots.map((l) => [`${l.SAP_Code}|${l.Lot_Number}`, {
    value: `${l.SAP_Code}|${l.Lot_Number}`,
    label: `${String(l.Equipment_Description ?? l.SAP_Code)} — lot ${l.Lot_Number} (SAP ${l.SAP_Code})`,
  }])).values())

  return (
    <Card size="small" style={{ marginTop: 16 }} data-testid="mtc-drive" title="Certificates (MTC) from Drive"
      extra={<Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {linked.length} file(s) on their lots · {data.lots_without_mtc.length} Surface Shield lot(s) without one</Typography.Text>}>
      {!!data.proposed.length && (
        <div data-testid="mtc-proposed" style={{ marginBottom: 12 }}>
          <Typography.Text strong>Waiting for QC to confirm</Typography.Text>
          {data.proposed.map((p) => (
            <div key={p.id} style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', padding: '4px 0' }}>
              <Button size="small" type="link" onClick={() => setView({ id: p.drive_file_id, name: p.file })}>{p.file}</Button>
              <span>→ SAP {p.SAP_Code}, lot <b>{p.Lot_Number}</b></span>
              <Tag>{p.source === 'manual' ? `assigned by ${p.proposed_by}` : `batch ${p.batch_text ?? ''} — no product to check`}</Tag>
              {QC.has(role) && (
                <Space size={4}>
                  <Button size="small" type="primary" data-testid="mtc-confirm" disabled={decide.isPending}
                    onClick={() => decide.mutate({ id: p.id, ok: true })}>Confirm</Button>
                  <Button size="small" danger disabled={decide.isPending}
                    onClick={() => decide.mutate({ id: p.id, ok: false })}>Reject</Button>
                </Space>
              )}
            </div>
          ))}
        </div>
      )}
      {!!open.length && (
        <div data-testid="mtc-open" style={{ marginBottom: 12 }}>
          <Typography.Text strong>Needs a person</Typography.Text>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: 0 }}>
            These files name no batch GI Hub holds (or no batch at all). Open one, and if you know
            which lot(s) it certifies, assign it — QC then confirms.
          </Typography.Paragraph>
          {open.map((f) => (
            <div key={f.id} style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', padding: '2px 0' }}>
              <Button size="small" type="link" onClick={() => setView({ id: f.id, name: f.name })}>{f.name}</Button>
              {f.parsed_key && <Typography.Text type="secondary" style={{ fontSize: 12 }}>batch {f.parsed_key}</Typography.Text>}
              {f.link_status === 'suggested' && <Tag color="gold">proposed</Tag>}
              {PROPOSERS.has(role) && (
                <Button size="small" data-testid="mtc-assign" onClick={() => { setAssign(f); setPick([]) }}>Assign to lot(s)</Button>
              )}
            </div>
          ))}
        </div>
      )}
      <Collapse size="small" items={[
        { key: 'linked', label: `On their lots (${linked.length})`, children: (
          <div>{linked.map((f) => (
            <div key={f.id}><Button size="small" type="link" onClick={() => setView({ id: f.id, name: f.name })}>{f.name}</Button>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {f.lots.map((l) => `${l.SAP_Code}/${l.Lot_Number}`).join(', ')}</Typography.Text></div>))}
          </div>) },
        { key: 'missing', label: `Surface Shield lots without a certificate (${data.lots_without_mtc.length})`, children: (
          <div data-testid="mtc-missing" style={{ fontSize: 12 }}>{data.lots_without_mtc.map((l) => (
            <div key={`${l.SAP_Code}|${l.Lot_Number}|${l.site}`}>{l.description} — lot <b>{l.Lot_Number}</b> (SAP {l.SAP_Code}, {l.site})</div>))}
          </div>) },
      ]} />
      {view && <DriveDocsModal title={view.name} docs={[{ id: view.id, name: view.name, mime: null }]}
        onClose={() => setView(null)} />}
      {assign && (
        <Modal open title={`Assign ${assign.name}`} onCancel={() => setAssign(null)} okText="Send to QC"
          okButtonProps={{ disabled: !pick.length || doAssign.isPending,
            ...({ 'data-testid': 'mtc-assign-ok' } as Record<string, string>) }}
          onOk={() => doAssign.mutate()}>
          <Typography.Paragraph type="secondary">Pick every lot this certificate covers. QC confirms
            before it counts as the lot&apos;s certificate.</Typography.Paragraph>
          <Select mode="multiple" style={{ width: '100%' }} value={pick} onChange={setPick} showSearch
            optionFilterProp="label" options={lotOptions} placeholder="Material — lot" data-testid="mtc-assign-lots" />
          <Input style={{ marginTop: 8 }} value={note} onChange={(e) => setNote(e.target.value)}
            placeholder="Note for QC (e.g. 1st container, DN 15707)" />
        </Modal>
      )}
    </Card>
  )
}
