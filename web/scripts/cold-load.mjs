// Cold-load timing of the built app under a throttled network: node scripts/cold-load.mjs <dist-dir> [runs]
// Serves <dist-dir> from a tiny gzip server (hashed assets immutable, index.html no-cache, like the edge), answers GET /api/health
// after 300 ms, then loads "/" in headless Chromium with Fast 3G network and 4x CPU throttling and reports the time until the
// role cards are on screen. No external host is contacted. Used for the before/after numbers in the cold-load PR.
import { createServer } from "node:http";
import { readFileSync, existsSync, statSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { extname, join, resolve } from "node:path";
import { chromium } from "@playwright/test";
import { localChromium } from "../pw-chromium.ts";

const dist = resolve(process.argv[2] ?? "dist");
const runs = Number(process.argv[3] ?? 3);
const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2", ".woff": "font/woff", ".svg": "image/svg+xml", ".png": "image/png", ".webmanifest": "application/manifest+json" };
const server = createServer((req, res) => {
  const path = new URL(req.url, "http://x").pathname;
  if (path === "/api/health") {
    setTimeout(() => { res.setHeader("content-type", "application/json"); res.end(JSON.stringify({ status: "ok", demo: { deployment: true, simulated_clock: true } })); }, 300);
    return;
  }
  let file = join(dist, path);
  const known = existsSync(file) && statSync(file).isFile();
  if (!known) { if (path.startsWith("/assets/")) { res.statusCode = 404; return res.end(); } file = join(dist, "index.html"); }
  const ext = extname(file);
  let body = readFileSync(file);
  res.setHeader("content-type", types[ext] ?? "application/octet-stream");
  res.setHeader("cache-control", path.startsWith("/assets/") ? "public, max-age=31536000, immutable" : "no-cache");
  if ([".html", ".js", ".css", ".svg"].includes(ext)) { body = gzipSync(body); res.setHeader("content-encoding", "gzip"); }
  res.end(body);
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ executablePath: localChromium() });
const results = [];
for (let i = 0; i < runs; i++) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.error("pageerror:", e.message));
  page.on("console", (m) => m.type() === "error" && console.error("console:", m.text()));
  const cdp = await ctx.newCDPSession(page);
  await cdp.send("Network.enable");
  if (!process.env.COLD_NO_THROTTLE) await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 562, downloadThroughput: (1.6 * 1024 * 1024) / 8, uploadThroughput: (750 * 1024) / 8 });
  if (!process.env.COLD_NO_THROTTLE) await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
  let bytes = 0, reqs = 0;
  const names = new Map();
  cdp.on("Network.requestWillBeSent", (e) => { reqs++; names.set(e.requestId, e.request.url.replace(base, "")); });
  cdp.on("Network.loadingFinished", (e) => { bytes += e.encodedDataLength; if (process.env.COLD_VERBOSE) console.log("  ", Math.round(e.encodedDataLength / 1024) + " KB", names.get(e.requestId)); });
  const t0 = Date.now();
  await page.goto(base + "/", { waitUntil: "commit" });
  const fcp = await page.waitForFunction(() => document.body.innerText.includes("Screening Closure"), null, { timeout: 20000 }).then(() => Date.now() - t0);
  await page.waitForSelector('[data-testid="role-cards"]', { timeout: 20000 });
  const cards = Date.now() - t0;
  results.push({ firstText: fcp, roleCards: cards, requests: reqs, kbOnWire: Math.round(bytes / 1024) });
  await ctx.close();
}
await browser.close(); server.close();
console.table(results);
const med = (k) => results.map((r) => r[k]).sort((a, b) => a - b)[Math.floor(results.length / 2)];
console.log(`median first text ${med("firstText")} ms, role cards ${med("roleCards")} ms`);
