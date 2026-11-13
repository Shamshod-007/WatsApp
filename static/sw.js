// Минимальный service worker: делает сайт устанавливаемым (PWA) и кэширует только иконки.
const CACHE = "msgr-v1";
self.addEventListener("install", (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(["/static/icons/icon-192.png", "/static/icons/icon-512.png"])));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())
  );
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/static/icons/")) {
    e.respondWith(caches.match(e.request).then((r) => r || fetch(e.request)));
  }
});
