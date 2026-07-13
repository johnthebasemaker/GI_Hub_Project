import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The SPA calls the FastAPI backend under /api; the dev server proxies that to
// the uvicorn process on :8000 (so there are no CORS concerns in dev and the
// same relative paths keep working in prod behind a reverse proxy).
//
// Tunnel mode (`VITE_TUNNEL=1 npm run dev`): exposes the dev server through the
// Cloudflare Tunnel at gi.giinventory.com for multi-user testing. It allows that
// host (Vite blocks unknown Hosts by default) and points HMR's websocket at the
// tunnel's TLS port. Local dev without the flag is unchanged.
const tunnel = process.env.VITE_TUNNEL === '1'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    ...(tunnel ? { allowedHosts: ['gi.giinventory.com'] as string[] } : {}),
    ...(tunnel ? { hmr: { host: 'gi.giinventory.com', clientPort: 443, protocol: 'wss' } } : {}),
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
})
