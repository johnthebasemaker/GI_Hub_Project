/**
 * Live | Practice — which backend this browser talks to (rule 17).
 *
 * Practice is a SECOND API PROCESS with its own database, mounted next to Live
 * at `/training-api/` (nginx in production, the Vite proxy in dev). This module
 * only decides which of the two bases the app uses; it never decides anything
 * about data, because the client is not where that decision can be trusted.
 *
 * ⚠️ THE CHOICE IS FIXED FOR THE LIFE OF THE PAGE. `CURRENT_ENV` is read once
 * at load, and switching persists the new value and RELOADS. That is what makes
 * it impossible for React Query's cache, an in-flight request, an SSE stream or
 * the offline queue's open handle to carry one environment's data into the
 * other — none of them survive the reload. (Operator ruling Q3: one environment
 * per browser at a time, and switching signs you out.)
 *
 * ⚠️ THE BANNER DOES NOT READ THIS. What is painted on screen comes from
 * `GET /instance` — the SERVER saying what it is — so a misrouted proxy shows
 * up as a mismatch instead of a comforting label (vector V10).
 */
export type GiEnv = 'production' | 'training'

export const ENV_KEY = 'gi_env'
export const ENV_HEADER = 'X-GI-Instance'
export const ENV_LABEL: Record<GiEnv, string> = { production: 'Live', training: 'Practice' }

function readEnv(): GiEnv {
  try {
    return localStorage.getItem(ENV_KEY) === 'training' ? 'training' : 'production'
  } catch {
    return 'production'
  }
}

/** This page's environment. Never changes without a reload. */
export const CURRENT_ENV: GiEnv = readEnv()

export const isPractice = (): boolean => CURRENT_ENV === 'training'

/**
 * The Practice twin of a Live API base: the trailing `/api` segment becomes
 * `/training-api`. Works for the relative web base (`/api`), the Vite dev
 * proxy, and a native build's absolute `https://host/api` — so the installed
 * apps get the toggle for free (ruling Q9).
 */
export function practiceBase(liveBase: string): string {
  const b = (liveBase || '/api').replace(/\/+$/, '')
  return /\/api$/i.test(b) ? b.replace(/\/api$/i, '/training-api') : `${b}/training-api`
}

export function baseFor(env: GiEnv, liveBase: string): string {
  return env === 'training' ? practiceBase(liveBase) : liveBase
}

/** Live keeps `gi_token` byte-for-byte (the E2E harness and every existing
 * session read it); Practice gets its own key, so a stale token from one
 * environment is never sent to the other. */
export function tokenKeyFor(env: GiEnv): string {
  return env === 'training' ? 'gi_token@training' : 'gi_token'
}

/** IndexedDB name for the offline queue — see offline/queue.ts (vector V4). */
export function offlineDbName(env: GiEnv): string {
  return env === 'training' ? 'gi-offline@training' : 'gi-offline'
}

export function persistEnv(env: GiEnv): void {
  try {
    if (env === 'training') localStorage.setItem(ENV_KEY, env)
    else localStorage.removeItem(ENV_KEY)
  } catch { /* private mode — the choice just won't persist */ }
}
