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
