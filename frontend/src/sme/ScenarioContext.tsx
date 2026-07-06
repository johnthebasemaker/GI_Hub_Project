/**
 * frontend/src/sme/ScenarioContext.tsx — persistent SME planning scenario
 * (Phase S1). Holds the equipment priority order that drives the client-side
 * cascade engine. State is React-only (the backend stays read-only per the
 * SME Canon) and persists to localStorage per site key, so a planning session
 * survives refresh/logout — something the Streamlit portal never could.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
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
}

const ScenarioContext = createContext<ScenarioState | null>(null)

export function ScenarioProvider({ siteId, children }: { siteId?: string; children: ReactNode }) {
  const siteKey = siteId ?? 'all'
  const [order, setOrderState] = useState<string[]>(() => readStore()[siteKey] ?? [])

  // Site switch → load that site's persisted scenario.
  useEffect(() => {
    setOrderState(readStore()[siteKey] ?? [])
  }, [siteKey])

  const setOrder = useCallback((next: string[]) => {
    const clean = [...new Set(next.map((t) => t.trim()).filter(Boolean))]
    setOrderState(clean)
    writeStore({ ...readStore(), [siteKey]: clean })
  }, [siteKey])

  const addTag = useCallback((tag: string) => {
    setOrderState((prev) => {
      const t = tag.trim()
      if (!t || prev.includes(t)) return prev
      const next = [...prev, t]
      writeStore({ ...readStore(), [siteKey]: next })
      return next
    })
  }, [siteKey])

  const removeTag = useCallback((tag: string) => {
    setOrderState((prev) => {
      const next = prev.filter((t) => t !== tag)
      writeStore({ ...readStore(), [siteKey]: next })
      return next
    })
  }, [siteKey])

  const moveTag = useCallback((from: number, to: number) => {
    setOrderState((prev) => {
      if (from === to || from < 0 || to < 0 || from >= prev.length || to >= prev.length) return prev
      const next = [...prev]
      const [item] = next.splice(from, 1)
      next.splice(to, 0, item)
      writeStore({ ...readStore(), [siteKey]: next })
      return next
    })
  }, [siteKey])

  const clear = useCallback(() => setOrder([]), [setOrder])

  const value = useMemo(
    () => ({ siteKey, order, setOrder, addTag, removeTag, moveTag, clear }),
    [siteKey, order, setOrder, addTag, removeTag, moveTag, clear],
  )
  return <ScenarioContext.Provider value={value}>{children}</ScenarioContext.Provider>
}

export function useScenario(): ScenarioState {
  const ctx = useContext(ScenarioContext)
  if (!ctx) throw new Error('useScenario must be used inside <ScenarioProvider>')
  return ctx
}
