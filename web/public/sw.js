/* Service worker: the app shell and static files, so the patient app opens offline. Deliberately small and safe.
 *
 * What it caches:
 *   - the app shell (index.html, manifest, icons), and the hashed JS/CSS/font files under /assets/.
 *   Nothing else. These files are the same for every person: no case, name, date or result is ever in them.
 * What it never touches:
 *   - API calls (/cases, /me, /auth, /health, ... and every cross-origin request) and every non-GET request: the service worker
 *     does not call respondWith for them, so the browser handles them as if it were not there. A response that holds personal
 *     data is therefore never written to a cache here. The patient page keeps the one case it last showed in localStorage
 *     (what the patient already sees, removable with "Remove saved information from this phone"); that is the offline data.
 *   - any response that says `Cache-Control: no-store` or `private` is not cached.
 * Strategy: network first for pages and for the manifest/icons (so a new version arrives at once), cache first for the
 * hashed /assets/ files (their name changes with their content).
 * Bump VERSION to invalidate old caches.
 */
const VERSION = "v2";
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

/** Safe to store: a successful, same-origin, non-private response. */
function cacheable(res) {
  if (!res || !res.ok || res.type !== "basic") return false;
  const cc = (res.headers.get("cache-control") || "").toLowerCase();
  return !/no-store|private/.test(cc);
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // the API may be on another origin: never ours to cache

  if (req.mode === "navigate") {
    // Pages: network first; offline, the cached shell boots the app, which shows what the phone saved.
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (cacheable(res)) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put("/index.html", copy));
          }
          return res;
        })
        .catch(() => caches.match("/index.html").then((r) => r || caches.match("/"))),
    );
    return;
  }

  const hashed = url.pathname.startsWith("/assets/");
  const staticFile = SHELL.includes(url.pathname) || /\.(png|svg|webmanifest|woff2?|ico)$/.test(url.pathname);
  if (!hashed && !staticFile) return; // anything else, including a same-origin API path, goes straight to the network

  if (hashed) {
    event.respondWith(
      caches.match(req).then((hit) =>
        hit ||
        fetch(req).then((res) => {
          if (cacheable(res)) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return res;
        }),
      ),
    );
    return;
  }
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (cacheable(res)) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req)),
  );
});
