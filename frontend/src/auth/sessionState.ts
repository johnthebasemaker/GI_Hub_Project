/**
 * frontend/src/auth/sessionState.ts — the ONE owner of "what belongs to the
 * signed-in user" in web storage.
 *
 * WHY THIS FILE EXISTS. Per-user UI state was written to localStorage keyed by
 * SITE, never by user. Sign out of an admin account, sign in as an HOD at the
 * same site, and the admin's Session Builder equipment was still sitting in the
 * tab — along with their expanded nav group, their form drafts and their
 * last-used form values. Every one of those keys is enumerated here, in one
 * place, so a key added next year is either listed or deliberately excluded
 * rather than quietly leaking.
 *
 * ⚠️ THE DENY-LIST IS THE POINT — READ BEFORE ADDING ANYTHING.
 *
 * This module clears NOTHING that powers offline mode. Three things are
 * off-limits and must stay off-limits:
 *
 *   1. **IndexedDB `gi-offline` (store `queue`)** — the offline mutation queue
 *      (offline/queue.ts). A store keeper on failing warehouse Wi-Fi can hold
 *      unsynced issue/receive/return/adjust payloads here. Signing out is not
 *      consent to destroy them, and they replay under their own captured auth
 *      headers, so they do not leak between users either.
 *   2. **Cache Storage `gi-api-read` + the workbox precache** — the Service
 *      Worker's read cache (vite.config.ts → VitePWA.workbox). This is what
 *      makes the app usable on a sudden network drop. Never cleared here.
 *   3. **The service worker registration.** Never unregistered here.
 *
 * Two device preferences are also kept on purpose: `gi-hub-theme` (dark/light
 * is a property of the screen, not the person) and `gi_api_base` (which server
 * a native install talks to — clearing it would strand the Tauri/Android app
 * with no way back to its server short of the config modal).
 *
 * TWO TRIGGERS, NOT ONE. Clearing only on logout misses the case where nobody
 * ever clicks logout — the browser is closed and reopened, or the session
 * simply expires. So `clearOnLogout()` runs on sign-out, and
 * `clearIfDifferentUser()` runs on every sign-in and wipes the lot whenever the
 * username changed. The second is the airtight net; the first just makes the
 * handover immediate.
 */

/** Session-scoped UI state. Cleared on sign-out AND on a user change. */
const SESSION_KEYS = [
  'gi.sme.scenario.v1',      // legacy un-scoped Session Builder order (pre-v2)
  'gi.sme.scenario.v2',      // Session Builder order, now keyed user::site
  'gi.sme.locorder.v1',      // Location Report — per-location priority orders
  'gi.sme.alleqorder.v1',    // Location Report — all-equipment order
  'gi-nav-open',             // which sidebar group is expanded
  'gi-nav-all-areas',        // admin-only "All areas" nav toggle
  'gi-delivery-pref',        // urgent/evening routing header on transactions
  'gi_last_activity',        // idle-logout timestamp
] as const

/**
 * Prefixed key families holding the user's OWN typed content. Cleared only on a
 * user change — a draft is exactly what should survive this same person's
 * session expiring and signing back in.
 */
const USER_CONTENT_PREFIXES = ['gi-form-draft:', 'gi-defaults:'] as const

/**
 * Keys this module must never remove. Not read by the code below — it is a
 * checklist for the next person, and the unit-test target for "did someone
 * start clearing the offline queue?".
 */
export const NEVER_CLEARED = [
  'gi_api_base',           // native install's server address
  'gi-hub-theme',          // display preference
  'gi_sync_interval_min',  // offline sync cadence
  'gi_token',              // owned by api/client.ts setAuthToken(), not us
  'gi_token@training',     // …and its Practice twin (rule 17)
  'gi_env',                // Live | Practice — api/environment.ts
] as const

/** Last username seen by a successful sign-in. Not user content itself. */
const LAST_USER_KEY = 'gi.last-user'

function removeKey(key: string) {
  try { localStorage.removeItem(key) } catch { /* private mode — non-fatal */ }
}

function removeByPrefix(prefix: string) {
  try {
    // Collect first: removing while iterating localStorage re-indexes it and
    // silently skips every other match.
    const doomed: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (k && k.startsWith(prefix)) doomed.push(k)
    }
    doomed.forEach((k) => localStorage.removeItem(k))
  } catch { /* private mode — non-fatal */ }
}

/**
 * Strip the shareable `?scenario=` order out of the address bar.
 *
 * This is the one that makes the difference. ScenarioContext encodes the
 * equipment order into the URL so a planning session can be sent as a link,
 * and on mount **the URL wins over localStorage**. Clear storage but leave the
 * param and a browser that restores the tab replays the previous user's
 * scenario straight back out of the address bar.
 */
function clearScenarioUrl() {
  try {
    const url = new URL(window.location.href)
    if (!url.searchParams.has('scenario')) return
    url.searchParams.delete('scenario')
    window.history.replaceState(null, '', url)
  } catch { /* non-fatal */ }
}

/** Sign-out: drop session state, keep this person's drafts and defaults. */
export function clearOnLogout() {
  SESSION_KEYS.forEach(removeKey)
  clearScenarioUrl()
}

/**
 * Sign-in: if this is a different person than last time, drop everything
 * user-scoped — session state AND their predecessor's drafts/defaults.
 * Returns true when a wipe happened (callers may want to log it).
 */
export function clearIfDifferentUser(username: string): boolean {
  let previous: string | null = null
  try { previous = localStorage.getItem(LAST_USER_KEY) } catch { /* ignore */ }

  const changed = previous !== null && previous !== username
  if (changed) {
    SESSION_KEYS.forEach(removeKey)
    USER_CONTENT_PREFIXES.forEach(removeByPrefix)
    clearScenarioUrl()
  }
  try { localStorage.setItem(LAST_USER_KEY, username) } catch { /* ignore */ }
  return changed
}
