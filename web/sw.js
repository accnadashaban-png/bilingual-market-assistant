// Minimal service worker so the app can be installed. It never caches API responses or settings.
const SHELL = "market-assistant-shell-v2";
self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(["/app/icon.svg", "/app/manifest.webmanifest"])));
  self.skipWaiting();
});
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method === "GET" && (url.pathname === "/app/icon.svg" || url.pathname === "/app/manifest.webmanifest")) {
    event.respondWith(caches.match(event.request).then((hit) => hit || fetch(event.request)));
  }
});
