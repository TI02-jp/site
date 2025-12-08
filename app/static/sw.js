// PWA service worker for JP Contábil
const VERSION = "v2.0.3";
const STATIC_CACHE = `jp-contabil-static-${VERSION}`;
const DYNAMIC_CACHE = `jp-contabil-dynamic-${VERSION}`;
const KNOWN_CACHES = [STATIC_CACHE, DYNAMIC_CACHE];
const OFFLINE_URL = "/offline";

const CORE_ASSETS = [
  "/",
  OFFLINE_URL,
  "/static/styles.css",
  "/static/tasks.css",
  "/static/dark-theme.css",
  "/static/javascript/notifications.js",
  "/static/javascript/realtime.js",
  "/static/javascript/modal_cleanup.js",
  "/static/javascript/paste_images.js",
  "/static/images/logo-jp-contabil.png",
  "/static/images/icon-192x192.png",
  "/static/images/icon-512x512.png",
];

const CDN_ASSETS = [
  "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
  "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.5/font/bootstrap-icons.css",
  "https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(STATIC_CACHE)
      .then((cache) => {
        const preCache = CORE_ASSETS.map(
          (url) => new Request(url, { cache: "reload" })
        );
        const cdnCache = CDN_ASSETS.map(
          (url) => new Request(url, { mode: "no-cors" })
        );
        return cache.addAll([...preCache, ...cdnCache]);
      })
      .catch((error) =>
        console.error("[Service Worker] Falha ao criar cache inicial", error)
      )
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((cacheNames) =>
        Promise.all(
          cacheNames
            .filter((name) => !KNOWN_CACHES.includes(name))
            .map((name) => caches.delete(name))
        )
      )
      .then(() => self.clients.claim())
  );
});

const isHtmlNavigation = (request) => {
  if (request.mode === "navigate") return true;
  const accept = request.headers.get("accept") || "";
  return accept.includes("text/html");
};

const cacheFirst = (request, cacheName = STATIC_CACHE) =>
  caches.match(request).then((cached) => {
    if (cached) return cached;
    return fetch(request)
      .then((response) => {
        if (response && response.status === 200) {
          caches
            .open(cacheName)
            .then((cache) => cache.put(request, response.clone()));
        }
        return response;
      })
      .catch(() => cached);
  });

const networkFirst = (request, { cacheName = DYNAMIC_CACHE, offlineFallback = false } = {}) =>
  fetch(request)
    .then((response) => {
      if (response && response.status === 200) {
        caches
          .open(cacheName)
          .then((cache) => cache.put(request, response.clone()));
      }
      return response;
    })
    .catch(() =>
      caches.match(request).then((cached) => {
        if (cached) return cached;
        return offlineFallback ? caches.match(OFFLINE_URL) : undefined;
      })
    );

// Fetch handler desabilitado para não interferir no carregamento do portal.
self.addEventListener("fetch", () => {});

self.addEventListener("message", (event) => {
  const { data } = event;
  if (!data) return;

  if (data === "SKIP_WAITING" || data?.type === "SKIP_WAITING") {
    self.skipWaiting();
  }

  if (data?.type === "CLEAR_RUNTIME_CACHE") {
    event.waitUntil(caches.delete(DYNAMIC_CACHE));
  }
});
