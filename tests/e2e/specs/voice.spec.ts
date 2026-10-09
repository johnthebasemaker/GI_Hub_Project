/**
 * Phase 23e — voice in and out (rulings Q23-10..12).
 *
 * Chromium plays a WAV file as the microphone (`--use-file-for-fake-audio-
 * capture`). On a Mac the file is spoken by `say`; Whistle on the API turns it
 * back into words, which must land IN THE BOX — never sent by themselves.
 * Where the server has no voice model (CI: models/stt is not in git), the
 * supported state is that the microphone is HIDDEN, and that is what is
 * asserted instead — a different assertion, not a skip (rule 16).
 */
import { execFileSync } from 'node:child_process'
import * as fs from 'node:fs'
import * as path from 'node:path'
import { test, expect } from '@playwright/test'
import { RUNTIME_DIR, storageStatePath } from '../harness/env'

const WAV = path.join(RUNTIME_DIR, 'voice-tyvek.wav')
let haveWav = false
try {
  fs.mkdirSync(RUNTIME_DIR, { recursive: true })
  execFileSync('say', ['-o', WAV, '--data-format=LEI16@16000', 'Show me the Tyvek coverall stock'])
  haveWav = fs.existsSync(WAV)
} catch { haveWav = false }

test.use({
  launchOptions: {
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
      ...(haveWav ? [`--use-file-for-fake-audio-capture=${WAV}`] : [])],
  },
})

test('23e: dictating to the Hub Assistant puts the words in the box (or the mic is hidden)', async ({ browser }) => {
  // Chromium's fake microphone loops the file and can hand over up to 30 s
  // of it; Whistle reads that in well under a second, the rest is the page
  test.setTimeout(120_000)
  const ctx = await browser.newContext({ storageState: storageStatePath('sk'), permissions: ['microphone'] })
  const page = await ctx.newPage()
  page.on('console', (m) => { if (process.env.VOICE_DEBUG) console.log('[console]', m.type(), m.text()) })
  page.on('pageerror', (e) => { if (process.env.VOICE_DEBUG) console.log('[pageerror]', e.message) })
  page.on('response', async (r) => {
    if (process.env.VOICE_DEBUG && r.url().includes('/ai/stt') && r.request().method() === 'POST') {
      console.log('[stt]', r.status(), await r.text())
    }
  })
  try {
    await page.goto('/')
    await page.getByRole('button', { name: 'Open Hub Assistant' }).click()
    const mic = page.getByTestId('assistant-mic')
    // ask the API directly what it says about voice
    const status = await page.evaluate(async () => {
      const mod = await import('/src/api/client.ts')
      return (await mod.api.get('/ai/stt/status')).data as { available: boolean }
    })
    if (!status.available || !haveWav) {
      await expect(mic).toHaveCount(0)
      return
    }
    await expect(mic).toBeVisible({ timeout: 15_000 })
    if (process.env.VOICE_DEBUG) {
      console.log('[gum]', await page.evaluate(async () => {
        try {
          const s = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } })
          const r = new MediaRecorder(s)
          return `ok tracks=${s.getAudioTracks().length} mime=${r.mimeType}`
        } catch (e) { return `ERR ${(e as Error).name}: ${(e as Error).message}` }
      }))
    }
    await mic.click()
    await expect(mic).toHaveText(/[2-9]s|[1-3]\ds/, { timeout: 15_000 })
    if (await mic.isEnabled()) await mic.click()
    const box = page.getByPlaceholder('Ask the manual…')
    // the words land in the box (how well a brand name is heard is Whistle's;
    // it has come back as Tyvek, "tie that" and "Tibet" on this fake microphone)
    await expect(box).toHaveValue(/cover ?all stock/i, { timeout: 60_000 })
    // the words are in the box — nothing was sent by speaking
    await expect(page.getByText('Thinking…')).toHaveCount(0)
  } finally {
    await ctx.close()
  }
})

test('23e: the top bar reads the page aloud and dictates into the last text box', async ({ browser }) => {
  test.setTimeout(120_000)
  const ctx = await browser.newContext({ storageState: storageStatePath('hod'), permissions: ['microphone'] })
  const page = await ctx.newPage()
  try {
    await page.goto('/requests-pending')
    await expect(page.getByTestId('voice-bar')).toBeAttached({ timeout: 20_000 })
    // a 1280 px window: the controls sit behind one button
    await page.getByTestId('voice-menu').click()
    const bar = page.locator('.ant-popover')
    await expect(bar.getByTestId('read-page')).toBeVisible()
    const status = await page.evaluate(async () => {
      const mod = await import('/src/api/client.ts')
      return (await mod.api.get('/ai/stt/status')).data as { available: boolean }
    })
    if (!status.available || !haveWav) {
      await expect(bar.getByTestId('dictate')).toHaveCount(0)
      return
    }
    const search = page.getByPlaceholder('Item, SAP, code or file')
    await search.click()
    await page.getByTestId('voice-menu').click()   // the click into the box closed it
    const dictate = bar.getByTestId('dictate')
    await dictate.click()
    await expect(dictate).toHaveText(/[2-9]s|[1-3]\ds/, { timeout: 15_000 })
    if (await dictate.isEnabled()) await dictate.click()
    // what is pinned here is the INSERTION into the box you were in; how well
    // a brand name is heard is Whistle's, measured in tools/stt_eval
    await expect(search).toHaveValue(/cover ?all stock/i, { timeout: 60_000 })
  } finally {
    await ctx.close()
  }
})
