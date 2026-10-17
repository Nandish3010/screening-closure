import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../src/api/contract";
import { createHttpAdapter } from "../src/api/httpAdapter";

function fakeFetch(routes: Record<string, { status?: number; body: unknown }>) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const u = String(url);
    calls.push({ url: u, init });
    const key = `${init?.method ?? "GET"} ${u.replace("http://api.test", "")}`;
    const r = routes[key];
    if (!r) return new Response(JSON.stringify({ detail: "no route " + key }), { status: 404 });
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200 });
  });
  return { fn: fn as unknown as typeof fetch, calls };
}

describe("http adapter", () => {
  it("passes list filters as query params (omits empty ones) and trims the base url slash", async () => {
    const { fn, calls } = fakeFetch({ "GET /cases?state=closed&owner=Sara&overdue=true": { body: [] }, "GET /cases": { body: [] } });
    const api = createHttpAdapter("http://api.test/", fn);
    await api.listCases({ state: "closed", owner: "Sara", overdue: true });
    await api.listCases({});
    expect(calls.map((c) => c.url)).toEqual(["http://api.test/cases?state=closed&owner=Sara&overdue=true", "http://api.test/cases"]);
  });
  it("posts events with a generated event_id and JSON body", async () => {
    const detail = { case_id: "c1", state: "acknowledged", events: [], due_at: null };
    const { fn, calls } = fakeFetch({ "POST /cases/c1/events": { body: detail } });
    const api = createHttpAdapter("http://api.test", fn);
    const out = await api.postEvent("c1", { type: "acknowledge", actor: "coordinator:m" });
    expect(out.state).toBe("acknowledged");
    const sent = JSON.parse(String(calls[0].init?.body));
    expect(sent).toMatchObject({ type: "acknowledge", actor: "coordinator:m", payload: {} });
    expect(sent.event_id).toMatch(/^[0-9a-f-]{36}$/);
    expect((calls[0].init?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });
  it("maps non-2xx to ApiError with the server detail", async () => {
    const { fn } = fakeFetch({ "POST /cases/c1/events": { status: 409, body: { detail: "event 'close' not allowed from state 'acknowledged'" } } });
    const api = createHttpAdapter("http://api.test", fn);
    await expect(api.postEvent("c1", { type: "close", actor: "x" })).rejects.toMatchObject({ status: 409, message: expect.stringContaining("not allowed") });
  });
  it("maps network failure to ApiError(0)", async () => {
    const api = createHttpAdapter("http://api.test", (async () => { throw new TypeError("fetch failed"); }) as typeof fetch);
    const err = await api.health().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
  });
  it("degrades: /me 404 -> coordinator; timeline 404 -> events from GET /cases/{id}", async () => {
    const detail = { case_id: "c1", state: "result_received", events: [{ seq: 1, type: "case_created", at: "2026-10-01T00:00:00Z", actor: "system:intake", payload: {} }] };
    const { fn } = fakeFetch({ "GET /cases/c1": { body: detail } });
    const api = createHttpAdapter("http://api.test", fn);
    expect((await api.me()).role).toBe("coordinator");
    const tl = await api.getTimeline("c1");
    expect(tl.items).toHaveLength(1);
    expect(tl.items[0]).toMatchObject({ kind: "event", type: "case_created" });
  });
  it("getCase passes the A2 read-model fields through and derives missing ones from the events", async () => {
    const full = { case_id: "c1", state: "acknowledged", events: [], owner: "Sara", due_at: "2026-11-01T00:00:00Z", overdue: true, escalation_level: 2, consent_frozen: true, last_event_at: "2026-10-30T00:00:00Z" };
    const bare = {
      case_id: "c2", state: "owner_accepted", updated_at: "2026-10-05T00:00:00Z",
      events: [
        { seq: 1, type: "accept_ownership", at: "2026-10-01T00:00:00Z", actor: "coordinator:amal", payload: { owner: "Amal" } },
        { seq: 2, type: "mark_overdue", at: "2026-10-04T00:00:00Z", actor: "system:scheduler", payload: { state: "owner_accepted", due_at: "2026-10-04T00:00:00Z" } },
      ],
    };
    const { fn } = fakeFetch({ "GET /cases/c1": { body: full }, "GET /cases/c2": { body: bare } });
    const api = createHttpAdapter("http://api.test", fn);
    expect(await api.getCase("c1")).toMatchObject({ owner: "Sara", due_at: "2026-11-01T00:00:00Z", overdue: true, escalation_level: 2, consent_frozen: true });
    expect(await api.getCase("c2")).toMatchObject({ owner: "Amal", due_at: null, overdue: true, escalation_level: 0, consent_frozen: false, last_event_at: "2026-10-04T00:00:00Z" });
  });
  it("listCases fills defaults on rows that lack the new fields", async () => {
    const { fn } = fakeFetch({ "GET /cases": { body: [{ case_id: "c1", state: "acknowledged", created_at: "a", updated_at: "b" }] } });
    const [row] = await createHttpAdapter("http://api.test", fn).listCases();
    expect(row).toMatchObject({ owner: null, due_at: null, overdue: false, escalation_level: 0, consent_frozen: false, last_event_at: "b" });
  });
  it("uses the draft and metrics endpoints from the contract", async () => {
    const { fn, calls } = fakeFetch({
      "POST /cases/c1/drafts": { body: { draft_id: "d1", text_ar: "a", text_en: "e", provider: "p", fallback_used: false } },
      "POST /drafts/d1/approve": { body: { draft_id: "d1", approved_by: "m", approved_at: "t" } },
      "GET /metrics/closure": { body: { p50_days: 1, p90_days: 2, open_by_age_bucket: {}, breaches_by_provider: [], bottleneck_by_transition: [], sim_time: "t" } },
    });
    const api = createHttpAdapter("http://api.test", fn);
    expect((await api.requestDraft("c1", "m")).draft_id).toBe("d1");
    expect((await api.approveDraft("d1", "m")).approved_by).toBe("m");
    expect((await api.closureMetrics()).p50_days).toBe(1);
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ requested_by: "m" });
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ approved_by: "m" });
  });
});

