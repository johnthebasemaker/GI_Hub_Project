// Reorder signals — intelligent minimum stock (Phase 18 Track 4).
// Its own module: `api/hooks.ts` is on the login critical path (zero growth).
import { useQuery } from '@tanstack/react-query'
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
  Min_Source: 'manual' | 'smart' | null
  Basis: string
  Why: string
  Daily_Use: number | null
  Plan_Demand_Base: number | null
  Base_UOM: string | null
  On_Order: number
  Suggested_Order: number
  Days_Of_Cover: number | null
  Status: RagStatus
}

export interface SmartMinSite {
  remaining_sqm: number
  sqm_per_day: number
  pace_source: 'planned' | 'approved_entries'
  plan_share: number
  basis: 'plan_pace' | 'plan_all'
  flags: string[]
}

export interface SmartMin {
  items: SmartMinRow[]
  counts: Record<RagStatus, number>
  sites: Record<string, SmartMinSite>
  params: { cover_days?: number; window_days?: number; surge_window_days?: number;
    pace_window_days?: number; planned_sqm_per_day?: number | null; amber_factor?: number }
}

export function useSmartMin(siteId?: string) {
  return useQuery({
    queryKey: ['/stock/smart-min', siteId ?? null],
    queryFn: async () =>
      (await api.get<SmartMin>('/stock/smart-min', { params: siteId ? { site_id: siteId } : {} })).data,
    staleTime: 60_000,
  })
}
