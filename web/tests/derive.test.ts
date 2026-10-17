import { describe, expect, it } from "vitest";
import type { CaseEvent } from "../src/api/contract";
import {
  actorName, actorRole, consentFrozen, drafts, escalationLevel, handoffs, isAutomatedActor, lostContact, overdueDays,
  overdueMarker, pendingEscalation, watchdogFlag,
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
  it("tracks draft -> approved (approver is the actor) -> sent", () => {
    const evs = [
      ev("message_drafted", "model:drafter", { draft_id: "d1", provider: "p", fallback_used: false, text_en: "e", text_ar: "a" }),
      ev("message_drafted", "model:drafter", { draft_id: "d2", provider: "p", fallback_used: true }),
      ev("message_approved", "coordinator:amal", { draft_id: "d1" }),
      ev("message_sent", "system:sender", { draft_id: "d1" }),
      ev("message_sent", "system:sender", { draft_id: "d2" }), // never approved: must not show as sent
    ];
    const [d1, d2] = drafts(evs);
    expect(d1).toMatchObject({ approved_by: "amal", text_en: "e" });
    expect(d1.sent_at).toBeTruthy();
    expect(d2.approved_by).toBeUndefined();
    expect(d2.sent_at).toBeUndefined();
  });
});

describe("consent and lost contact", () => {
  it("consent is frozen by the latest consent_updated that says withdrawn", () => {
    const w = ev("consent_updated", "patient:c1", { channel: "sms", share_with_specialist: false, withdrawn: true });
    expect(consentFrozen([w])).toBe(true);
    expect(consentFrozen([w, ev("consent_updated", "patient:c1", { channel: "sms", share_with_specialist: true })])).toBe(false);
  });
  it("lost contact is cleared by a new patient contact", () => {
    const l = ev("mark_lost_contact", "coordinator:amal");
    expect(lostContact([l])).toBe(true);
    expect(lostContact([l, ev("contact_patient", "coordinator:amal", { channel: "phone" })])).toBe(false);
  });
});
