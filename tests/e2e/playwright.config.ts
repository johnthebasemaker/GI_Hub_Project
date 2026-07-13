import { defineConfig, devices } from '@playwright/test'
import { WEB_URL } from './harness/env'

/**
 * GI Hub headless E2E. The whole stack (throwaway DB + hermetic backend +
 * Vite) is built by global-setup and torn down by global-teardown; the `setup`
 * project then mints one storageState per role so specs never log in through
 * the UI (except auth.spec.ts, which tests the login form itself).
 */
export default defineConfig({
  testDir: '.',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  workers: process.env.CI ? 2 : 4,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: '.report' }]],
  outputDir: '.results',
  globalSetup: './global-setup',
  globalTeardown: './global-teardown',
  use: {
    baseURL: WEB_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'setup', testMatch: /setup\/auth\.setup\.ts/ },
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
      dependencies: ['setup'],
      testMatch: /specs\/.*\.spec\.ts/,
    },
  ],
})
