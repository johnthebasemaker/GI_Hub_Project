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

// Ledger data entry: post a goods receipt, then refresh the affected views.
export function useReceiptEntry() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Row) => api.post('/entry/receipts', body).then((r) => r.data),
    onSuccess: () => {
      for (const k of ['/stock/live', '/stock/by-site', '/stock/lots', '/receipts']) {
        qc.invalidateQueries({ queryKey: [k] })
      }
    },
  })
}
