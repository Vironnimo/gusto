// Service worker for Gusto (shopping-list PWA).
// Strategy:
//   install   -> precache the app shell + skipWaiting
//   activate  -> delete old caches + clients.claim
//   fetch     -> navigations: network-first, offline fallback to /shopping
//                /api/...:     network-only (do not cache)
//                other GET:    cache-first (static)

const CACHE = "gusto-v3";

const APP_SHELL = [
  "/shopping",
  "/",
  "/static/style.css",
  "/static/app.js",
  "/static/shopping-client.js",
  "/static/manifest.webmanifest",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;

  // Only handle GET; everything else (POST etc.) goes straight to the network.
  if (request.method !== "GET") {
    return;
  }

  const url = new URL(request.url);

  // Do not touch foreign origins (e.g. Google Fonts).
  if (url.origin !== self.location.origin) {
    return;
  }

  // Navigations: network-first, on error serve the cached /shopping.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() =>
        caches.match("/shopping").then((cached) => cached || caches.match("/"))
      )
    );
    return;
  }

  // API: never cache (the client handles offline via localStorage).
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(fetch(request));
    return;
  }

  // Other same-origin GET (static): cache-first.
  event.respondWith(
    caches.match(request).then(
      (cached) =>
        cached ||
        fetch(request).then((response) => {
          // Put successful responses into the cache after the fact.
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
    )
  );
});
