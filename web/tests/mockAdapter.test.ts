import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { ApiError, type ApiClient } from "../src/api/contract";
import { DEMO_OVERDUE_CASE_ID, DEMO_PATIENT_CASE_ID, SIM_NOW, seedDrafts } from "../src/api/fixtures";
import { ROLE_PROVIDERS } from "../src/api/identities";
import { DRAFT_TEXT, createMockAdapter } from "../src/api/mockAdapter";
import { overdueDays } from "../src/lib/derive";

let api: ApiClient;
beforeEach(() => {
  api = createMockAdapter(null);
});

describe("mock adapter: reads", () => {
  it("lists seeded cases with the A2 read-model fields", async () => {
    const cases = await api.listCases();
    expect(cases.length).toBeGreaterThanOrEqual(10);
    const top = cases.find((c) => c.case_id === DEMO_OVERDUE_CASE_ID)!;
    expect(top.state).toBe("confirmatory_completed");
    expect(top.overdue).toBe(true);
    expect(overdueDays(top.due_at, SIM_NOW)).toBeGreaterThan(10);
    expect(top.owner).toBe("Dr. Layla Haddad");
    expect(top.owner_actor).toBe("primary_care_owner:Dr. Layla Haddad");
    expect(typeof top.risk_score).toBe("number");
    expect(top.due_at).toBeTruthy();
    expect(top.escalation_level).toBe(0);
    expect(top.consent_frozen).toBe(false);
    expect(top.last_event_at).toBe(top.updated_at);
    expect("overdue_days" in top).toBe(false);
    expect("sla_due_at" in top).toBe(false);
    const closed = cases.find((c) => c.state === "closed")!;
    expect(closed).toMatchObject({ due_at: null, overdue: false });
  });
  it("carries escalation level, frozen consent and watchdog flags on the read model", async () => {
    const cases = await api.listCases();
    const escalated = cases.filter((c) => c.escalation_level > 0);
    expect(escalated.map((c) => c.escalation_level)).toEqual([2]);
    expect(cases.filter((c) => c.consent_frozen)).toHaveLength(1);
    const flagged = cases.filter((c) => c.watchdog_flagged);
    expect(flagged).toHaveLength(1);
    expect(flagged[0].watchdog_flagged).toMatchObject({ reason: "idle", idle_days: 11 });
  });
  it("seeds the scheduler's mark_overdue for overdue cases, with state and due_at", async () => {
    const d = await api.getCase(DEMO_OVERDUE_CASE_ID);
    const mark = d.events.find((e) => e.type === "mark_overdue")!;
    expect(mark).toMatchObject({ actor: "system:scheduler", payload: { state: "confirmatory_completed", due_at: d.due_at } });
    const seqs = d.events.map((e) => e.seq);
    expect(seqs).toEqual(seqs.map((_, i) => i + 1));
  });
  it("filters by state, owner and overdue", async () => {
    expect((await api.listCases({ state: "closed" })).every((c) => c.state === "closed")).toBe(true);
    expect((await api.listCases({ owner: "Omar Khalid" })).every((c) => c.owner === "Omar Khalid")).toBe(true);
    const overdue = await api.listCases({ overdue: true });
    expect(overdue.length).toBeGreaterThan(2);
    expect(overdue.every((c) => c.overdue)).toBe(true);
  });
  it("404s an unknown case", async () => {
    await expect(api.getCase("nope")).rejects.toMatchObject({ status: 404 });
  });
  it("builds a timeline with derived SLA, escalation, bounce and watchdog items for the cases that have them", async () => {
    const tl = await api.getTimeline(DEMO_OVERDUE_CASE_ID);
    const kinds = new Set(tl.items.map((i) => i.kind));
    expect(kinds).toContain("event");
    expect(kinds).toContain("sla_started");
    expect(kinds).toContain("sla_breached");
    expect(kinds).toContain("escalation");
    expect(tl.items.every((i) => typeof i.kind === "string" && typeof i.at === "string")).toBe(true);
    expect((await api.getTimeline(cid(15))).items.find((i) => i.kind === "bounce")).toMatchObject({ to_provider: "Marina Digestive Health", reason: "timeout" });
    expect((await api.getTimeline(cid(13))).items.find((i) => i.kind === "watchdog")).toMatchObject({ reason: "idle", idle_days: 11 });
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
    const esc = await api.postEvent(DEMO_OVERDUE_CASE_ID, { type: "escalate", actor: "coordinator:m", payload: { rung: "supervisor", target_role: "supervisor" } });
    expect(esc.state).toBe(before.state);
    expect(esc.overdue).toBe(before.overdue);
    expect(esc.escalation_level).toBe(2);
    const advanced = await api.postEvent(DEMO_OVERDUE_CASE_ID, { type: "clinician_review", actor: "clinician:x", payload: { signed_by: "x" } });
    expect(advanced.overdue).toBe(false);
    expect(advanced.escalation_level).toBe(0);
  });
  it("first provider rejects a hand-off with a taxonomy reason, others accept (and ownership moves only then)", async () => {
    const r = await api.postEvent(DEMO_PATIENT_CASE_ID, { type: "handoff_requested", actor: "primary_care_owner:Sara Al Mansoori", payload: { to_provider: "Al Noor Endoscopy Centre" } });
    expect(r.events.at(-1)).toMatchObject({ type: "handoff_rejected", payload: { reason: "capacity" } });
    expect(r).toMatchObject({ owner: "Sara Al Mansoori", pending_handoff: null, last_handoff: { outcome: "rejected", reason: "capacity", to_provider: "Al Noor Endoscopy Centre" } });
    const a = await api.postEvent(DEMO_PATIENT_CASE_ID, { type: "handoff_requested", actor: "primary_care_owner:Sara Al Mansoori", payload: { to_provider: "Corniche Gastro Unit" } });
    expect(a.events.at(-1)?.type).toBe("handoff_accepted");
    expect(a).toMatchObject({ owner: "Dr. Karim Nasser", owner_actor: "specialist_owner:Dr. Karim Nasser", provider: "Corniche Gastro Unit", pending_handoff: null });
    expect(a.last_handoff).toMatchObject({ outcome: "accepted", to_provider: "Corniche Gastro Unit", by: "specialist_owner:Dr. Karim Nasser" });
  });
  it("rejects the old event names", async () => {
    for (const type of ["hand_off", "hand_off_accepted", "hand_off_rejected", "draft_generated", "draft_approved"]) {
      await expect(api.postEvent(DEMO_PATIENT_CASE_ID, { type, actor: "coordinator:m", payload: {} })).rejects.toMatchObject({ status: 409 });
    }
  });
  it("accepts the human escalation acceptance and the lost-contact marker as annotations", async () => {
    const before = await api.getCase(DEMO_PATIENT_CASE_ID);
    await api.postEvent(DEMO_PATIENT_CASE_ID, { type: "escalation_accepted", actor: "coordinator:m", payload: { rung: "supervisor" } });
    const after = await api.postEvent(DEMO_PATIENT_CASE_ID, { type: "mark_lost_contact", actor: "coordinator:m", payload: {} });
    expect(after.state).toBe(before.state);
    expect(after.events.slice(-2).map((e) => e.type)).toEqual(["escalation_accepted", "mark_lost_contact"]);
  });
  it("reopen: clinician only, needs a reason, closed cases only", async () => {
    const closed = (await api.listCases({ state: "closed" }))[0].case_id;
    await expect(api.postEvent(closed, { type: "reopen", actor: "coordinator:m", payload: { reason: "new finding" } })).rejects.toMatchObject({ status: 403 });
    await expect(api.postEvent(closed, { type: "reopen", actor: "clinician:dr_ali", payload: {} })).rejects.toThrow(/reason/);
    await expect(api.postEvent(DEMO_PATIENT_CASE_ID, { type: "reopen", actor: "clinician:dr_ali", payload: { reason: "x" } })).rejects.toMatchObject({ status: 409 });
    const reopened = await api.postEvent(closed, { type: "reopen", actor: "clinician:dr_ali", payload: { reason: "new finding" } });
    expect(reopened.state).toBe("reopened");
    expect(reopened.events.at(-1)).toMatchObject({ type: "reopen", payload: { reason: "new finding" } });
  });
  it("login records who is acting and the provider a clinician acts for; me() reports it", async () => {
    await expect(api.login("clinician", "Dr. Huda Rahman", ROLE_PROVIDERS.clinician)).resolves.toBeUndefined();
    expect(await api.me()).toMatchObject({ role: "clinician", name: "Dr. Huda Rahman", user_id: "primary_care_owner:Dr. Huda Rahman", provider: "Marina Digestive Health" });
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

const cid = (n: number) => `c${String(n).padStart(7, "0")}-0000-4000-8000-${String(n).padStart(12, "0")}`;
const OWNER = "primary_care_owner:Dr. Layla Haddad";
const request = { purpose: "result_notification", channel: "sms", language: "en" } as const;

describe("mock adapter: drafts and the approval gate (D4, D5)", () => {
  it("requesting a draft returns the D4 shape and writes a message_drafted event WITHOUT text", async () => {
    await api.login("coordinator", "Maryam Al Suwaidi");
    const d = await api.requestDraft(DEMO_PATIENT_CASE_ID, request);
    expect(d).toMatchObject({ language: "en", provider: "template", fallback_used: false, abstention_reason: null, text_ar: null });
    expect(d.text).toBe(d.text_en);
    expect(d.prompt_hash).toMatch(/^[0-9a-f]{64}$/);
    const ar = await api.requestDraft(DEMO_PATIENT_CASE_ID, { ...request, language: "ar" });
    expect(ar).toMatchObject({ language: "ar", text_en: null });
    expect(ar.text_ar).toMatch(/تنظير القولون/);
    const evs = (await api.getCase(DEMO_PATIENT_CASE_ID)).events;
    expect(evs.at(-2)).toMatchObject({ type: "message_drafted", actor: "model:template", payload: { draft_id: d.draft_id, provider: "template", fallback_used: false, prompt_hash: d.prompt_hash, language: "en" } });
    expect(evs.at(-2)?.payload).not.toHaveProperty("text_en");
    expect(evs.at(-2)?.payload).not.toHaveProperty("text_ar");
    const listed = await api.listDrafts(DEMO_PATIENT_CASE_ID);
    expect(listed.map((x) => x.draft_id)).toEqual([d.draft_id, ar.draft_id]);
    expect(listed[0]).toMatchObject({ case_id: DEMO_PATIENT_CASE_ID, purpose: "result_notification", channel: "sms", text: d.text, approved_at: null, rejected_at: null });
  });

  it("lists the seeded drafts, including a fallback with an abstention reason", async () => {
    const drafts = await api.listDrafts(cid(2));
    expect(drafts[0]).toMatchObject({ draft_id: "d-seed-2", fallback_used: true, abstention_reason: "provider_unavailable", approved_at: null });
  });

  it("a coordinator may request but not approve or reject: 403, nothing written", async () => {
    await api.login("coordinator", "Maryam Al Suwaidi");
    const d = await api.requestDraft(DEMO_PATIENT_CASE_ID, request);
    const before = (await api.getCase(DEMO_PATIENT_CASE_ID)).events.length;
    await expect(api.approveDraft(d.draft_id)).rejects.toMatchObject({ status: 403 });
    await expect(api.rejectDraft(d.draft_id, { reason: "not_needed" })).rejects.toMatchObject({ status: 403 });
    expect((await api.getCase(DEMO_PATIENT_CASE_ID)).events).toHaveLength(before);
  });

  it("only the case's owner approves; another clinician gets 403 (owner scoping)", async () => {
    await api.login("coordinator", "Maryam Al Suwaidi");
    const d = await api.requestDraft(DEMO_OVERDUE_CASE_ID, request);
    await api.login("clinician", "Dr. Huda Rahman", ROLE_PROVIDERS.clinician);
    await expect(api.approveDraft(d.draft_id)).rejects.toMatchObject({ status: 403, message: expect.stringContaining("Dr. Layla Haddad") });
  });

  it("approve: no body, approved_by is the actor, message_approved then message_sent from system:notifier", async () => {
    await api.login("owner", "Dr. Layla Haddad", ROLE_PROVIDERS.owner);
    const d = await api.requestDraft(DEMO_OVERDUE_CASE_ID, request);
    const ap = await api.approveDraft(d.draft_id);
    expect(ap).toMatchObject({ draft_id: d.draft_id, approved_by: OWNER, sent: true, blocked_reason: null, notification_id: expect.any(String) });
    const evs = (await api.getCase(DEMO_OVERDUE_CASE_ID)).events;
    expect(evs.slice(-3).map((e) => e.type)).toEqual(["message_drafted", "message_approved", "message_sent"]);
    expect(evs.at(-2)).toMatchObject({ actor: OWNER, payload: { draft_id: d.draft_id } });
    expect(evs.at(-1)).toMatchObject({ actor: "system:notifier", payload: { draft_id: d.draft_id, channel: "sms", language: "en", purpose: "result_notification", notification_id: ap.notification_id } });
    expect((await api.listDrafts(DEMO_OVERDUE_CASE_ID)).at(-1)).toMatchObject({ approved_by: OWNER, rejected_at: null });
    await expect(api.approveDraft(d.draft_id)).rejects.toMatchObject({ status: 409 });
    await expect(api.rejectDraft(d.draft_id, { reason: "tone" })).rejects.toMatchObject({ status: 409 });
    await expect(api.approveDraft("missing")).rejects.toMatchObject({ status: 404 });
  });

  it("an approval whose channel the patient switched off stands but is blocked: sent false, reason consent, no message_sent", async () => {
    await api.postEvent(DEMO_OVERDUE_CASE_ID, { type: "consent_updated", actor: "patient:x", payload: { channel_sms: false } });
    await api.login("owner", "Dr. Layla Haddad", ROLE_PROVIDERS.owner);
    const d = await api.requestDraft(DEMO_OVERDUE_CASE_ID, request);
    const ap = await api.approveDraft(d.draft_id);
    expect(ap).toMatchObject({ sent: false, blocked_reason: "consent", approved_by: OWNER });
    const types = (await api.getCase(DEMO_OVERDUE_CASE_ID)).events.slice(-2).map((e) => e.type);
    expect(types).toEqual(["message_drafted", "message_approved"]);
  });

  it("reject (A16): a reason from the taxonomy (422 otherwise), message_rejected in the log, the note only on the draft, then 409 on a second decision", async () => {
    await api.login("owner", "Dr. Layla Haddad", ROLE_PROVIDERS.owner);
    const d = await api.requestDraft(DEMO_OVERDUE_CASE_ID, request);
    const before = (await api.getCase(DEMO_OVERDUE_CASE_ID)).events.length;
    for (const bad of [
      { reason: "   " }, { reason: "Wrong tone for this patient" }, { reason: "other_with_note" }, { reason: "other_with_note", note: "  " },
      { reason: "tone", note: "a note needs other_with_note" }, { reason: "other_with_note", note: "x".repeat(201) },
    ]) {
      await expect(api.rejectDraft(d.draft_id, bad as never)).rejects.toMatchObject({ status: 422 });
    }
    expect((await api.getCase(DEMO_OVERDUE_CASE_ID)).events).toHaveLength(before); // nothing written by a refused call
    const rej = await api.rejectDraft(d.draft_id, { reason: "tone" });
    expect(rej).toMatchObject({ draft_id: d.draft_id, rejected_by: OWNER, reason: "tone", note: null });
    const evs = (await api.getCase(DEMO_OVERDUE_CASE_ID)).events;
    expect(evs).toHaveLength(before + 1);
    expect(evs.at(-1)).toMatchObject({ type: "message_rejected", actor: OWNER, payload: { draft_id: d.draft_id, reason: "tone" } });
    expect(Object.keys(evs.at(-1)!.payload).sort()).toEqual(["draft_id", "reason"]); // no note, no text in the event
    expect((await api.listDrafts(DEMO_OVERDUE_CASE_ID)).at(-1)).toMatchObject({ rejected_by: OWNER, rejection_reason: "tone", approved_at: null });
    await expect(api.approveDraft(d.draft_id)).rejects.toMatchObject({ status: 409 });
  });

  it("reject with other_with_note keeps the note on the draft row, not in the event", async () => {
    await api.login("owner", "Dr. Layla Haddad", ROLE_PROVIDERS.owner);
    const d = await api.requestDraft(DEMO_OVERDUE_CASE_ID, request);
    await expect(api.rejectDraft(d.draft_id, { reason: "other_with_note", note: " wrong clinic named " })).resolves.toMatchObject({ reason: "other_with_note", note: "wrong clinic named" });
    expect((await api.getCase(DEMO_OVERDUE_CASE_ID)).events.at(-1)).toMatchObject({ type: "message_rejected", payload: { draft_id: d.draft_id, reason: "other_with_note" } });
    expect((await api.listDrafts(DEMO_OVERDUE_CASE_ID)).at(-1)).toMatchObject({ rejection_reason: "other_with_note: wrong clinic named" });
  });

  it("message_rejected cannot be posted through the events route (403 for every role)", async () => {
    await expect(api.postEvent(DEMO_PATIENT_CASE_ID, { type: "message_rejected", actor: "coordinator:m", payload: { draft_id: "x", reason: "tone" } })).rejects.toMatchObject({ status: 403 });
  });

  it("409 when outreach is frozen (consent withdrawn) or the case is closed, and for an appointment confirmation without a date", async () => {
    await api.login("coordinator", "Maryam Al Suwaidi");
    await expect(api.requestDraft(cid(14), request)).rejects.toMatchObject({ status: 409, message: expect.stringContaining("withdrawn consent") });
    await expect(api.requestDraft(cid(11), request)).rejects.toMatchObject({ status: 409, message: expect.stringContaining("closed") });
    await expect(api.requestDraft(cid(9), { ...request, purpose: "appointment_confirmation" })).rejects.toMatchObject({ status: 409, message: expect.stringContaining("appointment") });
  });

  it("a draft pending when consent is withdrawn can no longer be approved (409) but can be rejected", async () => {
    await api.login("owner", "Dr. Layla Haddad", ROLE_PROVIDERS.owner);
    const d = await api.requestDraft(DEMO_OVERDUE_CASE_ID, request);
    await api.postEvent(DEMO_OVERDUE_CASE_ID, { type: "consent_updated", actor: "patient:x", payload: { withdrawn: true } });
    await expect(api.approveDraft(d.draft_id)).rejects.toMatchObject({ status: 409 });
    await expect(api.rejectDraft(d.draft_id, { reason: "not_needed" })).resolves.toMatchObject({ reason: "not_needed" });
  });
});

describe("mock adapter: consent (A6)", () => {
  it("merges partial updates over the all-allowed defaults and keeps the history", async () => {
    await api.postEvent(DEMO_PATIENT_CASE_ID, { type: "consent_updated", actor: "patient:x", payload: { channel_sms: false } });
    const d = await api.postEvent(DEMO_PATIENT_CASE_ID, { type: "consent_updated", actor: "patient:x", payload: { share_with_specialist: false } });
    expect(d.consent).toMatchObject({ channel_sms: false, channel_whatsapp: true, channel_phone: true, channel_letter: true, share_with_specialist: false, withdrawn: false, updated_by: "patient:x" });
    expect(d.consent_history?.map((h) => h.changes)).toEqual([{ channel_sms: false }, { share_with_specialist: false }]);
    expect(d.consent_frozen).toBe(false);
  });
  it("422 for an empty or non-boolean payload", async () => {
    await expect(api.postEvent(DEMO_PATIENT_CASE_ID, { type: "consent_updated", actor: "patient:x", payload: {} })).rejects.toMatchObject({ status: 422 });
    await expect(api.postEvent(DEMO_PATIENT_CASE_ID, { type: "consent_updated", actor: "patient:x", payload: { channel_sms: "no" } })).rejects.toMatchObject({ status: 422 });
    await expect(api.postEvent(DEMO_PATIENT_CASE_ID, { type: "consent_updated", actor: "patient:x", payload: { channel: "sms" } })).rejects.toMatchObject({ status: 422 }); // the old shape
  });
  it("withdrawal freezes outreach (409 on contact, handoff, drafts), adds review_requested, and restoring clears it", async () => {
    const w = await api.postEvent(cid(3), { type: "consent_updated", actor: "patient:x", payload: { withdrawn: true } });
    expect(w).toMatchObject({ consent_frozen: true, review_reason: "consent_withdrawn" });
    expect(w.events.at(-1)).toMatchObject({ type: "review_requested", actor: "system:consent", payload: { reason: "consent_withdrawn" } });
    await expect(api.postEvent(cid(3), { type: "contact_patient", actor: "coordinator:m", payload: {} })).rejects.toMatchObject({ status: 409 });
    await expect(api.postEvent(cid(3), { type: "handoff_requested", actor: "primary_care_owner:Omar Khalid", payload: { to_provider: "Corniche Gastro Unit" } })).rejects.toMatchObject({ status: 409 });
    const r = await api.postEvent(cid(3), { type: "consent_updated", actor: "coordinator:m", payload: { withdrawn: false } });
    expect(r).toMatchObject({ consent_frozen: false, review_reason: null });
  });
  it("review_requested, message_approved and message_sent cannot be posted by anyone", async () => {
    for (const type of ["review_requested", "message_approved", "message_sent"]) {
      await expect(api.postEvent(DEMO_PATIENT_CASE_ID, { type, actor: "coordinator:m", payload: { draft_id: "x" } })).rejects.toMatchObject({ status: 403 });
    }
  });
});

describe("mock adapter: referral handoff (A5) and owner scoping", () => {
  it("shows a pending request and a bounced one on the read model", async () => {
    const pending = await api.getCase(cid(16));
    expect(pending.pending_handoff).toMatchObject({ to_provider: "Corniche Gastro Unit", requested_by: "primary_care_owner:Dr. Layla Haddad", from_owner: "primary_care_owner:Dr. Layla Haddad" });
    expect(pending.owner).toBe("Dr. Layla Haddad");
    const bounced = await api.getCase(cid(15));
    expect(bounced.state).toBe("referral_rejected");
    expect(bounced.pending_handoff).toBeNull();
    expect(bounced.last_handoff).toMatchObject({ outcome: "rejected", reason: "timeout", bounced: true, to_provider: "Marina Digestive Health", by: "system:scheduler" });
    expect(bounced.overdue).toBe(true); // reassign: 2 working days from the bounce
    expect(bounced.due_at).toBeTruthy();
  });
  it("a clinician acts only on cases they own: 403 and nothing written", async () => {
    const before = (await api.getCase(DEMO_OVERDUE_CASE_ID)).events.length;
    await expect(api.postEvent(DEMO_OVERDUE_CASE_ID, { type: "clinician_review", actor: "primary_care_owner:Dr. Huda Rahman", payload: { signed_by: "x" } }))
      .rejects.toMatchObject({ status: 403, message: expect.stringContaining("Dr. Layla Haddad") });
    expect((await api.getCase(DEMO_OVERDUE_CASE_ID)).events).toHaveLength(before);
  });
  it("a non-owner clinician cannot request a handoff, even to their own provider (the real API answers 403)", async () => {
    await api.login("clinician", "Dr. Huda Rahman", "Marina Digestive Health");
    const actor = "primary_care_owner:Dr. Huda Rahman";
    for (const to_provider of ["Corniche Gastro Unit", "Marina Digestive Health"]) {
      await expect(api.postEvent(DEMO_OVERDUE_CASE_ID, { type: "handoff_requested", actor, payload: { to_provider } })).rejects.toMatchObject({ status: 403 });
    }
    expect((await api.getCase(DEMO_OVERDUE_CASE_ID)).pending_handoff ?? null).toBeNull();
  });
  it("the receiving provider's clinician accepts a pending request and becomes the owner", async () => {
    await api.login("specialist", "Dr. Karim Nasser", "Corniche Gastro Unit");
    const d = await api.postEvent(cid(16), { type: "handoff_accepted", actor: "specialist_owner:Dr. Karim Nasser", payload: {} });
    expect(d).toMatchObject({ owner: "Dr. Karim Nasser", provider: "Corniche Gastro Unit", pending_handoff: null, last_handoff: { outcome: "accepted" } });
  });
  it("an answer needs a pending request, the requester cannot accept it, and the reason must be in the taxonomy", async () => {
    await expect(api.postEvent(DEMO_PATIENT_CASE_ID, { type: "handoff_accepted", actor: "coordinator:m", payload: {} })).rejects.toMatchObject({ status: 409 });
    await expect(api.postEvent(cid(16), { type: "handoff_accepted", actor: "primary_care_owner:Dr. Layla Haddad", payload: {} })).rejects.toMatchObject({ status: 409 });
    await api.login("specialist", "Dr. Karim Nasser", "Corniche Gastro Unit");
    const karim = "specialist_owner:Dr. Karim Nasser";
    await expect(api.postEvent(cid(16), { type: "handoff_rejected", actor: karim, payload: { reason: "no idea" } })).rejects.toMatchObject({ status: 422 });
    await expect(api.postEvent(cid(16), { type: "handoff_rejected", actor: karim, payload: { reason: "timeout" } })).rejects.toMatchObject({ status: 422 });
    const declined = await api.postEvent(cid(16), { type: "handoff_rejected", actor: karim, payload: { reason: "wrong_service" } });
    expect(declined).toMatchObject({ owner: "Dr. Layla Haddad", pending_handoff: null, last_handoff: { outcome: "rejected", reason: "wrong_service" } });
  });
  it("a bounced referral is re-routed by a coordinator with reassign, which moves it back to owner_accepted", async () => {
    await expect(api.postEvent(cid(15), { type: "reassign", actor: "primary_care_owner:Dr. Layla Haddad", payload: { owner: "specialist_owner:Dr. Karim Nasser" } })).rejects.toMatchObject({ status: 403 });
    const d = await api.postEvent(cid(15), { type: "reassign", actor: "coordinator:Maryam Al Suwaidi", payload: { owner: "specialist_owner:Dr. Karim Nasser" } });
    expect(d).toMatchObject({ state: "owner_accepted", owner: "Dr. Karim Nasser", owner_actor: "specialist_owner:Dr. Karim Nasser" });
  });
});

describe("mock adapter: worklist filters, alerts, clocks", () => {
  it("flagged=true keeps only cases with an open watchdog flag; rows carry provider and pending_handoff", async () => {
    const flagged = await api.listCases({ flagged: true });
    expect(flagged.map((c) => c.case_id)).toEqual([cid(13)]);
    expect(flagged[0].watchdog_flagged).toMatchObject({ reason: "idle", idle_days: 11 });
    const all = await api.listCases();
    expect(all.find((c) => c.case_id === cid(9))).toMatchObject({ provider: "Corniche Gastro Unit", owner: "Dr. Karim Nasser" });
    expect(all.find((c) => c.case_id === cid(16))?.pending_handoff?.to_provider).toBe("Corniche Gastro Unit");
    expect(all.find((c) => c.case_id === cid(5))).toMatchObject({ provider: null, pending_handoff: null, last_handoff: null });
  });
  it("GET /alerts: none at start; the scheduler-down alert can be raised and cleared", async () => {
    expect(await api.listAlerts()).toEqual([]);
    await api.setSchedulerDown!(true);
    expect(await api.listAlerts()).toEqual([{ kind: "scheduler_down", since: expect.any(String), cleared_at: null }]);
    await api.setSchedulerDown!(false);
    expect((await api.listAlerts())[0].cleared_at).toBe(SIM_NOW);
  });
  it("the colonoscopy milestone starts at handoff_accepted when there was one (A13, A18), state clocks at state entry", async () => {
    const d9 = await api.getCase(cid(9)); // handoff accepted 20 days ago, booked 12 days ago
    const tl = await api.getTimeline(cid(9));
    const started = tl.items.find((i) => i.kind === "milestone_started" && i.milestone === "confirmatory_completed");
    const accepted = d9.events.find((e) => e.type === "handoff_accepted")!;
    expect(started?.at).toBe(accepted.at);
    expect(d9.milestones?.confirmatory_completed).toMatchObject({ started_at: accepted.at, met_at: null, overdue: false, working_days: 31 });
    expect(d9.milestones?.referral_requested).toMatchObject({ overdue: false, working_days: 15 });
    expect(tl.items.find((i) => i.kind === "sla_started")).toBeUndefined(); // confirmatory_booked has no per-state SLA
    const t1 = (await api.getTimeline(DEMO_OVERDUE_CASE_ID)).items.find((i) => i.kind === "sla_started");
    expect(t1).toMatchObject({ sla: "clinician_review" });
  });
  it("closed has no clock; clinician_reviewed has the 3-working-day close SLA; referral_rejected the 2-day reassign SLA", async () => {
    expect((await api.getCase(cid(11))).due_at).toBeNull();
    const reviewed = await api.getCase(cid(10));
    expect(reviewed.state).toBe("clinician_reviewed");
    expect(reviewed.due_at).toBeTruthy();
    expect(reviewed.overdue).toBe(false);
    expect((await api.getTimeline(cid(10))).items.find((i) => i.kind === "sla_started")).toMatchObject({ sla: "close" });
    expect((await api.getTimeline(cid(15))).items.find((i) => i.kind === "sla_started")).toMatchObject({ sla: "reassign" });
  });
});

/** English `result_disclosure` entries of api/app/ai/forbidden_phrases.yaml (E14), compiled as the API does (case-insensitive, whole word). */
function englishResultDisclosurePatterns(): { id: string; re: RegExp }[] {
  const yaml = readFileSync(resolve(process.cwd(), "../api/app/ai/forbidden_phrases.yaml"), "utf-8");
  const en = yaml.slice(yaml.indexOf("\nen:\n"), yaml.indexOf("\nar:\n"));
  const out: { id: string; re: RegExp }[] = [];
  for (const block of en.split(/\n {2}- id: /).slice(1)) {
    if (!/\n {4}category: result_disclosure\b/.test(block)) continue;
    const id = block.split("\n")[0].trim();
    const m = /\n {4}(phrase|regex): ("(?:[^"\\]|\\.)*")/.exec(block);
    if (!m) throw new Error(`cannot read the pattern of ${id}`);
    const value = JSON.parse(m[2]) as string;
    const body = m[1] === "phrase" ? value.trim().split(/\s+/).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s+") : value;
    out.push({ id, re: new RegExp(`\\b(?:${body})\\b`, "i") });
  }
  return out;
}

describe("E14: demo draft texts never state the finding", () => {
  it("reads the English result_disclosure entries (guards the parser above)", () => {
    const ids = englishResultDisclosurePatterns().map((p) => p.id);
    expect(ids).toEqual(expect.arrayContaining(["found_blood", "blood_in_stool", "positive_result", "abnormal_result", "result_shows"]));
    const found = englishResultDisclosurePatterns().find((p) => p.id === "found_blood")!;
    expect(found.re.test("Your screening test found blood.")).toBe(true);
  });

  it("no seeded fixture draft and no mock-generated draft text (any purpose, either language) matches one", async () => {
    const patterns = englishResultDisclosurePatterns();
    const texts: string[] = [];
    for (const d of Object.values(seedDrafts())) texts.push(d.text, d.text_en ?? "", d.text_ar ?? "");
    for (const byPurpose of Object.values(DRAFT_TEXT)) texts.push(...Object.values(byPurpose)); // every purpose, both languages
    await api.login("coordinator", "Maryam Al Suwaidi");
    for (const language of ["en", "ar"] as const) {
      const d = await api.requestDraft(DEMO_PATIENT_CASE_ID, { purpose: "result_notification", channel: "sms", language });
      texts.push(d.text);
    }
    expect(texts.filter((t) => t).length).toBeGreaterThanOrEqual(8);
    for (const text of texts) {
      const hits = patterns.filter((p) => p.re.test(text)).map((p) => p.id);
      expect(hits, text).toEqual([]);
      expect(text).not.toMatch(/diagnos/i);
    }
  });
});
