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

## Theming & motion

The app wears the legacy GI **"Navy vault, gold key"** brand identity, with a
**dark/light toggle** (dark is the flagship default) in the header.

- **`src/theme/tokens.ts`** — the single source of truth for the palette, mirroring
  the Streamlit theme in root `config.py` (navy `#003366`, gold `#D4AF37`, dark
  surfaces, status colors). Change a color here, not inline in a component.
- **`src/theme/themes.ts`** — three Ant Design `ThemeConfig`s applied via
  `ConfigProvider`: `darkTheme` (flagship), `lightTheme` (uses amber `#B45309` for
  text-level accents so gold passes contrast on white), and `siderTheme` (the
  sidebar rail is navy in **both** modes). Theming at the token level restyles every
  screen at once — components don't hardcode colors.
- **`src/theme/ThemeContext.tsx`** — holds the mode, persists it to `localStorage`,
  and sets `data-theme` on `<html>` (which drives the CSS-side gradients/scrollbars).
- **`src/index.css`** — what tokens can't do: background gradients, the navy sider
  rail, the glassmorphic login, and all keyframes.
- **Animations** are deliberately **subtle-premium** — 120–200 ms, ease-out, no
  bounce — and every one is wrapped in a `prefers-reduced-motion: reduce` guard.
  Route fade-in, skeleton first-loads, KPI count-ups (`src/lib/useCountUp.ts`),
  hover lifts, and a one-shot notification-bell ring.

**Do not** move colors, endpoints, or data-fetching into components — the visual
layer sits cleanly on top of the existing TanStack Query hooks.

## Layout

```
src/
  api/        client.ts (axios + types), hooks.ts (TanStack Query hooks)
  auth/       AuthContext.tsx (login/JWT/2FA session)
  config/     entities.ts (entity + form-field metadata driving the screens)
  theme/      tokens.ts (palette), themes.ts (AntD configs), ThemeContext.tsx (toggle)
  components/ AppLayout.tsx (sider nav + header), BrowseTable.tsx (generic read grid),
              KpiCard.tsx, NotificationBell.tsx, ErrorBoundary.tsx
  lib/        columns.tsx (auto columns from row keys), useCountUp.ts
  pages/      Dashboard, Stock, Records, MasterData, entry/HOD/Logistics/Warehouse/
              Supervisor/SK/SME/Admin/Reports/Security screens (24 total)
```

The screens are **config-driven** (`src/config/entities.ts`): adding a new read
browser or CRUD form is a one-entry change there once the backend exposes it.
