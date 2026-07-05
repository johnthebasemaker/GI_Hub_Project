import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api, fetchList } from './client'
import type { Health, InventorySummary, ListResponse, Row } from './client'

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: async () => (await api.get<Health>('/health')).data,
  })
}

export function useSites() {
  return useQuery({
    queryKey: ['sites'],
    queryFn: async () => (await api.get<{ sites: string[] }>('/meta/sites')).data.sites,
  })
}

export function useInventorySummary() {
  return useQuery({
    queryKey: ['inventory-summary'],
    queryFn: async () =>
      (await api.get<InventorySummary>('/meta/inventory-summary')).data,
  })
}

export interface ListParams {
  limit?: number
  offset?: number
  site_id?: string
  within_days?: number
}

export function useList(path: string, params: ListParams) {
  return useQuery<ListResponse>({
    queryKey: [path, params],
    queryFn: () => fetchList(path, params as Record<string, unknown>),
    placeholderData: (prev) => prev,
  })
}

export function useCreate(path: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Row) => api.post(path, body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: [path] }),
  })
}

export function useUpdate(path: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string | number; body: Row }) =>
      api.put(`${path}/${id}`, body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: [path] }),
  })
}

export function useDelete(path: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string | number) => api.delete(`${path}/${id}`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: [path] }),
  })
}

// Ledger data entry: post a write, then refresh the affected read views.
function invalidateLedger(qc: ReturnType<typeof useQueryClient>, extra: string[] = []) {
  for (const k of ['/stock/live', '/stock/by-site', '/stock/lots', '/stock/expiring', ...extra]) {
    qc.invalidateQueries({ queryKey: [k] })
  }
}

function useLedgerPost(path: string, extra: string[]) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Row) => api.post(path, body).then((r) => r.data),
    onSuccess: () => invalidateLedger(qc, extra),
  })
}

export const useReceiptEntry = () => useLedgerPost('/entry/receipts', ['/receipts'])
export const useConsumptionEntry = () => useLedgerPost('/entry/consumption', ['/consumption'])
export const useReturnEntry = () => useLedgerPost('/entry/returns', ['/returns'])
export const useAdjustmentEntry = () =>
  useLedgerPost('/entry/adjustments', ['/receipts', '/consumption'])

export function useAdjustmentReasons() {
  return useQuery({
    queryKey: ['adjustment-reasons'],
    queryFn: async () =>
      (await api.get<Record<string, string>>('/entry/adjustment-reasons')).data,
  })
}

// --- HOD approvals ----------------------------------------------------------
export function useHodCounts(siteId?: string) {
  return useQuery({
    queryKey: ['/hod/pending', siteId],
    queryFn: async () =>
      (await api.get<Record<string, number>>('/hod/pending', { params: siteId ? { site_id: siteId } : {} })).data,
  })
}

export function useHodPending(kind: string, siteId?: string) {
  return useQuery({
    queryKey: [`/hod/pending/${kind}`, siteId],
    queryFn: async () =>
      (await api.get<{ items: Row[] }>(`/hod/pending/${kind}`, { params: siteId ? { site_id: siteId } : {} })).data.items,
  })
}

export function useHodDecision() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ kind, id, action, reason }:
      { kind: string; id: number; action: 'approve' | 'reject'; reason?: string }) =>
      api.post(`/hod/pending/${kind}/${id}/${action}`, action === 'reject' ? { reason } : {}).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['/hod/pending'] })
      invalidateLedger(qc, ['/receipts', '/consumption', '/returns'])
    },
  })
}

export function useBurnRate(siteId: string | undefined, days: number) {
  return useQuery({
    queryKey: ['/hod/burn-rate', siteId, days],
    queryFn: async () =>
      (await api.get('/hod/burn-rate', { params: { ...(siteId ? { site_id: siteId } : {}), days } })).data,
  })
}

// --- procurement (PR → PO → assign) -----------------------------------------
export function useHodPrs(siteId?: string) {
  return useQuery({
    queryKey: ['/hod/prs', siteId],
    queryFn: async () =>
      (await api.get<{ items: Row[] }>('/hod/prs', { params: siteId ? { site_id: siteId } : {} })).data.items,
  })
}

export function useCreatePr() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Row) => api.post('/hod/prs', body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['/hod/prs'] }),
  })
}

