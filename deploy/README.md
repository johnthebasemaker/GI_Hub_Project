# deploy/ — new-stack (React + FastAPI + PostgreSQL) production kit

Turnkey Docker deployment for the **new** stack. Separate from the Streamlit app
(repo-root `docker-compose.yml`) — they don't interfere.

**Full runbook: [`../docs/DEPLOY.md`](../docs/DEPLOY.md).**

Quick start (on the server, after DNS points at the box):
```bash
cp .env.example .env      # then fill in DOMAIN, JWT_SECRET, POSTGRES_PASSWORD, …
./init-letsencrypt.sh     # one-time TLS bootstrap
docker compose -f docker-compose.prod.yml up -d
# then do the one-time SQLite→Postgres data migration — see the runbook §4
```

Services: `db` (Postgres 16) · `api` (FastAPI, internal) · `web` (nginx: SPA + `/api`
proxy + TLS) · `certbot` (auto-renew). Only `web` binds host ports (80/443).

Nothing here has been run against a server — provision the box and go when ready.
`deploy/.env` holds secrets and is gitignored; never commit it.
