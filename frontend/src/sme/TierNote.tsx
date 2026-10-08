/**
 * frontend/src/sme/TierNote.tsx — the legend that makes the SME stock tiers
 * readable on every tab.
 *
 * Two locked rules meet here:
 *
 *  · 2026-08-03 STRICT TIER SEGREGATION — "ready to build" is a claim about
 *    drums on a shelf, so only ARRIVED stock counts toward readiness.
 *  · 2026-08-05 THE SUBSET RULE — in the source workbook `Ordered_Qty` is the
 *    TOTAL procured for the project and `Available_Qty` is the part of it that
 *    has already arrived. Available is a SUBSET of ordered, so the second tier
 *    is the PENDING DELIVERY (`ordered − available`), never the raw order.
 *    Showing "Available 143,000 / On Order 143,000" made a fully-delivered
 *    material look like it had 286,000, which is why the wording is now
 *    explicit about what has and has not landed.
 *
 * Green = on the shelf. Amber = still to arrive. Red = still to buy.
 */
import { Alert } from 'antd'
import { status } from '../theme/tokens'

const dot = (color: string): React.CSSProperties => ({
  display: 'inline-block', width: 8, height: 8, borderRadius: 4,
  background: color, marginRight: 5, verticalAlign: 'middle',
})

export default function TierNote({ style }: { style?: React.CSSProperties }) {
  return (
    <Alert
      type="info"
      showIcon
      style={{ fontSize: '0.75rem', ...style }}
      message="Arrived stock and pending deliveries are counted separately"
      description={(
        <span>
          <b><span style={dot(status.ok)} />Available / “now”</b> is what has
          ARRIVED and is on the shelf today — the only thing that can make a unit
          “Ready to Build”.{' '}
          <b><span style={dot(status.low)} />Pending delivery / “when delivered”</b> is
          the part of the purchase order that has <i>not</i> arrived yet
          (ordered − available). It is a forecast of what becomes buildable when
          it lands, never a readiness figure — and it is <i>not</i> extra stock on
          top of the order: available and pending together are the whole PO.{' '}
          <b><span style={dot(status.critical)} />To buy</b> is the gap that remains once
          the entire order has landed — the quantity to raise a PR against.
        </span>
      )}
    />
  )
}
