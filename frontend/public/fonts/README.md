# Self-hosted fonts (Phase 21e, ruling Q21-23)

| File | Family | Weights | Licence |
|---|---|---|---|
| `IBMPlexSans-latin-var.woff2` | IBM Plex Sans (variable, Latin subset) | 400–600 | SIL OFL 1.1 — `OFL-IBMPlexSans.txt` |
| `SourceSerif4-latin-600.woff2` | Source Serif 4 (Latin subset) | 600 — page titles only | SIL OFL 1.1 — `OFL-SourceSerif4.txt` |

Fetched once from Google Fonts (fonts.gstatic.com) on 2026-10-06 and served from
here — no third-party font request at runtime. Declared in `src/index.css`
with `font-display: swap`, so text renders at once in the system font and
swaps when the file arrives; never on the login page's JS critical path.
Design contract: `docs/DESIGN_SYSTEM.md` §3.
