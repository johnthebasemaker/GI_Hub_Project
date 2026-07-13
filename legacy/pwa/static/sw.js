/*
 * sw.js — service worker for the GI Floor PWA
 * --------------------------------------------
 * Tiny offline shell. Three jobs:
 *   1. Pre-cache the HTML + manifest + this file itself on install.
 *   2. Network-first for /api/* (we always want fresh data when online).
 *   3. Cache-first for everything else (so the app shell loads offline).
 *
 * The IndexedDB write queue is owned by the PAGE, not the worker — keeping
 * the worker simple avoids the complexity of Background Sync API on iOS
 * where it isn't supported anyway.
 */

const CACHE = "gi-floor-v1";
const SHELL = ["/", "/app.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))),
    ).then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);

  // Network-first for API: we always want fresh truth from the backend.
  if (url.pathname.startsWith("/api/")) {
    e.respondWith(fetch(e.request).catch(() => new Response(
      JSON.stringify({offline: true, error: "Offline — queued locally"}),
      {status: 503, headers: {"Content-Type": "application/json"}},
    )));
    return;
  }

  // Cache-first for static shell (HTML, manifest).
  e.respondWith(
    caches.match(e.request).then((cached) => cached || fetch(e.request).then((resp) => {
      // Opportunistically cache GETs so the shell survives offline reloads.
      if (e.request.method === "GET" && resp.ok) {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
      }
      return resp;
    }).catch(() => caches.match("/"))),
  );
});
