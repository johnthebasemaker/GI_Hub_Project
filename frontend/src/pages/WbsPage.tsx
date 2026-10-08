/**
 * WbsPage — WBS numbers, and the work types that charge to them.
 *
 * THE PROBLEM THIS SCREEN EXISTS TO FIX. `wbs_master`, the `assert_wbs` gate
 * and three HOD endpoints to manage them have shipped since the parity build,
 * and nothing in the frontend ever called them. With zero rows the gate is a
 * permanent no-op, so no entry was ever asked for a WBS and all 1,674 live
 * consumption rows carry none. The plumbing was complete and the tap was never
 * opened. This is the tap.
 *
 * ⚠️ BOTH RULES ARE CONDITIONAL, AND THE PAGE SAYS SO OUT LOUD. An empty list
 * means the entry forms keep their free-text input and nothing is refused —
 * turning the rule on is the HOD's act, taken here, not something a release
 * does to them overnight. That is also why the banners lead with what is
 * currently enforced rather than with instructions.
 *
 * ⚠️ THE MAPPING IS A DEFAULT, NOT A CORRECTION. `wbs.resolve_wbs` fills in a
 * WBS only when the entry did not name one; a store keeper who picked one
 * picked it for a reason this table does not know. The page states that where
 * an HOD would otherwise assume the map overrides the form.
 */
