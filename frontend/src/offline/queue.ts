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
 *
 * ⚠️ RULE 17, VECTOR V4 — THE ONE CONTAMINATION PATH NO SERVER WALL CAN SEE.
 * A queued entry used to be replayed against whatever API base was current,
 * under whatever token was current. So a receipt practised offline in Practice,
 * followed by a switch to Live and a sign-in, arrived at LIVE as a perfectly
 * valid request from a perfectly valid Live session. Three things close it:
 *   1. one IndexedDB per environment (`gi-offline` / `gi-offline@training`),
 *      so this page can only ever SEE its own environment's queue;
 *   2. every entry is STAMPED with the environment it was made in, and an
 *      entry whose stamp is not this page's is never sent (entries from before
 *      the stamp existed were made by Live, and are read as Live);
 *   3. the stamp rides the replay as `X-GI-Instance`, so if 1 and 2 were ever
 *      both wrong the server refuses it with a 409 (backend/api/instance.py).
 */
import { api } from '../api/client'
import { CURRENT_ENV, ENV_HEADER, offlineDbName, type GiEnv } from '../api/environment'

const DB_NAME = offlineDbName(CURRENT_ENV)
const STORE = 'queue'

export interface QueuedEntry {
  id?: number
  path: string
  body: unknown
  headers: Record<string, string>
  queuedAt: string
  /** The environment this entry was made in. Absent on pre-rule-17 entries,
   * which were all made by Live. */
  env?: GiEnv
}

/** An entry is replayed only in the environment that made it. */
export function entryEnv(e: Pick<QueuedEntry, 'env'>): GiEnv {
  return e.env === 'training' ? 'training' : 'production'
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
  await addEntry({ path, body, headers, queuedAt: new Date().toISOString(), env: CURRENT_ENV })
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
      // Never sent, never dropped: it belongs to the other environment and
      // will be replayed when this browser is back in it.
      if (entryEnv(entry) !== CURRENT_ENV) continue
      try {
        await api.post(entry.path, entry.body, {
          headers: { ...entry.headers, [ENV_HEADER]: entryEnv(entry), 'X-Offline-Replay': '1' },
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
