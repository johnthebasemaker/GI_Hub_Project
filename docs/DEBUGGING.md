# Debugging & Diagnostics — the one-stop index

> Companion to [ARCHITECTURE.md](ARCHITECTURE.md) §8 (the gates). This page is
> the "something's wrong — what do I run?" cheat-sheet.

## 0. First move for ANY sync complaint

```bash
.venv/bin/python tools/diagnose_sync.py
```

`tools/diagnose_sync.py` is the **backend/frontend sync doctor**: read-only,
credential-free, exit-code-driven. It walks the whole chain a browser entry
travels and pinpoints the broken link:

| Check | What a FAIL means |
|---|---|
| `api.health` | FastAPI down or its DB connection is broken (`./run_api.sh`?) |
| `db.alembic-single-head` / `db.revision-matches` | migration drift — DB not stamped at the script head |
| `web.vite` | dev server down (note: Vite may bind IPv6 `::1` only — use `localhost`, not `127.0.0.1`) |
| `web.api-proxy` | Vite `/api` proxy can't reach FastAPI — offline entries will queue forever |
| `offline.replay-header` | middleware rejects `X-Offline-Replay` — queued entries can never sync |
| `pwa.build` | `dist/sw.js` missing — installed PWAs keep serving stale bundles |
| `db.gi_ai_ro` | the AI read-only role lost its grants (re-run `backend/scripts/create_ai_readonly_role.sql`) |

Flags: `--json` for scripts/CI, `--api` / `--web` for non-default ports.

## 1. The standard gates (run before every commit)

```bash
# backend service tests (CI mirror, hermetic)
DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub \
JWT_SECRET=ci-only-service-test-secret-key-32bytes-min \
.venv/bin/python -u -m backend.api.service_tests

# frontend build + types
npm run build --prefix frontend && cd frontend && npx tsc --noEmit

# headless E2E (builds/destroys its own gihub_e2e_pw stack)
cd tests/e2e && npm test

# legacy regression (must stay green until Streamlit switch-off)
.venv/bin/python legacy/bug_check.py

# SME engine golden parity (TS twin)
npm run parity:sme --prefix frontend
```

## 2. Client-side offline-queue debugging (browser console)

The queue exposes itself on `window.__giOffline` (set in `initOfflineQueue`):

```js
await __giOffline.count()   // entries waiting
await __giOffline.list()    // inspect queued payloads
await __giOffline.flush()   // force a Send now
```

Watch the events it emits: `gi-offline-queue` (count changed),
`gi-offline-queued` (an entry went to disk), `gi-offline-flushed`
(sync attempt finished — `detail.sent` / `detail.failed`).

The **Send / Receive** header button (SyncControls) is the user-facing wrapper
around exactly these calls: flush the queue, then refetch every open query.

## 3. Known traps (bite in this order of frequency)

- **Vite binds `::1` only** → `curl 127.0.0.1:5173` refuses while `localhost:5173`
  works. The doctor's `web.vite` check says so.
- **Preview/hidden browser tabs throttle rendering** — clicks/typing lag or go
  stale. Verify via API/DB instead (project memory: `preview-hidden-tab-throttling`).
- **After ANY mirror reload**: re-run `backend/scripts/create_ai_readonly_role.sql`
  AND the Excel sync chain (ARCHITECTURE §1) — `tools/parity_check.py` fails
  against the live mirror BY DESIGN since the Excel injection.
- **`GI_DOTENV=0`** pins hermetic runs (service_tests) — never remove; without it
  `config.py` dotenv-loads `deploy/.env` on bare metal.
- **PWA served stale after deploy** → check `pwa.build`, then hard-reload once;
  the service worker auto-updates on the next periodic check (15 min) or reload.
