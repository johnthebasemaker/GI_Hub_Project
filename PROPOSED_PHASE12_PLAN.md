# PROPOSED PHASE 12 PLAN — Automated Role-Based Video Tutorials

> **Status: APPROVED. Q1–Q5 and Q11 answered by the operator 2026-09-06 and
> now LOCKED (§9). ALL FIVE SLICES ARE MERGED — 12a, 12b, 12c and 12f;
> only 12d (HeyGen, needs a key) and 12e (object storage, waits for Hetzner)
> remain.** ⚠️ **Q7 was answered on 2026-09-06: narration egress is SIGNED
> OFF**, so 12d waits on a key rather than on a decision. No application code
> was changed by this plan itself.
> Branches `chore/phase12-prototype` → `feat/phase12-tutorial-dataset` →
> `feat/phase12-recorder` → `feat/phase12-scripts` →
> `feat/phase12-deeplinks`, written 2026-09-06 against `main` @ `2e95f9d`,
> after reading `PROJECT_HANDOVER.md`, `SESSION_HANDOVER.md`, `.claude/RULES.md`,
> `docs/ARCHITECTURE.md`, `REPO_MAP.md`, `USER_MANUAL.md` (§ index),
> `tests/e2e/**`, `backend/api/training.py` and the slice-10b migration.
>
> **The one-line verdict:** **pre-render, in a batch, into the training hub
> Phase 10 already built** — never on-the-fly. And **the video is not the
> sensitive artefact; the screencast is.** HeyGen only ever receives narration
> text a person reviewed in a diff; the screencast, which is the half that can
> carry real employee names and real stock, never leaves the machine.
>
> ⚠️ **The prototype found a problem the brief did not anticipate, and it is the
> most important sentence in this document.** `tests/e2e/global-setup.ts` loads
> the throwaway database from the **real** `gi_database.db` via
> `tools/migration/cutover_migrate.py`. So a tutorial recorded today is a
> recording of live proprietary data, and the risk is not the HeyGen API — it
> is the MP4 itself, which is a file people forward. See §3.2. **Nothing may be
> published until a synthetic tutorial dataset exists.**

---

## 0. Contents

