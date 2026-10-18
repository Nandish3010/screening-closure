import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Role } from "../src/api/contract";
import { DEMO_OVERDUE_CASE_ID } from "../src/api/fixtures";
import { STRINGS } from "../src/i18n/strings";
import { CaseDetailPage } from "../src/pages/CaseDetailPage";
import { CoordinatorPage } from "../src/pages/CoordinatorPage";
import { renderWithApp } from "./helpers";

const cid = (n: number) => `c${String(n).padStart(7, "0")}-0000-4000-8000-${String(n).padStart(12, "0")}`;
const openCase = (n: number, role?: Role) =>
  renderWithApp(<CaseDetailPage />, { role, route: `/coordinator/${cid(n)}`, path: "/coordinator/:caseId" });

describe("Coordinator worklist", () => {
  it("shows the most overdue case first and filters by overdue", async () => {
    const user = userEvent.setup();
    renderWithApp(<CoordinatorPage />);
    const rows = await screen.findAllByTestId("worklist-row");
    expect(within(rows[0]).getByRole("link")).toHaveAttribute("href", `/coordinator/${DEMO_OVERDUE_CASE_ID}`);
    const total = rows.length;
    await user.click(screen.getByLabelText("Overdue only"));
    const overdueRows = screen.getAllByTestId("worklist-row");
    expect(overdueRows.length).toBeLessThan(total);
    overdueRows.forEach((r) => expect(r).toHaveClass("row-overdue"));
  });
});

describe("Milestone clocks on the case page (A18)", () => {
  it("shows the referral and colonoscopy clocks: met and on track, or overdue", async () => {
    openCase(9); // referred in time; the colonoscopy clock runs from the referral acceptance
    const ref = await screen.findByTestId("milestone-referral_requested");
    expect(screen.getByText("Referral within 15 working days of result")).toBeInTheDocument();
    expect(screen.getByText("Colonoscopy within 31 working days of referral")).toBeInTheDocument();
    expect(ref).toHaveTextContent(/due .*met /);
    expect(screen.getByTestId("milestone-confirmatory_completed")).toHaveTextContent(/due .*on track/);
  });
  it("marks a referral that was never requested as overdue, and the colonoscopy clock as not started", async () => {
    openCase(3);
    const ref = await screen.findByTestId("milestone-referral_requested");
    expect(ref).toHaveTextContent(/overdue/);
    expect(screen.getByTestId("milestone-confirmatory_completed")).toHaveTextContent("starts when the referral is accepted");
  });
});