export function useSubmitPr() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ pr, site }: { pr: string; site: string }) =>
      api.post(`/hod/prs/${pr}/submit`, null, { params: { site_id: site } }).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['/hod/prs'] })
      qc.invalidateQueries({ queryKey: ['/logistics/prs'] })
    },
  })
}

export function useLogisticsPrs(siteId?: string) {
  return useQuery({
    queryKey: ['/logistics/prs', siteId],
    queryFn: async () =>
      (await api.get<{ items: Row[] }>('/logistics/prs', { params: siteId ? { site_id: siteId } : {} })).data.items,
  })
}

export function useLogisticsPos(status?: string) {
  return useQuery({
    queryKey: ['/logistics/pos', status],
    queryFn: async () =>
      (await api.get<{ items: Row[] }>('/logistics/pos', { params: status ? { status } : {} })).data.items,
  })
}

export function usePoItems(po: string | null) {
  return useQuery({
    queryKey: ['/logistics/pos', po, 'items'],
    enabled: !!po,
    queryFn: async () =>
      (await api.get<{ items: Row[] }>(`/logistics/pos/${po}/items`)).data.items,
  })
}

export function useCreatePo() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Row) => api.post('/logistics/pos', body).then((r) => r.data),
    onSuccess: () => {
      for (const k of ['/logistics/prs', '/logistics/pos', '/hod/prs']) {
        qc.invalidateQueries({ queryKey: [k] })
      }
    },
  })
}

export function useAssignPo() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ po, body }: { po: string; body: Row }) =>
      api.post(`/logistics/pos/${po}/assign`, body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['/logistics/pos'] }),
  })
}

// --- warehouse (assignment → receive → DN → outbound) -----------------------
export function useWhAssignments(warehouseId?: string) {
  return useQuery({
    queryKey: ['/warehouse/assignments', warehouseId],
    enabled: !!warehouseId,
    queryFn: async () =>
      (await api.get<{ items: Row[] }>('/warehouse/assignments', { params: { warehouse_id: warehouseId } })).data.items,
  })
}

export function useWhAssignmentItems(assignmentId: number | null) {
  return useQuery({
    queryKey: ['/warehouse/assignments', assignmentId, 'items'],
    enabled: !!assignmentId,
    queryFn: async () =>
      (await api.get<{ items: Row[] }>(`/warehouse/assignments/${assignmentId}/items`)).data.items,
  })
}

function useWhMutation<V>(fn: (v: V) => Promise<Row>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['/warehouse/assignments'] })
      qc.invalidateQueries({ queryKey: ['/warehouse/dns'] })
      qc.invalidateQueries({ queryKey: ['/logistics/pos'] })
    },
  })
}

export const useWhAck = () =>
  useWhMutation((id: number) => api.post(`/warehouse/assignments/${id}/acknowledge`).then((r) => r.data as Row))
export const useWhReceive = () =>
  useWhMutation(({ id, received }: { id: number; received: Record<string, number> }) =>
    api.post(`/warehouse/assignments/${id}/receive`, { received }).then((r) => r.data as Row))
export const useCreateDn = () =>
  useWhMutation((body: Row) => api.post('/warehouse/dns', body).then((r) => r.data as Row))
export const useShipDn = () =>
  useWhMutation((dn: string) => api.post(`/warehouse/dns/${dn}/ship`).then((r) => r.data as Row))

export function useWhDns(warehouseId?: string, status?: string) {
  return useQuery({
    queryKey: ['/warehouse/dns', warehouseId, status],
    queryFn: async () =>
      (await api.get<{ items: Row[] }>('/warehouse/dns', {
        params: { ...(warehouseId ? { warehouse_id: warehouseId } : {}), ...(status ? { status } : {}) },
      })).data.items,
  })
}

export function useDnItems(dn: string | null) {
  return useQuery({
    queryKey: ['/warehouse/dns', dn, 'items'],
    enabled: !!dn,
    queryFn: async () =>
      (await api.get<{ items: Row[] }>(`/warehouse/dns/${dn}/items`)).data.items,
  })
}

// --- site receiving (incoming DNs → stage receipts; closes the loop) ---------
export function useIncomingDns(siteId?: string) {
  return useQuery({
    queryKey: ['/site/incoming-dns', siteId],
    queryFn: async () =>
      (await api.get<{ items: Row[] }>('/site/incoming-dns', { params: siteId ? { site_id: siteId } : {} })).data.items,
  })
}

