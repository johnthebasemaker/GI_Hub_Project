import { useMemo, useState } from 'react'
import { baseReadout } from '../lib/units'
import {
  Alert, App, Button, Card, Checkbox, Col, DatePicker, Form, Input, InputNumber,
  Row, Select, Space, Typography,
} from 'antd'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import {
  useCategories, useDocsRequired, useInventoryMaster, useQcReturnLookup,
  useReturnEntry, useReturnSources, useSites,
} from '../api/hooks'
import type { Row as ApiRow } from '../api/client'
import DeliveryPrefRadio from '../components/DeliveryPrefRadio'
import DraftBanner from '../components/DraftBanner'
import EntryDocsUpload from '../components/EntryDocsUpload'
import type { EntryDoc } from '../components/EntryDocsUpload'
import ItemSnapshot from '../components/ItemSnapshot'
import { useFormDraft } from '../lib/formDraft'
import { loadDefaults, saveDefaults } from '../lib/smartDefaults'

/**
 * Parity A2 — the legacy Return Items gates, rebuilt: the return is made
 * AGAINST a receipt from the last 30 days (365 with the override window +
 * a mandatory justification, flagged red for the HOD), quantity is capped to
 * that receipt's quantity, Return DN No. is required, and a supporting
 * document must be attached (the one upload legacy always enforced).
 */
interface FormValues {
  Site_ID: string
  SAP_Code: string
  source_receipt_id?: number
  Quantity: number
  Date: Dayjs
  Reason?: string
  Return_DN_No?: string
  override_reason?: string
  Remarks?: string
}

/** What `/entry/qc-return/{no}` hands back — enough to fill the form. */
interface QcReturn {
  return_no: string
  SAP_Code: string
  Material_Name?: string | null
  Site_ID: string
  Lot_Number?: string | null
  rejected_qty: number
  submitted_qty: number
  decision_reason?: string | null
  inspected_by?: string | null
  source_receipt_id?: number | null
}

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Request failed'
}

const REASONS = ['defect', 'damage', 'overstock', 'unused', 'return_to_supplier', 'other']

