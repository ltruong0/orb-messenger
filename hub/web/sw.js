// Orb Messenger service worker: app shell offline, API always from the network,
// sticker PNGs cached (they are content-addressed by the manifest anyway).
const VERSION = 'orb-shell-v2';
const SHELL = [
  '/', '/app.css', '/app.js', '/manifest.webmanifest',
  '/fonts/fredoka-latin-wght-normal.woff2', '/fonts/dm-sans-latin-wght-normal.woff2',
  '/fonts/jetbrains-mono-latin-wght-normal.woff2', '/icons/icon-192.png',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin || e.request.method !== 'GET') return;
  if (url.pathname.startsWith('/api/')) return;  // network only (SSE, auth)
  if (url.pathname.startsWith('/media/stickers/')) {
    e.respondWith(caches.open(VERSION).then(async c => {
      const hit = await c.match(e.request);
      const net = fetch(e.request).then(r => { if (r.ok) c.put(e.request, r.clone()); return r; }).catch(() => hit);
      return hit || net;
    }));
    return;
  }
  // shell + photos: network first, cache fallback
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok && SHELL.includes(url.pathname)) caches.open(VERSION).then(c => c.put(e.request, r.clone()));
    return r;
  }).catch(() => caches.match(e.request.mode === 'navigate' ? '/' : e.request)));
});
