/**
 * frontend/src/components/CommandPalette.tsx — ⌘K / Ctrl-K launcher (Phase 3).
 *
 * A fuzzy jump-to-page over the nav manifest, respecting access (admin shadow
 * included). Lets us keep the sidebar lean without hiding capability: any page
 * a role can open is two keystrokes away. Keyboard: ⌘K/Ctrl-K to open,
 * type to filter, ↑/↓ to move, Enter to go, Esc to close.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Empty, Input, Modal, Tag } from 'antd'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { accessibleNodes } from '../config/nav'
import type { FlatNav } from '../config/nav'

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

export default function CommandPalette() {
  const navigate = useNavigate()
  const { user } = useAuth()
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef<import('antd').InputRef>(null)

  const nodes = useMemo(() => accessibleNodes(user), [user])
  const results = useMemo(
    () => nodes.filter((n) => fuzzy(q, `${n.group} ${n.label}`)).slice(0, 8),
    [nodes, q],
  )

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
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [open])

  useEffect(() => { setActive(0) }, [q])

  const go = (n?: FlatNav) => {
    const target = n ?? results[active]
    if (!target) return
    setOpen(false)
    navigate(target.key)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, results.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); go() }
  }

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
        placeholder="Jump to…  (type a page name)"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={onKeyDown}
        allowClear
      />
      <div style={{ marginTop: 10 }}>
        {results.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No matching pages" />
        ) : (
          results.map((n, i) => (
            <div
              key={n.key}
              onMouseEnter={() => setActive(i)}
              onClick={() => go(n)}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '8px 12px', borderRadius: 6, cursor: 'pointer',
                background: i === active ? 'var(--gi-palette-active, rgba(0,31,64,0.08))' : 'transparent',
              }}
            >
              <span>{n.label}</span>
              {n.group && <Tag style={{ marginInlineEnd: 0 }}>{n.group}</Tag>}
            </div>
          ))
        )}
      </div>
      <div style={{ marginTop: 8, fontSize: 11, opacity: 0.55, textAlign: 'right' }}>
        ↑↓ to navigate · Enter to open · Esc to close
      </div>
    </Modal>
  )
}
