/**
 * frontend/src/sme/ScenarioContext.tsx — persistent SME planning scenario
 * (Phase S1). Holds the equipment priority order that drives the client-side
 * cascade engine. State is React-only (the backend stays read-only per the
 * SME Canon) and persists to localStorage per site key, so a planning session
 * survives refresh/logout — something the Streamlit portal never could.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

const STORAGE_KEY = 'gi.sme.scenario.v1'

type Store = Record<string, string[]> // siteKey → ordered equipment tags

function readStore(): Store {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : {}
    return parsed && typeof parsed === 'object' ? (parsed as Store) : {}
  } catch {
    return {} // corrupted storage → start clean, never crash the portal
  }
}

function writeStore(store: Store) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(store))
  } catch {
    /* quota/private-mode failures are non-fatal: scenario stays in memory */
  }
}

// --- URL sharing (Phase S3) ---------------------------------------------------
// The priority order is also encoded into ?scenario= so a planning session can
// be shared as a link. Equipment tags never contain '~' (SAP-style codes), so
// '~' delimits encodeURIComponent()-escaped tags. URL wins over localStorage
// on first load (an opened share-link shows the sender's exact scenario).
const URL_PARAM = 'scenario'

function readUrlOrder(): string[] | null {
  const p = new URLSearchParams(window.location.search).get(URL_PARAM)
  if (!p) return null
  const tags = p.split('~').map((t) => {
    try { return decodeURIComponent(t).trim() } catch { return '' }
  }).filter(Boolean)
  return tags.length ? [...new Set(tags)] : null
}

function writeUrlOrder(order: string[]) {
  try {
    const url = new URL(window.location.href)
    if (order.length) url.searchParams.set(URL_PARAM, order.map(encodeURIComponent).join('~'))
    else url.searchParams.delete(URL_PARAM)
    window.history.replaceState(null, '', url)
  } catch {
    /* non-fatal: sharing degrades, scenario still works */
  }
}

export interface ScenarioState {
  /** Site this scenario belongs to ('all' for the admin cross-site view). */
  siteKey: string
  /** Equipment tags in priority order (top = allocated first). */
  order: string[]
  setOrder: (next: string[]) => void
  addTag: (tag: string) => void
  removeTag: (tag: string) => void
  /** Move the tag at `from` to position `to` (dnd-kit reorder handler). */
  moveTag: (from: number, to: number) => void
  clear: () => void
  /** Current shareable URL (already synced on every change). */
  shareUrl: () => string
}

const ScenarioContext = createContext<ScenarioState | null>(null)

export function ScenarioProvider({ siteId, children }: { siteId?: string; children: ReactNode }) {
  const siteKey = siteId ?? 'all'
  const [order, setOrderState] = useState<string[]>(
    () => readUrlOrder() ?? readStore()[siteKey] ?? [])

  // First mount: a ?scenario= URL wins (and is persisted so refresh keeps it).
  // Site switch afterwards: load that site's persisted scenario.
  const firstMount = useRef(true)
  useEffect(() => {
    const fromUrl = firstMount.current ? readUrlOrder() : null
    firstMount.current = false
    const next = fromUrl ?? readStore()[siteKey] ?? []
    setOrderState(next)
    if (fromUrl) writeStore({ ...readStore(), [siteKey]: fromUrl })
    writeUrlOrder(next)
  }, [siteKey])

  const persist = useCallback((next: string[]) => {
    writeStore({ ...readStore(), [siteKey]: next })
    writeUrlOrder(next)
  }, [siteKey])

  const setOrder = useCallback((next: string[]) => {
    const clean = [...new Set(next.map((t) => t.trim()).filter(Boolean))]
    setOrderState(clean)
    persist(clean)
  }, [persist])

  const addTag = useCallback((tag: string) => {
    setOrderState((prev) => {
      const t = tag.trim()
      if (!t || prev.includes(t)) return prev
      const next = [...prev, t]
      persist(next)
      return next
    })
  }, [persist])

  const removeTag = useCallback((tag: string) => {
    setOrderState((prev) => {
      const next = prev.filter((t) => t !== tag)
      persist(next)
      return next
    })
  }, [persist])

  const moveTag = useCallback((from: number, to: number) => {
    setOrderState((prev) => {
      if (from === to || from < 0 || to < 0 || from >= prev.length || to >= prev.length) return prev
      const next = [...prev]
      const [item] = next.splice(from, 1)
      next.splice(to, 0, item)
      persist(next)
      return next
    })
  }, [persist])

  const clear = useCallback(() => setOrder([]), [setOrder])
  const shareUrl = useCallback(() => window.location.href, [])

  const value = useMemo(
    () => ({ siteKey, order, setOrder, addTag, removeTag, moveTag, clear, shareUrl }),
    [siteKey, order, setOrder, addTag, removeTag, moveTag, clear, shareUrl],
  )
  return <ScenarioContext.Provider value={value}>{children}</ScenarioContext.Provider>
}

export function useScenario(): ScenarioState {
  const ctx = useContext(ScenarioContext)
  if (!ctx) throw new Error('useScenario must be used inside <ScenarioProvider>')
  return ctx
}
