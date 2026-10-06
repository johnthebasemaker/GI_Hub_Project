import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { App } from 'antd'
import { api, detectClientType, setAuthRole, setAuthToken, TOKEN_KEY } from '../api/client'
import { isReadOnly as roleIsReadOnly } from './readOnly'
import { isPractice } from '../api/environment'
import { clearIfDifferentUser, clearOnLogout } from './sessionState'

export interface User {
  username: string
  role: string
  site_id: string
  warehouse_id: string
  label: string
  level: number
}

/**
 * What a sign-in attempt resolved to. THREE outcomes, not two (Phase 10):
 *   · straight through            → { mfa: false }
 *   · 2FA challenge               → { mfa: true, mfaToken }
 *   · 2FA REQUIRED but not set up → { enroll: true, enrollToken, enforcedFrom }
 *
 * ⚠️ `enrollToken` IS NOT A SESSION. The server scopes it to `/auth/2fa/*`,
 * so it must never reach `setAuthToken` — storing it as a session would turn
 * "you must set up 2FA" into a way to skip it.
 */
export interface LoginOutcome {
  mfa: boolean
  mfaToken?: string
  enroll?: boolean
  enrollToken?: string
  enforcedFrom?: string | null
  /** Inside the grace window: the date the hard block starts. */
  mfaDue?: string | null
}

interface AuthState {
  user: User | null
  /** True for view-only accounts (Auditor) — see auth/readOnly.ts. */
  readOnly: boolean
  /** Set when this account's role will require 2FA from `mfaDue` onward. */
  mfaDue: string | null
  login: (username: string, password: string) => Promise<LoginOutcome>
  loginMfa: (mfaToken: string, code: string) => Promise<void>
  logout: () => void
}

const Ctx = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const { message } = App.useApp()
  const [user, setUser] = useState<User | null>(null)
  const [mfaDue, setMfaDue] = useState<string | null>(null)
  const [ready, setReady] = useState(false)
  const userRef = useRef<User | null>(null)
  userRef.current = user

  useEffect(() => {
    // Boot: a stale access token is fine — /auth/me 401s, the client silently
    // refreshes via the httpOnly cookie and replays, so the session survives
    // reloads (and 15-minute token expiry) without re-login.
    const t = localStorage.getItem(TOKEN_KEY)
    if (t) {
      api
        .get<User>('/auth/me')
        .then((r) => {
          // Boot is a sign-in too. A browser reopened on a restored tab never
          // passes through login(), so this is where a swapped user is caught
          // when the previous session was simply abandoned.
          clearIfDifferentUser(r.data.username)
          setUser(r.data)
        })
        .catch(() => setAuthToken(null))
        .finally(() => setReady(true))
    } else {
      setReady(true)
    }
    // Fired by the API client only when a silent refresh FAILED — the session
    // is really over. Show why, instead of a mystery kick to the login screen.
    const onExpired = () => {
      if (userRef.current) {
        message.warning('Your session has expired — please sign in again.', 6)
      }
      setUser(null)
    }
    window.addEventListener('gi-session-expired', onExpired)
    // Phase 21f — the self-driving demo "signs out and in" as another Practice
    // role: POST /practice/demo/switch (Practice process only, audited, never
    // admin) returns a real session, which is adopted here exactly as a login
    // result is. Ignored outside Practice.
    const onAdopt = (e: Event) => {
      const d = (e as CustomEvent<{ access_token?: string; user?: User }>).detail
      if (!isPractice() || !d?.access_token || !d.user) return
      setAuthToken(d.access_token)
      clearIfDifferentUser(d.user.username)
      setUser(d.user)
    }
    window.addEventListener('gi-session-adopt', onAdopt)
    return () => {
      window.removeEventListener('gi-session-expired', onExpired)
      window.removeEventListener('gi-session-adopt', onAdopt)
    }
  }, [message])

  const login = async (username: string, password: string) => {
    // client_type steers the refresh-family TTL server-side: 'native'
    // (Tauri/Capacitor installs) = 90 days, 'web' = 7. On an MFA login the
    // server carries it inside the signed mfa_token, so /login/2fa needs
    // nothing extra.
    const { data } = await api.post('/auth/login', {
      username, password, client_type: detectClientType(),
    })
    if (data.mfa_required) return { mfa: true, mfaToken: data.mfa_token as string }
    // The THIRD outcome (Phase 10): the role requires 2FA, the deadline has
    // passed, and this account has no authenticator. The token that comes back
    // is scope-limited to /auth/2fa/* — it is NOT a session, and storing it as
    // one would make "you must set up 2FA" a way to skip it.
    if (data.enrollment_required) {
      return {
        mfa: false,
        enroll: true,
        enrollToken: data.enroll_token as string,
        enforcedFrom: (data.enforced_from as string) ?? null,
      }
    }
    setAuthToken(data.access_token)
    // Before the new user's screens mount: if this is somebody else, drop the
    // previous person's UI state (see auth/sessionState.ts — offline caches are
    // explicitly NOT touched).
    clearIfDifferentUser(data.user.username)
    setUser(data.user)
    // Inside the grace window the sign-in succeeded and carries the deadline.
    // Surfaced so the app can warn; a silent grace period is one nobody uses,
    // and then the deadline lands as an outage.
    setMfaDue((data.mfa_enrollment_due as string) ?? null)
    return { mfa: false, mfaDue: (data.mfa_enrollment_due as string) ?? null }
  }

  const loginMfa = async (mfaToken: string, code: string) => {
    const { data } = await api.post('/auth/login/2fa', { mfa_token: mfaToken, code })
    setAuthToken(data.access_token)
    clearIfDifferentUser(data.user.username)
    setUser(data.user)
    setMfaDue(null)   // they have an authenticator; nothing is due
  }

  const logout = () => {
    // Revoke the server-side refresh session too (fire-and-forget).
    api.post('/auth/logout').catch(() => {})
    setAuthToken(null)
    // Hand the machine over clean: session UI state and the shareable
    // ?scenario= param go now, rather than waiting for the next sign-in to
    // notice the user changed. Form drafts survive — the same person signing
    // back in should still find what they were typing.
    clearOnLogout()
    setUser(null)
    setMfaDue(null)
  }

  // Keep the API client's copy of the role in step, so its request interceptor
  // can refuse a mutating call from a view-only account without importing
  // React state. Runs on every user change, including sign-out (null).
  useEffect(() => { setAuthRole(user?.role ?? null) }, [user])

  const value = useMemo(
    () => ({ user, readOnly: roleIsReadOnly(user), mfaDue, login, loginMfa, logout }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- login/loginMfa/logout
    // are stable closures over setState; `mfaDue` must be here or the grace
    // banner never appears (the memo would hand out the value from sign-in).
    [user, mfaDue],
  )
  if (!ready) return null
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth() {
  const c = useContext(Ctx)
  if (!c) throw new Error('useAuth must be used within AuthProvider')
  return c
}
