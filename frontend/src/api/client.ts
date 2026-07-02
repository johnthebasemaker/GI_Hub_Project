import axios from 'axios'

// All calls go through the Vite dev proxy (/api -> uvicorn :8000).
export const api = axios.create({ baseURL: '/api' })

export type Row = Record<string, unknown>

export interface ListResponse<T = Row> {
  total: number
  limit: number
  offset: number
  count: number
  items: T[]
}

export interface InventorySummary {
  total_items: number
  by_site: { Site_ID: string | null; count: number }[]
  by_category: { Category: string | null; count: number }[]
}

export interface Health {
  status: string
  dialect: string
  database: string
  entities: string[]
}

export async function fetchList<T = Row>(
  path: string,
  params: Record<string, unknown> = {},
): Promise<ListResponse<T>> {
  const { data } = await api.get<ListResponse<T>>(path, { params })
  return data
}
