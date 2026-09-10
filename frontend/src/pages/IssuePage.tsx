import { useMemo, useState } from 'react'
import {
  Alert, App, Button, Card, Col, DatePicker, Form, Input, InputNumber, Popconfirm, Row, Select,
  Space, Tag, Typography,
} from 'antd'
import { Table } from '../lib/smartTable'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { ColumnsType } from 'antd/es/table'
import { BarcodeOutlined, DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { useBins, useBulkEntry, useCategories, useDocsRequired, useInventoryMaster, usePpeEligible, useSites, useWbsOptions, useWorkTypeOptions } from '../api/hooks'
import type { Row as ApiRow } from '../api/client'
import PpeIssueFields from '../components/PpeIssueFields'
import { emptyPpe, findPpeRule } from '../lib/ppe'
import type { PpeState } from '../lib/ppe'
import DeliveryPrefRadio from '../components/DeliveryPrefRadio'
import DraftBanner from '../components/DraftBanner'
import EntryDocsUpload from '../components/EntryDocsUpload'
import type { EntryDoc, OcrDocResult } from '../components/EntryDocsUpload'
import { useFormDraft } from '../lib/formDraft'
import ItemSnapshot from '../components/ItemSnapshot'
import QcClearanceBanner from '../components/QcClearanceBanner'
import QrScanner from '../components/QrScanner'
import { BARCODE_FORMATS, matchScanToSap } from '../lib/barcode'
import { loadDefaults, saveDefaults } from '../lib/smartDefaults'

interface FormValues {
  Site_ID: string
  SAP_Code: string
  Quantity: number
  Date: Dayjs
  Work_Type?: string
  Issued_To?: string
  Issued_By?: string
  PR_Number?: string
  Tank_No?: string
  Serial_No?: string
  Lot_Number?: string
  Remarks?: string
  wbs?: string
  FEFO_Override?: string
}

// A batched line: API-shaped payload + a local uid + a display label.
interface StagedRow extends ApiRow {
  _uid: string
  _label: string
}

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Request failed'
}

let _seq = 0

