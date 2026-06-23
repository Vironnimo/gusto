// Service-Worker fuer Kuechenbuch (Einkaufsliste-PWA).
// Strategie:
//   install   -> App-Shell precachen + skipWaiting
//   activate  -> alte Caches loeschen + clients.claim
//   fetch     -> Navigationen: network-first, Offline-Fallback auf /einkauf
//                /api/...:     network-only (nicht cachen)
//                sonst GET:    cache-first (Static)

const CACHE = "kuechenbuch-v1";

const APP_SHELL = [
  "/einkauf",
  "/",
  "/static/style.css",
  "/static/app.js",
  "/static/einkauf-client.js",
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

  // Nur GET behandeln; alles andere (POST etc.) direkt ans Netz.
  if (request.method !== "GET") {
    return;
  }

  const url = new URL(request.url);

  // Fremd-Origin (z.B. Google Fonts) nicht anfassen.
  if (url.origin !== self.location.origin) {
    return;
  }

  // Navigationen: network-first, bei Fehler gecachte /einkauf liefern.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() =>
        caches.match("/einkauf").then((cached) => cached || caches.match("/"))
      )
    );
    return;
  }

  // API: niemals cachen (Offline regelt der Client via localStorage).
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(fetch(request));
    return;
  }

  // Sonstige same-origin GET (Static): cache-first.
  event.respondWith(
    caches.match(request).then(
      (cached) =>
        cached ||
        fetch(request).then((response) => {
          // Erfolgreiche Antworten nachtraeglich in den Cache legen.
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
    )
  );
});
