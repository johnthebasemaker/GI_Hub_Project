/**
 * Phase C — "Ask your data" card (POST /ai/query, template lane). As an HOD
 * (site-scoped, level 2): the card renders on the dashboard, an example
 * question routes through the instant template lane, and the executed SQL is
 * inspectable. No Ollama needed — the template lane is deterministic.
 */
import { test, expect } from '@playwright/test'
import { storageStatePath } from '../harness/env'

test.use({ storageState: storageStatePath('hod') })

test('HOD asks a question → instant template answer with inspectable SQL', async ({ page }) => {
  await page.goto('/')
  const card = page.locator('.ant-card', { hasText: 'Ask your data' })
  await expect(card).toBeVisible()

  await card.getByText('How many issues in the last 30 days?').click()
  await expect(card.getByText('instant')).toBeVisible()
  await expect(card.getByText(/issue entries \(last 30 days\)/)).toBeVisible()

  await card.getByText(/Show SQL/).click()
  await expect(card.getByText(/SELECT COUNT\(\*\)/i)).toBeVisible()
})
