import { beforeEach, describe, expect, it } from "vitest";
import { ApiError, type ApiClient } from "../src/api/contract";
import { DEMO_OVERDUE_CASE_ID, DEMO_PATIENT_CASE_ID, SIM_NOW } from "../src/api/fixtures";
import { createMockAdapter } from "../src/api/mockAdapter";

let api: ApiClient;
beforeEach(() => {
  api = createMockAdapter(null);
});

describe("mock adapter: reads", () => {
  it("lists seeded cases with the PENDING summary fields", async () => {
    const cases = await api.listCases();
    expect(cases.length).toBeGreaterThanOrEqual(10);
    const top = cases.find((c) => c.case_id === DEMO_OVERDUE_CASE_ID)!;
    expect(top.state).toBe("confirmatory_completed");
    expect(top.overdue_days).toBeGreaterThan(10);
    expect(top.owner).toBe("Dr. Layla Haddad");
    expect(typeof top.risk_score).toBe("number");
    expect(top.sla_due_at).toBeTruthy();
  });
  it("filters by state, owner and overdue", async () => {
    expect((await api.listCases({ state: "closed" })).every((c) => c.state === "closed")).toBe(true);
    expect((await api.listCases({ owner: "Omar Khalid" })).every((c) => c.owner === "Omar Khalid")).toBe(true);
    const overdue = await api.listCases({ overdue: true });
    expect(overdue.length).toBeGreaterThan(2);
    expect(overdue.every((c) => (c.overdue_days ?? 0) > 0)).toBe(true);
  });
  it("404s an unknown case", async () => {
    await expect(api.getCase("nope")).rejects.toMatchObject({ status: 404 });
  });
  it("builds a timeline with SLA breach and escalation items for an overdue case", async () => {
    const tl = await api.getTimeline(DEMO_OVERDUE_CASE_ID);
    const kinds = new Set(tl.items.map((i) => i.kind));
    expect(kinds).toContain("sla_breach");
    expect(kinds).toContain("escalation");
    expect(tl.sim_time).toBe(SIM_NOW);
    const ats = tl.items.map((i) => i.at);
    expect([...ats].sort()).toEqual(ats);
  });
  it("serves metrics with the sim clock", async () => {
    const m = await api.closureMetrics();
    expect(m.sim_time).toBe(SIM_NOW);
    expect(Object.keys(m.open_by_age_bucket)).toEqual(["0-30", "31-60", "61-90", "90+"]);
  });
});

describe("mock adapter: writes mirror the engine", () => {
  it("rejects an invalid transition with 409 and writes nothing", async () => {
    const before = (await api.getCase(DEMO_PATIENT_CASE_ID)).events.length;
    await expect(api.postEvent(DEMO_PATIENT_CASE_ID, { type: "close", actor: "clinician:x", payload: { evidence_ref: "e", signed_by: "s" } }))
      .rejects.toMatchObject({ status: 409 });
    expect((await api.getCase(DEMO_PATIENT_CASE_ID)).events).toHaveLength(before);
  });
  it("close requires evidence_ref and signed_by, and refuses automated actors", async () => {
    const id = DEMO_OVERDUE_CASE_ID;
    await api.postEvent(id, { type: "clinician_review", actor: "clinician:Dr X", payload: { signed_by: "Dr X" } });
    await expect(api.postEvent(id, { type: "close", actor: "clinician:Dr X", payload: { signed_by: "Dr X" } })).rejects.toThrow(/evidence_ref/);
    await expect(api.postEvent(id, { type: "close", actor: "system:auto", payload: { evidence_ref: "e", signed_by: "s" } })).rejects.toThrow(/automated/);
    const closed = await api.postEvent(id, { type: "close", actor: "clinician:Dr X", payload: { evidence_ref: "DiagnosticReport/1", signed_by: "Dr X" } });
    expect(closed.state).toBe("closed");
    await expect(api.postEvent(id, { type: "escalate", actor: "coordinator:m" })).rejects.toBeInstanceOf(ApiError);
  });
  it("is idempotent on event_id", async () => {
    const body = { type: "callback_requested", actor: "patient:x", payload: {}, event_id: "11111111-1111-4111-8111-111111111111" };
    const a = await api.postEvent(DEMO_PATIENT_CASE_ID, body);
    const b = await api.postEvent(DEMO_PATIENT_CASE_ID, body);
    expect(b.events).toHaveLength(a.events.length);
  });
  it("annotation events do not change state and a transition resets the SLA clock", async () => {
    const before = await api.getCase(DEMO_OVERDUE_CASE_ID);
    const esc = await api.postEvent(DEMO_OVERDUE_CASE_ID, { type: "escalate", actor: "coordinator:m", payload: { notify: "owner_supervisor" } });
    expect(esc.state).toBe(before.state);
    expect(esc.overdue_days).toBe(before.overdue_days);
    const advanced = await api.postEvent(DEMO_OVERDUE_CASE_ID, { type: "clinician_review", actor: "clinician:x", payload: { signed_by: "x" } });
    expect(advanced.overdue_days).toBe(0);
  });
  it("first provider rejects a hand-off with a reason, others accept", async () => {
    const r = await api.postEvent(DEMO_PATIENT_CASE_ID, { type: "hand_off", actor: "coordinator:m", payload: { to_provider: "Al Noor Endoscopy Centre" } });
    expect(r.events.at(-1)).toMatchObject({ type: "hand_off_rejected", payload: { reason: expect.stringContaining("slots") } });
    const a = await api.postEvent(DEMO_PATIENT_CASE_ID, { type: "hand_off", actor: "coordinator:m", payload: { to_provider: "Corniche Gastro Unit" } });
    expect(a.events.at(-1)?.type).toBe("hand_off_accepted");
  });
  it("draft: generate then approve writes provenance events", async () => {
    const d = await api.requestDraft(DEMO_PATIENT_CASE_ID, "coordinator:m");
    expect(d).toMatchObject({ provider: expect.any(String), fallback_used: expect.any(Boolean) });
    expect(d.text_ar.length).toBeGreaterThan(5);
    const ap = await api.approveDraft(d.draft_id, "Maryam");
    expect(ap.approved_by).toBe("Maryam");
    const types = (await api.getCase(DEMO_PATIENT_CASE_ID)).events.map((e) => e.type);
    expect(types.slice(-2)).toEqual(["draft_generated", "draft_approved"]);
    await expect(api.approveDraft("missing", "x")).rejects.toMatchObject({ status: 404 });
  });
  it("persists to storage so a second adapter (another tab) sees the change", async () => {
    const store = new Map<string, string>();
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v), removeItem: (k: string) => void store.delete(k) };
    const a = createMockAdapter(storage);
    const b = createMockAdapter(storage);
    await a.postEvent(DEMO_PATIENT_CASE_ID, { type: "callback_requested", actor: "patient:x", payload: {} });
    expect((await b.getCase(DEMO_PATIENT_CASE_ID)).events.at(-1)?.type).toBe("callback_requested");
    await a.resetDemo!();
    expect((await b.getCase(DEMO_PATIENT_CASE_ID)).events.some((e) => e.type === "callback_requested")).toBe(false);
  });
});