describe("Case detail actions", () => {
  it("a coordinator escalates and requests a draft, sees its provenance and text, but cannot approve it", async () => {
    const user = userEvent.setup();
    const { api } = renderWithApp(<CaseDetailPage />, { route: `/coordinator/${DEMO_OVERDUE_CASE_ID}`, path: "/coordinator/:caseId" });
    await screen.findByTestId("escalate");
    await user.click(screen.getByTestId("escalate"));
    await waitFor(() => expect(screen.getByTestId("flash")).toHaveTextContent(/escalated/i));
    await user.click(screen.getByTestId("request-draft"));
    const draftItem = await screen.findByTestId(/^draft-d-mock/);
    expect(draftItem).toHaveTextContent(/AI draft/);
    // The patient answered in Arabic, so the draft form defaults to Arabic and the text is right-to-left.
    expect(within(draftItem).getByText(/تنظير القولون/).closest("p")).toHaveAttribute("dir", "rtl");
    // The event carries no text: the timeline loads it from GET /cases/{id}/drafts.
    const tl = screen.getByTestId("timeline");
    const drafted = [...tl.querySelectorAll('li[data-event-type="message_drafted"]')] as HTMLElement[];
    expect(within(drafted.at(-1)!).getByText(/تنظير القولون/)).toBeInTheDocument();
    const approve = within(draftItem).getByRole("button", { name: /approve draft/i });
    expect(approve).toBeDisabled();
    expect(within(screen.getByTestId("drafts-panel")).getByTestId("decide-why")).toHaveTextContent(/only the case owner can approve or reject.*cannot do this/i);
    expect(approve).toHaveAccessibleDescription(/only the case owner/i);
    const types = (await api.getCase(DEMO_OVERDUE_CASE_ID)).events.slice(-2).map((e) => e.type);
    expect(types).toEqual(["escalate", "message_drafted"]);
    const all = (await api.getCase(DEMO_OVERDUE_CASE_ID)).events;
    expect(all.find((e) => e.type === "escalate" && e.actor.startsWith("coordinator:"))?.payload).toEqual({ rung: "supervisor", state: expect.any(String), target: "supervisor" });
    expect(all.at(-1)?.payload).not.toHaveProperty("text_en");
  });

  it("the case owner approves a draft: message_approved by the owner, then the backend's message_sent", async () => {
    const user = userEvent.setup();
    const { api } = renderWithApp(<CaseDetailPage />, { role: "owner", route: `/coordinator/${DEMO_OVERDUE_CASE_ID}`, path: "/coordinator/:caseId" });
    await screen.findByTestId("request-draft");
    await user.click(screen.getByTestId("request-draft"));
    const draftItem = await screen.findByTestId(/^draft-d-mock/);
    await user.click(within(draftItem).getByRole("button", { name: /approve draft/i }));
    await waitFor(() => expect(screen.getByTestId(/^approved-d-mock/)).toHaveTextContent(/Approved by Dr\. Layla Haddad/));
    expect(screen.getByTestId(/^sent-d-mock/)).toHaveTextContent(/Sent/);
    const evs = (await api.getCase(DEMO_OVERDUE_CASE_ID)).events;
    expect(evs.slice(-3).map((e) => e.type)).toEqual(["message_drafted", "message_approved", "message_sent"]);
    expect(evs.at(-2)?.actor).toBe("primary_care_owner:Dr. Layla Haddad");
    expect(evs.at(-1)?.actor).toBe("system:notifier");
  });

  it("rejecting a draft needs a reason from the list, writes message_rejected and removes the approve button", async () => {
    const user = userEvent.setup();
    const { api } = renderWithApp(<CaseDetailPage />, { role: "owner", route: `/coordinator/${DEMO_OVERDUE_CASE_ID}`, path: "/coordinator/:caseId" });
    await screen.findByTestId("request-draft");
    await user.click(screen.getByTestId("request-draft"));
    const draftItem = await screen.findByTestId(/^draft-d-mock/);
    const reject = within(draftItem).getByRole("button", { name: /reject draft/i });
    expect(reject).toBeDisabled();
    await user.selectOptions(within(draftItem).getByLabelText(/reason for rejecting/i), "tone");
    expect(reject).toBeEnabled();
    const before = (await api.getCase(DEMO_OVERDUE_CASE_ID)).events.length;
    await user.click(reject);
    await waitFor(() => expect(screen.getByTestId(/^rejected-d-mock/)).toHaveTextContent(/Wrong tone/));
    expect(within(screen.getByTestId(/^draft-d-mock/)).queryByRole("button", { name: /approve draft/i })).toBeNull();
    const after = (await api.getCase(DEMO_OVERDUE_CASE_ID)).events;
    expect(after).toHaveLength(before + 1);
    expect(after.at(-1)).toMatchObject({ type: "message_rejected", payload: { reason: "tone" } });
  });

  it("shows hand-off rejection reasons", async () => {
    renderWithApp(<CaseDetailPage />, { route: `/coordinator/${DEMO_OVERDUE_CASE_ID}`, path: "/coordinator/:caseId" });
    expect(await screen.findByTestId("rejections")).toHaveTextContent(/No capacity/);
  });

  it("explains why an action is disabled for the wrong role", async () => {
    renderWithApp(<CaseDetailPage />, { role: "specialist", route: `/coordinator/${DEMO_OVERDUE_CASE_ID}`, path: "/coordinator/:caseId" });
    const btn = await screen.findByTestId("escalate");
    expect(btn).toBeDisabled();
    expect(screen.getByTestId("escalate-why")).toHaveTextContent(/specialist.*cannot/i);
  });
});

