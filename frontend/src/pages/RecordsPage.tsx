import { useCallback, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Button, Empty, Space, Typography } from 'antd'
import { EditOutlined, PaperClipOutlined, PlusOutlined } from '@ant-design/icons'
import BrowseTable from '../components/BrowseTable'
import InventoryItemModal from '../components/InventoryItemModal'
import type { ItemMode } from '../components/InventoryItemModal'
import { lockedSite } from '../components/SiteField'
import { READ_ENTITIES } from '../config/entities'
import { useAuth } from '../auth/AuthContext'
import { useReadOnly } from '../auth/useReadOnly'
import type { Row } from '../api/client'
import type { buildColumns } from '../lib/columns'

type Cols = ReturnType<typeof buildColumns>

// 2026-09-30: who may add / edit inventory items from THIS page. The admin
// always could (Admin → Inventory); the HOD now can too, for their own site —
// the server (`/inventory-items`) enforces both, this only shows the buttons.
const ITEM_EDITORS = new Set(['admin', 'hod'])

// Parity C5 — the ledger tabs link straight to their supporting documents.
const DOCS_LINK: Record<string, string> = {
  receipts: 'receipt', consumption: 'consumption', returns: 'return',
}

export default function RecordsPage() {
  const { key } = useParams<{ key: string }>()
  const { user } = useAuth()
  const entity = READ_ENTITIES.find((e) => e.key === key)
  const { readOnly } = useReadOnly()
  const [mode, setMode] = useState<ItemMode>(null)
  const [target, setTarget] = useState<Row | null>(null)
  const canEditItems = entity?.key === 'inventory' && !readOnly && ITEM_EDITORS.has(user?.role ?? '')
  const decorate = useCallback((cols: Cols): Cols => (!canEditItems ? cols : [
    ...cols,
    { title: '', key: '__edit', width: 70, fixed: 'right' as const,
      render: (_: unknown, r: Row) => (
        <Button size="small" icon={<EditOutlined />} aria-label={`Edit ${String(r.SAP_Code)}`}
          onClick={() => { setTarget(r); setMode('edit') }}>Edit</Button>) },
  ] as Cols), [canEditItems])

  if (!entity) return <Empty description={`Unknown record type: ${key}`} />

  const docType = DOCS_LINK[entity.key]
  const canSeeDocs = (user?.level ?? 0) >= 2 || user?.role === 'admin'

  return (
    <div>
      <Space style={{ width: '100%', justifyContent: 'space-between' }} align="center">
        <Typography.Title level={3} style={{ marginTop: 0 }}>
          {entity.label}
        </Typography.Title>
        {docType && canSeeDocs && (
          <Link to="/hod/documents">
            <Button icon={<PaperClipOutlined />}>Supporting documents</Button>
          </Link>
        )}
      </Space>
      <BrowseTable path={entity.path} hasSite={entity.hasSite} searchable
        hasCategory={entity.key === 'inventory'}
        decorateColumns={canEditItems ? decorate : undefined}
        toolbarExtra={canEditItems ? (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { setTarget(null); setMode('create') }}>
            New item
          </Button>) : undefined} />
      {canEditItems && (
        <InventoryItemModal mode={mode} target={target} base="/inventory-items"
          lockedSite={lockedSite(user)} onClose={() => { setMode(null); setTarget(null) }} />
      )}
    </div>
  )
}
