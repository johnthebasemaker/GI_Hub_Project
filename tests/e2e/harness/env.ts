/**
 * Shared constants for the E2E harness. Everything runs on its OWN ports and
 * its OWN throwaway database so a developer's normal dev stack (:8000 / :5173 /
 * gihub) is never touched.
 */
import * as path from 'node:path'

export const ROOT = path.resolve(__dirname, '..', '..', '..') // repo root
export const PY = path.join(ROOT, '.venv', 'bin', 'python')

export const PG_HOST = process.env.E2E_PG_HOST ?? '127.0.0.1'
export const PG_PORT = process.env.E2E_PG_PORT ?? '5433'
export const PG_USER = process.env.E2E_PG_USER ?? 'postgres'
export const E2E_DB = process.env.E2E_DB ?? 'gihub_e2e_pw'

export const API_PORT = Number(process.env.E2E_API_PORT ?? 8010)
export const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 5183)
export const API_URL = `http://127.0.0.1:${API_PORT}`
export const WEB_URL = `http://127.0.0.1:${WEB_PORT}`

export const JWT_SECRET = 'ci-only-service-test-secret-key-32bytes-min'

export const SYNC_DB_URL = `postgresql://${PG_USER}@${PG_HOST}:${PG_PORT}/${E2E_DB}`
export const ASYNC_DB_URL = `postgresql+asyncpg://${PG_USER}@${PG_HOST}:${PG_PORT}/${E2E_DB}`

// Every seeded role user gets this password in global-setup (the clone's real
// bcrypt hashes are overwritten INSIDE the throwaway DB only).
export const E2E_PASSWORD = 'E2ePlaywright!2026'

export type Role = 'admin' | 'hod' | 'sk' | 'supervisor' | 'logistics'
export const USERS: Record<Role, string> = {
  admin: 'admin', // global admin
  hod: 'hod', // head_of_department @ CNCEC
  sk: 'worker', // store_keeper @ CNCEC
  supervisor: 'supervisor', // supervisor @ CNCEC
  logistics: 'Logistics', // logistics, global
}

export const RUNTIME_DIR = path.resolve(__dirname, '..', '.runtime')
export const AUTH_DIR = path.resolve(__dirname, '..', '.auth')
export const storageStatePath = (role: Role) => path.join(AUTH_DIR, `${role}.json`)

// The backend rate-limits logins per client IP (CF-Connecting-IP → X-Real-IP →
// peer). Direct API contexts stamp their own bucket so parallel specs never
// trip the 10/60 login limit.
export const apiHeaders = (bucket: string) => ({ 'X-Real-IP': `203.0.113.${bucket}` })
