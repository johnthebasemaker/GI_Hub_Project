import { useEffect } from 'react'
import type { CSSProperties } from 'react'
import { Form, Select, Tag, Typography } from 'antd'
import { useAuth } from '../auth/AuthContext'
import type { User } from '../auth/AuthContext'
import { useSites } from '../api/hooks'

/**
 * The site a user is LOCKED to, or null when they may choose.
 *
 * Mirrors `backend/api/auth.py::site_scope` exactly: below level 3 a user reads
 * and writes their own site only (the API refuses any other with a 403), the
 * Head of Qualities excepted. A scoped user with NO site keeps the picker — the
 * server then refuses what they pick, which is the honest failure; a guessed
 * site would not be.
 */
export function lockedSite(user: User | null | undefined): string | null {
  if (!user || user.role === 'qc_hod' || (user.level ?? 0) >= 3) return null
  const s = (user.site_id || '').trim()
  return s || null
}

/**
 * The Site field on an entry form (2026-09-30).
 *
 * A store keeper is assigned to ONE site and every entry they post lands there
 * whatever they pick, so asking was a question with one right answer and a
 * wrong one the server refuses. A locked user sees their site as a fact; the
 * form value is pinned to it, overriding a remembered default or a restored
 * draft from another account. Everyone else keeps the dropdown.
 */
export default function SiteField({ name = 'Site_ID', label = 'Site', required = true }: {
  name?: string; label?: string; required?: boolean
}) {
  const { user } = useAuth()
  const locked = lockedSite(user)
  const form = Form.useFormInstance()
  const { data: sites } = useSites()
  const current = Form.useWatch(name, form)

  useEffect(() => {
    if (locked && current !== locked) form.setFieldValue(name, locked)
  }, [locked, current, form, name])

  if (locked) {
    return (
      <>
        <Form.Item name={name} hidden initialValue={locked}><input /></Form.Item>
        <Form.Item label={label}>
          <Tag color="blue" style={{ fontSize: 14, padding: '2px 10px' }} data-testid="site-locked">{locked}</Tag>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>your site</Typography.Text>
        </Form.Item>
      </>
    )
  }
  return (
    <Form.Item name={name} label={label} rules={required ? [{ required: true }] : undefined}>
      <Select placeholder="Select site" options={(sites ?? []).map((s) => ({ value: s, label: s }))} />
    </Form.Item>
  )
}

/**
 * The "All sites" FILTER on a report or queue (2026-09-30). For a user locked
 * to one site the server already pins every read to it — and answers any
 * OTHER site with a 403 — so the picker could only ever show that site or an
 * error. They see their site as a label instead; everyone else, the picker.
 */
export function SiteFilter({ value, onChange, style, placeholder = 'All sites' }: {
  value?: string
  onChange: (v: string | undefined) => void
  style?: CSSProperties
  placeholder?: string
}) {
  const { user } = useAuth()
  const locked = lockedSite(user)
  const { data: sites } = useSites()
  if (locked) return <Tag color="blue" style={{ ...style, width: 'auto' }} data-testid="site-locked">{locked}</Tag>
  return (
    <Select allowClear placeholder={placeholder} style={style} value={value} onChange={onChange}
      options={(sites ?? []).map((s) => ({ value: s, label: s }))} />
  )
}