export function useSiteDnItems(dn: string | null) {
  return useQuery({
    queryKey: ['/site/incoming-dns', dn, 'items'],
    enabled: !!dn,
    queryFn: async () =>
      (await api.get<{ items: Row[] }>(`/site/incoming-dns/${dn}/items`)).data.items,
  })
}

export function useReceiveDn() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (dn: string) => api.post(`/site/dns/${dn}/receive`).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['/site/incoming-dns'] })
      qc.invalidateQueries({ queryKey: ['/hod/pending'] })
    },
  })
}

// --- supervisor material requests (SMR) -------------------------------------
export function useSmrList(params: { mine?: boolean; site_id?: string; status?: string }) {
  return useQuery({
    queryKey: ['/requests', params],
    queryFn: async () => (await api.get<{ items: Row[] }>('/requests', { params })).data.items,
  })
}

export function useSmrItems(id: number | null) {
  return useQuery({
    queryKey: ['/requests', id, 'items'],
    enabled: !!id,
    queryFn: async () => (await api.get<{ items: Row[] }>(`/requests/${id}/items`)).data.items,
  })
}

export function useCreateSmr() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Row) => api.post('/requests', body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['/requests'] }),
  })
}

export function useSmrDecision() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, action, reason, adjustments }:
      { id: number; action: 'approve' | 'reject'; reason?: string; adjustments?: Record<string, number> }) =>
      api.post(`/requests/${id}/${action}`,
        action === 'reject' ? { reason } : (adjustments ? { adjustments } : {})).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['/requests'] })
      qc.invalidateQueries({ queryKey: ['/hod/pending'] })
    },
  })
}

// --- SME estimator (read-only) ----------------------------------------------
export function useSmeSummary(siteId?: string) {
  return useQuery({
    queryKey: ['/sme/summary', siteId],
    queryFn: async () => (await api.get('/sme/summary', { params: siteId ? { site_id: siteId } : {} })).data,
  })
}

function useSmeList(path: string, params: Record<string, unknown> = {}) {
  return useQuery({
    queryKey: [path, params],
    queryFn: async () => (await api.get<{ items: Row[] }>(path, { params })).data.items,
  })
}

export const useSmeEquipment = (siteId?: string) =>
  useSmeList('/sme/equipment', siteId ? { site_id: siteId } : {})
export const useSmeRecipes = (lsc?: string) =>
  useSmeList('/sme/recipes', lsc ? { lining_system_code: lsc } : {})
export const useSmeSqm = (siteId?: string) =>
  useSmeList('/sme/sqm-progress', siteId ? { site_id: siteId } : {})
export const useSmeMaterials = () => useSmeList('/sme/materials')

// --- admin console (users + audit log) --------------------------------------
export function useAdminUsers() {
  return useQuery({
    queryKey: ['/admin/users'],
    queryFn: async () => (await api.get<{ items: Row[] }>('/admin/users')).data.items,
  })
}

export function useAdminRoles() {
  return useQuery({
    queryKey: ['/admin/roles'],
    queryFn: async () => (await api.get<{ roles: Row[] }>('/admin/roles')).data.roles,
  })
}

function useUserMutation<V>(fn: (v: V) => Promise<Row>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['/admin/users'] }),
  })
}

export const useCreateUser = () =>
  useUserMutation((body: Row) => api.post('/admin/users', body).then((r) => r.data))
export const useUpdateUser = () =>
  useUserMutation(({ username, body }: { username: string; body: Row }) =>
    api.patch(`/admin/users/${encodeURIComponent(username)}`, body).then((r) => r.data))
export const useResetPassword = () =>
  useUserMutation(({ username, password }: { username: string; password: string }) =>
    api.post(`/admin/users/${encodeURIComponent(username)}/reset-password`, { password }).then((r) => r.data))
export const useResetUser2fa = () =>
  useUserMutation((username: string) =>
    api.post(`/admin/users/${encodeURIComponent(username)}/reset-2fa`).then((r) => r.data))
export const useDeleteUser = () =>
  useUserMutation((username: string) =>
    api.delete(`/admin/users/${encodeURIComponent(username)}`).then((r) => r.data))

export interface AuditParams {
  username?: string
  action_type?: string
  target_table?: string
  q?: string
  limit?: number
  offset?: number
}

