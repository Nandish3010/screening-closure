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
    const detail = { case_id: "c1", state: "acknowledged", events: [], sla_due_at: null };
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
  it("getCase borrows SLA fields from the list row when the detail lacks them", async () => {
    const { fn } = fakeFetch({
      "GET /cases/c1": { body: { case_id: "c1", state: "acknowledged", events: [] } },
      "GET /cases": { body: [{ case_id: "c1", state: "acknowledged", owner: "Sara", sla_due_at: "2026-11-01T00:00:00Z", overdue_days: 3, risk_score: 0.5 }] },
    });
    const d = await createHttpAdapter("http://api.test", fn).getCase("c1");
    expect(d).toMatchObject({ owner: "Sara", overdue_days: 3 });
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
