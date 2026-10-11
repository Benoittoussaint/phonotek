// Phonotek : garde l'application et les pochettes en mémoire pour fonctionner sans réseau.
const SHELL = "phonotek-shell-v1";
const COVERS = "phonotek-covers-v1";
const SHELL_FILES = ["./", "index.html", "manifest.webmanifest", "icon.svg", "icon-180.png", "icon-512.png",
  "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4/dist/umd/supabase.min.js"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(k => k !== SHELL && k !== COVERS).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  // Pochettes : d'abord la mémoire, sinon le réseau (puis on garde une copie).
  if (url.pathname.includes("/storage/v1/object/public/covers/")) {
    e.respondWith(caches.open(COVERS).then(async c => {
      const hit = await c.match(e.request);
      if (hit) return hit;
      const res = await fetch(e.request);
      if (res.ok) c.put(e.request, res.clone());
      return res;
    }));
    return;
  }
  // Application : le réseau d'abord (pour avoir la dernière version), la mémoire si hors ligne.
  if (url.origin === location.origin || url.hostname === "cdn.jsdelivr.net") {
    e.respondWith(fetch(e.request).then(res => {
      if (res.ok) caches.open(SHELL).then(c => c.put(e.request, res.clone()));
      return res;
    }).catch(() => caches.match(e.request, { ignoreSearch: true })));
  }
});