describe("Worklist: A2 read model", () => {
  it("shows escalation level, the red watchdog marker, frozen consent and overdue days computed from due_at", async () => {
    renderWithApp(<CoordinatorPage />);
    const rows = await screen.findAllByTestId("worklist-row");
    const rowFor = (n: number) => rows.find((r) => within(r).queryByRole("link")?.getAttribute("href") === `/coordinator/${cid(n)}`)!;
    expect(rowFor(1)).toHaveTextContent("23 days");
    expect(rowFor(2)).toHaveTextContent("Level 2");
    expect(rowFor(13)).toHaveTextContent("Nobody is looking at this");
    expect(rowFor(13)).toHaveClass("row-watchdog");
    expect(within(rowFor(13)).getByTestId("watchdog-marker")).toBeInTheDocument();
    expect(rowFor(14)).toHaveTextContent("Consent frozen");
    expect(screen.getAllByTestId("watchdog-marker")).toHaveLength(1);
    expect(rowFor(5)).not.toHaveTextContent("Nobody is looking at this");
  });
});

describe("Case detail: vocabulary and markers", () => {
  it("renders the watchdog flag as a red banner and a timeline marker, with the automated chip on the watchdog actor", async () => {
    openCase(13);
    const banner = await screen.findByTestId("watchdog-banner");
    expect(banner).toHaveTextContent("Nobody is looking at this");
    expect(banner).toHaveTextContent(/11 days/);
    const item = within(screen.getByTestId("timeline")).getByText("Nobody is looking at this").closest("li")!;
    expect(item).toHaveClass("tl-watchdog");
    expect(within(item).getByTestId("automated-chip")).toBeInTheDocument();
    expect(item).toHaveTextContent("watchdog");
  });

  it("marks automated actors (scheduler, model, system) with a chip and human actors without one", async () => {
    openCase(1);
    const timeline = await screen.findByTestId("timeline");
    const byType = (t: string) => timeline.querySelector(`li[data-event-type="${t}"]`)!;
    expect(within(byType("mark_overdue") as HTMLElement).getByTestId("automated-chip")).toBeInTheDocument();
    expect(byType("mark_overdue")).toHaveTextContent("Marked overdue by the scheduler");
    expect(within(byType("message_drafted") as HTMLElement).getByTestId("automated-chip")).toBeInTheDocument();
    expect(within(byType("message_sent") as HTMLElement).getByTestId("automated-chip")).toBeInTheDocument();
    expect(within(byType("message_approved") as HTMLElement).queryByTestId("automated-chip")).toBeNull();
    expect(within(byType("accept_ownership") as HTMLElement).queryByTestId("automated-chip")).toBeNull();
    expect(byType("handoff_requested")).toHaveTextContent("Hand-off requested");
    expect(byType("handoff_rejected")).toHaveTextContent("No capacity");
  });

  it("an escalation raised by the scheduler waits for a human, who accepts it with escalation_accepted", async () => {
    const user = userEvent.setup();
    const { api } = openCase(2);
    const pending = await screen.findByTestId("pending-escalation");
    expect(screen.getByTestId("escalation-level")).toHaveTextContent(/Level 2 of 3.*waiting/);
    await user.click(within(pending).getByTestId("accept-escalation"));
    await waitFor(() => expect(screen.queryByTestId("pending-escalation")).toBeNull());
    const last = (await api.getCase(cid(2))).events.at(-1)!;
    expect(last).toMatchObject({ type: "escalation_accepted", payload: { rung: "supervisor" } });
  });

  it("frozen consent disables patient contact and drafts, and says why", async () => {
    openCase(14);
    expect(await screen.findByTestId("consent-frozen-banner")).toBeInTheDocument();
    expect(screen.getByTestId("request-draft")).toBeDisabled();
    expect(screen.getByTestId("request-draft-why")).toHaveTextContent(/consent is frozen/i);
  });

  it("frozen consent also blocks recording a patient contact", async () => {
    // Case 14 is at patient_contacted, so exercise the owner_accepted button via a case that is frozen there.
    const api = (await import("../src/api/mockAdapter")).createMockAdapter(null);
    await api.postEvent(cid(3), { type: "consent_updated", actor: "patient:c3", payload: { withdrawn: true } });
    renderWithApp(<CaseDetailPage />, { api, route: `/coordinator/${cid(3)}`, path: "/coordinator/:caseId" });
    const btn = await screen.findByTestId("contact-patient");
    expect(btn).toBeDisabled();
    expect(screen.getByTestId("contact-patient-why")).toHaveTextContent(/consent is frozen/i);
  });

  it("mark lost contact posts mark_lost_contact and shows a banner", async () => {
    const user = userEvent.setup();
    const { api } = openCase(7);
    await user.click(await screen.findByTestId("mark-lost-contact"));
    expect(await screen.findByTestId("lost-contact-banner")).toBeInTheDocument();
    expect((await api.getCase(cid(7))).events.at(-1)?.type).toBe("mark_lost_contact");
  });
});

