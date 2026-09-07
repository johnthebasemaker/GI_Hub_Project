/**
 * tutorial.spec.ts — the ONE recorder for the whole catalogue.
 *
 * It performs whatever `steps:` the tracked YAML declares (see
 * `harness/steps.ts` for the vocabulary) and stamps a beat at each narration
 * point. It replaced `sample_tutorial.spec.ts`, which hard-coded the Hub
 * Assistant walk-through: ruling Q2 puts ~60 clips in the catalogue, and sixty
 * bespoke spec files would be sixty places for the harness to drift.
 *
 * ⚠️ THIS IS NOT A TEST AND MUST NEVER BECOME A GATE. It asserts only enough to
 * fail loudly on a recording that would be SILENTLY wrong — a page that never
 * loaded, a panel that never opened. A tutorial that renders badly is a
 * tutorial to re-render, not a red build. It is kept out of
 * `tests/e2e/playwright.config.ts` by living under its own config.
 *
 * Run: `.venv/bin/python tools/generate_tutorial.py [--all]`
 */
import { test } from '@playwright/test'
import * as path from 'node:path'
import { type Role, storageStatePath } from '../e2e/harness/env'
import {
  Beats, RENDER, VIDEO, installTutorialChrome, loadShotList,
  newRecordingContext, outDir, scriptAssistant, trackNavigation,
} from './harness/record'
import { runSteps, type Step } from './harness/steps'

const shot = loadShotList()

// Serial and alone. Recording is wall-clock work: a parallel worker stealing
// CPU shows up as dropped frames, which is a defect you can only see by
// watching the file.
test.describe.configure({ mode: 'serial' })

test(`record ${shot.tutorial_id}`, async ({ browser }) => {
  const dir = outDir()
  const raw = path.join(dir, 'raw')

  const context = await newRecordingContext(browser, storageStatePath(shot.role as Role), raw)
  await installTutorialChrome(context, shot)
  const beats = new Beats()
  const page = await context.newPage()
  trackNavigation(page, beats)
  await scriptAssistant(page, shot)

  await runSteps(page, shot, beats, shot.steps as Step[])

  // Grab the Video handle BEFORE the context closes — the reference stays
  // valid, `page.video()` afterwards does not.
  const video = page.video()
  if (!video) throw new Error('recordVideo produced no video handle')
  const total = beats.elapsedMs
  await context.close()

  const dest = path.join(dir, `${shot.tutorial_id}.webm`)
  await video.saveAs(dest)
  beats.write(path.join(dir, 'beats.json'), {
    tutorial_id: shot.tutorial_id,
    role: shot.role,
    language: shot.language,
    video: dest,
    size: VIDEO,
    css_viewport: RENDER.css,
    device_scale_factor: RENDER.dpr,
    approx_duration_ms: total,
  })
  console.log(`[video] ${dest}`)
})
