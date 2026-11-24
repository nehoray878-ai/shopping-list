// שומר את האפליקציה במכשיר כדי שתיפתח מהר וגם בלי אינטרנט בסופר
const VERSION = "v12";
const SHELL = ["./", "index.html", "manifest.webmanifest", "icon-192.png", "icon-512.png", "icon-180.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open("shell-" + VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(k => !k.endsWith(VERSION)).map(k => caches.delete(k))
  )).then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;

  // תמונות מוצרים: מהמטמון קודם, כדי לחסוך גלישה
  if (url.hostname === "img.rami-levy.co.il" || (url.hostname === "www.gstatic.com" && url.pathname.startsWith("/firebasejs/"))) {
    e.respondWith(caches.open("img-" + VERSION).then(async c => {
      const hit = await c.match(e.request);
      if (hit) return hit;
      try {
        const res = await fetch(e.request, { mode: "no-cors" });
        c.put(e.request, res.clone());
        return res;
      } catch { return Response.error(); }
    }));
    return;
  }

  if (url.origin !== location.origin) return;

  // הקטלוג והדף: קודם מהרשת (מחירים עדכניים), ואם אין חיבור – מהמטמון
  e.respondWith(fetch(e.request).then(res => {
    const copy = res.clone();
    caches.open("shell-" + VERSION).then(c => c.put(e.request, copy));
    return res;
  }).catch(() => caches.match(e.request).then(r => r || caches.match("index.html"))));
});
