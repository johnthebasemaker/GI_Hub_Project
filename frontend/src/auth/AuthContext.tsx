import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { App } from 'antd'
import { api, setAuthToken, TOKEN_KEY } from '../api/client'

export interface User {
  username: string
  role: string
  site_id: string
  warehouse_id: string
  label: string
  level: number
}

interface AuthState {
  user: User | null
  login: (username: string, password: string) => Promise<{ mfa: boolean; mfaToken?: string }>
  loginMfa: (mfaToken: string, code: string) => Promise<void>
  logout: () => void
}

const Ctx = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const { message } = App.useApp()
  const [user, setUser] = useState<User | null>(null)
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
        .then((r) => setUser(r.data))
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
    return () => window.removeEventListener('gi-session-expired', onExpired)
  }, [message])

  const login = async (username: string, password: string) => {
    const { data } = await api.post('/auth/login', { username, password })
    if (data.mfa_required) return { mfa: true, mfaToken: data.mfa_token as string }
    setAuthToken(data.access_token)
    setUser(data.user)
    return { mfa: false }
  }

  const loginMfa = async (mfaToken: string, code: string) => {
    const { data } = await api.post('/auth/login/2fa', { mfa_token: mfaToken, code })
    setAuthToken(data.access_token)
    setUser(data.user)
  }

  const logout = () => {
    // Revoke the server-side refresh session too (fire-and-forget).
    api.post('/auth/logout').catch(() => {})
    setAuthToken(null)
    setUser(null)
  }

  const value = useMemo(() => ({ user, login, loginMfa, logout }), [user])
  if (!ready) return null
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth() {
  const c = useContext(Ctx)
  if (!c) throw new Error('useAuth must be used within AuthProvider')
  return c
}
