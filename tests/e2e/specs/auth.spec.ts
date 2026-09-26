/**
 * UI login flow — the one spec that exercises the login FORM instead of a
 * pre-minted storageState. Matrix rows: C1 (login), C2 (bad creds), C3 (logout).
 */
import { test, expect } from '@playwright/test'
import { E2E_PASSWORD, USERS } from '../harness/env'

test.describe('auth', () => {
  test('valid login lands on the dashboard, sign-out returns to login', async ({ page }) => {
    await page.goto('/')
    await page.getByPlaceholder('Username').fill(USERS.admin)
    await page.getByPlaceholder('Password').fill(E2E_PASSWORD)
    await page.getByRole('button', { name: /sign in/i }).click()

    await expect(page.getByText('Dashboard').first()).toBeVisible()
    await expect(page.getByText(`Admin · ${USERS.admin}`)).toBeVisible()

    await page.getByRole('button', { name: /sign out/i }).click()
    await expect(page.getByPlaceholder('Username')).toBeVisible()
  })

  // 2026-09-26: signing in while the address bar said /login (a bookmark, or
  // the URL left behind) used to leave a BLANK page — no route matched once
  // signed in. The layout's catch-all now lets its guard send you home.
  test('signing in at /login lands on your home page, not a blank screen', async ({ page }) => {
    await page.goto('/login')
    await page.getByPlaceholder('Username').fill(USERS.admin)
    await page.getByPlaceholder('Password').fill(E2E_PASSWORD)
    await page.getByRole('button', { name: /sign in/i }).click()
    await expect(page.getByText('Dashboard').first()).toBeVisible()
    await expect(page).not.toHaveURL(/\/login$/)
    // …and an unknown address while signed in goes home too
    await page.goto('/no-such-page')
    await expect(page.getByText('Dashboard').first()).toBeVisible()
    await expect(page).not.toHaveURL(/no-such-page/)
  })

  test('wrong password stays on the login screen', async ({ page }) => {
    await page.goto('/')
    await page.getByPlaceholder('Username').fill(USERS.admin)
    await page.getByPlaceholder('Password').fill('definitely-wrong')
    await page.getByRole('button', { name: /sign in/i }).click()

    // still on the login form, no session created
    await expect(page.getByPlaceholder('Username')).toBeVisible()
    await expect(page.getByText('Dashboard')).toHaveCount(0)
  })
})