export default function ReturnPage() {
  const { message } = App.useApp()
  const [form] = Form.useForm<FormValues>()
  const { data: sites } = useSites()
  const inventory = useInventoryMaster()
  const ret = useReturnEntry()
  const [docs, setDocs] = useState<EntryDoc[]>([])
  const [olderWindow, setOlderWindow] = useState(false)
  const draft = useFormDraft(form, 'return')
  const watchSap = Form.useWatch('SAP_Code', form)
  // Phase 14a (Q14-6): packs are entered; the base figure is read out live.
  const watchQty = Form.useWatch('Quantity', form)
  const watchSite = Form.useWatch('Site_ID', form)
  const watchSource = Form.useWatch('source_receipt_id', form)
  const { data: docsRequired } = useDocsRequired()
  const { data: sources, isFetching: sourcesLoading } =
    useReturnSources(watchSap, watchSite, olderWindow ? 365 : 30)

  const { data: categories } = useCategories()
  const [category, setCategory] = useState<string | undefined>(undefined)
  const itemOptions = (inventory.data?.items ?? [])
    .filter((r: ApiRow) => !category || String(r.Category ?? '').trim() === category)
    .map((r: ApiRow) => ({
      value: String(r.SAP_Code),
      label: `${r.SAP_Code} — ${r.Equipment_Description ?? ''}`,
    }))

  const source = useMemo(
    () => (sources ?? []).find((s) => s.id === watchSource),
    [sources, watchSource])
  const sourceIsOld = source
    ? dayjs().diff(dayjs(source.Date), 'day') > 30
    : false

  // ── the QC rejection this return discharges (Track 4) ────────────────────
  const qcLookup = useQcReturnLookup()
  const [qcNo, setQcNo] = useState('')
  const [qc, setQc] = useState<QcReturn | null>(null)

  const fetchQc = async () => {
    const ref = qcNo.trim()
    if (!ref) { message.warning('Type the Return No the inspector gave you'); return }
    try {
      const r = (await qcLookup.mutateAsync(ref)) as unknown as QcReturn
      setQc(r)
      // Fill, do not lock. A store keeper may legitimately send back LESS
      // than QC rejected — some already issued, some still being argued over
      // with the vendor — so the quantity is a starting point and a cap, not
      // a fixed value. The API enforces the cap regardless of what is typed.
      form.setFieldsValue({
        Site_ID: r.Site_ID,
        SAP_Code: r.SAP_Code,
        Quantity: r.rejected_qty,
        Reason: 'defect',
        source_receipt_id: r.source_receipt_id ?? undefined,
        Remarks: r.decision_reason
          ? `QC ${r.return_no}: ${r.decision_reason}`
          : `QC ${r.return_no}`,
      })
      message.success(`${r.return_no} — ${r.rejected_qty} of ${r.SAP_Code} rejected`)
    } catch (e) {
      setQc(null)
      message.error(errMsg(e))
    }
  }

  const clearQc = () => { setQc(null); setQcNo('') }

  const onFinish = async (v: FormValues) => {
    // A QC return always needs the paperwork, whatever the entry-document
    // setting says — rejected material going back to a supplier is not
    // something an operator convenience switch should be able to wave through.
    if ((docsRequired !== false || qc) && !docs.length) {
      message.error(qc
        ? `Attach the signed delivery note for ${qc.return_no} — rejected material does not leave site undocumented`
        : 'A supporting document must be attached to a return (legacy rule)')
      return
    }
    saveDefaults('return', { Site_ID: v.Site_ID, Reason: v.Reason ?? '' })
    const payload: ApiRow = {
      Date: v.Date.format('YYYY-MM-DD'),
      SAP_Code: v.SAP_Code,
      Quantity: v.Quantity,
      Site_ID: v.Site_ID,
      Reason: v.Reason || null,
      Remarks: v.Remarks || null,
      Return_DN_No: v.Return_DN_No || null,
      source_receipt_id: v.source_receipt_id ?? null,
      override_reason: sourceIsOld ? (v.override_reason || null) : null,
      attachment_ids: docs.map((d) => d.id),
      qc_return_no: qc?.return_no ?? null,
    }
    try {
      const res = await ret.mutateAsync(payload)
      if ((res as { queued?: boolean }).queued) message.warning('Offline — entry saved to the sync queue')
      else message.success(String((res as { message?: string }).message ?? 'Submitted for HOD approval'))
      form.resetFields(['SAP_Code', 'source_receipt_id', 'Quantity', 'Reason', 'Return_DN_No', 'override_reason', 'Remarks'])
      setDocs([])
      clearQc()
      draft.clear()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Return Stock
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Returns are made against a receipt from the last 30 days — pick the source receipt,
        cap is its received quantity. Older receipts need the override window and a
        justification (flagged for the HOD). A Return DN No. and a supporting document are
        required.
      </Typography.Paragraph>

      <DraftBanner hasDraft={draft.hasDraft} onRestore={draft.restore} onDiscard={draft.discard} />

      {/* The QC handoff. Sits ABOVE the form because it fills the form —
          putting it inside would suggest it is one more field to complete
          rather than the thing that completes the rest. */}
      <Card size="small" style={{ maxWidth: 820, marginBottom: 16 }}
        title="Returning material QC rejected?">
        <Typography.Paragraph type="secondary" style={{ marginTop: 0, marginBottom: 8 }}>
          Paste the Return No from the QC rejection and the form fills itself —
          material, site, quantity, lot and the inspector's reason. You can still
          change anything before posting, and you may return less than was rejected.
        </Typography.Paragraph>
        <Space.Compact style={{ width: '100%', maxWidth: 460 }}>
          <Input value={qcNo} placeholder="QCR-20260813-41" allowClear
            onChange={(e) => setQcNo(e.target.value)}
            onPressEnter={(e) => { e.preventDefault(); void fetchQc() }} />
          <Button type="primary" loading={qcLookup.isPending} onClick={() => void fetchQc()}>
            Fetch
          </Button>
        </Space.Compact>
        {qc && (
          <Alert type="info" showIcon style={{ marginTop: 12 }}
            title={`${qc.return_no} — ${qc.rejected_qty} of ${qc.submitted_qty} rejected`}
            description={(
              <span>
                {qc.Material_Name ? `${qc.Material_Name} · ` : ''}
                {qc.SAP_Code} at {qc.Site_ID}
                {qc.Lot_Number ? ` · lot ${qc.Lot_Number}` : ''}
                {qc.inspected_by ? ` · inspected by ${qc.inspected_by}` : ''}
                {qc.decision_reason ? <><br />Reason: {qc.decision_reason}</> : null}
                <br />
                <strong>A delivery note number and a signed document are required</strong>
                {' '}for this return, regardless of the site's document setting.
              </span>
            )}
            action={<Button size="small" onClick={clearQc}>Clear</Button>} />
        )}
      </Card>

      <Card style={{ maxWidth: 820 }}>
        <Form<FormValues> form={form} layout="vertical"
          onValuesChange={draft.onValuesChange}
          initialValues={{ Date: dayjs(), ...loadDefaults('return') }} onFinish={onFinish}>
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item name="Site_ID" label="Site" rules={[{ required: true }]}>
                <Select placeholder="Select site" options={(sites ?? []).map((s) => ({ value: s, label: s }))} />
              </Form.Item>
            </Col>
            <Col xs={24} md={5}>
              <Form.Item label="Category">
                <Select allowClear showSearch placeholder="All" value={category}
                  onChange={(v) => setCategory(v)}
                  options={(categories ?? []).map((c) => ({ value: c, label: c }))} />
              </Form.Item>
            </Col>
            <Col xs={24} md={11}>
              <Form.Item name="SAP_Code" label="Material (SAP Code)" rules={[{ required: true }]}>
                <Select showSearch placeholder="Search material" loading={inventory.isFetching} optionFilterProp="label" options={itemOptions} />
              </Form.Item>
            </Col>
          </Row>

          <ItemSnapshot sap={watchSap} site={watchSite} />

          <Row gutter={16}>
            <Col xs={24} md={16}>
              <Form.Item name="source_receipt_id" label="Source receipt (what is being returned)"
                rules={docsRequired !== false ? [{ required: true, message: 'Pick the source receipt' }] : []}
                extra={
                  <Checkbox checked={olderWindow} onChange={(e) => setOlderWindow(e.target.checked)}>
                    Override 30-day window (search a full year — justification required)
                  </Checkbox>
                }>
                <Select showSearch loading={sourcesLoading} optionFilterProp="label"
                  placeholder={watchSap ? 'Pick the receipt' : 'Pick a material first'}
                  options={(sources ?? []).map((s) => ({
                    value: s.id,
                    label: `${s.Date} · qty ${s.Quantity} · DN ${s.DN_No || '—'} · ${s.Supplier || 'no supplier'}`,
                  }))} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="Quantity" extra={baseReadout(watchSap, watchQty) ?? undefined} label={`Return quantity${source ? ` (max ${source.Quantity})` : ''}`}
                rules={[{ required: true }]}>
                <InputNumber min={0.0001} max={source ? Number(source.Quantity) : undefined}
                  style={{ width: '100%' }} placeholder="0" />
              </Form.Item>
            </Col>
          </Row>

          {sourceIsOld && (
            <>
              <Alert type="warning" showIcon style={{ marginBottom: 12 }}
                title="This receipt is older than 30 days — the return will be flagged for the HOD." />
              <Form.Item name="override_reason" label="Override justification"
                rules={[{ required: true, min: 3, message: 'Justify returning against an old receipt' }]}>
                <Input placeholder="why is this old receipt being returned?" />
              </Form.Item>
            </>
          )}

          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item name="Date" label="Date" rules={[{ required: true }]}>
                <DatePicker style={{ width: '100%' }} format="YYYY-MM-DD" />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="Reason" label="Reason" rules={[{ required: true, message: 'Reason required' }]}>
                <Select placeholder="reason" options={REASONS.map((r) => ({ value: r, label: r }))} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="Return_DN_No" label="Return DN No."
                rules={docsRequired !== false ? [{ required: true, message: 'Return DN No. required' }] : []}>
                <Input placeholder="delivery-note number" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="Remarks" label="Remarks"><Input.TextArea rows={2} /></Form.Item>

          <EntryDocsUpload docType="return" siteId={watchSite}
            value={docs} onChange={setDocs} required={docsRequired !== false} />

          <Space size={16} wrap>
            <Button type="primary" htmlType="submit" loading={ret.isPending}>Post return</Button>
            <DeliveryPrefRadio />
          </Space>
        </Form>
      </Card>
    </div>
  )
}
