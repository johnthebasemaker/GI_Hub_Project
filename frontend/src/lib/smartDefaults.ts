// Smart last-entry defaults (UAT Phase 3 QoL) — remembers the routine fields
// of each entry form (site, work type, supplier, …) in localStorage so a store
// keeper doing repetitive entry doesn't retype them. Values are per-form and
// per-user-agnostic (localStorage is already per browser profile); volatile
// fields (material, qty, lot) are deliberately never remembered.

const PREFIX = 'gi-defaults:'

export function loadDefaults(formKey: string): Record<string, string> {
  try {
    const raw = localStorage.getItem(PREFIX + formKey)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return Object.fromEntries(
        Object.entries(parsed as Record<string, unknown>)
          .filter(([, v]) => typeof v === 'string' && v)
      ) as Record<string, string>
    }
  } catch { /* corrupted storage — start fresh */ }
  return {}
}

export function saveDefaults(formKey: string, values: Record<string, unknown>): void {
  try {
    const keep = Object.fromEntries(
      Object.entries(values).filter(([, v]) => typeof v === 'string' && v))
    localStorage.setItem(PREFIX + formKey, JSON.stringify(keep))
  } catch { /* storage full/blocked — defaults are best-effort */ }
}
