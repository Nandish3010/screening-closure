import { describe, expect, it } from "vitest";
import type { CaseEvent, DraftRecord } from "../src/api/contract";
import {
  actorName, actorRole, completeDetail, completeSummary, consentFrozen, consentOf, draftViews, escalationLevel, handoffs,
  isAutomatedActor, lostContact, normalizeOwner, overdueDays, overdueMarker, ownerOf, pendingEscalation, replayHandoffs,
  milestonesOf, replayState, slaClock, watchdogFlag,
} from "../src/lib/derive";

let n = 0;
const ev = (type: string, actor: string, payload: Record<string, unknown> = {}, at?: string): CaseEvent => {
  n += 1;
  return { seq: n, type, actor, payload, at: at ?? new Date(Date.UTC(2026, 9, 1, 0, n)).toISOString() };
};

describe("actors", () => {
  it("splits role:name and treats scheduler, watchdog, model and system as automated", () => {
    expect(actorRole("coordinator:amal")).toBe("coordinator");
    expect(actorName("clinician:dr_ali")).toBe("dr_ali");
    expect(actorName("Name:with:colons")).toBe("with:colons");
    expect(actorRole("watchdog")).toBe("watchdog");
    expect(actorName("watchdog")).toBe("");
    for (const a of ["system:scheduler", "watchdog", "model:drafter", "system:intake", "scheduler"]) expect(isAutomatedActor(a)).toBe(true);
    for (const a of ["coordinator:amal", "clinician:dr_ali", "patient:c1", "provider:Al Noor", "systematic:bob"]) expect(isAutomatedActor(a)).toBe(false);
  });
});

describe("overdue", () => {
  it("computes whole days from due_at and the sim clock", () => {
    expect(overdueDays("2026-11-01T09:00:00Z", "2026-11-04T09:00:00Z")).toBe(3);
    expect(overdueDays("2026-11-01T09:00:00Z", "2026-11-01T20:00:00Z")).toBe(0);
    expect(overdueDays("2026-11-05T09:00:00Z", "2026-11-04T09:00:00Z")).toBe(0);
    expect(overdueDays(null, "2026-11-04T09:00:00Z")).toBe(0);
    expect(overdueDays("2026-11-01T09:00:00Z", null)).toBe(0);
  });
  it("derives overdue from the latest mark_overdue unless a state change followed", () => {
    const base = [ev("case_created", "system:intake"), ev("acknowledge", "lab:x")];
    expect(overdueMarker(base)).toBeNull();
    const marked = [...base, ev("mark_overdue", "system:scheduler", { state: "acknowledged", due_at: "2026-10-05T00:00:00Z" })];
    expect(overdueMarker(marked)).toMatchObject({ state: "acknowledged", due_at: "2026-10-05T00:00:00Z" });
    expect(overdueMarker([...marked, ev("escalate", "system:scheduler", { rung: "reminder", target_role: "owner" })])).not.toBeNull();
    expect(overdueMarker([...marked, ev("accept_ownership", "coordinator:amal", { owner: "Amal" })])).toBeNull();
  });
});

describe("watchdog", () => {
  const start = [ev("case_created", "system:intake"), ev("acknowledge", "lab:x")];
  it("is open after watchdog_flagged and carries reason and idle_days", () => {
    expect(watchdogFlag(start)).toBeNull();
    const flagged = [...start, ev("watchdog_flagged", "watchdog", { reason: "idle", idle_days: 9 })];
    expect(watchdogFlag(flagged)).toMatchObject({ reason: "idle", idle_days: 9 });
    const down = [...start, ev("watchdog_flagged", "watchdog", { reason: "scheduler_down" })];
    expect(watchdogFlag(down)).toMatchObject({ reason: "scheduler_down" });
    expect(watchdogFlag(down)?.idle_days).toBeUndefined();
  });
  it("stays open through automated events, clears on a human event or a state change", () => {
    const flagged = [...start, ev("watchdog_flagged", "watchdog", { reason: "idle", idle_days: 9 })];
    expect(watchdogFlag([...flagged, ev("mark_overdue", "system:scheduler", {})])).not.toBeNull();
    expect(watchdogFlag([...flagged, ev("escalate", "coordinator:amal", { rung: "supervisor", target_role: "supervisor" })])).toBeNull();
    expect(watchdogFlag([...flagged, ev("accept_ownership", "coordinator:amal", { owner: "Amal" })])).toBeNull();
  });
});

