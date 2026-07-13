# GI Hub — headless E2E suite (Playwright)

The scripted version of [docs/automatic_test.md](../../docs/automatic_test.md).
Fully self-contained: one command builds an isolated stack (throwaway Postgres
DB loaded with the **real** legacy data via the production cutover script, a
hermetic FastAPI on **:8010** with WhatsApp/SMTP/scheduler disabled, and a Vite
dev server on **:5183** proxying to it), runs every spec headlessly, then tears
it all down. A developer's normal `:8000` / `:5173` / `gihub` stack is never
touched.

## Run it

```bash
cd tests/e2e
npm install                      # once
npx playwright install chromium  # once (browser binary)
npm test                         # full suite
npm run report                   # open the HTML report
```

Prereqs: repo `.venv` (backend deps), local Postgres on `:5433` with the
`gihub` mirror's cluster (the suite creates/drops its own `gihub_e2e_pw` DB),
`frontend/node_modules` installed.

## Layout

| piece | job |
|---|---|
| `global-setup.ts` | create+load `gihub_e2e_pw` (cutover_migrate --wipe), reset role passwords to a known value, spawn uvicorn :8010 + Vite :5183 |
| `global-teardown.ts` | kill both process groups, `DROP DATABASE … WITH (FORCE)` |
| `setup/auth.setup.ts` | logs in via the API as admin / hod / sk / supervisor / logistics and mints one `storageState` per role — specs never log in through the UI |
| `specs/auth.spec.ts` | the login form itself (valid, invalid, sign-out) |
| `specs/smoke.spec.ts` | per-role route sweep: renders, non-blank, zero pageerrors |
| `specs/workflows.spec.ts` | W1/W1b/W2/W3 multi-role state machines (the 21-check QA harness, ported) |
| `specs/negative-access.spec.ts` | role-lock 403 lattice + UI affordance hiding |
| `specs/exec-summary.spec.ts` | Executive Summary renders; Download PDF yields a real `%PDF-` file |

## CI

GitHub Actions shape (see docs/PROJECT_STATUS.md §F2): a `postgres:16` service
container, `pip install -r backend/requirements.txt`, `npm ci` in `frontend/`
and `tests/e2e/`, `npx playwright install --with-deps chromium`, then
`npm test`. Publish `.report/` as the artifact.

## Porting the rest of the matrix

`docs/automatic_test.md` rows map 1:1 to `test()` blocks. Still to port:
master-data CRUD tabs, admin console actions, man-hours, SME read tabs,
command palette, dark-mode toggle. Add each as a new spec under `specs/` using
the same storageState pattern.