import { useMemo, useState } from 'react'
import {
  Alert, App, Button, Card, Form, Input, Modal, Popconfirm, Select, Space,
  Table, Tabs, Tag, Tooltip, Typography,
} from 'antd'
import {
  DeleteOutlined, ImportOutlined, PlusOutlined, TagsOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import {
  useAddWbs, useAddWorkType, useDeleteWorkType, usePatchWorkType,
  useSetWbsStatus, useWbsRows, useWorkTypeSuggestions, useWorkTypes,
} from '../api/hooks'
import type { Row } from '../api/client'
import type { WorkTypeSuggestion } from '../api/hooks'
import PreparersCard from '../components/PreparersCard'
import { useAuth } from '../auth/AuthContext'
import { useReadOnly } from '../auth/useReadOnly'

function errMsg(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail ?? x?.message ?? 'Action failed'
}

export default function WbsPage() {
  const { message } = App.useApp()
  const { user } = useAuth()
  const { readOnly } = useReadOnly()
  const site = user?.site_id || undefined

  const { data: wbsRows, isLoading: wbsLoading } = useWbsRows(site)
  const { data: workTypes, isLoading: wtLoading } = useWorkTypes(site)
  const [showSuggest, setShowSuggest] = useState(false)
  const { data: suggestions, isFetching: sugLoading } =
    useWorkTypeSuggestions(site, showSuggest)

  const addWbs = useAddWbs()
  const setWbsStatus = useSetWbsStatus()
  const addWt = useAddWorkType()
  const patchWt = usePatchWorkType()
  const delWt = useDeleteWorkType()

  const [wbsOpen, setWbsOpen] = useState(false)
  const [wtOpen, setWtOpen] = useState(false)
  const [wbsForm] = Form.useForm()
  const [wtForm] = Form.useForm()

  // Only ACTIVE WBS numbers may be mapped — a closed one would resolve onto
  // every issue of that work type and the report would still balance, which is
  // exactly what makes a wrong cost centre hard to notice.
  const activeWbs = useMemo(
    () => (wbsRows ?? []).filter((r) => r.status === 'active')
      .map((r) => String(r.WBS_Number)),
    [wbsRows],
  )
  const activeTypes = (workTypes ?? []).filter((r) => r.status === 'active')
  const unmapped = activeTypes.filter((r) => !r.WBS_Number)

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    try { await fn(); message.success(ok) } catch (e) { message.error(errMsg(e)) }
  }

  // ── WBS numbers ─────────────────────────────────────────────────────────
  const wbsCols: ColumnsType<Row> = [
    { title: 'WBS Number', dataIndex: 'WBS_Number', width: 180 },
    { title: 'Description', dataIndex: 'Description', render: (v) => v || '—' },
    {
      title: 'Status', dataIndex: 'status', width: 110,
      render: (v: string) => (
        <Tag color={v === 'active' ? 'green' : 'default'}>{v}</Tag>
      ),
    },
    {
      title: 'Action', width: 130, align: 'right',
      render: (_, r) => {
        const closing = r.status === 'active'
        const used = (workTypes ?? []).filter(
          (w) => w.WBS_Number === r.WBS_Number && w.status === 'active').length
        return (
          <Popconfirm
            title={closing ? 'Close this WBS number?' : 'Reopen this WBS number?'}
            description={closing && used > 0
              ? `${used} work type(s) still map to it. Closing leaves those `
                + 'mappings in place but they will stop resolving.'
              : undefined}
            onConfirm={() => run(
              () => setWbsStatus.mutateAsync({
                id: Number(r.id), status: closing ? 'closed' : 'active',
              }), closing ? 'WBS closed' : 'WBS reopened')}
            disabled={readOnly}
          >
            <Button size="small" disabled={readOnly}>
              {closing ? 'Close' : 'Reopen'}
            </Button>
          </Popconfirm>
        )
      },
    },
  ]

  // ── Work types ──────────────────────────────────────────────────────────
  const wtCols: ColumnsType<Row> = [
    { title: 'Work Type', dataIndex: 'Work_Type', width: 200 },
    {
      title: 'WBS Number', dataIndex: 'WBS_Number', width: 220,
      render: (v: string | null, r) => (
        <Select
          size="small" style={{ width: 190 }} value={v ?? undefined}
          placeholder="— not mapped —" allowClear showSearch
          disabled={readOnly || r.status !== 'active'}
          options={activeWbs.map((w) => ({ label: w, value: w }))}
          onChange={(val) => run(
            () => patchWt.mutateAsync({ id: Number(r.id), WBS_Number: val ?? '' }),
            val ? `${r.Work_Type} → ${val}` : 'Mapping cleared')}
        />
      ),
    },
    { title: 'Description', dataIndex: 'Description', render: (v) => v || '—' },
    {
      title: 'Status', dataIndex: 'status', width: 150,
      render: (v: string, r) => (
        <Space size={4}>
          <Tag color={v === 'active' ? 'green' : 'default'}>{v}</Tag>
          <Button
            size="small" type="link" disabled={readOnly}
            onClick={() => run(
              () => patchWt.mutateAsync({
                id: Number(r.id), status: v === 'active' ? 'retired' : 'active',
              }), v === 'active' ? 'Retired' : 'Reactivated')}
          >
            {v === 'active' ? 'Retire' : 'Reactivate'}
          </Button>
        </Space>
      ),
    },
    {
      title: '', width: 50, align: 'right',
      render: (_, r) => (
        <Popconfirm
          title="Remove this work type?"
          description={'Entries already posted keep the work type they were '
            + 'posted with — removing the option cannot un-happen them. '
            + 'Retire it instead if it was ever used.'}
          onConfirm={() => run(() => delWt.mutateAsync(Number(r.id)), 'Removed')}
          disabled={readOnly}
        >
          <Button size="small" type="text" danger icon={<DeleteOutlined />}
            disabled={readOnly} />
        </Popconfirm>
      ),
    },
  ]

  const sugCols: ColumnsType<WorkTypeSuggestion> = [
    { title: 'Work Type', dataIndex: 'Work_Type', width: 200 },
    { title: 'Entries', dataIndex: 'count', width: 90, align: 'right' },
    {
      title: 'Spelled in the ledger as', dataIndex: 'variants',
      render: (v: string[]) => (v.length > 1
        ? (
          <Space size={4} wrap>
            {v.map((x) => <Tag key={x} color="orange">{x}</Tag>)}
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              — adopting this merges {v.length} spellings into one
            </Typography.Text>
          </Space>
        )
        : <Typography.Text type="secondary">{v[0]}</Typography.Text>),
    },
    {
      title: '', width: 90, align: 'right',
      render: (_, r) => (
        <Button
          size="small" type="primary" ghost disabled={readOnly}
          onClick={() => run(
            () => addWt.mutateAsync({ Work_Type: r.Work_Type, site_id: site }),
            `${r.Work_Type} added`)}
        >
          Add
        </Button>
      ),
    },
  ]

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        🏷️ WBS &amp; Work Types
      </Typography.Title>

      <Alert
        type={activeWbs.length ? 'info' : 'warning'}
        showIcon
        message={activeWbs.length
          ? `${activeWbs.length} active WBS number(s) at ${site ?? 'this site'} — `
            + 'entry forms now require one'
          : 'No WBS numbers yet — entry forms are not asking for one'}
        description={activeWbs.length
          ? 'Both rules are conditional and are now ON for this site. Issues and '
            + 'receipts must carry an active WBS, and a work type that maps to one '
            + 'fills it in automatically when the form leaves it blank.'
          : 'Nothing is enforced until you add the first WBS number. Once you do, '
            + 'Issue and Receive will require one on every entry at this site.'}
      />

      <Tabs
        items={[
          {
            key: 'types',
            label: <span><TagsOutlined /> Work Types</span>,
            children: (
              <Card
                title={`Work types at ${site ?? '—'}`}
                extra={(
                  <Space>
                    <Button
                      icon={<ImportOutlined />}
                      onClick={() => setShowSuggest((v) => !v)}
                    >
                      {showSuggest ? 'Hide history' : 'Import from history'}
                    </Button>
                    <Button
                      type="primary" icon={<PlusOutlined />} disabled={readOnly}
                      onClick={() => { wtForm.resetFields(); setWtOpen(true) }}
                    >
                      Add work type
                    </Button>
                  </Space>
                )}
              >
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  {activeTypes.length === 0 && (
                    <Alert
                      type="info" showIcon
                      message="The work-type list is empty, so the entry forms keep their free-text box"
                      description={'Add the first work type and the Issue form becomes a '
                        + 'strict dropdown at this site. Import from history proposes the '
                        + 'ones your ledger has actually used, already merged where they '
                        + 'differ only in spelling.'}
                    />
                  )}
                  {unmapped.length > 0 && (
                    <Alert
                      type="warning" showIcon
                      message={`${unmapped.length} active work type(s) have no WBS number`}
                      description={'Entries using them will resolve to no WBS unless the '
                        + 'store keeper picks one on the form. That is legal — it is just '
                        + 'not automatic.'}
                    />
                  )}
                  {showSuggest && (
                    <Card size="small" title="Work types seen in this site's ledger">
                      <Table<WorkTypeSuggestion>
                        rowKey="Work_Type_Norm" size="small" loading={sugLoading}
                        dataSource={suggestions ?? []} columns={sugCols}
                        pagination={{ pageSize: 10, hideOnSinglePage: true }}
                        locale={{ emptyText: 'Nothing left to import — every work type in the ledger is already on the list.' }}
                      />
                    </Card>
                  )}
                  <Table<Row>
                    rowKey="id" size="small" loading={wtLoading}
                    dataSource={workTypes ?? []} columns={wtCols}
                    pagination={{ pageSize: 20, hideOnSinglePage: true }}
                  />
                </Space>
              </Card>
            ),
          },
          {
            key: 'numbers',
            label: 'WBS Numbers',
            children: (
              <Card
                title={`WBS numbers at ${site ?? '—'}`}
                extra={(
                  <Button
                    type="primary" icon={<PlusOutlined />} disabled={readOnly}
                    onClick={() => { wbsForm.resetFields(); setWbsOpen(true) }}
                  >
                    Add WBS number
                  </Button>
                )}
              >
                <Table<Row>
                  rowKey="id" size="small" loading={wbsLoading}
                  dataSource={wbsRows ?? []} columns={wbsCols}
                  pagination={{ pageSize: 20, hideOnSinglePage: true }}
                  locale={{ emptyText: 'No WBS numbers yet. Adding one turns the requirement on for this site.' }}
                />
              </Card>
            ),
          },
        ]}
      />

      <Modal
        title="Add a WBS number" open={wbsOpen} onCancel={() => setWbsOpen(false)}
        okText="Add" confirmLoading={addWbs.isPending}
        onOk={async () => {
          const v = await wbsForm.validateFields()
          await run(async () => {
            await addWbs.mutateAsync({ ...v, site_id: site })
            setWbsOpen(false)
          }, `WBS ${v.WBS_Number} added`)
        }}
      >
        <Form form={wbsForm} layout="vertical">
          <Form.Item
            name="WBS_Number" label="WBS Number"
            rules={[{ required: true, message: 'A WBS number is required' }]}
          >
            <Input placeholder="e.g. P-4711-02-030" />
          </Form.Item>
          <Form.Item name="Description" label="Description">
            <Input placeholder="What this cost centre covers" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="Add a work type" open={wtOpen} onCancel={() => setWtOpen(false)}
        okText="Add" confirmLoading={addWt.isPending}
        onOk={async () => {
          const v = await wtForm.validateFields()
          await run(async () => {
            await addWt.mutateAsync({ ...v, site_id: site })
            setWtOpen(false)
          }, `${v.Work_Type} added`)
        }}
      >
        <Form form={wtForm} layout="vertical">
          <Form.Item
            name="Work_Type" label="Work Type"
            rules={[{ required: true, message: 'A name is required' }]}
            extra="The spelling you choose here is what gets stored on every entry."
          >
            <Input placeholder="e.g. Blasting" />
          </Form.Item>
          <Form.Item
            name="WBS_Number" label="Charges to WBS"
            extra={activeWbs.length
              ? 'Optional. A work type with no WBS is legal — it just will not fill one in.'
              : 'Add a WBS number first to be able to map one.'}
          >
            <Tooltip title={activeWbs.length ? '' : 'No active WBS numbers at this site yet'}>
              <Select
                allowClear showSearch placeholder="— not mapped —"
                disabled={!activeWbs.length}
                options={activeWbs.map((w) => ({ label: w, value: w }))}
              />
            </Tooltip>
          </Form.Item>
          <Form.Item name="Description" label="Description">
            <Input placeholder="Optional note" />
          </Form.Item>
        </Form>
      </Modal>
      {/* Phase 22e — the site's Day / Night preparers (ruling Q22-16) */}
      {site && <PreparersCard fixedSite={site} />}
    </Space>
  )
}
