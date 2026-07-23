/**
 * Offline mutation queue (Phase B — PWA).
 *
 * A store keeper on warehouse Wi-Fi loses signal mid-submit: instead of an
 * error and a lost form, the transaction payload is saved to IndexedDB and
 * replayed automatically when the network returns. Only the material-
 * transaction POSTs opt in (issue / receive / return / adjust / bulk) via
 * postWithOfflineFallback() — approvals, auth and admin actions stay
 * strictly online.
 *
 * Sync triggers: the browser 'online' event, app boot, a 60 s interval while
 * anything is queued, and the header badge's manual "sync now". Replay is
 * serialized and stops on the first network failure (still offline). A
 * replayed request that the server REJECTS (4xx/5xx) is dropped from the
 * queue and surfaced — it will never block the entries behind it.
 *
 * UI plumbing is window events (the module is imported outside React):
 *   'gi-offline-queue'   detail {count}          — badge updates
 *   'gi-offline-queued'  detail {path}           — "saved offline" toast
 *   'gi-offline-flushed' detail {sent, failed[]} — "synced" toast
 */
import { api } from '../api/client'

const DB_NAME = 'gi-offline'
const STORE = 'queue'

export interface QueuedEntry {
  id?: number
  path: string
  body: unknown
  headers: Record<string, string>
  queuedAt: string
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1)
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) {
        req.result.createObjectStore(STORE, { keyPath: 'id', autoIncrement: true })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

function tx<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return openDb().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const t = db.transaction(STORE, mode)
        const req = run(t.objectStore(STORE))
        req.onsuccess = () => resolve(req.result)
        req.onerror = () => reject(req.error)
      }),
  )
}

export const listQueue = () => tx<QueuedEntry[]>('readonly', (s) => s.getAll() as IDBRequest<QueuedEntry[]>)
export const queueCount = () => tx<number>('readonly', (s) => s.count())
const addEntry = (e: QueuedEntry) => tx('readwrite', (s) => s.add(e))
const removeEntry = (id: number) => tx('readwrite', (s) => s.delete(id))

async function emitCount() {
  const count = await queueCount().catch(() => 0)
  window.dispatchEvent(new CustomEvent('gi-offline-queue', { detail: { count } }))
  return count
}

/** True for "the request never reached the server" failures only. */
export function isNetworkError(err: unknown): boolean {
  const e = err as { response?: unknown; code?: string; message?: string }
  return !e?.response && (e?.code === 'ERR_NETWORK' || e?.code === 'ECONNABORTED' || e?.message === 'Network Error')
}

export async function enqueue(path: string, body: unknown, headers: Record<string, string>): Promise<void> {
  await addEntry({ path, body, headers, queuedAt: new Date().toISOString() })
  window.dispatchEvent(new CustomEvent('gi-offline-queued', { detail: { path } }))
  await emitCount()
}

// --- user-configurable auto-sync cadence ("Outlook-style" Send/Receive) -----
// The header SyncControls UI writes the cap; the boot timer re-arms on change.
const SYNC_INTERVAL_KEY = 'gi_sync_interval_min'

export function getSyncIntervalMin(): number {
  const n = Number(localStorage.getItem(SYNC_INTERVAL_KEY))
  return Number.isFinite(n) && n >= 1 && n <= 120 ? Math.round(n) : 1
}

export function setSyncIntervalMin(min: number): void {
  localStorage.setItem(SYNC_INTERVAL_KEY, String(Math.min(120, Math.max(1, Math.round(min)))))
  window.dispatchEvent(new Event('gi-sync-interval'))
}

let flushing = false

/** Replay everything queued, oldest first. Safe to call any time. */
export async function flushQueue(): Promise<{ sent: number; failed: string[] }> {
  if (flushing) return { sent: 0, failed: [] }
  flushing = true
  const failed: string[] = []
  let sent = 0
  try {
    const entries = await listQueue()
    for (const entry of entries.sort((a, b) => (a.id ?? 0) - (b.id ?? 0))) {
      try {
        await api.post(entry.path, entry.body, {
          headers: { ...entry.headers, 'X-Offline-Replay': '1' },
        })
        await removeEntry(entry.id!)
        sent += 1
      } catch (err) {
        if (isNetworkError(err)) break // still offline — keep the rest queued
        // server rejected it — drop so it can't dam the queue, but surface it
        const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        failed.push(`${entry.path}: ${detail ?? 'rejected by the server'}`)
        await removeEntry(entry.id!)
      }
    }
  } finally {
    flushing = false
    await emitCount()
    if (sent || failed.length) {
      window.dispatchEvent(new CustomEvent('gi-offline-flushed', { detail: { sent, failed } }))
    }
  }
  return { sent, failed }
}

/**
 * The transaction POST used by the entry-form hooks: normal request first;
 * on a NETWORK failure the payload is queued and a synthetic result comes
 * back so the form clears like a success (the UI shows a "saved offline"
 * toast instead of the usual one).
 */
export async function postWithOfflineFallback<T>(
  path: string,
  body: unknown,
  headers: Record<string, string>,
): Promise<T | { queued: true }> {
  try {
    return (await api.post<T>(path, body, { headers })).data
  } catch (err) {
    if (!isNetworkError(err)) throw err
    await enqueue(path, body, headers)
    return { queued: true }
  }
}

export function initOfflineQueue() {
  window.addEventListener('online', () => void flushQueue())
  // Auto-sync timer honours the user's Sync Settings cap (default 1 min) and
  // re-arms itself whenever SyncControls changes the setting.
  let timer = 0
  const arm = () => {
    window.clearInterval(timer)
    timer = window.setInterval(() => {
      if (navigator.onLine) void queueCount().then((n) => n && void flushQueue())
    }, getSyncIntervalMin() * 60_000)
  }
  window.addEventListener('gi-sync-interval', arm)
  arm()
  void emitCount()
  if (navigator.onLine) void flushQueue()
  // exposed for the Playwright offline spec + console debugging
  ;(window as unknown as Record<string, unknown>).__giOffline = {
    post: postWithOfflineFallback, flush: flushQueue, count: queueCount, list: listQueue,
  }
}
