# `tests/video_gen/` — the tutorial recorder (Phase 12 prototype)

Records a role tutorial as a screencast plus a `beats.json` timeline.
`tools/generate_tutorial.py` is the only thing that should invoke it.

```bash
.venv/bin/python tools/generate_tutorial.py            # one tutorial
.venv/bin/python tools/generate_tutorial.py --all      # the whole catalogue
.venv/bin/python tools/generate_tutorial.py --all --dry-run
```

⚠️ It records against the **synthetic** dataset by default
(`tools/make_tutorial_db.py`, ruling P12-0) on its own database
`gihub_tutorial_pw` at :8011/:5184 — not the gate's `gihub_e2e_pw` at
:8010/:5183, and not the real `gi_database.db`.

## What this is not

**It is not a test and it must never become a gate.** It lives under its own
Playwright config precisely so `cd tests/e2e && npm test` cannot pick it up: a
tutorial that renders badly is a tutorial to re-render, not a red build. It
asserts only enough to fail loudly on a recording that would be *silently*
wrong — a panel that never opened, an answer that never rendered.

## What it reuses, and why

| Reused from `tests/e2e/` | Why not a copy |
|---|---|
| `global-setup` / `global-teardown` | A recording must run against the throwaway `gihub_e2e_pw` on :8010/:5183, never a live database (**rule 15**). A second stack builder is a second place for that rule to be got wrong. |
| `setup/auth.setup.ts` | One login fixture. A recorder with its own would drift from the suite's. |
| `harness/env.ts` | Ports, users, storage-state paths — one source. |
| `node_modules` | Symlinked by the orchestrator. Same Playwright build as the gate, so a recording cannot be made in a different browser than the one the suite tests with. No second 300 MB install. |

## Running beside the E2E suite

Safe since slice 12a: the recorder sets `E2E_DB`, `E2E_API_PORT`,
`E2E_WEB_PORT` and `GI_DB_FILE` in the environment `tests/e2e/harness/env.ts`
already reads from, so it builds `gihub_tutorial_pw` on :8011/:5184 and never
touches the gate's database or ports. `--reuse-stack`
(`GI_VIDEO_REUSE_STACK=1`) still exists for batches — raise the stack once,
record many, tear down once — and the batch runner sets it automatically after
the first render.

⚠️ `--dataset e2e` opts back into the gate's stack and the REAL data. It is a
diagnostic; nothing recorded that way may be published.

## Files

| File | Job |
|---|---|
| `playwright.config.ts` | One worker, serial, long timeouts, no retries. |
| `stack.ts` / `stack-teardown.ts` | Wrap the E2E lifecycle; honour `GI_VIDEO_REUSE_STACK`. |
| `harness/record.ts` | Beats, the synthetic cursor, the redaction hook, the scripted AI lane, the recording context. |
| `tutorial.spec.ts` | **The ONE recorder for the whole catalogue.** It performs whatever `steps:` the tracked YAML declares. It replaced the prototype's hand-written `sample_tutorial.spec.ts`: ruling Q2 puts ~60 clips in the catalogue, and sixty bespoke spec files would be sixty places for the harness to drift — invisibly, because a tutorial recorded through a slightly different helper still produces a video, just a worse one. |
| `harness/steps.ts` | The step vocabulary: `goto`, `click`, `type`, `press`, `hover`, `scroll`, `expect_visible`, `expect_hidden`. Deliberately small, and every verb is one a viewer can SEE — no `evaluate`, no `waitForResponse`, no branch. A tutorial that needs a branch is two tutorials. An unknown verb is **refused, never skipped**. |

## The rule-14 lint has two halves, and the second one is the oracle

`frontend/scripts/nav_access_dump.mjs` is a MODEL of `canAccessPath`, used as a
fast pre-flight so `--dry-run` can refuse a bad script before a browser starts.
A second implementation of an access decision is exactly what this repository
distrusts — so `trackNavigation` records every path the browser actually landed
on, and the orchestrator refuses any render that visited a path the script did
not declare. `canAccessPath` fails closed by REDIRECTING, so a role walking
into a forbidden page lands somewhere undeclared and the run stops. No model
can drift past that.

## Three things in `harness/record.ts` that are load-bearing

1. **The context is created by hand**, not by `test.use({ video })`. Playwright
   starts recording when the *context* is created; through the fixture that
   happens before the test body and the spec never learns when. Beat alignment
   needs `t0` to be a number we hold.

2. **`recordVideo.size` does not scale the page.** Measured: a 1536×864
   viewport in a 1920×1080 canvas is drawn 1:1 at the top-left and leaves black
   bands over exactly 20% of each axis. `deviceScaleFactor` does not change it.
   The two are pinned equal here; the upscale to the delivery canvas happens
   once, in ffmpeg.

3. **The holds come from the audio, not from the spec.** A Playwright-driven UI
   is far faster than a person explaining it. Recorded with guessed pauses this
   tutorial overran all six beats — 40.2 s of speech over 19.6 s of video. The
   orchestrator renders the narration first, measures it, and passes per-beat
   hold times in the shot list.