export default function IssuePage() {
  const { message } = App.useApp()
  const [form] = Form.useForm<FormValues>()
  const { data: sites } = useSites()
  const inventory = useInventoryMaster()
  const bulk = useBulkEntry('consumption', ['/consumption'])
  const [staged, setStaged] = useState<StagedRow[]>([])
  const [editingUid, setEditingUid] = useState<string | null>(null)
  const [scanOpen, setScanOpen] = useState(false)
  const [docs, setDocs] = useState<EntryDoc[]>([])
  const draft = useFormDraft(form, 'issue')

  const watchSap = Form.useWatch('SAP_Code', form)
  const watchSite = Form.useWatch('Site_ID', form)
  const watchLot = Form.useWatch('Lot_Number', form)
  const { data: bins } = useBins(watchSap, watchSite)
  const { data: wbsOptions } = useWbsOptions(watchSite)
  // Phase 9a. `enforced` is false until the HOD curates a list for this site,
  // and the field stays a free-text Input in that case — the backend gate is
  // conditional, so a Select over an empty list would refuse entries the API
  // still accepts.
  const { data: workTypes } = useWorkTypeOptions(watchSite)
  const { data: docsRequired } = useDocsRequired()

  // ── PPE (QSEP slice 4, Option A) ─────────────────────────────────────────
  // There is no separate PPE form. When the picked material is PPE this form
  // grows an employee + safety-approval panel and the backend writes a
  // ppe_distributions row beside the ordinary stock movement. `ppeRule` is
  // null for the ~450 materials that are not PPE, and the panel renders
  // nothing at all for them.
  const { data: ppeEligible } = usePpeEligible(watchSite)
  const ppeRule = useMemo(() => findPpeRule(ppeEligible, watchSap),
                          [ppeEligible, watchSap])
  const [ppe, setPpe] = useState<PpeState>(emptyPpe())

  // Category narrows the material picker (search stays available inside it).
  const { data: categories } = useCategories()
  const [category, setCategory] = useState<string | undefined>(undefined)

  // ── Surface-Shields workflow (2026-07-18) ────────────────────────────────
  // Lining materials are issued PER SYSTEM: the SK picks a System Code first,
  // which filters the picker to that recipe's SAPs (sme_recipe.SAP_Code) and
  // shows the site's Done vs Pending SQM for the system.
  const SURFACE_SHIELDS = 'Surface Shields'
  const [liningCode, setLiningCode] = useState<string | undefined>()
  const lining = useQuery({
    queryKey: ['/entry/lining-systems', watchSite],
    // Only the Surface-Shields flow reads this, so it must not be on the
    // critical path of an ordinary issue: fetched when that category is
    // picked, then cached for the rest of the session.
    enabled: category === SURFACE_SHIELDS,
    staleTime: 300_000,
    queryFn: async () => (await api.get('/entry/lining-systems',
      { params: watchSite ? { site_id: watchSite } : {} })).data as {
        systems: { code: string; short_name: string; substrate: string
          lining_system: string; saps: string[]
          sqm: { equipment_count: number; original_sqm: number
            done_sqm: number; pending_sqm: number } }[]
        sap_index: Record<string, string[]>
      },
  })
  const [liningTag, setLiningTag] = useState<string | undefined>()
  // The equipment carrying the chosen system code, at this site — the same
  // list the 13f queue offers, from the same endpoint, so the two can never
  // disagree about which tanks a system applies to.
  const liningEquipment = useQuery({
    queryKey: ['/execution/sme-link/equipment', liningCode, watchSite],
    enabled: !!liningCode,
    staleTime: 300_000,
    queryFn: async () => (await api.get<{ items: { tag: string; name?: string }[] }>(
      '/execution/sme-link/equipment',
      { params: { code: liningCode, ...(watchSite ? { site_id: watchSite } : {}) } })
    ).data.items,
  })
  const liningSystem = useMemo(
    () => lining.data?.systems.find((s) => s.code === liningCode),
    [lining.data, liningCode])
  const isShieldContext = category === SURFACE_SHIELDS
  const shieldSapOf = useMemo(() => {
    const set = new Set<string>()
    for (const r of (inventory.data?.items ?? []) as ApiRow[]) {
      if (String(r.Category ?? '').trim() === SURFACE_SHIELDS) set.add(String(r.SAP_Code))
    }
    return set
  }, [inventory.data])

  const itemOptions = useMemo(() => (inventory.data?.items ?? [])
    .filter((r: ApiRow) => !category || String(r.Category ?? '').trim() === category)
    .filter((r: ApiRow) => !(isShieldContext && liningSystem)
      || liningSystem.saps.includes(String(r.SAP_Code)))
    .map((r: ApiRow) => ({
      value: String(r.SAP_Code),
      label: `${r.SAP_Code} — ${r.Equipment_Description ?? ''}`,
    })), [inventory.data, category, isShieldContext, liningSystem])
  const labelFor = (sap: string) => itemOptions.find((o) => o.value === sap)?.label ?? sap

  // Barcode/QR pick: decoded text → SAP code → select it in the form.
  const onScan = (decoded: string) => {
    setScanOpen(false)
    const sap = matchScanToSap(decoded, inventory.data?.items ?? [])
    if (sap) {
      setCategory(undefined) // the scanned item may sit outside the filter
      form.setFieldsValue({ SAP_Code: sap })
      message.success(`Scanned: ${sap}`)
    } else {
      message.warning(`No material matches the scanned code "${decoded.slice(0, 60)}"`)
    }
  }

  // C3 doc assist: hand-written consumption note → fuzzy-matched rows.
  // A single confident row pre-fills the form; more rows → point at the
  // OCR Import page, which stages a whole grid at once.
  const onOcrResult = (res: OcrDocResult) => {
    const rows = res.rows ?? []
    const auto = rows.filter((r) => r.match_state === 'auto')
    if (auto.length === 1 && !form.getFieldValue('SAP_Code')) {
      const r = auto[0]
      setCategory(undefined)
      form.setFieldsValue({
        SAP_Code: String(r.SAP_Code),
        ...(r.quantity != null ? { Quantity: Number(r.quantity) } : {}),
        ...(r.issued_to ? { Issued_To: String(r.issued_to) } : {}),
        ...(r.work_type ? { Work_Type: String(r.work_type) } : {}),
      })
      message.success(`Read from the note: ${r.SAP_Code} — fields pre-filled`)
    } else if (rows.length > 1) {
      message.info(`The note lists ${rows.length} materials — use the OCR Import `
        + 'page to stage them all at once')
    } else if (auto.length === 0 && rows.length === 1) {
      message.warning('Read the note but could not confidently match the '
        + 'material — pick it manually')
    } else {
      message.warning('No consumption rows found on this document')
    }
  }

  // Add the current form to the batch (or update the line being edited).
  const addToBatch = async () => {
    const v = await form.validateFields()
    // Surface-Shields gate: a lining material can only be issued against a
    // selected System Code (the code travels in Remarks for the HOD).
    if (shieldSapOf.has(String(v.SAP_Code)) && !liningCode) {
      setCategory(SURFACE_SHIELDS)
      message.error('This is a Surface Shields material — select its Lining '
        + 'System Code first (the picker filters to that recipe).')
      return
    }
    // PPE gate, mirrored from services/ppe.py. The API is the boundary — this
    // is here so the SK is told at the point of adding the line rather than
    // after submitting a batch of six.
    if (ppeRule) {
      if (!ppe.employeeId.trim()) {
        message.error('This is PPE — enter the ID number of the employee receiving it.')
        return
      }
      if (ppeRule.requires_safety_doc && !ppe.doc.length) {
        message.error('This is PPE — attach the signed Safety Approval before adding the line.')
        return
      }
    }
    // Smart defaults: remember the routine fields for the next session.
    saveDefaults('issue', { Site_ID: v.Site_ID, Work_Type: v.Work_Type ?? '', Issued_By: v.Issued_By ?? '' })
    // ⚠️ THE `LS <code>` SUFFIX IS READ BACK BY THE 13f QUEUE, so its shape is
    // a contract now rather than a note for a human. `sme_link.hint_system_code`
    // parses the token after "LS "; keep the code first and unpunctuated.
    const lsNote = liningCode && shieldSapOf.has(String(v.SAP_Code))
      ? `LS ${liningCode}${liningSystem ? ` (${liningSystem.short_name})` : ''}`
      : null
    const payload: StagedRow = {
      _uid: editingUid ?? `r${++_seq}`,
      _label: labelFor(v.SAP_Code),
      Date: v.Date.format('YYYY-MM-DD'),
      SAP_Code: v.SAP_Code,
      Quantity: v.Quantity,
      Site_ID: v.Site_ID,
      Work_Type: v.Work_Type || null,
      Issued_To: v.Issued_To || null,
      Issued_By: v.Issued_By || null,
      PR_Number: v.PR_Number || null,
      // The SK's chosen vessel wins over the free-text Tank No. box when they
      // picked one from the filtered list — it is a real equipment tag rather
      // than whatever somebody typed, and the queue can act on it.
      Tank_No: (shieldSapOf.has(String(v.SAP_Code)) && liningTag)
        ? liningTag : (v.Tank_No || null),
      Serial_No: v.Serial_No || null,
      Lot_Number: v.Lot_Number || null,
      Remarks: [v.Remarks, lsNote].filter(Boolean).join(' · ') || null,
      wbs: v.wbs || null,
      // Parity B1 — a manual lot pick is a FEFO override; the reason travels
      // to the HOD (allow-and-log ruling: never blocks).
      FEFO_Override: v.Lot_Number ? (v.FEFO_Override || 'manual lot (no reason given)') : null,
      // QSEP — carried per LINE, not per batch. Two workers can be issued
      // different PPE in the same batch, so a batch-level field would
      // attribute the second person's gear to the first.
      ...(ppeRule ? {
        employee_id_number: ppe.employeeId.trim(),
        safety_doc_id: ppe.doc[0]?.id ?? null,
        early_reason: ppe.earlyReason.trim() || null,
      } : {}),
    }
    setStaged((prev) => editingUid
      ? prev.map((r) => (r._uid === editingUid ? payload : r))
      : [...prev, payload])
    setEditingUid(null)
    // Keep Site + Date for the next line; clear the item-specific fields.
    form.resetFields(['SAP_Code', 'Quantity', 'Issued_To', 'PR_Number', 'Tank_No', 'Serial_No', 'Lot_Number', 'Remarks'])
    // The PPE panel clears with them: the next line is very likely a
    // different person, and a sticky employee ID is how somebody else's
    // boots end up on the wrong record.
    setPpe(emptyPpe())
  }

  const editLine = (r: StagedRow) => {
    setEditingUid(r._uid)
    form.setFieldsValue({
      Site_ID: r.Site_ID as string, SAP_Code: r.SAP_Code as string,
      Quantity: r.Quantity as number, Date: dayjs(r.Date as string),
      Work_Type: (r.Work_Type as string) ?? undefined, Issued_To: (r.Issued_To as string) ?? undefined,
      Issued_By: (r.Issued_By as string) ?? undefined, PR_Number: (r.PR_Number as string) ?? undefined,
      Tank_No: (r.Tank_No as string) ?? undefined, Serial_No: (r.Serial_No as string) ?? undefined,
      Lot_Number: (r.Lot_Number as string) ?? undefined, Remarks: (r.Remarks as string) ?? undefined,
    })
  }

  const removeLine = (uid: string) => {
    setStaged((prev) => prev.filter((r) => r._uid !== uid))
    if (editingUid === uid) setEditingUid(null)
  }

  const submitBatch = async () => {
    if (!staged.length) return
    if (docsRequired !== false && !docs.length) {
      message.error('Attach a supporting document (hand-written note / delivery note) before submitting')
      return
    }
    const rows = staged.map(({ _uid, _label, ...rest }) => { void _uid; void _label; return rest })
    try {
      const res = await bulk.mutateAsync({ rows, attachment_ids: docs.map((d) => d.id) })
      if (res.queued) message.warning(`Offline — ${res.staged} issue line(s) saved to the sync queue`)
      else message.success(`${res.staged} issue line(s) submitted for HOD approval`)
      setStaged([])
      setDocs([])
      draft.clear()
      form.resetFields(['SAP_Code', 'Quantity', 'Issued_To', 'PR_Number', 'Tank_No', 'Serial_No', 'Lot_Number', 'Remarks'])
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns: ColumnsType<StagedRow> = [
    { title: 'Material', dataIndex: '_label', ellipsis: true },
    { title: 'Qty', dataIndex: 'Quantity', align: 'right', width: 80 },
    { title: 'Work Type', dataIndex: 'Work_Type', width: 120, render: (v) => v ?? '—' },
    { title: 'Issued To', dataIndex: 'Issued_To', width: 120, render: (v) => v ?? '—' },
    { title: 'Lot', dataIndex: 'Lot_Number', width: 110, render: (v) => v ?? 'FEFO' },
    {
      title: '', key: '_act', width: 90, align: 'right',
      render: (_: unknown, r: StagedRow) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => editLine(r)} />
          <Popconfirm title="Remove this line?" onConfirm={() => removeLine(r._uid)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Issue Stock (Consumption)</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Add each material to the batch, review the list, then submit them all at once for HOD
        approval. Leave Lot blank to auto-tag the earliest-expiry open lot (FEFO). Over-issue is
        allowed and logged (not blocked) — same as the old app.
      </Typography.Paragraph>

      <DraftBanner hasDraft={draft.hasDraft} onRestore={draft.restore} onDiscard={draft.discard} />
      <Card style={{ maxWidth: 860, marginBottom: 16 }}>
        <Form<FormValues> form={form} layout="vertical"
          onValuesChange={draft.onValuesChange}
          initialValues={{ Date: dayjs(), ...loadDefaults('issue') }}>
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
              <Form.Item label="Material (SAP Code)" required style={{ marginBottom: 0 }}>
                <Space.Compact style={{ width: '100%' }}>
                  <Form.Item name="SAP_Code" noStyle rules={[{ required: true, message: 'Pick a material' }]}>
                    <Select showSearch placeholder="Search material" loading={inventory.isFetching} optionFilterProp="label" options={itemOptions} />
                  </Form.Item>
                  <Button icon={<BarcodeOutlined />} onClick={() => setScanOpen(true)}
                    aria-label="Scan a material barcode" title="Scan barcode / QR" />
                </Space.Compact>
              </Form.Item>
            </Col>
          </Row>

          {(isShieldContext || (watchSap && shieldSapOf.has(String(watchSap)))) && (
            <div style={{ marginBottom: 12 }}>
              <Space wrap align="center">
                <Typography.Text strong>Lining System:</Typography.Text>
                <Select showSearch allowClear style={{ minWidth: 320 }}
                  placeholder="Select the system code FIRST"
                  loading={lining.isFetching} value={liningCode}
                  onChange={(v) => { setLiningCode(v); setLiningTag(undefined); form.resetFields(['SAP_Code']) }}
                  optionFilterProp="label"
                  options={(lining.data?.systems ?? [])
                    .filter((s) => s.saps.length > 0)
                    .map((s) => ({
                      value: s.code,
                      label: `${s.code} — ${s.short_name} (${s.substrate || '?'})`,
                    }))} />
                {/* ⚠️ PHASE 13f — THE TAG, AT THE MOMENT THE SK KNOWS IT.
                    The store keeper knows which vessel the drum is walking to;
                    they do NOT know how many square metres it will cover,
                    because the drum leaves before the area is applied. That is
                    the same reasoning that made Phase 9d paper-first. So the
                    Issue form captures what an SK can actually answer, and the
                    field supplies the area later, in the queue.

                    Optional on purpose: an SK who genuinely does not know the
                    destination leaves it blank rather than inventing one, and
                    the queue asks. A required box gets a wrong answer. */}
                {liningSystem && (
                  <Select showSearch allowClear style={{ minWidth: 240 }}
                    placeholder="Equipment / tank (optional)"
                    value={liningTag} onChange={setLiningTag}
                    loading={liningEquipment.isFetching}
                    optionFilterProp="label"
                    options={(liningEquipment.data ?? []).map((e) => ({
                      value: e.tag,
                      label: `${e.tag}${e.name && e.name !== e.tag ? ` — ${e.name}` : ''}`,
                    }))} />
                )}
                {liningSystem && (
                  <>
                    <Tag color="green">
                      Done {liningSystem.sqm.done_sqm} SQM
                    </Tag>
                    <Tag color={liningSystem.sqm.pending_sqm > 0 ? 'orange' : 'default'}>
                      Pending {liningSystem.sqm.pending_sqm} SQM
                    </Tag>
                    <Typography.Text type="secondary">
                      of {liningSystem.sqm.original_sqm} SQM ·{' '}
                      {liningSystem.sqm.equipment_count} unit(s)
                    </Typography.Text>
                  </>
                )}
              </Space>
              {!liningCode && (
                <Alert type="info" showIcon style={{ marginTop: 8 }}
                  title="Surface Shields materials are issued per lining system —
                    pick the System Code to filter the materials to its recipe." />
              )}
            </div>
          )}

          {/* Both halves of the issue gate — MTC on file AND QC-approved qty.
              Silent for every material outside the controlled category. */}
          <QcClearanceBanner sap={watchSap} site={watchSite} />

          {/* Current stock + 30-day trend for the picked material (advisory). */}
          <ItemSnapshot sap={watchSap} site={watchSite} />

          {/* QSEP — appears ONLY when the picked material is PPE. Sits above
              Quantity so the SK reads "who is this for" before they type a
              number, which is the order the paper form has always used. */}
          <PpeIssueFields rule={ppeRule} siteId={watchSite}
            value={ppe} onChange={setPpe} />

          {!!bins?.length && (
            <div style={{ marginTop: -4, marginBottom: 12 }}>
              <Typography.Text type="secondary" style={{ marginRight: 6 }}>Pull from bin:</Typography.Text>
              {bins.map((b) => <Tag key={b} color="blue">{b}</Tag>)}
            </div>
          )}

          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item name="Quantity" label="Quantity" rules={[{ required: true }]}>
                <InputNumber min={0.0001} style={{ width: '100%' }} placeholder="0" />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="Date" label="Date" rules={[{ required: true }]}>
                <DatePicker style={{ width: '100%' }} format="YYYY-MM-DD" />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="Lot_Number" label="Lot (optional)">
                <Input placeholder="blank → FEFO auto-pick" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            {!!wbsOptions?.length && (
              <Col xs={24} md={8}>
                <Form.Item name="wbs" label="WBS Number"
                  rules={[{ required: true, message: 'This site requires a WBS' }]}>
                  <Select showSearch placeholder="Pick WBS"
                    options={wbsOptions.map((w) => ({ value: w, label: w }))} />
                </Form.Item>
              </Col>
            )}
            {!!watchLot && (
              <Col xs={24} md={8}>
                <Form.Item name="FEFO_Override" label="Reason for manual lot (FEFO override)"
                  rules={[{ min: 5, message: 'Give at least 5 characters' }]}>
                  <Input placeholder="why not the FEFO lot?" />
                </Form.Item>
              </Col>
            )}
          </Row>
          <Row gutter={16}>
            <Col xs={24} md={8}>
              {workTypes?.enforced ? (
                <Form.Item name="Work_Type" label="Work Type"
                  extra={(() => {
                    const wt = form.getFieldValue('Work_Type')
                    const hit = workTypes.items.find((o) => o.Work_Type === wt)
                    return hit?.WBS_Number
                      ? `Charges to WBS ${hit.WBS_Number}`
                      : 'This site uses a fixed list, managed by your HOD.'
                  })()}
                  rules={[{ required: true, message: 'This site requires a work type' }]}>
                  <Select showSearch placeholder="Pick a work type"
                    options={workTypes.items.map((o) => ({
                      value: o.Work_Type,
                      label: o.WBS_Number ? `${o.Work_Type} → ${o.WBS_Number}` : o.Work_Type,
                    }))} />
                </Form.Item>
              ) : (
                <Form.Item name="Work_Type" label="Work Type">
                  <Input placeholder="e.g. Maintenance" />
                </Form.Item>
              )}
            </Col>
            <Col xs={24} md={8}><Form.Item name="Issued_To" label="Issued To"><Input placeholder="recipient / crew" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name="Issued_By" label="Issued By"><Input placeholder="issuer" /></Form.Item></Col>
          </Row>
          <Row gutter={16}>
            <Col xs={24} md={8}><Form.Item name="PR_Number" label="PR Number"><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name="Tank_No" label="Tank No"><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name="Serial_No" label="Serial No"><Input /></Form.Item></Col>
          </Row>
          <Form.Item name="Remarks" label="Remarks"><Input.TextArea rows={2} /></Form.Item>
          <Space>
            <Button type={editingUid ? 'primary' : 'default'} icon={<PlusOutlined />} onClick={addToBatch}>
              {editingUid ? 'Update line' : 'Add to batch'}
            </Button>
            {editingUid && <Button onClick={() => { setEditingUid(null); form.resetFields(['SAP_Code', 'Quantity', 'Issued_To', 'PR_Number', 'Tank_No', 'Serial_No', 'Lot_Number', 'Remarks']) }}>Cancel edit</Button>}
          </Space>
        </Form>
      </Card>

      <Card
        title={`Batch (${staged.length} line${staged.length === 1 ? '' : 's'})`}
        extra={
          <Space size={16} wrap>
            <DeliveryPrefRadio />
            <Button type="primary" disabled={!staged.length} loading={bulk.isPending} onClick={submitBatch}>
              Submit batch to HOD
            </Button>
          </Space>
        }
      >
        <EntryDocsUpload docType="consumption" siteId={watchSite}
          value={docs} onChange={setDocs} required={docsRequired !== false}
          ocrKind="ocr_consumption" onOcrResult={onOcrResult} />
        <Table<StagedRow> sticky={{ offsetHeader: 64 }} size="small" rowKey="_uid" columns={columns} dataSource={staged}
          pagination={false}
          locale={{ emptyText: 'No lines yet — add materials above, then submit them all at once.' }} />
      </Card>

      <QrScanner open={scanOpen} title="Scan material barcode / QR"
        formats={BARCODE_FORMATS} manualPlaceholder="…or type the SAP code"
        onClose={() => setScanOpen(false)} onDecode={onScan} />
    </div>
  )
}