describe("Hand-off: pending, bounced, owner scoping (A5)", () => {
  it("a bounced hand-off is shown distinctly from a rejection, with the 3-day wording and the re-route action", async () => {
    const user = userEvent.setup();
    const { api } = openCase(15);
    const banner = await screen.findByTestId("handoff-bounced");
    expect(banner).toHaveTextContent(/bounced: no response/i);
    expect(banner).toHaveTextContent(/Marina Digestive Health did not answer in 3 working days/);
    expect(screen.queryByTestId("rejections")).toBeNull(); // not listed as an ordinary rejection
    expect(within(screen.getByTestId("timeline")).getByTestId("tl-bounce")).toHaveTextContent(/no response/i);
    await user.click(screen.getByTestId("reassign"));
    await waitFor(() => expect(screen.getByTestId("flash")).toHaveTextContent(/re-routed/i));
    expect((await api.getCase(cid(15))).state).toBe("owner_accepted");
    expect(screen.queryByTestId("handoff-bounced")).toBeNull();
  });

  it("an ordinary rejection is not called a bounce", async () => {
    openCase(1);
    expect(await screen.findByTestId("rejections")).toHaveTextContent(/No capacity/);
    expect(screen.queryByTestId("handoff-bounced")).toBeNull();
  });

  it("a clinician who does not own the case cannot request a hand-off: the page says to ask the current owner (the API answers 403)", async () => {
    const { api } = openCase(1, "clinician");
    expect(await screen.findByTestId("handoff-not-owner")).toHaveTextContent(/do not own this case.*Dr\. Layla Haddad/i);
    expect(screen.getByTestId("handoff-not-owner")).toHaveTextContent(/Ask the current owner \(Dr\. Layla Haddad\) to hand it off to Marina Digestive Health/);
    expect(screen.queryByTestId("hand-off")).toBeNull();
    expect(screen.queryByTestId("request-my-provider")).toBeNull();
    expect(screen.queryByText(/Request handoff to my provider/i)).toBeNull();
    const d = await api.getCase(DEMO_OVERDUE_CASE_ID);
    expect(d.pending_handoff ?? null).toBeNull();
  });

  it("the owner requests a hand-off from the provider list and waits for acceptance when the provider has not answered", async () => {
    const { api } = openCase(16, "owner");
    expect(await screen.findByTestId("handoff-pending")).toHaveTextContent(/Corniche Gastro Unit/);
    expect(screen.getByTestId("handoff-waiting")).toBeInTheDocument();
    expect(screen.queryByTestId("accept-handoff")).toBeNull(); // the requester cannot accept their own request
    expect((await api.getCase(cid(16))).pending_handoff).not.toBeNull();
  });

  it("the clinician at the receiving provider can accept a pending hand-off and becomes the owner", async () => {
    const user = userEvent.setup();
    const { api } = openCase(16, "specialist");
    await user.click(await screen.findByTestId("accept-handoff"));
    await waitFor(() => expect(screen.queryByTestId("handoff-waiting")).toBeNull());
    expect(screen.getByTestId("handoff-accepted")).toHaveTextContent(/Accepted.*Corniche Gastro Unit/);
    expect(screen.getByTestId("owner")).toHaveTextContent("Dr. Karim Nasser");
    expect((await api.getCase(cid(16))).owner_actor).toBe("specialist_owner:Dr. Karim Nasser");
  });

  it("the worklist shows owner and provider, pending and bounced hand-offs, and filters flagged cases", async () => {
    const user = userEvent.setup();
    renderWithApp(<CoordinatorPage />);
    const rows = await screen.findAllByTestId("worklist-row");
    const rowFor = (n: number) => rows.find((r) => within(r).queryByRole("link")?.getAttribute("href") === `/coordinator/${cid(n)}`)!;
    expect(rowFor(15)).toHaveTextContent("Hand-off bounced");
    expect(rowFor(16)).toHaveTextContent("Hand-off pending");
    expect(rowFor(9)).toHaveTextContent("Dr. Karim Nasser");
    expect(rowFor(9)).toHaveTextContent("Corniche Gastro Unit");
    await user.click(screen.getByLabelText(/Flagged only/));
    const flagged = screen.getAllByTestId("worklist-row");
    expect(flagged).toHaveLength(1);
    expect(within(flagged[0]).getByTestId("watchdog-marker")).toBeInTheDocument();
  });
});

