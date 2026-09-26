import { useCallback, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Alert, Input, Select, Skeleton, Space } from 'antd'
import { Table } from '../lib/smartTable'
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
  /** Adjust the generated columns (e.g. a badge in the SAP cell). Keep it
   * stable (module-level or memoised) — it is part of the columns memo. */
  decorateColumns?: (cols: ReturnType<typeof buildColumns>) => ReturnType<typeof buildColumns>
  /** A class per row (e.g. highlight rows that need attention). */
  rowClassName?: (row: Record<string, unknown>) => string
}

/** Hoisted so it is not a fresh object on every render. */
const SCROLL_X = { x: 'max-content' as const }

// Generic read-only browser: server-side pagination + optional Site_ID filter,
// free-text search and category filter.
export default function BrowseTable({
  path, hasSite, searchable, hasCategory, extraParams, toolbarExtra,
  decorateColumns, rowClassName,
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
  // `params` is rebuilt every render on purpose — TanStack hashes a query key
  // STRUCTURALLY, so a fresh object with identical contents is the same key.
  const { data, isFetching, isError, error } = useList(path, params)
  const rows = useMemo(() => data?.items ?? [], [data])

  // ── memoisation: everything below is handed to antd's Table ──────────────
  //
  // ⚠️ THE ROW CLONE WAS THE EXPENSIVE ONE. `dataSource` was
  // `rows.map((r, i) => ({ ...r, __rk: i }))` written inline, so EVERY render
  // produced a new array of newly-allocated row objects. antd compares rows by
  // identity to decide what to repaint, so nothing ever matched and a full
  // page of cells re-rendered on every keystroke in the search box, every
  // category change, and every parent re-render — none of which had touched
  // the data. The clone is now tied to the fetched rows, so it happens when
  // the ROWS change and not when the COMPONENT renders.
  const dataSource = useMemo(
    () => rows.map((r, i) => ({ ...r, __rk: i })), [rows])

  // buildColumns() walks the first row and allocates a fresh `render` closure
  // per column. New closures mean new column identities, which is the second
  // reason every cell repainted.
  const columns = useMemo(() => {
    const cols = buildColumns(rows)
    return decorateColumns ? decorateColumns(cols) : cols
  }, [rows, decorateColumns])

  const onPageChange = useCallback((p: number, ps: number) => {
    setPage(p)
    setPageSize(ps)
  }, [])

  const pagination = useMemo(() => ({
    current: page,
    pageSize,
    total: data?.total ?? 0,
    showSizeChanger: true,
    showTotal: (t: number) => `${t} rows`,
    onChange: onPageChange,
  }), [page, pageSize, data?.total, onPageChange])

  // `sticky` was `{ offsetHeader: 64 }` inline: a new object each render, and
  // a hard-coded 64 that smartTable already replaces with the measured header
  // height (the hard-coded value sat 8px low on a phone and opened a gap rows
  // showed through). Passing `true` asks for the measured offset explicitly
  // rather than supplying a number that is immediately overwritten.
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
        columns={columns}
        dataSource={dataSource}
        rowKey="__rk"
        rowClassName={rowClassName}
        scroll={SCROLL_X}
        sticky
        pagination={pagination}
      />
      )}
    </div>
  )
}
