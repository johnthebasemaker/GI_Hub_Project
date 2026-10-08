# GI-Hub design contract

*Phase 21e, rulings Q21-21..24 (2026-10-06). **Every UI change follows this file.**
It is the "decisions written down so pages stop drifting" that the Interface
Design skill asks for, and it is what the project skills in `.claude/skills/`
(frontend-design, Emil Kowalski's motion skills, Vercel's guidelines,
webapp-testing) are applied inside. Where a skill and this file differ, **this
file wins**. The mechanical half is enforced by `npm run test:design` (part of
`npm run build`, so CI runs it).*

---

## 1. Direction: industrial luxury corporate

GI-Hub is an **operations tool** used all day on a construction site and in an
office: inventory, approvals, lining progress. It is read by store keepers on
phones and by a Finance Manager on a projector. The direction (ruling Q21-21)
is **industrial luxury corporate**:

- **Navy carries the structure.** It is the surface, the frame, the trust.
- **Gold is the one thing that matters on a screen right now.** Primary actions,
  the active tab, the number someone must act on. Spend it once per view (the
  frontend-design skill's *spend your boldness in one place*).
- **Quiet everywhere else.** No gradient washes on cards, no decorative motion,
  no confetti. Status colours (ok / low / critical / info) mean something, so
  they appear only where they carry information.
- **Numbers are the content.** Quantities, m², days of cover. They are set in
  tabular figures, right-aligned, with their unit.

A **minimalist-brutalism** view was drawn for comparison only
(`docs/design/brutalism-view.pdf`); it is not the product's direction.

## 2. Colour — tokens only

The palette lives in `frontend/src/theme/tokens.ts` (TS) and the `--gi-*`
variables in `frontend/src/index.css` (CSS). **A colour outside those files is a
bug** (`test:design` ratchets the count of raw hex values in components: it may
only go down — 195 when written, 153 after the Phase 21g pass over the
daily-use pages, and **0** since Phase 22f, which moved the SME estimator's
charts onto the tokens. A new raw colour now fails the build outright).

| Role | Token | Value |
|---|---|---|
| Structure | `brand.navy` / `navyLight` / `navyDark` | #003366 / #1A4D80 / #001F40 |
| Attention | `brand.gold` / `goldLight`; `goldDeep` on white | #D4AF37 / #F0D060; #B45309 |
| Dark surfaces | `dark.bg` / `surface` / `surface2` / `border` | #0A1628 / #162038 / #1E3050 / #2A4060 |
| Light surfaces | `light.bg` / `surface` / `border` | #F8FAFC / #FFFFFF / #E5E7EB |
| Status | `status.ok` / `low` / `critical` / `info` / `neutral` | #22C55E / #F59E0B / #EF4444 / #4A90D9 / #94A3B8 |
| Chart series | `chartColors[0..7]` | gold, navyLight, the status colours, then #2E7D8C / #8B3A62 / #4A90D9 |
| Media | `media.letterbox` | #000000 — a camera or video frame, not the theme |

Phase 22f mapped every raw colour onto these (ruling Q22-20): Tailwind emerald
(#10B981) and antd greens → `status.ok`; amber (#F59E0B, #FBBF24) → `status.low`;
reds (#EF4444, #CF1322, #DC2626, #DC3545, #F87171) → `status.critical`; blues →
`status.info`; slate #94A3B8 (an empty bar, "no data") → `status.neutral`; gold →
`brand.gold`; white text on a coloured chip → `light.surface`. The only visible
change is the green, a touch brighter. A file whose code already has a variable
named `status` imports the tokens as `status as tone`.

- **Primary buttons:** gold with navy text, in both themes.
- **Contrast:** text and its background meet WCAG AA in light AND dark (gold on
  white uses `goldDeep`).

## 3. Type

| Use | Family | Size / weight |
|---|---|---|
| UI (everything) | **IBM Plex Sans**, self-hosted (`/fonts/`), then the system stack | 14 body · 12 secondary · 16 emphasis — 400 / 500 / 600 |
| Page titles only (`h1`–`h3` of a page) | **Source Serif 4** 600, self-hosted | 32 / 24 / 20 |

- **Scale:** 12 · 14 · 16 · 20 · 24 · 32. No other sizes in new code.
- **Quantities:** `font-variant-numeric: tabular-nums` on every table and
  statistic (set globally in `index.css`), right-aligned, unit beside the number.
- **Fonts never block:** `font-display: swap`; the files load after first paint
  and are not on the login critical path's JS.
- **Writing** (frontend-design skill):
  - sentence case;
  - a button says what happens ("Approve 3 jobs", not "Submit");
  - errors say what went wrong and what to do, and never apologise;
  - no ALL-CAPS labels;
  - `…` not `...`;
  - loading text ends with `…`.

## 4. Space and shape

- **4 px grid:** 4 · 8 · 12 · 16 · 24 · 32 · 48.
- **Radii:** 6 (inputs, tags), 8 (cards, buttons), 12 (modals, drawers). One
  radius per kind of thing, never one radius on everything.
- **Elevation:** surfaces step by colour (`surface` → `surface2`), and a shadow
  appears only on what floats (modals, popovers, dropdowns).
- **Hit targets:** at least 32 px on desktop and 44 px on touch.

## 5. Motion (Emil Kowalski's rules, applied to an operations tool)

| Token (`index.css`) | Value | For |
|---|---|---|
| `--gi-ease-out` | `cubic-bezier(0.23, 1, 0.32, 1)` | anything entering or responding to a press |
| `--gi-ease-in-out` | `cubic-bezier(0.77, 0, 0.175, 1)` | something moving on screen |
| `--gi-dur-press` | 140 ms | button press feedback (`scale(0.97)`) |
| `--gi-dur-fast` | 150 ms | hovers, tooltips |
| `--gi-dur-mid` | 200 ms | dropdowns, popovers |
| `--gi-dur-slow` | 300 ms | modals, drawers (the ceiling) |

1. **Should it animate at all?** Anything used 100+ times a day (tables,
   keyboard actions, the ⌘K palette, approval queues) does **not** animate.
   Motion is for what a person just did: opened, confirmed, removed.
2. Animate **only `transform` and `opacity`**. Never `transition: all`
   (`test:design` fails on it).
3. **Never `ease-in`** for UI. Nothing appears from `scale(0)`: use
   `scale(0.95)` + opacity. Popovers scale from their trigger; modals stay
   centred.
4. **`prefers-reduced-motion: reduce` turns motion off everywhere.** This is
   global in `index.css`, and `test:design` checks it is there.
5. **CSS only on the login critical path** (zero JS growth, RULES.md). A motion
   library only in a lazy chunk, with a reason in the PR.

## 6. Components

- **Ant Design v6** is the component library. **No second library** (shadcn,
  MUI…), and no new component kind where AntD has one.
- **Every table** is `lib/smartTable` (sort, filter, sticky header,
  tabular figures).
- **Every async button** shows its loading state.
- **Every list** has an empty state that says what to do.
- **Focus:** visible on every interactive element (`:focus-visible`), never
  `outline: none` without a replacement.
- **Destructive actions** confirm (Popconfirm or a modal), never fire on one click.
- **State in the URL** where a person would share it: sort, filter, tab.

## 7. How a UI change is checked

1. `npm run build`: typecheck, the critical path (zero JS growth), and
   **`test:design`**.
2. `cd tests/e2e && npm test`: behaviour (E2E asserts text and roles, not pixels).
3. **The screenshot report** (`tests/e2e/specs/visual-report.spec.ts`): 12 key
   pages, light and dark, saved per run as a **report, never a gate** (ruling
   Q21-24). Compare before / after in the PR.
4. **Skills, on demand:**
   - `/web-design-guidelines <files>` for the pre-ship checklist;
   - `/review-animations` on any motion change;
   - `/frontend-design` when shaping a new page.

## 8. Do not

| Do not | Because |
|---|---|
| Add a colour, font, size or duration outside the tokens | Pages drift. The ratchet catches colours and `transition: all`; review catches the rest |
| Animate a table, a form field, or a keyboard action | Used hundreds of times a day, so motion is delay |
| Add a font or library to the login critical path | Zero-growth rule (Phase 14e) |
| Use gold for decoration | Gold means *act here* |
| Replace an AntD component with a hand-rolled one for looks | Accessibility and behaviour come with AntD |
