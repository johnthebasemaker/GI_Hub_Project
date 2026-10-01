import { useEffect } from 'react'
import { App, AutoComplete, Form, Input, InputNumber, Modal, Select, Space, Tag } from 'antd'
import { useCategories, useCreateInventory, useSites, useUpdateInventory } from '../api/hooks'
import type { InventoryBase } from '../api/hooks'
import type { Row } from '../api/client'

type Detail = string | { code?: string; message?: string; value?: string }

function detailOf(e: unknown): Detail | undefined {
  return (e as { response?: { data?: { detail?: Detail } } })?.response?.data?.detail
}

export function errMsg(e: unknown): string {
  const d = detailOf(e)
  if (d && typeof d === 'object') return d.message ?? 'Action failed'
  return d ?? (e as { message?: string })?.message ?? 'Action failed'
}

/** Phase 15a: the server snaps a Site / Category onto the spelling already in
 * use and refuses a NEW one until the admin says so — `cncec` typed into a
 * free-text box once hid an item from every CNCEC store keeper. */
function unknownValue(e: unknown): { code: string; value?: string } | null {
  const d = detailOf(e)
  return d && typeof d === 'object' && (d.code === 'unknown_site' || d.code === 'unknown_category')
    ? { code: d.code, value: d.value } : null
}

export type ItemMode = 'create' | 'edit' | null

/**
 * The inventory item editor — one form for two doors (2026-09-30):
 *   · Admin → Inventory (`base='/admin/inventory'`), any site;
 *   · the Inventory page, for the admin AND the HOD (`base='/inventory-items'`).
 *     The server pins a HOD to their own site, so `lockedSite` shows it as a
 *     fact instead of a box they could only get a 403 from.
 */
