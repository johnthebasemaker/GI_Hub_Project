// Reorder signals — intelligent minimum stock (Phase 18 Track 4).
// Its own module: `api/hooks.ts` is on the login critical path (zero growth).
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'

export type RagStatus = 'red' | 'amber' | 'green' | 'none'

export interface SmartMinRow {
  SAP_Code: string
  Site_ID: string
  Material_Code: string | null
  Description: string | null
  Category: string | null
  UOM: string | null
  Surface_Shield: boolean
  Current_Stock: number
  Manual_Min: number | null
  Recommended_Min: number
  Effective_Min: number
  Min_Source: 'accepted' | 'manual' | 'smart' | null
  /** the site's minimum as the HOD accepted it (Phase 19a), if any */
  Accepted_Min: number | null
  Accepted_By: string | null
  Accepted_At: string | null
  /** the recommendation's move away from the accepted minimum, as a fraction,
   *  when beyond the ±20 % band — else null */
  Changed: number | null
  Basis: string
  Why: string
  Daily_Use: number | null
  Plan_Demand_Base: number | null
  Base_UOM: string | null
  /** open PO quantity raised from THIS site's PRs (Phase 19b) */
  On_Order: number
  /** open PO quantity on POs not raised from a PR — shown, never subtracted */
  Global_On_Order: number
  /** Phase 22d — requested WITHOUT a PR, not yet received (Q22-13) */
  Requested_No_PR?: number
  Suggested_Order: number
  /** Phase 21b: why there is no order (no SQM pace → set one), else null */
  Order_Hint?: string | null
  /** Phase 21b: how a Surface Shield minimum was worked out, one step a line */
  Trail?: string[] | null
  Days_Of_Cover: number | null
  Status: RagStatus
}

export interface SmartMinSite {
  remaining_sqm: number
  sqm_per_day: number
  pace_source: 'site_plan' | 'planned' | 'approved_entries'
  /** the site's approved SQM per day over the pace window — the suggestion */
  suggested_sqm_per_day: number
  /** the rate the site set for itself (ruling Q6 option B), if any */
  site_planned_sqm_per_day: number | null
  plan_share: number
  basis: 'plan_pace' | 'plan_all'
  flags: string[]
}

export interface SmartMin {
  items: SmartMinRow[]
  counts: Record<RagStatus, number> & { changed?: number; accepted?: number }
  sites: Record<string, SmartMinSite>
  params: { cover_days?: number; window_days?: number; surge_window_days?: number;
    pace_window_days?: number; planned_sqm_per_day?: number | null; amber_factor?: number
    changed_band?: number }
}

export function useSmartMin(siteId?: string) {
  return useQuery({
    queryKey: ['/stock/smart-min', siteId ?? null],
    queryFn: async () =>
      (await api.get<SmartMin>('/stock/smart-min', { params: siteId ? { site_id: siteId } : {} })).data,
    staleTime: 60_000,
  })
}

/** Set (sqm_per_day > 0) or clear (null) one site's planned SQM per day. */
export function useSetSitePace() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { site_id: string; sqm_per_day: number | null }) =>
      api.put('/stock/smart-min/pace', body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['/stock/smart-min'] }),
  })
}

/** The HOD accepts (or edits) recommended minimums — Phase 19a. */
export function useAcceptMinimums() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (items: { site_id: string; sap_code: string; minimum_qty: number }[]) =>
      api.post<{ accepted: number }>('/stock/smart-min/accept', { items }).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['/stock/smart-min'] }),
  })
}
