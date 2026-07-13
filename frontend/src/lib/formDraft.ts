import { useEffect, useRef, useState } from 'react'
import type { FormInstance } from 'antd'
import dayjs from 'dayjs'

/**
 * Parity C4 — form draft recovery (the legacy Phase-7E "🛟 Save Form Draft"
 * reborn). Entry-form values are debounced into localStorage per form key;
 * on mount, a banner offers restore/discard. Date fields (ISO strings in
 * storage) are revived to dayjs on restore.
 */
const PREFIX = 'gi-form-draft:'
const DATE_KEYS = new Set(['Date', 'Expiry_Date'])

type Values = Record<string, unknown>

function serialize(values: Values): string {
  return JSON.stringify(values, (_k, v) =>
    v && typeof v === 'object' && 'format' in (v as object) && typeof (v as { format: unknown }).format === 'function'
      ? (v as { format: (f: string) => string }).format('YYYY-MM-DD')
      : v)
}

export function useFormDraft(form: FormInstance, key: string) {
  const storageKey = PREFIX + key
  const [hasDraft, setHasDraft] = useState<boolean>(() => Boolean(localStorage.getItem(storageKey)))
  const timer = useRef<number | null>(null)

  // debounce-save on any change (call from the Form's onValuesChange)
  const onValuesChange = () => {
    if (timer.current != null) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      try {
        const values = form.getFieldsValue()
        localStorage.setItem(storageKey, serialize(values as Values))
      } catch { /* quota etc. — drafts are best-effort */ }
    }, 800)
  }

  const restore = () => {
    try {
      const raw = localStorage.getItem(storageKey)
      if (!raw) return
      const parsed = JSON.parse(raw) as Values
      for (const k of Object.keys(parsed)) {
        if (DATE_KEYS.has(k) && typeof parsed[k] === 'string') parsed[k] = dayjs(parsed[k] as string)
      }
      form.setFieldsValue(parsed)
    } finally {
      setHasDraft(false)
    }
  }

  const discard = () => {
    localStorage.removeItem(storageKey)
    setHasDraft(false)
  }

  // clear on successful submit
  const clear = () => {
    localStorage.removeItem(storageKey)
    setHasDraft(false)
  }

  useEffect(() => () => {
    if (timer.current != null) window.clearTimeout(timer.current)
  }, [])

  return { hasDraft, restore, discard, clear, onValuesChange }
}
