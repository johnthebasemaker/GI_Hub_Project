import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

// The SPA calls the FastAPI backend under /api; the dev server proxies that to
// the uvicorn process on :8000 (so there are no CORS concerns in dev and the
// same relative paths keep working in prod behind a reverse proxy).
//
// Tunnel mode (`VITE_TUNNEL=1 npm run dev`): exposes the dev server through the
// Cloudflare Tunnel at gi.giinventory.com for multi-user testing. The tunnel
// host is allowed unconditionally below (Vite blocks unknown Hosts by default);
// the flag only repoints HMR's websocket at the tunnel's TLS port, so local dev
// without it is unchanged (HMR just won't work through the tunnel).
const tunnel = process.env.VITE_TUNNEL === '1'
const TUNNEL_HOST = 'gi.giinventory.com'

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
        navigateFallbackDenylist: [/^\/api\//],
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
    allowedHosts: [TUNNEL_HOST],
    ...(tunnel ? { hmr: { host: TUNNEL_HOST, clientPort: 443, protocol: 'wss' } } : {}),
    proxy: {
      '/api': {
        // VITE_API_PROXY lets the Playwright E2E harness (tests/e2e) point a
        // throwaway dev server at its own isolated backend port.
        target: process.env.VITE_API_PROXY ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
  preview: {
    allowedHosts: [TUNNEL_HOST],
  },
})
