/**
 * Playwright config for the TUTORIAL RECORDER (Phase 12 prototype).
 *
 * Deliberately a SEPARATE config from `tests/e2e/playwright.config.ts`:
 *   · the gate must stay fast, headless and silent — recording is neither;
 *   · a recording runs one worker, serially, with animations ON and human
 *     pauses in it, all of which are the opposite of what a gate wants;
 *   · and nothing here may ever become a merge blocker. A video that renders
 *     badly is a video to re-render, not a red build.
 *
 * It REUSES the E2E suite's stack and its per-role storage states, so a
 * recording logs in exactly the way the gate does — into the throwaway
 * `gihub_e2e_pw`, never a live database (rule 15).
 *
 * Run it through `tools/generate_tutorial.py`; the raw invocation is
 *   cd tests/e2e && npx playwright test -c ../video_gen/playwright.config.ts
 */
import { defineConfig, devices } from '@playwright/test'
import * as path from 'node:path'
import { WEB_URL } from '../e2e/harness/env'

export default defineConfig({
  // tests/ — wide enough to reach the E2E suite's auth.setup.ts, which mints
  // the storage states. Duplicating that file here would give us a second
  // login fixture to keep in step with the first.
  testDir: path.resolve(__dirname, '..'),
  timeout: 240_000,          // a tutorial holds on frames; it is not a gate
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir: path.resolve(__dirname, '.results'),
  globalSetup: path.resolve(__dirname, 'stack.ts'),
  globalTeardown: path.resolve(__dirname, 'stack-teardown.ts'),
  use: {
    baseURL: WEB_URL,
    // ⚠️ PLAYWRIGHT'S `actionTimeout` DEFAULTS TO ZERO, WHICH MEANS NO TIMEOUT.
    // A `scrollIntoViewIfNeeded` or `hover` on a selector that does not exist
    // therefore waits until the whole TEST times out — the first bad selector
    // in this catalogue burned the full 300 s and reported only "Test timeout
    // exceeded", naming neither the step nor the selector. Bounded, it fails in
    // twenty seconds and says which locator it was waiting for.
    actionTimeout: 20_000,
    trace: 'off',
    screenshot: 'off',
    // ⚠️ NOT `video: 'on'`. The spec creates its own context so it owns t0 —
    // see harness/record.ts `newRecordingContext`.
    video: 'off',
  },
  projects: [
    { name: 'setup', testMatch: /e2e[/\\]setup[/\\]auth\.setup\.ts/ },
    {
      name: 'record',
      dependencies: ['setup'],
      testMatch: /video_gen[/\\].*\.spec\.ts/,
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
