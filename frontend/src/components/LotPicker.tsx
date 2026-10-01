import { Form, Input, Select, Tag, Typography } from 'antd'
import { useLotOptions } from '../api/lotHooks'
import type { LotOption } from '../api/lotHooks'

/** "in 85 d" · "expired 12 d ago" · "no expiry" */
export function daysLabel(o: Pick<LotOption, 'days_left' | 'status'>): string {
  if (o.days_left == null) return 'no expiry'
  if (o.days_left < 0) return `expired ${-o.days_left} d ago`
  return o.days_left === 0 ? 'expires today' : `${o.days_left} d left`
}

export function statusColor(status: string): string | undefined {
  if (status === 'expired') return 'red'
  if (status === 'expiring_30') return 'volcano'
  if (status === 'expiring_60') return 'orange'
  if (status === 'expiring_90') return 'gold'
  if (status === 'ok') return 'green'
  return undefined
}

/**
 * Phase 16c — the Issue form's Lot field.
 *
 * For a lot-tracked material it lists the OPEN lots in FEFO order, each with
 * its expiry, days left and remaining quantity; the first is marked FEFO and is
 * what a blank field posts (the server's own auto-pick uses the same order —
 * `ledger._FEFO_PICK`). An EXPIRED lot sorts last and is marked red: never the
 * suggestion, still pickable (FEFO is allow-and-log, never a block).
 *
 * For a ROLL item (CHEMOLINE) the store keeper picks the ROLL; its batch
 * follows into the Lot field (ruling Q16-3). For anything else the field stays
 * the plain optional box it always was.
 */
export default function LotPicker({ sap, site }: { sap?: string; site?: string }) {
  const form = Form.useFormInstance()
  const { data, isFetching } = useLotOptions(sap, site)
  const mode = data?.mode ?? null
  const items = data?.items ?? []
  const fefo = items.find((o) => o.fefo)

  if (!mode) {
    return (
      <Form.Item name="Lot_Number" label="Lot (optional)">
        <Input placeholder="blank → FEFO auto-pick" />
      </Form.Item>
    )
  }
  const lotOptions = items.map((o) => ({
    value: o.lot,
    label: (
      <span>
        <strong>{o.lot}</strong>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {' '}· {o.expiry ? `exp ${o.expiry}` : 'no expiry'}{o.expiry_source === 'derived' ? ' (derived)' : ''}
          {' '}· {daysLabel(o)} · {o.remaining} left
        </Typography.Text>
        {o.fefo && <Tag color="green" style={{ marginInlineStart: 6 }}>FEFO</Tag>}
        {o.status === 'expired' && <Tag color="red" style={{ marginInlineStart: 6 }}>expired</Tag>}
      </span>
    ),
  }))
  return (
    <>
      {mode === 'roll' && (
        <Form.Item name="Serial_No" label="Roll"
          extra="Pick the roll you are issuing; its batch fills in below.">
          <Select showSearch allowClear placeholder="Roll number" loading={isFetching}
            data-testid="roll-picker" optionFilterProp="label"
            onChange={(unit?: string) => {
              const u = data?.units.find((x) => x.unit === unit)
              form.setFieldValue('Lot_Number', u?.lot)
            }}
            options={(data?.units ?? []).map((u) => ({
              value: u.unit, label: `${u.unit} · batch ${u.lot}${u.location ? ` · ${u.location}` : ''}`,
            }))} />
        </Form.Item>
      )}
      <Form.Item name="Lot_Number" label={mode === 'roll' ? 'Batch' : 'Lot'}
        extra={fefo
          ? `Blank = FEFO: ${fefo.lot}${fefo.expiry ? ` (exp ${fefo.expiry})` : ''}`
          : items.length === 0 ? 'No lot of this material has stock at this site.' : undefined}>
        <Select allowClear showSearch placeholder={fefo ? `FEFO: ${fefo.lot}` : 'No open lot'}
          loading={isFetching} data-testid="lot-picker" options={lotOptions}
          optionFilterProp="value" />
      </Form.Item>
    </>
  )
}
