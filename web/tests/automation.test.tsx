/**
 * A21: automate every step that is not a human decision. The "Automated" chip, the closure-proposed banner with a prefilled
 * sign-off, the per-case counter, the dashboard's human-effort card, the mock's mirror of the automation actor, and the
 * http adapter's mapping of GET /metrics/effort. Humans still decide: the owner accepts, a clinician signs off.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ApiClient, EffortMetrics, Role, TimelineItem } from "../src/api/contract";
import { createHttpAdapter } from "../src/api/httpAdapter";
import { DEMO_ASSIGNED_CASE_ID, DEMO_CLOSURE_PROPOSED_CASE_ID, DEMO_REROUTE_EXHAUSTED_CASE_ID, SIM_NOW } from "../src/api/fixtures";
import { ROLE_PROVIDERS } from "../src/api/identities";
import { REROUTE_LIST, createMockAdapter } from "../src/api/mockAdapter";
import { mockAutomationFromEnv } from "../src/api";
import { EffortCard } from "../src/components/EffortCard";
import { Timeline } from "../src/components/Timeline";
import { actorKindOf, closureProposalOf } from "../src/lib/derive";
import { effortOf } from "../src/lib/effort";
import { CaseDetailPage } from "../src/pages/CaseDetailPage";
import { CoordinatorPage } from "../src/pages/CoordinatorPage";
import { DashboardPage } from "../src/pages/DashboardPage";
import { renderWithApp } from "./helpers";

const cid = (n: number) => `c${String(n).padStart(7, "0")}-0000-4000-8000-${String(n).padStart(12, "0")}`;
const LAYLA = "primary_care_owner:Dr. Layla Haddad";
const HUDA = "primary_care_owner:Dr. Huda Rahman";
const OMAR = "primary_care_owner:Omar Khalid";
const COORD = "coordinator:Maryam Al Suwaidi";
const auto = () => createMockAdapter(null, { automation: true });
const types = (evs: { type: string }[]) => evs.map((e) => e.type);

const openCase = (api: ApiClient, id: string, role: Role) =>
  renderWithApp(<CaseDetailPage />, { api, role, route: `/coordinator/${id}`, path: "/coordinator/:caseId" });

describe("actor kinds", () => {
  it("splits the actor string the way the API does", () => {
    expect(actorKindOf("system:automation")).toBe("automation");
    expect(actorKindOf("model:template")).toBe("model");
    for (const a of ["system:scheduler", "system:watchdog", "system:notifier", "system:consent", "system:intake", "scheduler", "watchdog"]) {
      expect(actorKindOf(a)).toBe("service");
    }
    for (const a of ["lab:Central Lab", "primary_care_owner:u1", "specialist_owner:u2", "coordinator:m", "supervisor:s", "patient:c1"]) {
      expect(actorKindOf(a)).toBe("human");
    }
  });
});

describe("Timeline: the Automated chip", () => {
  const ev = (seq: number, type: string, actor: string, actor_kind?: "human" | "automation" | "model" | "service", payload: Record<string, unknown> = {}): TimelineItem =>
    ({ kind: "event", seq, event_id: `e${seq}`, type, at: `2026-10-0${seq}T08:00:00Z`, actor, ...(actor_kind ? { actor_kind } : {}), payload });

  it("marks every event of actor_kind automation, with an accessible label, and nothing else", () => {
    render(<Timeline items={[
      ev(1, "acknowledge", "system:automation", "automation", { auto: true }),
      ev(2, "accept_ownership", "primary_care_owner:u1", "human"),
      ev(3, "message_drafted", "model:template", "model", { draft_id: "d1" }),
      ev(4, "mark_overdue", "system:scheduler", "service", { state: "acknowledged", due_at: "2026-10-04T00:00:00Z" }),
    ]} />);
    const row = (t: string) => screen.getByTestId("timeline").querySelector(`li[data-event-type="${t}"]`) as HTMLElement;
    const chip = within(row("acknowledge")).getByTestId("automation-chip");
    expect(chip).toHaveTextContent("Automated");
    expect(chip).toHaveAccessibleName(/done by the system, not by a person/i);
    expect(within(row("acknowledge")).queryByTestId("automated-chip")).toBeNull();
    expect(within(row("accept_ownership")).queryByTestId("automation-chip")).toBeNull();
    expect(within(row("accept_ownership")).queryByTestId("automated-chip")).toBeNull();
    // The model and service chips are apart from the automation chip.
    expect(within(row("message_drafted")).queryByTestId("automation-chip")).toBeNull();
    expect(within(row("message_drafted")).getByTestId("automated-chip")).toHaveTextContent("AI model");
    expect(within(row("mark_overdue")).queryByTestId("automation-chip")).toBeNull();
    expect(screen.getAllByTestId("automation-chip")).toHaveLength(1);
  });

  it("falls back to the actor string when the wire has no actor_kind", () => {
    render(<Timeline items={[ev(1, "acknowledge", "system:automation"), ev(2, "acknowledge", "lab:Central Lab")]} />);
    expect(screen.getAllByTestId("automation-chip")).toHaveLength(1);
  });

  it("labels the two review reasons and the template message", () => {
    render(<Timeline items={[
      ev(1, "review_requested", "system:consent", "service", { reason: "consent_withdrawn", source_event_id: "x" }),
      ev(2, "review_requested", "system:automation", "automation", { reason: "reroute_exhausted", source_event_id: "y", auto: true }),
      ev(3, "message_sent", "system:automation", "automation", { auto: true, template_id: "result_notification.sms", template_version: 1, channel: "sms" }),
    ]} />);
    const tl = screen.getByTestId("timeline");
    expect(tl).toHaveTextContent("Flagged for human review (consent withdrawn)");
    expect(tl).toHaveTextContent("Reroute list exhausted: a coordinator must reassign");
    expect(tl).toHaveTextContent("Template message sent to the patient");
  });
});

describe("mock automation: the sequence", () => {
  it("a new case is acknowledged and assigned by the automation actor, and the owner still has to accept", async () => {
    const api = auto();
    await api.login("coordinator", "Maryam Al Suwaidi");
    const id = await api.createCase!();
    const d = await api.getCase(id);
    expect(types(d.events)).toEqual(["case_created", "acknowledge", "assign_ownership"]);
    expect(d.events.map((e) => e.actor)).toEqual(["system:intake", "system:automation", "system:automation"]);
    expect(d.events.map((e) => e.actor_kind)).toEqual(["service", "automation", "automation"]);
    expect(d.events[1].payload).toEqual({ auto: true });
    expect(d.events[2].payload).toEqual({ owner: LAYLA, provider: "Riverside Primary Care", auto: true });
    expect(d.state).toBe("acknowledged");
    expect(d.owner).toBe("Dr. Layla Haddad");
    expect(d.effort).toMatchObject({ human_actions: 0, automated_actions: 2 });

    // Another clinician cannot take it; the assigned owner accepts, and only then does the template go out.
    await api.login("clinician", "Dr. Huda Rahman", ROLE_PROVIDERS.clinician);
    await expect(api.postEvent(id, { type: "accept_ownership", actor: HUDA, payload: {} })).rejects.toMatchObject({ status: 403 });
    await api.login("owner", "Dr. Layla Haddad", ROLE_PROVIDERS.owner);
    const accepted = await api.postEvent(id, { type: "accept_ownership", actor: LAYLA, payload: {} });
    expect(accepted.state).toBe("owner_accepted");
    expect(types(accepted.events).slice(-2)).toEqual(["accept_ownership", "message_sent"]);
    const sent = accepted.events.at(-1)!;
    expect(sent).toMatchObject({
      actor: "system:automation", actor_kind: "automation",
      payload: { channel: "sms", language: "en", purpose: "result_notification", auto: true, template_id: "result_notification.sms", template_version: 1 },
    });
    expect(sent.payload.notification_id).toEqual(expect.any(String));
    expect(sent.payload).not.toHaveProperty("draft_id");
  });

  it("without automation nothing is automatic: the new case waits for a person to acknowledge it", async () => {
    const api = createMockAdapter(null);
    expect(api.automation).toBe(false);
    await api.login("coordinator", "Maryam Al Suwaidi");
    const id = await api.createCase!();
    const d = await api.getCase(id);
    expect(types(d.events)).toEqual(["case_created"]);
    expect(d.state).toBe("result_received");
    expect(d.effort).toMatchObject({ human_actions: 0, automated_actions: 0, automation_share: null });
  });

  it("the template message respects consent: no SMS consent, no message", async () => {
    const api = auto();
    await api.login("coordinator", "Maryam Al Suwaidi");
    const id = await api.createCase!();
    await api.postEvent(id, { type: "consent_updated", actor: "patient:x", payload: { channel_sms: false } });
    await api.login("owner", "Dr. Layla Haddad", ROLE_PROVIDERS.owner);
    const accepted = await api.postEvent(id, { type: "accept_ownership", actor: LAYLA, payload: {} });
    expect(types(accepted.events)).not.toContain("message_sent");
  });

  it("a declined referral is re-routed down the list, skipping providers already asked", async () => {
    const api = auto();
    // Case 8: patient acknowledged, owned by Omar Khalid. Al Noor declines (capacity); the next on the list is asked automatically.
    const d = await api.postEvent(cid(8), { type: "handoff_requested", actor: OMAR, payload: { to_provider: "Al Noor Endoscopy Centre" } });
    expect(types(d.events).slice(-4)).toEqual(["handoff_requested", "handoff_rejected", "handoff_requested", "handoff_accepted"]);
    const reroute = d.events.at(-2)!;
    expect(reroute).toMatchObject({ actor: "system:automation", actor_kind: "automation", payload: { to_provider: REROUTE_LIST[0], auto: true } });
    expect(d.state).toBe("owner_accepted");
    expect(d.owner).toBe("Dr. Karim Nasser");
    expect(d.review_reason).toBeNull();
  });

  it("without automation a declined referral stays rejected for a human to re-route", async () => {
    const api = createMockAdapter(null);
    const d = await api.postEvent(cid(8), { type: "handoff_requested", actor: OMAR, payload: { to_provider: "Al Noor Endoscopy Centre" } });
    expect(d.state).toBe("referral_rejected");
    expect(types(d.events).at(-1)).toBe("handoff_rejected");
  });

  it("when the reroute list is exhausted the automation actor asks for a human, and a reassign clears it", async () => {
    const api = createMockAdapter(null, { automation: true, decliningProviders: REROUTE_LIST });
    const d = await api.postEvent(cid(8), { type: "handoff_requested", actor: OMAR, payload: { to_provider: "Marina Digestive Health" } });
    const asked = d.events.filter((e) => e.type === "handoff_requested").map((e) => e.payload.to_provider);
    expect(asked).toEqual(["Marina Digestive Health", "Corniche Gastro Unit", "Al Noor Endoscopy Centre"]); // nobody asked twice
    expect(d.events.at(-1)).toMatchObject({
      type: "review_requested", actor: "system:automation", actor_kind: "automation",
      payload: { reason: "reroute_exhausted", auto: true, source_event_id: expect.any(String) },
    });
    expect(d.state).toBe("referral_rejected");
    expect(d.review_reason).toBe("reroute_exhausted");
    await api.login("coordinator", "Maryam Al Suwaidi");
    const owner = await api.actorFor("specialist", "Dr. Karim Nasser", ROLE_PROVIDERS.specialist);
    const re = await api.postEvent(cid(8), { type: "reassign", actor: COORD, payload: { owner } });
    expect(re.state).toBe("owner_accepted");
    expect(re.review_reason).toBeNull();
  });

  it("evidence found: the colonoscopy is recorded and closure is proposed, but only a human closes", async () => {
    const api = auto();
    await api.login("specialist", "Dr. Karim Nasser", ROLE_PROVIDERS.specialist);
    const karim = "specialist_owner:Dr. Karim Nasser";
    expect((await api.getCase(cid(9))).state).toBe("confirmatory_booked"); // owned by Dr. Karim after the accepted hand-off
    const r = await api.recordConfirmatoryResult(cid(9));
    const d = await api.getCase(cid(9));
    expect(types(d.events).slice(-2)).toEqual(["complete_confirmatory", "closure_proposed"]);
    expect(d.events.slice(-2).map((e) => e.actor_kind)).toEqual(["automation", "automation"]);
    expect(d.events.at(-2)!.payload).toEqual({ result_ref: r.evidence_ref, auto: true });
    expect(d.events.at(-1)!.payload).toEqual({ evidence_ref: r.evidence_ref, auto: true });
    expect(d.state).toBe("confirmatory_completed"); // not closed
    expect(d.closure_proposal).toMatchObject({ evidence_ref: r.evidence_ref, proposed_at: expect.any(String), event_id: expect.any(String) });
    expect(closureProposalOf(d.events)?.evidence_ref).toBe(r.evidence_ref);

    // The automation actor cannot close, and nothing else closes on its own.
    await expect(api.postEvent(cid(9), { type: "close", actor: "system:automation", payload: { evidence_ref: r.evidence_ref, signed_by: "system:automation" } })).rejects.toMatchObject({ status: 409 });
    await expect(api.postEvent(cid(9), { type: "closure_proposed", actor: karim, payload: { evidence_ref: "x" } })).rejects.toMatchObject({ status: 403 });
    expect((await api.getCase(cid(9))).state).toBe("confirmatory_completed");

    // A clinician reviews and signs off; the proposal is cleared.
    await api.postEvent(cid(9), { type: "clinician_review", actor: karim, payload: { signed_by: "Dr. Karim Nasser" } });
    const closed = await api.postEvent(cid(9), { type: "close", actor: karim, payload: { evidence_ref: r.evidence_ref, signed_by: "Dr. Karim Nasser" } });
    expect(closed.state).toBe("closed");
    expect(closed.closure_proposal).toBeNull();
    expect(closed.events.at(-1)).toMatchObject({ type: "close", actor: karim, actor_kind: "human" });
  });

  it("without automation recording the colonoscopy writes no event", async () => {
    const api = createMockAdapter(null);
    await api.login("specialist", "Dr. Karim Nasser", ROLE_PROVIDERS.specialist);
    const before = (await api.getCase(cid(9))).events.length;
    await api.recordConfirmatoryResult(cid(9));
    const d = await api.getCase(cid(9));
    expect(d.events).toHaveLength(before);
    expect(d.closure_proposal).toBeNull();
  });

  it("every staff event carries actor_kind, in the case and in the timeline; a patient's view carries none", async () => {
    const api = auto();
    const d = await api.getCase(DEMO_CLOSURE_PROPOSED_CASE_ID);
    expect(d.events.every((e) => e.actor_kind)).toBe(true);
    expect(d.events.find((e) => e.type === "case_created")!.actor_kind).toBe("service");
    expect(d.events.find((e) => e.type === "accept_ownership")!.actor_kind).toBe("human");
    const tl = await api.getTimeline(DEMO_CLOSURE_PROPOSED_CASE_ID);
    expect(tl.items.filter((i) => i.kind === "event").every((i) => "actor_kind" in i && i.actor_kind)).toBe(true);
    await api.login("patient", "Patient");
    const p = await api.getCase(DEMO_CLOSURE_PROPOSED_CASE_ID);
    expect(p.events.every((e) => e.actor_kind === undefined)).toBe(true);
    expect(types(p.events)).not.toContain("assign_ownership");
    expect(p.effort).toBeUndefined();
  });

  it("the automation demo seed has the three cases the demo shows, the plain seed does not", async () => {
    expect((await auto().getCase(DEMO_CLOSURE_PROPOSED_CASE_ID)).closure_proposal).toMatchObject({ evidence_ref: "Procedure/colonoscopy-0017" });
    expect((await auto().getCase(DEMO_REROUTE_EXHAUSTED_CASE_ID)).review_reason).toBe("reroute_exhausted");
    expect((await auto().getCase(DEMO_ASSIGNED_CASE_ID)).state).toBe("acknowledged");
    await expect(createMockAdapter(null).getCase(DEMO_CLOSURE_PROPOSED_CASE_ID)).rejects.toMatchObject({ status: 404 });
  });

  it("the build switch: ?automation=off beats the default, and is remembered for the tab", () => {
    sessionStorage.clear();
    expect(mockAutomationFromEnv("")).toBe(true);
    expect(mockAutomationFromEnv("?automation=off")).toBe(false);
    expect(mockAutomationFromEnv("")).toBe(false); // remembered
    expect(mockAutomationFromEnv("?automation=on")).toBe(true);
    sessionStorage.clear();
  });
});

describe("effort", () => {
  it("counts staff human events and automation events per case, with assumed minutes", async () => {
    const d = await auto().getCase(DEMO_CLOSURE_PROPOSED_CASE_ID);
    // human: accept_ownership 1, phone contact 6, hand-off request 8, hand-off accepted 3, booking 5 = 23 min.
    // automation: acknowledge 2, assignment 2, template message 3, evidence 5, closure preparation 2 = 14 min replaced.
    expect(d.effort).toEqual({
      human_actions: 5, automated_actions: 5, automation_share: 0.5, human_minutes: 23, baseline_minutes: 37, minutes_saved: 14, reduction_pct: 37.8,
    });
    expect(effortOf(d.events)).toEqual(d.effort);
  });

  it("does not count the patient's own events or the services", () => {
    const e = effortOf([
      { seq: 1, type: "case_created", at: SIM_NOW, actor: "system:intake", payload: {} },
      { seq: 2, type: "patient_acknowledge", at: SIM_NOW, actor: "patient:c1", payload: {} },
      { seq: 3, type: "mark_overdue", at: SIM_NOW, actor: "system:scheduler", payload: {} },
      { seq: 4, type: "message_drafted", at: SIM_NOW, actor: "model:template", payload: {} },
    ]);
    expect(e).toMatchObject({ human_actions: 0, automated_actions: 0, automation_share: null, reduction_pct: null });
  });

  it("GET /metrics/effort: totals add up per case, the target is 40 and the minutes are flagged as assumed", async () => {
    const api = auto();
    await api.login("coordinator", "Maryam Al Suwaidi");
    const m = (await api.getEffortMetrics())!;
    const all = await api.listCases();
    expect(m.cases).toBe(all.length);
    expect(m.per_case).toHaveLength(all.length);
    const sum = (k: "human_actions" | "automated_actions" | "human_minutes" | "baseline_minutes") => m.per_case.reduce((a, c) => a + c[k], 0);
    expect(m.totals).toMatchObject({
      human_actions: sum("human_actions"), automated_actions: sum("automated_actions"),
      human_minutes: sum("human_minutes"), baseline_minutes: sum("baseline_minutes"), target_pct: 40,
    });
    expect(m.totals.minutes_saved).toBe(m.totals.baseline_minutes - m.totals.human_minutes);
    expect(m.totals.meets_target).toBe((m.totals.reduction_pct ?? 0) >= 40);
    expect(m.assumptions.status).toBe("assumed");
    expect(m.assumptions.minutes).toMatchObject({ acknowledge: 2, phone_call: 6, referral_handling: 8, sign_off: 4 });
    expect(m.per_case.find((c) => c.case_id === DEMO_CLOSURE_PROPOSED_CASE_ID)).toMatchObject({ human_actions: 5, automated_actions: 5 });
    await api.login("supervisor", "Dr. Noura Al Hammadi");
    expect(await api.getEffortMetrics()).not.toBeNull();
    await api.login("owner", "Dr. Layla Haddad", ROLE_PROVIDERS.owner);
    expect(await api.getEffortMetrics()).toBeNull(); // 403 on the real API
  });

  it("every person-led action the demo makes lowers nothing: automation grows the share", async () => {
    const api = auto();
    await api.login("coordinator", "Maryam Al Suwaidi");
    const before = (await api.getEffortMetrics())!.totals;
    await api.createCase!();
    const after = (await api.getEffortMetrics())!.totals;
    expect(after.automated_actions).toBe(before.automated_actions + 2);
    expect(after.human_actions).toBe(before.human_actions);
    expect(after.reduction_pct!).toBeGreaterThan(before.reduction_pct!);
  });
});

describe("Case page: closure proposed banner and prefilled sign-off", () => {
  it("a clinician who owns the case sees the evidence, one click reaches the sign-off panel with the reference filled in, and the sign-off is still a human click", async () => {
    const user = userEvent.setup();
    const api = auto();
    openCase(api, DEMO_CLOSURE_PROPOSED_CASE_ID, "clinician");
    const banner = await screen.findByTestId("closure-proposal-banner");
    expect(banner).toHaveTextContent(/found the colonoscopy evidence/i);
    expect(within(banner).getByTestId("closure-proposal-evidence")).toHaveTextContent("Procedure/colonoscopy-0017");
    expect(banner).toHaveTextContent(/Nothing has been closed/i);
    // The sign-off panel already holds the proposed reference.
    expect(screen.getByLabelText("Evidence reference")).toHaveValue("Procedure/colonoscopy-0017");
    await user.click(within(banner).getByTestId("closure-proposal-review"));
    expect(screen.getByTestId("signoff-panel")).toHaveFocus();
    // Nothing closed yet: the signer is still to be named, and the button says so.
    expect((await api.getCase(DEMO_CLOSURE_PROPOSED_CASE_ID)).state).toBe("confirmatory_completed");
    expect(screen.getByTestId("signoff")).toBeDisabled();
    await user.type(screen.getByLabelText("Signed by"), "Dr. Huda Rahman");
    await user.click(screen.getByTestId("signoff"));
    await waitFor(() => expect(screen.getByTestId("flash")).toHaveTextContent(/closed/i));
    const d = await api.getCase(DEMO_CLOSURE_PROPOSED_CASE_ID);
    expect(d.state).toBe("closed");
    expect(d.events.slice(-2).map((e) => [e.type, e.actor, e.actor_kind])).toEqual([
      ["clinician_review", HUDA, "human"], ["close", HUDA, "human"],
    ]);
    expect(d.events.at(-1)!.payload).toMatchObject({ evidence_ref: "Procedure/colonoscopy-0017", signed_by: "Dr. Huda Rahman" });
    expect(screen.queryByTestId("closure-proposal-banner")).toBeNull();
  });

  it("is absent on a closed case", async () => {
    openCase(auto(), cid(11), "clinician");
    await screen.findByTestId("timeline");
    expect(screen.queryByTestId("closure-proposal-banner")).toBeNull();
  });

  it("is absent once the case is closed, even if a stale proposal is still on the read model", async () => {
    const api = auto();
    const base = await api.getCase(DEMO_CLOSURE_PROPOSED_CASE_ID);
    const stale = { ...api, getCase: async (id: string) => ({ ...(await api.getCase(id)), state: "closed", closure_proposal: base.closure_proposal }) } as ApiClient;
    openCase(stale, DEMO_CLOSURE_PROPOSED_CASE_ID, "clinician");
    await screen.findByTestId("timeline");
    expect(screen.queryByTestId("closure-proposal-banner")).toBeNull();
  });

  it("is absent when there is no proposal", async () => {
    openCase(auto(), cid(6), "owner");
    await screen.findByTestId("timeline");
    expect(screen.queryByTestId("closure-proposal-banner")).toBeNull();
  });

  it("a non-clinician sees the proposal but gets no way to sign off, and says why", async () => {
    const api = auto();
    openCase(api, DEMO_CLOSURE_PROPOSED_CASE_ID, "coordinator");
    const banner = await screen.findByTestId("closure-proposal-banner");
    expect(within(banner).queryByTestId("closure-proposal-review")).toBeNull();
    expect(within(banner).getByTestId("closure-proposal-why")).toHaveTextContent(/only the case owner \(a clinician\) can sign off/i);
    expect(screen.getByTestId("signoff")).toBeDisabled();
    expect((await api.getCase(DEMO_CLOSURE_PROPOSED_CASE_ID)).state).toBe("confirmatory_completed");
  });

  it("a clinician who does not own the case gets no button either", async () => {
    openCase(auto(), DEMO_CLOSURE_PROPOSED_CASE_ID, "owner"); // Dr. Layla; Dr. Huda owns this case
    const banner = await screen.findByTestId("closure-proposal-banner");
    expect(within(banner).queryByTestId("closure-proposal-review")).toBeNull();
    expect(within(banner).getByTestId("closure-proposal-why")).toHaveTextContent(/do not own this case/i);
    expect(screen.getByTestId("signoff")).toBeDisabled();
  });
});

describe("Case page: the effort counter and the review reasons", () => {
  it("shows Human actions N / Automated actions M with the share and the saved minutes, flagged as assumed", async () => {
    openCase(auto(), DEMO_CLOSURE_PROPOSED_CASE_ID, "coordinator");
    const counter = await screen.findByTestId("effort-counter");
    expect(counter).toHaveTextContent("Human actions 5 / Automated actions 5");
    expect(screen.getByTestId("effort-share")).toHaveTextContent("50%");
    expect(screen.getByTestId("effort-saved")).toHaveTextContent("14");
    expect(screen.getByTestId("effort-saved")).toHaveTextContent(/assumed minutes, to source/);
  });

  it("is also shown with automation off (all human)", async () => {
    openCase(createMockAdapter(null), cid(10), "coordinator");
    expect(await screen.findByTestId("effort-counter")).toHaveTextContent(/Human actions \d+ \/ Automated actions 0/);
    expect(screen.getByTestId("effort-share")).toHaveTextContent("0%");
    expect(screen.queryByTestId("effort-saved")).toBeNull();
  });

  it("is not shown to a patient (no effort on the redacted case)", async () => {
    openCase(auto(), DEMO_CLOSURE_PROPOSED_CASE_ID, "patient");
    await screen.findByTestId("timeline");
    expect(screen.queryByTestId("effort-counter")).toBeNull();
  });

  it("an exhausted reroute list is flagged for a coordinator, in the banner and on the timeline", async () => {
    openCase(auto(), DEMO_REROUTE_EXHAUSTED_CASE_ID, "coordinator");
    const banner = await screen.findByTestId("reroute-exhausted-banner");
    expect(banner).toHaveTextContent("Reroute list exhausted: a coordinator must reassign");
    expect(screen.getByTestId("reassign")).toBeEnabled();
    const row = screen.getByTestId("timeline").querySelector('li[data-event-type="review_requested"]') as HTMLElement;
    expect(row).toHaveTextContent("Reroute list exhausted: a coordinator must reassign");
    expect(within(row).getByTestId("automation-chip")).toBeInTheDocument();
  });

  it("a withdrawn consent keeps its own wording", async () => {
    openCase(auto(), cid(14), "coordinator");
    const banner = await screen.findByTestId("consent-frozen-banner");
    expect(banner).toHaveTextContent(/flagged for human review \(the patient withdrew consent\)/);
    expect(screen.queryByTestId("reroute-exhausted-banner")).toBeNull();
  });

  it("an assigned case waits for its owner to accept", async () => {
    openCase(auto(), DEMO_ASSIGNED_CASE_ID, "clinician");
    expect(await screen.findByTestId("assigned-note")).toHaveTextContent(/Assigned to Dr\. Layla Haddad/);
    expect(screen.getByTestId("accept-ownership")).toBeDisabled();
    expect(screen.getByTestId("accept-ownership-why")).toHaveTextContent(/must accept it/);
  });
});

describe("Worklist demo control", () => {
  it("files a new positive result and links to the automatically acknowledged and assigned case", async () => {
    const user = userEvent.setup();
    const api = auto();
    renderWithApp(<CoordinatorPage />, { api });
    await screen.findAllByTestId("worklist-row");
    await user.click(screen.getByTestId("file-result"));
    expect(await screen.findByTestId("filed-note")).toHaveTextContent(/acknowledged it and assigned an owner/);
  });
});

describe("Dashboard: the human-effort card", () => {
  it("shows the reduction against the 40% target, human versus automated actions, and the assumed-minutes caveat", async () => {
    const api = auto();
    renderWithApp(<DashboardPage />, { api, role: "coordinator" });
    const card = await screen.findByTestId("effort-card");
    await waitFor(() => expect(within(card).getByTestId("effort-total-automated")).not.toHaveTextContent(/^$/));
    const m = (await api.getEffortMetrics())!;
    expect(within(card).getByTestId("effort-reduction")).toHaveTextContent(`${m.totals.reduction_pct}%`);
    expect(within(card).getByTestId("effort-target")).toHaveTextContent("Target: 40%");
    expect(within(card).getByTestId("effort-total-human")).toHaveTextContent(String(m.totals.human_actions));
    expect(within(card).getByTestId("effort-total-automated")).toHaveTextContent(String(m.totals.automated_actions));
    expect(within(card).getByTestId("effort-target-status")).toHaveTextContent(m.totals.meets_target ? "Target met" : "Below target");
    expect(within(card).getByRole("meter")).toHaveAttribute("aria-valuenow", String(m.totals.reduction_pct ?? 0));
    expect(within(card).getByTestId("effort-caveat")).toHaveTextContent(/assumed minutes, to source/i);
  });

  it("is for the coordinator and supervisor only", async () => {
    renderWithApp(<DashboardPage />, { api: auto(), role: "owner" });
    await screen.findByTestId("kpi-p50");
    expect(screen.queryByTestId("effort-card")).toBeNull();
  });

  it("says there is no data when the API has none, and still shows the caveat", () => {
    render(<EffortCard metrics={null} />);
    expect(screen.getByTestId("effort-empty")).toBeInTheDocument();
    expect(screen.getByTestId("effort-caveat")).toBeInTheDocument();
  });

  it("shows a missed target as below target", () => {
    const m: EffortMetrics = {
      sim_time: SIM_NOW, cases: 1, per_case: [],
      totals: { human_actions: 4, automated_actions: 1, automation_share: 0.2, human_minutes: 20, baseline_minutes: 24, minutes_saved: 4, reduction_pct: 16.7, target_pct: 40, meets_target: false },
      assumptions: { status: "assumed", note: "Assumed.", minutes: { acknowledge: 2 } },
    };
    render(<EffortCard metrics={m} />);
    expect(screen.getByTestId("effort-target-status")).toHaveTextContent("Below target");
    expect(screen.getByTestId("effort-reduction")).toHaveTextContent("16.7%");
  });
});

describe("http adapter: GET /metrics/effort", () => {
  const effort: EffortMetrics = {
    sim_time: "2026-11-04T09:00:00Z", cases: 2,
    totals: { human_actions: 6, automated_actions: 4, automation_share: 0.4, human_minutes: 30, baseline_minutes: 50, minutes_saved: 20, reduction_pct: 40, target_pct: 40, meets_target: true },
    per_case: [
      { case_id: "c1", human_actions: 3, automated_actions: 2, automation_share: 0.4, human_minutes: 15, baseline_minutes: 25 },
      { case_id: "c2", human_actions: 3, automated_actions: 2, automation_share: 0.4, human_minutes: 15, baseline_minutes: 25 },
    ],
    assumptions: { status: "assumed", note: "Minutes are assumed.", minutes: { acknowledge: 2 } },
  };
  const make = (status: number, body: unknown) => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const fn = (async (url: string | URL | Request, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      return new Response(JSON.stringify(body), { status });
    }) as typeof fetch;
    return { api: createHttpAdapter("http://api.test/", fn), calls };
  };

  it("reads the totals, the per-case rows and the assumptions as the API sends them", async () => {
    const { api, calls } = make(200, effort);
    expect(await api.getEffortMetrics()).toEqual(effort);
    expect(calls[0].url).toBe("http://api.test/metrics/effort");
    expect(calls[0].init?.method ?? "GET").toBe("GET");
  });
  it("degrades to null on 404 (not shipped) and 403 (role may not read it), but not on a server error", async () => {
    expect(await make(404, { detail: "Not Found" }).api.getEffortMetrics()).toBeNull();
    expect(await make(403, { detail: "role may not call this route" }).api.getEffortMetrics()).toBeNull();
    await expect(make(500, { detail: "boom" }).api.getEffortMetrics()).rejects.toMatchObject({ status: 500 });
  });
  it("passes actor_kind, closure_proposal and effort of a case through unchanged", async () => {
    const wire = {
      case_id: "c1", state: "confirmatory_completed",
      read_model: { closure_proposal: { evidence_ref: "Procedure/p1", proposed_at: "2026-11-03T09:00:00Z", event_id: "e9" }, review_reason: "reroute_exhausted" },
      effort: effort.per_case[0] && { human_actions: 3, automated_actions: 2, automation_share: 0.4, human_minutes: 15, baseline_minutes: 25, minutes_saved: 10, reduction_pct: 40 },
      events: [{ seq: 1, type: "acknowledge", at: "2026-11-01T09:00:00Z", actor: "system:automation", actor_kind: "automation", payload: { auto: true } }],
    };
    const d = await make(200, wire).api.getCase("c1");
    expect(d.events[0].actor_kind).toBe("automation");
    expect(d.closure_proposal).toEqual({ evidence_ref: "Procedure/p1", proposed_at: "2026-11-03T09:00:00Z", event_id: "e9" });
    expect(d.review_reason).toBe("reroute_exhausted");
    expect(d.effort).toMatchObject({ human_actions: 3, automated_actions: 2 });
  });
});
