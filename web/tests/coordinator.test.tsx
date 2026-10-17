import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { DEMO_OVERDUE_CASE_ID } from "../src/api/fixtures";
import { STRINGS } from "../src/i18n/strings";
import { CaseDetailPage } from "../src/pages/CaseDetailPage";
import { CoordinatorPage } from "../src/pages/CoordinatorPage";
import { renderWithApp } from "./helpers";

const cid = (n: number) => `c${String(n).padStart(7, "0")}-0000-4000-8000-${String(n).padStart(12, "0")}`;
const openCase = (n: number, role?: "coordinator" | "clinician" | "specialist") =>
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

describe("Case detail actions", () => {
  it("escalate, request draft and approve each post through the client and show provenance", async () => {
    const user = userEvent.setup();
    const { api } = renderWithApp(<CaseDetailPage />, { route: `/coordinator/${DEMO_OVERDUE_CASE_ID}`, path: "/coordinator/:caseId" });
    await screen.findByTestId("escalate");
    await user.click(screen.getByTestId("escalate"));
    await waitFor(() => expect(screen.getByTestId("flash")).toHaveTextContent(/escalated/i));
    await user.click(screen.getByTestId("request-draft"));
    const draftItem = await screen.findByTestId(/^draft-d-mock/);
    expect(draftItem).toHaveTextContent(/AI draft/);
    expect(draftItem).toHaveTextContent(/template fallback/);
    expect(within(draftItem).getByText(/تنظير القولون/).closest("p")).toHaveAttribute("dir", "rtl");
    await user.click(within(draftItem).getByRole("button", { name: /approve draft/i }));
    await waitFor(() => expect(screen.getByTestId(/^approved-d-mock/)).toHaveTextContent(/Approved by Maryam/));
    const types = (await api.getCase(DEMO_OVERDUE_CASE_ID)).events.slice(-4).map((e) => e.type);
    expect(types).toEqual(["escalate", "message_drafted", "message_approved", "message_sent"]);
    // The backend's message_sent follows the approval; the panel says so.
    const all = (await api.getCase(DEMO_OVERDUE_CASE_ID)).events;
    expect(all.at(-1)?.type).toBe("message_sent");
    expect(screen.getByTestId(/^sent-d-mock/)).toHaveTextContent(/Sent/);
    expect(all.find((e) => e.type === "escalate" && e.actor.startsWith("coordinator:"))?.payload).toEqual({ rung: "supervisor", target_role: "supervisor" });
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
    await api.postEvent(cid(3), { type: "consent_updated", actor: "patient:c3", payload: { channel: "sms", share_with_specialist: false, withdrawn: true } });
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

describe("Reopen (clinician only, closed cases only)", () => {
  it("is not offered on an open case", async () => {
    openCase(1, "clinician");
    await screen.findByTestId("signoff");
    expect(screen.queryByTestId("reopen-panel")).toBeNull();
  });

  it("is disabled for a coordinator, with the reason", async () => {
    openCase(11);
    const btn = await screen.findByTestId("reopen");
    expect(btn).toBeDisabled();
    expect(screen.getByTestId("reopen-why")).toHaveTextContent(/only|cannot|clinician/i);
  });

  it("a clinician must give a reason; then the case is reopened through the client", async () => {
    const user = userEvent.setup();
    const { api } = openCase(11, "clinician");
    const btn = await screen.findByTestId("reopen");
    expect(btn).toBeDisabled();
    expect(screen.getByTestId("reopen-why")).toHaveTextContent(/reason/i);
    await user.type(screen.getByLabelText("Reason for reopening"), "New finding on the report");
    expect(btn).toBeEnabled();
    await user.click(btn);
    await waitFor(() => expect(screen.getByTestId("flash")).toHaveTextContent(/reopened/i));
    const d = await api.getCase(cid(11));
    expect(d.state).toBe("reopened");
    expect(d.events.at(-1)).toMatchObject({ type: "reopen", actor: expect.stringMatching(/^clinician:/), payload: { reason: "New finding on the report" } });
    expect(screen.queryByTestId("reopen-panel")).toBeNull();
    expect(screen.getByTestId("accept-ownership")).toBeInTheDocument();
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
