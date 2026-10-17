/* Service worker: app shell + runtime cache so the last-loaded case renders offline.
 * - Navigations: network first, fall back to the cached app shell (index.html), so any
 *   /patient/:caseId route boots offline.
 * - Same-origin static assets (hashed JS/CSS/fonts/icons): cache first.
 * - API calls (cross-origin or /cases, /me...) are NOT cached here: the patient page keeps the
 *   last case in localStorage, which is the offline data source (and avoids serving stale clinical data
 *   from a generic HTTP cache).
 * Bump VERSION to invalidate old caches.
 */
const VERSION = "v1";
const CACHE = `sc-shell-${VERSION}`;
const SHELL = ["/", "/index.html", "/manifest.webmanifest", "/icon.svg", "/icon-192.png", "/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("sc-shell-") && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put("/index.html", copy));
          return res;
        })
        .catch(() => caches.match("/index.html").then((r) => r || caches.match("/"))),
    );
    return;
  }

  const isAsset = url.pathname.startsWith("/assets/") || /\.(png|svg|webmanifest|woff2?|ico)$/.test(url.pathname);
  if (!isAsset) return;
  event.respondWith(
    caches.match(req).then((hit) =>
      hit ||
      fetch(req).then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      }),
    ),
  );
});
