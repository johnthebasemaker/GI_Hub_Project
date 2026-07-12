import { useState } from 'react'
import type { ReactNode } from 'react'
import { Alert, Input, Select, Skeleton, Space, Table } from 'antd'
import { useCategories, useList, useSites } from '../api/hooks'
import type { ListParams } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'
import { buildColumns } from '../lib/columns'

interface Props {
  path: string
  hasSite?: boolean
  /** Free-text search box (server-side `q` across SAP code / name / etc.). */
  searchable?: boolean
  /** Category dropdown (server-side `category`, from the inventory master). */
  hasCategory?: boolean
  extraParams?: ListParams
  toolbarExtra?: ReactNode
}

// Generic read-only browser: server-side pagination + optional Site_ID filter,
// free-text search and category filter.
export default function BrowseTable({
  path, hasSite, searchable, hasCategory, extraParams, toolbarExtra,
}: Props) {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [siteId, setSiteId] = useState<string | undefined>(undefined)
  const [q, setQ] = useState('')
  const [category, setCategory] = useState<string | undefined>(undefined)
  const { data: sites } = useSites()
  const { data: categories } = useCategories(!!hasCategory)
  const { user } = useAuth()
  // Below logistics (level 3) the server pins reads to the user's own site,
  // so a site picker would be a no-op (or a 403) — hide it.
  const siteScoped = (user?.level ?? 0) < 3

  const params: ListParams = {
    limit: pageSize,
    offset: (page - 1) * pageSize,
    ...(siteId ? { site_id: siteId } : {}),
    ...(q.trim() ? { q: q.trim() } : {}),
    ...(category ? { category } : {}),
    ...extraParams,
  }
  const { data, isFetching, isError, error } = useList(path, params)
  const rows = data?.items ?? []

  const hasToolbar = (hasSite && !siteScoped) || searchable || hasCategory || toolbarExtra

  return (
    <div>
      {hasToolbar && (
        <Space style={{ marginBottom: 12 }} wrap>
          {searchable && (
            <Input.Search
              allowClear
              placeholder="Search SAP code / name…"
              style={{ width: 240 }}
              onSearch={(v) => { setQ(v); setPage(1) }}
              onChange={(e) => { if (!e.target.value) { setQ(''); setPage(1) } }}
            />
          )}
          {hasCategory && (
            <Select
              allowClear
              showSearch
              placeholder="All categories"
              style={{ width: 190 }}
              value={category}
              onChange={(v) => { setCategory(v); setPage(1) }}
              options={(categories ?? []).map((c) => ({ value: c, label: c }))}
            />
          )}
          {hasSite && !siteScoped && (
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
          title={(error as Error).message}
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
