/**
 * Stack lifecycle for the recorder — a thin wrapper over the E2E suite's own
 * global setup/teardown, so a tutorial is recorded against the SAME isolated
 * stack the gate runs against (rule 15: `gihub_e2e_pw`, :8010 / :5183, never
 * the developer's :8000 / :5173 and never `gihub`).
 *
 * ⚠️ THE WRAPPER EXISTS FOR TWO ENVIRONMENT VARIABLES, AND THEY ARE
 * INDEPENDENT ON PURPOSE:
 *
 *   GI_VIDEO_REUSE_STACK=1  → skip SETUP  (something already raised it)
 *   GI_VIDEO_KEEP_STACK=1   → skip TEARDOWN (something else will drop it)
 *
 * A batch pays `cutover_migrate.py --wipe` once rather than once per video —
 * ~30 s of the ~70 s a single short tutorial costs. It does that by keeping the
 * stack up between renders.
 *
 * ⚠️ ONE FLAG WAS NOT ENOUGH, AND THE BUG ONLY APPEARED WITH A SECOND SCRIPT.
 * Slice 12b had `reuse` alone: the first render raised the stack AND TORE IT
 * DOWN on its way out, and every later render then attached to a database that
 * had just been dropped. With one tutorial in the catalogue nothing failed, so
 * the batch runner looked correct for exactly as long as there was nothing to
 * batch.
 *
 * ⚠️ AND THE HAZARD THAT COMES WITH IT: this drops and rebuilds `gihub_e2e_pw`.
 * Do not record while `cd tests/e2e && npm test` is running — they own the same
 * database and the same two ports, and the loser fails in a way that looks like
 * a flaky spec.
 */
import type { FullConfig } from '@playwright/test'

/**
 * ⚠️ The E2E lifecycle modules are TypeScript ESM compiled through Playwright's
 * own CJS loader, and the shape that comes back from `await import()` depends
 * on which side of that interop you land on: sometimes `mod.default` is the
 * function, sometimes it is the module record and the function is one level
 * further down. Reaching straight for `mod.default` gave
 * "TypeError: mod.default is not a function" — which reads as a broken setup
 * file rather than an interop wrapper, so unwrap it explicitly.
 */
type Lifecycle = (c: FullConfig) => Promise<void> | void

function unwrap(mod: unknown, name: string): Lifecycle {
  let fn: unknown = mod
  for (let i = 0; i < 3 && fn && typeof fn !== 'function'; i++) {
    fn = (fn as { default?: unknown }).default
  }
  if (typeof fn !== 'function') throw new Error(`${name} has no callable default export`)
  return fn as Lifecycle
}

export async function up(config: FullConfig): Promise<void> {
  if (process.env.GI_VIDEO_REUSE_STACK === '1') {
    console.log('[video] GI_VIDEO_REUSE_STACK=1 — attaching to the running stack')
    return
  }
  await refuseStaleStack()
  await unwrap(await import('../e2e/global-setup'), 'global-setup')(config)
}

/**
 * ⚠️ REFUSE TO BUILD ON TOP OF A STACK THAT IS ALREADY THERE, and this one was
 * paid for.
 *
 * `global-setup` finishes by polling the API and the web port until something
 * ANSWERS — which is the right check for a stack it just spawned and the wrong
 * one when a previous run left its servers behind. A stale uvicorn answers
 * instantly, Vite's `--strictPort` makes the NEW dev server die on a bound
 * port, and setup then reports "stack ready" and records against the old pair.
 * `pids.json` names processes that never started, so teardown kills nothing
 * and the next run inherits the same mess.
 *
 * That happened here: a keep-alive stack raised by hand was never dropped, and
 * three tutorials were recorded through servers nobody thought were running.
 * Nothing failed, which is the problem. Answering ports are now a refusal with
 * the fix in it, not a green light.
 */
async function refuseStaleStack(): Promise<void> {
  const ports = [
    ['API', process.env.E2E_API_PORT ?? '8010'],
    ['web', process.env.E2E_WEB_PORT ?? '5183'],
  ] as const
  for (const [label, port] of ports) {
    const alive = await fetch(`http://127.0.0.1:${port}/`, {
      signal: AbortSignal.timeout(1500),
    }).then(() => true).catch(() => false)
    if (alive) {
      throw new Error(
        `[video] something is already listening on the ${label} port ${port}.\n`
        + '  A previous recording left its stack up, or the E2E gate is running.\n'
        + '  Either re-run with --reuse-stack to record against it deliberately,\n'
        + `  or stop it first:  lsof -nP -iTCP:${port} -sTCP:LISTEN\n`
        + '  Refusing rather than recording against a stack nobody meant to use.')
    }
  }
}

export async function down(config: FullConfig): Promise<void> {
  if (process.env.GI_VIDEO_KEEP_STACK === '1') {
    console.log('[video] GI_VIDEO_KEEP_STACK=1 — leaving the stack up for the '
      + 'next render')
    return
  }
  await unwrap(await import('../e2e/global-teardown'), 'global-teardown')(config)
}

export default up
