/**
 * The case timeline reads in plain words by default: a sentence per event and who did it by name. The raw fields (event number,
 * `role:id`, ISO times, template and notification ids) sit behind a collapsed "Details" toggle.
 */
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { TimelineItem } from "../src/api/contract";
import { handoffReasonLabel } from "../src/lib/pathway";
import { Timeline, plainSummary, plainWho } from "../src/components/Timeline";

const ev = (seq: number, type: string, actor: string, actor_kind: "human" | "automation" | "model" | "service", payload: Record<string, unknown> = {}): TimelineItem =>
  ({ kind: "event", seq, event_id: `e${seq}`, type, at: `2026-01-0${seq}T08:00:00Z`, actor, actor_kind, payload });

describe("Timeline in plain language", () => {
  it("shows a sentence and a name, and keeps the technical fields behind a closed Details toggle", () => {
    render(<Timeline items={[
      ev(9, "escalate", "system:scheduler", "service", { rung: "reminder", level: 1, target: "owner", due_at: "2026-01-12T08:00:00+00:00", notification_id: "c3c4ade9-c805" }),
    ]} />);
    const li = screen.getByTestId("timeline").querySelector("li")!;
    expect(within(li).getByTestId("tl-plain")).toHaveTextContent("Passed up to the owner (level 1).");
    expect(within(li).getByTestId("tl-who")).toHaveTextContent("Event #9 · By Scheduler");
    const details = within(li).getByTestId("tl-details");
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByTestId("tl-raw")).toHaveTextContent(/#9 · system:scheduler · .*due at: 2026-01-12T08:00:00\+00:00/);
    // nothing outside the Details toggle looks like a log line
    const visible = li.cloneNode(true) as HTMLElement;
    visible.querySelector("[data-testid=tl-details]")!.remove();
    expect(visible.textContent).not.toMatch(/system:scheduler|\+00:00|notification/);
  });

  it("names people and machinery in plain words", () => {
    expect(plainWho("system:automation", "automation")).toBe("Automation");
    expect(plainWho("system:triage", "service")).toBe("Triage");
    expect(plainWho("model:template", "model")).toBe("AI model");
    expect(plainWho("watchdog", "service")).toBe("Watchdog");
  });

  it("names every disclosure, so a screen reader can tell 20 of them apart", () => {
    render(<Timeline items={[ev(3, "escalate", "system:scheduler", "service", { target: "owner", level: 1 }), ev(4, "close", "specialist_owner:u1", "human", { evidence_ref: "Procedure/colonoscopy-0017" })]} />);
    const summaries = screen.getAllByText(/^Event details/);
    expect(summaries.map((s) => s.textContent)).toEqual(["Event details for event 3, Escalated", "Event details for event 4, Case closed (signed)"]);
  });

  it.each([
    ["complete_confirmatory", { result_ref: "Procedure/colonoscopy-0017" }, "Result recorded: the colonoscopy record (\u20260017)."],
    ["closure_proposed", { evidence_ref: "Procedure/colonoscopy-0017" }, "The system proposed closing on the colonoscopy record (\u20260017). A clinician must sign."],
    ["clinician_review", { evidence_ref: "Procedure/colonoscopy-0017" }, "Evidence reviewed: the colonoscopy record (\u20260017)."],
    ["close", { evidence_ref: "Procedure/colonoscopy-0017", signed_by: "Practitioner/771a63a98e3d" }, "Closed on the colonoscopy record (\u20260017), signed by the clinician (\u2026a98e3d)."],
    ["reopen", { reason: "new_finding" }, "Reopened: new finding."],
    ["handoff_requested", { to_provider: "Al Noor Endoscopy Centre" }, "Hand-off requested to Al Noor Endoscopy Centre."],
    ["handoff_accepted", { provider: "Al Noor Endoscopy Centre" }, "Al Noor Endoscopy Centre accepted the referral."],
    ["handoff_rejected", { reason: "capacity" }, "Rejected: " + handoffReasonLabel("capacity") + "."],
    ["escalation_accepted", { rung: "supervisor" }, "Accepted by a person at the supervisor level."],
    ["assign_ownership", { owner: "primary_care_owner:u1", provider: "Marina Digestive Health" }, "Owner: u1 at Marina Digestive Health."],
    ["mark_lost_contact", { attempts: 3 }, "After 3 failed contact attempts."],
    ["message_rejected", { reason: "wrong tone" }, "Rejected: wrong tone."],
  ])("%s: says the decision-relevant fact in plain words", (type, payload, expected) => {
    expect(plainSummary(type, payload as Record<string, unknown>)).toBe(expected);
  });

  it.each([
    ["contact_patient", { channel: "whatsapp" }, "By whatsapp."],
    ["reply_received", { channel: "sms" }, "Arrived by sms. The reply text is kept outside the audit trail."],
    ["escalate", { target: "supervisor", level: 2 }, "Passed up to the supervisor (level 2)."],
    ["message_sent", { channel: "sms" }, "Sent by sms."],
    ["consent_updated", { channel_sms: false, share_with_specialist: true }, "text messages: not allowed · sharing with the specialist: allowed"],
    ["consent_updated", { withdrawn: true }, "consent withdrawn"],
    ["book_confirmatory", { appointment_ref: "Appointment/appt-00a1b2" }, "Booked: the appointment (\u202600a1b2)."],
    ["reassign", { owner: "specialist_owner:u2", to_provider: "Marina Digestive Health" }, "Re-routed to u2 at Marina Digestive Health."],
    ["mark_overdue", { due_at: "2026-01-12T08:00:00Z" }, "The step was due 2026-01-12 08:00 UTC."],
    ["review_requested", { reason: "consent_withdrawn" }, "Reason: consent withdrawn."],
    ["complete_confirmatory", { result_ref: "Procedure/colposcopy-7f3a9c10-0017" }, "Result recorded: the colposcopy record (\u20260017)."],
    ["close", { evidence_ref: "DiagnosticReport/colposcopy-report-7f3a9c10-00ab12" }, "Closed on the colposcopy report (\u202600ab12)."],
  ])("%s: more events say their decision-relevant fact", (type, payload, expected) => {
    expect(plainSummary(type, payload as Record<string, unknown>)).toBe(expected);
  });

  it("a refused hand-off and an accepted escalation carry their event number and sentence in the row", () => {
    render(<Timeline items={[
      ev(5, "handoff_rejected", "specialist_owner:u1", "human", { reason: "capacity" }),
      ev(6, "escalation_accepted", "supervisor:u3", "human", { rung: "supervisor" }),
      ev(7, "reply_classified", "model:triage", "model", { intent: "other", confidence: 0.4, needs_human: true, model_id: "qwen2.5-1.5b-4bit+lora-8b5a49fc" }),
    ]} />);
    const rows = screen.getByTestId("timeline").querySelectorAll("li");
    expect(within(rows[0] as HTMLElement).getByTestId("tl-plain")).toHaveTextContent("Rejected: " + handoffReasonLabel("capacity"));
    expect(within(rows[0] as HTMLElement).getByTestId("tl-who")).toHaveTextContent("Event #5");
    expect(within(rows[1] as HTMLElement).getByTestId("tl-who")).toHaveTextContent("Event #6");
    expect(within(rows[2] as HTMLElement).getByTestId("tl-plain")).toHaveTextContent("A person must look at it.");
    expect(within(rows[2] as HTMLElement).getByTestId("tl-plain")).not.toHaveTextContent("qwen");
  });

  it("an event type with nothing to say returns nothing, and a refused hand-off keeps its reason", () => {
    expect(plainSummary("accept_ownership", {})).toBe("");
    expect(plainSummary("handoff_rejected", {})).toMatch(/^Rejected: /);
  });

  it("fails closed: only an explicit needs_human false says no person is needed", () => {
    expect(plainSummary("reply_classified", { intent: "confirm", confidence: 0.9, needs_human: false })).toMatch(/No person needed/);
    expect(plainSummary("reply_classified", { intent: "confirm", confidence: 0.9 })).toMatch(/A person must look at it/);
    expect(plainSummary("reply_classified", { intent: "confirm", confidence: 0.9, needs_human: "no" })).toMatch(/A person must look at it/);
  });

  it("reads a classified reply as a sentence with no model id", () => {
    const s = plainSummary("reply_classified", { intent: "confirm", confidence: 0.98, model_id: "qwen2.5-1.5b-4bit+lora-8b5a49fc", needs_human: true });
    expect(s).toBe("Read as confirm (98% sure). A person must look at it.");
  });
});