export function useAuditLog(params: AuditParams) {
  return useQuery({
    queryKey: ['/admin/audit', params],
    queryFn: async () =>
      (await api.get<{ total: number; items: Row[] }>('/admin/audit', { params })).data,
    placeholderData: (prev) => prev,
  })
}

export function useAuditMeta() {
  return useQuery({
    queryKey: ['/admin/audit/meta'],
    queryFn: async () =>
      (await api.get<{ action_types: string[]; target_tables: string[] }>('/admin/audit/meta')).data,
  })
}

// --- work-queue badges (sidebar) ---------------------------------------------
// One round-trip for every count the caller's nav shows (role- & site-aware).
export function useWorkQueues() {
  return useQuery({
    queryKey: ['/meta/work-queues'],
    queryFn: async () => (await api.get<Record<string, number>>('/meta/work-queues')).data,
    refetchOnWindowFocus: true,
    // Gentle visible-tab polling; hidden tabs rely on the focus refetch
    // (background intervals don't reliably re-render — see useUnreadCount).
    refetchInterval: 60_000,
  })
}

// --- notifications (sidebar bell) -------------------------------------------
export function useUnreadCount() {
  return useQuery({
    queryKey: ['/notifications/unread-count'],
    queryFn: async () => (await api.get<{ unread: number }>('/notifications/unread-count')).data.unread,
    // The badge refreshes after your own actions (mutations invalidate this
    // key) and whenever the window regains focus (refetchOnWindowFocus default).
    // No refetchInterval: a background-polling query does not reliably re-render
    // on invalidation while the tab is hidden.
    refetchOnWindowFocus: true,
  })
}

export function useNotifications(enabled: boolean) {
  return useQuery({
    queryKey: ['/notifications'],
    enabled,
    queryFn: async () => (await api.get<{ items: Row[] }>('/notifications', { params: { limit: 30 } })).data.items,
  })
}

const UNREAD_KEY = ['/notifications/unread-count']

// Optimistically adjust the badge count so it updates the instant you act,
// then reconcile with the server. Rolls back on error.
export function useMarkNotifRead() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.post(`/notifications/${id}/read`).then((r) => r.data),
    onMutate: async () => {
      await qc.cancelQueries({ queryKey: UNREAD_KEY })
      const prev = qc.getQueryData<number>(UNREAD_KEY)
      qc.setQueryData<number>(UNREAD_KEY, (n) => Math.max(0, (n ?? 1) - 1))
      return { prev }
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev !== undefined) qc.setQueryData(UNREAD_KEY, ctx.prev)
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['/notifications'] })
      qc.invalidateQueries({ queryKey: UNREAD_KEY })
    },
  })
}

export function useMarkAllNotifRead() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post('/notifications/read-all').then((r) => r.data),
    onMutate: async () => {
      await qc.cancelQueries({ queryKey: UNREAD_KEY })
      const prev = qc.getQueryData<number>(UNREAD_KEY)
      qc.setQueryData(UNREAD_KEY, 0)
      return { prev }
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev !== undefined) qc.setQueryData(UNREAD_KEY, ctx.prev)
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['/notifications'] })
      qc.invalidateQueries({ queryKey: UNREAD_KEY })
    },
  })
}

// --- admin inventory master editor ------------------------------------------
function useInventoryMutation<V>(fn: (v: V) => Promise<Row>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['/inventory'] })
      qc.invalidateQueries({ queryKey: ['inventory-summary'] })
    },
  })
}
export const useCreateInventory = () =>
  useInventoryMutation((body: Row) => api.post('/admin/inventory', body).then((r) => r.data))
export const useUpdateInventory = () =>
  useInventoryMutation(({ sap, body }: { sap: string; body: Row }) =>
    api.patch(`/admin/inventory/${encodeURIComponent(sap)}`, body).then((r) => r.data))
export const useDeleteInventory = () =>
  useInventoryMutation((sap: string) =>
    api.delete(`/admin/inventory/${encodeURIComponent(sap)}`).then((r) => r.data))