describe("Reopen (clinician only, closed cases only)", () => {
  it("is not offered on an open case", async () => {
    openCase(1, "clinician");
    await screen.findByTestId("signoff");
    expect(screen.queryByTestId("reopen-panel")).toBeNull();
  });

  it("is refused for a clinician who does not own the closed case, with the reason", async () => {
    renderWithApp(<CaseDetailPage />, { role: "owner", route: `/coordinator/${cid(11)}`, path: "/coordinator/:caseId" });
    const btn = await screen.findByTestId("reopen");
    expect(btn).toBeDisabled();
    expect(screen.getByTestId("reopen-why")).toHaveTextContent(/do not own this case.*Dr\. Huda Rahman/i);
  });

  it("is disabled for a coordinator, with the reason", async () => {
    openCase(11);
    const btn = await screen.findByTestId("reopen");
    expect(btn).toBeDisabled();
    expect(screen.getByTestId("reopen-why")).toHaveTextContent(/only|cannot|clinician/i);
  });

  it("a clinician picks a reason from the taxonomy; then the case is reopened through the client", async () => {
    const user = userEvent.setup();
    const { api } = openCase(11, "clinician"); // Dr. Huda Rahman signed and owns this case
    const btn = await screen.findByTestId("reopen");
    expect(btn).toBeEnabled(); // the reason is a taxonomy select with a default
    await user.selectOptions(screen.getByLabelText("Reason for reopening"), "evidence_inadequate");
    await user.click(btn);
    await waitFor(() => expect(screen.getByTestId("flash")).toHaveTextContent(/reopened/i));
    const d = await api.getCase(cid(11));
    expect(d.state).toBe("reopened");
    expect(d.events.at(-1)).toMatchObject({ type: "reopen", actor: "primary_care_owner:Dr. Huda Rahman", payload: { reason: "evidence_inadequate" } });
    expect(screen.queryByTestId("reopen-panel")).toBeNull();
    // A reopened case goes back to clinician review (YAML: reopened --clinician_review--> clinician_reviewed).
    expect(screen.getByTestId("signoff")).toBeInTheDocument();
  });
});

describe("i18n completeness", () => {
  it("Arabic mirrors every English key and step count", () => {
    expect(Object.keys(STRINGS.ar).sort()).toEqual(Object.keys(STRINGS.en).sort());
    expect(STRINGS.ar.nextSteps).toHaveLength(STRINGS.en.nextSteps.length);
    expect(STRINGS.ar.progress).toHaveLength(STRINGS.en.progress.length);
    expect(STRINGS.ar.resultBody).toMatch(/[؀-ۿ]/);
  });
});
