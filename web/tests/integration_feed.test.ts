import { describe, expect, it, vi } from "vitest";
import { createHttpAdapter } from "../src/api/httpAdapter";
import { createMockAdapter } from "../src/api/mockAdapter";
import { mockIntegrationFeed } from "../src/lib/integrationFeed";

const feed = { simulated: true, synthetic: true, scope: "x", max_entries: 200, entries: [] };

describe("integration feed client", () => {
  it("the http adapter asks for a bounded limit and passes the answer through", async () => {
    const fn = vi.fn(async () => new Response(JSON.stringify(feed), { status: 200 }));
    const api = createHttpAdapter("http://api.test", fn as unknown as typeof fetch);
    expect(await api.integrationFeed!(7)).toEqual(feed);
    expect(String((fn.mock.calls as unknown as unknown[][])[0][0])).toBe("http://api.test/integrations/feed?limit=7");
  });

  it("an older backend (404) or a role that may not read it (403) gives null, not an error", async () => {
    for (const status of [404, 403]) {
      const fn = vi.fn(async () => new Response(JSON.stringify({ detail: "no" }), { status }));
      const api = createHttpAdapter("http://api.test", fn as unknown as typeof fetch);
      expect(await api.integrationFeed!(5)).toBeNull();
    }
  });

  it("the mock lists a fixed, labelled sample for staff who may read it and nothing for others", async () => {
    const api = createMockAdapter(null);
    await api.login("coordinator", "x");
    const f = (await api.integrationFeed!(3))!;
    expect(f.simulated && f.synthetic).toBe(true);
    expect(f.entries).toHaveLength(3);
    expect(f.entries.map((e) => e.seq)).toEqual([...f.entries.map((e) => e.seq)].sort((a, b) => b - a));
    await api.login("patient", "y");
    expect(await api.integrationFeed!(3)).toBeNull();
  });

  it("the sample carries metadata only and its limit is bounded", () => {
    const f = mockIntegrationFeed(1000, "2026-10-20T09:00:00Z");
    expect(f.entries.length).toBeLessThanOrEqual(f.max_entries);
    for (const e of f.entries) {
      expect(["hl7", "fhir", "notification"]).toContain(e.kind);
      expect(["in", "out"]).toContain(e.direction);
      expect(e.summary).not.toMatch(/MSH\||PID\||@/);
    }
    expect(mockIntegrationFeed(0, "2026-10-20T09:00:00Z").entries).toHaveLength(1);
  });
});