// --- 2FA self-enrollment ----------------------------------------------------
export function use2faStatus() {
  return useQuery({
    queryKey: ['/auth/2fa/status'],
    queryFn: async () => (await api.get<{ enabled: boolean }>('/auth/2fa/status')).data.enabled,
  })
}
export function useEnroll2fa() {
  return useMutation({
    mutationFn: () =>
      api.post<{ secret: string; otpauth_uri: string; qr: string }>('/auth/2fa/enroll').then((r) => r.data),
  })
}
export function useVerify2fa() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (code: string) => api.post('/auth/2fa/verify', { code }).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['/auth/2fa/status'] }),
  })
}
export function useDisable2fa() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (code: string) => api.post('/auth/2fa/disable', { code }).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['/auth/2fa/status'] }),
  })
}

// --- registration + access requests -----------------------------------------
export function useRegister() {
  return useMutation({
    mutationFn: (body: Row) => api.post('/auth/register', body).then((r) => r.data),
  })
}

export function usePendingUsers() {
  return useQuery({
    queryKey: ['/admin/pending-users'],
    queryFn: async () => (await api.get<{ items: Row[] }>('/admin/pending-users')).data.items,
  })
}

function usePendingMutation<V>(fn: (v: V) => Promise<Row>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['/admin/pending-users'] })
      qc.invalidateQueries({ queryKey: ['/admin/users'] })
    },
  })
}
export const useApprovePending = () =>
  usePendingMutation(({ id, body }: { id: number; body: Row }) =>
    api.post(`/admin/pending-users/${id}/approve`, body).then((r) => r.data))
export const useRejectPending = () =>
  usePendingMutation((id: number) =>
    api.post(`/admin/pending-users/${id}/reject`).then((r) => r.data))

// --- reports (downloadable exports) -----------------------------------------
export function useReports() {
  return useQuery({
    queryKey: ['/reports'],
    queryFn: async () => (await api.get<{ reports: Row[] }>('/reports')).data.reports,
  })
}

// --- Store-keeper toolbox -------------------------------------------------------
export function useCountSheet(siteId?: string) {
  return useQuery({
    queryKey: ['/entry/count-sheet', siteId],
    queryFn: async () =>
      (await api.get<{ items: Row[] }>('/entry/count-sheet', { params: siteId ? { site_id: siteId } : {} })).data.items,
  })
}

export function useSubmitCount() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { site_id: string; reason_code: string; rows: Record<string, unknown>[] }) =>
      api.post('/entry/count-sheet', body).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['/hod/pending'] })
      qc.invalidateQueries({ queryKey: ['/meta/work-queues'] })
    },
  })
}

export function useBins(sapCode?: string, siteId?: string) {
  return useQuery({
    queryKey: ['/entry/bins', sapCode, siteId],
    enabled: !!sapCode,
    queryFn: async () =>
      (await api.get<{ bins: string[] }>(`/entry/bins/${encodeURIComponent(sapCode!)}`,
        { params: siteId ? { site_id: siteId } : {} })).data.bins,
  })
}

export function useReturnables(status?: string) {
  return useQuery({
    queryKey: ['/entry/returnables', status],
    queryFn: async () =>
      (await api.get<{ items: Row[]; now: string }>('/entry/returnables',
        { params: status ? { status } : {} })).data,
  })
}

export function useCreateReturnable() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api.post('/entry/returnables', body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['/entry/returnables'] }),
  })
}

export function useMarkReturned() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) =>
      api.post(`/entry/returnables/${id}/return`).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['/entry/returnables'] })
      qc.invalidateQueries({ queryKey: ['/meta/work-queues'] })
    },
  })
}

// --- Warehouse: returns-from-site + history ------------------------------------
export function useWhReturns(status?: string) {
  return useQuery({
    queryKey: ['/warehouse/returns', status],
    queryFn: async () =>
      (await api.get<{ items: Row[] }>('/warehouse/returns', { params: status ? { status } : {} })).data.items,
  })
}

export function useWhCreateReturn() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api.post('/warehouse/returns', body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['/warehouse/returns'] }),
  })
}

export function useWhDisposition() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, status, notes }: { id: number; status: string; notes?: string }) =>
      api.post(`/warehouse/returns/${id}/disposition`, { status, notes }).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['/warehouse/returns'] }),
  })
}

export function useWhHistory(warehouseId?: string) {
  return useQuery({
    queryKey: ['/warehouse/history', warehouseId],
    queryFn: async () =>
      (await api.get('/warehouse/history', { params: warehouseId ? { warehouse_id: warehouseId } : {} })).data as {
        dns: Row[]; assignments: Row[];
        throughput: { dn_by_status: Row[]; dn_by_family: Row[] }
      },
  })
}

