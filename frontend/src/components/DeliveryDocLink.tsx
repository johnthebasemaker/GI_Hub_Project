import { Button, Space, Tag, Tooltip } from 'antd'
import { DownloadOutlined, FileTextOutlined } from '@ant-design/icons'
import type { ColumnType } from 'antd/es/table'
import type { Row } from '../api/client'
import { openAuthed } from '../api/authedFile'

/**
 * The delivery document, shown wherever a delivery is shown.
 *
 * THE REQUIREMENT (2026-08-13): "ALL uploaded delivery/return documents (and
 * their Document Numbers) must be clearly visible and downloadable in all
 * relevant areas (SK, HOD, QC, Logistics, Warehouse) right next to the item
 * details."
 *
 * THE SHAPE, AND WHY IT IS ONE COMPONENT AND NOT FIVE COLUMNS: those five
 * portals read the same `delivery_notes` rows through five different
 * endpoints. Written out per page, the number renders in three of them, gets
 * forgotten in the fourth, and drifts in the fifth the next time somebody
 * touches a column list — which is the failure mode the requirement is
 * describing in the first place. The backend does the matching half with a
 * single `DN_DOC_COLUMNS` constant selected by every reader.
 *
 * The download deliberately goes through `/api/entry/attachments/{id}/download`
 * rather than a stored path: that route already enforces site scoping and role
 * access on the BLOB, so a link that leaked would leak to somebody who could
 * already open the page. Rendering a bare path here would have quietly created
 * a second, unguarded way to reach the file.
 *
 * A DN shipped before this existed has no document, and says so plainly rather
 * than rendering a dead link. That is most historical rows and it is not an
 * error — no backfill was attempted, because inventing a document number for a
 * delivery nobody scanned would be worse than admitting there isn't one.
 */
export function DeliveryDocLink({
  docNumber, attachmentId, emptyText = 'none',
}: {
  docNumber?: string | null
  attachmentId?: number | null
  emptyText?: string
}) {
  if (!docNumber && !attachmentId) {
    return <span style={{ opacity: 0.45 }}>{emptyText}</span>
  }
  return (
    <Space size={4}>
      {docNumber
        ? <Tag icon={<FileTextOutlined />} color="geekblue">{docNumber}</Tag>
        : <Tag color="default">no number</Tag>}
      {attachmentId
        ? (
          <Tooltip title="Open the signed document">
            <Button
              size="small" type="link" icon={<DownloadOutlined />}
              style={{ padding: '0 4px' }}
              aria-label="Open the signed document"
              onClick={() => void openAuthed(`/entry/attachments/${attachmentId}/download`, { inline: 1 })
                .catch(() => undefined)}
            />
          </Tooltip>
        )
        : <Tooltip title="No file was attached"><span style={{ opacity: 0.4 }}>—</span></Tooltip>}
    </Space>
  )
}

/** Drop-in antd column, so every DN grid shows the document identically. */
export const DN_DOC_COLUMN: ColumnType<Row> = {
  title: 'Delivery doc',
  key: '__dn_doc',
  width: 190,
  render: (_: unknown, r: Row) => (
    <DeliveryDocLink
      docNumber={r.dn_document_no as string | null}
      attachmentId={r.dn_attachment_id as number | null}
      emptyText="not shipped yet"
    />
  ),
}

/** The same, for a RETURN row (Track 4 — returns carry a DN too). */
export const RETURN_DOC_COLUMN: ColumnType<Row> = {
  title: 'Return doc',
  key: '__ret_doc',
  width: 190,
  render: (_: unknown, r: Row) => (
    <DeliveryDocLink
      docNumber={r.return_document_no as string | null}
      attachmentId={r.return_attachment_id as number | null}
    />
  ),
}