describe("escalation", () => {
  it("level is the highest rung since the last state change", () => {
    const evs = [ev("case_created", "system:intake"), ev("acknowledge", "lab:x")];
    expect(escalationLevel(evs)).toBe(0);
    const a = [...evs, ev("escalate", "system:scheduler", { rung: "reminder", target_role: "owner" })];
    expect(escalationLevel(a)).toBe(1);
    const b = [...a, ev("escalate", "system:scheduler", { rung: "coordinator", target_role: "coordinator" }), ev("escalate", "x:y", { rung: "supervisor", target_role: "supervisor" })];
    expect(escalationLevel(b)).toBe(3);
    expect(escalationLevel([...b, ev("accept_ownership", "coordinator:amal", { owner: "A" })])).toBe(0);
  });
  it("an escalate stays pending until a human posts escalation_accepted", () => {
    const e = [ev("escalate", "system:scheduler", { rung: "supervisor", target_role: "supervisor" })];
    expect(pendingEscalation(e)).toMatchObject({ rung: "supervisor", target_role: "supervisor" });
    expect(pendingEscalation([...e, ev("escalation_accepted", "coordinator:amal", { rung: "supervisor" })])).toBeNull();
    expect(pendingEscalation([])).toBeNull();
  });
});

describe("hand-offs", () => {
  it("resolves requests by the provider named in the actor, payload or (failing both) the oldest open one", () => {
    const evs = [
      ev("handoff_requested", "coordinator:amal", { to_provider: "A" }),
      ev("handoff_requested", "coordinator:amal", { to_provider: "B" }),
      ev("handoff_rejected", "provider:B", { reason: "wrong_service" }),
      ev("handoff_accepted", "system:router", {}),
    ];
    expect(handoffs(evs)).toMatchObject([
      { provider: "A", status: "accepted" },
      { provider: "B", status: "rejected", reason: "wrong_service" },
    ]);
  });
  it("shows an answer with no matching request rather than dropping it", () => {
    expect(handoffs([ev("handoff_rejected", "provider:Z", { reason: "capacity" })])).toMatchObject([{ provider: "Z", status: "rejected", reason: "capacity" }]);
  });
});

describe("drafts", () => {
  const rec = (id: string, o: Partial<DraftRecord> = {}): DraftRecord => ({
    draft_id: id, case_id: "c", purpose: "result_notification", channel: "sms", language: "en", text: `text ${id}`, text_ar: null, text_en: `text ${id}`,
    provider: "template", fallback_used: false, abstention_reason: null, prompt_hash: "h", created_by: "coordinator:amal", created_at: "2026-10-01T00:00:00Z",
    approved_at: null, approved_by: null, rejected_at: null, rejected_by: null, rejection_reason: null, ...o,
  });
  it("takes the text and decisions from GET /cases/{id}/drafts and the sent state from message_sent", () => {
    const evs = [
      ev("message_drafted", "model:template", { draft_id: "d1", provider: "template", fallback_used: false, prompt_hash: "h", language: "en" }),
      ev("message_approved", "primary_care_owner:amal", { draft_id: "d1" }),
      ev("message_sent", "system:notifier", { draft_id: "d1" }),
      ev("message_sent", "system:notifier", { draft_id: "d2" }), // never approved: must not show as sent
    ];
    const [d1, d2, d3] = draftViews([
      rec("d1", { approved_at: "2026-10-01T01:00:00Z", approved_by: "primary_care_owner:amal" }),
      rec("d2", { created_at: "2026-10-01T02:00:00Z" }),
      rec("d3", { created_at: "2026-10-01T03:00:00Z", rejected_at: "2026-10-01T04:00:00Z", rejected_by: "primary_care_owner:amal", rejection_reason: "tone" }),
    ], evs);
    expect(d1).toMatchObject({ status: "approved", text: "text d1" });
    expect(d1.sent_at).toBeTruthy();
    expect(d2).toMatchObject({ status: "pending" });
    expect(d2.sent_at).toBeUndefined();
    expect(d3).toMatchObject({ status: "rejected", rejection_reason: "tone" });
  });
  it("rebuilds a draft the API did not return from its message_drafted event, which never carries text", () => {
    const evs = [
      // text in an event payload is ignored: the log holds ids and hashes only (A14, ai-boundary.md)
      ev("message_drafted", "model:template", { draft_id: "old", provider: "p", fallback_used: true, language: "ar", text_ar: "عربي" }),
      ev("message_drafted", "model:template", { draft_id: "new", provider: "p", fallback_used: false, prompt_hash: "abc", language: "en" }),
    ];
    const [old, fresh] = draftViews([], evs);
    expect(old).toMatchObject({ draft_id: "old", language: "ar", text: "", text_ar: null, fallback_used: true, status: "pending" });
    expect(fresh).toMatchObject({ draft_id: "new", text: "", prompt_hash: "abc" }); // no text anywhere: the panel says unavailable
  });
});