export default function InventoryItemModal({ mode, target, onClose, base, lockedSite }: {
  mode: ItemMode
  target: Row | null
  onClose: () => void
  base: InventoryBase
  lockedSite?: string | null
}) {
  const { message, modal } = App.useApp()
  const [form] = Form.useForm()
  const { data: sites } = useSites()
  const { data: categories } = useCategories()
  const create = useCreateInventory(base)
  const update = useUpdateInventory(base)

  useEffect(() => {
    if (mode === 'create') {
      form.resetFields()
      if (lockedSite) form.setFieldValue('Site_ID', lockedSite)
    } else if (mode === 'edit' && target) {
      form.setFieldsValue({
        SAP_Code: target.SAP_Code, Equipment_Description: target.Equipment_Description,
        Material_Code: target.Material_Code, Category: target.Category, UOM: target.UOM,
        Minimum_Qty: target.Minimum_Qty, Unit_Cost: target.Unit_Cost,
        Opening_Stock: target.Opening_Stock, Site_ID: target.Site_ID, Expiry_Date: target.Expiry_Date,
        Shelf_Life_Months: target.Shelf_Life_Months ?? undefined,
        Lot_Tracked: target.Lot_Tracked == null ? 'auto' : target.Lot_Tracked ? 'on' : 'off',
      })
    }
  }, [mode, target, form, lockedSite])

  const close = () => { form.resetFields(); onClose() }

  const save = async (confirmNew: boolean) => {
    const raw = await form.validateFields()
    // Phase 16c: 'auto' (a Surface Shield is lot-tracked) is NULL on the server
    const { Lot_Tracked: lt, ...rest } = raw
    const v = { ...rest, ...(lt && lt !== 'auto' ? { Lot_Tracked: lt === 'on' } : {}) }
    if (mode === 'create') {
      await create.mutateAsync({ ...v, confirm_new: confirmNew })
      message.success(`Item ${v.SAP_Code} created`)
    } else if (mode === 'edit' && target) {
      const { SAP_Code: _omit, ...body } = v
      await update.mutateAsync({ sap: String(target.SAP_Code), body: { ...body, confirm_new: confirmNew } })
      message.success(`Item ${target.SAP_Code} updated`)
    }
    close()
  }

  const onOk = async () => {
    try {
      await save(false)
    } catch (e) {
      if ((e as { errorFields?: unknown }).errorFields) return
      const unk = unknownValue(e)
      if (!unk) { message.error(errMsg(e)); return }
      const what = unk.code === 'unknown_site' ? 'site' : 'category'
      modal.confirm({
        title: `Create a new ${what} “${unk.value ?? ''}”?`,
        content: `No item uses this ${what} yet. If you meant an existing one, press Cancel and pick it from the list.`,
        okText: `Yes, new ${what}`,
        onOk: async () => {
          try { await save(true) } catch (e2) { message.error(errMsg(e2)) }
        },
      })
    }
  }

  return (
    <Modal open={mode !== null} onOk={onOk} onCancel={close} forceRender
      title={mode === 'create' ? 'New inventory item' : `Edit ${target?.SAP_Code ?? ''}`}
      confirmLoading={create.isPending || update.isPending} okText="Save">
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item name="SAP_Code" label="SAP Code" rules={[{ required: true }]}>
          <Input disabled={mode === 'edit'} placeholder="e.g. 5001" />
        </Form.Item>
        <Form.Item name="Equipment_Description" label="Description">
          <Input placeholder="Material description" />
        </Form.Item>
        <Space style={{ display: 'flex' }} align="start">
          <Form.Item name="Material_Code" label="Material Code"><Input /></Form.Item>
          <Form.Item name="Category" label="Category">
            <AutoComplete placeholder="e.g. Consumables" style={{ width: 170 }} aria-label="Category"
              options={(categories ?? []).map((c) => ({ value: c }))}
              filterOption={(i, o) => String(o?.value ?? '').toLowerCase().includes(i.toLowerCase())} />
          </Form.Item>
          <Form.Item name="UOM" label="UoM"><Input placeholder="Each" /></Form.Item>
        </Space>
        <Space style={{ display: 'flex' }} align="start">
          <Form.Item name="Minimum_Qty" label="Minimum Qty"><InputNumber min={0} style={{ width: 130 }} /></Form.Item>
          <Form.Item name="Unit_Cost" label="Unit Cost"><InputNumber min={0} style={{ width: 130 }} /></Form.Item>
          <Form.Item name="Opening_Stock" label="Opening Stock"><InputNumber min={0} style={{ width: 130 }} /></Form.Item>
        </Space>
        <Space style={{ display: 'flex' }} align="start">
          {lockedSite ? (
            <>
              <Form.Item name="Site_ID" hidden><Input /></Form.Item>
              <Form.Item label="Site"><Tag color="blue">{lockedSite}</Tag></Form.Item>
            </>
          ) : (
            <Form.Item name="Site_ID" label="Site" rules={[{ required: mode === 'create', message: 'Pick the site' }]}>
              <AutoComplete placeholder="Site" style={{ width: 170 }} aria-label="Site"
                options={(sites ?? []).map((s) => ({ value: s }))}
                filterOption={(i, o) => String(o?.value ?? '').toLowerCase().includes(i.toLowerCase())} />
            </Form.Item>
          )}
          <Form.Item name="Expiry_Date" label="Expiry Date"><Input placeholder="YYYY-MM-DD" /></Form.Item>
        </Space>
        {/* Phase 16 — lots: who is lot-tracked, and how long a lot keeps */}
        <Space style={{ display: 'flex' }} align="start">
          <Form.Item name="Lot_Tracked" label="Lot tracking" initialValue="auto"
            tooltip="Automatic: every Surface Shield is tracked by lot (batch), except bricks.">
            <Select style={{ width: 170 }} aria-label="Lot tracking" options={[
              { value: 'auto', label: 'Automatic' }, { value: 'on', label: 'Tracked by lot' },
              { value: 'off', label: 'Not tracked' }]} />
          </Form.Item>
          <Form.Item name="Shelf_Life_Months" label="Shelf life (months)"
            tooltip="A lot with a manufacture date but no expiry gets MFD + this many months (shown as derived).">
            <InputNumber min={1} max={240} style={{ width: 150 }} aria-label="Shelf life" />
          </Form.Item>
        </Space>
      </Form>
    </Modal>
  )
}
