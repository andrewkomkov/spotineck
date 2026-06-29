// spotineck PWA service worker — кеширует app-shell для офлайн-старта и
// устанавливаемости. Данные (/api, /ws) всегда идут в сеть.
const CACHE = "spotineck-v1";
const SHELL = [
  "/", "/index.html", "/styles.css", "/app.js",
  "/manifest.webmanifest", "/icon-192.png", "/icon-512.png", "/apple-touch-icon.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // живые данные — мимо кеша
  if (e.request.method !== "GET" || url.pathname.startsWith("/api") || url.pathname === "/ws") return;
  // app-shell: отдаём из кеша мгновенно, в фоне обновляем
  e.respondWith(
    caches.match(e.request).then((cached) => {
      const net = fetch(e.request)
        .then((res) => {
          if (res && res.ok) { const cp = res.clone(); caches.open(CACHE).then((c) => c.put(e.request, cp)); }
          return res;
        })
        .catch(() => cached);
      return cached || net;
    })
  );
});
