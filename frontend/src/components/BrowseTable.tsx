import { useState } from 'react'
import type { ReactNode } from 'react'
import { Alert, Select, Skeleton, Space, Table } from 'antd'
import { useList, useSites } from '../api/hooks'
import type { ListParams } from '../api/hooks'
import { buildColumns } from '../lib/columns'

interface Props {
  path: string
  hasSite?: boolean
  extraParams?: ListParams
  toolbarExtra?: ReactNode
}

// Generic read-only browser: server-side pagination + optional Site_ID filter.
export default function BrowseTable({ path, hasSite, extraParams, toolbarExtra }: Props) {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [siteId, setSiteId] = useState<string | undefined>(undefined)
  const { data: sites } = useSites()

  const params: ListParams = {
    limit: pageSize,
    offset: (page - 1) * pageSize,
    ...(siteId ? { site_id: siteId } : {}),
    ...extraParams,
  }
  const { data, isFetching, isError, error } = useList(path, params)
  const rows = data?.items ?? []

  return (
    <div>
      {(hasSite || toolbarExtra) && (
        <Space style={{ marginBottom: 12 }} wrap>
          {hasSite && (
            <Select
              allowClear
              placeholder="All sites"
              style={{ width: 160 }}
              value={siteId}
              onChange={(v) => {
                setSiteId(v)
                setPage(1)
              }}
              options={(sites ?? []).map((s) => ({ value: s, label: s }))}
            />
          )}
          {toolbarExtra}
        </Space>
      )}
      {isError && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message={(error as Error).message}
        />
      )}
      {/* First load = shimmer skeleton; refetches keep the spinner overlay */}
      {isFetching && !data ? (
        <Skeleton active title={false} paragraph={{ rows: 8 }} />
      ) : (
      <Table
        size="small"
        loading={isFetching}
        columns={buildColumns(rows)}
        dataSource={rows.map((r, i) => ({ ...r, __rk: i }))}
        rowKey="__rk"
        scroll={{ x: 'max-content' }}
        sticky={{ offsetHeader: 64 }}
        pagination={{
          current: page,
          pageSize,
          total: data?.total ?? 0,
          showSizeChanger: true,
          showTotal: (t) => `${t} rows`,
          onChange: (p, ps) => {
            setPage(p)
            setPageSize(ps)
          },
        }}
      />
      )}
    </div>
  )
}
