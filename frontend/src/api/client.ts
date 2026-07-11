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

// For fetch-based callers (SSE streams) that can't use the axios interceptor.
export function getAuthToken(): string | null {
  return _token
}

// --- WhatsApp delivery preference (Phase 6) -----------------------------------
// "urgent" (default) = WhatsApp alerts send immediately; "evening" = staged and
// batched into one 16:00 digest. Sent as a header on every write so the backend
// dispatch() picks it up without per-endpoint plumbing.
export const DELIVERY_PREF_KEY = 'gi-delivery-pref'

export function getDeliveryPreference(): 'urgent' | 'evening' {
  return localStorage.getItem(DELIVERY_PREF_KEY) === 'evening' ? 'evening' : 'urgent'
}

export function setDeliveryPreference(pref: 'urgent' | 'evening') {
  if (pref === 'evening') localStorage.setItem(DELIVERY_PREF_KEY, pref)
  else localStorage.removeItem(DELIVERY_PREF_KEY)
}

api.interceptors.request.use((cfg) => {
  if (_token) cfg.headers.Authorization = `Bearer ${_token}`
  const pref = getDeliveryPreference()
  if (pref !== 'urgent') cfg.headers['X-Delivery-Preference'] = pref
  return cfg
})

// --- silent session refresh ---------------------------------------------------
// Access tokens are short-lived (15 min); the long-lived rotating refresh token
// lives in an httpOnly cookie the JS never sees. On any 401 we try ONE silent
// refresh (single-flight across concurrent 401s) and replay the request — a
// worker mid-shift never notices. Only when the refresh itself fails is the
// session truly over.
const NO_RETRY = ['/auth/login', '/auth/login/2fa', '/auth/refresh', '/auth/register']

let _refreshing: Promise<string | null> | null = null

async function refreshAccessToken(): Promise<string | null> {
  try {
    // Raw axios, not `api` — the interceptor below must not recurse.
    const { data } = await axios.post('/api/auth/refresh')
    const t = (data?.access_token as string) ?? null
    if (t) setAuthToken(t)
    return t
  } catch {
    return null
  }
}

api.interceptors.response.use(
  (r) => r,
  async (err) => {
    const cfg = err?.config
    const url: string = cfg?.url ?? ''
    if (
      err?.response?.status === 401 &&
      cfg &&
      !cfg._retried &&
      !NO_RETRY.some((p) => url.startsWith(p))
    ) {
      cfg._retried = true
      _refreshing ??= refreshAccessToken().finally(() => {
        _refreshing = null
      })
      const t = await _refreshing
      // The request interceptor re-stamps Authorization from the new token.
      if (t) return api(cfg)
      if (_token) {
        setAuthToken(null)
        window.dispatchEvent(new Event('gi-session-expired'))
      }
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
