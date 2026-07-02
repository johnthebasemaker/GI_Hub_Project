import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { api, setAuthToken, TOKEN_KEY } from '../api/client'

export interface User {
  username: string
  role: string
  site_id: string
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
  const [user, setUser] = useState<User | null>(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
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
    const onUnauth = () => setUser(null)
    window.addEventListener('gi-unauthorized', onUnauth)
    return () => window.removeEventListener('gi-unauthorized', onUnauth)
  }, [])

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