// --- HOD operations pack ------------------------------------------------------
export function useHodPreflight(siteId?: string) {
  return useQuery({
    queryKey: ['/hod/preflight', siteId],
    queryFn: async () =>
      (await api.get<{ items: Row[] }>('/hod/preflight', { params: siteId ? { site_id: siteId } : {} })).data.items,
  })
}

export function useHodEditPending() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ kind, id, fields }: { kind: string; id: number; fields: Record<string, unknown> }) =>
      api.patch(`/hod/pending/${kind}/${id}`, { fields }).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['/hod/pending'] })
      qc.invalidateQueries({ queryKey: ['/hod/preflight'] })
    },
  })
}

export function useHodBulkApprove() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ kind, ids }: { kind: string; ids: number[] }) =>
      api.post(`/hod/pending/${kind}/bulk-approve`, { ids }).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['/hod/pending'] })
      qc.invalidateQueries({ queryKey: ['/hod/preflight'] })
      qc.invalidateQueries({ queryKey: ['/meta/work-queues'] })
      invalidateLedger(qc, ['/receipts', '/consumption', '/returns'])
    },
  })
}

export function useLowStock(siteId?: string) {
  return useQuery({
    queryKey: ['/hod/low-stock', siteId],
    queryFn: async () =>
      (await api.get<{ items: Row[] }>('/hod/low-stock', { params: siteId ? { site_id: siteId } : {} })).data.items,
  })
}

export function useAutoDraftPr() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ siteId }: { siteId: string }) =>
      api.post('/hod/prs/auto-draft', { site_id: siteId }).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['/hod/prs'] }),
  })
}

export async function downloadPrPdf(prNumber: string, siteId?: string) {
  const res = await api.get(`/hod/prs/${encodeURIComponent(prNumber)}/pdf`, {
    params: siteId ? { site_id: siteId } : {}, responseType: 'blob',
  })
  const url = URL.createObjectURL(res.data as Blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${prNumber}.pdf`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

// --- Report archive + schedules -------------------------------------------------
export function useReportArchive(reportType?: string) {
  return useQuery({
    queryKey: ['/reports/archive', reportType],
    queryFn: async () =>
      (await api.get<{ items: Row[] }>('/reports/archive',
        { params: reportType ? { report_type: reportType } : {} })).data.items,
  })
}

export function useArchiveReport() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api.post('/reports/archive', body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['/reports/archive'] }),
  })
}

export function useDeleteArchived() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.delete(`/reports/archive/${id}`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['/reports/archive'] }),
  })
}

export async function downloadArchived(id: number, name: string) {
  const res = await api.get(`/reports/archive/${id}/download`, { responseType: 'blob' })
  const url = URL.createObjectURL(res.data as Blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export function useSchedules() {
  return useQuery({
    queryKey: ['/reports/schedules'],
    queryFn: async () => (await api.get<{ items: Row[] }>('/reports/schedules')).data.items,
  })
}

export function useScheduleMutation() {
  const qc = useQueryClient()
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['/reports/schedules'] })
    qc.invalidateQueries({ queryKey: ['/reports/archive'] })
  }
  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api.post('/reports/schedules', body).then((r) => r.data),
    onSuccess: invalidate,
  })
  const toggle = useMutation({
    mutationFn: (id: number) => api.post(`/reports/schedules/${id}/toggle`).then((r) => r.data),
    onSuccess: invalidate,
  })
  const remove = useMutation({
    mutationFn: (id: number) => api.delete(`/reports/schedules/${id}`).then((r) => r.data),
    onSuccess: invalidate,
  })
  const run = useMutation({
    mutationFn: (id: number) => api.post(`/reports/schedules/${id}/run`).then((r) => r.data),
    onSuccess: invalidate,
  })
  return { create, toggle, remove, run }
}

// Authenticated file download: the axios `api` instance carries the bearer
// token, so we fetch the file as a blob and trigger a browser save.
export async function downloadReport(key: string, format: string, params: Record<string, unknown>) {
  const res = await api.get(`/reports/${key}`, {
    params: { format, ...params }, responseType: 'blob',
  })
  const cd = (res.headers['content-disposition'] as string | undefined) ?? ''
  const name = cd.match(/filename="?([^"]+)"?/)?.[1] ?? `${key}.${format}`
  const url = URL.createObjectURL(res.data as Blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
