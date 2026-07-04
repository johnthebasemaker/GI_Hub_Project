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
    mutationFn: ({ id, action, reason }: { id: number; action: 'approve' | 'reject'; reason?: string }) =>
      api.post(`/requests/${id}/${action}`, action === 'reject' ? { reason } : {}).then((r) => r.data),
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
