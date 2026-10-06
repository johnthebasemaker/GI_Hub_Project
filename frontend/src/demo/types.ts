/**
 * Phase 21f — the self-driving demo's script format (PROPOSED_PHASE21_PLAN §3).
 *
 * A script is DATA: a list of beats. Each beat says one sentence (spoken and
 * shown as a subtitle — the subtitle is the source of truth, so a muted laptop
 * still works) and optionally does one thing to the REAL page. Sentences are
 * written here and reviewed in a diff; a model never writes narration (P12-1 /
 * P12-3: a demo says the same thing every time). `{name}` placeholders are
 * filled from what is on screen at that moment (`do: 'read'`) or from the
 * script's own `vars()`.
 *
 * Targets are CSS selectors — mostly `data-testid`s, the same ones E2E uses,
 * so a renamed button breaks a test before it breaks a demo.
 */
export type Action =
  | 'point'      // spotlight the target, nothing else
  | 'click'      // element.click()
  | 'type'       // React's native value setter + an input event, a character at a time
  | 'select'     // open an AntD Select, type `value`, choose the first option containing it
  | 'attach'     // put a generated "DEMO slip" image into the file input inside the target
  | 'check'      // tick every unticked checkbox matching `target` inside each `within` match
  | 'read'       // vars[as] = the target's text (or value)
  | 'navigate'   // go to `value` (a path)
  | 'switch'     // sign out, sign in as the Practice account of role `value` (never admin)
  | 'wait'       // only wait for `until` / the target

export interface Beat {
  say: string
  do?: Action
  /** CSS selector. `tid('x')` builds `[data-testid="x"]`. */
  target?: string
  /** Only an element whose text contains this (placeholders filled). */
  text?: string
  /** Look inside the first visible `css` element whose text contains `hasText`. */
  within?: { css: string; hasText: string }
  value?: string
  as?: string
  /** After the action, wait until this selector is visible. */
  until?: string
  /** …or until this text is on the page. */
  untilText?: string
  /** ms to wait for the target / `until` (default 15 s). */
  timeout?: number
  /** A target that never shows is skipped, not a failure. */
  optional?: boolean
}

export interface DemoScript {
  id: string
  title: string
  /** One line for the chooser. */
  blurb: string
  /** Who may START it (it switches roles itself as it goes). */
  roles: string[]
  /** Fresh values per run — e.g. the DEMO- reference that tags its entries. */
  vars?: () => Record<string, string>
  beats: Beat[]
}

export const tid = (id: string) => `[data-testid="${id}"]`
