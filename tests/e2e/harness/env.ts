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

// ── Rule 17: the Practice leg ───────────────────────────────────────────────
// A SECOND API process on its own database, exactly as in production. Built by
// the shipping tool (tools/practice_db.py build) from the synthetic dataset —
// never from gi_database.db — and walled off from THIS run's Live database.
// The name must end `_training` (rule 17's boot check); it is derived from
// E2E_DB so the tutorial recorder's stack (E2E_DB=gihub_tutorial_pw) gets its
// own and never collides with the gate's.
export const PRACTICE_API_PORT = Number(process.env.E2E_PRACTICE_PORT ?? API_PORT + 10)
export const PRACTICE_API_URL = `http://127.0.0.1:${PRACTICE_API_PORT}`
export const PRACTICE_DB = `${E2E_DB.replace(/_/g, '')}_training`
export const PRACTICE_SEED_DB = `${E2E_DB.replace(/_/g, '')}_seed_training`
export const PRACTICE_ROLE_DB_USER = 'gi_training'
export const PRACTICE_PASSWORD = 'Practice@2026'           // overlay default
export const PRACTICE_ADMIN_PASSWORD = 'E2ePracticeAdmin!2026'
// A DIFFERENT signing key from Live's — the primary wall between the two.
export const PRACTICE_JWT_SECRET = 'ci-only-practice-e2e-signing-key-32bytes-min'
export const PRACTICE_DB_URL =
  `postgresql://${PRACTICE_ROLE_DB_USER}@${PG_HOST}:${PG_PORT}/${PRACTICE_DB}`

export type Role =
  | 'admin' | 'hod' | 'sk' | 'supervisor' | 'logistics'
  | 'warehouse' | 'auditor'
  | 'qc' | 'qcwh' | 'qcnone' | 'qchod' | 'news'
export const USERS: Record<Role, string> = {
  admin: 'admin', // global admin
  hod: 'hod', // head_of_department @ CNCEC
  sk: 'worker', // store_keeper @ CNCEC
  supervisor: 'supervisor', // supervisor @ CNCEC
  logistics: 'Logistics', // logistics, global
  // The last two of the eight roles. Created by global-setup step 1f, for the
  // same reason as the QC accounts below: the legacy data has nobody to log in
  // as. Without them the RBAC matrix could only assert six of eight roles, and
  // the two it could not reach are precisely the two the 2026-08-12 pass
  // narrowed most (warehouse lost the roster and the PPE forecast; the auditor
  // lost the locator and a duplicate menu entry).
  warehouse: 'e2e_wh', // warehouse_user @ WH-01
  auditor: 'e2e_auditor', // auditor, global, view-only
  // QSEP. The legacy data has no QC accounts, so global-setup CREATES these
  // three inside the throwaway DB (step 1d) before the password reset runs.
  // All three exist because QC is the system's only DUAL-SCOPE role and each
  // axis is a different code path:
  qc: 'e2e_qc', // site-bound   @ CNCEC
  qcwh: 'e2e_qc_wh', // warehouse-bound @ WH-01
  qcnone: 'e2e_qc_none', // NEITHER — must see nothing, never everything
  // Phase 8 slice 8d. Cross-site by definition, so NO site and NO warehouse —
  // binding it to one would contradict the reason the role exists.
  qchod: 'e2e_qc_hod', // Head of Qualities, global, Surface Shield only
  // Phase 14d. The What's-new panel OPENS BY ITSELF for its audience, and the
  // specs run in parallel — so the announcement spec speaks to a user nobody
  // else signs in as: a QC at a site of its own (global-setup step 1g).
  news: 'e2e_news', // qc @ E2E-NEWS
}

// Site and warehouse the QC fixtures are pinned to. Specs assert against these
// rather than re-deriving them, so a change here cannot half-land.
export const QC_SITE = 'CNCEC'
export const QC_WAREHOUSE = 'WH-01'

export const RUNTIME_DIR = path.resolve(__dirname, '..', '.runtime')
/** Phase 22b — the Live API's Drive cache in the E2E stack (DN / MTC copies a spec places). */
export const DRIVE_CACHE_DIR = path.join(RUNTIME_DIR, 'drive-cache')
export const AUTH_DIR = path.resolve(__dirname, '..', '.auth')
export const storageStatePath = (role: Role) => path.join(AUTH_DIR, `${role}.json`)

// The backend rate-limits logins per client IP (CF-Connecting-IP → X-Real-IP →
// peer). Direct API contexts stamp their own bucket so parallel specs never
// trip the 10/60 login limit.
export const apiHeaders = (bucket: string) => ({ 'X-Real-IP': `203.0.113.${bucket}` })
