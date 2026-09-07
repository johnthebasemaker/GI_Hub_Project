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

Current baselines (2026-09-07): **service_tests 2328/0 (suites A…CX) ·
Playwright 128/128 · AI evals Tier 1 147/147 · parity:sme 1,313 ·
ui-math 33/0 · bug_check 599/0/0 · nav 51 · alembic head `a1c9e64b3d70`.**
`tools/parity_check.py` is meaningful only on CI or a freshly-cutover DB.

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

- **502 on every `/api` call in dev = uvicorn isn't running** — the Vite proxy
  target (`127.0.0.1:8000`) has never been the problem. The app now says so
  itself: a throttled console error names the exact launch command and the
  login page shows a "backend unreachable" toast. Launch config: the
  `backend` entry in `.claude/launch.json`, or
  `.venv/bin/uvicorn backend.api.main:app --host 127.0.0.1 --port 8000`.
- **Native app says "Server unreachable" while the web portal works** →
  almost certainly the Cloudflare Access wall, not the server: the webview
  has no Access SSO session, so `/api` 302s to the Access login and CORS
  kills it (a bare network error). `client.ts logApiFailure()` prints the
  status/headers and a "Possible Cloudflare Access block" note on
  network-err/403/5xx. Fix = the Access **Bypass** policy for
  `gi.giinventory.com/api/*` (NATIVE_APPS.md §6).
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
- **httpx test cookies:** `client.cookies.set(..., domain="host")` with a
  host-only domain SILENTLY drops the cookie — replay/reuse tests then pass
  vacuously ("no refresh token" 401 looks like success). Never pass `domain=`
  (bit suites E/AQ, fixed 2026-07-25).
- **Android builds need JDK 21** (Capacitor 8 hardcodes Java 21). On this Mac:
  `JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"`.
- **CI job logs are unreadable without signing in** (public repo, anonymous
  API 403s) — both `postgres-dual-ci.yml`'s bug_check step and
  `release-android.yml`'s gradle step therefore re-emit failures as
  `::error::` annotations (visible on the run page + anonymous check-runs
  API); dual-CI also uploads `bugcheck_ci.log` + `BUG_REPORT.md` artifacts.
