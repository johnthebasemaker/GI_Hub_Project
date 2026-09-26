import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

// The SPA calls the FastAPI backend under /api; the dev server proxies that to
// the uvicorn process on :8000 (so there are no CORS concerns in dev and the
// same relative paths keep working in prod behind a reverse proxy).
//
// Tunnel mode (`VITE_TUNNEL=1 npm run dev`): exposes the dev server through a
// Cloudflare Tunnel for multi-user testing. Vite rejects unknown Host headers
// with "Blocked request. This host is not allowed" (a 403 from Vite, not from
// Cloudflare), so EVERY hostname that may reach the dev server must be listed.
//
//   local.giinventory.com — the local dev tunnel; use this day to day.
//   gi.giinventory.com    — kept working for existing setups, but it becomes
//                           the PRODUCTION hostname once the Hetzner box is
//                           live, at which point it must stop resolving here.
//
// HMR's websocket has to target the exact host you are browsing, so it follows
// VITE_TUNNEL_HOST (default: local.giinventory.com). Point that at whichever
// hostname your tunnel serves:
//
//   npm run dev:local   → VITE_TUNNEL=1 VITE_TUNNEL_HOST=local.giinventory.com
//   npm run dev:gi      → VITE_TUNNEL=1 VITE_TUNNEL_HOST=gi.giinventory.com
//
// Without VITE_TUNNEL the flag is inert and plain localhost dev (`npm run dev`)
// is unchanged. Only ONE of these may run at a time — see `strictPort` below.
const tunnel = process.env.VITE_TUNNEL === '1'
const TUNNEL_HOSTS = ['local.giinventory.com', 'gi.giinventory.com']
const TUNNEL_HOST = process.env.VITE_TUNNEL_HOST ?? 'local.giinventory.com'
// Union, so an explicit VITE_TUNNEL_HOST is always accepted even if it is not
// one of the defaults above.
const ALLOWED_HOSTS = Array.from(new Set([...TUNNEL_HOSTS, TUNNEL_HOST]))

export default defineConfig({
  plugins: [
    react(),
    // Phase B — PWA: installable app + offline read cache. The service worker
    // is generated only for `vite build` output (dev/HMR is unaffected). The
    // offline MUTATION queue is separate app code (src/offline/queue.ts) and
    // works in dev too.
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg', 'apple-touch-icon.png'],
      manifest: {
        name: 'GI Hub — Warehouse & Inventory',
        short_name: 'GI Hub',
        description: 'Warehouse, inventory & procurement console',
        theme_color: '#0a192f',
        background_color: '#0a192f',
        display: 'standalone',
        start_url: '/',
        icons: [
          { src: '/pwa-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/pwa-512.png', sizes: '512x512', type: 'image/png' },
          { src: '/pwa-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // never let the SPA fallback swallow API calls
        navigateFallbackDenylist: [/^\/api\//, /^\/training-api\//],
        runtimeCaching: [
          {
            // core READ endpoints for offline warehouse viewing: stock views,
            // inventory master, ledger lists, notifications. Network first
            // (4 s), fall back to the last good copy for up to a day.
            urlPattern: /\/api\/(stock\/|inventory|receipts|consumption|returns|notifications|meta\/)/,
            method: 'GET',
            handler: 'NetworkFirst',
            options: {
              cacheName: 'gi-api-read',
              networkTimeoutSeconds: 4,
              expiration: { maxEntries: 300, maxAgeSeconds: 24 * 60 * 60 },
              cacheableResponse: { statuses: [200] },
            },
          },
        ],
      },
    }),
  ],
  server: {
    port: 5173,
    // Fail loudly instead of drifting to 5174. A tunnel's ingress points at a
    // FIXED port, so a second dev server silently taking the next one looks
    // like it started fine while the tunnel serves the first one (or nothing) —
    // the two environments can never quietly fight over 5173 this way.
    strictPort: true,
    allowedHosts: ALLOWED_HOSTS,
    ...(tunnel ? { hmr: { host: TUNNEL_HOST, clientPort: 443, protocol: 'wss' } } : {}),
    proxy: {
      '/api': {
        // VITE_API_PROXY lets the Playwright E2E harness (tests/e2e) point a
        // throwaway dev server at its own isolated backend port.
        target: process.env.VITE_API_PROXY ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
      // Rule 17: the Practice API is a SECOND process (bin/dev.sh starts it on
      // :8001). Same shape as /api, different backend — nothing in the SPA
      // decides data, it only picks which of these two prefixes to call.
      '/training-api': {
        target: process.env.VITE_PRACTICE_PROXY ?? 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/training-api/, ''),
      },
    },
  },
  // `vite preview` serves the BUILT bundle on the same port and proxy as dev.
  // Testing over the tunnel this way is the honest measurement: `npm run dev`
  // ships ~28 MB of unbundled ES modules to the browser, which is fine on
  // localhost and painfully slow through Cloudflare.
  preview: {
    port: 5173,
    strictPort: true,
    allowedHosts: ALLOWED_HOSTS,
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY || 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
      '/training-api': {
        target: process.env.VITE_PRACTICE_PROXY || 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/training-api/, ''),
      },
    },
  },
})
