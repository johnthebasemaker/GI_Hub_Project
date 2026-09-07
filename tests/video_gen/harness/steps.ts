/**
 * tests/video_gen/harness/steps.ts — the STEP VOCABULARY.
 *
 * ⚠️ WHY A DECLARATIVE RUNNER RATHER THAN ONE SPEC PER TUTORIAL. Ruling Q2 puts
 * roughly SIXTY short clips in the catalogue. Sixty hand-written Playwright
 * files is sixty places for the recording harness to drift, and the drift would
 * be invisible — a tutorial recorded through a slightly different helper still
 * produces a video, just a worse one. One executor, and the steps live in the
 * same reviewed YAML as the narration they illustrate, next to the sentence
 * that describes them.
 *
 * The vocabulary is deliberately SMALL. Every verb here is one a viewer can see
 * happening; there is no `evaluate`, no `waitForResponse`, no conditional. A
 * tutorial that needs a branch is two tutorials, and a tutorial that needs to
 * reach into the page is teaching something the UI does not show.
 *
 *   goto           path            navigate (the only verb that changes route)
 *   click          selector        glide the cursor there, then click
 *   type           selector, text  click, then type a character at a time
 *   press          key             a keyboard key on the focused element
 *   expect_visible selector | text
 *   expect_hidden  selector
 *   hover          selector
 *   scroll         selector        bring it into view
 *
 * Every step MAY carry `beat:`; a step that has one is a narration point and is
 * stamped the moment it completes. A step without one is stagecraft.
 */
import { expect, type Page } from '@playwright/test'
import { Beats, glide, type ShotList } from './record'

export interface Step {
  beat?: string
  note?: string
  do: string
  path?: string
  selector?: string
  text?: string
  key?: string
  /** Extra settle time BEFORE the beat is stamped (ms). Rare — for a chart. */
  settle?: number
}

/**
 * Resolve a step's target. Three forms:
 *
 *   `label=Material (SAP Code)`  the form control that label names
 *   `text=Upload a filled form`  the first element containing that string
 *   anything else                a CSS selector
 *
 * ⚠️ `label=` EXISTS BECAUSE ANT DESIGN'S SELECT HAS NO PLACEHOLDER ATTRIBUTE.
 * It renders the placeholder as a sibling `<span>`, so
 * `input[placeholder="Search material"]` matches nothing at all — the return
 * tutorial failed on exactly that, reporting only "element(s) not found". The
 * label is the thing a person reads to find the field, so it is also the
 * honest way for a tutorial to name it.
 *
 * ⚠️ AND EVERYTHING IS `.first()`. Playwright's strict mode refuses an
 * ambiguous locator, and prose repeats button names: `Download PDF` is both
 * the button and the sentence underneath explaining what the button does. For
 * a GATE that ambiguity is a bug worth failing on; for a RECORDING it is a
 * paragraph doing its job, and the first match is the control.
 */
function locate(page: Page, step: Step) {
  const sel = step.selector ?? ''
  if (sel.startsWith('label=')) return page.getByLabel(sel.slice(6)).first()
  if (sel.startsWith('text=')) {
    return page.getByText(new RegExp(escapeRe(sel.slice(5)))).first()
  }
  return page.locator(sel).first()
}

/** Glide the synthetic cursor to a locator's centre, once it is visible. */
async function glideTo(page: Page, el: ReturnType<typeof locate>): Promise<void> {
  await expect(el).toBeVisible()
  const box = await el.boundingBox()
  if (box) await glide(page, box.x + box.width / 2, box.y + box.height / 2)
}

export function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export async function runSteps(page: Page, shot: ShotList, beats: Beats,
                               steps: Step[]): Promise<void> {
  for (const step of steps) {
    switch (step.do) {
      case 'goto':
        await page.goto(step.path ?? '/')
        // Park the pointer centre-screen so the synthetic cursor exists on the
        // first frame instead of appearing from nowhere at the first click.
        await page.mouse.move(768, 432)
        await page.waitForTimeout(500)
        break

      case 'click':
        await glideTo(page, locate(page, step))
        await locate(page, step).click()
        break

      case 'type': {
        await glideTo(page, locate(page, step))
        await locate(page, step).click()
        // ⚠️ A character at a time, on purpose. A field that fills instantly
        // reads as a screenshot and the viewer stops believing the recording
        // is the app.
        await locate(page, step).pressSequentially(step.text ?? '', { delay: 55 })
        break
      }

      case 'press':
        await page.keyboard.press(step.key ?? 'Enter')
        await page.waitForTimeout(300)
        break

      case 'expect_visible':
        await expect(locate(page, step)).toBeVisible()
        break

      case 'expect_hidden':
        await expect(locate(page, step)).toBeHidden()
        break

      case 'hover':
        await glideTo(page, locate(page, step))
        break

      case 'scroll':
        await locate(page, step).scrollIntoViewIfNeeded()
        await page.waitForTimeout(400)
        break

      default:
        // ⚠️ Refused, never ignored. A typo'd verb that silently did nothing
        // would produce a video missing the step it was written for, and
        // nothing anywhere would say so.
        throw new Error(`unknown step verb ${JSON.stringify(step.do)} — refused rather than skipped`)
    }

    if (step.settle) await page.waitForTimeout(step.settle)
    if (step.beat) {
      beats.mark(step.beat, step.note ?? '')
      await beats.hold(page, shot, step.beat, 2000)
    }
  }
}
