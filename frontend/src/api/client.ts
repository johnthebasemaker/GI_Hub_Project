import axios from 'axios'

// All calls go through the Vite dev proxy (/api -> uvicorn :8000).
export const api = axios.create({ baseURL: '/api' })

// --- auth token plumbing -----------------------------------------------------
export const TOKEN_KEY = 'gi_token'
let _token: string | null = localStorage.getItem(TOKEN_KEY)

export function setAuthToken(token: string | null) {
  _token = token
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

api.interceptors.request.use((cfg) => {
  if (_token) cfg.headers.Authorization = `Bearer ${_token}`
  return cfg
})

// On 401 anywhere, drop the token and let the app fall back to the login screen.
api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401 && _token) {
      setAuthToken(null)
      window.dispatchEvent(new Event('gi-unauthorized'))
    }
    return Promise.reject(err)
  },
)

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
