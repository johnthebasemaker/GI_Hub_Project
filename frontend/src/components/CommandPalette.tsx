/**
 * frontend/src/components/CommandPalette.tsx — ⌘K / Ctrl-K launcher.
 *
 * Two kinds of result in one list:
 *
 *   PAGES     a fuzzy jump over the nav manifest, respecting access (admin
 *             shadow included). Keeps the sidebar lean without hiding
 *             capability: any page a role can open is two keystrokes away.
 *   MATERIALS live search over stock by SAP code, material code or
 *             description (2026-08-04). Warehouse staff think in SAP codes,
 *             not page names — "1042" now takes you straight to that
 *             material's card instead of via Stock → filter → click.
 *
 * The material half reuses `/stock/by-site`, which already applies the
 * caller's site scoping server-side, so the palette cannot become a way to
 * see another site's inventory. Nothing here re-implements access control.
 *
 * Keyboard: ⌘K/Ctrl-K to open, type to filter, ↑/↓ to move, Enter to go,
 * Esc to close.
 */
import { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react'
import { Empty, Input, Modal, Spin, Tag } from 'antd'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { accessibleNodes } from '../config/nav'
import type { FlatNav } from '../config/nav'

// Phase 23d — loaded only when materials are listed (off the critical path)
const PaletteThumb = lazy(() => import('../catalogue/PaletteThumbs'))

// Subsequence fuzzy match ("isu" matches "Issue Stock"); returns false if no match.
function fuzzy(query: string, text: string): boolean {
  const q = query.toLowerCase().replace(/\s+/g, '')
  if (!q) return true
  const t = text.toLowerCase()
  let i = 0
  for (const ch of t) {
    if (ch === q[i]) i++
    if (i === q.length) return true
  }
  return false
}

interface MaterialHit {
  SAP_Code: string
  Equipment_Description?: string | null
  Material_Code?: string | null
  UOM?: string | null
  Current_Stock?: number | null
}

type Row =
  | { kind: 'page'; key: string; node: FlatNav }
  | { kind: 'material'; key: string; hit: MaterialHit }

const MIN_QUERY = 2
const DEBOUNCE_MS = 200
const MAX_PAGES = 6
const MAX_MATERIALS = 6

export default function CommandPalette() {
  const navigate = useNavigate()
  const { user } = useAuth()
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  const [materials, setMaterials] = useState<MaterialHit[]>([])
  /** SAP → the rack to walk to. The locator's whole payoff is here: a store
   *  keeper types a material and is told the shelf, without leaving the
   *  palette or opening a second page. */
  const [racks, setRacks] = useState<Record<string, string>>({})
  const [searching, setSearching] = useState(false)
  const inputRef = useRef<import('antd').InputRef>(null)

  const nodes = useMemo(() => accessibleNodes(user), [user])
  const pages = useMemo(
    () => nodes.filter((n) => fuzzy(q, `${n.group} ${n.label}`)).slice(0, MAX_PAGES),
    [nodes, q],
  )

  // --- live material lookup -------------------------------------------------
  // Debounced, and every in-flight request is aborted when the query moves on,
  // so a slow response for "10" can never overwrite the results for "1042".
  useEffect(() => {
    const term = q.trim()
    if (!open || term.length < MIN_QUERY) {
      setMaterials([])
      setRacks({})
      setSearching(false)
      return
    }
    const ctl = new AbortController()
    setSearching(true)
    const t = window.setTimeout(() => {
      // Two requests, one debounce, one abort signal. `/stock/by-site` is the
      // authority on WHICH materials this caller may see (it applies site
      // scoping server-side); `/locations/lookup` only says where they sit, so
      // it can never widen the result set — a rack with no matching stock row
      // simply has nothing to attach to.
      Promise.all([
        api.get('/stock/by-site', {
          params: { q: term, limit: MAX_MATERIALS }, signal: ctl.signal,
        }),
        api.get('/locations/lookup', {
          params: { q: term, limit: MAX_MATERIALS }, signal: ctl.signal,
        }).catch(() => null),   // the locator is a bonus, never a blocker
      ])
        .then(([stock, loc]) => {
          setMaterials((stock.data?.items ?? []) as MaterialHit[])
          const m: Record<string, string> = {}
          for (const it of (loc?.data?.items ?? []) as {
            SAP_Code: string; primary_label?: string | null
            primary_location?: string | null
          }[]) {
            if (it.primary_location) {
              m[String(it.SAP_Code)] = it.primary_label
                ? `${it.primary_location} · ${it.primary_label}`
                : it.primary_location
            }
          }
          setRacks(m)
        })
        .catch(() => { /* aborted, offline, or a role with no site — show pages only */ })
        .finally(() => setSearching(false))
    }, DEBOUNCE_MS)
    return () => {
      window.clearTimeout(t)
      ctl.abort()
    }
  }, [q, open])

  // One flat list so ↑/↓ crosses the section boundary naturally.
  const rows = useMemo<Row[]>(() => [
    ...pages.map((n) => ({ kind: 'page' as const, key: `p:${n.key}`, node: n })),
    ...materials.map((m) => ({ kind: 'material' as const, key: `m:${m.SAP_Code}`, hit: m })),
  ], [pages, materials])

  // Global ⌘K / Ctrl-K toggle + a custom event so the header button can open it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen((v) => !v)
      }
    }
    const onOpenEvent = () => setOpen(true)
    window.addEventListener('keydown', onKey)
    window.addEventListener('gi-open-command-palette', onOpenEvent)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('gi-open-command-palette', onOpenEvent)
    }
  }, [])

  // Reset + focus each time it opens.
  useEffect(() => {
    if (open) {
      setQ('')
      setActive(0)
      setMaterials([])
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [open])

  useEffect(() => { setActive(0) }, [q])

  const go = (row?: Row) => {
    const target = row ?? rows[active]
    if (!target) return
    setOpen(false)
    if (target.kind === 'page') navigate(target.node.key)
    else navigate(`/stock/material/${encodeURIComponent(target.hit.SAP_Code)}`)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, rows.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); go() }
  }

  const rowStyle = (i: number): React.CSSProperties => ({
    display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8,
    padding: '8px 12px', borderRadius: 6, cursor: 'pointer',
    background: i === active ? 'var(--gi-palette-active, rgba(0,31,64,0.08))' : 'transparent',
  })

  const heading = (text: string) => (
    <div style={{
      fontSize: 10, letterSpacing: '.08em', textTransform: 'uppercase',
      opacity: 0.5, padding: '8px 12px 4px',
    }}>{text}</div>
  )

  return (
    <Modal
      open={open}
      onCancel={() => setOpen(false)}
      footer={null}
      closable={false}
      destroyOnHidden
      styles={{ body: { padding: 12 } }}
      width={560}
    >
      <Input
        ref={inputRef}
        size="large"
        placeholder="Jump to…  (page name, SAP code, or material)"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={onKeyDown}
        allowClear
        suffix={searching ? <Spin size="small" /> : <span style={{ width: 14 }} />}
      />
      <div style={{ marginTop: 10 }}>
        {rows.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={q.trim().length < MIN_QUERY
              ? 'Type to search pages and materials'
              : 'No matching pages or materials'}
          />
        ) : (
          <>
            {pages.length > 0 && heading('Pages')}
            {pages.map((n, i) => (
              <div key={`p:${n.key}`} onMouseEnter={() => setActive(i)}
                onClick={() => go(rows[i])} style={rowStyle(i)}>
                <span>{n.label}</span>
                {n.group && <Tag style={{ marginInlineEnd: 0 }}>{n.group}</Tag>}
              </div>
            ))}
            {materials.length > 0 && heading('Materials')}
            {materials.map((m, j) => {
              const i = pages.length + j
              return (
                <div key={`m:${m.SAP_Code}`} onMouseEnter={() => setActive(i)}
                  onClick={() => go(rows[i])} style={rowStyle(i)}>
                  {/* Phase 23d — the material's picture (one batched call, lazy) */}
                  <Suspense fallback={null}>
                    <PaletteThumb sap={m.SAP_Code} all={materials.map((x) => x.SAP_Code)} />
                  </Suspense>
                  <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap' }}>
                    <b style={{ fontFamily: 'JetBrains Mono, monospace' }}>{m.SAP_Code}</b>
                    {m.Equipment_Description ? ` · ${m.Equipment_Description}` : ''}
                  </span>
                  {/* The rack, when we know it. Shown BEFORE the quantity
                      because "where do I go" is the question being asked —
                      the store keeper already knows they want the material. */}
                  {racks[m.SAP_Code] && (
                    <Tag color="blue" style={{ marginInlineEnd: 0, flexShrink: 0,
                      fontFamily: 'JetBrains Mono, monospace' }}>
                      📍 {racks[m.SAP_Code]}
                    </Tag>
                  )}
                  <Tag style={{ marginInlineEnd: 0, flexShrink: 0 }}>
                    {Number(m.Current_Stock ?? 0).toLocaleString()} {m.UOM ?? ''}
                  </Tag>
                </div>
              )
            })}
          </>
        )}
      </div>
      <div style={{ marginTop: 8, fontSize: 11, opacity: 0.55, textAlign: 'right' }}>
        ↑↓ to navigate · Enter to open · Esc to close
      </div>
    </Modal>
  )
}
