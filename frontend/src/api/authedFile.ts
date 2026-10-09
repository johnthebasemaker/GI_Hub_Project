import { useEffect, useState } from 'react'
import { api } from './client'

/**
 * Phase 23a — open or preview a file the API serves, SIGNED IN.
 *
 * ⚠️ A PLAIN LINK IS NOT SIGNED IN. The access token lives in memory and is
 * sent as a bearer header; it is never a cookie. So `<a href="/api/…">`,
 * `<img src="/api/…">`, `<iframe src="/api/…">` and `window.open(url)` all
 * reach the server with no token and come back 401 — the QC certificate link,
 * the delivery-note link, the document-library preview and the Execution
 * report exports all did, silently. These fetch through `api` (token attached,
 * one refresh on 401) and hand the browser a local blob URL instead.
 */
export async function fetchBlob(path: string, params?: Record<string, unknown>): Promise<Blob> {
  const r = await api.get(path, { params, responseType: 'blob' })
  return r.data as Blob
}

/** Open in a new tab. The tab is opened FIRST, inside the click, so a popup
 * blocker sees a user gesture; it is pointed at the file when it arrives. */
export async function openAuthed(path: string, params?: Record<string, unknown>): Promise<void> {
  const tab = window.open('', '_blank')
  try {
    const url = URL.createObjectURL(await fetchBlob(path, params))
    if (tab) tab.location.href = url
    else window.location.assign(url)
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
  } catch (e) {
    tab?.close()
    throw e
  }
}

/** A blob URL for an <img> / <iframe>, revoked when the path changes. */
export function useAuthedUrl(path: string | null): { url: string | null; error: string | null } {
  const [state, setState] = useState<{ url: string | null; error: string | null }>({ url: null, error: null })
  useEffect(() => {
    if (!path) { setState({ url: null, error: null }); return }
    let alive = true
    let made: string | null = null
    fetchBlob(path)
      .then((b) => {
        made = URL.createObjectURL(b)
        if (alive) setState({ url: made, error: null })
      })
      .catch((e: unknown) => {
        if (alive) setState({ url: null, error: (e as Error)?.message || 'Could not open the file' })
      })
    return () => { alive = false; if (made) URL.revokeObjectURL(made) }
  }, [path])
  return state
}
