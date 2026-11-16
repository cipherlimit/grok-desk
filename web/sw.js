/* App shell cache only. Data files under d/ are never cached (always network, no-store).
   Web push handler for plan A; plan B has no push endpoint, so no subscription is ever made there. */
const SHELL = "desk-shell-v1", FILES = ["./", "index.html", "app.css", "app.js", "chart.js", "manifest.json", "icons/apple-touch-icon.png"];
self.addEventListener("install", e => { e.waitUntil(caches.open(SHELL).then(c => c.addAll(FILES)).then(() => self.skipWaiting())); });
self.addEventListener("activate", e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== SHELL).map(k => caches.delete(k)))).then(() => self.clients.claim())); });
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (u.origin !== location.origin || u.pathname.includes("/d/")) return;            // data: straight to the network
  e.respondWith(fetch(e.request).then(r => { const c = r.clone(); caches.open(SHELL).then(x => x.put(e.request, c)); return r; }).catch(() => caches.match(e.request)));
});
self.addEventListener("push", e => {
  let d = {}; try { d = e.data.json(); } catch (x) {}
  e.waitUntil(self.registration.showNotification(d.title || "Grok Bot · now", { body: d.body || "", icon: d.icon || "icons/icon-192.png", data: { url: d.url } }));
});
self.addEventListener("notificationclick", e => { e.notification.close(); if (e.notification.data && e.notification.data.url) e.waitUntil(clients.openWindow(e.notification.data.url)); });
