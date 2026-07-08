# Deploying the new stack (React + FastAPI + PostgreSQL)

Turnkey deploy for the **new** React/FastAPI/Postgres stack — the feature-complete
app built in the 2026-07 session. This is a *separate* deployment from the
Streamlit app (repo-root `docker-compose.yml`); the two do not interfere.

Everything lives in [`deploy/`](../deploy/):

| File | What it is |
|---|---|
| `docker-compose.prod.yml` | db (Postgres 16) · api (FastAPI) · web (nginx: SPA + `/api` proxy + TLS) · certbot · **backup** (nightly pg_dump) |
| `Dockerfile.api` | FastAPI image (uvicorn, 4 workers, `GI_ENV=production`) |
| `Dockerfile.web` | multi-stage: builds the Vite bundle → nginx serves it |
| `nginx.conf` | SPA fallback + `/api/`→api proxy (strips prefix) + TLS + ACME |
| `init-letsencrypt.sh` | one-time TLS bootstrap (dummy cert → real cert) |
| `.env.example` | secrets template → copy to `deploy/.env` (gitignored) |
| `backup/backup-pg.sh` | nightly `pg_dump -Fc` + 14-day retention + `.last_success`/`.last_failure` markers |
| `deploy-v2.sh` · `health-check.sh` · `rollback.sh` | server-side manual-deploy orchestrator + health gate + automatic rollback (see §9) |

> ⚠️ **Nothing here has been run against a server.** It's a kit. Provision the
> box and run it when you're ready.

---

