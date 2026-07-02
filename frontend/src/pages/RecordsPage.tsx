import { useParams } from 'react-router-dom'
import { Empty, Typography } from 'antd'
import BrowseTable from '../components/BrowseTable'
import { READ_ENTITIES } from '../config/entities'

export default function RecordsPage() {
  const { key } = useParams<{ key: string }>()
  const entity = READ_ENTITIES.find((e) => e.key === key)

  if (!entity) return <Empty description={`Unknown record type: ${key}`} />

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        {entity.label}
      </Typography.Title>
      <BrowseTable path={entity.path} hasSite={entity.hasSite} />
    </div>
  )
}
