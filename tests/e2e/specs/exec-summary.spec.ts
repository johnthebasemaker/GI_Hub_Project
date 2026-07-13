/**
 * HOD Executive Summary: the tab renders live data and the server-rendered
 * PDF download really is a PDF (fpdf2 output, not a printed web page).
 */
import { test, expect } from '@playwright/test'
import * as fs from 'node:fs'
import { storageStatePath } from '../harness/env'

test.use({ storageState: storageStatePath('hod') })

test('executive summary renders and downloads a real PDF', async ({ page }) => {
  await page.goto('/hod/executive-summary')
  const pdfButton = page.getByRole('button', { name: /download pdf/i })
  await expect(pdfButton).toBeVisible()

  const [download] = await Promise.all([
    page.waitForEvent('download'),
    pdfButton.click(),
  ])
  expect(download.suggestedFilename()).toMatch(/executive_summary_.*\.pdf$/)

  const file = await download.path()
  const head = fs.readFileSync(file!).subarray(0, 5).toString('latin1')
  expect(head.startsWith('%PDF-')).toBe(true)
})