## 0. Prerequisites
- A Linux VPS (the parked **Hetzner CPX42** per the workstream-C decisions), Ubuntu 22.04+.
- **Docker Engine + Compose v2** installed (`docker --version`, `docker compose version`).
- A **DNS A/AAAA record** for your domain pointing at the server's IP.
- Firewall: inbound **80** and **443** open (Let's Encrypt + the app).
- A copy of the live **`gi_database.db`** (for the one-time data migration).

## 1. Get the code + configure secrets
```bash
git clone https://github.com/johnthebasemaker/GI_Hub_Project.git gihub && cd gihub/deploy
cp .env.example .env
# Fill in .env:
#   DOMAIN, LETSENCRYPT_EMAIL
#   POSTGRES_PASSWORD   →  openssl rand -base64 32
#   JWT_SECRET          →  openssl rand -hex 32   (MANDATORY, >=32 chars)
nano .env
```
`GI_ENV=production` is set by compose, so the API **refuses to boot without a strong
`JWT_SECRET`** — that's intentional.

## 2. Issue the TLS certificate (once)
DNS must already resolve to the box. Optionally set `LETSENCRYPT_STAGING=1` in `.env`
for a rate-limit-free dry run first, then flip to `0` and re-run.
```bash
./init-letsencrypt.sh
```
It builds the images, seeds a throwaway cert so nginx can start, then swaps in the
real Let's Encrypt cert. The `certbot` service auto-renews every 12h thereafter.

## 3. Bring the stack up
```bash
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps      # all healthy?
```
At this point `https://DOMAIN` serves the SPA, but Postgres is **empty** — do the
data migration next.

## 4. One-time data migration — SQLite → PostgreSQL
This makes Postgres the system of record. It uses the already-proven
`backend/dual_ci.py` (migrates all 64 tables + asserts parity).

> 🛑 **`dual_ci` WIPES and re-copies the target Postgres.** Run it ONLY for the
> initial cutover (or a pre-go-live re-sync). **Never** run it again after users
> start writing to production — it would erase their data.

Copy `gi_database.db` to `deploy/` on the server, then (note the **psycopg2** URL —
dual_ci is synchronous; substitute your `.env` password/user/db):
```bash
docker compose -f docker-compose.prod.yml run --rm \
  -e DATABASE_URL=postgresql+psycopg2://gihub:YOUR_PG_PASSWORD@db:5432/gihub \
  -v "$(pwd)/gi_database.db:/data/gi_database.db:ro" \
  api python backend/dual_ci.py --source /data/gi_database.db
# expect:  == DUAL-CI: ✅ PASS ==   (64/64 tables, identity-math parity)
```
Then hand the schema over to Alembic for future changes — `dual_ci` already
created the tables, so **stamp** the baseline (don't upgrade):
```bash
docker compose -f docker-compose.prod.yml run --rm \
  -e DATABASE_URL=postgresql+psycopg2://gihub:YOUR_PG_PASSWORD@db:5432/gihub \
  api alembic -c backend/alembic.ini stamp head
```
After this, schema changes go through Alembic (`backend/alembic/README.md`).

## 5. Verify
```bash
curl -s https://DOMAIN/api/health          # {"status":"ok","dialect":"postgresql",...}
```
Then in a browser: `https://DOMAIN` → sign in (`admin` / your migrated password) →
click through Dashboard, Stock, Reports (download an Excel), Admin → Users.
Optional smoke test from inside the api container:
```bash
docker compose -f docker-compose.prod.yml run --rm \
  -e DATABASE_URL=postgresql+psycopg2://gihub:YOUR_PG_PASSWORD@db:5432/gihub \
  -e JWT_SECRET="$(grep ^JWT_SECRET .env | cut -d= -f2)" \
  api python -m backend.api.service_tests      # 386/386
```

## 6. Cutover decision (making React primary)
When you're satisfied:
1. **Freeze** Streamlit writes (put it in maintenance mode, or take it offline).
2. Re-run the **step-4 migration** one last time to catch any writes since the first run.
3. Point users at `https://DOMAIN`.
4. Retire Streamlit, or keep it running **read-only** as a fallback for a transition.

**Rollback is trivial** while you're deciding: the Streamlit app + `gi_database.db`
are completely untouched — if anything's wrong, send users back to Streamlit.

## 7. Operations
- **Logs:** `docker compose -f docker-compose.prod.yml logs -f api` (or `web`, `db`).
- **Restart / update:** `git pull && docker compose -f docker-compose.prod.yml up -d --build`.
- **TLS renewal:** automatic (the `certbot` service). Force: `docker compose -f docker-compose.prod.yml run --rm certbot renew`.
- **Backups (automated):** the `backup` service runs `deploy/backup/backup-pg.sh`
  nightly at **02:00 Asia/Riyadh** — `pg_dump -Fc` (custom format) into the
  `pg-backups` volume, **14-day retention**, writing `.last_success`/`.last_failure`
  markers (same convention as the v1 SQLite backup, so the Admin **Service Health**
  card reads them unchanged). The console's manual **Admin → Backup** button
  (`POST /admin/backup`) writes to the **same** volume, so manual and nightly dumps
  live together. Run one on demand:
  ```bash
  docker compose -f docker-compose.prod.yml exec backup /bin/sh /usr/local/bin/backup-pg.sh
  ```
  Restore a dump:
  ```bash
  docker compose -f docker-compose.prod.yml exec -T db \
    pg_restore -U gihub -d gihub -c < gihub-<stamp>.dump
  ```
  ⚠️ **Off-box before go-live:** the `pg-backups` volume is on the same VPS disk.
  Two options to survive a total-VPS-loss:
    - **S3 (recommended):** set `AWS_S3_BUCKET` + IAM creds in `deploy/.env` —
      `backup-pg.sh` then pushes each dump to S3 (SSE-encrypted). Retention is a
      bucket **lifecycle policy** (e.g. 30d → Glacier, 90d → expire). Use a
      dedicated IAM user scoped to put/list on that bucket. Restore: `aws s3 cp`
      the dump back, then `pg_restore` as above.
    - **Hetzner Storage Box:** bind the `pg-backups` volume to it (the compose
      `volumes:` block has the CIFS stub).

## 8. What this does NOT include (confirm before go-live)
Not ported to the new stack (Streamlit-only): WhatsApp, email/mailer. The local-LLM
(Ollama) Intelligence layer (Q&A, OCR, NL→SQL, CV) **is** in the new stack (the
`ollama` service). Reads **are** site-scoped as of 2026-07-05 (below level 3, reads
pin to the user's own `Site_ID`; policy in `backend/api/auth.py: site_scope()` /
`resolve_site_param()`). Remaining pre-cutover item: the **WhatsApp/email outbox
(Phase 7)**, on hold for the Meta permanent token. Address it before day one if
outbound messaging matters at launch.

## 9. Automated deploy + rollback (manual trigger)
For repeatable cutover/redeploy, `.github/workflows/deploy-v2.yml` drives the whole
thing — **manual trigger only** (`workflow_dispatch`, type `deploy` to confirm), on
its own concurrency group so it can never collide with the v1 pipeline (`deploy.yml`,
untouched). Flow:

1. **Gate** — the v2 test matrix on GitHub runners: `dual_ci` populate → `parity_check`
   → `service_tests` → frontend build. Black runs **advisory only** (`continue-on-error`)
   — it never blocks a deploy (no forced repo-wide reformat).
2. **Smoke-build** — builds the `api` + `web` production images (catches Dockerfile
   breakage before anything ships).
3. **Deploy** — SSH to the server (reusing `HETZNER_*` secrets + `SLACK_WEBHOOK_URL`)
   and run `deploy/deploy-v2.sh`, which:
   - pre-flight (`.env` present, docker present, ≥2 GB free) → `git reset --hard origin/main`;
   - builds **SHA-tagged** images (`gi-hub-newstack-{api,web}:<sha>`) for rollback;
   - `db` up → `alembic upgrade head`;
   - **PORT-HANDOVER** — stops the v1 root `nginx` (frees `:80`/`:443`), then `up -d` the v2 stack;
   - runs `deploy/health-check.sh` (api `/health` <2s · web `/` <400 · alembic at head);
   - on success: records the SHA, prunes layers, Slack ✅. On failure: runs
     `deploy/rollback.sh` — **reverts the port-handover** (stops v2 `web`, restarts v1
     `nginx` so users land back on the known-good Streamlit app), retags the previous
     SHA images, Slack 🔁.

**The v1 and v2 stacks both bind `:80`/`:443`** — only one serves at a time; the
handover/rollback is how that's arbitrated. **DB schema is never auto-downgraded** —
rollback reverts containers/images only; a schema rollback stays a deliberate manual op.