describe("http adapter: auth", () => {
  const tokenFor: Record<string, string> = { coordinator: "tok-coord", clinician: "tok-clin" };

  /** A tiny fake backend: dev-login mints a token per role, everything else requires the bearer token. */
  function authedFetch(opts: { devLogin404?: boolean; expireOnce?: boolean } = {}) {
    const calls: { url: string; method: string; auth: string | null; body?: string }[] = [];
    let expired = !!opts.expireOnce;
    const fn = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      const path = String(url).replace("http://api.test", "");
      const headers = (init?.headers ?? {}) as Record<string, string>;
      const auth = headers.Authorization ?? null;
      const method = init?.method ?? "GET";
      calls.push({ url: path, method, auth, body: init?.body as string | undefined });
      if (path === "/auth/dev-login") {
        if (opts.devLogin404) return new Response("{}", { status: 404 });
        const { role } = JSON.parse(String(init?.body));
        return new Response(JSON.stringify({ access_token: tokenFor[role], token_type: "bearer" }), { status: 200 });
      }
      if (expired && auth) {
        expired = false;
        return new Response(JSON.stringify({ detail: "token expired" }), { status: 401 });
      }
      if (!auth) return new Response(JSON.stringify({ detail: "Not authenticated" }), { status: 401 });
      return new Response(JSON.stringify(path === "/me" ? { role: "x", name: "y" } : []), { status: 200 });
    });
    return { fn: fn as unknown as typeof fetch, calls };
  }

  it("logs in with {role, name} and attaches the bearer token to later requests", async () => {
    const { fn, calls } = authedFetch();
    const api = createHttpAdapter("http://api.test", fn);
    await api.login("coordinator", "amal");
    await api.listCases();
    expect(calls[0]).toMatchObject({ url: "/auth/dev-login", method: "POST", auth: null });
    expect(JSON.parse(calls[0].body!)).toEqual({ role: "coordinator", name: "amal" });
    expect(calls[1]).toMatchObject({ url: "/cases", auth: "Bearer tok-coord" });
  });
  it("swaps the token on a role switch, and a request issued during the switch waits for the new token", async () => {
    const { fn, calls } = authedFetch();
    const api = createHttpAdapter("http://api.test", fn);
    await api.login("coordinator", "amal");
    void api.login("clinician", "dr_ali"); // not awaited on purpose
    await api.listCases();
    expect(calls.at(-1)).toMatchObject({ url: "/cases", auth: "Bearer tok-clin" });
    expect(calls.filter((c) => c.url === "/auth/dev-login")).toHaveLength(2);
  });
  it("does not log in twice for the same identity", async () => {
    const { fn, calls } = authedFetch();
    const api = createHttpAdapter("http://api.test", fn);
    await api.login("coordinator", "amal");
    await api.login("coordinator", "amal");
    expect(calls.filter((c) => c.url === "/auth/dev-login")).toHaveLength(1);
  });
  it("re-logs-in once and retries when a request gets a 401", async () => {
    const { fn, calls } = authedFetch({ expireOnce: true });
    const api = createHttpAdapter("http://api.test", fn);
    await api.login("coordinator", "amal");
    await expect(api.listCases()).resolves.toEqual([]);
    expect(calls.map((c) => c.url)).toEqual(["/auth/dev-login", "/cases", "/auth/dev-login", "/cases"]);
    expect(calls[3].auth).toBe("Bearer tok-coord");
  });
  it("surfaces a persistent 401 instead of looping", async () => {
    const { fn, calls } = authedFetch();
    const api = createHttpAdapter("http://api.test", fn); // never logged in -> no identity -> no retry
    await expect(api.listCases()).rejects.toMatchObject({ status: 401 });
    expect(calls).toHaveLength(1);
  });
  it("carries on without a token when the backend has no dev-login (404)", async () => {
    const { fn, calls } = authedFetch({ devLogin404: true });
    const api = createHttpAdapter("http://api.test", fn);
    await expect(api.login("coordinator", "amal")).resolves.toBeUndefined();
    await api.listCases().catch(() => undefined);
    expect(calls[1].auth).toBeNull();
  });
  it("fails the login promise on a real dev-login error", async () => {
    const fn = (async () => new Response(JSON.stringify({ detail: "bad role" }), { status: 422 })) as unknown as typeof fetch;
    await expect(createHttpAdapter("http://api.test", fn).login("coordinator", "")).rejects.toMatchObject({ status: 422 });
  });
});
