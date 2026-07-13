/**
 * global-teardown — kill the backend + Vite process groups spawned by
 * global-setup, then drop the throwaway database. Leaves the machine exactly
 * as it was found.
 */
import { execFileSync } from 'node:child_process'
import * as fs from 'node:fs'
import * as path from 'node:path'
import { E2E_DB, PG_HOST, PG_PORT, PG_USER, RUNTIME_DIR } from './harness/env'

export default async function globalTeardown() {
  const pidsFile = path.join(RUNTIME_DIR, 'pids.json')
  if (fs.existsSync(pidsFile)) {
    const pids = JSON.parse(fs.readFileSync(pidsFile, 'utf-8')) as { api?: number; web?: number }
    for (const [name, pid] of Object.entries(pids)) {
      if (!pid) continue
      try {
        process.kill(-pid, 'SIGTERM') // negative pid ⇒ whole process group
        console.log(`[e2e] stopped ${name} (pgid ${pid})`)
      } catch { /* already gone */ }
    }
    fs.rmSync(pidsFile)
  }
  // brief grace so Postgres sees the backend's connections close
  await new Promise((r) => setTimeout(r, 1500))
  try {
    execFileSync('psql', [
      '-h', PG_HOST, '-p', PG_PORT, '-U', PG_USER, '-d', 'postgres', '-tAc',
      `DROP DATABASE IF EXISTS ${E2E_DB} WITH (FORCE)`,
    ], { stdio: 'inherit' })
    console.log(`[e2e] dropped ${E2E_DB}`)
  } catch (e) {
    console.warn(`[e2e] could not drop ${E2E_DB}: ${e}`)
  }
}