| § | Topic | Verdict in one line |
|---|---|---|
| [1](#1-what-already-exists) | What already exists | Phase 10 built the shelf: `training_modules` / `training_assets` / `training_compliance`, four languages, a soft gate and a compliance record. Phase 12 fills it |
| [2](#2-question-1--generation-strategy) | **Q1 — generation strategy** | **PRE-RENDER.** On-the-fly is 66 s per answer, costs per view, and — decisively — **cannot be evidence**, because P10-6 keys compliance on a version an on-demand video does not have |
| [3](#3-question-2--data-privacy) | **Q2 — data privacy** | Five rulings, `P12-1`…`P12-5`. The boundary is a **provenance whitelist**, not a scrubber. It refused its own first payload |
| [4](#4-question-3--the-tooling-pipeline) | **Q3 — tooling** | **ffmpeg CLI via `subprocess`. Reject `ffmpeg-python`.** Three measured facts about this machine changed the design |
| [5](#5-the-prototype-what-was-built-and-what-it-measured) | The prototype | Built, run, four defects found and fixed — three of which were only visible by watching the output |
| [6](#6-scaling-to-the-whole-catalogue) | Scaling | One screencast per tutorial, N language cuts by **freeze-padding at beat boundaries**. The avatar is cached by narration hash, so a UI change costs **zero** HeyGen credits |
| [7](#7-sequencing--six-slices) | Sequencing | 6 slices. **12a is blocking and is not the video work** |
| [8](#8-risks-and-what-i-am-not-confident-about) | Risks | The HeyGen path is unverified; `say` is macOS-only; beat `t0` is asserted, not proved |
| [9](#9-clarifying-questions) | **Questions** | 14 questions. **All the blocking ones are answered and locked** — Q1 (the dataset), Q2, Q3, Q4 (what a re-render does to compliance), Q5, Q11, and Q7 (narration egress, signed off 2026-09-06) |

---

## 1. What already exists

**This is not a greenfield phase.** Slice 10b
(`20260903_0900_e7f2a4c916b8`) already shipped the entire delivery half, and
its own migration says what is missing:

> *"No `training_assets` row is seeded: the videos are produced externally and
> an asset row pointing at a URI that does not exist yet would render a broken
> player."*

**Phase 12 is that external production.** Concretely, already in `main`:

| Thing | Where | State |
|---|---|---|
| `training_modules` (`module_key`, `version`, `required_roles`, `gates_feature`) | slice 10b | ✅ one row seeded (`ocr_workflow_v1`) |
| `training_assets` (`language`, `storage_uri`, **`captions_uri`**, `duration_s`) | slice 10b | ✅ empty |
| `training_compliance` (`(username, module_id, module_version)`) | slice 10b | ✅ live |
| `POST /training/assets` — admin-only publish | `backend/api/training.py:343` | ✅ |
| Four languages `en · ta · ta-Latn · ar` — *"Tanglish … which is what people actually speak on site and what the avatar videos are recorded in"* | `training.LANGUAGES` | ✅ **the schema already assumed avatar videos** |
| `COMPLETE_AT = 0.90` watched-fraction rule | `training.py:60` | ✅ |
| The soft gate + "Watch Later" deferral counter | `TrainingGate.tsx` | ✅ |

Two consequences worth stating plainly:

* **`captions_uri` is already a column.** Emitting a WebVTT alongside every
  render is free — the beats file already holds every cue's start time. It is
  in slice 12b rather than as a "nice to have".
* **The language list was written for this phase.** Nobody has to be persuaded
  that Tanglish matters; the schema comment already argues it.

---

## 2. Question 1 — generation strategy

### 2.1 Verdict: **pre-render in a batch. On-the-fly is rejected.**

Not close. Five arguments, in ascending order of how hard they are to answer.

**(a) Latency — measured, not estimated.** The prototype's single 47.6-second
tutorial cost, end to end on this machine:

| Stage | Cold | Warm (`--reuse-stack`) |
|---|---|---|
| Stack build (`cutover_migrate.py --wipe`, uvicorn, Vite, Vite warm) | ~28 s | 0 s |
| Pass A — narration render + measure | 4.1 s | 4.1 s |
| Pass B — Playwright screencast | **53.2 s** | 53.2 s |
| Avatar + overlays | 3.6 s | 3.6 s |
| ffmpeg composite (1080p, CRF 20, `preset medium`) | 8.9 s | 8.9 s |
| **Total** | **≈ 98 s** | **≈ 70 s** |

And that excludes HeyGen, which is a queued cloud render measured in minutes,
not seconds. A user who clicks *"show me how"* is not waiting 70 seconds, and
they should not have to: **the Hub Assistant already answers that user in
seconds and is the right surface for it.**

**(b) The screencast is wall-clock-bound and cannot be made faster.** Pass B is
76% of the run, and it is 76% *by design* — the holds are the narration's
length (§4.3). Making it fast means making the tutorial unwatchable. There is
no optimisation here, only a different product.

**(c) Cost scales with views, not with content.** A pre-rendered catalogue is a
fixed HeyGen bill paid once per content change. On-the-fly pays per view, the
same question costs twice when two people ask it, and the bill is unbounded and
correlates with exactly the thing you want to encourage. This is P11-8's
argument transplanted: **a tutorial about how the UI works is not live data.**
It changes when the UI changes, which is a *deploy*, not a request — so the
invalidation key is the release, and a cache keyed on the release is just a
pre-render with extra steps.

**(d) On-the-fly needs a browser on the API box, and there are four of it.**
Every render is a headless Chromium plus a database plus a Vite server. The
standing warning applies without modification: *"`uvicorn --workers 4` has
produced three production bugs … if you add anything that runs on a timer or
holds state in memory, assume four of it."* Four concurrent Chromium instances
on the box that also holds the one warm Ollama model is not a capacity
question, it is an outage.

**(e) ⚠️ THE DECIDING ONE: an on-demand video cannot be evidence.** Ruling
**P10-6** puts `module_version` in the compliance unique key precisely so that
*"re-recording a tutorial because the workflow changed would leave everybody
certified against a video they have never seen — worse than no record at all,
because it looks like evidence."* A video generated at request time has no
version, no hash and no second viewer who saw the same thing. It can never be
produced to an auditor. The training hub's entire purpose is a record you can
produce, so on-the-fly is not a cheaper way of doing this job — **it is a
different job that does not do this one at all.**

### 2.2 What the "search for help in the UI" case gets instead

The brief's on-the-fly instinct is answering a real need. It gets a better
answer than generation, and the prototype already produces the ingredient:

> **`beats.json` is a chapter index.** Every UI step is stamped with the
> millisecond it happened. So the Hub Assistant can answer *"how do I stage a
> return?"* in text, as it does now, and attach a **deep link into the
> pre-rendered Store Keeper tutorial at 31.5 s** — the beat where that answer
> is on screen.

Instant, free, versioned, and it reuses the retrieval fence rather than
bypassing it. Slice 12f. This is the hybrid, and it is strictly better than
generating: the user gets an answer in under a second *and* the exact frame.

### 2.3 What "batch" means concretely

A make-style job over `tools/tutorials/*.yaml`:

* skips a tutorial whose script hash, dataset id and UI fingerprint are all
  unchanged since its manifest (§6.2);
* renders the rest, screencast once and language cuts N times;
* writes `manifest.json` per render — script SHA-256, payload SHA-256, git SHA,
  dataset id, HeyGen job ids, durations;
* **stops at the artefact.** Publishing is a separate, deliberate,
  admin-authenticated `POST /training/assets`, because a batch job that can
  publish is a batch job that can un-certify the entire workforce by accident
  (§9 Q4).

**It never runs in CI.** Same reasoning as P10-7: it needs a browser, a
database, a cloud API and a wall clock, and a gate that is slow, networked and
occasionally ugly is a gate people re-run rather than read.

---

## 3. Question 2 — data privacy

### 3.1 The finding that reframes the question

The brief asks how to stop proprietary data reaching HeyGen's API. That is the
easy half, and the prototype closes it (§3.3). **The hard half is that the
screencast is the sensitive artefact, and it is sensitive whether or not it
ever touches a cloud.**

`tests/e2e/global-setup.ts` builds the throwaway database like this:

```ts
execFileSync(PY, [ROOT/'tools/migration/cutover_migrate.py',
                  '--wipe', '--target', SYNC_DB_URL], …)
```

— which loads the **real** `gi_database.db`. That file is gitignored precisely
because *"it holds real employee names and stock"* (rule 15's own footnote,
commit `a09da0b`). The first prototype frame therefore carried real material
descriptions, real SAP codes, real per-category counts and a real username, and
the finished MP4 is a file that gets emailed, put on a phone and forwarded to a
contractor.

**The rule this produces:**

> ### P12-0 — a tutorial is recorded against SYNTHETIC data, or it is not published.
> Not "redacted". Not "blurred". Synthetic. Redaction is a filter that improves
> over time; a synthetic dataset is correct on day one and cannot regress by
> someone adding a panel that the mask list has never heard of.

The repository already has the precedent and the shape:
`tools/make_ci_fixture_db.py` exists because CI cannot have the real file
either. **Slice 12a is `tools/make_tutorial_db.py`, and it is blocking.**

### 3.2 The five rulings

| | Ruling | Why it is not an oversight |
|---|---|---|
| **P12-1** | **The HeyGen payload is built from the tracked YAML's `narration:` block and nothing else**, and `assert_text_only()` refuses any free-text string that is not character-for-character a reviewed `say:` line. | A scrubber asks "does this look dangerous?" and is only as good as the last incident. A **provenance whitelist** asks "did a person approve this exact sentence in a diff?" and is complete on day one. It is the same shape as rule 9: the fence runs BEFORE the thing it protects. ⚠️ It works — it refused its own first payload, because `payload.title` was composed from four YAML scalars and the whitelist only knew about narration lines. The fix was to make the builder return the whitelist it is allowed to emit, so a new field cannot be added without naming its source. |
| **P12-2** | **The screencast never leaves the machine.** The avatar comes back as a clip and ffmpeg composites LOCALLY. | This is the whole architecture in one sentence. It also rules out HeyGen features that would be tempting later — *video translate*, *avatar from a screen recording*, anything that takes an upload. Those are not integrations, they are the exact egress this design exists to prevent. |
| **P12-3** | **The assistant's on-screen answer is SCRIPTED, not sampled.** The model is never called during a recording. | Three reasons, ascending: an 8B model answers differently every render, so a re-render silently changes what the video *teaches*; the answer is user-visible copy, so scripted it is reviewed and sampled it is reviewed by nobody; and `docs/PROJECT_STATUS.md` §1a already records what this model does when unsure — *it invents UI no chapter describes*. A tutorial is the one artefact where that cannot be allowed. ⚠️ This does **not** weaken rule 9: nothing reaches the fence, because nothing reaches the model. |
| **P12-4** | **Recording is a test-shaped activity and obeys rule 15.** It runs against `gihub_e2e_pw` on :8010/:5183, built and destroyed by the E2E suite's own lifecycle, and reuses that suite's `global-setup`, `auth.setup.ts`, `harness/env.ts` and `node_modules`. | A second stack builder is a second place for rule 15 to be got wrong, and this one would be got wrong quietly — a tutorial recorded against `gihub` looks identical to one recorded against the clone. Reuse makes it structurally impossible rather than a thing to remember. |
| **P12-5** | **Every render writes a manifest**: script SHA-256, payload SHA-256, git SHA, dataset id, HeyGen job ids, durations. | "What did we send them, and when?" must be answerable a year later without re-deriving it from a script that has since changed. Same instinct as `ai_traces` (P11-1) — a file rather than a table until the batch has a home, because P11-3's lesson is that observability must never be able to break the thing it observes. |

### 3.3 What the guard actually does, and what it does not

```
      ✅ egress guard: every free-text field traces to a reviewed
         line in store_keeper_hub_assistant.yaml (7 whitelisted strings)
      ── the exact JSON that would be POSTed to https://api.heygen.com/v2/video/generate ──
```

Two independent checks, because either alone fails open:

1. **Structural** — every leaf is a scalar. A payload cannot carry bytes, a file
   handle or a nested asset descriptor, because there is nowhere in the tree
   for one to sit.
2. **Provenance** — every free-text leaf is exactly a reviewed `say:` line.

A shape sweep (`data:` URIs, base64 runs, absolute paths, media extensions,
email addresses, long digit runs) then runs **over the whitelisted strings** —
because provenance proves the string was reviewed, not that the review was any
good. It catches a script whose author pasted a real name or a SAP code into
the narration.

**What it does not do:** it says nothing about the *screencast*. That is P12-0's
job and it is not done yet.

### 3.4 The zero-egress fallback, which already works

⚠️ Worth knowing before any commercial conversation: **the pipeline degrades to
sending nothing at all.** The prototype's mock path is fully local — Pillow for
the card, `say` for the voice — and produces a finished, watchable MP4. Swap
`say` for the Kokoro pipeline this repository already runs offline
(`docs/exec_video/project/speak.py`, `bm_george`, level-checked per segment) and
you have narrated, captioned, beat-aligned role tutorials with **no third party
involved at all**. What HeyGen adds is a human face, which matters for
onboarding and matters more in Tamil and Arabic. It is a product decision, not
a technical dependency, and the operator should make it knowing the fallback is
already built (§9 Q7).

---

## 4. Question 3 — the tooling pipeline

### 4.1 Verdict on `ffmpeg-python`: **reject**

Same reasoning that rejected Portkey, LiteLLM and Guardrails-AI in Phase 11.

* It builds the same `argv` we build, then calls the same binary. Zero
  capability, one dependency in the `requirements.txt` **both** Python stacks
  share.
* Its error surface is worse: it raises on non-zero exit and hands you a
  truncated stderr, where a `subprocess` call hands you the whole filtergraph
  error, which is the only useful thing ffmpeg prints.
* **The house already shells ffmpeg.** `docs/exec_video/project_v3/build.py`
  renders a 4 min 23 s executive video that way. A second idiom for the same
  job is the `docs/USER_MANUAL.md` duplicate all over again.

**Adopted instead:** `subprocess.run` with an argv list and no shell, exactly as
`generate_tutorial.py` does. Pillow (already a transitive dependency) renders
text.

### 4.2 Three measured facts about this machine that changed the design

⚠️ None of these were assumptions in the brief, and two of them fail *silently*.

**(a) This ffmpeg has no `drawtext`.** Homebrew's ffmpeg 8.1.2 is built without
libfreetype:

```
$ ffmpeg -h filter=drawtext
Unknown filter 'drawtext'.
```

Captions are therefore rendered to RGBA PNGs with Pillow and composited with
`overlay`. That is not a workaround — it is better: real fonts, brand colours,
and no filtergraph escaping for text a human wrote.

**(b) This ffmpeg silently discards video alpha.** `-c:v libvpx-vp9 -pix_fmt
yuva420p` exits 0, prints no warning, and writes a `yuv420p` stream. VP8 does
the same. The only symptom is a black square around the avatar in the finished
MP4 — which is how it was found, by watching the file.

> **The rule that follows is rule 16 in a new domain: an exit code is not a
> result.** `build_avatar_clip()` now reads the `pix_fmt` back with `ffprobe`
> and falls through to an `alphamerge` mask path when the claim does not hold.
> The mask path is not a downgrade — it is also the path a **green-screen**
> HeyGen delivery takes (`colorkey` → `alphamerge`), so it is the branch most
> real footage will use anyway.

**(c) `recordVideo.size` does not scale the page.** A 1536×864 viewport in a
1920×1080 canvas is drawn 1:1 at the top-left, leaving black bands over exactly
20% of each axis — 1536/1920, to the pixel. `deviceScaleFactor` does not change
it. The two are pinned equal in `harness/record.ts` and the upscale to the
delivery canvas happens once, in ffmpeg, with lanczos. 1536 CSS px rather than
1920 is a legibility choice: this is a dense ERP, and a 1.25× upscale of
readable table text beats a native-1080p capture of text nobody can read.

### 4.3 ⚠️ The ordering, which is the most important thing in the pipeline

```
YAML ─▶ [egress guard] ─▶ HeyGen payload ─▶ avatar audio ─┐   ← PASS A
                                    (measured durations)  │
                                                          ▼
          shot list + per-beat HOLDS ─▶ Playwright ─▶ screencast.webm  ← PASS B
                                          + beats.json   │
                                                         ▼
                                               ffmpeg composite ─▶ .mp4
```

**The narration is rendered and measured BEFORE the browser opens.** This was
not obvious and was arrived at by getting it wrong. Built the other way round —
record first, narrate second — the first run reported:

```
  beat            starts     window     narration   verdict
  ──────────────────────────────────────────────────────────────
  title             0.86s     3.15s      8.30s   OVERRUNS by  5.15s
  open_assistant    4.02s     4.17s      6.46s   OVERRUNS by  2.29s
  question          8.18s     1.23s      5.41s   OVERRUNS by  4.17s
  thinking          9.41s     1.83s      5.98s   OVERRUNS by  4.15s
  answer           11.24s     5.74s      8.71s   OVERRUNS by  2.97s
  close            16.98s     2.66s      5.34s   OVERRUNS by  2.68s
```

Six beats, six overruns — **40.2 s of speech over a 19.6 s recording.** A
Playwright-driven UI is far faster than a person explaining it, so a narration
written for humans will *always* outrun it. The fix is not a longer guessed
pause; it is to hold each step for the measured length of its own line, which
means the audio has to exist first. After the change, same script, same spec:

```
  title             0.88s     9.20s      8.30s   ok
  open_assistant   10.09s     8.78s      6.46s   ok
  question         18.86s     6.28s      5.41s   ok
  thinking         25.14s     6.38s      5.98s   ok
  answer           31.52s     9.59s      8.71s   ok
  close            41.11s     6.53s      5.34s   ok
                                    worst overrun: +0.00s
```

**With a real key the ordering is unchanged:** HeyGen is called *before* the
browser opens, because the avatar's timing is what the screencast is cut to.
This is the single thing most likely to be undone by someone "simplifying" the
script into a linear record-then-narrate flow.

A pleasing side effect: the assistant's *"Thinking…"* pause is no longer a
cosmetic delay. It is **derived from the narration line that plays over it**, so
the model appears to think for exactly as long as the voice needs.

### 4.4 The composite

One filtergraph, four layers, driven from `beats.json`:

```
[0:v] scale=1920:1080:flags=lanczos, fps=30, format=rgba          [bg]   screencast
[1:v] scale=360:360, format=rgba                                  [avc]  avatar
[3:v] scale=360:360, format=gray                                  [avm]  circle mask
[avc][avm] alphamerge                                             [av]
[bg][av]  overlay=56:H-h-56                                       [v0]   avatar corner
[v0][2:v] overlay=0:0                                             [v1]   watermark
[v1][4:v] overlay=0:0:enable=between(t\,0.88\,10.09)              [c0]   caption per beat
…
[cN] format=yuv420p                                               [v]
```

* Commas inside a filter option are escaped (`\,`); the parser reads a bare
  comma as *next filter in the chain*.
* Audio is mixed separately: `anullsrc` of the full duration, one `adelay` per
  line at its measured beat time, one `amix` with `normalize=0`.
* Delivery: H.264 High@4.1, CRF 20, `+faststart`, AAC 160k. **47.6 s → 3.8 MB.**
* ⚠️ **The avatar's corner is a per-tutorial YAML field, not a house style.**
  Pinned bottom-right by default, the stand-in spent thirty seconds sitting on
  top of the Hub Assistant panel — in the tutorial about the Hub Assistant. The
  rule is that the avatar never covers the control being demonstrated, and only
  the script's author knows which control that is.

### 4.5 What is missing before a key arrives

`heygen_live()` is written from the published v2 contract and is marked
**UNVERIFIED** in the source, because no key has ever been used against this
repository. The poll-and-download half is deliberately **not** written: guessing
at a job-status shape produces code that looks finished and is not. It is
slice 12d and it is one afternoon once a key exists.

---

## 5. The prototype: what was built, and what it measured

Branch `chore/phase12-prototype`. Nothing under `backend/`, `frontend/` or
`legacy/` is touched.

| File | Lines | Job |
|---|---|---|
| `tools/generate_tutorial.py` | ~900 | The orchestrator: rule-14 lint → egress guard → payload → pass A → pass B → freeze-pad → composite → WebVTT → manifest, plus the batch runner |
| `tools/make_tutorial_db.py` | ~470 | The synthetic dataset (P12-0) |
| `frontend/scripts/nav_access_dump.mjs` | ~175 | Which roles may open which route — the pre-flight half of the rule-14 lint |
| `tools/tutorials/store_keeper_hub_assistant.yaml` | ~100 | **The entire HeyGen payload.** Narration, on-screen answer, redaction rules, avatar placement |
| `tests/video_gen/sample_tutorial.spec.ts` | ~110 | The recording. Six beats |
| `tests/video_gen/harness/record.ts` | ~270 | Beats, synthetic cursor, redaction hook, scripted AI lane, recording context |
| `tests/video_gen/playwright.config.ts` · `stack.ts` | ~90 | Its own config; reuses the E2E lifecycle, honours `GI_VIDEO_REUSE_STACK` |
| `tests/video_gen/README.md` | — | The three load-bearing facts in `record.ts` |

**Output:** `docs/tutorials/out/store_keeper_hub_assistant.mp4` — **47.64 s,
1920×1080, 3.8 MB**, six beat-aligned captions, worst narration overrun
**+0.00 s**. Gitignored (§3.1).

### 5.1 Four defects the prototype found in itself

Three of the four were invisible in the logs and only appeared by *watching the
output*. That is worth recording, because it is the argument for a human review
step in slice 12e.

| # | Defect | How it presented | Fix |
|---|---|---|---|
| 1 | Narration outran every beat | Timing report — the only one the logs caught | Two-pass ordering (§4.3) |
| 2 | Screencast filled 80% of the frame | Black bands, right and bottom | Pin `recordVideo.size` to the viewport, upscale in ffmpeg |
| 3 | Avatar alpha silently discarded | Black square around the avatar | Read the `pix_fmt` back; `alphamerge` fallback |
| 4 | Avatar covered the panel it was explaining | Thirty seconds of a talking head over the Hub Assistant | `avatar.corner` per tutorial |

And a fifth, caught by the guard rather than by a person: the egress check
refused the pipeline's own first payload, because the whitelist and the builder
were separate functions. They are one function now.

### 5.2 The redaction hook, demonstrated

Not theoretical. The shipped script sets:

```yaml
redaction:
  mask:    [.ant-table-tbody]     # every data row on the dashboard
  replace: {worker: demo.user}    # the seeded Store Keeper's real username
```

In the finished MP4 the header reads **"Store Keeper · demo.user"** and every
data row is blurred. It runs on **every DOM mutation**, not once — an SPA paints
its shell first and fills it from `fetch`, so a single pass at
`DOMContentLoaded` would redact an empty page and congratulate itself.

⚠️ **It is a second line, not the boundary.** It only masks what somebody
thought to name. P12-0 stands.

### 5.3 Gates, after the prototype

Nothing shared was modified, and the suite that shares the most was re-run to
prove it:

```
cd tests/e2e && npm test                      ✅ 128 / 128  (51.3 s)
npm run test:nav --prefix frontend            ✅ 51 routes, all claimed
```

---

## 6. Scaling to the whole catalogue

### 6.1 ⚠️ The screencast is language-independent. The holds are not.

This is the tension that decides the batch design. One recording could serve
four languages — except that the holds are the *narration's* length, and Tamil
does not take as long as English.

**Resolution: record once, freeze-pad at beat boundaries.** Every beat already
ends on a static hold (that is what a hold is), so extending it means
duplicating identical frames — invisible. `beats.json` gives the segment
boundaries, so one screencast becomes N language cuts in ffmpeg alone:

```
per language:  for each beat segment, hold the final frame until that
               language's narration for the beat is finished
```

Without `beats.json` this would be impossible and the answer would be four
recordings. With it, the browser runs **once per tutorial, not once per
language** — a 4× saving on the most expensive stage.

✅ **Built and measured in 12b.** The padding is applied inside the composite
filtergraph (`split` → `trim` → `tpad=stop_mode=clone` → `concat`) rather than
as an intermediate file, so a hold costs no second generation loss. Exercised by
lengthening one narration line and re-compositing the SAME screencast:
**8.86 s padded into one beat, 47.72 s → 56.58 s, worst overrun +0.00 s, no
re-recording.** The frozen region was checked numerically rather than by eye —
mean per-channel frame difference inside the pad **0.12/255** (pure H.264
requantisation noise) against **1.5/255** across the freeze boundary.

### 6.2 ⚠️ The avatar is cached by narration hash — a UI change costs zero credits

The avatar clip is a pure function of `(say text, avatar_id, voice_id,
language)`. It does not know the UI exists. So:

* **UI moved, narration unchanged** → re-record the screencast, re-composite,
  **zero HeyGen credits**.
* **Narration edited** → re-render only the lines whose text changed.

This is the difference between a per-release bill and a rounding error, and it
falls out of the design rather than being bolted on. The cache key belongs in
the manifest (P12-5).

### 6.3 Sizing

The nav manifest claims **51 routes** across **9 roles**. How many *processes*
that is depends on granularity, which is a question for the operator (§9 Q2),
but for a plausible 60-tutorial catalogue at ~1.5 min each:

| | Per full rebuild |
|---|---|
| Screencasts (60 × ~70 s warm) | ~70 min |
| Language cuts (60 × 4, ffmpeg only) | ~35 min |
| HeyGen rendered minutes (60 × 1.5 × 4) | **360 min** — *first* build only; thereafter only changed lines |

An overnight job, once. Then near-nothing per release.

### 6.4 Where the output goes

`POST /training/assets` with `module_key`, `language`, `storage_uri`,
`captions_uri`, `duration_s` — the endpoint already exists and is admin-only and
audited. `storage_uri` wants object storage rather than the repo: these are
binaries that change every release, and `docs/tutorials/out/` is gitignored for
the reason in §3.1. **This is the one piece of new infrastructure Phase 12
needs**, and it is coupled to the Hetzner decision (§9 Q3).

---

## 7. Sequencing — six slices

Ordered so the blocking non-video work lands first and nothing is published
before it is safe to publish.

| Slice | Branch | Contents | Blocking? |
|---|---|---|---|
| **12a** ✅ | `feat/phase12-tutorial-dataset` | **`tools/make_tutorial_db.py`** — 306 items across the eleven real categories, 90 receipts, 140 consumption rows, 90 dated lots, 14 invented employees on a reserved badge block, the fourteen real lining systems, and the rule-1c tier fixture. Deterministic (pinned `ANCHOR`). Plus `--dataset {tutorial,e2e}` on the recorder, its own database and its own two ports. | ⚠️ **WAS BLOCKING — DONE.** |
| **12b** ✅ | `feat/phase12-recorder` | `scripts/` → `tools/` (Q11). Manifest (P12-5) with `script_sha256` + git SHA + dataset version. **WebVTT** into the existing `captions_uri`. **Freeze-padding** in the composite. A batch runner with `--dry-run`. The **rule-14 route lint**. | — |
| **12c** ✅ | `feat/phase12-scripts` | A **declarative** recorder (`steps:` in the YAML — ~60 bespoke spec files would be 60 places for the harness to drift) plus the first four tutorials, narrated from role-fenced manual chapters and checked against `manual_qa.allowed_sections()` itself. **The rest of the catalogue is now a YAML file each.** | — |
| **12d** | `feat/phase12-heygen` | Live HeyGen: submit, poll, download, retry, the credit budget, and a real 200 against the unverified path. **Needs a key.** | ✅ Q7 signed off — needs only the key |
| **12e** | `feat/phase12-publish` | Publishing: object storage, `POST /training/assets`, the version-bump policy (Q4), and a **human review step** — §5.1 is the argument for it. | Needs Q3, Q4 |
| **12f** ✅ | `feat/phase12-deeplinks` | The assistant returns a text answer **plus a deep link into the pre-rendered tutorial at the right beat** (§2.2) — the feature the brief actually wanted. Behind the same fence rule 9 uses (**P12-6**), with a two-token overlap rule the floor alone could not provide. Suite **CX** (15). | — |

**Two guardrails on the whole phase:**

* **Rule 13.** `USER_MANUAL.md` §26 and a `MANUAL_TESTING_GUIDE.md` section
  ship in the same PRs, not after. And **a new role added to `ROLE_META` must
  gain a tutorial catalogue entry** in the same commit as its manual allowlist
  and its PDF recipe — otherwise it silently inherits the Store Keeper's, which
  is exactly the failure mode rule 13 already documents.
* **Rule 14.** A tutorial must never show a page its role cannot open. That is
  checkable and deterministic: cross-reference each script's visited routes
  against `canAccessPath`. Proposed as a lint in 12b — cheap, and it fails a
  *script*, never a build.

---

## 8. Risks, and what I am not confident about

| Risk | Severity | Position |
|---|---|---|
| **Beat `t0` is asserted, not proved.** `t0` is stamped immediately after `newContext()`; the recorder starts a frame or so later. The run asserts no beat lands past the video's end, which catches a *gross* mismatch, not a 40 ms one. | Low | A visual clapper (a marker frame at a known time, found in post) would prove it. Deferred — 40 ms is inaudible against a 350 ms breath. |
| **`say` is macOS-only.** The mock voice does not exist on Linux. | Low | Kokoro is already in the repo and offline (`docs/exec_video/project/speak.py`). Swap in 12b if the batch ever moves to a server. |
| **The HeyGen path is unverified.** No key has ever been used here. | Medium | Marked UNVERIFIED in the source and unreachable without `--live` **and** a key. The poll half is deliberately unwritten. |
| **A tutorial silently goes stale** when the UI it shows moves. | ⚠️ **High** | This is the real long-term hazard: a *wrong* tutorial is worse than none, and nothing fails. Mitigation in 12b: record the git SHA and the touched-route fingerprint in the manifest, and flag a tutorial whose routes changed. |
| **Re-rendering un-certifies the workforce** (P10-6). | ⚠️ **High** | Needs an operator ruling before 12e. See Q4. |
| **The recorder and the E2E gate collide.** Same database, same two ports. | Low | Documented in three places; `--reuse-stack` exists for batches. Could be made structural with a separate DB name — see Q10. |

---

## 9. Clarifying questions

### 9.1 ⛔ ANSWERED AND LOCKED (operator, 2026-09-06)

These are decisions now, not questions. Each is implemented on the branch named
beside it.

| # | Question | **Locked answer** | Where it landed |
|---|---|---|---|
| **Q1** | Synthetic dataset — invent everything, or keep the real structure? | **Keep the real STRUCTURE** (categories, lining systems, roles, UOMs, work types, equipment types, locations); **invent every employee name, material description, SAP code, quantity, price, date and vendor.** | `tools/make_tutorial_db.py`, and the docstring states the split explicitly so a later edit cannot blur it |
| **Q2** | Catalogue granularity | **~60 short per-process clips, 60–120 s each.** | Slice 12c; the batch runner in 12b is built for many small scripts rather than nine long ones |
| **Q3** | Where the MP4s live | **`docs/tutorials/out/` locally for now**; object storage at the Hetzner cutover. | Gitignored (§3.1). `storage_uri` stays a local path until the cut |
| **Q4** | What a re-render does to compliance | **A cosmetic UI re-render does NOT bump `training_modules.version`.** Bump only when the narration or the demonstrated process changes — **keyed on the script's SHA-256.** | The manifest carries `script_sha256`; the dataset was made **deterministic** (a pinned `ANCHOR` date) so the same script cannot produce different numbers on screen |
| **Q5** | Languages at launch | **English first for the whole catalogue.** Tanglish and the rest follow. | Freeze-padding (§6.1) is built anyway, so adding a language later is an ffmpeg pass, not a re-record |
| **Q11** | `scripts/` vs `tools/` | **Move to `tools/generate_tutorial.py` and `tools/tutorials/`.** | Slice 12b, `git mv`; `REPO_MAP.md` rule 1 no longer widened |

⚠️ **Q4 had a consequence nobody asked about, and it is the reason the dataset
is pinned.** Keying the version bump on the script's hash only works if
everything *else* is stable. A dataset generated from `date.today()` would move
every number on screen daily while the script's SHA-256 said nothing had
changed — so the compliance record would point at a video nobody has seen,
which is precisely the failure P10-6 exists to prevent. `make_tutorial_db.py`
therefore computes every date from a pinned `ANCHOR` constant that is bumped by
hand alongside `DATASET_VERSION`. **The dataset ages on purpose**, because
ageing is a versioned decision somebody makes and drift is one nobody notices.

### 9.2 Still open — not blocking 12a or 12b

| # | Question | My recommendation |
|---|---|---|
| **Q6** | Who writes the narration — extracted from `USER_MANUAL.md`, or fresh? | Extracted and then edited for the ear. The manual is already role-fenced and already the AI corpus; fresh prose creates a fourth thing to keep in step with it. |
| ~~**Q7**~~ ✅ | ~~Does narration text leaving the network need written sign-off?~~ | **ANSWERED 2026-09-06 — signed off.** Reviewed narration text is approved to leave the network for the HeyGen API once a live key is configured. 12d now waits on the key, not on a decision. §3.4 stands as the fallback: the pipeline still works with zero egress. |
| **Q8** | Stock HeyGen avatar, or a likeness of a real GI person? | Stock. A likeness is a consent question, a leaver question, and a re-record every time that person leaves. |
| **Q12** | Do tutorials cover the LEGACY Streamlit app? | No. It is feature-frozen and being switched off; a tutorial is a reason to keep using it. |
| **Q13** | Do site users watch on phones? | If yes, add a 9:16 cut — a second composite pass over the same screencast, not a second recording. |
| **Q14** | Does `ocr_workflow_v1` get priority in the catalogue? | Yes — it is the only module that currently *gates* a feature, and its player says "not published yet" today. |

### 9.3 Answered by building it

| # | Question | Answer |
|---|---|---|
| **Q9** | `bin/` or `scripts/` for the batch runner? | Neither — `tools/`, per Q11. `bin/` is for the three long-lived operator scripts (`dev.sh`, `power.sh`, `backup_db.sh`), and this is not one. |
| **Q10** | Its own database name so it cannot collide with the E2E gate? | **Done in 12a, and it cost four environment variables.** `tests/e2e/harness/env.ts` already reads `E2E_DB`, `E2E_API_PORT`, `E2E_WEB_PORT` and (through `cutover_migrate`) `GI_DB_FILE` from the environment, so the recorder sets all four and runs on `gihub_tutorial_pw` at :8011/:5184. **No file under `tests/e2e/` was edited**, and a documented hazard became an impossible one. |

---

## 10. What I recommend you approve now

1. **The strategy** — pre-render, batch, into the existing training hub; deep
   links, not on-demand generation (§2).
2. **The five rulings** `P12-0`…`P12-5`, into `PROJECT_HANDOVER.md` alongside
   the P10 and P11 sets (§3).
3. **Slice 12a first**, and the rule that nothing publishes before it (§3.1).
4. **Answers to Q1–Q5 and Q7**, which are what 12b and 12c need to start.

The prototype on this branch is not a demo of a thing that might work. It is
the pipeline, minus one HTTP call, and it has already been run end to end.
