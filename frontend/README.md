# GI Hub — Frontend (React + Vite + Ant Design)

A decoupled single-page ERP console that talks to the FastAPI backend
(`backend/api/`) over REST. Separate from the Streamlit app — this is the new UI
built on the PostgreSQL foundation.

Stack: **React + TypeScript + Vite**, **Ant Design** (UI), **TanStack Query**
(data fetching/caching), **React Router**, **axios**.

## Run it (two processes)

**1. Backend** (from the repo root) — serves the data on :8000:
```bash
./run_api.sh
```

**2. Frontend** — this dev server on :5173 (proxies `/api` → `:8000`):
```bash
npm install          # first time
npm run dev
```
Open **http://localhost:5173**.

> The Vite dev server proxies `/api/*` to `http://127.0.0.1:8000` (see
> `vite.config.ts`), so the backend must be running for data to load. The header
> tag turns red ("API offline") if it isn't.

Build for production: `npm run build` (outputs to `dist/`).

## Screens

- **Dashboard** — inventory/site/expiry KPIs, inventory-by-category, expiring stock.
- **Stock** — the derived views as tabs: Live (global), By site, Lot balances,
  Expiring (with a within-days filter). These map to the parity-tested
  `/stock/*` endpoints.
- **Records** — read-only browsers for inventory / receipts / consumption /
  returns / lots / purchase orders / equipment (pagination + `Site_ID` filter).
- **Master Data** — full CRUD (add/edit/delete) for vendors / warehouses /
  employees (the API's writable entities).

## Layout

```
src/
  api/       client.ts (axios + types), hooks.ts (TanStack Query hooks)
  config/    entities.ts (entity + form-field metadata driving the screens)
  components/ AppLayout.tsx (sider nav), BrowseTable.tsx (generic read grid)
  lib/       columns.tsx (auto columns from row keys)
  pages/     Dashboard, StockPage, RecordsPage, MasterDataPage
```

The screens are **config-driven** (`src/config/entities.ts`): adding a new read
browser or CRUD form is a one-entry change there once the backend exposes it.
