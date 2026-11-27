// שומר את האפליקציה במכשיר כדי שתיפתח מהר וגם בלי אינטרנט בסופר.
// כשמעלים גרסה חדשה: מעלים את VERSION, והטלפונים יתעדכנו בפתיחה הבאה.
const VERSION = "v17";
const SHELL = "shell-" + VERSION;
const DATA = "data-v1";       // הקטלוג: לא נמחק בין גרסאות, כדי שתמיד יהיו מחירים
const IMG = "img-v1";
const FONTS = "fonts-v1";
const KEEP = [SHELL, DATA, IMG, FONTS];
const SHELL_FILES = ["./", "index.html", "manifest.webmanifest", "icon-192-v2.png", "icon-512-v2.png", "icon-maskable-512-v2.png", "icon-180-v2.png"];
const MAX_IMAGES = 300;       // תקרה לתמונות שמורות, כדי שהזיכרון לא יתמלא
const PAGE_TIMEOUT = 3500;    // אחרי כמה זמן בלי רשת פותחים את הדף מהזיכרון

self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(k => !KEEP.includes(k)).map(k => caches.delete(k))
  )).then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  if (url.hostname === "img.rami-levy.co.il") return e.respondWith(cacheFirst(req, IMG, true));
  if (url.hostname === "www.gstatic.com" && url.pathname.startsWith("/firebasejs/")) return e.respondWith(cacheFirst(req, IMG, false));
  if (url.hostname === "fonts.gstatic.com") return e.respondWith(cacheFirst(req, FONTS, false));
  if (url.hostname === "fonts.googleapis.com") return e.respondWith(staleWhileRevalidate(req, FONTS, e));
  if (url.origin !== location.origin) return;

  if (url.pathname.endsWith("/catalog.json")) return e.respondWith(catalog(req, e));
  e.respondWith(networkFirst(req));
});

// הדף עצמו: קודם מהרשת (כך גרסה חדשה מגיעה מיד), ואם הרשת איטית או אין חיבור – מהזיכרון.
// no-cache: תמיד שואלים את השרת אם יש גרסה חדשה (בדיקה קטנה), במקום לחכות 10 דקות של GitHub
async function networkFirst(req) {
  const cache = await caches.open(SHELL);
  const net = fetch(req.url, { cache: "no-cache", credentials: "same-origin" }).then(res => { if (res.ok) cache.put(req, res.clone()); return res; });
  net.catch(() => {});
  const timeout = new Promise(r => setTimeout(r, PAGE_TIMEOUT));
  try {
    const res = await Promise.race([net, timeout]);
    if (res) return res;
    const hit = await cache.match(req, { ignoreSearch: true });
    return hit || await net;
  } catch {
    return (await cache.match(req, { ignoreSearch: true })) || (await cache.match("index.html")) || Response.error();
  }
}

// הקטלוג (3MB): נפתח מיד מהזיכרון, ובמקביל מורידים עותק חדש. אם השתנה – מודיעים לדף
async function catalog(req, e) {
  const cache = await caches.open(DATA);
  const key = new Request(new URL("catalog.json", self.registration.scope));
  const hit = await cache.match(key);
  const refresh = fetch(req, { cache: "no-cache" }).then(async res => {
    if (!res.ok) return res;
    const changed = !hit || hit.headers.get("etag") !== res.headers.get("etag") || hit.headers.get("last-modified") !== res.headers.get("last-modified");
    await cache.put(key, res.clone());
    if (hit && changed) {
      for (const c of await self.clients.matchAll({ type: "window" })) c.postMessage({ type: "catalog-updated" });
    }
    return res;
  });
  if (hit) { e.waitUntil(refresh.catch(() => {})); return hit; }
  return refresh;
}

async function cacheFirst(req, name, limit) {
  const cache = await caches.open(name);
  const hit = await cache.match(req);
  if (hit) return hit;
  try {
    const res = await fetch(req);
    if (res.ok || res.type === "opaque") {
      await cache.put(req, res.clone());
      if (limit) trim(cache);
    }
    return res;
  } catch { return Response.error(); }
}

async function staleWhileRevalidate(req, name, e) {
  const cache = await caches.open(name);
  const hit = await cache.match(req);
  const net = fetch(req).then(res => { if (res.ok) cache.put(req, res.clone()); return res; });
  if (hit) { e.waitUntil(net.catch(() => {})); return hit; }
  try { return await net; } catch { return Response.error(); }
}

// מוחקים את התמונות הישנות ביותר מעבר לתקרה (המטמון שומר לפי סדר ההוספה)
let trimming = false;
async function trim(cache) {
  if (trimming) return; trimming = true;
  try {
    const keys = await cache.keys();
    for (const k of keys.slice(0, Math.max(0, keys.length - MAX_IMAGES))) await cache.delete(k);
  } finally { trimming = false; }
}
