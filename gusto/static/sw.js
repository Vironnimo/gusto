// Service worker for Gusto (shopping-list PWA).
// Strategy:
//   install   -> precache the app shell + skipWaiting
//   activate  -> delete old caches + clients.claim
//   fetch     -> navigations: network-first, offline fallback to /shopping
//                /api/...:     network-only (do not cache)
//                recipe/archive media: network-only (moved images must stay current)
//                favorite media: cache-first (filenames change on replacement)
//                static GET:   cache-first (active cache only)
//                fetched HTML: network-only

// The version bump also evicts HTML pages cached by the old fallthrough.
const CACHE = "gusto-v16";

const APP_SHELL = [
  "/shopping",
  "/",
  "/static/style.css",
  "/static/app.js",
  "/static/photo-input.js",
  "/static/shopping-client.js",
  "/static/manifest.webmanifest",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(
        APP_SHELL.map((url) => new Request(url, { cache: "reload" }))
      ))
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

  // Recipe images keep a stable URL while their selection may change. Product
  // images use content-specific filenames and deliberately fall through to the
  // cache-first branch so recommendations remain recognizable offline.
  if (url.pathname.startsWith("/media/recipe/")
      || url.pathname.startsWith("/media/archive/")) {
    event.respondWith(fetch(request));
    return;
  }

  // HTML fetched for live search or source metadata must be current, even
  // when its URL is also part of the precached navigation fallback.
  if (!url.pathname.startsWith("/static/")
      && !url.pathname.startsWith("/media/favorite/")) {
    event.respondWith(fetch(request));
    return;
  }

  // Static assets and favorite product photos stay available offline. Reject
  // HTML responses here too, e.g. an error page at a stale asset URL.
  event.respondWith(
    caches.open(CACHE).then((cache) => cache.match(request)).then(
      (cached) =>
        cached ||
        fetch(request).then((response) => {
          // Cache successful responses after the fact, but never HTML
          // documents (see above).
          const contentType = response && response.headers.get("content-type");
          if (response && response.ok && contentType
              && !contentType.startsWith("text/html")) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
    )
  );
});
