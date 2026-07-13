/**
 * global-setup — builds the ENTIRE isolated stack before any spec runs:
 *
 *   1. (re)create the throwaway Postgres DB `gihub_e2e_pw` on the local :5433
 *      cluster and load it with the REAL legacy data via the production
 *      cutover script (tools/migration/cutover_migrate.py --wipe).
 *   2. overwrite the role users' bcrypt hashes with a known E2E password —
 *      inside the throwaway DB only.
 *   3. spawn a hermetic uvicorn on :8010 (GI_DOTENV=0 ⇒ WhatsApp/SMTP disabled,
 *      GI_SCHEDULER=0 ⇒ no digest loop) pointed at the throwaway DB.
 *   4. spawn a Vite dev server on :5183 whose /api proxy targets :8010
 *      (VITE_API_PROXY, see frontend/vite.config.ts).
 *
 * PIDs are persisted to .runtime/pids.json; global-teardown kills both process
 * groups and DROPs the database. Nothing here touches gi_database.db, the
 * `gihub` mirror, or a developer's own :8000/:5173 servers.
 */
import { execFileSync, spawn } from 'node:child_process'
import * as fs from 'node:fs'
import * as path from 'node:path'
import {
  API_PORT, API_URL, ASYNC_DB_URL, AUTH_DIR, E2E_DB, E2E_PASSWORD, JWT_SECRET,
  PG_HOST, PG_PORT, PG_USER, PY, ROOT, RUNTIME_DIR, SYNC_DB_URL, USERS,
  WEB_PORT, WEB_URL,
} from './harness/env'

function psql(sql: string, db = 'postgres'): string {
  return execFileSync(
    'psql', ['-h', PG_HOST, '-p', PG_PORT, '-U', PG_USER, '-d', db, '-tAc', sql],
    { encoding: 'utf-8' },
  ).trim()
}

async function waitFor(url: string, label: string, timeoutMs = 90_000): Promise<void> {
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutMs) {
    try {
      const r = await fetch(url)
      if (r.ok) return
    } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error(`${label} did not become ready at ${url} within ${timeoutMs / 1000}s`)
}

export default async function globalSetup() {
  fs.mkdirSync(RUNTIME_DIR, { recursive: true })
  fs.mkdirSync(AUTH_DIR, { recursive: true })

  // ── 1. throwaway DB, loaded with the real legacy data ────────────────────
  console.log(`[e2e] creating ${E2E_DB} and loading it via cutover_migrate.py …`)
  if (psql(`SELECT 1 FROM pg_database WHERE datname='${E2E_DB}'`) !== '1') {
    psql(`CREATE DATABASE ${E2E_DB}`)
  }
  execFileSync(
    PY, [path.join(ROOT, 'tools', 'migration', 'cutover_migrate.py'),
      '--wipe', '--target', SYNC_DB_URL],
    { cwd: ROOT, stdio: ['ignore', 'ignore', 'inherit'] },
  )

  // ── 1b. relax the entry-document gate for the functional specs ──────────
  // (require_entry_documents defaults ON in production; the entry-docs spec
  // flips it on itself to test the gate.)
  psql("INSERT INTO app_settings (key, value) VALUES ('require_entry_documents','0') "
       + "ON CONFLICT (key) DO UPDATE SET value='0'", E2E_DB)

  // ── 2. known passwords for the role users (throwaway DB only) ────────────
  const resetScript = [
    'import bcrypt, sys',
    'from sqlalchemy import create_engine, text',
    `e = create_engine(${JSON.stringify(SYNC_DB_URL)})`,
    `h = bcrypt.hashpw(${JSON.stringify(E2E_PASSWORD)}.encode(), bcrypt.gensalt(rounds=4)).decode()`,
    'with e.begin() as c:',
    `    n = c.execute(text("UPDATE users SET password_hash=:h WHERE username = ANY(:u)"),`,
    `                  {"h": h, "u": ${JSON.stringify(Object.values(USERS))}}).rowcount`,
    'print(f"[e2e] reset {n} user password(s)")',
    `assert n == ${Object.values(USERS).length}, f"expected ${Object.values(USERS).length} users, matched {n}"`,
  ].join('\n')
  execFileSync(PY, ['-c', resetScript], { cwd: ROOT, stdio: 'inherit' })

  // ── 3. hermetic backend ───────────────────────────────────────────────────
  console.log(`[e2e] starting uvicorn on :${API_PORT} …`)
  const apiLog = fs.openSync(path.join(RUNTIME_DIR, 'api.log'), 'w')
  const api = spawn(
    PY, ['-m', 'uvicorn', 'backend.api.main:app', '--host', '127.0.0.1', '--port', String(API_PORT)],
    {
      cwd: ROOT,
      detached: true,
      stdio: ['ignore', apiLog, apiLog],
      env: {
        ...process.env,
        GI_DOTENV: '0',
        GI_SCHEDULER: '0',
        JWT_SECRET,
        DATABASE_URL: ASYNC_DB_URL,
      },
    },
  )
  api.unref()

  // ── 4. Vite dev server proxying /api → the hermetic backend ──────────────
  console.log(`[e2e] starting Vite on :${WEB_PORT} …`)
  const webLog = fs.openSync(path.join(RUNTIME_DIR, 'web.log'), 'w')
  const web = spawn(
    'npm', ['run', 'dev', '--', '--host', '127.0.0.1', '--port', String(WEB_PORT), '--strictPort'],
    {
      cwd: path.join(ROOT, 'frontend'),
      detached: true,
      stdio: ['ignore', webLog, webLog],
      env: { ...process.env, VITE_API_PROXY: API_URL, BROWSER: 'none' },
    },
  )
  web.unref()

  fs.writeFileSync(
    path.join(RUNTIME_DIR, 'pids.json'),
    JSON.stringify({ api: api.pid, web: web.pid }, null, 2),
  )

  await waitFor(`${API_URL}/health`, 'backend')
  await waitFor(WEB_URL, 'frontend')
  console.log('[e2e] stack ready — backend :%d, frontend :%d, db %s', API_PORT, WEB_PORT, E2E_DB)
}