describe("consent and lost contact", () => {
  it("consent defaults to everything allowed and merges partial boolean updates in order (A6)", () => {
    expect(consentOf([])).toMatchObject({ channel_sms: true, channel_whatsapp: true, channel_phone: true, channel_letter: true, share_with_specialist: true, withdrawn: false, updated_at: null });
    const c = consentOf([ev("consent_updated", "patient:c1", { channel_sms: false }), ev("consent_updated", "patient:c1", { share_with_specialist: false, channel_sms: "yes" })]);
    expect(c).toMatchObject({ channel_sms: false, share_with_specialist: false, channel_whatsapp: true, updated_by: "patient:c1" });
  });
  it("consent is frozen exactly while the latest withdrawn flag is true", () => {
    const w = ev("consent_updated", "patient:c1", { withdrawn: true });
    expect(consentFrozen([w])).toBe(true);
    expect(consentFrozen([w, ev("consent_updated", "patient:c1", { channel_sms: false })])).toBe(true);
    expect(consentFrozen([w, ev("consent_updated", "patient:c1", { withdrawn: false })])).toBe(false);
  });
  it("lost contact is cleared by a new patient contact", () => {
    const l = ev("mark_lost_contact", "coordinator:amal");
    expect(lostContact([l])).toBe(true);
    expect(lostContact([l, ev("contact_patient", "coordinator:amal", { channel: "phone" })])).toBe(false);
  });
});

describe("owner, handoffs and states (A5)", () => {
  const base = [ev("case_created", "system:intake"), ev("acknowledge", "lab:x")];
  it("the owner is the actor of accept_ownership, handoff_accepted and reassign, never of a request", () => {
    const e1 = [...base, ev("accept_ownership", "primary_care_owner:Dr A", { owner_role: "Primary care physician" })];
    expect(ownerOf(e1)).toEqual({ actor: "primary_care_owner:Dr A", name: "Dr A", role: "Primary care physician" });
    const e2 = [...e1, ev("handoff_requested", "primary_care_owner:Dr A", { to_provider: "P" })];
    expect(ownerOf(e2)?.name).toBe("Dr A");
    expect(replayHandoffs(e2).pending).toMatchObject({ to_provider: "P", requested_by: "primary_care_owner:Dr A", from_owner: "primary_care_owner:Dr A" });
    const e3 = [...e2, ev("handoff_accepted", "specialist_owner:Dr S", {})];
    expect(replayHandoffs(e3)).toMatchObject({ owner: { name: "Dr S", role: "Specialist" }, pending: null, provider: "P", last: { outcome: "accepted", to_provider: "P", by: "specialist_owner:Dr S" } });
    const e4 = [...e3, ev("reassign", "coordinator:m", { owner: "primary_care_owner:Dr B" })];
    expect(ownerOf(e4)?.actor).toBe("primary_care_owner:Dr B");
  });
  it("a rejection records its reason; the scheduler's timeout bounce is flagged bounced", () => {
    const evs = [...base, ev("accept_ownership", "primary_care_owner:Dr A"), ev("handoff_requested", "primary_care_owner:Dr A", { to_provider: "P" }),
      ev("handoff_rejected", "system:scheduler", { reason: "timeout", bounced: true })];
    expect(replayHandoffs(evs)).toMatchObject({ pending: null, last: { outcome: "rejected", reason: "timeout", bounced: true, to_provider: "P" } });
    expect(handoffs(evs)).toMatchObject([{ provider: "P", status: "rejected", bounced: true }]);
  });
  it("a rejected handoff moves a case to referral_rejected only from a referral state", () => {
    const upTo = (n: number) => [
      ev("case_created", "system:intake"), ev("acknowledge", "lab:x"), ev("accept_ownership", "primary_care_owner:Dr A"),
      ev("contact_patient", "coordinator:m"), ev("patient_acknowledge", "patient:c"),
    ].slice(0, n);
    const reject = ev("handoff_rejected", "specialist_owner:Dr S", { reason: "capacity" });
    expect(replayState([...upTo(4), reject])).toBe("patient_contacted"); // annotation
    expect(replayState([...upTo(5), reject])).toBe("referral_rejected");
    expect(replayState([...upTo(5), reject, ev("reassign", "coordinator:m", { owner: "x" })])).toBe("owner_accepted");
  });
  it("the colonoscopy milestone starts at the latest handoff_accepted, state entry otherwise; confirmatory_booked has no state SLA (A13, A18)", () => {
    const evs = [
      ev("case_created", "system:intake", {}, "2026-10-01T09:00:00Z"), ev("acknowledge", "lab:x", {}, "2026-10-01T10:00:00Z"),
      ev("accept_ownership", "primary_care_owner:A", {}, "2026-10-01T11:00:00Z"), ev("contact_patient", "coordinator:m", {}, "2026-10-01T12:00:00Z"),
      ev("patient_acknowledge", "patient:c", {}, "2026-10-01T13:00:00Z"), ev("handoff_requested", "primary_care_owner:A", { to_provider: "P" }, "2026-10-02T09:00:00Z"),
      ev("handoff_accepted", "specialist_owner:S", {}, "2026-10-05T09:00:00Z"), ev("book_confirmatory", "specialist_owner:S", {}, "2026-10-12T09:00:00Z"),
    ];
    const now = "2027-01-01T00:00:00Z";
    expect(slaClock(evs, "confirmatory_booked", now).due_at).toBeNull();
    expect(milestonesOf(evs, now).confirmatory_completed).toMatchObject({ started_at: "2026-10-05T09:00:00Z", due_at: "2026-11-17T09:00:00.000Z", overdue: true });
    expect(milestonesOf(evs.slice(0, 5).concat(evs[7]), now).confirmatory_completed.started_at).toBe("2026-10-12T09:00:00Z");
    // the referral milestone runs from the result and is met by the request, in time
    expect(milestonesOf(evs, now).referral_requested).toMatchObject({ started_at: "2026-10-01T09:00:00Z", met_at: "2026-10-02T09:00:00Z", met_late: false, overdue: false });
    expect(slaClock(evs, "clinician_reviewed", now)).toMatchObject({ sla_event: "close" });
    expect(slaClock(evs, "referral_rejected", now)).toMatchObject({ sla_event: "reassign" });
    expect(slaClock(evs, "closed", now).due_at).toBeNull();
  });
});

