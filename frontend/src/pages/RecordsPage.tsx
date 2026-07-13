import { Link, useParams } from 'react-router-dom'
import { Button, Empty, Space, Typography } from 'antd'
import { PaperClipOutlined } from '@ant-design/icons'
import BrowseTable from '../components/BrowseTable'
import { READ_ENTITIES } from '../config/entities'
import { useAuth } from '../auth/AuthContext'

// Parity C5 — the ledger tabs link straight to their supporting documents.
const DOCS_LINK: Record<string, string> = {
  receipts: 'receipt', consumption: 'consumption', returns: 'return',
}

export default function RecordsPage() {
  const { key } = useParams<{ key: string }>()
  const { user } = useAuth()
  const entity = READ_ENTITIES.find((e) => e.key === key)

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
        hasCategory={entity.key === 'inventory'} />
    </div>
  )
}
