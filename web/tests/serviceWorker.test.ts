import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";

/**
 * The service worker is plain JS served from /public. It is run here in a sandbox with a fake `self`, `caches` and `fetch`, to
 * prove the rule that matters for privacy: it only ever stores the app shell and hashed static files, never an API answer.
 */
const SW = readFileSync(resolve(__dirname, "../public/sw.js"), "utf8");
const MANIFEST = JSON.parse(readFileSync(resolve(__dirname, "../public/manifest.webmanifest"), "utf8"));

type Handler = (e: unknown) => void;
function boot(network: (req: Request) => Promise<Response>) {
  const handlers: Record<string, Handler> = {};
  const stored = new Map<string, Response>();
  const caches = {
    open: async () => ({ addAll: async () => undefined, put: async (k: string | Request, v: Response) => { stored.set(typeof k === "string" ? k : new URL(k.url).pathname, v); } }),
    match: async (k: string | Request) => stored.get(typeof k === "string" ? k : new URL(k.url).pathname),
    keys: async () => [],
    delete: async () => true,
  };
  const self = { location: { origin: "https://app.test" }, addEventListener: (t: string, h: Handler) => { handlers[t] = h; }, skipWaiting: async () => undefined, clients: { claim: async () => undefined } };
  new Function("self", "caches", "fetch", SW)(self, caches, network);
  const run = async (path: string, init: { method?: string; mode?: string; origin?: string } = {}) => {
    const req = new Request(`${init.origin ?? "https://app.test"}${path}`, { method: init.method ?? "GET" });
    Object.defineProperty(req, "mode", { value: init.mode ?? "cors" });
    let responded: Promise<Response> | null = null;
    handlers.fetch({ request: req, respondWith: (p: Promise<Response>) => { responded = p; } });
    const res = responded ? await (responded as Promise<Response>) : null;
    await new Promise((r) => setTimeout(r, 0));
    return { intercepted: responded !== null, res };
  };
  return { run, stored };
}

const ok = (body = "x", headers: Record<string, string> = {}) => { const r = new Response(body, { status: 200, headers }); Object.defineProperty(r, "type", { value: "basic" }); return r; };

describe("service worker", () => {
  let sw: ReturnType<typeof boot>;
  beforeEach(() => { sw = boot(async () => ok("{\"patient\":\"secret\"}")); });

  it("never intercepts, and so never stores, an API call or any non-GET request", async () => {
    for (const path of ["/cases", "/cases/abc", "/cases/abc/access-log", "/me", "/auth/dev-login", "/health", "/metrics/closure", "/agency/org", "/drafts/1/approve", "/hl7"]) {
      const r = await sw.run(path);
      expect(r.intercepted, path).toBe(false);
    }
    expect((await sw.run("/cases/abc", { method: "POST" })).intercepted).toBe(false);
    expect((await sw.run("/cases/abc/events", { method: "POST" })).intercepted).toBe(false);
    expect(sw.stored.size).toBe(0);
  });

  it("never intercepts a request to another origin (the API may live there)", async () => {
    expect((await sw.run("/cases/abc", { origin: "https://api.test" })).intercepted).toBe(false);
    expect((await sw.run("/assets/app.js", { origin: "https://cdn.test" })).intercepted).toBe(false);
  });

  it("caches the app shell for a page visit and the hashed assets, and nothing else", async () => {
    const page = await sw.run("/patient/abc", { mode: "navigate" });
    expect(page.intercepted).toBe(true);
    expect([...sw.stored.keys()]).toEqual(["/index.html"]);
    const asset = await sw.run("/assets/index-1a2b3c.js");
    expect(asset.intercepted).toBe(true);
    expect([...sw.stored.keys()].sort()).toEqual(["/assets/index-1a2b3c.js", "/index.html"]);
  });

  it("does not store a response marked private or no-store, nor an error", async () => {
    for (const cc of ["no-store", "private, max-age=0"]) {
      const s = boot(async () => ok("x", { "cache-control": cc }));
      await s.run("/assets/a.js");
      expect(s.stored.size, cc).toBe(0);
    }
    const bad = boot(async () => { const r = new Response("no", { status: 500 }); Object.defineProperty(r, "type", { value: "basic" }); return r; });
    await bad.run("/assets/a.js");
    expect(bad.stored.size).toBe(0);
  });

  it("offline, a page opens from the cached shell", async () => {
    let up = true;
    const s = boot(async () => { if (!up) throw new TypeError("offline"); return ok("<html>shell</html>"); });
    await s.run("/patient/abc", { mode: "navigate" });
    up = false;
    const r = await s.run("/patient/abc", { mode: "navigate" });
    expect(await r.res!.text()).toBe("<html>shell</html>");
  });
});

describe("manifest", () => {
  it("is an installable standalone app that starts at /patient, with icons and a privacy shortcut", () => {
    expect(MANIFEST).toMatchObject({ start_url: "/patient", display: "standalone", scope: "/", id: "/patient" });
    expect(MANIFEST.icons.some((i: { sizes: string }) => i.sizes === "192x192")).toBe(true);
    expect(MANIFEST.icons.some((i: { sizes: string }) => i.sizes === "512x512")).toBe(true);
    expect(MANIFEST.shortcuts.map((s: { url: string }) => s.url)).toContain("/patient?tab=privacy");
    expect(MANIFEST.description).toMatch(/synthetic/i);
  });
});
