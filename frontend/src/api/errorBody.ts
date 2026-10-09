/**
 * Phase 23a — make every failed request say WHY, including file downloads.
 *
 * ⚠️ A DOWNLOAD ASKS FOR A BLOB, AND SO DOES ITS ERROR. With
 * `responseType: 'blob'` axios hands back the server's JSON explanation as a
 * Blob, and every `errMsg` helper in the app reads `response.data.detail` — so
 * an admin printing a consumption form saw "Request failed with status code
 * 422" instead of "site_id is required for a global role". This turns the blob
 * back into the JSON it was before any caller sees it.
 *
 * ⚠️ AND A PYDANTIC 422 IS A LIST. FastAPI's own validation error is
 * `detail: [{loc, msg}, …]`; the helpers expect a string and fell back to
 * "Action failed". No caller reads the list shape, so it becomes one readable
 * line ("site_id: Field required").
 */
interface ErrLike {
  message?: string
  response?: { data?: unknown; headers?: Record<string, unknown> }
}

function isBlob(x: unknown): x is Blob {
  return typeof Blob !== 'undefined' && x instanceof Blob
}

export function pydanticLine(detail: unknown): string | null {
  if (!Array.isArray(detail)) return null
  const parts = detail.map((d) => {
    const x = d as { loc?: unknown[]; msg?: string }
    const where = Array.isArray(x?.loc)
      ? x.loc.filter((p) => p !== 'body' && p !== 'query' && p !== 'path').join('.')
      : ''
    return where ? `${where}: ${x?.msg ?? 'invalid'}` : (x?.msg ?? 'invalid')
  })
  return parts.length ? parts.join(' · ') : null
}

/** Mutates `err` in place: blob → JSON, list detail → one line, and the
 * message follows the detail so helpers that only read `.message` agree. */
export async function normaliseErrorBody(err: ErrLike): Promise<void> {
  const res = err?.response
  if (!res) return
  if (isBlob(res.data)) {
    const blob = res.data
    if (!/json|text/i.test(blob.type || '')) return
    try {
      const text = await blob.text()
      try {
        res.data = JSON.parse(text)
      } catch {
        res.data = { detail: text.slice(0, 500) }
      }
    } catch {
      return
    }
  }
  const data = res.data as { detail?: unknown } | undefined
  if (!data || typeof data !== 'object') return
  const line = pydanticLine(data.detail)
  if (line) data.detail = line
  if (typeof data.detail === 'string' && data.detail) err.message = data.detail
}
