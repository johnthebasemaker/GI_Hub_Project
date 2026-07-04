# Deploying the new stack (React + FastAPI + PostgreSQL)

Turnkey deploy for the **new** React/FastAPI/Postgres stack — the feature-complete
app built in the 2026-07 session. This is a *separate* deployment from the
Streamlit app (repo-root `docker-compose.yml`); the two do not interfere.

Everything lives in [`deploy/`](../deploy/):

| File | What it is |
|---|---|
| `docker-compose.prod.yml` | db (Postgres 16) · api (FastAPI) · web (nginx: SPA + `/api` proxy + TLS) · certbot |
| `Dockerfile.api` | FastAPI image (uvicorn, 4 workers, `GI_ENV=production`) |
| `Dockerfile.web` | multi-stage: builds the Vite bundle → nginx serves it |
| `nginx.conf` | SPA fallback + `/api/`→api proxy (strips prefix) + TLS + ACME |
| `init-letsencrypt.sh` | one-time TLS bootstrap (dummy cert → real cert) |
| `.env.example` | secrets template → copy to `deploy/.env` (gitignored) |

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
git clone https://github.com/johnthebasemaker/CNCEC-System.git gihub && cd gihub/deploy
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
  api python -m backend.api.service_tests      # 52/52
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
- **Backups (do this before go-live):** Postgres is now authoritative, so back up the
  `pg-data-prod` volume off-box. A nightly dump:
  ```bash
  docker compose -f docker-compose.prod.yml exec -T db \
    pg_dump -U gihub gihub | gzip > gihub-$(date +%F).sql.gz
  ```
  Ship it to a Hetzner Storage Box / S3 (the repo-root compose notes the off-box gap).

## 8. What this does NOT include (confirm before go-live)
Not ported to the new stack (Streamlit-only): WhatsApp, email/mailer, local-LLM
(Ollama) Q&A + OCR, computer-vision. Also open: **reads are not site-scoped** (any
authenticated user can read any site's records — see `NEW_STACK_HANDOFF.md` §4c). If
any of these matter for day one, address them before cutover.
