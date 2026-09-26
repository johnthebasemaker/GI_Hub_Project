/**
 * Phase 14a — Surface Shield quantities show their BASE figure beside the pack
 * count, base first ("189 KG · 21 Each"), and the factor comes from the server.
 * global-setup gives SAP 1045 (BC 3004, a 9 kg can) its Unit Size.
 */
import { test, expect } from '@playwright/test'
import { apiAs } from '../harness/api'
import { storageStatePath } from '../harness/env'

test.use({ storageState: storageStatePath('sk') })

test('the factor map is served by the API, and the material card shows KG first', async ({ page }) => {
  const sk = await apiAs('sk', '14')
  const map = (await (await sk.get('/meta/unit-sizes')).json()) as
    { items: Record<string, { factor: number | null; base_uom: string | null }> }
  expect(map.items['1045']?.factor).toBe(9)
  expect(map.items['1045']?.base_uom).toBe('KG')
  await sk.dispose()

  await page.goto('/stock/material/1045')
  await expect(page.getByText(/\d[\d.,]* KG · \d[\d.,]* Each/)).toBeVisible({ timeout: 20_000 })
})