describe("the wire shapes the adapters accept", () => {
  it("normalises the owner: object {actor, display_name, role} (A14), actor string, or bare name", () => {
    expect(normalizeOwner({ actor: "primary_care_owner:dr_k", display_name: "Dr K", role: "Primary care physician" })).toEqual({ owner: "Dr K", owner_actor: "primary_care_owner:dr_k", owner_role: "Primary care physician" });
    expect(normalizeOwner("primary_care_owner:dr_k")).toEqual({ owner: "dr_k", owner_actor: "primary_care_owner:dr_k", owner_role: null });
    expect(normalizeOwner("Sara")).toEqual({ owner: "Sara", owner_actor: null, owner_role: null });
    expect(normalizeOwner({ actor: "specialist_owner:s" })).toMatchObject({ owner: "s" });
    expect(normalizeOwner(null)).toEqual({ owner: null, owner_actor: null, owner_role: null });
    expect(completeSummary({ case_id: "c", owner: { actor: "a:b", display_name: "B", role: "r" } } as never)).toMatchObject({ owner: "B", owner_actor: "a:b", watchdog_flagged: null, provider: null, pending_handoff: null });
  });
  it("lifts the nested read_model of the real GET /cases/{id}", () => {
    const d = completeDetail({
      case_id: "c", state: "owner_accepted", events: [],
      read_model: {
        owner: "primary_care_owner:dr_k", overdue: true, breach_due_at: "2026-10-03T00:00:00Z", escalation_level: 1, consent_frozen: true, last_event_at: "t",
        consent: { channel_sms: false, withdrawn: true }, review_reason: "consent_withdrawn", provider: "P",
        pending_handoff: { to_provider: "Q", requested_by: "a:b", from_owner: "a:c", requested_at: "t" },
        last_handoff: { outcome: "rejected", to_provider: "Q", by: "system:scheduler", at: "t", reason: "timeout", bounced: true },
      },
    } as never);
    expect(d).toMatchObject({
      owner: "dr_k", owner_actor: "primary_care_owner:dr_k", overdue: true, due_at: "2026-10-03T00:00:00Z", escalation_level: 1, consent_frozen: true,
      review_reason: "consent_withdrawn", provider: "P", pending_handoff: { to_provider: "Q" }, last_handoff: { bounced: true },
      consent: { channel_sms: false, channel_whatsapp: true, withdrawn: true },
    });
  });
});
